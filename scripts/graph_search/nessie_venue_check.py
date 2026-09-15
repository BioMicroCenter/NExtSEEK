#!/usr/bin/env python3
"""The Nessie evaluation venue's helper (graph_search follow-up 1, plan task T8; spec section 6).

``scripts/graph_search/nessie_venue.sh`` calls it. Every subcommand but ``check`` uses the standard library only and
runs on the host; ``check`` runs inside the venue (``gs-nessie-venue``), with Django and this branch's chat_nextseek.

  settings --template T --operator O --out F   the venue's dmac/local_settings.py: the tracked template with only the
                                               operator's ASSISTANT_PARTICIPATING_PROJECTS (prepare)
  env --out F [--lookup DOTENV] FILE...        the live checkout's compose env files as one docker run --env-file (up)
  overrides                                    the venue's -e overrides, one NAME=value per line; none is a secret (up)
  progress RUN_DIR [--log F] [--running S]     questions done per arm, errors and outages, the log's last lines
  check [--out F]                              the venue check (inside the venue); prints overrides-ok and PASS
  selftest                                     this file's tests: the pure parts and the shell script, no container

Nothing here prints an environment value or a settings value: an error names the variable or the line number, and
``check`` prints counts, sizes, timings and pass flags.
"""
from __future__ import annotations

import argparse
import ast
import collections
import contextlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
SCRIPT = HERE / "nessie_venue.sh"


class VenueError(Exception):
    """A refusal; the message names what to fix and never carries a value."""


# ====================================================================================================================
# The venue's environment (spec section 6)
# ====================================================================================================================

VENUE_URL = "http://127.0.0.1:8000"

# Set with -e on the venue, after the rendered env file; the check compares each one. None of them is a secret.
OVERRIDES: dict[str, str] = {
    "DJANGO_ALLOWED_HOSTS": "127.0.0.1 localhost",
    # The API agent's REST self-calls stay in the venue (operator ruling R10).
    "NEXTSEEK_INTERNAL_BASE_URL": VENUE_URL,
    "NEXTSEEK_BASE_URL": VENUE_URL,
    "LOG_DIR": "/venue/logs",
    "NEXTSEEK_OUTPUTS_DIR": "/venue/outputs",
    "NEXTSEEK_EVAL_PARSER_FORCE": "1",
    "NEXTSEEK_POSTERIOR_ROUTING_ENABLED": "0",
    "PYTHONDONTWRITEBYTECODE": "1",
    # The snapshot's engine source first, ahead of the image's editable installs.
    "PYTHONPATH": "/src:/src/NessieAI/chat_nextseek/src:/src/NessieAI/dmac_assistant/src",
    # The snapshot's model catalog, not the image's copy that the live env file names.
    "CATALOG_FILE": "/src/NessieAI/chat_nextseek/agent_model_catalog.json",
    # No Luria key: the pipeline submit tool is never offered.
    "LURIA_USER": "",
    "LURIAKEY": "",
    "LURIA_WORKING_PATH": "",
}
# NessieAI/CLAUDE.md "Box env": when set, they beat the package defaults. Left out of the rendered env file.
MUST_BE_UNSET = ("DMAC_ROUTE_CAPABILITIES_FILE", "DMAC_ROUTER_MODEL_CLASS_MAP_FILE")
# Without the first five the venue reaches neither database; without the other three Django cannot boot
# (dmac/settings.py builds SEEK_URL from SEEK_HOST and concatenates NEXTSEEK_HOSTNAME; signing needs the key).
REQUIRED_ENV = ("MYSQL_HOST", "MYSQL_USER", "MYSQL_PASSWORD", "NEO4J_URI", "NEO4J_PASSWORD",
                "SEEK_HOST", "NEXTSEEK_HOSTNAME", "DJANGO_SECRET_KEY")
# Without these every model call fails: the check can still pass, a paid turn cannot.
MODEL_KEYS = ("GCP_API_KEY", "AWS_BEARER_TOKEN_BEDROCK")
PLACEHOLDER = "SET_IN_LOCAL_ENV"  # the unset value in startup/templates/nextseek.env.template


def overrides_lines() -> list[str]:
    """The venue's ``-e`` flags, as ``NAME=value``."""
    return [f"{name}={value}" for name, value in OVERRIDES.items()]


# ====================================================================================================================
# settings: the venue's dmac/local_settings.py
# ====================================================================================================================

PROJECTS_NAME = "ASSISTANT_PARTICIPATING_PROJECTS"
SETTINGS_HEADER = (
    "# Rendered by scripts/graph_search/nessie_venue.sh prepare for the Nessie evaluation venue: the tracked\n"
    "# startup/templates/local_settings.py.template with the operator's ASSISTANT_PARTICIPATING_PROJECTS, and\n"
    "# nothing else from the operator's file.\n")


def _parse_python(text: str, what: str) -> ast.Module:
    try:
        return ast.parse(text)
    except SyntaxError as exc:  # the message would quote the line, so only its number is kept
        raise VenueError(f"{what} does not parse (line {exc.lineno})") from None


def _top_level_assignment(tree: ast.Module, name: str):
    """The last top-level ``name = ...`` (or ``name: T = ...``), as Python would leave it; None when there is none."""
    found = None
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in stmt.targets):
            found = stmt
        elif (isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name) and stmt.target.id == name
              and stmt.value is not None):
            found = stmt
    return found


def _project_key(item) -> tuple:
    text = str(item)
    return (0, int(text), text) if text.isdigit() else (1, 0, text)


def participating_projects(operator_text: str) -> list:
    """The operator's ``ASSISTANT_PARTICIPATING_PROJECTS``, when it is a literal set, list or tuple of ids.

    Read with ``ast``, never executed. ``set(...)`` and ``frozenset(...)`` around a literal are accepted.
    """
    what = "the operator's dmac/local_settings.py"
    stmt = _top_level_assignment(_parse_python(operator_text, what), PROJECTS_NAME)
    if stmt is None:
        raise VenueError(f"{what} assigns no {PROJECTS_NAME} at top level")
    where = f"{PROJECTS_NAME} ({what}, line {stmt.lineno})"
    node = stmt.value
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("set", "frozenset"):
        if node.keywords or len(node.args) != 1:
            raise VenueError(f"{where} is empty or not a literal set, list or tuple of project ids")
        node = node.args[0]
    try:
        items = ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        raise VenueError(f"{where} is not a literal set, list or tuple of project ids") from None
    if not isinstance(items, (set, frozenset, list, tuple)):
        raise VenueError(f"{where} is not a literal set, list or tuple of project ids")
    if not items:
        raise VenueError(f"{where} is empty")
    if any(isinstance(i, bool) or not isinstance(i, (str, int)) for i in items):
        raise VenueError(f"{where} holds something other than project ids")
    return sorted(set(items), key=_project_key)


def active_prod_overrides(text: str) -> list[str]:
    """The ``_PROD_OVERRIDES`` names whose value is not None; any one of them makes the settings build a PROD config."""
    stmt = _top_level_assignment(_parse_python(text, "the rendered settings"), "_PROD_OVERRIDES")
    if stmt is None:
        return []
    if not isinstance(stmt.value, ast.Dict):
        return ["_PROD_OVERRIDES"]
    active = []
    for key, value in zip(stmt.value.keys, stmt.value.values):
        if not (isinstance(value, ast.Constant) and value.value is None):
            active.append(str(key.value) if isinstance(key, ast.Constant) else "?")
    return active


def render_settings(template_text: str, operator_text: str) -> str:
    """The template with its ``ASSISTANT_PARTICIPATING_PROJECTS`` line replaced by the operator's ids, normalized.

    Refuses when the result would build a PROD config (spec section 6: no PROD config in the venue).
    """
    projects = participating_projects(operator_text)
    stmt = _top_level_assignment(_parse_python(template_text, "the settings template"), PROJECTS_NAME)
    if stmt is None:
        raise VenueError(f"the settings template assigns no {PROJECTS_NAME}")
    lines = template_text.splitlines(keepends=True)
    line = f"{PROJECTS_NAME} = set({projects!r})  # from the operator's dmac/local_settings.py\n"
    text = SETTINGS_HEADER + "".join(lines[: stmt.lineno - 1] + [line] + lines[stmt.end_lineno:])
    active = active_prod_overrides(text)
    if active:
        raise VenueError(f"the settings template sets PROD overrides ({', '.join(active)}); the venue builds none")
    return text


# ====================================================================================================================
# env: the live checkout's compose env files as one docker run --env-file
# ====================================================================================================================

_ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*")
_ENV_REF = re.compile(r"\$(?:(\$)|\{([A-Za-z_][A-Za-z0-9_]*)(?:(:?[-?])([^}]*))?\}|([A-Za-z_][A-Za-z0-9_]*))")
_ESCAPED_DOLLAR = "\ue000"  # a private-use character stands in for \$ until expansion is done
_DQ_ESCAPES = {"n": "\n", "r": "\r", "t": "\t", "$": _ESCAPED_DOLLAR}


def _closing_quote(body: str, quote: str) -> int | None:
    if quote == "'":
        end = body.find("'")
        return None if end < 0 else end
    i = 0
    while i < len(body):
        if body[i] == "\\":
            i += 2
            continue
        if body[i] == '"':
            return i
        i += 1
    return None


def _dotenv_entries(text: str, label: str):
    """``(key, raw value, quote, line number)`` per assignment in compose's env-file syntax.

    A line that is not ``KEY=value`` yields ``(None, note, None, line number)``.
    """
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        lineno, stripped = i + 1, lines[i].strip()
        i += 1
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("export "):
            stripped = stripped[len("export "):].lstrip()
        key, eq, rest = stripped.partition("=")
        key = key.strip()
        if not eq or not _ENV_KEY.fullmatch(key):
            yield None, f"{label} line {lineno} is not KEY=value; skipped", None, lineno
            continue
        rest = rest.lstrip()
        if rest[:1] in ("'", '"'):
            quote, body = rest[0], rest[1:]
            end = _closing_quote(body, quote)
            while end is None:  # compose lets a quoted value run over several lines
                if i >= len(lines):
                    raise VenueError(f"{key} ({label} line {lineno}) opens a quote it never closes")
                body += "\n" + lines[i]
                i += 1
                end = _closing_quote(body, quote)
            yield key, body[:end], quote, lineno
        else:
            yield key, re.split(r"\s#", rest, maxsplit=1)[0].strip(), "", lineno


def _expand(value: str, lookup, key: str, where: str, undefined: list) -> str:
    def sub(match: re.Match) -> str:
        if match.group(1):
            return "$"
        name = match.group(2) or match.group(5)
        op, arg = match.group(3), match.group(4) or ""
        found = lookup(name)
        if op == ":-":
            return found if found else arg
        if op == "-":
            return arg if found is None else found
        if op in (":?", "?"):
            if found is None or (op == ":?" and found == ""):
                raise VenueError(f"{key} ({where}) requires ${{{name}}}, which is unset")
            return found
        if found is None:
            undefined.append(name)
            return ""
        return found

    return _ENV_REF.sub(sub, value)


def _parse_into(env: dict, text: str, label: str, lookup_env: dict, notes: list) -> None:
    """Add one env file's assignments to ``env``, expanding references first from ``lookup_env``, then ``env``."""

    def lookup(name: str):
        return lookup_env[name] if name in lookup_env else env.get(name)

    for key, raw, quote, lineno in _dotenv_entries(text, label):
        if key is None:
            notes.append(raw)
            continue
        where = f"{label} line {lineno}"
        undefined: list[str] = []
        if quote == "'":
            value = raw
        else:
            if quote == '"':
                raw = re.sub(r"\\(.)", lambda m: _DQ_ESCAPES.get(m.group(1), m.group(1)), raw, flags=re.S)
            value = _expand(raw, lookup, key, where, undefined).replace(_ESCAPED_DOLLAR, "$")
        for name in dict.fromkeys(undefined):
            notes.append(f"{key} ({where}) uses ${name}, which no env file defines; it is empty in the venue")
        if "\n" in value or "\r" in value:
            raise VenueError(f"{key} ({where}) holds a line break, which docker run --env-file cannot carry")
        env[key] = value


def compose_env(texts, lookup_env: dict | None = None, drop=()) -> tuple[dict[str, str], list[str]]:
    """The variables a service with ``env_file: [texts...]`` gets from ``docker compose``, minus the overrides.

    Compose strips quotes and expands ``$VAR`` and ``${VAR}`` (with ``:-``, ``-``, ``:?`` and ``?``), looking a name
    up first in the project environment (``lookup_env``: the checkout's ``.env``) and then in the values read so far,
    earlier files included; a later file wins. ``docker run --env-file`` does none of this. Each item of ``texts`` is
    a file's text or a ``(label, text)`` pair. Returns the variables and notes that name variables, never values.
    """
    lookup_env = dict(lookup_env or {})
    env: dict[str, str] = {}
    notes: list[str] = []
    for n, item in enumerate(texts, start=1):
        label, text = item if isinstance(item, tuple) else (f"env file {n}", item)
        _parse_into(env, text, label, lookup_env, notes)
    for name in MUST_BE_UNSET:
        if env.pop(name, None) is not None:
            notes.append(f'{name} is set in the env files; left out of the venue (NessieAI/CLAUDE.md "Box env")')
    for name in (*OVERRIDES, *drop):
        env.pop(name, None)
    missing = [name for name in REQUIRED_ENV if not env.get(name)]
    if missing:
        raise VenueError(f"the env files give no value for {', '.join(missing)}")
    for name in MODEL_KEYS:
        if env.get(name, "") in ("", PLACEHOLDER):
            notes.append(f"{name} is unset or a placeholder: the check can pass, but every paid turn will fail")
    return env, notes


def dotenv_values(text: str) -> dict[str, str]:
    """A project ``.env`` file's values, for ``compose_env``'s lookup."""
    values: dict[str, str] = {}
    _parse_into(values, text, ".env", {}, [])
    return values


def render_env_file(env: dict[str, str]) -> str:
    """``docker run --env-file`` text: ``KEY=value`` lines, taken literally by docker."""
    return "".join(f"{key}={value}\n" for key, value in env.items())


def write_private(path, text: str) -> None:
    """Write ``text`` to ``path`` with mode 600, replacing it atomically."""
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


# ====================================================================================================================
# progress: a run directory written by manage.py nessie (arms.json for --arms, else manifest.json)
# ====================================================================================================================


def _load_json_file(path: Path):
    for attempt in range(2):
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except PermissionError:
            raise VenueError(f"cannot read {path.name}: run progress while the venue is up, or down, which hands "
                             "the files back") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            if attempt:
                raise VenueError(f"{path.name} does not parse (it may be mid-write); try again") from None
            time.sleep(0.5)
    return None


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _is_outage(row: dict, entry: dict | None) -> bool:
    if "outage" in row:
        return bool(row["outage"])
    return bool(entry and entry.get("outage"))


def _entries(manifest) -> list[dict]:
    entries = (manifest or {}).get("entries") if isinstance(manifest, dict) else None
    return [e for e in entries or [] if isinstance(e, dict)]


def _tail(path: Path, n: int) -> list[str]:
    with path.open(encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\n") for line in collections.deque(handle, maxlen=max(n, 0))]


def summarize_run(run_dir, running: str = "unknown", log=None, tail: int = 8) -> list[str]:
    """The progress lines for one run directory: per arm the questions done, their outcomes, errors and outages."""
    run_dir = Path(run_dir)
    state = None  # arms.json's progress.state, when there is one
    if not run_dir.is_dir():
        out = [f"no run directory {run_dir.name} yet"]
    else:
        out = [f"run {run_dir.name}"]
        arms_path = run_dir / "arms.json"
        doc = _load_json_file(arms_path)
        if isinstance(doc, dict):
            # runner.run_arms lists every cases-file question up front and fills in each arm as it runs.
            meta = doc.get("run_meta") or {}
            progress = doc.get("progress") if isinstance(doc.get("progress"), dict) else {}
            questions = [q for q in doc.get("questions") or [] if isinstance(q, dict)]
            arms = list(meta.get("arms") or []) or sorted({a for q in questions for a in (q.get("arms") or {})})
            total = progress.get("questions") if isinstance(progress.get("questions"), int) else None
            preflight = meta.get("preflight") or {}
            out.append(f"preflight: passed at {preflight['passed_at']}" if preflight.get("passed_at")
                       else "preflight: not recorded")
            errors = outages = 0
            for arm in arms:
                by_id = {e.get("id"): e for e in _entries(_load_json_file(run_dir / arm / "manifest.json"))}
                rows = [(q.get("id"), (q.get("arms") or {}).get(arm)) for q in questions]
                rows = [(qid, row) for qid, row in rows if isinstance(row, dict)]
                outage = {qid for qid, row in rows if _is_outage(row, by_id.get(qid))}
                outcomes = Counter("outage" if qid in outage else str(row.get("status") or "unknown")
                                   for qid, row in rows)
                arm_errors = outcomes.get("error", 0)
                errors, outages = errors + arm_errors, outages + len(outage)
                detail = ", ".join(f"{n} {s}" for s, n in sorted(outcomes.items())) or "none yet"
                of = f" of {total}" if total is not None else ""
                out.append(f"{arm}: {len(rows)}{of} questions done ({detail}); "
                           f"{_plural(arm_errors, 'error')}, {_plural(len(outage), 'outage')}")
            out.append(f"all arms: {_plural(errors, 'error')} not counting outages, {_plural(outages, 'outage')} "
                       "(a resume reruns the outages)")
            ran = [q.get("id") for q in questions if any(isinstance(r, dict) for r in (q.get("arms") or {}).values())]
            if ran:
                out.append(f"last question run: {ran[-1]}")
            state = progress.get("state")
            if state:
                text = f"run state: {state}"
                if isinstance(progress.get("turns_driven"), int):
                    text += f", {progress['turns_driven']} turns driven"
                if progress.get("max_turns") is not None:
                    text += f" (cap {progress['max_turns']})"
                out.append(text)
            out.append(f"arms.json written {int(time.time() - arms_path.stat().st_mtime)} s ago")
        else:
            entries = _entries(_load_json_file(run_dir / "manifest.json"))
            if entries:
                outcomes = Counter("outage" if e.get("outage") else str(e.get("status") or "unknown") for e in entries)
                detail = ", ".join(f"{n} {s}" for s, n in sorted(outcomes.items()))
                out.append(f"{len(entries)} cases done ({detail}); {_plural(outcomes.get('error', 0), 'error')}, "
                           f"{_plural(outcomes.get('outage', 0), 'outage')}")
            else:
                out.append("no arms.json or manifest.json yet")
    if running == "yes":
        out.append("harness: running")
    elif running == "no" and state == "running":
        out.append("harness: stopped before the run finished (arms.json still says running); continue it with "
                   "the same command and --resume")
    elif running == "no":
        tail_note = ("; the next block is the same command with --resume" if state in ("max_turns", "interrupted")
                     else "")
        out.append(f"harness: done (no harness process in the venue){tail_note}")
    else:
        out.append("harness: unknown (the venue is not running)")
    if log is not None and Path(log).is_file():
        lines = _tail(Path(log), tail)
        out.append(f"--- the last {len(lines)} lines of {Path(log).name}")
        out.extend(lines)
    return out


# ====================================================================================================================
# check: the parts that need no Django
# ====================================================================================================================

_RESOLVED_RE = re.compile(r"^## Resolved sample types: .*?\((?:the (\d+) most-filled|(attribute names only))", re.M)
_OMITTED_RE = re.compile(r"^Left out to fit the context budget: (.*?) \(see the type index\)\.?$", re.M)


def compare_env(environ, expected: dict[str, str]) -> list[str]:
    """The names whose value differs from the expected one; an expected empty value also accepts unset."""
    differ = []
    for name, want in expected.items():
        got = environ.get(name)
        if (got or "") != want if want == "" else got != want:
            differ.append(name)
    return sorted(differ)


def _proc_status(text: str) -> dict[str, str]:
    fields = {}
    for line in text.splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def _runs_gunicorn(argv: list[str]) -> bool:
    """The process is gunicorn itself: its program, the script its Python runs, or a gunicorn process title.

    A wrapper (tini, the venue's sh) carries the gunicorn path as a later argument; that is not gunicorn.
    """
    if not argv:
        return False
    program = os.path.basename(argv[0])
    if program.startswith("gunicorn"):
        return True
    return program.startswith("python") and len(argv) > 1 and os.path.basename(argv[1]).startswith("gunicorn")


def gunicorn_memory(proc_root) -> dict:
    """The gunicorn master and workers under ``proc_root`` and their resident memory, in MiB."""
    procs: dict[int, dict] = {}
    for entry in Path(proc_root).iterdir():
        if not entry.name.isdigit():
            continue
        try:
            parts = [p.decode("utf-8", "replace") for p in (entry / "cmdline").read_bytes().split(b"\0") if p]
            status = _proc_status((entry / "status").read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        if not _runs_gunicorn(parts):
            continue
        rss = (status.get("VmRSS") or "0").split()[0]
        procs[int(entry.name)] = {"ppid": int(status.get("PPid") or 0), "rss_kb": int(rss) if rss.isdigit() else 0}
    masters = sorted(pid for pid, p in procs.items() if p["ppid"] not in procs)
    workers = sorted(pid for pid, p in procs.items() if p["ppid"] in procs)
    mib = lambda pid: round(procs[pid]["rss_kb"] / 1024, 1)  # noqa: E731
    return {"masters": len(masters), "workers": len(workers), "master_rss_mib": [mib(p) for p in masters],
            "worker_rss_mib": [mib(p) for p in workers]}


def context_shape(text: str) -> dict:
    """What ``graph_context.render_graph_context`` produced: bytes, the K it settled on (0 names only), sections."""
    match = _RESOLVED_RE.search(text)
    k = None if match is None else (int(match.group(1)) if match.group(1) else 0)
    after = text[match.end():] if match else ""
    omitted = _OMITTED_RE.search(text)
    return {"bytes": len(text.encode("utf-8")), "k": k,
            "sections": sum(1 for line in after.splitlines() if line.startswith("### ")),
            "omitted": [t.strip() for t in omitted.group(1).split(",")] if omitted else []}


def measure_rendering(label: str, codes, get_details, render, snapshot, budget: int) -> dict:
    """Render ``codes`` as the graph agent would and measure the text against ``budget``."""
    codes = list(codes)
    details = list(get_details(codes))
    titles = [getattr(d, "title", None) for d in details]
    missing = [code for code in codes if code not in titles]
    shape = context_shape(render(snapshot, details))
    return {"label": label, "types": codes, "missing": missing, **shape, "budget": budget,
            "ok": shape["bytes"] <= budget and not missing}


def largest_types(snapshot, n: int = 3) -> list[str]:
    """The ``n`` non-deprecated types with the most attributes holding values."""
    rows = [r for r in snapshot.index if not r.deprecated]
    rows.sort(key=lambda r: (-(r.attributes_with_values or 0), r.title))
    return [r.title for r in rows[:n]]


_TX_METHODS = frozenset({"execute_read", "execute_write", "run", "begin_transaction", "read_transaction",
                         "write_transaction"})


class _SpySession:
    def __init__(self, inner, calls: list, mode: str):
        self._inner, self._calls, self._mode = inner, calls, mode

    def __enter__(self):
        enter = getattr(self._inner, "__enter__", None)
        if enter is not None:
            enter()
        return self

    def __exit__(self, *exc):
        leave = getattr(self._inner, "__exit__", None)
        return leave(*exc) if leave is not None else False

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name not in _TX_METHODS or not callable(attr):
            return attr

        def recorded(*args, **kwargs):
            self._calls.append({"kind": name, "access_mode": self._mode})
            return attr(*args, **kwargs)

        return recorded


class _SpyDriver:
    def __init__(self, inner, calls: list):
        self._inner, self._calls = inner, calls

    def session(self, *args, **kwargs):
        mode = str(kwargs.get("default_access_mode") or "default")
        return _SpySession(self._inner.session(*args, **kwargs), self._calls, mode)

    def __enter__(self):
        enter = getattr(self._inner, "__enter__", None)
        if enter is not None:
            enter()
        return self

    def __exit__(self, *exc):
        leave = getattr(self._inner, "__exit__", None)
        return leave(*exc) if leave is not None else False

    def __getattr__(self, name):
        return getattr(self._inner, name)


@contextlib.contextmanager
def read_spy(graph_database, calls: list):
    """Record the transaction kind of every session call on drivers made by ``graph_database.driver`` meanwhile.

    Proves that the code under test reads through ``execute_read`` (spec D3) without attempting a write.
    """
    had = "driver" in vars(graph_database)
    raw = vars(graph_database).get("driver")
    original = graph_database.driver

    def driver(*args, **kwargs):
        return _SpyDriver(original(*args, **kwargs), calls)

    graph_database.driver = staticmethod(driver)
    try:
        yield calls
    finally:
        if had:
            setattr(graph_database, "driver", raw)
        else:
            delattr(graph_database, "driver")


def reads_only(calls: list) -> bool:
    """At least one transaction was observed, and every one was an ``execute_read``."""
    return bool(calls) and all(call["kind"] == "execute_read" for call in calls)


def verdict(checks: list[dict]) -> tuple[str, str | None]:
    if not checks:
        return "FAIL", "no checks ran"
    for check in checks:
        if not check.get("ok"):
            return "FAIL", check.get("name")
    return "PASS", None


def answered(status: int | None) -> bool:
    """Django answered for this host name: any status but none, a 400 (a disallowed host) or a server error."""
    return status is not None and status != 400 and status < 500


def http_probe(url: str, host: str | None = None, timeout: float = 20.0) -> dict:
    """GET ``url`` (as ``host`` when given): the HTTP status, or why there was none, and the seconds taken."""
    request = urllib.request.Request(url, headers={"Host": host} if host else {})
    started = time.monotonic()
    status, error = None, None
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except Exception as exc:  # noqa: BLE001 (no answer at all)
        reason = getattr(exc, "reason", exc)
        error = _short(reason) if isinstance(reason, BaseException) else str(reason)[:200]
    return {"status": status, "error": error, "seconds": round(time.monotonic() - started, 3)}


def probe_text(probe: dict) -> str:
    if probe.get("status") is not None:
        return f"HTTP {probe['status']}"
    return f"no answer ({probe.get('error') or 'unknown'}, {probe.get('seconds')} s)"


# ====================================================================================================================
# check: inside the venue (Django, this branch's chat_nextseek, the live graph)
# ====================================================================================================================

TOOL_PROBE = "MATCH (s:T_TIS) RETURN count(s) AS n"
RENDER_SETS = (("TIS", "D.SEQ", "A.VCF"), ("PAT", "PAV"))
CATALOG_STATEMENTS = ("META", "INDEX", "GUARD", "VOCAB_INVESTIGATIONS", "VOCAB_PROJECTS", "VOCAB_STUDIES",
                      "VOCAB_PUBLISHED", "VOCAB_EDGES")
VOCAB_FIELDS = ("investigation_titles", "project_titles", "study_titles", "published_studies", "assay_titles",
                "protocol_titles", "assay_connections")
# A question that opens only the always-sent blocks, and one that opens every keyword-gated block.
VOCAB_QUESTION_PLAIN = "How many tissue samples are in the database?"
VOCAB_QUESTION_EVERY = ("Which study, paper or publication (DOI, PMID) used this assay's sequencing data, and which "
                        "protocol method?")
ENDPOINT_PATH = "/nextseek_api/"
EXPECTED_WORKERS = 2


@contextlib.contextmanager
def quiet():
    """Swallow what the libraries print (the config's start-up chatter names hosts and users)."""
    sink = io.StringIO()
    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
        yield sink


def _short(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {' '.join(str(exc).split())[:200]}"


def _writable(path: Path) -> bool:
    try:
        fd, probe = tempfile.mkstemp(dir=path, prefix=".venue-rw-probe-")
    except OSError:
        return False
    os.close(fd)
    os.unlink(probe)
    return True


def _iso(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


class _Report:
    def __init__(self):
        self.checks: list[dict] = []
        self.measurements: dict = {}
        self.started = time.time()

    def add(self, name: str, ok, detail="") -> None:
        self.checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})

    def finish(self, out: Path | None) -> int:
        result, first = verdict(self.checks)
        doc = {"verdict": result, "first_failure": first, "started_at": _iso(self.started),
               "finished_at": _iso(time.time()), "checks": self.checks, "measurements": self.measurements}
        if out is not None:
            write_private(out, json.dumps(doc, indent=2, default=str) + "\n")
        if any(c["name"] == "overrides" and c["ok"] for c in self.checks):
            print("overrides-ok")
        for check in self.checks:
            print(f"{'ok  ' if check['ok'] else 'FAIL'}  {check['name']}" + (f": {check['detail']}"
                                                                           if check["detail"] else ""))
        if out is not None:
            print(f"report: {out}")
        print("PASS" if result == "PASS" else f"FAIL: {first}")
        return 0 if result == "PASS" else 1


def _check_process(report: _Report) -> None:
    m = report.measurements
    differ = compare_env(os.environ, OVERRIDES)
    report.add("overrides", not differ, f"{len(OVERRIDES) - len(differ)} of {len(OVERRIDES)} match"
               + (f"; differ: {', '.join(differ)}" if differ else ""))
    stray = [name for name in MUST_BE_UNSET if os.environ.get(name)]
    report.add("router_files_unset", not stray, f"set: {', '.join(stray)}" if stray else "")
    report.add("no_docker_socket", not os.path.exists("/var/run/docker.sock"))
    m["env_names_pointing_at_app"] = sorted(n for n, v in os.environ.items() if v.startswith("/app/"))
    try:
        sha = Path("/src/SNAPSHOT").read_text(encoding="utf-8").strip()
    except OSError:
        sha = ""
    m["snapshot_sha"] = sha or None
    report.add("snapshot", re.fullmatch(r"[0-9a-f]{40}", sha) is not None, sha[:12] or "no /src/SNAPSHOT")
    report.add("src_read_only", not _writable(Path("/src")))
    m["image_id"] = os.environ.get("GS_VENUE_IMAGE_ID") or None
    memory = gunicorn_memory(Path("/proc"))  # at rest: before this check sends a request
    m["gunicorn"] = memory
    report.add("gunicorn_workers", memory["workers"] == EXPECTED_WORKERS,
               f"{memory['workers']} workers at rest, RSS MiB {memory['worker_rss_mib']}")
    by_ip = http_probe(VENUE_URL + ENDPOINT_PATH)
    by_name = http_probe(VENUE_URL + ENDPOINT_PATH, host="localhost:8000")
    m["endpoint"] = {"path": ENDPOINT_PATH, "as_127": by_ip, "as_localhost": by_name}
    report.add("endpoint", answered(by_ip["status"]) and answered(by_name["status"]),
               f"{probe_text(by_ip)} as 127.0.0.1, {probe_text(by_name)} as localhost")


def _check_django(report: _Report):
    try:
        with quiet():
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
            import django  # noqa: PLC0415 (only inside the venue)

            django.setup()
            from django.conf import settings  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        report.add("django", False, _short(exc))
        return None
    report.add("django", True, "settings loaded")
    config = getattr(settings, "NEXTSEEK_CHAT_CONFIG", None)
    report.add("chat_config", type(config).__name__ == "ChatConfig", type(config).__name__)
    report.add("no_prod_config", getattr(settings, "NEXTSEEK_CHAT_CONFIG_PROD", None) is None)
    projects = getattr(settings, PROJECTS_NAME, None) or ()
    report.add("participating_projects", len(projects) > 0, f"{len(projects)} projects")
    report.add("allowed_hosts", list(getattr(settings, "ALLOWED_HOSTS", None) or []) == ["127.0.0.1", "localhost"])
    if config is None:
        return None
    report.add("base_url", getattr(config, "NEXTSEEK_BASE_URL", None) == VENUE_URL)
    outputs, logs = str(getattr(config, "OUTPUTS_DIR", "")), str(getattr(config, "LOG_DIR", ""))
    report.add("outputs_and_logs", outputs.startswith("/venue/outputs") and logs.startswith("/venue/logs"))
    report.add("luria_off", not getattr(config, "LURIA_ENV_COMPLETE", False))
    with quiet():
        import chat_nextseek  # noqa: PLC0415
        from chat_nextseek import graph_catalog, graph_context  # noqa: PLC0415
        from chat_nextseek.helpers.tools import neo4j as neo4j_tool  # noqa: PLC0415
    modules = {"chat_nextseek": chat_nextseek, "graph_catalog": graph_catalog, "graph_context": graph_context,
               "helpers.tools.neo4j": neo4j_tool}
    stray = [name for name, module in modules.items() if not str(getattr(module, "__file__", "")).startswith("/src/")]
    report.add("code_origin", not stray, f"not from /src: {', '.join(stray)}" if stray else "chat_nextseek from /src")
    try:
        import importlib.util  # noqa: PLC0415

        report.measurements["baml_router_client"] = (
            importlib.util.find_spec("dmac_assistant.router.baml_client") is not None)
    except Exception:  # noqa: BLE001
        report.measurements["baml_router_client"] = False
    return config


def _time_statement(driver, database: str, statement: str, timeout: float) -> dict:
    """Run one catalog statement in a READ transaction with ``timeout``; its time and row count."""
    from neo4j import READ_ACCESS, unit_of_work  # noqa: PLC0415

    @unit_of_work(timeout=timeout)
    def work(tx):
        return sum(1 for _ in tx.run(statement, {}))

    started = time.monotonic()
    try:
        with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
            rows = session.execute_read(work)
    except Exception as exc:  # noqa: BLE001
        elapsed = time.monotonic() - started
        code = str(getattr(exc, "code", "") or "")
        return {"seconds": round(elapsed, 3), "rows": None, "ok": False,
                "timed_out": "TimedOut" in code or elapsed >= timeout - 0.5, "error": _short(exc)}
    return {"seconds": round(time.monotonic() - started, 3), "rows": rows, "ok": True, "timed_out": False}


def _check_catalog(report: _Report, config, graph_catalog):
    graph_catalog.reset_cache()
    started = time.monotonic()
    try:
        with quiet():
            snapshot = graph_catalog.get_snapshot(config)
    except Exception as exc:  # noqa: BLE001 (CatalogUnavailable: the graph agent would use the fallback)
        report.add("catalog_live", False, f"the fallback would be used: {_short(exc)}")
        return None
    seconds = round(time.monotonic() - started, 3)
    state = graph_catalog.cache_state(config)
    report.measurements["catalog"] = {
        "state": state.get("state"),
        "schema_version": getattr(snapshot, "schema_version", graph_catalog.SCHEMA_VERSION),
        "min_schema_version": graph_catalog.SCHEMA_VERSION,
        "catalog_hash": snapshot.catalog_hash, "synced_at": snapshot.synced_at, "has_usage": snapshot.has_usage,
        "types": len(snapshot.index), "types_deprecated": sum(1 for r in snapshot.index if r.deprecated),
        "seconds_cold": seconds}
    report.add("catalog_live", state.get("state") == "live",
               f"schema {getattr(snapshot, 'schema_version', graph_catalog.SCHEMA_VERSION)} "
               f"(reader accepts {graph_catalog.SCHEMA_VERSION} or later), hash {snapshot.catalog_hash[:12]}, "
               f"{len(snapshot.index)} types, read in {seconds} s")
    return snapshot


def _check_timings(report: _Report, config, graph_catalog, remeasure_timeout: float) -> None:
    timeout = graph_catalog.QUERY_TIMEOUT_S
    database = str(getattr(config, "NEO4J_DATABASE", None) or "neo4j")
    timings: dict[str, dict] = {}
    report.measurements["catalog_timings"] = {"timeout_s": timeout, "statements": timings}
    driver = None
    try:
        with quiet():
            driver = graph_catalog._make_driver(config)
        for name in CATALOG_STATEMENTS:
            statement = getattr(graph_catalog, name, None)
            if not statement:
                continue
            timing = _time_statement(driver, database, statement, timeout)
            if timing["timed_out"] and remeasure_timeout > timeout:  # how long it really takes
                timing["remeasured"] = _time_statement(driver, database, statement, remeasure_timeout)
            timings[name] = timing
    except Exception as exc:  # noqa: BLE001
        report.add("catalog_timings", False, _short(exc))
        return
    finally:
        if driver is not None:
            with contextlib.suppress(Exception):
                driver.close()
    slow = [name for name, t in timings.items() if not t["ok"]]
    if slow:
        parts = []
        for name in slow:
            t = timings[name]
            text = f"{name} ({t['seconds']} s{', timed out' if t['timed_out'] else ''}"
            again = t.get("remeasured")
            if again:
                text += (f"; {again['seconds']} s with a {remeasure_timeout:g} s timeout" if again["ok"]
                         else f"; still failing with a {remeasure_timeout:g} s timeout")
            parts.append(text + ")")
        detail = f"failed or over the {timeout} s catalog timeout: " + ", ".join(parts)
        first_error = next((timings[n]["error"] for n in slow if timings[n].get("error")), None)
        if first_error:
            detail += f"; first error: {first_error}"
    else:
        detail = ", ".join(f"{name} {t['seconds']} s" for name, t in timings.items())
    report.add("catalog_timings", bool(timings) and not slow, detail)


def _check_renderings(report: _Report, config, snapshot, graph_catalog, graph_context) -> None:
    budget = graph_context.BUDGET_BYTES
    with quiet():
        report.measurements["context_parts"] = {
            "structure_bytes": len(graph_context.load_structure().encode("utf-8")),
            "index_bytes": len(graph_context.render_type_index(snapshot.index).encode("utf-8"))}

    def get(codes):
        return graph_catalog.get_type_details(config, codes)

    def render(snap, details):
        return graph_context.render_graph_context(snap, details)

    results = []
    for label, codes in [("named", codes) for codes in RENDER_SETS] + [("largest", tuple(largest_types(snapshot)))]:
        started = time.monotonic()
        try:
            with quiet():
                result = measure_rendering(label, codes, get, render, snapshot, budget)
        except Exception as exc:  # noqa: BLE001
            result = {"label": label, "types": list(codes), "ok": False, "error": _short(exc)}
        result["seconds"] = round(time.monotonic() - started, 3)
        results.append(result)
        if "error" in result:
            detail = result["error"]
        else:
            k = "names only" if result["k"] == 0 else result["k"]
            detail = f"{result['bytes']:,} of {budget:,} bytes, K {k}, {result['sections']} sections"
            if result["missing"]:
                detail += f"; not in the catalog: {', '.join(result['missing'])}"
            if result["omitted"]:
                detail += f"; left out for the budget: {', '.join(result['omitted'])}"
        report.add(f"render {' + '.join(codes) or 'no types'} ({label})", result["ok"], detail)
    report.measurements["renderings"] = results


def _check_vocabulary(report: _Report, config, graph_catalog, graph_context) -> None:
    started = time.monotonic()
    try:
        with quiet():
            vocab = graph_catalog.get_vocabulary(config)
    except Exception as exc:  # noqa: BLE001
        report.add("vocabulary", False, _short(exc))
        return
    seconds = round(time.monotonic() - started, 3)
    failed = list(graph_catalog.cache_state(config).get("vocabulary_failed") or [])
    counts = {name: len(getattr(vocab, name, ()) or ()) for name in VOCAB_FIELDS}
    plain = len(graph_context.render_vocabulary(vocab, VOCAB_QUESTION_PLAIN).encode("utf-8"))
    every = len(graph_context.render_vocabulary(vocab, VOCAB_QUESTION_EVERY).encode("utf-8"))
    report.measurements["vocabulary"] = {"counts": counts, "failed": failed, "seconds": seconds,
                                         "bytes_plain_question": plain, "bytes_every_block": every}
    detail = (", ".join(f"{n} {name}" for name, n in counts.items())
              + f"; {plain:,} bytes for a plain question, {every:,} with every block; {seconds} s")
    if failed:
        detail = f"failed sources, sent empty: {', '.join(failed)}; " + detail
    report.add("vocabulary", not failed, detail)


def _check_tool(report: _Report, config, neo4j_tool) -> None:
    started = time.monotonic()
    with quiet():
        result = neo4j_tool.tool_neo4j_query(config, TOOL_PROBE)
    seconds = round(time.monotonic() - started, 3)
    rows = result.get("data") or []
    n = rows[0].get("n") if result.get("ok") and rows and isinstance(rows[0], dict) else None
    report.measurements["tool_query"] = {"statement": TOOL_PROBE, "ok": bool(result.get("ok")), "tis_count": n,
                                         "seconds": seconds}
    ok = bool(result.get("ok")) and isinstance(n, int)
    report.add("tool_query", ok, f"TIS count {n:,}, {seconds} s" if ok
               else " ".join(str(result.get("error") or "no rows").split())[:200])


def _check_graph(report: _Report, config, remeasure_timeout: float) -> None:
    import neo4j  # noqa: PLC0415

    from chat_nextseek import graph_catalog, graph_context  # noqa: PLC0415
    from chat_nextseek.helpers.tools import neo4j as neo4j_tool  # noqa: PLC0415

    calls: list = []
    with read_spy(neo4j.GraphDatabase, calls):
        snapshot = _check_catalog(report, config, graph_catalog)
        _check_timings(report, config, graph_catalog, remeasure_timeout)
        if snapshot is not None:
            _check_renderings(report, config, snapshot, graph_catalog, graph_context)
            _check_vocabulary(report, config, graph_catalog, graph_context)
        _check_tool(report, config, neo4j_tool)
    kinds = Counter(call["kind"] for call in calls)
    modes = Counter(call["access_mode"] for call in calls)
    report.measurements["transactions"] = {"kinds": dict(kinds), "session_modes": dict(modes)}
    report.add("reads_are_read", reads_only(calls),
               ", ".join(f"{n} {kind}" for kind, n in sorted(kinds.items())) or "no transaction observed")


def run_check(out: Path | None, remeasure_timeout: float = 120.0) -> int:
    """The venue check (plan task T8): every result is in ``out``; prints overrides-ok, one line per check, PASS."""
    report = _Report()
    try:
        _check_process(report)
        config = _check_django(report)
        if config is not None:
            _check_graph(report, config, remeasure_timeout)
    except Exception as exc:  # noqa: BLE001 (the report says where the check itself broke)
        report.add("check", False, f"the check broke: {_short(exc)}")
    return report.finish(out)


# ====================================================================================================================
# selftest: the pure parts and the shell script, with no container and no network
# ====================================================================================================================

_TEMPLATE_PATH = HERE.parent.parent / "startup" / "templates" / "local_settings.py.template"
_SENTINELS = ("sentinel-operator-secret", "sentinel-db-secret", "sentinel-neo4j-secret", "sentinel-demo-secret",
              "sentinel-django-secret")

_OPERATOR_SETTINGS = '''import os
OPERATOR_ONLY = "sentinel-operator-secret-1"
ASSISTANT_PARTICIPATING_PROJECTS = set([
    "3", "14",
    "2",
])
_PROD_OVERRIDES = {"NEO4J_PASSWORD": "sentinel-operator-secret-2"}
'''
_DB_ENV = ('# the database\nMYSQL_HOST="db"\nMYSQL_USER="seek_db_user"\nMYSQL_PASSWORD="sentinel-db-secret"\n'
           'NEXTSEEK_MYSQL_DATABASE="dmac"\n')
_NEXTSEEK_ENV = ('SEEK_HOST="seek"\nNEXTSEEK_HOSTNAME="127.0.0.1:${NEXTSEEK_PORT:-8000}"\n'
                 'DJANGO_SECRET_KEY="sentinel-django-secret"\n'
                 'NEXTSEEK_NEO4J_HOST="neo4j"\nNEXTSEEK_NEO4J_PASSWORD="sentinel-neo4j-secret"\n'
                 'NEO4J_URI="neo4j://$NEXTSEEK_NEO4J_HOST"\nNEO4J_PASSWORD=$NEXTSEEK_NEO4J_PASSWORD\n'
                 'SESSION_DB_HOST=$MYSQL_HOST\nLOG_DIR="/app/logs"\nGCP_API_KEY="SET_IN_LOCAL_ENV"\n')
# Every name compose_env requires, with inert values.
_REQUIRED = ("MYSQL_HOST=db\nMYSQL_USER=u\nMYSQL_PASSWORD=p\nNEO4J_URI=neo4j://neo4j\nNEO4J_PASSWORD=n\n"
             "SEEK_HOST=seek\nNEXTSEEK_HOSTNAME=127.0.0.1:8000\nDJANGO_SECRET_KEY=s\n"
             "GCP_API_KEY=k\nAWS_BEARER_TOKEN_BEDROCK=t\n")

_FAKE_DOCKER = """#!/bin/sh
# The selftest's docker: records every call and starts nothing.
printf '%s\\n' "$*" >> "$FAKE_DOCKER_LOG"
case "$1" in
  inspect) if [ "${FAKE_DOCKER_RUNNING:-0}" = 1 ]; then echo true; exit 0; fi; exit 1 ;;
esac
exit 97
"""


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class PureTests(unittest.TestCase):
    """The helper's pure functions."""

    def template(self) -> str:
        return _TEMPLATE_PATH.read_text(encoding="utf-8")

    # --- settings ---------------------------------------------------------------------------------------------------

    def test_settings_take_only_the_projects_line(self):
        text = render_settings(self.template(), _OPERATOR_SETTINGS)
        ast.parse(text)
        self.assertIn("ASSISTANT_PARTICIPATING_PROJECTS = set(['2', '3', '14'])", text)
        self.assertNotIn('set(["1"])', text)
        self.assertIn("NEXTSEEK_CHAT_CONFIG = ChatConfig()", text)
        self.assertEqual(active_prod_overrides(text), [])
        for sentinel in _SENTINELS:
            self.assertNotIn(sentinel, text)

    def test_settings_accept_literal_forms(self):
        for rhs, want in [('{"2", "3"}', ["2", "3"]), ("frozenset({4, 2})", [2, 4]), ('["5"]', ["5"]),
                          ('("6",)', ["6"]), ('set(("7", "7"))', ["7"])]:
            with self.subTest(rhs=rhs):
                self.assertEqual(participating_projects(f"ASSISTANT_PARTICIPATING_PROJECTS = {rhs}\n"), want)

    def test_settings_use_the_last_top_level_assignment(self):
        text = "ASSISTANT_PARTICIPATING_PROJECTS = {'1'}\nASSISTANT_PARTICIPATING_PROJECTS = {'9'}\n"
        self.assertEqual(participating_projects(text), ["9"])

    def test_settings_refuse_what_is_not_a_literal_list_of_ids(self):
        for text in ["X = 1\n", "ASSISTANT_PARTICIPATING_PROJECTS = set(os.environ['P'])\n",
                     "ASSISTANT_PARTICIPATING_PROJECTS = set()\n", "ASSISTANT_PARTICIPATING_PROJECTS = 'x'\n",
                     "ASSISTANT_PARTICIPATING_PROJECTS = [1.5]\n", "ASSISTANT_PARTICIPATING_PROJECTS = [True]\n"]:
            with self.subTest(text=text), self.assertRaises(VenueError):
                participating_projects(text)

    def test_settings_errors_name_the_line_not_the_text(self):
        text = 'A = 1\nASSISTANT_PARTICIPATING_PROJECTS = set(["sentinel-operator-secret-3"\n'
        with self.assertRaises(VenueError) as ctx:
            participating_projects(text)
        self.assertIn("line", str(ctx.exception))
        self.assertNotIn("sentinel", str(ctx.exception))

    def test_settings_refuse_an_active_prod_block(self):
        template = self.template().replace('"NEO4J_URI": None', '"NEO4J_URI": "bolt://elsewhere"')
        self.assertNotEqual(template, self.template())
        with self.assertRaises(VenueError):
            render_settings(template, _OPERATOR_SETTINGS)

    # --- env files --------------------------------------------------------------------------------------------------

    def test_env_strips_quotes_and_expands_like_compose(self):
        env, notes = compose_env([_DB_ENV, _NEXTSEEK_ENV + "AWS_BEARER_TOKEN_BEDROCK=t\n"])
        self.assertEqual(env["MYSQL_HOST"], "db")
        self.assertEqual(env["NEO4J_URI"], "neo4j://neo4j")
        self.assertEqual(env["NEO4J_PASSWORD"], "sentinel-neo4j-secret")
        self.assertEqual(env["SESSION_DB_HOST"], "db")
        self.assertEqual(env["NEXTSEEK_HOSTNAME"], "127.0.0.1:8000")
        self.assertNotIn("LOG_DIR", env)  # an override: the -e flag sets it
        self.assertTrue(any("GCP_API_KEY" in note for note in notes))
        for note in notes:
            for sentinel in _SENTINELS:
                self.assertNotIn(sentinel, note)

    def test_env_quoting_rules(self):
        text = ('export A=plain # a comment\nB=\'single $A\'\nC="esc \\"q\\" \\$A"\nD=${UNSET_X:-fallback}\n'
                'E=$UNSET_Y\nF="x${A}y"\nG=a$$b\nH="  padded  "\nI=\n')
        env, notes = compose_env([_REQUIRED, text])
        self.assertEqual(env["A"], "plain")
        self.assertEqual(env["B"], "single $A")
        self.assertEqual(env["C"], 'esc "q" $A')
        self.assertEqual(env["D"], "fallback")
        self.assertEqual(env["E"], "")
        self.assertEqual(env["F"], "xplainy")
        self.assertEqual(env["G"], "a$b")
        self.assertEqual(env["H"], "  padded  ")
        self.assertEqual(env["I"], "")
        self.assertTrue(any("UNSET_Y" in note and "E" in note for note in notes))

    def test_env_lookup_wins_and_later_files_win(self):
        env, _ = compose_env([_REQUIRED + "K=1\nX=$K\n", "K=2\n"], lookup_env={"K": "dot"})
        self.assertEqual(env["X"], "dot")
        self.assertEqual(env["K"], "2")

    def test_env_refuses_line_breaks_and_missing_required_names(self):
        for text in [_REQUIRED + 'K="a\\nb"\n', _REQUIRED + 'K="a\nb"\n', _REQUIRED + "K=${NOPE:?must}\n",
                     "MYSQL_HOST=db\n", _REQUIRED + 'K="unterminated\n',
                     _REQUIRED.replace("NEXTSEEK_HOSTNAME=127.0.0.1:8000\n", ""),
                     _REQUIRED.replace("SEEK_HOST=seek\n", "")]:
            with self.subTest(text=text[-24:]), self.assertRaises(VenueError) as ctx:
                compose_env([text])
            self.assertNotIn("sentinel", str(ctx.exception))

    def test_env_drops_the_router_file_overrides(self):
        env, notes = compose_env([_REQUIRED + "DMAC_ROUTE_CAPABILITIES_FILE=/app/x.json\n"])
        self.assertNotIn("DMAC_ROUTE_CAPABILITIES_FILE", env)
        self.assertTrue(any("DMAC_ROUTE_CAPABILITIES_FILE" in note for note in notes))

    def test_env_file_text_and_overrides(self):
        self.assertEqual(render_env_file({"A": "1 2", "B": ""}), "A=1 2\nB=\n")
        lines = overrides_lines()
        for want in ("NEXTSEEK_EVAL_PARSER_FORCE=1", "NEXTSEEK_POSTERIOR_ROUTING_ENABLED=0", "LURIAKEY=",
                     "LOG_DIR=/venue/logs", "DJANGO_ALLOWED_HOSTS=127.0.0.1 localhost",
                     "NEXTSEEK_INTERNAL_BASE_URL=http://127.0.0.1:8000"):
            self.assertIn(want, lines)

    # --- progress ---------------------------------------------------------------------------------------------------

    def _arms_run(self, root: Path) -> Path:
        run = root / "full-a"
        (run / "graph").mkdir(parents=True)
        (run / "api").mkdir()
        q = lambda status, *extra: {"status": status, "task_ids": [], "elapsed_s": 1.0}  # noqa: E731
        # The layout runner.run_arms writes: every cases-file question up front, arms filled in as they run.
        doc = {"run_meta": {"git_sha": "a" * 40, "arms": ["graph", "api"],
                            "preflight": {"passed_at": "2026-09-15T10:00:00Z", "git_sha": "a" * 40}},
               "progress": {"state": "running", "turns_driven": 5, "max_turns": 60, "questions": 4,
                            "updated_at": "2026-09-15T10:05:00Z"},
               "questions": [
                   {"id": "q1", "family": "sample_search", "first_arm": "graph",
                    "arms": {"graph": q("passed"), "api": q("failed")}},
                   {"id": "q2", "family": "sample_search", "first_arm": "api",
                    "arms": {"api": q("error"), "graph": q("error")}},
                   {"id": "q3", "family": "harmonization", "first_arm": "graph", "arms": {"graph": q("passed")}},
                   {"id": "q4", "family": "harmonization", "first_arm": "api", "arms": {}}]}
        (run / "arms.json").write_text(json.dumps(doc))
        (run / "graph" / "manifest.json").write_text(json.dumps({"entries": [{"id": "q2", "outage": True}]}))
        (run / "api" / "manifest.json").write_text(json.dumps({"entries": [{"id": "q2", "outage": False}]}))
        (root / "full-a.console.log").write_text("".join(f"line {i}\n" for i in range(20)))
        return run

    def test_progress_counts_each_arm(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._arms_run(Path(tmp))
            text = "\n".join(summarize_run(run, running="yes", log=run.parent / "full-a.console.log", tail=3))
        self.assertIn("preflight: passed", text)
        self.assertIn("graph: 3 of 4 questions done", text)
        self.assertIn("api: 2 of 4 questions done", text)
        graph_line = next(line for line in text.splitlines() if line.startswith("graph:"))
        api_line = next(line for line in text.splitlines() if line.startswith("api:"))
        self.assertIn("0 errors, 1 outage", graph_line)
        self.assertIn("1 error, 0 outages", api_line)
        self.assertIn("last question run: q3", text)
        self.assertIn("run state: running, 5 turns driven (cap 60)", text)
        self.assertIn("harness: running", text)
        self.assertIn("line 19", text)
        self.assertNotIn("line 5\n", text + "\n")

    def test_progress_states(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = self._arms_run(Path(tmp))
            self.assertIn("harness: stopped before the run finished", "\n".join(summarize_run(run, running="no")))
            self.assertIn("harness: unknown", "\n".join(summarize_run(run, running="unknown")))
            doc = json.loads((run / "arms.json").read_text())
            doc["progress"]["state"] = "max_turns"
            (run / "arms.json").write_text(json.dumps(doc))
            done = "\n".join(summarize_run(run, running="no"))
            self.assertIn("harness: done", done)
            self.assertIn("--resume", done)

    def test_progress_reads_a_plain_manifest_and_a_missing_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp) / "plain"
            run.mkdir()
            (run / "manifest.json").write_text(json.dumps({"entries": [
                {"id": "a", "status": "passed"}, {"id": "b", "status": "error", "outage": True}]}))
            text = "\n".join(summarize_run(run, running="no"))
            self.assertIn("2 cases done", text)
            self.assertIn("1 outage", text)
            self.assertIn("no run directory", "\n".join(summarize_run(Path(tmp) / "nothing")))

    # --- the check's parts ------------------------------------------------------------------------------------------

    def test_compare_env_returns_names_only(self):
        diff = compare_env({"A": "1", "B": "sentinel-db-secret", "L": ""}, {"A": "1", "B": "y", "C": "z", "L": ""})
        self.assertEqual(diff, ["B", "C"])
        self.assertEqual(compare_env({}, {"L": ""}), [])

    def test_gunicorn_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc = Path(tmp)

            def add(pid, ppid, cmd, rss):
                d = proc / str(pid)
                d.mkdir()
                (d / "cmdline").write_bytes(b"\0".join(part.encode() for part in cmd) + b"\0")
                (d / "status").write_text(f"Name:\tx\nPPid:\t{ppid}\nVmRSS:\t  {rss} kB\n")

            # tini carries the gunicorn path as an argument of the wrapper shell it started
            add(1, 0, ["/sbin/docker-init", "--", "sh", "-c", 'umask 077; exec "$@"', "venue-gunicorn",
                       "/app/.venv/bin/gunicorn", "dmac.wsgi"], 1000)
            add(7, 1, ["/app/.venv/bin/python", "/app/.venv/bin/gunicorn", "dmac.wsgi"], 100000)
            add(8, 7, ["/app/.venv/bin/python", "/app/.venv/bin/gunicorn", "dmac.wsgi"], 300000)
            add(9, 7, ["gunicorn: worker [dmac.wsgi]"], 400000)
            add(20, 1, ["/app/.venv/bin/python", "scripts/graph_search/nessie_venue_check.py", "check"], 50000)
            (proc / "self").mkdir()
            got = gunicorn_memory(proc)
        self.assertEqual(got["masters"], 1)
        self.assertEqual(got["workers"], 2)
        self.assertEqual(got["master_rss_mib"], [97.7])
        self.assertEqual(got["worker_rss_mib"], [293.0, 390.6])

    def test_http_probe(self):
        import http.server  # noqa: PLC0415
        import threading  # noqa: PLC0415

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                self.send_response(400 if self.headers.get("Host", "").startswith("bad") else 404)
                self.end_headers()

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{server.server_address[1]}/x"
        try:
            found = http_probe(url)
            self.assertEqual(found["status"], 404)
            self.assertTrue(answered(found["status"]))
            refused = http_probe(url, host="bad-host:1")
            self.assertEqual(refused["status"], 400)
            self.assertFalse(answered(refused["status"]))
        finally:
            server.shutdown()
            server.server_close()
        gone = http_probe(url, timeout=2)
        self.assertIsNone(gone["status"])
        self.assertTrue(gone["error"])
        self.assertIn("no answer", probe_text(gone))
        self.assertEqual(probe_text(found), "HTTP 404")

    def test_context_shape(self):
        text = ("## Nodes\n### not a section\n## Resolved sample types: TIS, PAT (the 15 most-filled attributes in "
                "full, then the rest by name; per attribute: ...)\n\n### TIS :T_TIS\n\n### PAT :T_PAT\n\n"
                "Left out to fit the context budget: A.VCF (see the type index).\n")
        self.assertEqual(context_shape(text), {"bytes": len(text.encode()), "k": 15, "sections": 2,
                                               "omitted": ["A.VCF"]})
        names = "## Resolved sample types: TIS (attribute names only; per ...)\n### TIS\n"
        self.assertEqual(context_shape(names)["k"], 0)
        self.assertIsNone(context_shape("## Nodes\n")["k"])

    def test_measure_rendering(self):
        details = {"TIS": SimpleNamespace(title="TIS"), "PAT": SimpleNamespace(title="PAT")}
        get = lambda codes: [details[c] for c in codes if c in details]  # noqa: E731
        small = lambda snapshot, ds: "## Resolved sample types: x (the 25 most-filled)\n" + "### s\n" * len(ds)  # noqa: E731
        ok = measure_rendering("named", ("TIS", "PAT"), get, small, None, 1000)
        self.assertTrue(ok["ok"])
        self.assertEqual((ok["k"], ok["sections"], ok["missing"]), (25, 2, []))
        missing = measure_rendering("named", ("TIS", "NOPE"), get, small, None, 1000)
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["missing"], ["NOPE"])
        big = measure_rendering("named", ("TIS",), get, lambda s, ds: "x" * 2000, None, 1000)
        self.assertFalse(big["ok"])

    def test_largest_types(self):
        row = lambda title, n, deprecated=False: SimpleNamespace(  # noqa: E731
            title=title, attributes_with_values=n, deprecated=deprecated)
        snapshot = SimpleNamespace(index=(row("A", 5), row("B", 50), row("C", 50), row("D", 900, True), row("E", 7)))
        self.assertEqual(largest_types(snapshot), ["B", "C", "E"])

    def test_read_spy_records_transaction_kinds_and_restores(self):
        class Session:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute_read(self, fn, *args):
                return fn("tx", *args)

            def run(self, statement):
                return None

        class Driver:
            def session(self, **kw):
                return Session()

            def close(self):
                pass

        class GraphDatabase:
            @classmethod
            def driver(cls, uri, **kw):
                return Driver()

        calls: list = []
        with read_spy(GraphDatabase, calls):
            with GraphDatabase.driver("bolt://x", auth=("u", "p")).session(database="neo4j") as s:
                self.assertEqual(s.execute_read(lambda tx: 7), 7)
        self.assertEqual([c["kind"] for c in calls], ["execute_read"])
        self.assertTrue(reads_only(calls))
        more: list = []
        with read_spy(GraphDatabase, more):
            with GraphDatabase.driver("bolt://x").session(default_access_mode="READ") as s:
                s.run("MATCH (n) RETURN n")
        self.assertEqual(more, [{"kind": "run", "access_mode": "READ"}])
        self.assertFalse(reads_only(more))
        self.assertFalse(reads_only([]))
        self.assertIsInstance(vars(GraphDatabase)["driver"], classmethod)

    def test_verdict(self):
        self.assertEqual(verdict([{"name": "a", "ok": True}]), ("PASS", None))
        self.assertEqual(verdict([{"name": "a", "ok": True}, {"name": "b", "ok": False}, {"name": "c", "ok": False}]),
                         ("FAIL", "b"))
        self.assertEqual(verdict([]), ("FAIL", "no checks ran"))

    def test_write_private(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.json"
            write_private(path, "{}\n")
            self.assertEqual(path.read_text(), "{}\n")
            self.assertEqual(_mode(path), 0o600)


class ScriptTests(unittest.TestCase):
    """nessie_venue.sh, run from a throwaway git repository with a fake docker that starts nothing."""

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="nessie-venue-selftest-")
        root = Path(cls._tmp.name)
        cls.root = root
        repo = root / "repo"
        (repo / "scripts" / "graph_search").mkdir(parents=True)
        (repo / "startup" / "templates").mkdir(parents=True)
        shutil.copy2(SCRIPT, repo / "scripts" / "graph_search" / SCRIPT.name)
        shutil.copy2(Path(__file__).resolve(), repo / "scripts" / "graph_search" / Path(__file__).name)
        shutil.copy2(_TEMPLATE_PATH, repo / "startup" / "templates" / _TEMPLATE_PATH.name)
        git_env = {**os.environ, "GIT_AUTHOR_NAME": "selftest", "GIT_AUTHOR_EMAIL": "selftest",
                   "GIT_COMMITTER_NAME": "selftest", "GIT_COMMITTER_EMAIL": "selftest"}
        git = ["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null"]
        for args in (["init", "-q"], ["add", "."], ["commit", "-q", "-m", "selftest"]):
            subprocess.run([*git, *args], cwd=repo, env=git_env, check=True, capture_output=True)
        cls.head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True,
                                  text=True).stdout.strip()
        cls.script = repo / "scripts" / "graph_search" / SCRIPT.name
        fakebin = root / "bin"
        fakebin.mkdir()
        (fakebin / "docker").write_text(_FAKE_DOCKER)
        (fakebin / "docker").chmod(0o755)
        cls.path = f"{fakebin}{os.pathsep}{os.environ.get('PATH', '')}"
        live = root / "live"
        (live / "dmac").mkdir(parents=True)
        (live / "docker").mkdir()
        (live / "dmac" / "local_settings.py").write_text(_OPERATOR_SETTINGS)
        (live / "docker" / "db.env").write_text(_DB_ENV)
        (live / "docker" / "nextseek.env").write_text(_NEXTSEEK_ENV + "AWS_BEARER_TOKEN_BEDROCK=\"t\"\n")
        cls.live = live

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def gswork(self) -> Path:
        return Path(tempfile.mkdtemp(dir=self.root, prefix="gswork-"))

    def run_script(self, gswork: Path, *args: str, **extra: str):
        env = {"PATH": self.path, "HOME": os.environ.get("HOME", "/tmp"), "LANG": "C.UTF-8",
               "GS_WORK": str(gswork), "NEXTSEEK_LIVE_CHECKOUT": str(self.live),
               "FAKE_DOCKER_LOG": str(gswork / "docker.log"), "GS_VENUE_MIN_GIB": "0", **extra}
        return subprocess.run(["bash", str(self.script), *args], env=env, capture_output=True, text=True,
                              timeout=180)

    def docker_calls(self, gswork: Path) -> list[str]:
        log = gswork / "docker.log"
        return log.read_text().splitlines() if log.exists() else []

    def prepare(self, gswork: Path):
        result = self.run_script(gswork, "prepare")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def assert_no_secret(self, *texts: str):
        for text in texts:
            for sentinel in _SENTINELS:
                self.assertNotIn(sentinel, text)

    def test_bash_syntax(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_shellcheck_when_installed(self):
        if shutil.which("shellcheck"):
            cmd = ["shellcheck"]
        elif shutil.which("uvx"):
            cmd = ["uvx", "--offline", "--from", "shellcheck-py", "shellcheck"]
        else:
            self.skipTest("shellcheck is not installed")
        probe = subprocess.run([*cmd, "--version"], capture_output=True, text=True)
        if probe.returncode != 0:
            self.skipTest("shellcheck is not installed")
        result = subprocess.run([*cmd, "-x", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_usage(self):
        gswork = self.gswork()
        self.assertEqual(self.run_script(gswork).returncode, 2)
        self.assertEqual(self.run_script(gswork, "nonsense").returncode, 2)
        self.assertEqual(self.docker_calls(gswork), [])

    def test_up_run_and_bg_refuse_in_the_benchmark_window(self):
        gswork = self.gswork()
        (gswork / ".gs-bench-running").touch()
        for args in (["up"], ["run", "p1", "--cases", "/venue/cases/pilot-a.json"], ["bg", "p1"],
                     ["--dry-run", "up"]):
            with self.subTest(args=args):
                result = self.run_script(gswork, *args, GS_DEMO_PASSWORD="sentinel-demo-secret")
                self.assertEqual(result.returncode, 3, result.stderr)
                self.assertIn("benchmark", result.stderr)
        self.assertEqual(self.docker_calls(gswork), [])

    def test_up_refuses_short_memory(self):
        gswork = self.gswork()
        result = self.run_script(gswork, "up", GS_VENUE_MIN_GIB="1000")
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("1000 GiB", result.stderr)
        self.assertEqual(self.docker_calls(gswork), [])

    def test_prepare_builds_a_private_snapshot(self):
        gswork = self.gswork()
        self.prepare(gswork)
        src = gswork / "nessie" / "venue" / "src"
        settings = src / "dmac" / "local_settings.py"
        self.assertEqual(_mode(src), 0o700)
        self.assertEqual(_mode(settings), 0o600)
        self.assertEqual((src / "SNAPSHOT").read_text().strip(), self.head)
        self.assertTrue((src / "schema_rag" / "duckdb").is_dir())
        self.assertTrue((src / "schema_rag" / "embedding_models").is_dir())
        text = settings.read_text()
        self.assertIn("ASSISTANT_PARTICIPATING_PROJECTS = set(['2', '3', '14'])", text)
        self.assertEqual(active_prod_overrides(text), [])
        self.assert_no_secret(text)
        for name in ("nessie", "nessie/venue", "nessie/venue/outputs", "nessie/venue/logs", "nessie/runs",
                     "nessie/cases", "nessie/truth", "nessie/questions"):
            self.assertEqual(_mode(gswork / name), 0o700, name)
        self.assertFalse((gswork / "nessie" / "venue" / "src.new").exists())
        self.assertTrue(all(call.startswith("inspect") for call in self.docker_calls(gswork)))

    def test_prepare_refuses_without_the_projects_line_and_keeps_the_old_snapshot(self):
        gswork = self.gswork()
        self.prepare(gswork)
        other = self.root / "live-no-projects"
        shutil.copytree(self.live, other)
        (other / "dmac" / "local_settings.py").write_text("OPERATOR_ONLY = 'sentinel-operator-secret-4'\n")
        result = self.run_script(gswork, "prepare", NEXTSEEK_LIVE_CHECKOUT=str(other))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ASSISTANT_PARTICIPATING_PROJECTS", result.stderr)
        self.assert_no_secret(result.stdout, result.stderr)
        self.assertEqual((gswork / "nessie" / "venue" / "src" / "SNAPSHOT").read_text().strip(), self.head)
        self.assertFalse((gswork / "nessie" / "venue" / "src.new").exists())

    def test_dry_run_up_prints_the_command_without_values(self):
        gswork = self.gswork()
        self.prepare(gswork)
        before = len(self.docker_calls(gswork))
        result = self.run_script(gswork, "--dry-run", "up")
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        for want in ("docker run -d --name gs-nessie-venue", "--env-file", ".venue.env", "--memory 6g",
                     "--memory-swap 6g", "127.0.0.1:8010:8000", "--network nextseek_default",
                     "NEXTSEEK_EVAL_PARSER_FORCE=1", "/src:ro", "nextseek-nextseek:latest", "--workers 2",
                     "--timeout 1200", "GS_HOST_UID="):
            self.assertIn(want, out)
        self.assertNotIn("/var/run/docker.sock", out)
        self.assert_no_secret(result.stdout, result.stderr)
        self.assertEqual(len(self.docker_calls(gswork)), before)
        self.assertFalse((gswork / "nessie" / "venue" / ".venue.env").exists())

    def test_dry_run_up_refuses_without_a_snapshot(self):
        gswork = self.gswork()
        result = self.run_script(gswork, "--dry-run", "up")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("prepare", result.stderr)

    def test_dry_run_bg_and_run_pass_the_password_by_name(self):
        gswork = self.gswork()
        result = self.run_script(gswork, "--dry-run", "bg", "full-a", "--cases", "/venue/cases/rest-a.json",
                                 "--arms", "graph", GS_DEMO_PASSWORD="sentinel-demo-secret")
        self.assertEqual(result.returncode, 0, result.stderr)
        for want in ("docker exec -d", "-e GS_DEMO_PASSWORD ", "--password-env GS_DEMO_PASSWORD",
                     "/venue/runs/full-a.console.log", "--out /venue/runs/full-a", "--tier full", "--user demo",
                     "--arms graph"):
            self.assertIn(want, result.stdout)
        result = self.run_script(gswork, "--dry-run", "run", "p1", "--max-turns", "0",
                                 GS_DEMO_PASSWORD="sentinel-demo-secret")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("docker exec -i", result.stdout)
        self.assertIn("--max-turns 0", result.stdout)
        self.assert_no_secret(result.stdout, result.stderr)
        self.assertEqual(self.docker_calls(gswork), [])

    def test_run_refuses_a_bad_name_and_a_missing_password(self):
        gswork = self.gswork()
        bad = self.run_script(gswork, "--dry-run", "run", "../x", GS_DEMO_PASSWORD="sentinel-demo-secret")
        self.assertNotEqual(bad.returncode, 0)
        nopass = self.run_script(gswork, "--dry-run", "run", "p1")
        self.assertNotEqual(nopass.returncode, 0)
        self.assertIn("GS_DEMO_PASSWORD", nopass.stderr)

    def test_progress_reads_arms_json_from_the_host_when_the_venue_is_down(self):
        gswork = self.gswork()
        PureTests._arms_run(None, gswork / "nessie" / "runs")
        result = self.run_script(gswork, "progress", "full-a")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("graph: 3 of 4 questions done", result.stdout)
        self.assertIn("harness: unknown", result.stdout)
        self.assertIn("line 19", result.stdout)
        self.assertTrue(all(call.startswith("inspect") for call in self.docker_calls(gswork)))

    def test_dry_run_down_check_stop_and_logs(self):
        gswork = self.gswork()
        self.prepare(gswork)
        before = len(self.docker_calls(gswork))
        down = self.run_script(gswork, "--dry-run", "down")
        self.assertEqual(down.returncode, 0, down.stderr)
        self.assertIn("docker rm -f gs-nessie-venue", down.stdout)
        self.assertIn("--network none", down.stdout)
        self.assertIn("--memory 256m", down.stdout)
        for args, want in ((["check"], "venue_check.json"), (["stop"], "pkill"), (["logs", "50"], "--tail 50"),
                           (["exec", "x.py"], "x.py")):
            with self.subTest(args=args):
                result = self.run_script(gswork, "--dry-run", *args)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(want, result.stdout)
        self.assertEqual(len(self.docker_calls(gswork)), before)


def _selftest() -> int:
    loader = unittest.defaultTestLoader
    suite = unittest.TestSuite([loader.loadTestsFromTestCase(PureTests), loader.loadTestsFromTestCase(ScriptTests)])
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


def _read(path: Path, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise VenueError(f"cannot read {what} ({type(exc).__name__})") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="nessie_venue_check.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("settings", help="render the venue's dmac/local_settings.py")
    p.add_argument("--template", type=Path, required=True)
    p.add_argument("--operator", type=Path, required=True, help="the live checkout's dmac/local_settings.py")
    p.add_argument("--out", type=Path, required=True)
    p = sub.add_parser("env", help="render the live checkout's compose env files for docker run --env-file")
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--lookup", type=Path, help="the live checkout's .env, for $VAR references")
    p.add_argument("files", type=Path, nargs="+")
    sub.add_parser("overrides", help="print the venue's -e overrides")
    p = sub.add_parser("progress", help="summarize a run directory")
    p.add_argument("run_dir", type=Path)
    p.add_argument("--log", type=Path)
    p.add_argument("--running", choices=("yes", "no", "unknown"), default="unknown")
    p.add_argument("--tail", type=int, default=8)
    p = sub.add_parser("check", help="the venue check (inside the venue)")
    p.add_argument("--out", type=Path)
    p.add_argument("--remeasure-timeout", type=float, default=120.0,
                   help="seconds for a second run of a catalog statement that timed out (0: none)")
    sub.add_parser("selftest", help="run this file's tests")
    args = parser.parse_args(argv)
    try:
        if args.command == "settings":
            operator = _read(args.operator, "the operator's dmac/local_settings.py")
            text = render_settings(_read(args.template, "the settings template"), operator)
            write_private(args.out, text)
            print(f"settings rendered: {PROJECTS_NAME} holds {len(participating_projects(operator))} projects")
        elif args.command == "env":
            lookup = dotenv_values(_read(args.lookup, "the .env")) if args.lookup else {}
            env, notes = compose_env([(f.name, _read(f, f.name)) for f in args.files], lookup)
            write_private(args.out, render_env_file(env))
            for note in notes:
                print(f"note: {note}", file=sys.stderr)
            print(f"env rendered: {len(env)} variables from {len(args.files)} files, then {len(OVERRIDES)} overrides")
        elif args.command == "overrides":
            print("\n".join(overrides_lines()))
        elif args.command == "progress":
            print("\n".join(summarize_run(args.run_dir, args.running, args.log, args.tail)))
        elif args.command == "check":
            return run_check(args.out, args.remeasure_timeout)
        elif args.command == "selftest":
            return _selftest()
    except VenueError as exc:
        print(f"refusing: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

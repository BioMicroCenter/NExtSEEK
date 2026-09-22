"""Score the forced NS arms against ground truth (graph_search Nessie POC, spec E6 to E8).

Run on the host from the repository root, with pydantic as the only dependency:

    uv run --no-project --with pydantic python -m NessieAI.tests.nessie_tests.engine_compare \\
        --group a|b --run DIR [--run DIR ...] --truth DIR --outputs DIR --prices FILE --out DIR
    ... --cost-only   # spend per run and in total, and per turn; writes nothing
    ... --checks      # the stop-rule counts (errors, outages, fallback contexts, unobserved)

It reads what `runner.run_arms` wrote (`<run>/arms.json`, `<run>/<arm>/manifest.json`,
`<run>/<arm>/payloads/<id>/<turn>.json`), the venue's outputs (the graph debug JSON a
turn lists in `files`, the saved API result at `debug.raw_json_path`, and the token
ledger), the truth files (`engine_truth`) and the operator's price table, and writes
`compare.json`, `compare.md` (the verdict first) and `questions.csv`.

Every stage of every turn is judged pass, fail, unobserved (its evidence is missing) or
n/a (the question or the product's own choice leaves it out). The first failing stage is
the question's attribution; only the reply decides correctness (spec E6). Void questions
(the route force did not land, or arm graph read the `fallback` context) are listed and
excluded for that arm; a provider outage on any arm excludes the question and lists it
for rerun; a parser that chose a non-retrieval mode is scored as the product behaved and
listed. Every verdict is computed with and without the truth's alternates (spec E7).

Cost (spec E8): chat_nextseek appends one line per LLM call to `llm_calls.jsonl` under
the process's `LOG_DIR` (the venue's `/venue/logs`), not into each run root. The scorer
reads a per-run ledger when one exists and otherwise attributes the global ledger's
lines by time: from the turn's run-root timestamp (`<YYMMDD_HHMMSS>_<user>`, the same
container clock) to that plus the turn's wall time and a few seconds. The runs are
strictly sequential, so the window holds exactly that turn's calls.
"""
from __future__ import annotations

import argparse
import contextlib
import csv
import io
import json
import math
import os
import re
import statistics
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path, PurePosixPath

from NessieAI.tests.nessie_tests import engine_truth as et
from NessieAI.tests.nessie_tests.outage import PROVIDER_OUTAGE_MARKER, is_provider_outage
from NessieAI.tests.nessie_tests.preflight import FORCE_NOTE_MARKER

RULE_A = {"margin_points": 15, "p": 0.05, "failure_margin_points": 2, "latency_ratio": 1.25,
          "cost_ratio": 1.5}
RULE_B = {"overall": 0.80, "family_floor": 0.60, "family_min_questions": 5, "max_failed": 0.05}
# prices.json: {"<model as in llm_calls.jsonl>": {"input_per_m": float, "output_per_m": float}, ...}

STAGES = ("route", "switch", "context", "entities", "parser", "request", "engine_value", "reply")
PASS, FAIL, UNOBSERVED, NA = "pass", "fail", "unobserved", "n/a"
VERDICTS_A = ("SUPPORTED", "SUPPORTED WITH COSTS", "NOT SUPPORTED")
VERDICTS_B = ("STILL WORKS", "NOT YET")
SCORED = ("correct", "wrong", "failed")

# The run layout `runner.run_arms` writes (pinned against runner by the tests; importing
# runner here would pull in the e2e DSL, which the scorer's one-dependency run lacks).
ARMS_FILE = "arms.json"
PAYLOADS_DIR = "payloads"
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

# Where the venue mounts the NS engine's outputs (scripts/graph_search/nessie_venue.sh).
CONTAINER_OUTPUTS = "/venue/outputs"
ROUTE_NS = "nextseek_query"
FORCED_SOURCE = "forced"
RETRIEVAL_MODES = {"graph": "graph_query", "api": "new_search"}
# The reply the NS graph turn gives when the graph agent (or its guard) produced no query.
GRAPH_REFUSAL = "Graph agent could not generate a query"
LEDGER_SLACK_S = 5.0
_RUN_ROOT = re.compile(r"^(\d{6}_\d{6})_")
_CHOSE = re.compile(r"\(parser chose ([^)]+)\)")


def safe_name(text) -> str:
    """The file name the harness gives an id or a turn label (runner._safe_name)."""
    name = _UNSAFE_NAME.sub("_", str(text)).strip()
    return name if name not in ("", ".", "..") else "_"


# ── loading the runs ─────────────────────────────────────────────────────────

def _read_json(path: Path):
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    return json.loads(text)


def _read_json_quiet(path: Path):
    try:
        return _read_json(path)
    except ValueError:
        return None


@dataclass
class Attempt:
    """One (question, arm) as one run directory recorded it."""
    run_dir: Path
    arm: str
    entry: dict
    payloads: dict            # payload file stem -> payload
    record: dict | None = None  # the arms.json record for this (question, arm)

    def payload(self, label: str | None = None) -> dict | None:
        if label is not None and safe_name(label) in self.payloads:
            return self.payloads[safe_name(label)]
        if len(self.payloads) == 1:
            return next(iter(self.payloads.values()))
        return None

    @property
    def outage(self) -> bool:
        if self.entry.get("outage"):
            return True
        return any(is_provider_outage((p.get("query_complete") or {}).get("reply"))
                   for p in self.payloads.values())


@dataclass
class Run:
    """Several run directories merged by question id, in `--run` order."""
    run_dirs: list = field(default_factory=list)
    arms: list = field(default_factory=list)
    attempts: dict = field(default_factory=dict)   # id -> arm -> [Attempt, ...]
    order: list = field(default_factory=list)
    meta: list = field(default_factory=list)


def _payloads(case_dir: Path) -> dict:
    if not case_dir.is_dir():
        return {}
    return {p.stem: _read_json(p) for p in sorted(case_dir.glob("*.json"))}


def load_runs(run_dirs) -> Run:
    """Merge run directories (a pilot and its full run, blocks, repeats) by question id.

    A question in two directories keeps both attempts, in `--run` order: the first one
    without a provider outage is scored, the later ones are repeats (spec P4).
    """
    run = Run()
    for d in map(Path, run_dirs):
        if not d.is_dir():
            raise FileNotFoundError(f"no run directory at {d}")
        doc = _read_json(d / ARMS_FILE) or {}
        meta = doc.get("run_meta") or {}
        arms = list(meta.get("arms") or sorted(
            p.name for p in d.iterdir() if (p / "manifest.json").is_file()))
        records = {q.get("id"): q for q in doc.get("questions") or []}
        run.run_dirs.append(d)
        run.meta.append({"dir": str(d), "name": d.name, "git_sha": meta.get("git_sha"),
                         "arms": arms, "cases_file": meta.get("cases_file"),
                         "preflight": meta.get("preflight"), "progress": doc.get("progress")})
        for arm in arms:
            manifest = _read_json(d / arm / "manifest.json")
            if manifest is None:
                continue
            if arm not in run.arms:
                run.arms.append(arm)
            for entry in manifest.get("entries") or []:
                qid = entry.get("id")
                if not qid:
                    continue
                record = ((records.get(qid) or {}).get("arms") or {}).get(arm)
                attempt = Attempt(d, arm, entry, _payloads(d / arm / PAYLOADS_DIR / safe_name(qid)),
                                  record)
                run.attempts.setdefault(qid, {}).setdefault(arm, []).append(attempt)
                if qid not in run.order:
                    run.order.append(qid)
    return run


def load_truth_questions(truth_dir, group: str) -> dict:
    """id -> TruthQuestion for one group, from every truth file in `truth_dir`."""
    g = {"a": "A", "b": "B"}.get(str(group).lower(), str(group).upper())
    out: dict = {}
    for path in et.truth_paths(truth_dir):
        truth = et.load_truth(path)
        if truth.group != g:
            continue
        for q in truth.questions:
            if q.id in out:
                raise ValueError(f"{q.id} is in more than one Group {g} truth file")
            out[q.id] = q
    return out


def load_prices(path) -> dict:
    prices = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(prices, dict):
        raise ValueError("the price table is a JSON object keyed by model")
    for model, price in prices.items():
        if not (isinstance(price, dict) and all(isinstance(price.get(k), (int, float))
                                                for k in ("input_per_m", "output_per_m"))):
            raise ValueError(f"the price of {model!r} needs numeric input_per_m and output_per_m")
    return prices


# ── evidence ─────────────────────────────────────────────────────────────────

def map_path(path, outputs_root) -> Path:
    """A path the engine wrote inside the venue, on the host.

    `/venue/outputs/<root>/...` maps onto `outputs_root/<root>/...`; any other path that
    contains a run root followed by `files` is rebased from the run root; anything else
    is taken as it is.
    """
    text = str(path)
    root = Path(outputs_root)
    prefix = CONTAINER_OUTPUTS.rstrip("/") + "/"
    if text.startswith(prefix):
        return root.joinpath(*PurePosixPath(text[len(prefix):]).parts)
    parts = PurePosixPath(text).parts
    for i in range(len(parts) - 1):
        if _RUN_ROOT.match(parts[i]) and parts[i + 1] == "files":
            return root.joinpath(*parts[i:])
    return Path(text)


def _qc(payload) -> dict:
    return (payload or {}).get("query_complete") or {}


def _debug(payload) -> dict:
    return _qc(payload).get("debug") or {}


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _notes_text(notes) -> str:
    if isinstance(notes, str):
        return notes
    return " | ".join(str(n) for n in notes or [])


def _codes(items) -> list[str]:
    return [str(i.get("code") if isinstance(i, dict) else i) for i in items or []
            if (i.get("code") if isinstance(i, dict) else i)]


def _fold(values) -> set[str]:
    return {str(v).casefold() for v in values}


def engine_value(payload: dict, arm: str, outputs_root) -> float | None:
    """The engine's own total or aggregate for the turn, or None when unobserved.

    graph: the graph debug JSON listed in `query_complete.files`. A single-row,
    single-number `data_preview` is the aggregate; else `neo4j_output.total`, the total
    probe on `debug.graph_result.total`, then `neo4j_output.count` (the rows returned).
    api: the saved result at `debug.raw_json_path`, its `total`.
    """
    qc, debug = _qc(payload), _debug(payload)
    if arm == "graph":
        files = [f for f in qc.get("files") or [] if isinstance(f, dict)
                 and (f.get("kind") == "graph" or str(f.get("key", "")).startswith("graph_debug"))]
        if not files or not files[-1].get("path"):
            return None
        doc = _read_json_quiet(map_path(files[-1]["path"], outputs_root))
        if not isinstance(doc, dict):
            return None
        out = doc.get("neo4j_output") or {}
        preview = out.get("data_preview")
        if (isinstance(preview, list) and len(preview) == 1 and isinstance(preview[0], dict)
                and len(preview[0]) == 1):
            only = next(iter(preview[0].values()))
            if _is_number(only):
                return float(only)
        for value in (out.get("total"), (debug.get("graph_result") or {}).get("total"),
                      out.get("count")):
            if _is_number(value):
                return float(value)
        return None
    path = debug.get("raw_json_path") or next(
        (f.get("path") for f in qc.get("files") or [] if isinstance(f, dict)
         and f.get("kind") == "api"), None)
    if not path:
        return None
    doc = _read_json_quiet(map_path(path, outputs_root))
    data = doc.get("data") if isinstance(doc, dict) else None
    if isinstance(data, dict):
        for key in ("total", "total_samples", "count"):
            if _is_number(data.get(key)):
                return float(data[key])
        return None
    if isinstance(data, list):
        return float(len(data))
    return None


_CYPHER_STRINGS = re.compile(r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\"")
_SAMPLE_VAR = re.compile(r"\b([A-Za-z_]\w*)\s*:\s*`?(?:Sample|T_\w+)`?\b")


def whole_node_returns(cypher: str) -> list[str]:
    """The Sample variables a query returns or collects whole (spec D13)."""
    masked = _CYPHER_STRINGS.sub("''", cypher or "")
    sample_vars = set(_SAMPLE_VAR.findall(masked))
    hits: list[str] = []
    for clause in re.findall(r"\bRETURN\b(.*?)(?=\bUNION\b|$)", masked, re.S | re.I):
        clause = re.split(r"\b(?:ORDER\s+BY|SKIP|LIMIT)\b", clause, flags=re.I)[0]
        clause = re.sub(r"^\s*DISTINCT\b", "", clause, flags=re.I)
        depth, item, items = 0, "", []
        for ch in clause:
            depth += ch in "([{"
            depth -= ch in ")]}"
            if ch == "," and depth == 0:
                items.append(item)
                item = ""
            else:
                item += ch
        items.append(item)
        for raw in items:
            expr = re.split(r"\s+AS\s+", raw.strip(), flags=re.I)[0].strip()
            m = re.fullmatch(r"collect\s*\(\s*(?:DISTINCT\s+)?(\w+)\s*\)", expr, flags=re.I)
            var = m.group(1) if m else expr
            if var in sample_vars and var not in hits:
                hits.append(var)
    return hits


def _type_label(code: str) -> str:
    return "T_" + re.sub(r"[^A-Za-z0-9]", "_", code)


def _cypher_problems(cypher: str, expected: et.Expected) -> list[str]:
    problems = []
    for code in expected.sampletypes:
        label = _type_label(code)
        if not (re.search(rf":\s*`?{re.escape(label)}`?(?![\w])", cypher)
                or re.search(rf"['\"]{re.escape(code)}['\"]", cypher)):
            problems.append(f"no {label} label or '{code}' type filter")
    for attr in expected.attributes:
        used = (re.search(rf"\.\s*(?:{re.escape(attr)}(?![\w])|`{re.escape(attr)}`)", cypher)
                or re.search(rf"['\"]{re.escape(attr)}['\"]", cypher))
        if not used:
            problems.append(f"attribute {attr} not used")
    for rel in expected.relationships:
        if not re.search(rf":\s*`?{re.escape(rel)}(?![\w])", cypher):
            problems.append(f"relationship {rel} not used")
    problems += [f"returns the whole node {v}" for v in whole_node_returns(cypher)]
    return problems


def _values_for(body, keys) -> list[str]:
    out: list[str] = []
    if isinstance(body, dict):
        for k, v in body.items():
            if k in keys:
                out += [str(x) for x in (v if isinstance(v, list) else [v]) if x not in (None, "")]
            out += _values_for(v, keys)
    elif isinstance(body, list):
        for v in body:
            out += _values_for(v, keys)
    return out


def _api_problems(api_plan: dict, expected: et.Expected) -> list[str]:
    body = api_plan.get("requestBody") or {}
    types = _fold(_values_for(body, {"sampletype", "sample_type", "sampletypes", "sampletype_code"}))
    attrs = _fold(_values_for(body, {"attribute", "attributes"}))
    problems = [f"sampletype {c} not requested" for c in expected.sampletypes
                if c.casefold() not in types]
    problems += [f"attribute {a} not requested" for a in expected.attributes
                 if a.casefold() not in attrs]
    return problems


def _accepted_numbers(expected: et.Expected) -> list[float]:
    numbers, _ = et.primary_requirements(expected)
    accepted = list(numbers)
    for alt in expected.alternates:
        accepted += alt.required_numbers
    if expected.kind == "none":
        accepted.append(0.0)
    return [float(n) for n in accepted]


def stage_details(payload, turn: et.TruthTurn, arm: str, outputs_root) -> dict:
    """stage -> (verdict, why). `stage_verdicts` is this without the reasons."""
    if payload is None:
        return {s: (UNOBSERVED, "no payload: the turn raised before it returned") for s in STAGES}
    exp = turn.expected
    debug = _debug(payload)
    plan = debug.get("parser_plan") or None
    mode = (plan or {}).get("mode")
    retrieval = mode in ("graph_query", "new_search")
    out: dict = {}

    route_obs = payload.get("route_obs") or {}
    route, source = route_obs.get("route"), route_obs.get("source")
    if route is None and source is None:
        out["route"] = (UNOBSERVED, "no route_decided event")
    elif route == ROUTE_NS and source == FORCED_SOURCE:
        out["route"] = (PASS, "")
    else:
        out["route"] = (FAIL, f"route {route!r} from source {source!r}")

    if not plan:
        out["switch"] = (UNOBSERVED, "no debug.parser_plan")
    elif not retrieval:
        out["switch"] = (NA, f"the parser chose {mode!r}, a non-retrieval mode")
    elif FORCE_NOTE_MARKER not in _notes_text(plan.get("notes")):
        out["switch"] = (FAIL, "no evaluation-switch note in parser_plan.notes")
    elif mode != RETRIEVAL_MODES.get(arm):
        out["switch"] = (FAIL, f"the plan ended on {mode!r}, not the arm's mode")
    else:
        out["switch"] = (PASS, "")

    if arm != "graph" or not retrieval:
        out["context"] = (NA, "")
    elif debug.get("graph_context") is None:
        out["context"] = (UNOBSERVED, "no debug.graph_context")
    elif debug.get("graph_context") == "catalog":
        out["context"] = (PASS, "")
    else:
        out["context"] = (FAIL, f"graph_context {debug.get('graph_context')!r}")

    if not exp.sampletypes:
        out["entities"] = (NA, "the question names no sample type")
    elif debug.get("entity_result") is None:
        out["entities"] = (UNOBSERVED, "no debug.entity_result")
    else:
        codes = _fold(_codes((debug.get("entity_result") or {}).get("sampletypes")))
        missing = [c for c in exp.sampletypes if c.casefold() not in codes]
        out["entities"] = (FAIL, f"not resolved: {missing}") if missing else (PASS, "")

    if not plan:
        out["parser"] = (UNOBSERVED, "no debug.parser_plan")
    elif not exp.sampletypes:
        out["parser"] = (NA, "the question names no sample type")
    else:
        chosen = [c.strip() for c in re.split(r"[,\s]+", str((plan.get("filters") or {}).get(
            "sampletype_code") or "")) if c.strip()]
        resolved = _codes((plan.get("resolved") or {}).get("sampletypes"))
        wanted = _fold(exp.sampletypes)
        missing = [c for c in exp.sampletypes if c.casefold() not in _fold(chosen + resolved)]
        if chosen and not _fold(chosen) & wanted:
            out["parser"] = (FAIL, f"filters.sampletype_code {chosen} is not an expected type")
        elif missing:
            out["parser"] = (FAIL, f"not in the plan: {missing}")
        else:
            out["parser"] = (PASS, "")

    if not retrieval:
        out["request"] = (NA, "")
    elif arm == "graph":
        graph_plan = debug.get("graph_plan")
        if graph_plan is None:
            out["request"] = (UNOBSERVED, "no debug.graph_plan")
        elif not str(graph_plan.get("cypher") or "").strip():
            out["request"] = (FAIL, "no Cypher: the graph agent or its guard gave none")
        else:
            problems = _cypher_problems(graph_plan["cypher"], exp)
            out["request"] = (FAIL, "; ".join(problems)) if problems else (PASS, "")
    else:
        api_plan = debug.get("api_plan")
        if api_plan is None:
            out["request"] = (UNOBSERVED, "no debug.api_plan")
        elif not api_plan.get("endpoint"):
            out["request"] = (FAIL, "no endpoint")
        else:
            problems = _api_problems(api_plan, exp)
            out["request"] = (FAIL, "; ".join(problems)) if problems else (PASS, "")

    accepted = _accepted_numbers(exp) if exp.kind in ("count", "value", "none") else []
    if not retrieval:
        out["engine_value"] = (NA, "")
    elif not accepted:
        out["engine_value"] = (NA, "the truth is not a number")
    else:
        value = engine_value(payload, arm, outputs_root)
        if value is None:
            out["engine_value"] = (UNOBSERVED, "the engine's result file is missing or has no number")
        elif any(math.isclose(value, a, rel_tol=0.0, abs_tol=1e-9) for a in accepted):
            out["engine_value"] = (PASS, "")
        else:
            out["engine_value"] = (FAIL, f"the engine got {et.fmt_number(value)}")

    ok, why = et.reply_satisfies(_qc(payload).get("reply"), exp)
    out["reply"] = (PASS, why) if ok else (FAIL, why)
    return out


def stage_verdicts(payload: dict, turn: et.TruthTurn, arm: str, outputs_root) -> dict:
    """stage -> pass | fail | unobserved | n/a, for the stages in STAGES (spec E6)."""
    return {stage: verdict for stage, (verdict, _) in
            stage_details(payload, turn, arm, outputs_root).items()}


def first_failing_stage(verdicts: dict) -> str | None:
    return next((s for s in STAGES if verdicts.get(s) == FAIL), None)


# ── cost ─────────────────────────────────────────────────────────────────────

_LEDGER_CACHE: dict = {}


def _read_ledger(path: Path) -> list[dict]:
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)
    if key not in _LEDGER_CACHE:
        rows = []
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                with contextlib.suppress(ValueError):
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
        _LEDGER_CACHE[key] = rows
    return _LEDGER_CACHE[key]


def _run_root(payload, outputs_root) -> Path | None:
    qc, debug = _qc(payload), _debug(payload)
    paths = [f.get("path") for f in qc.get("files") or [] if isinstance(f, dict)]
    paths.append(debug.get("raw_json_path"))
    for raw in paths:
        if not raw:
            continue
        parts = map_path(raw, outputs_root).parts
        for i in range(1, len(parts)):
            if parts[i] == "files" and _RUN_ROOT.match(parts[i - 1]):
                return Path(*parts[:i])
    return None


def _ts(row) -> datetime | None:
    try:
        return datetime.fromisoformat(str(row.get("ts"))).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def turn_ledger(payload, outputs_root, ledger_path=None) -> list[dict] | None:
    """The LLM calls one turn made, or None when they cannot be found (see the module doc)."""
    if payload is None:
        return None
    root = _run_root(payload, outputs_root)
    if root is None:
        return None
    for candidate in (root / "llm_calls.jsonl", root / "files" / "llm_calls.jsonl"):
        if candidate.is_file():
            return _read_ledger(candidate)
    ledger = Path(ledger_path) if ledger_path else Path(outputs_root).parent / "logs" / "llm_calls.jsonl"
    stamp = _RUN_ROOT.match(root.name)
    if not ledger.is_file() or not stamp:
        return None
    start = datetime.strptime(stamp.group(1), "%y%m%d_%H%M%S")
    end = start + timedelta(seconds=float(payload.get("elapsed_s") or 0.0) + LEDGER_SLACK_S)
    return [row for row in _read_ledger(ledger) if (t := _ts(row)) is not None and start <= t <= end]


def question_cost(payload: dict, outputs_root, prices: dict, *, ledger_path=None) -> float | None:
    """USD for one turn from its ledger lines and the price table; None when unmeasured.

    A model missing from the price table makes the whole turn unmeasured rather than
    cheaper: a partial sum presented as a cost is the lie `manifest.cost_summary` refuses.
    """
    rows = turn_ledger(payload, outputs_root, ledger_path)
    if not rows or not prices:
        return None
    total, priced = 0.0, False
    for row in rows:
        tokens_in, tokens_out = row.get("prompt_tokens"), row.get("completion_tokens")
        if tokens_in is None and tokens_out is None:
            continue
        price = prices.get(row.get("model"))
        if price is None:
            return None
        total += ((tokens_in or 0) * float(price["input_per_m"])
                  + (tokens_out or 0) * float(price["output_per_m"])) / 1e6
        priced = True
    return total if priced else None


def unpriced_models(payload, outputs_root, prices, *, ledger_path=None) -> set[str]:
    rows = turn_ledger(payload, outputs_root, ledger_path) or []
    return {str(r.get("model")) for r in rows
            if (r.get("prompt_tokens") is not None or r.get("completion_tokens") is not None)
            and r.get("model") not in (prices or {})}


# ── statistics and the pass rules ────────────────────────────────────────────

def mcnemar_exact(b: int, c: int) -> float:
    """Two-sided exact McNemar test over the discordant pairs (a binomial test at 1/2)."""
    n = b + c
    if n == 0:
        return 1.0
    tail = sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n
    return min(1.0, 2 * tail)


def _guard(g_value, a_value, limit) -> dict:
    if g_value is None or a_value is None:
        return {"ratio": None, "limit": limit, "holds": False, "note": "unmeasured"}
    ratio = (g_value / a_value) if a_value else (math.inf if g_value else 1.0)
    return {"ratio": ratio, "limit": limit, "holds": ratio <= limit}


def verdict_a(scores: dict, rule=RULE_A) -> dict:
    """SUPPORTED | SUPPORTED WITH COSTS | NOT SUPPORTED (spec E7, Group A).

    Main rule: G's correct rate exceeds A's by at least `margin_points`, the exact
    McNemar p over the discordant pairs is under `p`, and G's failure rate is at most A's
    plus `failure_margin_points`. Guards: G's median wall time at most `latency_ratio`
    times A's, and its median cost at most `cost_ratio` times A's. An unmeasured guard
    does not hold.
    """
    n = scores.get("n") or 0
    g, a = scores.get("g") or {}, scores.get("a") or {}
    b, c = scores.get("b") or 0, scores.get("c") or 0
    p = mcnemar_exact(b, c)
    if n == 0:
        return {"verdict": "NOT SUPPORTED", "n": 0, "b": b, "c": c, "p": p,
                "main_rule": {"holds": False}, "reasons": ["no question was scorable on both arms"],
                "guards": {"latency": _guard(None, None, rule["latency_ratio"]),
                           "cost": _guard(None, None, rule["cost_ratio"])}}
    g_rate, a_rate = g["correct"] / n, a["correct"] / n
    margin = round((g_rate - a_rate) * 100, 9)
    g_fail, a_fail = round(g["failed"] / n * 100, 9), round(a["failed"] / n * 100, 9)
    main = {"g_correct_rate": g_rate, "a_correct_rate": a_rate, "margin_points": margin,
            "margin_ok": margin >= rule["margin_points"], "p": p, "p_ok": p < rule["p"],
            "g_failed_points": g_fail, "a_failed_points": a_fail,
            "failure_ok": g_fail <= a_fail + rule["failure_margin_points"]}
    main["holds"] = main["margin_ok"] and main["p_ok"] and main["failure_ok"]
    guards = {"latency": _guard(g.get("median_s"), a.get("median_s"), rule["latency_ratio"]),
              "cost": _guard(g.get("median_cost"), a.get("median_cost"), rule["cost_ratio"])}
    reasons = []
    if not main["margin_ok"]:
        reasons.append(f"margin {margin:.1f} points, under {rule['margin_points']}")
    if not main["p_ok"]:
        reasons.append(f"McNemar p {p:.4f} on {b} against {c} discordant pairs, not under {rule['p']}")
    if not main["failure_ok"]:
        reasons.append(f"G fails {g_fail:.1f}% against A's {a_fail:.1f}% plus "
                       f"{rule['failure_margin_points']} points")
    for name, guard in guards.items():
        if not guard["holds"]:
            reasons.append(f"the {name} guard fails ("
                           + ("unmeasured" if guard["ratio"] is None
                              else f"ratio {guard['ratio']:.2f} over {guard['limit']}") + ")")
    if main["holds"]:
        verdict = "SUPPORTED" if all(gd["holds"] for gd in guards.values()) else "SUPPORTED WITH COSTS"
    else:
        verdict = "NOT SUPPORTED"
    return {"verdict": verdict, "n": n, "b": b, "c": c, "p": p, "main_rule": main,
            "guards": guards, "reasons": reasons}


def verdict_b(scores: dict, rule=RULE_B) -> dict:
    """STILL WORKS | NOT YET, with the failing families and their first failing stages."""
    n = scores.get("n") or 0
    if n == 0:
        return {"verdict": "NOT YET", "overall": None, "failed_rate": None,
                "failing_families": [], "reasons": ["no scorable question"]}
    overall = scores["correct"] / n
    failed_rate = scores["failed"] / n
    failing = []
    for family, s in sorted((scores.get("families") or {}).items()):
        if s["n"] >= rule["family_min_questions"] and s["correct"] / s["n"] < rule["family_floor"]:
            failing.append({"family": family, "n": s["n"], "correct": s["correct"],
                            "rate": s["correct"] / s["n"],
                            "first_failing_stages": dict(s.get("first_failing_stages") or {})})
    reasons = []
    if round(overall, 9) < rule["overall"]:
        reasons.append(f"correct on {overall:.1%}, under {rule['overall']:.0%}")
    if failing:
        reasons.append("under the family floor: " + ", ".join(f["family"] for f in failing))
    if round(failed_rate, 9) > rule["max_failed"]:
        reasons.append(f"{failed_rate:.1%} failed outright, over {rule['max_failed']:.0%}")
    return {"verdict": "NOT YET" if reasons else "STILL WORKS", "overall": overall,
            "failed_rate": failed_rate, "failing_families": failing, "reasons": reasons}


# ── scoring the questions ────────────────────────────────────────────────────

_TOTAL_PHRASES = (
    re.compile(r"\b(?:in total|total(?: of)?|altogether|overall|all told)\b[^.\d\n]{0,30}?"
               r"(\d[\d,   ]*\d|\d)", re.IGNORECASE),
    re.compile(r"(\d[\d,]*\d|\d)\s+(?:[A-Za-z]+\s+){0,2}?(?:in total|total|altogether)\b",
               re.IGNORECASE),
)


def contradiction_suspect(reply, expected: et.Expected) -> bool:
    """A reply that also states another number phrased as a total (triage in R2)."""
    text = et.reply_text(reply)
    accepted = _accepted_numbers(expected)
    if _is_number(expected.value):
        accepted.append(float(expected.value))
    for pattern in _TOTAL_PHRASES:
        for m in pattern.finditer(text):
            with contextlib.suppress(ValueError):
                n = float(re.sub(r"[,\s  ]", "", m.group(1)))
                if not any(math.isclose(n, a, rel_tol=0.0, abs_tol=1e-9) for a in accepted):
                    return True
    return False


def _judge(attempt: Attempt, turn: et.TruthTurn, arm: str, outputs_root, prices,
           ledger_path=None) -> dict:
    payload = attempt.payload(turn.label)
    entry = attempt.entry
    details = stage_details(payload, turn, arm, outputs_root)
    verdicts = {s: v for s, (v, _) in details.items()}
    qc, debug = _qc(payload), _debug(payload)
    reply = qc.get("reply")
    plan = debug.get("parser_plan") or {}
    mode = plan.get("mode")
    chose = _CHOSE.search(_notes_text(plan.get("notes")))
    which, failure, void = None, None, None
    if payload is None:
        outcome, failure = "failed", entry.get("reason") or "no payload: the turn raised before it returned"
    elif verdicts["route"] == FAIL:
        outcome, void = "void", f"the route force did not land ({details['route'][1]})"
    elif arm == "graph" and verdicts["context"] == FAIL:
        outcome, void = "void", f"graph_context {debug.get('graph_context')!r}, not the live catalog"
    elif entry.get("status") == "error":
        outcome, failure = "failed", entry.get("reason") or "the harness recorded an error"
    elif payload.get("status") != "completed":
        outcome, failure = "failed", f"the turn ended {payload.get('status')!r}"
    elif not et.reply_text(reply).strip():
        outcome, failure = "failed", "no answer"
    elif GRAPH_REFUSAL in et.reply_text(reply):
        outcome, failure = "failed", "the graph agent or its guard refused"
    else:
        ok, which = et.reply_satisfies(reply, turn.expected)
        outcome = "correct" if ok else "wrong"
        if not ok:
            which = None
    value = engine_value(payload, arm, outputs_root) if payload else None
    return {
        "outcome": outcome, "which": which,
        "correct_primary": outcome == "correct" and which == "primary",
        "correct_any": outcome == "correct",
        "failure_reason": failure, "void_reason": void,
        "stages": verdicts, "stage_reasons": {s: why for s, (_, why) in details.items() if why},
        "first_failing_stage": first_failing_stage(verdicts),
        "engine_value": value, "expected_value": turn.expected.value,
        "elapsed_s": (payload or {}).get("elapsed_s", entry.get("elapsed_s")),
        "cost_usd": question_cost(payload, outputs_root, prices, ledger_path=ledger_path)
        if payload else None,
        "parser_mode": mode, "parser_chose": chose.group(1) if chose else None,
        "non_retrieval": mode is not None and mode not in ("graph_query", "new_search"),
        "contradiction_suspect": outcome == "correct" and contradiction_suspect(reply, turn.expected),
        "run_dir": attempt.run_dir.name,
        "git_sha": (attempt.record or {}).get("git_sha"),
    }


def score_arm(attempts, question: et.TruthQuestion, arm: str, outputs_root, prices,
              ledger_path=None) -> dict:
    """One (question, arm) row: the first attempt without a provider outage is scored,
    later ones are recorded as repeats."""
    row = {"id": question.id, "family": question.family, "source": question.source,
           "group": question.group, "arm": arm, "flags": list(question.flags)}
    if not attempts:
        return {**row, "outcome": "missing", "stages": {}, "first_failing_stage": None,
                "correct_primary": False, "correct_any": False, "repeats": []}
    live = [a for a in attempts if not a.outage]
    if not live:
        return {**row, "outcome": "outage", "stages": {}, "first_failing_stage": None,
                "correct_primary": False, "correct_any": False, "repeats": [],
                "run_dir": attempts[-1].run_dir.name}
    turn = question.turns[0]
    judged = _judge(live[0], turn, arm, outputs_root, prices, ledger_path)
    repeats = [{"run_dir": a.run_dir.name, **{k: v for k, v in _judge(
        a, turn, arm, outputs_root, prices, ledger_path).items()
        if k in ("outcome", "correct_primary", "correct_any", "elapsed_s", "cost_usd")}}
        for a in live[1:]]
    return {**row, **judged, "repeats": repeats}


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median(values) if values else None


def _arm_stats(rows, with_alternates: bool) -> dict:
    key = "correct_any" if with_alternates else "correct_primary"
    return {"n": len(rows), "correct": sum(1 for r in rows if r[key]),
            "failed": sum(1 for r in rows if r["outcome"] == "failed"),
            "wrong": sum(1 for r in rows if r["outcome"] == "wrong"),
            "median_s": _median(r.get("elapsed_s") for r in rows),
            "median_cost": _median(r.get("cost_usd") for r in rows),
            "cost_observed": sum(1 for r in rows if r.get("cost_usd") is not None)}


def _scores_a(per_q: dict, excluded: set, with_alternates: bool) -> dict:
    key = "correct_any" if with_alternates else "correct_primary"
    paired = [qid for qid, rows in per_q.items() if qid not in excluded
              and all((rows.get(arm) or {}).get("outcome") in SCORED for arm in ("graph", "api"))]
    g_rows = [per_q[q]["graph"] for q in paired]
    a_rows = [per_q[q]["api"] for q in paired]
    return {"n": len(paired), "paired": paired,
            "b": sum(1 for g, a in zip(g_rows, a_rows) if g[key] and not a[key]),
            "c": sum(1 for g, a in zip(g_rows, a_rows) if a[key] and not g[key]),
            "g": _arm_stats(g_rows, with_alternates), "a": _arm_stats(a_rows, with_alternates)}


def _group_stats(rows, with_alternates: bool) -> dict:
    key = "correct_any" if with_alternates else "correct_primary"
    stats = _arm_stats(rows, with_alternates)
    stats["first_failing_stages"] = dict(Counter(
        r.get("first_failing_stage") or "none" for r in rows if not r[key]))
    return stats


def _scores_b(per_q: dict, excluded: set, with_alternates: bool) -> dict:
    rows = [r["graph"] for qid, r in per_q.items() if qid not in excluded
            and (r.get("graph") or {}).get("outcome") in SCORED]
    families: dict = {}
    for row in rows:
        families.setdefault(row["family"], []).append(row)
    scores = _group_stats(rows, with_alternates)
    scores["families"] = {f: _group_stats(rs, with_alternates) for f, rs in sorted(families.items())}
    scores["rest_routed_today"] = _group_stats(
        [r for r in rows if et.FLAG_REST_ROUTED_TODAY in r["flags"]], with_alternates)
    return scores


def score(run: Run, truth: dict, group: str, outputs_root, prices, *, ledger_path=None) -> dict:
    """Everything compare.json holds: the verdicts with and without alternates, the
    scores, the lists to triage, one row per (question, arm), the checks and repeats."""
    g = {"a": "A", "b": "B"}[str(group).lower()]
    arms = list(run.arms)
    lists = {"void": [], "outage_rerun": [], "non_retrieval": [], "contradiction_suspect": [],
             "no_truth": [], "not_scorable": [], "not_run": [], "missing": []}
    per_q: dict = {}
    rows: list = []
    for qid in run.order:
        q = truth.get(qid)
        if q is None:
            lists["no_truth"].append(qid)
            continue
        if not q.scorable or q.merged_into:
            lists["not_scorable"].append(qid)
            continue
        per_q[qid] = {arm: score_arm(run.attempts.get(qid, {}).get(arm), q, arm, outputs_root,
                                     prices, ledger_path) for arm in arms}
        rows += per_q[qid].values()
    lists["not_run"] = [qid for qid, q in truth.items()
                        if q.scorable and not q.merged_into and qid not in per_q]
    for qid, arm_rows in per_q.items():
        if any(r["outcome"] == "outage" for r in arm_rows.values()):
            lists["outage_rerun"].append(qid)
        for arm, r in arm_rows.items():
            if r["outcome"] == "void":
                lists["void"].append({"id": qid, "arm": arm, "reason": r["void_reason"]})
            if r["outcome"] == "missing":
                lists["missing"].append({"id": qid, "arm": arm})
            if r.get("non_retrieval"):
                lists["non_retrieval"].append({"id": qid, "arm": arm, "mode": r["parser_mode"]})
            if r.get("contradiction_suspect") and qid not in lists["contradiction_suspect"]:
                lists["contradiction_suspect"].append(qid)
    excluded = set(lists["outage_rerun"])
    if g == "A":
        scores = {k: _scores_a(per_q, excluded, alt)
                  for k, alt in (("with_alternates", True), ("without_alternates", False))}
        verdict = {k: verdict_a(s) for k, s in scores.items()}
    else:
        scores = {k: _scores_b(per_q, excluded, alt)
                  for k, alt in (("with_alternates", True), ("without_alternates", False))}
        verdict = {k: verdict_b(s) for k, s in scores.items()}
    repeats = {}
    for arm in arms:
        pairs = [(r["correct_any"], rep["correct_any"]) for r in rows if r["arm"] == arm
                 for rep in r.get("repeats") or []]
        if pairs:
            repeats[arm] = {"repeat_attempts": len(pairs),
                            "agreeing": sum(1 for a, b in pairs if a == b)}
    return {"group": g, "arms": arms, "runs": run.meta, "rules": RULE_A if g == "A" else RULE_B,
            "verdict": verdict, "scores": scores, "lists": lists, "repeats": repeats,
            "checks": checks(run, truth, outputs_root, prices, ledger_path=ledger_path),
            "questions": rows}


# ── checks and cost reports (print only) ─────────────────────────────────────

def _all_attempts(run: Run, arm: str | None = None, run_dir: Path | None = None):
    for qid in run.order:
        for a, attempts in run.attempts.get(qid, {}).items():
            if arm is not None and a != arm:
                continue
            for attempt in attempts:
                if run_dir is None or attempt.run_dir == run_dir:
                    yield qid, attempt


def checks(run: Run, truth: dict, outputs_root, prices, *, ledger_path=None) -> dict:
    """The stop-rule counts per arm, over every attempt in the runs (plan stage P)."""
    out: dict = {}
    for arm in run.arms:
        counts = {"driven": 0, "infrastructure_errors": 0, "outages": 0, "void_route": 0,
                  "fallback_contexts": 0, "no_payload": 0, "unobserved": Counter(),
                  "cost_observed": 0, "cost_unmeasured": 0, "cost_usd": 0.0,
                  "unpriced_models": set()}
        for qid, attempt in _all_attempts(run, arm):
            counts["driven"] += 1
            q = truth.get(qid)
            turn = q.turns[0] if q else et.TruthTurn(label="main", query="", reading="",
                                                     oracle=None, expected=et.Expected(kind="none"))
            payload = attempt.payload(turn.label)
            if attempt.outage:
                counts["outages"] += 1
                continue
            if attempt.entry.get("status") == "error" or (
                    payload is not None and payload.get("status") not in (None, "completed")):
                counts["infrastructure_errors"] += 1
            if payload is None:
                counts["no_payload"] += 1
                counts["cost_unmeasured"] += 1
                continue
            if _debug(payload).get("graph_context") == "fallback":
                counts["fallback_contexts"] += 1
            verdicts = stage_verdicts(payload, turn, arm, outputs_root)
            if verdicts["route"] == FAIL:
                counts["void_route"] += 1
            counts["unobserved"].update(s for s, v in verdicts.items() if v == UNOBSERVED)
            cost = question_cost(payload, outputs_root, prices, ledger_path=ledger_path)
            if cost is None:
                counts["cost_unmeasured"] += 1
                counts["unpriced_models"] |= unpriced_models(payload, outputs_root, prices,
                                                             ledger_path=ledger_path)
            else:
                counts["cost_observed"] += 1
                counts["cost_usd"] += cost
        counts["unobserved"] = dict(counts["unobserved"])
        counts["unpriced_models"] = sorted(counts["unpriced_models"])
        out[arm] = counts
    return out


def _print_checks(run: Run, doc: dict, out) -> None:
    names = ", ".join(m["name"] for m in run.meta)
    print(f"checks for {names} (arms {', '.join(run.arms)})", file=out)
    totals = Counter()
    for arm, c in doc.items():
        unobserved = ", ".join(f"{s} {n}" for s, n in sorted(c["unobserved"].items())) or "none"
        print(f"{arm}: {c['driven']} driven; infrastructure errors: {c['infrastructure_errors']}; "
              f"outages: {c['outages']}; route force not landed: {c['void_route']}; "
              f"fallback contexts: {c['fallback_contexts']}; no payload: {c['no_payload']}; "
              f"unobserved stages: {unobserved}", file=out)
        for key in ("infrastructure_errors", "outages", "fallback_contexts", "void_route",
                    "cost_observed", "cost_unmeasured"):
            totals[key] += c[key]
        totals["unobserved"] += sum(c["unobserved"].values())
        totals["cost_usd"] += c["cost_usd"]
    per_turn = (f"${totals['cost_usd'] / totals['cost_observed']:.4f} per priced turn"
                if totals["cost_observed"] else "no priced turn")
    unpriced = sorted({m for c in doc.values() for m in c["unpriced_models"]})
    print(f"total: infrastructure errors: {totals['infrastructure_errors']}; outages: "
          f"{totals['outages']}; route force not landed: {totals['void_route']}; fallback "
          f"contexts: {totals['fallback_contexts']}; unobserved stages: {totals['unobserved']}",
          file=out)
    print(f"cost: ${totals['cost_usd']:.4f} over {totals['cost_observed']} priced turn(s), "
          f"{totals['cost_unmeasured']} unmeasured; {per_turn}"
          + (f"; unpriced models: {', '.join(unpriced)}" if unpriced else ""), file=out)


def _print_cost(run: Run, outputs_root, prices, ledger_path, out) -> None:
    grand = Counter()
    for run_dir in run.run_dirs:
        lines = []
        per_run = Counter()
        for arm in run.arms:
            c = Counter()
            for _, attempt in _all_attempts(run, arm, run_dir):
                c["turns"] += 1
                cost = question_cost(attempt.payload(), outputs_root, prices,
                                     ledger_path=ledger_path) if attempt.payload() else None
                if cost is None:
                    c["unmeasured"] += 1
                else:
                    c["priced"] += 1
                    c["usd"] += cost
            if c["turns"]:
                lines.append(_cost_line(f"  {arm}", c))
                per_run.update(c)
        print(_cost_line(f"{run_dir.name}", per_run), file=out)
        for line in lines:
            print(line, file=out)
        grand.update(per_run)
    print(_cost_line("all runs", grand), file=out)


def _cost_line(label: str, c: Counter) -> str:
    per_turn = f"${c['usd'] / c['priced']:.4f} per turn" if c["priced"] else "no priced turn"
    return (f"{label}: ${c['usd']:.4f} over {c['priced']} priced turn(s) of {c['turns']}, "
            f"{c['unmeasured']} unmeasured; {per_turn}")


# ── the report files ─────────────────────────────────────────────────────────

def _pct(n, d) -> str:
    return f"{n / d * 100:.0f}%" if d else "-"


def _cell(value) -> str:
    if value is None or value == "":
        return "-"
    if _is_number(value):
        return et.fmt_number(value) if float(value).is_integer() else f"{value:.4g}"
    text = str(value) if not isinstance(value, list) else ", ".join(map(str, value))
    return text.replace("|", "/").replace("\n", " ")


def _question_table(rows) -> list[str]:
    lines = ["| id | family | source | arm | outcome | first failing stage | by | engine value | "
             "expected | wall s | cost $ |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        cost = f"{r['cost_usd']:.4f}" if r.get("cost_usd") is not None else "-"
        lines.append(f"| {r['id']} | {r['family']} | {r['source']} | {r['arm']} | {r['outcome']} | "
                     f"{_cell(r.get('first_failing_stage'))} | {_cell(r.get('which'))} | "
                     f"{_cell(r.get('engine_value'))} | {_cell(r.get('expected_value'))} | "
                     f"{_cell(r.get('elapsed_s'))} | {cost} |")
    return lines


def _lists_section(lists: dict) -> list[str]:
    lines = ["## Excluded and listed", ""]
    lines.append("- Void (excluded for that arm): " + (", ".join(
        f"{v['id']} ({v['arm']}: {v['reason']})" for v in lists["void"]) or "none"))
    lines.append("- Provider outage, excluded and to rerun: " + (", ".join(lists["outage_rerun"]) or "none"))
    lines.append("- Non-retrieval parser mode, scored as the product behaved: " + (", ".join(
        f"{v['id']} ({v['arm']}: {v['mode']})" for v in lists["non_retrieval"]) or "none"))
    lines.append("- contradiction_suspect, for triage (R2): "
                 + (", ".join(lists["contradiction_suspect"]) or "none"))
    lines.append("- In the runs but not in the truth: " + (", ".join(lists["no_truth"]) or "none"))
    lines.append("- In the truth but not yet run: " + (", ".join(lists["not_run"]) or "none"))
    lines.append("- Not scorable or merged into another question: "
                 + (", ".join(lists["not_scorable"]) or "none"))
    return lines + [""]


def _md_a(doc: dict) -> str:
    with_v, without_v = doc["verdict"]["with_alternates"], doc["verdict"]["without_alternates"]
    lines = [f"# Group A: {with_v['verdict']}", "",
             f"Without alternates: {without_v['verdict']}. The rule (spec E7): G's correct rate "
             f"exceeds A's by at least {RULE_A['margin_points']} points, an exact two-sided McNemar "
             f"test gives p < {RULE_A['p']}, and G's failure rate is at most A's plus "
             f"{RULE_A['failure_margin_points']} points; guards: G's median wall time at most "
             f"{RULE_A['latency_ratio']} times A's, its median cost at most {RULE_A['cost_ratio']} "
             f"times A's.", ""]
    for name, v in (("With alternates", with_v), ("Without alternates", without_v)):
        if v["reasons"]:
            lines.append(f"{name}, what does not hold: " + "; ".join(v["reasons"]) + ".")
    lines += ["", "| | with alternates | without alternates |", "|---|---|---|"]
    s_w, s_o = doc["scores"]["with_alternates"], doc["scores"]["without_alternates"]

    def row(label, fn):
        lines.append(f"| {label} | {fn(s_w, with_v)} | {fn(s_o, without_v)} |")

    row("paired questions", lambda s, v: s["n"])
    row("G correct", lambda s, v: f"{s['g']['correct']} ({_pct(s['g']['correct'], s['n'])})")
    row("A correct", lambda s, v: f"{s['a']['correct']} ({_pct(s['a']['correct'], s['n'])})")
    row("margin, points", lambda s, v: f"{v['main_rule'].get('margin_points', 0):.1f}")
    row("discordant: G only / A only", lambda s, v: f"{s['b']} / {s['c']}")
    row("McNemar p", lambda s, v: f"{v['p']:.4f}")
    row("failed outright: G / A", lambda s, v: f"{s['g']['failed']} / {s['a']['failed']}")
    row("median wall time per turn, s: G / A",
        lambda s, v: f"{_cell(s['g']['median_s'])} / {_cell(s['a']['median_s'])}")
    row("median cost per question, $: G / A",
        lambda s, v: " / ".join(f"{x:.4f}" if x is not None else "unmeasured"
                                for x in (s["g"]["median_cost"], s["a"]["median_cost"])))
    lines.append("")
    lines += _lists_section(doc["lists"])
    lines += _repeats_section(doc)
    lines += ["## Questions", "", "The first failing stage is the attribution; only the reply "
              "decides correctness. `by` is the reading the reply met.", ""]
    lines += _question_table(doc["questions"])
    return "\n".join(lines) + "\n"


def _md_b(doc: dict) -> str:
    with_v, without_v = doc["verdict"]["with_alternates"], doc["verdict"]["without_alternates"]
    s = doc["scores"]["with_alternates"]
    lines = [f"# Group B: {with_v['verdict']}", "",
             f"Without alternates: {without_v['verdict']}. The rule (spec E7): G correct on at least "
             f"{RULE_B['overall']:.0%} of the scorable questions, no family of at least "
             f"{RULE_B['family_min_questions']} questions under {RULE_B['family_floor']:.0%}, and at "
             f"most {RULE_B['max_failed']:.0%} failed outright.", ""]
    for name, v in (("With alternates", with_v), ("Without alternates", without_v)):
        if v["reasons"]:
            lines.append(f"{name}, what does not hold: " + "; ".join(v["reasons"]) + ".")
    lines += ["", f"Correct {s['correct']} of {s['n']} ({_pct(s['correct'], s['n'])}), "
              f"{doc['scores']['without_alternates']['correct']} without alternates; failed outright "
              f"{s['failed']} ({_pct(s['failed'], s['n'])}); median wall time "
              f"{_cell(s['median_s'])} s.", ""]
    lines += ["## Per family", "", "| family | n | correct | rate | failed | first failing stages |",
              "|---|---|---|---|---|---|"]
    for family, f in s["families"].items():
        stages = ", ".join(f"{k} {v}" for k, v in sorted(f["first_failing_stages"].items(),
                                                         key=lambda kv: -kv[1])) or "-"
        lines.append(f"| {family} | {f['n']} | {f['correct']} | {_pct(f['correct'], f['n'])} | "
                     f"{f['failed']} | {stages} |")
    rr = s["rest_routed_today"]
    stages = ", ".join(f"{k} {v}" for k, v in sorted(rr["first_failing_stages"].items())) or "-"
    lines += ["", f"REST-routed lineage questions (routed to a REST endpoint today, answered here by "
              f"the forced graph agent): {rr['correct']} of {rr['n']} correct "
              f"({_pct(rr['correct'], rr['n'])}); first failing stages: {stages}.", ""]
    lines += _lists_section(doc["lists"])
    lines += _repeats_section(doc)
    lines += ["## Questions", ""]
    lines += _question_table(doc["questions"])
    return "\n".join(lines) + "\n"


def _repeats_section(doc: dict) -> list[str]:
    if not doc["repeats"]:
        return []
    lines = ["## Repeats", ""]
    for arm, r in doc["repeats"].items():
        lines.append(f"- {arm}: {r['agreeing']} of {r['repeat_attempts']} repeat attempts agree "
                     f"with the scored attempt on correctness.")
    return lines + [""]


CSV_FIELDS = ("id", "family", "source", "group", "arm", "outcome", "correct_primary",
              "correct_with_alternates", "which", "first_failing_stage", *[f"stage_{s}" for s in STAGES],
              "engine_value", "expected_value", "elapsed_s", "cost_usd", "parser_mode",
              "parser_chose", "non_retrieval", "contradiction_suspect", "void_reason",
              "failure_reason", "flags", "run_dir", "git_sha")


def _csv(rows) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({**{k: r.get(k) for k in CSV_FIELDS},
                         "correct_with_alternates": r.get("correct_any"),
                         **{f"stage_{s}": (r.get("stages") or {}).get(s) for s in STAGES},
                         "flags": ";".join(r.get("flags") or [])})
    return buf.getvalue()


def _write_private(path: Path, text: str) -> None:
    if not path.parent.is_dir():
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def write_reports(doc: dict, out_dir) -> list[Path]:
    out_dir = Path(out_dir)
    md = _md_a(doc) if doc["group"] == "A" else _md_b(doc)
    files = {"compare.json": json.dumps(doc, indent=2, default=str) + "\n",
             "compare.md": md, "questions.csv": _csv(doc["questions"])}
    for name, text in files.items():
        _write_private(out_dir / name, text)
    return [out_dir / n for n in files]


# ── the command line ─────────────────────────────────────────────────────────

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="engine_compare",
        description="Score the graph and API arms against ground truth (graph_search Nessie POC).")
    parser.add_argument("--group", required=True, choices=("a", "b", "A", "B"))
    parser.add_argument("--run", action="append", required=True, metavar="DIR",
                        help="a run directory; repeat to merge a pilot, blocks and repeats by id")
    parser.add_argument("--truth", help="the truth directory")
    parser.add_argument("--outputs", required=True,
                        help="the venue's outputs directory (mounted at /venue/outputs)")
    parser.add_argument("--prices", help="the price table (prices.json)")
    parser.add_argument("--ledger", help="the global llm_calls.jsonl (default: logs/ beside --outputs)")
    parser.add_argument("--out", help="where compare.json, compare.md and questions.csv go")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--cost-only", action="store_true",
                      help="print spend per run and in total, and per turn; write nothing")
    mode.add_argument("--checks", action="store_true",
                      help="print the stop-rule counts; write nothing")
    args = parser.parse_args(argv)
    out = sys.stdout

    try:
        prices = load_prices(args.prices) if args.prices else {}
    except (OSError, ValueError) as exc:
        parser.error(f"--prices: {exc}")
    try:
        run = load_runs(args.run)
    except (FileNotFoundError, ValueError) as exc:
        parser.error(str(exc))
    outputs = Path(args.outputs)
    if args.cost_only:
        if not prices:
            print("no --prices given: every turn is unmeasured", file=out)
        _print_cost(run, outputs, prices, args.ledger, out)
        return 0
    truth = {}
    if args.truth:
        try:
            truth = load_truth_questions(args.truth, args.group)
        except (OSError, ValueError) as exc:
            parser.error(f"--truth: {exc}")
    if args.checks:
        _print_checks(run, checks(run, truth, outputs, prices, ledger_path=args.ledger), out)
        return 0
    if not args.truth or not args.out:
        parser.error("scoring needs --truth and --out (or give --cost-only or --checks)")
    doc = score(run, truth, args.group, outputs, prices, ledger_path=args.ledger)
    paths = write_reports(doc, args.out)
    for key in ("with_alternates", "without_alternates"):
        print(f"Group {doc['group']} {key.replace('_', ' ')}: {doc['verdict'][key]['verdict']}",
              file=out)
    for path in paths:
        print(f"wrote {path}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The concrete `collect.Sources`: the three reads, done through the container.

`collect.py` takes its sources by injection and every unit test hands it a fake.
This is the one real implementation, and it reads everything through the running
`nextseek` container rather than talking to MySQL or the volume directly:

* the task rows and the transcripts come out of **the container's own Django
  ORM**, so no database credential ever reaches the host and the row shape is
  whatever the product's models say it is rather than a second SQL rendering of
  them that can drift;
* the trees come out of `docker cp`, because the `dmac-cc-users` volume is
  already mounted into that container at `/dmac/users` (docker-compose.yml:50).
  There is no second mount to arrange and no host path to guess.

The `nessie_tests` host lane runs in an isolated env holding only pytest,
pydantic, requests and beautifulsoup4. Nothing here adds to it: the only host
imports are the standard library, and every product import happens on the far
side of the boundary.

TRANSPORT, AND WHY IT IS INJECTED
---------------------------------
One seam, `run(argv, *, timeout) -> Completed`, is the only thing that touches
the outside world. The unit suite drives a fake runner and asserts on the
commands built and the output parsed, which is the same reason `collect` takes a
`Sources` one level up. Nothing in `nessie_tests/tests` starts a process.

The command SHAPE is `output-skill/scripts/fetch_run.py`'s, deliberately, not a
second invention: base64 the script and hand it to `bash -c`, where `--host ""`
means the LOCAL docker daemon and a non-empty `--host` means ssh (with an
optional `sudo -u`) first. `ssh localhost` is NOT the local path -- there is no
sshd -- which is exactly the bug that shape exists to have already fixed.

That seam only reaches the EDGE, though. Every decision the far side makes --
which rows exist, which spelling they come back under, how a multi-turn
transcript is folded -- is a plain function HERE (`canonical_task_ids`,
`project_task_rows`, `merge_transcripts`), embedded into the script by
`inspect.getsource`. Logic that lives only inside a string is logic no test can
reach, and these are the three places a wrong answer is silent rather than loud.

WHY EVERYTHING CROSSING THE BOUNDARY IS BASE64
----------------------------------------------
`cc_transcript` returns raw zstd bytes and `copy_tree` moves a tar stream, and
neither can travel as-is. The channel is a shared, line-oriented TEXT channel:
the payload shares stdout with framing markers, and `docker exec` / `ssh` /
`bash -c` are each free to interleave their own diagnostics on it. Raw zstd and
raw tar contain NUL, CR and LF bytes, so they would break the framing outright,
and any hop that translated line endings would corrupt them silently -- the worse
failure, because a truncated transcript still decompresses to something. Base64
makes the payload text, at 33% more bytes, and the decode happens on this side.

FOUR THINGS ABOUT THE PRODUCT, VERIFIED AGAINST THE LIVE CONTAINER
------------------------------------------------------------------
1.  `CCSessionTranscript.turn_id` is `str(run_id)` where `run_id =
    str(query_task.task_id)` (cc_engine.py:833, cc_assistant.py:415) -- a random
    UUID. It is a `CharField`, so `order_by("turn_id")` is LEXICOGRAPHIC over
    random UUIDs, which is a shuffle wearing an ordering's clothes. The honest
    key is `created_at` (tie-broken on `id`), and that is what is used.

2.  Each row's blob is the CUMULATIVE session `.jsonl`, not that turn's delta.
    `cc_state_mnt` is per CHAT SESSION (cc_provision.py:122), `--resume` keeps
    Claude appending to the one file under it, and `_newest_jsonl_under`
    (cc_engine.py:798) re-reads that whole grown file every turn. Checked on all
    four multi-turn sessions in the live database: turn 2's transcript
    `startswith` turn 1's, byte for byte, every time, and the `cc_session_id` is
    identical across the rows. So CONCATENATING the rows -- which is what the
    `Sources` docstring asks for -- would duplicate every earlier turn's lines.
    `merge_transcripts` resolves this by containment rather than by assumption;
    see it.

3.  `QueryTask` stores `status`, `progress` and `result` under exactly those
    names (assistant/models_db.py:57-59), so the contract's row shape is a
    straight projection and nothing has to be derived. `task_id` is a UUIDField,
    which is why `project_task_rows` maps back to the caller's own spelling and
    why a non-UUID id is dropped rather than handed to a lookup that would raise
    on the whole batch.

4.  `copy_tree`'s `src` is an absolute path INSIDE the container:
    `{user_mount}/scratch/{task_id}` and `{user_mount}/output/artifacts/{task_id}`,
    where `user_mount` is `/dmac/users/{project}/{user}`. Verified live --
    `docker cp nextseek:/dmac/users/1-published-data/demo/output/artifacts -`
    lists per-turn directories named by task UUID.
"""
from __future__ import annotations

import base64
import dataclasses
import inspect
import io
import json
import pathlib
import shlex
import shutil
import subprocess
import tarfile
import tempfile
import uuid

from nessie_tests import collect

DEFAULT_CONTAINER = "nextseek"
DEFAULT_APP_DIR = "/app"
# Generous: one `task_rows` call carries every progress event of ~158 turns, and
# a `docker cp` of a run_root is bounded only by what the run wrote. A timeout is
# still a timeout -- it becomes a loud failure, never a quiet empty result.
DEFAULT_TIMEOUT_S = 900

# Framing. `uv run`, ssh and docker all feel free to write to the stream, so the
# payload is everything AFTER this line and nothing before it is parsed.
MARKER = "@@@NESSIE-SOURCES@@@"

_COPY_PREFIX = "@@@COPY:"
COPIED, ABSENT, FAILED, UNREACHABLE = "COPIED", "ABSENT", "FAILED", "UNREACHABLE"

# The archive is DELIMITED at both ends, not taken as "whatever stdout held".
# An ssh banner, a sudo lecture or a docker deprecation notice printed before the
# payload would otherwise be concatenated into it, and `b64decode` is happy to
# fold stray characters in and hand back a corrupt tar -- which surfaces as a
# `tarfile.ReadError` from somewhere else entirely. This bites hardest on the
# `--host` path, which is precisely the path with the most hops that can talk.
_COPY_BEGIN = "@@@COPY-ARCHIVE@@@"


class SourcesUnavailable(RuntimeError):
    """The TRANSPORT did not work: no container, no daemon, no Django, no MySQL.

    Distinct from an empty result, and it RAISES rather than returning one. A
    `task_rows` that answered `{}` when the database was unreachable would put
    254 "no assistant_query_task row for this task_id" misses into
    `collection.json`, which reads exactly like a product that lost every row.
    The same split `collect.CopyFailed` draws for the trees.
    """


@dataclasses.dataclass(frozen=True)
class Completed:
    """What a runner returns. `subprocess.CompletedProcess`'s three fields."""

    returncode: int
    stdout: bytes
    stderr: bytes


def subprocess_runner(argv, *, timeout: float = DEFAULT_TIMEOUT_S) -> Completed:
    """The real transport. `stdin` is closed: every input travels in the argv."""
    proc = subprocess.run(
        list(argv), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=timeout,
    )
    return Completed(proc.returncode, proc.stdout, proc.stderr)


def canonical_task_ids(requested):
    """`{canonical uuid: [the caller's own spellings of it]}`.

    `QueryTask.task_id` is a UUIDField, so the lookup wants canonical
    lowercase-hyphenated ids while the ANSWER has to come back under whatever the
    manifest wrote -- key on the canonical form and any id the manifest spelled
    differently is silently lost from the mapping.

    An id that is not a UUID at all is DROPPED rather than raised on: no row can
    exist for it, one malformed id must not cost the other 253 theirs, and
    "absent from the mapping" is exactly what the contract says an id with no row
    looks like. A UUIDField lookup would instead raise on the whole batch.

    Embedded into the in-container script; see `_container_script`.
    """
    out = {}
    for raw in requested:
        try:
            canon = str(uuid.UUID(str(raw)))
        except (ValueError, AttributeError, TypeError):
            continue
        out.setdefault(canon, []).append(raw)
    return out


def project_task_rows(requested, rows):
    """`QueryTask` rows -> the contract's `{task_id: row}`, and NOTHING else.

    Driven by the ROWS, never by the requested ids: an id the database did not
    answer for gets no entry, because the alternative -- a placeholder row -- is
    how "the run lost this turn" becomes "the turn completed with no events" four
    steps later, on a page a human is grading.

    The three fields are `QueryTask`'s own (`status`, `progress`, `result`,
    assistant/models_db.py:57-59), so this is a projection and not a derivation;
    there is no fourth field to reconstruct.

    Embedded into the in-container script; see `_container_script`.
    """
    by_canon = canonical_task_ids(requested)
    out = {}
    for row in rows:
        for orig in by_canon.get(str(row["task_id"]), []):
            out[orig] = {"status": row["status"],
                         "progress": row["progress"],
                         "result": row["result"]}
    return out


def merge_transcripts(chunks):
    """Fold one chat session's transcript rows into ONE session `.jsonl`.

    Resolved by CONTAINMENT, not by a rule about what the product does, because
    the product does two different things and the difference is invisible in the
    row:

    * WITHIN one `cc_session_id` the rows are cumulative snapshots of the same
      growing file (fact 2 in the module docstring), so row N already contains
      rows 1..N-1 and appending it would duplicate them;
    * ACROSS `cc_session_id`s -- a wiped cc-state store makes the next turn start
      fresh (cc_engine.py:632) -- the rows are disjoint segments of the session
      and dropping any of them would lose a turn.

    So: a chunk that starts with everything gathered so far REPLACES it, and any
    other chunk is appended. Both cases fall out of the same line, no branch is
    taken on a guess about which one is happening, and a future product change
    between them needs no change here.

    Embedded verbatim into the in-container script (see `_container_script`) so
    the decision is unit-tested on this side rather than living only inside a
    string. `test_sources.py` pins that the two cannot drift apart.
    """
    out = b""
    for chunk in chunks:
        if not chunk:
            continue
        if chunk.startswith(out):
            out = chunk
        else:
            if out and not out.endswith(b"\n"):
                out += b"\n"
            out += chunk
    return out


# The far side of the boundary. Placeholders rather than `.format`, because the
# body is Python and doubling every brace in it would make it unreadable and
# unreviewable.
_CONTAINER_PY = '''\
import base64, json, os, sys, uuid

sys.path.insert(0, "__APP_DIR__")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
import django
django.setup()

from nextseek_api.assistant.models_db import CCSessionTranscript, QueryTask
from nextseek_api.cc_assistant import cc_transcript_store

__CANON_SRC__

__PROJECT_SRC__

__MERGE_SRC__

REQ = json.loads(base64.b64decode("__REQ_B64__").decode("utf-8"))
OP = REQ["op"]

if OP == "ping":
    # Counts, not a bare "yes": they prove the ORM reached MySQL and came back,
    # which a successful `import django` alone does not.
    OUT = {"query_tasks": QueryTask.objects.count(),
           "cc_transcripts": CCSessionTranscript.objects.count()}

elif OP == "task_rows":
    # The query is the ONLY thing this side owns; both halves around it are
    # host-side functions embedded above, so the "never invent a row" rule is
    # under test rather than living inside a string.
    OUT = project_task_rows(
        REQ["task_ids"],
        list(QueryTask.objects
             .filter(task_id__in=list(canonical_task_ids(REQ["task_ids"])))
             .values("task_id", "status", "progress", "result")))

elif OP == "cc_transcript":
    # The NExtSEEK ChatSession id, NOT cc_session_id. A malformed one would make
    # the UUIDField lookup raise; it can have no rows, so it has none.
    sid = REQ["session_id"]
    OUT = None
    try:
        uuid.UUID(str(sid))
    except (ValueError, AttributeError, TypeError):
        rows = []
    else:
        rows = list(CCSessionTranscript.objects.filter(chat_session_id=sid)
                    .order_by("created_at", "id"))
    if rows:
        merged = merge_transcripts(
            [cc_transcript_store.decompress(bytes(r.blob)) for r in rows])
        if merged:
            # Recompressed to ONE frame. The rows are separate zstd frames and
            # `collect.decompress_transcript` opens a single stream over what it
            # is handed, so shipping the frames end to end would decompress to
            # the first turn alone under any zstandard whose stream reader does
            # not cross frames -- a truncation that still parses as jsonl.
            OUT = base64.b64encode(
                cc_transcript_store.compress(merged)).decode("ascii")

else:
    raise SystemExit("unknown op: %r" % (OP,))

sys.stdout.write("__MARKER__\\n")
sys.stdout.write(json.dumps(OUT, default=str))
sys.stdout.write("\\n")
'''


def _container_script(request: dict, app_dir: str) -> str:
    """The Python to run inside the container, with `merge_transcripts` inlined."""
    if '"' in app_dir or "'" in app_dir:
        raise ValueError(f"app_dir must not carry quotes: {app_dir!r}")
    req_b64 = base64.b64encode(
        json.dumps(request).encode("utf-8")).decode("ascii")
    script = (_CONTAINER_PY
              .replace("__APP_DIR__", app_dir)
              .replace("__REQ_B64__", req_b64)
              .replace("__MARKER__", MARKER))
    # Last, so a placeholder appearing in any function's own source cannot be
    # substituted a second time.
    for token, func in (("__CANON_SRC__", canonical_task_ids),
                        ("__PROJECT_SRC__", project_task_rows),
                        ("__MERGE_SRC__", merge_transcripts)):
        script = script.replace(token, inspect.getsource(func))
    return script


def _shell_argv(script: str, host: str, user: str) -> list[str]:
    """`fetch_run.run_remote`'s shape, and for its reasons.

    Base64 so that ssh/sudo/bash quoting cannot mangle the script. `host=""` runs
    against the LOCAL docker daemon rather than SSHing; `ssh localhost` is not a
    fallback, because there is no sshd. The script body is identical either way.
    """
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")
    if not host:
        return ["bash", "-c", f"echo {b64} | base64 -d | bash"]
    argv = ["ssh", "-o", "ConnectTimeout=30", host]
    if user:
        argv += ["sudo", "-n", "-u", user]
    return argv + ["bash", "-c", f'"echo {b64} | base64 -d | bash"']


def _tail(raw: bytes, limit: int = 600) -> str:
    text = raw.decode("utf-8", errors="replace").strip()
    return text[-limit:]


def _extract(archive: bytes, dest: pathlib.Path) -> None:
    """Unpack a `docker cp` tar so that `src`'s CONTENTS land at `dest`.

    `docker cp <c>:/a/b -` emits an archive rooted at `b/`, so extracting it
    where `dest` lives would produce `dest.parent/b` and the caller's chosen name
    would be lost -- and every one of `collect`'s destinations is a name it chose
    (`run_root`, `cc_scratch/<task_id>`). The single top-level entry is therefore
    staged and then moved onto `dest`.

    Staged in a sibling of `dest` so the move is a same-filesystem rename;
    `/tmp` would silently degrade to a whole-tree copy across a mount boundary.
    An existing `dest` is REPLACED, because collection is designed to be re-run.

    RAISES ONLY `CopyFailed`. `filter="data"` refuses an absolute symlink, and a
    CC scratch dir holding a venv (`bin/python -> /usr/bin/python3`) has one, so
    `AbsoluteLinkError` is a REALISTIC input here rather than a theoretical one.
    Left to propagate it would abort a 254-arm collection over one arm. The
    filter is kept -- extracting an absolute symlink out of a container onto this
    filesystem is not something to do quietly -- and the refusal is reported as
    what it is: a copy this collector could not perform.
    """
    dest = pathlib.Path(dest)
    staging = None
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        staging = pathlib.Path(tempfile.mkdtemp(prefix=".nessie-cp-",
                                                dir=dest.parent))
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r|*") as tf:
            try:
                tf.extractall(staging, filter="data")
            except TypeError:  # pragma: no cover - pythons before the filter arg
                tf.extractall(staging)
        entries = list(staging.iterdir())
        if len(entries) != 1:
            raise collect.CopyFailed(
                f"the `docker cp` archive held {len(entries)} top-level entries; "
                f"exactly one (the source's own basename) was expected")
        if dest.is_symlink() or dest.is_file():
            dest.unlink()
        elif dest.is_dir():
            shutil.rmtree(dest)
        shutil.move(str(entries[0]), str(dest))
    except collect.CopyFailed:
        raise
    except Exception as exc:
        raise collect.CopyFailed(
            f"unpacking the archive onto {dest} failed: "
            f"{type(exc).__name__}: {exc}") from exc
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


class DockerSources:
    """`collect.Sources` over a running container. See the module docstring.

    `run` is the injected transport, `run(argv, *, timeout) -> Completed`. It
    defaults to `subprocess_runner`; the unit suite passes a fake and no test
    starts a process.
    """

    def __init__(self, *, container: str = DEFAULT_CONTAINER, host: str = "",
                 user: str = "", app_dir: str = DEFAULT_APP_DIR, run=None,
                 timeout: float = DEFAULT_TIMEOUT_S):
        self.container = container
        self.host = host
        self.user = user
        self.app_dir = app_dir
        self.timeout = timeout
        self._run = run or subprocess_runner

    # --- transport ---------------------------------------------------------
    def _execute(self, script: str) -> Completed:
        return self._run(_shell_argv(script, self.host, self.user),
                         timeout=self.timeout)

    def _query(self, request: dict):
        """Run one op inside the container and return its decoded payload."""
        out = self._execute(self._python_command(request))
        text = out.stdout.decode("utf-8", errors="replace")
        if MARKER not in text:
            raise SourcesUnavailable(
                f"{request['op']}: the container produced no result "
                f"(rc={out.returncode}, container={self.container!r}, "
                f"host={self.host or 'local'}).\n"
                f"stderr: {_tail(out.stderr)}\nstdout: {_tail(out.stdout)}")
        body = text.split(MARKER, 1)[1].strip()
        try:
            return json.loads(body or "null")
        except ValueError as exc:
            raise SourcesUnavailable(
                f"{request['op']}: the result after {MARKER} was not JSON: "
                f"{exc}\n{body[:600]}") from exc

    def _python_command(self, request: dict) -> str:
        """The bash that feeds the Python script into the container's stdin.

        Piped in rather than written to a file: this is a READ of a live stack
        and it must leave nothing behind in the container to clean up.
        """
        py_b64 = base64.b64encode(
            _container_script(request, self.app_dir).encode("utf-8")).decode("ascii")
        inner = shlex.quote(f"cd {shlex.quote(self.app_dir)} && uv run python -")
        return (
            "set -u\n"
            f"printf %s {shlex.quote(py_b64)} | base64 -d | "
            f"docker exec -i {shlex.quote(self.container)} sh -c {inner}\n"
        )

    # --- the contract ------------------------------------------------------
    def ping(self) -> dict:
        """Prove the whole chain works BEFORE a collection commits to its results.

        Without it a dead container or an unreachable database produces a
        complete-looking `collection.json` in which every single artifact is
        recorded missing -- which is indistinguishable, on the page, from a
        product that produced nothing.
        """
        return self._query({"op": "ping"}) or {}

    def task_rows(self, task_ids: list[str]) -> dict[str, dict]:
        """`{task_id: {"status", "progress", "result"}}`, absent ids simply absent.

        One round trip for the whole batch: a paired run's ~158 turns are ~254
        ids, and a query per id would be 254 container starts for one join.
        """
        ids = [str(t) for t in (task_ids or []) if t]
        if not ids:
            return {}
        rows = self._query({"op": "task_rows", "task_ids": ids})
        return dict(rows or {})

    def cc_transcript(self, session_id: str) -> bytes | None:
        """The session's transcript as ONE zstd blob, or None when there is none.

        `session_id` is the NExtSEEK `ChatSession` id. `collect` passes `""` when
        it could not find one on any of the arm's turns; that is not a question
        worth a round trip, and it has the same answer either way.
        """
        if not session_id:
            return None
        blob_b64 = self._query({"op": "cc_transcript",
                                "session_id": str(session_id)})
        if not blob_b64:
            return None
        return base64.b64decode(blob_b64)

    def copy_tree(self, src: str, dest: pathlib.Path) -> bool:
        """`docker cp` the tree at `src` out of the container and onto `dest`.

        True copied / False absent / `collect.CopyFailed` when the copy MECHANISM
        broke -- the split that whole exception exists for.

        The classification does NOT read `docker cp`'s exit code, which cannot
        carry it: `docker exec <missing-container> true` and `docker exec <live>
        test -e <missing-path>` BOTH exit 1, so "the container is gone" and "the
        scratch was reaped" would be one outcome. On a failed copy a second probe
        runs inside the container and ECHOES which of the three it is, so the
        answer comes from the container's own filesystem rather than from an
        exit code that conflates them.

        The archive is streamed to stdout rather than to a path on the docker
        host, so `--host` works exactly like the local case instead of leaving
        the collected tree on the far side of the ssh hop.

        THOSE THREE OUTCOMES ARE THE ONLY THINGS THAT ESCAPE. Every other failure
        -- a `subprocess.TimeoutExpired` out of the runner, a `tarfile.ReadError`
        from a corrupted stream, an `AbsoluteLinkError` from a venv symlink in a
        CC scratch dir, an `OSError` from a full disk -- is re-raised as
        `CopyFailed`, because `collect._copy_into` catches only that and anything
        else propagates out of `collect.collect`. One such arm out of 254 then
        aborts the whole collection: no `collection.json` is written and
        `artifacts/` is left half-written, which `export` cannot detect at all
        (it warns only when the tree is ABSENT) and so exports silently. That is
        the exact failure `collect`'s docstring means by "NOTHING here raises on
        a missing artifact", and it is this method's job to keep true.
        """
        try:
            out = self._execute(self._copy_script(src))
        except collect.CopyFailed:
            raise
        except Exception as exc:
            raise collect.CopyFailed(
                f"the transport failed while copying {src}: "
                f"{type(exc).__name__}: {exc}") from exc
        lines = out.stdout.decode("utf-8", errors="replace").splitlines()
        status, cut = "", len(lines)
        for i in range(len(lines) - 1, -1, -1):
            line = lines[i].strip()
            if line.startswith(_COPY_PREFIX):
                status = line[len(_COPY_PREFIX):].rstrip("@")
                cut = i
                break

        if status == ABSENT:
            return False
        if status == COPIED:
            try:
                begin = lines.index(_COPY_BEGIN)
            except ValueError:
                raise collect.CopyFailed(
                    f"`docker cp` reported success for {src} but the archive was "
                    f"not delimited; the stream is not what this expects") from None
            body = "".join(ln.strip() for ln in lines[begin + 1:cut])
            if not body:
                raise collect.CopyFailed(
                    f"`docker cp` reported success for {src} but sent no archive")
            try:
                # `validate=True`: with the payload delimited there is nothing
                # legitimate left to discard, so a stray character is evidence
                # that the stream is corrupt and must not be quietly dropped into
                # a tar that then fails to open somewhere else.
                archive = base64.b64decode(body, validate=True)
            except Exception as exc:
                raise collect.CopyFailed(
                    f"the archive for {src} did not decode: "
                    f"{type(exc).__name__}: {exc}") from exc
            _extract(archive, pathlib.Path(dest))
            return True
        if status == FAILED:
            raise collect.CopyFailed(
                f"`docker cp` failed while {src} was present in {self.container}: "
                f"{_tail(out.stderr)}")
        if status == UNREACHABLE:
            raise collect.CopyFailed(
                f"container {self.container!r} did not answer a probe, so whether "
                f"{src} exists is unknown: {_tail(out.stderr)}")
        raise collect.CopyFailed(
            f"the copy of {src} produced no status marker (rc={out.returncode}): "
            f"{_tail(out.stderr)}")

    def _copy_script(self, src: str) -> str:
        """`docker cp` to stdout as base64, delimited, then the outcome.

        The status marker TRAILS the payload so nothing has to be buffered to a
        file on the docker host; a `_COPY_BEGIN` line LEADS it so anything the
        transport printed first cannot be read as part of the archive.
        """
        cref = shlex.quote(f"{self.container}:{src}")
        cont = shlex.quote(self.container)
        probe = shlex.quote(
            f"if [ -e {shlex.quote(src)} ]; then echo PRESENT; else echo ABSENT; fi")
        return (
            "set -u\n"
            f"printf '%s\\n' '{_COPY_BEGIN}'\n"
            f"docker cp {cref} - 2>/dev/null | base64 -w0\n"
            "rc=${PIPESTATUS[0]}\n"
            "printf '\\n'\n"
            'if [ "$rc" -eq 0 ]; then\n'
            f"  printf '%s\\n' '{_COPY_PREFIX}{COPIED}@@@'\n"
            "else\n"
            f"  probe=$(docker exec {cont} sh -c {probe} 2>/dev/null) || probe=''\n"
            '  if [ "$probe" = PRESENT ]; then\n'
            f"    printf '%s\\n' '{_COPY_PREFIX}{FAILED}@@@'\n"
            '  elif [ "$probe" = ABSENT ]; then\n'
            f"    printf '%s\\n' '{_COPY_PREFIX}{ABSENT}@@@'\n"
            "  else\n"
            f"    printf '%s\\n' '{_COPY_PREFIX}{UNREACHABLE}@@@'\n"
            "  fi\n"
            "fi\n"
        )

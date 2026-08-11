"""#72: the user's plaintext password must not survive anywhere in the CC
transcript chain.

Commit 83b8b99 added ``_scrub_secret_bytes`` and cleaned the two DERIVED sinks
(the ``raw/`` on-disk copy and the ``assistant_cc_transcript`` DB blob). It left
the SOURCE untouched: the per-session jsonl in the ``cc-state`` volume, which is
deliberately never deleted because ``--resume`` needs it. Two production paths
then re-read that unscrubbed source:

* ``cc_memory_io.stage_transcripts`` copies it into a dir mounted READ-ONLY into
  LATER agent containers at ``/home/user/.cc-memory/transcripts``;
* ``cc_sweep`` reads it raw and feeds it to the summarizer, whose output lands
  in the merged ``CLAUDE.md``.

And the scrub itself only matched the UTF-8 plaintext, so the encoding a real
``curl -v`` actually emits — ``Authorization: Basic <base64(user:pass)>`` —
passed through untouched.

Hermetic: tmp_path + stdlib only, no docker, no DB.
"""

import ast
import base64
from pathlib import Path

from nextseek_api.cc_assistant import cc_engine, cc_memory_io

PW = "hunter2-s3cr3t"
USER = "demo"
ENV = {
    "NEXTSEEK_USERNAME": USER,
    "API_USER": USER,
    "NEXTSEEK_PASSWORD": PW,
    "API_PASS": PW,
}

BASIC = base64.b64encode(f"{USER}:{PW}".encode()).decode()

# One realistic turn: an `env` dump, a `curl -v` Basic header, and a URL with
# userinfo. Only the first of the three was covered before this fix.
TRANSCRIPT = (
    '{"type":"assistant","message":{"content":"checking"}}\n'
    '{"type":"user","message":{"content":[{"type":"tool_result","content":'
    f'"NEXTSEEK_PASSWORD={PW}\\nAPI_USER={USER}\\n"'
    "}]}}\n"
    '{"type":"user","message":{"content":[{"type":"tool_result","content":'
    f'"> Authorization: Basic {BASIC}\\r\\n"'
    "}]}}\n"
    '{"type":"user","message":{"content":[{"type":"tool_result","content":'
    f'"curl https://{USER}:{PW}@nextseek_nginx/nextseek_api/samples/"'
    "}]}}\n"
).encode()


def _leaks(blob: bytes) -> list[str]:
    """Every encoding of the password that must not survive."""
    found = []
    if PW.encode() in blob:
        found.append("plaintext")
    if BASIC.encode() in blob:
        found.append("base64-basic")
    if BASIC.rstrip("=").encode() in blob:
        found.append("base64-basic-unpadded")
    return found


def _write_store(root: Path, name: str = "sess-a.jsonl") -> Path:
    """Lay out a cc-state store the way the engine does: <cc_state>/projects/**."""
    path = root / "projects" / "-home-user" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(TRANSCRIPT)
    return path


# ---------------------------------------------------------------------------
# The encodings themselves
# ---------------------------------------------------------------------------


def test_base64_basic_auth_pair_is_scrubbed():
    """A `curl -v` never prints the plaintext — it prints base64(user:pass)."""
    out = cc_engine._scrub_secret_bytes(TRANSCRIPT, ENV)
    assert BASIC.encode() not in out
    assert b"<REDACTED>" in out


def test_url_encoded_password_is_scrubbed():
    pw = "p@ss/w rd+1"
    env = {"NEXTSEEK_USERNAME": USER, "NEXTSEEK_PASSWORD": pw}
    from urllib.parse import quote

    encoded = quote(pw, safe="").encode()
    assert encoded != pw.encode(), "test value must actually percent-encode"
    raw = b'{"tool_result":"curl https://demo:' + encoded + b'@host/"}'
    out = cc_engine._scrub_secret_bytes(raw, env)
    assert encoded not in out
    assert pw.encode() not in out


def test_unpadded_base64_is_scrubbed():
    unpadded = BASIC.rstrip("=")
    raw = b'{"tool_result":"Basic ' + unpadded.encode() + b'"}'
    out = cc_engine._scrub_secret_bytes(raw, ENV)
    assert unpadded.encode() not in out


def test_scrub_is_idempotent_and_leaves_nonsecrets_alone():
    once = cc_engine._scrub_secret_bytes(TRANSCRIPT, ENV)
    assert cc_engine._scrub_secret_bytes(once, ENV) == once
    assert USER.encode() in once, "the non-secret username must survive"


def test_scrub_without_a_username_still_removes_plaintext():
    """No username in env -> no Basic pair to build, but plaintext still goes."""
    out = cc_engine._scrub_secret_bytes(TRANSCRIPT, {"NEXTSEEK_PASSWORD": PW})
    assert PW.encode() not in out


# ---------------------------------------------------------------------------
# Gap 1: the SOURCE transcript
# ---------------------------------------------------------------------------


def test_source_transcript_is_scrubbed_in_place(tmp_path):
    path = _write_store(tmp_path)
    assert _leaks(path.read_bytes()), "fixture must start dirty"

    rewritten = cc_engine.scrub_transcript_store(tmp_path, ENV)

    assert rewritten == 1
    assert _leaks(path.read_bytes()) == []


def test_scrubbed_source_stays_valid_jsonl_for_resume(tmp_path):
    """--resume parses this file; the rewrite must not corrupt its structure."""
    import json

    path = _write_store(tmp_path)
    cc_engine.scrub_transcript_store(tmp_path, ENV)

    lines = [ln for ln in path.read_bytes().splitlines() if ln.strip()]
    assert len(lines) == 4
    for line in lines:
        json.loads(line)  # raises if the rewrite broke escaping


def test_source_scrub_is_a_noop_when_nothing_leaked(tmp_path):
    path = tmp_path / "projects" / "clean.jsonl"
    path.parent.mkdir(parents=True)
    clean = b'{"type":"assistant","message":{"content":"hello"}}\n'
    path.write_bytes(clean)

    assert cc_engine.scrub_transcript_store(tmp_path, ENV) == 0
    assert path.read_bytes() == clean


def test_source_scrub_tolerates_a_missing_store(tmp_path):
    assert cc_engine.scrub_transcript_store(tmp_path / "nope", ENV) == 0


def test_source_scrub_covers_every_session_in_the_store(tmp_path):
    """Not just the newest — older turns' files linger and are re-read too."""
    a = _write_store(tmp_path, "sess-a.jsonl")
    b = _write_store(tmp_path, "sess-b.jsonl")

    assert cc_engine.scrub_transcript_store(tmp_path, ENV) == 2
    assert _leaks(a.read_bytes()) == []
    assert _leaks(b.read_bytes()) == []


def test_scrub_runs_in_the_finally_so_failed_turns_are_covered():
    """A turn that errored/timed out still leaves a jsonl behind holding
    whatever the agent echoed before it died, and that file survives for
    --resume. Structural (AST) rather than substring: the call must be in the
    finalbody of run_cc_turn's try, not merely somewhere in the function."""
    src = Path(cc_engine.__file__).with_suffix(".py").read_text()
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "run_cc_turn"
    )
    tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try) and n.finalbody]
    called = {
        node.func.id
        for t in tries
        for stmt in t.finalbody
        for node in ast.walk(stmt)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "scrub_transcript_store" in called, (
        "run_cc_turn must scrub the transcript store in a finally block, so an "
        "errored or timed-out turn does not leave plaintext in cc-state"
    )


# ---------------------------------------------------------------------------
# Gap 2a: the staged copy mounted RO into LATER agent containers
# ---------------------------------------------------------------------------


class _Meta:
    def __init__(self, session_id, transcript_path):
        self.session_id = session_id
        self.transcript_path = str(transcript_path)


def test_staged_copy_for_the_next_agent_is_scrubbed(tmp_path):
    source = _write_store(tmp_path / "state")
    staging = tmp_path / "staging"

    out = cc_memory_io.stage_transcripts(
        [_Meta("sess-a", source)], staging,
        scrub=cc_engine.transcript_scrubber(ENV),
    )

    assert out == staging
    staged = staging / "sess-a.jsonl"
    assert _leaks(staged.read_bytes()) == []
    assert b"<REDACTED>" in staged.read_bytes()


def test_staged_copy_is_not_rewritten_every_turn(tmp_path):
    """Copy-on-change compares against the SCRUBBED length; comparing against
    the source length would make a scrubbed file look changed forever."""
    source = _write_store(tmp_path / "state")
    staging = tmp_path / "staging"
    scrub = cc_engine.transcript_scrubber(ENV)

    cc_memory_io.stage_transcripts([_Meta("sess-a", source)], staging, scrub=scrub)
    first = (staging / "sess-a.jsonl").stat().st_mtime_ns

    cc_memory_io.stage_transcripts([_Meta("sess-a", source)], staging, scrub=scrub)
    assert (staging / "sess-a.jsonl").stat().st_mtime_ns == first


def test_stage_transcripts_without_a_scrubber_is_unchanged(tmp_path):
    """Back-compat: the pruning/copy contract is untouched when scrub is None."""
    source = _write_store(tmp_path / "state")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "stale.jsonl").write_bytes(b"{}")

    out = cc_memory_io.stage_transcripts([_Meta("sess-a", source)], staging)

    assert out == staging
    assert (staging / "sess-a.jsonl").read_bytes() == TRANSCRIPT
    assert not (staging / "stale.jsonl").exists()


def test_staging_prunes_scrubbed_files_not_in_the_window(tmp_path):
    source = _write_store(tmp_path / "state")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "gone.jsonl").write_bytes(b"{}")

    cc_memory_io.stage_transcripts(
        [_Meta("sess-a", source)], staging, scrub=cc_engine.transcript_scrubber(ENV)
    )

    assert not (staging / "gone.jsonl").exists()


# ---------------------------------------------------------------------------
# Gap 2b: what the Celery sweep feeds to the summarizer
# ---------------------------------------------------------------------------


def test_sweep_reads_clean_bytes_after_the_source_scrub(tmp_path):
    """cc_sweep has no credentials of its own (it iterates every user with no
    request in scope), so it CANNOT scrub at its own read point. Its safety
    comes entirely from the source being clean on disk — this asserts exactly
    the read cc_sweep._run_sweep performs: Path(transcript_path).read_bytes().
    """
    path = _write_store(tmp_path)
    cc_engine.scrub_transcript_store(tmp_path, ENV)

    raw = Path(path).read_bytes()  # the literal cc_sweep.py:39 read

    assert _leaks(raw) == []
    assert b"<REDACTED>" in raw

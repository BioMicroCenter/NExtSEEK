"""#72 residual: the in-request sync summarizer must not be handed an unscrubbed
transcript.

``_start_task`` picks a sync target with ``cc_memory.select_sync_target(metas,
current_id=cc_state_key)`` — deliberately the most recently changed session that
is NOT the current one. The engine's in-place source scrub
(``cc_engine.scrub_transcript_store``) only runs in the ``finally`` of a turn
that THAT session itself ran, so the target's transcript can predate the scrub
entirely, or belong to a chat that never runs another turn.

Those bytes then flow into ``cc_summary.summarize_transcript`` -> a third-party
summarizer model -> ``extra_state["summary"]`` -> the merged ``CLAUDE.md`` that
is mounted into later agent containers. The credentials needed to scrub them are
in scope at the call site: twenty-six lines below, the staging copy of the very
same transcripts is already scrubbed with exactly
``{"NEXTSEEK_PASSWORD": user_api_pass, "API_PASS": user_api_pass}``.

Hermetic: tmp_path + monkeypatch, no DB, no docker, no network (both the
summarizer LLM call and the ORM write are patched out).
"""

import ast
import base64
from pathlib import Path

import nextseek_api.services.cc_assistant as cc_svc
from nextseek_api.cc_assistant import cc_engine, cc_summary

PW = "hunter2-s3cr3t"
USER = "demo"
ENV = {
    "NEXTSEEK_USERNAME": USER,
    "NEXTSEEK_PASSWORD": PW,
    "API_PASS": PW,
}
BASIC = base64.b64encode(f"{USER}:{PW}".encode()).decode()

TRANSCRIPT = (
    '{"type":"assistant","message":{"content":"checking"}}\n'
    '{"type":"user","message":{"content":[{"type":"tool_result","content":'
    f'"NEXTSEEK_PASSWORD={PW}\\n"'
    "}]}}\n"
    '{"type":"user","message":{"content":[{"type":"tool_result","content":'
    f'"> Authorization: Basic {BASIC}\\r\\n"'
    "}]}}\n"
).encode()


def _leaks(blob: bytes) -> list[str]:
    found = []
    if PW.encode() in blob:
        found.append("plaintext")
    if BASIC.encode() in blob:
        found.append("base64-basic")
    return found


class _Tgt:
    """The shape ``cc_memory.select_sync_target`` returns (a SessionMeta)."""

    def __init__(self, path):
        self.session_id = "sess-other"
        self.transcript_path = str(path)
        self.summary = {"claude_session_id": "claude-1"}


class _Summary:
    def model_dump(self):
        return {"gist": "g", "items": []}


def _fixture(tmp_path, monkeypatch):
    src = tmp_path / "sess-other.jsonl"
    src.write_bytes(TRANSCRIPT)
    seen = {}

    def fake_summarize(raw, provenance, cfg, **kwargs):
        seen["raw"] = raw
        return _Summary()

    def fake_persist(user, session_id, summary_dict, fp):
        seen["persisted"] = (session_id, summary_dict, fp)

    monkeypatch.setattr(cc_summary, "summarize_transcript", fake_summarize)
    monkeypatch.setattr(cc_svc, "_persist_summary_standalone", fake_persist)
    return src, _Tgt(src), seen


def test_sync_summarizer_never_sees_the_password(tmp_path, monkeypatch):
    src, tgt, seen = _fixture(tmp_path, monkeypatch)
    assert _leaks(src.read_bytes()), "fixture must start dirty"

    ok = cc_svc._summarize_sync_target(
        None, tgt, None, cc_engine.transcript_scrubber(ENV)
    )

    assert ok is True
    assert _leaks(seen["raw"]) == []
    assert b"<REDACTED>" in seen["raw"]


def test_sync_summary_fingerprint_still_tracks_the_file_on_disk(tmp_path, monkeypatch):
    """The persisted fingerprint must be of the RAW bytes, not the scrubbed ones.

    ``_session_metas`` and ``cc_sweep`` both fingerprint the file exactly as it
    lies on disk. Persisting a fingerprint of the scrubbed bytes would make
    every later comparison mismatch, so the same session would be re-summarized
    (a paid LLM call) on every single turn, forever.
    """
    src, tgt, seen = _fixture(tmp_path, monkeypatch)

    cc_svc._summarize_sync_target(None, tgt, None, cc_engine.transcript_scrubber(ENV))

    _sid, _summary, fp = seen["persisted"]
    assert fp == cc_summary.fingerprint(src.read_bytes())
    assert fp != cc_summary.fingerprint(seen["raw"]), (
        "the scrub must actually have changed the bytes for this test to mean "
        "anything"
    )


def test_sync_summarize_without_a_scrubber_still_works(tmp_path, monkeypatch):
    """Defensive: a caller with no credentials in scope passes scrub=None."""
    src, tgt, seen = _fixture(tmp_path, monkeypatch)

    assert cc_svc._summarize_sync_target(None, tgt, None, None) is True
    assert seen["raw"] == src.read_bytes()


def test_sync_summarize_failure_is_swallowed(tmp_path, monkeypatch):
    """A summarizer failure must never fail the user's turn — and must not
    report success, or the caller would re-derive metas for nothing."""
    src, tgt, seen = _fixture(tmp_path, monkeypatch)
    tgt.transcript_path = str(tmp_path / "does-not-exist.jsonl")

    assert cc_svc._summarize_sync_target(None, tgt, None, None) is False
    assert "raw" not in seen


# ---------------------------------------------------------------------------
# The call site
# ---------------------------------------------------------------------------


def _start_task_ast() -> ast.FunctionDef:
    src = Path(cc_svc.__file__).with_suffix(".py").read_text()
    tree = ast.parse(src)
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_start_task"
    )


def _calls_named(fn: ast.FunctionDef, name: str) -> list[ast.Call]:
    out = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id == name:
            out.append(node)
        elif isinstance(func, ast.Attribute) and func.attr == name:
            out.append(node)
    return out


def test_start_task_summarizes_through_the_helper():
    calls = _calls_named(_start_task_ast(), "_summarize_sync_target")
    assert len(calls) == 1, (
        "_start_task must route the sync-target transcript read through "
        "_summarize_sync_target, which scrubs it"
    )


def test_start_task_gives_the_summarizer_the_staging_scrubber():
    """One scrubber, two consumers.

    The staging copy has been scrubbed since 11e0750; the summarizer had not.
    Binding both to the SAME local means a future edit cannot re-diverge them
    without the divergence being visible in one line of the diff.
    """
    fn = _start_task_ast()
    summarize = _calls_named(fn, "_summarize_sync_target")[0]
    stage = _calls_named(fn, "stage_transcripts")[0]

    stage_scrub = next(k.value for k in stage.keywords if k.arg == "scrub")
    assert isinstance(stage_scrub, ast.Name), (
        "bind the scrubber to a local so both consumers can share it"
    )
    passed = [a for a in summarize.args if isinstance(a, ast.Name)]
    passed += [k.value for k in summarize.keywords if isinstance(k.value, ast.Name)]
    assert stage_scrub.id in {n.id for n in passed}, (
        "the sync summarizer must be handed the same scrubber the staged "
        "transcripts get"
    )

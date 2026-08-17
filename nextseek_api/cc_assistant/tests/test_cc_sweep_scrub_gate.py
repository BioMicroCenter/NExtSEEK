"""#76: the Celery sweep must refuse to summarize an unverified transcript.

``cc_sweep._run_sweep`` runs on a beat (``beat_schedule`` 300 s,
``sweep_idle_seconds`` 900) with no request in scope. It therefore holds no user
credential and CANNOT scrub the bytes it is about to send — its safety depends
entirely on the turn that wrote the file having scrubbed it. That scrub lives in
the ``finally`` of a turn dispatched on a ``daemon=True`` thread, which a
gunicorn worker recycle or a SIGKILL skips outright.

What the sweep does with those bytes is the reason this matters:
``cc_summary.summarize_transcript`` (a third-party model) ->
``extra_state["summary"]`` -> the merged ``CLAUDE.md`` mounted into later agent
containers. So an unverified transcript is skipped, not summarized.

Hermetic: every Django/LLM seam is patched, including the ``User`` model itself,
so this needs no database.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from nextseek_api.cc_assistant import cc_engine, cc_memory, cc_summary, cc_sweep
from nextseek_api.cc_assistant import router as cc_router

PW = "hunter2-s3cr3t"
ENV = {"NEXTSEEK_USERNAME": "demo", "NEXTSEEK_PASSWORD": PW, "API_PASS": PW}
DIRTY = (
    '{"type":"user","message":{"content":[{"type":"tool_result","content":'
    f'"NEXTSEEK_PASSWORD={PW}"'
    "}]}}\n"
).encode()


class _Summary:
    def model_dump(self):
        return {"gist": "g", "items": []}


class _FakeUser:
    username = "demo"


class _FakeUserModel:
    class objects:
        @staticmethod
        def all():
            return [_FakeUser()]


@pytest.fixture
def sweep(tmp_path, monkeypatch):
    """Drive ``_run_sweep`` over ONE planted transcript and report what the
    summarizer was handed. Returns a callable taking the transcript file."""
    seen: dict = {"summarized": [], "persisted": []}

    def fake_summarize(raw, provenance, cfg, **kw):
        seen["summarized"].append(raw)
        return _Summary()

    def fake_persist(user, session_id, summary_dict, fp):
        seen["persisted"].append(session_id)

    monkeypatch.setattr("django.contrib.auth.models.User", _FakeUserModel)
    monkeypatch.setattr(cc_summary, "summarize_transcript", fake_summarize)
    monkeypatch.setattr(cc_router, "_resolve_cc_model_id", lambda: "model-x")
    monkeypatch.setattr(
        "nextseek_api.services.cc_assistant._persist_summary_standalone", fake_persist)

    def run(transcript: Path):
        meta = cc_memory.SessionMeta(
            session_id="sess-b", updated_at=0.0, fingerprint=None, summary=None,
            transcript_path=str(transcript), changed=True)
        monkeypatch.setattr(
            "nextseek_api.services.cc_assistant._session_metas",
            lambda user, current_id, paths, mem_cfg, project_dirname=None: [meta])
        return cc_sweep._run_sweep(), seen

    return run


def _plant(tmp_path) -> Path:
    d = tmp_path / "cc-state" / "sess-b" / "projects" / "-home-user"
    d.mkdir(parents=True)
    f = d / "sess.jsonl"
    f.write_bytes(DIRTY)
    return f


def test_the_fixture_actually_reaches_the_summarizer(tmp_path, sweep):
    """Guard against a vacuous suite: if the harness never got as far as the
    summarizer, the two tests below would 'pass' for the wrong reason."""
    f = _plant(tmp_path)
    cc_engine.scrub_transcript_store(f.parents[2], ENV)

    count, seen = sweep(f)

    assert count == 1
    assert seen["summarized"] and seen["persisted"] == ["sess-b"]


def test_an_unscrubbed_transcript_is_never_summarized(tmp_path, sweep):
    """The crash case: the turn that wrote this file died before its finally,
    so nothing ever scrubbed it and the password is still in the bytes."""
    f = _plant(tmp_path)
    assert PW.encode() in f.read_bytes()

    count, seen = sweep(f)

    assert count == 0
    assert seen["summarized"] == [], (
        "the sweep forwarded bytes carrying a plaintext credential to a "
        "third-party summarizer"
    )
    assert seen["persisted"] == []


def test_a_transcript_appended_to_after_its_scrub_is_not_summarized(tmp_path, sweep):
    """A watermark is a claim about content. The agent kept writing after the
    last scrub, and that tail has never been through one."""
    f = _plant(tmp_path)
    cc_engine.scrub_transcript_store(f.parents[2], ENV)
    with f.open("ab") as fh:
        fh.write(b'{"type":"user","message":{"content":"' + PW.encode() + b'"}}\n')

    count, seen = sweep(f)

    assert count == 0
    assert seen["summarized"] == []


def test_a_scrubbed_transcript_is_summarized_and_carries_no_secret(tmp_path, sweep):
    f = _plant(tmp_path)
    cc_engine.scrub_transcript_store(f.parents[2], ENV)

    count, seen = sweep(f)

    assert count == 1
    assert PW.encode() not in seen["summarized"][0]
    assert b"<REDACTED>" in seen["summarized"][0]


# ---------------------------------------------------------------------------
# The call site
# ---------------------------------------------------------------------------


def test_the_gate_sits_between_the_read_and_the_summarize():
    """Ordering is the whole point: gating after the summarize call would be
    decorative. Asserted on the source so the gate cannot be relocated or
    dropped without this failing."""
    tree = ast.parse(Path(cc_sweep.__file__).read_text())
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.FunctionDef) and n.name == "_run_sweep")
    lines: dict[str, int] = {}
    for call in sorted((c for c in ast.walk(fn) if isinstance(c, ast.Call)),
                       key=lambda c: c.lineno):
        f = call.func
        name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
        if name:
            lines.setdefault(name, call.lineno)

    assert "transcript_is_verified_scrubbed" in lines, (
        "cc_sweep has no credentials of its own, so gating on a watermark "
        "written by a turn that did is the only check available to it"
    )
    assert lines["read_bytes"] < lines["transcript_is_verified_scrubbed"] \
        < lines["summarize_transcript"]

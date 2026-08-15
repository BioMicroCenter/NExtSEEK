"""Hermetic: sweep target selection (idle + changed). No Django/Celery import."""
from nextseek_api.cc_assistant import cc_sweep, cc_memory


def _m(sid, updated_ts, changed):
    return cc_memory.SessionMeta(session_id=sid, updated_at=updated_ts, fingerprint=None,
                                 summary=None, transcript_path=f"/{sid}.jsonl", changed=changed)


def test_selects_idle_and_changed_only():
    now = 1000.0
    metas = [
        _m("idle_changed", now - 1000, True),
        _m("idle_unchanged", now - 1000, False),
        _m("fresh_changed", now - 10, True),
    ]
    picked = {m.session_id for m in cc_sweep.select_sweep_targets(metas, now, idle_seconds=900)}
    assert picked == {"idle_changed"}


def test_empty_when_none_qualify():
    assert cc_sweep.select_sweep_targets([], 0.0, idle_seconds=900) == []


def test_run_sweep_skips_empty_path_and_swallows_errors(monkeypatch):
    class User:
        objects = type("O", (), {"all": staticmethod(lambda: [object()])})()

    class FakePath:
        def __init__(self, p):
            self.p = p

        def read_bytes(self):
            if self.p == "/boom.jsonl":
                raise OSError("nope")
            return b"{}"

    metas = [
        _m("no_path", 0, True),
        cc_memory.SessionMeta(
            session_id="ok", updated_at=0, fingerprint=None, summary={"claude_session_id": "c"},
            transcript_path="/ok.jsonl", changed=True,
        ),
        cc_memory.SessionMeta(
            session_id="boom", updated_at=0, fingerprint=None, summary=None,
            transcript_path="/boom.jsonl", changed=True,
        ),
    ]
    metas[0] = cc_memory.SessionMeta(
        session_id="no_path", updated_at=0, fingerprint=None, summary=None,
        transcript_path="", changed=True,
    )

    monkeypatch.setattr("django.contrib.auth.models.User", User, raising=False)
    import types, sys
    django_auth = types.ModuleType("django.contrib.auth.models")
    django_auth.User = User
    monkeypatch.setitem(sys.modules, "django.contrib.auth.models", django_auth)
    tz = types.SimpleNamespace(now=lambda: types.SimpleNamespace(timestamp=lambda: 10_000.0, isoformat=lambda: "t"))
    monkeypatch.setitem(sys.modules, "django.utils.timezone", tz)

    class Paths:
        @staticmethod
        def from_env():
            return object()

    class Mem:
        sweep_idle_seconds = 1

        @staticmethod
        def from_env():
            return Mem()

    monkeypatch.setattr("nextseek_api.cc_assistant.cc_config.CCPaths.from_env", Paths.from_env)
    monkeypatch.setattr("nextseek_api.cc_assistant.cc_config.CCMemoryConfig.from_env", Mem.from_env)
    monkeypatch.setattr(
        "nextseek_api.services.cc_assistant._session_metas",
        lambda *a, **k: metas,
    )
    persisted = []
    monkeypatch.setattr(
        "nextseek_api.services.cc_assistant._persist_summary_standalone",
        lambda *a, **k: persisted.append(a),
    )
    monkeypatch.setattr(
        "nextseek_api.cc_assistant.cc_summary.summarize_transcript",
        lambda *a, **k: types.SimpleNamespace(model_dump=lambda: {"ok": True}),
    )
    monkeypatch.setattr(
        "nextseek_api.cc_assistant.cc_summary.fingerprint",
        lambda raw: "fp",
    )
        monkeypatch.setattr(
            "nextseek_api.cc_assistant.router._resolve_cc_model_id",
            lambda: "opus",
        )
        import pathlib
        monkeypatch.setattr(pathlib, "Path", FakePath)

        count = cc_sweep._run_sweep()
        assert count == 1
        assert persisted


def test_sweep_task_wrapper_calls_run(monkeypatch):
    monkeypatch.setattr(cc_sweep, "_run_sweep", lambda: 3)
    if hasattr(cc_sweep, "sweep_cc_summaries"):
        fn = cc_sweep.sweep_cc_summaries
        raw = getattr(fn, "__wrapped__", fn)
        assert raw() == 3 or fn() == 3

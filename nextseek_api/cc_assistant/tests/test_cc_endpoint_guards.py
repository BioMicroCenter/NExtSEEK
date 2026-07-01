import pytest


def test_resolve_artifact_path_rejects_traversal(tmp_path):
    from nextseek_api.cc_assistant.cc_endpoint_guards import resolve_artifact_path
    art = tmp_path / "artifacts" / "turn1"
    art.mkdir(parents=True)
    with pytest.raises(ValueError):
        resolve_artifact_path(str(art.parent), "../etc/passwd")


def test_session_owned_by_user_false_for_wrong_user(monkeypatch):
    import sys
    import types
    from nextseek_api.cc_assistant import cc_endpoint_guards as g
    class Q:
        def filter(self, **kw): return self
        def exists(self): return False
    fake_models = types.ModuleType("nextseek_api.assistant.models_db")
    fake_models.ChatSession = type("M", (), {"objects": Q()})
    monkeypatch.setitem(sys.modules, "nextseek_api.assistant.models_db", fake_models)
    assert g.session_owned_by_user(1, "sess") is False

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


def test_resolve_artifact_path_accepts_nested_key(tmp_path):
    from nextseek_api.cc_assistant.cc_endpoint_guards import resolve_artifact_path
    art = tmp_path / "artifacts" / "turn1"
    nested = art / "sub"
    nested.mkdir(parents=True)
    (nested / "out.txt").write_text("ok")
    got = resolve_artifact_path(str(art), "sub/out.txt")
    assert got == (nested / "out.txt").resolve()


def test_resolve_artifact_path_rejects_empty_key(tmp_path):
    from nextseek_api.cc_assistant.cc_endpoint_guards import resolve_artifact_path
    art = tmp_path / "artifacts"
    art.mkdir()
    with pytest.raises(ValueError):
        resolve_artifact_path(str(art), "")


def test_session_owned_by_user_true(monkeypatch):
    import sys
    import types
    from nextseek_api.cc_assistant import cc_endpoint_guards as g

    class Q:
        def filter(self, **kw):
            return self

        def exists(self):
            return True

    fake_models = types.ModuleType("nextseek_api.assistant.models_db")
    fake_models.ChatSession = type("M", (), {"objects": Q()})
    monkeypatch.setitem(sys.modules, "nextseek_api.assistant.models_db", fake_models)
    assert g.session_owned_by_user(7, "abc") is True

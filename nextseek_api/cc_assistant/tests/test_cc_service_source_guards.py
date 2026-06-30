"""Source guards for CC service wiring that is hard to import hermetically."""
from pathlib import Path


SERVICE = Path(__file__).resolve().parents[2] / "services" / "cc_assistant.py"


def test_project_resolution_uses_user_creds_not_prod_swapped_agent_creds():
    src = SERVICE.read_text()

    assert "user_api_user, user_api_pass = api_user, api_pass" in src
    assert "resolve_user_project(user_api_user, user_api_pass)" in src


def test_session_project_dirname_is_initialized_not_overwritten():
    src = SERVICE.read_text()

    assert 'or project.dirname' in src
    assert 'if not (chat_session.extra_state or {}).get("cc_project_dirname")' in src
    assert 'session_project = es.get("cc_project_dirname") or project_dirname' in src

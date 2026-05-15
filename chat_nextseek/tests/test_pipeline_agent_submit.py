"""Submit step: hand the emitted launch.yml to seqera/submitter."""
from unittest.mock import MagicMock, patch

from chat_nextseek import pipeline_agent


def _state_with_launch_yml():
    return {"pipeline_agent": {
        "active": True,
        "phase": "awaiting_validation",
        "directive": {"pipeline_key": "rnaseq"},
        "build_artifacts": {"launch_yml": "/tmp/run/launch.yml"},
        "samplesheet_rows": [{}],
    }}


def test_submit_calls_submit_launch_and_clears():
    session = _state_with_launch_yml()
    config = MagicMock()
    config.tower_env = {"access_token": "tok", "workspace": "ws"}
    with patch("chat_nextseek.pipeline_agent.submit_launch",
               return_value=["https://tower/run/1"]) as sub:
        out = pipeline_agent._handle_submit(session, config, log_dir=None)
    assert "https://tower/run/1" in out["reply"]
    sub.assert_called_once_with("/tmp/run/launch.yml", tower_env=config.tower_env)
    assert session.get("pipeline_agent") in (None, {})


def test_submit_with_no_run_urls_keeps_state():
    session = _state_with_launch_yml()
    config = MagicMock()
    config.tower_env = {"access_token": "tok", "workspace": "ws"}
    with patch("chat_nextseek.pipeline_agent.submit_launch", return_value=[]):
        out = pipeline_agent._handle_submit(session, config, log_dir=None)
    assert "didn't return" in out["reply"].lower() or "no run" in out["reply"].lower()
    assert session["pipeline_agent"].get("active") is True


def test_submit_with_no_launch_yml_explains():
    session = {"pipeline_agent": {
        "active": True, "phase": "awaiting_validation",
        "directive": {"pipeline_key": "rnaseq"},
        "build_artifacts": {},
    }}
    out = pipeline_agent._handle_submit(session, MagicMock(), log_dir=None)
    assert "no launch" in out["reply"].lower() or "no artifact" in out["reply"].lower()


def test_submit_with_missing_tower_env_explains():
    session = _state_with_launch_yml()
    config = MagicMock()
    config.tower_env = {"access_token": None, "workspace": None}
    out = pipeline_agent._handle_submit(session, config, log_dir=None)
    assert "tower" in out["reply"].lower()
    assert session["pipeline_agent"].get("active") is True

"""Hermetic tests for the SEEK op helper: the spawner client is faked, no Docker and no network."""

import json
from unittest.mock import patch

import pytest

from NessieAI.cc import spawner_client
from nextseek_api.services.seek_rails_runner import (
    SeekRailsRunnerError,
    SeekRailsUnavailableError,
    run_seek_op,
)


def _answer(exit_code=0, last_line="", stderr=""):
    return {"exit_code": exit_code, "last_line": last_line, "stderr": stderr}


def _ok(**result):
    return _answer(0, json.dumps({"ok": True, **result}))


@pytest.fixture(autouse=True)
def _spawner_on(monkeypatch):
    monkeypatch.setenv("NEXTSEEK_SPAWNER_URL", "http://cc-spawner:8080")


def _via(answer=None, error=None):
    return patch.object(spawner_client, "seek_op", side_effect=error, return_value=answer)


def test_run_seek_op_parses_the_last_json_line_and_sends_op_and_payload():
    with _via(_ok(user_id=99)) as call:
        result = run_seek_op("create", {"a": 1})
    assert result["user_id"] == 99
    assert call.call_args.args == ("create", {"a": 1})


def test_run_seek_op_raises_on_failure_json():
    with _via(_answer(1, json.dumps({"ok": False, "error": "boom"}), "stderr")):
        with pytest.raises(SeekRailsRunnerError, match="boom"):
            run_seek_op("patch", {})


def test_run_seek_op_raises_on_non_object_json():
    with _via(_answer(0, '"not-an-object"')):
        with pytest.raises(SeekRailsRunnerError, match="must be an object"):
            run_seek_op("patch", {})


def test_run_seek_op_raises_on_empty_output():
    with _via(_answer(0)):
        with pytest.raises(SeekRailsRunnerError, match="no JSON output"):
            run_seek_op("patch", {})


def test_run_seek_op_raises_on_invalid_json():
    with _via(_answer(0, "not-json")):
        with pytest.raises(SeekRailsRunnerError, match="not valid JSON"):
            run_seek_op("patch", {})


def test_unavailable_when_the_spawner_cannot_be_reached():
    with _via(error=spawner_client.SpawnerUnavailable("down")):
        with pytest.raises(SeekRailsUnavailableError):
            run_seek_op("create", {})


def test_unavailable_when_the_spawner_says_the_seek_container_is_missing():
    with _via(error=spawner_client.SpawnerUnavailable("spawner answered 503: SEEK container 'seek' not found")):
        with pytest.raises(SeekRailsUnavailableError, match="not found|unavailable"):
            run_seek_op("create", {})


def test_a_refusal_is_a_runner_error_not_unavailable():
    with _via(error=spawner_client.SpawnerRefused(404, "unknown SEEK op")):
        with pytest.raises(SeekRailsRunnerError) as caught:
            run_seek_op("create", {})
    assert not isinstance(caught.value, SeekRailsUnavailableError)


def test_seek_ops_need_the_spawner_even_when_the_switch_is_off(monkeypatch):
    monkeypatch.setenv("NEXTSEEK_SPAWNER_URL", "")
    with pytest.raises(SeekRailsUnavailableError, match="NEXTSEEK_SPAWNER_URL"):
        run_seek_op("create", {})


def test_a_runner_killed_with_no_output_says_it_was_killed():
    """Exit 137 is SIGKILL: the seek container's memory cap killed the fresh Rails boot, and both streams are empty.

    The 502 detail is all an operator sees, so it has to name the kill rather than come back empty.
    """
    with _via(_answer(137)):
        with pytest.raises(SeekRailsRunnerError, match="no JSON output") as caught:
            run_seek_op("create", {})
    err = caught.value
    assert err.exit_code == 137
    assert "137" in str(err)
    assert "killed" in err.detail and "out of memory" in err.detail and "seek container" in err.detail


def test_a_runner_that_exits_non_zero_with_no_output_reports_its_exit_code():
    with _via(_answer(1)):
        with pytest.raises(SeekRailsRunnerError) as caught:
            run_seek_op("create", {})
    err = caught.value
    assert err.exit_code == 1
    assert "exited 1" in err.detail
    assert "out of memory" not in err.detail


def test_the_exit_code_is_reported_beside_what_the_runner_printed():
    with _via(_answer(1, "", "LoadError: cannot load such file")):
        with pytest.raises(SeekRailsRunnerError) as caught:
            run_seek_op("create", {})
    assert "exited 1" in caught.value.detail
    assert "LoadError: cannot load such file" in caught.value.detail


def test_a_runner_killed_after_printing_a_line_that_is_not_json_says_it_was_killed():
    with _via(_answer(137, "Loading production environment")):
        with pytest.raises(SeekRailsRunnerError, match="not valid JSON") as caught:
            run_seek_op("create", {})
    assert caught.value.exit_code == 137
    assert "killed" in caught.value.detail and "Loading production environment" in caught.value.detail


def test_a_clean_empty_run_keeps_its_old_message():
    with _via(_answer(0)):
        with pytest.raises(SeekRailsRunnerError, match="no JSON output") as caught:
            run_seek_op("create", {})
    assert caught.value.exit_code == 0

"""Hermetic tests for SEEK rails runner helper."""

import json
from unittest.mock import MagicMock, patch

import pytest

from nextseek_api.services.seek_rails_runner import (
    SeekRailsRunnerError,
    SeekRailsUnavailableError,
    run_seek_rails_runner,
)


def test_run_seek_rails_runner_parses_last_json_line():
    fake_container = MagicMock()
    fake_container.exec_run.return_value = (
        0,
        (b"loading...\n" + json.dumps({"ok": True, "user_id": 99}).encode() + b"\n", b""),
    )
    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("docker.from_env", return_value=fake_client):
        result = run_seek_rails_runner("puts({ok: true, user_id: 1}.to_json)", {"a": 1})

    assert result["user_id"] == 99
    fake_container.exec_run.assert_called_once()
    cmd = fake_container.exec_run.call_args[0][0]
    assert cmd[0] == "bin/rails"
    assert "payload = JSON.parse" in cmd[2]


def test_run_seek_rails_runner_raises_on_failure_json():
    fake_container = MagicMock()
    fake_container.exec_run.return_value = (
        1,
        (json.dumps({"ok": False, "error": "boom"}).encode(), b"stderr"),
    )
    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("docker.from_env", return_value=fake_client):
        with pytest.raises(SeekRailsRunnerError, match="boom"):
            run_seek_rails_runner("raise 'nope'")


def test_run_seek_rails_runner_raises_on_non_object_json():
    fake_container = MagicMock()
    fake_container.exec_run.return_value = (0, (b'"not-an-object"', b""))
    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("docker.from_env", return_value=fake_client):
        with pytest.raises(SeekRailsRunnerError, match="must be an object"):
            run_seek_rails_runner("puts '\"x\"'")


def test_run_seek_rails_runner_raises_on_empty_output():
    fake_container = MagicMock()
    fake_container.exec_run.return_value = (0, (b"", b""))
    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("docker.from_env", return_value=fake_client):
        with pytest.raises(SeekRailsRunnerError, match="no JSON output"):
            run_seek_rails_runner("puts ''")


def test_run_seek_rails_runner_unavailable_when_container_missing():
    from docker.errors import NotFound

    fake_client = MagicMock()
    fake_client.containers.get.side_effect = NotFound("missing")

    with patch("docker.from_env", return_value=fake_client):
        with pytest.raises(SeekRailsUnavailableError):
            run_seek_rails_runner("puts '{}'")


def test_run_seek_rails_runner_raises_on_invalid_json():
    fake_container = MagicMock()
    fake_container.exec_run.return_value = (0, (b"not-json", b""))
    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("docker.from_env", return_value=fake_client):
        with pytest.raises(SeekRailsRunnerError, match="not valid JSON"):
            run_seek_rails_runner("puts 'broken'")


def test_run_seek_rails_runner_handles_non_tuple_output():
    fake_container = MagicMock()
    fake_container.exec_run.return_value = (
        0,
        json.dumps({"ok": True, "user_id": 1}).encode(),
    )
    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("docker.from_env", return_value=fake_client):
        result = run_seek_rails_runner("puts '{}'")
    assert result["user_id"] == 1


def test_run_seek_rails_runner_unavailable_on_exec_api_error():
    from docker.errors import APIError

    fake_container = MagicMock()
    fake_container.exec_run.side_effect = APIError("exec failed")
    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container

    with patch("docker.from_env", return_value=fake_client):
        with pytest.raises(SeekRailsUnavailableError, match="Docker exec"):
            run_seek_rails_runner("puts '{}'")


def test_run_seek_rails_runner_unavailable_on_connect_error():
    with patch("docker.from_env", side_effect=OSError("no socket")):
        with pytest.raises(SeekRailsUnavailableError, match="Cannot connect"):
            run_seek_rails_runner("puts '{}'")


def _exec_returning(exit_code, stdout=b"", stderr=b""):
    fake_container = MagicMock()
    fake_container.exec_run.return_value = (exit_code, (stdout, stderr))
    fake_client = MagicMock()
    fake_client.containers.get.return_value = fake_container
    return fake_client


def test_a_runner_killed_with_no_output_says_it_was_killed():
    """Exit 137 is SIGKILL: the seek container's memory cap killed the fresh Rails boot, and both streams are empty.

    The 502 detail is all an operator sees, so it has to name the kill rather than come back empty.
    """
    with patch("docker.from_env", return_value=_exec_returning(137)):
        with pytest.raises(SeekRailsRunnerError, match="no JSON output") as caught:
            run_seek_rails_runner("puts '{}'")
    err = caught.value
    assert err.exit_code == 137
    assert "137" in str(err)
    assert "killed" in err.detail and "out of memory" in err.detail and "seek container" in err.detail


def test_a_runner_that_exits_non_zero_with_no_output_reports_its_exit_code():
    with patch("docker.from_env", return_value=_exec_returning(1)):
        with pytest.raises(SeekRailsRunnerError) as caught:
            run_seek_rails_runner("puts '{}'")
    err = caught.value
    assert err.exit_code == 1
    assert "exited 1" in err.detail
    assert "out of memory" not in err.detail


def test_the_exit_code_is_reported_beside_what_the_runner_printed():
    with patch("docker.from_env", return_value=_exec_returning(1, stderr=b"LoadError: cannot load such file")):
        with pytest.raises(SeekRailsRunnerError) as caught:
            run_seek_rails_runner("puts '{}'")
    assert "exited 1" in caught.value.detail
    assert "LoadError: cannot load such file" in caught.value.detail


def test_a_runner_killed_after_printing_a_line_that_is_not_json_says_it_was_killed():
    with patch("docker.from_env", return_value=_exec_returning(137, stdout=b"Loading production environment")):
        with pytest.raises(SeekRailsRunnerError, match="not valid JSON") as caught:
            run_seek_rails_runner("puts '{}'")
    assert caught.value.exit_code == 137
    assert "killed" in caught.value.detail and "Loading production environment" in caught.value.detail


def test_a_clean_empty_run_keeps_its_old_message():
    with patch("docker.from_env", return_value=_exec_returning(0)):
        with pytest.raises(SeekRailsRunnerError, match="no JSON output") as caught:
            run_seek_rails_runner("puts ''")
    assert caught.value.exit_code == 0

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

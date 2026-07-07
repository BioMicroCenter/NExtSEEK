"""Tests for startup.steps.validate."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from startup.steps.validate import (
    HealthResult,
    check_http,
    run_django_check,
)


@patch("startup.steps.validate.urllib.request.urlopen")
def test_check_http_ok_returns_health_result(mock_urlopen: MagicMock) -> None:
    mock_response = MagicMock()
    mock_response.getcode.return_value = 200
    mock_urlopen.return_value.__enter__.return_value = mock_response
    r = check_http("nextseek", "http://localhost:8000")
    assert r.ok is True
    assert r.detail.endswith("200")


@patch("startup.steps.validate.urllib.request.urlopen")
def test_check_http_failure_returns_not_ok(mock_urlopen: MagicMock) -> None:
    mock_urlopen.side_effect = OSError("connection refused")
    r = check_http("nextseek", "http://localhost:8000")
    assert r.ok is False


@patch("startup.steps.validate.compose_exec")
def test_run_django_check_ok_on_clean_exit(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "System check identified no issues (0 silenced).\n"
    r = run_django_check(repo_root=Path("/repo"), env={})
    assert r.ok is True


@patch("startup.steps.validate.compose_exec")
def test_run_django_check_ok_on_zero_exit_warning_output(mock_exec: MagicMock) -> None:
    mock_exec.return_value = "WARNINGS:\n?: (models.W042) Auto-created primary key used\n"
    r = run_django_check(repo_root=Path("/repo"), env={})
    assert r.ok is True
    assert "Auto-created primary key" in r.detail


@patch("startup.steps.validate.compose_exec")
def test_run_django_check_fail_on_exception(mock_exec: MagicMock) -> None:
    from startup.lib.docker_ops import DockerOpsError
    mock_exec.side_effect = DockerOpsError("boom")
    r = run_django_check(repo_root=Path("/repo"), env={})
    assert r.ok is False

"""Tests for bootstrap.steps.prereqs."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from bootstrap.steps.prereqs import (
    PrereqResult,
    check_command_version,
    check_docker,
    check_compose,
    check_uv,
)


@patch("bootstrap.steps.prereqs.subprocess.run")
def test_check_command_version_returns_ok_on_success(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="Docker version 28.1.0, build abc\n", stderr="")
    r = check_command_version("docker", ["--version"])
    assert r.ok is True
    assert "28.1.0" in r.detail


@patch("bootstrap.steps.prereqs.subprocess.run")
def test_check_command_version_returns_fail_on_missing(mock_run: MagicMock) -> None:
    mock_run.side_effect = FileNotFoundError("docker")
    r = check_command_version("docker", ["--version"])
    assert r.ok is False
    assert "not installed" in r.detail or "not found" in r.detail


@patch("bootstrap.steps.prereqs.subprocess.run")
def test_check_docker_parses_version(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="Docker version 28.1.0, build abc\n", stderr="")
    r = check_docker()
    assert r.ok is True
    assert r.name == "docker"


@patch("bootstrap.steps.prereqs.subprocess.run")
def test_check_compose_parses_version(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="Docker Compose version v2.30.0\n", stderr="")
    r = check_compose()
    assert r.ok is True
    assert r.name == "docker compose"


@patch("bootstrap.steps.prereqs.subprocess.run")
def test_check_uv_parses_version(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="uv 0.5.20\n", stderr="")
    r = check_uv()
    assert r.ok is True
    assert r.name == "uv"

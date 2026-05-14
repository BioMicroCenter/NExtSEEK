"""Tests for bootstrap.lib.docker_ops."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from bootstrap.lib.docker_ops import (
    DockerOpsError,
    compose_up,
    compose_down,
    compose_exec,
    volume_exists,
    volume_create,
)


@patch("bootstrap.lib.docker_ops.subprocess.run")
def test_compose_up_invokes_compose_up_d(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    compose_up(services=["db", "neo4j"], project_dir="/repo", env={})
    args = mock_run.call_args.args[0]
    assert args[:3] == ["docker", "compose", "up"]
    assert "-d" in args
    assert "db" in args and "neo4j" in args


@patch("bootstrap.lib.docker_ops.subprocess.run")
def test_compose_up_passes_env(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    compose_up(services=["db"], project_dir="/repo", env={"INSTANCE_PREFIX": "test-"})
    call_env = mock_run.call_args.kwargs["env"]
    assert call_env["INSTANCE_PREFIX"] == "test-"


@patch("bootstrap.lib.docker_ops.subprocess.run")
def test_compose_up_raises_on_nonzero_exit(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
    with pytest.raises(DockerOpsError, match="boom"):
        compose_up(services=["db"], project_dir="/repo", env={})


@patch("bootstrap.lib.docker_ops.subprocess.run")
def test_volume_exists_returns_true_on_zero_exit(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="my-volume\n", stderr="")
    assert volume_exists("my-volume") is True


@patch("bootstrap.lib.docker_ops.subprocess.run")
def test_volume_exists_returns_false_on_nonzero(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
    assert volume_exists("my-volume") is False


@patch("bootstrap.lib.docker_ops.subprocess.run")
def test_volume_create_invokes_docker_volume_create(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="my-volume\n", stderr="")
    volume_create("my-volume")
    args = mock_run.call_args.args[0]
    assert args == ["docker", "volume", "create", "my-volume"]


@patch("bootstrap.lib.docker_ops.subprocess.run")
def test_compose_exec_passes_service_and_command(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")
    compose_exec(
        service="db",
        command=["mysql", "-e", "SHOW DATABASES;"],
        project_dir="/repo",
        env={},
    )
    args = mock_run.call_args.args[0]
    assert args[:3] == ["docker", "compose", "exec"]
    assert "db" in args
    assert "SHOW DATABASES;" in args

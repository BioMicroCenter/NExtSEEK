"""Tests for startup.lib.docker_ops."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from startup.lib.docker_ops import (
    DockerOpsError,
    compose_build,
    compose_up,
    compose_down,
    compose_exec,
    volume_exists,
    volume_create,
    bootstrap_staging_dir,
)


@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_up_invokes_compose_up_d(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    compose_up(services=["db", "neo4j"], project_dir="/repo", env={})
    args = mock_run.call_args.args[0]
    assert args[:3] == ["docker", "compose", "up"]
    assert "-d" in args
    assert "db" in args and "neo4j" in args


@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_up_can_force_recreate(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    compose_up(
        services=["nextseek_nginx"],
        project_dir="/repo",
        env={},
        force_recreate=True,
    )
    args = mock_run.call_args.args[0]
    assert "--force-recreate" in args
    assert args[-1] == "nextseek_nginx"


@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_up_passes_env(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    compose_up(services=["db"], project_dir="/repo", env={"INSTANCE_PREFIX": "test-"})
    call_env = mock_run.call_args.kwargs["env"]
    assert call_env["INSTANCE_PREFIX"] == "test-"


@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_up_raises_on_nonzero_exit(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
    with pytest.raises(DockerOpsError, match="boom"):
        compose_up(services=["db"], project_dir="/repo", env={})


@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_build_invokes_compose_build(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    compose_build(services=["cc-agent"], project_dir="/repo", env={})
    args = mock_run.call_args.args[0]
    assert args == ["docker", "compose", "build", "cc-agent"]


@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_build_raises_on_nonzero_exit(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="build failed")
    with pytest.raises(DockerOpsError, match="build failed"):
        compose_build(services=["cc-agent"], project_dir="/repo", env={})


@patch("startup.lib.docker_ops.subprocess.run")
def test_volume_exists_returns_true_on_zero_exit(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="my-volume\n", stderr="")
    assert volume_exists("my-volume") is True


@patch("startup.lib.docker_ops.subprocess.run")
def test_volume_exists_returns_false_on_nonzero(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="not found")
    assert volume_exists("my-volume") is False


@patch("startup.lib.docker_ops.subprocess.run")
def test_volume_create_invokes_docker_volume_create(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="my-volume\n", stderr="")
    volume_create("my-volume")
    args = mock_run.call_args.args[0]
    assert args == ["docker", "volume", "create", "my-volume"]


@patch("startup.lib.docker_ops.subprocess.run")
def test_bootstrap_staging_dir_invokes_docker_run_alpine_mkdir_chown(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    bootstrap_staging_dir("dmac-cc-users")
    args = mock_run.call_args.args[0]
    assert args[:4] == ["docker", "run", "--rm", "-v"]
    assert "dmac-cc-users:/v" in args
    assert args[-4] == "alpine"
    assert args[-3] == "sh"
    assert args[-2] == "-c"
    shell_cmd = args[-1]
    assert "mkdir -p /v/_staging" in shell_cmd
    assert "chown 1001 /v/_staging" in shell_cmd


@patch("startup.lib.docker_ops.subprocess.run")
def test_bootstrap_staging_dir_uid_overridable(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    bootstrap_staging_dir("dmac-cc-users", uid=2000)
    shell_cmd = mock_run.call_args.args[0][-1]
    assert "chown 2000 /v/_staging" in shell_cmd


@patch("startup.lib.docker_ops.subprocess.run")
def test_bootstrap_staging_dir_raises_on_nonzero_exit(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="boom")
    with pytest.raises(DockerOpsError, match="boom"):
        bootstrap_staging_dir("dmac-cc-users")


@patch("startup.lib.docker_ops.subprocess.run")
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

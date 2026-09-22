"""Tests for startup.lib.docker_ops."""
from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, patch

import pytest

from startup.lib import docker_ops
from startup.lib.docker_ops import (
    DockerOpsError,
    compose_build,
    compose_up,
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
def test_compose_up_can_exclude_dependencies(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    compose_up(
        services=["nextseek"],
        project_dir="/repo",
        env={},
        no_deps=True,
    )
    args = mock_run.call_args.args[0]
    assert "--no-deps" in args


@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_up_force_recreate_preserves_named_volumes(mock_run: MagicMock) -> None:
    """Routine rebuild recreation must never renew or delete attached volumes."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    compose_up(
        services=["nextseek"],
        project_dir="/repo",
        env={},
        force_recreate=True,
        no_deps=True,
    )
    args = mock_run.call_args.args[0]
    assert "--force-recreate" in args
    assert "--no-deps" in args
    assert "--renew-anon-volumes" not in args
    assert "down" not in args
    assert "-v" not in args


@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_up_passes_env(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    compose_up(services=["db"], project_dir="/repo", env={"INSTANCE_PREFIX": "test-"})
    call_env = mock_run.call_args.kwargs["env"]
    assert call_env["INSTANCE_PREFIX"] == "test-"


@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_up_can_run_attached(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    compose_up(services=["db"], project_dir="/repo", env={}, detached=False)

    assert "-d" not in mock_run.call_args.args[0]


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
def test_compose_build_targets_explicit_builder(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

    compose_build(
        services=["nextseek"],
        project_dir="/repo",
        env={},
        builder="p18t8-builder",
    )

    assert mock_run.call_args.args[0] == [
        "docker", "compose", "build", "--builder", "p18t8-builder", "nextseek"
    ]


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


@patch("startup.lib.docker_ops.subprocess.run")
def test_compose_exec_can_allocate_interactive_terminal(mock_run: MagicMock) -> None:
    mock_run.return_value = MagicMock(returncode=0, stdout="ok\n", stderr="")

    compose_exec(
        service="db",
        command=["mysql"],
        project_dir="/repo",
        env={},
        interactive=True,
    )

    assert "-T" not in mock_run.call_args.args[0]


def test_compose_ps_running_filters_to_requested_services(monkeypatch):
    calls = {}

    def fake_run(cmd, **kwargs):
        calls["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="bedrock-proxy\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    running = docker_ops.compose_ps_running(
        ["bedrock-proxy", "nextseek-sidecar"], "/tmp", {}
    )
    assert running == ["bedrock-proxy"]
    assert calls["cmd"][:4] == ["docker", "compose", "ps", "--services"]
    assert "--status=running" in calls["cmd"]


# ---------------------------------------------------------------------------
# copy_from_image: read a built image's files without running anything
# ---------------------------------------------------------------------------

def _fake_docker(monkeypatch, *, create=0, cp=0, rm=0):
    """Answer docker create/cp/rm with the given exit codes; record every argv."""
    calls: list[list[str]] = []
    codes = {"create": create, "cp": cp, "rm": rm}

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        code = codes[cmd[1]]
        stdout = "c0ffee\n" if cmd[1] == "create" and code == 0 else ""
        return subprocess.CompletedProcess(cmd, code, stdout=stdout,
                                           stderr="" if code == 0 else f"{cmd[1]} broke")

    monkeypatch.setattr(subprocess, "run", fake_run)
    return calls


def test_copy_from_image_creates_copies_and_removes_without_starting(monkeypatch):
    calls = _fake_docker(monkeypatch)

    docker_ops.copy_from_image("dmac-assistant:poc", "/app/plugins/nextseek/context", "/tmp/out")

    assert calls == [
        ["docker", "create", "dmac-assistant:poc", "true"],
        ["docker", "cp", "c0ffee:/app/plugins/nextseek/context", "/tmp/out"],
        ["docker", "rm", "c0ffee"],
    ]
    # Nothing in the image is ever executed, and no running container is touched.
    assert not any(cmd[1] in {"start", "run", "exec", "compose"} for cmd in calls)


def test_copy_from_image_removes_the_container_when_the_copy_fails(monkeypatch):
    calls = _fake_docker(monkeypatch, cp=1)

    with pytest.raises(DockerOpsError, match="cp broke"):
        docker_ops.copy_from_image("dmac-assistant:poc", "/nope", "/tmp/out")

    assert calls[-1] == ["docker", "rm", "c0ffee"]


def test_copy_from_image_copies_nothing_when_the_image_cannot_be_created(monkeypatch):
    calls = _fake_docker(monkeypatch, create=1)

    with pytest.raises(DockerOpsError, match="create broke"):
        docker_ops.copy_from_image("dmac-assistant:poc", "/app", "/tmp/out")

    assert [cmd[1] for cmd in calls] == ["create"]


def test_copy_from_image_names_a_container_it_could_not_remove(monkeypatch):
    """A stray created container is litter the operator should hear about."""
    _fake_docker(monkeypatch, rm=1)

    with pytest.raises(DockerOpsError, match="c0ffee"):
        docker_ops.copy_from_image("dmac-assistant:poc", "/app", "/tmp/out")

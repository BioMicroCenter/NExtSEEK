"""Subprocess wrappers around docker / docker compose."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Sequence


class DockerOpsError(RuntimeError):
    """A docker / docker compose invocation failed."""


def _build_env(overrides: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(overrides)
    return env


def _check(result: subprocess.CompletedProcess[Any], context: str) -> None:
    """Raise DockerOpsError on non-zero exit. Accepts either str or bytes streams."""
    if result.returncode != 0:
        stderr = result.stderr.decode() if isinstance(result.stderr, bytes) else (result.stderr or "")
        stdout = result.stdout.decode() if isinstance(result.stdout, bytes) else (result.stdout or "")
        raise DockerOpsError(f"{context} failed (exit {result.returncode}): {stderr.strip() or stdout.strip()}")


def compose_up(
    services: Sequence[str],
    project_dir: str | Path,
    env: dict[str, str],
    detached: bool = True,
    build: bool = False,
) -> None:
    """Run `docker compose up [-d] [--build] <services...>` in project_dir."""
    cmd = ["docker", "compose", "up"]
    if detached:
        cmd.append("-d")
    if build:
        cmd.append("--build")
    cmd.extend(services)
    result = subprocess.run(
        cmd,
        cwd=str(project_dir),
        env=_build_env(env),
        capture_output=True,
        text=True,
    )
    _check(result, f"docker compose up {' '.join(services)}")


def compose_down(
    project_dir: str | Path,
    env: dict[str, str],
    volumes: bool = False,
) -> None:
    """Run `docker compose down [-v]`."""
    cmd = ["docker", "compose", "down"]
    if volumes:
        cmd.append("-v")
    result = subprocess.run(
        cmd,
        cwd=str(project_dir),
        env=_build_env(env),
        capture_output=True,
        text=True,
    )
    _check(result, "docker compose down")


def compose_exec(
    service: str,
    command: Sequence[str],
    project_dir: str | Path,
    env: dict[str, str],
    interactive: bool = False,
    stdin: bytes | None = None,
) -> str:
    """Run `docker compose exec [-T] <service> <command...>`, return stdout."""
    cmd = ["docker", "compose", "exec"]
    if not interactive:
        cmd.append("-T")
    cmd.append(service)
    cmd.extend(command)
    if stdin is None:
        result = subprocess.run(
            cmd,
            cwd=str(project_dir),
            env=_build_env(env),
            capture_output=True,
            text=True,
        )
    else:
        result = subprocess.run(
            cmd,
            cwd=str(project_dir),
            env=_build_env(env),
            capture_output=True,
            input=stdin,
        )
    _check(result, f"docker compose exec {service} {' '.join(command)}")
    return result.stdout if isinstance(result.stdout, str) else result.stdout.decode()


def volume_exists(name: str) -> bool:
    """True if `docker volume inspect <name>` succeeds."""
    result = subprocess.run(
        ["docker", "volume", "inspect", name],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def volume_create(name: str) -> None:
    """Create a docker volume by name. Raises DockerOpsError if it already exists; callers should
    check `volume_exists(name)` first when idempotent behavior is needed."""
    result = subprocess.run(
        ["docker", "volume", "create", name],
        capture_output=True,
        text=True,
    )
    _check(result, f"docker volume create {name}")

"""Subprocess wrappers around docker / docker compose."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence


class DockerOpsError(RuntimeError):
    """A docker / docker compose invocation failed."""


def _build_env(overrides: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env.update(overrides)
    return env


def _check(result: subprocess.CompletedProcess[str], context: str) -> None:
    if result.returncode != 0:
        raise DockerOpsError(f"{context} failed (exit {result.returncode}): {result.stderr.strip() or result.stdout.strip()}")


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
    """Create a docker volume by name. No-op-ish if it already exists (docker handles this)."""
    result = subprocess.run(
        ["docker", "volume", "create", name],
        capture_output=True,
        text=True,
    )
    _check(result, f"docker volume create {name}")

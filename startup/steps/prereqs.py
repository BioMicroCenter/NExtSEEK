"""Preflight checks: docker, docker compose, uv, disk space."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PrereqResult:
    name: str
    ok: bool
    detail: str
    remediation: str = ""


def check_command_version(cmd: str, args: list[str]) -> PrereqResult:
    """Run `cmd args` and capture its stdout as the version line."""
    try:
        result = subprocess.run(
            [cmd] + args,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except FileNotFoundError:
        return PrereqResult(name=cmd, ok=False, detail=f"{cmd} not installed (not in PATH)")
    except subprocess.TimeoutExpired:
        return PrereqResult(name=cmd, ok=False, detail=f"{cmd} timed out")
    if result.returncode != 0:
        return PrereqResult(name=cmd, ok=False, detail=result.stderr.strip() or "non-zero exit")
    first_line = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    return PrereqResult(name=cmd, ok=True, detail=first_line)


def check_docker() -> PrereqResult:
    r = check_command_version("docker", ["--version"])
    if not r.ok:
        r.remediation = "Install Docker: https://docs.docker.com/get-docker/"
    return PrereqResult(name="docker", ok=r.ok, detail=r.detail, remediation=r.remediation)


def check_compose() -> PrereqResult:
    r = check_command_version("docker", ["compose", "version"])
    if not r.ok:
        r.remediation = "Install Docker Compose v2: https://docs.docker.com/compose/install/"
    return PrereqResult(name="docker compose", ok=r.ok, detail=r.detail, remediation=r.remediation)


def check_uv() -> PrereqResult:
    r = check_command_version("uv", ["--version"])
    if not r.ok:
        r.remediation = "Install uv: https://docs.astral.sh/uv/getting-started/installation/"
    return PrereqResult(name="uv", ok=r.ok, detail=r.detail, remediation=r.remediation)


def check_disk_space(path: str, gb_required: int = 5) -> PrereqResult:
    """Check that `path` has at least gb_required GB free."""
    total, used, free = shutil.disk_usage(path)
    free_gb = free / (1024 ** 3)
    if free_gb < gb_required:
        return PrereqResult(
            name=f"disk:{path}",
            ok=False,
            detail=f"{free_gb:.1f} GB free (need {gb_required})",
            remediation="Free up disk space before installing",
        )
    return PrereqResult(name=f"disk:{path}", ok=True, detail=f"{free_gb:.1f} GB free")


def run_all() -> list[PrereqResult]:
    """Run every prereq check and return the list of results."""
    docker_check_path = "/var/lib/docker"
    fallback_path = "/"
    check_path = docker_check_path if Path(docker_check_path).exists() else fallback_path
    return [
        check_docker(),
        check_compose(),
        check_uv(),
        check_disk_space(check_path),
    ]

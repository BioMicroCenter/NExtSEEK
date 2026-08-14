"""Validation for split clean-source / existing-runtime deployments."""
from __future__ import annotations

import subprocess
from pathlib import Path


class DeploySourceError(RuntimeError):
    """The requested build source cannot be proven to be exact origin/dev."""


_RUNTIME_CONTROL_PATHS = (
    "docker-compose.yml",
    "startup/cli.py",
    "startup/lib",
    "startup/steps",
)


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "git command failed"
        raise DeploySourceError(f"cannot verify deploy source {repo}: {detail}")
    return result.stdout.strip()


def resolve_verified_source(runtime_root: Path, requested: Path | None) -> Path:
    """Return a clean build root pinned to the runtime checkout's origin/dev.

    A separate source tree lets a shared deployment checkout retain unrelated
    operator-owned runtime files while ensuring Docker receives only committed
    source. Runtime recreation still runs from ``runtime_root`` so existing
    bind-mounted outputs, logs, and configuration paths do not move.
    """
    source = (requested or runtime_root).expanduser().resolve()
    if not source.is_dir():
        raise DeploySourceError(f"deploy source is not a directory: {source}")

    source_head = _git(source, "rev-parse", "HEAD")
    source_origin_dev = _git(source, "rev-parse", "origin/dev")
    if source_head != source_origin_dev:
        raise DeploySourceError(
            f"deploy source HEAD {source_head} is not origin/dev {source_origin_dev}"
        )
    if _git(source, "status", "--porcelain", "--untracked-files=all"):
        raise DeploySourceError(f"deploy source is dirty: {source}")

    runtime_head = _git(runtime_root, "rev-parse", "HEAD")
    if runtime_head != source_head:
        raise DeploySourceError(
            f"runtime checkout HEAD {runtime_head} does not match deploy source {source_head}"
        )
    runtime_control_drift = _git(
        runtime_root,
        "status",
        "--porcelain",
        "--",
        *_RUNTIME_CONTROL_PATHS,
    )
    if runtime_control_drift:
        raise DeploySourceError(
            "runtime deployment controls are dirty: "
            + ", ".join(line[3:] for line in runtime_control_drift.splitlines())
        )
    return source

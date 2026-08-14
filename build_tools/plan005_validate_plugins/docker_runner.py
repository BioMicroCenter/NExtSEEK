"""Shell-free Docker invocation for Claude plugin validation."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DockerRunResult:
    returncode: int
    stdout: bytes
    stderr: bytes


def run_claude_plugin_validate(
    *,
    plugin_dir: Path,
    validator_image: str,
    timeout_seconds: int,
) -> DockerRunResult:
    """Run `claude plugin validate --strict` network-disabled without a shell."""
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-v",
            f"{plugin_dir.resolve()}:/plugin:ro",
            "--entrypoint",
            "claude",
            validator_image,
            "plugin",
            "validate",
            "--strict",
            "/plugin",
        ],
        capture_output=True,
        timeout=timeout_seconds,
        check=False,
    )
    return DockerRunResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )

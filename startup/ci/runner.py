"""Invoke the CI smoke suite.

SUBPROCESSES, never imports. startup/ is pinned to typer, rich, neo4j, orjson and
PyMySQL so ./startup.sh stays bootstrappable on a host with no C toolchain;
importing the suite would drag requests and playwright into it.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from startup.lib.instance import InstanceState


def run_ci(repo_root: Path, state: InstanceState, *, wait_ready: bool,
           profile: str | None = None, force_profile: str | None = None,
           confirm_force: bool = False) -> int:
    box_profile = state.ci_profile or "prod"      # fail closed
    port = state.ports.get("nextseek", 8000)
    cmd = [
        "uv", "run", "--no-project",
        "--with", "pytest", "--with", "requests", "--with", "playwright",
        "pytest", "ci/smoke/",
        "--base-url", f"http://127.0.0.1:{port}",
    ]
    if wait_ready:
        cmd.append("--wait-ready")
    if profile:
        cmd += ["--profile", profile]
    if force_profile:
        cmd += ["--force-profile", force_profile]
    env = {
        **os.environ,
        # PYTHONDONTWRITEBYTECODE: a repo-root pytest run must never leave
        # __pycache__ behind in the working tree it is testing.
        "PYTHONDONTWRITEBYTECODE": "1",
        "CI_BOX_PROFILE": box_profile,
    }
    if confirm_force:
        # Set for this invocation only, and only after the operator answered the
        # prompt in cli.ci. Never persisted, never exported to a workflow file.
        env["CI_FORCE_PROFILE_CONFIRM"] = "yes"
    return subprocess.run(cmd, cwd=repo_root, env=env).returncode

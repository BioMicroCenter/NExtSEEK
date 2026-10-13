"""A resumed turn still sees turn 1 after the entrypoint rebuilds ~/.claude (step 1, requirement 4).

No model is called: Claude Code talks to a local stand-in for the Messages API that records what it is sent
(resume_check/stub_messages_api.js). Turn 1 states a code word; the entrypoint then runs as at every container
start, after a turn left extra files behind; turn 2 resumes turn 1 and must send the code word back. A control
turn with the store removed must not.

Opt-in (RUN_CC_RESUME_CHECK=1): it runs in a throwaway node:22 container, downloads the Claude Code version
NessieAI/docker/cc-runtime/Dockerfile pins, and jq, so it needs docker and network. Free.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from NessieAI import paths

CHECK_DIR = Path(__file__).resolve().parent / "resume_check"

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CC_RESUME_CHECK") != "1" or shutil.which("docker") is None,
    reason="opt-in: set RUN_CC_RESUME_CHECK=1 on a host with docker and network",
)


def _pinned_version() -> str:
    text = (paths.CC_RUNTIME_DIR / "Dockerfile").read_text(encoding="utf-8")
    return re.search(r"@anthropic-ai/claude-code@([0-9.]+)", text).group(1)


def test_a_resumed_turn_still_sees_turn_one_after_the_reset():
    res = subprocess.run(
        ["docker", "run", "--rm",
         "-v", f"{CHECK_DIR}:/check:ro",
         "-v", f"{paths.CC_RUNTIME_DIR}:/cc-runtime:ro",
         "-e", f"CC_VERSION={_pinned_version()}",
         "node:22-bookworm-slim", "sh", "/check/resume_check.sh"],
        capture_output=True, text=True, timeout=900)
    print(res.stdout)
    print(res.stderr)
    assert res.returncode == 0, res.stdout[-4000:] + res.stderr[-4000:]
    assert "RESUME CHECK PASSED" in res.stdout
    assert "CONTROL PASSED" in res.stdout

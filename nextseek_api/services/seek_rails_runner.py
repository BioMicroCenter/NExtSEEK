"""Run one of the fixed SEEK admin scripts (create, patch, compensate) in the live SEEK container.

The app does not touch the Docker socket. It asks cc-spawner (``NessieAI/cc/spawner_client.py``), which holds the
three Ruby scripts, passes the payload to ``bin/rails runner`` in the exec environment and answers with the script's
last JSON line and the exit status. This module turns that answer into a result or one of the two errors below.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

from NessieAI.cc import spawner_client

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 120


class SeekRailsRunnerError(Exception):
    """Rails runner returned a structured failure or unparseable output.

    ``exit_code`` is the exec's exit status once the runner has run, and None before that."""

    def __init__(self, message: str, *, detail: Optional[str] = None, exit_code: Optional[int] = None):
        super().__init__(message)
        self.detail = detail
        self.exit_code = exit_code


class SeekRailsUnavailableError(SeekRailsRunnerError):
    """cc-spawner or the SEEK container is not reachable from this runtime."""


@dataclass(frozen=True)
class SeekRailsRunnerConfig:
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> "SeekRailsRunnerConfig":
        return cls(
            timeout_seconds=int(os.environ.get("SEEK_RAILS_RUNNER_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)),
        )


# 128 + 9: the kernel's SIGKILL, which is how a container's memory cap ends a process. A fresh ``bin/rails runner``
# boots the whole Rails app beside SEEK's running puma workers, so a seek container near its cap kills the runner.
SIGKILL_EXIT = 137


def _exit_note(exit_code: Optional[int]) -> str:
    """What the exec's exit status says, for an error detail; empty for a clean exit."""
    if exit_code == SIGKILL_EXIT:
        return (f"bin/rails runner exited {SIGKILL_EXIT}: it was killed (SIGKILL), most likely out of memory in the "
                "seek container. Check that container's memory use against its cap, SEEK_MEMORY (DEPLOYMENT.md).")
    if exit_code is None or exit_code == 0:
        return ""
    if exit_code > 128:
        return f"bin/rails runner exited {exit_code}: it was killed by signal {exit_code - 128}."
    return f"bin/rails runner exited {exit_code}."


def _detail(exit_code: Optional[int], output: str) -> str:
    """The exit status first, when it says anything, then what the runner printed."""
    return "\n".join(part for part in (_exit_note(exit_code), output) if part)


def run_seek_op(
    op_name: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    config: Optional[SeekRailsRunnerConfig] = None,
) -> Dict[str, Any]:
    """Run the SEEK script ``op_name`` ("create", "patch" or "compensate") with ``payload``.

    The script ends by printing a single JSON object as its last stdout line.
    """
    config = config or SeekRailsRunnerConfig.from_env()
    if not spawner_client.enabled():
        raise SeekRailsUnavailableError(
            "NEXTSEEK_SPAWNER_URL is not set: SEEK user operations run through cc-spawner",
        )
    try:
        # The exec has no limit of its own; the wait is the runner timeout plus a margin for the Rails boot.
        answer = spawner_client.seek_op(op_name, payload, timeout=config.timeout_seconds + 30)
    except spawner_client.SpawnerUnavailable as exc:
        raise SeekRailsUnavailableError("cc-spawner unavailable for the SEEK operation", detail=str(exc)) from exc
    except spawner_client.SpawnerRefused as exc:
        raise SeekRailsRunnerError("cc-spawner refused the SEEK operation", detail=str(exc)) from exc

    exit_code = answer.get("exit_code")
    last_line = (answer.get("last_line") or "").strip()
    stderr_text = (answer.get("stderr") or "").strip()
    if stderr_text:
        logger.debug("seek rails runner stderr: %s", stderr_text)

    if not last_line:
        message = "SEEK rails runner produced no JSON output"
        if exit_code:
            message += f" (exit code {exit_code})"
        raise SeekRailsRunnerError(message, detail=_detail(exit_code, stderr_text), exit_code=exit_code)

    try:
        result = json.loads(last_line)
    except json.JSONDecodeError as exc:
        raise SeekRailsRunnerError(
            "SEEK rails runner last stdout line is not valid JSON",
            detail=_detail(exit_code, last_line),
            exit_code=exit_code,
        ) from exc

    if not isinstance(result, dict):
        raise SeekRailsRunnerError("SEEK rails runner JSON must be an object", detail=_detail(exit_code, last_line),
                                   exit_code=exit_code)

    if exit_code != 0 or not result.get("ok"):
        message = str(result.get("error") or result.get("message") or "SEEK rails runner failed")
        raise SeekRailsRunnerError(message, detail=result.get("detail") or stderr_text, exit_code=exit_code)

    return result

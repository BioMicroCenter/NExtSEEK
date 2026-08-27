"""Execute Ruby in the live SEEK container via the Docker Engine API.

The nextseek compose stack mounts ``/var/run/docker.sock`` but does not ship the
``docker`` CLI. This module uses the ``docker`` Python SDK (already present via
``dmac-assistant``) to ``exec`` into the ``seek`` container and run
``bin/rails runner`` with an optional JSON payload embedded in the script.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_SEEK_CONTAINER = "seek"
DEFAULT_TIMEOUT_SECONDS = 120


class SeekRailsRunnerError(Exception):
    """Rails runner returned a structured failure or unparseable output."""

    def __init__(self, message: str, *, detail: Optional[str] = None):
        super().__init__(message)
        self.detail = detail


class SeekRailsUnavailableError(SeekRailsRunnerError):
    """Docker socket or SEEK container is not reachable from this runtime."""


@dataclass(frozen=True)
class SeekRailsRunnerConfig:
    container_name: str = DEFAULT_SEEK_CONTAINER
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    @classmethod
    def from_env(cls) -> "SeekRailsRunnerConfig":
        return cls(
            container_name=os.environ.get("SEEK_CONTAINER_NAME", DEFAULT_SEEK_CONTAINER),
            timeout_seconds=int(os.environ.get("SEEK_RAILS_RUNNER_TIMEOUT", DEFAULT_TIMEOUT_SECONDS)),
        )


def _import_docker():
    try:
        import docker  # type: ignore
        from docker.errors import APIError, NotFound  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise SeekRailsUnavailableError(
            "docker Python SDK is not installed",
            detail=str(exc),
        ) from exc
    return docker, APIError, NotFound


def run_seek_rails_runner(
    ruby_script: str,
    payload: Optional[Dict[str, Any]] = None,
    *,
    config: Optional[SeekRailsRunnerConfig] = None,
) -> Dict[str, Any]:
    """Run ``ruby_script`` inside ``bin/rails runner``.

    When ``payload`` is provided, a ``payload`` local is set from embedded JSON.
    The Ruby snippet must ``puts`` a single JSON object as its last stdout line.
    """
    config = config or SeekRailsRunnerConfig.from_env()
    docker_mod, APIError, NotFound = _import_docker()

    preamble = ""
    if payload is not None:
        preamble = f"payload = JSON.parse({json.dumps(json.dumps(payload))})\n"

    wrapped = "require 'json'\n" + preamble + ruby_script

    try:
        client = docker_mod.from_env()
        container = client.containers.get(config.container_name)
    except NotFound as exc:
        raise SeekRailsUnavailableError(
            f"SEEK container {config.container_name!r} not found",
            detail=str(exc),
        ) from exc
    except APIError as exc:
        raise SeekRailsUnavailableError(
            "Docker Engine API error while locating SEEK container",
            detail=str(exc),
        ) from exc
    except Exception as exc:  # pragma: no cover
        raise SeekRailsUnavailableError(
            "Cannot connect to Docker Engine (is /var/run/docker.sock mounted?)",
            detail=str(exc),
        ) from exc

    exec_cmd = ["bin/rails", "runner", wrapped]
    try:
        exit_code, output = container.exec_run(exec_cmd, demux=True, workdir="/seek")
    except APIError as exc:
        raise SeekRailsUnavailableError(
            "Docker exec into SEEK container failed",
            detail=str(exc),
        ) from exc

    if isinstance(output, tuple):
        stdout, stderr = output
        stdout = stdout or b""
        stderr = stderr or b""
    else:
        stdout = output or b""
        stderr = b""

    stdout_text = stdout.decode("utf-8", errors="replace").strip()
    stderr_text = stderr.decode("utf-8", errors="replace").strip()
    if stderr_text:
        logger.debug("seek rails runner stderr: %s", stderr_text)

    last_line = ""
    for line in reversed(stdout_text.splitlines()):
        line = line.strip()
        if line:
            last_line = line
            break

    if not last_line:
        raise SeekRailsRunnerError(
            "SEEK rails runner produced no JSON output",
            detail=stderr_text or stdout_text,
        )

    try:
        result = json.loads(last_line)
    except json.JSONDecodeError as exc:
        raise SeekRailsRunnerError(
            "SEEK rails runner last stdout line is not valid JSON",
            detail=last_line,
        ) from exc

    if not isinstance(result, dict):
        raise SeekRailsRunnerError("SEEK rails runner JSON must be an object", detail=last_line)

    if exit_code != 0 or not result.get("ok"):
        message = str(result.get("error") or result.get("message") or "SEEK rails runner failed")
        raise SeekRailsRunnerError(message, detail=result.get("detail") or stderr_text)

    return result

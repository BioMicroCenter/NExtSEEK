"""Container Claude Code (CC) engine — the sandboxed agentic route.

Runs ONE headless ``claude`` container per query, feeds the user message as a
stream-json envelope on stdin, reads the stream-json events back, and translates
them into the existing ``{event, data}`` progress contract via
``CCStreamTranslator`` so the unchanged ``chat_frontend`` renders them.

This is a deliberately lean re-implementation of dmac_assistant's container
lifecycle for the NExtSEEK host: one-shot container per turn (matching the
NExtSEEK ``QueryTask`` = one turn model), an optional scratch bind-mount, and
Bedrock auth injected from the process env. It reuses dmac's proven
``BridgeAttachSocket`` stdcopy reader (copied into ``attach.py``).

It runs inside the daemon worker thread that ``CCAssistantViewSet`` spawns, so
everything here is synchronous/blocking.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

from .attach import BridgeAttachSocket
from .translate import CCStreamTranslator

logger = logging.getLogger(__name__)

SendEvent = Callable[[str, dict[str, Any]], None]

DEFAULT_IMAGE = os.environ.get("NEXTSEEK_CC_IMAGE", "nextseek-cc:lean")

# Bedrock env keys forwarded from the Django process env into the CC container.
_BEDROCK_KEYS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "ANTHROPIC_API_KEY",  # fallback auth path if Bedrock not configured
)

_BASE_CMD = [
    "claude",
    "--print",
    "--input-format", "stream-json",
    "--output-format", "stream-json",
    "--verbose",
    "--dangerously-skip-permissions",
]


def cc_runner_available() -> tuple[bool, str]:
    """Return (ok, detail). ok=False means the CC route cannot run right now."""
    try:
        import docker  # noqa: F401
    except Exception as exc:  # pragma: no cover - import guard
        return False, f"docker-py not importable: {type(exc).__name__}"
    try:
        import docker
        client = docker.from_env()
        client.ping()
    except Exception as exc:
        return False, f"docker daemon unreachable: {type(exc).__name__}"
    try:
        client.images.get(DEFAULT_IMAGE)
    except Exception:
        return False, f"CC image '{DEFAULT_IMAGE}' not found (build docker/cc-runner)"
    return True, "ok"


def _bedrock_environment() -> dict[str, str]:
    env = {k: os.environ[k] for k in _BEDROCK_KEYS if os.environ.get(k)}
    # Bedrock needs a region; default to us-east-1 if a bearer token is set
    # without an explicit region.
    if env.get("AWS_BEARER_TOKEN_BEDROCK") and not (
        env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION")
    ):
        env["AWS_REGION"] = "us-east-1"
    return env


def run_cc_turn(
    *,
    query: str,
    model_id: str | None,
    send_event: SendEvent,
    session_id: str | None = None,
    image: str | None = None,
    scratch_host_path: str | None = None,
    extra_environment: dict[str, str] | None = None,
) -> None:
    """Execute one Container-CC turn, emitting {event,data} frames via send_event.

    Always terminates with exactly one ``query_complete`` or ``query_error``
    (the translator guarantees this), so the QueryTask reaches a terminal state.
    """
    import docker
    from docker.errors import APIError, NotFound

    image = image or DEFAULT_IMAGE
    command = list(_BASE_CMD)
    if model_id:
        command += ["--model", model_id]
    if session_id:
        command += ["--resume", session_id]

    environment = _bedrock_environment()
    if extra_environment:
        environment.update(extra_environment)

    volumes: dict[str, dict[str, str]] = {}
    if scratch_host_path:
        volumes[scratch_host_path] = {"bind": "/data/scratch", "mode": "rw"}

    translator = CCStreamTranslator()
    client = docker.from_env()
    container = None
    try:
        container = client.containers.run(
            image=image,
            command=command,
            environment=environment,
            volumes=volumes or None,
            working_dir="/home/user",
            labels={"nextseek.cc": "1"},
            platform="linux/amd64",
            detach=True,
            stdin_open=True,
            tty=False,
            stdout=True,
            stderr=True,
        )

        raw = container.attach_socket(
            params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1}
        )
        stdout_stream = container.logs(stream=True, follow=True, stdout=True, stderr=False)
        sock = BridgeAttachSocket(raw, stdout_stream=stdout_stream)

        envelope = json.dumps(
            {"type": "user", "message": {"role": "user", "content": query}},
            separators=(",", ":"),
        )
        sock.send_stdin((envelope + "\n").encode("utf-8"))
        sock.close_stdin()

        while True:
            line = sock.read_event_line()
            if line is None:
                break
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                logger.info("CC: skipping non-JSON stdout line: %r", line[:200])
                continue
            for event, data in translator.handle(payload):
                send_event(event, data)
            if translator._terminated:  # result event already emitted terminal frame
                break

        # Safety net: emit a terminal frame if the stream ended without `result`.
        for event, data in translator.finalize():
            send_event(event, data)

    except (APIError, NotFound) as exc:
        logger.exception("CC docker error")
        send_event("query_error", {
            "error": f"Container error: {type(exc).__name__}",
            "agent": "container_cc",
            "session_id": translator.session_id,
        })
    except Exception as exc:  # noqa: BLE001 - contain all failures to a terminal frame
        logger.exception("CC turn failed")
        send_event("query_error", {
            "error": f"Container-CC turn failed: {type(exc).__name__}",
            "agent": "container_cc",
            "session_id": translator.session_id,
        })
    finally:
        if container is not None:
            try:
                container.stop(timeout=5)
            except Exception:
                pass
            try:
                container.remove(force=True)
            except Exception:
                pass

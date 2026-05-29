"""T06: WebSocket bridge endpoint at ``/ws/chat``.

Authenticates the upgrade against ``TokenStore``, resolves (or skips)
auto-resume against the mounted ``.claude/`` tree, launches one Claude
container via T05's docker-py wrapper, and relays Claude's stream-json
output back to the chat UI.

Invariants (spec §3):
  * Missing / bad Authorization -> close(4401) before ``accept``.
  * Malformed first frame -> error frame + close(4400).
  * Container-start failure -> error frame + close(1011) + cleanup.
  * Frame ordering on resume mismatch: ``error(reason="resume_failed", ...)``
    FIRST, then ``session_started`` with the actual session id (DD-14).
  * ``session_started`` precedes any assistant / tool_use frame.
  * On clean EOF without an explicit Claude ``result`` event, a final
    ``session_ended`` frame is still emitted before close.
  * Every exit path uses ``try/finally`` (no ``asyncio.shield``) to close
    the attach socket and stop the container if they exist.
  * No logging of ``AuthenticatedIdentity``, ``bridge_env``, raw headers,
    NExtSEEK passwords, or ``AWS_BEARER_TOKEN_BEDROCK`` (R-03).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re as _re
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, BinaryIO

from fastapi import APIRouter, Depends, WebSocket
from starlette.websockets import WebSocketDisconnect, WebSocketState

from dmac_assistant.auth import (
    AuthenticatedIdentity,
    AuthenticationError,
    TokenStore,
    get_token_store,
)
from dmac_assistant.config import BridgeConfig, load_config
from dmac_assistant.containers import (
    async_attach,
    async_start_container,
    async_stop_and_remove,
    exec_cc_turn,
    exec_ns_turn,
    kill_exec_pid,
)
from dmac_assistant.copier import copy_files
from dmac_assistant.ns_adapter import ns_event_to_frames
from dmac_assistant.router import models as router_models
from dmac_assistant.router.agent import RouterAgent
from dmac_assistant.router.baml_client.types import (
    ModelClass,
    Route,
    RouterDecision,
)
from dmac_assistant.run_tracker import diff_files, snapshot_scratch_files
from dmac_assistant.sessions import most_recent_session
from dmac_assistant.streamjson import StreamEvent, StreamJsonParser

router = APIRouter()
log = logging.getLogger(__name__)

DEFAULT_IMAGE = "dmac-assistant:poc"
CWD = "/home/user"
_ROUTER_TRUTHY = frozenset({"1", "true", "yes", "on"})

# BAML 0.222.0 .value returns capitalized identifiers, not @alias strings.
_ROUTE_ALIAS: dict[Route, str] = {
    Route.NextseekQuery: "nextseek_query",
    Route.ContainerCC: "container_cc",
}
_MODEL_CLASS_ALIAS: dict[ModelClass, str] = {
    ModelClass.Sonnet: "sonnet",
    ModelClass.Haiku: "haiku",
    ModelClass.Opus: "opus",
}

_NS_TURN_TIMEOUT_SECONDS = 600.0
_CC_TURN_TIMEOUT_SECONDS = 300.0
_router_agent: RouterAgent | None = None

# Subprotocol name used by browser clients that cannot set an Authorization
# header on the WS upgrade. Client passes ["dmac.bearer", "<token>"] as the
# subprotocol list; server echoes "dmac.bearer" back on accept().
BEARER_SUBPROTOCOL = "dmac.bearer"

# T3 / H3: anchored user_id pattern used by ensure_user_output_dir. Mirrors
# the defense-in-depth pattern in containers.py (config.py does not export
# _USER_ID_RE publicly; see R-08).
_USER_ID_RE_OUTPUT = _re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def ensure_user_output_dir(output_root: Path, user_id: str) -> Path:
    """Idempotently create <output_root>/<user_id>/.

    Called at session start before the container is launched, so the
    bind mount has a real source. H3 — without this the Docker API
    fails with 'invalid mount config: bind source path does not exist'.
    """
    if not _USER_ID_RE_OUTPUT.fullmatch(user_id):
        raise ValueError(f"invalid user_id: {user_id!r}")
    target = output_root / user_id
    target.mkdir(parents=True, exist_ok=True)
    return target


class _InitMalformed(Exception):
    """Raised when a system/init event has no usable ``session_id``.

    The message is intentionally static so it cannot carry dynamic content
    (env values, stream bytes, etc.) into any log record.
    """

    def __init__(self) -> None:
        super().__init__("init event missing session_id")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_subprotocol_token(websocket: WebSocket) -> str | None:
    """Return a bearer token smuggled via the ``Sec-WebSocket-Protocol`` header.

    Browsers cannot set ``Authorization`` on the WS upgrade, so the demo UI
    passes ``new WebSocket(url, ["dmac.bearer", "<token>"])``. Starlette
    exposes the requested subprotocols on ``websocket.scope["subprotocols"]``.
    The token is accepted only when the protocol list begins with
    ``dmac.bearer`` followed by a non-empty token entry.
    """
    subprotocols = websocket.scope.get("subprotocols") or []
    if len(subprotocols) >= 2 and subprotocols[0] == BEARER_SUBPROTOCOL:
        token = subprotocols[1]
        if isinstance(token, str) and token:
            return token
    return None


def _verify_websocket_identity(
    websocket: WebSocket, token_store: TokenStore
) -> tuple[Any, str | None]:
    """Authenticate the upgrade.

    Returns ``(identity, accept_subprotocol)``. ``accept_subprotocol`` is the
    subprotocol name the server must echo on ``accept()`` when the client
    authenticated via ``Sec-WebSocket-Protocol``; ``None`` for header auth.
    """
    authorization = websocket.headers.get("authorization")
    if authorization:
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise AuthenticationError("invalid authorization header")
        return token_store.verify(token), None

    subprotocol_token = _extract_subprotocol_token(websocket)
    if subprotocol_token is not None:
        return token_store.verify(subprotocol_token), BEARER_SUBPROTOCOL

    raise AuthenticationError("missing authorization header")


def stream_event_to_ws_frames(
    event: StreamEvent, *, current_session_id: str | None
) -> list[dict[str, Any]]:
    """Translate one parser event into zero or more WS frames.

    ``system/init`` events are handled by the caller (needs mismatch logic).
    This function handles the content-only subset: assistant text blocks,
    tool_use blocks, result events, and parser errors.
    """
    if event.kind == "error":
        return [
            {
                "type": "error",
                "reason": "stream_json_error",
                "detail": event.error.reason if event.error else "",
            }
        ]

    payload = event.payload or {}
    etype = payload.get("type")
    if etype == "assistant":
        frames: list[dict[str, Any]] = []
        content = (payload.get("message") or {}).get("content") or []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text" and isinstance(block.get("text"), str):
                frames.append(
                    {"type": "assistant_message", "content": block["text"]}
                )
            elif btype == "tool_use":
                tool_input = block.get("input")
                frames.append(
                    {
                        "type": "tool_use",
                        "tool": block.get("name"),
                        "input": tool_input
                        if isinstance(tool_input, dict)
                        else {},
                        "id": block.get("id"),
                    }
                )
        return frames
    if etype == "result":
        return [
            {"type": "session_ended", "session_id": current_session_id}
        ]
    return []


async def _send_json_safe(websocket: WebSocket, frame: dict[str, Any]) -> bool:
    """Best-effort send. Returns False if the socket is already gone."""
    if websocket.client_state != WebSocketState.CONNECTED:
        return False  # pragma: no cover — defensive; send path always guards upstream
    try:
        await websocket.send_json(frame)
        return True
    except (WebSocketDisconnect, RuntimeError):  # pragma: no cover — race guard
        return False


async def _read_frame(attach_socket: Any) -> tuple[str, bytes] | None:
    """Run the blocking attach read in a worker thread."""
    return await asyncio.to_thread(attach_socket.read_frame)


async def _send_stdin_line(
    attach_socket: Any,
    content: str,
    *,
    new_session: bool = False,
) -> bool:
    """Write one Claude stream-json user event to container stdin."""
    envelope: dict[str, Any] = {
        "type": "user",
        "message": {
            "role": "user",
            "content": content,
        },
    }
    if new_session:
        envelope["new_session"] = True
    payload = json.dumps(envelope, separators=(",", ":"))
    try:
        await asyncio.to_thread(
            attach_socket.send_stdin,
            (payload + "\n").encode("utf-8"),
        )
        return True
    except Exception:  # pragma: no cover - error behavior asserted at callers
        return False


def dispatch_post_turn_copy(
    *,
    scratch_root: Path,
    output_root: Path,
    user_id: str,
    new_files: set[str],
) -> None:
    """Publish every new/changed file discovered during the turn.

    Errors are swallowed (logged with type name only — R-03) so copier
    failure cannot kill the WS session (L2). Per-file iteration is
    sorted so multi-file publication is deterministic. One bad file
    does not block the rest (M-2).
    """
    for rel in sorted(new_files):
        try:
            copy_files(
                scratch_root=scratch_root,
                output_root=output_root,
                user_id=user_id,
                rel_paths={rel},
            )
        except Exception as exc:  # noqa: BLE001 — broad-but-contained
            log.warning(
                "copier failed for user=%s rel=%s: %s",
                user_id, rel, type(exc).__name__,
            )


def _build_bridge_env(
    *,
    config: BridgeConfig | None = None,
    identity: AuthenticatedIdentity | None = None,
) -> dict[str, str]:
    """Assemble the env passed to the in-container Claude Code from the
    bridge's process environment.

    Legacy keys (``AWS_REGION``, ``AWS_BEARER_TOKEN_BEDROCK``,
    ``NEXTSEEK_URL``) are always emitted, even as empty strings, to
    preserve the pre-T9 passthrough contract. New skip-if-empty keys are
    ``GCP_API_KEY``, ``NEO4J_URI``, ``NEO4J_USER``, ``NEO4J_PASSWORD``,
    ``NEO4J_DATABASE``.

    ``NEXTSEEK_BASE_URL`` is DERIVED from ``NEXTSEEK_URL`` when the host
    env lacks an explicit override (LLM router plan T0.3 / F-T0.3-2 hardener,
    2026-05-14). Mirrors ``container/entrypoint.sh:14`` because per-turn
    ``docker exec`` bypasses the entrypoint per DD-04. Always emitted; empty
    string only when BOTH ``NEXTSEEK_BASE_URL`` and ``NEXTSEEK_URL`` are unset.

    When both ``config`` and ``identity`` are supplied, also emits a
    ``DMAC_PATH_MAPPINGS`` JSON env var that maps container paths (the
    fixed ``/data/output`` and ``/data/scratch`` mount points) to their
    per-user host roots (``<config.output_root>/<user_id>`` and
    ``<config.scratch_root>/<user_id>``). Plan A T9b implementation of D19.
    """
    env: dict[str, str] = {}
    # Legacy keys: ALWAYS emitted (W3-C2 — preserves pre-T9 contract).
    # DO NOT change to skip-if-empty — `tests/unit/test_ws_bridge_env.py::
    # test_unset_keys_omitted` asserts these keys are present in bridge_env
    # even when unset.
    for key in ("AWS_REGION", "AWS_BEARER_TOKEN_BEDROCK", "NEXTSEEK_URL"):
        env[key] = os.environ.get(key, "")
    # NEXTSEEK_BASE_URL: derived from NEXTSEEK_URL when unset (T0.3 F-T0.3-2
    # hardener, LLM router plan 2026-05-14). Mirrors `container/entrypoint.sh:14`
    # `: ${NEXTSEEK_BASE_URL:=${NEXTSEEK_URL:-}}` because per-turn `docker exec`
    # bypasses the entrypoint per DD-04. Always emitted (legacy contract);
    # empty string only when BOTH host vars are unset. `chat_nextseek.config.
    # ChatConfig:273` reads NEXTSEEK_BASE_URL directly with no NEXTSEEK_URL
    # fallback, so this derivation is load-bearing.
    env["NEXTSEEK_BASE_URL"] = (
        os.environ.get("NEXTSEEK_BASE_URL")
        or os.environ.get("NEXTSEEK_URL", "")
    )
    # New keys: skip-if-empty.
    # NEO4J_DATABASE joined in T0.3 (LLM router plan 2026-05-14): chat_nextseek
    # reads this exact name for entity-graph queries; sibling of the other
    # NEO4J_* skip-if-empty keys.
    for key in (
        "GCP_API_KEY",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASSWORD",
        "NEO4J_DATABASE",
    ):
        value = os.environ.get(key)
        if value is not None and value.strip():
            env[key] = value
    if config is not None and identity is not None:
        env["DMAC_PATH_MAPPINGS"] = json.dumps(
            {
                "output": {
                    "container_root": "/data/output",
                    "host_root": str(config.output_root / identity.user_id),
                },
                "scratch": {
                    "container_root": "/data/scratch",
                    "host_root": str(config.scratch_root / identity.user_id),
                },
            },
            separators=(",", ":"),
        )
    return env


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.websocket("/chat")
async def chat_ws(
    websocket: WebSocket,
    token_store: TokenStore = Depends(get_token_store),
) -> None:
    # 1. Authenticate BEFORE accepting the upgrade.
    try:
        identity, accept_subprotocol = _verify_websocket_identity(
            websocket, token_store
        )
    except AuthenticationError:
        await websocket.close(code=4401)
        return

    if accept_subprotocol is not None:
        await websocket.accept(subprotocol=accept_subprotocol)
    else:
        await websocket.accept()

    container: Any = None
    attach_socket: Any = None
    parser = StreamJsonParser()
    current_session_id: str | None = None
    session_started_emitted = False
    session_ended_emitted = False
    # Shared single-element box so the relay task can signal "a new turn
    # started" to the outer loop. Drives the EOF synthetic session_ended:
    # we only emit one on clean socket close if a turn was still in flight.
    awaiting_end: list[bool] = [False]
    requested_session_id: str | None = None
    start_task: asyncio.Task[Any] | None = None

    try:
        # 2. Load config + read the first client frame.
        config = load_config()
        try:
            first_frame = await websocket.receive_json()
        except (WebSocketDisconnect, ValueError):  # pragma: no cover — client race
            return

        if (
            not isinstance(first_frame, dict)
            or first_frame.get("type") != "user_message"
            or not isinstance(first_frame.get("content"), str)
        ):
            await _send_json_safe(
                websocket, {"type": "error", "reason": "bad_handshake"}
            )
            await websocket.close(code=4400)
            return

        first_content: str = first_frame["content"]
        new_session = bool(first_frame.get("new_session", False))

        if not new_session:
            claude_root = (
                Path(config.claude_users_root) / identity.user_id / ".claude"
            )
            recent = most_recent_session(claude_root, CWD)
            requested_session_id = recent.session_id if recent else None

        # 3. Start the container.
        bridge_env = _build_bridge_env(config=config, identity=identity)
        # H3 / T3: ensure the per-user host dirs exist BEFORE Docker creates
        # the bind mounts. Without this, the first login for a brand-new user
        # fails with `invalid mount config: bind source path does not exist`.
        ensure_user_output_dir(config.output_root, identity.user_id)
        (config.scratch_root / identity.user_id).mkdir(parents=True, exist_ok=True)
        # T3.2: branch only after the H3 host-side directory creation block,
        # so router-on idle startup inherits the bind mount source dirs too.
        if _router_enabled():
            return await _chat_ws_router_on(
                websocket=websocket,
                identity=identity,
                config=config,
                first_content=first_content,
                requested_session_id=requested_session_id,
                bridge_env=bridge_env,
                new_session=new_session,
            )
        pre_turn_files = snapshot_scratch_files(
            config.scratch_root, identity.user_id
        )

        async def fire_post_turn_copy() -> None:
            after = snapshot_scratch_files(config.scratch_root, identity.user_id)
            new = diff_files(pre_turn_files, after)
            if not new:
                return
            await asyncio.to_thread(
                dispatch_post_turn_copy,
                scratch_root=config.scratch_root,
                output_root=config.output_root,
                user_id=identity.user_id,
                new_files=new,
            )
            pre_turn_files.clear()
            pre_turn_files.update(after)

        try:
            start_task = asyncio.create_task(
                async_start_container(
                    identity,
                    image=DEFAULT_IMAGE,
                    session_id=requested_session_id,
                    bridge_env=bridge_env,
                    config=config,
                )
            )
            done, _ = await asyncio.wait(
                {start_task}, return_when=asyncio.ALL_COMPLETED
            )
            container = done.pop().result()
            start_task = None
        except asyncio.CancelledError:
            if start_task is not None:
                try:
                    done, _ = await asyncio.wait(
                        {start_task}, return_when=asyncio.ALL_COMPLETED
                    )
                    container = done.pop().result()
                except Exception:
                    container = None
                finally:
                    start_task = None
            raise
        except BaseException as exc:
            # DEBUG (temp): include exception type to diagnose silent failures.
            log.warning("container start failed: %s", type(exc).__name__)
            await _send_json_safe(
                websocket,
                {"type": "error", "reason": "container_start_failed"},
            )
            await websocket.close(code=1011)
            return

        try:
            attach_socket = await async_attach(container)
        except BaseException as exc:
            log.warning("container attach failed: %s", type(exc).__name__)
            await _send_json_safe(
                websocket,
                {"type": "error", "reason": "container_start_failed"},
            )
            await websocket.close(code=1011)
            return

        # 4. Forward the first user message to Claude's stdin.
        if not await _send_stdin_line(
            attach_socket, first_content, new_session=new_session
        ):
            log.warning("stdin write failed on first frame")
            await _send_json_safe(
                websocket, {"type": "error", "reason": "internal"}
            )
            await websocket.close(code=1011)
            return
        awaiting_end[0] = True

        # 5. Launch the client->container relay task.
        relay_task = asyncio.create_task(
            _relay_client_to_container(websocket, attach_socket, awaiting_end)
        )

        try:
            # 6. Drive the container->client read loop.
            while True:
                frame_task = asyncio.create_task(_read_frame(attach_socket))
                done, pending = await asyncio.wait(
                    {frame_task, relay_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if relay_task in done:
                    frame_task.cancel()
                    for pending_task in pending:
                        pending_task.cancel()
                    break
                frame = frame_task.result()
                if frame is None:
                    break
                stream_name, payload = frame
                if stream_name != "stdout":
                    # stderr is intentionally not forwarded; it can carry
                    # diagnostic strings we do not want to relay as-is.
                    continue
                events = parser.feed(payload)
                for event in events:
                    (
                        session_started_emitted,
                        session_ended_emitted,
                        current_session_id,
                    ) = await _dispatch_event(
                        websocket,
                        event,
                        session_started_emitted=session_started_emitted,
                        session_ended_emitted=session_ended_emitted,
                        current_session_id=current_session_id,
                        requested_session_id=requested_session_id,
                        parser=parser,
                        awaiting_end=awaiting_end,
                        post_turn_callback=fire_post_turn_copy,
                    )

            # Clean EOF: flush parser + emit synthetic session_ended if none.
            for event in parser.flush():
                (
                    session_started_emitted,
                    session_ended_emitted,
                    current_session_id,
                ) = await _dispatch_event(
                    websocket,
                    event,
                    session_started_emitted=session_started_emitted,
                    session_ended_emitted=session_ended_emitted,
                    current_session_id=current_session_id,
                    requested_session_id=requested_session_id,
                    parser=parser,
                    awaiting_end=awaiting_end,
                    post_turn_callback=fire_post_turn_copy,
                )

            if session_started_emitted and awaiting_end[0]:
                await _send_json_safe(
                    websocket,
                    {
                        "type": "session_ended",
                        "session_id": current_session_id,
                    },
                )
                session_ended_emitted = True
                awaiting_end[0] = False
                await fire_post_turn_copy()
        finally:
            relay_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(relay_task, timeout=0.2)

    except asyncio.CancelledError:  # pragma: no cover — server-shutdown branch
        raise
    except WebSocketDisconnect:  # pragma: no cover — outer race
        pass
    except _InitMalformed:
        # Stream-contract violation by the container. Message is static so
        # it cannot carry canaries; type name is safe.
        log.error("ws bridge: malformed init event (no session_id)", exc_info=False)
        await _send_json_safe(
            websocket,
            {
                "type": "error",
                "reason": "stream_json_error",
                "detail": "init event missing session_id",
            },
        )
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close(code=1011)
            except Exception:  # pragma: no cover — close-during-shutdown race
                pass
    except Exception as exc:
        # R-03: never log str(exc)/repr(exc)/exc.args/exc_info=True — docker
        # APIError bodies can echo env values including AWS_BEARER_TOKEN_BEDROCK
        # and the user's NExtSEEK password. Only the exception type name is safe.
        log.error(
            "unhandled error in ws bridge: %s",
            type(exc).__name__,
            exc_info=False,
        )
        await _send_json_safe(
            websocket, {"type": "error", "reason": "internal"}
        )
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close(code=1011)
            except Exception:  # pragma: no cover — close-during-shutdown race
                pass
    finally:
        if start_task is not None:
            try:
                started_container = await start_task
                if container is None:
                    container = started_container
            except Exception:  # pragma: no cover — startup already failed/cancelled
                pass
        if attach_socket is not None:
            try:
                await asyncio.to_thread(attach_socket.close)
            except Exception:  # pragma: no cover — cleanup best-effort
                pass
        if container is not None:
            try:
                await async_stop_and_remove(container)
            except Exception:  # pragma: no cover — cleanup best-effort
                pass
        if websocket.client_state == WebSocketState.CONNECTED:
            try:
                await websocket.close()
            except Exception:  # pragma: no cover — already-closed race
                pass


async def _dispatch_event(
    websocket: WebSocket,
    event: StreamEvent,
    *,
    session_started_emitted: bool,
    session_ended_emitted: bool,
    current_session_id: str | None,
    requested_session_id: str | None,
    parser: StreamJsonParser,
    awaiting_end: list[bool],
    post_turn_callback: Callable[[], Awaitable[None]] | None = None,
) -> tuple[bool, bool, str | None]:
    """Emit the WS frames for a single parser event and update state.

    When a ``session_ended`` frame is dispatched and ``post_turn_callback``
    is set, the callback is awaited before the function returns. This is
    the single hook the bridge uses to fire the post-turn copier on the
    NORMAL turn-end path. The synthetic-EOF branch in ``chat_ws`` awaits
    the same callable directly.
    """
    # system/init: drives session_started + resume-mismatch ordering.
    if event.kind == "event":
        payload = event.payload or {}
        if (
            payload.get("type") == "system"
            and payload.get("subtype") == "init"
        ):
            actual = parser.session_id or payload.get("session_id")
            if not isinstance(actual, str) or not actual:
                # Stream-contract violation: init event without session_id.
                # Surfaced to the outer relay loop which owns WS lifecycle.
                raise _InitMalformed()
            previous_session_id = current_session_id
            current_session_id = actual
            if (
                requested_session_id is not None
                and requested_session_id != actual
            ):
                await _send_json_safe(
                    websocket,
                    {
                        "type": "error",
                        "reason": "resume_failed",
                        "requested": requested_session_id,
                        "actual": actual,
                    },
                )
            if not session_started_emitted:
                await _send_json_safe(
                    websocket,
                    {
                        "type": "session_started",
                        "session_id": actual,
                    },
                )
                session_started_emitted = True
            elif (
                previous_session_id is not None
                and actual != previous_session_id
            ):
                # New Claude session mid-socket (e.g. user_message with new_session: true).
                await _send_json_safe(
                    websocket,
                    {
                        "type": "session_started",
                        "session_id": actual,
                    },
                )
                session_started_emitted = True
            return session_started_emitted, session_ended_emitted, current_session_id

    # All other events (assistant, tool_use, result, parser errors).
    for frame in stream_event_to_ws_frames(
        event, current_session_id=current_session_id
    ):
        await _send_json_safe(websocket, frame)
        if frame.get("type") == "session_ended":
            session_ended_emitted = True
            awaiting_end[0] = False
            if post_turn_callback is not None:
                await post_turn_callback()
    return session_started_emitted, session_ended_emitted, current_session_id


async def _relay_client_to_container(
    websocket: WebSocket, attach_socket: Any, awaiting_end: list[bool]
) -> None:
    """Forward subsequent ``user_message`` frames from the client to stdin."""
    try:
        while True:
            frame = await websocket.receive_json()
            if not isinstance(frame, dict):
                continue
            if frame.get("type") != "user_message":
                continue
            content = frame.get("content")
            if not isinstance(content, str):
                continue
            ns = bool(frame.get("new_session", False))
            if not await _send_stdin_line(attach_socket, content, new_session=ns):
                return
            awaiting_end[0] = True
    except (WebSocketDisconnect, asyncio.CancelledError, RuntimeError, ValueError):
        return


# ---------------------------------------------- T3.2: per-turn router dispatch


_NS_SESSION_ID_RE = _re.compile(r"^ns-[0-9a-f]{12}$")


def _router_enabled() -> bool:
    """True when DMAC_ROUTER_ENABLED is truthy for this WS connection."""
    raw = os.environ.get("DMAC_ROUTER_ENABLED", "")
    return raw.strip().lower() in _ROUTER_TRUTHY


def _open_ns_stderr_capture(ns_session_id: str) -> BinaryIO | None:
    """Open the per-session NS-route stderr capture file (opt-in).

    Phase 7 residual #1 visibility (2026-05-18): the in-container chat_nextseek
    runner emits parser_agent exceptions to docker exec stderr (post-fd-shuffle
    in container/runner_ns.py). The bridge consumes those frames via
    ``BridgeAttachSocket.read_event_line``; without this capture they were
    truncated to 80 bytes and DEBUG-logged only, which uvicorn at
    ``--log-level error`` silences entirely.

    When ``DMAC_BRIDGE_NS_STDERR_DIR`` is set, the bridge writes every stderr
    frame verbatim (append-mode binary) to
    ``<DMAC_BRIDGE_NS_STDERR_DIR>/<ns_session_id>.stderr.log`` so the
    underlying parser exception text is durable on disk and greppable
    post-mortem. Defaults to None (production-safe; no behavior change).

    Returns None when the env var is unset, the directory can't be created,
    or ``ns_session_id`` fails the canonical format guard (path-traversal
    defense; T3.2 spec L424 NS session id format is ``^ns-[0-9a-f]{12}$``).
    Diagnostic capture must never block a turn.
    """
    raw = os.environ.get("DMAC_BRIDGE_NS_STDERR_DIR", "").strip()
    if not raw:
        return None
    if not _NS_SESSION_ID_RE.fullmatch(ns_session_id):
        log.warning(
            "ns stderr capture refused: ns_session_id failed format guard"
        )
        return None
    try:
        dir_path = Path(raw).expanduser()
        dir_path.mkdir(parents=True, exist_ok=True)
        return (dir_path / f"{ns_session_id}.stderr.log").open("ab")
    except OSError as exc:
        log.warning(
            "ns stderr capture disabled (cannot open %r): %s",
            raw,
            type(exc).__name__,
        )
        return None


def _get_router_agent() -> RouterAgent:  # pragma: no cover
    """Return the bridge-process RouterAgent singleton.

    Per F-T3.2-2-3, unit tests monkeypatch this host orchestration surface;
    the real lazy initialization path is covered by T4.2 image integration.
    """
    global _router_agent
    if _router_agent is None:
        _router_agent = RouterAgent()
    return _router_agent


async def _chat_ws_router_on(  # pragma: no cover
    *,
    websocket: WebSocket,
    identity: AuthenticatedIdentity,
    config: BridgeConfig,
    first_content: str,
    requested_session_id: str | None,
    bridge_env: dict[str, str],
    new_session: bool,
) -> None:
    """Router-on WS orchestration, covered end-to-end by T4.2 image tests."""
    del new_session
    container: Any = None
    current_session_id: str | None = requested_session_id
    session_started_emitted = False
    session_ended_emitted = False
    ns_session_key = (
        f"{identity.user_id}-ws-{int(asyncio.get_running_loop().time())}"
    )
    pre_turn_files = snapshot_scratch_files(config.scratch_root, identity.user_id)

    async def fire_post_turn_copy() -> None:
        after = snapshot_scratch_files(config.scratch_root, identity.user_id)
        new = diff_files(pre_turn_files, after)
        if not new:
            return
        await asyncio.to_thread(
            dispatch_post_turn_copy,
            scratch_root=config.scratch_root,
            output_root=config.output_root,
            user_id=identity.user_id,
            new_files=new,
        )
        pre_turn_files.clear()
        pre_turn_files.update(after)

    try:
        container = await async_start_container(
            identity,
            image=DEFAULT_IMAGE,
            session_id=None,
            bridge_env=bridge_env,
            config=config,
            runtime_mode="idle",
            command_override=[],
        )
        (
            session_started_emitted,
            session_ended_emitted,
            current_session_id,
        ) = await _dispatch_one_turn(
            websocket=websocket,
            container=container,
            query=first_content,
            identity=identity,
            config=config,
            bridge_env=bridge_env,
            current_session_id=current_session_id,
            session_started_emitted=session_started_emitted,
            session_ended_emitted=session_ended_emitted,
            requested_session_id=requested_session_id,
            ns_session_key=ns_session_key,
            post_turn_callback=fire_post_turn_copy,
        )

        while True:
            try:
                frame = await websocket.receive_json()
            except (WebSocketDisconnect, ValueError):
                break
            if not isinstance(frame, dict) or frame.get("type") != "user_message":
                continue
            content = frame.get("content")
            if not isinstance(content, str):
                continue
            (
                session_started_emitted,
                session_ended_emitted,
                current_session_id,
            ) = await _dispatch_one_turn(
                websocket=websocket,
                container=container,
                query=content,
                identity=identity,
                config=config,
                bridge_env=bridge_env,
                current_session_id=current_session_id,
                session_started_emitted=session_started_emitted,
                session_ended_emitted=session_ended_emitted,
                requested_session_id=current_session_id,
                ns_session_key=ns_session_key,
                post_turn_callback=fire_post_turn_copy,
            )
    finally:
        if container is not None:
            with contextlib.suppress(Exception):
                await async_stop_and_remove(container)


async def _dispatch_one_turn(
    *,
    websocket: WebSocket,
    container: Any,
    query: str,
    identity: AuthenticatedIdentity,
    config: BridgeConfig,
    bridge_env: dict[str, str],
    current_session_id: str | None,
    session_started_emitted: bool,
    session_ended_emitted: bool,
    requested_session_id: str | None = None,
    ns_session_key: str | None = None,
    post_turn_callback: Callable[[], Awaitable[None]] | None = None,
) -> tuple[bool, bool, str | None]:
    """Route one user_message, emit route_decided, then dispatch the turn."""
    agent = _get_router_agent()
    decision: RouterDecision = await agent.route(query)

    model_class: ModelClass | None = decision.model_class
    if decision.route == Route.ContainerCC and model_class is None:
        model_class = ModelClass.Sonnet
        log.warning(
            "router fallback: container_cc/model_class=null -> sonnet substituted"
        )

    await _send_json_safe(
        websocket,
        {
            "type": "route_decided",
            "route": _ROUTE_ALIAS[decision.route],
            "model_class": (
                _MODEL_CLASS_ALIAS[model_class]
                if model_class is not None
                else None
            ),
        },
    )

    if decision.route == Route.ContainerCC:
        model_id = router_models.resolve(model_class)
        return await _dispatch_cc_turn(
            websocket=websocket,
            container=container,
            query=query,
            model_id=model_id,
            session_id=current_session_id,
            identity=identity,
            config=config,
            bridge_env=bridge_env,
            current_session_id=current_session_id,
            session_started_emitted=session_started_emitted,
            session_ended_emitted=session_ended_emitted,
            requested_session_id=requested_session_id,
            post_turn_callback=post_turn_callback,
        )

    await _dispatch_ns_turn(
        websocket=websocket,
        container=container,
        query=query,
        identity=identity,
        config=config,
        bridge_env=bridge_env,
        ns_session_key=ns_session_key,
    )
    if post_turn_callback is not None:
        await post_turn_callback()
    return session_started_emitted, session_ended_emitted, current_session_id


async def _dispatch_cc_turn(
    *,
    websocket: WebSocket,
    container: Any,
    query: str,
    model_id: str,
    session_id: str | None,
    identity: AuthenticatedIdentity,
    config: BridgeConfig,
    bridge_env: dict[str, str],
    current_session_id: str | None,
    session_started_emitted: bool,
    session_ended_emitted: bool,
    requested_session_id: str | None,
    post_turn_callback: Callable[[], Awaitable[None]] | None = None,
) -> tuple[bool, bool, str | None]:
    """Run one Claude Code turn via T3.1 exec primitives."""
    sock: Any = None
    parser = StreamJsonParser()
    awaiting_end = [True]
    try:
        try:
            sock = await asyncio.to_thread(
                exec_cc_turn,
                container,
                query=query,
                model_id=model_id,
                session_id=session_id,
                identity=identity,
                config=config,
                bridge_env=bridge_env,
            )
        except Exception as cc_exc:  # noqa: BLE001
            log.warning("cc exec failed: %s", type(cc_exc).__name__)
            await _send_json_safe(
                websocket, {"type": "error", "reason": "cc_exec_failed"}
            )
            await _send_json_safe(
                websocket,
                {"type": "session_ended", "session_id": current_session_id},
            )
            return session_started_emitted, True, current_session_id

        state: dict[str, Any] = {
            "session_started_emitted": session_started_emitted,
            "session_ended_emitted": session_ended_emitted,
            "current_session_id": current_session_id,
        }

        async def _read_and_dispatch() -> None:
            while True:
                frame = await asyncio.to_thread(sock.read_frame)
                if frame is None:
                    break
                stream_name, payload = frame
                if stream_name != "stdout":
                    continue
                for event in parser.feed(payload):
                    (
                        state["session_started_emitted"],
                        state["session_ended_emitted"],
                        state["current_session_id"],
                    ) = await _dispatch_event(
                        websocket,
                        event,
                        session_started_emitted=state[
                            "session_started_emitted"
                        ],
                        session_ended_emitted=state["session_ended_emitted"],
                        current_session_id=state["current_session_id"],
                        requested_session_id=requested_session_id,
                        parser=parser,
                        awaiting_end=awaiting_end,
                        post_turn_callback=post_turn_callback,
                    )
            for event in parser.flush():
                (
                    state["session_started_emitted"],
                    state["session_ended_emitted"],
                    state["current_session_id"],
                ) = await _dispatch_event(
                    websocket,
                    event,
                    session_started_emitted=state["session_started_emitted"],
                    session_ended_emitted=state["session_ended_emitted"],
                    current_session_id=state["current_session_id"],
                    requested_session_id=requested_session_id,
                    parser=parser,
                    awaiting_end=awaiting_end,
                    post_turn_callback=post_turn_callback,
                )
            if state["session_started_emitted"] and awaiting_end[0]:
                await _send_json_safe(
                    websocket,
                    {
                        "type": "session_ended",
                        "session_id": state["current_session_id"],
                    },
                )
                state["session_ended_emitted"] = True
                awaiting_end[0] = False
                if post_turn_callback is not None:
                    await post_turn_callback()

        try:
            await asyncio.wait_for(
                _read_and_dispatch(), timeout=_CC_TURN_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            exec_id = getattr(sock, "_exec_id", None) if sock is not None else None
            if exec_id:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(kill_exec_pid, container, exec_id)
            await _send_json_safe(
                websocket, {"type": "error", "reason": "exec_timeout"}
            )
            await _send_json_safe(
                websocket,
                {
                    "type": "session_ended",
                    "session_id": state["current_session_id"],
                },
            )
            return (
                state["session_started_emitted"],
                True,
                state["current_session_id"],
            )
        return (
            state["session_started_emitted"],
            state["session_ended_emitted"],
            state["current_session_id"],
        )
    finally:
        if sock is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(sock.close)


async def _dispatch_ns_turn(
    *,
    websocket: WebSocket,
    container: Any,
    query: str,
    identity: AuthenticatedIdentity,
    config: BridgeConfig,
    bridge_env: dict[str, str],
    ns_session_key: str | None = None,
) -> None:
    """Run one NExtSEEK turn via T3.1 exec primitives."""
    ns_session_id = f"ns-{uuid.uuid4().hex[:12]}"
    terminal_emitted = False
    sock: Any = None
    stderr_capture_fh = _open_ns_stderr_capture(ns_session_id)
    try:
        try:
            sock = await asyncio.to_thread(
                exec_ns_turn,
                container,
                query=query,
                session_id=ns_session_key,
                identity=identity,
                config=config,
                bridge_env=bridge_env,
            )
        except Exception as ns_exc:  # noqa: BLE001
            log.warning("ns exec failed: %s", type(ns_exc).__name__)
            await _send_json_safe(
                websocket, {"type": "error", "reason": "ns_exec_failed"}
            )
            await _send_json_safe(
                websocket,
                {"type": "session_ended", "session_id": ns_session_id},
            )
            return
        if stderr_capture_fh is not None:
            sock.stderr_sink = stderr_capture_fh

        await _send_json_safe(
            websocket, {"type": "session_started", "session_id": ns_session_id}
        )

        async def _read_and_dispatch() -> None:
            nonlocal terminal_emitted
            event_index = 0
            while True:
                line = await asyncio.to_thread(sock.read_event_line)
                if line is None:
                    break
                if terminal_emitted:
                    log.debug("ns event dropped post-terminal: %s", line[:80])
                    continue
                try:
                    event = json.loads(line)
                except (TypeError, ValueError):
                    log.debug("ns invalid jsonl line dropped")
                    continue
                frames, is_terminal = ns_event_to_frames(
                    event, session_id=ns_session_id, event_index=event_index
                )
                for frame in frames:
                    await _send_json_safe(websocket, frame)
                if is_terminal:
                    terminal_emitted = True
                event_index += 1

            if not terminal_emitted:
                await _send_json_safe(
                    websocket, {"type": "error", "reason": "ns_exec_truncated"}
                )
                await _send_json_safe(
                    websocket,
                    {"type": "session_ended", "session_id": ns_session_id},
                )

        try:
            await asyncio.wait_for(
                _read_and_dispatch(), timeout=_NS_TURN_TIMEOUT_SECONDS
            )
        except asyncio.TimeoutError:
            exec_id = getattr(sock, "_exec_id", None) if sock is not None else None
            if exec_id:
                with contextlib.suppress(Exception):
                    await asyncio.to_thread(kill_exec_pid, container, exec_id)
            await _send_json_safe(
                websocket, {"type": "error", "reason": "exec_timeout"}
            )
            await _send_json_safe(
                websocket,
                {"type": "session_ended", "session_id": ns_session_id},
            )
    finally:
        if sock is not None:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(sock.close)
        if stderr_capture_fh is not None:
            with contextlib.suppress(Exception):
                stderr_capture_fh.close()

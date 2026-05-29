"""T05: Docker-py wrapper for the DMAC bridge's per-session container.

Owns four things (see task-05-containers spec):
  1. Mount assembly from AuthenticatedIdentity + BridgeConfig
  2. Command assembly for `claude --print --output-format stream-json ...`
  3. Container lifecycle (run / attach / stop / remove)
  4. Attach-socket normalization (Docker stdcopy demux -> stdout/stderr bytes)

DD-07: project mounts are `ro`; `/data/scratch` and `/home/user/.claude` are `rw`.
DD-13: every run uses working_dir="/home/user" so resume targets the correct cwd.
DD-14: T05 only appends `--resume <uuid>`; mismatch detection is T06.
R-02 split: T05 proves mount assembly; T07 does the live EROFS assertion.
R-03: ContainerSpec.__repr__ redacts sensitive env keys by name.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import struct
from typing import Any, BinaryIO, Mapping

import docker
from docker.errors import APIError, NotFound
from docker.models.containers import Container
from pydantic import BaseModel, ConfigDict

from dmac_assistant.auth import AuthenticatedIdentity
from dmac_assistant.config import BridgeConfig

log = logging.getLogger(__name__)

# Local defense-in-depth user_id validator (R-08): config.py exposes only a
# private _USER_ID_RE, so we mirror the anchored pattern here rather than
# importing a nonexistent public symbol.
_USER_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_REDACTED_ENV_KEYS = frozenset({
    "NEXTSEEK_PASSWORD",
    "AWS_BEARER_TOKEN_BEDROCK",
    "NEO4J_PASSWORD",
    "GCP_API_KEY",
    # Encodes host filesystem layout (output_root + scratch_root paths).
    # Not a credential but R-03 forbids logging bridge_env contents.
    "DMAC_PATH_MAPPINGS",
    "MYSQL_DEV_PASSWORD",
    "SESSION_DB_PASSWORD",
    "API_PASS",
})

_BASE_COMMAND: tuple[str, ...] = (
    "claude",
    "--print",
    "--input-format",
    "stream-json",
    "--output-format",
    "stream-json",
    "--verbose",
    "--dangerously-skip-permissions",
)

_CONTAINER_WORKING_DIR = "/home/user"
_CONTAINER_CLAUDE_HOME = "/home/user/.claude"
_CONTAINER_SCRATCH = "/data/scratch"
_CONTAINER_PROJECTS_PREFIX = "/data/projects"
_CONTAINER_OUTPUT = "/data/output"
_CONTAINER_CATALOG_FILE = "/etc/dmac/agent_model_catalog.json"


class ContainerSpec(BaseModel):
    """Frozen description of a container to be launched."""

    model_config = ConfigDict(frozen=True)

    image: str
    command: list[str]
    environment: dict[str, str]
    volumes: dict[str, dict[str, str]]
    working_dir: str
    labels: dict[str, str]
    name: str | None = None

    def __repr__(self) -> str:  # pragma: no cover trivial
        safe_env = {
            k: ("<REDACTED>" if k in _REDACTED_ENV_KEYS else v)
            for k, v in self.environment.items()
        }
        return (
            f"ContainerSpec(image={self.image!r}, command={self.command!r}, "
            f"environment={safe_env!r}, volumes={self.volumes!r}, "
            f"working_dir={self.working_dir!r}, labels={self.labels!r}, "
            f"name={self.name!r})"
        )

    __str__ = __repr__

    def model_dump(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Dump with sensitive env keys redacted.

        Pydantic's default model_dump bypasses __repr__, so without this
        override any operational logging that serializes the spec would leak
        NEXTSEEK_PASSWORD / AWS_BEARER_TOKEN_BEDROCK. Redaction applied here
        keeps the serialization contract aligned with __repr__.
        """
        data = super().model_dump(*args, **kwargs)
        env = data.get("environment")
        if isinstance(env, dict):
            data["environment"] = {
                k: ("<REDACTED>" if k in _REDACTED_ENV_KEYS else v)
                for k, v in env.items()
            }
        return data

    def model_dump_json(self, *args: Any, **kwargs: Any) -> str:
        """JSON dump with sensitive env keys redacted (see model_dump)."""
        return json.dumps(self.model_dump())


class BridgeAttachSocket:
    """Thin wrapper over docker-py's hijacked attach socket.

    Demuxes Docker's stdcopy 8-byte-header framing so callers read plain
    stdout/stderr payload bytes, and exposes stdin-side helpers the bridge
    needs for relaying WebSocket messages.
    """

    def __init__(
        self,
        raw_socket: Any,
        *,
        stdout_stream: Any | None = None,
        stderr_sink: BinaryIO | None = None,
    ) -> None:
        self._raw = raw_socket
        self._stdout_stream = stdout_stream
        self._line_buffer: bytearray = bytearray()
        # Phase 7 residual #1 visibility (2026-05-18): when set, every stderr
        # frame's payload is written verbatim (untruncated) to this sink so the
        # in-container runner's debug output — including chat_nextseek's
        # `[DEBUG][PARSER] Exception or parse error: <repr>` — survives even
        # when uvicorn is at --log-level error. Sink is opt-in; default None
        # preserves prior behavior (DEBUG-log only).
        self.stderr_sink: BinaryIO | None = stderr_sink

    # ------------------------------------------------------------------ read
    def read_frame(self) -> tuple[str, bytes] | None:
        """Read one Docker stdcopy frame.

        Returns ("stdout", payload), ("stderr", payload), or None on EOF.
        """
        if self._stdout_stream is not None:
            return self._read_log_chunk()
        header = self._recv_exact(8)
        if header is None:
            return None
        stream_id = header[0]
        size = struct.unpack(">I", header[4:8])[0]
        if size == 0:
            payload = b""
        else:
            payload = self._recv_exact(size) or b""
        stream_name = "stdout" if stream_id == 1 else "stderr"
        return stream_name, payload

    # ----------------------------------------------------------------- write
    def send_stdin(self, data: bytes) -> None:
        self._transport().sendall(data)

    def close_stdin(self) -> None:
        # 1 == SHUT_WR; avoid importing socket just for the constant so tests
        # can drop in a minimal fake.
        self._transport().shutdown(1)

    def close(self) -> None:
        if self._stdout_stream is not None and hasattr(self._stdout_stream, "close"):
            self._stdout_stream.close()
        self._raw.close()

    # ---------------------------------------------------------------- helper
    def _read_log_chunk(self) -> tuple[str, bytes] | None:
        try:
            payload = next(self._stdout_stream)
        except StopIteration:
            return None
        if not isinstance(payload, bytes):
            payload = bytes(payload)
        return "stdout", payload

    def _recv_exact(self, size: int) -> bytes | None:
        buf = bytearray()
        while len(buf) < size:
            chunk = self._read_chunk(size - len(buf))
            if not chunk:
                # Partial-frame EOF: return None so callers treat it as a clean
                # end-of-stream. Returning a short buffer would crash read_frame's
                # struct.unpack when the 8-byte header is truncated.
                return None
            buf.extend(chunk)
        return bytes(buf)

    def _read_chunk(self, size: int) -> bytes:
        if hasattr(self._raw, "read"):
            return self._raw.read(size)
        return self._transport().recv(size)

    def _transport(self) -> Any:
        """Return the recv/send/shutdown-capable socket-like transport.

        docker-py often returns a ``SocketIO`` wrapper from ``attach_socket``.
        That wrapper exposes the real socket under ``._sock``; read/write
        operations that mutate the stream target the underlying transport, while
        reads go through the file-like wrapper when it exists so docker-py can
        manage any buffering correctly.
        """
        return getattr(self._raw, "_sock", self._raw)

    def read_event_line(self) -> str | None:
        """Read one UTF-8 line of stdout output, demuxing frames internally.

        Consumes :meth:`read_frame` in a loop until either a full newline-terminated
        line is available in the internal buffer, or EOF arrives. Handles five
        edge cases per the locked LLM-router design spec:

        - Multi-line stdout payload: one frame yields several lines.
        - Line continuation: a line spans multiple frames.
        - Interleaved stderr: ``stderr`` frames are DEBUG-logged but NOT routed
          as lines.
        - EOF with residual: if EOF arrives mid-line, the partial line is
          returned on the final call. The next call returns ``None``.
        - Zero-length stdout frame mid-stream: Docker emits ``("stdout", b"")``
          as a keep-alive / heartbeat. Treated as a no-op continue.

        Returns ``None`` only when ``read_frame`` returns ``None`` (EOF) AND
        the internal buffer is empty.
        """
        while True:
            newline_idx = self._line_buffer.find(b"\n")
            if newline_idx >= 0:
                line_bytes = bytes(self._line_buffer[:newline_idx])
                del self._line_buffer[: newline_idx + 1]
                return line_bytes.decode("utf-8", errors="replace")

            frame = self.read_frame()
            if frame is None:
                if self._line_buffer:
                    residual = bytes(self._line_buffer)
                    self._line_buffer.clear()
                    return residual.decode("utf-8", errors="replace")
                return None

            stream_name, payload = frame
            if stream_name == "stderr":
                # Phase 7 residual #1 visibility (2026-05-18):
                # 1) Untruncated copy to optional file sink for post-mortem.
                # 2) INFO-level log (was DEBUG; --log-level error silenced it).
                # 3) Truncate log to 512 bytes; the file sink keeps the full
                #    payload so operators can grep for the real exception text.
                if self.stderr_sink is not None:
                    try:
                        self.stderr_sink.write(bytes(payload))
                        self.stderr_sink.flush()
                    except (OSError, ValueError):
                        # ValueError covers writes to a closed file; OSError
                        # covers disk-full / permission. Diagnostic-only sink
                        # must never break the bridge — drop and continue.
                        log.warning(
                            "bridge stderr sink write failed; dropping payload"
                        )
                log.info(
                    "bridge stderr frame (truncated to 512B): %r",
                    bytes(payload[:512]),
                )
                continue

            if not payload:
                continue

            self._line_buffer.extend(payload)


# -------------------------------------------------------------------- helpers


def _validate_user_id(user_id: str) -> None:
    if not isinstance(user_id, str) or not _USER_ID_RE.fullmatch(user_id):
        raise ValueError(f"invalid user_id: {user_id!r}")


def _build_volumes(
    identity: AuthenticatedIdentity, config: BridgeConfig
) -> dict[str, dict[str, str]]:
    volumes: dict[str, dict[str, str]] = {}
    for project in identity.projects:
        host = str(config.dropbox_root / project)
        volumes[host] = {
            "bind": f"{_CONTAINER_PROJECTS_PREFIX}/{project}",
            "mode": "ro",
        }
    volumes[str(config.scratch_root / identity.user_id)] = {
        "bind": _CONTAINER_SCRATCH,
        "mode": "rw",
    }
    volumes[str(config.claude_users_root / identity.user_id / ".claude")] = {
        "bind": _CONTAINER_CLAUDE_HOME,
        "mode": "rw",
    }
    volumes[str(config.output_root / identity.user_id)] = {
        "bind": _CONTAINER_OUTPUT,
        "mode": "ro",
    }
    # B17c: host-mounted agent model catalog (read-only). The container path
    # is fixed; the host path is configurable via DMAC_CATALOG_FILE_HOST_PATH.
    volumes[str(config.catalog_file)] = {
        "bind": _CONTAINER_CATALOG_FILE,
        "mode": "ro",
    }
    return volumes


def _build_environment(
    identity: AuthenticatedIdentity,
    bridge_env: Mapping[str, str],
    *,
    runtime_mode: str | None = None,
) -> dict[str, str]:
    env: dict[str, str] = {
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "NEXTSEEK_USERNAME": identity.user_id,
        "NEXTSEEK_PASSWORD": identity.password.get_secret_value(),
    }
    if "AWS_REGION" in bridge_env:
        env["AWS_REGION"] = bridge_env["AWS_REGION"]
    if "AWS_BEARER_TOKEN_BEDROCK" in bridge_env:
        env["AWS_BEARER_TOKEN_BEDROCK"] = bridge_env["AWS_BEARER_TOKEN_BEDROCK"]
    if "NEXTSEEK_URL" in bridge_env:
        env["NEXTSEEK_URL"] = bridge_env["NEXTSEEK_URL"]
    for forwarded_key in (
        "GCP_API_KEY",
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASSWORD",
        "DMAC_PATH_MAPPINGS",
        "MYSQL_HOST_DEV",
        "MYSQL_PORT",
        "MYSQL_USER",
        "MYSQL_DEV_PASSWORD",
        "SESSION_DB_TYPE",
        "SESSION_DB_HOST",
        "SESSION_DB_PORT",
        "SESSION_DB_USER",
        "SESSION_DB_PASSWORD",
        "SESSION_DB_NAME",
        "SESSION_DB_PATH",
        "NEO4J_DATABASE",
    ):
        if forwarded_key in bridge_env:
            env[forwarded_key] = bridge_env[forwarded_key]
    # B17c: catalog file is always mounted; CATALOG_FILE points at the bind.
    env["CATALOG_FILE"] = _CONTAINER_CATALOG_FILE
    if runtime_mode is not None:
        env["DMAC_RUNTIME_MODE"] = runtime_mode
    return env


def _build_command(session_id: str | None) -> list[str]:
    command = list(_BASE_COMMAND)
    if session_id:
        # Claude 2.1.92: `--resume <uuid>` takes the session id as its value.
        # `--session-id` is a separate flag (for pre-assigning a UUID to a
        # NEW session) and cannot be combined with --resume unless
        # --fork-session is also set; combining them emits
        # "--session-id can only be used with --continue or --resume if
        # --fork-session is also specified" and exits nonzero.
        command.extend(["--resume", session_id])
    return command


# --------------------------------------------------------------------- public


def build_container_spec(
    identity: AuthenticatedIdentity,
    config: BridgeConfig,
    *,
    image: str,
    session_id: str | None,
    bridge_env: Mapping[str, str],
    runtime_mode: str | None = None,
) -> ContainerSpec:
    """Assemble the frozen ContainerSpec that `start_container` will launch."""
    _validate_user_id(identity.user_id)
    return ContainerSpec(
        image=image,
        command=_build_command(session_id),
        environment=_build_environment(
            identity, bridge_env, runtime_mode=runtime_mode
        ),
        volumes=_build_volumes(identity, config),
        working_dir=_CONTAINER_WORKING_DIR,
        labels={
            "dmac.bridge": "1",
            "dmac.user_id": identity.user_id,
        },
    )


def start_container(
    identity: AuthenticatedIdentity,
    *,
    image: str,
    session_id: str | None,
    bridge_env: Mapping[str, str],
    config: BridgeConfig,
    client: Any | None = None,
    runtime_mode: str | None = None,
    command_override: list[str] | None = None,
) -> Container:
    """Build a spec and launch the container detached."""
    spec = build_container_spec(
        identity,
        config,
        image=image,
        session_id=session_id,
        bridge_env=bridge_env,
        runtime_mode=runtime_mode,
    )
    client = client or docker.from_env()
    run_kwargs: dict[str, Any] = {
        "image": spec.image,
        "command": command_override if command_override is not None else spec.command,
        "environment": spec.environment,
        "volumes": spec.volumes,
        "working_dir": spec.working_dir,
        "labels": spec.labels,
        "platform": "linux/amd64",
        "detach": True,
        "stdin_open": True,
        "tty": False,
        "stdout": True,
        "stderr": True,
    }
    if spec.name is not None:
        run_kwargs["name"] = spec.name
    return client.containers.run(**run_kwargs)


def attach(container: Container) -> BridgeAttachSocket:
    """Open stdin via attach_socket and stdout via the working logs stream."""
    raw = container.attach_socket(
        params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1}
    )
    stdout_stream = container.logs(
        stream=True,
        follow=True,
        stdout=True,
        stderr=False,
    )
    return BridgeAttachSocket(raw, stdout_stream=stdout_stream)


def stop_and_remove(container: Container, *, timeout: int = 5) -> None:
    """Best-effort stop + remove.

    `remove(force=True)` MUST run even if `stop()` fails. `claude` in
    `--input-format stream-json` mode blocks on stdin, which means SIGTERM
    frequently times out and docker-py raises an APIError — if that short-
    circuits the remove call, the container is left alive and the next
    resume reconnect sees a stale sibling under the same label.
    `remove(force=True)` sends SIGKILL unconditionally and is the real
    teardown primitive.
    """
    try:
        container.stop(timeout=timeout)
    except NotFound:
        pass
    except APIError as exc:
        # Log only the exception type (R-03: APIError body may echo env).
        log.warning("container.stop failed: %s", type(exc).__name__)
    try:
        container.remove(force=True)
    except NotFound:
        pass
    except APIError as exc:
        log.error("container.remove failed: %s", type(exc).__name__)
        raise


# --------------------------------------------------------- async wrappers (T06)


async def async_start_container(
    identity: AuthenticatedIdentity,
    *,
    image: str,
    session_id: str | None,
    bridge_env: Mapping[str, str],
    config: BridgeConfig,
    client: Any | None = None,
    runtime_mode: str | None = None,
    command_override: list[str] | None = None,
) -> Container:
    return await asyncio.to_thread(
        start_container,
        identity,
        image=image,
        session_id=session_id,
        bridge_env=bridge_env,
        config=config,
        client=client,
        runtime_mode=runtime_mode,
        command_override=command_override,
    )


async def async_attach(container: Container) -> BridgeAttachSocket:
    return await asyncio.to_thread(attach, container)


async def async_stop_and_remove(container: Container, *, timeout: int = 5) -> None:
    await asyncio.to_thread(stop_and_remove, container, timeout=timeout)


# ---------------------------------------------- T3.1: per-turn exec primitives


def _build_exec_environment(
    identity: AuthenticatedIdentity,
    bridge_env: Mapping[str, str],
    *,
    route: str,
    ns_session_id: str | None = None,
) -> dict[str, str]:
    """Assemble the per-exec environment per locked spec L124-126."""
    env = _build_environment(identity, bridge_env)
    env["API_USER"] = identity.user_id
    env["API_PASS"] = identity.password.get_secret_value()
    env["NEXTSEEK_BASE_URL"] = bridge_env.get("NEXTSEEK_BASE_URL", "")
    if route == "ns":
        if ns_session_id is None:
            # Defensive guard for direct helper misuse. Public exec_ns_turn()
            # requires session_id: str, so this branch is unreachable there.
            raise ValueError(  # pragma: no cover
                "ns_session_id required for route='ns'",
            )
        env["OUTPUTS_DIR"] = (
            f"/data/scratch/{identity.user_id}/chat_nextseek/{ns_session_id}/"
        )
        env["CHAT_NEXTSEEK_SESSION_DB"] = (
            "/home/user/.claude/chat_nextseek/sessions.sqlite"
        )
        env["NEXTSEEK_MODE"] = "gcp"
    return env


def exec_cc_turn(
    container: Container,
    *,
    query: str,
    model_id: str,
    session_id: str | None,
    identity: AuthenticatedIdentity,
    config: BridgeConfig,
    bridge_env: Mapping[str, str],
    client: Any | None = None,
) -> BridgeAttachSocket:
    """Run one Claude Code turn against the idle container via docker exec."""
    api_client = client or container.client
    cmd: list[str] = [
        "claude",
        "--print",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--dangerously-skip-permissions",
        "--model",
        model_id,
    ]
    if session_id:
        cmd.extend(["--resume", session_id])
    environment = _build_exec_environment(identity, bridge_env, route="cc")
    del config
    exec_info = api_client.api.exec_create(
        container.id,
        cmd=cmd,
        stdin=True,
        stdout=True,
        stderr=True,
        tty=False,
        environment=environment,
    )
    exec_id = exec_info["Id"] if isinstance(exec_info, dict) else exec_info
    raw_socket = api_client.api.exec_start(exec_id, socket=True)
    sock = BridgeAttachSocket(raw_socket)
    sock._exec_id = exec_id  # type: ignore[attr-defined]
    envelope = json.dumps(
        {"type": "user", "message": {"role": "user", "content": query}},
        separators=(",", ":"),
    )
    sock.send_stdin((envelope + "\n").encode("utf-8"))
    sock.close_stdin()
    return sock


def exec_ns_turn(
    container: Container,
    *,
    query: str,
    session_id: str,
    identity: AuthenticatedIdentity,
    config: BridgeConfig,
    bridge_env: Mapping[str, str],
    client: Any | None = None,
) -> BridgeAttachSocket:
    """Run one NExtSEEK turn against the idle container via docker exec."""
    api_client = client or container.client
    cmd: list[str] = ["python", "/opt/dmac/runner_ns.py", "--session", session_id]
    environment = _build_exec_environment(
        identity,
        bridge_env,
        route="ns",
        ns_session_id=session_id,
    )
    del config
    exec_info = api_client.api.exec_create(
        container.id,
        cmd=cmd,
        stdin=True,
        stdout=True,
        stderr=True,
        tty=False,
        environment=environment,
    )
    exec_id = exec_info["Id"] if isinstance(exec_info, dict) else exec_info
    raw_socket = api_client.api.exec_start(exec_id, socket=True)
    sock = BridgeAttachSocket(raw_socket)
    sock._exec_id = exec_id  # type: ignore[attr-defined]
    sock.send_stdin((query + "\n").encode("utf-8"))
    sock.close_stdin()
    return sock


def kill_exec_pid(
    container: Container,
    exec_id: str,
    *,
    client: Any | None = None,
) -> None:
    """SIGKILL a running docker exec process while leaving the container alive."""
    api_client = client or container.client
    try:
        info = api_client.api.exec_inspect(exec_id)
    except APIError as exc:
        log.warning("kill_exec_pid: exec_inspect failed: %s", type(exc).__name__)
        return
    pid = info.get("Pid", 0) if isinstance(info, dict) else 0
    if not pid:
        return
    try:
        kill_info = api_client.api.exec_create(
            container.id,
            cmd=["kill", "-9", str(pid)],
            stdin=False,
            stdout=False,
            stderr=False,
            tty=False,
        )
        kill_id = kill_info["Id"] if isinstance(kill_info, dict) else kill_info
        api_client.api.exec_start(kill_id, detach=True)
    except APIError as exc:
        log.warning("kill_exec_pid: kill exec failed: %s", type(exc).__name__)

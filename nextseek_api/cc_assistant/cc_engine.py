"""Container Claude Code (CC) engine — sandboxed agentic route, per dmac SDS.

Runs ONE headless ``claude`` container per query with the dmac_assistant data
model (ADR-003 / SDS §5.3-5.5):

* project data mounted READ-ONLY at ``/data/projects/<project>`` (scoped per user),
* a per-user RW scratch at ``/data/scratch`` (working dir is the per-run subdir),
* after the turn, a host-side copier publishes new scratch files to the user's
  output dir (``output_root/<user_id>/<run_id>/...`` — the Dropbox
  ``example-project/demo/`` folder for the dev demo user).

CC never writes the output dir directly; the curated post-turn copier
(``dmac_assistant.copier`` + ``run_tracker``, imported, not reimplemented) does.
The terminal ``query_complete`` is deferred until after the copier so the reply
can report the host-side artifact paths (D19).

Topology note: this runs in the nextseek Django container; the CC container is a
sibling spawned via the host docker socket, so bind *sources* are host paths
(``CCPaths.host_*``) while the copier reads/writes the same dirs at their
nextseek-container mount points (``CCPaths.scratch_mount`` / ``output_mount``).
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from .attach import BridgeAttachSocket
from .translate import CCStreamTranslator
from .cc_config import CCPaths

logger = logging.getLogger(__name__)

SendEvent = Callable[[str, dict[str, Any]], None]

DEFAULT_IMAGE = os.environ.get("NEXTSEEK_CC_IMAGE", "dmac-assistant:poc")

# The CC sibling container joins this existing compose network so the forwarded
# NExtSEEK topology resolves by service name (NEO4J_URI=neo4j://neo4j,
# MYSQL_HOST=db, the nextseek REST service at nextseek:8000). MySQL is NOT
# host-published, so host.docker.internal cannot reach it — network attach is
# the correct (and simpler) approach. Override via NEXTSEEK_CC_NETWORK.
DEFAULT_NETWORK = os.environ.get("NEXTSEEK_CC_NETWORK", "nextseek_default")

_BEDROCK_KEYS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "AWS_BEARER_TOKEN_BEDROCK",
    "AWS_REGION",
    "AWS_DEFAULT_REGION",
    "ANTHROPIC_API_KEY",
)

_BASE_CMD = [
    "claude", "--print",
    "--input-format", "stream-json",
    "--output-format", "stream-json",
    "--verbose", "--dangerously-skip-permissions",
]

_CONTAINER_SCRATCH = "/data/scratch"
_CONTAINER_OUTPUT = "/data/output"
_CONTAINER_PROJECTS = "/data/projects"
# Image WORKDIR: the baked CLAUDE.md (-> /app/CLAUDE.md) and the nextseek plugin
# (~/.claude/plugins/local/nextseek) are discovered by claude-code only when cwd
# is here. Running in /data/scratch leaves the agent with no plugin guidance, so
# it never invokes nextseek-query. The agent writes artifacts to /data/scratch
# per the container CLAUDE.md.
_CONTAINER_WORKDIR = "/home/user"


def cc_runner_available() -> tuple[bool, str]:
    """Return (ok, detail). ok=False means the CC route cannot run right now."""
    try:
        import docker
        client = docker.from_env()
        client.ping()
    except Exception as exc:  # noqa: BLE001
        return False, f"docker daemon unreachable: {type(exc).__name__}"
    try:
        client.images.get(DEFAULT_IMAGE)
    except Exception:
        return False, (
            f"CC image '{DEFAULT_IMAGE}' not found "
            "(build it via dmac's `make image-build`, or set NEXTSEEK_CC_IMAGE)"
        )
    return True, "ok"


def _bedrock_environment() -> dict[str, str]:
    env = {k: os.environ[k] for k in _BEDROCK_KEYS if os.environ.get(k)}
    if env.get("AWS_BEARER_TOKEN_BEDROCK") and not (
        env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION")
    ):
        env["AWS_REGION"] = "us-east-1"
    return env


# chat_nextseek / nextseek-plugin credential + topology contract (the env vars
# ChatConfig reads at construction). Forwarded verbatim from the Django
# container into the sibling CC container; service-name hosts (neo4j, db)
# resolve over the shared compose network (see DEFAULT_NETWORK).
_NEXTSEEK_PASSTHROUGH_KEYS = (
    "API_USER",
    "API_PASS",
    "NEO4J_URI",
    "NEO4J_USER",
    "NEO4J_PASSWORD",
    "NEO4J_DATABASE",
    "MYSQL_HOST",
    "MYSQL_HOST_DEV",
    "MYSQL_PORT",
    "MYSQL_USER",
    "MYSQL_PASSWORD",
    "MYSQL_DEV_PASSWORD",
    "GCP_API_KEY",
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "0.0.0.0"})
_NEXTSEEK_SERVICE_HOST = "nextseek"


def _rewrite_loopback_url(url: str, service: str = _NEXTSEEK_SERVICE_HOST) -> str:
    """Rewrite a loopback host in ``url`` to a compose service name.

    The Django container reaches the NExtSEEK REST API at http://127.0.0.1:8000;
    a sibling CC container on the shared network must reach http://nextseek:8000
    instead. Non-loopback hosts (already service names like ``neo4j``) are
    returned unchanged, preserving scheme, port, and path.
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if parts.hostname in _LOOPBACK_HOSTS:
        netloc = f"{service}:{parts.port}" if parts.port else service
        parts = parts._replace(netloc=netloc)
        return urlunsplit(parts)
    return url


def _nextseek_environment(
    source: Mapping[str, str] | None = None,
    *,
    api_user: str | None = None,
    api_pass: str | None = None,
) -> dict[str, str]:
    """Build the NExtSEEK / chat_nextseek env for the CC container.

    Forwards the topology vars the in-container nextseek plugin and chat_nextseek
    read, from the Django container's env into the sibling CC container. NEO4J/
    MySQL hosts are compose service names that resolve verbatim on the shared
    network; only the NExtSEEK REST base URL (loopback in the Django env) is
    rewritten to the ``nextseek`` service.

    The NExtSEEK login (``api_user``/``api_pass``) is resolved per-request from
    the authenticated user, NOT from env (API_USER/API_PASS are unset in the
    Django container), so it is injected explicitly — under both API_USER/API_PASS
    (what ChatConfig reads) and NEXTSEEK_USERNAME/PASSWORD (what the poc image
    entrypoint maps to API_USER/API_PASS).
    """
    src = os.environ if source is None else source
    env: dict[str, str] = {k: src[k] for k in _NEXTSEEK_PASSTHROUGH_KEYS if src.get(k)}

    if api_user:
        env["API_USER"] = api_user
        env["NEXTSEEK_USERNAME"] = api_user
    if api_pass:
        env["API_PASS"] = api_pass
        env["NEXTSEEK_PASSWORD"] = api_pass

    base = src.get("NEXTSEEK_BASE_URL") or src.get("NEXTSEEK_URL")
    if base:
        rewritten = _rewrite_loopback_url(base)
        # entrypoint falls back NEXTSEEK_BASE_URL <- NEXTSEEK_URL; set both.
        env["NEXTSEEK_BASE_URL"] = rewritten
        env["NEXTSEEK_URL"] = rewritten

    # chat_nextseek internal LLM profile (gcp -> Gemini). Set explicitly so it is
    # deterministic regardless of the image's entrypoint default.
    env["NEXTSEEK_MODE"] = src.get("NEXTSEEK_MODE", "gcp")
    return env


def _run_kwargs(
    *,
    image: str,
    command: list[str],
    environment: dict[str, str],
    volumes: dict[str, dict[str, str]] | None,
    run_id: str,
    user_id: str,
    network: str = DEFAULT_NETWORK,
    workdir: str = _CONTAINER_WORKDIR,
) -> dict[str, Any]:
    """Build the docker-py ``containers.run`` kwargs for one CC turn.

    The container joins ``network`` (the nextseek compose network) so the
    forwarded service-name hosts resolve, and runs in ``workdir`` (the image
    WORKDIR) so the baked CLAUDE.md + nextseek plugin guidance are discovered.
    """
    return {
        "image": image,
        "command": command,
        "environment": environment,
        "volumes": volumes or None,
        "working_dir": workdir,
        "network": network,
        "labels": {
            "nextseek.cc": "1",
            "nextseek.cc.user": user_id,
            "nextseek.cc.run": run_id,
        },
        "platform": "linux/amd64",
        "detach": True,
        "stdin_open": True,
        "tty": False,
        "stdout": True,
        "stderr": True,
    }


def _dropbox_display(host_path: Path, paths: CCPaths) -> str:
    """Render a host artifact path as a friendly Dropbox-relative location."""
    s = str(host_path)
    root = paths.host_dropbox_root.rstrip("/")
    if s.startswith(root + "/"):
        return s[len(root) + 1:]  # e.g. example-project/demo/<run_id>/file.py
    return s


def run_cc_turn(
    *,
    query: str,
    model_id: str | None,
    send_event: SendEvent,
    user_id: str,
    projects: list[str],
    run_id: str,
    paths: CCPaths,
    session_id: str | None = None,
    image: str | None = None,
    api_user: str | None = None,
    api_pass: str | None = None,
) -> None:
    """Execute one Container-CC turn with scoped Dropbox mounts + artifact publish.

    Always terminates with exactly one ``query_complete`` (reply augmented with
    published host paths) or ``query_error``.
    """
    import docker
    from docker.errors import APIError, NotFound

    image = image or DEFAULT_IMAGE
    scratch_mount = Path(paths.scratch_mount)
    output_mount = Path(paths.output_mount)

    # Per-run working dir lives under the user's scratch. Create it via the
    # nextseek-container mount so it exists on the host before the CC container
    # (which mounts the same host dir) starts.
    (scratch_mount / user_id / run_id).mkdir(parents=True, exist_ok=True)

    # Bind mounts for the CC sibling container (sources are HOST paths).
    volumes: dict[str, dict[str, str]] = {}
    for project in projects:
        volumes[f"{paths.host_dropbox_root}/{project}"] = {
            "bind": f"{_CONTAINER_PROJECTS}/{project}", "mode": "ro",
        }
    volumes[f"{paths.host_scratch_root}/{user_id}"] = {
        "bind": _CONTAINER_SCRATCH, "mode": "rw",
    }

    # D19: tell the in-container agent how to translate container paths to host
    # paths when it reports artifact locations to the user.
    path_mappings = {
        "output": {"container_root": _CONTAINER_OUTPUT,
                   "host_root": f"{paths.host_output_root}/{user_id}"},
        "scratch": {"container_root": _CONTAINER_SCRATCH,
                    "host_root": f"{paths.host_scratch_root}/{user_id}"},
    }
    environment = {
        **_bedrock_environment(),
        **_nextseek_environment(api_user=api_user, api_pass=api_pass),
    }
    environment["DMAC_PATH_MAPPINGS"] = json.dumps(path_mappings, separators=(",", ":"))

    command = list(_BASE_CMD)
    if model_id:
        command += ["--model", model_id]
    if session_id:
        command += ["--resume", session_id]

    before = snapshot_before(scratch_mount, user_id)

    translator = CCStreamTranslator()
    terminal: tuple[str, dict[str, Any]] | None = None
    client = docker.from_env()
    container = None
    try:
        container = client.containers.run(
            **_run_kwargs(
                image=image, command=command, environment=environment,
                volumes=volumes, run_id=run_id, user_id=user_id,
            )
        )
        raw = container.attach_socket(params={"stdin": 1, "stdout": 1, "stderr": 1, "stream": 1})
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
                if event in ("query_complete", "query_error"):
                    terminal = (event, data)   # defer until after copier
                else:
                    send_event(event, data)
            if translator._terminated:
                break
        if terminal is None:
            for event, data in translator.finalize():
                terminal = (event, data)

        # Post-turn publish: diff scratch, copy new files to the user's output dir.
        published = _publish_artifacts(scratch_mount, output_mount, user_id, before, paths)

        if terminal is None:
            terminal = ("query_complete", {"reply": "(no response)", "bundle_id": None,
                                           "session_id": translator.session_id})
        event, data = terminal
        if event == "query_complete" and published:
            listing = "\n".join(f"- `{p}`" for p in published)
            data = dict(data)
            data["reply"] = (data.get("reply") or "") + (
                f"\n\n---\n📁 **Saved to your Dropbox** "
                f"({len(published)} file(s)):\n{listing}"
            )
            data["artifacts_published"] = published
        send_event(event, data)

    except (APIError, NotFound) as exc:
        logger.exception("CC docker error")
        send_event("query_error", {"error": f"Container error: {type(exc).__name__}",
                                   "agent": "container_cc", "session_id": translator.session_id})
    except Exception as exc:  # noqa: BLE001
        logger.exception("CC turn failed")
        send_event("query_error", {"error": f"Container-CC turn failed: {type(exc).__name__}",
                                   "agent": "container_cc", "session_id": translator.session_id})
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


def snapshot_before(scratch_mount: Path, user_id: str) -> dict[str, tuple[int, int]]:
    from dmac_assistant.run_tracker import snapshot_scratch_files
    return snapshot_scratch_files(scratch_mount, user_id)


def _publish_artifacts(
    scratch_mount: Path, output_mount: Path, user_id: str,
    before: dict[str, tuple[int, int]], paths: CCPaths,
) -> list[str]:
    """Diff scratch, copy new/changed files to output, return Dropbox-relative paths."""
    try:
        from dmac_assistant.run_tracker import snapshot_scratch_files, diff_files
        from dmac_assistant.copier import copy_files
    except Exception as exc:  # noqa: BLE001
        logger.warning("CC: copier/run_tracker import failed (%s); no publish", type(exc).__name__)
        return []
    after = snapshot_scratch_files(scratch_mount, user_id)
    changed = diff_files(before, after)
    if not changed:
        return []
    written = copy_files(scratch_mount, output_mount, user_id, changed)
    display: list[str] = []
    for dst in written:
        try:
            rel = dst.relative_to(output_mount)         # <user_id>/<run_id>/<file>
        except ValueError:
            rel = Path(user_id) / dst.name
        host_path = Path(paths.host_output_root) / rel
        display.append(_dropbox_display(host_path, paths))
    return sorted(display)

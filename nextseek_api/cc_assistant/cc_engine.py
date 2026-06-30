"""Container Claude Code (CC) engine — sandboxed agentic route.

Runs ONE headless ``claude`` container per CC query:

* private user input mounted READ-ONLY at ``/data/input``,
* project shared input mounted READ-ONLY at ``/data/shared``,
* per-user RW scratch mounted at ``/data/scratch``,
* after the turn, a host-side copier publishes new scratch files to the user's
  nested output dir.

CC never writes the output dir directly; the bridge diffs scratch and publishes
regular non-symlink files after the container exits.
The terminal ``query_complete`` is deferred until after the copier so the reply
can report the host-side artifact paths (D19).

Topology note: this runs in the nextseek Django container; the CC container is a
sibling spawned via the host docker socket, so bind *sources* are host paths
while the bridge reads/writes the same dirs at ``CCPaths.user_root_mount``.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable

from .attach import BridgeAttachSocket
from .translate import CCStreamTranslator
from .cc_config import CCPaths
from nextseek_api.cc_assistant import cc_session

logger = logging.getLogger(__name__)

SendEvent = Callable[[str, dict[str, Any]], None]

DEFAULT_IMAGE = os.environ.get("NEXTSEEK_CC_IMAGE", "dmac-assistant:poc")

# OI-3 / audit A1: the CC sibling joins a DEDICATED 2/3-node network
# (agent + bedrock-proxy + the nginx entrypoint) — NOT the shared
# ``nextseek_default`` where neo4j/mysql/seek/solr live. Network segmentation is
# the real containment control: the de-credentialed agent must not even have L3
# reach to ``neo4j:7687`` / ``db:3306`` (whose password is the committed default
# ``demopassword``). The agent reaches only (a) the Bedrock auth-proxy and (b)
# the NExtSEEK REST API via nginx — both attached to this net. Override via
# NEXTSEEK_CC_NETWORK; a missing network is fail-fast (see run_cc_turn).
DEFAULT_NETWORK = os.environ.get("NEXTSEEK_CC_NETWORK", "dmac-cc-net")

# OI-3 (T4): the agent reaches Bedrock ONLY through the auth-proxy sidecar, which
# holds the institutional AWS_BEARER_TOKEN_BEDROCK and adds the Authorization
# header server-side. The agent emits UNSIGNED requests
# (CLAUDE_CODE_SKIP_BEDROCK_AUTH=1) to this URL and carries ZERO AWS creds.
_DEFAULT_BEDROCK_PROXY_URL = os.environ.get(
    "DMAC_BEDROCK_PROXY_URL", "http://bedrock-proxy:8080"
)

# Per-turn cost + time bounds. A hard USD spend cap via ``claude
# --max-budget-usd`` (Claude Code stops the turn when cost hits it; 0 disables),
# a turn cap via ``--max-turns``, and a wall-clock timeout (hard-capped 180s)
# that stops + force-removes the container if the turn overruns. All overridable.
_DEFAULT_MAX_BUDGET_USD = float(os.environ.get("NEXTSEEK_CC_MAX_BUDGET_USD", "2.00"))
_DEFAULT_MAX_TURNS = os.environ.get("NEXTSEEK_CC_MAX_TURNS", "50")
_TIMEOUT_HARD_MAX = 180  # seconds; project rule (run_headless._TIMEOUT_HARD_MAX)
_DEFAULT_TURN_TIMEOUT = min(
    int(os.environ.get("NEXTSEEK_CC_TIMEOUT_SECONDS", str(_TIMEOUT_HARD_MAX))),
    _TIMEOUT_HARD_MAX,
)

# I-4 (audit B2): user_id / project flow into bind-mount SOURCES, so they must be
# validated before any path interpolation or a ``..`` user_id is a host-dir escape.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9._@+-]{1,64}$")

# I-14: keys whose values must never reach a log line. After OI-3 the agent env
# holds no AWS/backend creds; the per-request NExtSEEK password is the remaining
# secret. DMAC_PATH_MAPPINGS encodes host layout (not a credential, still redact).
_REDACTED_ENV_KEYS = frozenset({
    "NEXTSEEK_PASSWORD", "API_PASS", "DMAC_PATH_MAPPINGS",
    # belt-and-suspenders: these must NEVER be in the agent env, but redact if seen.
    "AWS_BEARER_TOKEN_BEDROCK", "NEO4J_PASSWORD", "MYSQL_PASSWORD",
    "MYSQL_DEV_PASSWORD", "GCP_API_KEY", "ANTHROPIC_API_KEY",
})

# I-10 (audit checklist 2): auto mode with a classifier gating each tool call —
# NOT ``--dangerously-skip-permissions``. Model + caps + $defaults-first
# allowlist are appended in _build_command.
_BASE_CMD = [
    "claude", "--print",
    "--input-format", "stream-json",
    "--output-format", "stream-json",
    "--verbose",
    "--permission-mode", "auto",
]

_CONTAINER_SCRATCH = "/data/scratch"
_CONTAINER_OUTPUT = "/data/output"
_CONTAINER_INPUT = "/data/input"
_CONTAINER_SHARED = "/data/shared"
# Image WORKDIR: the baked CLAUDE.md (-> /app/CLAUDE.md) and the nextseek plugin
# (~/.claude/plugins/local/nextseek) are discovered by claude-code only when cwd
# is here. Running in /data/scratch leaves the agent with no plugin guidance, so
# it never invokes nextseek-query. The agent writes artifacts to /data/scratch
# per the container CLAUDE.md.
_CONTAINER_WORKDIR = "/home/user"
# The agent's HOME .claude (session transcripts + config). Mounted per chat
# session so --resume finds the transcript across the ephemeral per-turn
# containers (Step 1b). HOME is the image default /home/user (no override).
_CONTAINER_CLAUDE_HOME = _CONTAINER_WORKDIR + "/.claude"
# Step 1c: user-tier rolling memory rendered host-side, RO-bound as a NESTED file
# over 1b's per-session .claude RW mount (live-verified MERGE with the baked
# project /home/user/CLAUDE.md — see evidence/1c-claude-md-merge-probe.md).
_CONTAINER_USER_MEMORY = _CONTAINER_CLAUDE_HOME + "/CLAUDE.md"
# Step 1c: the 10 most-recent OTHER sessions' raw transcripts, RO, for on-demand
# depth. Outside .claude so it never collides with the session store / resume.
_CONTAINER_MEMORY_TRANSCRIPTS = _CONTAINER_WORKDIR + "/.cc-memory/transcripts"


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
    # I-17 / audit A1: the bridge NEVER creates the network — a missing one is a
    # deployment error. Gating here keeps the de-credentialed agent off the shared
    # net (the segmented net + bedrock-proxy must be up first).
    try:
        client.networks.get(DEFAULT_NETWORK)
    except Exception:
        return False, (
            f"CC network '{DEFAULT_NETWORK}' not found — bring up the segmented "
            "network + bedrock-proxy sidecar first (NEXTSEEK_CC_NETWORK)."
        )
    return True, "ok"


def _validate_user_id(user_id: str) -> None:
    """I-4 (audit B2): reject anything that could escape the per-user mount root.

    ``user_id`` becomes a single path segment of the scratch bind SOURCE
    (``scratch_root/<user_id>``). A value of ``..`` / ``../x`` / ``a/b`` would
    mount an arbitrary host dir rw into the agent. Allow Django username chars
    but never ``.``/``..`` as the whole id and never a path separator.
    """
    if (
        not isinstance(user_id, str)
        or not _USER_ID_RE.fullmatch(user_id)
        or user_id in (".", "..")
        or "/" in user_id
    ):
        raise ValueError(f"invalid user_id: {user_id!r}")


def _validate_project(name: str) -> None:
    """Reject project dirname values that could traverse out of the user root.

    Project dirname values are built from SEEK project ids + slugs (or a
    personal namespace), so this is a traversal guard rather than a strict slug
    validator.
    """
    if (
        not isinstance(name, str)
        or not name
        or len(name) > 128
        or "/" in name
        or "\x00" in name
        or name in (".", "..")
    ):
        raise ValueError(f"invalid project name: {name!r}")


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "0.0.0.0"})
# Route the CC container's NExtSEEK REST calls through nginx, not daphne-direct.
# daphne-direct (nextseek:8000) sends Host: nextseek, which Django's restrictive
# ALLOWED_HOSTS rejects with HTTP 400; nginx (nextseek_nginx:80) forces a
# Django-safe upstream Host (proxy_set_header Host localhost) so it returns 200.
_NEXTSEEK_SERVICE_HOST = "nextseek_nginx"


def _rewrite_loopback_url(url: str, service: str = _NEXTSEEK_SERVICE_HOST) -> str:
    """Rewrite a loopback host in ``url`` to the in-network nginx entrypoint.

    The Django container reaches the NExtSEEK REST API at http://127.0.0.1:8000
    (itself, daphne). A sibling CC container on the shared network must instead
    go through nginx (``nextseek_nginx`` on :80), which normalizes the upstream
    Host so Django's ALLOWED_HOSTS accepts it. Non-loopback hosts are returned
    unchanged, preserving scheme, port, and path.
    """
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if parts.hostname in _LOOPBACK_HOSTS:
        # nginx listens on :80; drop the daphne :8000 from the loopback URL.
        parts = parts._replace(netloc=service)
        return urlunsplit(parts)
    return url


def build_agent_environment(
    *,
    source: Mapping[str, str] | None = None,
    api_user: str | None,
    api_pass: str | None,
    path_mappings: Mapping[str, Any],
) -> dict[str, str]:
    """The COMPLETE env for the sandboxed Container-CC agent (OI-3).

    SINGLE source of truth for the agent env — ``run_cc_turn`` and the
    containment canary both call this, so a secret can never sneak in via a
    separate inline dict (audit B3). The agent holds ZERO AWS creds and NONE of
    the 16 shared backend credentials (NEO4J_* / MYSQL_* / GCP_API_KEY): it
    reaches Bedrock only through the auth-proxy, and NExtSEEK data only through
    the authenticated REST API as the user. ``source`` is the Django/process env
    to read non-secret topology from (defaults to os.environ; the canary passes a
    hostile source to prove nothing leaks).
    """
    src = os.environ if source is None else source
    env: dict[str, str] = {
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "ANTHROPIC_BEDROCK_BASE_URL": src.get(
            "DMAC_BEDROCK_PROXY_URL", _DEFAULT_BEDROCK_PROXY_URL
        ),
        "CLAUDE_CODE_SKIP_BEDROCK_AUTH": "1",
        "CLAUDE_CODE_ENABLE_AUTO_MODE": "1",
    }
    # I-9: the agent acts as the USER's OWN login, injected per-request (never a
    # shared env secret). Entrypoint maps NEXTSEEK_* -> API_USER/API_PASS.
    if api_user:
        env["NEXTSEEK_USERNAME"] = api_user
        env["API_USER"] = api_user
    if api_pass:
        env["NEXTSEEK_PASSWORD"] = api_pass
        env["API_PASS"] = api_pass
    # Non-secret topology the agent legitimately needs.
    region = src.get("AWS_REGION") or src.get("AWS_DEFAULT_REGION")
    if region:
        env["AWS_REGION"] = region
    base = src.get("NEXTSEEK_BASE_URL") or src.get("NEXTSEEK_URL")
    if base:
        rewritten = _rewrite_loopback_url(base)
        env["NEXTSEEK_BASE_URL"] = rewritten
        env["NEXTSEEK_URL"] = rewritten
    # D19: container->host path translation for artifact-location reporting.
    env["DMAC_PATH_MAPPINGS"] = json.dumps(path_mappings, separators=(",", ":"))
    return env


def _redact_env(env: Mapping[str, str]) -> dict[str, str]:
    """I-14: redact secret-bearing keys for any operational logging."""
    return {
        k: ("<REDACTED>" if k in _REDACTED_ENV_KEYS else v) for k, v in env.items()
    }


def _cc_limit_args(max_budget_usd: float) -> list[str]:
    """OI-5 per-turn caps: turn count + hard USD budget (both exit-with-error)."""
    args = ["--max-turns", _DEFAULT_MAX_TURNS]
    if max_budget_usd and max_budget_usd > 0:
        args += ["--max-budget-usd", str(max_budget_usd)]
    return args


def _automode_settings_args(source: Mapping[str, str] | None = None) -> list[str]:
    """OI-5 / audit D2: ``$defaults``-FIRST auto-mode allowlist describing trusted
    lab infra so the classifier doesn't abort the agent's legitimate NS-API calls.

    Built from the DJANGO/source env (NOT the stripped agent env) and carries only
    DESCRIPTORS (URLs/URIs are topology) — never a secret VALUE. The list MUST
    start with the literal ``$defaults`` so built-in trust is EXTENDED, not replaced.
    """
    src = os.environ if source is None else source
    environment: list[str] = ["$defaults"]
    ns_url = src.get("NEXTSEEK_BASE_URL") or src.get("NEXTSEEK_URL")
    if ns_url:
        environment.append(
            "Trusted internal service: the NExtSEEK metadata REST API at "
            f"{_rewrite_loopback_url(ns_url)} — the MIT BioMicro Center lab's own "
            "sample/project database (reached as the authenticated user)."
        )
    neo4j_uri = src.get("NEO4J_URI")
    if neo4j_uri:
        environment.append(
            f"Trusted internal graph database: Neo4j at {neo4j_uri} (lab lineage graph)."
        )
    if src.get("GCP_API_KEY"):
        environment.append(
            "Trusted LLM provider: Google Cloud Gemini API — the assistant's own "
            "model provider."
        )
    settings = {"autoMode": {"environment": environment}}
    return ["--settings", json.dumps(settings, separators=(",", ":"))]


def _build_command(
    *,
    model_id: str | None,
    session_id: str | None = None,
    max_budget_usd: float = _DEFAULT_MAX_BUDGET_USD,
    source: Mapping[str, str] | None = None,
) -> list[str]:
    """Build the in-container ``claude`` command: auto-mode base + model + per-turn
    caps + the ``$defaults``-first trusted-infra allowlist (OI-5)."""
    cmd = list(_BASE_CMD)
    if model_id:
        cmd += ["--model", model_id]
    cmd += _cc_limit_args(max_budget_usd)
    cmd += _automode_settings_args(source)
    if session_id:
        cmd += ["--resume", session_id]
    return cmd


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


def _build_volumes(
    *,
    paths: CCPaths,
    project_dirname: str,
    user_id: str,
    cc_state_key: str | None,
    user_memory_file: str | None = None,
    transcripts_dir: str | None = None,
) -> dict[str, dict[str, str]]:
    """Bind mounts for the CC sibling container using Step-2 nested sources.

    Precondition: callers MUST validate ``project_dirname``, ``user_id``, and
    ``cc_state_key`` before interpolation into bind sources.
    """
    from .cc_provision import build_user_dirs

    dirs = build_user_dirs(paths, project_dirname, user_id, session_id=cc_state_key)
    volumes: dict[str, dict[str, str]] = {
        dirs.input_src: {"bind": _CONTAINER_INPUT, "mode": "ro"},
        dirs.shared_src: {"bind": _CONTAINER_SHARED, "mode": "ro"},
        dirs.scratch_src: {
        "bind": _CONTAINER_SCRATCH, "mode": "rw",
        },
    }
    if cc_state_key and dirs.cc_state_src:
        volumes[dirs.cc_state_src] = {
            "bind": _CONTAINER_CLAUDE_HOME, "mode": "rw",
        }
    if user_memory_file:
        volumes[user_memory_file] = {"bind": _CONTAINER_USER_MEMORY, "mode": "ro"}
    if transcripts_dir:
        volumes[transcripts_dir] = {"bind": _CONTAINER_MEMORY_TRANSCRIPTS, "mode": "ro"}
    return volumes


def run_cc_turn(
    *,
    query: str,
    model_id: str | None,
    send_event: SendEvent,
    user_id: str,
    project_dirname: str,
    run_id: str,
    paths: CCPaths,
    session_id: str | None = None,
    cc_state_key: str | None = None,
    user_memory_file: str | None = None,
    transcripts_dir: str | None = None,
    image: str | None = None,
    api_user: str | None = None,
    api_pass: str | None = None,
    max_budget_usd: float = _DEFAULT_MAX_BUDGET_USD,
    turn_timeout: int = _DEFAULT_TURN_TIMEOUT,
) -> None:
    """Execute one Container-CC turn with scoped Dropbox mounts + artifact publish.

    Always terminates with exactly one ``query_complete`` (reply augmented with
    published host paths) or ``query_error``.
    """
    import docker
    from docker.errors import APIError, NotFound

    image = image or DEFAULT_IMAGE

    # I-4 (audit B2): validate BEFORE any path interpolation / mkdir / mount.
    _validate_user_id(user_id)
    _validate_user_id(run_id)
    _validate_project(project_dirname)
    if cc_state_key:
        _validate_user_id(cc_state_key)  # single-segment path guard (UUID chat id)

    from .cc_provision import build_user_dirs

    effective_session_id = session_id
    dirs = build_user_dirs(paths, project_dirname, user_id, session_id=cc_state_key)
    scratch_mount = Path(dirs.scratch_mnt)
    output_mount = Path(dirs.output_mnt)

    # Per-run working dir lives under the user's scratch. Create it via the
    # nextseek-container mount so it exists on the host before the CC container
    # (which mounts the same host dir) starts.
    user_scratch = Path(dirs.scratch_mnt)
    (user_scratch / run_id).mkdir(parents=True, exist_ok=True)
    # The Django container runs as root; the agent runs as the unprivileged image
    # user (uid 1001). Make the per-user scratch writable by the agent so it can
    # create artifacts under /data/scratch (best-effort; dev-instance scratch).
    for _p in (user_scratch, user_scratch / run_id):
        try:
            os.chmod(_p, 0o777)
        except OSError:
            pass

    # Per-session claude-state store: persists transcripts across the ephemeral
    # per-turn containers so --resume works. Created via the nextseek-container
    # mount (user_root_mount) so the host dir exists before the CC sibling mounts
    # it. Resume only when a prior transcript actually exists (turn-1 / wiped
    # store -> start fresh, never resume a missing session).
    if cc_state_key:
        cc_state_dir = Path(dirs.cc_state_mnt)
        if session_id and not cc_session.store_has_transcripts(cc_state_dir):
            logger.info("cc: resume id present but store empty; starting fresh")
            effective_session_id = None
        cc_state_dir.mkdir(parents=True, exist_ok=True)
        for _p in (cc_state_dir.parent, cc_state_dir):
            try:
                os.chmod(_p, 0o777)
            except OSError:
                pass

    volumes = _build_volumes(
        paths=paths, project_dirname=project_dirname, user_id=user_id, cc_state_key=cc_state_key,
        user_memory_file=user_memory_file, transcripts_dir=transcripts_dir,
    )

    # D19: tell the in-container agent how to translate container paths to host
    # paths when it reports artifact locations to the user.
    path_mappings = {
        "output": {"container_root": _CONTAINER_OUTPUT,
                   "host_root": dirs.output_src},
        "scratch": {"container_root": _CONTAINER_SCRATCH,
                    "host_root": dirs.scratch_src},
    }
    # OI-3: the COMPLETE agent env from the single builder — zero AWS/backend
    # creds; Bedrock only via the auth-proxy, NExtSEEK only via the user's login.
    environment = build_agent_environment(
        source=os.environ, api_user=api_user, api_pass=api_pass,
        path_mappings=path_mappings,
    )

    command = _build_command(
        model_id=model_id, session_id=effective_session_id, max_budget_usd=max_budget_usd,
        source=os.environ,
    )

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

        # Hard wall-clock bound on the turn (belt-and-suspenders to --max-budget-usd):
        # if it overruns, stop + force-remove the container, which ends the stdout
        # stream so the read loop below exits.
        _done = threading.Event()
        _timed_out = threading.Event()

        def _watchdog() -> None:
            if not _done.wait(turn_timeout):
                _timed_out.set()
                for _op in (lambda: container.stop(timeout=2),
                            lambda: container.remove(force=True)):
                    try:
                        _op()
                    except Exception:
                        pass

        threading.Thread(target=_watchdog, daemon=True).start()

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

        _done.set()
        if _timed_out.is_set():
            send_event("query_error", {
                "error": f"Container-CC turn exceeded the {turn_timeout}s limit and was stopped.",
                "reason": "exec_timeout", "agent": "container_cc",
                "cc_session_id": translator.session_id,
            })
            return
        if terminal is None:
            for event, data in translator.finalize():
                terminal = (event, data)

        # Post-turn publish: diff scratch, copy new files to the user's output dir.
        published = _publish_artifacts(
            scratch_mount, output_mount, output_host_root=dirs.output_src, before=before)

        if terminal is None:
            terminal = ("query_complete", {"reply": "(no response)", "bundle_id": None,
                                           "cc_session_id": translator.session_id})
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
                                   "agent": "container_cc", "cc_session_id": translator.session_id})
    except Exception as exc:  # noqa: BLE001
        logger.exception("CC turn failed")
        send_event("query_error", {"error": f"Container-CC turn failed: {type(exc).__name__}",
                                   "agent": "container_cc", "cc_session_id": translator.session_id})
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


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    """Return regular, non-symlink file versions under root, keyed by relpath."""
    out: dict[str, tuple[int, int]] = {}
    if not root.is_dir():
        return out
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for filename in filenames:
            full = Path(dirpath) / filename
            if full.is_symlink():
                continue
            try:
                st = full.stat()
            except OSError:
                continue
            out[str(full.relative_to(root))] = (st.st_size, st.st_mtime_ns)
    return out


def snapshot_before(scratch_mount: Path, user_id: str) -> dict[str, tuple[int, int]]:
    return _snapshot_tree(scratch_mount)


def _safe_relpath(rel: str) -> bool:
    if not rel:
        return False
    path = Path(rel)
    return not path.is_absolute() and ".." not in path.parts


def _publish_artifacts(
    scratch_mount: Path,
    output_mount: Path,
    *,
    output_host_root: str,
    before: dict[str, tuple[int, int]],
) -> list[str]:
    """Diff nested scratch, copy new/changed files to nested output, return host paths."""
    from dmac_assistant.run_tracker import diff_files

    after = _snapshot_tree(scratch_mount)
    changed = diff_files(before, after)
    if not changed:
        return []
    written: list[Path] = []
    for rel in sorted(changed):
        if not _safe_relpath(rel):
            logger.warning("CC: refusing unsafe artifact relpath %r", rel)
            continue
        src = scratch_mount / rel
        if src.is_symlink() or not src.is_file():
            continue
        dst = output_mount / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        written.append(dst)
    display: list[str] = []
    for dst in written:
        try:
            rel = dst.relative_to(output_mount)
        except ValueError:
            rel = Path(dst.name)
        display.append(str(Path(output_host_root) / rel))
    return sorted(display)

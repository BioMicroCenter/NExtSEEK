"""Container Claude Code (CC) engine — sandboxed agentic route.

Runs ONE headless ``claude`` container per CC query:

* private user input mounted READ-ONLY at ``/data/input``,
* project shared input mounted READ-ONLY at ``/data/shared``,
* PER-TURN RW scratch mounted at ``/data/scratch`` (#70/#36 — the user-scoped
  scratch root is never mounted into an agent),
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

import base64
import json
import logging
import os
import re
import shutil
import threading
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Callable, NamedTuple
from urllib.parse import quote, quote_plus

import docker

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
_DEFAULT_MAX_BUDGET_USD = float(os.environ.get("NEXTSEEK_CC_MAX_BUDGET_USD", "0.50"))
_DEFAULT_MAX_TURNS = os.environ.get("NEXTSEEK_CC_MAX_TURNS", "50")
# Hard ceiling on a single turn's wall-clock. Historically a fixed 180s project
# rule; now env-configurable (default 180) so a deployment can raise it for heavy
# ops (e.g. reingest) while keeping a bounded default. Per-request overrides —
# the Debug panel's max-turn-length control — are clamped to this ceiling
# server-side (see clamp_turn_timeout), so the UI can never exceed what the
# deployment allows.
_TIMEOUT_HARD_MAX = int(os.environ.get("NEXTSEEK_CC_TIMEOUT_HARD_MAX", "180"))
_TIMEOUT_FLOOR = 30  # never allow a turn shorter than this
_DEFAULT_TURN_TIMEOUT = min(
    int(os.environ.get("NEXTSEEK_CC_TIMEOUT_SECONDS", str(_TIMEOUT_HARD_MAX))),
    _TIMEOUT_HARD_MAX,
)

# #73 (production DoS): hard cgroup ceilings for the per-turn sibling container.
# Cost/turn/time caps above bound spend and wall-clock, but NOT RAM/CPU/PIDs/disk
# — so a single turn (malicious, or an accidental huge query result / artifact
# write) can exhaust the host and starve the co-tenant mysql/neo4j/seek serving
# real users; the kernel OOM killer selects by RSS (the JVM/mysqld), not the
# agent. All env-tunable, same pattern as the caps above.
_DEFAULT_MEM_LIMIT = os.environ.get("NEXTSEEK_CC_MEM_LIMIT", "4g")
_DEFAULT_NANO_CPUS = int(os.environ.get("NEXTSEEK_CC_NANO_CPUS", str(2_000_000_000)))
_DEFAULT_PIDS_LIMIT = int(os.environ.get("NEXTSEEK_CC_PIDS_LIMIT", "512"))
# Portable per-file write cap (fsize ulimit, bytes). storage_opt is unusable on
# overlayfs + the containerd snapshotter (no xfs pquota) and would only cap the
# rootfs anyway, not the scratch volume; the fsize ulimit brakes runaway writes.
_DEFAULT_FSIZE_BYTES = int(os.environ.get("NEXTSEEK_CC_FSIZE_BYTES", str(10 * 1024 ** 3)))


def clamp_turn_timeout(seconds: int | None) -> int:
    """Clamp a requested per-turn wall-clock (seconds) to
    ``[_TIMEOUT_FLOOR, _TIMEOUT_HARD_MAX]``.

    ``None`` / non-positive returns the configured default. The hard ceiling is
    env-bounded (``NEXTSEEK_CC_TIMEOUT_HARD_MAX``), so a UI override can never
    exceed what the deployment allows.
    """
    if not seconds or seconds <= 0:
        return _DEFAULT_TURN_TIMEOUT
    return max(_TIMEOUT_FLOOR, min(int(seconds), _TIMEOUT_HARD_MAX))

# I-4 (audit B2): user_id / project flow into bind-mount SOURCES, so they must be
# validated before any path interpolation or a ``..`` user_id is a host-dir escape.
_USER_ID_RE = re.compile(r"^[A-Za-z0-9._@+-]{1,64}$")

# Step 2b (iter-1 H-2 / iter-2 R2-L2): the deterministic agent container NAME
# is built from ``run_id`` (a Celery task UUID). ``_USER_ID_RE`` above admits
# ``@``/``+`` (legal in a NExtSEEK username) but those are NOT safe inside a
# Docker container ``name=`` — this fail-closed guard is intentionally
# stricter and scoped only to the name-construction path.
_CONTAINER_NAME_SAFE_RE = re.compile(r"^[0-9a-f-]{1,64}$")

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
# #70/#36 note: this stays /home/user, so a bare relative path from the agent
# does NOT land in scratch. Per-turn isolation is therefore achieved by mounting
# the per-run subtree AT _CONTAINER_SCRATCH (see _build_volumes) rather than by
# moving the workdir — moving it would break plugin discovery, which is the very
# thing this constant exists to preserve.
_CONTAINER_WORKDIR = "/home/user"
# The agent's HOME .claude (session transcripts + config). Mounted per chat
# session so --resume finds the transcript across the ephemeral per-turn
# containers (Step 1b). HOME is the image default /home/user (no override).
_CONTAINER_CLAUDE_HOME = _CONTAINER_WORKDIR + "/.claude"
# Step 1c / G7-10: user-tier rolling merged CLAUDE.md. Docker volume subpaths
# mount DIRECTORIES, not single-file overlays, so the merged memory is
# byte-copied into the cc-state subpath at ``{cc_state_mnt}/CLAUDE.md`` before
# spawn (cc-state mounts to /home/user/.claude, so this lands at
# /home/user/.claude/CLAUDE.md — NOT a nested .claude/.claude path). The old RO
# file bind is dropped; MERGE with the baked project /home/user/CLAUDE.md is
# preserved (live-verified — see evidence/run_1c_claude_md_live_probe.py).
_CONTAINER_MEMORY_CLAUDE_MD = "CLAUDE.md"  # basename copied into the cc-state subpath
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
            "(run `docker compose build cc-agent` to build/tag it compose-natively "
            "from docker/cc-runtime/, or set NEXTSEEK_CC_IMAGE to an existing image)"
        )
    # I-17 / audit A1: the bridge NEVER creates the network — a missing one is a
    # deployment error. Gating here keeps the de-credentialed agent off the shared
    # net (the segmented net + bedrock-proxy must be up first).
    try:
        client.networks.get(DEFAULT_NETWORK)
    except Exception:
        return False, (
            f"CC network '{DEFAULT_NETWORK}' not found — run `docker compose up -d` "
            "(dmac-cc-net and bedrock-proxy are compose-managed; see docker-compose.yml) "
            "or override via NEXTSEEK_CC_NETWORK."
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

# Step 2 (G7-11, Task 13): the agent's nextseek plugin dials the NS shared-cred
# sidecar directly over a WebSocket (docker/cc-runtime/build_context/plugins/
# nextseek/bin/_sidecar_client.py — NEXTSEEK_SIDECAR_HOST/NEXTSEEK_SIDECAR_PORT
# defaults). These two literals MUST match the compose SERVICE name
# (docker-compose.yml's ``nextseek-sidecar:`` key — the Docker DNS alias
# agents actually resolve, matching that file's own load-bearing-identity
# note) and the sidecar's SIDECAR_WS_PORT. Overridable via the same-named env
# vars on the Django/source side for non-default topologies.
_DEFAULT_SIDECAR_HOST = "nextseek-sidecar"
_DEFAULT_SIDECAR_PORT = "8765"


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
    chat_session_id: str | None = None,
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
    # Prefer the container-internal transport URL (rendered by startup,
    # decoupled from the host publish port) over the public base URL — the
    # segmented agent can never reach a host-published or public address.
    base = (
        src.get("NEXTSEEK_INTERNAL_BASE_URL")
        or src.get("NEXTSEEK_BASE_URL")
        or src.get("NEXTSEEK_URL")
    )
    if base:
        rewritten = _rewrite_loopback_url(base)
        env["NEXTSEEK_BASE_URL"] = rewritten
        env["NEXTSEEK_URL"] = rewritten
    # Step 2 (G7-11): the NS sidecar's compose service DNS name + WS port —
    # the ONLY two keys the plugin's _sidecar_client.py needs (never a
    # credential; per-request user Basic auth travels inside WS frames).
    env["NEXTSEEK_SIDECAR_HOST"] = src.get("NEXTSEEK_SIDECAR_HOST", _DEFAULT_SIDECAR_HOST)
    env["NEXTSEEK_SIDECAR_PORT"] = src.get("NEXTSEEK_SIDECAR_PORT", _DEFAULT_SIDECAR_PORT)
    # D19: container->host path translation for artifact-location reporting.
    env["DMAC_PATH_MAPPINGS"] = json.dumps(path_mappings, separators=(",", ":"))
    # §4.C: the live chat session id for nextseek-recall/query — not a credential.
    if chat_session_id:
        env["NEXTSEEK_CHAT_SESSION_ID"] = chat_session_id
    return env


def _redact_env(env: Mapping[str, str]) -> dict[str, str]:
    """I-14: redact secret-bearing keys for any operational logging."""
    return {
        k: ("<REDACTED>" if k in _REDACTED_ENV_KEYS else v) for k, v in env.items()
    }


def _secret_variants(environment: Mapping[str, str]) -> list[bytes]:
    """#72: every on-the-wire ENCODING of a secret value, longest-first.

    Scrubbing the UTF-8 plaintext alone is not enough, because the tools the
    agent actually reaches for do not print the plaintext:

    * ``curl -v -u user:pass`` (and requests' ``HTTPBasicAuth``) emit
      ``Authorization: Basic <base64("user:pass")>`` — the plaintext never
      appears, so a plaintext-only scrub leaves a trivially decodable
      credential in the transcript.
    * ``curl https://user:pass@host`` and any URL built with ``urlencode``
      percent-encode the password — with ``+`` for a space in a form body
      (``quote_plus``) but ``%20`` in a URL path (``quote``): two distinct
      strings, and a space is legal in a SEEK password.
    * The transcript is JSONL, so a password containing ``"`` or ``\\`` — both
      legal — is stored ESCAPED (``pa\\"ss``), and its plaintext then appears
      nowhere in the file. That case is why the escaped body is covered here
      and not left to chance: without it the scrub does not merely miss one
      encoding, it silently no-ops entirely, source file included, with no
      signal that it did.

    Longest-first ordering matters twice over: a value containing a shorter
    one is masked whole, and the padded base64 form is consumed before its
    own unpadded prefix.

    Deliberately NOT covered, because the scrub defends against an ACCIDENTAL
    echo (an ``env`` dump, a ``curl -v``, a traceback) and not against an agent
    that is trying to exfiltrate — which no literal-match filter can stop:
    ``base64(password)`` with no username (nothing in the agent's toolbelt emits
    it; Basic auth always encodes the pair), base64 at a non-3-byte-aligned
    offset (Basic auth pins the offset at 0), a value split across two JSON
    strings (the emitter would have to chunk it, and the plaintext then exists
    contiguously nowhere), and lowercase percent-hex (urllib, requests, curl and
    ``encodeURIComponent`` all emit uppercase; matching it needs a ``%XX``-aware
    transform, which is a moving part inside a security-critical function bought
    for no known emitter).
    """
    secrets = {v for k in _REDACTED_ENV_KEYS if (v := environment.get(k))}
    # Not secrets themselves — only the left half of the Basic-auth pair.
    users = {u for k in ("NEXTSEEK_USERNAME", "API_USER") if (u := environment.get(k))}
    variants: set[bytes] = set()
    for value in secrets:
        variants.add(value.encode("utf-8"))
        # url-encoded (userinfo in a URL, path/query) and its form-body cousin
        variants.add(quote(value, safe="").encode("utf-8"))
        variants.add(quote_plus(value).encode("utf-8"))
        # JSON-escaped body, as the transcript writer stores it. Both forms:
        # ensure_ascii=True escapes non-ASCII to \\uXXXX, False leaves it UTF-8.
        # For a password needing neither, these collapse into the plaintext.
        variants.add(json.dumps(value)[1:-1].encode("utf-8"))
        variants.add(json.dumps(value, ensure_ascii=False)[1:-1].encode("utf-8"))
        for user in users:
            encoded = base64.b64encode(f"{user}:{value}".encode("utf-8"))
            variants.add(encoded)
            variants.add(encoded.rstrip(b"="))  # emitters that drop padding
    variants.discard(b"")
    return sorted(variants, key=len, reverse=True)


def _scrub_secret_bytes(raw: bytes, environment: Mapping[str, str]) -> bytes:
    """#72: replace any secret VALUE present in the agent env with ``<REDACTED>``
    inside the raw transcript bytes, before they are copied to disk or persisted
    into ``assistant_cc_transcript``.

    The leak is the value appearing verbatim inside a ``tool_result`` string — an
    accidental ``env``/``printenv``, a ``curl -v`` printing the Basic-auth tuple,
    a requests/urllib traceback — so this is a literal-VALUE replacement over the
    jsonl bytes. ``_redact_env`` is key-based and cannot cover this.

    Idempotent: ``<REDACTED>`` contains no secret, so re-running is a no-op.
    """
    if not raw:
        return raw
    for needle in _secret_variants(environment):
        raw = raw.replace(needle, b"<REDACTED>")
    return raw


def transcript_scrubber(environment: Mapping[str, str]) -> Callable[[bytes], bytes]:
    """Bind ``environment`` to a ``bytes -> bytes`` scrub for read/copy points
    that hold credentials but not the whole agent env (memory staging)."""
    return lambda raw: _scrub_secret_bytes(raw, environment)


class ScrubReport(NamedTuple):
    """Outcome of one ``scrub_transcript_store`` pass.

    ``rewritten`` alone cannot tell a caller whether the store is clean: a pass
    that scrubbed nothing because there was nothing to scrub and a pass that
    scrubbed nothing because every file threw both report 0. ``skipped`` is the
    count of files this function KNOWS it left unscrubbed.
    """

    rewritten: int
    skipped: int


def scrub_transcript_store(
    cc_state_dir: Path | str, environment: Mapping[str, str]
) -> ScrubReport:
    """#72: scrub the SOURCE session transcripts in place.

    The per-session jsonl under ``<cc_state>/projects`` is the origin of every
    other copy, and it is deliberately never deleted — ``--resume`` needs it
    across the ephemeral per-turn containers. Scrubbing only the derived copies
    (the ``raw/`` file and the DB blob) therefore left the plaintext password
    sitting in the ``cc-state`` volume indefinitely, where two production paths
    re-read it: ``cc_memory_io.stage_transcripts`` copies it into a dir mounted
    read-only into LATER agent containers, and ``cc_sweep`` feeds it verbatim to
    the summarizer whose output lands in the merged ``CLAUDE.md``.

    Rewritten via tmp + ``os.replace`` so a reader never observes a truncated
    file and the jsonl stays structurally valid: ``<REDACTED>`` carries no quote
    or backslash, so replacing a value inside a JSON string cannot break the
    escaping that ``--resume`` parses.

    Every file it cannot scrub is LOGGED with its path and the error, and
    counted in ``ScrubReport.skipped``. ``cc_sweep`` re-reads these same files
    raw and has no credentials of its own to scrub with, so this function is the
    single thing standing between the store and the summarizer: skipping a file
    quietly is a silent leak into the merged ``CLAUDE.md``, and a bare return
    count cannot distinguish "scrubbed 0, skipped 0" from "scrubbed 0,
    skipped 4".
    """
    root = Path(cc_state_dir) / "projects"
    if not root.is_dir():
        return ScrubReport(0, 0)
    rewritten = 0
    skipped = 0
    for path in root.rglob("*.jsonl"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            skipped += 1
            logger.warning("cc #72: cannot read transcript %s, left unscrubbed: %r",
                           path, exc)
            continue
        clean = _scrub_secret_bytes(raw, environment)
        if clean == raw:
            continue
        tmp = path.with_name(path.name + ".scrub-tmp")
        try:
            tmp.write_bytes(clean)
            os.replace(tmp, path)
            # os.replace installs a NEW inode owned by root (Django); the agent
            # runs as uid 1001 and must still read/append it on the next turn.
            # Same world-permission approach as the mount backing dirs above.
            try:
                os.chmod(path, 0o666)
            except OSError:
                pass
            rewritten += 1
        except OSError as exc:
            skipped += 1
            logger.warning("cc #72: failed to scrub transcript %s, left "
                           "unscrubbed: %r", path, exc)
            try:
                tmp.unlink()
            except OSError:
                pass
    return ScrubReport(rewritten=rewritten, skipped=skipped)


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
    ns_url = (
        src.get("NEXTSEEK_INTERNAL_BASE_URL")
        or src.get("NEXTSEEK_BASE_URL")
        or src.get("NEXTSEEK_URL")
    )
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


def _container_name_for_run(run_id: str) -> str:
    """Step 2b (iter-1 H-2): the deterministic ``dmac-cc-agent-<run_id>`` name
    used so agents are identifiable BY NAME in ``docker network inspect``
    output (previously a random Docker name, making any name-based
    network-membership check unevaluable).

    Fail-closed (iter-2 R2-L2): raises if ``run_id`` doesn't match the strict
    Docker-name-safe charset ``^[0-9a-f-]{1,64}$`` — Celery task UUIDs satisfy
    this; ``_USER_ID_RE`` is NOT sufficient here (it admits ``@``/``+``).
    """
    if not _CONTAINER_NAME_SAFE_RE.fullmatch(run_id or ""):
        raise ValueError(f"run_id not safe for a docker container name: {run_id!r}")
    return f"dmac-cc-agent-{run_id}"


def _run_kwargs(
    *,
    image: str,
    command: list[str],
    environment: dict[str, str],
    mounts: list[dict] | None,
    run_id: str,
    user_id: str,
    network: str = DEFAULT_NETWORK,
    workdir: str = _CONTAINER_WORKDIR,
) -> dict[str, Any]:
    """Build the docker-py ``containers.run`` kwargs for one CC turn.

    The container joins ``network`` — which DEFAULTS to the segmented
    ``dmac-cc-net`` (``DEFAULT_NETWORK``), NOT the shared nextseek compose
    network — so the de-credentialed agent reaches only the bedrock-proxy and
    the nginx entrypoint; it runs in ``workdir`` (the image WORKDIR) so the
    baked CLAUDE.md + nextseek plugin guidance are discovered. CC user trees are
    passed as ``mounts`` (Engine-API volume-subpath ``Mount`` dicts), never as a
    host-path ``volumes`` dict (G7-10 named-volume cutover). ``name`` is the
    Step 2b deterministic ``dmac-cc-agent-<run_id>`` name.
    """
    return {
        "image": image,
        "command": command,
        "environment": environment,
        "mounts": mounts or None,
        "working_dir": workdir,
        "network": network,
        "name": _container_name_for_run(run_id),
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
        # #73: hard resource ceilings so one turn cannot DoS the co-tenant
        # mysql/neo4j/seek. memswap_limit == mem_limit, or the cap escapes into
        # swap. fsize ulimit is the portable disk brake (see _DEFAULT_FSIZE_BYTES).
        "mem_limit": _DEFAULT_MEM_LIMIT,
        "memswap_limit": _DEFAULT_MEM_LIMIT,
        "nano_cpus": _DEFAULT_NANO_CPUS,
        "pids_limit": _DEFAULT_PIDS_LIMIT,
        "ulimits": [docker.types.Ulimit(
            name="fsize", soft=_DEFAULT_FSIZE_BYTES, hard=_DEFAULT_FSIZE_BYTES)],
        # F15: reap the sibling even when the parent Django worker dies mid-turn
        # (a SIGKILL/worker-recycle skips the finally-block remove, orphaning the
        # container on dmac-cc-net with an rw mount and live spend). Safe here:
        # the code never container.wait()s or inspects post-exit, and the now-
        # redundant finally remove(force=True) is already guarded by except pass.
        "auto_remove": True,
    }


def _spawn_with_stale_name_retry(client: Any, run_kwargs: dict[str, Any]) -> Any:
    """Spawn the CC sibling container, handling a stale same-name collision.

    Step 2b: Celery retries reuse the same ``task_id`` (== ``run_id``), so a
    crashed prior attempt can still hold the deterministic ``name=`` (Step 2b
    above). On the Docker SDK this surfaces as ``docker.errors.APIError``
    with ``status_code == 409`` (Engine's "Conflict. The container name … is
    already in use" response) — force-remove the EXACT stale name and retry
    the spawn ONCE. Any other error (including a repeat 409 on the retry)
    propagates unchanged.
    """
    from docker.errors import APIError, NotFound

    try:
        return client.containers.run(**run_kwargs)
    except APIError as exc:
        if exc.status_code != 409:
            raise
        stale_name = run_kwargs.get("name")
        if not stale_name:
            raise
        logger.warning(
            "cc: stale container %s held the deterministic name (409 Conflict) "
            "— removing and retrying once", stale_name,
        )
        try:
            client.containers.get(stale_name).remove(force=True)
        except NotFound:
            pass
        return client.containers.run(**run_kwargs)


def _mount_volume_subpath(source: str, target: str, subpath: str, *, read_only: bool = False) -> dict:
    """docker-py 7.1.0: Mount() has no subpath kwarg — patch VolumeOptions onto Mount dict subclass."""
    m = docker.types.Mount(target=target, source=source, type="volume", read_only=read_only)
    m["VolumeOptions"] = {"Subpath": subpath}  # PascalCase key required by Engine API
    return m


def _build_volumes(
    *,
    paths: CCPaths,
    project_dirname: str,
    user_id: str,
    cc_state_key: str | None,
    run_id: str,
    transcripts_subpath: str | None = None,
) -> list[dict]:
    """Engine-API ``Mount`` payloads (volume subpaths of ``dmac-cc-users``) for
    the CC sibling container.

    Every mount targets the SAME named volume (``paths.users_volume``) and
    isolates the agent to its own per-user (or, for ``shared``, per-project)
    ``VolumeOptions.Subpath`` tail — the cross-user-leak guard (OI-3). The
    per-mount Subpath VALUE is supplied directly by ``build_user_dirs``'s
    ``*_subpath`` fields (never by stripping a prefix): ``shared`` is
    project-scoped, and transcripts is the ``_memory/<session>`` tail plus a
    ``/transcripts`` child.

    Precondition: callers MUST validate ``project_dirname``, ``user_id``, and
    ``cc_state_key`` before interpolation into subpaths.
    """
    from .cc_provision import build_user_dirs

    dirs = build_user_dirs(
        paths, project_dirname, user_id, session_id=cc_state_key, run_id=run_id
    )
    vol = paths.users_volume
    mounts: list[dict] = [
        _mount_volume_subpath(vol, _CONTAINER_INPUT, dirs.input_subpath, read_only=True),
        _mount_volume_subpath(vol, _CONTAINER_SHARED, dirs.shared_subpath, read_only=True),
        # #70/#36: the PER-TURN subtree, not the user-scoped scratch root. Scratch
        # used to be mounted whole and read-write, so one turn could read, act on
        # and overwrite files another turn left behind — and a bare
        # ``/data/scratch/foo.json`` from two different turns was the SAME file.
        # The user root is now not mounted into the agent at all; nothing in the
        # agent's contract needs cross-turn scratch (prior artifacts are published
        # host-side into output/, which is not an agent mount either).
        _mount_volume_subpath(
            vol, _CONTAINER_SCRATCH, dirs.run_scratch_subpath, read_only=False
        ),
    ]
    if cc_state_key and dirs.cc_state_subpath:
        mounts.append(
            _mount_volume_subpath(vol, _CONTAINER_CLAUDE_HOME, dirs.cc_state_subpath, read_only=False)
        )
    if transcripts_subpath:
        mounts.append(
            _mount_volume_subpath(
                vol, _CONTAINER_MEMORY_TRANSCRIPTS, transcripts_subpath, read_only=True
            )
        )
    return mounts


def _preflight_subpath_dirs(user_root_mount: str, mounts: list[dict]) -> None:
    """Fail closed BEFORE spawn if any mount's backing subpath dir is absent in
    the volume. The Engine's ``VolumeOptions.Subpath`` refuses to start a
    container when the exact subdir does not exist, so Django must have mkdir'd
    every one (via ``user_root_mount``) first."""
    root = Path(user_root_mount)
    for m in mounts:
        subpath = m.get("VolumeOptions", {}).get("Subpath")
        if not subpath:
            raise ValueError(f"mount missing VolumeOptions.Subpath: {m!r}")
        backing = root / subpath
        if not backing.is_dir():
            raise FileNotFoundError(
                f"CC mount subpath dir missing in {user_root_mount}: {subpath}"
            )


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
    memory_claude_md: str | None = None,
    transcripts_subpath: str | None = None,
    image: str | None = None,
    api_user: str | None = None,
    api_pass: str | None = None,
    max_budget_usd: float = _DEFAULT_MAX_BUDGET_USD,
    turn_timeout: int = _DEFAULT_TURN_TIMEOUT,
    chat_session: Any | None = None,
    user_query: str = "",
    on_turn_complete: Callable[..., None] | None = None,
    chat_session_id: str | None = None,
) -> None:
    """Execute one Container-CC turn with scoped input/shared mounts + artifact publish.

    Always terminates with exactly one ``query_complete`` (structured ``artifacts``
    channel for deliverables, ``cc_raw_files`` for scratch/raw/) or ``query_error``.
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
    dirs = build_user_dirs(
        paths, project_dirname, user_id, session_id=cc_state_key, run_id=run_id
    )
    # #70/#36: every turn-scoped consumer of "scratch" — the pre/post snapshot
    # diff that publishes artifacts, the path mappings the agent reports paths
    # with, and the sidecar staging sweep — must address the SAME per-turn
    # subtree the agent has mounted at /data/scratch. Re-pointing the mount
    # without re-pointing these would silently publish nothing.
    scratch_mount = Path(dirs.run_scratch_mnt)
    output_mount = Path(dirs.output_mnt)
    mount_root = Path(paths.user_root_mount.rstrip("/"))

    # G7-10: build the volume-subpath Mounts first; every mount's backing subdir
    # must exist inside the dmac-cc-users volume before spawn (the Engine's
    # VolumeOptions.Subpath refuses to start a container otherwise), so Django
    # (root in nextseek) mkdirs + chmod 0777 each one via user_root_mount.
    mounts = _build_volumes(
        paths=paths, project_dirname=project_dirname, user_id=user_id,
        cc_state_key=cc_state_key, run_id=run_id,
        transcripts_subpath=transcripts_subpath,
    )
    for _m in mounts:
        _backing = mount_root / _m["VolumeOptions"]["Subpath"]
        _backing.mkdir(parents=True, exist_ok=True)
        # The Django container runs as root; the agent runs as the unprivileged
        # image user (uid 1001). Make each backing dir writable (best-effort;
        # dev-instance). Task 10 sentinel scratch write proves uid-1001 writes.
        try:
            os.chmod(_backing, 0o777)
        except OSError:
            pass

    # Per-run working dir lives under the user's scratch subpath. The mount pass
    # above already mkdir'd it (it is now the scratch mount's own backing dir);
    # kept for the chmod and as a belt-and-braces preflight.
    scratch_mount.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(scratch_mount, 0o777)
    except OSError:
        pass

    # Per-session claude-state store: persists transcripts across the ephemeral
    # per-turn containers so --resume works. Resume only when a prior transcript
    # actually exists (turn-1 / wiped store -> start fresh, never resume a
    # missing session). The dir itself was mkdir'd in the mount pass above.
    if cc_state_key and dirs.cc_state_mnt:
        cc_state_dir = Path(dirs.cc_state_mnt)
        if session_id and not cc_session.store_has_transcripts(cc_state_dir):
            logger.info("cc: resume id present but store empty; starting fresh")
            effective_session_id = None
        # G7-10 1c: byte-copy the merged user-tier CLAUDE.md into the cc-state
        # subpath (mounts to /home/user/.claude) so it lands at
        # /home/user/.claude/CLAUDE.md and MERGES with the baked project
        # /home/user/CLAUDE.md. Replaces the dropped RO file bind; the agent may
        # transiently overwrite it within a turn (re-copied next turn — accepted).
        if memory_claude_md:
            try:
                shutil.copyfile(memory_claude_md, cc_state_dir / _CONTAINER_MEMORY_CLAUDE_MD)
            except OSError:
                logger.warning("cc-1c: failed to stage merged CLAUDE.md into cc-state")

    # Fail closed if any mount's backing subpath dir is still missing.
    _preflight_subpath_dirs(str(mount_root), mounts)

    # D19: tell the in-container agent how to translate container paths to the
    # user-facing logical paths (under user_root_mount) when it reports artifact
    # locations. G7-10 retires host-bind ``host_root`` strings for ``logical_root``.
    path_mappings = {
        "output": {"container_root": _CONTAINER_OUTPUT,
                   "logical_root": dirs.output_mnt},
        "scratch": {"container_root": _CONTAINER_SCRATCH,
                    "logical_root": dirs.run_scratch_mnt},
    }
    # OI-3: the COMPLETE agent env from the single builder — zero AWS/backend
    # creds; Bedrock only via the auth-proxy, NExtSEEK only via the user's login.
    environment = build_agent_environment(
        source=os.environ, api_user=api_user, api_pass=api_pass,
        path_mappings=path_mappings,
        chat_session_id=chat_session_id,
    )

    command = _build_command(
        model_id=model_id, session_id=effective_session_id, max_budget_usd=max_budget_usd,
        source=os.environ,
    )

    before = snapshot_before(scratch_mount, user_id)
    # G7-11 (Task 14): timestamp captured BEFORE the agent runs, so the in-turn
    # staging sweep can distinguish THIS turn's ``.complete`` markers (mtime >=
    # sweep_since) from older strays. Sidecar + Django share the host clock.
    sweep_since = time.time()

    translator = CCStreamTranslator()
    translator._turn_start_ts = time.time()
    terminal: tuple[str, dict[str, Any]] | None = None
    client = docker.from_env()
    container = None
    try:
        spawn_kwargs = _run_kwargs(
            image=image, command=command, environment=environment,
            mounts=mounts, run_id=run_id, user_id=user_id,
        )
        container = _spawn_with_stale_name_retry(client, spawn_kwargs)

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

        # G7-11 (Task 14): same-turn sidecar staging sweep. Mirrors upstream
        # ws.py:276-293 — sweep this user's ``.complete``-marked staging dirs
        # into their OWN ``{project}/{user}/scratch/nextseek-artifacts/`` subtree
        # BEFORE the publish diff, so turn-N staged artifacts (report /
        # generate-submission downloads) surface in turn N's published set.
        # Trusted-code only (never the agent, never the sidecar). ``sweep_since``
        # limits the in-turn sweep to THIS turn's markers; older strays are left
        # for the ``cc_sweep_staging`` recovery entrypoint. Never fatal to the
        # turn (upstream _sweep_then_diff swallows sweep errors likewise).
        if api_user:
            try:
                from . import cc_staging
                cc_staging.sweep_user_staging(
                    user_root_mount=paths.user_root_mount,
                    # #70/#36: sweep into THIS turn's scratch subtree — that is
                    # what the publish diff below looks at, and what the agent
                    # had mounted. Sweeping into the user root would drop these
                    # artifacts outside the diffed tree entirely.
                    scratch_dir=dirs.run_scratch_mnt,
                    api_user=api_user,
                    user_id=user_id,
                    project_dirname=project_dirname,
                    since_ts=sweep_since,
                )
            except Exception as exc:  # noqa: BLE001 — sweep must not kill the turn
                logger.warning(
                    "cc staging sweep failed for user=%s: %s", user_id, type(exc).__name__
                )

        # Post-turn publish: diff scratch, split deliverables from scratch/raw/.
        result = _publish_artifacts(
            scratch_mount, output_mount,
            turn_id=str(run_id),
            output_logical_root=dirs.output_mnt, before=before,
        )

        if terminal is None:
            terminal = ("query_complete", {"reply": "(no response)", "bundle_id": None,
                                           "cc_session_id": translator.session_id})
        event, data = terminal
        if event == "query_complete":
            data = dict(data)
            data["mode"] = "cc"
            data["artifacts"] = result["artifacts"] or None
            data["cc_raw_files"] = result["raw"]
        if event == "query_complete" and on_turn_complete and chat_session is not None:
            from django.utils import timezone
            from . import cc_summary, cc_trace
            from .cc_turn_complete import TurnCompletePayload
            turn_start = translator._turn_start_ts
            jsonl_path = None
            for _ in range(3):
                jsonl_path = _newest_jsonl_under(
                    Path(dirs.cc_state_mnt) / "projects", min_mtime=turn_start - 1)
                if jsonl_path:
                    break
                time.sleep(0.2)
            raw = jsonl_path.read_bytes() if jsonl_path else b""
            # #72: scrub secret VALUES (the per-request NExtSEEK password et al.)
            # out of the raw transcript BEFORE either sink — the on-disk raw/ copy
            # AND the assistant_cc_transcript blob below (raw_jsonl=raw). Without
            # this a single tool call echoing the env writes the user's plaintext
            # password into a permanent DB row.
            raw = _scrub_secret_bytes(raw, environment)
            if raw:
                raw_copy = Path(dirs.output_mnt) / "raw" / f"transcript-{run_id}.jsonl"
                raw_copy.parent.mkdir(parents=True, exist_ok=True)
                if not _safe_relpath(raw_copy.name):
                    raise ValueError("bad transcript basename")
                # write the SCRUBBED bytes (not shutil.copy2 of the raw file) so
                # the on-disk copy carries no secrets either.
                raw_copy.write_bytes(raw)
            parsed = cc_summary.parse_transcript(raw) if raw else None
            trace = cc_trace.extract_trace(
                parsed, cc_session_id=translator.session_id or "",
                ts=timezone.now().isoformat(),
                files_created=result["files_created"],
                files_modified=result["files_modified"],
                result_meta={"num_turns": data.get("num_turns"),
                             "duration_ms": data.get("duration_ms"),
                             "cost_usd": data.get("total_cost_usd")},
            ) if parsed else None
            from django.conf import settings
            strict = getattr(settings, "CC_PERSIST_STRICT", False)
            if trace is not None:
                data = dict(data)
                data["mode"] = "cc"
                data["cc_traces"] = [trace.model_dump()]
                try:
                    on_turn_complete(TurnCompletePayload(
                        chat_session=chat_session, user_query=user_query or "",
                        assistant_reply=data.get("reply") or "",
                        ts=timezone.now().isoformat(),
                        artifacts=data.get("artifacts"),
                        cc_traces=[trace.model_dump()],
                        turn_id=str(run_id),
                        cc_session_id=translator.session_id,
                        raw_jsonl=raw,
                    ))
                except Exception:
                    logger.exception("CC persist failed after a successful turn "
                                     "(run_id=%s); delivering reply, trace not persisted", run_id)
                    if strict:
                        raise
            else:
                logger.error("cc persist: missing transcript jsonl after successful turn "
                             "(run_id=%s); delivering reply without persisted trace", run_id)
                if strict:
                    raise RuntimeError("cc persist: missing transcript jsonl after successful turn")
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
        # Stop the agent BEFORE scrubbing (#72). Order matters: on any exception
        # that leaves the container ALIVE — an attach failure, a mid-stream
        # docker error — the agent goes on writing its transcript, so a scrub
        # that ran first would be overtaken by the tail appended after it, and
        # nothing re-scrubs that tail unless the same chat happens to run
        # another turn. Stopping first also closes the smaller race the other
        # way: scrub_transcript_store rewrites via tmp + os.replace, which would
        # silently discard whatever a still-running agent appended between the
        # read and the swap.
        #
        # Both calls stay best-effort: the container is spawned with
        # auto_remove=True, so a clean stop usually removes it and the remove()
        # below is the belt-and-braces path for a container that outlived its
        # own cleanup. Neither failing may skip the scrub.
        if container is not None:
            try:
                container.stop(timeout=5)
            except Exception:
                pass
            try:
                container.remove(force=True)
            except Exception:
                pass
        # #72: scrub the SOURCE transcript, not just the derived copies. In the
        # finally (not the query_complete branch) because a turn that errored,
        # timed out or was budget-killed still leaves a jsonl behind holding
        # whatever the agent echoed before it died — and that file survives for
        # --resume, gets staged read-only into later agents, and is fed to the
        # summarizer. Best-effort: a scrub failure must never fail the turn.
        try:
            if dirs.cc_state_mnt:
                report = scrub_transcript_store(Path(dirs.cc_state_mnt), environment)
                if report.skipped:
                    logger.warning(
                        "cc #72: transcript store scrub left %d of %d file(s) "
                        "UNSCRUBBED (run_id=%s) — cc_sweep will read those raw",
                        report.skipped, report.skipped + report.rewritten, run_id)
        except Exception:  # noqa: BLE001
            logger.warning("cc #72: transcript store scrub failed", exc_info=True)


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


def _newest_jsonl_under(root: Path, *, min_mtime: float | None = None) -> Path | None:
    """Pick newest *.jsonl under root; if min_mtime set, only files with mtime >= min_mtime."""
    candidates = [p for p in root.rglob("*.jsonl") if p.is_file()]
    if min_mtime is not None:
        candidates = [p for p in candidates if p.stat().st_mtime >= min_mtime]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def _publish_artifacts(
    scratch_mount: Path,
    output_mount: Path,
    *,
    turn_id: str,
    output_logical_root: str,
    before: dict[str, tuple[int, int]],
) -> dict:
    """Diff scratch; split deliverables (artifacts) from scratch/raw/ (raw).
    Artifacts -> output/artifacts/<turn_id>/ (zipped if >1 per turn, downloadable);
    raw -> output/raw/ (on disk, not bundled). Keys are turn-scoped: "<turn_id>/<relpath>"."""
    from dmac_assistant.run_tracker import diff_files
    from . import cc_artifacts

    after = _snapshot_tree(scratch_mount)
    changed = set(diff_files(before, after))
    if not changed:
        return {"artifacts": [], "raw": [], "raw_zip": None, "files_created": [], "files_modified": []}

    created = {r for r in changed if r not in before}
    modified = changed - created

    art_rels, raw_rels = cc_artifacts.partition_changed(set(changed))
    art_dir = output_mount / "artifacts" / turn_id
    raw_dir = output_mount / "raw"

    def _copy(rels: set[str], dest_root: Path, *, strip_raw_prefix: bool = False) -> list[Path]:
        written: list[Path] = []
        for rel in sorted(rels):
            if not _safe_relpath(rel):
                logger.warning("CC: refusing unsafe artifact relpath %r", rel)
                continue
            src = scratch_mount / rel
            if src.is_symlink() or not src.is_file():
                continue
            out_rel = rel.removeprefix("raw/") if strip_raw_prefix else rel
            dst = dest_root / out_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            written.append(dst)
        return written

    art_files = _copy(art_rels, art_dir)
    raw_files = _copy(raw_rels, raw_dir, strip_raw_prefix=True)

    artifacts: list[dict] = []
    if len(art_files) > 1:
        from nextseek_api.cc_assistant.cc_artifacts import build_artifact_zip
        zip_path = art_dir / "artifacts.zip"
        build_artifact_zip(art_files, zip_path, arc_prefix=art_dir)
        artifacts.append({
            "artifact_type": "file", "key": f"{turn_id}/artifacts.zip",
            "label": "artifacts.zip", "file_format": "zip",
        })
    elif len(art_files) == 1:
        dst = art_files[0]
        rel = dst.relative_to(art_dir)
        artifacts.append({
            "artifact_type": "file", "key": f"{turn_id}/{rel}",
            "label": dst.name, "file_format": dst.suffix.lstrip(".") or "file",
        })
    return {
        "artifacts": artifacts,
        "raw": [str(Path(output_logical_root) / "raw" / p.relative_to(raw_dir)) for p in raw_files],
        "raw_zip": None,
        "files_created": sorted(created),
        "files_modified": sorted(modified),
    }

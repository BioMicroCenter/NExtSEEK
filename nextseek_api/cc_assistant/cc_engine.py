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
import hashlib
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
from . import cc_transcript_store
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


# #76: the per-session transcript store lives at <cc_state_dir>/projects.
_TRANSCRIPT_STORE_DIRNAME = "projects"
_SCRUB_MANIFEST_VERSION = 1


def _scrub_manifest_path(cc_state_dir: Path | str) -> Path:
    """#76: where the clean-watermark for ``cc_state_dir`` is recorded.

    ONE LEVEL ABOVE the per-session cc-state dir, deliberately. The agent
    container mounts ``cc_state_subpath`` (``<project>/<user>/cc-state/
    <session_id>``) READ-WRITE at ``/home/user/.claude``, so anything written
    inside that dir is forgeable from inside the sandbox — including a
    watermark claiming a transcript full of plaintext is clean. Its PARENT
    (``.../cc-state/``) is mounted into no agent at all. The leading dot keeps
    it out of the ``*.jsonl`` globs that walk this tree.

    The placement only holds if ``cc_state_dir`` is the REAL session dir. It is
    ``transcript_is_verified_scrubbed`` that has to establish that from a bare
    transcript path, and getting that derivation wrong puts this file back
    inside the agent's mount — see the attack recorded there.
    """
    cc_state_dir = Path(cc_state_dir)
    return cc_state_dir.parent / f".{cc_state_dir.name}.scrub.json"


def _transcript_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_scrub_manifest(cc_state_dir: Path | str) -> dict[str, str]:
    """Recorded ``{relpath-under-cc_state_dir: sha256-of-clean-bytes}``.

    Returns ``{}`` — i.e. "nothing is known to be clean" — for every failure:
    absent, unreadable, not JSON, wrong shape, wrong version. The single
    consumer treats an unrecorded file as unscrubbed, so every error path here
    has to fail towards "unknown", never towards "clean".
    """
    try:
        blob = _scrub_manifest_path(cc_state_dir).read_bytes()
    except OSError:
        return {}
    try:
        data = json.loads(blob)
    except ValueError:
        return {}
    if not isinstance(data, dict) or data.get("version") != _SCRUB_MANIFEST_VERSION:
        return {}
    files = data.get("files")
    if not isinstance(files, dict):
        return {}
    return {str(k): str(v) for k, v in files.items() if isinstance(v, str)}


def _write_scrub_manifest(cc_state_dir: Path | str, files: dict[str, str]) -> None:
    """Replace the watermark for ``cc_state_dir`` with exactly ``files``.

    A WHOLE-FILE replace, not a merge: an entry whose transcript has since been
    deleted, or which this pass could not read, must not survive as a stale
    "clean" claim. tmp + ``os.replace`` so a concurrent reader never sees a
    half-written manifest and read it as ``{}``-on-parse-error.
    """
    path = _scrub_manifest_path(cc_state_dir)
    payload = json.dumps(
        {"version": _SCRUB_MANIFEST_VERSION, "files": files},
        sort_keys=True, separators=(",", ":"),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o644)
    except OSError:
        pass


def transcript_is_verified_scrubbed(transcript_path: Path | str, raw: bytes) -> bool:
    """#76: has EXACTLY these bytes been through ``scrub_transcript_store``?

    ``cc_sweep`` runs on a Celery beat with no request and therefore no
    credentials, so it cannot scrub the transcript it is about to feed to a
    third-party summarizer — its safety depends entirely on the source having
    been scrubbed already. That scrub runs in the ``finally`` of a turn on a
    ``daemon=True`` thread, which a worker recycle or a SIGKILL skips outright,
    leaving the user's plaintext password in a file the sweep then reads raw.

    This is the sweep's gate. The digest is of the bytes the scrub LEFT
    BEHIND, so a transcript the agent appended to after the last scrub also
    fails it: "verified" means this exact content was cleaned, not that this
    path was cleaned once.

    Fail-closed on every unknown: no ``projects`` ancestor to locate the
    manifest from, no manifest, unparseable manifest, no entry, digest
    mismatch — all ``False``. A skipped session is retried on the next beat and
    is repaired the next time its owner runs a CC turn (see
    ``scrub_sibling_transcript_stores``).

    #76-A — why the OUTERMOST ``projects`` ancestor, not the nearest. The
    caller (``cc_sweep``) has only a transcript path, so the session dir has to
    be derived from it, and the first version of this took the nearest
    ``projects`` ancestor. That is forgeable from inside the sandbox. An agent
    that mkdirs a NESTED store inside its own read-write mount::

        transcript  <cc_state_mnt>/projects/evil/projects/x/t.jsonl
        nearest  -> cc_state_dir = <cc_state_mnt>/projects/evil
        manifest -> <cc_state_mnt>/projects/.evil.scrub.json   ← agent-writable

    relocates the watermark into a directory it can write, and the forged entry
    then verifies bytes that were never scrubbed. ``_session_metas`` rglobs the
    store, so such a path really can become ``tgt.transcript_path``.

    Taking the OUTERMOST match closes it. The agent's writable root IS
    ``cc_state_dir`` and the genuine store is its direct child, so every path
    the agent can fabricate lies strictly BELOW the genuine ``projects`` and can
    never be the outermost one. The agent cannot create a directory above its
    own mount root, which is what it would need.

    Known liveness caveat, not a security one: if a path component ABOVE the
    cc-state dir is itself literally named ``projects`` — a Django username of
    ``projects`` (``_validate_user_id`` permits it), or a
    ``DMAC_USER_ROOT_MOUNT`` containing one — this resolves to the wrong dir
    and nothing in that deployment ever verifies. That fails CLOSED: the sweep
    skips instead of leaking, and the in-request readers scrub at their own read
    point regardless.
    """
    path = Path(transcript_path)
    store_roots = [p for p in path.parents if p.name == _TRANSCRIPT_STORE_DIRNAME]
    if not store_roots:
        return False
    # path.parents runs nearest -> root, so [-1] is the outermost match.
    cc_state_dir = store_roots[-1].parent
    try:
        rel = str(path.relative_to(cc_state_dir))
    except ValueError:
        return False
    recorded = _read_scrub_manifest(cc_state_dir).get(rel)
    return bool(recorded) and recorded == _transcript_digest(raw)


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

    #76: every file this pass leaves VERIFIED CLEAN — the already-clean branch
    as much as the rewritten one — is recorded in a durable watermark next to
    the store (``_scrub_manifest_path``), keyed by relpath and digested over
    the bytes left behind. That watermark is what lets ``cc_sweep`` tell "this
    was scrubbed" from "this was never touched because the process died before
    the ``finally`` ran". Files this pass skipped are deliberately NOT recorded,
    and the manifest is replaced wholesale rather than merged, so a previous
    pass's claim about a file that has since become unreadable does not
    survive.
    """
    cc_state_dir = Path(cc_state_dir)
    root = cc_state_dir / _TRANSCRIPT_STORE_DIRNAME
    if not root.is_dir():
        return ScrubReport(0, 0)
    rewritten = 0
    skipped = 0
    verified: dict[str, str] = {}
    for path in root.rglob("*.jsonl"):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            rel = str(path.relative_to(cc_state_dir))
        except ValueError:  # pragma: no cover - rglob cannot leave its root
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
            verified[rel] = _transcript_digest(raw)
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
            verified[rel] = _transcript_digest(clean)
        except OSError as exc:
            skipped += 1
            logger.warning("cc #72: failed to scrub transcript %s, left "
                           "unscrubbed: %r", path, exc)
            try:
                tmp.unlink()
            except OSError:
                pass
    try:
        _write_scrub_manifest(cc_state_dir, verified)
    except OSError as exc:
        # The files ARE scrubbed; only the proof is missing. Deliberately does
        # not touch the counts: an unrecorded file is treated as unscrubbed by
        # cc_sweep, which is the safe direction, and the next turn re-records it.
        logger.warning("cc #76: could not record scrub watermark for %s: %r",
                       cc_state_dir, exc)
    return ScrubReport(rewritten=rewritten, skipped=skipped)


def scrub_sibling_transcript_stores(
    cc_state_root: Path | str,
    environment: Mapping[str, str],
    *,
    exclude: Path | str | None = None,
) -> ScrubReport:
    """#76: scrub the user's OTHER session transcript stores under
    ``cc_state_root`` (``<project>/<user>/cc-state/``).

    ``scrub_transcript_store`` is turn-scoped: it only ever sees the store of
    the session whose turn is running. The durability requirement is
    process-lifetime — the turn runs on a ``daemon=True`` thread, so a worker
    recycle or SIGKILL skips its ``finally`` entirely — and no boot-time repair
    is possible, because the secret being scrubbed for is the per-request
    user's plaintext SEEK password and a startup hook has neither a request nor
    a user. Scrubbing for an empty environment produces no needles at all and
    would report success having done nothing.

    The next moment that credential legitimately exists is that user's next CC
    turn. So this widens the repair to every session store they own: a
    transcript orphaned by an abrupt death is cleaned, and re-watermarked, then
    — rather than never.

    Accepted race, inherited and widened rather than introduced: the rewrite is
    read -> ``os.replace``, so bytes a CONCURRENTLY RUNNING agent appends
    between the two are lost. The caller closes that for the current session by
    stopping its container first; for a sibling session it cannot. It requires
    a second live CC turn for the same user in a different chat AND a genuinely
    dirty transcript, and it costs a truncated ``--resume`` tail rather than a
    wrong answer — which is the right side of the trade against leaving a
    plaintext credential in the volume.

    COST, paid on every turn (#76-B). This is a SECOND full pass over the
    user's cc-state tree, and a heavier one than the pass already there:
    ``_session_metas`` reads only the NEWEST jsonl per session, to fingerprint
    it, whereas this reads EVERY jsonl in every session store and rewrites the
    dirty ones. Per-turn I/O therefore scales with the user's total retained
    transcript BYTES, not with their session count — and transcripts are kept
    indefinitely for ``--resume`` with nothing pruning them, so it grows without
    bound for a heavy user.

    Accepted because the alternative is leaving a plaintext credential on disk,
    and because the steady state is read-only: a file that is already clean
    short-circuits at ``clean == raw`` and is never rewritten. It is still real
    work on the turn's critical path. The obvious bounded version — skip a store
    whose manifest already covers every file at its current size and mtime — is
    the next step if this shows up in turn latency.
    """
    root = Path(cc_state_root)
    if not root.is_dir():
        return ScrubReport(0, 0)
    skip_name = Path(exclude).name if exclude is not None else None
    rewritten = 0
    skipped = 0
    try:
        entries = sorted(root.iterdir())
    except OSError as exc:
        logger.warning("cc #76: cannot list cc-state root %s: %r", root, exc)
        return ScrubReport(0, 0)
    for child in entries:
        # Skips the current session, the ".<sid>.scrub.json" manifests that
        # live at this level, and any symlink (which could point out of the
        # user's tree entirely).
        if child.name == skip_name or child.is_symlink() or not child.is_dir():
            continue
        if not (child / _TRANSCRIPT_STORE_DIRNAME).is_dir():
            continue
        report = scrub_transcript_store(child, environment)
        rewritten += report.rewritten
        skipped += report.skipped
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

    # #68: line counts of this chat's transcript store BEFORE the agent is
    # spawned. ``--resume`` appends this turn's records to the SAME session
    # jsonl the previous turns wrote, so this is the only moment at which the
    # boundary between "already stored in an earlier row" and "this turn" can be
    # observed. Taken after the previous turn's finally scrubbed the store,
    # which is safe because the scrub is line-count preserving (see the
    # ``_turn_slice`` block comment).
    #
    # The root expression MUST match the one ``_read_turn_transcript`` resolves:
    # ``_transcript_line_counts`` keys are un-normalised ``str(path)``, so a
    # different spelling misses every lookup and silently degrades every row
    # back to the whole cumulative session.
    pre_turn_lines = _transcript_line_counts(
        Path(dirs.cc_state_mnt) / "projects" if dirs.cc_state_mnt else None)
    # #68: whether a CCSessionTranscript row exists for this turn yet. Bound
    # BEFORE the try because the finally reads it: an exception raised between
    # the try and an in-try assignment would make the finally raise
    # UnboundLocalError and mask the real failure.
    transcript_persisted = False

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
            # Locate + read + scrub (#72) + slice (#68) in one place, shared with
            # the failure fallback in the finally. ``captured.session`` is the
            # whole --resume session file; ``captured.turn`` is only the records
            # THIS turn appended. Both are already scrubbed, so neither sink can
            # write the user's plaintext password to disk or into a DB row.
            captured = _read_turn_transcript(
                dirs.cc_state_mnt,
                turn_start=translator._turn_start_ts,
                prior_lines=pre_turn_lines,
                environment=environment,
            )
            if captured.turn:
                # The SCRUBBED per-TURN slice (not shutil.copy2 of the raw file):
                # this copy is named for run_id, so it must hold that run's
                # records rather than the whole conversation so far.
                _write_raw_turn_copy(dirs.output_mnt, run_id, captured.turn)
            # ...but the trace keeps the FULL session. extract_trace's steps,
            # transcript_line_count and turn_count are conversation-scoped;
            # feeding it the slice would change every Debug-panel trace, which is
            # a separate defect and deliberately out of scope for #68.
            parsed = (cc_summary.parse_transcript(captured.session)
                      if captured.session else None)
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
                        raw_jsonl=captured.turn,
                    ))
                    # #68: only once the write RETURNED. The finally's fallback
                    # capture keys off this flag, so setting it any earlier (or
                    # in the handler below) would skip exactly the turn whose row
                    # is missing.
                    transcript_persisted = True
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
        # #68: the fallback capture, for a turn that did NOT reach the
        # ``query_complete`` gate above — a ``query_error`` result frame, the
        # watchdog timeout (which returns before the gate), or either exception
        # handler. Those three used to leave no CCSessionTranscript row, no
        # ``raw/`` copy and no trace at all, so exactly the turns worth triaging
        # were the only ones with no durable record.
        #
        # ONE fallback here rather than five patched branches: the finally runs
        # on every path (a ``return`` inside the try does not skip it) and this
        # is the only point at which the container is guaranteed stopped, so the
        # read cannot race the agent's own appends.
        #
        # POSITION IS LOAD-BEARING, both ways. After the stop above, for that
        # race. And BEFORE the scrub below, because scrub_transcript_store
        # rewrites every jsonl in the store via os.replace, which stamps them
        # all with a fresh mtime, and _newest_jsonl_under picks by mtime —
        # capturing after the scrub could resolve to a DIFFERENT session's file.
        #
        # Its own try/except: a failure in here must never skip that scrub,
        # which is the #72/#76 security control. The bytes persisted are
        # scrubbed in-process by _read_turn_transcript exactly as the success
        # path's are, so neither sink can carry the user's password even though
        # the on-disk source has not been rewritten yet.
        #
        # Deliberately NOT on_turn_complete: that is _append_cc_turn_complete,
        # which also appends a chat_log entry with status "completed". The
        # service layer's own finally already appends a status "error" entry for
        # this same turn, so calling it would double-log and mislabel a failed
        # turn as completed — and chat_log is what the sticky-CC rule reads, so
        # a failed CC turn would wrongly make the chat sticky. The row and the
        # raw/ copy, nothing else.
        #
        # ``terminal``, not ``event``: ``terminal`` is initialised to None ahead
        # of the try and so is bound on every path, while ``event`` is bound
        # only on the normal-completion path and would raise UnboundLocalError
        # from here on exactly the exception and timeout branches this exists for.
        if not transcript_persisted and chat_session is not None and dirs.cc_state_mnt:
            _terminal_class = terminal[0] if terminal else "<no terminal event>"
            try:
                fallback = _read_turn_transcript(
                    dirs.cc_state_mnt,
                    turn_start=translator._turn_start_ts,
                    prior_lines=pre_turn_lines,
                    environment=environment,
                )
                if not fallback.turn:
                    # Every outcome of this block logs, including the two that
                    # persist nothing: "the fallback ran and found no
                    # transcript" must not be indistinguishable from "the
                    # fallback never ran", or a silent drop here would be a
                    # worse defect than the missing row this exists to fix.
                    logger.warning(
                        "cc #68: no transcript found for a turn that did not "
                        "complete (run_id=%s, terminal=%s); that turn has no "
                        "durable record", run_id, _terminal_class)
                elif not fallback.turn_is_attributable:
                    # The store held records before this turn ran and gained
                    # none, so _turn_slice recovered the WHOLE file. Persisting
                    # it would file the PRIOR turns' transcript under this
                    # run_id — the same misattribution the pre-scrub capture
                    # ordering above guards against, and SPEC.md's "only that
                    # turn's records" is the requirement it would break. No row
                    # is the honest answer; the earlier turns keep their own.
                    logger.warning(
                        "cc #68: not persisting a transcript for a turn that did "
                        "not complete (run_id=%s, terminal=%s): the store gained "
                        "no records this turn, so the only bytes available are "
                        "EARLIER turns' and are not this turn's to claim",
                        run_id, _terminal_class)
                else:
                    _write_raw_turn_copy(dirs.output_mnt, run_id, fallback.turn)
                    cc_transcript_store.store_transcript(
                        chat_session=chat_session,
                        cc_session_id=translator.session_id or "",
                        turn_id=str(run_id),
                        raw_jsonl=fallback.turn,
                    )
                    logger.warning(
                        "cc #68: persisted the transcript of a turn that did not "
                        "complete (run_id=%s, terminal=%s, %d bytes)",
                        run_id, _terminal_class, len(fallback.turn))
            except Exception:  # noqa: BLE001 — must not skip the scrub below
                logger.exception(
                    "cc #68: could NOT persist the transcript of a turn that did "
                    "not complete (run_id=%s, terminal=%s); that turn has no "
                    "durable record", run_id, _terminal_class)
        # #72: scrub the SOURCE transcript, not just the derived copies. In the
        # finally (not the query_complete branch) because a turn that errored,
        # timed out or was budget-killed still leaves a jsonl behind holding
        # whatever the agent echoed before it died — and that file survives for
        # --resume, gets staged read-only into later agents, and is fed to the
        # summarizer. Best-effort: a scrub failure must never fail the turn.
        try:
            if dirs.cc_state_mnt:
                current = Path(dirs.cc_state_mnt)
                report = scrub_transcript_store(current, environment)
                # #76: and every OTHER session store this user owns. This turn
                # holds the only thing that can clean them — the user's own
                # credential — and a session whose own turn died before this
                # finally ran is otherwise never revisited. Same best-effort
                # contract: it must not fail the turn.
                #
                # #76-B: this reads EVERY jsonl the user has retained, not just
                # the newest one per session that _session_metas already reads,
                # so it scales with total transcript bytes and grows without
                # bound (nothing prunes transcripts — --resume needs them). It
                # runs AFTER the reply has been sent, so it costs the user no
                # latency, but it does hold the turn's thread. See the
                # scrub_sibling_transcript_stores docstring for the trade.
                siblings = scrub_sibling_transcript_stores(
                    current.parent, environment, exclude=current)
                total_skipped = report.skipped + siblings.skipped
                if total_skipped:
                    total_files = (total_skipped + report.rewritten
                                   + siblings.rewritten)
                    logger.warning(
                        "cc #72/#76: transcript store scrub left %d of %d file(s) "
                        "UNSCRUBBED (run_id=%s) — those carry no scrub watermark, "
                        "so cc_sweep will SKIP them until a later turn cleans them",
                        total_skipped, total_files, run_id)
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


def _write_raw_turn_copy(output_mnt: str | os.PathLike[str], run_id: object,
                         payload: bytes) -> Path:
    """Write ONE turn's transcript slice to ``<output_mnt>/raw/transcript-<run_id>.jsonl``.

    The on-disk half of the per-turn transcript record, and the sibling of the
    ``CCSessionTranscript`` row. ``run_cc_turn`` writes it from two places — the
    ``query_complete`` path and the #68 fallback in its ``finally`` — and this
    exists so those two cannot drift: they already had the basename check and
    the ``mkdir`` in opposite orders, which is exactly the kind of divergence
    that ends with one path validating and the other not.

    Order here is validate-then-``mkdir``: a name that is about to be rejected
    should not leave a directory behind.

    ``payload`` must ALREADY be scrubbed (#72) — this writes bytes, it does not
    redact them. Raises ``ValueError`` on a path that would land outside
    ``<output_mnt>/raw``, and lets OS errors propagate; both callers wrap it.

    ON THE TWO CHECKS. ``_safe_relpath(raw_copy.name)`` came from both original
    call sites and is kept, but on its own it is VESTIGIAL and must not be read
    as the containment guard: ``Path.name`` is a single component by
    construction and this one always ends ``.jsonl``, so it can never be
    absolute, empty, or ``..``. A ``run_id`` of ``a/b`` would escape into
    ``<output_mnt>/raw/a/`` while presenting a perfectly "safe" basename. The
    parent check is the one that can actually fire, and it is pure path
    arithmetic — no ``resolve()``, no I/O, nothing to fail.

    Neither is reachable today: ``run_cc_turn`` runs ``_validate_user_id(run_id)``
    before any of this, and that rejects ``/`` outright. This is defence in
    depth for the day ``run_id``'s provenance widens.
    """
    raw_dir = Path(output_mnt) / "raw"
    raw_copy = raw_dir / f"transcript-{run_id}.jsonl"
    if not _safe_relpath(raw_copy.name) or raw_copy.parent != raw_dir:
        raise ValueError(f"unsafe transcript path for run_id {run_id!r}")
    raw_copy.parent.mkdir(parents=True, exist_ok=True)
    raw_copy.write_bytes(payload)
    return raw_copy


def _newest_jsonl_under(root: Path, *, min_mtime: float | None = None) -> Path | None:
    """Pick newest *.jsonl under root; if min_mtime set, only files with mtime >= min_mtime."""
    candidates = [p for p in root.rglob("*.jsonl") if p.is_file()]
    if min_mtime is not None:
        candidates = [p for p in candidates if p.stat().st_mtime >= min_mtime]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


# --------------------------------------------------------------------------
# #68 — the per-turn transcript slice.
#
# ``--resume`` appends every turn of a chat to ONE session jsonl, but
# ``CCSessionTranscript`` rows are keyed per turn, so reading the whole file
# into each row makes row N hold turns 1..N and the stored bytes grow
# quadratically in turn count. These three pure helpers let a caller snapshot
# the store's line counts BEFORE spawn and afterwards keep only the records
# this turn appended.
#
# The boundary is a LINE INDEX and not a byte offset, deliberately.
# ``_scrub_secret_bytes`` (#72) replaces each secret with the literal
# ``b"<REDACTED>"``, which contains no newline: the scrub is therefore
# line-count preserving but NOT length preserving. A byte offset recorded
# before the turn is measured against the DIRTY bytes and is invalidated the
# moment the preceding turns' records are scrubbed in place — it would then cut
# mid-record. A line index survives that rewrite untouched.
# --------------------------------------------------------------------------

def _jsonl_line_count(raw: bytes) -> int:
    """Count jsonl records in ``raw`` the way this codebase already counts them.

    Same convention as ``cc_summary.parse_transcript``, and it MUST stay that
    way: split on ``b"\\n"``, drop ONE trailing empty element (the artefact of a
    final newline), keep interior blank/malformed lines. A boundary recorded by
    one convention and applied by the other would be off by a record.
    ``test_cc_transcript_turn_slice.py`` pins the agreement.
    """
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    return len(lines)


def _turn_slice(raw: bytes, prior_lines: int) -> bytes:
    """Return only the records ``raw`` gained after its first ``prior_lines``.

    ``prior_lines`` is the line count of the same session jsonl snapshotted
    before this turn's agent was spawned (see ``_transcript_line_counts``). A
    LINE index rather than a byte offset because ``<REDACTED>`` carries no
    newline, so the #72 in-place scrub preserves line counts but not byte
    offsets — see the block comment above.

    Returns ``raw`` UNCHANGED when ``prior_lines <= 0`` or when the current line
    count is ``<= prior_lines`` (the file shrank, was rewritten, or the turn
    appended nothing). That fallback is deliberate and load-bearing, not
    laziness: it gives the invariant *the slice is empty only if the input is
    empty*, which is what stops a caller from persisting a transcript row with
    an empty blob. Storing too much always beats storing nothing.

    A returned slice always ends in a newline even when ``raw`` did not — a
    killed agent can leave a truncated final line, and the slice is persisted as
    a standalone jsonl blob, so it is normalised rather than propagated. The
    unsliced fallback paths above return ``raw`` byte-for-byte.
    """
    if prior_lines <= 0 or not raw:
        return raw
    lines = raw.split(b"\n")
    if lines and lines[-1] == b"":
        lines.pop()
    if len(lines) <= prior_lines:
        return raw
    return b"\n".join(lines[prior_lines:]) + b"\n"


def _transcript_line_counts(store_root: Path | str | None) -> dict[str, int]:
    """Map ``str(path) -> _jsonl_line_count`` for every ``*.jsonl`` under a store.

    The pre-spawn snapshot whose values later feed ``_turn_slice``'s
    ``prior_lines``.

    KEY SPELLING IS THE CALLER'S, not a normal form. Keys are ``str(path)`` for
    whatever ``root.rglob`` yields, and nothing here calls ``.resolve()``: the
    spelling of ``store_root`` going in is the spelling coming out. The reader
    (``_read_turn_transcript``) looks its own resolved path up in this mapping,
    so the two must build the root from the SAME expression — and normalising on
    one side only would break an agreement that holds today. A key that does not
    match is not an error: it reads as ``prior_lines = 0`` downstream, which
    degrades to storing the whole cumulative session, the safe direction.

    Line counts rather than sizes or offsets for the reason in the block
    comment above: the #72 scrub rewrites these files in place and changes their
    length, but never their line count.

    Total-function on purpose — it runs on the turn's hot path, before the agent
    is even spawned, and must never be the reason a turn fails. Returns ``{}``
    for a falsy root or a path that is not a directory; skips symlinks and
    non-files; skips a file it cannot read rather than raising; and returns
    whatever it had counted so far if the walk itself dies.
    """
    if not store_root:
        return {}
    root = Path(store_root)
    counts: dict[str, int] = {}
    # ``is_dir()`` and ``rglob`` are INSIDE the try, not ahead of it.
    # ``Path.is_dir()`` swallows ENOENT/ENOTDIR/ELOOP but re-raises EACCES, so
    # an unreadable parent directory would otherwise propagate straight past
    # this function's "must never be the reason a turn fails" guarantee; and
    # ``rglob`` is a generator, so anything it raises surfaces at iteration.
    try:
        if not root.is_dir():
            return {}
        for path in root.rglob("*.jsonl"):
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                counts[str(path)] = _jsonl_line_count(path.read_bytes())
            except OSError:
                continue
    except OSError:
        # Partial counts are still correct counts for the files they name, and
        # a file that never got counted just reads as 0 — same safe direction.
        return counts
    return counts


class CapturedTranscript(NamedTuple):
    """One turn's transcript capture, in the two shapes its readers need.

    ``session`` is the WHOLE ``--resume`` session file (scrubbed) and feeds
    ``cc_summary.parse_transcript`` / ``cc_trace.extract_trace``, which count
    turns and steps across the conversation and would report differently off a
    slice. ``turn`` is only the records this turn appended (scrubbed) and feeds
    the two per-TURN-keyed sinks — the ``raw/transcript-<run_id>.jsonl`` copy and
    the ``CCSessionTranscript`` blob — which otherwise store turns 1..N in row N.

    Both are ``b""`` when there was nothing to capture, and by ``_turn_slice``'s
    invariant ``turn`` is empty only when ``session`` is: a caller can gate on
    either without risking a content-free transcript row.

    ``turn_is_attributable`` disambiguates the one case where ``turn`` is a
    RECOVERY rather than a slice. ``_turn_slice`` deliberately returns the whole
    file when the current line count did not exceed the pre-spawn snapshot —
    "storing too much beats storing nothing" — which is right for a turn that
    COMPLETED, where a non-increasing count can only mean a stale snapshot. It is
    wrong for a turn that FAILED, where "the agent appended nothing" is a
    first-class expected state (a spawn that raised before the agent ever wrote):
    there the recovered bytes are the PRIOR turns' records, and persisting them
    under this turn's ``turn_id`` misattributes another turn's transcript.

    So this flag is False exactly when the snapshot saw records for this file
    (``prior_lines > 0``) and the file did not grow. A file the snapshot never
    saw, or saw as empty, reads as ``prior_lines == 0`` — a fresh session, whose
    whole content genuinely IS this turn's — and stays True. It is meaningful
    only when ``turn`` is non-empty; an empty capture is trivially attributable,
    hence the default, which also keeps ``CapturedTranscript(b"", b"")`` a valid
    and equal spelling of "nothing captured".
    """

    session: bytes
    turn: bytes
    turn_is_attributable: bool = True


def _read_turn_transcript(
    cc_state_mnt: str | os.PathLike[str] | None,
    *,
    turn_start: float,
    prior_lines: Mapping[str, int],
    environment: Mapping[str, str],
    attempts: int = 3,
) -> CapturedTranscript:
    """Locate, read, scrub and slice this turn's Claude Code session jsonl.

    The one place that turns "a turn just ran" into bytes worth persisting, so
    the success path and the #68 failure fallback in ``run_cc_turn``'s ``finally``
    cannot drift apart.

    ``turn_start`` is the pre-spawn timestamp; only a jsonl at least that recent
    (minus a second of clock slack, as before) can be this turn's. The agent may
    not have flushed the file by the time the stream ends, hence the retry —
    ``attempts`` tries 0.2 s apart, with NO sleep after the last one.

    ``prior_lines`` is the pre-spawn ``_transcript_line_counts`` snapshot; the
    boundary for this file is ``prior_lines.get(str(resolved_path), 0)``. Those
    keys are NOT normalised, so the ``projects`` root resolved here must be the
    same expression the caller snapshotted — a mismatch silently reads as 0 and
    degrades to storing the whole cumulative session.

    The scrub (#72) runs ONCE over the full file and the slice is taken from the
    scrubbed bytes: ``_scrub_secret_bytes`` is line-count preserving, so slicing
    before or after is equivalent, and this way secrets cannot survive in either
    member for the cost of one pass.

    Total by contract — it runs on the reply path and, from the ``finally``, on
    paths that are already failing. Returns ``CapturedTranscript(b"", b"")``
    rather than raising when ``cc_state_mnt`` is falsy, the ``projects`` dir does
    not exist (turn 1 of a chat), no recent-enough jsonl appears within
    ``attempts``, or ANY of the file I/O fails. That last clause covers the
    DIRECTORY PROBE and the LOCATE step as well as the read: ``Path.is_dir()``
    re-raises ``EACCES`` (it only swallows ENOENT/ENOTDIR/ELOOP), and
    ``_newest_jsonl_under`` calls ``p.stat()`` twice per candidate with no
    handler of its own, so a transcript vanishing between the ``rglob`` and the
    ``stat`` raises ``OSError`` out of the search — and everything past the
    guards below is pure byte work that cannot fail.
    """
    if not cc_state_mnt:
        return CapturedTranscript(b"", b"")
    root = Path(cc_state_mnt) / "projects"

    # The LOCATE step is inside the try, not only the read: the store is live —
    # the agent, a concurrent sweep or a sibling turn can unlink a jsonl between
    # the rglob and the stat — and this helper is called from run_cc_turn's
    # finally, where an escape would skip the #72/#76 scrub that follows it.
    #
    # ``root.is_dir()`` is inside it too, for the same reason it is inside
    # ``_transcript_line_counts``': it swallows ENOENT/ENOTDIR/ELOOP but
    # RE-RAISES EACCES, so an unreadable parent directory would escape a
    # function whose docstring promises to be total. Both callers happen to
    # wrap this today, but on the SUCCESS path that escape costs the user the
    # reply they had already earned.
    try:
        # Checked up front rather than left to rglob: on turn 1 the store does
        # not exist yet, and retrying an absent directory would spend the
        # back-off budget as pure latency on the user's reply.
        if not root.is_dir():
            return CapturedTranscript(b"", b"")
        jsonl_path = None
        for attempt in range(attempts):
            jsonl_path = _newest_jsonl_under(root, min_mtime=turn_start - 1)
            if jsonl_path:
                break
            if attempt < attempts - 1:
                time.sleep(0.2)
        if not jsonl_path:
            return CapturedTranscript(b"", b"")
        raw = jsonl_path.read_bytes()
    except OSError:
        logger.warning("cc #68: could not read this turn's transcript under %s",
                       root, exc_info=True)
        return CapturedTranscript(b"", b"")

    session = _scrub_secret_bytes(raw, environment)
    prior = prior_lines.get(str(jsonl_path), 0)
    return CapturedTranscript(
        session=session,
        turn=_turn_slice(session, prior),
        # The slice is a genuine slice unless _turn_slice fell back to the whole
        # file on a store that had records before this turn ran. See the
        # CapturedTranscript docstring for why the failure-path caller cannot
        # treat that recovery as this turn's records.
        turn_is_attributable=(prior <= 0
                              or _jsonl_line_count(session) > prior),
    )


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

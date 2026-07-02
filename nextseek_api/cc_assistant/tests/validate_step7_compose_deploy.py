"""Zero-spend, reproducible validator for the Step 7 (compose-native deploy)
evidence bundle.

PLAN-7 Task 1 implemented the preflight/step3_deploy_gate anti-staleness
checks (``check_preflight_json_present`` .. ``check_live_gate_transcript_committed``
below). PLAN-7 Task 2 (this extension) adds every remaining check named in
its own task brief's dense "Step 2: Implement the validator" paragraph,
covering the rest of the SPEC-7 section 8 evidence contract: the
deploy_commit/meta.json cross-check, the live-transcript content-marker
allowlist (byte-identical with PLAN-3 Task 13 Step 8), the MBP greenfield
pre-bootstrap volume/network scans, the locked ``host_label`` enum, the
independent Docker Engine/API/Compose subpath-floor re-parse, compose
topology + forced-CC success/cost/correlation checks, the dev-only
``migration_policy`` conditionality, the paired pre-turn-seed /
in-turn-isolation cross-user-leak oracle, legacy-filename rejection, and the
secret scan + screenshot-review gate.

Validator independence from the collector (``step7_preflight_collector.py``)
is deliberate: constants here (e.g. the Docker Engine/Compose subpath floors)
are re-declared rather than imported, so a compromised/buggy collector cannot
silently relax what the validator enforces.

    python -m nextseek_api.cc_assistant.tests.validate_step7_compose_deploy <run_dir> [repo_root]
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from nextseek_api.cc_assistant.tests.validate_cc_acceptance import (
    LEAK_MARKERS as _CC_LEAK_MARKERS,
    OPUS as _CC_OPUS_MODEL_ID,
    SHARED_CRED_KEYS as _CC_SHARED_CRED_KEYS,
    is_dmac_cc_net_closed_set_member as _cc_net_closed_set_member,
    matrix_executor_name as _matrix_executor_name,
)

try:
    # Authoritative six SEEK volume base names (do not invent names here).
    # Task 6 extends the set with "dmac-cc-users" -- EXPECTED_VOLUME_BASE_NAMES
    # below is the module constant Task 6 may further extend.
    from startup.steps.volumes import REQUIRED_VOLUMES as _SEEK_REQUIRED_VOLUMES
except ImportError:  # pragma: no cover - defensive; repo_root always has startup/
    _SEEK_REQUIRED_VOLUMES = [
        "seek-filestore", "seek-mysql-db", "seek-solr-data",
        "seek-cache", "nextseek-static-files", "neo4j-data",
    ]

# Matches typical MBP host_label spellings: "taishajo-mbp", "MBP.local",
# "mbp-taishajo", "MacBook-Pro", "MacBookPro16,1", etc. Used ONLY for the
# narrow Task-1 in-bundle tracker-snapshot exception below -- NOT the same
# thing as the Task-2 locked host_label enum (HOST_LABEL_VALID), which
# requires the *exact* literal "mbp" (see check_host_label_enum_valid).
MBP_HOST_LABEL_RE = re.compile(r"\bmbp\b|macbook[\s_-]*pro", re.IGNORECASE)

MBP_SNAPSHOT_BASENAME = "integration_plan_snapshot.json"

# The single accepted live-evidence dir (PLAN-3 Task 13 Step 9; user decision
# 2026-06-30: handoff-only fallback rejected).
LIVE_EVIDENCE_PATH_LITERAL = "nextseek_api/cc_assistant/evidence/3-ui-based-io-live/"
LIVE_GATE_TRANSCRIPT_REL = LIVE_EVIDENCE_PATH_LITERAL + "live_gate_transcript.txt"

REQUIRED_FILE_HASH_KEYS = ("docker-compose.yml", "docker/nextseek.env.example", "DEPLOY.md")

REQUIRED_GATE_KEYS = (
    "integration_plan_path",
    "tracker_step3_status",
    "integration_plan_sha256",
    "canonical_integration_plan_sha256",
    "live_gate_transcript_committed",
    "deploy_commit",
    "user_signoff_handoff_path",
    "live_evidence_path",
    "pre_step3_snapshot_tag",
    "docker_engine_meets_subpath_floor",
    "docker_compose_meets_subpath_floor",
    "port_source_path",
    "port_source_commit",
    "had_host_bind_data",
)

# --- Task 2 constants --------------------------------------------------------

DEPLOY_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")

# Byte-identical allowlist, shared verbatim with PLAN-3 Task 13 Step 8 (both
# sides MUST name the same strings -- see the Task 2 brief). Do NOT require
# the command substrings ("migrate nextseek_api 0007", "inspect registered")
# or an "exit-code" substring: PLAN-3 Task 13 Step 8 is contracted only to
# capture stdout/stderr + exit codes, not echoed command lines.
TRANSCRIPT_MIGRATION_MARKER_RE = re.compile(
    r"Applying nextseek_api\.0007|\[X\] 0007_ccsessiontranscript"
)
TRANSCRIPT_CC_UPLOAD_MARKER = "cc_assistant.upload"
TRANSCRIPT_CC_TRACES_MARKER = "cc_traces"

# `host_label` locked enum (SPEC-7 section 8 / Task 2 brief): exact string
# match, not regex. "mbp" is the ONLY accepted spelling of the MBP
# authoritative gate.
HOST_LABEL_VALID = ("mbp", "dev-vm", "nextseek-dev")

# The six SEEK volume base names (read from startup/steps/volumes.py,
# authoritative) plus "dmac-cc-users" (G7-10). A plain module-level list so
# Task 6 can extend it (e.g. append further per-user volumes) without
# touching check logic.
EXPECTED_VOLUME_BASE_NAMES: list[str] = list(_SEEK_REQUIRED_VOLUMES)
if "dmac-cc-users" not in EXPECTED_VOLUME_BASE_NAMES:
    EXPECTED_VOLUME_BASE_NAMES.append("dmac-cc-users")

MBP_REQUIRED_NETWORK = "dmac-cc-net"

# Real, unconditional Docker Engine / API floor (docker-py Engine API
# VolumeOptions.Subpath gates the per-user isolation mount -- NOT compose
# YAML). Deliberately re-declared here independent of the collector's
# DOCKER_ENGINE_SUBPATH_FLOOR constant.
ENGINE_SUBPATH_FLOOR = (26, 0, 0)
API_SUBPATH_FLOOR = (1, 45)
# Compose plugin floor is CONDITIONAL: only enforced when compose_config.json
# shows the compose YAML itself using `subpath:` syntax (this plan mounts the
# whole dmac-cc-users volume with no YAML subpath syntax -- Task 5/6).
COMPOSE_SUBPATH_FLOOR = (2, 26, 0)

_ENGINE_SECTION_VERSION_RE = re.compile(r"Engine:\s*\n\s*Version:\s*(\d+)\.(\d+)(?:\.(\d+))?", re.IGNORECASE)
_GENERIC_VERSION_RE = re.compile(r"Version:\s*(\d+)\.(\d+)(?:\.(\d+))?", re.IGNORECASE)
_CLIENT_VERSION_RE = re.compile(r"Docker version (\d+)\.(\d+)(?:\.(\d+))?", re.IGNORECASE)
_API_VERSION_RE = re.compile(r"API version:\s*(\d+)\.(\d+)", re.IGNORECASE)
_COMPOSE_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")

DEFAULT_BUDGET_CAP_USD = 2.0

# The pinned isolation-scan foreign-token grep pattern (SPEC-7 amend
# 2026-06-30 / Task 2 brief): applied line-wise as the Python equivalent of
# `grep -nE 'SENTINEL_FOREIGN|(^| |/)otherproj(/|$| )|(^| |/)bob(/|$| )'`. The
# canonical *values* of the foreign tokens come from meta.json.foreign_tokens
# (used by the pre-turn seed-scan check below); this compiled pattern is the
# pinned detector for that canonical set, not derived from meta.json.
FOREIGN_TOKEN_GREP_RE = re.compile(r"SENTINEL_FOREIGN|(^| |/)otherproj(/|$| )|(^| |/)bob(/|$| )")

# The canonical foreign-token set (SPEC-7 amend 2026-06-30 / Task 2 brief):
# the exact set of tokens FOREIGN_TOKEN_GREP_RE is pinned to detect. This is
# the *values* counterpart of that compiled regex; meta.json.foreign_tokens
# MUST equal this set exactly (see check_foreign_tokens_canonical_set) --
# otherwise the pre-turn seed-scan oracle only proves presence of whatever a
# harness chose to seed, while a real leak of the actual pinned tokens stays
# invisible to the in-turn (regex-based) detector.
CANONICAL_FOREIGN_TOKENS = frozenset({"SENTINEL_FOREIGN", "otherproj", "bob"})

# Legacy (pre-SPEC-7) evidence filenames that must never appear anywhere in a
# Step 7 bundle -- their SPEC-7-named replacements are forced_cc_result.json,
# proxy_log_window.txt, network_inspect.json.
LEGACY_ARTIFACT_BASENAMES = ("forced_result.json", "proxy_log.txt", "network.json")

_SCREENSHOT_SUFFIXES = (".png", ".jpg", ".jpeg")

# SPEC-7 section 9 secret-scan negative controls.
_SECRET_KEY_ASSIGN_MARKERS = ("AWS_BEARER_TOKEN_BEDROCK", "GCP_API_KEY", "MYSQL_PASSWORD", "NEO4J_PASSWORD")
_SECRET_LITERAL_MARKERS = ("Authorization: Bearer", "ABSK", "demopassword")
_DJANGO_SECRET_KEY_RE = re.compile(r"SECRET_KEY\s*=\s*['\"]?[A-Za-z0-9+/=_\-]{16,}")
_NEXTSEEK_PASSWORD_UNREDACTED_RE = re.compile(
    r"NEXTSEEK_PASSWORD\s*=\s*(?!\*{3,}\b|REDACTED\b|<redacted>|\[redacted\])\S+", re.IGNORECASE
)

# Task 16 debt fix: pinned to the single allowed Bedrock model id (the
# bedrock-proxy allowlist -- docker/bedrock-proxy/app/config.py's
# `_DEFAULT_ALLOWED_MODELS`, mirrored here as `_CC_OPUS_MODEL_ID` /
# `validate_cc_acceptance.OPUS`), NOT a `\S+` wildcard that would accept a
# proxy invoke-200 for ANY model id. A generic wildcard here would pass even
# when the proxy relayed a call to a model the allowlist never authorized --
# defeating the point of pinning the proxy to exactly one model.
_INVOKE_200_OPUS_RE = re.compile(
    r"POST\s+/model/" + re.escape(_CC_OPUS_MODEL_ID) + r"/invoke(?:-with-response-stream)?\b[^\n]*?->\s*200"
)

# --- Task 15 (G7-11 capability gate) constants ------------------------------

# The exact 9 bin command names -- `plugin_ops_matrix.json` keys are exactly
# these (iter-1 L-3: three name spaces exist -- bin name, wire-op name, and
# the assistant-viewset URL segment -- the matrix is keyed on BIN names).
BIN_OPS: tuple[str, ...] = (
    "nextseek-entity-extract",
    "nextseek-parse",
    "nextseek-api-read",
    "nextseek-api-write",
    "nextseek-graph",
    "nextseek-report",
    "nextseek-generate-submission",
    "nextseek-query",
    "nextseek-plan",
)

# bin name -> wire-op name (docker/cc-runtime/.../_nextseek_runner.py's
# --agent value / the sidecar op name); recorded for readability, not used to
# derive the endpoint below (two bins deliberately share one endpoint).
BIN_TO_WIRE_OP: dict[str, str] = {
    "nextseek-entity-extract": "entity",
    "nextseek-parse": "parse",
    "nextseek-api-read": "api-read",
    "nextseek-api-write": "api-write",
    "nextseek-graph": "graph",
    "nextseek-report": "report",
    "nextseek-generate-submission": "generate-submission",
    "nextseek-query": "query",
    "nextseek-plan": "plan",
}

# bin name -> the NExtSEEK assistant-viewset endpoint it traverses (derived
# from docker/ns-sidecar/app/ns_client.py's ``POST
# /nextseek_api/assistant/{op}/`` for the 7 sidecar ops, and
# docker/cc-runtime/.../_assistant_client.py's ``POST .../query/async/`` for
# the 2 viewset ops -- iter-3 L-4). ``nextseek-query`` and ``nextseek-plan``
# deliberately map to the SAME literal endpoint: the access-log hit check
# below is endpoint-keyed (not op-keyed), so this pair is evaluated as ONE
# shared hit requirement, never double-counted.
OP_ASSISTANT_ENDPOINT: dict[str, str] = {
    "nextseek-entity-extract": "/nextseek_api/assistant/entity/",
    "nextseek-parse": "/nextseek_api/assistant/parse/",
    "nextseek-api-read": "/nextseek_api/assistant/api-read/",
    "nextseek-api-write": "/nextseek_api/assistant/api-write/",
    "nextseek-graph": "/nextseek_api/assistant/graph/",
    "nextseek-report": "/nextseek_api/assistant/report/",
    "nextseek-generate-submission": "/nextseek_api/assistant/generate-submission/",
    "nextseek-query": "/nextseek_api/assistant/query/async/",
    "nextseek-plan": "/nextseek_api/assistant/query/async/",
}

# Per-op top-level response-field allowlist (anti-fabrication -- iter-1 M-4 /
# iter-2 R2-M4): the 7 sidecar ops echo the AssistantViewSet's
# ``{op, result, download?}`` envelope (nextseek_api/assistant/models_api.py
# *OpResponse models, all ``extra="forbid"`` at the top level); the 2 viewset
# ops echo the runner's ``{reply, debug, bundle_id}`` shape
# (_nextseek_runner.py's ``_run_viewset``). A recorded excerpt with a
# top-level field outside this set cannot have come from the real server.
OP_EXCERPT_ALLOWED_FIELDS: dict[str, frozenset[str]] = {
    "nextseek-entity-extract": frozenset({"op", "result"}),
    "nextseek-parse": frozenset({"op", "result"}),
    "nextseek-graph": frozenset({"op", "result"}),
    "nextseek-api-read": frozenset({"op", "result"}),
    "nextseek-api-write": frozenset({"op", "result"}),
    "nextseek-report": frozenset({"op", "result", "download"}),
    "nextseek-generate-submission": frozenset({"op", "result", "download"}),
    "nextseek-query": frozenset({"reply", "debug", "bundle_id"}),
    "nextseek-plan": frozenset({"reply", "debug", "bundle_id"}),
}

# The two ops whose row must additionally carry `published_path` (Sweep
# cross-check, iter-1 H-1 / iter-2 R2-M4).
PUBLISHED_PATH_OPS: tuple[str, ...] = ("nextseek-report", "nextseek-generate-submission")

REQUIRED_MATRIX_ROW_KEYS: tuple[str, ...] = (
    "op", "transport", "exit_code", "excerpt",
    "container_id", "container_name", "image", "wall_secs",
)

# images.json key the harness records the CC agent image tag under (mirrors
# the other images.json keys, which are compose SERVICE names -- "nextseek",
# "bedrock-proxy" -- so the CC agent's compose service name "cc-agent" is
# used here too).
IMAGES_JSON_CC_IMAGE_KEY = "cc-agent"

# Exit-code law (iter-1 M-4 / SPEC-7 section 8's plugin_ops_matrix.json
# paragraph): 7 == TRANSPORT_ERROR (missing backend, the amendment's
# defining failure) always fails; the ONLY other acceptable nonzero exit is
# the pinned Layer-2 write-blocked form (exit 5, stderr code WRITE_BLOCKED,
# op == nextseek-api-write only -- no other op can legitimately produce it).
TRANSPORT_ERROR_EXIT = 7
WRITE_BLOCKED_EXIT = 5
WRITE_BLOCKED_OP = "nextseek-api-write"
WRITE_BLOCKED_STDERR_MARKER = "WRITE_BLOCKED"

# The documented in-turn headroom constant (iter-3 M-2 / SPEC-7 section 8):
# 150s = cc_engine._TIMEOUT_HARD_MAX (180) minus boot/prompt slack (30s).
# Re-declared here independent of cc_engine per this validator's existing
# independence discipline (see module docstring) -- DO NOT import
# _TIMEOUT_HARD_MAX and subtract inline; this is the one place the 150s
# figure is pinned for the in_turn_viable evaluation.
IN_TURN_HEADROOM_SECS = 150


def _try_load_json(p: Path) -> dict | None:
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


def _try_load_json_any(p: Path) -> Any:
    """Like _try_load_json but accepts any JSON top-level shape (list/dict/…)."""
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_network_inspect_containers(p: Path) -> dict[str, dict] | None:
    """Parse a ``docker network inspect <net>`` capture (Task 15 / SPEC-7
    section 8: "the FULL `docker network inspect` JSON, containers keyed by
    ID with `Name` fields"). Accepts the real CLI shape (a JSON array whose
    first element carries ``"Containers": {<id>: {"Name": ..., ...}, ...}``)
    and, defensively, a bare ``{"Containers": {...}}`` object. Fail-closed
    (returns None) on any other shape -- including the pre-Task-15
    ``{"containers": [<name>, ...]}`` list-of-strings shape, which is no
    longer accepted."""
    obj = _try_load_json_any(p)
    if isinstance(obj, list):
        if not obj or not isinstance(obj[0], dict):
            return None
        containers = obj[0].get("Containers")
    elif isinstance(obj, dict):
        containers = obj.get("Containers")
    else:
        return None
    return containers if isinstance(containers, dict) else None


def _network_inspect_names(p: Path) -> set[str] | None:
    """The set of container ``Name`` values present in a network-inspect
    capture, or None if the artifact is missing/unreadable/malformed."""
    containers = _load_network_inspect_containers(p)
    if containers is None:
        return None
    return {
        rec.get("Name") for rec in containers.values()
        if isinstance(rec, dict) and isinstance(rec.get("Name"), str) and rec.get("Name")
    }


def _sha256_file(p: Path) -> str | None:
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest()
    except OSError:
        return None


def _git_blob_size(repo_root: Path, commit: str, rel_path: str) -> int | None:
    """Size in bytes of ``<commit>:<rel_path>`` per `git cat-file -s`, or None
    on any git failure (nonexistent commit, path absent at that commit, not a
    git repo). `-s` (not `-e`) so a committed zero-byte blob is detectable."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "-s", f"{commit}:{rel_path}"],
            capture_output=True, text=True, check=True,
        )
        return int(proc.stdout.strip())
    except (subprocess.CalledProcessError, OSError, ValueError):
        return None


def _git_blob_text(repo_root: Path, commit: str, rel_path: str) -> str | None:
    """Contents of ``<commit>:<rel_path>`` per `git cat-file -p`, or None on
    any git failure. Used ONLY to independently re-verify the live-gate
    transcript's content markers -- never trusts a hand-edited copy."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo_root), "cat-file", "-p", f"{commit}:{rel_path}"],
            capture_output=True, text=True, check=True,
        )
        return proc.stdout
    except (subprocess.CalledProcessError, OSError):
        return None


def _load_instance_prefix(repo_root: Path) -> str:
    """Read startup/.instance.json's "prefix" field from the repo under test,
    defaulting to "" (bare volume names) when absent/unreadable."""
    obj = _try_load_json(repo_root / "startup" / ".instance.json")
    if obj is None:
        return ""
    return str(obj.get("prefix") or "")


def _parse_engine_version(text: str) -> tuple[int, int, int] | None:
    m = _ENGINE_SECTION_VERSION_RE.search(text) or _GENERIC_VERSION_RE.search(text) or _CLIENT_VERSION_RE.search(text)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def _parse_api_version(text: str) -> tuple[int, int] | None:
    m = _API_VERSION_RE.search(text)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def _parse_compose_version(text: str) -> tuple[int, int, int] | None:
    m = _COMPOSE_VERSION_RE.search(text)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def _compose_uses_subpath_syntax(obj: Any) -> bool:
    """Recursively search parsed compose_config.json for a `subpath:` key
    (YAML long-form bind/volume subpath syntax). Whole-volume mounts with no
    subpath key (this plan's dmac-cc-users mount) do NOT trigger this."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str) and k.lower() == "subpath":
                return True
            if _compose_uses_subpath_syntax(v):
                return True
    elif isinstance(obj, list):
        return any(_compose_uses_subpath_syntax(v) for v in obj)
    return False


def _find_key_anywhere(obj: Any, key: str) -> Any:
    """Recursively search a nested dict/list structure for the first value of
    a dict key named `key`. Returns None if not found."""
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for v in obj.values():
            found = _find_key_anywhere(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _find_key_anywhere(v, key)
            if found is not None:
                return found
    return None


@dataclass(frozen=True)
class Context:
    run_dir: Path
    preflight: dict[str, Any] | None
    meta: dict[str, Any]
    repo_root: Path
    # Independently re-fetched (via git, not trusted from any file in the
    # bundle) content of live_gate_transcript.txt at deploy_commit, computed
    # once in validate_run and shared by the three transcript-marker checks.
    transcript_text: str | None = None


def _tracker_step3_status(tracker: dict) -> str | None:
    for step in tracker.get("steps", []) if isinstance(tracker, dict) else []:
        if str(step.get("id")) == "3":
            return step.get("status")
    return None


def _resolve_tracker_source(ctx: Context) -> tuple[Path | None, dict | None, str]:
    """Resolve+validate where the step-3 tracker truth must be re-read from.

    Returns (path, parsed_tracker_dict, detail). path/parsed are None on any
    rejection (arbitrary in-bundle file, failed MBP conditions, unreadable).
    """
    gate = (ctx.preflight or {}).get("step3_deploy_gate") or {}
    raw_path = gate.get("integration_plan_path")
    if not raw_path:
        return None, None, "no integration_plan_path recorded in step3_deploy_gate"

    p = Path(raw_path)
    run_dir = ctx.run_dir.resolve()
    try:
        resolved = p.resolve()
        # Is p located AT or UNDER run_dir (i.e. shipped inside the bundle)?
        in_bundle = resolved == run_dir or str(resolved).startswith(str(run_dir) + "/")
    except OSError:
        in_bundle = False

    if not in_bundle:
        tracker = _try_load_json(p)
        if tracker is None:
            return None, None, f"integration_plan_path {p} is unreadable/not valid JSON"
        return p, tracker, "integration_plan_path resolved outside the evidence bundle (normal case)"

    # Path points inside the bundle: only the MBP exception may allow this.
    host_label = str(ctx.meta.get("host_label", ""))
    if not MBP_HOST_LABEL_RE.search(host_label):
        return None, None, (
            f"integration_plan_path {p} points inside the evidence bundle but "
            f"host_label {host_label!r} does not match the MBP exception "
            f"(arbitrary in-bundle tracker files are rejected)"
        )

    if p.name != MBP_SNAPSHOT_BASENAME or p.resolve() != (run_dir / MBP_SNAPSHOT_BASENAME):
        return None, None, (
            f"MBP exception requires integration_plan_path == "
            f"<run_dir>/{MBP_SNAPSHOT_BASENAME} exactly; got {p}"
        )

    canonical = gate.get("canonical_integration_plan_sha256")
    if not canonical:
        return None, None, "MBP exception requires canonical_integration_plan_sha256 to be recorded"

    recorded_sha256 = gate.get("integration_plan_sha256")
    actual_sha256 = _sha256_file(p)
    if actual_sha256 is None:
        return None, None, f"MBP snapshot {p} is unreadable"
    if actual_sha256 != recorded_sha256:
        return None, None, (
            f"MBP snapshot sha256 mismatch: file={actual_sha256} "
            f"recorded integration_plan_sha256={recorded_sha256} (tampered/stale snapshot)"
        )

    tracker = _try_load_json(p)
    if tracker is None:
        return None, None, f"MBP snapshot {p} is not valid JSON"
    return p, tracker, f"MBP exception satisfied: snapshot sha256 matches integration_plan_sha256 ({actual_sha256})"


# --- Composable checks (Task 1: preflight / step3_deploy_gate) --------------

def check_preflight_json_present(ctx: Context) -> tuple[str, bool, str]:
    ok = ctx.preflight is not None
    return "preflight_json_present", ok, ("" if ok else "preflight.json missing, unreadable, or not a JSON object")


def check_branch_and_commit_recorded(ctx: Context) -> tuple[str, bool, str]:
    pf = ctx.preflight or {}
    branch = pf.get("branch")
    commit = pf.get("commit")
    ok = bool(branch) and isinstance(commit, str) and len(commit) >= 7
    return "branch_and_commit_recorded", ok, f"branch={branch!r} commit={commit!r}"


def check_required_file_hashes_present(ctx: Context) -> tuple[str, bool, str]:
    pf = ctx.preflight or {}
    hashes = pf.get("file_hashes")
    if not isinstance(hashes, dict):
        return "required_file_hashes_present", False, "file_hashes missing or not an object"
    missing = [
        k for k in REQUIRED_FILE_HASH_KEYS
        if not isinstance(hashes.get(k), dict) or not hashes[k].get("sha256")
    ]
    ok = not missing
    detail = "all required file hashes present" if ok else f"missing/empty hashes for: {missing}"
    return "required_file_hashes_present", ok, detail


def check_step3_gate_fields_present(ctx: Context) -> tuple[str, bool, str]:
    pf = ctx.preflight or {}
    gate = pf.get("step3_deploy_gate")
    if not isinstance(gate, dict):
        return "step3_gate_fields_present", False, "step3_deploy_gate missing or not an object"
    missing = [k for k in REQUIRED_GATE_KEYS if k not in gate]
    ok = not missing
    detail = "all step3_deploy_gate fields present" if ok else f"missing gate fields: {missing}"
    return "step3_gate_fields_present", ok, detail


def check_tracker_path_not_arbitrary(ctx: Context) -> tuple[str, bool, str]:
    if not isinstance((ctx.preflight or {}).get("step3_deploy_gate"), dict):
        return "tracker_path_not_arbitrary", False, "no step3_deploy_gate to resolve a tracker path from"
    path, tracker, detail = _resolve_tracker_source(ctx)
    return "tracker_path_not_arbitrary", tracker is not None, detail


def check_tracker_step3_done(ctx: Context) -> tuple[str, bool, str]:
    if not isinstance((ctx.preflight or {}).get("step3_deploy_gate"), dict):
        return "tracker_step3_done", False, "no step3_deploy_gate to re-read tracker status from"
    path, tracker, detail = _resolve_tracker_source(ctx)
    if tracker is None:
        return "tracker_step3_done", False, f"could not re-read tracker: {detail}"
    status = _tracker_step3_status(tracker)
    ok = status == "done"
    return "tracker_step3_done", ok, f"tracker step 3 status (re-read at {path}) = {status!r}"


def check_live_evidence_path_literal(ctx: Context) -> tuple[str, bool, str]:
    pf = ctx.preflight or {}
    gate = pf.get("step3_deploy_gate")
    if not isinstance(gate, dict):
        return "live_evidence_path_literal", False, "no step3_deploy_gate to read live_evidence_path from"
    val = gate.get("live_evidence_path")
    ok = val == LIVE_EVIDENCE_PATH_LITERAL
    return "live_evidence_path_literal", ok, (
        f"live_evidence_path={val!r}" + ("" if ok else f" (must be exactly {LIVE_EVIDENCE_PATH_LITERAL!r})")
    )


def check_live_gate_transcript_committed(ctx: Context) -> tuple[str, bool, str]:
    """The recorded bool must be the literal True AND git must agree, re-checked
    independently at validation time: `git cat-file -s <deploy_commit>:<transcript>`
    must succeed with size > 0. A hand-edited preflight.json claiming true
    cannot pass this — the git re-check is authoritative."""
    pf = ctx.preflight or {}
    gate = pf.get("step3_deploy_gate")
    if not isinstance(gate, dict) or "live_gate_transcript_committed" not in gate:
        return "live_gate_transcript_committed", False, "live_gate_transcript_committed not recorded"

    val = gate["live_gate_transcript_committed"]
    if val is not True:  # must be the bool True, not a truthy string/1/etc.
        return "live_gate_transcript_committed", False, (
            f"recorded value={val!r} (type={type(val).__name__}); must be the bool true"
        )

    deploy_commit = gate.get("deploy_commit")
    if not deploy_commit or not isinstance(deploy_commit, str):
        return "live_gate_transcript_committed", False, (
            f"cannot re-verify transcript: deploy_commit={deploy_commit!r}"
        )

    size = _git_blob_size(ctx.repo_root, deploy_commit, LIVE_GATE_TRANSCRIPT_REL)
    if size is None:
        return "live_gate_transcript_committed", False, (
            f"recorded true but git disagrees: {LIVE_GATE_TRANSCRIPT_REL} not found at "
            f"{deploy_commit} in {ctx.repo_root} (missing path, unknown commit, or not a repo)"
        )
    if size == 0:
        return "live_gate_transcript_committed", False, (
            f"transcript committed at {deploy_commit} but EMPTY (0 bytes) — not acceptable evidence"
        )
    return "live_gate_transcript_committed", True, (
        f"git re-verified: {size} bytes at {deploy_commit}:{LIVE_GATE_TRANSCRIPT_REL}"
    )


# --- Composable checks (Task 2) ---------------------------------------------

def check_deploy_commit_format_valid(ctx: Context) -> tuple[str, bool, str]:
    """Enforced BEFORE any git re-check: a malformed deploy_commit must not be
    handed to git at all -- it must fail this dedicated format check."""
    gate = (ctx.preflight or {}).get("step3_deploy_gate") or {}
    val = gate.get("deploy_commit")
    ok = isinstance(val, str) and bool(DEPLOY_COMMIT_RE.match(val))
    return "deploy_commit_format_valid", ok, f"deploy_commit={val!r} (must match ^[0-9a-f]{{40}}$)"


def check_deploy_commit_matches_meta_repo_commit(ctx: Context) -> tuple[str, bool, str]:
    """preflight.deploy_commit == meta.json.repo_commit -- both must have
    been collected in the same <run_id> bundle (Task 9/10 regenerate
    preflight immediately before other section-8 artifacts)."""
    gate = (ctx.preflight or {}).get("step3_deploy_gate") or {}
    deploy_commit = gate.get("deploy_commit")
    repo_commit = ctx.meta.get("repo_commit")
    ok = bool(deploy_commit) and deploy_commit == repo_commit
    return "deploy_commit_matches_meta_repo_commit", ok, (
        f"preflight.deploy_commit={deploy_commit!r} meta.json.repo_commit={repo_commit!r}"
    )


def check_transcript_migration_marker_present(ctx: Context) -> tuple[str, bool, str]:
    text = ctx.transcript_text
    if not text:
        return "transcript_migration_marker_present", False, "transcript content unavailable (invalid deploy_commit / uncommitted / unreadable)"
    ok = bool(TRANSCRIPT_MIGRATION_MARKER_RE.search(text))
    return "transcript_migration_marker_present", ok, (
        "migration marker found" if ok else
        "neither 'Applying nextseek_api.0007' nor '[X] 0007_ccsessiontranscript' found in committed transcript"
    )


def check_transcript_cc_upload_marker_present(ctx: Context) -> tuple[str, bool, str]:
    text = ctx.transcript_text
    if not text:
        return "transcript_cc_upload_marker_present", False, "transcript content unavailable"
    ok = TRANSCRIPT_CC_UPLOAD_MARKER in text
    return "transcript_cc_upload_marker_present", ok, f"{TRANSCRIPT_CC_UPLOAD_MARKER!r} in transcript={ok}"


def check_transcript_cc_traces_marker_present(ctx: Context) -> tuple[str, bool, str]:
    text = ctx.transcript_text
    if not text:
        return "transcript_cc_traces_marker_present", False, "transcript content unavailable"
    ok = TRANSCRIPT_CC_TRACES_MARKER in text
    return "transcript_cc_traces_marker_present", ok, f"{TRANSCRIPT_CC_TRACES_MARKER!r} in transcript={ok}"


def check_supplementary_handoff_valid(ctx: Context) -> tuple[str, bool, str]:
    """user_signoff_handoff_path is supplementary (NOT a substitute for the
    committed transcript). If recorded, it must parse as an SRS report
    (report_meta.schema_version present) and either cite step3_status=="done"
    or record the same integration_plan_sha256 as the gate."""
    gate = (ctx.preflight or {}).get("step3_deploy_gate") or {}
    path_str = gate.get("user_signoff_handoff_path")
    name = "supplementary_handoff_valid"
    if not path_str:
        return name, True, "no supplementary handoff recorded (transcript commit is the hard gate)"

    p = Path(path_str)
    if not p.is_absolute():
        p = ctx.repo_root / p
    obj = _try_load_json(p)
    if obj is None:
        return name, False, f"user_signoff_handoff_path {path_str!r} unreadable/not valid JSON"

    report_meta = obj.get("report_meta")
    schema_version = report_meta.get("schema_version") if isinstance(report_meta, dict) else None
    if not schema_version or "handoff" not in str(schema_version):
        return name, False, f"handoff JSON missing SRS report_meta.schema_version (got {schema_version!r})"

    step3_status = _find_key_anywhere(obj, "step3_status")
    cites_done = step3_status == "done"
    recorded_sha = _find_key_anywhere(obj, "integration_plan_sha256")
    expected_sha = gate.get("integration_plan_sha256")
    matches_sha = bool(expected_sha) and recorded_sha == expected_sha
    ok = cites_done or matches_sha
    return name, ok, f"cites_step3_done={cites_done} matches_integration_plan_sha256={matches_sha}"


def check_host_label_enum_valid(ctx: Context) -> tuple[str, bool, str]:
    val = ctx.meta.get("host_label")
    ok = val in HOST_LABEL_VALID
    return "host_label_enum_valid", ok, f"host_label={val!r} (must be exactly one of {HOST_LABEL_VALID})"


def _parse_volume_ls_names(text: str) -> set[str]:
    """`docker volume ls` columns are ``DRIVER  VOLUME NAME`` -- the name is
    the LAST whitespace-separated column."""
    names: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.upper().startswith("DRIVER"):
            continue
        parts = line.split()
        if parts:
            names.add(parts[-1])
    return names


def _parse_network_ls_names(text: str) -> set[str]:
    """`docker network ls` columns are ``NETWORK ID  NAME  DRIVER  SCOPE`` --
    unlike `docker volume ls`, the name is the SECOND column, not the last."""
    names: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.upper().startswith("NETWORK ID"):
            continue
        parts = line.split()
        if len(parts) >= 2:
            names.add(parts[1])
    return names


def check_mbp_pre_bootstrap_volumes_absent(ctx: Context) -> tuple[str, bool, str]:
    name = "mbp_pre_bootstrap_volumes_absent"
    if ctx.meta.get("host_label") != "mbp":
        return name, True, "not MBP; N/A"
    p = ctx.run_dir / "pre_bootstrap_docker_volume_ls.txt"
    if not p.is_file():
        return name, False, "pre_bootstrap_docker_volume_ls.txt missing (MBP-required)"
    prefix = _load_instance_prefix(ctx.repo_root)
    expected = [f"{prefix}{n}" for n in EXPECTED_VOLUME_BASE_NAMES]
    present_names = _parse_volume_ls_names(p.read_text(encoding="utf-8", errors="replace"))
    found = [n for n in expected if n in present_names]
    exception = bool(ctx.meta.get("greenfield_exception")) and bool(ctx.meta.get("greenfield_exception_handoff_path"))
    if found and not exception:
        return name, False, f"pre-existing external volumes found: {found} (no greenfield_exception + handoff ref)"
    return name, True, (
        "no pre-existing external volumes found" if not found else
        f"pre-existing volumes {found} covered by greenfield_exception"
    )


def check_mbp_pre_bootstrap_network_absent(ctx: Context) -> tuple[str, bool, str]:
    name = "mbp_pre_bootstrap_network_absent"
    if ctx.meta.get("host_label") != "mbp":
        return name, True, "not MBP; N/A"
    p = ctx.run_dir / "pre_bootstrap_docker_network_ls.txt"
    if not p.is_file():
        return name, False, "pre_bootstrap_docker_network_ls.txt missing (MBP-required)"
    present_names = _parse_network_ls_names(p.read_text(encoding="utf-8", errors="replace"))
    exists = MBP_REQUIRED_NETWORK in present_names
    exception = bool(ctx.meta.get("greenfield_exception")) and bool(ctx.meta.get("greenfield_exception_handoff_path"))
    if exists and not exception:
        return name, False, f"{MBP_REQUIRED_NETWORK!r} network pre-exists (no greenfield_exception + handoff ref)"
    return name, True, (
        f"{MBP_REQUIRED_NETWORK!r} absent" if not exists else
        f"{MBP_REQUIRED_NETWORK!r} pre-existing but covered by greenfield_exception"
    )


def check_docker_engine_floor_independent(ctx: Context) -> tuple[str, bool, str]:
    """Independently parse preflight.json's docker version text; fail when
    Engine <26 or API <v1.45 EVEN IF the recorded boolean flag claims true.
    Also fail when the flag itself is not literally true."""
    name = "docker_engine_floor_independent"
    pf = ctx.preflight or {}
    gate = pf.get("step3_deploy_gate") or {}
    flag = gate.get("docker_engine_meets_subpath_floor")
    text = pf.get("docker_version_summary") or ""
    engine_v = _parse_engine_version(text)
    api_v = _parse_api_version(text)
    engine_ok = engine_v is not None and engine_v >= ENGINE_SUBPATH_FLOOR
    api_ok = api_v is not None and api_v >= API_SUBPATH_FLOOR
    ok = flag is True and engine_ok and api_ok
    return name, ok, (
        f"flag={flag!r} parsed_engine={engine_v} (floor {ENGINE_SUBPATH_FLOOR}) "
        f"parsed_api={api_v} (floor {API_SUBPATH_FLOOR})"
    )


def check_docker_compose_floor_conditional(ctx: Context) -> tuple[str, bool, str]:
    """Compose >=2.26 is CONDITIONAL: only enforced when compose_config.json
    shows the compose YAML using `subpath:` syntax. This plan mounts the
    whole dmac-cc-users volume with no subpath syntax, so a host whose
    compose file never uses it must NOT be rejected on this floor alone."""
    name = "docker_compose_floor_conditional"
    compose_config = _try_load_json_any(ctx.run_dir / "compose_config.json")
    if not _compose_uses_subpath_syntax(compose_config):
        return name, True, "compose YAML does not use volume `subpath:` syntax; Compose floor not required"
    gate = (ctx.preflight or {}).get("step3_deploy_gate") or {}
    flag = gate.get("docker_compose_meets_subpath_floor")
    compose_version_text = (ctx.preflight or {}).get("docker_compose_version") or ""
    v = _parse_compose_version(compose_version_text)
    ok = flag is True and v is not None and v >= COMPOSE_SUBPATH_FLOOR
    return name, ok, f"subpath: syntax IS used; flag={flag!r} parsed_compose_version={v} (floor {COMPOSE_SUBPATH_FLOOR})"


def check_compose_topology_recorded(ctx: Context) -> tuple[str, bool, str]:
    name = "compose_topology_recorded"
    obj = _try_load_json(ctx.run_dir / "compose_config.json")
    if obj is None:
        return name, False, "compose_config.json missing/unreadable/not an object"
    services = obj.get("services") or {}
    networks = obj.get("networks") or {}
    volumes = obj.get("volumes") or {}
    text = json.dumps(obj)
    has_nextseek = "nextseek" in services
    has_cc_net = "dmac-cc-net" in networks
    has_cc_volume = "dmac-cc-users" in volumes or "dmac-cc-users" in text
    no_legacy_bind = "/srv/dmac/users" not in text
    ok = has_nextseek and has_cc_net and has_cc_volume and no_legacy_bind
    return name, ok, (
        f"nextseek_service={has_nextseek} dmac-cc-net={has_cc_net} "
        f"dmac-cc-users_volume={has_cc_volume} no_legacy_bind={no_legacy_bind}"
    )


def check_image_service_status_recorded(ctx: Context) -> tuple[str, bool, str]:
    name = "image_service_status_recorded"
    services_txt = ctx.run_dir / "compose_services.txt"
    ps_txt = ctx.run_dir / "docker_ps.txt"
    images_json = ctx.run_dir / "images.json"
    has_status_txt = any(
        p.is_file() and p.stat().st_size > 0 for p in (services_txt, ps_txt)
    )
    images_obj = _try_load_json_any(images_json)
    has_images = bool(images_obj)
    ok = has_status_txt and has_images
    return name, ok, f"status_txt_present={has_status_txt} images_json_present={has_images}"


def check_cc_runner_available_ok(ctx: Context) -> tuple[str, bool, str]:
    name = "cc_runner_available_ok"
    obj = _try_load_json_any(ctx.run_dir / "cc_runner_available.json")
    if obj is None:
        return name, False, "cc_runner_available.json missing/unreadable"
    if isinstance(obj, list) and obj:
        ok = obj[0] is True
    elif isinstance(obj, dict):
        ok = obj.get("ok") is True
    else:
        return name, False, f"unexpected cc_runner_available.json shape: {obj!r}"
    return name, ok, f"cc_runner_available() result={obj!r}"


def check_forced_cc_success(ctx: Context) -> tuple[str, bool, str]:
    name = "forced_cc_success"
    obj = _try_load_json(ctx.run_dir / "forced_cc_result.json")
    if obj is None:
        return name, False, "forced_cc_result.json missing/unreadable"
    is_err = bool(obj.get("is_error") or obj.get("error"))
    sentinel = obj.get("sentinel")
    ok = (not is_err) and bool(sentinel)
    return name, ok, f"is_error={is_err} sentinel={sentinel!r}"


def check_forced_cc_cost_within_budget(ctx: Context) -> tuple[str, bool, str]:
    name = "forced_cc_cost_within_budget"
    obj = _try_load_json(ctx.run_dir / "forced_cc_result.json") or {}
    cost = obj.get("cost")
    cap = ctx.meta.get("budget_cap_usd", DEFAULT_BUDGET_CAP_USD)
    try:
        cost_f = float(cost)
        cap_f = float(cap)
    except (TypeError, ValueError):
        return name, False, f"cost={cost!r} budget_cap_usd={cap!r} not numeric"
    ok = cost_f <= cap_f
    return name, ok, f"cost=${cost_f:.4f} <= budget_cap_usd=${cap_f:.2f}"


def check_forced_cc_cost_positive_unless_exception(ctx: Context) -> tuple[str, bool, str]:
    name = "forced_cc_cost_positive_unless_zero_cost_exception"
    obj = _try_load_json(ctx.run_dir / "forced_cc_result.json") or {}
    cost = obj.get("cost")
    exception = bool(ctx.meta.get("zero_cost_exception"))
    try:
        cost_f = float(cost)
    except (TypeError, ValueError):
        return name, False, f"cost={cost!r} not numeric"
    ok = cost_f > 0 or exception
    return name, ok, f"cost=${cost_f:.4f} zero_cost_exception={exception}"


def check_forced_cc_run_id_matches_meta(ctx: Context) -> tuple[str, bool, str]:
    name = "forced_cc_result_run_id_matches_meta"
    obj = _try_load_json(ctx.run_dir / "forced_cc_result.json") or {}
    result_run_id = obj.get("run_id")
    meta_run_id = ctx.meta.get("run_id")
    ok = bool(meta_run_id) and result_run_id == meta_run_id
    return name, ok, f"forced_cc_result.run_id={result_run_id!r} meta.run_id={meta_run_id!r}"


def check_proxy_invoke_recorded(ctx: Context) -> tuple[str, bool, str]:
    name = "proxy_invoke_recorded"
    p = ctx.run_dir / "proxy_log_window.txt"
    if not p.is_file():
        return name, False, "proxy_log_window.txt missing"
    text = p.read_text(encoding="utf-8", errors="replace")
    ok = bool(text.strip()) and bool(_INVOKE_200_OPUS_RE.search(text))
    return name, ok, (
        "proxy invoke ->200 found for the allowed model" if ok else
        f"no proxy invoke ->200 line found for the allowed model ({_CC_OPUS_MODEL_ID}) in proxy_log_window.txt"
    )


def check_network_segmentation_ok(ctx: Context) -> tuple[str, bool, str]:
    """OI-3: `nextseek` itself must NOT be attached to the agent's network --
    only nginx (the dual-homed `nextseek_nginx` service) may bridge it to the
    backend. Unlike `nextseek` below, `nextseek_nginx` carries NO
    `container_name:` pin in docker-compose.yml -- its runtime name follows
    Compose's default project-prefixed naming (e.g.
    `nextseek-nextseek_nginx-1`). This check does not need to allowlist that
    name explicitly: as the next paragraph explains, none of the `_PEER_RE`
    backend stems match it, so it is simply never flagged.

    The shared `_PEER_RE` stems ("neo4j", "seek", "mysql") are word-boundary
    regexes and deliberately do NOT include a "nextseek" stem: adding one
    would false-positive the legitimately dual-homed nginx entrypoint, whose
    compose-project-prefixed runtime name (e.g.
    "nextseek-nextseek_nginx-1") CONTAINS the substring "nextseek". A regex
    can't distinguish that from the app container itself joining the network
    by drift.

    Instead this check adds a second, non-regex test: exact string equality
    against the literal "nextseek". docker-compose.yml pins
    `container_name: nextseek` for the app service (see
    check_agent_container_in_network_inspect's sibling checks / DEPLOY.md),
    so at runtime the container name IS exactly "nextseek" when healthy --
    there is no compose-prefixed variant to worry about missing. Any name
    containing "nginx" (bare "nextseek_nginx" or prefixed
    "nextseek-nextseek_nginx-1") is unaffected by this exact check and stays
    legitimate.
    """
    name = "network_segmentation_ok"
    names = _network_inspect_names(ctx.run_dir / "network_inspect.json")
    if names is None:
        return name, False, (
            "network_inspect.json missing/unreadable/not the full `docker network "
            "inspect` shape (containers keyed by ID with Name fields)"
        )
    bad = sorted(
        nm for nm in names
        if nm == "nextseek" or any(rx.search(nm) for rx in _cc_peer_res().values())
    )
    ok = not bad
    return name, ok, f"forbidden backend peers on agent net: {bad}"


def _cc_peer_res():
    from nextseek_api.cc_assistant.tests.validate_cc_acceptance import _PEER_RE
    return _PEER_RE


def check_agent_env_decredentialed(ctx: Context) -> tuple[str, bool, str]:
    name = "agent_env_decredentialed"
    p = ctx.run_dir / "agent_env_scan.txt"
    if not p.is_file():
        return name, False, "agent_env_scan.txt missing"
    text = p.read_text(encoding="utf-8", errors="replace")
    present_keys = [k for k in _CC_SHARED_CRED_KEYS if re.search(rf"(^|\W){re.escape(k)}=", text)]
    present_markers = [m for m in _CC_LEAK_MARKERS if m in text]
    ok = not present_keys and not present_markers
    return name, ok, f"leaked_keys={present_keys} leak_markers={present_markers}"


def check_proxy_token_not_logged(ctx: Context) -> tuple[str, bool, str]:
    name = "proxy_token_not_logged"
    p = ctx.run_dir / "proxy_log_window.txt"
    if not p.is_file():
        return name, False, "proxy_log_window.txt missing"
    text = p.read_text(encoding="utf-8", errors="replace")
    leaks = sum(text.count(m) for m in ("ABSK", "Authorization", "authorization"))
    ok = leaks == 0
    return name, ok, f"{leaks} token/authz occurrence(s) in proxy_log_window.txt"


def check_run_id_in_proxy_log(ctx: Context) -> tuple[str, bool, str]:
    name = "cross_artifact_run_id_in_proxy_log"
    run_id = ctx.meta.get("run_id")
    p = ctx.run_dir / "proxy_log_window.txt"
    if not run_id:
        return name, False, "meta.json.run_id missing"
    if not p.is_file():
        return name, False, "proxy_log_window.txt missing"
    text = p.read_text(encoding="utf-8", errors="replace")
    ok = run_id in text
    return name, ok, f"run_id {run_id!r} present in proxy_log_window.txt={ok}"


def check_agent_container_in_network_inspect(ctx: Context) -> tuple[str, bool, str]:
    """Task 15 Step 2 (iter-2 R2-M1 / iter-3 M-3): REPLACES the former fragile
    ``run_id in json.dumps(network_inspect)`` substring check (a
    self-referential-injection seam -- run_id could appear anywhere in the
    JSON, not just as an actual peer's Name) with deterministic name
    presence: the bundle's own ``dmac-cc-agent-<meta.run_id>`` container must
    be an ACTUAL peer (a Name field of some container record) in the
    DURING-TURN network-inspect capture."""
    name = "cross_artifact_agent_container_in_network_inspect"
    run_id = ctx.meta.get("run_id")
    if not run_id:
        return name, False, "meta.json.run_id missing"
    names = _network_inspect_names(ctx.run_dir / "network_inspect.json")
    if names is None:
        return name, False, (
            "network_inspect.json missing/unreadable/not the full `docker network "
            "inspect` shape (containers keyed by ID with Name fields)"
        )
    expected = f"dmac-cc-agent-{run_id}"
    ok = expected in names
    return name, ok, f"expected agent container name {expected!r} present in network_inspect.json={ok}"


def check_dmac_cc_net_closed_set(ctx: Context) -> tuple[str, bool, str]:
    """Task 15 Step 2 (SPEC-7 section 10 G7-11): ``dmac-cc-net`` membership,
    gathered from BOTH network-inspect captures (during-turn + matrix
    window), must be a CLOSED SET -- the legitimate trio (nginx / bedrock
    proxy / sidecar), general-pattern transient agents, and (when this run
    spawned one) the reserved matrix executor. Anything else -- including the
    exact literal ``"nextseek"`` -- fails. Fail-closed if neither inspect
    capture is present/parseable."""
    name = "dmac_cc_net_closed_set"
    run_id = ctx.meta.get("run_id")
    names: set[str] = set()
    any_present = False
    for fname in ("network_inspect.json", "network_inspect_matrix.json"):
        found = _network_inspect_names(ctx.run_dir / fname)
        if found is not None:
            any_present = True
            names |= found
    if not any_present:
        return name, False, "neither network_inspect.json nor network_inspect_matrix.json is readable"
    strangers = sorted(
        nm for nm in names if not _cc_net_closed_set_member(nm, run_id=run_id)
    )
    ok = not strangers
    return name, ok, (
        "dmac-cc-net membership is the closed set" if ok
        else f"stranger peer(s) on dmac-cc-net (not in the closed set): {strangers}"
    )


def check_migration_policy_conditionality(ctx: Context) -> tuple[str, bool, str]:
    """dev-only migration_policy required iff host_label is dev-vm/nextseek-dev
    AND preflight.json.had_host_bind_data == true (greenfield dev-VM: optional).
    Forbidden when host_label == "mbp"."""
    name = "migration_policy_conditionality"
    host_label = ctx.meta.get("host_label")
    migration_policy = ctx.meta.get("migration_policy")
    gate = (ctx.preflight or {}).get("step3_deploy_gate") or {}
    had_host_bind_data = gate.get("had_host_bind_data")

    if host_label == "mbp":
        ok = not migration_policy
        return name, ok, f"host_label=mbp: migration_policy must be absent; got {migration_policy!r}"
    if host_label in ("dev-vm", "nextseek-dev"):
        if had_host_bind_data is True:
            ok = bool(migration_policy)
            return name, ok, (
                f"host_label={host_label!r} had_host_bind_data=True: migration_policy required; "
                f"got {migration_policy!r}"
            )
        return name, True, (
            f"host_label={host_label!r} had_host_bind_data={had_host_bind_data!r} "
            f"(greenfield or unset): migration_policy optional"
        )
    return name, True, f"host_label={host_label!r} not dev-vm/nextseek-dev/mbp; N/A here (see host_label_enum_valid)"


def check_pre_turn_seed_scan_contains_foreign_tokens(ctx: Context) -> tuple[str, bool, str]:
    name = "pre_turn_seed_scan_contains_foreign_tokens"
    p = ctx.run_dir / "pre_turn_seed_scan.txt"
    foreign = ctx.meta.get("foreign_tokens")
    if not p.is_file():
        return name, False, "pre_turn_seed_scan.txt missing"
    text = p.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return name, False, "pre_turn_seed_scan.txt is empty"
    if not isinstance(foreign, list) or not foreign:
        return name, False, "meta.json.foreign_tokens missing/empty; cannot prove seed"
    missing = [t for t in foreign if t not in text]
    ok = not missing
    return name, ok, ("all foreign tokens present pre-turn" if ok else f"missing foreign tokens pre-turn: {missing}")


def check_subpath_isolation_scan_valid(ctx: Context) -> tuple[str, bool, str]:
    """(a) contains own_marker, (b) contains live_sentinel, (c) contains none
    of the foreign tokens via the pinned grep pattern. Neither this check
    alone nor check_pre_turn_seed_scan_contains_foreign_tokens alone suffices
    -- the pair is the cross-user isolation gate."""
    name = "subpath_isolation_scan_valid"
    p = ctx.run_dir / "subpath_isolation_scan.txt"
    own_marker = ctx.meta.get("own_marker")
    live_sentinel = ctx.meta.get("live_sentinel")
    if not p.is_file():
        return name, False, "subpath_isolation_scan.txt missing"
    text = p.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return name, False, "subpath_isolation_scan.txt is empty"
    if not own_marker or own_marker not in text:
        return name, False, f"meta.json.own_marker {own_marker!r} not found in subpath_isolation_scan.txt"
    if not live_sentinel or live_sentinel not in text:
        return name, False, f"meta.json.live_sentinel {live_sentinel!r} not found in subpath_isolation_scan.txt"
    bad_lines = [ln for ln in text.splitlines() if FOREIGN_TOKEN_GREP_RE.search(ln)]
    ok = not bad_lines
    return name, ok, ("own_marker + live_sentinel present; no foreign tokens" if ok else
                       f"foreign token(s) found on line(s): {bad_lines[:5]}")


def check_foreign_tokens_canonical_set(ctx: Context) -> tuple[str, bool, str]:
    """meta.json.foreign_tokens must equal CANONICAL_FOREIGN_TOKENS exactly
    (set equality) -- fail-closed on missing/wrong-type. This pins the
    pre-turn seed-scan oracle (check_pre_turn_seed_scan_contains_foreign_tokens)
    to the same tokens the in-turn leak detector (FOREIGN_TOKEN_GREP_RE) is
    hardcoded to catch, so a harness cannot substitute non-canonical tokens
    that prove presence pre-turn while a real leak of the canonical tokens
    goes undetected in-turn."""
    name = "foreign_tokens_canonical_set"
    foreign = ctx.meta.get("foreign_tokens")
    if not isinstance(foreign, list) or not all(isinstance(t, str) for t in foreign):
        return name, False, f"meta.json.foreign_tokens missing or not a list of strings: {foreign!r}"
    ok = set(foreign) == CANONICAL_FOREIGN_TOKENS
    return name, ok, (
        f"foreign_tokens={foreign!r} (must equal exactly {sorted(CANONICAL_FOREIGN_TOKENS)})"
    )


def check_meta_tokens_pairwise_disjoint(ctx: Context) -> tuple[str, bool, str]:
    name = "meta_tokens_pairwise_disjoint"
    foreign = ctx.meta.get("foreign_tokens")
    own_marker = ctx.meta.get("own_marker")
    live_sentinel = ctx.meta.get("live_sentinel")
    if not isinstance(foreign, list) or not own_marker or not live_sentinel:
        return name, False, "meta.json missing foreign_tokens/own_marker/live_sentinel"
    ok = own_marker not in foreign and live_sentinel not in foreign and own_marker != live_sentinel
    return name, ok, f"own_marker={own_marker!r} live_sentinel={live_sentinel!r} foreign_tokens={foreign!r}"


def check_no_legacy_artifact_filenames(ctx: Context) -> tuple[str, bool, str]:
    name = "no_legacy_artifact_filenames"
    found = sorted(
        str(p.relative_to(ctx.run_dir)) for p in ctx.run_dir.rglob("*")
        if p.is_file() and p.name in LEGACY_ARTIFACT_BASENAMES
    )
    ok = not found
    return name, ok, ("no legacy filenames present" if ok else f"legacy filenames rejected: {found}")


def check_secret_scan_report_present(ctx: Context) -> tuple[str, bool, str]:
    name = "secret_scan_report_present"
    obj = _try_load_json_any(ctx.run_dir / "secret_scan_report.json")
    ok = isinstance(obj, dict)
    return name, ok, ("secret_scan_report.json present" if ok else "secret_scan_report.json missing/unreadable/not an object")


def _iter_bundle_text_files(run_dir: Path):
    for p in sorted(run_dir.rglob("*")):
        if p.is_file() and p.suffix.lower() not in _SCREENSHOT_SUFFIXES:
            yield p


def check_secret_scan_clean(ctx: Context) -> tuple[str, bool, str]:
    """SPEC-7 section 9 negative controls: fail on AWS_BEARER_TOKEN_BEDROCK=,
    Authorization: Bearer, ABSK, GCP_API_KEY=, MYSQL_PASSWORD=, NEO4J_PASSWORD=,
    demopassword, Django SECRET_KEY values, unredacted NEXTSEEK_PASSWORD=."""
    name = "secret_scan_clean"
    hits: list[str] = []
    for p in _iter_bundle_text_files(ctx.run_dir):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(p.relative_to(ctx.run_dir))
        for key in _SECRET_KEY_ASSIGN_MARKERS:
            if re.search(rf"(^|\W){re.escape(key)}\s*=\s*\S", text):
                hits.append(f"{rel}:{key}")
        for marker in _SECRET_LITERAL_MARKERS:
            if marker in text:
                hits.append(f"{rel}:{marker}")
        if _DJANGO_SECRET_KEY_RE.search(text):
            hits.append(f"{rel}:SECRET_KEY")
        if _NEXTSEEK_PASSWORD_UNREDACTED_RE.search(text):
            hits.append(f"{rel}:NEXTSEEK_PASSWORD")
    ok = not hits
    return name, ok, ("no secret markers found in any evidence artifact" if ok else f"secret markers found: {hits}")


def check_screenshot_review_recorded(ctx: Context) -> tuple[str, bool, str]:
    name = "screenshot_review_recorded"
    screenshots = sorted(
        p for p in ctx.run_dir.rglob("*") if p.is_file() and p.suffix.lower() in _SCREENSHOT_SUFFIXES
    )
    if not screenshots:
        return name, True, "no screenshots present; review not required"
    report = _try_load_json_any(ctx.run_dir / "secret_scan_report.json")
    if not isinstance(report, dict):
        return name, False, "screenshots present but secret_scan_report.json missing/unreadable"
    entries = report.get("screenshots")
    if not isinstance(entries, dict):
        return name, False, "screenshots present but secret_scan_report.json has no 'screenshots' entries"
    missing = []
    for p in screenshots:
        rel = str(p.relative_to(ctx.run_dir))
        entry = entries.get(rel)
        method = entry.get("method") if isinstance(entry, dict) else None
        if method not in ("ocr", "manual_review"):
            missing.append(rel)
    ok = not missing
    return name, ok, ("all screenshots have documented OCR/manual review" if ok else
                       f"missing/undocumented review for: {missing}")


def check_not_markdown_only_bundle(ctx: Context) -> tuple[str, bool, str]:
    name = "not_markdown_only_bundle"
    files = [p for p in ctx.run_dir.rglob("*") if p.is_file()]
    if not files:
        return name, False, "run_dir is empty"
    non_md = [p for p in files if p.suffix.lower() not in (".md", ".markdown")]
    ok = bool(non_md)
    return name, ok, ("bundle has non-Markdown evidence" if ok else
                       "bundle contains ONLY Markdown files -- Markdown prose is not proof (SPEC-7 G7-8)")


# --- Task 15 (G7-11 capability gate: plugin_ops_matrix.json + companions) --

def _load_matrix(ctx: Context) -> dict[str, Any] | None:
    obj = _try_load_json_any(ctx.run_dir / "plugin_ops_matrix.json")
    return obj if isinstance(obj, dict) else None


def check_plugin_ops_matrix_present(ctx: Context) -> tuple[str, bool, str]:
    name = "plugin_ops_matrix_present"
    ok = _load_matrix(ctx) is not None
    return name, ok, ("plugin_ops_matrix.json present" if ok else
                       "plugin_ops_matrix.json missing/unreadable/not an object")


def check_plugin_ops_matrix_all_ops_present(ctx: Context) -> tuple[str, bool, str]:
    """Bundle FAILS on any missing op (all 9 bin names required, keyed exactly)."""
    name = "plugin_ops_matrix_all_ops_present"
    matrix = _load_matrix(ctx)
    if matrix is None:
        return name, False, "plugin_ops_matrix.json missing/unreadable"
    missing = [op for op in BIN_OPS if op not in matrix]
    unexpected = sorted(k for k in matrix if k not in BIN_OPS)
    ok = not missing and not unexpected
    return name, ok, f"missing={missing} unexpected_keys={unexpected}"


def check_plugin_ops_matrix_row_schema_valid(ctx: Context) -> tuple[str, bool, str]:
    name = "plugin_ops_matrix_row_schema_valid"
    matrix = _load_matrix(ctx)
    if matrix is None:
        return name, False, "plugin_ops_matrix.json missing/unreadable"
    bad: list[str] = []
    for op in BIN_OPS:
        row = matrix.get(op)
        if not isinstance(row, dict):
            bad.append(f"{op}: missing/not-an-object row")
            continue
        missing_keys = [k for k in REQUIRED_MATRIX_ROW_KEYS if k not in row]
        if missing_keys:
            bad.append(f"{op}: missing keys {missing_keys}")
        if op in PUBLISHED_PATH_OPS and not row.get("published_path"):
            bad.append(f"{op}: missing published_path")
    ok = not bad
    return name, ok, ("all rows well-formed" if ok else f"bad rows: {bad}")


def check_plugin_ops_matrix_exit_codes_valid(ctx: Context) -> tuple[str, bool, str]:
    """Exit-code law: exit 7 (TRANSPORT_ERROR) always fails; any other
    nonzero exit fails EXCEPT exit 5 + a WRITE_BLOCKED stderr marker on
    nextseek-api-write (the pinned Layer-2 unconfirmed-write-leg form)."""
    name = "plugin_ops_matrix_exit_codes_valid"
    matrix = _load_matrix(ctx)
    if matrix is None:
        return name, False, "plugin_ops_matrix.json missing/unreadable"
    bad: list[str] = []
    for op in BIN_OPS:
        row = matrix.get(op)
        if not isinstance(row, dict):
            bad.append(f"{op}: missing row")
            continue
        exit_code = row.get("exit_code")
        excerpt = row.get("excerpt") or ""
        if exit_code == TRANSPORT_ERROR_EXIT:
            bad.append(f"{op}: exit {TRANSPORT_ERROR_EXIT} (TRANSPORT_ERROR / missing backend)")
        elif exit_code == 0:
            continue
        elif exit_code == WRITE_BLOCKED_EXIT and op == WRITE_BLOCKED_OP:
            if WRITE_BLOCKED_STDERR_MARKER not in str(excerpt):
                bad.append(f"{op}: exit {WRITE_BLOCKED_EXIT} without stderr marker {WRITE_BLOCKED_STDERR_MARKER!r}")
        else:
            bad.append(f"{op}: disallowed exit_code={exit_code!r}")
    ok = not bad
    return name, ok, ("all exit codes valid" if ok else f"invalid row(s): {bad}")


def check_plugin_ops_matrix_excerpt_shape_valid(ctx: Context) -> tuple[str, bool, str]:
    """Anti-fabrication: on exit-0 rows, the excerpt must parse as the op's
    allowlisted success shape -- no 'error' field, no 'ok: false', and no
    top-level field outside the per-op allowlist. A documented SUCCESSFUL-
    empty result (empty rows/saved_files WITH the op's success marker) still
    passes; an exit-0 excerpt carrying a failure payload does not (agent
    failure is not empty data)."""
    name = "plugin_ops_matrix_excerpt_shape_valid"
    matrix = _load_matrix(ctx)
    if matrix is None:
        return name, False, "plugin_ops_matrix.json missing/unreadable"
    bad: list[str] = []
    for op in BIN_OPS:
        row = matrix.get(op)
        if not isinstance(row, dict):
            bad.append(f"{op}: missing row")
            continue
        if row.get("exit_code") != 0:
            continue  # only exit-0 rows carry a success-shape excerpt
        excerpt = row.get("excerpt")
        obj = None
        if isinstance(excerpt, str):
            try:
                obj = json.loads(excerpt)
            except json.JSONDecodeError:
                obj = None
        if not isinstance(obj, dict):
            bad.append(f"{op}: excerpt is not a JSON object")
            continue
        if obj.get("error"):
            bad.append(f"{op}: excerpt carries a failure 'error' field ({obj['error']!r})")
            continue
        if obj.get("ok") is False:
            bad.append(f"{op}: excerpt carries ok:false")
            continue
        allowed = OP_EXCERPT_ALLOWED_FIELDS.get(op)
        if allowed is not None:
            extra = sorted(set(obj) - allowed)
            if extra:
                bad.append(f"{op}: excerpt has field(s) outside the allowlist: {extra}")
    ok = not bad
    return name, ok, ("all excerpt shapes ok" if ok else f"bad excerpt(s): {bad}")


def check_plugin_ops_matrix_executor_provenance(ctx: Context) -> tuple[str, bool, str]:
    """Every row's image must equal images.json's CC image, and its
    container_id must join network_inspect_matrix.json to a container whose
    Name == dmac-cc-matrix-<run_id> (iter-3 M-3: no in-turn-agent exception --
    ALL matrix rows come from the dedicated gate executor)."""
    name = "plugin_ops_matrix_executor_provenance"
    matrix = _load_matrix(ctx)
    if matrix is None:
        return name, False, "plugin_ops_matrix.json missing/unreadable"
    run_id = ctx.meta.get("run_id")
    if not run_id:
        return name, False, "meta.json.run_id missing"
    expected_name = _matrix_executor_name(run_id)
    images = _try_load_json_any(ctx.run_dir / "images.json")
    cc_image = images.get(IMAGES_JSON_CC_IMAGE_KEY) if isinstance(images, dict) else None
    if not cc_image:
        return name, False, f"images.json missing/unreadable, or no {IMAGES_JSON_CC_IMAGE_KEY!r} key"
    containers = _load_network_inspect_containers(ctx.run_dir / "network_inspect_matrix.json")
    if containers is None:
        return name, False, (
            "network_inspect_matrix.json missing/unreadable/not the full "
            "`docker network inspect` shape"
        )
    bad: list[str] = []
    for op in BIN_OPS:
        row = matrix.get(op)
        if not isinstance(row, dict):
            bad.append(f"{op}: missing row")
            continue
        cid = row.get("container_id")
        image = row.get("image")
        cname = row.get("container_name")
        if image != cc_image:
            bad.append(f"{op}: image {image!r} != images.json CC image {cc_image!r}")
        rec = containers.get(cid) if isinstance(cid, str) else None
        inspect_name = rec.get("Name") if isinstance(rec, dict) else None
        if inspect_name != expected_name:
            bad.append(
                f"{op}: container_id {cid!r} joins to Name={inspect_name!r} "
                f"(expected {expected_name!r})"
            )
        if cname != expected_name:
            bad.append(f"{op}: row.container_name={cname!r} != {expected_name!r}")
    ok = not bad
    return name, ok, ("executor provenance ok for every row" if ok else f"mismatches: {bad}")


def check_plugin_ops_matrix_published_paths_under_user_subtree(ctx: Context) -> tuple[str, bool, str]:
    """Sweep cross-check (iter-1 H-1; hardened iter-2 R2-M4): nextseek-report
    and nextseek-generate-submission rows must record published_path under
    the gate user's own {project}/{user}/ subtree; a dead /staging/...-only
    path fails."""
    name = "plugin_ops_matrix_published_paths_under_user_subtree"
    matrix = _load_matrix(ctx)
    if matrix is None:
        return name, False, "plugin_ops_matrix.json missing/unreadable"
    project = ctx.meta.get("gate_project")
    user = ctx.meta.get("gate_user_id")
    bad: list[str] = []
    for op in PUBLISHED_PATH_OPS:
        row = matrix.get(op)
        if not isinstance(row, dict):
            bad.append(f"{op}: missing row")
            continue
        path = row.get("published_path")
        if not isinstance(path, str) or not path:
            bad.append(f"{op}: no published_path recorded")
            continue
        if "/staging/" in path or path.startswith("/staging") or path.startswith("_staging/"):
            bad.append(f"{op}: published_path is a dead /staging/... path: {path!r}")
            continue
        if not project or not user:
            bad.append(f"{op}: meta.json missing gate_project/gate_user_id; cannot verify subtree")
            continue
        needle = f"/{project}/{user}/"
        if needle not in path:
            bad.append(f"{op}: published_path {path!r} not under the gate user's own {needle!r} subtree")
    ok = not bad
    return name, ok, ("published_path ok for every row that needs one" if ok else f"bad: {bad}")


def check_post_sweep_user_tree_scan_contains_published_paths(ctx: Context) -> tuple[str, bool, str]:
    """Anti-fabrication (on-disk artifact check, not a bare string assertion):
    every recorded published_path must appear in a harness-captured recursive
    `find` of the gate user's subtree taken AFTER the recorded sweep
    invocation."""
    name = "post_sweep_user_tree_scan_contains_published_paths"
    p = ctx.run_dir / "post_sweep_user_tree_scan.txt"
    if not p.is_file():
        return name, False, "post_sweep_user_tree_scan.txt missing"
    text = p.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return name, False, "post_sweep_user_tree_scan.txt is empty"
    matrix = _load_matrix(ctx)
    if matrix is None:
        return name, False, "plugin_ops_matrix.json missing/unreadable"
    missing: list[tuple[str, Any]] = []
    for op in PUBLISHED_PATH_OPS:
        row = matrix.get(op) or {}
        path = row.get("published_path")
        if not path or path not in text:
            missing.append((op, path))
    ok = not missing
    return name, ok, (
        "every published_path found on disk in post_sweep_user_tree_scan.txt" if ok
        else f"published_path(s) not found on disk: {missing}"
    )


def check_gate_access_log_window_hits_every_op(ctx: Context) -> tuple[str, bool, str]:
    """Anti-fabrication binding (iter-2 R2-M4): the nginx access-log window
    for the matrix run must show >=1 hit per op's assistant endpoint.
    nextseek-query/nextseek-plan share ONE endpoint (query/async/) and are
    therefore evaluated as ONE shared hit requirement, not two."""
    name = "gate_access_log_window_hits_every_op"
    p = ctx.run_dir / "gate_access_log_window.txt"
    if not p.is_file():
        return name, False, "gate_access_log_window.txt missing"
    text = p.read_text(encoding="utf-8", errors="replace")
    if not text.strip():
        return name, False, "gate_access_log_window.txt is empty"
    missing_endpoints: list[str] = []
    seen: set[str] = set()
    for op in BIN_OPS:
        endpoint = OP_ASSISTANT_ENDPOINT[op]
        if endpoint in seen:
            continue
        seen.add(endpoint)
        pattern = re.compile(r"\b(?:POST|GET)\s+" + re.escape(endpoint) + r"(?:[\s?]|$)", re.MULTILINE)
        if not pattern.search(text):
            missing_endpoints.append(endpoint)
    ok = not missing_endpoints
    return name, ok, (
        "every distinct op endpoint has >=1 hit in the window" if ok
        else f"no access-log hit for endpoint(s): {missing_endpoints}"
    )


def check_matrix_env_scan_no_shared_creds(ctx: Context) -> tuple[str, bool, str]:
    """matrix_env_scan.txt is validated with the SAME no-shared-creds rules
    as agent_env_scan.txt (iter-3 M-1)."""
    name = "matrix_env_scan_no_shared_creds"
    p = ctx.run_dir / "matrix_env_scan.txt"
    if not p.is_file():
        return name, False, "matrix_env_scan.txt missing"
    text = p.read_text(encoding="utf-8", errors="replace")
    present_keys = [k for k in _CC_SHARED_CRED_KEYS if re.search(rf"(^|\W){re.escape(k)}=", text)]
    present_markers = [m for m in _CC_LEAK_MARKERS if m in text]
    ok = not present_keys and not present_markers
    return name, ok, f"leaked_keys={present_keys} leak_markers={present_markers}"


def check_sweep_invocation_valid(ctx: Context) -> tuple[str, bool, str]:
    """sweep_invocation.json {command, exit_code, output_excerpt, timestamp}
    -- presence + exit 0 required whenever the matrix artifacts are present."""
    name = "sweep_invocation_valid"
    obj = _try_load_json(ctx.run_dir / "sweep_invocation.json")
    if obj is None:
        return name, False, "sweep_invocation.json missing/unreadable/not an object"
    problems: list[str] = []
    command = obj.get("command")
    if not isinstance(command, str) or "cc_sweep_staging" not in command:
        problems.append("command missing or does not invoke the trusted cc_sweep_staging entrypoint")
    if obj.get("exit_code") != 0:
        problems.append(f"exit_code={obj.get('exit_code')!r} (must be 0)")
    output_excerpt = obj.get("output_excerpt")
    if not isinstance(output_excerpt, str) or not output_excerpt.strip():
        problems.append("output_excerpt missing/empty")
    if not obj.get("timestamp"):
        problems.append("timestamp missing")
    ok = not problems
    return name, ok, ("sweep_invocation.json ok" if ok else f"problems: {problems}")


def check_seeded_fixture_present(ctx: Context) -> tuple[str, bool, str]:
    """Step 3b: the minimal seeded fixture (sandbox project + sample UIDs)
    created via the gate user's authenticated REST calls, BEFORE the matrix
    run, that data-dependent ops target."""
    name = "seeded_fixture_present"
    obj = _try_load_json_any(ctx.run_dir / "seeded_fixture.json")
    if not isinstance(obj, dict):
        return name, False, "seeded_fixture.json missing/unreadable/not an object"
    project = obj.get("project")
    uids = obj.get("uids")
    ok = isinstance(project, str) and bool(project) and isinstance(uids, list) and len(uids) > 0
    return name, ok, f"project={project!r} uid_count={len(uids) if isinstance(uids, list) else 'N/A'}"


def check_plugin_ops_matrix_in_turn_viability(ctx: Context) -> tuple[str, bool, str]:
    """in_turn_viable = wall_secs < IN_TURN_HEADROOM_SECS, evaluated per op
    (iter-3 M-2). Exceeding the headroom does NOT fail the bundle (capability
    != latency) -- it is only listed here, for Task 11's handoff to surface
    as a named user decision. Malformed/missing wall_secs data DOES fail this
    check (fail-closed on the input, not on the latency verdict itself)."""
    name = "plugin_ops_matrix_in_turn_viability"
    matrix = _load_matrix(ctx)
    if matrix is None:
        return name, False, "plugin_ops_matrix.json missing/unreadable"
    viable: list[str] = []
    exceeders: list[str] = []
    problems: list[str] = []
    for op in BIN_OPS:
        row = matrix.get(op)
        if not isinstance(row, dict):
            problems.append(f"{op}: missing row")
            continue
        try:
            wall_secs = float(row.get("wall_secs"))
        except (TypeError, ValueError):
            problems.append(f"{op}: wall_secs not numeric ({row.get('wall_secs')!r})")
            continue
        if wall_secs < IN_TURN_HEADROOM_SECS:
            viable.append(op)
        else:
            exceeders.append(f"{op}={wall_secs}s")
    ok = not problems
    return name, ok, (
        f"in_turn_viable(<{IN_TURN_HEADROOM_SECS}s)={viable}; "
        f"EXCEEDS_HEADROOM(named user decision, Task 11 handoff)={exceeders}"
        + (f"; problems={problems}" if problems else "")
    )


def check_meta_matrix_spend_estimate_recorded(ctx: Context) -> tuple[str, bool, str]:
    """meta.json gains matrix_spend_estimate_usd (best-effort estimate) +
    matrix_spend_estimate_method (the method note: exact per-op server-side
    cost is not programmatically available, and that limitation is recorded,
    not hidden)."""
    name = "meta_matrix_spend_estimate_recorded"
    matrix = _load_matrix(ctx)
    if matrix is None:
        return name, False, "plugin_ops_matrix.json missing/unreadable"
    val = ctx.meta.get("matrix_spend_estimate_usd")
    method = ctx.meta.get("matrix_spend_estimate_method")
    try:
        v = float(val)
        ok_num = v >= 0
    except (TypeError, ValueError):
        ok_num = False
    ok = ok_num and isinstance(method, str) and bool(method.strip())
    return name, ok, f"matrix_spend_estimate_usd={val!r} matrix_spend_estimate_method={method!r}"


CHECKS: list[Callable[[Context], tuple[str, bool, str]]] = [
    check_preflight_json_present,
    check_branch_and_commit_recorded,
    check_required_file_hashes_present,
    check_step3_gate_fields_present,
    check_deploy_commit_format_valid,
    check_tracker_path_not_arbitrary,
    check_tracker_step3_done,
    check_live_evidence_path_literal,
    check_live_gate_transcript_committed,
    check_deploy_commit_matches_meta_repo_commit,
    check_transcript_migration_marker_present,
    check_transcript_cc_upload_marker_present,
    check_transcript_cc_traces_marker_present,
    check_supplementary_handoff_valid,
    check_host_label_enum_valid,
    check_mbp_pre_bootstrap_volumes_absent,
    check_mbp_pre_bootstrap_network_absent,
    check_docker_engine_floor_independent,
    check_docker_compose_floor_conditional,
    check_compose_topology_recorded,
    check_image_service_status_recorded,
    check_cc_runner_available_ok,
    check_forced_cc_success,
    check_forced_cc_cost_within_budget,
    check_forced_cc_cost_positive_unless_exception,
    check_forced_cc_run_id_matches_meta,
    check_proxy_invoke_recorded,
    check_network_segmentation_ok,
    check_agent_env_decredentialed,
    check_proxy_token_not_logged,
    check_run_id_in_proxy_log,
    check_agent_container_in_network_inspect,
    check_migration_policy_conditionality,
    check_pre_turn_seed_scan_contains_foreign_tokens,
    check_subpath_isolation_scan_valid,
    check_foreign_tokens_canonical_set,
    check_meta_tokens_pairwise_disjoint,
    check_no_legacy_artifact_filenames,
    check_secret_scan_report_present,
    check_secret_scan_clean,
    check_screenshot_review_recorded,
    check_not_markdown_only_bundle,
    # Task 15 (G7-11 capability gate)
    check_dmac_cc_net_closed_set,
    check_plugin_ops_matrix_present,
    check_plugin_ops_matrix_all_ops_present,
    check_plugin_ops_matrix_row_schema_valid,
    check_plugin_ops_matrix_exit_codes_valid,
    check_plugin_ops_matrix_excerpt_shape_valid,
    check_plugin_ops_matrix_executor_provenance,
    check_plugin_ops_matrix_published_paths_under_user_subtree,
    check_post_sweep_user_tree_scan_contains_published_paths,
    check_gate_access_log_window_hits_every_op,
    check_matrix_env_scan_no_shared_creds,
    check_sweep_invocation_valid,
    check_seeded_fixture_present,
    check_plugin_ops_matrix_in_turn_viability,
    check_meta_matrix_spend_estimate_recorded,
]


def default_repo_root() -> Path:
    """The NExtSEEK repo containing this validator module
    (tests/ -> cc_assistant -> nextseek_api -> repo root)."""
    return Path(__file__).resolve().parents[3]


def validate_run(
    run_dir: str | Path, repo_root: str | Path | None = None
) -> tuple[bool, list[tuple[str, bool, str]]]:
    """Return (all_passed, [(check_name, ok, detail), ...]).

    ``repo_root`` is the git repo the committed live-gate transcript is
    independently re-verified against at ``deploy_commit``; defaults to the
    repo containing this module. Hermetic tests inject a tmp_path temp repo.
    """
    d = Path(run_dir)
    preflight = _try_load_json(d / "preflight.json")
    meta = _try_load_json(d / "meta.json") or {}
    root = Path(repo_root) if repo_root is not None else default_repo_root()

    deploy_commit = ((preflight or {}).get("step3_deploy_gate") or {}).get("deploy_commit")
    transcript_text = None
    if isinstance(deploy_commit, str) and DEPLOY_COMMIT_RE.match(deploy_commit):
        transcript_text = _git_blob_text(root, deploy_commit, LIVE_GATE_TRANSCRIPT_REL)

    ctx = Context(
        run_dir=d, preflight=preflight, meta=meta, repo_root=root,
        transcript_text=transcript_text,
    )

    checks = [check(ctx) for check in CHECKS]
    all_ok = all(ok for _, ok, _ in checks)
    return all_ok, checks


def format_report(all_ok: bool, checks: list[tuple[str, bool, str]]) -> str:
    lines = [f"{'PASS' if ok else 'FAIL'}  {name:48s} {detail}" for name, ok, detail in checks]
    lines.append("")
    lines.append(f"{'ALL CHECKS PASSED' if all_ok else 'STEP 7 PREFLIGHT GATE FAILED'} "
                 f"({sum(ok for _, ok, _ in checks)}/{len(checks)})")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print("usage: python -m ...validate_step7_compose_deploy <run_dir> [repo_root]",
              file=sys.stderr)
        return 2
    all_ok, checks = validate_run(argv[1], argv[2] if len(argv) == 3 else None)
    print(format_report(all_ok, checks))
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))

"""Step 7 (compose-native deploy) preflight collector.

Produces the SPEC-7 §8 required ``preflight.json`` artifact: a snapshot of
current repo/tracker/environment state taken immediately before Step 7 acts,
so the implementer never acts on stale planning-session file state (that is
the whole point of this module — see PLAN-7 Task 1's "Purpose").

The collector never shells out on its own initiative during a hermetic test:
all git/docker facts are supplied via the injectable ``GitProbe`` /
``DockerProbe`` dataclasses. ``default_git_probe`` / ``default_docker_probe``
are the *real* probes (subprocess-based) used outside tests; hermetic tests
always construct fakes so no docker socket / network / git binary is needed.

``collect_preflight`` returns a plain dict — the caller decides whether/where
to write it as ``preflight.json`` (tests write it under ``tmp_path``; a real
run would write it under
``acceptance_evidence/step7/<run_id>/preflight.json``).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

# --- Required Step-7 file-hash targets -------------------------------------
# Label (as named verbatim in PLAN-7 Task 1) -> actual repo-relative path.
# docker-compose.yml / docker/nextseek.env.example live at the NExtSEEK repo
# root; the Step-3 docs live under nextseek_api/cc_assistant/.
REQUIRED_FILE_HASH_TARGETS: dict[str, str] = {
    "docker-compose.yml": "docker-compose.yml",
    "docker/nextseek.env.example": "docker/nextseek.env.example",
    "DEPLOY.md": "nextseek_api/cc_assistant/DEPLOY.md",
}
# "if present" per the brief's success conditions.
OPTIONAL_FILE_HASH_TARGETS: dict[str, str] = {
    "SPEC-3-ui-based-io.md": "nextseek_api/cc_assistant/SPEC-3-ui-based-io.md",
    "PLAN-3-ui-based-io.md": "nextseek_api/cc_assistant/PLAN-3-ui-based-io.md",
}

LIVE_EVIDENCE_PATH = "nextseek_api/cc_assistant/evidence/3-ui-based-io-live/"
LIVE_GATE_TRANSCRIPT_NAME = "live_gate_transcript.txt"
LIVE_GATE_TRANSCRIPT_REL = LIVE_EVIDENCE_PATH + LIVE_GATE_TRANSCRIPT_NAME

# The known Container-CC env keys DEPLOY.md's Phase B currently instructs
# appending by hand into docker/nextseek.env. Recorded (not gated) here —
# whether/when they land in the *example* file is a later Step-7 task.
CC_ENV_KEYS = (
    "NEXTSEEK_CC_IMAGE",
    "NEXTSEEK_CC_NETWORK",
    "DMAC_BEDROCK_PROXY_URL",
    "NEXTSEEK_CC_MAX_BUDGET_USD",
    "DMAC_ROUTER_ENABLED",
    "DMAC_ROUTE_CAPABILITIES_FILE",
    "DMAC_ROUTER_MODEL_CLASS_MAP_FILE",
    # RETIRED (Task 6, G7-10): the host-bind path this key configured
    # ("/srv/dmac/users") was eradicated in favor of the `dmac-cc-users`
    # named volume + per-user Engine-API `Subpath` mounts (see
    # docker/nextseek.env.example's "the old DMAC_USER_ROOT host-bind model
    # is retired" note). This collector only RECORDS whichever of
    # CC_ENV_KEYS are present in docker/nextseek.env.example -- it does not
    # gate on any of them -- so the key stays here for visibility if a
    # stale env file still sets it. Do NOT reintroduce a host-path bind
    # under this key (Task 16 debt-fix annotation).
    "DMAC_USER_ROOT",
    "DMAC_USER_ROOT_MOUNT",
    "DMAC_CC_USERS_VOLUME",
)

# Heuristic markers of DEPLOY.md's old hand-run bootstrap procedure (Phase A /
# Phase B manual docker + env-append steps this Step-7 effort compose-natives
# away). Recording-only in Task 1 (Task 2 may gate on it).
_OLD_BOOTSTRAP_MARKERS = (
    "docker network create",
    "docker commit nextseek",
    "Append to `docker/nextseek.env`",
    "Phase A",
    "Phase B",
)

# Plan-pinned real floors (Task 2 brief): the per-user runtime subpaths are
# applied via docker-py (Engine API) VolumeOptions.Subpath, not compose YAML,
# so ENGINE -- not Compose -- gates the isolation mount; this is the
# unconditional, real floor. Compose's floor (>=2.26) is CONDITIONAL (only
# required when the compose YAML itself uses `subpath:` syntax, which this
# plan's whole-volume dmac-cc-users mount does not) -- see the validator's
# independent, conditional re-check in validate_step7_compose_deploy.py.
DOCKER_ENGINE_SUBPATH_FLOOR = (26, 0, 0)
DOCKER_API_SUBPATH_FLOOR = (1, 45)
DOCKER_COMPOSE_SUBPATH_FLOOR = (2, 26, 0)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")
_API_VERSION_RE = re.compile(r"API version:\s*(\d+)\.(\d+)", re.IGNORECASE)


@dataclass(frozen=True)
class GitProbe:
    """Injectable git facts for one collection run."""

    branch: str
    commit: str
    dirty: bool
    # (commit, path) -> blob size in bytes from `git cat-file -s
    # <commit>:<path>`, or None if the path is absent at that commit / the
    # commit does not exist. Size (not mere existence) matters: a committed
    # zero-byte transcript is NOT acceptable live-gate evidence.
    cat_file_size: Callable[[str, str], int | None]


@dataclass(frozen=True)
class DockerProbe:
    """Injectable docker facts for one collection run."""

    version_summary: str
    info_summary: str
    compose_version: str
    engine_meets_subpath_floor: bool
    compose_meets_subpath_floor: bool


def _run_git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def default_git_probe(repo_root: Path) -> GitProbe:
    """Real (subprocess-based) git probe. NEVER called by hermetic tests."""
    repo_root = Path(repo_root)
    branch = _run_git(repo_root, "rev-parse", "--abbrev-ref", "HEAD")
    commit = _run_git(repo_root, "rev-parse", "HEAD")
    dirty = bool(_run_git(repo_root, "status", "--porcelain"))

    def cat_file_size(commit_: str, path_: str) -> int | None:
        try:
            proc = subprocess.run(
                ["git", "-C", str(repo_root), "cat-file", "-s", f"{commit_}:{path_}"],
                capture_output=True, text=True, check=True,
            )
            return int(proc.stdout.strip())
        except (subprocess.CalledProcessError, ValueError):
            return None

    return GitProbe(branch=branch, commit=commit, dirty=dirty, cat_file_size=cat_file_size)


def _parse_version_tuple(text: str) -> tuple[int, int, int] | None:
    m = _VERSION_RE.search(text)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def _parse_api_version_tuple(text: str) -> tuple[int, int] | None:
    m = _API_VERSION_RE.search(text)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def engine_meets_subpath_floor(version_summary: str) -> bool:
    """Pure, hermetically-testable: True iff `docker version` output shows
    BOTH Engine >= DOCKER_ENGINE_SUBPATH_FLOOR and API >= DOCKER_API_SUBPATH_FLOOR."""
    engine_v = _parse_version_tuple(version_summary)
    api_v = _parse_api_version_tuple(version_summary)
    return (
        engine_v is not None and engine_v >= DOCKER_ENGINE_SUBPATH_FLOOR
        and api_v is not None and api_v >= DOCKER_API_SUBPATH_FLOOR
    )


def compose_meets_subpath_floor(compose_version: str) -> bool:
    """Pure, hermetically-testable: True iff Compose plugin version text
    parses to >= DOCKER_COMPOSE_SUBPATH_FLOOR."""
    compose_v = _parse_version_tuple(compose_version)
    return compose_v is not None and compose_v >= DOCKER_COMPOSE_SUBPATH_FLOOR


def default_docker_probe() -> DockerProbe:
    """Real (subprocess-based) docker probe. NEVER called by hermetic tests."""
    def _out(cmd: list[str]) -> str:
        try:
            return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout.strip()
        except Exception as e:  # noqa: BLE001
            return f"<unavailable: {e}>"

    version_summary = _out(["docker", "version"])
    info_summary = _out(["docker", "info"])
    compose_version = _out(["docker", "compose", "version"])

    engine_ok = engine_meets_subpath_floor(version_summary)
    compose_ok = compose_meets_subpath_floor(compose_version)

    return DockerProbe(
        version_summary=version_summary,
        info_summary=info_summary,
        compose_version=compose_version,
        engine_meets_subpath_floor=engine_ok,
        compose_meets_subpath_floor=compose_ok,
    )


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resolve_integration_plan_path(repo_root: Path, env: Mapping[str, str] | None = None) -> Path:
    """Resolve INTEGRATION_PLAN_PATH, defaulting to repo-relative ``../state/
    integration-plan.json`` from the NExtSEEK repo root. Never a baked-in
    home path (per PLAN-7 Task 1)."""
    src = os.environ if env is None else env
    raw = src.get("INTEGRATION_PLAN_PATH")
    if raw:
        return Path(raw)
    return Path(repo_root) / ".." / "state" / "integration-plan.json"


def _tracker_step_status(tracker: dict, step_id: str) -> str | None:
    for step in tracker.get("steps", []):
        if str(step.get("id")) == step_id:
            return step.get("status")
    return None


def read_tracker_step3_status(path: Path) -> str | None:
    """Best-effort: None if unreadable/missing/malformed (validator, not this
    collector, is the source of truth on gate pass/fail)."""
    try:
        tracker = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _tracker_step_status(tracker, "3")


def _parse_compose(path: Path) -> tuple[list[str], list[str]]:
    if not path.is_file():
        return [], []
    import yaml  # local import: only needed here, keeps module import light

    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    services = sorted((data.get("services") or {}).keys())
    networks = sorted((data.get("networks") or {}).keys())
    return services, networks


def _parse_cc_env_keys(path: Path) -> list[str]:
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    present = []
    for key in CC_ENV_KEYS:
        if re.search(rf"(?m)^\s*{re.escape(key)}\s*=", text):
            present.append(key)
    return present


def _deploy_md_has_old_bootstrap(path: Path) -> bool:
    if not path.is_file():
        return False
    text = path.read_text(encoding="utf-8")
    return any(marker in text for marker in _OLD_BOOTSTRAP_MARKERS)


def collect_preflight(
    *,
    repo_root: Path,
    git: GitProbe,
    docker: DockerProbe,
    env: Mapping[str, str] | None = None,
    port_source_path: str,
    port_source_commit: str,
    had_host_bind_data: bool,
    pre_step3_snapshot_tag: str | None = None,
    canonical_integration_plan_sha256: str | None = None,
    user_signoff_handoff_path: str | None = None,
) -> dict:
    """Collect one preflight.json payload. Pure function of its inputs plus
    the repo_root filesystem contents — no hidden global state."""
    repo_root = Path(repo_root)

    integration_plan_path = resolve_integration_plan_path(repo_root, env)
    integration_plan_sha256 = sha256_file(integration_plan_path)
    tracker_step3_status = read_tracker_step3_status(integration_plan_path)

    file_hashes: dict[str, dict] = {}
    for label, rel in REQUIRED_FILE_HASH_TARGETS.items():
        p = repo_root / rel
        file_hashes[label] = {"path": rel, "exists": p.is_file(), "sha256": sha256_file(p)}
    for label, rel in OPTIONAL_FILE_HASH_TARGETS.items():
        p = repo_root / rel
        if p.is_file():
            file_hashes[label] = {"path": rel, "exists": True, "sha256": sha256_file(p)}

    # True only when the transcript blob exists at HEAD AND is non-empty
    # (`cat-file -s` > 0; `-e` would wrongly pass a committed zero-byte file).
    _transcript_size = git.cat_file_size(git.commit, LIVE_GATE_TRANSCRIPT_REL)
    live_gate_transcript_committed = _transcript_size is not None and _transcript_size > 0

    compose_services, compose_networks = _parse_compose(repo_root / "docker-compose.yml")
    cc_env_keys = _parse_cc_env_keys(repo_root / "docker" / "nextseek.env.example")
    deploy_md_has_old_bootstrap = _deploy_md_has_old_bootstrap(
        repo_root / "nextseek_api" / "cc_assistant" / "DEPLOY.md"
    )

    step3_deploy_gate = {
        "integration_plan_path": str(integration_plan_path),
        "tracker_step3_status": tracker_step3_status,
        "integration_plan_sha256": integration_plan_sha256,
        "canonical_integration_plan_sha256": canonical_integration_plan_sha256,
        "live_gate_transcript_committed": live_gate_transcript_committed,
        "deploy_commit": git.commit,
        "user_signoff_handoff_path": user_signoff_handoff_path,
        "live_evidence_path": LIVE_EVIDENCE_PATH,
        "pre_step3_snapshot_tag": pre_step3_snapshot_tag,
        "docker_engine_meets_subpath_floor": docker.engine_meets_subpath_floor,
        "docker_compose_meets_subpath_floor": docker.compose_meets_subpath_floor,
        "port_source_path": port_source_path,
        "port_source_commit": port_source_commit,
        "had_host_bind_data": had_host_bind_data,
    }

    return {
        "branch": git.branch,
        "commit": git.commit,
        "dirty": git.dirty,
        "file_hashes": file_hashes,
        "compose_services": compose_services,
        "compose_networks": compose_networks,
        "cc_env_keys": cc_env_keys,
        "deploy_md_has_old_bootstrap": deploy_md_has_old_bootstrap,
        "docker_version_summary": docker.version_summary,
        "docker_info_summary": docker.info_summary,
        "docker_compose_version": docker.compose_version,
        "step3_deploy_gate": step3_deploy_gate,
    }

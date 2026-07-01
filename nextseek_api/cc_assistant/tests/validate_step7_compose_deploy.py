"""Zero-spend, reproducible validator for the Step 7 (compose-native deploy)
evidence bundle's ``preflight.json`` / ``step3_deploy_gate``.

Purpose (PLAN-7 Task 1): prevent the Step 7 implementer from acting on stale
planning-session file state. Before Step 7 may proceed, the bundle must carry
a fresh ``preflight.json`` proving:

  - current branch/commit/dirty status and hashes of the files Step 7 will
    touch were captured at collection time (not hand-typed);
  - the production-readiness tracker's step 3 ("UI-based I/O") reads "done"
    **at validation time**, re-read from the tracker file at the recorded
    ``integration_plan_path`` — not merely trusted from a baked-in string;
  - the committed Step-3 live-gate transcript (PLAN-3 Task 13 Step 9) exists
    on the branch under test at the exact ``deploy_commit`` SHA. A
    supplementary handoff JSON is NOT an accepted substitute (user decision,
    2026-06-30).

Only an MBP host may point ``integration_plan_path`` at a snapshot file
*inside* the evidence bundle (``integration_plan_snapshot.json``) — every
other bundle pointing its tracker path inside itself is rejected outright,
since that would let a bundle carry (and validate against) an arbitrary,
hand-edited tracker file instead of the real one.

This module implements ONLY the preflight/step3_deploy_gate checks named in
PLAN-7 Task 1. Task 2 extends ``CHECKS`` with the rest of the generated
evidence bundle's checks (compose services, CC env keys, DEPLOY.md bootstrap
flag, docker/compose version floors, etc. — currently recorded by the
collector but not yet gated here).

    python -m nextseek_api.cc_assistant.tests.validate_step7_compose_deploy <run_dir>
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

# Matches typical MBP host_label spellings: "taishajo-mbp", "MBP.local",
# "mbp-taishajo", "MacBook-Pro", "MacBookPro16,1", etc.
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


def _try_load_json(p: Path) -> dict | None:
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return obj if isinstance(obj, dict) else None


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


@dataclass(frozen=True)
class Context:
    run_dir: Path
    preflight: dict[str, Any] | None
    meta: dict[str, Any]
    repo_root: Path


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


# --- Composable checks ------------------------------------------------------
# Task 2 appends its own checks to CHECKS; each check takes the Context and
# returns (name, ok, detail), same convention as validate_cc_acceptance.py.

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


CHECKS: list[Callable[[Context], tuple[str, bool, str]]] = [
    check_preflight_json_present,
    check_branch_and_commit_recorded,
    check_required_file_hashes_present,
    check_step3_gate_fields_present,
    check_tracker_path_not_arbitrary,
    check_tracker_step3_done,
    check_live_evidence_path_literal,
    check_live_gate_transcript_committed,
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
    ctx = Context(
        run_dir=d, preflight=preflight, meta=meta,
        repo_root=Path(repo_root) if repo_root is not None else default_repo_root(),
    )

    checks = [check(ctx) for check in CHECKS]
    all_ok = all(ok for _, ok, _ in checks)
    return all_ok, checks


def format_report(all_ok: bool, checks: list[tuple[str, bool, str]]) -> str:
    lines = [f"{'PASS' if ok else 'FAIL'}  {name:32s} {detail}" for name, ok, detail in checks]
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

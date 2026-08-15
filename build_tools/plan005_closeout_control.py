"""Task 13 preflight / finalize / verify control stages."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from build_tools.plan005_closeout import (
    APPROVED_PLAN_SHA,
    BACKUP_REL,
    BACKUP_SHA,
    COLD_REVIEW_REL,
    COMMAND_TIMEOUT_SECONDS,
    DEFAULT_MIRROR,
    DEFAULT_SIGNOFF_DIR,
    EVIDENCE_PARENT,
    IMMUTABLE_NEXTSEEK_IMAGE,
    IMMUTABLE_VALIDATOR_IMAGE,
    PLAN005_BASE_COMMIT,
    PLAN_REL,
    PROTOCOL_RECORD_IDS,
    REPO_ROOT_TEMPLATE,
    REQUIRED_SIGNOFF_IDS,
    ROUTE_CAPABILITIES_SHA,
    SEQUENCE_BUDGET_SECONDS,
    artifact_namespace,
    protocol_rows,
)


class CloseoutError(ValueError):
    """Raised when a closeout control stage is RED."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def presented_diff_hash(repo_root: Path, artifact_paths: list[str]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(artifact_paths):
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update((repo_root / rel).read_bytes())
    return digest.hexdigest()


def git_cmd(repo_root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo_root,
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise CloseoutError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout


def git_identity(repo_root: Path) -> dict[str, str]:
    return {
        "branch": git_cmd(repo_root, "branch", "--show-current").strip(),
        "head": git_cmd(repo_root, "rev-parse", "HEAD").strip(),
        "tree": git_cmd(repo_root, "rev-parse", "HEAD^{tree}").strip(),
        "porcelain": git_cmd(
            repo_root, "status", "--porcelain=v1", "--untracked-files=all"
        ),
    }


def expand_argv(
    template: list[str],
    *,
    repo: str,
    image: str,
    evidence_root: str,
    candidate: str,
    writable: str,
) -> list[str]:
    mapping = {
        "{repo}": repo,
        "{image}": image,
        "{evidence_root}": evidence_root,
        "{candidate}": candidate,
        "{writable}": writable,
    }
    expanded: list[str] = []
    for token in template:
        for key, value in mapping.items():
            token = token.replace(key, value)
        expanded.append(token)
    return expanded


def semantic_argv_equal(expected: list[str], actual: list[str]) -> bool:
    return expected == actual


def parse_docker_volumes(argv: list[str]) -> list[tuple[str, str, str]]:
    volumes: list[tuple[str, str, str]] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        spec = None
        if token in {"-v", "--volume"} and index + 1 < len(argv):
            spec = argv[index + 1]
            index += 2
        elif token.startswith("--volume="):
            spec = token.split("=", 1)[1]
            index += 1
        else:
            index += 1
            continue
        parts = spec.split(":")
        host, container = parts[0], parts[1]
        mode = parts[2] if len(parts) > 2 else "rw"
        volumes.append((host, container, mode))
    return volumes


def assert_control_stage_mounts(argv: list[str], *, stage: str) -> None:
    joined = " ".join(argv)
    if "--network" not in argv or "none" not in argv:
        raise CloseoutError("control stage must set --network none")
    if "GIT_CONFIG_KEY_0=safe.directory" not in joined:
        raise CloseoutError("control stage missing narrow safe.directory")
    volumes = parse_docker_volumes(argv)
    saw_repo = saw_git = saw_mirror = saw_evidence = saw_vet = saw_control = False
    for host, container, mode in volumes:
        writable = mode != "ro"
        if "NExtSEEK-plan005" in host or "NExtSEEK-plan005" in container:
            saw_repo = True
            if writable:
                raise CloseoutError("writable repository mount refused")
        if host.endswith("/NExtSEEK/.git") or container.endswith("/NExtSEEK/.git"):
            saw_git = True
            if writable:
                raise CloseoutError("writable common Git mount refused")
        if host.rstrip("/").endswith("NExtSEEK-dev") or container.rstrip("/").endswith(
            "NExtSEEK-dev"
        ):
            saw_mirror = True
            if writable:
                raise CloseoutError("writable dev-mirror mount refused")
        if container in {"/all-evidence", "/vet-reports"} and writable:
            raise CloseoutError(f"writable {container} mount refused")
        if container == "/all-evidence":
            saw_evidence = True
        if container == "/vet-reports":
            saw_vet = True
        if container == "/control-output":
            saw_control = True
            if f"/control/{stage}" not in host:
                raise CloseoutError("control output stage path mismatch")
    if not saw_repo:
        raise CloseoutError("missing linked-worktree repository mount")
    if not saw_git:
        raise CloseoutError("missing common Git directory mount")
    if not saw_mirror:
        raise CloseoutError("missing NExtSEEK-dev mirror mount")
    if not saw_evidence:
        raise CloseoutError("missing read-only aggregate evidence mount")
    if not saw_vet:
        raise CloseoutError("missing read-only vet-report mount")
    if not saw_control:
        raise CloseoutError("missing exclusive /control-output mount")


def load_records(evidence_root: Path) -> list[dict[str, Any]]:
    records_dir = evidence_root / "records"
    if not records_dir.is_dir():
        raise CloseoutError("missing records directory")
    loaded: list[dict[str, Any]] = []
    for path in sorted(records_dir.glob("*/record.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["_record_path"] = str(path)
        loaded.append(payload)
    return loaded


def _parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_protocol_binding(
    records: list[dict[str, Any]],
    *,
    repo: str,
    evidence_root: Path,
    candidate: str,
) -> None:
    ids = [row["name"] for row in records]
    if len(ids) != len(set(ids)):
        raise CloseoutError("duplicate protocol record ids")
    extra = sorted(set(ids) - set(PROTOCOL_RECORD_IDS))
    missing = sorted(set(PROTOCOL_RECORD_IDS) - set(ids))
    if extra or missing:
        raise CloseoutError(
            f"unexpected or missing records extra={extra} missing={missing}"
        )
    ordered = sorted(records, key=lambda row: row["name"])
    if [row["name"] for row in ordered] != list(PROTOCOL_RECORD_IDS):
        raise CloseoutError(
            "protocol record ids are out of order; 16-final-gate must be last"
        )
    by_start = sorted(records, key=lambda row: row["start_time"])
    if [row["name"] for row in by_start] != list(PROTOCOL_RECORD_IDS):
        raise CloseoutError(
            "protocol record ids are out of order; 16-final-gate must be last"
        )
    rows = {row["id"]: row for row in protocol_rows()}
    for record in ordered:
        proto = rows[record["name"]]
        writable = str(evidence_root / proto["declared_output_namespace"])
        expected = expand_argv(
            proto["argv_template"],
            repo=repo,
            image=proto["image"] or IMMUTABLE_NEXTSEEK_IMAGE,
            evidence_root=str(evidence_root),
            candidate=candidate,
            writable=writable,
        )
        actual = list(record.get("argv") or [])
        if not semantic_argv_equal(expected, actual):
            raise CloseoutError(
                f"{record['name']}: argv does not match protocol template"
            )
        if int(record.get("exit_code", 1)) != 0:
            raise CloseoutError(f"{record['name']}: nonzero exit")
        if record.get("command_timeout_seconds") != COMMAND_TIMEOUT_SECONDS:
            raise CloseoutError(
                f"{record['name']}: missing 600-second command timeout field"
            )
        if record.get("sequence_budget_seconds") != SEQUENCE_BUDGET_SECONDS:
            raise CloseoutError(
                f"{record['name']}: missing 3600-second sequence budget field"
            )
        if (record.get("pre") or {}).get("head") not in {None, candidate}:
            if record["pre"].get("head") != candidate:
                raise CloseoutError(f"{record['name']}: record-time HEAD mismatch")
        ns = proto["declared_output_namespace"]
        before = record.get("evidence_root_before") or {}
        after = record.get("evidence_root_after") or {}
        allowed = (f"records/{record['name']}/", f"{ns}/")
        if not any(rel.startswith(f"records/{record['name']}/") for rel in after):
            raise CloseoutError(f"write+restore or removed evidence: {record['name']}")
        for rel, digest in after.items():
            if rel in before:
                if before[rel] != digest:
                    raise CloseoutError(f"prior evidence mutated: {rel}")
                continue
            if not rel.startswith(allowed):
                raise CloseoutError(f"undeclared evidence-root write: {rel}")
        for rel in before:
            if rel not in after:
                raise CloseoutError(f"write+restore or removed evidence: {rel}")


def validate_span(records: list[dict[str, Any]]) -> float:
    ordered = sorted(records, key=lambda row: row["name"])
    start = _parse_iso(ordered[0]["start_time"])
    end = _parse_iso(ordered[-1]["end_time"])
    span = (end - start).total_seconds()
    if span > SEQUENCE_BUDGET_SECONDS:
        raise CloseoutError("final-sequence wall-clock span exceeds 3600 seconds")
    if ordered[0]["start_time"] > ordered[-1]["end_time"]:
        raise CloseoutError("stale or inverted timestamps")
    now = datetime.now(start.tzinfo)
    if end > now.replace(year=now.year + 1):
        raise CloseoutError("future timestamp")
    return span


def validate_vet_reports(paths: list[Path], approved_sha: str) -> list[dict[str, str]]:
    if len(paths) != 3:
        raise CloseoutError("preflight requires exactly three vet reports")
    reports: list[dict[str, str]] = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        if approved_sha[:8] not in text and approved_sha not in text:
            raise CloseoutError(f"vet report does not name approved plan SHA: {path.name}")
        lowered = text.lower()
        if "pass" not in lowered and "clean" not in lowered:
            raise CloseoutError(f"vet report missing PASS/CLEAN verdict: {path.name}")
        reports.append({"path": str(path), "sha256": sha256_file(path)})
    return reports


def validate_signoffs(repo_root: Path, signoff_dir: Path) -> list[dict[str, Any]]:
    route = repo_root / "dmac_assistant/build_context/route_capabilities.json"
    actual_route = sha256_file(route)
    if actual_route != ROUTE_CAPABILITIES_SHA:
        raise CloseoutError(
            "STOP: route_capabilities.json SHA-256 changed from "
            f"{ROUTE_CAPABILITIES_SHA} to {actual_route}"
        )
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted(signoff_dir.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if payload.get("interpretation_source") != "user_stated":
            raise CloseoutError(f"sign-off {path.name} is not user_stated")
        if not (payload.get("quote") or "").strip():
            raise CloseoutError(f"sign-off {path.name} missing quote")
        artifacts = payload.get("artifact_paths") or []
        hashes = payload.get("final_byte_hashes") or {}
        if set(artifacts) != set(hashes):
            raise CloseoutError(f"sign-off {path.name} artifact/hash mismatch")
        for rel, expected in hashes.items():
            actual = sha256_file(repo_root / rel)
            if actual != expected:
                raise CloseoutError(
                    f"sign-off {path.name} made against earlier bytes: {rel}"
                )
        presented = presented_diff_hash(repo_root, list(artifacts))
        if payload.get("presented_diff_hash") != presented:
            raise CloseoutError(f"sign-off {path.name} presented diff hash mismatch")
        seen.add(payload["id"])
        records.append(payload)
    missing = [item for item in REQUIRED_SIGNOFF_IDS if item not in seen]
    if missing:
        raise CloseoutError(f"missing sign-off records: {missing}")
    return records


def load_neither_success(repo_root: Path) -> dict[str, Any]:
    evidence = (
        repo_root
        / "nextseek_api/cc_assistant/op_registry/route_example_evidence.json"
    )
    payload = json.loads(evidence.read_text(encoding="utf-8"))
    ids = list(payload.get("audit", {}).get("neither_success") or [])
    return {"count": len(ids), "query_ids": ids}


def refuse_nonempty_control_output(output_dir: Path, output_name: str) -> None:
    if not output_dir.exists():
        raise CloseoutError("control output directory missing")
    entries = [p for p in output_dir.iterdir() if p.name != output_name]
    if entries:
        raise CloseoutError("reused or nonempty control output stage")
    target = output_dir / output_name
    if target.exists():
        raise CloseoutError("control output reuse refused")
    try:
        target.resolve().relative_to(output_dir.resolve())
    except ValueError as exc:
        raise CloseoutError("output outside /control-output") from exc


def _baml_manifest(repo_root: Path, relative: str) -> dict[str, str]:
    root = repo_root / relative
    if not root.exists():
        return {}
    return {
        p.relative_to(root).as_posix(): sha256_file(p)
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def run_preflight(
    *,
    evidence_root: Path,
    repo_root: Path,
    approved_plan_sha: str,
    vet_reports: list[Path],
    output_path: Path,
    plan_path: Path,
    plan_mirror: Path,
    backup_path: Path,
    signoff_dir: Path,
    mirror_root: Path,
) -> dict[str, Any]:
    refuse_nonempty_control_output(output_path.parent, output_path.name)
    identity = git_identity(repo_root)
    if evidence_root.name != identity["head"]:
        raise CloseoutError("evidence directory basename must equal HEAD")
    if identity["porcelain"].strip():
        raise CloseoutError("dirty tree")
    plan_hash = sha256_file(plan_path)
    mirror_hash = sha256_file(plan_mirror)
    if plan_hash != approved_plan_sha:
        raise CloseoutError("plan SHA mismatch vs --approved-plan-sha")
    if plan_hash != mirror_hash:
        raise CloseoutError("unequal plan copies")
    backup_hash = sha256_file(backup_path)
    if backup_hash != BACKUP_SHA:
        raise CloseoutError("pre-hardening backup hash mismatch")
    vet = validate_vet_reports(vet_reports, approved_plan_sha)
    signoffs = validate_signoffs(repo_root, signoff_dir)
    records = load_records(evidence_root)
    validate_protocol_binding(
        records,
        repo=str(repo_root),
        evidence_root=evidence_root,
        candidate=identity["head"],
    )
    span = validate_span(records)
    commit_time = git_cmd(repo_root, "log", "-1", "--format=%cI").strip()
    for record in records:
        pre = record.get("pre") or {}
        post = record.get("post") or {}
        for snap in (pre, post):
            if snap.get("head") != identity["head"]:
                raise CloseoutError(f"{record['name']}: record-time HEAD mismatch")
            if snap.get("tree") != identity["tree"]:
                raise CloseoutError(f"{record['name']}: record-time tree mismatch")
        if record["start_time"] < commit_time:
            raise CloseoutError(
                f"{record['name']}: stale timestamp preceding final commit"
            )
    neither = load_neither_success(repo_root)
    baml_src = _baml_manifest(repo_root, "dmac_assistant/baml_src")
    baml_client = _baml_manifest(
        repo_root, "dmac_assistant/src/dmac_assistant/router/baml_client"
    )
    baseline_dir = Path(EVIDENCE_PARENT) / "base-a9d69522" / identity["head"]
    if (baseline_dir / "baml_src-manifest.json").is_file():
        baseline_src = json.loads(
            (baseline_dir / "baml_src-manifest.json").read_text(encoding="utf-8")
        )
        if isinstance(baseline_src, list) and set(baseline_src) != set(baml_src):
            raise CloseoutError("BAML source manifest drifted from baseline")
    if (baseline_dir / "baml_client-manifest.json").is_file():
        baseline_client = json.loads(
            (baseline_dir / "baml_client-manifest.json").read_text(encoding="utf-8")
        )
        if isinstance(baseline_client, list) and set(baseline_client) != set(baml_client):
            raise CloseoutError("BAML generated-client manifest drifted from baseline")
    payload = {
        "stage": "preflight",
        "verdict": "GREEN",
        "branch": identity["branch"],
        "head": identity["head"],
        "base": PLAN005_BASE_COMMIT,
        "tree": identity["tree"],
        "porcelain": identity["porcelain"],
        "plan_path": str(plan_path),
        "plan_hash": plan_hash,
        "plan_mirror_path": str(plan_mirror),
        "plan_mirror_hash": mirror_hash,
        "plan_byte_equal": True,
        "backup_path": str(backup_path),
        "backup_hash": backup_hash,
        "approved_plan_sha": approved_plan_sha,
        "immutable_nextseek_image": IMMUTABLE_NEXTSEEK_IMAGE,
        "immutable_validator_image": IMMUTABLE_VALIDATOR_IMAGE,
        "sequence_span_seconds": span,
        "records": [row["name"] for row in sorted(records, key=lambda r: r["name"])],
        "signoffs": signoffs,
        "vet_reports": vet,
        "baml_src_manifest": baml_src,
        "baml_generated_client_manifest": baml_client,
        "canonical_capabilities_sha256": sha256_file(
            repo_root / "chat_nextseek/src/chat_nextseek/context/capabilities.md"
        ),
        "baked_capabilities_sha256": sha256_file(
            repo_root
            / "docker/cc-runtime/build_context/plugins/nextseek/context/capabilities.md"
        ),
        "route_capabilities_sha256": ROUTE_CAPABILITIES_SHA,
        "plan018_neither_success": neither,
        "evidence_root": str(evidence_root),
        "mirror_root": str(mirror_root),
        "artifact_namespace_example": artifact_namespace("05-future-op"),
        "repo_root_template": REPO_ROOT_TEMPLATE,
        "approved_plan_sha_default": APPROVED_PLAN_SHA,
    }
    output_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def _require_cold_pass(path: Path) -> None:
    if not path.is_file():
        raise CloseoutError("missing cold-review artifact")
    text = path.read_text(encoding="utf-8")
    if "reviewer_kind: cold_subagent" not in text:
        raise CloseoutError("cold review missing provenance")
    if "subagent_id:" not in text:
        raise CloseoutError("cold review missing subagent_id")
    if "prompt_verbatim: true" not in text:
        raise CloseoutError("cold review missing prompt_verbatim")
    if re.search(r"verdict:\s*(PARTIAL|FAIL)", text, re.IGNORECASE):
        raise CloseoutError("cold review is not PASS")
    if re.search(r"verdict:\s*PASS", text, re.IGNORECASE) is None and not re.search(
        r"\bPASS\b", text
    ):
        raise CloseoutError("cold review is not PASS")


def run_finalize(
    *,
    evidence_root: Path,
    repo_root: Path,
    preflight_path: Path,
    cold_path: Path,
    output_path: Path,
) -> dict[str, Any]:
    refuse_nonempty_control_output(output_path.parent, output_path.name)
    identity = git_identity(repo_root)
    if identity["porcelain"].strip():
        raise CloseoutError("dirty tree")
    preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
    if preflight.get("head") != identity["head"]:
        raise CloseoutError("finalize HEAD drifted from preflight")
    _require_cold_pass(cold_path)
    payload = {
        "stage": "finalize",
        "verdict": "GREEN",
        "head": identity["head"],
        "preflight_sha256": sha256_file(preflight_path),
        "cold_review_sha256": sha256_file(cold_path),
        "cold_review_rel": str(COLD_REVIEW_REL),
        "plan018_neither_success": preflight.get("plan018_neither_success"),
        "records": preflight.get("records"),
        "evidence_root": str(evidence_root),
    }
    output_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return payload


def run_verify(*, finalize_path: Path, output_path: Path) -> dict[str, Any]:
    refuse_nonempty_control_output(output_path.parent, output_path.name)
    finalize = json.loads(finalize_path.read_text(encoding="utf-8"))
    if finalize.get("verdict") != "GREEN":
        raise CloseoutError("finalize verdict is not GREEN")
    payload = {
        "stage": "verify",
        "verdict": "GREEN",
        "finalize_sha256": sha256_file(finalize_path),
        "exit": 0,
        "backup_rel": BACKUP_REL,
        "plan_rel": PLAN_REL,
        "default_mirror": str(DEFAULT_MIRROR),
        "default_signoff_dir": str(DEFAULT_SIGNOFF_DIR),
    }
    output_path.write_text(
        json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    return payload

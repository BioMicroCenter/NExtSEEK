#!/usr/bin/env python3
"""Generate and validate the immutable Plan 018 V4-9 owned-surface manifest."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "plan018-v4-9-owned-surface/v1"
BASE_SHA = "6881b6a870d68a6efaeb483b111cb9244488c5f9"
SOURCE_SHA = "517dffd18554a409e5d8e4b7fe43c8ffbb03bb09"
OWNERSHIP_RULES = "evidence/plan018-v4-0-accepted-ownership-rules.json"
OWNERSHIP_RULES_SHA256 = "faf30fb866ed56520fa367dd5c25a408ffcc2f5a516a90240e9cdb7805db921b"
ORACLE_KINDS = frozenset({"coverage", "structural", "regeneration", "mutation", "justified_exclusion"})


# Closed oracle registry.  Each target is either a concrete validator or an
# executable test-glob; validation resolves it against the checked-out tree.
ORACLE_REGISTRY: dict[str, dict[str, str]] = {
    "nessie_coverage": {"kind": "coverage", "target": "test:nessie_tests/tests/test_*.py"},
    "nessie_schema": {"kind": "structural", "target": "test:nessie_tests/tests/test_strict_manifest_schema.py"},
    "nessie_policy_mutation": {"kind": "mutation", "target": "test:nessie_tests/tests/test_route_policy*.py"},
    "nessie_command": {"kind": "coverage", "target": "test:nessie_tests/tests_container/test_management_command.py"},
    "nessie_output_regeneration": {"kind": "regeneration", "target": "test:nessie_tests/tests/test_output_skill_scripts.py"},
    "eval_coverage": {"kind": "coverage", "target": "test:nextseek_api/eval/tests/test_*.py"},
    "eval_mutation": {"kind": "mutation", "target": "test:nextseek_api/eval/tests/test_*mutation*.py"},
    "eval_structural": {"kind": "structural", "target": "test:nextseek_api/eval/tests/test_v4_8_manifest.py"},
    "eval_regeneration": {"kind": "regeneration", "target": "test:nextseek_api/eval/tests/test_v14_*.py"},
    "cc_coverage": {"kind": "coverage", "target": "test:nextseek_api/cc_assistant/tests/test_*.py"},
    "cc_mutation": {"kind": "mutation", "target": "test:nextseek_api/cc_assistant/tests/test_*mutation*.py"},
    "cc_structural": {"kind": "structural", "target": "test:nextseek_api/cc_assistant/tests/test_*.py"},
    "migration_lineage": {"kind": "structural", "target": "validator:evidence/plan018-migration-leaf.json"},
    "baml_regeneration": {"kind": "regeneration", "target": "test:nextseek_api/cc_assistant/tests/test_router_family.py"},
    "docker_regeneration": {"kind": "regeneration", "target": "test:nextseek_api/cc_assistant/tests/test_eval_vendoring.py"},
    "lane_c_structural": {"kind": "structural", "target": "validator:evidence/plan018-v4-2-lane-c.sidecar.json"},
    "plan018_verifier": {"kind": "structural", "target": "test:scripts/test_plan018_verifier_support.py"},
    "lane_m_structural": {"kind": "structural", "target": "validator:evidence/plan018-v4-8-lane-m.sidecar.json"},
    "evidence_structural": {"kind": "structural", "target": "validator:evidence/plan018-preflight.json"},
    "documentation_review": {"kind": "justified_exclusion", "target": "validator:evidence/plan018-v4-0-ownership-map.md"},
    "validator_self": {"kind": "structural", "target": "test:scripts/test_plan018_v4_9_owned_surface.py"},
    "manifest_regeneration": {"kind": "regeneration", "target": "command:python3 scripts/plan018_v4_9_owned_surface.py generate --check"},
    "task_report": {"kind": "justified_exclusion", "target": "validator:evidence/plan018-preflight.json"},
}

CONTROL_ENTRIES: tuple[dict[str, Any], ...] = (
    {"path": "evidence/plan018-v4-0-accepted-ownership-rules.json", "classification": "accepted_ownership_rules", "cluster": "v4_9_owned_surface_control", "oracle": {"id": "evidence_structural", "target": ORACLE_REGISTRY["evidence_structural"]["target"]}, "rationale": "Machine-readable immutable transcription of the accepted ownership map.", "sources": ["task_1_control"], "change": "task_1_control"},
    {"path": "scripts/plan018_v4_9_owned_surface.py", "classification": "validator_tooling", "cluster": "v4_9_owned_surface_control", "oracle": {"id": "validator_self", "target": ORACLE_REGISTRY["validator_self"]["target"]}, "rationale": "Task 1 generator and validator.", "sources": ["task_1_control"], "change": "task_1_control"},
    {"path": "scripts/test_plan018_v4_9_owned_surface.py", "classification": "validator_test", "cluster": "v4_9_owned_surface_control", "oracle": {"id": "validator_self", "target": ORACLE_REGISTRY["validator_self"]["target"]}, "rationale": "Focused Git-bound validation regression suite.", "sources": ["task_1_control"], "change": "task_1_control"},
    {"path": "evidence/plan018-v4-9-owned-surface.json", "classification": "generated_manifest", "cluster": "v4_9_owned_surface_control", "oracle": {"id": "manifest_regeneration", "target": ORACLE_REGISTRY["manifest_regeneration"]["target"]}, "rationale": "Generated authoritative inventory.", "sources": ["task_1_control"], "change": "task_1_control"},
    {"path": ".superpowers/sdd/2026-08-13-plan018-v4-9/task-1-report.md", "classification": "task_report", "cluster": "v4_9_owned_surface_control", "oracle": {"id": "task_report", "target": ORACLE_REGISTRY["task_report"]["target"]}, "rationale": "Required Task 1 report, not product behavior.", "sources": ["task_1_control"], "change": "task_1_control"},
)

# Task 2 artifacts are controls over the immutable source inventory: they do
# not redefine historical ownership, but make its current coverage gate
# reproducible and visible to --current validation.
CONTROL_ENTRIES += (
    {"path": "evidence/plan018-v4-9-task2-ownership.json", "classification": "task_ownership", "cluster": "v4_9_task2", "oracle": {"id": "evidence_structural", "target": ORACLE_REGISTRY["evidence_structural"]["target"]}, "rationale": "Pinned task-to-path evolution mapping for Task 2.", "sources": ["task_2_control"], "change": "task_2_control"},
    {"path": "scripts/plan018_v4_9_task2_coverage.py", "classification": "validator_tooling", "cluster": "v4_9_task2", "oracle": {"id": "validator_self", "target": ORACLE_REGISTRY["validator_self"]["target"]}, "rationale": "Task 2 source-bound coverage validator.", "sources": ["task_2_control"], "change": "task_2_control"},
    {"path": "scripts/test_plan018_v4_9_task2_coverage.py", "classification": "validator_test", "cluster": "v4_9_task2", "oracle": {"id": "validator_self", "target": ORACLE_REGISTRY["validator_self"]["target"]}, "rationale": "Task 2 validator adversarial tests.", "sources": ["task_2_control"], "change": "task_2_control"},
    {"path": "nextseek_api/eval/tests/test_v4_9_task2_behavior.py", "classification": "test", "cluster": "v4_9_task2", "oracle": {"id": "eval_structural", "target": ORACLE_REGISTRY["eval_structural"]["target"]}, "rationale": "Task 2 behavioral and defensive-fault tests.", "sources": ["task_2_control"], "change": "task_2_control"},
    {"path": "evidence/plan018-v4-9-task2-coverage.raw.json", "classification": "evidence", "cluster": "v4_9_task2", "oracle": {"id": "evidence_structural", "target": ORACLE_REGISTRY["evidence_structural"]["target"]}, "rationale": "Raw Task 2 coverage evidence.", "sources": ["task_2_control"], "change": "task_2_control"},
    {"path": "evidence/plan018-v4-9-task2-coverage.json", "classification": "evidence", "cluster": "v4_9_task2", "oracle": {"id": "evidence_structural", "target": ORACLE_REGISTRY["evidence_structural"]["target"]}, "rationale": "Validated Task 2 coverage summary.", "sources": ["task_2_control"], "change": "task_2_control"},
    {"path": "evidence/plan018-v4-9-task2-evidence.json", "classification": "evidence", "cluster": "v4_9_task2", "oracle": {"id": "evidence_structural", "target": ORACLE_REGISTRY["evidence_structural"]["target"]}, "rationale": "Task 2 command, provenance, and no-external-effects evidence.", "sources": ["task_2_control"], "change": "task_2_control"},
    {"path": ".superpowers/sdd/2026-08-13-plan018-v4-9/task-2-report.md", "classification": "task_report", "cluster": "v4_9_task2", "oracle": {"id": "task_report", "target": ORACLE_REGISTRY["task_report"]["target"]}, "rationale": "Required Task 2 report.", "sources": ["task_2_control"], "change": "task_2_control"},
)


@dataclass(frozen=True)
class DiffRecord:
    status: str
    old_path: str | None
    new_path: str | None

    def as_json(self) -> dict[str, str | None]:
        return {"status": self.status, "old_path": self.old_path, "new_path": self.new_path}


@dataclass(frozen=True)
class Candidate:
    path: str
    sources: tuple[str, ...]
    change: str
    exists_at_source: bool


def _git(root: Path, *args: str, text: bool = True) -> str | bytes:
    completed = subprocess.run(["git", *args], cwd=root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return completed.stdout.decode() if text else completed.stdout


def _object_bytes(root: Path, commit: str, path: str) -> bytes:
    return _git(root, "show", f"{commit}:{path}", text=False)


def _blob_sha(root: Path, commit: str, path: str) -> str:
    return _git(root, "rev-parse", f"{commit}:{path}").strip()


def _parse_records(raw: bytes) -> list[DiffRecord]:
    # Name-status with rename detection preserves every deletion and both paths
    # of a rename/copy instead of collapsing to only the destination path.
    fields = raw.decode().split("\0")
    records: list[DiffRecord] = []
    index = 0
    while index < len(fields) - 1:
        status = fields[index]
        index += 1
        if not status:
            continue
        if status.startswith(("R", "C")):
            old_path, new_path = fields[index], fields[index + 1]
            index += 2
            records.append(DiffRecord(status, old_path, new_path))
        else:
            path = fields[index]
            index += 1
            records.append(DiffRecord(status, path if status.startswith("D") else None, None if status.startswith("D") else path))
    return records


def _records(root: Path, left: str, right: str) -> list[DiffRecord]:
    return _parse_records(_git(root, "diff", "--name-status", "--find-renames", "-z", f"{left}..{right}", text=False))


def _tree_paths(root: Path, commit: str) -> set[str]:
    return set(_git(root, "ls-tree", "-r", "--name-only", commit).splitlines())


def _accepted_rules(root: Path) -> dict[str, Any]:
    if hashlib.sha256((root / OWNERSHIP_RULES).read_bytes()).hexdigest() != OWNERSHIP_RULES_SHA256:
        raise ValueError("immutable ownership rules document digest mismatch")
    return json.loads((root / OWNERSHIP_RULES).read_text())


def _accepted_identity_errors(root: Path, identity: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    expected = _accepted_rules(root)["accepted_ownership"]
    actual = identity.get("accepted_ownership")
    if actual != expected:
        return ["immutable accepted ownership identity mismatch"]
    try:
        if _blob_sha(root, expected["acceptance_commit"], expected["acceptance_path"]) != expected["acceptance_blob_sha1"]:
            errors.append("immutable accepted ownership acceptance blob unavailable or changed")
        if _blob_sha(root, expected["map_commit"], expected["map_path"]) != expected["map_blob_sha1"]:
            errors.append("immutable accepted ownership map blob unavailable or changed")
        if hashlib.sha256(_object_bytes(root, expected["map_commit"], expected["map_path"])).hexdigest() != expected["map_bytes_sha256"]:
            errors.append("immutable accepted ownership map digest mismatch")
    except subprocess.CalledProcessError:
        errors.append("immutable accepted ownership blob unavailable")
    return errors


def _rule_paths(root: Path, source_sha: str) -> list[tuple[str, str, bool]]:
    tree = _tree_paths(root, source_sha)
    result: list[tuple[str, str, bool]] = []
    for rule in _accepted_rules(root)["rules"]:
        if rule["match"] == "exact":
            path = rule["path"]
            if path in tree or rule.get("include_if_absent"):
                result.append((path, rule["id"], path in tree))
        else:
            result.extend((path, rule["id"], True) for path in sorted(tree) if fnmatch.fnmatchcase(path, rule["glob"]))
    return result


def source_candidates(manifest: dict[str, Any], *, root: Path) -> list[Candidate]:
    identity = manifest["identity"]
    by_path: dict[str, Candidate] = {}
    for record in _records(root, identity["base_sha"], identity["source_sha"]):
        if record.new_path:
            by_path[record.new_path] = Candidate(record.new_path, ("base_to_source_diff",), record.status, True)
    for path, rule_id, exists in _rule_paths(root, identity["source_sha"]):
        previous = by_path.get(path)
        if previous:
            by_path[path] = Candidate(path, tuple(sorted((*previous.sources, f"accepted_ownership:{rule_id}"))), previous.change, exists)
        else:
            by_path[path] = Candidate(path, (f"accepted_ownership:{rule_id}",), "owned_unchanged" if exists else "declared_absent_ownership", exists)
    return [by_path[path] for path in sorted(by_path)]


def _entry(path: str, classification: str, cluster: str, oracle_id: str, rationale: str) -> dict[str, Any]:
    oracle = ORACLE_REGISTRY[oracle_id]
    return {"path": path, "classification": classification, "cluster": cluster, "oracle": {"id": oracle_id, "target": oracle["target"]}, "rationale": rationale}


def classify_path(path: str) -> dict[str, Any] | None:
    """Explicit surface classifier; no catch-all for executable extensions."""
    if path == "nextseek_api/cc_assistant/tests/conftest.py":
        return _entry(path, "test", "task0_shared_fixtures", "cc_structural", "Accepted exact shared-fixture ownership declaration.")
    if path.startswith(("nessie_tests/tests/", "nessie_tests/tests_container/")):
        oracle = "nessie_policy_mutation" if any(token in path for token in ("route_policy", "write_refusal", "inline_route")) else "nessie_schema"
        return _entry(path, "test", "v4_2_nessie", oracle, "Nessie regression/policy oracle.")
    if path == "nextseek_api/management/commands/nessie.py":
        return _entry(path, "production", "v4_2_nessie", "nessie_command", "Nessie management command.")
    if path.startswith("nessie_tests/") and path.endswith(".py"):
        return _entry(path, "production", "v4_2_nessie", "nessie_coverage", "Behavior-bearing Nessie harness module.")
    if path.startswith("nessie_tests/") and ("/output-skill" in path or path.endswith((".tpl", ".html", ".md"))):
        return _entry(path, "configuration", "v4_2_nessie_output", "nessie_output_regeneration", "Nessie output template/skill configuration is regeneration-bearing.")
    if path.startswith("nessie_tests/") and path.endswith((".json", ".tsv", ".sh")):
        return _entry(path, "generated", "v4_2_nessie", "nessie_schema", "Nessie corpus/probe/launcher structural input.")

    if path.startswith("nextseek_api/eval/tests/"):
        return _entry(path, "test", "eval_test_suite", "eval_mutation" if "mutation" in path else "eval_structural", "Evaluation test/mutation oracle.")
    if path.startswith("nextseek_api/eval/fit/vendor/"):
        return _entry(path, "production" if path.endswith(".py") else "configuration", "task_6_vendor_fit", "eval_coverage" if path.endswith(".py") else "eval_regeneration", "Vendored fit code/configuration.")
    if path.startswith("nextseek_api/eval/fit/") and path.endswith(".py"):
        return _entry(path, "production", "v4_4_posterior_fit", "eval_coverage", "Posterior fit/recovery behavior.")
    if path.startswith("nextseek_api/eval/") and path.endswith((".csv", ".json", ".yaml", ".yml")):
        return _entry(path, "generated", "eval_data_contract", "eval_structural", "Evaluation data contract.")
    if path.startswith("nextseek_api/eval/") and path.endswith(".py"):
        return _entry(path, "production", "v4_eval", "eval_coverage", "Behavior-bearing evaluation module.")
    if path.startswith("nextseek_api/cc_assistant/tests/"):
        return _entry(path, "test", "cc_assistant_v4", "cc_mutation" if "mutation" in path else "cc_structural", "CC Assistant test/mutation oracle.")
    if path in {"nextseek_api/cc_assistant/router.py", "nextseek_api/services/cc_assistant.py"}:
        return _entry(path, "production", "v4_6_online_router", "cc_coverage", "Online routing call path.")
    if path in {"nextseek_api/cc_assistant/family_labels.py", "nextseek_api/cc_assistant/posterior_selector.py", "nextseek_api/cc_assistant/transport_trace.py", "nextseek_api/cc_assistant/risk_overlay.py", "nextseek_api/cc_assistant/route_monitoring.py", "nextseek_api/cc_assistant/turn_ledger.py", "nextseek_api/cc_assistant/baml_introspect.py"}:
        return _entry(path, "production", "cc_assistant_v4", "cc_coverage", "Behavior-bearing CC Assistant module.")
    if path in {"nextseek_api/assistant/models_api.py", "nextseek_api/assistant/models_db.py"}:
        return _entry(path, "production", "task_1_ledger_models", "cc_coverage", "Ledger persistence model/API.")
    if path.startswith("nextseek_api/migrations/") and path.endswith(".py"):
        return _entry(path, "migration", "migration_lineage", "migration_lineage", "Schema migration lineage.")
    if path in {"dmac_assistant/baml_src/router.baml", "docker/cc-runtime/baml_src/router.baml"}:
        return _entry(path, "schema", "task_3_router_baml", "baml_regeneration", "Accepted router.baml ownership seam.")
    if path.endswith(".baml"):
        return _entry(path, "schema", "diff_only_baml", "baml_regeneration", "Diff-derived BAML surface, not accepted router.baml ownership.")
    if path == "docker/eval/Dockerfile":
        return _entry(path, "docker", "task_6_eval_container", "docker_regeneration", "Evaluation image definition.")
    if path in {"pyproject.toml", "uv.lock"}:
        return _entry(path, "dependency", "task_6_lockfile", "docker_regeneration", "Accepted Task 6 dependency/lockfile surface.")
    if path == "dmac/test_settings.py":
        return _entry(path, "configuration", "v4_test_harness", "lane_c_structural", "Lane C settings seam.")
    if path == "scripts/plan018_lane_m_mysql.sh":
        return _entry(path, "verifier", "v4_lane_m", "lane_m_structural", "Disposable Lane M launcher.")
    if path.startswith("scripts/") and path in {"scripts/plan018_v4_2_verifier.py", "scripts/plan018_v4_3_verifier.py", "scripts/plan018_v4_4_verifier.py", "scripts/plan018_v4_5_verifier.py", "scripts/plan018_v4_6_verifier.py", "scripts/plan018_v4_7_verifier.py", "scripts/plan018_v4_8_verifier.py", "scripts/plan018_verifier_support.py", "scripts/test_plan018_verifier_support.py"}:
        return _entry(path, "verifier", "v4_verification", "plan018_verifier", "Named Plan 018 verifier support surface.")
    if path.startswith("evidence/") and path.endswith((".json", ".xml", ".log", ".md", ".txt", ".tsv")):
        return _entry(path, "evidence", "plan018_evidence", "evidence_structural", "Evidence artifact with provenance/structural oracle.")
    if path.startswith(".superpowers/sdd/") and path.endswith((".md", ".diff")):
        return _entry(path, "evidence", "plan018_sdd", "evidence_structural", "SDD evidence artifact.")
    if path == "CLAUDE.md" or path.startswith("docs/"):
        return _entry(path, "docs", "plan018_docs", "documentation_review", "Documentation-only surface.")
    return None


def _source_identity(root: Path, base_sha: str, source_sha: str) -> dict[str, Any]:
    rules = _accepted_rules(root)
    return {
        "base_sha": base_sha,
        "source_sha": source_sha,
        "source_tree_sha": _git(root, "rev-parse", f"{source_sha}^{{tree}}").strip(),
        "base_to_source_records_sha256": hashlib.sha256(json.dumps([record.as_json() for record in _records(root, base_sha, source_sha)], sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "accepted_ownership": rules["accepted_ownership"],
        "accepted_ownership_rules_sha256": OWNERSHIP_RULES_SHA256,
    }


def generate_manifest(*, root: Path, base_sha: str = BASE_SHA, source_sha: str = SOURCE_SHA) -> dict[str, Any]:
    identity = _source_identity(root, base_sha, source_sha)
    seed = {"identity": identity}
    entries: list[dict[str, Any]] = []
    for candidate in source_candidates(seed, root=root):
        entry = classify_path(candidate.path)
        if entry is None:
            raise ValueError(f"unclassified source candidate: {candidate.path}")
        entry.update({"sources": list(candidate.sources), "change": candidate.change, "exists_at_source": candidate.exists_at_source})
        entries.append(entry)
    manifest = {"schema": SCHEMA, "identity": identity, "source_diff_records": [record.as_json() for record in _records(root, base_sha, source_sha)], "candidate_derivation": {"union": ["exact_base_to_source_records", "immutable_accepted_ownership_rules"], "ownership_rules": OWNERSHIP_RULES, "future_path_policy": "validate --current rejects additions, deletions, and renames unless an explicit current change is recorded."}, "entries": entries, "control_entries": list(CONTROL_ENTRIES)}
    manifest["summary"] = {"source_candidates": len(entries), "control_entries": len(CONTROL_ENTRIES), "by_classification": {kind: sum(entry["classification"] == kind for entry in entries) for kind in sorted({entry["classification"] for entry in entries})}}
    return manifest


def _target_exists(root: Path, target: str) -> bool:
    kind, value = target.split(":", 1)
    if kind in {"test", "validator"}:
        return bool(list(root.glob(value))) if any(character in value for character in "*?[") else (root / value).exists()
    if kind == "command":
        parts = value.split()
        return parts[:2] == ["python3", "scripts/plan018_v4_9_owned_surface.py"] and (root / parts[1]).exists()
    return False


def oracle_errors(entry: dict[str, Any], *, root: Path) -> list[str]:
    oracle = entry.get("oracle", {})
    definition = ORACLE_REGISTRY.get(oracle.get("id"))
    if definition is None:
        return [f"unknown oracle id for {entry.get('path')}"]
    if oracle.get("target") != definition["target"]:
        return [f"oracle target mismatch for {entry.get('path')}"]
    if definition["kind"] not in ORACLE_KINDS or not _target_exists(root, definition["target"]):
        return [f"unresolvable oracle for {entry.get('path')}"]
    return []


def _current_records(root: Path, source_sha: str) -> list[DiffRecord]:
    records = _records(root, source_sha, "HEAD")
    records.extend(_parse_records(_git(root, "diff", "--cached", "--name-status", "--find-renames", "-z", text=False)))
    records.extend(_parse_records(_git(root, "diff", "--name-status", "--find-renames", "-z", text=False)))
    records.extend(DiffRecord("??", None, path) for path in _git(root, "ls-files", "--others", "--exclude-standard").splitlines() if path)
    return records


def validate_manifest(manifest: dict[str, Any], *, root: Path, additional_paths: Iterable[str] = (), include_current: bool = False) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema") != SCHEMA:
        return [f"schema must be {SCHEMA}"]
    identity = manifest.get("identity", {})
    if identity.get("base_sha") != BASE_SHA or identity.get("source_sha") != SOURCE_SHA:
        errors.append("manifest base/source identity mismatch")
    try:
        expected_identity = _source_identity(root, identity["base_sha"], identity["source_sha"])
    except (KeyError, subprocess.CalledProcessError):
        return errors + ["source SHA is not a resolvable commit"]
    for key in ("source_tree_sha", "base_to_source_records_sha256", "accepted_ownership_rules_sha256"):
        if identity.get(key) != expected_identity[key]:
            errors.append(f"source identity drift: {key}")
    errors.extend(_accepted_identity_errors(root, identity))
    expected_records = [record.as_json() for record in _records(root, identity["base_sha"], identity["source_sha"])]
    if manifest.get("source_diff_records") != expected_records:
        errors.append("source diff records mismatch (deletions/rename sides must be preserved)")
    expected_controls = list(CONTROL_ENTRIES)
    if manifest.get("control_entries") != expected_controls:
        errors.append("control_entries do not equal the exact generated CONTROL_ENTRIES set")

    candidates = source_candidates(manifest, root=root)
    expected_paths = {candidate.path for candidate in candidates}
    entries = manifest.get("entries", [])
    by_path = {entry.get("path"): entry for entry in entries}
    if len(entries) != len(by_path):
        errors.append("duplicate source entry path")
    if set(by_path) != expected_paths:
        errors.append("source candidate set does not equal authoritative manifest entries")
    candidate_by_path = {candidate.path: candidate for candidate in candidates}
    for path, candidate in candidate_by_path.items():
        entry = by_path.get(path)
        if entry is None:
            continue
        expected = classify_path(path)
        if expected is None or any(entry.get(key) != expected[key] for key in ("classification", "cluster", "oracle", "rationale")):
            errors.append(f"classification/oracle rule mismatch for {path}")
        if entry.get("sources") != list(candidate.sources) or entry.get("change") != candidate.change or entry.get("exists_at_source") is not candidate.exists_at_source:
            errors.append(f"candidate derivation mismatch for {path}")
        errors.extend(oracle_errors(entry, root=root))
        if entry.get("classification") == "production" and ORACLE_REGISTRY.get(entry.get("oracle", {}).get("id"), {}).get("kind") not in {"coverage", "mutation"}:
            errors.append(f"behavior-bearing production path lacks coverage/mutation oracle: {path}")
    for control in manifest.get("control_entries", []):
        errors.extend(oracle_errors(control, root=root))

    if include_current:
        known = expected_paths | {control["path"] for control in CONTROL_ENTRIES}
        for record in _current_records(root, identity["source_sha"]):
            if record.status.startswith("D") and record.old_path in expected_paths:
                errors.append(f"owned path deleted without explicit current change: {record.old_path}")
            elif record.status.startswith(("R", "C")) and record.old_path in expected_paths:
                errors.append(f"owned path renamed without explicit current change: {record.old_path} -> {record.new_path}")
            elif record.new_path and record.new_path not in known:
                errors.append(("unclassified new path" if classify_path(record.new_path) is None else "new path absent from authoritative manifest") + f": {record.new_path}")
    for path in additional_paths:
        if path not in expected_paths and path not in {control["path"] for control in CONTROL_ENTRIES}:
            errors.append(("unclassified new path" if classify_path(path) is None else "new path absent from authoritative manifest") + f": {path}")
    return sorted(set(errors))


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "validate"))
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--manifest", type=Path, default=Path("evidence/plan018-v4-9-owned-surface.json"))
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--current", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    manifest_path = args.manifest if args.manifest.is_absolute() else root / args.manifest
    if args.command == "generate":
        generated = generate_manifest(root=root)
        if args.check:
            if not manifest_path.exists() or manifest_path.read_bytes() != _json_bytes(generated):
                print("FAIL: generated manifest differs from checked-in bytes", file=sys.stderr)
                return 1
            print(f"PASS: {manifest_path.relative_to(root)} is reproducible ({len(generated['entries'])} source candidates)")
            return 0
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_bytes(_json_bytes(generated))
        print(f"WROTE: {manifest_path.relative_to(root)} ({len(generated['entries'])} source candidates)")
        return 0
    try:
        errors = validate_manifest(json.loads(manifest_path.read_text()), root=root, include_current=args.current)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot read manifest: {exc}", file=sys.stderr)
        return 1
    if errors:
        print("FAIL: owned-surface validation", file=sys.stderr)
        print(*(f"- {error}" for error in errors), sep="\n", file=sys.stderr)
        return 1
    print("PASS: owned-surface manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

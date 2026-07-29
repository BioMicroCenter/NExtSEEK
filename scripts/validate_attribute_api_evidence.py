#!/usr/bin/env python3
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

_CORPUS_VERIFIER_PATH = Path(
    "/home/taishajo/work/state/attribute-viewset/verification/verify_corrupt_corpus.py"
)
_corpus_spec = importlib.util.spec_from_file_location(
    "verify_corrupt_corpus", _CORPUS_VERIFIER_PATH
)
_corpus_verifier = importlib.util.module_from_spec(_corpus_spec)
assert _corpus_spec.loader is not None
_corpus_spec.loader.exec_module(_corpus_verifier)

MANIFEST_PATH = Path("/home/taishajo/work/state/attribute-viewset/VERIFICATION-MANIFEST.json")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
TASK_DEPENDENCIES = {
    "task-00": (), "task-01": ("task-00",), "task-02": ("task-01",),
    "task-03": ("task-00", "task-01"), "task-04": ("task-01", "task-03"),
    "task-05": ("task-01", "task-02", "task-03", "task-04"),
    "task-06": ("task-00", "task-03"), "task-07": ("task-03", "task-05", "task-06"),
    "task-08": ("task-02", "task-03", "task-07"),
    "task-09": ("task-02", "task-04", "task-05", "task-07", "task-08"),
    "task-10": ("task-09",), "task-11": ("task-10",),
}
TASK_REQUIRED_LANES = {
    "task-00": {"unit", "db", "collect", "lint", "coverage", "full"},
    "task-01": {"unit", "openapi", "coverage", "full"},
    "task-02": {"unit", "db", "collect", "lint", "coverage", "mutants", "full"},
    "task-03": {"unit", "db", "schema", "collect", "lint", "coverage"},
    "task-04": {"unit", "db", "collect", "lint", "coverage"},
    "task-05": {"unit", "db", "collect", "lint", "coverage", "mutants"},
    "task-06": {"unit", "db", "benchmark", "coverage", "collect", "lint"},
    "task-07": {"unit", "db", "coverage", "collect", "lint"},
    "task-08": {"unit", "db", "worker", "coverage", "collect", "lint"},
    "task-09": {"unit", "db", "openapi", "coverage", "collect", "lint"},
    "task-10": {"unit", "benchmark", "coverage", "collect", "lint"},
    "task-11": {"unit", "db", "schema", "worker", "openapi", "benchmark", "coverage", "collect", "lint", "raw-full", "full", "mutants"},
}
TASK_REQUIRED_MUTANTS = {
    "task-02": ("M-AUTH-01", "M-AUTH-02", "M-CANCEL-02", "M-AUTH-FALLBACK-01",
                "M-AUTH-PRIORITY-01", "M-AUTH-XSEEK-01", "M-AUTH-PERSON-01",
                "M-AUTH-ROLETYPE-01", "M-AUTH-SCOPEROLE-01", "M-AUTH-ALIAS-01",
                "M-AUTH-CACHE-01", "M-AUTH-ADAPTER-01", "M-AUTH-SIGNATURE-01",
                "M-AUTH-SESSION-01", "M-CANCEL-DENY-01"),
    "task-05": ("M-AUTH-01", "M-AUTH-02", "M-CANCEL-02", "M-UID-01", "M-TITLE-01", "M-DRY-01"),
    "task-11": ("M-AUTH-01", "M-AUTH-02", "M-UID-01", "M-TITLE-01", "M-DRY-01",
                "M-LOCK-01", "M-VERSION-01", "M-TXN-01", "M-RECOVER-01",
                "M-DELIVERY-01", "M-CANCEL-01", "M-CANCEL-02", "M-HTTP-01",
                "M-ROUTE-01", "M-WORKER-01"),
}
ZERO_COUNT_LANES = {"collect", "lint"}


class Rejected(Exception):
    def __init__(self, code):
        self.code = code


def exclusive_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2, sort_keys=True); stream.write("\n")
            stream.flush(); os.fsync(stream.fileno())
        os.link(temporary, path)
        os.chmod(path, 0o444)
    finally:
        Path(temporary).unlink(missing_ok=True)


def nested_set(value, pointer, replacement):
    """Set one existing control leaf; JSON Pointer preserves keys containing dots."""
    if pointer.startswith("/"):
        parts = [part.replace("~1", "/").replace("~0", "~")
                 for part in pointer[1:].split("/")]
        target = value
        for part in parts[:-1]:
            target = target[int(part)] if isinstance(target, list) else target[part]
        final = parts[-1]
        if isinstance(target, list):
            target[int(final)] = replacement
        else:
            target[final] = replacement
        return
    parts = pointer.split(".")
    if parts[0] == "payload":
        target = value
    else:
        target = value["payload"]
    for part in parts[:-1]:
        match = re.fullmatch(r"(.+)\[(\d+)\]", part)
        target = target[match.group(1)][int(match.group(2))] if match else target[part]
    final = parts[-1]
    match = re.fullmatch(r"(.+)\[(\d+)\]", final)
    if match:
        target[match.group(1)][int(match.group(2))] = replacement
    else:
        target[final] = replacement


def nested_remove_existing(value, pointer):
    """Remove exactly one existing JSON-Pointer member; missing targets fail closed."""
    if not pointer.startswith("/") or pointer == "/":
        raise KeyError(pointer)
    parts = [part.replace("~1", "/").replace("~0", "~")
             for part in pointer[1:].split("/")]
    target = value
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    final = parts[-1]
    if isinstance(target, list):
        del target[int(final)]
    else:
        del target[final]


def source_tree_hash(cwd):
    raw_paths = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=cwd,
    ).split(b"\0")
    digest = hashlib.sha256()
    for raw in sorted(path for path in raw_paths if path):
        path = cwd / raw.decode()
        stat = path.lstat()
        content = path.readlink().as_posix().encode() if path.is_symlink() else path.read_bytes()
        digest.update(len(raw).to_bytes(8, "big") + raw)
        digest.update(stat.st_mode.to_bytes(8, "big"))
        digest.update(len(content).to_bytes(8, "big") + content)
    return digest.hexdigest()


def observed_dependencies(cwd, head, task_id):
    result = []
    for dependency in TASK_DEPENDENCIES[task_id]:
        commits = subprocess.check_output(
            ["git", "log", head, "--format=%H", "--grep", f"^Attribute-Task: {dependency} "],
            cwd=cwd, text=True,
        ).splitlines()
        if not commits:
            raise Rejected("E_DEPENDENCY_SHA_MISMATCH")
        result.append({"task_id": dependency, "sha": commits[0]})
    return result


def validate(record, manifest, artifact_root=None, logical_only=False, runner_exit=None):
    for field in manifest["evidence_record_contract"]["required_fields"]:
        if field not in record:
            raise Rejected("E_REQUIRED_FIELD")
    if runner_exit is not None and record["exit_code"] != runner_exit:
        raise Rejected("E_FORGED_EXIT_CODE")
    if record["exit_code"] != 0:
        raise Rejected("E_NONZERO_EXIT")
    expected_argv = manifest["runner_contract"]["commands"].get(record["lane"])
    if expected_argv is None or record["argv"] != expected_argv:
        raise Rejected("E_ARGV_MISMATCH")
    if not re.fullmatch(r"task-(?:0[0-9]|1[01])", str(record["task_id"])):
        raise Rejected("E_TASK_ID")
    if record["base_sha"] != manifest["source_identity"]["base_sha"]:
        raise Rejected("E_BASE_SHA_MISMATCH")
    if record["plan_sha256"] != manifest["source_identity"]["plan_sha256"]:
        raise Rejected("E_PLAN_SHA_MISMATCH")
    if record["decisions_sha256"] != manifest["source_identity"]["decisions_sha256"]:
        raise Rejected("E_DECISIONS_SHA_MISMATCH")
    if record["lane"] in {"full", "raw-full"} and record["collected"] < manifest["minimum_collected_tests"]["full"]:
        raise Rejected("E_TEST_MINIMUM")
    dependencies = record["dependency_shas"]
    if not isinstance(dependencies, list) or tuple(item.get("task_id") for item in dependencies if isinstance(item, dict)) != TASK_DEPENDENCIES[record["task_id"]]:
        raise Rejected("E_DEPENDENCY_SHA_MISMATCH")
    if any(not isinstance(item, dict) or set(item) != {"task_id", "sha"} or not COMMIT_SHA.fullmatch(str(item["sha"])) for item in dependencies):
        raise Rejected("E_DEPENDENCY_SHA_MISMATCH")
    if not COMMIT_SHA.fullmatch(str(record["base_sha"])) or not COMMIT_SHA.fullmatch(str(record["task_head_sha"])):
        raise Rejected("E_BASE_SHA_MISMATCH")
    raw_cwd = Path(record["cwd"])
    cwd_is_git = (
        not logical_only
        and raw_cwd.is_dir()
        and not raw_cwd.is_symlink()
        and subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=raw_cwd,
            capture_output=True, text=True,
        ).stdout.strip() == "true"
    )
    if not logical_only and not cwd_is_git and artifact_root is None:
        raise Rejected("E_CWD")
    if cwd_is_git:
        cwd = raw_cwd.resolve(strict=True)
        head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=cwd, text=True).strip()
        if record["task_head_sha"] != head:
            raise Rejected("E_BASE_SHA_MISMATCH")
        if dependencies != observed_dependencies(cwd, head, record["task_id"]):
            raise Rejected("E_DEPENDENCY_SHA_MISMATCH")
        if record["source_tree_sha256"] != source_tree_hash(cwd):
            raise Rejected("E_SOURCE_TREE")
    if record["collected"] == 0 and record["lane"] not in ZERO_COUNT_LANES:
        raise Rejected("E_ZERO_COLLECTED")
    if record["lane"] == "db":
        db_contract = manifest["runner_contract"]["db_lane_contract"][record["task_id"]]
        if record["collected"] < db_contract["minimum_collected"]:
            raise Rejected("E_TEST_MINIMUM")
    if record["failed"]:
        raise Rejected("E_UNEXPECTED_FAILURE")
    if any(record[name] for name in ("skipped", "xfailed", "deselected")):
        raise Rejected("E_TEST_SELECTION_DRIFT")
    if record["collected"] != sum(record[name] for name in ("passed", "failed", "skipped", "xfailed", "deselected")):
        raise Rejected("E_TEST_COUNTS")
    if record["image_id"] != manifest["source_identity"]["reference_image_id"]:
        raise Rejected("E_IMAGE_ID")
    identity = record["database_server_identity"] or {}
    required_identity = {"server_uuid", "hostname", "port", "version", "database_name"}
    if not required_identity <= set(identity) or not all(identity.get(key) not in (None, "") for key in required_identity) or identity.get("database_name") in manifest["shared_state_safety"]["forbidden_database_names"] or record["disposable_database_uuid"] in (None, "", "00000000-0000-0000-0000-000000000000"):
        raise Rejected("E_DATABASE_IDENTITY")
    for name in ("stdout_sha256", "stderr_sha256", "source_tree_sha256"):
        if not SHA256.fullmatch(str(record[name])):
            raise Rejected("E_HASH_FORMAT")
    for name in ("started_at", "finished_at"):
        try:
            datetime.fromisoformat(record[name].replace("Z", "+00:00"))
        except (TypeError, ValueError):
            raise Rejected("E_TIMESTAMP_FORMAT")
    coverage_sources = record.get("coverage_sources")
    if (coverage_sources is not None or record["lane"] in {"coverage", "raw-full"}) and coverage_sources != manifest["coverage_contract"]["required_source_paths"]:
        raise Rejected("E_COVERAGE_SOURCE")
    if record["lane"] in {"coverage", "raw-full"} and not logical_only:
        cwd = Path(record["cwd"])
        observed_hashes = {}
        for relative in coverage_sources:
            source = cwd / relative
            files = sorted(source.rglob("*.py")) if source.is_dir() else [source]
            for path in files:
                observed_hashes[path.relative_to(cwd).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        if record.get("coverage_source_hashes") != observed_hashes:
            raise Rejected("E_COVERAGE_SOURCE_HASH")
        report_path = artifact_root / "coverage.json"
        report = json.loads(report_path.read_text())
        reported = {Path(name).as_posix() for name in report.get("files", {})}
        if any(not any(name == relative or name.endswith(f"/{relative}") for name in reported) for relative in observed_hashes):
            raise Rejected("E_COVERAGE_SOURCE")
    seen = set()
    for artifact in record["artifacts"]:
        path = Path(artifact["path"])
        if path.is_absolute() or ".." in path.parts:
            raise Rejected("E_ARTIFACT_PATH")
        if artifact["path"] in seen:
            raise Rejected("E_ARTIFACT_DUPLICATE")
        seen.add(artifact["path"])
        if not SHA256.fullmatch(str(artifact["sha256"])):
            raise Rejected("E_HASH_FORMAT")
        if artifact_root is not None and not logical_only:
            full = artifact_root / path
            resolved_root = artifact_root.resolve(strict=True)
            current = artifact_root
            for part in path.parts:
                current = current / part
                if current.is_symlink():
                    raise Rejected("E_ARTIFACT_SYMLINK")
            if not full.resolve(strict=False).is_relative_to(resolved_root):
                raise Rejected("E_ARTIFACT_PATH")
            if not full.is_file() or full.stat().st_size != artifact["size_bytes"] or hashlib.sha256(full.read_bytes()).hexdigest() != artifact["sha256"]:
                raise Rejected("E_ARTIFACT_CHECKSUM")
            if full.stat().st_mode & 0o222:
                raise Rejected("E_ARTIFACT_WRITABLE")
        if artifact["path"] in {"stdout.log", f"{record['lane']}.stdout.log"} and artifact["sha256"] != record["stdout_sha256"]:
            raise Rejected("E_ARTIFACT_CHECKSUM")
        if artifact["path"] in {"stderr.log", f"{record['lane']}.stderr.log"} and artifact["sha256"] != record["stderr_sha256"]:
            raise Rejected("E_ARTIFACT_CHECKSUM")
    required_artifacts = set(manifest["runner_contract"]["required_artifacts_by_lane"][record["lane"]])
    required_artifacts.update(manifest["runner_contract"]["required_artifact_overlays_by_task_lane"].get(
        f"{record['task_id']}:{record['lane']}", []))
    if record["lane"] == "mutants":
        required_artifacts.update(
            f"mutant-{mutant_id}.json"
            for mutant_id in manifest["critical_mutants_by_task"][record["task_id"]]
        )
    if not required_artifacts <= seen:
        raise Rejected("E_REQUIRED_ARTIFACT")
    boundary_path = artifact_root / "boundary-identity.json" if artifact_root is not None else None
    evidence_boundary = boundary_path is not None and boundary_path.is_file()
    if evidence_boundary and not logical_only:
        boundary = json.loads(boundary_path.read_text())
        if boundary.get("torn_down") is not True:
            raise Rejected("E_DATABASE_IDENTITY")
        if boundary.get("teardown_server_uuid") != boundary.get("server_identity", {}).get("server_uuid"):
            raise Rejected("E_DATABASE_IDENTITY")
        if boundary.get("database_uuid") != record["disposable_database_uuid"]:
            raise Rejected("E_DATABASE_IDENTITY")
        network = boundary.get("network_name")
        if not network or subprocess.run(
            ["docker", "network", "inspect", network], capture_output=True,
        ).returncode == 0:
            raise Rejected("E_BOUNDARY_NOT_TORN_DOWN")
    if record["lane"] == "collect" and evidence_boundary and not logical_only:
        nodes = json.loads((artifact_root / "collected-nodeids.json").read_text())
        assertions = json.loads((artifact_root / "assertion_counts.json").read_text())
        if len(nodes) < manifest["minimum_collected_tests"]["full"] or len(nodes) != len(set(nodes)):
            raise Rejected("E_TEST_MINIMUM")
        if set(assertions) != set(nodes) or any(not isinstance(value, int) or value < 0 for value in assertions.values()):
            raise Rejected("E_ASSERTION_COUNTS")
    if record["lane"] in {"full", "raw-full"} and evidence_boundary and not logical_only:
        rows = json.loads((artifact_root / "node-results.json").read_text())
        allowed = {"passed", "failed", "skipped", "xfailed", "deselected"}
        if (not isinstance(rows, list) or rows != sorted(rows, key=lambda row: row.get("nodeid", ""))
                or any(set(row) != {"nodeid", "outcome"} or row["outcome"] not in allowed for row in rows)
                or len({row["nodeid"] for row in rows}) != len(rows)
                or len(rows) != record["collected"]):
            raise Rejected("E_NODE_RESULTS")
        observed = {name: sum(row["outcome"] == name for row in rows) for name in allowed}
        if any(observed[name] != record[name] for name in allowed):
            raise Rejected("E_TEST_COUNTS")
    if record["lane"] == "mutants" and evidence_boundary and not logical_only:
        report = json.loads((artifact_root / "mutants.json").read_text())
        keys = {"killed", "survived", "timed_out", "skipped", "errored"}
        declared = tuple(manifest["critical_mutants_by_task"].get(record["task_id"], ()))
        if declared != TASK_REQUIRED_MUTANTS.get(record["task_id"]):
            raise Rejected("E_MUTANT_REPORT")
        expected = set(declared)
        flattened = [item for key in keys for item in report.get(key, [])]
        if (set(report) != keys or any(values != sorted(set(values)) for values in report.values())
                or len(flattened) != len(set(flattened)) or set(flattened) != expected):
            raise Rejected("E_MUTANT_REPORT")
        adapters_path = Path(manifest["artifact_paths"]["mutation_adapters"])
        if hashlib.sha256(adapters_path.read_bytes()).hexdigest() != manifest["artifact_paths"]["mutation_adapters_sha256"]:
            raise Rejected("E_MUTANT_REPORT")
        rules = {row["id"]: row for row in json.loads(adapters_path.read_text())["rules"]}
        expected_map = {row["id"]: (row["symbol"], row["required_killer"])
                        for row in manifest["critical_mutants"]}
        if ({key: (row["symbol"], row["killer"]) for key, row in rules.items()}
                != expected_map):
            raise Rejected("E_MUTANT_REPORT")
        handshake_keys = {"schema_version", "mutant_id", "source_path", "symbol",
                          "expected_matches", "observed_matches", "pre_sha256",
                          "mutated_sha256", "loaded_mutated_sha256", "restored_sha256",
                          "applied", "restored"}
        for mutant_id in expected:
            rule = rules[mutant_id]
            handshake = artifact_root / f"mutant-{mutant_id}.json"
            if not handshake.is_file():
                raise Rejected("E_MUTANT_REPORT")
            proof = json.loads(handshake.read_text())
            wanted = [1] * len(rule["transforms"])
            if (set(proof) != handshake_keys or proof["mutant_id"] != mutant_id
                    or proof["source_path"] != rule["path"] or proof["symbol"] != rule["symbol"]
                    or proof["expected_matches"] != wanted or proof["observed_matches"] != wanted
                    or proof["pre_sha256"] == proof["mutated_sha256"]
                    or proof["loaded_mutated_sha256"] != proof["mutated_sha256"]
                    or proof["restored_sha256"] != proof["pre_sha256"]
                    or proof["applied"] is not True or proof["restored"] is not True):
                raise Rejected("E_MUTANT_REPORT")
    return "ACCEPT"


def validate_corpus_subject(subject, manifest):
    """Validate a schema-valid control or its one-fault neighbor without consulting fixture ID."""
    if set(subject) != {"domain", "payload"}:
        raise Rejected("E_REQUIRED_FIELD")
    domain, payload = subject["domain"], subject["payload"]
    if domain == "evidence_record":
        try:
            _corpus_verifier.validate_evidence(payload, manifest)
        except _corpus_verifier.Rejected as exc:
            raise Rejected(exc.code) from exc
        return "ACCEPT"
    if domain == "semantic_artifacts":
        validate_semantic_artifacts(payload, manifest)
        return "ACCEPT"
    if domain in {"report_generation", "telemetry"}:
        try:
            _corpus_verifier.validate_subject(subject, manifest)
        except _corpus_verifier.Rejected as exc:
            raise Rejected(exc.code) from exc
        return "ACCEPT"
    raise Rejected("E_REQUIRED_FIELD")


def _json_no_duplicates(text):
    def pairs(rows):
        value = {}
        for key, item in rows:
            if key in value:
                raise Rejected("E_SEMANTIC_SECTION_MISSING")
            value[key] = item
        return value
    return json.loads(text, object_pairs_hook=pairs)


def _markdown_tables(text, required_headings):
    headings = re.findall(r"^## (.+)$", text, flags=re.MULTILINE)
    if headings != required_headings:
        raise Rejected("E_SEMANTIC_SECTION_MISSING")
    sections = {}
    for index, heading in enumerate(headings):
        start = text.index(f"## {heading}") + len(f"## {heading}")
        end = text.index(f"## {headings[index + 1]}", start) if index + 1 < len(headings) else len(text)
        body = text[start:end].strip()
        if not body:
            raise Rejected("E_SEMANTIC_SECTION_MISSING")
        lines = [line for line in body.splitlines() if line.startswith("|")]
        if len(lines) < 2:
            sections[heading] = []
            continue
        keys = [cell.strip() for cell in lines[0].strip("|").split("|")]
        rows = []
        for line in lines[2:]:
            values = [cell.strip() for cell in line.strip("|").split("|")]
            if len(values) != len(keys):
                raise Rejected("E_SEMANTIC_SECTION_MISSING")
            rows.append(dict(zip(keys, values)))
        sections[heading] = rows
    return sections


def parse_semantic_artifact(name, text, artifact_contract):
    if name.endswith(".json"):
        payload = _json_no_duplicates(text)
        if set(payload) != set(artifact_contract["exact_top_level_keys"]):
            raise Rejected("E_SEMANTIC_SECTION_MISSING")
        if payload["schema_version"] != artifact_contract["schema_version"]:
            raise Rejected("E_SEMANTIC_SECTION_MISSING")
        if name == "DB-FACTS.json":
            commands = [{"argv": row["command_argv"], "command_sha256": row["command_sha256"]}
                        for row in payload["observations"]]
            classified = payload["observations"]
        else:
            commands = [{"argv": row["command_argv"], "command_sha256": row["command_sha256"]}
                        for row in payload["raw_sources"]]
            classified = payload["surfaces"]
        return {"commands": commands, "classified_records": classified}
    sections = _markdown_tables(text, artifact_contract["required_headings_in_order"])
    if name == "BASELINE.md":
        commands = [{"argv": json.loads(row["argv_json"]), "command_sha256": row["command_sha256"]}
                    for row in sections["Commands"]]
        classified = sections["Classified Records"]
    elif name == "TEST-LANES.md":
        commands = []
        for row in sections["Lane Matrix"]:
            argv = json.loads(row["argv_json"])
            encoded = json.dumps(argv, sort_keys=True, separators=(",", ":")).encode()
            commands.append({"argv": argv, "command_sha256": hashlib.sha256(encoded).hexdigest()})
        classified = []
    else:
        commands = [{"argv": json.loads(row["argv_json"]), "command_sha256": row["command_sha256"]}
                    for row in sections["Lane Matrix"]]
        classified = []
    return {"commands": commands, "classified_records": classified}


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def validate_contract_object(value, contract):
    if set(value) != set(contract["required"]):
        raise Rejected("E_SEMANTIC_NESTED_KEYS")


def validate_raw_source_rows(rows, raw_bytes, contract):
    if not isinstance(rows, list) or not rows:
        raise Rejected("E_SEMANTIC_RAW_SOURCE_EMPTY")
    for row in rows:
        if set(row) != set(contract["raw_source_exact_keys"]):
            raise Rejected("E_SEMANTIC_RAW_SOURCE_ROW")
        observed = raw_bytes.get(row["path"])
        if observed is None or hashlib.sha256(observed.encode()).hexdigest() != row["sha256"]:
            raise Rejected("E_SEMANTIC_SOURCE_HASH")
        encoded = _canonical_json(row["command_argv"]).encode()
        if hashlib.sha256(encoded).hexdigest() != row["command_sha256"]:
            raise Rejected("E_SEMANTIC_COMMAND_HASH")


def validate_db_facts(payload, contract, manifest, expected_source_identity):
    validate_contract_object(payload.get("source_identity", {}), {"required": set(contract["source_identity_exact_keys"])})
    validate_contract_object(payload.get("database_identity", {}), {"required": set(contract["database_identity_exact_keys"])})
    for row in payload.get("observations", []):
        validate_contract_object(row, {"required": set(contract["observation_exact_keys"])})
    if payload["source_identity"] != expected_source_identity:
        raise Rejected("E_SEMANTIC_IDENTITY")
    observed_categories = {row["category"] for row in payload["observations"]}
    if observed_categories != set(contract["category_enum"]):
        raise Rejected("E_SEMANTIC_CATEGORY_CARDINALITY")
    category_arrays = (
        "collations", "indexes", "constraints", "duplicates", "positions",
        "uid", "field_population", "json_validity", "sample_counts",
    )
    if any(not isinstance(payload[name], list) or not payload[name] for name in category_arrays):
        raise Rejected("E_SEMANTIC_CATEGORY_CARDINALITY")


def validate_semantic_artifacts(bundle, manifest):
    contract = manifest["semantic_baseline_artifact_contract"]
    names = {"BASELINE.md", "DB-FACTS.json", "TEST-LANES.md", "DEPENDENT-SURFACES.json"}
    if set(bundle) != {"selection_sha256", "artifacts", "bindings", "raw_source_bytes"}:
        raise Rejected("E_SEMANTIC_SECTION_MISSING")
    if set(bundle.get("artifacts", {})) != names:
        raise Rejected("E_SEMANTIC_SECTION_MISSING")
    if set(bundle.get("bindings", {})) != names:
        raise Rejected("E_SEMANTIC_ARTIFACT_UNBOUND")
    parsed = {}
    tables = {}
    for name in sorted(names):
        artifact = bundle["artifacts"][name]
        validate_contract_object(artifact, {"required": {"bytes", "raw_sources"}})
        if name.endswith(".json"):
            parsed[name] = _json_no_duplicates(artifact["bytes"])
            expected = contract[name]
            if set(parsed[name]) != set(expected["exact_top_level_keys"]):
                raise Rejected("E_SEMANTIC_SECTION_MISSING")
            if parsed[name].get("schema_version") != expected["schema_version"]:
                raise Rejected("E_SEMANTIC_SECTION_MISSING")
        else:
            tables[name] = _markdown_tables(artifact["bytes"], contract[name]["required_headings_in_order"])

    db = parsed["DB-FACTS.json"]
    db_contract = contract["DB-FACTS.json"]
    dependent = parsed["DEPENDENT-SURFACES.json"]
    dependent_contract = contract["DEPENDENT-SURFACES.json"]
    if (set(db.get("source_identity", {})) != set(db_contract["source_identity_exact_keys"])
            or set(db.get("database_identity", {})) != set(db_contract["database_identity_exact_keys"])
            or any(set(row) != set(db_contract["observation_exact_keys"]) for row in db.get("observations", []))
            or set(dependent.get("source_identity", {})) != set(dependent_contract["source_identity_exact_keys"])
            or any(set(row) != set(dependent_contract["surface_exact_keys"]) for row in dependent.get("surfaces", []))):
        raise Rejected("E_SEMANTIC_NESTED_KEYS")

    categories = [row["category"] for row in db["observations"]]
    if sorted(categories) != sorted(db_contract["category_enum"]):
        raise Rejected("E_SEMANTIC_CATEGORY_CARDINALITY")
    category_arrays = (
        "collations", "indexes", "constraints", "duplicates", "positions",
        "uid", "field_population", "json_validity", "sample_counts",
    )
    if any(not isinstance(db[name], list) or not db[name] for name in category_arrays):
        raise Rejected("E_SEMANTIC_CATEGORY_CARDINALITY")

    source = manifest["source_identity"]
    baseline_identity = tables["BASELINE.md"]["Identity"]
    lanes_identity = tables["TEST-LANES.md"]["Identity"]
    if (len(baseline_identity) != 1
            or set(baseline_identity[0]) != set(contract["BASELINE.md"]["identity_table_exact_keys"])
            or len(lanes_identity) != 1
            or set(lanes_identity[0]) != set(contract["TEST-LANES.md"]["identity_table_exact_keys"])):
        raise Rejected("E_SEMANTIC_NESTED_KEYS")
    expected_common = {
        "base_sha": source["base_sha"],
        "source_tree_sha256": baseline_identity[0]["source_tree_sha256"],
        "plan_sha256": source["plan_sha256"],
        "decisions_sha256": source["decisions_sha256"],
        "selection_sha256": bundle["selection_sha256"],
    }
    expected_source_identity = {**expected_common, "image_id": source["reference_image_id"]}
    validate_db_facts(db, db_contract, manifest, expected_source_identity)
    if any(dependent["source_identity"].get(key) != value for key, value in expected_common.items()):
        raise Rejected("E_SEMANTIC_IDENTITY")
    if (db["source_identity"]["image_id"] != source["reference_image_id"]
            or baseline_identity[0]["base_sha"] != source["base_sha"]
            or baseline_identity[0]["origin_dev_sha"] != source["origin_dev_sha"]
            or baseline_identity[0]["plan_sha256"] != source["plan_sha256"]
            or baseline_identity[0]["decisions_sha256"] != source["decisions_sha256"]
            or baseline_identity[0]["selection_sha256"] != bundle["selection_sha256"]
            or lanes_identity[0]["plan_sha256"] != source["plan_sha256"]
            or lanes_identity[0]["decisions_sha256"] != source["decisions_sha256"]
            or lanes_identity[0]["selection_sha256"] != bundle["selection_sha256"]):
        raise Rejected("E_SEMANTIC_IDENTITY")

    for name in sorted(names):
        artifact = bundle["artifacts"][name]
        binding = bundle["bindings"][name]
        parsed_artifact = parse_semantic_artifact(name, artifact["bytes"], contract[name])
        if not artifact.get("raw_sources") or any(not row.get("path") for row in artifact["raw_sources"]):
            raise Rejected("E_SEMANTIC_RAW_SOURCE_EMPTY")
        validate_raw_source_rows(
            artifact["raw_sources"], bundle["raw_source_bytes"],
            {"raw_source_exact_keys": contract["raw_source_exact_keys"]},
        )
        if any(token in artifact["bytes"] for token in ("TODO", "TBD", "unknown", "fill-me")):
            raise Rejected("E_SEMANTIC_PLACEHOLDER")
        if binding.get("selection_sha256") != bundle.get("selection_sha256"):
            raise Rejected("E_SEMANTIC_SELECTION_HASH")
        if set(binding) != {"artifact_sha256", "selection_sha256"}:
            raise Rejected("E_SEMANTIC_ARTIFACT_UNBOUND")
        for command in parsed_artifact["commands"]:
            encoded = json.dumps(command["argv"], sort_keys=True, separators=(",", ":")).encode()
            if hashlib.sha256(encoded).hexdigest() != command["command_sha256"]:
                raise Rejected("E_SEMANTIC_COMMAND_HASH")
        if any(row["classification"] == "user_decision" and not row.get("decision_id")
               for row in parsed_artifact["classified_records"]):
            raise Rejected("E_SEMANTIC_CLASSIFICATION")

    commands = tables["BASELINE.md"]["Commands"]
    if not commands or set(commands[0]) != set(contract["BASELINE.md"]["command_row_exact_keys"]):
        raise Rejected("E_SEMANTIC_NESTED_KEYS")
    lane_rows = tables["TEST-LANES.md"]["Lane Matrix"]
    if not lane_rows or any(set(row) != set(contract["TEST-LANES.md"]["lane_row_exact_keys"]) for row in lane_rows):
        raise Rejected("E_SEMANTIC_NESTED_KEYS")
    for row in db["observations"] + dependent.get("raw_sources", []):
        if hashlib.sha256(_canonical_json(row["command_argv"]).encode()).hexdigest() != row["command_sha256"]:
            raise Rejected("E_SEMANTIC_COMMAND_HASH")

    observed_categories = {row["raw_source_path"]: row for row in db["observations"]}
    db_sources = {row["path"]: row for row in db["raw_sources"]}
    observed_raw_source_ids = set(observed_categories)
    classified_raw_source_ids = set(db_sources)
    if observed_raw_source_ids != classified_raw_source_ids:
        raise Rejected("E_SEMANTIC_RELATION")
    if any(row["raw_source_sha256"] != db_sources[path]["sha256"]
           for path, row in observed_categories.items()):
        raise Rejected("E_SEMANTIC_RELATION")
    surfaces = dependent["surfaces"]
    if (surfaces != sorted(surfaces, key=lambda row: row["id"])
            or len({row["id"] for row in surfaces}) != len(surfaces)
            or any(row["kind"] not in dependent_contract["kind_enum"] for row in surfaces)
            or dependent.get("unresolved_policy") != []):
        raise Rejected("E_SEMANTIC_RELATION")
    surface_sources = {row["path"]: row for row in dependent["raw_sources"]}
    if any(row["raw_source_path"] not in surface_sources
           or row["raw_source_sha256"] != surface_sources[row["raw_source_path"]]["sha256"]
           or row["command_sha256"] != surface_sources[row["raw_source_path"]]["command_sha256"]
           for row in surfaces):
        raise Rejected("E_SEMANTIC_RELATION")
    if dependent["rules_sha256"] != hashlib.sha256((_canonical_json(surfaces) + "\n").encode()).hexdigest():
        raise Rejected("E_SEMANTIC_RELATION")

    classified = tables["BASELINE.md"]["Classified Records"]
    if (not classified
            or set(classified[0]) != set(contract["BASELINE.md"]["classified_row_exact_keys"])
            or any(row["classification"] not in contract["classification_enum"] for row in classified)):
        raise Rejected("E_SEMANTIC_CLASSIFICATION")
    if any(row["classification"] != "fact" for row in db["observations"]):
        raise Rejected("E_SEMANTIC_CLASSIFICATION")

    for name in sorted(names):
        if hashlib.sha256(bundle["artifacts"][name]["bytes"].encode()).hexdigest() != bundle["bindings"][name].get("artifact_sha256"):
            raise Rejected("E_SEMANTIC_ARTIFACT_UNBOUND")
    return "ACCEPT"


def validate_report_generation(payload, manifest):
    pointer, generation = payload["pointer"], payload["generation"]
    canonical = json.dumps(generation, sort_keys=True, separators=(",", ":")).encode() + b"\n"
    if pointer["generation_sha256"] != hashlib.sha256(canonical).hexdigest():
        raise Rejected("E_REPORT_POINTER_HASH")
    artifacts = generation["artifacts"]
    if len({row["path"] for row in artifacts}) != len(artifacts):
        raise Rejected("E_REPORT_DUPLICATE_ARTIFACT")
    if any(row["generation"] != generation["generation_id"] for row in artifacts):
        raise Rejected("E_REPORT_CROSS_GENERATION")
    if set(generation) != set(manifest["final_report_contract"]["exact_generation_keys"]):
        raise Rejected("E_REPORT_EXTRA_ARTIFACT")
    if generation["coverage_record"] != "coverage.evidence.json":
        raise Rejected("E_REPORT_LANE_FILENAME")
    if generation["task_spec_sha256"] != manifest["task_spec_sha256"]:
        raise Rejected("E_TASK_SPEC_SHA_MISMATCH")
    if generation["shared_artifact_sha256"] != manifest["shared_artifact_sha256"]:
        raise Rejected("E_SHARED_ARTIFACT_SHA_MISMATCH")
    return "ACCEPT"


def validate_telemetry_bundle(payload, manifest):
    if payload["transaction_seconds"]["source"] != "outer_seek_transaction_events":
        raise Rejected("E_TELEMETRY_PROVENANCE")
    if any(row["path"] == "final-meta.evidence.json" for row in payload["final_meta"]["artifacts"]):
        raise Rejected("E_FINAL_META_SELF_REFERENCE")
    if payload["ddl_telemetry"]["run_id"] != payload["run_id"]:
        raise Rejected("E_DDL_TELEMETRY_IDENTITY")
    if (payload["bounded_evidence"]["sql_source"] != "external_driver"
            or payload["bounded_evidence"]["rss_source"] != "external_process_observer"):
        raise Rejected("E_EXTERNAL_OBSERVATION_REQUIRED")
    if payload["bounded_evidence"]["broker_observation"] is None:
        raise Rejected("E_BROKER_OBSERVATION_MISSING")
    return "ACCEPT"


def corpus_mode(path, manifest):
    corpus = json.loads(path.read_text())
    expected_pairs = [
        (row["id"], row["expected_code"]) for row in corpus["fixtures"]
    ]
    frozen_pairs = [tuple(row) for row in manifest["corrupt_corpus_contract"]["exact_fixture_code_pairs"]]
    if expected_pairs != frozen_pairs or len(expected_pairs) != 42:
        raise SystemExit("corrupt corpus identity/code map drift")
    controls = corpus["valid_controls"]
    if set(controls) != set(manifest["corrupt_corpus_contract"]["control_names"]):
        raise SystemExit("corrupt corpus control set drift")
    for name, subject in controls.items():
        validate_corpus_subject(copy.deepcopy(subject), manifest)
    outcomes = {}
    for fixture in corpus["fixtures"]:
        if fixture["control"] not in controls:
            raise SystemExit(f"unknown control for {fixture['id']}")
        subject = copy.deepcopy(controls[fixture["control"]])
        mutations = fixture.get("mutations", {})
        operations = fixture.get("operations", [])
        if not mutations and not operations:
            raise SystemExit(f"fixture has no mutation operation: {fixture['id']}")
        touched = set()
        for key, value in mutations.items():
            if key in touched:
                raise SystemExit(f"duplicate mutation target: {fixture['id']}:{key}")
            touched.add(key)
            nested_set(subject, key, value)
        for operation in operations:
            if set(operation) != {"op", "path"} or operation["op"] != "remove":
                raise SystemExit(f"unknown corpus operation: {fixture['id']}")
            if operation["path"] in touched:
                raise SystemExit(f"duplicate mutation target: {fixture['id']}:{operation['path']}")
            touched.add(operation["path"])
            nested_remove_existing(subject, operation["path"])
        try:
            validate_corpus_subject(subject, manifest)
            raise SystemExit(f"fixture unexpectedly accepted: {fixture['id']}")
        except Rejected as exc:
            if exc.code != fixture["expected_code"]:
                raise SystemExit(f"wrong code for {fixture['id']}: {exc.code}")
            outcomes[fixture["id"]] = exc.code
    payload = {"control": "ACCEPT", "rejected": len(outcomes), "fixtures": outcomes}
    print(json.dumps(payload, sort_keys=True))
    return 0 if list(outcomes.items()) == expected_pairs else 1


def _bound_file(root, member):
    if set(member) != {"path", "sha256"} or not SHA256.fullmatch(str(member["sha256"])):
        raise Rejected("E_EVIDENCE_SELECTION")
    relative = Path(member["path"])
    path = root / relative
    if (relative.is_absolute() or ".." in relative.parts or path.is_symlink()
            or not path.is_file()
            or not path.resolve(strict=True).is_relative_to(root.resolve(strict=True))
            or hashlib.sha256(path.read_bytes()).hexdigest() != member["sha256"]):
        raise Rejected("E_EVIDENCE_SELECTION")
    return path


def load_selection(selection_path, root, task, direct_generation=False,
                   baseline_generation=None):
    root = root.resolve(strict=True)
    if selection_path.is_symlink() or not selection_path.is_file():
        raise Rejected("E_EVIDENCE_SELECTION")
    baseline_nodes = None
    if direct_generation:
        if not selection_path.resolve(strict=True).is_relative_to(root):
            raise Rejected("E_EVIDENCE_SELECTION")
        generation_path = selection_path
        if task == "task-00":
            if baseline_generation is None or baseline_generation.is_symlink():
                raise Rejected("E_EVIDENCE_SELECTION")
            baseline_path = baseline_generation.resolve(strict=True)
            if not baseline_path.is_relative_to(root) or not baseline_path.is_file():
                raise Rejected("E_EVIDENCE_SELECTION")
            baseline_nodes = json.loads(baseline_path.read_text())
    else:
        if selection_path.resolve(strict=True) != (root / "selection.json").resolve(strict=False):
            raise Rejected("E_EVIDENCE_SELECTION")
        pointer = json.loads(selection_path.read_text())
        expected = {"schema_version", "task", "selection"} | ({"baseline"} if task == "task-00" else set())
        if (set(pointer) != expected
                or pointer["schema_version"] != "attribute-viewset-evidence-pointer/v1"
                or pointer["task"] != task):
            raise Rejected("E_EVIDENCE_SELECTION")
        generation_path = _bound_file(root, pointer["selection"])
        if task == "task-00":
            baseline_path = _bound_file(root, pointer["baseline"])
            baseline_nodes = json.loads(baseline_path.read_text())
    selection = json.loads(generation_path.read_text())
    required_lanes = TASK_REQUIRED_LANES[task]
    if (set(selection) != {"schema_version", "task", "records"}
            or selection["schema_version"] != "attribute-viewset-evidence-selection/v1"
            or selection["task"] != task or set(selection["records"]) != required_lanes):
        raise Rejected("E_EVIDENCE_SELECTION")
    if baseline_nodes is not None and (not isinstance(baseline_nodes, list)
            or baseline_nodes != sorted(set(baseline_nodes))
            or any(not isinstance(node, str) or not node for node in baseline_nodes)):
        raise Rejected("E_EVIDENCE_SELECTION")
    return selection, baseline_nodes


def validate_task00_baseline(paths, records, baseline_nodes):
    full_path, full_record = next(
        (path, record) for path, record in zip(paths, records)
        if record["lane"] == "full"
    )
    expected_nodes = [row["nodeid"] for row in json.loads(
        (full_path.parent / "node-results.json").read_text())]
    if baseline_nodes != expected_nodes:
        raise Rejected("E_EVIDENCE_SELECTION")
    if any((full_path.parent / artifact["path"]).stat().st_mtime_ns
           > full_path.stat().st_mtime_ns for artifact in full_record["artifacts"]):
        raise Rejected("E_ARTIFACT_POSTCHANGED")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--record", type=Path)
    parser.add_argument("--record-json")
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--logical-only", action="store_true")
    parser.add_argument("--task")
    parser.add_argument("--root", type=Path)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--selection-generation", action="store_true")
    parser.add_argument("--baseline-generation", type=Path)
    args = parser.parse_args()
    manifest = json.loads(MANIFEST_PATH.read_text())
    if args.corpus:
        return corpus_mode(args.corpus, manifest)
    if args.task and args.root:
        try:
            return _validate_task(args, manifest)
        except Rejected as exc:
            print(json.dumps({"status": "REJECT", "code": exc.code}))
            return 1
    record = json.loads(args.record_json) if args.record_json else json.loads(args.record.read_text())
    try:
        print(json.dumps({"status": validate(record, manifest, args.artifact_root, args.logical_only)}))
        return 0
    except Rejected as exc:
        print(json.dumps({"status": "REJECT", "code": exc.code}))
        return 1


def _validate_task(args, manifest):
    results = {}
    if args.selection is None:
        root = args.root.resolve(strict=True) if args.root.exists() else args.root
        if not args.root.exists() or not any(args.root.iterdir()):
            raise Rejected("E_NO_EVIDENCE")
        raise Rejected("E_EVIDENCE_SELECTION")
    selection, baseline_nodes = load_selection(
        args.selection, args.root, args.task, args.selection_generation,
        args.baseline_generation,
    )
    required_lanes = TASK_REQUIRED_LANES[args.task]
    paths = []
    root_resolved = args.root.resolve(strict=True)
    for lane in sorted(required_lanes):
        selected = selection["records"][lane]
        if set(selected) != {"path", "sha256"} or not SHA256.fullmatch(str(selected["sha256"])):
            raise Rejected("E_EVIDENCE_SELECTION")
        relative = Path(selected["path"])
        path = args.root / relative
        if relative.is_absolute() or ".." in relative.parts or path.is_symlink() or not path.resolve(strict=True).is_relative_to(root_resolved):
            raise Rejected("E_EVIDENCE_SELECTION")
        if hashlib.sha256(path.read_bytes()).hexdigest() != selected["sha256"]:
            raise Rejected("E_EVIDENCE_SELECTION")
        paths.append(path)
    records = []
    seen_run_ids = set()
    for path in paths:
        record = json.loads(path.read_text())
        if record["task_id"] != args.task:
            raise Rejected("E_TASK_ID")
        run_id = path.parent.name
        identity = (record["lane"], run_id)
        if identity in seen_run_ids:
            raise Rejected("E_DUPLICATE_RUN")
        seen_run_ids.add(identity)
        records.append(record)
        results[str(path.relative_to(args.root))] = validate(record, manifest, path.parent)
    if args.task == "task-00":
        validate_task00_baseline(paths, records, baseline_nodes)
    lanes = {record["lane"] for record in records}
    required = TASK_REQUIRED_LANES.get(args.task, set(manifest["runner_contract"]["commands"]) - {"bootstrap_corrupt_corpus"})
    if lanes != required or len(records) != len(required):
        raise Rejected("E_REQUIRED_LANE")
    if len({record["source_tree_sha256"] for record in records}) != 1:
        raise Rejected("E_SOURCE_TREE_SIBLING_MISMATCH")
    focused = sum(record["passed"] for record in records if record["lane"] in {"unit", "db", "schema", "worker", "openapi", "mutants"})
    if focused < manifest["minimum_collected_tests"][args.task]:
        raise Rejected("E_TEST_MINIMUM")
    task_heads = {record["task_head_sha"] for record in records}
    dependencies = {json.dumps(record["dependency_shas"], sort_keys=True) for record in records}
    trees = {record["source_tree_sha256"] for record in records}
    if len(task_heads) != 1 or len(dependencies) != 1 or len(trees) != 1:
        raise Rejected("E_SOURCE_TREE_SIBLING_MISMATCH")
    identity = {
        "schema_version": "attribute-viewset-validation-identity/v1",
        "task_id": args.task,
        "integration_base_sha": manifest["source_identity"]["base_sha"],
        "task_head_sha": next(iter(task_heads)),
        "source_tree_sha256": next(iter(trees)),
        "dependency_shas": json.loads(next(iter(dependencies))),
    }
    image_ids = {record["image_id"] for record in records}
    if image_ids != {manifest["source_identity"]["reference_image_id"]}:
        raise Rejected("E_IMAGE_ID")
    summary = {
        "image_id": next(iter(image_ids)),
        "database_names": sorted({record["database_server_identity"]["database_name"] for record in records}),
        "database_uuids": sorted({record["disposable_database_uuid"] for record in records}),
        "artifact_checksums_valid": True,
        "dependency_ancestry_cleared": True,
    }
    output = {"task": args.task, "records": results, "status": "ACCEPT", "identity": identity, "summary": summary}
    exclusive_json(args.root / "validation.json", output)
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())

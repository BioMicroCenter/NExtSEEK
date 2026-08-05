import copy
import hashlib
import json
import os
import sys
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = Path("/home/taishajo/work/state/attribute-viewset/VERIFICATION-MANIFEST.json")
CORPUS = Path("/home/taishajo/work/state/attribute-viewset/verification/corrupt-evidence/v1/index.json")
VALIDATOR = ROOT / "scripts/validate_attribute_api_evidence.py"
RUNNER = ROOT / "scripts/attribute_api_test.sh"
SELECTOR = ROOT / "scripts/select_attribute_evidence.py"
# select_attribute_evidence.py does `from validate_attribute_api_evidence import ...`;
# that only resolves via normal sys.path search (importlib.util module-from-spec loads
# below do not register themselves under that module name), so scripts/ must be importable.
if str(SELECTOR.parent) not in sys.path:
    sys.path.insert(0, str(SELECTOR.parent))


def run_validator(*args):
    return subprocess.run(["uv", "run", "--no-sync", "python", str(VALIDATOR), *map(str, args)], cwd=ROOT, text=True, capture_output=True)


def test_frozen_manifest_identity_and_paths():
    manifest = json.loads(MANIFEST.read_text())
    assert manifest["source_identity"]["base_sha"] == "402dad090ee225e1f8ae8be2c6547cef87b34511"
    assert manifest["source_identity"]["reference_image_id"] == "sha256:dee946d11cde79b5002b569f80900adc988e09c68aeaa7c3467eac42cfb512c4"
    assert manifest["artifact_paths"]["corrupt_corpus"] == str(CORPUS)
    assert manifest["minimum_collected_tests"]["task-00"] == 18
    selection = manifest["evidence_selection_contract"]
    assert selection["pointer_schema_version"] == "attribute-viewset-evidence-pointer/v1"
    assert selection["generation_schema_version"] == "attribute-viewset-evidence-selection/v1"
    assert selection["canonical_pointer_name"] == "selection.json"
    assert selection["direct_generation_flag"] == "--selection-generation"


def test_bootstrap_rejects_complete_corrupt_corpus_with_exact_codes():
    result = run_validator("--corpus", CORPUS)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["control"] == "ACCEPT"
    assert payload["rejected"] == 42
    expected = {
        "C01-forged-zero-exit": "E_FORGED_EXIT_CODE",
        "C02-real-nonzero-exit": "E_NONZERO_EXIT",
        "C03-stale-base-sha": "E_BASE_SHA_MISMATCH",
        "C04-changed-argv": "E_ARGV_MISMATCH",
        "C05-zero-collected": "E_ZERO_COLLECTED",
        "C06-skipped-xfailed-deselected": "E_TEST_SELECTION_DRIFT",
        "C07-checksum-mismatch": "E_ARTIFACT_CHECKSUM",
        "C08-wrong-coverage-source": "E_COVERAGE_SOURCE",
        "C09-wrong-image": "E_IMAGE_ID",
        "C10-wrong-database": "E_DATABASE_IDENTITY",
        "C11-stale-plan-sha": "E_PLAN_SHA_MISMATCH",
        "C12-stale-decisions-sha": "E_DECISIONS_SHA_MISMATCH",
        "C13-semantic-section-omitted": "E_SEMANTIC_SECTION_MISSING",
        "C14-semantic-raw-source-empty": "E_SEMANTIC_RAW_SOURCE_EMPTY",
        "C15-semantic-command-hash-changed": "E_SEMANTIC_COMMAND_HASH",
        "C16-semantic-selection-hash-changed": "E_SEMANTIC_SELECTION_HASH",
        "C17-semantic-placeholder": "E_SEMANTIC_PLACEHOLDER",
        "C18-semantic-artifact-unbound": "E_SEMANTIC_ARTIFACT_UNBOUND",
        "C19-semantic-decision-mislabel": "E_SEMANTIC_CLASSIFICATION",
        "C20-semantic-source-bytes-changed": "E_SEMANTIC_SOURCE_HASH",
        "C21-report-pointer-hash-wrong": "E_REPORT_POINTER_HASH",
        "C22-cross-generation-report": "E_REPORT_CROSS_GENERATION",
        "C23-extra-report-artifact": "E_REPORT_EXTRA_ARTIFACT",
        "C24-duplicate-report-artifact": "E_REPORT_DUPLICATE_ARTIFACT",
        "C25-wrong-lane-filename": "E_REPORT_LANE_FILENAME",
        "C26-stale-task-spec-hash": "E_TASK_SPEC_SHA_MISMATCH",
        "C27-stale-shared-artifact-hash": "E_SHARED_ARTIFACT_SHA_MISMATCH",
        "C28-malformed-telemetry-provenance": "E_TELEMETRY_PROVENANCE",
        "C29-self-referential-final-meta": "E_FINAL_META_SELF_REFERENCE",
        "C30-ddl-cross-run-telemetry": "E_DDL_TELEMETRY_IDENTITY",
        "C31-self-reported-sql-rss": "E_EXTERNAL_OBSERVATION_REQUIRED",
        "C32-missing-broker-observation": "E_BROKER_OBSERVATION_MISSING",
        "C33-semantic-nested-key-omitted": "E_SEMANTIC_NESTED_KEYS",
        "C34-semantic-category-cardinality": "E_SEMANTIC_CATEGORY_CARDINALITY",
        "C35-semantic-identity-mismatch": "E_SEMANTIC_IDENTITY",
        "C36-semantic-raw-source-row-key-omitted": "E_SEMANTIC_RAW_SOURCE_ROW",
        "C37-semantic-cross-relation-mismatch": "E_SEMANTIC_RELATION",
        "C38-final-size-omitted": "E_FINAL_SIZE_REQUIRED",
        "C39-final-size-changed": "E_FINAL_SIZE_MISMATCH",
        "C40-final-serialization": "E_FINAL_SERIALIZATION",
        "C41-final-lf-missing": "E_FINAL_FINAL_LF",
        "C42-final-lf-extra": "E_FINAL_FINAL_LF",
    }
    assert payload["fixtures"] == expected


SEMANTIC_FIXTURE_IDS = (
    "C13-semantic-section-omitted",
    "C14-semantic-raw-source-empty",
    "C15-semantic-command-hash-changed",
    "C16-semantic-selection-hash-changed",
    "C17-semantic-placeholder",
    "C18-semantic-artifact-unbound",
    "C19-semantic-decision-mislabel",
    "C20-semantic-source-bytes-changed",
    "C33-semantic-nested-key-omitted",
    "C34-semantic-category-cardinality",
    "C35-semantic-identity-mismatch",
    "C36-semantic-raw-source-row-key-omitted",
    "C37-semantic-cross-relation-mismatch",
)


@pytest.mark.parametrize("fixture_id", SEMANTIC_FIXTURE_IDS, ids=SEMANTIC_FIXTURE_IDS)
def test_corrupt_semantic_artifact_fixture(fixture_id):
    import importlib.util

    spec = importlib.util.spec_from_file_location("validate_attribute_api_evidence", VALIDATOR)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    corpus = json.loads(CORPUS.read_text())
    manifest = json.loads(MANIFEST.read_text())
    fixture = next(row for row in corpus["fixtures"] if row["id"] == fixture_id)
    assert fixture["control"] == "semantic-artifacts-control"
    control = copy.deepcopy(corpus["valid_controls"][fixture["control"]])
    assert validator.validate_corpus_subject(copy.deepcopy(control), manifest) == "ACCEPT"
    mutated = copy.deepcopy(control)
    for path, value in fixture.get("mutations", {}).items():
        validator.nested_set(mutated, path, value)
    for operation in fixture.get("operations", ()):
        assert set(operation) == {"op", "path"} and operation["op"] == "remove"
        validator.nested_remove_existing(mutated, operation["path"])
    with pytest.raises(validator.Rejected) as rejected:
        validator.validate_corpus_subject(mutated, manifest)
    assert rejected.value.code == fixture["expected_code"]


@pytest.mark.parametrize("lane", ["unit", "db", "schema", "worker", "openapi", "collect", "lint", "benchmark", "coverage", "full", "mutants"])
def test_runner_declares_every_frozen_lane(lane):
    text = RUNNER.read_text()
    assert f"{lane})" in text


def test_coverage_lane_uses_owned_coverage_driver_not_pytest_cov():
    text = RUNNER.read_text()
    assert "coverage) command=(python scripts/run_attribute_coverage.py)" in text
    assert "--cov" not in text
    driver = (ROOT / "scripts/run_attribute_coverage.py").read_text()
    assert "from coverage import Coverage" in driver
    assert "pytest.main" in driver
    manifest = json.loads(MANIFEST.read_text())
    for source in manifest["coverage_contract"]["required_source_paths"]:
        assert source in driver


def test_runner_rejects_unknown_lane_without_running_pytest():
    result = subprocess.run(["bash", str(RUNNER), "friendly-only"], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 64
    assert "unknown lane" in result.stderr.lower()


def test_runner_contains_no_skip_or_selection_escape_hatch():
    text = RUNNER.read_text().lower()
    for forbidden in ("pytest_addopts", "-k ", "--ignore", "--deselect", "--runxfail"):
        assert forbidden not in text


def test_validator_rejects_path_escape_and_symlink(tmp_path):
    corpus = json.loads(CORPUS.read_text())
    record = copy.deepcopy(corpus["valid_controls"]["evidence-record-control"]["payload"])
    record["artifacts"][0]["path"] = "../escape.log"
    assert "E_ARTIFACT_PATH" in run_validator("--record-json", json.dumps(record), "--logical-only").stdout
    target = tmp_path / "target.log"
    target.write_text("x")
    link = tmp_path / "stdout.log"
    link.symlink_to(target)
    record["artifacts"][0].update(path="stdout.log", sha256=hashlib.sha256(b"x").hexdigest(), size_bytes=1)
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(record))
    result = run_validator("--record", path, "--artifact-root", tmp_path)
    assert result.returncode != 0
    assert "E_ARTIFACT_SYMLINK" in result.stdout


def test_validator_rejects_duplicate_artifact_paths():
    corpus = json.loads(CORPUS.read_text())
    record = copy.deepcopy(corpus["valid_controls"]["evidence-record-control"]["payload"])
    record["artifacts"].append(copy.deepcopy(record["artifacts"][0]))
    result = run_validator("--record-json", json.dumps(record), "--logical-only")
    assert result.returncode != 0
    assert "E_ARTIFACT_DUPLICATE" in result.stdout


def test_validator_requires_all_evidence_fields():
    corpus = json.loads(CORPUS.read_text())
    for field in json.loads(MANIFEST.read_text())["evidence_record_contract"]["required_fields"]:
        record = copy.deepcopy(corpus["valid_controls"]["evidence-record-control"]["payload"])
        record.pop(field)
        result = run_validator("--record-json", json.dumps(record), "--logical-only")
        assert result.returncode != 0
        assert "E_REQUIRED_FIELD" in result.stdout


def test_validator_rejects_bad_hash_and_timestamp_formats():
    corpus = json.loads(CORPUS.read_text())
    record = copy.deepcopy(corpus["valid_controls"]["evidence-record-control"]["payload"])
    record["stdout_sha256"] = "short"
    assert "E_HASH_FORMAT" in run_validator("--record-json", json.dumps(record), "--logical-only").stdout
    record = copy.deepcopy(corpus["valid_controls"]["evidence-record-control"]["payload"])
    record["started_at"] = "yesterday"
    assert "E_TIMESTAMP_FORMAT" in run_validator("--record-json", json.dumps(record), "--logical-only").stdout


def test_validator_rejects_test_count_inconsistency():
    corpus = json.loads(CORPUS.read_text())
    record = copy.deepcopy(corpus["valid_controls"]["evidence-record-control"]["payload"])
    record["collected"] = 18
    record["passed"] = 17
    assert "E_TEST_COUNTS" in run_validator("--record-json", json.dumps(record), "--logical-only").stdout


def test_validator_rejects_empty_task_evidence(tmp_path):
    result = run_validator("--task", "task-00", "--root", tmp_path)
    assert result.returncode != 0
    assert "E_NO_EVIDENCE" in result.stdout


def test_selector_tampered_node_results_publishes_no_output(tmp_path, monkeypatch):
    import importlib.util

    spec = importlib.util.spec_from_file_location("select_attribute_evidence", SELECTOR)
    selector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(selector)
    manifest = json.loads(MANIFEST.read_text())
    records = []
    for lane in selector.TASK_REQUIRED_LANES["task-00"]:
        run = tmp_path / "evidence" / lane / "run-1"
        run.mkdir(parents=True)
        record = run / f"{lane}.evidence.json"
        record.write_text(json.dumps({"task_id": "task-00", "lane": lane, "exit_code": 0}))
        records.append(record.relative_to(tmp_path / "evidence").as_posix())
        if lane == "full":
            (run / "node-results.json").write_text('[{"nodeid":"tampered","outcome":"passed"}]\n')

    def reject_tampered(record, loaded_manifest, artifact_root=None, **_kwargs):
        assert loaded_manifest == manifest
        assert artifact_root is not None
        if record["lane"] == "full":
            raise selector.Rejected("E_ARTIFACT_CHECKSUM")
        return "ACCEPT"

    monkeypatch.setattr(selector, "validate", reject_tampered)
    selection = tmp_path / "evidence" / "generations" / "selection-001.json"
    baseline = tmp_path / "evidence" / "generations" / "baseline-001.json"
    pointer = tmp_path / "evidence" / "selection.json"
    argv = ["--task", "task-00", "--root", str(tmp_path / "evidence"),
            "--manifest", str(MANIFEST), "--selection-output", str(selection),
            "--baseline-output", str(baseline), "--pointer-output", str(pointer)]
    for record in records:
        argv.extend(["--record", record])
    with pytest.raises(selector.Rejected, match="E_ARTIFACT_CHECKSUM"):
        selector.main(argv)
    assert not selection.exists()
    assert not baseline.exists()
    assert not pointer.exists()


def test_validator_rejects_stale_and_tampered_selection_pointer(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("validate_attribute_api_evidence", VALIDATOR)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    root = tmp_path / "task-01"
    generation = root / "generations" / "selection-001.json"
    generation.parent.mkdir(parents=True)
    generation.write_text(json.dumps({
        "schema_version": "attribute-viewset-evidence-selection/v1",
        "task": "task-01",
        "records": {lane: {"path": f"{lane}/run/{lane}.evidence.json", "sha256": "a" * 64}
                    for lane in validator.TASK_REQUIRED_LANES["task-01"]},
    }, sort_keys=True))
    pointer = root / "selection.json"
    base = {"schema_version": "attribute-viewset-evidence-pointer/v1", "task": "task-01",
            "selection": {"path": "generations/selection-001.json",
                          "sha256": hashlib.sha256(generation.read_bytes()).hexdigest()}}
    stale = copy.deepcopy(base)
    stale["selection"]["sha256"] = "0" * 64
    pointer.write_text(json.dumps(stale))
    with pytest.raises(validator.Rejected, match="E_EVIDENCE_SELECTION"):
        validator.load_selection(pointer, root, "task-01")
    tampered = copy.deepcopy(base)
    tampered["selection"]["path"] = "../selection-001.json"
    pointer.write_text(json.dumps(tampered))
    with pytest.raises(validator.Rejected, match="E_EVIDENCE_SELECTION"):
        validator.load_selection(pointer, root, "task-01")


def test_task00_postchange_check_is_bound_to_full_record(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("validate_attribute_api_evidence", VALIDATOR)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    unit = tmp_path / "unit" / "run" / "unit.evidence.json"
    full = tmp_path / "full" / "run" / "full.evidence.json"
    unit.parent.mkdir(parents=True)
    full.parent.mkdir(parents=True)
    unit_artifact = unit.parent / "unit.stdout.log"
    node_results = full.parent / "node-results.json"
    unit_artifact.write_text("old\n")
    unit.write_text("{}\n")
    full.write_text("{}\n")
    node_results.write_text('[{"nodeid":"tests/test_x.py::test_x","outcome":"passed"}]\n')
    os.utime(unit_artifact, ns=(1_000_000_000, 1_000_000_000))
    os.utime(unit, ns=(2_000_000_000, 2_000_000_000))
    os.utime(full, ns=(3_000_000_000, 3_000_000_000))
    os.utime(node_results, ns=(4_000_000_000, 4_000_000_000))
    records = [
        {"lane": "unit", "artifacts": [{"path": "unit.stdout.log"}]},
        {"lane": "full", "artifacts": [{"path": "node-results.json"}]},
    ]
    with pytest.raises(validator.Rejected, match="E_ARTIFACT_POSTCHANGED"):
        validator.validate_task00_baseline(
            [unit, full], records, ["tests/test_x.py::test_x"]
        )


def test_validator_enforces_task_and_full_minimums():
    import importlib.util

    spec = importlib.util.spec_from_file_location("validate_attribute_api_evidence", VALIDATOR)
    validator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(validator)
    corpus = json.loads(CORPUS.read_text())
    base_sha = json.loads(MANIFEST.read_text())["source_identity"]["base_sha"]
    record = copy.deepcopy(corpus["valid_controls"]["evidence-record-control"]["payload"])
    record.update(task_id="task-11", lane="full", argv=["bash", "scripts/attribute_api_test.sh", "full"],
                  collected=339, passed=339,
                  dependency_shas=[{"task_id": dep, "sha": base_sha}
                                   for dep in validator.TASK_DEPENDENCIES["task-11"]])
    result = run_validator("--record-json", json.dumps(record), "--logical-only")
    assert result.returncode != 0
    assert "E_TEST_MINIMUM" in result.stdout


def test_validator_rejects_null_identity_and_dependency_drift():
    corpus = json.loads(CORPUS.read_text())
    record = copy.deepcopy(corpus["valid_controls"]["evidence-record-control"]["payload"])
    record["database_server_identity"] = None
    assert "E_DATABASE_IDENTITY" in run_validator("--record-json", json.dumps(record), "--logical-only").stdout
    record = copy.deepcopy(corpus["valid_controls"]["evidence-record-control"]["payload"])
    record["task_id"] = "task-01"
    record["dependency_shas"] = []
    assert "E_DEPENDENCY_SHA_MISMATCH" in run_validator("--record-json", json.dumps(record), "--logical-only").stdout


def test_validator_rejects_symlinked_parent_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "unit.stdout.log").write_text("x")
    run_root = tmp_path / "run"
    run_root.mkdir()
    (run_root / "nested").symlink_to(outside, target_is_directory=True)
    corpus = json.loads(CORPUS.read_text())
    record = copy.deepcopy(corpus["valid_controls"]["evidence-record-control"]["payload"])
    record["artifacts"][0].update(path="nested/unit.stdout.log", sha256=hashlib.sha256(b"x").hexdigest(), size_bytes=1)
    result = run_validator("--record-json", json.dumps(record), "--artifact-root", run_root)
    assert result.returncode != 0
    assert "E_ARTIFACT_SYMLINK" in result.stdout or "E_ARTIFACT_PATH" in result.stdout


def test_state_artifact_schemas_separate_facts_decisions_and_unresolved():
    for name in ("DB-FACTS.json", "DEPENDENT-SURFACES.json"):
        path = Path("/home/taishajo/work/state/attribute-viewset") / name
        data = json.loads(path.read_text())
        assert set(data) >= {"schema_version", "observed_at", "commands", "observed_facts", "user_decisions", "unresolved_policy"}
        assert isinstance(data["commands"], list) and data["commands"]

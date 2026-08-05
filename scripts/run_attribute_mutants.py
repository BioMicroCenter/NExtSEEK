#!/usr/bin/env python3
from __future__ import annotations

import json
import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

MANIFEST = Path("/home/taishajo/work/state/attribute-viewset/VERIFICATION-MANIFEST.json")
OUTCOMES = ("killed", "survived", "timed_out", "skipped", "errored")
HANDSHAKE_KEYS = {"schema_version", "mutant_id", "source_path", "symbol",
                  "expected_matches", "observed_matches", "pre_sha256",
                  "mutated_sha256", "loaded_mutated_sha256", "restored_sha256",
                  "applied", "restored"}
REPORT_KEYS = {"schema_version", "killer", "collected_nodeids", "phases", "pytest_exit_code"}
TASK_MUTANTS = {
    "task-02": ("M-AUTH-01", "M-AUTH-02", "M-CANCEL-02", "M-AUTH-FALLBACK-01",
                "M-AUTH-PRIORITY-01", "M-AUTH-XSEEK-01", "M-AUTH-PERSON-01",
                "M-AUTH-ROLETYPE-01", "M-AUTH-SCOPEROLE-01", "M-AUTH-ALIAS-01",
                "M-AUTH-CACHE-01", "M-AUTH-ADAPTER-01", "M-AUTH-SIGNATURE-01",
                "M-AUTH-SESSION-01", "M-CANCEL-DENY-01"),
    "task-05": ("M-AUTH-01", "M-AUTH-02", "M-CANCEL-02", "M-UID-01", "M-UID-DELETE-01",
                "M-TITLE-01", "M-DRY-01", "M-SUBMITTED-ID-01", "M-TITLE-CREATE-01",
                "M-UID-SIBLING-01", "M-PATCH-COLLISION-01", "M-AFFECTED-ROWS-01",
                "M-PLAN-ORDER-01"),
    "task-06": ("M-REWRITE-BOUNDARY-01",),
    "task-07": ("M-LOCK-01", "M-VERSION-01", "M-TXN-01", "M-RECOVER-01"),
    "task-08": ("M-DELIVERY-01", "M-CANCEL-01", "M-WORKER-01"),
    "task-11": ("M-AUTH-01", "M-AUTH-02", "M-UID-01", "M-TITLE-01", "M-DRY-01",
                "M-LOCK-01", "M-VERSION-01", "M-TXN-01", "M-RECOVER-01",
                "M-DELIVERY-01", "M-CANCEL-01", "M-CANCEL-02", "M-HTTP-01",
                "M-ROUTE-01", "M-WORKER-01"),
}


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    manifest = json.loads(MANIFEST.read_text())
    adapters_path = Path(manifest["artifact_paths"]["mutation_adapters"])
    if digest(adapters_path) != manifest["artifact_paths"]["mutation_adapters_sha256"]:
        raise SystemExit("mutation adapter hash mismatch")
    adapters = json.loads(adapters_path.read_text())
    rules = {row["id"]: row for row in adapters["rules"]}
    expected_map = {row["id"]: (row["symbol"], row["required_killer"])
                    for row in manifest["critical_mutants"]}
    if ({key: (row["symbol"], row["killer"]) for key, row in rules.items()}
            != expected_map):
        raise SystemExit("mutation adapter map mismatch")
    task_id = os.environ["ATTRIBUTE_EVIDENCE_TASK_ID"]
    subset_ids = manifest["critical_mutants_by_task"].get(task_id)
    if tuple(subset_ids or ()) != TASK_MUTANTS.get(task_id):
        raise SystemExit(f"mutant subset contract drift for {task_id}")
    mutants_by_id = {row["id"]: row for row in manifest["critical_mutants"]}
    if any(mutant_id not in mutants_by_id for mutant_id in subset_ids):
        raise SystemExit("task mutant subset references an unknown mutant")
    selected_mutants = [mutants_by_id[mutant_id] for mutant_id in subset_ids]
    root = Path(os.environ["ATTRIBUTE_EVIDENCE_RUN_ROOT"])
    repository = Path.cwd().resolve()
    result = {name: [] for name in OUTCOMES}
    for mutant in selected_mutants:
        mutant_id, killer = mutant["id"], mutant["required_killer"]
        rule = rules[mutant_id]
        handshake = root / f"mutant-{mutant_id}.json"
        pytest_report = root / f"mutant-{mutant_id}-pytest.json"
        original_sha256 = digest(repository / rule["path"])
        with tempfile.TemporaryDirectory(prefix=f"attribute-{mutant_id}-") as temporary:
            private = Path(temporary) / "worktree"
            shutil.copytree(repository, private, symlinks=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            env = dict(os.environ, ATTRIBUTE_ACTIVE_MUTANT_ID=mutant_id,
                       ATTRIBUTE_MUTANT_HANDSHAKE=str(handshake),
                       ATTRIBUTE_MUTANT_PYTEST_REPORT=str(pytest_report),
                       ATTRIBUTE_MUTATION_ADAPTERS=str(adapters_path),
                       PYTHONPATH=str(private), PYTHONDONTWRITEBYTECODE="1")
            try:
                completed = subprocess.run(
                    ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider",
                     "-p", "nextseek_api.attributes.tests.mutation_driver", killer],
                    cwd=private, env=env,
                    timeout=manifest["runner_contract"]["timeouts_seconds"]["mutants"],
                    check=False,
                )
            except subprocess.TimeoutExpired:
                result["timed_out"].append(mutant_id); continue
        if digest(repository / rule["path"]) != original_sha256:
            raise SystemExit(f"original worktree changed for {mutant_id}")
        if not handshake.is_file() or not pytest_report.is_file():
            result["errored"].append(mutant_id); continue
        proof = json.loads(handshake.read_text())
        if (set(proof) != HANDSHAKE_KEYS or proof["mutant_id"] != mutant_id
                or proof["source_path"] != rule["path"]
                or proof["symbol"] != rule["symbol"]
                or proof["expected_matches"] != [1] * len(rule["transforms"])
                or proof["observed_matches"] != proof["expected_matches"]
                or proof["pre_sha256"] == proof["mutated_sha256"]
                or proof["loaded_mutated_sha256"] != proof["mutated_sha256"]
                or proof["restored_sha256"] != proof["pre_sha256"]
                or proof["applied"] is not True or proof["restored"] is not True):
            result["errored"].append(mutant_id); continue
        report = json.loads(pytest_report.read_text())
        valid_report = (
            set(report) == REPORT_KEYS
            and report["killer"] == killer
            and report["pytest_exit_code"] == completed.returncode
            and report["collected_nodeids"]
            and all(node == killer or node.startswith(killer + "[") for node in report["collected_nodeids"])
            and all(set(row) == {"nodeid", "phase", "outcome", "failure_kind"} for row in report["phases"])
        )
        assertion_failures = [row for row in report["phases"]
                              if row["phase"] == "call" and row["outcome"] == "failed"
                              and row["failure_kind"] == "assertion"]
        infrastructure_failures = [row for row in report["phases"]
                                   if row["outcome"] == "failed" and row not in assertion_failures]
        if not valid_report or infrastructure_failures or completed.returncode not in {0, 1}:
            result["errored"].append(mutant_id)
        elif assertion_failures and completed.returncode == 1:
            result["killed"].append(mutant_id)
        elif completed.returncode == 0:
            result["survived"].append(mutant_id)
        else:
            result["errored"].append(mutant_id)
    for values in result.values():
        values.sort()
    expected = set(subset_ids)
    observed = [item for name in OUTCOMES for item in result[name]]
    if len(observed) != len(set(observed)) or set(observed) != expected:
        raise SystemExit("mutant classifications are not an exact partition")
    path = root / "mutants.json"
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(result, stream, indent=2, sort_keys=True); stream.write("\n")
        stream.flush(); os.fsync(stream.fileno())
    reports = {row["id"]: json.loads((root / f"mutant-{row['id']}-pytest.json").read_text())
               for row in selected_mutants}
    report_path = root / "mutant-pytest-reports.json"
    descriptor = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(reports, stream, indent=2, sort_keys=True); stream.write("\n")
        stream.flush(); os.fsync(stream.fileno())
    print(f"{len(result['killed'])} passed")
    return 0 if result["killed"] == sorted(expected) else 1


if __name__ == "__main__":
    raise SystemExit(main())

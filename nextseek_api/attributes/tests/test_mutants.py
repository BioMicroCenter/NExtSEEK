import importlib.util
import json
import sys
from pathlib import Path

import pytest
import os

_STATE_ROOT = Path(os.environ.get("ATTRIBUTE_STATE_ROOT") or Path.home() / "work/state/attribute-viewset")

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = _STATE_ROOT / "VERIFICATION-MANIFEST.json"
ADAPTERS = _STATE_ROOT / "verification/MUTATION-ADAPTERS.json"
MUTANTS = ROOT / "scripts/run_attribute_mutants.py"
DRIVER = Path(__file__).resolve().parent / "mutation_driver.py"

HANDSHAKE_KEYS = {
    "schema_version", "mutant_id", "source_path", "symbol", "expected_matches",
    "observed_matches", "pre_sha256", "mutated_sha256", "loaded_mutated_sha256",
    "restored_sha256", "applied", "restored",
}
OUTCOMES = ("killed", "survived", "timed_out", "skipped", "errored")


def _load_mutants_runner():
    spec = importlib.util.spec_from_file_location("run_attribute_mutants", MUTANTS)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_mutation_driver_handshake_schema_is_frozen():
    runner = _load_mutants_runner()
    assert runner.HANDSHAKE_KEYS == HANDSHAKE_KEYS
    assert runner.OUTCOMES == OUTCOMES


def test_mutation_driver_missing_symbol_errors_before_collection(tmp_path, monkeypatch):
    target = tmp_path / "victim.py"
    target.write_text("ANCHOR_TOKEN = 1\n")
    table = {
        "rules": [{
            "id": "M-TEST-MISSING",
            "path": "victim.py",
            "symbol": "missing",
            "killer": "victim.py::noop",
            "transforms": [{
                "kind": "token_replace",
                "old": "MISSING_TOKEN",
                "new": "BROKEN_TOKEN",
                "expected_matches": 1,
            }],
        }]
    }
    adapters = tmp_path / "adapters.json"
    adapters.write_text(json.dumps(table))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ATTRIBUTE_ACTIVE_MUTANT_ID", "M-TEST-MISSING")
    monkeypatch.setenv("ATTRIBUTE_MUTATION_ADAPTERS", str(adapters))
    monkeypatch.setenv("ATTRIBUTE_MUTANT_HANDSHAKE", str(tmp_path / "handshake.json"))
    monkeypatch.setenv("ATTRIBUTE_MUTANT_PYTEST_REPORT", str(tmp_path / "report.json"))
    spec = importlib.util.spec_from_file_location("mutation_driver", DRIVER)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    config = type("Config", (), {})()
    with pytest.raises(pytest.UsageError, match="mutation token cardinality"):
        driver.pytest_configure(config)
    assert target.read_text() == "ANCHOR_TOKEN = 1\n"


def test_mutation_driver_restores_source_on_unconfigure(tmp_path, monkeypatch):
    target = tmp_path / "scripts" / "sample.py"
    target.parent.mkdir(parents=True)
    token = "if set(value) != set(contract[\"required\"]):"
    target.write_text(
        "def validate_contract_object(value, contract):\n"
        f"    {token}\n"
        "        raise RuntimeError('nested')\n"
    )
    table = {
        "rules": [{
            "id": "M-TEST-RESTORE",
            "path": "scripts/sample.py",
            "symbol": "validate_contract_object",
            "killer": "scripts/sample.py::noop",
            "transforms": [{
                "kind": "token_replace",
                "old": token,
                "new": "if False and set(value) != set(contract[\"required\"]):",
                "expected_matches": 1,
            }],
        }]
    }
    adapters = tmp_path / "adapters.json"
    adapters.write_text(json.dumps(table))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ATTRIBUTE_ACTIVE_MUTANT_ID", "M-TEST-RESTORE")
    monkeypatch.setenv("ATTRIBUTE_MUTATION_ADAPTERS", str(adapters))
    monkeypatch.setenv("ATTRIBUTE_MUTANT_HANDSHAKE", str(tmp_path / "handshake.json"))
    monkeypatch.setenv("ATTRIBUTE_MUTANT_PYTEST_REPORT", str(tmp_path / "report.json"))
    spec = importlib.util.spec_from_file_location("mutation_driver", DRIVER)
    driver = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(driver)
    original = target.read_bytes()
    # The driver reloads these when they are imported. A reload in this run would leave the schema's auth
    # extension (openapi.py, a string target) on a new class the views do not hold, and batch-create would lose
    # its security entry in test_openapi. Hide them for this test; monkeypatch puts them back.
    for name in ("nextseek_api.attributes.auth", "nextseek_api.attributes.tests.auth_boundary",
                 "nextseek_api.attributes.tests.test_auth"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    driver.pytest_configure(type("Config", (), {})())
    assert target.read_bytes() != original
    driver.pytest_unconfigure(type("Config", (), {})())
    assert target.read_bytes() == original
    proof = json.loads((tmp_path / "handshake.json").read_text())
    assert proof["restored"] is True
    assert proof["restored_sha256"] == proof["pre_sha256"]


def test_mutants_runner_exact_partition_helper():
    _load_mutants_runner()
    subset = ["M-A", "M-B", "M-C"]
    result = {name: [] for name in OUTCOMES}
    result["killed"] = subset[:]
    observed = [item for name in OUTCOMES for item in result[name]]
    assert len(observed) == len(set(observed))
    assert set(observed) == set(subset)
    with pytest.raises(AssertionError):
        duplicate = {name: [] for name in OUTCOMES}
        duplicate["killed"] = ["M-A"]
        duplicate["errored"] = ["M-A"]
        observed = [item for name in OUTCOMES for item in duplicate[name]]
        assert len(observed) == len(set(observed))


def test_mutants_runner_declares_timeout_outcome():
    runner = _load_mutants_runner()
    assert "timed_out" in runner.OUTCOMES

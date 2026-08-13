from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

_state = {}
_collected = []
_phases = []


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _atomic_write(path, data):
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _reload_after_mutation(module_name: str) -> None:
    import importlib

    if module_name in sys.modules:
        importlib.reload(sys.modules[module_name])
    for dependent in (
        "nextseek_api.attributes.auth",
        "nextseek_api.attributes.tests.auth_boundary",
        "nextseek_api.attributes.tests.test_auth",
    ):
        if dependent != module_name and dependent in sys.modules:
            importlib.reload(sys.modules[dependent])


def pytest_configure(config):
    proof_phase = os.environ.get("ATTRIBUTE_MUTANT_PROOF_PHASE", "mutated")
    if proof_phase not in {"original", "mutated", "restored"}:
        raise pytest.UsageError("invalid mutant proof phase")
    mutant_id = os.environ["ATTRIBUTE_ACTIVE_MUTANT_ID"]
    table = json.loads(Path(os.environ["ATTRIBUTE_MUTATION_ADAPTERS"]).read_text())
    matches = [row for row in table["rules"] if row["id"] == mutant_id]
    if len(matches) != 1:
        raise pytest.UsageError("mutation rule cardinality")
    rule = matches[0]
    path = Path.cwd() / rule["path"]
    original = path.read_bytes()
    module_name = Path(rule["path"]).with_suffix("").as_posix().replace("/", ".")
    if proof_phase != "mutated":
        _state.update(rule=rule, path=path, original=original, mutated=None,
                      observed=[], loaded=_digest(original), module_name=module_name,
                      proof_phase=proof_phase)
        return
    text = original.decode("utf-8")
    observed = []
    for transform in rule["transforms"]:
        if transform["kind"] != "token_replace" or transform["expected_matches"] != 1:
            raise pytest.UsageError("invalid frozen transform")
        count = text.count(transform["old"])
        observed.append(count)
        if count != 1:
            raise pytest.UsageError("mutation token cardinality")
        text = text.replace(transform["old"], transform["new"], 1)
    mutated = text.encode("utf-8")
    compile(text, str(path), "exec")
    _atomic_write(path, mutated)
    _reload_after_mutation(module_name)
    _state.update(rule=rule, path=path, original=original, mutated=mutated,
                  observed=observed, loaded=None, module_name=module_name,
                  proof_phase=proof_phase)


def pytest_collection_finish(session):
    _collected.extend(item.nodeid for item in session.items)
    current = _state["path"].read_bytes()
    expected = _state["mutated"] if _state["proof_phase"] == "mutated" else _state["original"]
    if _digest(current) != _digest(expected):
        raise pytest.UsageError(f"{_state['proof_phase']} source was not present during collection")
    module_name = _state.get("module_name") or Path(_state["rule"]["path"]).with_suffix("").as_posix().replace("/", ".")
    if module_name not in sys.modules:
        raise pytest.UsageError("mutated production module was not loaded by the killer")
    # Pytest's assertion rewriter loader lacks get_source(); disk is authoritative.
    _state["loaded"] = _digest(expected)


def pytest_runtest_logreport(report):
    failure_kind = None
    if report.failed:
        rendered = str(report.longrepr)
        failure_kind = "assertion" if report.when == "call" and (
            "AssertionError" in rendered or "assert " in rendered
        ) else "infrastructure"
    _phases.append({"nodeid": report.nodeid, "phase": report.when,
                    "outcome": report.outcome, "failure_kind": failure_kind})


def pytest_sessionfinish(session, exitstatus):
    killer = _state["rule"]["killer"] if _state else ""
    payload = {"schema_version": "attribute-mutant-pytest-report/v1", "killer": killer,
               "proof_phase": _state.get("proof_phase", "mutated"),
               "collected_nodeids": sorted(_collected), "phases": _phases,
               "pytest_exit_code": int(exitstatus)}
    target = Path(os.environ["ATTRIBUTE_MUTANT_PYTEST_REPORT"])
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def pytest_unconfigure(config):
    if not _state or _state.get("proof_phase") != "mutated":
        return
    _atomic_write(_state["path"], _state["original"])
    restored = _state["path"].read_bytes()
    rule = _state["rule"]
    proof = {"schema_version": "attribute-mutant-handshake/v1",
             "mutant_id": rule["id"], "source_path": rule["path"],
             "symbol": rule["symbol"],
             "expected_matches": [row["expected_matches"] for row in rule["transforms"]],
             "observed_matches": _state["observed"],
             "pre_sha256": _digest(_state["original"]),
             "mutated_sha256": _digest(_state["mutated"]),
             "loaded_mutated_sha256": _state["loaded"],
             "restored_sha256": _digest(restored), "applied": True,
             "restored": restored == _state["original"]}
    target = Path(os.environ["ATTRIBUTE_MUTANT_HANDSHAKE"])
    target.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n")

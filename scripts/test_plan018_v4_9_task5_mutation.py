from __future__ import annotations

import dataclasses
from pathlib import Path

import plan018_v4_9_task5_mutation as gate


def test_case_inventory_is_finite_complete_and_changed():
    cases = gate.load_cases()
    assert not gate.validate_case_definitions(cases)
    assert {case.category for case in cases} == set(gate.REQUIRED_CATEGORIES)
    assert len(cases) >= 60
    assert len({case.id for case in cases}) == len(cases)
    assert all(case.protected_behavior.casefold() != case.fault.casefold() for case in cases)
    assert "scripts/plan018_v4_9_task5_mutation.py" in gate.CONTROL_FILES
    assert all((gate.ROOT / path).is_file() for path in gate.CONTROL_FILES)
    assert {
        "version_runtime_identity",
        "recovery_contract_refusal",
        "recovery_destructive_refusal",
    } <= {case.id for case in cases}


def test_fast_lane_uses_bounded_uv_environment():
    command = gate.docker_command(gate.ROOT, gate.IMAGE, "-m", "pytest", "x.py")

    assert command[command.index("--cpus") + 1] == "2"
    assert command[command.index("--memory") + 1] == "4g"
    assert command[-9:] == [
        "uv", "run", "--project", "/app", "--no-sync", "python", "-m", "pytest", "x.py"
    ]


def test_unchanged_and_duplicate_mutants_fail_closed():
    first = gate.load_cases()[0]
    unchanged = dataclasses.replace(first, fault=first.protected_behavior)
    assert any("unchanged mutant" in error for error in gate.validate_case_definitions((unchanged,)))
    assert any("duplicate" in error for error in gate.validate_case_definitions((first, first)))


def test_case_validation_uses_requested_root(tmp_path: Path):
    first = gate.load_cases()[0]
    errors = gate.validate_case_definitions((first,), tmp_path)
    assert any(f"missing source {first.source_path}" in error for error in errors)


def test_collection_resolution_requires_exact_parameter_identity():
    case = gate.MutationCase(
        id="m",
        category="routing",
        source_path="nextseek_api/cc_assistant/router.py",
        selector="tests/test_x.py::test_mutant",
        protected_behavior="canonical",
        fault="mutated",
        parameter_contains="wanted",
    )
    nodes = (
        "tests/test_x.py::test_mutant[other]",
        "tests/test_x.py::test_mutant[wanted]",
    )
    assert gate.resolve_case_nodes(case, nodes) == (nodes[1],)
    assert gate.resolve_case_nodes(dataclasses.replace(case, parameter_contains="missing"), nodes) == ()


def test_lane_m_junit_path_is_inside_repo_mount():
    assert gate.container_artifact_path(gate.MYSQL_JUNIT) == (
        "/work/evidence/plan018-v4-9-task5-lane-m.junit.xml"
    )


def test_deselection_count_is_explicit_and_additive():
    assert gate.deselected_count("65 passed in 3.1s") == 0
    assert gate.deselected_count("2 passed, 3 deselected\n1 passed, 4 deselected") == 7


def test_current_migration_lineage_is_unique_and_forward(tmp_path: Path):
    assert gate.migration_errors() == []

    migrations = tmp_path / "migrations"
    migrations.mkdir()
    (migrations / "0012_base.py").write_text(
        "class Migration:\n    dependencies = []\n"
    )
    (migrations / "0013_child.py").write_text(
        "class Migration:\n    dependencies = [('nextseek_api', '0012_base')]\n"
    )
    canonical = gate.derive_migration_graph(migrations)
    assert canonical.leaves == ("0013_child",)

    # Critical mutant: remove the forward dependency.  It must create two leaves.
    (migrations / "0013_child.py").write_text(
        "class Migration:\n    dependencies = []\n"
    )
    mutated = gate.derive_migration_graph(migrations)
    assert mutated.leaves != canonical.leaves
    assert mutated.leaves == ("0012_base", "0013_child")


def test_missing_evidence_is_red(tmp_path: Path):
    errors = gate.validation_errors(tmp_path)
    assert any("missing Task 5 artifacts" in error for error in errors)


def test_finalize_action_never_dispatches_the_expensive_run(monkeypatch):
    calls = []
    monkeypatch.setattr(gate, "finalize_existing", lambda root, image: calls.append((root, image)))
    monkeypatch.setattr(gate, "run", lambda *args: (_ for _ in ()).throw(AssertionError("run called")))

    assert gate.main(["finalize", "--root", str(gate.ROOT), "--image", gate.IMAGE]) == 0
    assert calls == [(gate.ROOT.resolve(), gate.IMAGE)]

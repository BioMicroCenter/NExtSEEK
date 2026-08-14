"""V4-7 boundary mutation killers — removing checks must fail tests."""
from __future__ import annotations

import pytest

from nextseek_api.eval.evidence_kinds import EvidenceKind, OnlineEvidenceRejected
from nextseek_api.eval.fit.fit_boundary import assert_paired_experimental_only, validate_publish_provenance
from nextseek_api.eval.online_observation import DEFAULT_SELECTION_CAVEAT, OnlineObservationalRow
from nextseek_api.eval.paired_run import build_paired_batch
from nextseek_api.eval.router_models_proposal import RouteSource


def test_mutation_online_kind_must_be_rejected():
    with pytest.raises(OnlineEvidenceRejected):
        assert_paired_experimental_only(
            {"evidence_kind": EvidenceKind.online_observational.value, "pair_id": "p1"}
        )


def test_mutation_publish_without_paired_run_id_fails():
    with pytest.raises(OnlineEvidenceRejected):
        validate_publish_provenance({"evidence_kind": "paired_experimental"})


def test_mutation_mixed_batch_kind_fails():
    with pytest.raises(Exception):
        build_paired_batch(
            paired_run_id="run-x",
            pairs=[],
            arm_records={},
            evidence_kind=EvidenceKind.online_observational,
        )


def test_monitoring_module_ast_has_no_publish_call():
    import ast
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "cc_assistant" / "route_monitoring.py"
    tree = ast.parse(path.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in {"publish", "activate_generation", "run_v14_generation"}


def test_online_row_cannot_use_forced_source():
    with pytest.raises((OnlineEvidenceRejected, Exception)):
        OnlineObservationalRow(
            observation_id="o1",
            session_id="s",
            turn_number=0,
            route="container_cc",
            route_source=RouteSource.forced,
            selection_caveat=DEFAULT_SELECTION_CAVEAT,
        )

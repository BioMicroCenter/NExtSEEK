"""V4-7 fit boundary refuse tests (Lane A)."""
from __future__ import annotations

import pytest

from nextseek_api.eval.conservation import FitAdmission
from nextseek_api.eval.evidence_kinds import OnlineEvidenceRejected, UnapprovedPairedRun
from nextseek_api.eval.fit.fit_boundary import (
    assert_paired_experimental_only,
    assert_zero_online_ids_in_hash,
    compute_paired_input_hash,
    refuse_raw_dict_fit_input,
    validate_publish_provenance,
)
from nextseek_api.eval.fit.v14.combined import run_v14_generation
from nextseek_api.eval.fit.v14.fit_config import V14FitConfig
from nextseek_api.eval.fit.v14.pair_rows import build_pair_rows
from nextseek_api.eval.online_observation import DEFAULT_SELECTION_CAVEAT, OnlineObservationalRow
from nextseek_api.eval.paired_run import build_paired_batch
from nextseek_api.eval.router_models_proposal import RouteSource


def _sample_batch(run_id: str = "unapproved-run"):
    pairs = [{"pair_id": "p1", "query_id": "q1", "family": "fam_a", "ns_arm_id": "ns1", "cc_arm_id": "cc1"}]
    arms = {
        "ns1": {"pair_id": "p1", "route": "nextseek", "combined_success": True, "latency_seconds": 2.0, "latency_censored": False},
        "cc1": {"pair_id": "p1", "route": "container_cc", "combined_success": False, "latency_seconds": 3.0, "latency_censored": False},
    }
    return build_paired_batch(paired_run_id=run_id, pairs=pairs, arm_records=arms), arms


def test_online_row_rejected_at_boundary():
    row = OnlineObservationalRow(
        observation_id="obs-1",
        session_id="s1",
        turn_number=1,
        route="container_cc",
        route_source=RouteSource.baml,
        selection_caveat=DEFAULT_SELECTION_CAVEAT,
    )
    with pytest.raises(OnlineEvidenceRejected):
        assert_paired_experimental_only(row)


def test_forged_online_dict_rejected():
    with pytest.raises(OnlineEvidenceRejected):
        assert_paired_experimental_only({"evidence_kind": "online_observational", "pair_id": "p1"})


def test_unapproved_paired_run_refused():
    batch, arms = _sample_batch("not-in-registry")
    admission = FitAdmission(retained_pairs=[("p1", "q1", "fam_a")], excluded_pair_ids=[], pending_pair_ids=[])
    with pytest.raises(UnapprovedPairedRun):
        build_pair_rows(admission, arms, paired_batch=batch)


def test_approved_batch_path_requires_registry(db):
    from nextseek_api.eval.paired_run_registry import register_paired_run

    batch, arms = _sample_batch("approved-run-a")
    register_paired_run(
        paired_run_id="approved-run-a",
        schema_version="paired_run/v1",
        content_hash="hash-a",
    )
    admission = FitAdmission(retained_pairs=[("p1", "q1", "fam_a")], excluded_pair_ids=[], pending_pair_ids=[])
    rows = build_pair_rows(admission, arms, paired_batch=batch)
    assert len(rows) == 1
    cfg = V14FitConfig()
    result = run_v14_generation(rows, cfg, seed=0, use_mcmc=False, paired_batch=batch)
    assert result.decision.generation_status


def test_publish_provenance_rejects_online_route_source():
    with pytest.raises(OnlineEvidenceRejected):
        validate_publish_provenance(
            {
                "paired_run_id": "run-1",
                "evidence_kind": "paired_experimental",
                "route_source": "baml",
            }
        )


def test_publish_provenance_requires_paired_run_id():
    with pytest.raises(OnlineEvidenceRejected):
        validate_publish_provenance({"evidence_kind": "paired_experimental"})


def test_raw_dict_fit_input_refused():
    with pytest.raises(OnlineEvidenceRejected):
        refuse_raw_dict_fit_input({"pair_id": "p1"}, context="v14")


def test_conservation_zero_online_ids_in_hash():
    batch, _ = _sample_batch("hash-run")
    digest = compute_paired_input_hash(batch)
    assert digest
    assert_zero_online_ids_in_hash(batch, {"obs-999"})
    with pytest.raises(OnlineEvidenceRejected):
        assert_zero_online_ids_in_hash(batch, {"p1"})

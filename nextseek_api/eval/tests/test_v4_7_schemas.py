"""V4-7 evidence schema unit tests (Lane A)."""
from __future__ import annotations

import pytest

from nextseek_api.eval.evidence_kinds import (
    EvidenceKind,
    ForgedEvidenceDiscriminator,
    MixedEvidenceBatch,
    OnlineEvidenceRejected,
    PAIRED_RUN_SCHEMA_VERSION,
)
from nextseek_api.eval.online_observation import (
    BANNED_COUNTERFACTUAL_PHRASES,
    DEFAULT_SELECTION_CAVEAT,
    OnlineObservationalRow,
)
from nextseek_api.eval.paired_run import PairedExperimentalBatch, build_paired_batch
from nextseek_api.eval.router_models_proposal import RouteSource


def test_paired_batch_requires_paired_kind():
    batch = build_paired_batch(
        paired_run_id="run-1",
        pairs=[{"pair_id": "p1", "query_id": "q1", "family": "fam"}],
        arm_records={},
    )
    assert batch.evidence_kind is EvidenceKind.paired_experimental
    assert batch.schema_version == PAIRED_RUN_SCHEMA_VERSION


def test_paired_batch_rejects_wrong_schema_version():
    with pytest.raises((ForgedEvidenceDiscriminator, Exception)):
        build_paired_batch(
            paired_run_id="run-1",
            pairs=[],
            arm_records={},
            schema_version="online_observation/v1",
        )


def test_paired_batch_rejects_online_kind():
    with pytest.raises(MixedEvidenceBatch):
        build_paired_batch(
            paired_run_id="run-1",
            pairs=[],
            arm_records={},
            evidence_kind=EvidenceKind.online_observational,
        )


def test_online_row_requires_caveat():
    row = OnlineObservationalRow(
        observation_id="obs-1",
        session_id="s1",
        turn_number=1,
        route="container_cc",
        route_source=RouteSource.baml,
        selection_caveat=DEFAULT_SELECTION_CAVEAT,
    )
    assert row.evidence_kind is EvidenceKind.online_observational


def test_online_row_rejects_forced_route_source():
    with pytest.raises((OnlineEvidenceRejected, Exception)):
        OnlineObservationalRow(
            observation_id="obs-1",
            session_id="s1",
            turn_number=1,
            route="container_cc",
            route_source=RouteSource.forced,
            selection_caveat=DEFAULT_SELECTION_CAVEAT,
        )


@pytest.mark.parametrize("phrase", BANNED_COUNTERFACTUAL_PHRASES)
def test_online_row_rejects_counterfactual_phrases(phrase: str):
    with pytest.raises((OnlineEvidenceRejected, Exception)):
        OnlineObservationalRow(
            observation_id="obs-1",
            session_id="s1",
            turn_number=1,
            route="container_cc",
            route_source=RouteSource.baml,
            selection_caveat=f"Traffic note: the {phrase} here.",
        )


def test_paired_batch_extra_forbid():
    with pytest.raises(Exception):
        PairedExperimentalBatch(
            schema_version=PAIRED_RUN_SCHEMA_VERSION,
            evidence_kind=EvidenceKind.paired_experimental,
            paired_run_id="run-1",
            pairs=[],
            arm_records={},
            forged_field="nope",
        )

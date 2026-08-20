"""Fast Task-3 publication, validation, activation, and snapshot edge tests."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from nextseek_api.assistant.models_db import (
    ActiveGenerationPointer,
    FamilyPosterior,
    PosteriorGeneration,
)
from nextseek_api.cc_assistant.family_labels import corpus_snapshot
from nextseek_api.cc_assistant.tests.generation_test_factory import (
    _publish_generation_for_test,
)
from nextseek_api.eval.fit.v14.combined import CombinedFitResult
from nextseek_api.eval.fit.v14.decision import (
    CandidateDecision,
    DecisionStatus,
    GenerationDecision,
)
from nextseek_api.eval.fit.v14.latency_model import DescriptiveLatencyResult
from nextseek_api.eval.fit.v14.quality_model import DescriptiveQualityResult
from nextseek_api.eval.generation_store import (
    EMPTY_ACTIVE_HASH,
    ActivationAbort,
    ActivationError,
    GenerationManifest,
    PermissionError as StorePermissionError,
    PublishError,
    activate_generation,
    create_generation,
    generation_content_hash,
    get_active_snapshot,
    get_current_active_hash,
    get_pinned_snapshot_for_turn,
    manifest_from_generation,
    pin_generation_for_turn,
    publish_generation,
    require_activate_permission,
    require_publish_permission,
    rollback_generation,
    set_test_abort_activate_after_pointer_mutate,
    set_test_abort_publish_after_generation,
)
from nextseek_api.eval import generation_store
from nextseek_api.eval.generation_validation import (
    require_valid_for_activation,
    validate_generation_for_activation,
)
from nextseek_api.eval.paired_run_registry import register_paired_run
from nextseek_api.eval.publish import (
    FitGroup,
    FitResult,
    PublicationEvidence,
    PublicationEvidenceRequired,
    _manifest_from_fit_result,
    manifest_for_combined,
)

pytestmark = pytest.mark.django_db


def _evidence(**overrides) -> PublicationEvidence:
    base = PublicationEvidence(
        input_hash="input",
        attempt_hash="attempt",
        aggregate_hash="aggregate",
        compatibility_keys={"taxonomy_version": "2", "corpus_hash": "corpus"},
        counts={"retained_pairs": 5},
        exclusions={},
        fit_diagnostics={"authoritative": True, "diagnostics_ok": True},
        source_provenance={
            "paired_run_id": "run",
            "paired_run_content_hash": "run-hash",
            "evidence_kind": "paired_experimental",
            "route_source": "forced",
            "model_mode": "authoritative_mcmc",
            "functional_success_source": "stored_judgments",
        },
        family_retained_pairs={"ns": 5, "cc": 5, "watch": 5},
    )
    return replace(base, **overrides)


def _combined() -> CombinedFitResult:
    decision = GenerationDecision(
        candidates=(
            CandidateDecision("ns", DecisionStatus.quality_ns, 0.01, True),
            CandidateDecision("cc", DecisionStatus.quality_cc, 0.02, True),
            CandidateDecision("watch", DecisionStatus.indecisive, 1.2, False),
        ),
        posterior_expected_fdr=0.015,
        activated_families=("ns", "cc"),
        generation_status="activated_all",
        config_fingerprint="fp",
    )
    descriptive_q = DescriptiveQualityResult("ns", np.ones(4) / 4, 0.0)
    return CombinedFitResult(
        quality={"ns": descriptive_q},
        latency={"ns": DescriptiveLatencyResult("ns", 5)},
        decision=decision,
        diagnostics_ok=True,
        quality_mcmc=False,
        latency_mcmc=False,
    )


def test_publication_evidence_validation_refusals_and_initial_override():
    with pytest.raises(PublicationEvidenceRequired, match="missing evidence hashes"):
        _evidence(input_hash="").validate(for_publication=False)
    with pytest.raises(PublicationEvidenceRequired, match="compatibility_keys"):
        _evidence(compatibility_keys={}).validate(for_publication=False)
    with pytest.raises(PublicationEvidenceRequired, match="retained_pairs"):
        _evidence(counts={}).validate(for_publication=False)
    with pytest.raises(PublicationEvidenceRequired, match="missing source provenance"):
        _evidence(source_provenance={}).validate(for_publication=False)

    human = {
        **_evidence().source_provenance,
        "functional_success_source": "human_grades",
        "judge_calls_used": 1,
    }
    with pytest.raises(PublicationEvidenceRequired, match="judge_calls_used=0"):
        _evidence(source_provenance=human).validate(for_publication=False)
    _evidence(
        source_provenance={**human, "judge_calls_used": 0}
    ).validate(for_publication=False)

    provisional = _evidence(
        fit_diagnostics={"authoritative": False, "diagnostics_ok": False}
    )
    with pytest.raises(PublicationEvidenceRequired, match="authoritative diagnostics"):
        provisional.validate(for_publication=True)
    initial_provenance = {
        **provisional.source_provenance,
        "model_mode": "initial_human_grade",
        "initial_release_override": True,
    }
    provisional = replace(provisional, source_provenance=initial_provenance)
    provisional.validate(for_publication=True, allow_initial_release_override=True)

    legacy = replace(
        _evidence(),
        source_provenance={
            **_evidence().source_provenance,
            "stack_identity_status": "legacy_git_sha_only",
        },
    )
    with pytest.raises(PublicationEvidenceRequired, match="legacy git-SHA"):
        legacy.validate(for_publication=True)
    legacy_initial = replace(
        legacy,
        source_provenance={
            **legacy.source_provenance,
            "model_mode": "initial_human_grade",
            "initial_release_override": True,
        },
    )
    legacy_initial.validate(for_publication=True, allow_initial_release_override=True)


def test_combined_manifest_all_routes_and_missing_count_refusal():
    manifest = manifest_for_combined(_combined(), _evidence())
    assert [group["route"] for group in manifest.groups] == [
        "nextseek_query",
        "container_cc",
        "container_cc",
    ]
    assert manifest.groups[-1]["posterior_mean"] == 0.0
    assert manifest.decision_results["activated_families"] == ["ns", "cc"]
    with pytest.raises(TypeError, match="unsupported fit result"):
        manifest_for_combined(object(), _evidence())
    with pytest.raises(PublicationEvidenceRequired, match="missing retained-pair"):
        manifest_for_combined(_combined(), replace(_evidence(), family_retained_pairs={}))


def _fit_result(**overrides) -> FitResult:
    base = FitResult(
        groups=[FitGroup("fam", "container_cc", 0.9, "Reliable", 5)],
        input_hash="input",
        attempt_hash="attempt",
        aggregate_hash="aggregate",
        config_fingerprint="fp",
        compatibility_keys={"taxonomy_version": "2", "corpus_hash": "corpus"},
        counts={"retained_pairs": 5},
        source_provenance={"source": "explicit"},
    )
    for key, value in overrides.items():
        setattr(base, key, value)
    return base


def test_fit_result_manifest_explicit_and_payload_fallbacks():
    manifest = _manifest_from_fit_result(_fit_result())
    assert manifest.groups[0]["name"] == "fam"
    fallback = _fit_result(
        compatibility_keys={},
        counts={},
        source_provenance={},
        payload={
            "compatibility_keys": {"taxonomy_version": "2", "corpus_hash": "corpus"},
            "counts": {"retained_pairs": 5},
            "source_provenance": {"source": "payload"},
            "exclusions": {"bad": 1},
            "fit_diagnostics": {"diagnostics_ok": True},
            "extra": "decision",
        },
    )
    fallback_manifest = _manifest_from_fit_result(fallback)
    assert fallback_manifest.exclusions == {"bad": 1}
    assert fallback_manifest.decision_results == {"extra": "decision"}


@pytest.mark.parametrize(
    ("update", "message"),
    [
        ({"input_hash": ""}, "missing explicit fields"),
        ({"compatibility_keys": {}}, "exact taxonomy_version"),
        ({"counts": {}}, "counts.retained_pairs"),
        ({"source_provenance": {}}, "explicit source_provenance"),
    ],
)
def test_fit_result_manifest_refusals(update, message):
    with pytest.raises(PublicationEvidenceRequired, match=message):
        _manifest_from_fit_result(_fit_result(**update))


def _store_manifest(suffix: str = "a", **overrides) -> GenerationManifest:
    current = corpus_snapshot()
    run_id = f"task3-store-{suffix}"
    run_hash = f"hash-{suffix}"
    register_paired_run(
        paired_run_id=run_id,
        schema_version="paired_run/v1",
        content_hash=run_hash,
    )
    base = GenerationManifest(
        input_hash=f"input-{suffix}",
        attempt_hash=f"attempt-{suffix}",
        aggregate_hash=f"aggregate-{suffix}",
        config_fingerprint="cfg",
        decision_status="activated_all",
        groups=[
            {
                "name": "fam",
                "route": "container_cc",
                "posterior_mean": 0.9,
                "band": "Reliable",
                "n_total": 5,
            }
        ],
        compatibility_keys={
            "taxonomy_version": current.taxonomy_version,
            "corpus_hash": current.corpus_sha256,
        },
        counts={"retained_pairs": 5},
        source_provenance={
            "paired_run_id": run_id,
            "paired_run_content_hash": run_hash,
            "evidence_kind": "paired_experimental",
            "route_source": "forced",
        },
    )
    return replace(base, **overrides)


def test_store_hooks_permissions_disabled_entrypoints_and_hash_stability(monkeypatch):
    set_test_abort_publish_after_generation(True)
    assert generation_store._should_abort_publish_after_generation()
    set_test_abort_publish_after_generation(False)
    monkeypatch.setenv("PLAN018_TEST_ABORT_PUBLISH_AFTER_GENERATION", "1")
    assert generation_store._should_abort_publish_after_generation()
    monkeypatch.delenv("PLAN018_TEST_ABORT_PUBLISH_AFTER_GENERATION")
    set_test_abort_activate_after_pointer_mutate(True)
    assert generation_store._should_abort_activate_after_pointer_mutate()
    set_test_abort_activate_after_pointer_mutate(False)

    require_publish_permission("local")
    require_activate_permission("local")
    with pytest.raises(StorePermissionError, match="live publish"):
        require_publish_permission("live:ops")
    with pytest.raises(StorePermissionError, match="live activation"):
        require_activate_permission("live:ops")
    manifest = _store_manifest()
    with pytest.raises(PublishError, match="direct generation creation"):
        create_generation(manifest)
    with pytest.raises(PublishError, match="direct generation publication"):
        publish_generation(manifest)
    assert generation_content_hash(value=1) == generation_content_hash(value=1)
    assert generation_content_hash(groups=[{"name": "x", "fitted_at": "now"}]) == generation_content_hash(groups=[{"name": "x", "fitted_at": "later"}])


def test_manifest_fallback_reconstructs_parent_and_timestamp():
    posterior = SimpleNamespace(
        task_family="fam",
        route="container_cc",
        posterior_mean=0.8,
        band="Reliable",
        n_total=4,
        fitted_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    generation = SimpleNamespace(
        payload={"counts": {"retained_pairs": "4"}},
        parent_id=1,
        parent=SimpleNamespace(generation_hash="parent"),
        input_hash="input",
        config_fingerprint="cfg",
        decision_status="activated_all",
    )
    manifest = manifest_from_generation(generation, [posterior])
    assert manifest.parent_hash == "parent"
    assert manifest.attempt_hash == "input"
    assert manifest.groups[0]["fitted_at"].startswith("2026-01-01")


def test_empty_pointer_rollback_pin_and_pinned_snapshot_refusals():
    assert get_current_active_hash() == EMPTY_ACTIVE_HASH
    assert get_active_snapshot() is None
    turn = SimpleNamespace(pinned_generation_id=None, pinned_generation_hash=None)
    with pytest.raises(ActivationError, match="no active"):
        pin_generation_for_turn(turn)
    assert get_pinned_snapshot_for_turn(turn) is None

    generation = _publish_generation_for_test(_store_manifest("pin"))
    turn = SimpleNamespace(
        pinned_generation_id=generation.id,
        pinned_generation_hash="wrong",
    )
    assert get_pinned_snapshot_for_turn(turn) is None
    turn.pinned_generation_id = 999999
    assert get_pinned_snapshot_for_turn(turn) is None


def test_rollback_stale_no_previous_and_activation_abort():
    generation = _publish_generation_for_test(_store_manifest("rollback"))
    activate_generation(generation, expected_hash=EMPTY_ACTIVE_HASH)
    with pytest.raises(ActivationError, match="stale CAS"):
        rollback_generation(expected_hash="wrong")
    with pytest.raises(ActivationError, match="no previous"):
        rollback_generation(expected_hash=generation.generation_hash)

    other = _publish_generation_for_test(_store_manifest("abort"))
    set_test_abort_activate_after_pointer_mutate(True)
    try:
        with pytest.raises(ActivationAbort):
            activate_generation(other, expected_hash=generation.generation_hash)
    finally:
        set_test_abort_activate_after_pointer_mutate(False)
    assert get_current_active_hash() == generation.generation_hash


def test_validation_missing_rows_canonical_and_current_corpus_failure(monkeypatch):
    generation = _publish_generation_for_test(_store_manifest("validate"))
    FamilyPosterior.objects.filter(generation=generation).delete()
    result = validate_generation_for_activation(generation)
    assert any("no family posteriors" in reason for reason in result.reasons)

    generation = _publish_generation_for_test(_store_manifest("no-canonical"))
    generation.payload = {
        key: value
        for key, value in (generation.payload or {}).items()
        if key != "_canonical_hash_inputs"
    }
    generation.save(update_fields=["payload"])
    result = validate_generation_for_activation(generation)
    assert any("canonical hash inputs missing" in reason for reason in result.reasons)

    generation = _publish_generation_for_test(_store_manifest("corpus-error"))
    monkeypatch.setattr(
        "nextseek_api.cc_assistant.family_labels.corpus_snapshot",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    result = validate_generation_for_activation(generation)
    assert any("current corpus unavailable" in reason for reason in result.reasons)
    with pytest.raises(Exception, match="current corpus unavailable"):
        require_valid_for_activation(generation)

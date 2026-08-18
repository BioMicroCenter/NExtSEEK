"""Fast branch coverage for the Plan 018 V4-9 Task-4 safety surface.

All provider-facing behavior uses in-memory fakes.  The Django-marked cases use
the ordinary disposable test database; no test in this module needs network or
statistical fitting.
"""
from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.db import IntegrityError
from django.utils import timezone

from nextseek_api.assistant.models_db import (
    ApprovedRunManifest,
    PaidRunState,
    SpendReservation,
)
from nextseek_api.cc_assistant import (
    baml_introspect,
    family_labels,
    posterior_selector,
    route_monitoring,
    router,
    transport_trace,
)
from nextseek_api.eval import judging_engine, run_authorization
from nextseek_api.eval.evidence_kinds import (
    EvidenceKind,
    ForgedEvidenceDiscriminator,
    ONLINE_OBSERVATION_SCHEMA_VERSION,
    OnlineEvidenceRejected,
    PAIRED_RUN_SCHEMA_VERSION,
)
from nextseek_api.eval.fake_provider import ProviderCallResult
from nextseek_api.eval.generation_store import GenerationSnapshot
from nextseek_api.eval.judging_engine import JudgeAttemptSpec, JudgingEngine
from nextseek_api.eval.online_observation import OnlineObservationalRow
from nextseek_api.eval.paid_run_state import (
    ResumeError,
    ensure_attempt_pending,
    mark_attempt_failed,
    mark_attempt_succeeded,
)
from nextseek_api.eval.paired_run import PairedExperimentalBatch
from nextseek_api.eval.router_models_proposal import RouteSource
from nextseek_api.eval.run_authorization import AuthorizationError
from nextseek_api.eval.run_manifest import manifest_body_hash
from nextseek_api.eval.spend_conservation import (
    ConservationSnapshot,
    compute_conservation,
)
from nextseek_api.eval.tests.v4_8_fixtures import (
    sample_manifest_dict,
    sample_run_manifest,
)


def test_label_helpers_cover_refusals_and_empty_descriptions(tmp_path):
    assert baml_introspect.validate_member(None, {"sample_search"}) is False
    assert baml_introspect.validate_member("sample_search", {"sample_search"}) is True
    assert family_labels._repo_root().is_dir()

    bad_corpus = tmp_path / "corpus.json"
    bad_corpus.write_text('{"families": ["not-an-object"]}')
    with pytest.raises(ValueError, match="families must be an object"):
        family_labels.corpus_snapshot(bad_corpus)

    snap = family_labels.CorpusSnapshot(
        corpus_path=str(bad_corpus),
        corpus_sha256="a" * 64,
        taxonomy_version="tax-v1",
        families=("no_description",),
        descriptions={},
    )
    builder = family_labels.runtime_type_builder(snap)
    assert "no_description" in baml_introspect.declared_family_members(
        family_labels.type_builder(snap)
    )
    assert builder is not None


def _selector_snapshot(*, posteriors=()) -> GenerationSnapshot:
    current = family_labels.corpus_snapshot()
    return GenerationSnapshot(
        generation_id=4,
        generation_hash="d" * 64,
        decision_status="activated_all",
        posteriors=tuple(posteriors),
        taxonomy_version=current.taxonomy_version,
        corpus_hash=current.corpus_sha256,
        content_valid=True,
    )


def test_selector_refuses_corpus_failure_missing_family_and_nondecisive(monkeypatch):
    snap = _selector_snapshot()
    assert posterior_selector.select_route("unrelated", snapshot=snap) is None
    monkeypatch.setattr(posterior_selector, "corpus_snapshot", lambda: (_ for _ in ()).throw(ValueError("bad")))
    assert posterior_selector.select_route("sample_search", snapshot=snap) is None

    monkeypatch.setattr(posterior_selector, "corpus_snapshot", family_labels.corpus_snapshot)
    assert posterior_selector.select_route("missing_family", snapshot=snap) is None
    uncertain = SimpleNamespace(
        task_family="sample_search",
        route="nextseek_query",
        posterior_mean=0.8,
        band="TooUncertain",
    )
    assert posterior_selector.select_route(
        "sample_search", snapshot=_selector_snapshot(posteriors=(uncertain,))
    ) is None


def _observational_row(**overrides):
    values = {
        "schema_version": ONLINE_OBSERVATION_SCHEMA_VERSION,
        "evidence_kind": EvidenceKind.online_observational,
        "observation_id": "obs-1",
        "session_id": "session-1",
        "turn_number": 1,
        "route": "nextseek_query",
        "route_source": RouteSource.baml,
        "task_family": "sample_search",
        "assignment_propensity": None,
        "propensity_unavailable": False,
        "propensity_unavailable_reason": None,
        "assignment_policy": None,
        "generation_id": None,
        "generation_hash": None,
        "selection_caveat": "Observational traffic only.",
    }
    values.update(overrides)
    return OnlineObservationalRow.model_construct(**values)


def test_monitoring_empty_and_no_optional_summary_paths():
    empty = route_monitoring.RouteMonitoringSnapshot({}, {}, 0, 0, {})
    assert route_monitoring._distribution_drift({}, {}) == 0.0
    assert route_monitoring.detect_monitoring_alerts(empty, empty) == []
    assert route_monitoring.build_route_monitoring_summary([]) == route_monitoring.MONITORING_DISCLAIMER
    row = _observational_row()
    baseline = route_monitoring.build_monitoring_snapshot([row])
    summary = route_monitoring.build_route_monitoring_summary([row], baseline=baseline)
    assert "assignment_policy=" not in summary
    assert "active_generation_hash=" not in summary
    assert "propensity_unavailable=" not in summary
    assert "Monitoring alerts:" not in summary
    with_generation = route_monitoring.build_route_monitoring_summary(
        [_observational_row(generation_hash="e" * 64)]
    )
    assert "active_generation_hash=" in with_generation


@pytest.mark.parametrize(
    ("task_family", "reasoning", "expected"),
    [(None, "unrelated", None), ("not_declared", "bad label", None)],
)
def test_classifier_refuses_null_and_undeclared_labels(monkeypatch, task_family, reasoning, expected):
    class FakeBaml:
        @staticmethod
        async def ClassifyQuery(**_kwargs):
            return SimpleNamespace(task_family=task_family, reasoning=reasoning)

    monkeypatch.setattr(
        router,
        "_load_router_deps",
        lambda: (object, object, SimpleNamespace(), FakeBaml),
    )
    monkeypatch.setattr(router, "runtime_type_builder", lambda _snap: object())
    monkeypatch.setattr(
        router,
        "type_builder",
        lambda _snap: {"members": [{"name": "sample_search"}]},
    )
    family, source, returned_reasoning = router._classify_query("query")
    assert family is expected
    assert source is None
    assert returned_reasoning


def test_router_provider_and_selector_exceptions_fall_back(monkeypatch):
    class RaisingBaml:
        @staticmethod
        async def ClassifyQuery(**_kwargs):
            raise RuntimeError("classifier offline")

    monkeypatch.setattr(
        router,
        "_load_router_deps",
        lambda: (object, object, SimpleNamespace(), RaisingBaml),
    )
    monkeypatch.setattr(router, "runtime_type_builder", lambda _snap: object())
    monkeypatch.setattr(
        router,
        "type_builder",
        lambda _snap: {"members": [{"name": "sample_search"}]},
    )
    assert router._classify_query("query") == (
        None,
        None,
        "classifier offline",
    )

    monkeypatch.setattr(router, "corpus_snapshot", lambda: object())
    monkeypatch.setattr(router, "type_builder", lambda _snap: {})
    monkeypatch.setattr(
        router,
        "_classify_query",
        lambda *_args: ("sample_search", "baml", "classified"),
    )
    monkeypatch.setattr(
        posterior_selector,
        "select_route",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("store offline")),
    )
    fallback = router.RouteDecision(
        route=router.ROUTE_NS,
        model_class=None,
        model_id=None,
        reasoning="legacy",
        source="baml",
    )
    monkeypatch.setattr(router, "_route_query", lambda *_args: fallback)
    decision = router._posterior_enabled_decide("query")
    assert decision.route == router.ROUTE_NS
    assert decision.source == "baml"
    assert decision.task_family == "sample_search"


def test_transport_hook_install_refuses_incomplete_generated_client():
    transport_trace.reset_transport_hooks()
    incomplete = SimpleNamespace(ClassifyQuery=object())
    transport_trace.install_transport_hooks(incomplete)
    assert transport_trace._HOOKS_INSTALLED is False


def _online_validator(**overrides):
    return _observational_row(**overrides)._validate_observational()


def test_online_observation_rejects_forged_discriminators_and_missing_reason():
    with pytest.raises(OnlineEvidenceRejected, match="expected online_observational"):
        _online_validator(evidence_kind=EvidenceKind.paired_experimental)
    with pytest.raises(ForgedEvidenceDiscriminator, match="expected schema"):
        _online_validator(schema_version="forged/v0")
    with pytest.raises(OnlineEvidenceRejected, match="requires propensity"):
        _online_validator(propensity_unavailable=True, propensity_unavailable_reason="")


def test_paired_model_validator_rejects_online_kind_and_blank_run_id():
    online = PairedExperimentalBatch.model_construct(
        schema_version=PAIRED_RUN_SCHEMA_VERSION,
        evidence_kind=EvidenceKind.online_observational,
        paired_run_id="run-1",
        pairs=[],
        arm_records={},
    )
    with pytest.raises(OnlineEvidenceRejected, match="expected paired_experimental"):
        online._validate_paired_only()

    blank = PairedExperimentalBatch.model_construct(
        schema_version=PAIRED_RUN_SCHEMA_VERSION,
        evidence_kind=EvidenceKind.paired_experimental,
        paired_run_id="  ",
        pairs=[],
        arm_records={},
    )
    with pytest.raises(ForgedEvidenceDiscriminator, match="non-empty"):
        blank._validate_paired_only()


@pytest.mark.django_db
def test_judging_engine_resumes_durable_success_without_transport():
    approved = run_authorization.approve_run_manifest(sample_run_manifest())
    state = ensure_attempt_pending(
        run_id="durable-run",
        manifest_hash=approved.manifest_hash,
        arm_id="arm-1",
        attempt_id="attempt-1",
        cache_key="cache-1",
    )
    mark_attempt_succeeded(state)
    engine = JudgingEngine(
        manifest_hash=approved.manifest_hash,
        cap_usd=Decimal("1"),
        run_id="durable-run",
    )
    result = engine.execute_attempt(
        JudgeAttemptSpec(
            arm_id="arm-1",
            attempt_id="attempt-1",
            idempotency_key="unused",
            input_fingerprint="unused",
            max_cost_usd=Decimal("0.01"),
        )
    )
    assert result.status == "succeeded"
    assert result.payload == "resumed:attempt-1"
    assert engine.transport.call_count == 0


@pytest.mark.django_db
def test_judging_engine_cache_tolerates_missing_durable_reload(monkeypatch):
    approved = run_authorization.approve_run_manifest(sample_run_manifest())
    spec = JudgeAttemptSpec(
        arm_id="arm-cache",
        attempt_id="attempt-cache",
        idempotency_key="unused",
        input_fingerprint="fp-cache",
        max_cost_usd=Decimal("0.01"),
    )
    engine = JudgingEngine(manifest_hash=approved.manifest_hash, cap_usd=Decimal("1"))
    cache_key = judging_engine.build_cache_key(
        input_fingerprint=spec.input_fingerprint,
        manifest_hash=approved.manifest_hash,
        model_version=spec.model_version,
    )
    engine.cache[cache_key] = ProviderCallResult("cached", Decimal("0.01"))
    monkeypatch.setattr(judging_engine, "get_attempt_state", lambda **_kwargs: None)
    result = engine.execute_attempt(spec)
    assert result.cached is True
    assert result.payload == "cached"
    assert engine.transport.call_count == 0


@pytest.mark.django_db
def test_paid_state_completed_refusal_and_failed_transition():
    approved = run_authorization.approve_run_manifest(sample_run_manifest())
    state = ensure_attempt_pending(
        run_id="state-run",
        manifest_hash=approved.manifest_hash,
        arm_id="arm-a",
        attempt_id="attempt-a",
        cache_key="cache-a",
    )
    mark_attempt_succeeded(state)
    with pytest.raises(ResumeError, match="already completed"):
        ensure_attempt_pending(
            run_id="state-run",
            manifest_hash=approved.manifest_hash,
            arm_id="arm-a",
            attempt_id="attempt-a",
            cache_key="cache-a",
        )

    failed = ensure_attempt_pending(
        run_id="state-run",
        manifest_hash=approved.manifest_hash,
        arm_id="arm-b",
        attempt_id="attempt-b",
        cache_key="cache-b",
    )
    mark_attempt_failed(failed, reason="provider refused")
    failed.refresh_from_db()
    assert failed.status == PaidRunState.STATUS_FAILED
    assert failed.failure_reason == "provider refused"


@pytest.mark.django_db
def test_approve_run_manifest_dict_replay_collision_and_consumed_paths():
    body = sample_manifest_dict(corpus_id="dict-replay")
    first = run_authorization.approve_run_manifest(body)
    assert run_authorization.approve_run_manifest(dict(body)).pk == first.pk

    first.manifest = {"forged": True}
    first.save(update_fields=["manifest"])
    with pytest.raises(AuthorizationError, match="collision"):
        run_authorization.approve_run_manifest(body)

    consumed_body = sample_manifest_dict(corpus_id="dict-consumed")
    consumed = run_authorization.approve_run_manifest(consumed_body)
    consumed.consumed = True
    consumed.save(update_fields=["consumed"])
    with pytest.raises(AuthorizationError, match="already consumed"):
        run_authorization.approve_run_manifest(consumed_body)


@pytest.mark.django_db
def test_manifest_approval_losing_insert_race_replays_existing(monkeypatch):
    run_body = sample_manifest_dict(corpus_id="race-run-api")
    run_existing = run_authorization.approve_run_manifest(run_body)
    legacy_body = sample_manifest_dict(corpus_id="race-legacy-api")
    legacy_existing = run_authorization.approve_manifest(legacy_body)

    def losing_create(**_kwargs):
        raise IntegrityError("simulated concurrent unique winner")

    for approve, body, expected in (
        (run_authorization.approve_run_manifest, run_body, run_existing),
        (run_authorization.approve_manifest, legacy_body, legacy_existing),
    ):
        with monkeypatch.context() as race:
            race.setattr(
                ApprovedRunManifest.objects,
                "filter",
                lambda **_kwargs: SimpleNamespace(exists=lambda: False),
            )
            race.setattr(ApprovedRunManifest.objects, "create", losing_create)
            assert approve(body).pk == expected.pk


@pytest.mark.django_db
def test_load_manifest_refusal_and_success_paths():
    with pytest.raises(AuthorizationError, match="not approved"):
        run_authorization._load_manifest("missing")

    valid = run_authorization.approve_run_manifest(
        sample_run_manifest(corpus_id="load-valid")
    )
    assert run_authorization._load_manifest(valid.manifest_hash).pk == valid.pk

    expired = run_authorization.approve_run_manifest(
        sample_run_manifest(corpus_id="load-expired")
    )
    expired.expires_at = timezone.now() - timedelta(seconds=1)
    expired.save(update_fields=["expires_at"])
    with pytest.raises(AuthorizationError, match="expired"):
        run_authorization._load_manifest(expired.manifest_hash)

    consumed = run_authorization.approve_run_manifest(
        sample_run_manifest(corpus_id="load-consumed")
    )
    consumed.consumed = True
    consumed.save(update_fields=["consumed"])
    with pytest.raises(AuthorizationError, match="already consumed"):
        run_authorization._load_manifest(consumed.manifest_hash)


@pytest.mark.django_db
def test_reservation_missing_nonpending_and_idempotent_terminal_transitions():
    approved = run_authorization.approve_run_manifest(sample_run_manifest())
    with pytest.raises(AuthorizationError, match="reservation required"):
        run_authorization.require_reservation(approved.manifest_hash, "missing")

    run_authorization.reserve_budget(
        approved.manifest_hash,
        attempt_id="terminal",
        idempotency_key="terminal-key",
        max_cost_usd=Decimal("0.10"),
    )
    run_authorization.reconcile_reservation("terminal", actual_usd=Decimal("0.08"))
    with pytest.raises(AuthorizationError, match="not pending"):
        run_authorization.require_reservation(approved.manifest_hash, "terminal")
    assert (
        run_authorization.reconcile_reservation("terminal", actual_usd=Decimal("0.09")).actual_usd
        == Decimal("0.08")
    )
    assert run_authorization.release_reservation("terminal").status == SpendReservation.STATUS_RECONCILED


def test_manifest_hash_accepts_validated_model_instance():
    manifest = sample_run_manifest()
    assert manifest_body_hash(manifest) == manifest_body_hash(
        manifest.model_dump(mode="json")
    )


def test_conservation_snapshot_refuses_bucket_and_call_mismatches():
    bad_money = ConservationSnapshot(
        approved_max_usd=Decimal("1"),
        available_usd=Decimal("0"),
        reserved_usd=Decimal("0"),
        reconciled_actual_usd=Decimal("0"),
        released_expired_usd=Decimal("0"),
        pending_calls=0,
        succeeded_calls=0,
        failed_calls=0,
    )
    with pytest.raises(ValueError, match="conservation mismatch"):
        bad_money.assert_balanced()

    negative_calls = ConservationSnapshot(
        approved_max_usd=Decimal("1"),
        available_usd=Decimal("1"),
        reserved_usd=Decimal("0"),
        reconciled_actual_usd=Decimal("0"),
        released_expired_usd=Decimal("0"),
        pending_calls=-1,
        succeeded_calls=0,
        failed_calls=0,
    )
    with pytest.raises(ValueError, match="negative call counts"):
        negative_calls.assert_balanced()


@pytest.mark.django_db
def test_conservation_refuses_unknown_reservation_status():
    approved = run_authorization.approve_run_manifest(sample_run_manifest())
    SpendReservation.objects.create(
        manifest=approved,
        attempt_id="unknown-status",
        idempotency_key="unknown-status-key",
        reserved_usd=Decimal("0.01"),
        status="unknown",
    )
    with pytest.raises(ValueError, match="do not partition"):
        compute_conservation(ApprovedRunManifest.objects.get(pk=approved.pk))


@pytest.mark.django_db
def test_conservation_counts_paid_run_statuses_and_refuses_unknown_status():
    approved = run_authorization.approve_run_manifest(sample_run_manifest())
    for index, status in enumerate(
        (
            PaidRunState.STATUS_PENDING,
            PaidRunState.STATUS_SUCCEEDED,
            PaidRunState.STATUS_CACHED,
            PaidRunState.STATUS_FAILED,
        )
    ):
        PaidRunState.objects.create(
            run_id="balanced-run",
            manifest=approved,
            overlap_lock=f"balanced-{index}",
            arm_id=f"arm-{index}",
            attempt_id=f"attempt-{index}",
            status=status,
        )
    compute_conservation(approved, run_id="balanced-run").assert_balanced()

    PaidRunState.objects.create(
        run_id="bad-run",
        manifest=approved,
        overlap_lock="bad-status",
        arm_id="bad-arm",
        attempt_id="bad-attempt",
        status="unknown",
    )
    with pytest.raises(ValueError, match="PaidRunState attempt IDs"):
        compute_conservation(approved, run_id="bad-run")

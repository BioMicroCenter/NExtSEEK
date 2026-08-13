"""Additional behavioral coverage for the source-derived V4-9 Task 2 cluster."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from nessie_tests import export as paired_export
from nextseek_api.eval.attempt_store import AttemptStore, AttemptStoreError
from nextseek_api.eval.disposition import OutcomeBucket, classify_arm, should_call_judge
from nextseek_api.eval.human_annotations import (
    HumanAnnotation,
    HumanAnnotationError,
    HumanAnnotationContext,
    HumanAnnotationRegistry,
    apply_human_annotation,
    content_hash,
    map_human_label,
)
from nextseek_api.eval.router_models_proposal import (
    ArtifactStatus,
    ErrorClass,
    EvalRow,
    FailureMode,
    FamilySource,
    RouteSource,
)


def _row(**overrides) -> EvalRow:
    values = {
        "query_id": "q1", "route": "nextseek_query", "task_family": "Search-Basic",
        "route_source": RouteSource.forced, "family_source": FamilySource.corpus,
        "stack_id": "stack-1", "answer_provided": True, "is_error": False,
        "timed_out": False, "runtime_success": True, "failure_mode": FailureMode.none,
        "error_class": ErrorClass.none, "latency_seconds": 1.0, "cost_usd": None,
        "artifact_expected": False, "artifact_status": ArtifactStatus.not_expected,
        "artifact_success": True, "functional_success": True,
    }
    values.update(overrides)
    return EvalRow(**values)


def _ctx() -> HumanAnnotationContext:
    return HumanAnnotationContext(
        run_id="run-1", corpus_fingerprint="corp", vocabulary_version="v1",
        allowed_authorities=frozenset({"maintainer"}), known_cases=frozenset({"c1"}),
        known_arms=frozenset({"arm-1"}), known_executions=frozenset({"ex-1"}),
    )


def _ann(label: str) -> HumanAnnotation:
    payload = {"label": label, "case_id": "c1", "arm_id": "arm-1",
               "execution_id": "ex-1", "question_hash": "qh"}
    return HumanAnnotation(
        run_id="run-1", corpus_fingerprint="corp", case_id="c1", question_hash="qh",
        arm_id="arm-1", execution_id="ex-1", annotator_id="human-1",
        annotator_authority="maintainer", vocabulary_version="v1", label=label,
        annotated_at="2026-08-11T00:00:00Z", content_hash=content_hash(payload),
    )


def test_attempt_store_rejects_unknown_id_and_tampered_retrievable_payload(tmp_path):
    """A stored hash must name retrievable, unaltered bytes before replay."""
    store = AttemptStore(tmp_path)
    record = store.write_attempt(
        arm_id="arm-1", call_index=0, input_fingerprint="fp", model_id="fake",
        prompt_version="v1", evaluator_version="v1", request_bytes=b"request",
        response_bytes=b"response", status="succeeded",
    )

    with pytest.raises(AttemptStoreError, match="unknown attempt_id"):
        store.load_attempt("absent")
    (tmp_path / record.request_path).write_bytes(b"substituted")
    with pytest.raises(AttemptStoreError, match="hash mismatch"):
        store.read_payload(record.request_sha256)


def test_attempt_store_verifies_defensively_when_a_payload_reader_is_inconsistent(tmp_path, monkeypatch):
    """Replay must fail closed if a lower payload reader violates its hash contract."""
    store = AttemptStore(tmp_path)
    record = store.write_attempt(
        arm_id="arm-1", call_index=0, input_fingerprint="fp", model_id="fake",
        prompt_version="v1", evaluator_version="v1", request_bytes=b"request",
        response_bytes=b"response", status="succeeded",
    )
    monkeypatch.setattr(store, "read_payload", lambda _sha: b"corrupted")
    with pytest.raises(AttemptStoreError, match="request hash verification failed"):
        store.verify_attempt(record.attempt_id)

    def inconsistent_reader(sha: str) -> bytes:
        return b"request" if sha == record.request_sha256 else b"corrupted-response"

    monkeypatch.setattr(store, "read_payload", inconsistent_reader)
    with pytest.raises(AttemptStoreError, match="response hash verification failed"):
        store.verify_attempt(record.attempt_id)


def test_disposition_distinguishes_infrastructure_and_desired_outcomes():
    """An unjudged broken arm is excluded, while a complete true outcome is scored."""
    desired = _row(functional_success=True)
    assert classify_arm(desired).bucket is OutcomeBucket.desired
    assert should_call_judge(_row(error_class=ErrorClass.provider_outage)) is False


def test_human_annotation_default_and_tamper_paths_are_fail_closed():
    """Human sidecars preserve matching judgment and reject unmapped or tampered facts."""
    annotation = _ann("pass")
    assert apply_human_annotation(OutcomeBucket.desired, None) is OutcomeBucket.desired
    assert apply_human_annotation(OutcomeBucket.desired, annotation) is OutcomeBucket.desired
    with pytest.raises(HumanAnnotationError, match="unknown label"):
        map_human_label("future-label")
    registry = HumanAnnotationRegistry(_ctx())
    with pytest.raises(HumanAnnotationError, match="tampered"):
        registry.ingest(annotation.model_copy(update={"content_hash": "wrong"}))


def test_human_annotation_rejects_corrupt_vocabulary_membership_and_corpus_identity(monkeypatch):
    """A vocabulary/config inconsistency cannot silently turn a sidecar into a score."""
    import nextseek_api.eval.human_annotations as annotations

    original_vocabulary = annotations.HUMAN_VOCABULARY
    monkeypatch.setattr(annotations, "HUMAN_VOCABULARY", frozenset({"future"}))
    with pytest.raises(HumanAnnotationError, match="unmapped"):
        annotations.map_human_label("future")
    monkeypatch.setattr(annotations, "HUMAN_VOCABULARY", original_vocabulary)
    with pytest.raises(HumanAnnotationError, match="orphan corpus_fingerprint"):
        HumanAnnotationRegistry(_ctx()).ingest(_ann("pass").model_copy(update={"corpus_fingerprint": "other"}))


def test_export_handles_empty_variant_and_refuses_an_empty_paired_manifest(tmp_path, capsys):
    """Missing question turns and a zero-pair manifest cannot silently make a valid export."""
    assert paired_export.query_text(SimpleNamespace(turns=[])) == ""
    assert paired_export.main(["--run", str(tmp_path)]) == 2
    assert "Run the suite with --bayesian first" in capsys.readouterr().err

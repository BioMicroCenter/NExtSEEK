from __future__ import annotations

from uuid import UUID

from chat_nextseek.evaluator.models import EvaluatorLookup, EvaluatorRunRecord
from chat_nextseek.evaluator.normalization import (
    _build_retry_signals,
    classify_path,
    normalize_from_bundle,
    normalize_from_task,
)


def test_classify_path_variants():
    assert classify_path("new_search", {}) == ("standard", "new_search", None)
    assert classify_path("plan", {}) == ("plan", "plan", None)
    assert classify_path("reporter", {"reporter_plan": {"reporter_mode": "summary"}}) == (
        "standard",
        "reporter",
        "reporter.summary",
    )
    assert classify_path("unknown", {}) == ("standard", "unsupported", None)


def test_build_retry_signals_for_search_bundle(fake_search_bundle):
    signals = _build_retry_signals(
        "completed",
        {"bundle_id": fake_search_bundle["id"]},
        fake_search_bundle,
        {"results_history": [fake_search_bundle]},
    )
    assert signals.api_ok is True
    assert signals.rows_returned == 3
    assert signals.has_artifacts is True


def test_build_retry_signals_for_plan_bundle(fake_plan_bundle):
    signals = _build_retry_signals(
        "completed",
        {"bundle_id": fake_plan_bundle["id"]},
        fake_plan_bundle,
        {"results_history": [fake_plan_bundle]},
    )
    assert signals.plan_steps_total == 2
    assert signals.plan_steps_failed == 1
    assert signals.plan_stop_reason == "bad step"


def test_normalize_from_bundle_uses_native_query_key(fake_session, fake_graph_bundle):
    response = normalize_from_bundle(session=fake_session, bundle=fake_graph_bundle)
    assert response.lookup.bundle_id == fake_graph_bundle["id"]
    assert response.run.query == "Show me sequencing data in the GBM study"
    assert response.routing.path_mode == "graph_query"
    assert response.lookup.session_id == UUID("00000000-0000-0000-0000-000000000001")


def test_normalize_from_bundle_marks_unsupported_without_mode(fake_session):
    bundle = {"id": 4, "user_query": "hello"}
    response = normalize_from_bundle(session=fake_session, bundle=bundle)
    assert response.routing.path_mode == "unsupported"
    assert response.retry_context.retryable is False


def test_normalize_from_task_prefers_persisted_payload(fake_session, fake_search_bundle):
    persisted = normalize_from_bundle(fake_session, fake_search_bundle)
    record = EvaluatorRunRecord(
        session_id=fake_session["session_id"],
        source_session_id=fake_session["session_id"],
        source_bundle_id=fake_search_bundle["id"],
        mode="standard",
        status="completed",
        query=fake_search_bundle["user_query"],
        normalized_payload=persisted.model_dump(mode="json"),
        lookup=EvaluatorLookup(
            task_id=UUID("00000000-0000-0000-0000-000000000123"),
            session_id=fake_session["session_id"],
            bundle_id=fake_search_bundle["id"],
            source="task",
        ),
    )

    restored = normalize_from_task(record)
    assert restored.lookup.bundle_id == fake_search_bundle["id"]
    assert restored.run.query == fake_search_bundle["user_query"]

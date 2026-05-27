from __future__ import annotations

from uuid import UUID

from chat_nextseek.orchestrator import _persist_bundle_reply
from chat_nextseek.evaluator.models import EvaluatorLookup, EvaluatorRunRecord
from chat_nextseek.evaluator.normalization import (
    _as_dict,
    _coerce_uuid,
    _extract_reply,
    _extract_rows_returned,
    build_retry_context,
    normalize_from_bundle,
    normalize_from_task,
)


def test_helper_extractors_cover_list_metadata_and_reply():
    assert _coerce_uuid("not-a-uuid") is None
    assert _as_dict(["x"]) == {}
    assert _extract_rows_returned([1, 2, 3]) == 3
    assert _extract_rows_returned({"metadata": {"count": 4}}) == 4
    assert _extract_reply({}, {"summary": "from result"}) == "from result"


def test_persist_bundle_reply_updates_search_graph_and_reporter_bundles():
    session = {
        "results_history": [
            {"id": 1, "mode": "new_search"},
            {"id": 2, "mode": "graph_query"},
            {"id": 3, "mode": "reporter"},
        ]
    }

    _persist_bundle_reply(session, 1, reply="search reply")
    _persist_bundle_reply(session, 2, reply="graph reply")
    _persist_bundle_reply(session, 3, reply="report reply")

    history = session["results_history"]
    assert history[0]["reply"] == "search reply"
    assert history[1]["reply"] == "graph reply"
    assert history[2]["reply"] == "report reply"


def test_persist_bundle_reply_updates_plan_bundle_with_provisional_and_final_reply():
    session = {"results_history": [{"id": 4, "mode": "plan"}]}

    _persist_bundle_reply(
        session,
        4,
        reply="final evaluator reply",
        provisional_reply="draft chatter reply",
    )

    bundle = session["results_history"][0]
    assert bundle["reply"] == "final evaluator reply"
    assert bundle["provisional_reply"] == "draft chatter reply"


def test_persist_bundle_reply_is_noop_for_unknown_bundle():
    session = {"results_history": [{"id": 1, "mode": "new_search"}]}

    _persist_bundle_reply(session, 999, reply="missing")

    assert "reply" not in session["results_history"][0]


def test_reporter_bundle_normalization_uses_reporter_subtype(fake_session):
    bundle = {
        "id": 5,
        "mode": "reporter",
        "user_query": "Summarize GBM project samples",
        "reporter_plan": {"reporter_mode": "summary"},
        "reporter_result": {"ok": True, "metadata": {"count": 6}},
        "report_saved_files": {"report.json": "/tmp/report.json"},
    }
    response = normalize_from_bundle(fake_session, bundle)

    assert response.routing.path_mode == "reporter"
    assert response.routing.path_subtype == "reporter.summary"
    assert response.retry_context.retry_signals.rows_returned == 6
    assert response.retry_context.retry_signals.has_artifacts is True


def test_normalize_from_bundle_uses_persisted_reply_for_search(fake_session, fake_search_bundle):
    bundle = dict(fake_search_bundle)
    bundle["reply"] = "Found three samples."

    response = normalize_from_bundle(fake_session, bundle)

    assert response.run.reply == "Found three samples."


def test_normalize_from_bundle_uses_final_plan_reply_over_provisional(fake_session, fake_plan_bundle):
    bundle = dict(fake_plan_bundle)
    bundle["provisional_reply"] = "draft"
    bundle["reply"] = "final"

    response = normalize_from_bundle(fake_session, bundle)

    assert response.run.reply == "final"


def test_build_retry_context_captures_error_state(fake_session):
    context = build_retry_context(
        task_status="failed",
        result={"error": "boom"},
        bundle={"mode": "graph_query", "graph_result": {"ok": False, "count": 0, "error": "boom"}},
        session=fake_session,
        retryable=True,
        assistant_context="ctx",
    )

    assert context.retry_signals.query_error_present is True
    assert context.retry_signals.graph_ok is False
    assert context.assistant_context == "ctx"


def test_normalize_from_task_with_bundle_overrides_lookup(fake_session, fake_search_bundle):
    record = EvaluatorRunRecord(
        task_id=UUID("00000000-0000-0000-0000-000000000124"),
        session_id=fake_session["session_id"],
        source_session_id=fake_session["session_id"],
        source_bundle_id=fake_search_bundle["id"],
        mode="standard",
        status="completed",
        query=fake_search_bundle["user_query"],
        reply="stored reply",
        user_id=9,
        lookup=EvaluatorLookup(
            task_id=UUID("00000000-0000-0000-0000-000000000124"),
            session_id=fake_session["session_id"],
            bundle_id=fake_search_bundle["id"],
            source="task",
        ),
    )

    response = normalize_from_task(record, session=fake_session, bundle=fake_search_bundle)
    assert response.lookup.source == "task"
    assert response.run.reply == "stored reply"
    assert response.run.user_id == 9


def test_normalize_from_task_without_bundle_handles_plan_and_standard_modes(fake_session):
    standard = EvaluatorRunRecord(
        task_id=UUID("00000000-0000-0000-0000-000000000125"),
        session_id=fake_session["session_id"],
        mode="standard",
        status="failed",
        query="hello",
        error="bad request",
    )
    plan = EvaluatorRunRecord(
        task_id=UUID("00000000-0000-0000-0000-000000000126"),
        session_id=fake_session["session_id"],
        mode="plan",
        status="completed",
        query="plan hello",
    )

    standard_response = normalize_from_task(standard, session=fake_session)
    plan_response = normalize_from_task(plan, session=fake_session)

    assert standard_response.routing.path_mode == "unsupported"
    assert standard_response.retry_context.retry_signals.query_error_present is True
    assert plan_response.routing.path_mode == "plan"

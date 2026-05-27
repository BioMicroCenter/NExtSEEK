from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from chat_nextseek.evaluator.reports import (
    AsyncBatchItemState,
    AsyncBatchRunState,
    EvalBatchReport,
    EvalReport,
    JudgmentSummary,
    RetryDecisionSummary,
    RetryOutcome,
    _build_judgment,
    _build_retry_decision,
    _build_retry_outcome,
    _coerce_uuid,
    _infer_bundle_mode,
    build_batch_report,
    build_dashboard_payload,
    evaluate_query,
    finalize_async_batch_state,
    load_async_batch_state,
    load_queries,
    run_batch_evaluation_async,
    run_batch_evaluation,
    result_to_report,
    write_async_batch_state,
    write_report,
)
from chat_nextseek.evaluator.workflow import InMemorySessionState


def _fake_retry_query(session, config, user_text, credentials=None, send_event=None):
    history = session.get("results_history", [])
    bundle_id = len(history) + 1
    history.append(
        {
            "id": bundle_id,
            "mode": "new_search",
            "user_query": user_text,
            "api_result_full": {"ok": True, "status_code": 200, "data": {"total": 2, "results": [{"id": 1}]}},
        }
    )
    session["results_history"] = history
    return {"reply": f"retried: {user_text}", "bundle_id": bundle_id, "files": []}


def test_write_batch_report(tmp_path):
    report = EvalBatchReport(
        run_id="demo-001",
        created_at=datetime(2026, 4, 17, tzinfo=timezone.utc),
        queries_total=1,
        queries_passed=1,
        queries_retried=0,
        queries_failed=0,
        results=[EvalReport(query="Find mice", status="completed")],
    )

    path = write_report(report, tmp_path)
    assert path.exists()
    assert path.name.startswith("eval-demo-001-")
    assert path.suffix == ".json"


def test_result_to_report_includes_retry_judgment(
    monkeypatch,
    evaluator_workflow,
    fake_empty_search_session,
    dummy_config,
):
    monkeypatch.setattr("chat_nextseek.orchestrator.run_query", _fake_retry_query)
    source = (
        f"bundle:{fake_empty_search_session['session_id']}:"
        f"{fake_empty_search_session['results_history'][0]['id']}"
    )
    result = evaluator_workflow.evaluate_source(
        source,
        session=fake_empty_search_session,
        execute_retry=True,
        config=dummy_config,
    )

    report = result_to_report("Find me mice treated with NDMA", result)
    assert report.retry_decision is not None
    assert report.retry_decision.verdict == "RETRY"
    assert report.retry_result is not None
    assert report.retry_result.retry_judgment.correctness in {"PASS", "PARTIAL"}


def test_report_helper_builders_handle_empty_payloads_and_uuid_values():
    sample_uuid = UUID("00000000-0000-0000-0000-000000000123")

    assert _coerce_uuid(None) is None
    assert _coerce_uuid(sample_uuid) == sample_uuid
    assert _build_judgment(None) is None
    assert _build_retry_decision(None) is None
    assert _build_retry_outcome(None) is None


def test_build_retry_outcome_returns_none_without_complete_retry_metadata():
    payload = {
        "retry_response": {"task_id": str(uuid4())},
        "evaluation": {
            "judgment": {
                "correctness": "PASS",
                "completeness": "PASS",
                "routing_quality": "PASS",
                "reasoning": "ok",
            }
        },
    }

    assert _build_retry_outcome(payload) is None


def test_build_dashboard_payload_counts_retry_comparison(
    monkeypatch,
    evaluator_workflow,
    fake_empty_search_session,
    dummy_config,
):
    monkeypatch.setattr("chat_nextseek.orchestrator.run_query", _fake_retry_query)
    source = (
        f"bundle:{fake_empty_search_session['session_id']}:"
        f"{fake_empty_search_session['results_history'][0]['id']}"
    )
    result = evaluator_workflow.evaluate_source(
        source,
        session=fake_empty_search_session,
        execute_retry=True,
        config=dummy_config,
    )
    report = build_batch_report([result_to_report("Find me mice treated with NDMA", result)])

    payload = build_dashboard_payload(report)
    assert payload["verdict_distribution"]["retry_count"] == 1
    assert len(payload["retry_comparisons"]) == 1


def test_build_batch_report_tracks_pass_and_fail_counts():
    report = build_batch_report(
        [
            EvalReport(
                query="pass",
                status="completed",
                judgment=JudgmentSummary(
                    correctness="PASS",
                    completeness="PASS",
                    routing_quality="PASS",
                    reasoning="ok",
                ),
                retry_decision=RetryDecisionSummary(
                    verdict="PASS",
                    should_retry=False,
                    reasoning="done",
                ),
            ),
            EvalReport(
                query="fail",
                status="failed",
                judgment=JudgmentSummary(
                    correctness="FAIL",
                    completeness="FAIL",
                    routing_quality="PASS",
                    reasoning="bad",
                ),
                retry_decision=RetryDecisionSummary(
                    verdict="FAIL",
                    should_retry=False,
                    reasoning="bad",
                ),
            ),
        ]
    )
    assert report.queries_passed == 1
    assert report.queries_failed == 1
    assert report.infra_failures == 0
    assert report.success is False
    assert report.run_status == "completed_with_failures"


def test_build_dashboard_payload_tracks_pass_and_fail_distribution():
    report = EvalBatchReport(
        run_id="demo",
        created_at=datetime(2026, 4, 17, tzinfo=timezone.utc),
        queries_total=2,
        queries_passed=1,
        queries_retried=0,
        queries_failed=1,
        results=[
            EvalReport(
                query="pass",
                status="completed",
                judgment=JudgmentSummary(
                    correctness="PASS",
                    completeness="PASS",
                    routing_quality="PASS",
                    reasoning="ok",
                ),
                retry_decision=RetryDecisionSummary(
                    verdict="PASS",
                    should_retry=False,
                    reasoning="done",
                ),
            ),
            EvalReport(
                query="fail",
                status="failed",
                judgment=JudgmentSummary(
                    correctness="FAIL",
                    completeness="PARTIAL",
                    routing_quality="FAIL",
                    reasoning="bad",
                ),
                retry_decision=RetryDecisionSummary(
                    verdict="FAIL",
                    should_retry=False,
                    reasoning="bad",
                ),
                retry_result=RetryOutcome(
                    retry_task_id=uuid4(),
                    retry_query="retry",
                    retry_judgment=JudgmentSummary(
                        correctness="PARTIAL",
                        completeness="FAIL",
                        routing_quality="PARTIAL",
                        reasoning="regressed",
                    ),
                ),
            ),
        ],
    )
    payload = build_dashboard_payload(report)
    assert payload["verdict_distribution"]["pass_count"] == 1
    assert payload["verdict_distribution"]["fail_count"] == 1
    assert payload["retry_comparisons"][0]["deltas"]["correctness"] == "improved"
    assert payload["retry_comparisons"][0]["deltas"]["completeness"] == "regressed"


def test_build_dashboard_payload_treats_unknown_scores_as_failures():
    report = EvalBatchReport(
        run_id="demo",
        created_at=datetime(2026, 4, 17, tzinfo=timezone.utc),
        queries_total=1,
        queries_passed=0,
        queries_retried=0,
        queries_failed=1,
        results=[
            EvalReport(
                query="unknown",
                status="failed",
                judgment=JudgmentSummary(
                    correctness="MAYBE",
                    completeness="PASS",
                    routing_quality="UNSURE",
                    reasoning="odd output",
                ),
                retry_decision=RetryDecisionSummary(
                    verdict="FAIL",
                    should_retry=False,
                    reasoning="bad",
                ),
            )
        ],
    )

    payload = build_dashboard_payload(report)
    assert payload["criterion_scores"]["correctness"]["fail_count"] == 1
    assert payload["criterion_scores"]["completeness"]["pass_count"] == 1
    assert payload["criterion_scores"]["routing_quality"]["fail_count"] == 1


def test_build_batch_report_marks_unsupported_as_queries_unsupported():
    report = build_batch_report(
        [
            EvalReport(
                query="unsupported",
                status="completed",
                path_mode="unsupported",
                judgment=JudgmentSummary(
                    correctness="FAIL",
                    completeness="FAIL",
                    routing_quality="FAIL",
                    reasoning="unsupported",
                ),
                retry_decision=RetryDecisionSummary(
                    verdict="FAIL",
                    should_retry=False,
                    reasoning="unsupported",
                ),
            )
        ]
    )

    assert report.queries_failed == 0
    assert report.queries_unsupported == 1
    assert report.infra_failures == 0
    assert report.success is False
    assert report.run_status == "completed_with_failures"


def test_load_queries_skips_comments(tmp_path):
    path = tmp_path / "queries.txt"
    path.write_text("\n# note\nfirst\nsecond\n", encoding="utf-8")
    assert load_queries(path) == ["first", "second"]


def test_load_queries_rejects_full_test_payload_without_list(tmp_path):
    path = tmp_path / "testing.json"
    path.write_text(json.dumps({"full_test": {"tests": "oops"}}), encoding="utf-8")

    with pytest.raises(ValueError, match="full_test\\.tests"):
        load_queries(path)


def test_load_queries_rejects_non_mapping_full_test_entry(tmp_path):
    path = tmp_path / "testing.json"
    path.write_text(json.dumps({"full_test": {"tests": ["oops"]}}), encoding="utf-8")

    with pytest.raises(ValueError, match="full_test entry 0"):
        load_queries(path)


def test_load_queries_rejects_non_list_json_payload(tmp_path):
    path = tmp_path / "queries.json"
    path.write_text(json.dumps("oops"), encoding="utf-8")

    with pytest.raises(ValueError, match="JSON list of query objects"):
        load_queries(path)


def test_load_queries_rejects_non_mapping_list_entry(tmp_path):
    path = tmp_path / "queries.json"
    path.write_text(json.dumps(["oops"]), encoding="utf-8")

    with pytest.raises(ValueError, match="entry 0"):
        load_queries(path)


def test_infer_bundle_mode_falls_back_to_planner_and_debug_mode():
    assert _infer_bundle_mode({}, planner=True) == "plan"
    assert _infer_bundle_mode({"mode": "reporter"}, planner=False) == "reporter"
    assert _infer_bundle_mode({"mode": "   "}, planner=False) is None


def test_evaluate_query_raises_when_runner_produces_no_bundle(monkeypatch, evaluator_workflow, dummy_config):
    def no_bundle(session, config, user_text, credentials=None, send_event=None):
        return {"reply": "no bundle"}

    monkeypatch.setattr("chat_nextseek.evaluator.reports.run_query", no_bundle)
    try:
        evaluate_query("missing", config=dummy_config, workflow=evaluator_workflow)
    except ValueError as exc:
        assert "without a bundle" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected ValueError")


def test_evaluate_query_synthesizes_bundle_from_runner_debug(monkeypatch, evaluator_workflow, dummy_config):
    def reporter_runner(session, config, user_text, credentials=None, send_event=None):
        return {
            "reply": "The reporter agent could not run the project report.\n\nError: backend unavailable",
            "debug": {
                "parser_plan": {"mode": "reporter", "report_mode": "summary"},
                "reporter_plan": {"reporter_mode": "summary", "summary_mode": "RPPR"},
                "reporter_result": {"ok": False, "error": "backend unavailable"},
            },
            "files": [{"key": "reporter_result", "path": "/tmp/reporter.json"}],
        }

    session = InMemorySessionState(
        {
            "session_id": str(uuid4()),
            "user_id": 0,
            "results_history": [],
            "last_debug": {},
        }
    )
    monkeypatch.setattr("chat_nextseek.evaluator.reports.run_query", reporter_runner)

    result = evaluate_query(
        "Generate an annual progress report for CSBC.",
        config=dummy_config,
        workflow=evaluator_workflow,
        execute_retry=False,
        session=session,
    )

    assert result["evaluation"]["routing"]["path_mode"] == "reporter"
    assert result["run_record"]["reply"].startswith("The reporter agent could not run")
    assert session["results_history"][0]["reply"].startswith("The reporter agent could not run")


def test_evaluate_query_uses_planner_runner(monkeypatch, evaluator_workflow, dummy_config):
    def planner_runner(session, config, user_text, credentials=None, send_event=None):
        session["results_history"] = [
            {
                "id": 1,
                "mode": "plan",
                "user_query": user_text,
                "plan": {"steps": []},
                "step_results": {},
                "plan_debug_path": "/tmp/plan.json",
            }
        ]
        return {"reply": "planned", "bundle_id": 1, "files": []}

    monkeypatch.setattr("chat_nextseek.evaluator.reports.run_query_plan", planner_runner)
    result = evaluate_query("plan it", config=dummy_config, workflow=evaluator_workflow, planner=True, execute_retry=False)
    assert result["evaluation"]["routing"]["execution_mode"] == "plan"


def test_run_batch_evaluation_records_exception(monkeypatch, evaluator_workflow, dummy_config):
    def raising_runner(session, config, user_text, credentials=None, send_event=None):
        raise RuntimeError("boom")

    monkeypatch.setattr("chat_nextseek.evaluator.reports.run_query", raising_runner)
    report = run_batch_evaluation(["bad query"], config=dummy_config, workflow=evaluator_workflow)
    assert report.queries_failed == 0
    assert report.queries_exceptions == 1
    assert report.results[0].status == "exception"
    assert report.infra_failures == 0
    assert report.success is False
    assert report.run_status == "crashed"


def test_async_batch_state_round_trip(tmp_path):
    state = AsyncBatchRunState(
        run_id="demo-async",
        created_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        output_dir=str(tmp_path),
        concurrency=3,
        total_queries=1,
        completed_queries=0,
        pending_queries=1,
        items=[AsyncBatchItemState(index=0, query="first")],
    )

    state_path = write_async_batch_state(state)
    restored = load_async_batch_state(state_path)

    assert restored.run_id == "demo-async"
    assert restored.state_path == str(state_path)
    assert restored.items[0].query == "first"


def test_run_batch_evaluation_async_resumes_pending_items(monkeypatch, evaluator_workflow, dummy_config, tmp_path):
    calls: list[str] = []

    def fake_runner(session, config, user_text, credentials=None, send_event=None):
        calls.append(user_text)
        if user_text == "second" and calls.count("second") == 1:
            raise RuntimeError("transient")
        history = session.get("results_history", [])
        bundle_id = len(history) + 1
        history.append(
            {
                "id": bundle_id,
                "mode": "new_search",
                "user_query": user_text,
                "reply": f"answer: {user_text}",
                "api_result_full": {"ok": True, "status_code": 200, "data": {"total": 2, "results": [{"id": 1}]}},
            }
        )
        session["results_history"] = history
        return {"reply": f"answer: {user_text}", "bundle_id": bundle_id, "files": []}

    monkeypatch.setattr("chat_nextseek.evaluator.reports.run_query", fake_runner)
    monkeypatch.setattr("chat_nextseek.orchestrator.run_query", fake_runner)

    output_dir = tmp_path / "async-out"
    state, state_path, first_report = asyncio.run(
        run_batch_evaluation_async(
            ["first", "second"],
            output_dir=output_dir,
            config=dummy_config,
            workflow=evaluator_workflow,
            concurrency=2,
            input_path=tmp_path / "queries.txt",
            mode="gcp",
        )
    )
    assert first_report.success is False
    assert first_report.queries_exceptions == 1
    assert first_report.infra_failures == 0
    assert first_report.run_status == "crashed"
    assert calls.count("first") == 1
    assert calls.count("second") == 1

    resumed_state, resumed_state_path, resumed_report = asyncio.run(
        run_batch_evaluation_async(
            output_dir=output_dir,
            config=dummy_config,
            workflow=evaluator_workflow,
            concurrency=2,
            resume_path=state_path,
            mode="gcp",
        )
    )

    assert resumed_state_path == state_path
    assert resumed_report.success is True
    assert resumed_report.infra_failures == 0
    assert calls.count("first") == 1
    assert calls.count("second") == 2
    assert resumed_state.completed_queries == 2
    assert resumed_state.pending_queries == 0


def test_finalize_async_batch_state_records_final_artifacts(tmp_path):
    state = AsyncBatchRunState(
        run_id="demo-async",
        created_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        updated_at=datetime(2026, 4, 23, tzinfo=timezone.utc),
        output_dir=str(tmp_path),
        concurrency=2,
        total_queries=1,
        completed_queries=1,
        pending_queries=0,
        items=[
            AsyncBatchItemState(
                index=0,
                query="first",
                status="completed",
                report=EvalReport(query="first", status="completed"),
            )
        ],
    )
    state_path = write_async_batch_state(state)
    report = build_batch_report([EvalReport(query="first", status="completed")], run_id="demo-async")
    report_path = tmp_path / "eval-demo-async.json"
    html_path = tmp_path / "eval-demo-async.html"
    report_path.write_text("{}", encoding="utf-8")
    html_path.write_text("<html></html>", encoding="utf-8")

    finalize_async_batch_state(
        state,
        report=report,
        report_path=report_path,
        html_path=html_path,
        state_path=state_path,
    )
    restored = load_async_batch_state(state_path)

    assert restored.final_report_path == str(report_path)
    assert restored.final_html_path == str(html_path)
    assert restored.success is True

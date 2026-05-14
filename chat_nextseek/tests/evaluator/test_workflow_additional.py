from __future__ import annotations

from pathlib import Path
from uuid import UUID

import pytest

from chat_nextseek.evaluator.models import EvaluatorRunRecord
from chat_nextseek.evaluator.workflow import EvaluatorWorkflow, FileEvaluatorRunRepository, parse_source_reference


class ExplodingBamlClient:
    def evaluate(self, response, config=None):
        raise RuntimeError("boom")


class AlwaysRetryBamlClient:
    def evaluate(self, response, config=None):
        return (
            {"correctness": "PARTIAL", "completeness": "PARTIAL", "routing_quality": "PASS", "reasoning": "retry"},
            {"verdict": "RETRY", "should_retry": True, "retry_query": "retry plan", "suggestions": None, "reasoning": "retry"},
        )


def test_repository_writes_index_and_filters_by_mode_and_bundle(evaluator_store, fake_session):
    workflow = EvaluatorWorkflow(repository=evaluator_store, baml_client=ExplodingBamlClient())
    search_result = workflow.evaluate_bundle(fake_session, fake_session["results_history"][0])
    plan_bundle = {
        "id": 7,
        "mode": "plan",
        "user_query": "plan retry",
        "plan": {"steps": [{"step_id": 1}]},
        "step_results": {"1": {"ok": True}},
    }
    workflow.evaluate_bundle(fake_session, plan_bundle)

    index_path = Path(evaluator_store._index_path())
    assert index_path.exists()
    assert evaluator_store.list_runs({"mode": "plan", "has_bundle": True, "limit": 10})
    assert evaluator_store.read_run(UUID(search_result["run_record"]["task_id"])).status == "failed"


def test_parse_source_reference_rejects_invalid_values():
    with pytest.raises(ValueError):
        parse_source_reference("bundle:missing")
    with pytest.raises(ValueError):
        parse_source_reference("task:")
    with pytest.raises(ValueError):
        parse_source_reference("other:1")


def test_repository_property_and_task_evaluation(evaluator_store, fake_session):
    workflow = EvaluatorWorkflow(repository=evaluator_store, baml_client=ExplodingBamlClient())
    assert workflow.repository is evaluator_store

    record = EvaluatorRunRecord(
        task_id=UUID("00000000-0000-0000-0000-000000000127"),
        session_id=fake_session["session_id"],
        mode="standard",
        status="completed",
        query="hello",
    )
    result = workflow.evaluate_task(record, session=fake_session)
    assert result["source"] == "task"
    assert result["run_record"]["status"] == "completed"


def test_get_retry_context_for_bundle_errors_when_missing(evaluator_workflow, fake_session):
    with pytest.raises(ValueError):
        evaluator_workflow.get_retry_context_for_bundle(
            session_id=fake_session["session_id"],
            bundle_id=404,
            session=fake_session,
        )


def test_evaluate_source_uses_session_lookup(monkeypatch, evaluator_workflow, fake_session):
    source = f"bundle:{fake_session['session_id']}:{fake_session['results_history'][0]['id']}"
    result = evaluator_workflow.evaluate_source(
        source,
        session_lookup=lambda session_id: fake_session,
    )
    assert result["source"] == "bundle"


def test_execute_retry_handles_no_retry_and_missing_config(evaluator_workflow, fake_session):
    normalized = evaluator_workflow.get_retry_context_for_bundle(
        session_id=fake_session["session_id"],
        bundle_id=fake_session["results_history"][0]["id"],
        session=fake_session,
    )
    assert evaluator_workflow._execute_retry(
        normalized=normalized,
        decision_payload={"should_retry": False, "retry_query": None},
        config=None,
    ) is None

    with pytest.raises(ValueError):
        evaluator_workflow._execute_retry(
            normalized=normalized,
            decision_payload={"should_retry": True, "retry_query": "retry me"},
            config=None,
        )


def test_execute_retry_uses_plan_runner(monkeypatch, evaluator_store, fake_plan_bundle, fake_session, dummy_config):
    plan_session = dict(fake_session)
    plan_session["results_history"] = [fake_plan_bundle]
    workflow = EvaluatorWorkflow(repository=evaluator_store, baml_client=AlwaysRetryBamlClient())

    def fake_run_query_plan(session, config, user_text, credentials=None, send_event=None):
        session["results_history"] = [{"id": 2, "user_query": user_text, "mode": "plan"}]
        return {"reply": "planned retry", "bundle_id": 2, "files": []}

    monkeypatch.setattr("chat_nextseek.orchestrator.run_query_plan", fake_run_query_plan)

    result = workflow.evaluate_source(
        f"bundle:{plan_session['session_id']}:{fake_plan_bundle['id']}",
        session=plan_session,
        execute_retry=True,
        config=dummy_config,
    )
    assert result["retry_executed"] is True
    assert result["retry_result"]["result"]["reply"] == "planned retry"

from __future__ import annotations

from types import SimpleNamespace


def test_bundle_context_lookup(evaluator_workflow, fake_session):
    bundle = fake_session["results_history"][0]
    payload = evaluator_workflow.get_retry_context_for_bundle(
        session_id=fake_session["session_id"],
        bundle_id=bundle["id"],
        session=fake_session,
    )
    assert payload.lookup.source == "bundle"
    assert payload.lookup.bundle_id == bundle["id"]


def test_list_runs_filters_status(evaluator_workflow, fake_session):
    bundle = fake_session["results_history"][0]
    evaluator_workflow.evaluate_bundle(fake_session, bundle)

    results = evaluator_workflow.list_runs(status="completed", limit=10)
    assert results
    assert all(item["status"] == "completed" for item in results)


def test_retry_executes_synchronously(monkeypatch, evaluator_workflow, fake_empty_search_session, dummy_config):
    def fake_run_query(session, config, user_text, credentials=None, send_event=None):
        session["results_history"] = [{"id": 1, "user_query": user_text, "mode": "new_search"}]
        return {"reply": f"retried: {user_text}", "bundle_id": 1, "files": []}

    monkeypatch.setattr("chat_nextseek.orchestrator.run_query", fake_run_query)

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

    assert result["evaluation"]["retry_decision"]["should_retry"] is True
    assert result["run_record"]["status"] in {"completed", "failed"}
    assert result["retry_executed"] is True
    assert result["retry_result"]["result"]["reply"].startswith("retried:")


def test_task_source_reuses_persisted_run(evaluator_workflow, fake_session):
    created = evaluator_workflow.evaluate_bundle(fake_session, fake_session["results_history"][0])
    task_id = created["run_record"]["task_id"]

    result = evaluator_workflow.evaluate_source(f"task:{task_id}")
    assert result["evaluation"]["lookup"]["source"] == "task"

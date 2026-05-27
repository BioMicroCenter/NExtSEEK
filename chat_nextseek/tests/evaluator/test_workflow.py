from __future__ import annotations

from chat_nextseek.evaluator.workflow import parse_source_reference


def test_evaluate_bundle_persists_result(evaluator_workflow, evaluator_store, fake_session):
    bundle = fake_session["results_history"][0]
    result = evaluator_workflow.evaluate_bundle(fake_session, bundle)

    assert result["evaluation"]["retry_decision"]["should_retry"] is False
    assert result["run_record"]["status"] == "completed"

    persisted = evaluator_store.read_run(result["run_record"]["task_id"])
    assert persisted.source_bundle_id == bundle["id"]
    assert persisted.evaluation_payload is not None


def test_evaluate_bundle_persists_reply_from_normalized_bundle(evaluator_workflow, evaluator_store, fake_session):
    bundle = dict(fake_session["results_history"][0])
    bundle["reply"] = "Found matching samples."

    result = evaluator_workflow.evaluate_bundle(fake_session, bundle)

    persisted = evaluator_store.read_run(result["run_record"]["task_id"])
    assert persisted.reply == "Found matching samples."


def test_evaluate_task_uses_task_lookup(evaluator_workflow, evaluator_store, fake_session):
    bundle = fake_session["results_history"][0]
    created = evaluator_workflow.evaluate_bundle(fake_session, bundle)
    task_id = created["run_record"]["task_id"]
    task_result = evaluator_workflow.evaluate_source(f"task:{task_id}")

    assert task_result["source"] == "task"
    assert task_result["evaluation"]["lookup"]["source"] == "task"
    assert evaluator_store.read_run(task_id).task_id


def test_unsupported_path_is_non_retryable(evaluator_workflow, fake_session):
    bundle = {"id": 99, "user_query": "hello"}
    result = evaluator_workflow.evaluate_bundle(fake_session, bundle)

    assert result["evaluation"]["routing"]["path_mode"] == "unsupported"
    assert result["evaluation"]["retry_decision"]["should_retry"] is False


def test_parse_source_reference_variants():
    kind, payload = parse_source_reference("bundle:00000000-0000-0000-0000-000000000001:5")
    assert kind == "bundle"
    assert payload["bundle_id"] == 5

    kind, payload = parse_source_reference("task:00000000-0000-0000-0000-000000000123")
    assert kind == "task"
    assert str(payload["task_id"]) == "00000000-0000-0000-0000-000000000123"

from __future__ import annotations

import json

import pytest
import pytest_asyncio

from chat_nextseek.evaluator.demo.server import NativeDemoEvaluator, StageEvent, create_app


def _demo_run_query(session, config, user_text, credentials=None, send_event=None):
    total = 0 if "NDMA" in user_text else 2
    history = session.get("results_history", [])
    bundle_id = len(history) + 1
    history.append(
        {
            "id": bundle_id,
            "mode": "new_search",
            "user_query": user_text,
            "api_result_full": {"ok": True, "status_code": 200, "data": {"total": total, "results": []}},
        }
    )
    session["results_history"] = history
    return {"reply": f"answer: {user_text}", "bundle_id": bundle_id, "files": []}


@pytest_asyncio.fixture
async def demo_client(aiohttp_client, evaluator_workflow, dummy_config, monkeypatch, tmp_path):
    html_file = tmp_path / "demo.html"
    html_file.write_text("<!DOCTYPE html><html><body>Native demo</body></html>", encoding="utf-8")
    monkeypatch.setattr("chat_nextseek.orchestrator.run_query", _demo_run_query)
    monkeypatch.setattr("chat_nextseek.orchestrator.run_query_plan", _demo_run_query)
    evaluator = NativeDemoEvaluator(
        config=dummy_config,
        workflow=evaluator_workflow,
        query_runner=_demo_run_query,
        plan_runner=_demo_run_query,
    )
    app = create_app(html_path=html_file, evaluator=evaluator)
    return await aiohttp_client(app)


@pytest.mark.asyncio
async def test_demo_server_health(demo_client):
    resp = await demo_client.get("/api/health")
    assert resp.status == 200
    payload = await resp.json()
    assert payload["status"] == "ok"
    assert payload["mode"] == "native"


@pytest.mark.asyncio
async def test_demo_server_test_cases(demo_client):
    resp = await demo_client.get("/api/test-cases")
    assert resp.status == 200
    payload = await resp.json()
    names = [item["name"] for item in payload["categories"]]
    assert "new_search" in names
    assert "negative_controls" in names


@pytest.mark.asyncio
async def test_demo_server_sse_evaluate(demo_client):
    resp = await demo_client.post("/api/evaluate", json={"test_case": "mice_ndma"})
    assert resp.status == 200
    assert "text/event-stream" in resp.headers.get("Content-Type", "")

    events = []
    async for line in resp.content:
        text = line.decode().strip()
        if text.startswith("data:"):
            event = json.loads(text[5:].strip())
            events.append(event)
            if event["stage"] == "result":
                break

    stages = [event["stage"] for event in events]
    assert "submit" in stages
    assert "fetch_context" in stages
    assert "judge" in stages
    assert "decide_retry" in stages
    assert "retry" in stages
    assert "result" in stages


@pytest.mark.asyncio
async def test_demo_server_missing_html_returns_404(aiohttp_client, evaluator_workflow, dummy_config, tmp_path):
    from pytest import MonkeyPatch

    monkeypatch = MonkeyPatch()
    monkeypatch.setattr("chat_nextseek.orchestrator.run_query", _demo_run_query)
    monkeypatch.setattr("chat_nextseek.orchestrator.run_query_plan", _demo_run_query)
    evaluator = NativeDemoEvaluator(
        config=dummy_config,
        workflow=evaluator_workflow,
        query_runner=_demo_run_query,
        plan_runner=_demo_run_query,
    )
    app = create_app(html_path=tmp_path / "missing.html", evaluator=evaluator)
    client = await aiohttp_client(app)
    resp = await client.get("/")
    assert resp.status == 404
    payload = await resp.json()
    assert "error" in payload
    monkeypatch.undo()


@pytest.mark.asyncio
async def test_demo_server_missing_test_case_returns_400(demo_client):
    resp = await demo_client.post("/api/evaluate", json={})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_demo_server_invalid_json_returns_400(demo_client):
    resp = await demo_client.post("/api/evaluate", data="not-json", headers={"Content-Type": "application/json"})
    assert resp.status == 400


@pytest.mark.asyncio
async def test_demo_server_unknown_case_emits_error(demo_client):
    resp = await demo_client.post("/api/evaluate", json={"test_case": "missing"})
    events = []
    async for line in resp.content:
        text = line.decode().strip()
        if text.startswith("data:"):
            events.append(json.loads(text[5:].strip()))
            break
    assert events[0]["stage"] == "error"


@pytest.mark.asyncio
async def test_demo_server_retry_none_emits_error(aiohttp_client, evaluator_workflow, dummy_config, monkeypatch, tmp_path):
    html_file = tmp_path / "demo.html"
    html_file.write_text("<!DOCTYPE html><html><body>Native demo</body></html>", encoding="utf-8")
    monkeypatch.setattr("chat_nextseek.orchestrator.run_query", _demo_run_query)

    class NoRetryWorkflow:
        def get_retry_context_for_bundle(self, **kwargs):
            return evaluator_workflow.get_retry_context_for_bundle(**kwargs)

        def evaluate_context(self, normalized, *, source_label="bundle"):
            return evaluator_workflow.evaluate_context(normalized, source_label=source_label)

        def execute_retry_for_context(self, **kwargs):
            return None

    evaluator = NativeDemoEvaluator(
        config=dummy_config,
        workflow=NoRetryWorkflow(),
        query_runner=_demo_run_query,
        plan_runner=_demo_run_query,
    )
    app = create_app(html_path=html_file, evaluator=evaluator)
    client = await aiohttp_client(app)
    resp = await client.post("/api/evaluate", json={"test_case": "mice_ndma"})
    events = []
    async for line in resp.content:
        text = line.decode().strip()
        if text.startswith("data:"):
            events.append(json.loads(text[5:].strip()))
    assert events[-1]["stage"] == "error"


@pytest.mark.asyncio
async def test_demo_server_refine_case_runs_with_seed_context(demo_client):
    resp = await demo_client.post("/api/evaluate", json={"test_case": "tumor_dfci4"})
    events = []
    async for line in resp.content:
        text = line.decode().strip()
        if text.startswith("data:"):
            event = json.loads(text[5:].strip())
            events.append(event)
            if event["stage"] == "result":
                break
    assert events[-1]["stage"] == "result"


@pytest.mark.asyncio
async def test_run_demo_server_returns_zero(monkeypatch):
    from chat_nextseek.evaluator.demo import server as demo_server

    called = {}

    def fake_run_app(app, port):
        called["port"] = port

    monkeypatch.setattr(demo_server.web, "run_app", fake_run_app)
    assert demo_server.run_demo_server(config=object(), port=8099) == 0
    assert called["port"] == 8099


@pytest.mark.asyncio
async def test_stage_event_to_dict():
    event = StageEvent(stage="submit", status="completed", payload={"bundle_id": 1})
    assert event.to_dict() == {
        "stage": "submit",
        "status": "completed",
        "payload": {"bundle_id": 1},
    }

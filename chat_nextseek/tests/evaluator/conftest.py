from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from uuid import UUID

import pytest

from pydantic import BaseModel

from chat_nextseek.evaluator.workflow import EvaluatorWorkflow, FileEvaluatorRunRepository


class FakeJudgment(BaseModel):
    correctness: str
    completeness: str
    routing_quality: str
    reasoning: str


class FakeDecision(BaseModel):
    verdict: str
    should_retry: bool
    retry_query: str | None
    suggestions: list[dict[str, str]] | None
    reasoning: str


class FakeBamlClient:
    def evaluate(self, response, config=None):
        rows = response.retry_context.retry_signals.rows_returned or 0
        should_retry = response.retry_context.retryable and rows == 0
        retry_query = None
        if should_retry and response.run.query:
            retry_query = f"{response.run.query} with more context"
        return (
            FakeJudgment(
                correctness="PASS" if not should_retry else "PARTIAL",
                completeness="PASS" if not should_retry else "PARTIAL",
                routing_quality="PASS",
                reasoning="fixture judgment",
            ),
            FakeDecision(
                verdict="RETRY" if should_retry else "PASS",
                should_retry=should_retry,
                retry_query=retry_query,
                suggestions=None,
                reasoning="fixture decision",
            ),
        )


@pytest.fixture
def fake_search_bundle():
    return {
        "id": 1,
        "mode": "new_search",
        "timestamp": "2026-04-17T00:00:00Z",
        "user_query": "Find me mice treated with NDMA",
        "parser_plan": {"mode": "new_search", "target_endpoint": "/samples/search"},
        "api_result_full": {
            "ok": True,
            "status_code": 200,
            "data": {"total": 3, "results": [{"uid": "A"}, {"uid": "B"}, {"uid": "C"}]},
        },
        "files": [{"key": "api_result", "path": "/tmp/api_result_bundle_1.json"}],
    }


@pytest.fixture
def fake_empty_search_bundle(fake_search_bundle):
    bundle = dict(fake_search_bundle)
    bundle["id"] = 9
    bundle["api_result_full"] = {"ok": True, "status_code": 200, "data": {"total": 0, "results": []}}
    bundle["files"] = []
    return bundle


@pytest.fixture
def fake_graph_bundle():
    return {
        "id": 2,
        "mode": "graph_query",
        "timestamp": "2026-04-17T00:01:00Z",
        "user_query": "Show me sequencing data in the GBM study",
        "graph_result": {"ok": True, "count": 4, "data": [{"id": 1}, {"id": 2}]},
    }


@pytest.fixture
def fake_plan_bundle():
    return {
        "id": 3,
        "mode": "plan",
        "timestamp": "2026-04-17T00:02:00Z",
        "user_query": "Plan a GBM search with retries",
        "plan": {
            "steps": [
                {"step_id": 1, "tool": "new_search"},
                {"step_id": 2, "tool": "graph_query"},
            ]
        },
        "step_results": {
            "1": {"ok": True, "tool": "new_search", "output": {"count": 2}},
            "2": {"ok": False, "tool": "graph_query", "error": "bad step"},
        },
        "plan_debug_path": "/tmp/plan_debug_3.json",
    }


@pytest.fixture
def fake_session(fake_search_bundle, fake_graph_bundle):
    return {
        "session_id": UUID("00000000-0000-0000-0000-000000000001"),
        "user_id": 7,
        "results_history": [fake_search_bundle, fake_graph_bundle],
        "last_debug": {},
    }


@pytest.fixture
def fake_empty_search_session(fake_empty_search_bundle):
    return {
        "session_id": UUID("00000000-0000-0000-0000-000000000010"),
        "user_id": 11,
        "results_history": [fake_empty_search_bundle],
        "last_debug": {},
    }


@pytest.fixture
def evaluator_store(tmp_path):
    return FileEvaluatorRunRepository(state_root=tmp_path / "evaluator-state")


@pytest.fixture
def evaluator_workflow(evaluator_store):
    return EvaluatorWorkflow(repository=evaluator_store, baml_client=FakeBamlClient())


@pytest.fixture
def dummy_config():
    return SimpleNamespace(API_USER="user", API_PASS="pass")


@dataclass
class FakeBundleSource:
    session_id: UUID
    bundle_id: int


@pytest.fixture
def fake_bundle_source(fake_session):
    bundle = fake_session["results_history"][0]
    return FakeBundleSource(session_id=fake_session["session_id"], bundle_id=bundle["id"])

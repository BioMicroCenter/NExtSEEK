from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from uuid import uuid4

from aiohttp import web
from aiohttp_sse import sse_response

from chat_nextseek.config import ChatConfig
from chat_nextseek.evaluator.reports import (
    InMemorySessionState,
    build_batch_report,
    build_dashboard_payload,
    result_to_report,
)
from chat_nextseek.evaluator.workflow import EvaluatorWorkflow
from chat_nextseek.orchestrator import run_query, run_query_plan


TEST_CASES: dict[str, list[dict[str, Any]]] = {
    "new_search": [
        {
            "key": "mice_ndma",
            "prompt": "Find me mice treated with NDMA",
            "description": "Good retry target",
            "execution_mode": "standard",
        },
        {
            "key": "monkeys_exist",
            "prompt": "What monkeys exist in the database?",
            "description": "Simple search",
            "execution_mode": "standard",
        },
        {
            "key": "tumor_patient_4",
            "prompt": "Show me tumor data for patient 4.",
            "description": "Patient-specific search",
            "execution_mode": "standard",
        },
        {
            "key": "omero_fibrin_images",
            "prompt": "What fibrin images exist?",
            "description": "Image search",
            "execution_mode": "standard",
        },
    ],
    "refine_last_search": [
        {
            "key": "tumor_dfci4",
            "prompt": "Try that search again but instead with DFCI4.",
            "description": "Refinement search",
            "execution_mode": "standard",
            "setup_queries": ["Show me tumor data for patient 4."],
        },
    ],
    "graph_query": [
        {
            "key": "sample_tree",
            "prompt": "Show me all samples derived from CEL-250319WHI-1-PUB?",
            "description": "Graph traversal",
            "execution_mode": "standard",
        },
        {
            "key": "gbm_study_seq",
            "prompt": "What sequencing data exists in the GBM Study?",
            "description": "Study-level query",
            "execution_mode": "standard",
        },
    ],
    "reporter": [
        {
            "key": "reporter_impact",
            "prompt": "How many samples were uploaded for impact from 2023 to 2025?",
            "description": "Aggregate query",
            "execution_mode": "standard",
        },
        {
            "key": "reporter_language",
            "prompt": "How many samples were uploaded last month?",
            "description": "Natural language date",
            "execution_mode": "standard",
        },
    ],
    "negative_controls": [
        {
            "key": "nonexistent_sampletype",
            "prompt": "Find me all zebrafish samples in the database",
            "description": "Expected empty result",
            "execution_mode": "standard",
        },
    ],
}

_ALL_CASES = {
    str(case["key"]): case
    for cases in TEST_CASES.values()
    for case in cases
}

_SAMPLE_DATA = [
    {
        "run_id": "demo-sample-001",
        "test_case": "mice_ndma",
        "prompt": "Find me mice treated with NDMA",
        "correctness": "PASS",
        "completeness": "PARTIAL",
        "routing_quality": "PASS",
        "retried": True,
    },
    {
        "run_id": "demo-sample-002",
        "test_case": "monkeys_exist",
        "prompt": "What monkeys exist in the database?",
        "correctness": "PASS",
        "completeness": "PASS",
        "routing_quality": "PASS",
        "retried": False,
    },
]

DEFAULT_HTML_PATH = Path(__file__).with_name("index.html")


@dataclass
class StageEvent:
    stage: str
    status: str
    payload: dict[str, Any] | None = field(default=None)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@runtime_checkable
class DemoEvaluatorProtocol(Protocol):
    async def run_evaluation(self, test_case: str) -> AsyncGenerator[StageEvent, None]: ...


class NativeDemoEvaluator:
    def __init__(
        self,
        *,
        config: ChatConfig,
        workflow: EvaluatorWorkflow | None = None,
        credentials: dict[str, str] | None = None,
        query_runner=None,
        plan_runner=None,
    ) -> None:
        self._config = config
        self._workflow = workflow or EvaluatorWorkflow(config=config)
        self._credentials = credentials
        self._query_runner = query_runner or run_query
        self._plan_runner = plan_runner or run_query_plan

    def _new_session(self) -> InMemorySessionState:
        return InMemorySessionState(
            {
                "session_id": str(uuid4()),
                "user_id": 0,
                "results_history": [],
                "last_debug": {},
            }
        )

    def _runner_for_mode(self, execution_mode: str):
        return self._plan_runner if execution_mode == "plan" else self._query_runner

    def _run_local_query(self, session: InMemorySessionState, prompt: str, execution_mode: str) -> dict[str, Any]:
        runner = self._runner_for_mode(execution_mode)
        return runner(session, self._config, prompt, credentials=self._credentials)

    async def run_evaluation(self, test_case: str) -> AsyncGenerator[StageEvent, None]:
        case = _ALL_CASES.get(test_case)
        if case is None:
            yield StageEvent(
                stage="error",
                status="error",
                payload={"stage": "error", "status": "error", "message": f"Unknown test case: {test_case}"},
            )
            return

        session = self._new_session()
        try:
            for setup_query in case.get("setup_queries", []):
                self._run_local_query(session, setup_query, case.get("execution_mode", "standard"))

            yield StageEvent(stage="submit", status="started")
            started = time.perf_counter()
            query_result = self._run_local_query(session, case["prompt"], case.get("execution_mode", "standard"))
            history = session.get("results_history") or []
            if not history:
                raise ValueError("Native demo query completed without producing a bundle.")
            bundle_id = query_result.get("bundle_id") or history[-1]["id"]
            source = f"bundle:{session['session_id']}:{bundle_id}"
            yield StageEvent(
                stage="submit",
                status="completed",
                payload={"bundle_id": bundle_id, "session_id": str(session["session_id"]), "source": source},
            )

            yield StageEvent(stage="poll", status="started")
            elapsed = round(time.perf_counter() - started, 3)
            yield StageEvent(
                stage="poll",
                status="completed",
                payload={"status": "completed", "elapsed_seconds": elapsed},
            )

            yield StageEvent(stage="fetch_context", status="started")
            normalized = self._workflow.get_retry_context_for_bundle(
                session_id=session["session_id"],
                bundle_id=bundle_id,
                session=session,
            )
            yield StageEvent(
                stage="fetch_context",
                status="completed",
                payload={
                    "path_mode": normalized.routing.path_mode,
                    "execution_mode": normalized.routing.execution_mode,
                    "retryable": normalized.retry_context.retryable,
                },
            )

            yield StageEvent(stage="judge", status="started")
            evaluation_result = self._workflow.evaluate_context(normalized, source_label="bundle")
            judgment = evaluation_result["evaluation"]["judgment"]
            yield StageEvent(stage="judge", status="completed", payload=judgment)

            yield StageEvent(stage="decide_retry", status="started")
            decision = evaluation_result["evaluation"]["retry_decision"]
            yield StageEvent(stage="decide_retry", status="completed", payload=decision)

            retry_result = None
            if decision.get("should_retry"):
                yield StageEvent(stage="retry", status="started")
                retry_result = self._workflow.execute_retry_for_context(
                    normalized=normalized,
                    decision_payload=decision,
                    credentials=self._credentials,
                    config=self._config,
                )
                if retry_result is None:
                    raise ValueError("Retry decision requested execution, but no retry result was produced.")
                retry_evaluation = retry_result.get("evaluation") or {}
                retry_response = retry_result.get("retry_response") or {}
                yield StageEvent(
                    stage="retry",
                    status="completed",
                    payload={
                        "retry_task_id": retry_response.get("task_id"),
                        "retry_query": decision.get("retry_query"),
                        "retry_judgment": retry_evaluation.get("judgment"),
                    },
                )

            combined_result = dict(evaluation_result)
            combined_result["retry_executed"] = retry_result is not None
            combined_result["retry_result"] = retry_result
            report = build_batch_report(
                [result_to_report(str(case["prompt"]), combined_result)],
                run_id=f"demo-{uuid4().hex[:8]}",
            )

            yield StageEvent(
                stage="result",
                status="completed",
                payload=build_dashboard_payload(report),
            )
        except Exception as exc:
            yield StageEvent(
                stage="error",
                status="error",
                payload={"stage": "error", "status": "error", "message": str(exc)},
            )


async def health_handler(request: web.Request) -> web.Response:
    evaluator = request.app["evaluator"]
    mode = "native" if isinstance(evaluator, NativeDemoEvaluator) else "custom"
    return web.json_response({"status": "ok", "mode": mode})


async def index_handler(request: web.Request) -> web.Response:
    html_path: Path | None = request.app.get("html_path")
    if html_path is None or not html_path.exists():
        return web.json_response({"error": "HTML file not found"}, status=404)
    return web.Response(text=html_path.read_text(encoding="utf-8"), content_type="text/html")


async def sample_data_handler(request: web.Request) -> web.Response:
    return web.json_response(_SAMPLE_DATA)


async def test_cases_handler(request: web.Request) -> web.Response:
    categories = []
    for name, cases in TEST_CASES.items():
        categories.append(
            {
                "name": name,
                "cases": [
                    {
                        "key": case["key"],
                        "prompt": case["prompt"],
                        "description": case.get("description", ""),
                    }
                    for case in cases
                ],
            }
        )
    return web.json_response({"categories": categories})


async def evaluate_handler(request: web.Request) -> web.StreamResponse | web.Response:
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "Request body must be valid JSON with 'test_case' field"}, status=400)

    test_case = body.get("test_case")
    if not test_case:
        return web.json_response({"error": "Missing 'test_case' field"}, status=400)

    evaluator: DemoEvaluatorProtocol = request.app["evaluator"]
    async with sse_response(request) as response:
        async for event in evaluator.run_evaluation(str(test_case)):
            await response.send(json.dumps(event.to_dict()))
        return response


def create_app(
    *,
    html_path: Path | None = None,
    config: ChatConfig | None = None,
    workflow: EvaluatorWorkflow | None = None,
    evaluator: DemoEvaluatorProtocol | None = None,
) -> web.Application:
    app = web.Application()
    app["html_path"] = html_path or DEFAULT_HTML_PATH
    app["evaluator"] = evaluator or NativeDemoEvaluator(
        config=config or ChatConfig(),
        workflow=workflow,
    )
    app.router.add_get("/api/health", health_handler)
    app.router.add_get("/", index_handler)
    app.router.add_get("/api/sample-data", sample_data_handler)
    app.router.add_get("/api/test-cases", test_cases_handler)
    app.router.add_post("/api/evaluate", evaluate_handler)
    return app


def run_demo_server(
    *,
    config: ChatConfig | None = None,
    html_path: Path | None = None,
    workflow: EvaluatorWorkflow | None = None,
    port: int = 8080,
) -> int:
    app = create_app(html_path=html_path, config=config, workflow=workflow)
    web.run_app(app, port=port)
    return 0

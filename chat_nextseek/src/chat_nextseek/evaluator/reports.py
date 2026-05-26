"""Evaluator report schemas and precedence-based classification.

Defines ``EvalReport`` / ``EvalBatchReport``, batch loaders, and
``_classify_report()`` for DD-43 bucket assignment.
Use this module when turning many evaluator results into resumable artifacts.
Invariant: batch counts and ``run_status`` derive from classifier output,
not from overlapping inline tallies.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from chat_nextseek.orchestrator import run_query, run_query_plan

from .workflow import EvaluatorWorkflow, InMemorySessionState


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_uuid(value: str | UUID | None) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    return UUID(str(value))


def _score_rank(value: str | None) -> int:
    order = {"FAIL": 0, "PARTIAL": 1, "PASS": 2}
    return order.get(value or "", -1)


def _score_delta(original: str | None, retry: str | None) -> str:
    original_rank = _score_rank(original)
    retry_rank = _score_rank(retry)
    if original_rank == retry_rank:
        return "same"
    if retry_rank > original_rank:
        return "improved"
    return "regressed"


def _score_bucket(value: str | None) -> str:
    normalized = (value or "").strip().upper()
    if normalized == "PASS":
        return "pass_count"
    if normalized == "PARTIAL":
        return "partial_count"
    return "fail_count"


class SuggestionSummary(BaseModel):
    category: str
    description: str

    model_config = ConfigDict(extra="forbid")


class JudgmentSummary(BaseModel):
    correctness: str
    completeness: str
    routing_quality: str
    reasoning: str

    model_config = ConfigDict(extra="forbid")


class RetryDecisionSummary(BaseModel):
    verdict: str
    should_retry: bool
    retry_query: str | None = None
    suggestions: list[SuggestionSummary] | None = None
    reasoning: str

    model_config = ConfigDict(extra="forbid")


class RetryOutcome(BaseModel):
    retry_task_id: UUID
    retry_query: str
    retry_judgment: JudgmentSummary

    model_config = ConfigDict(extra="forbid")


class EvalReport(BaseModel):
    query: str
    status: str = Field(..., description="completed | failed | error | timeout | exception")
    task_id: UUID | None = None
    error: str | None = None
    execution_mode: str | None = None
    path_mode: str | None = None
    judgment: JudgmentSummary | None = None
    retry_decision: RetryDecisionSummary | None = None
    retry_result: RetryOutcome | None = None

    model_config = ConfigDict(extra="forbid")


class EvalBatchReport(BaseModel):
    run_id: str
    created_at: datetime
    queries_total: int
    queries_passed: int
    queries_retried: int
    queries_failed: int
    queries_exceptions: int = 0
    queries_unsupported: int = 0
    queries_with_errors: int = 0
    infra_failures: int = 0
    success: bool = False
    run_status: str = "completed_with_failures"
    results: list[EvalReport]

    model_config = ConfigDict(extra="forbid")


class AsyncBatchItemState(BaseModel):
    index: int
    query: str
    status: str = "pending"
    attempts: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    report: EvalReport | None = None
    error: str | None = None

    model_config = ConfigDict(extra="forbid")


class AsyncBatchRunState(BaseModel):
    run_id: str
    created_at: datetime
    updated_at: datetime
    input_path: str | None = None
    output_dir: str
    planner: bool = False
    mode: str | None = None
    concurrency: int = 5
    state_path: str | None = None
    status: str = "pending"
    total_queries: int = 0
    completed_queries: int = 0
    pending_queries: int = 0
    final_report_path: str | None = None
    final_html_path: str | None = None
    success: bool | None = None
    items: list[AsyncBatchItemState]

    model_config = ConfigDict(extra="forbid")


def _build_judgment(payload: Mapping[str, object] | None) -> JudgmentSummary | None:
    if not payload:
        return None
    return JudgmentSummary.model_validate(payload)


def _build_retry_decision(payload: Mapping[str, object] | None) -> RetryDecisionSummary | None:
    if not payload:
        return None
    return RetryDecisionSummary.model_validate(payload)


def _build_retry_outcome(
    payload: Mapping[str, object] | None,
    *,
    fallback_retry_query: str | None = None,
) -> RetryOutcome | None:
    if not payload:
        return None
    retry_response = payload.get("retry_response") or {}
    evaluation = payload.get("evaluation") or {}
    judgment = _build_judgment(evaluation.get("judgment"))
    retry_task_id = _coerce_uuid(retry_response.get("task_id"))
    retry_query = (
        retry_response.get("query")
        or evaluation.get("retry_decision", {}).get("retry_query")
        or fallback_retry_query
    )
    if retry_task_id is None or judgment is None or not retry_query:
        return None
    return RetryOutcome(
        retry_task_id=retry_task_id,
        retry_query=str(retry_query),
        retry_judgment=judgment,
    )


def result_to_report(query: str, result: Mapping[str, object]) -> EvalReport:
    run_record = result.get("run_record") or {}
    evaluation = result.get("evaluation") or {}
    retry_query = None
    if evaluation.get("retry_decision"):
        retry_query = evaluation["retry_decision"].get("retry_query")
    routing = evaluation.get("routing") or {}
    return EvalReport(
        query=query,
        status=str(run_record.get("status") or "completed"),
        task_id=_coerce_uuid(run_record.get("task_id")),
        error=run_record.get("error"),
        execution_mode=routing.get("execution_mode"),
        path_mode=routing.get("path_mode"),
        judgment=_build_judgment(evaluation.get("judgment")),
        retry_decision=_build_retry_decision(evaluation.get("retry_decision")),
        retry_result=_build_retry_outcome(
            result.get("retry_result"),
            fallback_retry_query=retry_query,
        ),
    )


_INFRA_ERROR_NEEDLES: tuple[str, ...] = (
    "neo4j",
    "bolt://",
    "unreachable",
    "missing required env",
    "missing env var",
    "provider returned 5",
    "503",
    "502",
    "504",
    "serviceunavailable",
    "service unavailable",
    "connection refused",
    "failed to establish connection",
    "rate limit",
    "timeout after",
)


def _is_infra_signal(error: str | None) -> bool:
    if not error:
        return False
    needle = error.lower()
    return any(candidate in needle for candidate in _INFRA_ERROR_NEEDLES)


def _has_unsupported_judgment(report: EvalReport) -> bool:
    judgment = report.judgment
    if judgment is None:
        return False
    reasoning = (judgment.reasoning or "").lower()
    return "unsupported prompt" in reasoning


def _final_verdict(report: EvalReport) -> str | None:
    if report.retry_result and report.retry_result.retry_judgment:
        return (report.retry_result.retry_judgment.correctness or "").upper() or None
    if report.retry_decision:
        return (report.retry_decision.verdict or "").upper() or None
    return None


def _classify_report(report: EvalReport) -> str:
    """Return the single batch-summary bucket for ``report``."""
    if report.status == "exception":
        return "queries_exceptions"

    if _is_infra_signal(report.error):
        return "infra_failures"

    if report.error:
        return "queries_with_errors"

    if report.path_mode == "unsupported" or _has_unsupported_judgment(report):
        return "queries_unsupported"

    verdict = _final_verdict(report)
    if verdict in {"FAIL", "PARTIAL"}:
        return "queries_failed"

    first_verdict = (report.retry_decision.verdict or "").upper() if report.retry_decision else None
    retry_verdict = None
    if report.retry_result and report.retry_result.retry_judgment:
        retry_verdict = (report.retry_result.retry_judgment.correctness or "").upper()
    if first_verdict and first_verdict != "PASS" and retry_verdict == "PASS":
        return "queries_retried"

    if first_verdict and first_verdict != "PASS":
        return "queries_failed"

    return "queries_passed"


def _summarize_batch_results(results: Sequence[EvalReport]) -> dict[str, int | bool | str]:
    buckets = {
        "queries_passed": 0,
        "queries_retried": 0,
        "queries_failed": 0,
        "queries_unsupported": 0,
        "queries_with_errors": 0,
        "queries_exceptions": 0,
        "infra_failures": 0,
    }
    for entry in results:
        buckets[_classify_report(entry)] += 1

    if buckets["queries_exceptions"] > 0:
        run_status = "crashed"
    elif any(
        buckets[key] > 0
        for key in ("queries_failed", "queries_unsupported", "queries_with_errors", "infra_failures")
    ):
        run_status = "completed_with_failures"
    else:
        run_status = "completed"

    success = run_status == "completed"
    return {**buckets, "success": success, "run_status": run_status}


def build_batch_report(
    results: Sequence[EvalReport],
    *,
    run_id: str | None = None,
    created_at: datetime | None = None,
) -> EvalBatchReport:
    summary = _summarize_batch_results(results)
    return EvalBatchReport(
        run_id=run_id or uuid4().hex[:8],
        created_at=created_at or _utcnow(),
        queries_total=len(results),
        queries_passed=int(summary["queries_passed"]),
        queries_retried=int(summary["queries_retried"]),
        queries_failed=int(summary["queries_failed"]),
        queries_exceptions=int(summary["queries_exceptions"]),
        queries_unsupported=int(summary["queries_unsupported"]),
        queries_with_errors=int(summary["queries_with_errors"]),
        infra_failures=int(summary["infra_failures"]),
        success=bool(summary["success"]),
        run_status=str(summary["run_status"]),
        results=list(results),
    )


def write_report(report: EvalBatchReport, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = report.created_at.astimezone(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = output_dir / f"eval-{report.run_id}-{stamp}.json"
    path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return path


def build_dashboard_payload(report: EvalBatchReport) -> dict[str, object]:
    criterion_scores = {
        "correctness": {"pass_count": 0, "partial_count": 0, "fail_count": 0},
        "completeness": {"pass_count": 0, "partial_count": 0, "fail_count": 0},
        "routing_quality": {"pass_count": 0, "partial_count": 0, "fail_count": 0},
    }
    verdict_distribution = {"pass_count": 0, "retry_count": 0, "fail_count": 0}
    retry_comparisons: list[dict[str, object]] = []

    for entry in report.results:
        if entry.judgment:
            for field in ("correctness", "completeness", "routing_quality"):
                bucket = _score_bucket(getattr(entry.judgment, field))
                criterion_scores[field][bucket] += 1

        verdict = entry.retry_decision.verdict if entry.retry_decision else "FAIL"
        if verdict == "PASS":
            verdict_distribution["pass_count"] += 1
        elif verdict == "RETRY":
            verdict_distribution["retry_count"] += 1
        else:
            verdict_distribution["fail_count"] += 1

        if entry.retry_result and entry.judgment:
            retry_comparisons.append(
                {
                    "query": entry.query,
                    "original": entry.judgment.model_dump(mode="json"),
                    "retry": entry.retry_result.retry_judgment.model_dump(mode="json"),
                    "deltas": {
                        "correctness": _score_delta(
                            entry.judgment.correctness,
                            entry.retry_result.retry_judgment.correctness,
                        ),
                        "completeness": _score_delta(
                            entry.judgment.completeness,
                            entry.retry_result.retry_judgment.completeness,
                        ),
                        "routing_quality": _score_delta(
                            entry.judgment.routing_quality,
                            entry.retry_result.retry_judgment.routing_quality,
                        ),
                    },
                }
            )

    return {
        "report": report.model_dump(mode="json"),
        "criterion_scores": criterion_scores,
        "verdict_distribution": verdict_distribution,
        "retry_comparisons": retry_comparisons,
    }


def _load_text_queries(path: Path) -> list[str]:
    queries = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        queries.append(line)
    return queries


def _load_json_queries(path: Path) -> list[str]:
    payload = json.loads(path.read_text(encoding="utf-8"))

    if isinstance(payload, Mapping):
        full_test_payload = payload.get("full_test")
        if isinstance(full_test_payload, Mapping):
            tests = full_test_payload.get("tests")
            if not isinstance(tests, list):
                raise ValueError(f"Expected {path} full_test.tests to contain a JSON list of test objects.")

            queries: list[str] = []
            for index, entry in enumerate(tests):
                if not isinstance(entry, Mapping):
                    raise ValueError(f"Expected full_test entry {index} in {path} to be a JSON object.")
                query = entry.get("query")
                if not query:
                    entry_id = entry.get("id", index)
                    raise ValueError(f"Expected full_test entry {entry_id!r} in {path} to include a query.")
                queries.append(str(query))
            return queries

        raise ValueError(f"Expected {path} to contain a JSON list of query objects or a testing.json full_test payload.")

    if not isinstance(payload, list):
        raise ValueError(f"Expected {path} to contain a JSON list of query objects.")

    queries: list[str] = []
    for index, entry in enumerate(payload):
        if not isinstance(entry, Mapping):
            raise ValueError(f"Expected entry {index} in {path} to be a JSON object.")
        question = entry.get("question")
        if not question:
            entry_id = entry.get("id", index)
            raise ValueError(f"Expected entry {entry_id!r} in {path} to include a question.")
        queries.append(str(question))
    return queries


def load_queries(path: Path) -> list[str]:
    if path.suffix.lower() == ".json":
        return _load_json_queries(path)
    return _load_text_queries(path)


def limit_queries(queries: Sequence[str], limit: int | None = None) -> list[str]:
    if limit is None:
        return list(queries)
    return list(queries[:limit])


def _infer_bundle_mode(debug: Mapping[str, object], *, planner: bool) -> str | None:
    parser_plan = debug.get("parser_plan")
    if isinstance(parser_plan, Mapping):
        mode = parser_plan.get("mode")
        if isinstance(mode, str) and mode.strip():
            return mode
    if planner:
        return "plan"
    mode = debug.get("mode")
    if isinstance(mode, str) and mode.strip():
        return mode
    return None


def _synthesize_bundle_from_result(
    session: InMemorySessionState,
    *,
    query: str,
    planner: bool,
    result: Mapping[str, object],
) -> int | None:
    debug = result.get("debug")
    if not isinstance(debug, Mapping):
        return None

    history = session.get("results_history") or []
    bundle_id = len(history) + 1
    mode = _infer_bundle_mode(debug, planner=planner)
    bundle: dict[str, object] = {
        "id": bundle_id,
        "timestamp": _utcnow().isoformat(),
        "user_query": query,
        "mode": mode,
    }

    reply = result.get("reply")
    if isinstance(reply, str) and reply.strip():
        bundle["reply"] = reply

    for key in (
        "parser_plan",
        "api_plan",
        "api_result_full",
        "api_result_slim",
        "graph_plan",
        "graph_result",
        "reporter_plan",
        "reporter_result",
        "report_writer_output",
        "report_saved_files",
        "plan",
        "step_results",
        "raw_result_path",
        "raw_result_paths",
        "graph_debug_path",
        "graph_debug_paths",
        "plan_debug_path",
    ):
        value = debug.get(key)
        if value is not None:
            bundle[key] = value

    files = result.get("files")
    if isinstance(files, list) and files:
        bundle["files"] = files

    history.append(bundle)
    session["results_history"] = history
    session["last_debug"] = dict(debug)
    return bundle_id


def evaluate_query(
    query: str,
    *,
    config,
    workflow: EvaluatorWorkflow,
    credentials: dict[str, str] | None = None,
    planner: bool = False,
    execute_retry: bool = True,
    session: InMemorySessionState | None = None,
):
    active_session = session or InMemorySessionState(
        {
            "session_id": str(uuid4()),
            "user_id": 0,
            "results_history": [],
            "last_debug": {},
        }
    )
    runner = run_query_plan if planner else run_query
    result = runner(active_session, config, query, credentials=credentials)
    bundle_id = result.get("bundle_id")
    if bundle_id is None:
        history = active_session.get("results_history") or []
        if not history:
            bundle_id = _synthesize_bundle_from_result(
                active_session,
                query=query,
                planner=planner,
                result=result,
            )
            history = active_session.get("results_history") or []
        if bundle_id is None and history:
            bundle_id = history[-1]["id"]
        if bundle_id is None:
            raise ValueError("Evaluator batch query completed without a bundle to evaluate.")

    source = f"bundle:{active_session['session_id']}:{bundle_id}"
    return workflow.evaluate_source(
        source,
        session=active_session,
        execute_retry=execute_retry,
        config=config,
        credentials=credentials,
    )


def _batch_state_path(output_dir: Path, run_id: str) -> Path:
    return output_dir / f"batch-{run_id}.json"


def write_async_batch_state(state: AsyncBatchRunState, state_path: Path | None = None) -> Path:
    if state_path is not None:
        resolved = state_path
    elif state.state_path:
        resolved = Path(state.state_path)
    else:
        resolved = _batch_state_path(Path(state.output_dir), state.run_id)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    state.updated_at = _utcnow()
    state.completed_queries = sum(1 for item in state.items if item.status == "completed")
    state.pending_queries = sum(1 for item in state.items if item.status != "completed")
    state.state_path = str(resolved)
    resolved.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    return resolved


def load_async_batch_state(state_path: Path) -> AsyncBatchRunState:
    return AsyncBatchRunState.model_validate_json(state_path.read_text(encoding="utf-8"))


def _build_async_batch_state(
    queries: Sequence[str],
    *,
    output_dir: Path,
    planner: bool,
    concurrency: int,
    input_path: Path | None,
    mode: str | None,
    run_id: str | None = None,
) -> AsyncBatchRunState:
    resolved_run_id = run_id or uuid4().hex[:8]
    created_at = _utcnow()
    items = [AsyncBatchItemState(index=index, query=query) for index, query in enumerate(queries)]
    state = AsyncBatchRunState(
        run_id=resolved_run_id,
        created_at=created_at,
        updated_at=created_at,
        input_path=str(input_path) if input_path is not None else None,
        output_dir=str(output_dir),
        planner=planner,
        mode=mode,
        concurrency=concurrency,
        status="pending",
        total_queries=len(items),
        completed_queries=0,
        pending_queries=len(items),
        items=items,
    )
    state.state_path = str(_batch_state_path(output_dir, resolved_run_id))
    return state


def finalize_async_batch_state(
    state: AsyncBatchRunState,
    *,
    report: EvalBatchReport,
    report_path: Path,
    html_path: Path,
    state_path: Path | None = None,
) -> Path:
    state.status = report.run_status
    state.success = report.success
    state.final_report_path = str(report_path)
    state.final_html_path = str(html_path)
    return write_async_batch_state(state, state_path=state_path)


async def run_batch_evaluation_async(
    queries: Sequence[str] | None = None,
    *,
    output_dir: Path,
    config,
    workflow: EvaluatorWorkflow,
    credentials: dict[str, str] | None = None,
    planner: bool = False,
    execute_retry: bool = True,
    concurrency: int = 5,
    resume_path: Path | None = None,
    input_path: Path | None = None,
    mode: str | None = None,
) -> tuple[AsyncBatchRunState, Path, EvalBatchReport]:
    output_dir.mkdir(parents=True, exist_ok=True)

    if resume_path is not None:
        state_path = resume_path
        state = load_async_batch_state(resume_path)
        output_dir = Path(state.output_dir)
    else:
        if queries is None:
            raise ValueError("queries are required when creating a new async batch run.")
        state = _build_async_batch_state(
            queries,
            output_dir=output_dir,
            planner=planner,
            concurrency=concurrency,
            input_path=input_path,
            mode=mode,
        )
        state_path = Path(state.state_path or _batch_state_path(output_dir, state.run_id))

    state.status = "running"
    state.concurrency = concurrency
    if mode is not None:
        state.mode = mode
    write_async_batch_state(state, state_path=state_path)

    state_lock = asyncio.Lock()
    semaphore = asyncio.Semaphore(concurrency)

    async def _run_item(item: AsyncBatchItemState) -> None:
        if item.status == "completed" and item.report is not None:
            return

        async with semaphore:
            async with state_lock:
                item.status = "running"
                item.attempts += 1
                item.started_at = _utcnow()
                item.completed_at = None
                item.error = None
                write_async_batch_state(state, state_path=state_path)

            try:
                result = await asyncio.to_thread(
                    evaluate_query,
                    item.query,
                    config=config,
                    workflow=workflow,
                    credentials=credentials,
                    planner=planner,
                    execute_retry=execute_retry,
                )
                report = result_to_report(item.query, result)
            except Exception as exc:
                report = EvalReport(
                    query=item.query,
                    status="exception",
                    error=f"{type(exc).__name__}: {exc}",
                )

            async with state_lock:
                item.report = report
                item.error = report.error
                item.status = "completed" if report.status != "exception" else "exception"
                item.completed_at = _utcnow()
                write_async_batch_state(state, state_path=state_path)

    tasks = [asyncio.create_task(_run_item(item)) for item in state.items if item.status != "completed"]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        async with state_lock:
            for item in state.items:
                if item.status == "running":
                    item.status = "pending"
            state.status = "interrupted"
            write_async_batch_state(state, state_path=state_path)
        raise

    reports: list[EvalReport] = []
    for item in sorted(state.items, key=lambda entry: entry.index):
        if item.report is not None:
            reports.append(item.report)
        else:
            reports.append(
                EvalReport(
                    query=item.query,
                    status="exception",
                    error="Missing async batch report entry.",
                )
            )

    batch_report = build_batch_report(reports, run_id=state.run_id, created_at=state.created_at)
    state.status = batch_report.run_status
    state.success = batch_report.success
    write_async_batch_state(state, state_path=state_path)
    return state, state_path, batch_report


def run_batch_evaluation(
    queries: Sequence[str],
    *,
    config,
    workflow: EvaluatorWorkflow,
    credentials: dict[str, str] | None = None,
    planner: bool = False,
    execute_retry: bool = True,
) -> EvalBatchReport:
    reports: list[EvalReport] = []
    for query in queries:
        try:
            result = evaluate_query(
                query,
                config=config,
                workflow=workflow,
                credentials=credentials,
                planner=planner,
                execute_retry=execute_retry,
            )
            reports.append(result_to_report(query, result))
        except Exception as exc:
            reports.append(
                EvalReport(
                    query=query,
                    status="exception",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    return build_batch_report(reports)

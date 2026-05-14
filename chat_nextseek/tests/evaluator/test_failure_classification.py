"""Precedence-based failure classification tests (DD-43, FU-01)."""
from __future__ import annotations

from uuid import uuid4

from chat_nextseek.evaluator.reports import (
    EvalReport,
    JudgmentSummary,
    RetryDecisionSummary,
    RetryOutcome,
    _classify_report,
    _summarize_batch_results,
    build_batch_report,
)


def _make(
    *,
    status: str = "completed",
    error: str | None = None,
    path_mode: str | None = "new_search",
    verdict: str = "PASS",
    retry_verdict: str | None = None,
    judgment_reasoning: str = "ok",
) -> EvalReport:
    decision = RetryDecisionSummary(
        verdict=verdict,
        should_retry=(verdict != "PASS"),
        retry_query=None,
        suggestions=[],
        reasoning="",
    )
    retry_result = None
    if retry_verdict is not None:
        retry_judgment = JudgmentSummary(
            correctness=retry_verdict,
            completeness=retry_verdict,
            routing_quality=retry_verdict,
            reasoning="retry",
        )
        retry_result = RetryOutcome(
            retry_task_id=uuid4(),
            retry_query="retry query",
            retry_judgment=retry_judgment,
        )
    return EvalReport(
        query="q",
        status=status,
        task_id=uuid4(),
        error=error,
        execution_mode="standard",
        path_mode=path_mode,
        judgment=JudgmentSummary(
            correctness="PASS",
            completeness="PASS",
            routing_quality="PASS",
            reasoning=judgment_reasoning,
        ),
        retry_decision=decision,
        retry_result=retry_result,
    )


def test_classify_pass_on_first_attempt():
    assert _classify_report(_make(verdict="PASS")) == "queries_passed"


def test_classify_retried_pass():
    report = _make(verdict="RETRY", retry_verdict="PASS")
    assert _classify_report(report) == "queries_retried"


def test_classify_retry_without_retry_result_counts_as_failed():
    report = _make(verdict="RETRY")
    assert _classify_report(report) == "queries_failed"


def test_classify_failed_on_final_attempt():
    assert _classify_report(_make(verdict="FAIL")) == "queries_failed"


def test_classify_partial_counts_as_failed():
    report = _make(verdict="RETRY", retry_verdict="PARTIAL")
    assert _classify_report(report) == "queries_failed"


def test_classify_unsupported_path_mode():
    report = _make(path_mode="unsupported", verdict="FAIL")
    assert _classify_report(report) == "queries_unsupported"


def test_classify_unsupported_via_judgment_signal():
    report = _make(verdict="FAIL", judgment_reasoning="Parser returned unsupported prompt")
    assert _classify_report(report) == "queries_unsupported"


def test_classify_with_errors_takes_precedence_over_unsupported():
    report = _make(path_mode="unsupported", error="reporter internal error: KeyError")
    assert _classify_report(report) == "queries_with_errors"


def test_classify_exception_outranks_errors():
    report = _make(status="exception", error="boom")
    assert _classify_report(report) == "queries_exceptions"


def test_classify_exception_outranks_infra_string():
    report = _make(status="exception", error="Neo4j unreachable at bolt://localhost:7687")
    assert _classify_report(report) == "queries_exceptions"


def test_classify_caught_infra_failure_neo4j():
    report = _make(status="completed", error="ServiceUnavailable: Failed to establish connection to Neo4j")
    assert _classify_report(report) == "infra_failures"


def test_classify_caught_infra_failure_5xx():
    report = _make(status="completed", error="anthropic provider returned 503 after 3 retries")
    assert _classify_report(report) == "infra_failures"


def test_classify_caught_infra_failure_missing_env_var():
    report = _make(status="completed", error="missing required env var NEXTSEEK_BASE_URL")
    assert _classify_report(report) == "infra_failures"


def test_classify_caught_infra_failure_rate_limit():
    report = _make(status="completed", error="rate limit exhausted after 5 retries")
    assert _classify_report(report) == "infra_failures"


def test_classify_first_attempt_partial_counts_as_failed():
    report = _make(verdict="PARTIAL")
    assert _classify_report(report) == "queries_failed"


def test_classify_exception_with_error_does_not_leak_to_with_errors():
    report = _make(status="exception", error="boom")
    assert _classify_report(report) == "queries_exceptions"


def test_classify_judgment_none_falls_through():
    report = _make(verdict="PASS")
    report.judgment = None  # type: ignore[misc]
    assert _classify_report(report) == "queries_passed"


def test_classify_retry_decision_none_defaults_to_passed():
    report = _make(verdict="PASS")
    report.retry_decision = None  # type: ignore[misc]
    assert _classify_report(report) == "queries_passed"


def test_summary_counts_are_mutually_exclusive():
    reports = [
        _make(verdict="PASS"),
        _make(verdict="RETRY", retry_verdict="PASS"),
        _make(verdict="FAIL"),
        _make(path_mode="unsupported", verdict="FAIL"),
        _make(error="reporter internal error: foo"),
        _make(status="exception", error="boom"),
        _make(status="completed", error="Neo4j unreachable"),
    ]
    summary = _summarize_batch_results(reports)
    assert summary["queries_passed"] == 1
    assert summary["queries_retried"] == 1
    assert summary["queries_failed"] == 1
    assert summary["queries_unsupported"] == 1
    assert summary["queries_with_errors"] == 1
    assert summary["queries_exceptions"] == 1
    assert summary["infra_failures"] == 1
    total = sum(
        summary[key]
        for key in (
            "queries_passed",
            "queries_retried",
            "queries_failed",
            "queries_unsupported",
            "queries_with_errors",
            "queries_exceptions",
            "infra_failures",
        )
    )
    assert total == len(reports), "every report must land in exactly one bucket"


def test_run_status_completed_when_only_passes():
    reports = [_make(verdict="PASS"), _make(verdict="RETRY", retry_verdict="PASS")]
    summary = _summarize_batch_results(reports)
    assert summary["run_status"] == "completed"
    assert summary["success"] is True


def test_run_status_crashed_when_exceptions_present():
    reports = [_make(verdict="PASS"), _make(status="exception", error="boom")]
    summary = _summarize_batch_results(reports)
    assert summary["run_status"] == "crashed"
    assert summary["success"] is False


def test_run_status_completed_with_failures_on_unsupported_only():
    reports = [_make(path_mode="unsupported", verdict="FAIL")]
    summary = _summarize_batch_results(reports)
    assert summary["run_status"] == "completed_with_failures"
    assert summary["success"] is False


def test_build_batch_report_preserves_disjoint_counts():
    reports = [
        _make(verdict="PASS"),
        _make(path_mode="unsupported", verdict="FAIL"),
        _make(status="completed", error="Neo4j unreachable"),
        _make(status="exception", error="Neo4j unreachable"),
    ]
    batch = build_batch_report(reports)
    assert batch.queries_passed == 1
    assert batch.queries_unsupported == 1
    assert batch.infra_failures == 1
    assert batch.queries_exceptions == 1
    assert batch.queries_failed == 0
    assert batch.run_status == "crashed"


def test_summary_empty_batch():
    summary = _summarize_batch_results([])
    assert summary["queries_passed"] == 0
    assert summary["queries_retried"] == 0
    assert summary["queries_failed"] == 0
    assert summary["queries_unsupported"] == 0
    assert summary["queries_with_errors"] == 0
    assert summary["queries_exceptions"] == 0
    assert summary["infra_failures"] == 0
    assert summary["run_status"] == "completed"
    assert summary["success"] is True

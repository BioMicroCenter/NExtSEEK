from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import uuid4

from chat_nextseek.evaluator.dashboard.data_adapter import DeltaValue, compute_dashboard_data, parse_batch_report
from chat_nextseek.evaluator.reports import EvalBatchReport, EvalReport, JudgmentSummary, RetryDecisionSummary, RetryOutcome


def _sample_report() -> EvalBatchReport:
    return EvalBatchReport(
        run_id="demo-dashboard",
        created_at=datetime(2026, 4, 20, tzinfo=timezone.utc),
        queries_total=3,
        queries_passed=1,
        queries_retried=1,
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
                query="retry",
                status="completed",
                judgment=JudgmentSummary(
                    correctness="FAIL",
                    completeness="PARTIAL",
                    routing_quality="FAIL",
                    reasoning="initial",
                ),
                retry_decision=RetryDecisionSummary(
                    verdict="RETRY",
                    should_retry=True,
                    retry_query="retry query",
                    reasoning="retry",
                ),
                retry_result=RetryOutcome(
                    retry_task_id=uuid4(),
                    retry_query="retry query",
                    retry_judgment=JudgmentSummary(
                        correctness="PARTIAL",
                        completeness="PASS",
                        routing_quality="FAIL",
                        reasoning="better",
                    ),
                ),
            ),
            EvalReport(
                query="weird",
                status="completed",
                judgment=JudgmentSummary(
                    correctness="MAYBE",
                    completeness="PASS",
                    routing_quality="UNKNOWN",
                    reasoning="odd",
                ),
                retry_decision=RetryDecisionSummary(
                    verdict="SOMETHING_ELSE",
                    should_retry=False,
                    reasoning="fallback",
                ),
            ),
        ],
    )


def test_parse_batch_report_accepts_eval_batch_report_json():
    report = parse_batch_report(_sample_report().model_dump_json())
    assert report.run_id == "demo-dashboard"
    assert len(report.results) == 3


def test_compute_dashboard_data_aggregates_scores_and_retry_deltas():
    dashboard = compute_dashboard_data(parse_batch_report(_sample_report().model_dump_json()))

    assert dashboard.criterion_scores.correctness.pass_count == 1
    assert dashboard.criterion_scores.correctness.partial_count == 0
    assert dashboard.criterion_scores.correctness.fail_count == 2
    assert dashboard.verdict_distribution.pass_count == 1
    assert dashboard.verdict_distribution.retry_count == 1
    assert dashboard.verdict_distribution.fail_count == 1
    assert len(dashboard.retry_comparisons) == 1
    assert dashboard.retry_comparisons[0].deltas.correctness == DeltaValue.IMPROVED
    assert dashboard.retry_comparisons[0].deltas.completeness == DeltaValue.IMPROVED
    assert dashboard.retry_comparisons[0].deltas.routing_quality == DeltaValue.SAME


def test_parse_batch_report_remains_superset_compatible_for_string_scores():
    payload = json.loads(_sample_report().model_dump_json())
    payload["results"][0]["judgment"]["correctness"] = "CUSTOM"
    payload["results"][0]["retry_decision"]["verdict"] = "CUSTOM"

    dashboard = compute_dashboard_data(parse_batch_report(json.dumps(payload)))
    assert dashboard.criterion_scores.correctness.fail_count >= 1
    assert dashboard.verdict_distribution.fail_count >= 1


def test_compute_dashboard_data_reports_degraded_delta():
    report = EvalBatchReport(
        run_id="demo-degraded",
        created_at=datetime(2026, 4, 20, tzinfo=timezone.utc),
        queries_total=1,
        queries_passed=0,
        queries_retried=1,
        queries_failed=0,
        results=[
            EvalReport(
                query="degraded",
                status="completed",
                judgment=JudgmentSummary(
                    correctness="PASS",
                    completeness="PASS",
                    routing_quality="PASS",
                    reasoning="initial",
                ),
                retry_decision=RetryDecisionSummary(
                    verdict="RETRY",
                    should_retry=True,
                    retry_query="retry",
                    reasoning="retry",
                ),
                retry_result=RetryOutcome(
                    retry_task_id=uuid4(),
                    retry_query="retry",
                    retry_judgment=JudgmentSummary(
                        correctness="FAIL",
                        completeness="PARTIAL",
                        routing_quality="PASS",
                        reasoning="worse",
                    ),
                ),
            )
        ],
    )

    dashboard = compute_dashboard_data(parse_batch_report(report.model_dump_json()))
    assert dashboard.retry_comparisons[0].deltas.correctness == DeltaValue.DEGRADED

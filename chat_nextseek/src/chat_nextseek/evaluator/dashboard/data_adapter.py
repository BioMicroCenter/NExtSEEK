"""Data adapter: parse evaluator batch reports into dashboard-friendly models."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict


class DeltaValue(str, Enum):
    IMPROVED = "IMPROVED"
    SAME = "SAME"
    DEGRADED = "DEGRADED"


class JudgmentData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correctness: str
    completeness: str
    routing_quality: str
    reasoning: str


class SuggestionData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    description: str


class RetryDecisionData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict: str
    should_retry: bool
    retry_query: str | None
    suggestions: list[SuggestionData] | None
    reasoning: str


class RetryResultData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    retry_task_id: str
    retry_query: str
    retry_judgment: JudgmentData


class EvalResultData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    status: str = "completed"
    task_id: str | None = None
    error: str | None = None
    execution_mode: str | None = None
    path_mode: str | None = None
    judgment: JudgmentData | None = None
    retry_decision: RetryDecisionData | None = None
    retry_result: RetryResultData | None = None


class BatchReportData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    created_at: str
    queries_total: int
    queries_passed: int
    queries_retried: int
    queries_failed: int
    queries_exceptions: int = 0
    queries_unsupported: int = 0
    queries_with_errors: int = 0
    infra_failures: int = 0
    success: bool | None = None
    run_status: str | None = None
    results: list[EvalResultData]


class ScoreDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pass_count: int
    partial_count: int
    fail_count: int


class VerdictDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pass_count: int
    retry_count: int
    fail_count: int


class CriterionScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correctness: ScoreDistribution
    completeness: ScoreDistribution
    routing_quality: ScoreDistribution


class RetryComparisonDeltas(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correctness: DeltaValue
    completeness: DeltaValue
    routing_quality: DeltaValue


class RetryComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    original: JudgmentData
    retry: JudgmentData
    deltas: RetryComparisonDeltas


class DashboardData(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report: BatchReportData
    criterion_scores: CriterionScores
    verdict_distribution: VerdictDistribution
    retry_comparisons: list[RetryComparison]


def _normalize_score(value: str | None) -> str:
    normalized = (value or "").strip().upper()
    if normalized in {"PASS", "PARTIAL", "FAIL"}:
        return normalized
    return "FAIL"


def _score_rank(value: str | None) -> int:
    return {"FAIL": 0, "PARTIAL": 1, "PASS": 2}[_normalize_score(value)]


def _compute_delta(original: str | None, retry: str | None) -> DeltaValue:
    original_rank = _score_rank(original)
    retry_rank = _score_rank(retry)
    if retry_rank > original_rank:
        return DeltaValue.IMPROVED
    if retry_rank < original_rank:
        return DeltaValue.DEGRADED
    return DeltaValue.SAME


def _normalize_verdict(value: str | None) -> str:
    normalized = (value or "").strip().upper()
    if normalized in {"PASS", "RETRY", "FAIL"}:
        return normalized
    return "FAIL"


def parse_batch_report(json_str: str) -> BatchReportData:
    """Parse a JSON string into a validated BatchReportData model."""

    return BatchReportData.model_validate_json(json_str)


def compute_dashboard_data(report: BatchReportData) -> DashboardData:
    """Transform a parsed batch report into pre-computed dashboard aggregates."""

    valid_results = [r for r in report.results if r.judgment is not None and r.retry_decision is not None]

    distributions: dict[str, ScoreDistribution] = {}
    for criterion in ("correctness", "completeness", "routing_quality"):
        counts = {"PASS": 0, "PARTIAL": 0, "FAIL": 0}
        for result in valid_results:
            counts[_normalize_score(getattr(result.judgment, criterion))] += 1
        distributions[criterion] = ScoreDistribution(
            pass_count=counts["PASS"],
            partial_count=counts["PARTIAL"],
            fail_count=counts["FAIL"],
        )

    verdict_counts = {"PASS": 0, "RETRY": 0, "FAIL": 0}
    for result in valid_results:
        verdict_counts[_normalize_verdict(result.retry_decision.verdict)] += 1

    retry_comparisons: list[RetryComparison] = []
    for result in valid_results:
        if result.retry_result is None:
            continue
        retry_comparisons.append(
            RetryComparison(
                query=result.query,
                original=result.judgment,
                retry=result.retry_result.retry_judgment,
                deltas=RetryComparisonDeltas(
                    correctness=_compute_delta(
                        result.judgment.correctness,
                        result.retry_result.retry_judgment.correctness,
                    ),
                    completeness=_compute_delta(
                        result.judgment.completeness,
                        result.retry_result.retry_judgment.completeness,
                    ),
                    routing_quality=_compute_delta(
                        result.judgment.routing_quality,
                        result.retry_result.retry_judgment.routing_quality,
                    ),
                ),
            )
        )

    return DashboardData(
        report=report,
        criterion_scores=CriterionScores(
            correctness=distributions["correctness"],
            completeness=distributions["completeness"],
            routing_quality=distributions["routing_quality"],
        ),
        verdict_distribution=VerdictDistribution(
            pass_count=verdict_counts["PASS"],
            retry_count=verdict_counts["RETRY"],
            fail_count=verdict_counts["FAIL"],
        ),
        retry_comparisons=retry_comparisons,
    )

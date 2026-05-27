from __future__ import annotations

import json
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from uuid import uuid4

import pytest

from contextlib import redirect_stdout

from chat_nextseek.evaluator.dashboard.data_adapter import compute_dashboard_data, parse_batch_report
from chat_nextseek.evaluator.dashboard.render import _PLACEHOLDER, find_latest_report, main, render, to_js_format
from chat_nextseek.evaluator.reports import EvalBatchReport, EvalReport, JudgmentSummary, RetryDecisionSummary, RetryOutcome


REPO_ROOT = Path(__file__).resolve().parents[2]
TEMPLATE_PATH = REPO_ROOT / "src/chat_nextseek/evaluator/dashboard/dashboard-template.html"


def _sample_report() -> EvalBatchReport:
    return EvalBatchReport(
        run_id="demo-render",
        created_at=datetime(2026, 4, 20, tzinfo=timezone.utc),
        queries_total=1,
        queries_passed=0,
        queries_retried=1,
        queries_failed=0,
        results=[
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
                        correctness="PASS",
                        completeness="PARTIAL",
                        routing_quality="FAIL",
                        reasoning="better",
                    ),
                ),
            )
        ],
    )


def test_find_latest_report_returns_most_recent_json(tmp_path):
    older = tmp_path / "eval-old.json"
    newer = tmp_path / "eval-new.json"
    older.write_text("{}", encoding="utf-8")
    newer.write_text("{}", encoding="utf-8")
    older.touch()
    newer.touch()

    assert find_latest_report(tmp_path) == newer


def test_find_latest_report_raises_for_missing_or_empty_dirs(tmp_path):
    with pytest.raises(FileNotFoundError):
        find_latest_report(tmp_path / "missing")
    with pytest.raises(FileNotFoundError):
        find_latest_report(tmp_path)


def test_to_js_format_uses_expected_keys_and_lowercased_deltas():
    dashboard = compute_dashboard_data(parse_batch_report(_sample_report().model_dump_json()))
    js_payload = to_js_format(dashboard)

    assert js_payload["criterion_scores"]["correctness"] == {"PASS": 0, "PARTIAL": 0, "FAIL": 1}
    assert js_payload["verdict_distribution"] == {"PASS": 0, "RETRY": 1, "FAIL": 0}
    assert js_payload["retry_comparisons"][0]["deltas"]["correctness"] == "improved"
    assert js_payload["retry_comparisons"][0]["deltas"]["completeness"] == "same"


def test_render_embeds_json_and_removes_placeholder(tmp_path):
    report_path = tmp_path / "eval-report.json"
    output_path = tmp_path / "rendered.html"
    report_path.write_text(_sample_report().model_dump_json(indent=2), encoding="utf-8")

    result = render(report_path, TEMPLATE_PATH, output_path)
    assert result == output_path

    html = output_path.read_text(encoding="utf-8")
    assert _PLACEHOLDER not in html
    assert "demo-render" in html
    assert "EMBEDDED_REPORT_DATA" in html


def test_render_requires_single_placeholder(tmp_path):
    report_path = tmp_path / "eval-report.json"
    template_path = tmp_path / "bad-template.html"
    output_path = tmp_path / "rendered.html"
    report_path.write_text(_sample_report().model_dump_json(indent=2), encoding="utf-8")
    template_path.write_text("<html><body>No placeholder here</body></html>", encoding="utf-8")

    with pytest.raises(ValueError, match="exactly once"):
        render(report_path, template_path, output_path)


def test_template_contains_single_placeholder():
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    assert template.count(_PLACEHOLDER) == 1


def test_render_main_writes_output_with_default_template(tmp_path):
    report_path = tmp_path / "eval-report.json"
    output_path = tmp_path / "rendered.html"
    report_path.write_text(_sample_report().model_dump_json(indent=2), encoding="utf-8")

    buf = StringIO()
    with redirect_stdout(buf):
        code = main(["--out-dir", str(tmp_path), "--output", str(output_path)])

    assert code == 0
    assert output_path.exists()
    output = buf.getvalue()
    assert f"report: {report_path}" in output
    assert f"html: {output_path}" in output

"""Render evaluator batch reports into a standalone HTML dashboard."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from .data_adapter import DashboardData, JudgmentData, RetryComparison, compute_dashboard_data, parse_batch_report

_PLACEHOLDER = "/* __EMBEDDED_DATA__ */null"


def find_latest_report(out_dir: Path) -> Path:
    """Find the most recently modified eval-*.json file in *out_dir*."""

    if not out_dir.exists():
        raise FileNotFoundError(f"Directory does not exist: {out_dir}")

    reports = sorted(out_dir.glob("eval-*.json"), key=lambda p: p.stat().st_mtime)
    if not reports:
        raise FileNotFoundError(f"No eval-*.json reports found in {out_dir}")
    return reports[-1]


def _judgment_to_dict(judgment: JudgmentData) -> dict[str, str]:
    return {
        "correctness": judgment.correctness,
        "completeness": judgment.completeness,
        "routing_quality": judgment.routing_quality,
        "reasoning": judgment.reasoning,
    }


def _retry_comparison_to_dict(retry_comparison: RetryComparison) -> dict[str, object]:
    return {
        "query": retry_comparison.query,
        "original_scores": _judgment_to_dict(retry_comparison.original),
        "retry_scores": _judgment_to_dict(retry_comparison.retry),
        "deltas": {
            "correctness": retry_comparison.deltas.correctness.value.lower(),
            "completeness": retry_comparison.deltas.completeness.value.lower(),
            "routing_quality": retry_comparison.deltas.routing_quality.value.lower(),
        },
    }


def to_js_format(dashboard_data: DashboardData) -> dict[str, object]:
    """Convert Python DashboardData into the JS-expected dict shape."""

    criterion_scores: dict[str, dict[str, int]] = {}
    for criterion in ("correctness", "completeness", "routing_quality"):
        distribution = getattr(dashboard_data.criterion_scores, criterion)
        criterion_scores[criterion] = {
            "PASS": distribution.pass_count,
            "PARTIAL": distribution.partial_count,
            "FAIL": distribution.fail_count,
        }

    verdict_distribution = {
        "PASS": dashboard_data.verdict_distribution.pass_count,
        "RETRY": dashboard_data.verdict_distribution.retry_count,
        "FAIL": dashboard_data.verdict_distribution.fail_count,
    }

    return {
        "criterion_scores": criterion_scores,
        "verdict_distribution": verdict_distribution,
        "retry_comparisons": [
            _retry_comparison_to_dict(retry_comparison)
            for retry_comparison in dashboard_data.retry_comparisons
        ],
        "report": json.loads(dashboard_data.report.model_dump_json()),
    }


def render(report_path: Path, template_path: Path, output_path: Path) -> Path:
    """Read a batch report, compute dashboard data, and embed into an HTML template."""

    report = parse_batch_report(report_path.read_text(encoding="utf-8"))
    dashboard = compute_dashboard_data(report)
    js_data = to_js_format(dashboard)

    template = template_path.read_text(encoding="utf-8")
    if template.count(_PLACEHOLDER) != 1:
        raise ValueError(
            f"Template {template_path} must contain the placeholder {_PLACEHOLDER!r} exactly once"
        )

    rendered = template.replace(_PLACEHOLDER, json.dumps(js_data, indent=2), 1)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    return output_path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render evaluator dashboard HTML")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("out"),
        help="Directory containing eval-*.json reports (default: out)",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=Path(__file__).with_name("dashboard-template.html"),
        help="Path to the HTML template",
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="Path for the rendered HTML output",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    report_path = find_latest_report(args.out_dir)
    result = render(report_path, args.template, args.output)
    print(f"report: {report_path}")
    print(f"html: {result}")
    return 0


__all__ = ["_PLACEHOLDER", "find_latest_report", "main", "render", "to_js_format"]

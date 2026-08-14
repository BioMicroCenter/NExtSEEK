"""HiBayes CSV exporter — DMAC headless HTML report → 14-column CSV.

Five layers, all in this module per DD-01:
1. HTML extraction (extract_manifest_json)
2. Raw Pydantic validation (RawQuerySummary, RawRunManifest)
3. Normalized query layer (NormalizedQueryRun, parse_query_id, derive_failure_mode)
4. Final HiBayes row (HiBayesEvalRow)
5. Export (HiBayesEvalTable.to_csv / to_dataframe)

Plan: .claude/plans/hibayes-csv-exporter-2026-05-08.md
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HIBAYES_CSV_COLUMNS: tuple[str, ...] = (
    "query_id",
    "task_family",
    "task_subtype",
    "image",
    "answer_provided",
    "is_error",
    "timed_out",
    "runtime_success",
    "failure_mode",
    "latency_seconds",
    "cost_usd",
    "tool_calls_total",
    "artifact_count",
    "is_opus",
)

DEFAULT_CSV_NAME = "hibayes_eval_rows.csv"

_MANIFEST_RE = re.compile(
    r'<script type="application/json" id="manifest">(.*?)</script>',
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ManifestNotFoundError(LookupError):
    """Raised when the embedded manifest <script> tag is missing from the HTML."""


class MalformedQueryIdError(ValueError):
    """Raised when a query_id cannot be parsed into a non-empty task_family."""


class ManifestConsistencyError(ValueError):
    """Raised when post-parse cross-checks against the manifest fail."""


# ---------------------------------------------------------------------------
# Layer 1: HTML extraction
# ---------------------------------------------------------------------------


def extract_manifest_json(html: str) -> dict[str, Any]:
    """Pull the embedded JSON manifest from a DMAC headless HTML report."""
    match = _MANIFEST_RE.search(html)
    if match is None:
        raise ManifestNotFoundError(
            'No <script type="application/json" id="manifest"> tag in HTML.'
        )
    return json.loads(match.group(1))


# ---------------------------------------------------------------------------
# Layer 2: Raw Pydantic models (extra="allow")
# ---------------------------------------------------------------------------


class ToolUseSummary(BaseModel):
    model_config = ConfigDict(extra="allow")
    tool: str
    count: int


class RawQuerySummary(BaseModel):
    model_config = ConfigDict(extra="allow")
    query_id: str
    query_text: str
    latency_seconds: float
    cost_usd: float | None
    cost_estimated: bool
    artifacts: list[str]
    tool_use_summary: list[ToolUseSummary]
    tool_calls_total: int
    answer_provided: bool
    is_error: bool
    error: str | None
    timed_out: bool
    num_turns: int | None  # Amendment B: live fixture has null for timeout summaries
    stop_reason: str | None
    record_path: str
    final_answer: str | None


class RawRunManifest(BaseModel):
    model_config = ConfigDict(extra="allow")
    # Required fields declared without defaults per DD-03 / R-05.
    run_id: str
    started_at: str
    completed_at: str
    image: str
    corpus: str
    timeout_seconds: int
    max_budget_usd: float
    queries_total: int
    queries_answered: int
    queries_errored: int
    queries_timed_out: int
    answer_rate: float
    total_latency_seconds: float
    total_cost_usd: float
    avg_latency_seconds: float
    avg_cost_usd: float
    aborted: bool
    abort_reason: str | None
    summaries: list[RawQuerySummary]


# ---------------------------------------------------------------------------
# Layer 3: Normalized query layer
# ---------------------------------------------------------------------------


class FailureMode(str, Enum):
    none = "none"
    timeout = "timeout"
    error = "error"
    no_answer = "no_answer"


_INT_TOKEN_RE = re.compile(r"[0-9]+")


def parse_query_id(query_id: str) -> tuple[str, str | None, int | None]:
    """Parse `Search-Basic-3` style IDs per DD-04.

    Returns ``(task_family, task_subtype, query_index)``. Pure-integer or empty
    IDs raise ``MalformedQueryIdError`` rather than emit ``task_family=""``.
    """
    if not query_id:
        raise MalformedQueryIdError("query_id is empty")
    tokens = query_id.split("-")
    query_index: int | None = None
    if tokens and _INT_TOKEN_RE.fullmatch(tokens[-1]):
        query_index = int(tokens[-1])
        tokens = tokens[:-1]
    if not tokens:
        raise MalformedQueryIdError(
            f"query_id={query_id!r} has no non-integer tokens — would emit empty task_family"
        )
    task_family = "-".join(tokens)
    task_subtype = "-".join(tokens[1:]) if len(tokens) > 1 else None
    return task_family, task_subtype, query_index


def derive_failure_mode(
    *, answer_provided: bool, is_error: bool, timed_out: bool
) -> FailureMode:
    """Pure derivation per DD-05; priority: timeout > error > no_answer > none."""
    if timed_out:
        return FailureMode.timeout
    if is_error:
        return FailureMode.error
    if not answer_provided:
        return FailureMode.no_answer
    return FailureMode.none


class NormalizedQueryRun(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str
    query_text: str
    task_family: str
    task_subtype: str | None
    query_index: int | None
    image: str
    answer_provided: bool
    is_error: bool
    timed_out: bool
    runtime_success: bool
    failure_mode: FailureMode
    latency_seconds: float
    cost_usd: float | None
    tool_calls_total: int
    artifact_count: int
    is_opus: int

    @model_validator(mode="after")
    def _check_derived_fields(self) -> NormalizedQueryRun:
        expected_failure = derive_failure_mode(
            answer_provided=self.answer_provided,
            is_error=self.is_error,
            timed_out=self.timed_out,
        )
        if self.failure_mode is not expected_failure:
            raise ValueError(
                f"failure_mode {self.failure_mode!r} inconsistent with flags; expected {expected_failure!r}"
            )
        expected_success = (
            self.answer_provided and not self.is_error and not self.timed_out
        )
        if self.runtime_success is not expected_success:
            raise ValueError(
                f"runtime_success {self.runtime_success!r} inconsistent with flags; expected {expected_success!r}"
            )
        return self


def normalize_query_run(
    raw: RawQuerySummary, *, image: str, is_opus: int
) -> NormalizedQueryRun:
    family, subtype, index = parse_query_id(raw.query_id)
    return NormalizedQueryRun(
        query_id=raw.query_id,
        query_text=raw.query_text,
        task_family=family,
        task_subtype=subtype,
        query_index=index,
        image=image,
        answer_provided=raw.answer_provided,
        is_error=raw.is_error,
        timed_out=raw.timed_out,
        runtime_success=raw.answer_provided and not raw.is_error and not raw.timed_out,
        failure_mode=derive_failure_mode(
            answer_provided=raw.answer_provided,
            is_error=raw.is_error,
            timed_out=raw.timed_out,
        ),
        latency_seconds=raw.latency_seconds,
        cost_usd=raw.cost_usd,
        tool_calls_total=raw.tool_calls_total,
        artifact_count=len(raw.artifacts),
        is_opus=is_opus,
    )


# ---------------------------------------------------------------------------
# Layer 4: Final HiBayes row
# ---------------------------------------------------------------------------


class HiBayesEvalRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query_id: str
    task_family: str
    task_subtype: str | None
    image: str
    answer_provided: bool
    is_error: bool
    timed_out: bool
    runtime_success: bool
    failure_mode: FailureMode
    latency_seconds: float
    cost_usd: float | None
    tool_calls_total: int
    artifact_count: int
    is_opus: int

    @classmethod
    def from_normalized(cls, n: NormalizedQueryRun) -> HiBayesEvalRow:
        return cls(
            query_id=n.query_id,
            task_family=n.task_family,
            task_subtype=n.task_subtype,
            image=n.image,
            answer_provided=n.answer_provided,
            is_error=n.is_error,
            timed_out=n.timed_out,
            runtime_success=n.runtime_success,
            failure_mode=n.failure_mode,
            latency_seconds=n.latency_seconds,
            cost_usd=n.cost_usd,
            tool_calls_total=n.tool_calls_total,
            artifact_count=n.artifact_count,
            is_opus=n.is_opus,
        )


# ---------------------------------------------------------------------------
# Layer 5: Export
# ---------------------------------------------------------------------------


def _row_to_csv_dict(row: HiBayesEvalRow) -> dict[str, str]:
    """Apply DD-07 formatting rules: bool → "true"/"false", None → "", enum → .value.

    Phase 4 MAJOR-3: uses `model_dump(mode="json")` to get deterministic JSON-mode
    serialization. In native mode, pydantic v2's behavior for `class FailureMode(str, Enum)`
    members is version-dependent (the enum member vs. its `.value` string), and Python 3.11+
    changed `str(EnumMember)` for mixed-in str-Enum types. JSON mode normalizes enum values
    to their `.value` string and Python booleans remain Python booleans, removing both
    sources of ambiguity.
    """

    def fmt(value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    dumped = row.model_dump(mode="json")
    return {col: fmt(dumped[col]) for col in HIBAYES_CSV_COLUMNS}


class HiBayesEvalTable(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
    rows: list[HiBayesEvalRow]

    def to_csv(self, output_path: str | Path | None = None) -> Path:
        """Write CSV. If output_path is None, default to CWD/hibayes_eval_rows.csv (DD-09)."""
        if output_path is None:
            target = Path.cwd() / DEFAULT_CSV_NAME
        else:
            target = Path(output_path)
        target = target.resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(HIBAYES_CSV_COLUMNS))
            writer.writeheader()
            for row in self.rows:
                writer.writerow(_row_to_csv_dict(row))
        return target

    def to_dataframe(self):  # noqa: ANN201 — pandas optional, lazy-imported
        """Return a pandas DataFrame with locked column order. Lazy-imports pandas.

        Phase 4 BLOCKER-2: setting `sys.modules["pandas"] = None` causes
        `import pandas` to raise ImportError ("import of pandas halted; use of
        sys.modules['pandas'] is not allowed"), which the except clause catches.
        A separate `if pd is None` guard would be unreachable dead code.
        """
        try:
            import pandas as pd  # noqa: WPS433 — deliberate lazy import (DD-12, R-03)
        except ImportError as exc:
            raise RuntimeError(
                "pandas is not installed in this environment. "
                "Run: uv sync --group tools"
            ) from exc
        records = [_row_to_csv_dict(row) for row in self.rows]
        df = pd.DataFrame(records, columns=list(HIBAYES_CSV_COLUMNS))
        return df


# ---------------------------------------------------------------------------
# Cross-checks (DD-08)
# ---------------------------------------------------------------------------


def _validate_consistency(
    *, manifest: RawRunManifest, rows: list[HiBayesEvalRow], raw_summary_count: int
) -> None:
    failures: list[str] = []
    # Check 0 — pre-Pydantic raw count vs declared total
    if raw_summary_count != manifest.queries_total:
        failures.append(
            f"check #0: raw summaries-array length {raw_summary_count} != "
            f"manifest.queries_total {manifest.queries_total}"
        )
    # Check 1 — final rows count vs declared total AND vs raw count
    if len(rows) != manifest.queries_total:
        failures.append(
            f"check #1: len(rows) {len(rows)} != manifest.queries_total {manifest.queries_total}"
        )
    if len(rows) != raw_summary_count:
        failures.append(
            f"check #1b: len(rows) {len(rows)} != raw_summary_count {raw_summary_count}"
        )
    # Check 2 — answered count
    answered = sum(1 for r in rows if r.answer_provided)
    if answered != manifest.queries_answered:
        failures.append(
            f"check #2: queries_answered: derived {answered} != manifest {manifest.queries_answered}"
        )
    # Check 3 — error count
    errors = sum(1 for r in rows if r.is_error)
    if errors != manifest.queries_errored:
        failures.append(
            f"check #3: queries_errored: derived {errors} != manifest {manifest.queries_errored}"
        )
    # Check 4 — timeout count
    timeouts = sum(1 for r in rows if r.timed_out)
    if timeouts != manifest.queries_timed_out:
        failures.append(
            f"check #4: queries_timed_out: derived {timeouts} != manifest {manifest.queries_timed_out}"
        )
    # Check 5 — runtime_success consistency
    bad_success = [
        r.query_id
        for r in rows
        if r.runtime_success
        and (r.is_error or r.timed_out or not r.answer_provided)
    ]
    if bad_success:
        failures.append(
            f"check #5: runtime_success=True with inconsistent flags on {bad_success}"
        )
    # Check 6 — is_opus is binary AND uniform
    is_opus_values = {r.is_opus for r in rows}
    if not is_opus_values.issubset({0, 1}):
        failures.append(
            f"check #6: is_opus values must be {0,1}; got {is_opus_values}"
        )
    if len(is_opus_values) > 1:
        failures.append(
            f"check #6: is_opus must be uniform per run; got {is_opus_values}"
        )
    if failures:
        raise ManifestConsistencyError(
            "Manifest consistency check failed:\n  - "
            + "\n  - ".join(failures)
        )


# ---------------------------------------------------------------------------
# Builder (full pipeline)
# ---------------------------------------------------------------------------


def build_table_from_html(html: str, *, image: str, is_opus: int) -> HiBayesEvalTable:
    """End-to-end: HTML string → validated, cross-checked HiBayesEvalTable."""
    manifest_json = extract_manifest_json(html)
    raw_summary_count = len(manifest_json.get("summaries", []))
    manifest = RawRunManifest.model_validate(manifest_json)
    rows = [
        HiBayesEvalRow.from_normalized(
            normalize_query_run(raw, image=image, is_opus=is_opus)
        )
        for raw in manifest.summaries
    ]
    _validate_consistency(
        manifest=manifest, rows=rows, raw_summary_count=raw_summary_count
    )
    return HiBayesEvalTable(rows=rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tools.hibayes.exporter",
        description="Convert DMAC headless HTML report to HiBayes CSV.",
    )
    parser.add_argument("report", type=Path, help="Path to report.html")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output CSV path. Default: <report-dir>/hibayes_eval_rows.csv",
    )
    parser.add_argument(
        "--model-family",
        choices=["sonnet", "opus"],
        default="sonnet",
        help="Model family for is_opus column (sonnet=0, opus=1). Default: sonnet.",
    )
    parser.add_argument(
        "--use-llm-classifier",
        action="store_true",
        help="(deferred) Run BAML semantic classifier — currently raises.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.use_llm_classifier:
        raise NotImplementedError(
            "BAML semantic classifier not implemented; "
            "see plan hibayes-csv-exporter-2026-05-08, DD-11."
        )
    report_path: Path = args.report
    if not report_path.is_file():
        print(f"error: report not found: {report_path}", file=sys.stderr)
        return 3
    is_opus = 1 if args.model_family == "opus" else 0
    try:
        html = report_path.read_text(encoding="utf-8")
        manifest_json = extract_manifest_json(html)
    except ManifestNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    image = manifest_json.get("image", "unknown")
    try:
        table = build_table_from_html(html, image=image, is_opus=is_opus)
    except ManifestConsistencyError as exc:
        print(f"consistency error: {exc}", file=sys.stderr)
        return 2
    output = args.output
    if output is None:
        output = report_path.parent / DEFAULT_CSV_NAME
    written = table.to_csv(output)
    print(str(written))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

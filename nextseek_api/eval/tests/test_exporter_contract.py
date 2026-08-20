# tools/hibayes/tests/test_exporter.py
"""Behavioral contract for the HiBayes CSV exporter.

Tests are organized in the same order as the TDD steps in task-01-hibayes-exporter.md
Section 4. Every assertion has a clear behavioral motivation; do not rephrase tests
to match an implementation that diverges from the spec.
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from nextseek_api.eval.exporter import (
    HIBAYES_CSV_COLUMNS,
    FailureMode,
    HiBayesEvalRow,
    ManifestConsistencyError,
    ManifestNotFoundError,
    MalformedQueryIdError,
    RawQuerySummary,
    RawRunManifest,
    build_table_from_html,
    derive_failure_mode,
    extract_manifest_json,
    main,
    normalize_query_run,
    parse_query_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# Test file is at tools/hibayes/tests/test_exporter.py — repo root is parents[3]:
#   parents[0] = tools/hibayes/tests/
#   parents[1] = tools/hibayes/
#   parents[2] = tools/
#   parents[3] = <repo root>   ← this is what we want
# (The conftest at tools/hibayes/conftest.py uses parents[2], one level shallower.
#  Phase 4 finding BLOCKER-4: a previous draft used parents[2] here, which silently
#  pointed LIVE_REPORT at tools/evidence/... and made the acceptance test skip forever.)
REPO_ROOT = Path(__file__).resolve().parents[3]
LIVE_REPORT = REPO_ROOT / "evidence" / "headless" / "20260507T224850Z" / "report.html"


def _make_summary(
    *,
    query_id: str = "Search-Basic-1",
    answer_provided: bool = True,
    is_error: bool = False,
    timed_out: bool = False,
    cost_usd: float | None = 0.10,
    cost_estimated: bool = False,
    error: str | None = None,
    artifacts: list[str] | None = None,
    tool_calls_total: int = 2,
    final_answer: str | None = "ok",
) -> dict:
    return {
        "query_id": query_id,
        "query_text": "test query",
        "latency_seconds": 1.23,
        "cost_usd": cost_usd,
        "cost_estimated": cost_estimated,
        "artifacts": artifacts or [],
        "tool_use_summary": [{"tool": "Read", "count": 1}],
        "tool_calls_total": tool_calls_total,
        "answer_provided": answer_provided,
        "is_error": is_error,
        "error": error,
        "timed_out": timed_out,
        "num_turns": 3,
        "stop_reason": "end_turn",
        "record_path": "evidence/test/record.json",
        "final_answer": final_answer,
    }


def _make_manifest(*, summaries: list[dict] | None = None, **overrides) -> dict:
    base = {
        "run_id": "TESTRUN",
        "started_at": "2026-05-08T00:00:00Z",
        "completed_at": "2026-05-08T00:01:00Z",
        "image": "dmac-assistant:test",
        "corpus": "/tmp/corpus.json",
        "timeout_seconds": 180,
        "max_budget_usd": 0.5,
        "queries_total": 1,
        "queries_answered": 1,
        "queries_errored": 0,
        "queries_timed_out": 0,
        "answer_rate": 1.0,
        "total_latency_seconds": 1.23,
        "total_cost_usd": 0.10,
        "avg_latency_seconds": 1.23,
        "avg_cost_usd": 0.10,
        "aborted": False,
        "abort_reason": None,
        "summaries": summaries if summaries is not None else [_make_summary()],
    }
    base.update(overrides)
    return base


def _wrap_html(manifest: dict) -> str:
    return (
        '<html><head></head><body><script type="application/json" id="manifest">'
        + json.dumps(manifest)
        + '</script></body></html>'
    )


# ---------------------------------------------------------------------------
# Test 1: HTML extraction
# ---------------------------------------------------------------------------

def test_extract_manifest_from_html_returns_dict():
    manifest = _make_manifest()
    html = _wrap_html(manifest)
    result = extract_manifest_json(html)
    assert isinstance(result, dict)
    assert result["run_id"] == "TESTRUN"
    assert len(result["summaries"]) == 1


def test_extract_manifest_raises_when_script_missing():
    html = "<html><body><p>no manifest here</p></body></html>"
    with pytest.raises(ManifestNotFoundError):
        extract_manifest_json(html)


# ---------------------------------------------------------------------------
# Test 2: row count equals queries_total
# ---------------------------------------------------------------------------

def test_row_count_equals_queries_total():
    # Phase 4 MAJOR-2: assert against a hard-coded literal (3), not against
    # manifest["queries_total"] — comparing a derived value to its own source
    # is a tautology. Cross-check #0 in _validate_consistency catches the
    # manifest-vs-array desync; this test pins actual produced row count.
    summaries = [_make_summary(query_id=f"Search-Basic-{i}") for i in range(1, 4)]
    manifest = _make_manifest(summaries=summaries, queries_total=3, queries_answered=3)
    table = build_table_from_html(_wrap_html(manifest), image=manifest["image"], is_opus=0)
    assert len(table.rows) == 3


# ---------------------------------------------------------------------------
# Test 3: task-family parsing per user spec
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "qid,family,subtype,index",
    [
        ("Search-Basic-1", "Search-Basic", "Basic", 1),
        ("Search-MultiAssay-2", "Search-MultiAssay", "MultiAssay", 2),
        ("Report-SRA-3", "Report-SRA", "SRA", 3),
        ("Memory-2", "Memory", None, 2),
        ("Unsupported-5", "Unsupported", None, 5),
        ("Edge-1", "Edge", None, 1),
        ("Report-NFCORE-3", "Report-NFCORE", "NFCORE", 3),
        # No trailing integer
        ("System-Capabilities", "System-Capabilities", "Capabilities", None),
        ("Memory", "Memory", None, None),
    ],
)
def test_parse_query_id_canonical_shapes(qid, family, subtype, index):
    fam, sub, idx = parse_query_id(qid)
    assert fam == family
    assert sub == subtype
    assert idx == index


def test_parse_query_id_pure_integer_raises():
    with pytest.raises(MalformedQueryIdError):
        parse_query_id("42")


def test_parse_query_id_empty_raises():
    with pytest.raises(MalformedQueryIdError):
        parse_query_id("")


def test_parse_query_id_unicode_superscript_not_treated_as_index():
    # DD-04: `"²".isdigit()` returns True but `int("²")` raises ValueError, so we
    # use `re.fullmatch(r"[0-9]+", token)` which excludes Unicode superscripts.
    # The trailing token "²" is therefore NOT popped as an integer index — it
    # remains part of task_family. Phase 4 BLOCKER-3 renamed this from
    # `_rejected` because the prior name suggested an exception was expected.
    # Amendment A (2026-05-09): subtype="²" matches impl behavior — subtypes
    # inherit non-integer trailing tokens (parallel to "Search-Basic-1" → subtype="Basic").
    fam, sub, idx = parse_query_id("Foo-²")
    assert fam == "Foo-²"
    assert sub == "²"
    assert idx is None


# ---------------------------------------------------------------------------
# Test 4: runtime_success
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "answer_provided,is_error,timed_out,expected",
    [
        (True, False, False, True),
        (True, True, False, False),
        (True, False, True, False),
        (False, False, False, False),
    ],
)
def test_runtime_success_derivation(answer_provided, is_error, timed_out, expected):
    raw = RawQuerySummary(
        **_make_summary(
            answer_provided=answer_provided,
            is_error=is_error,
            timed_out=timed_out,
            error="boom" if is_error else None,
        )
    )
    norm = normalize_query_run(raw, image="img", is_opus=0)
    assert norm.runtime_success is expected


# ---------------------------------------------------------------------------
# Test 5: failure_mode (incl. DD-05 compound case)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "answer_provided,is_error,timed_out,expected",
    [
        (True, False, True, FailureMode.timeout),
        (True, True, False, FailureMode.error),
        (False, False, False, FailureMode.no_answer),
        (True, False, False, FailureMode.none),
        # DD-05 compound: timed_out wins over is_error
        (True, True, True, FailureMode.timeout),
        # DD-05 compound: is_error wins over no_answer
        (False, True, False, FailureMode.error),
    ],
)
def test_failure_mode_priority(answer_provided, is_error, timed_out, expected):
    assert (
        derive_failure_mode(
            answer_provided=answer_provided,
            is_error=is_error,
            timed_out=timed_out,
        )
        is expected
    )


# ---------------------------------------------------------------------------
# Test 6 + 7: is_opus default 0 (sonnet); 1 (opus)
# ---------------------------------------------------------------------------

def test_is_opus_default_sonnet_is_zero(tmp_path: Path):
    manifest = _make_manifest()
    report = tmp_path / "report.html"
    report.write_text(_wrap_html(manifest))
    out = tmp_path / "out.csv"
    rc = main([str(report), "--output", str(out)])
    assert rc == 0
    with out.open() as fh:
        rows = list(csv.DictReader(fh))
    assert all(r["is_opus"] == "0" for r in rows)


def test_is_opus_opus_family_is_one(tmp_path: Path):
    manifest = _make_manifest()
    report = tmp_path / "report.html"
    report.write_text(_wrap_html(manifest))
    out = tmp_path / "out.csv"
    rc = main([str(report), "--output", str(out), "--model-family", "opus"])
    assert rc == 0
    with out.open() as fh:
        rows = list(csv.DictReader(fh))
    assert all(r["is_opus"] == "1" for r in rows)


# ---------------------------------------------------------------------------
# Test 8: CSV column order
# ---------------------------------------------------------------------------

def test_csv_column_order_locked():
    expected = (
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
    assert HIBAYES_CSV_COLUMNS == expected
    assert len(HIBAYES_CSV_COLUMNS) == 14


def test_csv_first_line_matches_column_order(tmp_path: Path):
    manifest = _make_manifest()
    report = tmp_path / "report.html"
    report.write_text(_wrap_html(manifest))
    out = tmp_path / "out.csv"
    main([str(report), "--output", str(out)])
    with out.open() as fh:
        header = fh.readline().strip()
    assert header == ",".join(HIBAYES_CSV_COLUMNS)


# ---------------------------------------------------------------------------
# Test 9: to_csv writes file and returns path
# ---------------------------------------------------------------------------

def test_to_csv_writes_file_and_returns_path(tmp_path: Path):
    manifest = _make_manifest()
    table = build_table_from_html(_wrap_html(manifest), image=manifest["image"], is_opus=0)
    out = tmp_path / "result.csv"
    returned = table.to_csv(out)
    assert returned == out.resolve()
    assert out.exists()
    with out.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1


def test_to_csv_default_path_is_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """DD-09: model-method default with output_path=None writes to CWD."""
    manifest = _make_manifest()
    table = build_table_from_html(_wrap_html(manifest), image=manifest["image"], is_opus=0)
    monkeypatch.chdir(tmp_path)
    returned = table.to_csv(None)
    assert returned == (tmp_path / "hibayes_eval_rows.csv").resolve()
    assert returned.exists()


def test_to_csv_serializes_none_cost_as_empty(tmp_path: Path):
    """R-01: cost_usd=None must be empty string, not 'None'."""
    manifest = _make_manifest(
        summaries=[_make_summary(cost_usd=None, cost_estimated=True)]
    )
    table = build_table_from_html(_wrap_html(manifest), image=manifest["image"], is_opus=0)
    out = tmp_path / "out.csv"
    table.to_csv(out)
    with out.open() as fh:
        rows = list(csv.DictReader(fh))
    assert rows[0]["cost_usd"] == ""


# ---------------------------------------------------------------------------
# Test 10: mismatch checks raise ManifestConsistencyError
# ---------------------------------------------------------------------------

def test_cross_check_count_mismatch_raises():
    """DD-08 #1: queries_total mutated to wrong value triggers consistency error."""
    summaries = [_make_summary(query_id=f"Search-Basic-{i}") for i in range(1, 4)]
    manifest = _make_manifest(summaries=summaries, queries_total=3, queries_answered=3)
    # Mutate queries_total AFTER summaries — desyncs raw count from declared total
    manifest["queries_total"] = 99
    with pytest.raises(ManifestConsistencyError) as excinfo:
        build_table_from_html(_wrap_html(manifest), image=manifest["image"], is_opus=0)
    assert "queries_total" in str(excinfo.value)


def test_cross_check_pre_pydantic_raw_count_independent_of_row_count():
    """Phase 4 MAJOR-7: cross-check #0 (raw JSON summaries length vs queries_total)
    must fire on its own, independently of cross-check #1 (len(rows) vs queries_total).

    In the normal pipeline rows == manifest.summaries, so #0 and #1 always fail
    together. This test bypasses build_table_from_html and calls _validate_consistency
    directly with a constructed scenario where the raw JSON array has length 5
    but the manifest declares queries_total=3 and rows is also length 3 — i.e.
    #0 should fire but #1 should NOT. This pins independent #0 enforcement.
    """
    from nextseek_api.eval.exporter import _validate_consistency, RawRunManifest

    # rows of length 3 with answered/error/timeout counts matching manifest
    summaries = [_make_summary(query_id=f"Search-Basic-{i}") for i in range(1, 4)]
    manifest_dict = _make_manifest(summaries=summaries, queries_total=3, queries_answered=3)
    manifest = RawRunManifest.model_validate(manifest_dict)
    rows = [
        HiBayesEvalRow.from_normalized(
            normalize_query_run(raw, image=manifest.image, is_opus=0)
        )
        for raw in manifest.summaries
    ]

    # Lie about the raw_summary_count: claim the JSON array had 5 elements
    with pytest.raises(ManifestConsistencyError) as excinfo:
        _validate_consistency(manifest=manifest, rows=rows, raw_summary_count=5)
    msg = str(excinfo.value)
    assert "check #0" in msg
    # Specifically: check #1 (len(rows) vs queries_total) should NOT have fired
    assert "check #1:" not in msg


def test_cross_check_answered_mismatch_raises():
    summaries = [_make_summary(query_id="Search-Basic-1", answer_provided=True)]
    manifest = _make_manifest(summaries=summaries, queries_total=1, queries_answered=99)
    with pytest.raises(ManifestConsistencyError) as excinfo:
        build_table_from_html(_wrap_html(manifest), image=manifest["image"], is_opus=0)
    assert "queries_answered" in str(excinfo.value)


def test_required_field_missing_raises_validation_error():
    """DD-03 / R-05: removing queries_total from the manifest raises ValidationError(missing)."""
    manifest = _make_manifest()
    del manifest["queries_total"]
    with pytest.raises(ValidationError) as excinfo:
        RawRunManifest.model_validate(manifest)
    assert any(err["type"] == "missing" and "queries_total" in err["loc"] for err in excinfo.value.errors())


def test_extra_fields_allowed_on_raw_manifest():
    """DD-03: live manifest carries corpus_key/keep_state/queries_cost_estimated."""
    manifest = _make_manifest(
        corpus_key=None, keep_state=False, queries_cost_estimated=4
    )
    parsed = RawRunManifest.model_validate(manifest)
    # Required fields populated correctly
    assert parsed.queries_total == 1
    # Extras don't break validation


# ---------------------------------------------------------------------------
# CLI tests
# ---------------------------------------------------------------------------

def test_cli_default_output_alongside_input(tmp_path: Path):
    """DD-09 CLI behavior: default output is <input_dir>/hibayes_eval_rows.csv."""
    manifest = _make_manifest()
    report = tmp_path / "report.html"
    report.write_text(_wrap_html(manifest))
    rc = main([str(report)])
    assert rc == 0
    expected_out = tmp_path / "hibayes_eval_rows.csv"
    assert expected_out.exists()


def test_cli_use_llm_classifier_raises():
    """DD-11."""
    with pytest.raises(NotImplementedError) as excinfo:
        main(["fake.html", "--use-llm-classifier"])
    assert "DD-11" in str(excinfo.value)


def test_cli_bad_model_family_rejected(capsys: pytest.CaptureFixture[str]):
    """R-07: argparse rejects values outside {sonnet, opus}."""
    with pytest.raises(SystemExit) as excinfo:
        main(["fake.html", "--model-family", "bogus"])
    assert excinfo.value.code == 2  # argparse default for bad args
    err = capsys.readouterr().err
    assert "model-family" in err or "invalid choice" in err


def test_cli_missing_input_file_returns_nonzero(tmp_path: Path):
    rc = main([str(tmp_path / "does_not_exist.html")])
    assert rc != 0


# ---------------------------------------------------------------------------
# to_dataframe
# ---------------------------------------------------------------------------

def test_to_dataframe_preserves_column_order(tmp_path: Path):
    pytest.importorskip("pandas")
    manifest = _make_manifest()
    table = build_table_from_html(_wrap_html(manifest), image=manifest["image"], is_opus=0)
    df = table.to_dataframe()
    assert tuple(df.columns) == HIBAYES_CSV_COLUMNS


def test_to_dataframe_lazy_import_failure_raises_runtime_error(monkeypatch):
    """R-03: pandas-not-installed surfaces as a clear RuntimeError."""
    manifest = _make_manifest()
    table = build_table_from_html(_wrap_html(manifest), image=manifest["image"], is_opus=0)
    # Simulate ImportError at the lazy-import site
    monkeypatch.setitem(sys.modules, "pandas", None)
    with pytest.raises(RuntimeError) as excinfo:
        table.to_dataframe()
    assert "uv sync --group tools" in str(excinfo.value)


# ---------------------------------------------------------------------------
# Acceptance: live 103-row fixture (skip if absent)
# ---------------------------------------------------------------------------

@pytest.mark.acceptance
def test_acceptance_103_row_live_fixture(tmp_path: Path):
    if not LIVE_REPORT.exists():
        pytest.skip(f"Live fixture not found at {LIVE_REPORT}")
    out = tmp_path / "hibayes_eval_rows.csv"
    rc = main([str(LIVE_REPORT), "--output", str(out)])
    assert rc == 0
    with out.open() as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 103
    assert sum(r["answer_provided"] == "true" for r in rows) == 99
    assert sum(r["is_error"] == "true" for r in rows) == 5
    assert sum(r["timed_out"] == "true" for r in rows) == 4
    assert all(r["is_opus"] == "0" for r in rows)
    assert tuple(rows[0].keys()) == HIBAYES_CSV_COLUMNS

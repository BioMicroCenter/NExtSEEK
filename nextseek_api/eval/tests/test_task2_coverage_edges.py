"""Fast, hermetic edge coverage for the V4-9 Task 2 owned modules."""
from __future__ import annotations

import csv
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from nessie_tests.bayes_manifest import BayesManifest, BayesPair, completed_arms
from nextseek_api.eval import exporter
from nextseek_api.eval import export as ledger_export
from nextseek_api.eval import functional_inputs as fi
from nextseek_api.eval import router_models_proposal as router


def _raw_manifest(**overrides) -> exporter.RawRunManifest:
    values = {
        "run_id": "r", "started_at": "start", "completed_at": "end",
        "image": "image", "corpus": "corpus", "timeout_seconds": 1,
        "max_budget_usd": 1.0, "queries_total": 1, "queries_answered": 1,
        "queries_errored": 0, "queries_timed_out": 0, "answer_rate": 1.0,
        "total_latency_seconds": 1.0, "total_cost_usd": 0.1,
        "avg_latency_seconds": 1.0, "avg_cost_usd": 0.1,
        "aborted": False, "abort_reason": None,
        "summaries": [{
            "query_id": "Search-Basic-1", "query_text": "q",
            "latency_seconds": 1.0, "cost_usd": 0.1, "cost_estimated": False,
            "artifacts": [], "tool_use_summary": [], "tool_calls_total": 0,
            "answer_provided": True, "is_error": False, "error": None,
            "timed_out": False, "num_turns": 1, "stop_reason": "end",
            "record_path": "record.json", "final_answer": "a",
        }],
    }
    values.update(overrides)
    return exporter.RawRunManifest.model_validate(values)


def _normalized(**overrides) -> exporter.NormalizedQueryRun:
    values = {
        "query_id": "Search-Basic-1", "query_text": "q",
        "task_family": "Search-Basic", "task_subtype": "Basic", "query_index": 1,
        "image": "image", "answer_provided": True, "is_error": False,
        "timed_out": False, "runtime_success": True,
        "failure_mode": exporter.FailureMode.none, "latency_seconds": 1.0,
        "cost_usd": 0.1, "tool_calls_total": 0, "artifact_count": 0, "is_opus": 0,
    }
    values.update(overrides)
    return exporter.NormalizedQueryRun.model_validate(values)


def test_exporter_derived_fields_and_all_consistency_failures(tmp_path, capsys):
    with pytest.raises(ValidationError, match="failure_mode"):
        _normalized(failure_mode=exporter.FailureMode.error)
    with pytest.raises(ValidationError, match="runtime_success"):
        _normalized(runtime_success=False)

    manifest = _raw_manifest()
    bad_values = _normalized().model_dump()
    bad_values.update(is_error=True, timed_out=True, runtime_success=True, is_opus=2)
    bad = exporter.NormalizedQueryRun.model_construct(**bad_values)
    mixed_values = _normalized().model_dump()
    mixed_values.update(query_id="Search-Basic-2", is_opus=0)
    mixed = exporter.NormalizedQueryRun.model_construct(**mixed_values)
    with pytest.raises(exporter.ManifestConsistencyError) as exc:
        exporter._validate_consistency(
            manifest=manifest, rows=[bad, mixed], raw_summary_count=2
        )
    message = str(exc.value)
    assert "queries_errored" in message
    assert "queries_timed_out" in message
    assert "runtime_success=True" in message
    assert "values must be" in message
    assert "uniform per run" in message

    missing_manifest = tmp_path / "missing-manifest.html"
    missing_manifest.write_text("<html></html>")
    assert exporter.main([str(missing_manifest)]) == 3
    inconsistent = tmp_path / "inconsistent.html"
    payload = _raw_manifest(queries_total=2).model_dump(mode="json")
    inconsistent.write_text(
        '<script type="application/json" id="manifest">'
        + json.dumps(payload) + "</script>"
    )
    assert exporter.main([str(inconsistent)]) == 2
    assert "consistency error" in capsys.readouterr().err


def test_functional_input_csv_edges_and_manifest_without_record(tmp_path):
    artifact_csv = tmp_path / "artifacts.csv"
    with artifact_csv.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "query_id", "artifact_expected", "expected_artifact_kind",
            "artifact_declared", "artifact_validity_status",
        ])
        writer.writeheader()
        writer.writerows([
            {"query_id": "q", "artifact_expected": "false",
             "expected_artifact_kind": "", "artifact_declared": "false",
             "artifact_validity_status": ""},
            {"query_id": "q", "artifact_expected": "false",
             "expected_artifact_kind": "", "artifact_declared": "false",
             "artifact_validity_status": "future-status"},
            {"query_id": "q", "artifact_expected": "true",
             "expected_artifact_kind": "REPORT", "artifact_declared": "true",
             "artifact_validity_status": "Valid"},
        ])
    parsed = fi._read_artifact_csv(artifact_csv)
    assert parsed["q"]["declared_count"] == 1
    assert parsed["q"]["statuses"] == [fi.ArtifactStatus.Valid]

    manifest_dir = tmp_path / "evidence" / "headless" / "run"
    manifest_dir.mkdir(parents=True)
    manifest = manifest_dir / "manifest.json"
    manifest.write_text(json.dumps({"summaries": [{
        "query_id": "q", "query_text": "question", "record_path": ""
    }]}))
    runtime = tmp_path / "runtime.csv"
    runtime.write_text(
        "query_id,task_family,runtime_success,failure_mode\n"
        "q,Search-Basic,True,none\n"
    )
    out = tmp_path / "out.csv"
    assert fi.run_stage_b(
        manifest_path=manifest, runtime_csv_path=runtime,
        artifact_csv_path=artifact_csv, out_csv_path=out,
    ) == 0
    assert next(csv.DictReader(out.open()))["final_answer"] == ""


def _row(**overrides) -> router.EvalRow:
    values = {
        "query_id": "q", "route": "nextseek_query", "task_family": "Search-Basic",
        "route_source": router.RouteSource.forced,
        "family_source": router.FamilySource.corpus, "stack_id": "s",
        "answer_provided": True, "is_error": False, "timed_out": False,
        "runtime_success": True, "failure_mode": router.FailureMode.none,
        "error_class": router.ErrorClass.none, "latency_seconds": 1.0,
        "cost_usd": 1.0, "artifact_expected": False,
        "artifact_status": router.ArtifactStatus.not_expected,
        "artifact_success": True, "functional_success": True,
    }
    values.update(overrides)
    return router.EvalRow(**values)


def test_router_validators_dispositions_outcomes_and_aggregation(monkeypatch):
    with pytest.raises(ValidationError, match="runtime_success"):
        _row(runtime_success=False)
    with pytest.raises(ValidationError, match="artifact_expected=True"):
        _row(artifact_expected=True)
    with pytest.raises(ValidationError, match="artifact_success"):
        _row(artifact_status=router.ArtifactStatus.missing, artifact_success=True)
    with pytest.raises(ValidationError, match="family_source=corpus"):
        _row(family_source=router.FamilySource.baml)
    with pytest.raises(ValidationError, match="n_success"):
        router.RouteFamilyAggregate(
            task_family="f", route="nextseek_query", n_total=0, n_success=1,
            avg_latency_seconds=0,
        )

    assert _row(error_class=router.ErrorClass.provider_outage).outcome() is None
    assert _row(runtime_success=False, answer_provided=False,
                failure_mode=router.FailureMode.no_answer).outcome() is False
    assert _row(functional_success=None).outcome() is None
    assert _row(functional_success=False).outcome() is False

    error_row = _row(error_class=router.ErrorClass.code_error)
    monkeypatch.delitem(router.ERROR_CLASS_DISPOSITION, router.ErrorClass.code_error)
    with pytest.raises(ValueError, match="error_class"):
        _ = error_row.disposition
    failure_row = _row()
    monkeypatch.delitem(router.FAILURE_MODE_DISPOSITION, router.FailureMode.none)
    with pytest.raises(ValueError, match="failure_mode"):
        _ = failure_row.disposition

    # Restore mappings before exercising aggregation in this test.
    monkeypatch.setitem(router.ERROR_CLASS_DISPOSITION, router.ErrorClass.code_error,
                        router.Disposition.scored)
    monkeypatch.setitem(router.FAILURE_MODE_DISPOSITION, router.FailureMode.none,
                        router.Disposition.scored)
    rows = [
        _row(query_id="a", cost_usd=2.0),
        _row(query_id="b", functional_success=False, cost_usd=None),
        _row(query_id="c", route="container_cc", route_source=router.RouteSource.baml,
             family_source=router.FamilySource.baml,
             error_class=router.ErrorClass.provider_outage, cost_usd=None),
    ]
    aggregates = router.aggregate_by_family_and_route(rows)
    assert [(a.n_total, a.n_success, a.n_excluded) for a in aggregates] == [
        (2, 1, 0), (0, 0, 1)
    ]
    assert aggregates[0].avg_latency_seconds == 1.0
    assert aggregates[0].avg_cost_usd == 2.0
    assert aggregates[1].avg_latency_seconds == 0.0
    assert aggregates[1].avg_cost_usd is None
    assert router.arm_cache_key(rows[0]) == ("a", "nextseek_query", "s", "Search-Basic")


def test_completed_arms_covers_half_written_pair():
    empty = BayesPair(id="empty", family="f", ns=None, cc=None)
    full = BayesPair.model_construct(id="full", family="f", ns=object(), cc=object())
    assert completed_arms(BayesManifest.model_construct(pairs=[empty, full])) == {
        ("full", "ns"), ("full", "cc")
    }


def test_observational_export_rejects_bad_sources_and_honors_watermark(monkeypatch):
    base = {
        "session_id": "s", "turn_number": 1, "route": "nextseek_query",
        "task_family": "f", "assignment_propensity": None,
        "pinned_generation_id": None, "pinned_generation_hash": "",
    }
    with pytest.raises(ValueError, match="unknown route_source"):
        ledger_export.ledger_row_to_observational(SimpleNamespace(**base, route_source="future"))
    with pytest.raises(ValueError, match="paired experimental"):
        ledger_export.ledger_row_to_observational(SimpleNamespace(**base, route_source="forced"))

    rows = [
        SimpleNamespace(**base, route_source="forced", created_at=1),
        SimpleNamespace(**base, route_source="baml", created_at=2),
    ]
    class QuerySet:
        def all(self): return self
        def order_by(self, field):
            assert field == "created_at"
            return self
        def filter(self, **kwargs):
            assert kwargs == {"created_at__gt": 1}
            return self
        def __iter__(self): return iter(rows)
    monkeypatch.setattr(ledger_export.TurnLedger, "objects", QuerySet())
    exported = ledger_export.export_observational_rows(since=1)
    assert [row.observation_id for row in exported] == ["s:1"]

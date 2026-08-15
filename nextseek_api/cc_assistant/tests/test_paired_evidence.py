"""Independent paired-evidence oracle for Plan 005 Task 4."""
from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from nextseek_api.cc_assistant.op_registry import paired_evidence as pe
from nessie_tests import export as nexport
from nessie_tests import runner

REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "paired_evidence"
DEFAULT_CORPUS = REPO_ROOT / "nessie_tests" / "corpus.json"
PINNED_ZIP = Path(
    "/home/taishajo/work/NExtSEEK-dev/testquestions-2026-08-07/testquestions.zip"
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def _manifest(*, selected_ids: list[str], pairs: list[dict[str, Any]], corpus_fp: str) -> bytes:
    payload = {
        "run_meta": {
            "selected_ids": selected_ids,
            "corpus_fingerprint": corpus_fp,
        },
        "pairs": pairs,
    }
    return json.dumps(payload, indent=2).encode("utf-8")


def _pair(
    query_id: str,
    *,
    family: str = "sample_search",
    ns_route: str = "nextseek_query",
    cc_route: str = "container_cc",
    ns_source: str = "forced",
    cc_source: str = "forced",
) -> dict[str, Any]:
    return {
        "id": query_id,
        "family": family,
        "hibayes_subtype": "Search-Basic",
        "ns": {
            "id": query_id,
            "family": family,
            "tier": "route",
            "status": "passed",
            "route": ns_route,
            "route_source": ns_source,
        },
        "cc": {
            "id": query_id,
            "family": family,
            "tier": "route",
            "status": "passed",
            "route": cc_route,
            "route_source": cc_source,
        },
    }


def _graded_row(
    query_id: str,
    *,
    image: str,
    answer_provided: str = "true",
    is_error: str = "false",
    timed_out: str = "false",
    runtime_success: str = "true",
    human_success: str = "true",
    llm_success: str = "",
    usefulness_score: str = "",
    task_family: str = "sample_search",
) -> dict[str, str]:
    return {
        "query_id": query_id,
        "task_family": task_family,
        "task_subtype": "Search-Basic",
        "image": image,
        "answer_provided": answer_provided,
        "is_error": is_error,
        "timed_out": timed_out,
        "runtime_success": runtime_success,
        "failure_mode": "none",
        "latency_seconds": "1.0",
        "cost_usd": "",
        "tool_calls_total": "1",
        "artifact_count": "0",
        "is_opus": "0" if image == "nextseek_query" else "1",
        "human_success": human_success,
        "llm_success": llm_success,
        "agree": "",
        "usefulness_score": usefulness_score,
        "primary_issue": "",
    }


def _functional_row(
    query_id: str,
    *,
    task_family: str = "sample_search",
    query_text: str,
) -> dict[str, str]:
    return {
        "query_id": query_id,
        "task_family": task_family,
        "query_text": query_text,
        "final_answer": "redacted in fixtures",
        "answer_provided": "true",
        "runtime_success": "true",
        "failure_mode": "none",
        "artifact_expected": "false",
        "artifact_status": "NotExpected",
        "artifact_kind": "NONE_EXPECTED",
        "declared_artifact_count": "0",
        "expected_behavior": "AnswerDirectly",
    }


def _csv_bytes(rows: list[dict[str, str]]) -> bytes:
    if not rows:
        return b""
    columns = list(rows[0].keys())
    buf = io.StringIO()
    import csv

    writer = csv.DictWriter(buf, fieldnames=columns)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("utf-8")


def _tiny_corpus(*variants: tuple[str, str, str]) -> Path:
    """Build a temp corpus.json with (id, family, query_text) tuples."""
    import tempfile

    families: dict[str, list[dict[str, Any]]] = {}
    for query_id, family, query_text in variants:
        families.setdefault(family, []).append(
            {
                "id": query_id,
                "family": family,
                "name": query_id,
                "tags": [],
                "turns": [{"label": "main", "query": query_text}],
            }
        )
    payload = {
        "version": 2,
        "provenance": {"adopted_from": "fixture"},
        "family_defaults": {
            "sample_search": {
                "hibayes_subtype": "Search-Basic",
                "expected_behavior": "AnswerDirectly",
                "artifact_expected": False,
                "artifact_kind": "NONE_EXPECTED",
            }
        },
        "families": {
            family: {"variants": entries} for family, entries in families.items()
        },
    }
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    tmp.write(json.dumps(payload))
    tmp.flush()
    tmp.close()
    return Path(tmp.name)


def _build_fixture_zip(
    tmp_path: Path,
    *,
    corpus_path: Path,
    selected_ids: list[str],
    pairs: list[dict[str, Any]],
    graded_rows: list[dict[str, str]],
    functional_rows: list[dict[str, str]],
    zip_sha: str | None = None,
) -> Path:
    corpus_fp = runner.corpus_fingerprint(corpus_path)
    manifest = _manifest(selected_ids=selected_ids, pairs=pairs, corpus_fp=corpus_fp)
    members = {
        pe.ZIP_MEMBER_MANIFEST: manifest,
        pe.ZIP_MEMBER_GRADED: _csv_bytes(graded_rows),
        pe.ZIP_MEMBER_FUNCTIONAL: _csv_bytes(functional_rows),
        pe.ZIP_MEMBER_CORPUS: corpus_path.read_bytes(),
    }
    zip_path = tmp_path / "fixture.zip"
    _write_zip(zip_path, members)
    return zip_path


def _independent_recompute(payload: dict[str, Any]) -> dict[str, Any]:
    selected_ids = payload["selected_ids"]
    records = []
    for record in payload["records"]:
        ns = dict(record["ns"])
        cc = dict(record["cc"])
        ns["success"] = pe.arm_success(
            _outcome_to_graded(ns),
            forced_image=pe.FORCED_IMAGE_BY_ARM["ns"],
        )
        cc["success"] = pe.arm_success(
            _outcome_to_graded(cc),
            forced_image=pe.FORCED_IMAGE_BY_ARM["cc"],
        )
        records.append({**record, "ns": ns, "cc": cc})
    audit = pe._audit_partition(records, selected_ids=selected_ids)
    return {
        "records": records,
        "audit": audit,
        "selected_ids": selected_ids,
    }


def _outcome_to_graded(outcome: dict[str, Any]) -> dict[str, str]:
    def success(value: bool | None) -> str:
        if value is None:
            return ""
        return "true" if value else "false"

    return {
        "image": outcome["image"],
        "answer_provided": success(outcome["answer_provided"]),
        "is_error": success(outcome["is_error"]),
        "timed_out": success(outcome["timed_out"]),
        "runtime_success": success(outcome["runtime_success"]),
        "human_success": success(outcome["human_success"]),
        "llm_success": success(outcome["llm_success"]),
    }


def _standard_rows(
    query_id: str,
    *,
    ns_ok: bool,
    cc_ok: bool,
    query_text: str,
    llm_success: str = "",
    usefulness_score: str = "",
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    def ok(image: str, ok_flag: bool) -> dict[str, str]:
        return _graded_row(
            query_id,
            image=image,
            runtime_success="true" if ok_flag else "false",
            answer_provided="true" if ok_flag else "false",
            human_success="true" if ok_flag else "false",
            llm_success=llm_success if ok_flag else "",
            usefulness_score=usefulness_score if ok_flag else "",
        )

    graded = [
        ok("nextseek_query", ns_ok),
        ok("container_cc", cc_ok),
    ]
    functional = [
        _functional_row(nexport.stage_b_query_id(query_id, "ns"), query_text=query_text),
        _functional_row(nexport.stage_b_query_id(query_id, "cc"), query_text=query_text),
    ]
    return graded, functional


@pytest.fixture
def matrix_corpus(tmp_path):
    variants = (
        ("demo.ns_only", "sample_search", "NS only question"),
        ("demo.cc_only", "sample_search", "CC only question"),
        ("demo.both", "sample_search", "Both succeed question"),
        ("demo.neither", "sample_search", "Neither succeed question"),
        ("demo.empty_llm", "sample_search", "Empty LLM question"),
        ("demo.scored", "sample_search", "Scored question"),
        ("demo.unscored", "sample_search", "Unscored question"),
    )
    return _tiny_corpus(*variants)


def _ingest_with_pinned_sha(
    tmp_path: Path,
    corpus_path: Path,
    *,
    selected_ids: list[str],
    pairs: list[dict[str, Any]],
    graded_rows: list[dict[str, str]],
    functional_rows: list[dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    zip_path = _build_fixture_zip(
        tmp_path,
        corpus_path=corpus_path,
        selected_ids=selected_ids,
        pairs=pairs,
        graded_rows=graded_rows,
        functional_rows=functional_rows,
    )
    monkeypatch.setattr(pe, "PINNED_ZIP_SHA256", _sha256(zip_path.read_bytes()))
    return pe.ingest_paired_evidence(zip_path=zip_path, corpus_path=corpus_path)


@pytest.mark.parametrize(
    ("query_id", "ns_ok", "cc_ok", "bucket"),
    [
        ("demo.ns_only", True, False, "ns_only"),
        ("demo.cc_only", False, True, "cc_only"),
        ("demo.both", True, True, "both_success"),
        ("demo.neither", False, False, "neither_success"),
    ],
)
def test_fixture_partition_matrix(
    tmp_path, matrix_corpus, monkeypatch, query_id, ns_ok, cc_ok, bucket
):
    graded, functional = _standard_rows(
        query_id,
        ns_ok=ns_ok,
        cc_ok=cc_ok,
        query_text=dict(
            zip(
                [
                    "demo.ns_only",
                    "demo.cc_only",
                    "demo.both",
                    "demo.neither",
                ],
                [
                    "NS only question",
                    "CC only question",
                    "Both succeed question",
                    "Neither succeed question",
                ],
            )
        )[query_id],
    )
    payload = _ingest_with_pinned_sha(
        tmp_path,
        matrix_corpus,
        selected_ids=[query_id],
        pairs=[_pair(query_id)],
        graded_rows=graded,
        functional_rows=functional,
        monkeypatch=monkeypatch,
    )
    assert payload["audit"][bucket] == [query_id]
    for other in ("ns_only", "cc_only", "both_success", "neither_success"):
        if other != bucket:
            assert payload["audit"][other] == []


def test_empty_llm_success_is_not_failure(tmp_path, matrix_corpus, monkeypatch):
    graded, functional = _standard_rows(
        "demo.empty_llm",
        ns_ok=True,
        cc_ok=False,
        query_text="Empty LLM question",
        llm_success="",
    )
    payload = _ingest_with_pinned_sha(
        tmp_path,
        matrix_corpus,
        selected_ids=["demo.empty_llm"],
        pairs=[_pair("demo.empty_llm")],
        graded_rows=graded,
        functional_rows=functional,
        monkeypatch=monkeypatch,
    )
    assert payload["records"][0]["ns"]["llm_success"] is None
    assert payload["records"][0]["ns"]["success"] is True


def test_scored_and_unscored_usefulness(tmp_path, matrix_corpus, monkeypatch):
    graded_scored, functional_scored = _standard_rows(
        "demo.scored",
        ns_ok=True,
        cc_ok=True,
        query_text="Scored question",
        usefulness_score="4.5",
    )
    graded_unscored, functional_unscored = _standard_rows(
        "demo.unscored",
        ns_ok=True,
        cc_ok=True,
        query_text="Unscored question",
        usefulness_score="",
    )
    payload = _ingest_with_pinned_sha(
        tmp_path,
        matrix_corpus,
        selected_ids=["demo.scored", "demo.unscored"],
        pairs=[_pair("demo.scored"), _pair("demo.unscored")],
        graded_rows=graded_scored + graded_unscored,
        functional_rows=functional_scored + functional_unscored,
        monkeypatch=monkeypatch,
    )
    scored = payload["records"][0]["ns"]["usefulness_score"]
    unscored = payload["records"][1]["ns"]["usefulness_score"]
    assert scored == 4.5
    assert unscored is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("timed_out", "true"),
        ("is_error", "true"),
        ("runtime_success", "false"),
        ("answer_provided", "false"),
    ],
)
def test_runtime_failure_modes(tmp_path, matrix_corpus, monkeypatch, field, value):
    graded = [
        _graded_row("demo.neither", image="nextseek_query", **{field: value}),
        _graded_row("demo.neither", image="container_cc"),
    ]
    functional = [
        _functional_row(
            nexport.stage_b_query_id("demo.neither", "ns"),
            query_text="Neither succeed question",
        ),
        _functional_row(
            nexport.stage_b_query_id("demo.neither", "cc"),
            query_text="Neither succeed question",
        ),
    ]
    payload = _ingest_with_pinned_sha(
        tmp_path,
        matrix_corpus,
        selected_ids=["demo.neither"],
        pairs=[_pair("demo.neither")],
        graded_rows=graded,
        functional_rows=functional,
        monkeypatch=monkeypatch,
    )
    assert payload["records"][0]["ns"]["success"] is False


def test_bad_image_fails_whole_ingest(tmp_path, matrix_corpus, monkeypatch):
    graded = [
        _graded_row("demo.both", image="wrong_route"),
        _graded_row("demo.both", image="container_cc"),
    ]
    functional = [
        _functional_row(
            nexport.stage_b_query_id("demo.both", "ns"),
            query_text="Both succeed question",
        ),
        _functional_row(
            nexport.stage_b_query_id("demo.both", "cc"),
            query_text="Both succeed question",
        ),
    ]
    zip_path = _build_fixture_zip(
        tmp_path,
        corpus_path=matrix_corpus,
        selected_ids=["demo.both"],
        pairs=[_pair("demo.both")],
        graded_rows=graded,
        functional_rows=functional,
    )
    monkeypatch.setattr(pe, "PINNED_ZIP_SHA256", _sha256(zip_path.read_bytes()))
    with pytest.raises(pe.PairedEvidenceError, match="graded-row key set mismatch"):
        pe.ingest_paired_evidence(zip_path=zip_path, corpus_path=matrix_corpus)


def test_missing_arm_fails_closed(tmp_path, matrix_corpus, monkeypatch):
    graded, functional = _standard_rows(
        "demo.both",
        ns_ok=True,
        cc_ok=True,
        query_text="Both succeed question",
    )
    graded = [row for row in graded if row["image"] == "nextseek_query"]
    zip_path = _build_fixture_zip(
        tmp_path,
        corpus_path=matrix_corpus,
        selected_ids=["demo.both"],
        pairs=[_pair("demo.both")],
        graded_rows=graded,
        functional_rows=functional,
    )
    monkeypatch.setattr(pe, "PINNED_ZIP_SHA256", _sha256(zip_path.read_bytes()))
    with pytest.raises(pe.PairedEvidenceError, match="graded-row key set mismatch"):
        pe.ingest_paired_evidence(zip_path=zip_path, corpus_path=matrix_corpus)


def test_duplicate_arm_fails_closed(tmp_path, matrix_corpus, monkeypatch):
    graded, functional = _standard_rows(
        "demo.both",
        ns_ok=True,
        cc_ok=True,
        query_text="Both succeed question",
    )
    graded.append(dict(graded[0]))
    zip_path = _build_fixture_zip(
        tmp_path,
        corpus_path=matrix_corpus,
        selected_ids=["demo.both"],
        pairs=[_pair("demo.both")],
        graded_rows=graded,
        functional_rows=functional,
    )
    monkeypatch.setattr(pe, "PINNED_ZIP_SHA256", _sha256(zip_path.read_bytes()))
    with pytest.raises(pe.PairedEvidenceError, match="duplicate graded row"):
        pe.ingest_paired_evidence(zip_path=zip_path, corpus_path=matrix_corpus)


def test_wrong_archive_prefix_fails(tmp_path, matrix_corpus, monkeypatch):
    corpus_fp = runner.corpus_fingerprint(matrix_corpus)
    members = {
        "wrong/set3_final/bayes_manifest.json": _manifest(
            selected_ids=["demo.both"],
            pairs=[_pair("demo.both")],
            corpus_fp=corpus_fp,
        ),
    }
    zip_path = tmp_path / "bad-prefix.zip"
    _write_zip(zip_path, members)
    monkeypatch.setattr(pe, "PINNED_ZIP_SHA256", _sha256(zip_path.read_bytes()))
    with pytest.raises(pe.PairedEvidenceError, match="missing required members"):
        pe.ingest_paired_evidence(zip_path=zip_path, corpus_path=matrix_corpus)


def test_stale_corpus_query_text_fails(tmp_path, matrix_corpus, monkeypatch):
    graded, functional = _standard_rows(
        "demo.both",
        ns_ok=True,
        cc_ok=True,
        query_text="Both succeed question",
    )
    functional[0]["query_text"] = "stale wording"
    zip_path = _build_fixture_zip(
        tmp_path,
        corpus_path=matrix_corpus,
        selected_ids=["demo.both"],
        pairs=[_pair("demo.both")],
        graded_rows=graded,
        functional_rows=functional,
    )
    monkeypatch.setattr(pe, "PINNED_ZIP_SHA256", _sha256(zip_path.read_bytes()))
    with pytest.raises(pe.PairedEvidenceError, match="query_text drift"):
        pe.ingest_paired_evidence(zip_path=zip_path, corpus_path=matrix_corpus)


def test_stale_corpus_family_fails(tmp_path, matrix_corpus, monkeypatch):
    graded, functional = _standard_rows(
        "demo.both",
        ns_ok=True,
        cc_ok=True,
        query_text="Both succeed question",
    )
    graded[0]["task_family"] = "catalog_browse"
    zip_path = _build_fixture_zip(
        tmp_path,
        corpus_path=matrix_corpus,
        selected_ids=["demo.both"],
        pairs=[_pair("demo.both")],
        graded_rows=graded,
        functional_rows=functional,
    )
    monkeypatch.setattr(pe, "PINNED_ZIP_SHA256", _sha256(zip_path.read_bytes()))
    with pytest.raises(pe.PairedEvidenceError, match="task_family mismatch"):
        pe.ingest_paired_evidence(zip_path=zip_path, corpus_path=matrix_corpus)


def test_strict_boolean_parsing_rejects_truthy_strings():
    assert pe.parse_strict_bool("false", field="x") is False
    with pytest.raises(pe.PairedEvidenceError):
        pe.parse_strict_bool("maybe", field="x")
    assert pe.arm_success(
        {
            "image": "nextseek_query",
            "answer_provided": "false",
            "is_error": "false",
            "timed_out": "false",
            "runtime_success": "true",
            "human_success": "true",
            "llm_success": "",
        },
        forced_image="nextseek_query",
    ) is False


def test_independent_oracle_recomputes_outcomes(tmp_path, matrix_corpus, monkeypatch):
    selected = ["demo.ns_only", "demo.cc_only", "demo.both", "demo.neither"]
    graded: list[dict[str, str]] = []
    functional: list[dict[str, str]] = []
    scenarios = {
        "demo.ns_only": (True, False),
        "demo.cc_only": (False, True),
        "demo.both": (True, True),
        "demo.neither": (False, False),
    }
    texts = {
        "demo.ns_only": "NS only question",
        "demo.cc_only": "CC only question",
        "demo.both": "Both succeed question",
        "demo.neither": "Neither succeed question",
    }
    for query_id, (ns_ok, cc_ok) in scenarios.items():
        g, f = _standard_rows(
            query_id,
            ns_ok=ns_ok,
            cc_ok=cc_ok,
            query_text=texts[query_id],
        )
        graded.extend(g)
        functional.extend(f)
    payload = _ingest_with_pinned_sha(
        tmp_path,
        matrix_corpus,
        selected_ids=selected,
        pairs=[_pair(query_id) for query_id in selected],
        graded_rows=graded,
        functional_rows=functional,
        monkeypatch=monkeypatch,
    )
    recomputed = _independent_recompute(payload)
    for left, right in zip(payload["records"], recomputed["records"]):
        assert left["ns"]["success"] == right["ns"]["success"]
        assert left["cc"]["success"] == right["cc"]["success"]
    assert payload["audit"] == recomputed["audit"]


def test_committed_evidence_has_no_final_answer_or_transcript_fields():
    payload = pe.load_committed_evidence()
    blob = json.dumps(payload)
    assert "final_answer" not in blob
    assert "session.jsonl" not in blob
    assert "transcript" not in blob


def test_source_check_fails_on_zip_byte_mismatch(tmp_path, matrix_corpus, monkeypatch):
    graded, functional = _standard_rows(
        "demo.both",
        ns_ok=True,
        cc_ok=True,
        query_text="Both succeed question",
    )
    zip_path = _build_fixture_zip(
        tmp_path,
        corpus_path=matrix_corpus,
        selected_ids=["demo.both"],
        pairs=[_pair("demo.both")],
        graded_rows=graded,
        functional_rows=functional,
    )
    monkeypatch.setattr(pe, "PINNED_ZIP_SHA256", "0" * 64)
    with pytest.raises(pe.PairedEvidenceError, match="zip sha256 mismatch"):
        pe.ingest_paired_evidence(zip_path=zip_path, corpus_path=matrix_corpus)


@pytest.mark.real_zip
def test_pinned_real_zip_streams_once_within_sixty_seconds():
    if not PINNED_ZIP.is_file():
        pytest.fail(f"pinned paired source zip is missing: {PINNED_ZIP}")
    payload = pe.ingest_paired_evidence(zip_path=PINNED_ZIP, corpus_path=DEFAULT_CORPUS)
    assert payload["source"]["zip_sha256"] == pe.PINNED_ZIP_SHA256
    assert len(payload["selected_ids"]) == 149
    assert len(payload["records"]) == 149
    assert set(payload["audit"]) == {
        "ns_only",
        "cc_only",
        "both_success",
        "neither_success",
    }
    partition = sum(len(payload["audit"][key]) for key in payload["audit"])
    assert partition == 149
    recomputed = _independent_recompute(payload)
    assert recomputed["audit"] == payload["audit"]


def test_check_without_external_zip_validates_committed_only(
    tmp_path, monkeypatch
):
    missing = tmp_path / "missing.zip"
    monkeypatch.setattr(pe, "DEFAULT_ZIP_PATH", missing)
    pe.check_export()


def test_mutation_selected_id_reorder_fails(tmp_path, matrix_corpus, monkeypatch):
    payload = _ingest_with_pinned_sha(
        tmp_path,
        matrix_corpus,
        selected_ids=["demo.both", "demo.ns_only"],
        pairs=[_pair("demo.both"), _pair("demo.ns_only")],
        graded_rows=_standard_rows(
            "demo.both", ns_ok=True, cc_ok=True, query_text="Both succeed question"
        )[0]
        + _standard_rows(
            "demo.ns_only", ns_ok=True, cc_ok=False, query_text="NS only question"
        )[0],
        functional_rows=_standard_rows(
            "demo.both", ns_ok=True, cc_ok=True, query_text="Both succeed question"
        )[1]
        + _standard_rows(
            "demo.ns_only", ns_ok=True, cc_ok=False, query_text="NS only question"
        )[1],
        monkeypatch=monkeypatch,
    )
    mutated = json.loads(json.dumps(payload))
    mutated["selected_ids"] = ["demo.ns_only", "demo.both"]
    with pytest.raises(pe.PairedEvidenceError, match="record ids must equal"):
        pe.validate_committed_structure(mutated)


def test_mutation_manifest_route_fails(tmp_path, matrix_corpus, monkeypatch):
    zip_path = _build_fixture_zip(
        tmp_path,
        corpus_path=matrix_corpus,
        selected_ids=["demo.both"],
        pairs=[_pair("demo.both", ns_route="container_cc")],
        graded_rows=_standard_rows(
            "demo.both", ns_ok=True, cc_ok=True, query_text="Both succeed question"
        )[0],
        functional_rows=_standard_rows(
            "demo.both", ns_ok=True, cc_ok=True, query_text="Both succeed question"
        )[1],
    )
    monkeypatch.setattr(pe, "PINNED_ZIP_SHA256", _sha256(zip_path.read_bytes()))
    with pytest.raises(pe.PairedEvidenceError, match="ns.route must be"):
        pe.ingest_paired_evidence(zip_path=zip_path, corpus_path=matrix_corpus)


def test_ensure_real_e2e_catalog_reloads_poisoned_namespace(monkeypatch):
    import sys
    import types

    stub = types.ModuleType("e2e.catalog")
    monkeypatch.setitem(sys.modules, "e2e.catalog", stub)
    pe.ensure_real_e2e_catalog()
    from e2e.catalog import load_catalog
    assert callable(load_catalog)


def test_paired_cli_check_and_write(tmp_path, monkeypatch):
    assert pe.main(["--check"]) == 0
    monkeypatch.setattr(pe, "ingest_paired_evidence", lambda **k: {"ok": True})
    monkeypatch.setattr(pe, "canonical_evidence_bytes", lambda payload: b'{"ok":true}\n')
    out = tmp_path / "route_example_evidence.json"
    rendered = pe.write_export(zip_path=tmp_path / "z.zip", evidence_path=out)
    assert rendered == b'{"ok":true}\n'
    assert out.read_bytes() == rendered
    assert pe.main([
        "--write",
        "--zip-path", str(tmp_path / "z.zip"),
        "--evidence-path", str(out),
        "--corpus-path", str(tmp_path / "corpus.json"),
    ]) == 0


def test_check_export_zip_present_differs(tmp_path, monkeypatch):
    committed = pe.load_committed_evidence()
    ev = tmp_path / "e.json"
    ev.write_bytes(pe.canonical_evidence_bytes(committed))
    z = tmp_path / "present.zip"
    z.write_bytes(b"zip")
    monkeypatch.setattr(pe, "ingest_paired_evidence", lambda **k: {"other": 1})
    with pytest.raises(SystemExit, match="differs from source ingest"):
        pe.check_export(evidence_path=ev, zip_path=z, corpus_path=tmp_path)

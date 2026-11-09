"""Public report types (GEO / SRA / PRIDE) strip the lab's internal notes before the coder and writer see the
metadata, keep the full metadata in the user's download, scrub the writer's cells, and carry the do-not-publish
warning in the result data and the reply. Made-up records only."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from chat_nextseek.reports import outputs
from chat_nextseek.schemas import ReportWriterOutputGEO

BUNDLE = {"ok": True, "data": {"data": [{"sample_type": "D.SEQ", "samples": [
    {"uuid": "D.1", "metadata": {"UID": "D.1", "Name": "libA", "Notes": "Lower quality, do not publish", "ShipNotes": "box 3"}},
    {"uuid": "D.2", "metadata": {"UID": "D.2", "Name": "libB", "Notes": "use this one"}},
]}]}}


@pytest.fixture
def flow(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr(outputs, "fetch_reporter_metadata", lambda config, uids: json.loads(json.dumps(BUNDLE)))
    monkeypatch.setattr(outputs, "annotate_metadata_with_sampletypes", lambda config, md: md)
    monkeypatch.setattr(outputs, "extract_protocol_refs_from_metadata", lambda md: [])
    monkeypatch.setattr(outputs, "load_report_template", lambda config, t: {})

    def fake_produce(**kw):
        seen.update(kw)
        return SimpleNamespace(model_dump=lambda: {"report_type": "x", "report": {"samples": []}})

    monkeypatch.setattr(outputs, "_produce_report_output", fake_produce)

    def run(report_type):
        out = outputs.generate_report_outputs(
            config=SimpleNamespace(BASE_DIR=str(tmp_path), SEQ_TEMPLATE_PATH=str(tmp_path / "none.xlsx")),
            user_query="q", parser_plan={}, reporter_plan=SimpleNamespace(report_type=report_type),
            uids=["D.1", "D.2"], log_dir=tmp_path, report_writer_fn=lambda *a: None, per_sample_reports=False)
        return seen, out, tmp_path

    return run


@pytest.mark.parametrize("report_type", ["GEO", "SRA", "PRIDE"])
def test_public_types_strip_notes_before_the_coder_and_warn(flow, report_type):
    seen, (reporter_result, _, saved_files, reply), tmp = flow(report_type)
    metas = [s["metadata"] for s in seen["metadata"]["data"]["data"][0]["samples"]]
    assert all("Notes" not in m and "ShipNotes" not in m for m in metas) and len(metas) == 2
    assert "Lower quality, do not publish" in seen["internal_notes"]
    warning = reporter_result["warnings"][0]
    assert '1 libraries are marked "do not publish"' in warning and "libA" in warning
    assert warning in reply
    # the user's metadata download stays complete
    saved = json.loads(open(saved_files["metadata"]).read())
    persisted = [s["metadata"] for s in next(iter(saved.values()))["data"]["data"][0]["samples"]]
    assert persisted[0]["Notes"] == "Lower quality, do not publish"


def test_no_do_not_publish_note_means_no_warning(flow, monkeypatch):
    monkeypatch.setattr(outputs, "fetch_reporter_metadata", lambda c, u: {"ok": True, "data": {"data": [
        {"sample_type": "D.SEQ", "samples": [{"uuid": "D.9", "metadata": {"Name": "libZ", "Notes": "fine"}}]}]}})
    _, (reporter_result, *_), _ = flow("GEO")
    assert not reporter_result.get("warnings")


def test_writer_path_cells_are_scrubbed():
    md = {"data": {"data": [{"sample_type": "D.SEQ", "samples": [{"metadata": {"UID": "D.1"}}]}]}}
    writer = MagicMock(return_value=ReportWriterOutputGEO(
        report={"samples": [{"description": "Arm: A; Lab-only remark here; SRA run: Lab_7"}]}))
    out = outputs._produce_report_output(
        config=MagicMock(), user_query="q", report_type_value="GEO", reporter_context={},
        template_for_llm={"samples": [{}]}, metadata=md, report_writer_fn=writer,
        report_coder_fn=MagicMock(), log_dir="/tmp", internal_notes=["Lab-only remark here"])
    assert out.report.samples[0]["description"] == "Arm: A"

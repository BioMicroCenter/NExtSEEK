"""Build step: hands resolution+groupby to generate_report_outputs and
renders artifacts."""
from unittest.mock import MagicMock, patch

from chat_nextseek.pipeline import agent as pipeline_agent


def _state_ready_to_build():
    return {
        "active": True,
        "phase": "directive_parse",
        "original_query": "run rnaseq on NHP-1, group by exposure",
        "directive": {
            "pipeline_key": "rnaseq",
            "samples_ref": {"kind": "explicit_uids", "uids": ["NHP-1"]},
            "group_by_phrase": "exposure",
        },
        "resolution": {
            "source_uids": ["NHP-1"],
            "leaves_filtered": [
                {"uid": "D.SEQ-1", "assay": "RNA-seq"},
                {"uid": "D.SEQ-2", "assay": "RNA-seq"},
            ],
            "source_uids_with_no_leaves": [],
            "dropped_by_assay_mismatch": [],
        },
        "sanity": {"verdict": "proceed_with_subset", "leaves_to_use": ["D.SEQ-1", "D.SEQ-2"],
                   "dropped_leaves_summary": ""},
        "groupby": {
            "field": {"sample_type": "NHP", "field_name": "Treatment1"},
            "distinct_values": ["NDMA", "vehicle"],
            "rationale": "exposure → Treatment1",
        },
        "metadata_bundle": {"data": []},
    }


def test_build_step_calls_generate_report_outputs_with_cohorts():
    session = {"pipeline_agent": _state_ready_to_build()}
    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return (
            {"raw_result_path": "/tmp/r.json"},  # reporter_result
            MagicMock(model_dump=lambda: {"report_type": "NFCORE_RNASEQ"}),  # report_writer_output
            {"samplesheet": "/tmp/run/samplesheet.csv", "launch_yml": "/tmp/run/launch.yml"},
            "Built it.",
        )

    with patch("chat_nextseek.pipeline.agent.generate_report_outputs",
               side_effect=fake_generate):
        out = pipeline_agent._run_build_step(session, MagicMock(), log_dir=None)
    assert out["action"] == "ask"
    assert captured["parser_plan"].report_type == "NFCORE_RNASEQ"
    assert captured["reporter_plan"].report_type == "NFCORE_RNASEQ"
    assert captured["uids"] == ["D.SEQ-1", "D.SEQ-2"]
    cohorts = captured["pre_supplied_cohorts"]
    assert len(cohorts) == 2
    assert {c["cohort_criterion"]["Treatment1"] for c in cohorts} == {"NDMA", "vehicle"}
    state = session["pipeline_agent"]
    assert state["phase"] == "awaiting_validation"
    assert state["build_artifacts"]["samplesheet"] == "/tmp/run/samplesheet.csv"


def test_build_step_single_cohort_when_no_groupby():
    state = _state_ready_to_build()
    state["groupby"] = None
    state["directive"]["group_by_phrase"] = None
    session = {"pipeline_agent": state}
    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return ({}, MagicMock(model_dump=lambda: {}), {}, "")

    with patch("chat_nextseek.pipeline.agent.generate_report_outputs",
               side_effect=fake_generate):
        pipeline_agent._run_build_step(session, MagicMock(), log_dir=None)
    cohorts = captured["pre_supplied_cohorts"]
    assert len(cohorts) == 1


def test_build_step_handles_accession_pipeline():
    state = _state_ready_to_build()
    state["directive"]["pipeline_key"] = "fetchngs"
    state["resolution"] = {
        "source_uids": [], "accessions": ["SRR1", "SRR2"],
        "leaves_filtered": [],
    }
    state["sanity"] = {"verdict": "proceed", "leaves_to_use": ["SRR1", "SRR2"]}
    session = {"pipeline_agent": state}
    captured = {}

    def fake_generate(**kwargs):
        captured.update(kwargs)
        return ({}, MagicMock(model_dump=lambda: {}), {}, "")

    with patch("chat_nextseek.pipeline.agent.generate_report_outputs",
               side_effect=fake_generate):
        pipeline_agent._run_build_step(session, MagicMock(), log_dir=None)
    assert captured["parser_plan"].report_type == "NFCORE_FETCHNGS"
    assert captured["reporter_plan"].report_type == "NFCORE_FETCHNGS"
    assert captured["uids"] == ["SRR1", "SRR2"]


def test_build_step_populates_samplesheet_rows_from_emitted_csv(tmp_path):
    """Regression: Task 10's edit step reads state['samplesheet_rows'] — must
    be populated by Task 9 from the emitted CSV, otherwise edits silently no-op."""
    csv_path = tmp_path / "samplesheet.csv"
    csv_path.write_text(
        "sample,fastq_1,fastq_2,strandedness\n"
        "S1,f1.fq,f2.fq,auto\n"
        "S2,f1.fq,f2.fq,auto\n",
        encoding="utf-8",
    )
    session = {"pipeline_agent": _state_ready_to_build()}

    def fake_generate(**kwargs):
        return ({}, MagicMock(model_dump=lambda: {}), {"samplesheet": str(csv_path)}, "")

    with patch("chat_nextseek.pipeline.agent.generate_report_outputs",
               side_effect=fake_generate):
        pipeline_agent._run_build_step(session, MagicMock(), log_dir=None)
    rows = session["pipeline_agent"]["samplesheet_rows"]
    assert len(rows) == 2
    assert {r["sample"] for r in rows} == {"S1", "S2"}


def test_build_step_samplesheet_rows_empty_when_no_csv_path():
    """When the emitter returns no samplesheet path, samplesheet_rows is []."""
    session = {"pipeline_agent": _state_ready_to_build()}

    def fake_generate(**kwargs):
        return ({}, MagicMock(model_dump=lambda: {}), {}, "")

    with patch("chat_nextseek.pipeline.agent.generate_report_outputs",
               side_effect=fake_generate):
        pipeline_agent._run_build_step(session, MagicMock(), log_dir=None)
    assert session["pipeline_agent"]["samplesheet_rows"] == []

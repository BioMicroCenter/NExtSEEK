"""Edit step: applies a user edit message to the in-memory samplesheet."""
from unittest.mock import MagicMock, patch

from chat_nextseek import pipeline_agent
from chat_nextseek.schemas.pipeline import EditDiffOutput


def _state_at_validation():
    return {
        "active": True,
        "phase": "awaiting_validation",
        "directive": {"pipeline_key": "rnaseq"},
        "samplesheet_rows": [
            {"sample": "S1", "fastq_1": "f1.fq", "fastq_2": "f2.fq", "strandedness": "auto"},
            {"sample": "S2", "fastq_1": "f1.fq", "fastq_2": "f2.fq", "strandedness": "auto"},
            {"sample": "S3", "fastq_1": "f1.fq", "fastq_2": "f2.fq", "strandedness": "auto"},
        ],
        "build_artifacts": {"samplesheet": "/tmp/run/samplesheet.csv"},
    }


def test_edit_apply_re_emits_artifacts():
    session = {"pipeline_agent": _state_at_validation()}
    with patch("chat_nextseek.pipeline_agent._pipeline_edit_step") as edit, \
         patch("chat_nextseek.pipeline_agent._re_emit_samplesheet",
               return_value={"samplesheet": "/tmp/run/samplesheet.csv"}):
        edit.return_value = EditDiffOutput(
            action="apply",
            rows=[
                {"sample": "S1", "fastq_1": "f1.fq", "fastq_2": "f2.fq", "strandedness": "auto"},
                {"sample": "S3", "fastq_1": "f1.fq", "fastq_2": "f2.fq", "strandedness": "auto"},
            ],
        )
        out = pipeline_agent._handle_edit(session, MagicMock(), "drop S2", log_dir=None)
    assert out["action"] == "ask"
    assert len(session["pipeline_agent"]["samplesheet_rows"]) == 2
    assert {r["sample"] for r in session["pipeline_agent"]["samplesheet_rows"]} == {"S1", "S3"}
    assert session["pipeline_agent"]["phase"] == "awaiting_validation"


def test_edit_ask_pauses_for_clarification():
    session = {"pipeline_agent": _state_at_validation()}
    with patch("chat_nextseek.pipeline_agent._pipeline_edit_step") as edit:
        edit.return_value = EditDiffOutput(
            action="ask",
            ask_reply="Which samples are 'the bad ones'?",
        )
        out = pipeline_agent._handle_edit(session, MagicMock(), "drop the bad ones", log_dir=None)
    assert "the bad ones" in out["reply"].lower()
    assert len(session["pipeline_agent"]["samplesheet_rows"]) == 3  # unchanged


def test_edit_reject_keeps_state_and_explains():
    session = {"pipeline_agent": _state_at_validation()}
    with patch("chat_nextseek.pipeline_agent._pipeline_edit_step") as edit:
        edit.return_value = EditDiffOutput(
            action="reject",
            reject_reason="SAMPLE-999 isn't in the current set.",
        )
        out = pipeline_agent._handle_edit(session, MagicMock(), "add SAMPLE-999", log_dir=None)
    assert "sample-999" in out["reply"].lower()
    assert len(session["pipeline_agent"]["samplesheet_rows"]) == 3

"""Pipeline-agent Pydantic schemas — round-trip and validation checks."""
from chat_nextseek.schemas.pipeline import EditDiffOutput


def test_edit_diff_apply_shape():
    out = EditDiffOutput(
        action="apply",
        rows=[{"sample": "S1", "fastq_1": "..."}, {"sample": "S2", "fastq_1": "..."}],
    )
    assert out.action == "apply"
    assert len(out.rows) == 2


def test_edit_diff_reject_shape():
    out = EditDiffOutput(action="reject", reject_reason="unknown UID")
    assert out.reject_reason == "unknown UID"

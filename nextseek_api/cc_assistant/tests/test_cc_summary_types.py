"""Assert the vendored BAML SessionSummary contract (Step 1c). Shape only, no network."""
import pytest

types = pytest.importorskip("dmac_assistant.router.baml_client.types")


def test_session_summary_fields():
    fields = set(types.SessionSummary.model_fields)
    assert {
        "chat_session_id", "claude_session_id", "transcript_path",
        "transcript_line_count", "turn_count", "chat_model", "gist",
        "items", "writer", "summary_model", "schema_version", "generated_at",
    } <= fields


def test_memory_item_and_evidence_fields():
    assert {"category", "statement", "evidence", "confidence"} <= set(types.MemoryItem.model_fields)
    assert {"line_start", "line_end", "quote", "verified"} <= set(types.EvidenceRef.model_fields)


def test_summarize_input_fields():
    assert {"chat_session_id", "chat_model", "actions_view"} <= set(types.SummarizeInput.model_fields)


def test_enums_have_expected_members():
    cats = {m.value.lower() for m in types.MemoryCategory}
    assert {"preference", "context", "artifact", "decision", "todo", "fact"} <= cats
    confs = {m.value.lower() for m in types.Confidence}
    assert {"high", "medium", "low"} <= confs


def test_can_construct_minimal_summary():
    ev = types.EvidenceRef(
        line_start=1, line_end=1,
        quote=types.Checked(value="x", checks={}),
        verified=False,
    )
    item = types.MemoryItem(
        category=types.MemoryCategory.Decision, statement="s", evidence=[ev],
        confidence=types.Confidence.High,
    )
    summ = types.SessionSummary(
        chat_session_id="S1", claude_session_id=None, transcript_path="/t.jsonl",
        transcript_line_count=1, turn_count=1, chat_model="m", gist="g",
        items=[item], writer="fallback_actions", summary_model="none",
        schema_version="1c/v1", generated_at="2026-06-29T00:00:00Z",
    )
    assert summ.items[0].evidence[0].verified is False

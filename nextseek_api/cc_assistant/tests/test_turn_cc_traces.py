"""Turn carries cc_traces through model_dump (extra='forbid' requires the field)."""
from nextseek_api.assistant.models_api import Turn


def test_turn_accepts_and_dumps_cc_traces():
    t = Turn(bundle_id=0, user_query="hi", reply="ok", mode="cc",
             cc_traces=[{"cc_session_id": "s", "ts": "t",
                         "steps": [{"line": 2, "kind": "bash", "tool": "Bash", "detail": "ls"}]}])
    d = t.model_dump(mode="json")
    assert d["cc_traces"][0]["steps"][0]["detail"] == "ls"


def test_turn_cc_traces_defaults_none():
    t = Turn(bundle_id=0, user_query="hi", reply="ok", mode="cc")
    assert t.model_dump(mode="json")["cc_traces"] is None


def test_projection_passes_cc_traces_through():
    """Hermetic guard for the Step 4 reload wiring in services/assistant.py.
    The Turn projection (assistant.py:521-529) MUST pass the chat_log entry's
    persisted trace onto the Turn (`cc_traces=entry.get("cc_traces")`); without it,
    reload silently returns NO traces and only the paid Task 13 live gate catches it.
    The projection lives inside the DRF `get_session` @action and is not callable
    without a DB, so this is a source-text guard (same pattern as the Task 11a
    `assistant_reply` grep guard). MUTATION-SENSITIVE: deleting the passthrough line
    removes the substring and FAILS this assertion."""
    from pathlib import Path
    src = (Path(__file__).parents[2] / "services" / "assistant.py").read_text()
    assert 'cc_traces=entry.get("cc_traces")' in src   # Step 4 passthrough is wired

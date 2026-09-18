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
    """Hermetic guard for the Step 4 reload wiring.
    The Turn projection MUST pass the chat_log entry's persisted trace onto the
    Turn; without it, reload silently returns NO traces and only the paid Task 13
    live gate catches it. The projection is ``session_export.turn_rows``, a plain
    function of the session row that needs no DB, so it is called here rather
    than grepped for. MUTATION-SENSITIVE: dropping the passthrough leaves
    ``cc_traces`` None and FAILS this assertion."""
    from types import SimpleNamespace

    from nextseek_api.assistant.session_export import turn_rows

    trace = [{"cc_session_id": "s", "ts": "t",
              "steps": [{"line": 2, "kind": "bash", "tool": "Bash", "detail": "ls"}]}]
    session = SimpleNamespace(results_history=[], extra_state={"chat_log": [
        {"turn_id": 1, "user_query": "hi", "assistant_reply": "ok", "mode": "cc",
         "cc_traces": trace},
    ]})

    (row,) = turn_rows(session)

    assert row.payload["cc_traces"][0]["steps"][0]["detail"] == "ls"


def test_reload_serves_turns_from_the_shared_projection():
    """``GET /assistant/sessions/{sid}/?include=turns`` must build its turns with
    ``turn_rows`` (the test above) and not with a second, inline walk that could
    drop the passthrough again. Source-text guard, as the @action needs a DB.
    MUTATION-SENSITIVE: an inline walk in ``get_session`` FAILS this assertion."""
    from NessieAI import paths
    src = (paths.REPO_ROOT / "nextseek_api" / "services" / "assistant.py").read_text()
    assert "session_export.turn_rows(session)" in src

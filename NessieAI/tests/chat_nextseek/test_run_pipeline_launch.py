import pytest

import chat_nextseek.orchestrator as orch


def test_start_accepts_message_without_plans(monkeypatch):
    """pipeline_agent.start must work with parser_plan/reporter_plan omitted."""
    import chat_nextseek.pipeline.agent as pa
    captured = {}

    def fake_run_loop(session, config, *, log_dir, send_event=None):
        captured["messages"] = session["pipeline_agent"]["messages"]
        return {"action": "ask", "reply": "ok", "params": None}

    monkeypatch.setattr(pa, "_run_loop", fake_run_loop)
    session = {}
    out = pa.start(session, object(), user_query="Launch scrnaseq on D.SEQ-1")
    assert out["reply"] == "ok"
    assert captured["messages"][0]["content"].startswith("Launch scrnaseq")


def test_run_pipeline_launch_seeds_and_emits(monkeypatch, tmp_path):
    """Directly starts the wizard from a message, records the turn, emits query_complete."""
    def fake_start(session, config, *, user_query, log_dir=None, **kw):
        session["pipeline_agent"] = {
            "active": True,
            "pipeline_key": "scrnaseq",
            "artifacts": {"cohorts": [{"uids": ["D.SEQ-1"]}]},
            "messages": [{"role": "user", "content": user_query}],
        }
        return {"action": "ask", "reply": f"Proposing scrnaseq for {user_query[:12]}"}

    monkeypatch.setattr(orch.pipeline_agent, "start", fake_start)

    events = []
    session = {"log_dir": str(tmp_path)}  # preset so _ensure_query_log_dir is a no-op
    out = orch.run_pipeline_launch(
        session, object(), "Launch scrnaseq on D.SEQ-1",
        send_event=lambda name, data: events.append((name, data)),
    )

    assert session["pipeline_agent"]["active"] is True
    assert out["reply"].startswith("Proposing scrnaseq")
    assert out["bundle_id"] is None
    assert events[-1][0] == "query_complete"
    assert events[-1][1]["reply"] == out["reply"]
    assert session["chat_log"][-1]["mode"] == "pipeline_agent"
    assert session["chat_log"][-1]["wizard_state"]["pipeline_key"] == "scrnaseq"


def _open_build():
    return {"active": True, "pipeline_key": "rnaseq", "artifacts": {"launch": "/x/launch.yml"},
            "messages": [{"role": "user", "content": "build it"},
                         {"role": "assistant", "content": [{"type": "text", "text": "Reply submit"}]}]}


def test_the_bridge_continues_an_open_build_instead_of_wiping_it(monkeypatch, tmp_path):
    """Prod chat e80cee5a: every CC relay called start(), so "submit" found nothing built."""
    import chat_nextseek.pipeline.agent as pa
    seen = {}

    def fake_run_loop(session, config, *, log_dir, send_event=None):
        seen["state"] = session["pipeline_agent"]
        return {"action": "ask", "reply": "kept", "params": None}

    monkeypatch.setattr(pa, "_run_loop", fake_run_loop)
    session = {"log_dir": str(tmp_path), "pipeline_agent": _open_build()}
    out = orch.run_pipeline_launch(session, object(), "The user replied: submit", send_event=lambda *a: None)
    assert out["reply"] == "kept"
    assert seen["state"]["artifacts"] == {"launch": "/x/launch.yml"} and seen["state"]["pipeline_key"] == "rnaseq"
    assert [m["content"] for m in seen["state"]["messages"]][-1] == "The user replied: submit"
    assert len(seen["state"]["messages"]) == 3


def test_the_bridge_starts_fresh_when_the_open_build_hands_off(monkeypatch, tmp_path):
    """CC asks for a new run on other samples: the wizard lets go of the old one and the new one starts."""
    import chat_nextseek.pipeline.agent as pa
    monkeypatch.setattr(pa, "handle_turn", lambda *a, **k: {"action": "passthrough", "reply": "", "params": None})
    monkeypatch.setattr(pa, "start", lambda session, config, *, user_query, **k: {"action": "ask", "reply": "fresh"})
    session = {"log_dir": str(tmp_path), "pipeline_agent": _open_build()}
    assert orch.run_pipeline_launch(session, object(), "Launch sarek on D.SEQ-9",
                                    send_event=lambda *a: None)["reply"] == "fresh"


def _turn(monkeypatch, tmp_path):
    monkeypatch.setattr(orch, "_identity_gate", lambda session, config, *a, **k: (config, None))
    monkeypatch.setattr(orch, "_ensure_query_log_dir", lambda session, config: str(tmp_path))
    monkeypatch.setattr(orch, "ArtifactStore", lambda log_dir: None)
    monkeypatch.setattr(orch, "_accepted_suggestion", lambda session, text: None)
    # Past the wizard the turn runs the parser path: this stands in for its first step.
    monkeypatch.setattr(orch, "_chip_rerun", lambda chip: (_ for _ in ()).throw(AssertionError("reached the parser")))
    monkeypatch.setattr(orch.pipeline_agent, "handle_turn",
                        lambda session, *a, **k: (session["pipeline_agent"].clear(),
                                                  {"action": "passthrough", "reply": "", "params": None})[1])


def test_run_query_gives_a_turn_back_when_the_wizard_hands_off_and_the_router_said_cc(monkeypatch, tmp_path):
    _turn(monkeypatch, tmp_path)
    session = {"pipeline_agent": _open_build()}
    out = orch.run_query(session, object(), "plot the species", lambda *a: None, stop_on_handoff=True)
    assert out["handed_off"] is True and "total_cost_usd" in out  # the wizard's spend, which cc/turn.py adds to the turn


def test_run_query_with_no_build_open_is_not_a_handoff(monkeypatch, tmp_path):
    """An inactive wizard is no handoff, whatever the flag: the turn carries on down the parser path."""
    _turn(monkeypatch, tmp_path)
    monkeypatch.setattr(orch, "_chip_rerun", lambda chip: (_ for _ in ()).throw(RuntimeError("parser path")))
    session = {"pipeline_agent": {}}
    with pytest.raises(RuntimeError, match="parser path"):
        orch.run_query(session, object(), "plot the species", lambda *a: None, stop_on_handoff=True)


def test_a_relay_in_the_same_cc_turn_is_not_the_users_reply(monkeypatch, tmp_path):
    """CC must not be able to confirm its own run: only a later turn, after the user answered, counts as a reply."""
    import chat_nextseek.pipeline.agent as pa
    from chat_nextseek.pipeline.agent_tools import _launch_unconfirmed, _user_msg_count
    monkeypatch.setattr(pa, "_run_loop", lambda *a, **k: {"action": "ask", "reply": "ok", "params": None})

    session = {"log_dir": str(tmp_path)}
    orch.run_pipeline_launch(session, object(), "Build rnaseq on D.SEQ-1", send_event=lambda *a: None, relay_turn="cc-1")
    state = session["pipeline_agent"]
    state["launch_built_at_user_msgs"] = _user_msg_count(state)
    state["artifacts"]["launch"] = "/x"

    orch.run_pipeline_launch(session, object(), "The user wants it sent now", send_event=lambda *a: None, relay_turn="cc-1")
    assert _launch_unconfirmed(session["pipeline_agent"]) is not None

    orch.run_pipeline_launch(session, object(), "The user replied: submit", send_event=lambda *a: None, relay_turn="cc-2")
    assert _launch_unconfirmed(session["pipeline_agent"]) is None


def test_a_passthrough_leaves_the_clearing_to_the_agent(monkeypatch, tmp_path):
    """A kept build (a side question over a built run) must survive the orchestrator's passthrough."""
    monkeypatch.setattr(orch.pipeline_agent, "handle_turn",
                        lambda *a, **k: {"action": "passthrough", "reply": "", "params": None})
    session = {"pipeline_agent": _open_build()}
    assert orch._handle_pipeline_agent_turn(session, object(), "side question", str(tmp_path), None, None) is None
    assert session["pipeline_agent"]["active"] is True

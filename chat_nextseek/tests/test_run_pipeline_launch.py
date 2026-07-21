import chat_nextseek.orchestrator as orch


def test_start_accepts_message_without_plans(monkeypatch):
    """pipeline_agent.start must work with parser_plan/reporter_plan omitted."""
    import chat_nextseek.pipeline.agent as pa
    captured = {}

    def fake_run_loop(session, config, *, log_dir):
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

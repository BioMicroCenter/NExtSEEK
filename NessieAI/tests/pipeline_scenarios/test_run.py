"""run_story with a stubbed router, a scripted agent model and stubbed I/O: no model, no stack."""
import json
from types import SimpleNamespace

import pytest

from NessieAI.tests.pipeline_scenarios import run as rn, scrub
from NessieAI.tests.pipeline_scenarios.stories import Story
from NessieAI.tests.pipeline_scenarios.tape import Tape, TapeMismatch


def _story(**kw):
    base = dict(id="s1", family="bulk_rna", question="Which genes change between the groups?",
                cohort="241219BRY::A.GEX", route="nextseek_query", acceptable=[["rnaseq"]],
                launch={"built": False, "must_match": {}}, why={"route": "r", "pipelines": "p"})
    return Story(**{**base, **kw})


class _Cfg:
    LOG_DIR = "/tmp"

    def get_agent_model(self, label):
        return None, "model-x", None

    def _load_prompt(self, name):
        return "SYSTEM"


def _decide(route="nextseek_query"):
    return lambda q, h: SimpleNamespace(route=route, source="baml", reasoning="r", router_model="m")


def _scripted_agent(monkeypatch, seen=None):
    from chat_nextseek.pipeline import agent as pa
    monkeypatch.setattr(pa, "start", lambda session, config, **k: (
        session.__setitem__("pipeline_agent", {"active": True, "selection": {"verdict": "chosen",
                                                                             "pipelines": ["rnaseq"]}}),
        {"action": "ask", "reply": "Group by Treatment?", "params": None})[1])

    def handle(session, config, text, **k):
        if seen is not None:
            seen.append(text)
        session["pipeline_agent"].update({"pipeline_key": "rnaseq", "artifacts": {"launch": "launch.yml"},
                                          "launch_plan": {"params": {"genome": "GRCh38"}}})
        return {"action": "ask", "reply": "Ready. Submit?", "params": None}
    monkeypatch.setattr(pa, "handle_turn", handle)
    monkeypatch.setattr(rn, "cohort_uids", lambda c: ["D.SEQ-1-PUB"])


def test_an_off_route_story_stops_after_the_router(tmp_path):
    outcome, record = rn.run_story(_story(route="unrelated"), _Cfg(), None, tmp_path, decide=_decide("unrelated"))
    assert outcome.route == "unrelated" and outcome.pipeline_key is None
    assert record["decision"]["route"] == "unrelated"


def test_the_session_holds_the_cohort_as_the_pinned_search():
    s = rn.make_session(_story(), ["D.SEQ-1-PUB", "D.SEQ-2-PUB"])
    bundle = s["results_history"][0]
    assert [r["uuid"] for r in bundle["graph_result"]["data"]] == ["D.SEQ-1-PUB", "D.SEQ-2-PUB"]


def test_the_runner_answers_the_agent_until_it_concludes(tmp_path, monkeypatch):
    seen = []
    _scripted_agent(monkeypatch, seen)
    outcome, record = rn.run_story(_story(), _Cfg(), None, tmp_path, decide=_decide())
    assert seen == [rn.DEFAULT_REPLY]
    assert outcome.pipeline_key == "rnaseq" and outcome.launch_params == {"genome": "GRCh38"}
    assert record["replies"] == ["Group by Treatment?", "Ready. Submit?"]
    assert outcome.uids == ["D.SEQ-1-PUB"] and outcome.replies == record["replies"]


def test_the_story_replies_are_used_before_the_default(tmp_path, monkeypatch):
    seen = []
    _scripted_agent(monkeypatch, seen)
    rn.run_story(_story(replies=["Treatment."]), _Cfg(), None, tmp_path, decide=_decide())
    assert seen == ["Treatment."]


def test_an_ask_story_stops_after_the_agents_first_reply(tmp_path, monkeypatch):
    seen = []
    _scripted_agent(monkeypatch, seen)
    outcome, _ = rn.run_story(_story(expect="ask"), _Cfg(), None, tmp_path, decide=_decide())
    assert seen == [] and outcome.launch_params is None


def test_the_runner_never_offers_a_submit_tool():
    cfg = rn.RunConfig(SimpleNamespace(LURIA_ENV_COMPLETE=True, get_agent_model=lambda l: (None, "m", None)), None)
    from chat_nextseek.pipeline.agent_tools import build_pipeline_tool_schemas
    assert "submit_to_luria" not in {t["name"] for t in build_pipeline_tool_schemas(cfg)}


def test_the_run_config_tapes_the_agent_models_answers():
    from NessieAI.tests.pipeline_scenarios.tape import RecordingClient
    inner = SimpleNamespace(chat=lambda **k: SimpleNamespace(content="x"))
    real = SimpleNamespace(get_agent_model=lambda label: (inner, "m", 1), LOG_DIR="/l")
    client, model, budget = rn.RunConfig(real, Tape(mode="record")).get_agent_model("selection")
    assert isinstance(client, RecordingClient) and (model, budget) == ("m", 1)
    assert rn.RunConfig(real, None).get_agent_model("selection")[0] is inner
    replaying = rn.RunConfig(real, Tape(mode="replay"))
    assert replaying.get_agent_model("selection")[0] is inner
    assert rn.RunConfig(real, None).LOG_DIR == "/l"


def test_live_redacts_people_and_changes_nothing_else():
    md = {"ok": True, "data": {"Scientist": "X", "UID": "U", "Protocol": "https://nextseek.example.org/sops/12",
                               "Notes": "mail a@b.org"}}
    out = rn.live("fetch_reporter_metadata", md)
    assert out["data"] == {"Scientist": "REDACTED", "UID": "U", "Protocol": "https://nextseek.example.org/sops/12",
                           "Notes": "mail a@b.org"}
    text = {"ok": True, "text": "Contact a@b.org at /home/u/x. We used STAR."}
    assert rn.live("gather_protocol_text", text) is text           # no trimming: production reads it whole
    assert rn.live("agent", {"content": "https://nextseek.example.org/x"}) == {"content": "https://nextseek.example.org/x"}


def test_store_redacts_trims_rewrites_and_refuses():
    clean = rn.store("fetch_reporter_metadata", {"ok": True, "data": {"Scientist": "X", "UID": "U"}})
    assert clean["data"]["Scientist"] == "REDACTED"
    with pytest.raises(ValueError, match="email"):
        rn.store("fetch_reporter_metadata", {"ok": True, "data": {"Notes": "mail a@b.org"}})
    out = rn.store("gather_protocol_text", {"ok": True, "text": "Contact a@b.org at /home/u/x. We used STAR."})
    assert "a@b.org" not in out["text"] and "/home" not in out["text"]
    with pytest.raises(ValueError, match="path"):
        rn.store("expand_uid_prefixes", {"note": "first line\n/home/someone/file.txt"})


SRA_ODP = "https://sra-pub-run-odp.s3.amazonaws.com/sra/SRR8615271/SRR8615271"
SRA_SRC = "s3://sra-pub-src-14/SRR8615271/R1.fastq.gz.1"
ENA_FASTQ = "https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR861/001/SRR8615271/SRR8615271_1.fastq.gz"
SOP = "https://nextseek.example.org/sops/12"


def _real_metadata(notes="paired-end"):
    return {"ok": True, "data": [{"metadata": {
        "UID": "D.SEQ-1-PUB", "Scientist": "A Person", "Protocol": SOP, "Notes": notes,
        "Link_PrimaryData": f"{SRA_ODP};{SRA_SRC}", "Link_Other": ENA_FASTQ}}]}


def test_real_cohort_urls_reach_the_live_run_and_only_archive_urls_reach_the_tape(monkeypatch):
    from chat_nextseek.pipeline import agent_tools as at
    from NessieAI.tests.pipeline_scenarios import tape as tp
    monkeypatch.setattr(at, "fetch_reporter_metadata", lambda *a, **k: _real_metadata())
    tape = Tape(mode="record")
    with tp.taped(tape, live=rn.live, store=rn.store):
        seen = at.fetch_reporter_metadata(object(), ["D.SEQ-1-PUB"])
    md = seen["data"][0]["metadata"]
    assert md["Protocol"] == SOP and md["Link_PrimaryData"] == f"{SRA_ODP};{SRA_SRC}" and md["Link_Other"] == ENA_FASTQ
    assert md["Scientist"] == "REDACTED"
    kept = tape.entries["fetch_reporter_metadata"][0]["value"]["data"][0]["metadata"]
    assert kept["Protocol"] == "<url>" and kept["Link_PrimaryData"] == f"{SRA_ODP};{SRA_SRC}"
    assert kept["Link_Other"] == ENA_FASTQ and kept["Scientist"] == "REDACTED" and tape.refused == []


def test_a_path_in_real_metadata_refuses_the_tape_but_not_the_tool(monkeypatch):
    from chat_nextseek.pipeline import agent_tools as at
    from NessieAI.tests.pipeline_scenarios import tape as tp
    monkeypatch.setattr(at, "fetch_reporter_metadata", lambda *a, **k: _real_metadata("see\n/home/someone/x.fq.gz"))
    tape = Tape(mode="record")
    with pytest.raises(ValueError, match="refused.*path"):
        with tp.taped(tape, live=rn.live, store=rn.store):
            seen = at.fetch_reporter_metadata(object(), ["D.SEQ-1-PUB"])      # no exception in the tool
            assert seen["data"][0]["metadata"]["Notes"] == "see\n/home/someone/x.fq.gz"
            assert seen["data"][0]["metadata"]["Protocol"] == SOP
    assert "fetch_reporter_metadata" not in tape.entries


def test_a_replay_that_does_not_match_the_tape_is_not_swallowed(tmp_path, monkeypatch):
    _scripted_agent(monkeypatch)
    tape = Tape(entries={"selection": [{"args": "x", "value": "never played"}]}, mode="replay")
    with pytest.raises(TapeMismatch):
        rn.run_story(_story(), _Cfg(), tape, tmp_path, decide=_decide())


def test_a_recording_the_scrubber_refused_is_not_swallowed(tmp_path, monkeypatch):
    _scripted_agent(monkeypatch)
    tape = Tape(mode="record")
    tape.refused.append("fetch_reporter_metadata: email a@b.org")
    with pytest.raises(ValueError, match="refused"):
        rn.run_story(_story(), _Cfg(), tape, tmp_path, decide=_decide())


def _must_not_be_called(*a, **k):
    raise AssertionError("called without --yes")


def test_main_without_yes_runs_nothing(capsys, monkeypatch):
    import chat_nextseek.config as cc
    story = _story(ruling={"state": "accepted", "date": "d", "note": ""})
    monkeypatch.setattr(rn, "load_stories", lambda: [story])
    monkeypatch.setattr(rn, "cohort_uids", lambda c: ["U"])
    monkeypatch.setattr(cc, "ChatConfig", _must_not_be_called)
    monkeypatch.setattr(rn, "run_story", _must_not_be_called)
    assert rn.main(["--ids", "s1"]) == 0
    assert "Not run" in capsys.readouterr().out


def test_main_says_which_ids_are_unknown_or_unruled(capsys, monkeypatch):
    ruled = _story(id="ok", ruling={"state": "accepted", "date": "d", "note": ""})
    unruled = _story(id="draft")
    monkeypatch.setattr(rn, "load_stories", lambda: [ruled, unruled])
    monkeypatch.setattr(rn, "cohort_uids", lambda c: ["U"])
    assert rn.main(["--ids", "ok,draft,nope"]) == 0
    out = capsys.readouterr().out
    assert "draft: not ruled" in out and "nope: no such story" in out


def test_main_writes_the_run_folder_even_when_a_story_crashes(tmp_path, monkeypatch):
    story = _story(ruling={"state": "accepted", "date": "d", "note": ""})
    monkeypatch.setattr(rn, "load_stories", lambda: [story])
    monkeypatch.setattr(rn, "cohort_uids", lambda c: ["U"])

    def boom(*a, **k):
        raise RuntimeError("model down")
    monkeypatch.setattr(rn, "run_story", boom)
    import chat_nextseek.config as cc
    monkeypatch.setattr(cc, "ChatConfig", lambda: _Cfg())
    assert rn.main(["--yes", "--out", str(tmp_path)]) == 0
    row = json.loads((tmp_path / "stories" / "s1.json").read_text())
    assert row["outcome"]["error"].startswith("RuntimeError") and row["score"]["grade"] == "FAIL"
    from NessieAI.tests.pipeline_scenarios import fingerprint
    assert row["fingerprint"] == fingerprint.current() and row["agent_model"] == "model-x"
    assert row["fingerprint_files"] == fingerprint.files()
    assert (tmp_path / "report.html").exists()
    assert json.loads((tmp_path / "summary.json").read_text())["summary"]["FAIL"] == 1


def test_the_summary_carries_the_fingerprint_the_run_was_made_under(tmp_path, monkeypatch):
    from NessieAI.tests.pipeline_scenarios import fingerprint
    import chat_nextseek.config as cc
    monkeypatch.setattr(cc, "ChatConfig", lambda: _Cfg())
    assert rn.main(["--ids", "nosuch", "--yes", "--out", str(tmp_path)]) == 0
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["fingerprint"] == fingerprint.current() and summary["agent_model"] == "model-x"
    assert summary["posterior_routing"] is False and summary["laya_mode"] == "off"
    assert summary["fingerprint_files"] == fingerprint.files()


def test_the_summary_says_when_posterior_routing_was_on(tmp_path, monkeypatch, settings):
    import chat_nextseek.config as cc
    settings.NEXTSEEK_POSTERIOR_ROUTING_ENABLED = True
    monkeypatch.setattr(cc, "ChatConfig", lambda: _Cfg())
    assert rn.main(["--ids", "nosuch", "--yes", "--out", str(tmp_path)]) == 0
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["posterior_routing"] is True and summary["laya_mode"] == "off"


def test_the_laya_mode_is_written_to_the_summary_and_every_row(tmp_path, monkeypatch):
    from NessieAI.router import laya
    import chat_nextseek.config as cc
    story = _story(ruling={"state": "accepted", "date": "d", "note": ""})
    monkeypatch.setattr(rn, "load_stories", lambda: [story])
    monkeypatch.setattr(rn, "cohort_uids", lambda c: ["U"])
    monkeypatch.setattr(rn, "run_story", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    monkeypatch.setattr(laya, "mode", lambda: "shadow")
    monkeypatch.setattr(cc, "ChatConfig", lambda: _Cfg())
    assert rn.main(["--yes", "--out", str(tmp_path)]) == 0
    assert json.loads((tmp_path / "summary.json").read_text())["laya_mode"] == "shadow"
    assert json.loads((tmp_path / "stories" / "s1.json").read_text())["laya_mode"] == "shadow"


def test_a_routing_setting_that_cannot_be_read_is_written_as_an_error_not_hidden(monkeypatch):
    from NessieAI.router import laya
    monkeypatch.setattr(laya, "mode", lambda: (_ for _ in ()).throw(OSError("no calibration file")))
    assert rn.routing_settings()["laya_mode"] == "error: OSError"


def test_the_report_groups_by_family_and_escapes(tmp_path):
    from NessieAI.tests.pipeline_scenarios.report import write_report
    rows = [{"story": {"id": "a", "family": "f1", "question": "<b>q</b>"}, "score": {"grade": "PASS", "reasons": ["ok"]},
             "outcome": {"route": "nextseek_query", "pipeline_key": None, "selection": {"pipelines": ["rnaseq", "x"]}}},
            {"story": {"id": "b", "family": "f2", "question": "q"}, "score": {"grade": "FAIL", "reasons": ["bad & worse"]},
             "outcome": {"route": "?", "pipeline_key": "rnaseq", "selection": None}}]
    page = write_report(tmp_path, rows).read_text()
    assert "<h2>f1</h2>" in page and "<h2>f2</h2>" in page and "&lt;b&gt;q&lt;/b&gt;" in page
    assert "rnaseq, x" in page and "bad &amp; worse" in page and "'PASS': 1" in page.replace("&#x27;", "'")


def test_the_real_agent_start_accepts_the_session_without_a_parser_plan(tmp_path):
    """No model: a client without chat_with_tools makes the real start() answer at once, after it
    has read the pinned search from our session with parser_plan left out."""
    real = SimpleNamespace(get_agent_model=lambda label: (object(), "m", None), LOG_DIR="/tmp")
    outcome, record = rn.run_story(_story(), real, None, tmp_path, decide=_decide())
    assert "tool-capable" in record["replies"][0] and outcome.pipeline_key is None


def test_a_story_uses_at_most_the_first_60_uids_of_its_cohort(tmp_path, monkeypatch):
    from chat_nextseek.pipeline import agent as pa
    every = [f"D.SEQ-{n}-PUB" for n in range(100)]
    monkeypatch.setattr(rn, "cohort_uids", lambda c: every)
    sessions = []
    monkeypatch.setattr(pa, "start", lambda session, config, **k: (
        sessions.append(session), {"action": "ask", "reply": "?", "params": None})[1])
    outcome, _ = rn.run_story(_story(expect="ask"), _Cfg(), None, tmp_path, decide=_decide())
    assert rn.MAX_STORY_UIDS == 60 and outcome.uids == every[:60]
    assert [r["uuid"] for r in sessions[0]["results_history"][0]["graph_result"]["data"]] == every[:60]


def _built_state():
    return {"artifacts": {"launch": "launch.yml", "samplesheet": "s.csv"}, "launch_built_at_user_msgs": 1,
            "messages": [{"role": "user", "content": "start"}, {"role": "user", "content": "yes, submit it"}]}


@pytest.mark.parametrize("name", ["submit_to_luria", "submit_to_tower"])
def test_submit_is_refused_by_name_even_over_a_complete_config(name, monkeypatch):
    from chat_nextseek.pipeline import agent_tools as at
    monkeypatch.setattr(at, "submit_luria", _must_not_be_called)
    monkeypatch.setattr(at, "submit_launch", _must_not_be_called)
    real = SimpleNamespace(LURIA_ENV_COMPLETE=True, LURIA_ENV={"user": "u"}, TOWER_ENV_COMPLETE=True,
                           TOWER_ENV={"access_token": "t", "workspace": "w"}, SEQERA_AUTO_LAUNCH=True)
    out = json.loads(at.dispatch_pipeline_tool_call(config=rn.RunConfig(real, None), session={}, state=_built_state(),
                                                    name=name, tool_input={}, log_dir="/tmp"))
    assert out["ok"] is False and "called without" not in out["message"] and "not configured" in out["message"]
    # the control: the same built, confirmed state over the real, complete config does reach the submitter
    # (the tool turns the submitter's exception into a failed reply)
    reached = json.loads(at.dispatch_pipeline_tool_call(config=real, session={}, state=_built_state(), name=name,
                                                        tool_input={}, log_dir="/tmp"))
    assert "called without" in reached["message"]
    cfg = rn.RunConfig(real, None)
    assert (cfg.TOWER_ENV, cfg.TOWER_ENV_COMPLETE, cfg.LURIA_ENV, cfg.SEQERA_AUTO_LAUNCH) == ({}, False, {}, False)


def test_the_router_gets_the_history_production_builds():
    from NessieAI.router.router_context import SAMPLE_UIDS_MAX
    uids = [f"D.SEQ-{n}-PUB" for n in range(100)]
    story = _story(history=[{"user": "and the controls?", "assistant": "Yes."}])
    turns = rn.router_history(story, uids)
    assert [t.position for t in turns] == [1, 2]
    assert turns[0].router_choice == "nextseek_query" and turns[0].status == "completed"
    assert turns[0].sample_uids == uids[:SAMPLE_UIDS_MAX] and turns[0].result_count == 100
    assert turns[1].user_message == "and the controls?" and turns[1].router_choice == "nextseek_query"
    log = rn.make_session(story, uids)["chat_log"]
    assert log[0]["result_summary"]["count"] == 100 and log[0]["router_choice"] == "nextseek_query"


def test_the_seed_question_is_how_a_user_would_ask_not_the_cohort_code():
    s = rn.make_session(_story(), ["D.SEQ-1-PUB", "D.SEQ-2-PUB", "D.SEQ-3-PUB", "D.SEQ-4-PUB"])
    asked = s["results_history"][0]["user_query"]
    assert asked == s["chat_log"][0]["user_query"]
    assert asked.startswith("Find these samples: D.SEQ-1-PUB, D.SEQ-2-PUB, D.SEQ-3-PUB") and "::" not in asked


def test_a_scrubber_refusal_keeps_the_decision_and_replies_on_the_row(tmp_path, monkeypatch):
    story = _story(ruling={"state": "accepted", "date": "d", "note": ""})
    _scripted_agent(monkeypatch)
    monkeypatch.setattr(rn, "load_stories", lambda: [story])
    monkeypatch.setattr(rn, "cohort_uids", lambda c: ["D.SEQ-1-PUB"])
    import NessieAI.router.router as router
    monkeypatch.setattr(router, "decide", _decide())
    import chat_nextseek.config as cc
    monkeypatch.setattr(cc, "ChatConfig", lambda: _Cfg())
    real_tape = rn.Tape
    def refusing(mode="record"):
        t = real_tape(mode=mode)
        t.refused.append("fetch_reporter_metadata: email a@b.org")
        return t
    monkeypatch.setattr(rn, "Tape", refusing)
    assert rn.main(["--yes", "--out", str(tmp_path)]) == 0
    row = json.loads((tmp_path / "stories" / "s1.json").read_text())
    # A refusal is the tape's problem, not the run's: the story is graded on what it did.
    assert row["outcome"]["error"] is None and row["tape"] is None
    assert row["unrecorded"].startswith("TapeRefused") and "a@b.org" in row["unrecorded"]
    assert row["decision"]["route"] == "nextseek_query"
    assert row["outcome"]["replies"] == ["Group by Treatment?", "Ready. Submit?"]
    assert row["outcome"]["pipeline_key"] == "rnaseq" and row["outcome"]["uids"] == ["D.SEQ-1-PUB"]
    assert row["outcome"]["selection"]["pipelines"] == ["rnaseq"]
    assert row["score"]["grade"] == "PASS"
    assert json.loads((tmp_path / "summary.json").read_text())["unrecorded"] == ["s1"]


def test_a_crash_with_a_refusal_pending_is_still_a_runner_error(tmp_path, monkeypatch):
    """Only a refusal alone is 'unrecorded'; a real failure in the run stays an error."""
    from chat_nextseek.pipeline import agent as pa
    story = _story(ruling={"state": "accepted", "date": "d", "note": ""})
    _scripted_agent(monkeypatch)
    monkeypatch.setattr(pa, "handle_turn", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("agent down")))
    monkeypatch.setattr(rn, "load_stories", lambda: [story])
    import NessieAI.router.router as router
    monkeypatch.setattr(router, "decide", _decide())
    import chat_nextseek.config as cc
    monkeypatch.setattr(cc, "ChatConfig", lambda: _Cfg())
    real_tape = rn.Tape
    def refusing(mode="record"):
        t = real_tape(mode=mode)
        t.refused.append("fetch_reporter_metadata: email a@b.org")
        return t
    monkeypatch.setattr(rn, "Tape", refusing)
    assert rn.main(["--yes", "--out", str(tmp_path)]) == 0
    row = json.loads((tmp_path / "stories" / "s1.json").read_text())
    assert row["outcome"]["error"].startswith("RuntimeError: agent down") and row["score"]["grade"] == "FAIL"
    assert row["tape"] is None


def test_a_reply_quoting_the_runs_own_output_folder_is_stored_under_run_and_refuses_nothing():
    """configure_run and write_samplesheet answer with absolute paths under the run's output
    folder, and the agent may quote them; the tape keeps them as /RUN/..."""
    assert str(rn.OUTPUTS) in scrub.OUTPUT_ROOTS and "/app/outputs" in scrub.OUTPUT_ROOTS
    for root in scrub.OUTPUT_ROOTS:
        reply = {"content": [{"type": "text", "text": f"Built {root}/20261027T1200_pipeline_scenarios/logs/s1/launch.yml."}]}
        kept = rn.store("agent", reply)
        assert kept["content"][0]["text"] == "Built /RUN/20261027T1200_pipeline_scenarios/logs/s1/launch.yml."
    assert rn.store("agent", {"t": "/app/outputs"}) == {"t": "/RUN"}
    with pytest.raises(ValueError, match="path"):                 # only the output root is exempt
        rn.store("agent", {"t": "/app/outputsX/a/b and /app/other/x"})


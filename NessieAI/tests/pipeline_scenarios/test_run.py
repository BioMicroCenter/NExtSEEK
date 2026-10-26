"""run_story with a stubbed router, a scripted agent model and stubbed I/O: no model, no stack."""
import json
from types import SimpleNamespace

import pytest

from NessieAI.tests.pipeline_scenarios import run as rn
from NessieAI.tests.pipeline_scenarios.stories import Story
from NessieAI.tests.pipeline_scenarios.tape import Tape, TapeMismatch


def _story(**kw):
    base = dict(id="s1", family="bulk_rna", question="Which genes change between the groups?",
                cohort="241219BRY::A.GEX", route="nextseek_query", acceptable=[["rnaseq"]],
                launch={"built": False, "must_match": {}}, why={"route": "r", "pipelines": "p"})
    return Story(**{**base, **kw})


class _Cfg:
    LOG_DIR = "/tmp"

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


def test_the_router_history_is_the_cohort_search_then_the_story_turns():
    story = _story(history=[{"user": "and the controls?", "assistant": "Yes."}])
    turns = rn.router_history(story, ["D.SEQ-1-PUB"])
    assert [t.position for t in turns] == [1, 2]
    assert turns[0].sample_uids == ["D.SEQ-1-PUB"] and turns[1].user_message == "and the controls?"
    assert rn.router_history(_story(), []) == []


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
    assert rn.RunConfig(real, None).LOG_DIR == "/l"


def test_the_scrubber_redacts_people_and_refuses_contacts():
    clean = rn.scrubber("fetch_reporter_metadata", {"ok": True, "data": {"Scientist": "X", "UID": "U"}})
    assert clean["data"]["Scientist"] == "REDACTED"
    with pytest.raises(ValueError, match="email"):
        rn.scrubber("fetch_reporter_metadata", {"ok": True, "data": {"Notes": "mail a@b.org"}})


def test_the_scrubber_keeps_only_markers_of_the_protocol_text():
    out = rn.scrubber("gather_protocol_text", {"ok": True, "text": "Contact a@b.org at /home/u/x. We used STAR."})
    assert "a@b.org" not in out["text"] and "/home" not in out["text"]


def test_the_scrubber_finds_a_path_after_an_escaped_newline():
    with pytest.raises(ValueError, match="path"):
        rn.scrubber("expand_uid_prefixes", {"note": "first line\n/home/someone/file.txt"})


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


def test_main_without_yes_runs_nothing(capsys, monkeypatch):
    monkeypatch.setattr(rn, "cohort_uids", lambda c: ["U"])
    assert rn.main(["--ids", "no-such-story"]) == 0
    assert "Not run" in capsys.readouterr().out


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
    assert row["fingerprint"] is None and (tmp_path / "report.html").exists()
    assert json.loads((tmp_path / "summary.json").read_text())["summary"]["FAIL"] == 1


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

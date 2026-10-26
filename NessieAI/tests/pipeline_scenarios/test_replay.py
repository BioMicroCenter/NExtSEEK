"""Free CI replay of the accepted paid run: the current code, the recorded answers (spec §4.2).

test_replay is parametrized over recorded/*.json, which a paid run and record.py fill. The
synthetic tests below need no paid run: they record a stubbed run of a real ruled story, write
the fixture the way record.py does, and replay it through the same path, so the replay
machinery itself is tested in CI before any recording exists.
"""
import dataclasses
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from NessieAI.tests.pipeline_scenarios import fingerprint, record, run as rn
from NessieAI.tests.pipeline_scenarios.record import RECORDED, STORY_FIELDS
from NessieAI.tests.pipeline_scenarios.scorer import score
from NessieAI.tests.pipeline_scenarios.stories import story_by_id
from NessieAI.tests.pipeline_scenarios.tape import ReplayClient, Tape, TapeMismatch

FIXTURES = sorted(RECORDED.glob("*.json"))
SCHEMAS = Path(__file__).resolve().parents[1] / "chat_nextseek" / "fixtures" / "nfcore"
REGENERATE = "re-record with a paid run (NessieAI/tests/pipeline_scenarios/README.md)"


class _ReplayConfig:
    """What the replay needs of a ChatConfig: the recorded model and the agent's prompt."""
    LOG_DIR = "/tmp"
    LURIA_ENV_COMPLETE = False
    PIPELINE_LAUNCH_MODE = "luria"

    def __init__(self, tape):
        self._tape = tape

    def get_agent_model(self, label):
        return ReplayClient(self._tape), "replay", None

    def _load_prompt(self, name):
        return ""


def _offline_schemas(url: str) -> bytes:
    """Serve the pinned schema fixtures by URL; anything else is a recording gap (a 404)."""
    import requests
    name = url.split("/nf-core/")[1]
    pipeline, _revision, *rest = name.split("/")
    path = SCHEMAS / f"{pipeline}.{rest[-1]}"
    if not path.exists():
        raise requests.HTTPError(response=SimpleNamespace(status_code=404))
    return path.read_bytes()


def replay_fixture(fixture: dict, tmp_path, monkeypatch):
    """Replay one recorded story against the current code and check it still ends the same."""
    assert fixture["fingerprint"] == fingerprint.current(), f"selection inputs changed: {REGENERATE}"
    story = story_by_id(fixture["story"]["id"])
    current = {k: getattr(story, k) for k in STORY_FIELDS}
    assert current == fixture["story"], f"the story changed since it was recorded: {REGENERATE}"
    assert story.ruled, "an unruled story has a recording"

    from chat_nextseek.seqera import nfcore_schema
    nfcore_schema.clear_cache()
    monkeypatch.setattr(nfcore_schema, "_default_fetcher", _offline_schemas)
    monkeypatch.setattr(rn, "cohort_uids", lambda c: list(fixture["outcome"]["uids"]))
    decision = SimpleNamespace(**fixture["decision"])
    tape = Tape.from_json(fixture["tape"])
    try:
        outcome, _ = rn.run_story(story, rn.RunConfig(_ReplayConfig(tape), None), tape, tmp_path,
                                  decide=lambda q, h: decision)
    except TapeMismatch as exc:
        pytest.fail(f"{exc}: {REGENERATE}")
    finally:
        nfcore_schema.clear_cache()
    s = score(story, outcome)
    want = fixture["outcome"]
    assert (s.grade, outcome.pipeline_key) == (fixture["score"]["grade"], want["pipeline_key"]), s.reasons
    kept = lambda sel: {k: (sel or {}).get(k) for k in ("verdict", "pipelines")}   # noqa: E731
    assert kept(outcome.selection) == kept(want["selection"])


def test_there_are_recorded_stories():
    if not FIXTURES:
        pytest.skip("no recorded stories yet: run the paid eval and record.py (README)")


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.stem)
def test_replay(path, tmp_path, monkeypatch):
    replay_fixture(json.loads(path.read_text()), tmp_path, monkeypatch)


# ---- the synthetic recording: no paid run, a real ruled story, fake model answers ----------

STORY_ID = "sas-transcriptome-vague"      # expect "ask"; acceptable [rnaseq, scrnaseq]
ASK = "Do you want the genes that differ between groups (bulk rna-seq) or per-cell profiles (single-cell)?"


@dataclasses.dataclass
class _Ctx:
    def to_prompt_text(self, sections=None):
        return "PAYLOAD"

    def size_report(self, sections=None):
        return {"est_tokens": 1}


class _FakeModel:
    """Chooses the story's acceptable pipelines; the agent loop itself is scripted."""
    def chat(self, **kw):
        return SimpleNamespace(content=json.dumps(
            {"pipelines": ["rnaseq", "scrnaseq"], "reason": "bulk or single-cell is not stated"}))

    def chat_with_tools(self, **kw):
        raise AssertionError("the agent loop is scripted through call_tools")


class _FakeConfig:
    LOG_DIR = "/tmp"
    PIPELINE_LAUNCH_MODE = "luria"

    def get_agent_model(self, label):
        return _FakeModel(), "fake", None

    def _load_prompt(self, name):
        return ""


def _stub_the_world(monkeypatch, story):
    from chat_nextseek.pipeline import agent, agent_tools as at
    import chat_nextseek.config as cc
    import NessieAI.router.router as router
    script = [
        {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": "t1", "name": "select_pipeline",
                                                  "input": {"kind": "last_search", "question": story.question}}]},
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": ASK}]},
    ]
    monkeypatch.setattr(agent, "call_tools", lambda *a, **k: script.pop(0))
    monkeypatch.setattr(at, "build_sample_digest", lambda *a, **k: {"digest": 1})
    monkeypatch.setattr(at, "build_selection_context", lambda *a, **k: _Ctx())
    for name in ("fetch_reporter_metadata", "annotate_metadata_with_sampletypes", "gather_protocol_text",
                 "expand_uid_prefixes", "lookup_run_sizes", "resolve_accessions"):
        monkeypatch.setattr(at, name, lambda *a, **k: pytest.fail("the scripted run reads nothing else"))
    monkeypatch.setattr(router, "decide", lambda q, h: SimpleNamespace(
        route="nextseek_query", source="baml", reasoning="r", router_model="m", router_cost_usd=0))
    monkeypatch.setattr(cc, "ChatConfig", _FakeConfig)


@pytest.fixture()
def synthetic(tmp_path, monkeypatch):
    """The fixture record.py writes for a stubbed run of a real ruled story."""
    story = story_by_id(STORY_ID)
    assert story.ruled and story.expect == "ask"
    _stub_the_world(monkeypatch, story)
    run_dir, recorded = tmp_path / "run", tmp_path / "recorded"
    assert rn.main(["--ids", STORY_ID, "--yes", "--out", str(run_dir)]) == 0
    monkeypatch.setattr(record, "RECORDED", recorded)
    assert record.main([str(run_dir)]) == 0
    return json.loads((recorded / f"{STORY_ID}.json").read_text())


def test_a_recorded_run_replays_to_the_same_grade(synthetic, tmp_path, monkeypatch):
    assert synthetic["score"]["grade"] == "PASS" and synthetic["outcome"]["selection"]["verdict"] == "fork"
    assert set(synthetic["tape"]["entries"]) >= {"agent", "selection", "digest", "payload"}
    assert len(synthetic["outcome"]["uids"]) == 20
    from chat_nextseek.pipeline import agent
    monkeypatch.setattr(agent, "call_tools", lambda *a, **k: pytest.fail("the model must not be called in replay"))
    import chat_nextseek.pipeline.agent_tools as at
    monkeypatch.setattr(at, "build_sample_digest", lambda *a, **k: pytest.fail("no digest in replay"))
    replay_fixture(synthetic, tmp_path / "replay", monkeypatch)


def test_a_changed_selection_input_fails_the_replay(synthetic, tmp_path, monkeypatch):
    monkeypatch.setattr(fingerprint, "current", lambda: "0" * 64)
    with pytest.raises(AssertionError, match="selection inputs changed"):
        replay_fixture(synthetic, tmp_path / "replay", monkeypatch)


def test_a_changed_story_fails_the_replay(synthetic, tmp_path, monkeypatch):
    synthetic["story"]["question"] += " (reworded)"
    with pytest.raises(AssertionError, match="story changed"):
        replay_fixture(synthetic, tmp_path / "replay", monkeypatch)


def test_a_call_the_tape_never_saw_fails_the_replay(synthetic, tmp_path, monkeypatch):
    synthetic["tape"]["entries"]["agent"].pop()          # the agent now asks the model once more than recorded
    with pytest.raises(pytest.fail.Exception, match="re-record"):
        replay_fixture(synthetic, tmp_path / "replay", monkeypatch)


def test_a_recorded_entry_the_code_no_longer_plays_fails_the_replay(synthetic, tmp_path, monkeypatch):
    synthetic["tape"]["entries"]["agent"].append(dict(synthetic["tape"]["entries"]["agent"][-1]))
    with pytest.raises(pytest.fail.Exception, match="never played"):
        replay_fixture(synthetic, tmp_path / "replay", monkeypatch)


def test_a_changed_outcome_fails_the_replay(synthetic, tmp_path, monkeypatch):
    synthetic["outcome"]["selection"]["pipelines"] = ["rnaseq"]
    with pytest.raises(AssertionError):
        replay_fixture(synthetic, tmp_path / "replay", monkeypatch)


# ---- record.py ----------------------------------------------------------------------------

def _row(story_id=STORY_ID, **over):
    story = story_by_id(story_id)
    row = {"story": dataclasses.asdict(story), "decision": {"route": "nextseek_query"},
           "outcome": {"route": "nextseek_query", "uids": ["U"], "error": None}, "score": {"grade": "PASS"},
           "tape": {"entries": {}}, "fingerprint": fingerprint.current()}
    return {**row, **over}


def _run_dir(tmp_path, rows, fp=None):
    (tmp_path / "stories").mkdir(parents=True)
    for r in rows:
        (tmp_path / "stories" / f"{r['story']['id']}.json").write_text(json.dumps(r))
    (tmp_path / "summary.json").write_text(json.dumps({"fingerprint": fp or fingerprint.current()}))
    return tmp_path


def test_record_refuses_a_run_made_under_another_fingerprint(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(record, "RECORDED", tmp_path / "rec")
    assert record.main([str(_run_dir(tmp_path / "run", [_row()], fp="old"))]) == 1
    assert "refusing" in capsys.readouterr().err and not (tmp_path / "rec").exists()


def test_record_skips_a_story_that_failed_in_the_runner(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(record, "RECORDED", tmp_path / "rec")
    bad = _row(outcome={"route": "?", "uids": [], "error": "RuntimeError: x"}, tape=None)
    assert record.main([str(_run_dir(tmp_path / "run", [bad]))]) == 0
    assert "runner error" in capsys.readouterr().out and not list((tmp_path / "rec").glob("*.json"))


def test_record_keeps_only_the_story_fields_and_honours_ids(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "RECORDED", tmp_path / "rec")
    other = _row("gene-fusion")
    run = _run_dir(tmp_path / "run", [_row(), other])
    assert record.main([str(run), "--ids", STORY_ID]) == 0
    assert [p.stem for p in (tmp_path / "rec").glob("*.json")] == [STORY_ID]
    kept = json.loads((tmp_path / "rec" / f"{STORY_ID}.json").read_text())
    assert set(kept["story"]) == set(STORY_FIELDS) and "why" not in kept["story"]


def test_record_refuses_a_path_hidden_after_an_escaped_newline(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(record, "RECORDED", tmp_path / "rec")
    row = _row(tape={"entries": {"agent": [{"args": "x", "value": "first line\n/home/someone/file.txt"}]}})
    assert record.main([str(_run_dir(tmp_path / "run", [row]))]) == 1
    assert "path" in capsys.readouterr().err and not list((tmp_path / "rec").glob("*.json"))


def test_the_offline_schema_fetcher_serves_pinned_fixtures_and_404s_the_rest():
    import requests
    from chat_nextseek.seqera import nfcore_schema as ns
    nfcore_schema_url = ns.schema_urls("rnaseq", "3.18.0")[0]
    assert json.loads(_offline_schemas(nfcore_schema_url))
    with pytest.raises(requests.HTTPError) as err:
        _offline_schemas(ns.schema_urls("nosuchpipeline", "1.0")[1])
    assert ns._is_not_found(err.value)

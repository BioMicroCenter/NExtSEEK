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


def replay_fixture(fixture: dict, tmp_path, monkeypatch):
    """Replay one recorded story against the current code and check it still ends the same."""
    assert fixture["fingerprint"] == fingerprint.current(), f"selection inputs changed: {REGENERATE}"
    story = story_by_id(fixture["story"]["id"])
    current = {k: getattr(story, k) for k in STORY_FIELDS}
    assert current == fixture["story"], f"the story changed since it was recorded: {REGENERATE}"
    assert story.ruled, "an unruled story has a recording"

    # Schema fetches replay from the tape too (taped() clears the schema cache around the run).
    monkeypatch.setattr(rn, "cohort_uids", lambda c: list(fixture["outcome"]["uids"]))
    decision = SimpleNamespace(**fixture["decision"])
    tape = Tape.from_json(fixture["tape"])
    try:
        outcome, _ = rn.run_story(story, rn.RunConfig(_ReplayConfig(tape), None), tape, tmp_path,
                                  decide=lambda q, h: decision)
    except TapeMismatch as exc:
        pytest.fail(f"{exc}: {REGENERATE}")
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
    pipelines = ["rnaseq", "scrnaseq"]

    def chat(self, **kw):
        return SimpleNamespace(content=json.dumps(
            {"pipelines": self.pipelines, "reason": "bulk or single-cell is not stated"}))

    def chat_with_tools(self, **kw):
        raise AssertionError("the agent loop is scripted through call_tools")


class _FakeConfig:
    LOG_DIR = "/tmp"
    PIPELINE_LAUNCH_MODE = "luria"

    def get_agent_model(self, label):
        return _FakeModel(), "fake", None

    def _load_prompt(self, name):
        return ""


def _stub_the_world(monkeypatch, story, real_context=False):
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
    if not real_context:
        monkeypatch.setattr(at, "build_selection_context", lambda *a, **k: _Ctx())
    for name in ("fetch_reporter_metadata", "annotate_metadata_with_sampletypes", "gather_protocol_text",
                 "expand_uid_prefixes", "lookup_run_sizes", "resolve_accessions"):
        monkeypatch.setattr(at, name, lambda *a, **k: pytest.fail("the scripted run reads nothing else"))
    monkeypatch.setattr(router, "decide", lambda q, h: SimpleNamespace(
        route="nextseek_query", source="baml", reasoning="r", router_model="m", router_cost_usd=0))
    monkeypatch.setattr(cc, "ChatConfig", _FakeConfig)


def _record_synthetic(tmp_path, monkeypatch, real_context=False):
    """The fixture record.py writes for a stubbed run of a real ruled story."""
    story = story_by_id(STORY_ID)
    assert story.ruled and story.expect == "ask"
    _stub_the_world(monkeypatch, story, real_context)
    run_dir, recorded = tmp_path / "run", tmp_path / "recorded"
    assert rn.main(["--ids", STORY_ID, "--yes", "--out", str(run_dir)]) == 0
    monkeypatch.setattr(record, "RECORDED", recorded)
    assert record.main([str(run_dir)]) == 0
    return json.loads((recorded / f"{STORY_ID}.json").read_text())


@pytest.fixture()
def synthetic(tmp_path, monkeypatch):
    return _record_synthetic(tmp_path, monkeypatch)


FETCHED = []


@pytest.fixture()
def synthetic_real_context(tmp_path, monkeypatch):
    """The same run, but select_pipeline builds the REAL selection context, which fetches the
    nf-core schemas of every rich pipeline through the taped fetcher. The model picks one pipeline."""
    import requests
    from chat_nextseek.seqera import nfcore_schema as ns
    fixtures = Path(__file__).resolve().parents[1] / "chat_nextseek" / "fixtures" / "nfcore"
    FETCHED.clear()

    def fetch(url):
        FETCHED.append(url)
        pipeline, _revision, *rest = url.split("/nf-core/")[1].split("/")
        name = f"{pipeline}.{rest[-1]}"
        if pipeline == "sarek" and name.endswith("schema_input.json"):
            raise requests.HTTPError(f"404 Client Error: Not Found for url: {url}",
                                     response=SimpleNamespace(status_code=404))
        data = (fixtures / name).read_bytes()
        return data + b"\n" if pipeline == "rnaseq" and name.endswith("nextflow_schema.json") else data
    monkeypatch.setattr(ns, "_default_fetcher", fetch)
    monkeypatch.setattr(_FakeModel, "pipelines", ["rnaseq"])
    return _record_synthetic(tmp_path, monkeypatch, real_context=True)


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


def _run_dir(tmp_path, rows, fp=None, summary=None):
    (tmp_path / "stories").mkdir(parents=True)
    for r in rows:
        (tmp_path / "stories" / f"{r['story']['id']}.json").write_text(json.dumps(r))
    (tmp_path / "summary.json").write_text(json.dumps(
        summary if summary is not None else {"fingerprint": fp or fingerprint.current(),
                                             "posterior_routing": False, "laya_mode": "off"}))
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


def test_the_fingerprint_flag_is_used_only_when_the_summary_has_none(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(record, "RECORDED", tmp_path / "rec")
    run = _run_dir(tmp_path / "run", [_row()], fp="old")
    # the summary names the run's fingerprint: a flag cannot override it
    assert record.main([str(run), "--fingerprint", fingerprint.current()]) == 1
    assert not (tmp_path / "rec").exists()
    # an older run dir with no key in its summary may be vouched for by the flag
    bare = _run_dir(tmp_path / "bare", [_row()], summary={"summary": {}, "posterior_routing": False})
    assert record.main([str(bare)]) == 1
    assert record.main([str(bare), "--fingerprint", fingerprint.current()]) == 0
    assert (tmp_path / "rec" / f"{STORY_ID}.json").exists()


def test_a_refusal_on_any_row_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "RECORDED", tmp_path / "rec")
    bad = _row("gene-fusion", tape={"entries": {"agent": [{"args": "x", "value": "mail a@b.org"}]}})
    run = _run_dir(tmp_path / "run", [bad, _row()])         # the refused row sorts first; the good one must not be kept
    assert record.main([str(run)]) == 1
    assert not (tmp_path / "rec").exists()
    run2 = _run_dir(tmp_path / "run2", [_row(), bad])      # and the other way round
    assert record.main([str(run2)]) == 1
    assert not (tmp_path / "rec").exists()


def test_the_fixture_keeps_the_family_and_the_agent_model(tmp_path, monkeypatch):
    assert "family" in STORY_FIELDS
    monkeypatch.setattr(record, "RECORDED", tmp_path / "rec")
    assert record.main([str(_run_dir(tmp_path / "run", [_row(agent_model="model-x")]))]) == 0
    kept = json.loads((tmp_path / "rec" / f"{STORY_ID}.json").read_text())
    assert kept["story"]["family"] == "bulk_rna" and kept["agent_model"] == "model-x"


def test_a_run_that_builds_the_real_selection_context_replays_clean(synthetic_real_context, tmp_path, monkeypatch):
    fx = synthetic_real_context
    schemas = fx["tape"]["entries"]["schemas"]
    # 10 rich pipelines, two documents each, every one fetched by the real context build
    assert len(schemas) == len(FETCHED) == 20
    kinds = [("fixture" if "fixture" in a else "bytes" if "__bytes__" in a else "error") for a in schemas.values()]
    assert kinds.count("fixture") == 18 and kinds.count("bytes") == 1 and kinds.count("error") == 1
    assert "https://" not in json.dumps(fx)
    assert fx["outcome"]["selection"] == {"verdict": "chosen", "pipelines": ["rnaseq"],
                                          "reason": "bulk or single-cell is not stated"}
    total = len(json.dumps(fx, indent=1, sort_keys=True))
    own_bytes = sum(len(a["__bytes__"]) for a in schemas.values() if "__bytes__" in a)
    print(f"tape size: {total} bytes; {own_bytes} of them the one schema that is not a committed fixture")
    assert total - own_bytes < 15_000       # a sha per committed schema, not its bytes
    FETCHED.clear()
    replay_fixture(fx, tmp_path / "replay", monkeypatch)     # raises on any unplayed entry or mismatch
    assert FETCHED == []                                      # the stubbed context build fetched nothing


@pytest.mark.parametrize("flag", [True, "error: ImportError"])
def test_record_refuses_a_run_made_with_posterior_routing_on_or_unknown(tmp_path, monkeypatch, capsys, flag):
    """The posterior selector reads the whole corpus file's sha, which the fingerprint does not."""
    monkeypatch.setattr(record, "RECORDED", tmp_path / "rec")
    summary = {"fingerprint": fingerprint.current(), "posterior_routing": flag, "laya_mode": "off"}
    assert record.main([str(_run_dir(tmp_path / "run", [_row()], summary=summary))]) == 1
    assert "posterior" in capsys.readouterr().err and not (tmp_path / "rec").exists()


def test_record_refuses_a_run_that_does_not_say_whether_posterior_routing_was_on(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(record, "RECORDED", tmp_path / "rec")
    run = _run_dir(tmp_path / "run", [_row()], summary={"fingerprint": fingerprint.current()})
    assert record.main([str(run)]) == 1
    assert "posterior" in capsys.readouterr().err


def test_the_fixture_keeps_the_laya_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(record, "RECORDED", tmp_path / "rec")
    assert record.main([str(_run_dir(tmp_path / "run", [_row(laya_mode="shadow")]))]) == 0
    assert json.loads((tmp_path / "rec" / f"{STORY_ID}.json").read_text())["laya_mode"] == "shadow"

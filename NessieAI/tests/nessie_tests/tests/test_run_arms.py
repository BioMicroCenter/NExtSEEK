"""`run_arms`: every question through each forced NS arm, back to back (graph_search Nessie POC).

Every test drives fakes; none reaches a model. The fake endpoint answers each
posted body as the evaluation switch would for its arm, so the preflight probes
and the questions go through the same doubles.
"""
from __future__ import annotations

import json
import os
import re
import stat
from pathlib import Path

import pytest

from NessieAI.tests.nessie_tests import corpus, preflight, runner
from NessieAI.tests.nessie_tests.manifest import load_manifest

CORPUS = Path(__file__).resolve().parents[1] / "corpus.json"
PROBES = {preflight.PROBE_QUERY, preflight.PARSER_FORCE_PROBE_QUERY}
OUTAGE_REPLY = "All provider fallbacks exhausted: agent 'parser' gave up"


def _question(vid, k=0):
    return f"How many samples for {vid} ({k})?"


def _qid(body):
    return re.search(r"for (\S+) \(", body["query"]).group(1)


def _cases(tmp_path, ids=("q.one", "q.two"), *, turns=1, name="cases.json", include_ids=()):
    variants = [{
        "family": "engine_compare", "id": vid, "name": vid,
        "tags": ["engine_compare", "group:A"],
        "turns": [{"label": f"t{k}", "query": _question(vid, k),
                   "pass_criteria": [{"field": "last_reply", "op": "matches_re",
                                      "value": "107,?412"}]}
                  for k in range(turns)],
    } for vid in ids]
    payload = {}
    if variants:
        payload["families"] = {"engine_compare": {"description": "test", "variants": variants}}
    if include_ids:
        payload["include_ids"] = list(include_ids)
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class Interrupted(BaseException):
    """Stands in for Ctrl-C: `run_case` catches Exception, never this."""


class Endpoint:
    """The async query endpoint, answering each body as the switch would for its arm."""

    def __init__(self, *, reply="There are 107,412 samples.", note=True, on_post=None,
                 outage_for=()):
        self.bodies = []
        self.reply = reply
        self.note = note
        self.on_post = on_post
        self.outage_for = set(outage_for)

    def post_query(self, body):
        self.bodies.append(body)
        if self.on_post:
            self.on_post(self, body)
        n = len(self.bodies)
        return {"task_id": f"t{n}", "session_id": f"s{n}"}

    def get_progress(self, task_id):
        body = self.bodies[int(task_id[1:]) - 1]
        arm = body.get("force_parser_mode")
        mode = "graph_query" if arm == "graph" else "new_search"
        notes = (f"forced to {arm} {preflight.FORCE_NOTE_MARKER} (parser chose new_search)"
                 if (self.note and arm) else None)
        debug = {"parser_plan": {"mode": mode, "notes": notes}}
        if arm == "graph":
            debug["graph_context"] = "catalog"
        reply = OUTAGE_REPLY if (body["query"], arm) in self.outage_for else self.reply
        return {"status": "completed", "progress": [
            {"event": "route_decided", "data": {"route": "nextseek_query", "source": "forced"}},
            {"event": "query_complete", "data": {"reply": reply, "debug": debug,
                                                  "files": [], "artifacts": []}},
            {"event": "ns_run_root", "data": {"run_root": "/venue/outputs/x"}},
        ]}

    def question_bodies(self):
        return [b for b in self.bodies if b["query"] not in PROBES]

    def order(self):
        return [(_qid(b), b["force_parser_mode"]) for b in self.question_bodies()]


def _run(tmp_path, ep, *, arms=("graph", "api"), cases=None, out=None, **kw):
    kw.setdefault("sleep", lambda s: None)
    kw.setdefault("clock", lambda: 0.0)
    kw.setdefault("corpus_path", CORPUS)
    return runner.run_arms(
        base_url="http://venue", auth_header="Basic x",
        cases_path=cases or _cases(tmp_path), out_dir=out or tmp_path / "run",
        arms=list(arms), post_query=ep.post_query, get_progress=ep.get_progress, **kw)


def _arms_doc(out):
    return json.loads((out / runner.ARMS_FILE).read_text(encoding="utf-8"))


def _ids(out, arm):
    return [e.id for e in load_manifest(out / arm / "manifest.json").entries]


# ── the acceptance shape ─────────────────────────────────────────────────────


def test_two_arms_over_two_questions_leave_the_whole_run_layout_on_disk(tmp_path):
    ep = Endpoint()
    cases = _cases(tmp_path)
    out = tmp_path / "run"
    result = _run(tmp_path, ep, cases=cases, out=out)

    for arm in ("graph", "api"):
        m = load_manifest(out / arm / "manifest.json")
        assert [e.id for e in m.entries] == ["q.one", "q.two"]
        assert all(e.status == "passed" for e in m.entries)
        assert m.tier == "full" and m.cases_file == str(cases)
        assert (out / arm / "report.html").exists()
        for vid in ("q.one", "q.two"):
            assert (out / arm / "payloads" / vid / "t0.json").exists()

    doc = _arms_doc(out)
    meta = doc["run_meta"]
    assert {"git_sha", "corpus_fingerprint", "cases_sha256", "arms", "base_url",
            "preflight"} <= set(meta)
    assert meta["arms"] == ["graph", "api"]
    assert meta["cases_sha256"] == corpus.sha256_of(cases)
    assert meta["corpus_fingerprint"] == runner.corpus_fingerprint(CORPUS)
    assert meta["base_url"] == "http://venue"
    assert meta["preflight"]["passed_at"]
    assert "git_sha" in meta["preflight"]

    q = {x["id"]: x for x in doc["questions"]}
    assert list(q) == ["q.one", "q.two"]
    assert q["q.one"]["family"] == "engine_compare"
    assert set(q["q.one"]["arms"]) == {"graph", "api"}
    rec = q["q.one"]["arms"]["graph"]
    assert rec["status"] == "passed" and rec["task_ids"] and "elapsed_s" in rec
    assert rec["outage"] is False

    assert result["arms_file"] == str(out / runner.ARMS_FILE)
    assert set(result["manifests"]) == {"graph", "api"}
    assert doc["progress"]["state"] == "complete"
    assert doc["progress"]["turns_driven"] == 4


def test_one_arm_leaves_one_manifest_and_one_report(tmp_path):
    ep = Endpoint()
    out = tmp_path / "run"
    _run(tmp_path, ep, arms=("graph",), out=out)
    assert (out / "graph" / "manifest.json").exists()
    assert (out / "graph" / "report.html").exists()
    assert not (out / "api").exists()
    assert ep.order() == [("q.one", "graph"), ("q.two", "graph")]
    assert [b["force_parser_mode"] for b in ep.bodies
            if b["query"] == preflight.PARSER_FORCE_PROBE_QUERY] == ["graph"]


def test_every_question_turn_is_forced_to_ns_and_to_its_arm(tmp_path):
    ep = Endpoint()
    _run(tmp_path, ep, skip_preflight=True)
    assert len(ep.question_bodies()) == 4
    for b in ep.question_bodies():
        assert b["force_route"] == "ns"
        assert b["force_parser_mode"] in ("graph", "api")
        assert b["force_new"] is True and b["fresh_session"] is True
        assert b["mode"] == "standard"


def test_the_first_arm_rotates_with_the_question_index(tmp_path):
    ep = Endpoint()
    cases = _cases(tmp_path, ids=("q.a", "q.b", "q.c"))
    result = _run(tmp_path, ep, cases=cases, skip_preflight=True)
    assert ep.order() == [("q.a", "graph"), ("q.a", "api"),
                          ("q.b", "api"), ("q.b", "graph"),
                          ("q.c", "graph"), ("q.c", "api")]
    assert [q["first_arm"] for q in result["questions"]] == ["graph", "api", "graph"]


def test_each_payload_carries_the_final_turn(tmp_path):
    out = tmp_path / "run"
    _run(tmp_path, Endpoint(), out=out, arms=("graph",), skip_preflight=True)
    p = json.loads((out / "graph" / "payloads" / "q.one" / "t0.json").read_text())
    assert p["query"] == _question("q.one")
    assert p["task_id"] and p["session_id"] and p["status"] == "completed"
    assert p["route_obs"]["source"] == "forced"
    assert p["query_complete"]["debug"]["graph_context"] == "catalog"
    assert p["force_route"] == "ns" and p["force_parser_mode"] == "graph"
    assert "elapsed_s" in p


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")
def test_the_evidence_is_private(tmp_path):
    """Questions and replies can name real people (spec E4): directories 700, files 600."""
    out = tmp_path / "run"
    _run(tmp_path, Endpoint(), out=out, skip_preflight=True)
    for d in (out, out / "graph", out / "graph" / "payloads",
              out / "graph" / "payloads" / "q.one"):
        assert stat.S_IMODE(d.stat().st_mode) == 0o700, d
    for f in (out / runner.ARMS_FILE, out / "graph" / "manifest.json",
              out / "graph" / "report.html", out / "graph" / "payloads" / "q.one" / "t0.json"):
        assert stat.S_IMODE(f.stat().st_mode) == 0o600, f


# ── written after every (question, arm) ──────────────────────────────────────


def test_the_first_arm_is_on_disk_before_the_second_arm_starts(tmp_path):
    out = tmp_path / "run"
    seen = {}

    def on_post(ep, body):
        if body["query"] == _question("q.one") and body.get("force_parser_mode") == "api":
            seen["graph"] = _ids(out, "graph")
            seen["doc"] = _arms_doc(out)

    _run(tmp_path, Endpoint(on_post=on_post), out=out, skip_preflight=True)
    assert seen["graph"] == ["q.one"]
    q1 = next(q for q in seen["doc"]["questions"] if q["id"] == "q.one")
    assert set(q1["arms"]) == {"graph"}
    assert seen["doc"]["progress"]["state"] == "running"


def test_an_interrupt_in_the_second_arm_keeps_the_first_and_writes_the_reports(tmp_path):
    out = tmp_path / "run"

    def on_post(ep, body):
        if body["query"] not in PROBES and body.get("force_parser_mode") == "api":
            raise Interrupted()

    with pytest.raises(Interrupted):
        _run(tmp_path, Endpoint(on_post=on_post), out=out, skip_preflight=True)

    assert _ids(out, "graph") == ["q.one"]
    doc = _arms_doc(out)
    q1 = next(q for q in doc["questions"] if q["id"] == "q.one")
    assert set(q1["arms"]) == {"graph"}
    assert doc["progress"]["state"] == "interrupted"
    assert (out / "graph" / "report.html").exists()
    assert (out / "api" / "report.html").exists()


# ── resume ───────────────────────────────────────────────────────────────────


def test_resume_without_an_arms_file_is_refused_before_any_turn(tmp_path):
    ep = Endpoint()
    with pytest.raises(runner.NoArmsRunToResume):
        _run(tmp_path, ep, resume=True)
    assert ep.bodies == []


def test_a_fresh_run_onto_a_recorded_one_is_refused_before_any_turn(tmp_path):
    cases = _cases(tmp_path)
    out = tmp_path / "run"
    _run(tmp_path, Endpoint(), cases=cases, out=out, max_turns=0)
    before = (out / runner.ARMS_FILE).read_bytes()
    ep = Endpoint()
    with pytest.raises(runner.PriorArmsRunWouldBeOverwritten):
        _run(tmp_path, ep, cases=cases, out=out)
    assert ep.bodies == []
    assert (out / runner.ARMS_FILE).read_bytes() == before


def test_resume_drives_only_what_is_missing(tmp_path):
    cases = _cases(tmp_path)
    out = tmp_path / "run"
    _run(tmp_path, Endpoint(), cases=cases, out=out, max_turns=2)
    assert _ids(out, "graph") == ["q.one"] and _ids(out, "api") == ["q.one"]

    ep = Endpoint()
    _run(tmp_path, ep, cases=cases, out=out, resume=True)
    assert ep.order() == [("q.two", "api"), ("q.two", "graph")]
    for arm in ("graph", "api"):
        assert _ids(out, arm) == ["q.one", "q.two"]
    doc = _arms_doc(out)
    assert all(set(q["arms"]) == {"graph", "api"} for q in doc["questions"])
    assert doc["run_meta"]["resumed"] is True


def test_resume_refuses_a_changed_cases_file(tmp_path):
    cases = _cases(tmp_path)
    out = tmp_path / "run"
    _run(tmp_path, Endpoint(), cases=cases, out=out, max_turns=2)
    cases.write_text(cases.read_text().replace("q.two", "q.three"))
    ep = Endpoint()
    with pytest.raises(runner.CasesChanged):
        _run(tmp_path, ep, cases=cases, out=out, resume=True)
    assert ep.bodies == []


def test_resume_refuses_a_different_arm_list(tmp_path):
    cases = _cases(tmp_path)
    out = tmp_path / "run"
    _run(tmp_path, Endpoint(), cases=cases, out=out, max_turns=2)
    ep = Endpoint()
    with pytest.raises(runner.ArmsChanged):
        _run(tmp_path, ep, cases=cases, out=out, resume=True, arms=("api", "graph"))
    assert ep.bodies == []


def test_resume_skips_the_preflight_only_after_a_pass_on_the_same_sha(tmp_path, monkeypatch):
    cases = _cases(tmp_path)
    out = tmp_path / "run"
    monkeypatch.setattr(runner, "git_sha", lambda: "abc1234")
    _run(tmp_path, Endpoint(), cases=cases, out=out, max_turns=0)
    assert _arms_doc(out)["run_meta"]["preflight"]["git_sha"] == "abc1234"

    same = Endpoint()
    _run(tmp_path, same, cases=cases, out=out, resume=True, max_turns=2)
    assert not [b for b in same.bodies if b["query"] in PROBES]
    assert same.order() == [("q.one", "graph"), ("q.one", "api")]

    monkeypatch.setattr(runner, "git_sha", lambda: "def5678")
    moved = Endpoint()
    _run(tmp_path, moved, cases=cases, out=out, resume=True)
    assert len([b for b in moved.bodies if b["query"] in PROBES]) == 3
    doc = _arms_doc(out)
    assert doc["run_meta"]["preflight"]["git_sha"] == "def5678"
    q = {x["id"]: x for x in doc["questions"]}
    assert q["q.one"]["arms"]["graph"]["git_sha"] == "abc1234"
    assert q["q.two"]["arms"]["graph"]["git_sha"] == "def5678"


def test_an_unknown_sha_never_vouches_for_a_skipped_preflight(tmp_path, monkeypatch):
    cases = _cases(tmp_path)
    out = tmp_path / "run"
    monkeypatch.setattr(runner, "git_sha", lambda: None)
    _run(tmp_path, Endpoint(), cases=cases, out=out, max_turns=0)
    ep = Endpoint()
    _run(tmp_path, ep, cases=cases, out=out, resume=True, max_turns=0)
    assert len([b for b in ep.bodies if b["query"] in PROBES]) == 3


def test_resume_redrives_a_provider_outage_and_replaces_its_entry(tmp_path):
    cases = _cases(tmp_path, ids=("q.one",))
    out = tmp_path / "run"
    _run(tmp_path, Endpoint(outage_for={(_question("q.one"), "graph")}),
         cases=cases, out=out, skip_preflight=True)
    first = load_manifest(out / "graph" / "manifest.json").entries
    assert len(first) == 1 and first[0].outage is True

    ep = Endpoint()
    _run(tmp_path, ep, cases=cases, out=out, resume=True, skip_preflight=True)
    assert ep.order() == [("q.one", "graph")]
    again = load_manifest(out / "graph" / "manifest.json").entries
    assert [e.id for e in again] == ["q.one"] and again[0].outage is False
    rec = _arms_doc(out)["questions"][0]["arms"]["graph"]
    assert rec["status"] == "passed" and rec["outage"] is False


def test_resume_refuses_a_changed_corpus_when_the_cases_name_corpus_ids(tmp_path):
    corpus_copy = tmp_path / "corpus.json"
    corpus_copy.write_text(CORPUS.read_text(encoding="utf-8"), encoding="utf-8")
    cases = _cases(tmp_path, ids=(), include_ids=("green.mus_ndma",))
    out = tmp_path / "run"
    _run(tmp_path, Endpoint(), cases=cases, out=out, corpus_path=corpus_copy,
         max_turns=0, skip_preflight=True)
    corpus_copy.write_text(CORPUS.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    ep = Endpoint()
    with pytest.raises(runner.CasesChanged):
        _run(tmp_path, ep, cases=cases, out=out, corpus_path=corpus_copy, resume=True,
             skip_preflight=True)
    assert ep.bodies == []


def test_an_inline_cases_file_resumes_across_a_corpus_edit(tmp_path):
    """Inline questions carry their own text; the corpus cannot change them."""
    corpus_copy = tmp_path / "corpus.json"
    corpus_copy.write_text(CORPUS.read_text(encoding="utf-8"), encoding="utf-8")
    cases = _cases(tmp_path)
    out = tmp_path / "run"
    _run(tmp_path, Endpoint(), cases=cases, out=out, corpus_path=corpus_copy,
         max_turns=2, skip_preflight=True)
    corpus_copy.write_text(CORPUS.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    ep = Endpoint()
    _run(tmp_path, ep, cases=cases, out=out, corpus_path=corpus_copy, resume=True,
         skip_preflight=True)
    assert ep.order() == [("q.two", "api"), ("q.two", "graph")]


# ── max_turns ────────────────────────────────────────────────────────────────


def test_max_turns_stops_before_a_question_that_would_exceed_it(tmp_path):
    ep = Endpoint()
    cases = _cases(tmp_path, ids=("q.a", "q.b", "q.c"))
    result = _run(tmp_path, ep, cases=cases, max_turns=3, skip_preflight=True)
    assert ep.order() == [("q.a", "graph"), ("q.a", "api")]
    assert result["progress"]["state"] == "max_turns"
    assert result["progress"]["turns_driven"] == 2


def test_max_turns_counts_this_invocation_only(tmp_path):
    cases = _cases(tmp_path, ids=("q.a", "q.b", "q.c"))
    out = tmp_path / "run"
    _run(tmp_path, Endpoint(), cases=cases, out=out, max_turns=2, skip_preflight=True)
    ep = Endpoint()
    _run(tmp_path, ep, cases=cases, out=out, max_turns=2, resume=True, skip_preflight=True)
    assert ep.order() == [("q.b", "api"), ("q.b", "graph")]


def test_max_turns_counts_every_turn_of_a_multi_turn_question(tmp_path):
    ep = Endpoint()
    cases = _cases(tmp_path, ids=("q.a", "q.b"), turns=2)
    _run(tmp_path, ep, cases=cases, max_turns=5, skip_preflight=True)
    assert len(ep.question_bodies()) == 4
    assert {q for q, _ in ep.order()} == {"q.a"}


def test_max_turns_zero_runs_the_preflight_and_no_question(tmp_path):
    ep = Endpoint()
    out = tmp_path / "run"
    result = _run(tmp_path, ep, out=out, max_turns=0)
    assert ep.question_bodies() == []
    assert len(ep.bodies) == 3, "one route probe and one parser probe per arm"
    assert {b["query"] for b in ep.bodies} == PROBES
    doc = _arms_doc(out)
    assert doc["run_meta"]["preflight"]["passed_at"]
    assert [q["arms"] for q in doc["questions"]] == [{}, {}]
    assert result["progress"]["state"] == "max_turns"


def test_a_negative_max_turns_is_refused(tmp_path):
    ep = Endpoint()
    with pytest.raises(ValueError):
        _run(tmp_path, ep, max_turns=-1)
    assert ep.bodies == []


# ── refusals before any question ─────────────────────────────────────────────


def test_a_preflight_refusal_stops_the_run_before_any_question(tmp_path):
    ep = Endpoint(note=False)
    out = tmp_path / "run"
    with pytest.raises(preflight.PreflightRefused):
        _run(tmp_path, ep, out=out)
    assert ep.question_bodies() == []
    assert not (out / runner.ARMS_FILE).exists()


@pytest.mark.parametrize("arms", [("graph", "cypher"), (), ("graph", "graph")])
def test_a_bad_arm_list_is_refused_before_any_turn(arms, tmp_path):
    ep = Endpoint()
    with pytest.raises(ValueError):
        _run(tmp_path, ep, arms=arms)
    assert ep.bodies == []


def test_an_unknown_arm_names_itself_and_the_known_ones(tmp_path):
    with pytest.raises(runner.UnknownArm) as e:
        _run(tmp_path, Endpoint(), arms=("graph", "cypher"))
    assert "cypher" in str(e.value) and "api" in str(e.value)

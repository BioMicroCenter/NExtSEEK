"""The arms scorer (graph_search Nessie POC, spec E6 to E8), on synthetic runs only.

Every run directory, payload, venue output, ledger and truth file here is built by the
test in the shapes the harness (`runner.run_arms`) and the NS engine write them. Nothing
reaches a model, the venue or the live stack.
"""
from __future__ import annotations

import csv
import itertools
import json
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from NessieAI.tests.nessie_tests import engine_compare as ec, engine_truth as et

NOTE = "forced to {forced} by the evaluation switch (parser chose {chose})"
_ROOTS = itertools.count()


def _root() -> str:
    """A fresh run-root name, one minute apart, as `<YYMMDD_HHMMSS>_<user>`."""
    t = datetime(2026, 9, 15, 10, 0, 0) + timedelta(minutes=next(_ROOTS))
    return t.strftime("%y%m%d_%H%M%S") + "_demo"


def _truth_q(qid, family="sample_search", *, group="A", source="corpus", value=107412,
             types=("TIS",), attributes=(), relationships=(), alternates=(), flags=(),
             kind="count"):
    exp = et.Expected(kind=kind, value=value,
                      required_numbers=[value] if isinstance(value, (int, float)) else [],
                      alternates=[et.Alternate(**a) for a in alternates],
                      sampletypes=list(types), attributes=list(attributes),
                      relationships=list(relationships))
    return et.TruthQuestion(
        id=qid, family=family, group=group, source=source, flags=list(flags),
        turns=[et.TruthTurn(label="main", query=f"How many for {qid}?", reading="r",
                            oracle=None, expected=exp)])


def _write_truth(directory: Path, group: str, questions, name="t") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    truth = et.TruthFile(name=name, group=group, questions=list(questions))
    (directory / f"{name}.json").write_text(truth.model_dump_json(), encoding="utf-8")
    return directory


def _payload(outputs: Path, qid: str, arm: str, *, reply="There are 107,412 tissue samples.",
             mode=None, chose=None, note=True, context="catalog", route="nextseek_query",
             source="forced", status="completed", entity=("TIS",), sampletype_code="TIS",
             cypher="MATCH (s:Sample:T_TIS) RETURN count(s) AS n", preview=({"n": 107412},),
             neo4j_total=None, graph_total=None, api_total=107412, api_body=None,
             write_files=True, elapsed=10.0, root=None, ledger=None):
    mode = mode or ("graph_query" if arm == "graph" else "new_search")
    root = root or _root()
    plan = {"mode": mode, "filters": {"sampletype_code": sampletype_code},
            "resolved": {"sampletypes": [{"code": c} for c in entity]},
            "notes": NOTE.format(forced=arm, chose=chose or mode) if note else ""}
    debug = {"parser_plan": plan,
             "entity_result": {"sampletypes": [{"code": c, "name": c} for c in entity]}}
    files = []
    if mode == "graph_query":
        debug["graph_context"] = context
        debug["graph_plan"] = {"cypher": cypher}
        debug["graph_result"] = {"ok": True, "count": len(preview),
                                 **({"total": graph_total} if graph_total is not None else {})}
        path = f"/venue/outputs/{root}/files/graph/graph_debug_20260915_100001.json"
        if write_files:
            out = {"ok": True, "count": len(preview), "data_preview": list(preview)}
            if neo4j_total is not None:
                out["total"] = neo4j_total
            _dump(outputs / root / "files" / "graph" / "graph_debug_20260915_100001.json",
                  {"neo4j_output": out})
        files.append({"key": "graph_debug", "kind": "graph", "path": path,
                      "filename": "graph_debug_20260915_100001.json"})
    elif mode == "new_search":
        debug["api_plan"] = {"endpoint": "/nextseek_api/samples/advanced_search/",
                             "requestBody": api_body if api_body is not None else
                             {"sampletype": "TIS", "attribute": "Organ", "filter_searchText": "lung"}}
        path = f"/venue/outputs/{root}/files/api/api_result_bundle_1.json"
        debug["raw_json_path"] = path
        if write_files:
            _dump(outputs / root / "files" / "api" / "api_result_bundle_1.json",
                  {"ok": True, "data": {"total": api_total, "rows": []}})
        files.append({"key": "api_result", "kind": "api", "path": path})
    if ledger is not None:
        (outputs / root).mkdir(parents=True, exist_ok=True)
        (outputs / root / "llm_calls.jsonl").write_text(
            "".join(json.dumps(e) + "\n" for e in ledger), encoding="utf-8")
    return {"variant_id": qid, "turn": "main", "query": f"How many for {qid}?", "task_id": "7",
            "session_id": "s", "status": status, "force_route": "ns", "force_parser_mode": arm,
            "route_obs": {"source": source, "route": route, "parser_mode": None, "engine": None,
                          "model_class": None, "reasoning": None},
            "query_complete": {"reply": reply, "debug": debug, "files": files},
            "elapsed_s": elapsed}


def _dump(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def _run(run_dir: Path, arms, questions, *, families=None):
    """questions: {qid: {arm: payload | None | ("error", payload_or_None) | ("outage", p)}}."""
    run_dir.mkdir(parents=True, exist_ok=True)
    records = []
    entries = {arm: [] for arm in arms}
    for i, (qid, per_arm) in enumerate(questions.items()):
        family = (families or {}).get(qid, "sample_search")
        rec = {"id": qid, "family": family, "first_arm": arms[i % len(arms)], "arms": {}}
        for arm in arms:
            if arm not in per_arm:
                continue
            spec = per_arm[arm]
            status, outage, payload = "passed", False, spec
            if isinstance(spec, tuple):
                kind, payload = spec
                status, outage = ("error", kind == "outage")
            entries[arm].append({"id": qid, "family": family, "tier": "full", "status": status,
                                 "outage": outage, "elapsed_s": 12.0, "task_ids": ["7"]})
            rec["arms"][arm] = {"status": status, "outage": outage, "task_ids": ["7"],
                                "elapsed_s": 12.0, "git_sha": "abc1234"}
            if payload is not None:
                _dump(run_dir / arm / "payloads" / ec.safe_name(qid) / "main.json", payload)
        records.append(rec)
    for arm in arms:
        _dump(run_dir / arm / "manifest.json",
              {"started_at": "t", "ended_at": "t", "tier": "full", "scope": f"arm:{arm}",
               "entries": entries[arm]})
    _dump(run_dir / "arms.json", {
        "run_meta": {"git_sha": "abc1234", "arms": list(arms), "cases_file": "c.json"},
        "progress": {"state": "complete", "turns_driven": 1, "questions": len(questions)},
        "questions": records})
    return run_dir


def _turn(**kw):
    return _truth_q("q", **kw).turns[0]


# ── each stage on its own evidence ───────────────────────────────────────────

def test_every_stage_passes_on_a_good_graph_turn(tmp_path):
    turn = _turn(attributes=("Organ",))
    cypher = "MATCH (s:Sample:T_TIS) WHERE s.Organ = 'Lung' RETURN count(s) AS n"
    p = _payload(tmp_path, "q", "graph", cypher=cypher)
    assert ec.stage_verdicts(p, turn, "graph", tmp_path) == {s: "pass" for s in ec.STAGES}


def test_every_stage_passes_on_a_good_api_turn(tmp_path):
    turn = _turn(attributes=("Organ",))
    v = ec.stage_verdicts(_payload(tmp_path, "q", "api"), turn, "api", tmp_path)
    assert v == {**{s: "pass" for s in ec.STAGES}, "context": "n/a"}


@pytest.mark.parametrize("arm,change,stage", [
    ("graph", {"source": "baml"}, "route"),
    ("graph", {"route": "container_cc"}, "route"),
    ("graph", {"note": False}, "switch"),
    ("api", {"mode": "graph_query", "chose": "graph_query"}, "switch"),
    ("graph", {"context": "fallback"}, "context"),
    ("graph", {"entity": ("MUS",)}, "entities"),
    ("graph", {"sampletype_code": "MUS", "entity": ("TIS",)}, "parser"),
    ("graph", {"cypher": "MATCH (s:Sample:T_MUS) RETURN count(s) AS n"}, "request"),
    ("graph", {"cypher": "MATCH (s:Sample:T_TIS) RETURN s LIMIT 5"}, "request"),
    ("graph", {"cypher": ""}, "request"),
    ("api", {"api_body": {"sampletype": "MUS", "attribute": "Organ"}}, "request"),
    ("graph", {"preview": ({"n": 99},)}, "engine_value"),
    ("api", {"api_total": 5}, "engine_value"),
    ("graph", {"reply": "There are 99 tissue samples."}, "reply"),
])
def test_each_stage_fails_on_its_own_evidence(tmp_path, arm, change, stage):
    turn = _turn(attributes=("Organ",) if arm == "api" else ())
    v = ec.stage_verdicts(_payload(tmp_path, "q", arm, **change), turn, arm, tmp_path)
    assert v[stage] == "fail", v
    assert ec.first_failing_stage(v) == stage


def test_a_missing_file_is_unobserved_not_a_failure(tmp_path):
    turn = _turn()
    v = ec.stage_verdicts(_payload(tmp_path, "q", "graph", write_files=False), turn, "graph",
                          tmp_path)
    assert v["engine_value"] == "unobserved"
    assert v["reply"] == "pass"
    v = ec.stage_verdicts(_payload(tmp_path, "q", "api", write_files=False), turn, "api", tmp_path)
    assert v["engine_value"] == "unobserved"
    assert set(ec.stage_verdicts(None, turn, "graph", tmp_path).values()) == {"unobserved"}


def test_stages_the_question_does_not_name_are_not_applicable(tmp_path):
    turn = _turn(types=(), value="Lung", kind="value")
    v = ec.stage_verdicts(_payload(tmp_path, "q", "graph", reply="Mostly Lung."), turn, "graph",
                          tmp_path)
    assert v["entities"] == "n/a" and v["parser"] == "n/a" and v["engine_value"] == "n/a"
    assert v["reply"] == "pass"


def test_a_non_retrieval_parser_mode_leaves_the_switch_and_engine_stages_out(tmp_path):
    turn = _turn()
    p = _payload(tmp_path, "q", "graph", mode="system_question", note=False)
    v = ec.stage_verdicts(p, turn, "graph", tmp_path)
    assert v["switch"] == v["context"] == v["request"] == v["engine_value"] == "n/a"


# ── the engine's own value and the path mapping ──────────────────────────────

def test_engine_value_reads_an_aggregate_and_a_total(tmp_path):
    agg = _payload(tmp_path, "q", "graph", preview=({"n": 22734},))
    assert ec.engine_value(agg, "graph", tmp_path) == 22734
    rows = _payload(tmp_path, "q", "graph", preview=({"id": 1, "t": "a"}, {"id": 2, "t": "b"}),
                    graph_total=5893)
    assert ec.engine_value(rows, "graph", tmp_path) == 5893
    in_file = _payload(tmp_path, "q", "graph", preview=({"id": 1, "t": "a"},), neo4j_total=17)
    assert ec.engine_value(in_file, "graph", tmp_path) == 17
    counted = _payload(tmp_path, "q", "graph", preview=({"id": 1, "t": "a"}, {"id": 2, "t": "b"}))
    assert ec.engine_value(counted, "graph", tmp_path) == 2
    assert ec.engine_value(_payload(tmp_path, "q", "api", api_total=46981), "api", tmp_path) == 46981


def test_container_paths_map_onto_the_outputs_root(tmp_path):
    root = tmp_path / "venue" / "outputs"
    assert ec.map_path("/venue/outputs/260915_100000_demo/files/api/a.json", root) == \
        root / "260915_100000_demo" / "files" / "api" / "a.json"
    assert ec.map_path("/app/outputs/260915_100000_demo/files/g.json", root) == \
        root / "260915_100000_demo" / "files" / "g.json"
    assert ec.map_path("/elsewhere/x.json", root) == Path("/elsewhere/x.json")


# ── cost ─────────────────────────────────────────────────────────────────────

PRICES = {"us.anthropic.claude-opus-4-7": {"input_per_m": 5.0, "output_per_m": 25.0},
          "gemini-3.5-flash": {"input_per_m": 0.30, "output_per_m": 2.50}}
LEDGER = [{"ts": "2026-09-15T10:00:01", "agent": "parser", "model": "us.anthropic.claude-opus-4-7",
           "prompt_tokens": 15500, "completion_tokens": 311},
          {"ts": "2026-09-15T10:00:03", "agent": "entity", "model": "gemini-3.5-flash",
           "prompt_tokens": 34200, "completion_tokens": 120},
          {"ts": "2026-09-15T10:00:04", "agent": "graph_agent", "model": "gemini-3.5-flash",
           "outcome": "timeout"}]
EXPECTED_COST = 15500 * 5 / 1e6 + 311 * 25 / 1e6 + 34200 * 0.30 / 1e6 + 120 * 2.50 / 1e6


def test_question_cost_prices_a_two_model_ledger(tmp_path):
    prices_file = tmp_path / "prices.json"
    prices_file.write_text(json.dumps(PRICES), encoding="utf-8")
    p = _payload(tmp_path, "q", "graph", ledger=LEDGER)
    cost = ec.question_cost(p, tmp_path, ec.load_prices(prices_file))
    assert cost == pytest.approx(EXPECTED_COST)


def test_question_cost_falls_back_to_the_global_ledger_window(tmp_path):
    outputs = tmp_path / "venue" / "outputs"
    root = "260915_100000_demo"
    p = _payload(outputs, "q", "graph", root=root, elapsed=20.0)
    stray = {"ts": "2026-09-15T10:05:00", "model": "gemini-3.5-flash", "prompt_tokens": 10 ** 9,
             "completion_tokens": 0}
    before = {**stray, "ts": "2026-09-15T09:59:59"}
    logs = tmp_path / "venue" / "logs"
    logs.mkdir(parents=True)
    (logs / "llm_calls.jsonl").write_text(
        "".join(json.dumps(e) + "\n" for e in [before, *LEDGER, stray]) + "not json\n",
        encoding="utf-8")
    assert ec.question_cost(p, outputs, PRICES) == pytest.approx(EXPECTED_COST)


def test_an_unpriced_model_or_no_ledger_is_unmeasured_not_zero(tmp_path):
    p = _payload(tmp_path, "q", "graph", ledger=LEDGER)
    assert ec.question_cost(p, tmp_path, {"gemini-3.5-flash": PRICES["gemini-3.5-flash"]}) is None
    assert ec.question_cost(_payload(tmp_path, "q", "graph"), tmp_path, PRICES) is None


# ── the statistics and the rules ─────────────────────────────────────────────

def test_mcnemar_exact():
    assert round(ec.mcnemar_exact(10, 2), 4) == 0.0386
    assert ec.mcnemar_exact(2, 10) == ec.mcnemar_exact(10, 2)
    assert ec.mcnemar_exact(0, 0) == 1.0
    assert ec.mcnemar_exact(3, 3) == 1.0


def _scores_a(g, a, *, n=100, b, c, g_failed=5, a_failed=5, g_s=10.0, a_s=10.0,
              g_cost=0.10, a_cost=0.10):
    return {"n": n, "b": b, "c": c,
            "g": {"n": n, "correct": g, "failed": g_failed, "median_s": g_s, "median_cost": g_cost},
            "a": {"n": n, "correct": a, "failed": a_failed, "median_s": a_s, "median_cost": a_cost}}


def test_verdict_a_supported():
    v = ec.verdict_a(_scores_a(70, 50, b=25, c=5))
    assert v["verdict"] == "SUPPORTED" and v["main_rule"]["holds"]
    assert v["main_rule"]["margin_points"] == pytest.approx(20.0)


def test_verdict_a_supported_with_costs():
    assert ec.verdict_a(_scores_a(70, 50, b=25, c=5, g_s=20.0))["verdict"] == "SUPPORTED WITH COSTS"
    assert ec.verdict_a(_scores_a(70, 50, b=25, c=5, g_cost=0.30))["verdict"] == \
        "SUPPORTED WITH COSTS"
    unmeasured = ec.verdict_a(_scores_a(70, 50, b=25, c=5, g_cost=None))
    assert unmeasured["verdict"] == "SUPPORTED WITH COSTS"
    assert unmeasured["guards"]["cost"]["holds"] is False


@pytest.mark.parametrize("kw", [
    dict(g=60, a=50, b=15, c=5),                                   # margin under 15 points
    dict(g=15, a=10, n=30, b=7, c=2, g_failed=1, a_failed=1),      # 16.7 points, p 0.18
    dict(g=70, a=50, b=25, c=5, g_failed=10),                      # G fails more than A + 2
    dict(g=3, a=1, b=2, c=0, n=4),                                 # 50 points on 2 pairs
])
def test_verdict_a_not_supported(kw):
    assert ec.verdict_a(_scores_a(**kw))["verdict"] == "NOT SUPPORTED"


def test_verdict_a_with_no_paired_question():
    v = ec.verdict_a(_scores_a(0, 0, n=0, b=0, c=0))
    assert v["verdict"] == "NOT SUPPORTED"


def test_verdict_b_both_outcomes():
    fams = {"graph_traversal": {"n": 60, "correct": 52, "failed": 1},
            "lineage_tree": {"n": 38, "correct": 31, "failed": 1},
            "tiny": {"n": 2, "correct": 0, "failed": 0}}
    ok = ec.verdict_b({"n": 100, "correct": 83, "failed": 2, "families": fams})
    assert ok["verdict"] == "STILL WORKS" and ok["failing_families"] == []
    low = dict(fams, lineage_tree={"n": 38, "correct": 20, "failed": 1,
                                   "first_failing_stages": {"request": 12}})
    not_yet = ec.verdict_b({"n": 100, "correct": 80, "failed": 2, "families": low})
    assert not_yet["verdict"] == "NOT YET"
    assert [f["family"] for f in not_yet["failing_families"]] == ["lineage_tree"]
    assert not_yet["failing_families"][0]["first_failing_stages"] == {"request": 12}
    failing = ec.verdict_b({"n": 100, "correct": 90, "failed": 6, "families": fams})
    assert failing["verdict"] == "NOT YET"
    assert ec.verdict_b({"n": 100, "correct": 79, "failed": 0, "families": fams})["verdict"] == \
        "NOT YET"


# ── loading and scoring whole runs ───────────────────────────────────────────

def _group_a_setup(tmp_path):
    outputs = tmp_path / "outputs"
    truth = _write_truth(tmp_path / "truth", "A", [
        _truth_q("q.both", value=107412),
        _truth_q("q.alt", value=22734,
                 alternates=[{"reading": "exact spelling", "required_numbers": [16841]}]),
        _truth_q("q.void", value=1),
        _truth_q("q.outage", value=1),
        _truth_q("q.system", value=1),
    ])
    run = _run(tmp_path / "runs" / "pilot-a", ["graph", "api"], {
        "q.both": {"graph": _payload(outputs, "q.both", "graph"),
                   "api": _payload(outputs, "q.both", "api", reply="About 99 samples.")},
        "q.alt": {"graph": _payload(outputs, "q.alt", "graph", reply="16,841 samples.",
                                    preview=({"n": 16841},)),
                  "api": _payload(outputs, "q.alt", "api", reply="22,734 samples.",
                                  api_total=22734)},
        "q.void": {"graph": _payload(outputs, "q.void", "graph", reply="1", context="fallback"),
                   "api": _payload(outputs, "q.void", "api", reply="1", api_total=1)},
        "q.outage": {"graph": _payload(outputs, "q.outage", "graph", reply="1"),
                     "api": ("outage", _payload(outputs, "q.outage", "api",
                                                reply="All provider fallbacks exhausted: x"))},
        "q.system": {"graph": _payload(outputs, "q.system", "graph", mode="system_question",
                                       note=False, reply="I answer catalog questions."),
                     "api": _payload(outputs, "q.system", "api", reply="1", api_total=1)},
    })
    return outputs, truth, run


def test_two_run_directories_merge_by_id_and_keep_repeats(tmp_path):
    outputs = tmp_path / "outputs"
    one = _run(tmp_path / "pilot", ["graph"], {"q1": {"graph": _payload(outputs, "q1", "graph")},
                                               "q2": {"graph": _payload(outputs, "q2", "graph")}})
    two = _run(tmp_path / "full", ["graph"], {"q3": {"graph": _payload(outputs, "q3", "graph")}})
    again = _run(tmp_path / "repeat", ["graph"], {"q1": {"graph": _payload(outputs, "q1", "graph")}})
    run = ec.load_runs([one, two, again])
    assert run.order == ["q1", "q2", "q3"]
    assert run.arms == ["graph"]
    assert [a.run_dir.name for a in run.attempts["q1"]["graph"]] == ["pilot", "repeat"]
    assert run.attempts["q3"]["graph"][0].payload("main")["variant_id"] == "q3"


def test_score_group_a_lists_void_outage_and_non_retrieval_and_splits_alternates(tmp_path):
    outputs, truth, run = _group_a_setup(tmp_path)
    doc = ec.score(ec.load_runs([run]), ec.load_truth_questions(truth, "A"), "a", outputs, PRICES)
    assert doc["lists"]["void"] == [{"id": "q.void", "arm": "graph",
                                     "reason": "graph_context 'fallback', not the live catalog"}]
    assert doc["lists"]["outage_rerun"] == ["q.outage"]
    assert [x["id"] for x in doc["lists"]["non_retrieval"]] == ["q.system"]
    with_alt = doc["scores"]["with_alternates"]
    without = doc["scores"]["without_alternates"]
    # Paired: q.both, q.alt, q.system (q.void is void on one arm, q.outage excluded).
    assert with_alt["n"] == without["n"] == 3
    assert with_alt["g"]["correct"] == 2 and without["g"]["correct"] == 1
    assert with_alt["a"]["correct"] == without["a"]["correct"] == 2
    # Discordant pairs: b is G right and A wrong, c the reverse. Without alternates
    # q.alt moves from concordant to c, so the split is lopsided and names each side.
    assert (with_alt["b"], with_alt["c"]) == (1, 1)
    assert (without["b"], without["c"]) == (1, 2)
    assert doc["verdict"]["with_alternates"]["verdict"] in ec.VERDICTS_A
    assert "without_alternates" in doc["verdict"]


def test_a_correct_reply_with_another_total_is_a_contradiction_suspect(tmp_path):
    outputs = tmp_path / "outputs"
    truth = _write_truth(tmp_path / "truth", "B", [
        _truth_q("q1", "graph_traversal", group="B", value=22734)])
    run = _run(tmp_path / "run", ["graph"], {"q1": {"graph": _payload(
        outputs, "q1", "graph", preview=({"n": 22734},),
        reply="There are 22,734 lung samples. In total, 46,981 samples mention lung.")}})
    doc = ec.score(ec.load_runs([run]), ec.load_truth_questions(truth, "B"), "b", outputs, PRICES)
    row = doc["questions"][0]
    assert row["outcome"] == "correct" and row["contradiction_suspect"] is True
    assert doc["lists"]["contradiction_suspect"] == ["q1"]


def test_main_writes_the_three_files_verdict_first(tmp_path, capsys):
    outputs, truth, run = _group_a_setup(tmp_path)
    out = tmp_path / "result"
    rc = ec.main(["--group", "a", "--run", str(run), "--truth", str(truth),
                  "--outputs", str(outputs), "--out", str(out)])
    assert rc == 0
    md = (out / "compare.md").read_text(encoding="utf-8")
    first = md.splitlines()[0]
    assert first.startswith("# Group A: ") and any(v in first for v in ec.VERDICTS_A)
    assert "Without alternates:" in md.split("\n## ", 1)[0]
    assert "| q.both | sample_search | corpus | api | wrong | reply |" in md.replace("  ", " ")
    doc = json.loads((out / "compare.json").read_text(encoding="utf-8"))
    assert doc["group"] == "A"
    with (out / "questions.csv").open(encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    assert {r["id"] for r in rows} >= {"q.both", "q.alt"}
    assert {"first_failing_stage", "outcome", "arm", "cost_usd"} <= set(rows[0])
    for name in ("compare.md", "compare.json", "questions.csv"):
        assert oct(os.stat(out / name).st_mode & 0o777) == oct(0o600)


def test_group_b_is_reported_per_family_and_for_the_rest_routed_questions(tmp_path):
    outputs = tmp_path / "outputs"
    qs = [_truth_q(f"g{i}", "graph_traversal", group="B", value=10) for i in range(5)]
    qs += [_truth_q(f"t{i}", "lineage_tree", group="B", value=10,
                    flags=["rest_routed_today"] if i < 3 else []) for i in range(5)]
    truth = _write_truth(tmp_path / "truth", "B", qs)
    per = {}
    for q in qs:
        good = not (q.id.startswith("t") and q.id in ("t0", "t1", "t2"))
        per[q.id] = {"graph": _payload(outputs, q.id, "graph", preview=({"n": 10},),
                                       reply="There are 10 samples." if good else "I found 3.",
                                       cypher="MATCH (s:Sample:T_TIS) RETURN count(s) AS n"
                                       if good else "MATCH (s:Sample:T_MUS) RETURN count(s)")}
    run = _run(tmp_path / "run", ["graph"], per,
               families={q.id: q.family for q in qs})
    out = tmp_path / "result"
    assert ec.main(["--group", "b", "--run", str(run), "--truth", str(truth),
                    "--outputs", str(outputs), "--out", str(out)]) == 0
    md = (out / "compare.md").read_text(encoding="utf-8")
    assert md.splitlines()[0] == "# Group B: NOT YET"
    assert "| graph_traversal | 5 | 5 | 100% |" in md
    assert "| lineage_tree | 5 | 2 | 40% |" in md
    assert "request" in md.split("## Per family", 1)[1]
    assert "REST-routed" in md and "0 of 3" in md


def _tree(root: Path) -> dict:
    return {str(p.relative_to(root)): p.stat().st_mtime_ns for p in root.rglob("*")}


def test_cost_only_and_checks_print_and_write_nothing(tmp_path, capsys):
    outputs = tmp_path / "outputs"
    truth = _write_truth(tmp_path / "truth", "A", [_truth_q("q1"), _truth_q("q2")])
    run = _run(tmp_path / "run", ["graph", "api"], {
        "q1": {"graph": _payload(outputs, "q1", "graph", ledger=LEDGER),
               "api": _payload(outputs, "q1", "api", ledger=LEDGER)},
        "q2": {"graph": _payload(outputs, "q2", "graph", context="fallback"),
               "api": ("error", None)},
    })
    prices = tmp_path / "prices.json"
    prices.write_text(json.dumps(PRICES), encoding="utf-8")
    before = _tree(tmp_path)
    base = ["--group", "a", "--run", str(run), "--truth", str(truth), "--outputs", str(outputs),
            "--prices", str(prices)]
    assert ec.main(base + ["--cost-only"]) == 0
    cost_out = capsys.readouterr().out
    assert f"${2 * EXPECTED_COST:.4f}" in cost_out and "per turn" in cost_out
    assert ec.main(base + ["--checks"]) == 0
    checks = capsys.readouterr().out
    assert "infrastructure errors: 1" in checks
    assert "fallback contexts: 1" in checks
    assert "outages: 0" in checks and "unobserved" in checks
    assert _tree(tmp_path) == before


def test_the_scorer_pins_the_harness_names():
    from NessieAI.tests.nessie_tests import outage, preflight, runner
    assert ec.FORCE_NOTE_MARKER == preflight.FORCE_NOTE_MARKER
    assert ec.PROVIDER_OUTAGE_MARKER == outage.PROVIDER_OUTAGE_MARKER
    for name in ("q.one", "a b/c", "..", "", "x!!y"):
        assert ec.safe_name(name) == runner._safe_name(name)
    assert ec.PAYLOADS_DIR == runner.PAYLOADS_DIR and ec.ARMS_FILE == runner.ARMS_FILE

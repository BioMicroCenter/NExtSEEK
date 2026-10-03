"""JevLevROUTING U3: build_dataset.py on a tiny made-up turns file (SPEC s8). No real text, split.py stubbed."""
import importlib.util
import json
import pathlib
import sys

import pytest

from NessieAI.router.laya_common import condense, norm_text_hash

REPO = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "scripts/laya"))
_spec = importlib.util.spec_from_file_location("laya_build_dataset", REPO / "scripts/laya/build_dataset.py")
bd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bd)


@pytest.fixture(autouse=True)
def stub_split(monkeypatch):
    monkeypatch.setattr(bd.split, "heldout_bucket", lambda fam, ent: fam == "held_fam" or ent == "SRP")
    monkeypatch.setattr(bd.split, "calib_bucket", lambda chat: chat.startswith("calib"))
    monkeypatch.setattr(bd.split, "load_manifest", lambda p: set(p))


def T(i, sess, q, route, src="baml", created=None, status="completed", **kw):
    return dict(id=str(i), session=sess, created=created or f"2026-10-01 00:00:{i:02d}", q=q, route=route,
                src=src, status=status, **kw)


CORPUS = {"families": {
    "fam_a": {"variants": [
        {"id": "a.1", "family": "fam_a", "status": "active", "turns": [
            {"query": "Alpha question one", "pass_criteria": [{"field": "route", "op": "eq", "value": "container_cc"}]}]},
        {"id": "a.2", "family": "fam_a", "status": "active", "turns": [
            {"query": "Beta either one", "pass_criteria": [
                {"field": "route", "op": "matches_re", "value": "(nextseek_query|container_cc)"}]}]},
        {"id": "a.3", "family": "fam_a", "status": "retired", "turns": [
            {"query": "Retired one", "pass_criteria": [{"field": "route", "op": "eq", "value": "container_cc"}]}]},
        {"id": "a.4", "family": "fam_a", "status": "active", "turns": [{"query": "Family default only"}]}]}}}
CAPS = {"routes": [{"task_families": [{"name": "held_fam", "example_queries": ["Seen in held family"]},
                                      {"name": "ok_fam", "example_queries": ["Seen in a kept family"]}]}]}


def rows_by_q(rows):
    return {r["query"]: r for r in rows}


def build(turns, manifest=frozenset(), extra=(), evidence=None):
    return bd.build_rows(turns, CORPUS, CAPS, set(manifest), list(extra), evidence)


def test_teacher_rules_and_exclusions():
    turns = [T(1, "s1", "q baml", "nextseek_query"),
             T(2, "s2", "q followup", "nextseek_query", src="followup", attempted_route="container_cc"),
             T(3, "s3", "q sticky", "container_cc", src="sticky", attempted_route="container_cc"),
             T(4, "s4", "q pipeline", "container_cc", src="pipeline"),
             T(5, "s5", "q posterior", "container_cc", src="posterior"),
             T(6, "s6", "q forced", "container_cc", src="forced"),
             T(7, "s7", "q heur", "nextseek_query", src="heuristic"),
             T(8, "s8", "q none", None, src="none"),
             T(9, "s9", "q laya", "container_cc", src="laya"),
             T(10, "s10", "q cc unavailable", "nextseek_query", src="cc_unavailable")]
    r = rows_by_q(build(turns))
    assert set(r) == {"q baml", "q followup", "q sticky", "q pipeline", "q cc unavailable"}
    assert r["q followup"]["teacher_route"] == "container_cc"
    assert r["q cc unavailable"]["teacher_route"] == "nextseek_query"


def test_soft_teacher_is_the_run_share():
    turns = [T(1, "s1", "same q", "nextseek_query"), T(2, "s2", "same q", "nextseek_query"),
             T(3, "s3", "same q", "container_cc")]
    (row,) = build(turns)
    assert row["teacher_route"] == "nextseek_query"
    assert row["soft_teacher"] == {"nextseek_query": pytest.approx(2 / 3), "container_cc": pytest.approx(1 / 3)}
    (solo,) = build([T(1, "s1", "one run", "nextseek_query")])
    assert solo["soft_teacher"] is None


def test_case_level_truth_replaces_teacher_either_keeps_it_and_retired_is_ignored():
    turns = [T(1, "s1", "Alpha question one", "nextseek_query"), T(2, "s2", "beta  EITHER one", "nextseek_query"),
             T(3, "s3", "Retired one", "nextseek_query"), T(4, "s4", "Family default only", "nextseek_query")]
    r = rows_by_q(build(turns))
    a = r["Alpha question one"]
    assert (a["teacher_route"], a["truth_route"], a["either"], a["family"]) == ("container_cc", "container_cc", False, "fam_a")
    b = r["beta  EITHER one"]
    assert (b["teacher_route"], b["truth_route"], b["either"]) == ("nextseek_query", None, True)
    assert r["Retired one"]["truth_route"] is None
    assert r["Family default only"]["truth_route"] is None and r["Family default only"]["family"] == "fam_a"


def test_paired_evidence_only_where_no_assertion():
    ev = {"records": [{"query_text": "Alpha question one", "ns": {"success": True}, "cc": {"success": False}},
                      {"query_text": "evidence only", "ns": {"success": False}, "cc": {"success": True}},
                      {"query_text": "both ok", "ns": {"success": True}, "cc": {"success": True}}]}
    turns = [T(1, "s1", "Alpha question one", "nextseek_query"), T(2, "s2", "evidence only", "nextseek_query"),
             T(3, "s3", "both ok", "nextseek_query")]
    r = rows_by_q(build(turns, evidence=ev))
    assert r["Alpha question one"]["truth_route"] == "container_cc"
    assert r["evidence only"]["truth_route"] == "container_cc"
    assert r["both ok"]["truth_route"] is None


def test_prompt_seen_slice_and_held_out_family_example_removed():
    turns = [T(1, "s1", "seen in a kept family", "nextseek_query"), T(2, "s2", "Seen in held family", "nextseek_query")]
    r = rows_by_q(build(turns))
    assert r["seen in a kept family"]["slice"] == "prompt_seen"
    assert "Seen in held family" not in r


def test_manifest_hash_removed_and_calib_split_by_chat():
    turns = [T(1, "calib-1", "calibrated q", "nextseek_query"), T(2, "s2", "train q", "nextseek_query"),
             T(3, "s3", "Frozen Q", "nextseek_query")]
    r = rows_by_q(build(turns, manifest={norm_text_hash("frozen q")}))
    assert "Frozen Q" not in r
    assert r["calibrated q"]["slice"] == "calib" and r["train q"]["slice"] == "train"


def test_history_goes_through_build_history_and_condense_and_is_per_session():
    turns = [T(1, "s1", "first", "nextseek_query"), T(2, "s1", "second", "container_cc"),
             T(3, "s1", "third", "nextseek_query", status="error"), T(4, "s2", "other chat", "nextseek_query")]
    r = rows_by_q(build(turns))
    assert r["first"]["history"] == []
    assert r["other chat"]["history"] == []
    h = r["third"]["history"]
    assert [x["router_choice"] for x in h] == ["nextseek_query", "container_cc"]
    assert set(h[0]) == {"user_message", "router_choice", "status"}
    # the same call a live turn makes gives the model text; no reply text can be in it
    from NessieAI.router.router_context import HistoryTurn
    hist = [HistoryTurn(position=i, **x) for i, x in enumerate(h, 1)]
    assert condense("third", hist).startswith("Current message: third\nPrevious message (container_cc, completed): second")


def test_history_window_is_five_turns():
    turns = [T(i, "s1", f"q{i}", "nextseek_query") for i in range(1, 9)]
    r = rows_by_q(build(turns))
    assert len(r["q8"]["history"]) == 5 and r["q8"]["history"][0]["user_message"] == "q3"


def test_extra_rows_are_synthetic_training_rows():
    extra = [{"query": "who won the cup", "route": "unrelated"}]
    (row,) = build([], extra=extra)
    assert (row["teacher_route"], row["family"], row["slice"]) == ("unrelated", "synthetic", "train")


def test_cli_refuses_to_write_inside_the_repo(tmp_path):
    with pytest.raises(SystemExit, match="public repo"):
        bd.main(["--turns", "x", "--corpus", "x", "--manifest", "x", "--out", str(REPO / "rows.jsonl")])


def test_cli_round_trip(tmp_path, monkeypatch):
    for n, d in {"t.json": [T(1, "s1", "hello there", "nextseek_query")], "c.json": CORPUS, "k.json": CAPS}.items():
        (tmp_path / n).write_text(json.dumps(d))
    monkeypatch.setattr(bd.split, "load_manifest", lambda p: set())
    out = tmp_path / "view.jsonl"
    assert bd.main(["--turns", str(tmp_path / "t.json"), "--corpus", str(tmp_path / "c.json"),
                    "--caps", str(tmp_path / "k.json"), "--manifest", "none", "--out", str(out)]) == 0
    (line,) = out.read_text().splitlines()
    assert json.loads(line)["query"] == "hello there"

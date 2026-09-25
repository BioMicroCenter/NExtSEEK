"""compute_over_rows: the follow-up loop computes over rows it already has, and says what it matched."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from chat_nextseek import orchestrator as orch
from chat_nextseek.agents.followup import (
    MAX_ITER, build_followup_tool_schemas, resolve_followup_outcome, run_followup,
)
from chat_nextseek.agents.followup_compute import COMPUTE_ROWS_MAX, compute_over_rows
from chat_nextseek.graph_review import review_compute
from chat_nextseek.schemas.graph import GraphAgentPlan


def _rows_731():
    return [{"uuid": f"D.SEQ-{i}{'SHA' if i % 7 else 'XYZ'}", "type": "D.SEQ", "Sex": "F" if i % 2 else "M"}
            for i in range(731)]


def _compute(rows, *, total=None, complete=True, where=None, group_by=None, code=None):
    return compute_over_rows(rows=rows, total=len(rows) if total is None else total, complete=complete,
                             where=where, group_by=group_by, code=code)


SMOKERS = (["Current smoker"] * 122 + ["Current reformed smoker for > 15 years"] * 311
           + ["Lifelong non-smoker"] * 152)
CONVERTERS = ["Non-converter"] * 57 + ["Converter"] * 32 + ["Reverter"] * 9


# ---------------------------------------------------------------- the tool

def test_where_contains_counts_every_matching_row():
    out = _compute(_rows_731(), where=[{"column": "uuid", "op": "contains", "value": "SHA"}])
    assert out["ok"] is True and out["count"] == 626 and out["where"][0]["rows_after"] == 626


def test_group_by_returns_every_group_largest_first():
    out = _compute(_rows_731(), group_by=["Sex"])
    assert out["groups"] == [{"Sex": "M", "n": 366}, {"Sex": "F", "n": 365}] and out["groups_total"] == 2


def test_present_counts_rows_that_hold_a_value():
    rows = [{"uuid": f"CEL-{i}", "Treatment": "DMSO" if i < 3 else ""} for i in range(4)]
    assert _compute(rows, where=[{"column": "Treatment", "op": "present"}])["count"] == 3


def test_a_metadata_field_is_read_from_a_dict_or_a_json_string():
    rows = [{"uid": f"LIB-{i}", "sample_type": "DNA",
             "json_metadata": json.dumps({"Type": "Illumina Library"}) if i % 2 else {"Type": "Illumina Library"}}
            for i in range(1000)]
    out = _compute(rows, where=[{"column": "json_metadata.Type", "op": "equals", "value": "illumina library"}],
                   group_by=["sample_type"])
    assert out["count"] == 1000 and out["groups"] == [{"sample_type": "DNA", "n": 1000}]


def test_code_runs_over_the_rows_left_after_where():
    out = _compute(_rows_731(), where=[{"column": "Sex", "op": "equals", "value": "f"}],
                   code="result = {'sha': len([r for r in rows if 'SHA' in r['uuid']])}")
    assert out["count"] == 365 and out["result"] == {"sha": 313}


def test_contains_reports_the_values_it_matched():
    rows = [{"uuid": f"PAT-{i}", "TobaccoSmokingStatus": s} for i, s in enumerate(SMOKERS)]
    out = _compute(rows, where=[{"column": "TobaccoSmokingStatus", "op": "contains", "value": "current"}])
    assert out["count"] == 433
    assert out["where"][0]["matched_values"] == [["Current reformed smoker for > 15 years", 311],
                                                 ["Current smoker", 122]]


def test_a_capped_source_is_refused_and_counts_nothing():
    rows = [{"uuid": f"TIS-{i}", "Tissue": "liver" if i % 3 == 0 else "lung"} for i in range(5000)]
    out = _compute(rows, total=36622, complete=False, where=[{"column": "Tissue", "op": "equals", "value": "liver"}])
    assert out["ok"] is False and out["needs_query"] is True and "5,000 of 36,622" in out["error"]
    assert "count" not in out and "groups" not in out


def test_a_result_that_kept_no_rows_is_refused():
    out = _compute([], total=6479, complete=False, group_by=["Lab"])
    assert out["ok"] is False and out["needs_query"] is True


def test_a_column_the_rows_do_not_hold_is_named_not_counted_as_zero():
    rows = [{"uuid": f"NHP-{i}", "type": "NHP", "Species": "Macaca mulatta"} for i in range(60)]
    out = _compute(rows, where=[{"column": "CD8Depletion", "op": "present"}])
    assert out["ok"] is False and out["needs_query"] is True
    assert "CD8Depletion" in out["error"] and "Species" in out["error"] and "count" not in out


def test_a_column_name_matches_regardless_of_case():
    rows = [{"uuid": f"NHP-{i}", "Species": "Macaca mulatta"} for i in range(3)]
    assert _compute(rows, group_by=["species"])["groups"] == [{"Species": "Macaca mulatta", "n": 3}]


def test_an_oversized_result_is_withheld_with_its_size():
    out = _compute(_rows_731(), code="result = {'uids': [r['uuid'] for r in rows]}")
    assert out["ok"] is True and out["result"] is None and out["result_truncated"] is True
    assert out["result_chars"] > 6000


def test_more_rows_than_the_tool_takes_are_refused():
    out = _compute([{"uuid": f"S-{i}"} for i in range(COMPUTE_ROWS_MAX + 1)], code="result = {}")
    assert out["ok"] is False and out["needs_query"] is True


def test_a_code_error_is_reported_not_raised():
    out = _compute(_rows_731(), code="import os\nresult = {}")
    assert out["ok"] is False and not out.get("needs_query") and "Disallowed" in out["error"]


# ---------------------------------------------------------------- the review

def _review(payload, *, question="q", source_total=None, target=3, newest=3, kind="stored"):
    return review_compute(question=question, source_kind=kind, source_total=source_total,
                          target_bundle_id=target, newest_bundle_id=newest, payload=payload)


def _fired(review):
    return {c.name for c in review.checks if c.fired}


def test_a_plain_count_is_quiet():
    r = _review(_compute(_rows_731(), where=[{"column": "uuid", "op": "contains", "value": "SHA"}]),
                question="Which of those have SHA in their UID?", source_total=731)
    assert r.verdict == "ok" and _fired(r) == set() and r.disclosure is None


def test_a_negated_match_is_disclosed_with_counts():
    rows = [{"uuid": f"PAT-{i}", "Classification": c} for i, c in enumerate(CONVERTERS)]
    r = _review(_compute(rows, where=[{"column": "Classification", "op": "contains", "value": "convert"}]),
                question="Of those, how many converted?", source_total=98)
    assert "negated_value" in _fired(r) and r.verdict == "suggest"
    assert "Non-converter 57" in r.disclosure and "Converter 32" in r.disclosure


def test_several_matched_values_are_disclosed_even_when_one_nests_in_another():
    rows = [{"uuid": f"PAT-{i}", "TobaccoSmokingStatus": s} for i, s in enumerate(SMOKERS)]
    r = _review(_compute(rows, where=[{"column": "TobaccoSmokingStatus", "op": "contains", "value": "current"}]),
                question="Of those, how many are current smokers?", source_total=585)
    assert "value_split" in _fired(r) and "Current smoker 122" in r.disclosure and "311" in r.disclosure


def test_unique_values_are_not_a_split():
    r = _review(_compute(_rows_731(), where=[{"column": "uuid", "op": "contains", "value": "SHA"}]), source_total=731)
    assert "value_split" not in _fired(r)


def test_a_wrong_number_in_the_question_fires_premise():
    rows = [{"uuid": f"MUS-{i}", "Genotype": f"CC0{i % 23:02d}"} for i in range(745)]
    r = _review(_compute(rows, group_by=["Genotype"]),
                question="how many of these 1,206 mouse sample records have transcriptomic data?", source_total=745)
    assert "premise" in _fired(r) and "745" in r.disclosure


def test_those_bound_to_an_older_result_fires_binding():
    rows = [{"uuid": f"CEL-{i}", "Treatment": "DMSO"} for i in range(4)]
    r = _review(_compute(rows, where=[{"column": "Treatment", "op": "present"}]),
                question="Of those, how many have a treatment recorded?", source_total=4, target=1, newest=3)
    assert "binding" in _fired(r)


def test_a_zero_over_rows_is_a_note_naming_the_columns():
    rows = [{"uuid": f"NHP-{i}", "Species": "Macaca mulatta", "Notes": "baseline"} for i in range(60)]
    r = _review(_compute(rows, where=[{"column": "Notes", "op": "contains", "value": "cd8"}]),
                question="Which of those monkeys are depleted of CD8?", source_total=60)
    assert "snapshot_zero" in _fired(r) and r.verdict == "note"
    assert "Species" in r.disclosure and "Notes" in r.disclosure


def test_a_failed_computation_is_a_note_and_a_refusal_is_not():
    assert "breakage" in _fired(_review(_compute(_rows_731(), code="import os\nresult = {}"), source_total=731))
    assert "breakage" not in _fired(_review(_compute([], total=6479, complete=False, group_by=["Lab"]),
                                            source_total=6479))


def test_the_review_never_raises():
    r = _review({"ok": True, "where": "not a list", "count": "x"})
    assert r.verdict == "ok" and r.error


# ---------------------------------------------------------------- the loop (copy of test_followup_agent.py:32-72)

class _ScriptedClient:
    provider = "bedrock"

    def __init__(self, script):
        self.script, self.turns = list(script), []

    def chat_with_tools(self, *, messages, tools, system, model, **kwargs):
        self.turns.append({"messages": json.loads(json.dumps(messages, default=str)), "tools": tools})
        return self.script.pop(0)


class _Cfg:
    LOG_DIR = "/tmp"
    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP = {None: None}
    AGENT_MODEL_CATALOG: dict = {}

    def __init__(self, client):
        self._client, self.LLM_CLIENT, self.LLM_MODEL, self.LLM_CLIENTS = client, client, "m", {"anth": client}

    def get_agent_model(self, label):
        return self._client, "us.anthropic.claude-opus-4-7", None

    def _load_prompt(self, name):
        return "SYSTEM PROMPT"


def _tool_use(name, payload, tid="t1"):
    return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": tid, "name": name, "input": payload}]}


def _bundle():
    return {"id": 1, "mode": "graph_query", "user_query": "scRNA-seq records in the project",
            "graph_result": {"ok": True, "count": 731, "total": 731, "data": _rows_731()}}


def test_compute_is_offered_only_with_a_seam():
    assert "compute_over_rows" not in [t["name"] for t in build_followup_tool_schemas()]
    assert "compute_over_rows" in [t["name"] for t in build_followup_tool_schemas(compute=True)]
    assert [t["name"] for t in build_followup_tool_schemas(final=True, compute=True)] == ["answer"]


def test_a_compute_call_reaches_the_seam_and_its_payload_reaches_the_model():
    calls = []

    def compute(**kw):
        calls.append(kw)
        return {"ok": True, "count": 626, "review": {"verdict": "ok"}}

    where = [{"column": "uuid", "op": "contains", "value": "SHA"}]
    client = _ScriptedClient([
        _tool_use("compute_over_rows", {"source": "stored", "where": where}),
        _tool_use("answer", {"text": "626 of the 731 have SHA in their UID.", "caveats": []}, tid="t2"),
    ])
    out = run_followup(_Cfg(client), user_text="Which of those have SHA in their UID?", bundle=_bundle(),
                       run_query=lambda **kw: {}, compute=compute)
    assert out["reply"].startswith("626")
    assert calls == [{"source": "stored", "where": where, "group_by": None, "code": None}]
    assert out["computes"][0]["result"]["count"] == 626
    assert '\\"count\\": 626' in json.dumps(client.turns[-1]["messages"])


def test_the_answer_only_pass_runs_after_computes_with_no_query():
    script = [_tool_use("compute_over_rows", {"source": "stored"})] * MAX_ITER
    script.append(_tool_use("answer", {"text": "731.", "caveats": []}))
    client = _ScriptedClient(script)
    out = run_followup(_Cfg(client), user_text="q", bundle=_bundle(), run_query=lambda **kw: {},
                       compute=lambda **kw: {"ok": True, "count": 731})
    assert out["reply"] == "731." and [t["name"] for t in client.turns[-1]["tools"]] == ["answer"]


def test_a_loop_that_computed_but_did_not_answer_keeps_what_it_found():
    reply, may_use_stored = resolve_followup_outcome(
        {"reply": None, "queries": [], "tool_calls": ["compute_over_rows"],
         "computes": [{"source": "stored", "result": {"ok": True, "count": 626}}]})
    assert may_use_stored is False and "626" in reply


# ---------------------------------------------------------------- the orchestrator seam

def _seam(bundle, tmp_path, calls, *, before=None):
    captured = {}

    def fake_followup(config, *, user_text, bundle, run_query, compute, log_dir, **_):
        if before:
            before(run_query)
        captured["payloads"] = [compute(**c) for c in calls]
        return {"reply": "x", "queries": [], "computes": [], "tool_calls": []}

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(orch, "run_followup", fake_followup)
        out = orch._run_followup_agent(MagicMock(LOG_DIR=str(tmp_path)), session={"results_history": [bundle]},
                                       user_text="Which of those have SHA in their UID?", bundle=bundle,
                                       log_dir=str(tmp_path))
    return captured["payloads"], out


def test_the_seam_computes_over_the_stored_rows_reviews_and_keeps_an_artifact(tmp_path):
    [p], out = _seam(_bundle(), tmp_path, [dict(source="stored", group_by=None, code=None,
                                                where=[{"column": "uuid", "op": "contains", "value": "SHA"}])])
    assert p["ok"] is True and p["count"] == 626
    assert p["source"] == {"kind": "stored", "bundle_id": 1, "rows_in": 731, "total": 731, "complete": True}
    assert p["review"]["verdict"] == "ok"
    path = Path(out["compute_runs"][0]["artifact"]["path"])
    assert json.loads(path.read_text())["payload"]["count"] == 626


def test_the_seam_refuses_a_capped_stored_result(tmp_path):
    bundle = {"id": 1, "mode": "graph_query", "user_query": "tissues in the project",
              "graph_result": {"ok": True, "count": 5000, "total": 36622, "truncated": True,
                               "data": [{"uuid": f"TIS-{i}", "Tissue": "liver"} for i in range(5000)]},
              "graph_plan": {"cypher": "MATCH (s:T_TIS) RETURN s.uuid AS uuid, s.Tissue AS Tissue", "parameters": {}}}
    [p], _ = _seam(bundle, tmp_path, [dict(source="stored", group_by=None, code=None,
                                           where=[{"column": "Tissue", "op": "equals", "value": "liver"}])])
    assert p["ok"] is False and p["needs_query"] is True and "count" not in p
    assert p["stored_query_available"] is True


def test_last_query_before_any_query_is_refused(tmp_path):
    [p], _ = _seam(_bundle(), tmp_path, [dict(source="last_query", where=None, group_by=["type"], code=None)])
    assert p["ok"] is False and "no query" in p["error"]


def test_the_seam_computes_over_every_row_of_the_loops_last_query(tmp_path, monkeypatch):
    rows = [{"type": f"T{i}", "n": 100 - i} for i in range(60)]
    cypher = "MATCH (m:Sample) WHERE m.uuid IN $uids RETURN m.type AS type, count(*) AS n"
    # Stub the graph the way test_followup_rows_and_file.py does after Task 13 (graph_agent, tool_neo4j_query,
    # and whatever Task 13 added for the reviewer's live values).
    monkeypatch.setattr(orch, "graph_agent", lambda *a, **k: GraphAgentPlan(cypher=cypher, context_mode="catalog"))
    monkeypatch.setattr(orch, "tool_neo4j_query", lambda *a, **k: {
        "ok": True, "count": 60, "total": 60, "truncated": False, "data": rows, "cypher": cypher,
        "submitted_cypher": cypher, "parameters": {}, "counters": {}, "scope": {"decision": "proven"}})
    [p], _ = _seam(_bundle(), tmp_path, [dict(source="last_query", where=None, group_by=None,
                                              code="result = {'types': len(rows)}")],
                   before=lambda run_query: run_query(question="downstream types", seed_uids=["D.SEQ-1SHA"]))
    assert p["ok"] is True and p["result"] == {"types": 60}
    assert p["source"]["kind"] == "last_query" and p["source"]["rows_in"] == 60


# ---------------------------------------------------------------- completeness, nulls and what a refusal claims

def test_a_null_value_is_in_no_group_and_is_counted_apart():
    rows = [{"uuid": f"MUS-{i}", "Lab": None if i % 4 == 0 else ("ABC" if i % 2 else "DEF")} for i in range(40)]
    out = _compute(rows, group_by=["Lab"])
    assert out["groups"] == [{"Lab": "ABC", "n": 20}, {"Lab": "DEF", "n": 10}]
    assert out["groups_total"] == 2 and out["group_nulls"] == 10 and out["count"] == 40


def test_a_contains_never_matches_a_null_and_counts_distinct_values_without_it():
    rows = [{"uuid": f"S-{i}", "Notes": None if i < 5 else "none recorded"} for i in range(10)]
    out = _compute(rows, where=[{"column": "Notes", "op": "contains", "value": "none"}])
    assert out["count"] == 5 and out["where"][0]["distinct_matched"] == 1


def test_rows_with_no_known_total_say_they_cover_only_the_rows_in_hand():
    rows = [{"uid": f"L-{i}", "Lab": "ABC"} for i in range(40)]
    out = compute_over_rows(rows=rows, total=None, complete=True, where=None, group_by=["Lab"], code=None)
    assert out["ok"] is True and "40 rows in hand" in out["scope_note"]
    assert "scope_note" not in _compute(rows, group_by=["Lab"])


def test_a_malformed_filter_is_an_error_not_a_reason_to_query():
    out = _compute(_rows_731(), where=[{"column": "Sex", "op": "equals"}])
    assert out["ok"] is False and not out.get("needs_query") and "value" in out["error"]


def test_the_review_discloses_a_negated_match_once():
    rows = [{"uuid": f"PAT-{i}", "Classification": c} for i, c in enumerate(CONVERTERS)]
    r = _review(_compute(rows, where=[{"column": "Classification", "op": "contains", "value": "convert"}]),
                source_total=98)
    assert r.disclosure.count("Non-converter 57") == 1


def test_a_capped_copy_with_no_rebuildable_query_is_not_promised_a_rebuild(tmp_path):
    cypher = "MATCH (s:Sample) WHERE s.uuid IN $uids RETURN s.uuid AS uuid, s.Tissue AS Tissue"
    bundle = {"id": 2, "mode": "graph_query", "user_query": "tissues of those",
              "graph_result": {"ok": True, "count": 500, "total": 2000, "truncated": True,
                               "data": [{"uuid": f"TIS-{i}", "Tissue": "liver"} for i in range(500)]},
              "graph_plan": {"cypher": cypher, "parameters": {}}}
    [p], _ = _seam(bundle, tmp_path, [dict(source="stored", group_by=["Tissue"], code=None, where=None)])
    assert p["ok"] is False and p["needs_query"] is True and p["stored_query_available"] is False
    assert "rebuilds the whole set" not in p["error"] and "cannot cover the whole" in p["error"]


def test_the_seam_says_when_the_stored_rows_have_no_known_total(tmp_path):
    bundle = {"id": 1, "mode": "api_query", "user_query": "samples",
              "memory_payload": {"data": {"rows": [{"uid": f"L-{i}", "Lab": "ABC"} for i in range(40)]}}}
    [p], _ = _seam(bundle, tmp_path, [dict(source="stored", where=None, group_by=["Lab"], code=None)])
    assert p["ok"] is True and p["count"] == 40 and "40 rows in hand" in p["scope_note"]
    assert p["source"]["total"] is None


def test_an_aggregate_is_not_counted_as_one_row(tmp_path):
    bundle = {"id": 1, "mode": "graph_query", "user_query": "how many mass spec samples",
              "graph_result": {"ok": True, "count": 1, "total": 1, "data": [{"n": 890}]}}
    [p], _ = _seam(bundle, tmp_path, [dict(source="stored", where=[{"column": "n", "op": "present"}],
                                           group_by=None, code=None)])
    assert p["ok"] is False and p["needs_query"] is True and "aggregate_values" in p["error"]
    assert "count" not in p


def test_a_plan_steps_count_is_the_total_of_its_rows():
    from chat_nextseek.agents.followup import describe_stored_result
    bundle = {"id": 4, "mode": "plan", "step_results": {
        1: {"ok": True, "output": {"data": [{"uid": f"S-{i}"} for i in range(20)], "count": 250}}}}
    described = describe_stored_result(bundle)
    assert described["rows_stored"] == 20 and described["total"] == 250 and described["capped"] is True


def test_the_loop_answers_from_its_last_computation_when_it_runs_out():
    reply, _ = resolve_followup_outcome({"reply": None, "queries": [], "computes": [
        {"source": "stored", "result": {"ok": True, "count": 0}}]})
    assert "does not mean that none exist" in reply
    reply, _ = resolve_followup_outcome({"reply": None, "queries": [], "computes": [
        {"source": "stored", "result": {"ok": False, "error": "x"}}]})
    assert "did not succeed" in reply


# ---------------------------------------------------------------- a plan bundle's rows, and a loop query's scope

def _plan_bundle(steps):
    return {"id": 5, "mode": "plan", "user_query": "tissues in the project",
            "step_results": {i + 1: step for i, step in enumerate(steps)}}


def _graph_step(rows, *, total, truncated):
    return {"ok": True, "tool": "graph_query",
            "output": {"data": rows, "count": len(rows), "total": total, "truncated": truncated}}


def test_a_plan_step_cut_at_its_limit_reads_as_capped_and_is_not_counted(tmp_path):
    from chat_nextseek.agents.followup import describe_stored_result
    rows = [{"uuid": f"TIS-{i}", "Tissue": "liver" if i % 3 == 0 else "lung"} for i in range(1000)]
    bundle = _plan_bundle([_graph_step(rows, total=36622, truncated=True)])
    described = describe_stored_result(bundle)
    assert described["capped"] is True and described["total"] == 36622
    [p], _ = _seam(bundle, tmp_path, [dict(source="stored", group_by=None, code=None,
                                           where=[{"column": "Tissue", "op": "equals", "value": "liver"}])])
    assert p["ok"] is False and p["needs_query"] is True and "count" not in p
    no_total = _plan_bundle([_graph_step(rows, total=None, truncated=True)])
    assert describe_stored_result(no_total)["capped"] is True


def test_those_after_a_search_and_a_filter_is_the_filtered_set(tmp_path):
    from chat_nextseek.agents.followup import _all_uids, _stored_rows, describe_stored_result
    searched = [{"uid": f"MUS-{i}", "Sex": "F" if i % 2 else "M"} for i in range(250)]
    filtered = searched[:40]
    bundle = _plan_bundle([
        {"ok": True, "tool": "new_search", "output": {"data": searched, "count": 250}},
        {"ok": True, "tool": "coding_filter", "output": {"data": filtered, "count": 40, "source_step_id": 1}},
        {"ok": False, "tool": "graph_query", "output": {"data": [{"uid": "X"}], "count": 1}},
        {"ok": True, "tool": "reporter", "output": {"reply": "a summary"}},
    ])
    assert _stored_rows(bundle) == filtered and _all_uids(bundle) == [r["uid"] for r in filtered]
    described = describe_stored_result(bundle)
    assert described["total"] == 40 and described["capped"] is False
    [p], _ = _seam(bundle, tmp_path, [dict(source="stored", where=None, group_by=["Sex"], code=None)])
    assert p["ok"] is True and p["count"] == 40 and p["source"]["complete"] is True


def _seam_after_a_query(monkeypatch, tmp_path, bundle, seed_uids, rows):
    cypher = "MATCH (m:Sample) WHERE m.uuid IN $uids RETURN m.uuid AS uuid, m.Sex AS Sex"
    monkeypatch.setattr(orch, "graph_agent", lambda *a, **k: GraphAgentPlan(cypher=cypher, context_mode="catalog"))
    monkeypatch.setattr(orch, "tool_neo4j_query", lambda *a, **k: {
        "ok": True, "count": len(rows), "total": len(rows), "truncated": False, "data": rows, "cypher": cypher,
        "submitted_cypher": cypher, "parameters": {}, "counters": {}, "scope": {"decision": "proven"}})
    queried = {}
    [p], _ = _seam(bundle, tmp_path, [dict(source="last_query", where=None, group_by=["Sex"], code=None)],
                   before=lambda run_query: queried.setdefault(
                       "payload", run_query(question="sex of the mice", seed_uids=seed_uids)))
    return p, queried["payload"]


def test_a_computation_over_a_query_scoped_to_part_of_the_set_says_so(monkeypatch, tmp_path):
    """A capped REST result has no query to rebuild from: the seeded query covers the 20 stored UIDs of 250."""
    stored = [{"uid": f"MUS-{i}"} for i in range(20)]
    bundle = {"id": 1, "mode": "api_query", "user_query": "mice in the project",
              "memory_payload": {"data": {"rows": stored, "total": 250}}}
    rows = [{"uuid": r["uid"], "Sex": "F" if i % 2 else "M"} for i, r in enumerate(stored)]
    p, query = _seam_after_a_query(monkeypatch, tmp_path, bundle, [r["uid"] for r in stored], rows)
    assert query["seed_mode"] == "uids" and "not the whole set" in query["scope_note"]
    assert p["ok"] is True and p["count"] == 20
    assert p["source"]["complete"] is False and p["source"]["seed_mode"] == "uids"
    assert p["scope_note"] == query["scope_note"]


def test_a_computation_over_a_query_scoped_to_the_whole_set_is_complete(monkeypatch, tmp_path):
    rows = [{"uuid": r["uuid"], "Sex": r["Sex"]} for r in _rows_731()]
    p, query = _seam_after_a_query(monkeypatch, tmp_path, _bundle(), [r["uuid"] for r in rows], rows)
    assert query["scope_note"] is None
    assert p["ok"] is True and p["count"] == 731 and p["source"]["complete"] is True
    assert "scope_note" not in p



# ---------------------------------------------------------------- a plan step inherits the cap of what it derives from

def _search_step(rows, count):
    return {"ok": True, "tool": "new_search", "output": {"data": rows, "count": count}}


def _filter_step(rows, source):
    return {"ok": True, "tool": "coding_filter", "output": {"data": rows, "count": len(rows), "source_step_id": source}}


def _reads_capped_and_refuses(bundle, tmp_path):
    from chat_nextseek.agents.followup import describe_stored_result
    described = describe_stored_result(bundle)
    assert described["capped"] is True and described["total"] is None and described["total_known"] is False
    assert "rows" not in described and "column_summary" not in described
    [p], _ = _seam(bundle, tmp_path, [dict(source="stored", where=None, group_by=["Tissue"], code=None)])
    assert p["ok"] is False and p["needs_query"] is True and "count" not in p
    return described


def test_a_filter_over_a_capped_search_is_capped_with_or_without_its_payload(tmp_path):
    searched = [{"uid": f"TIS-{i}", "Tissue": "liver" if i % 3 == 0 else "lung"} for i in range(1000)]
    filtered = [r for r in searched if r["Tissue"] == "liver"]
    steps = [_search_step(searched, 36622), _filter_step(filtered, 1)]
    without = _plan_bundle(steps)
    assert _reads_capped_and_refuses(without, tmp_path)["rows_stored"] == len(filtered) == 334

    payload = orch._plan_filter_payload(without["step_results"], 2, [])
    assert payload["data"]["total"] is None and payload["data"]["truncated"] is True
    with_payload = {**without, "memory_payload": payload}
    _reads_capped_and_refuses(with_payload, tmp_path)
    payload_only = {"id": 5, "mode": "plan", "user_query": "q", "memory_payload": payload}
    _reads_capped_and_refuses(payload_only, tmp_path)

    twice = _plan_bundle(steps + [_filter_step(filtered[:100], 2)])
    _reads_capped_and_refuses(twice, tmp_path)


def test_a_filter_over_a_whole_search_keeps_its_own_total():
    searched = [{"uid": f"TIS-{i}", "Tissue": "liver"} for i in range(60)]
    step_results = _plan_bundle([_search_step(searched, 60), _filter_step(searched[:20], 1)])["step_results"]
    assert orch._plan_filter_payload(step_results, 2, [])["data"] == {"rows": searched[:20], "total": 20}


def test_a_filter_over_a_truncated_graph_step_is_capped_and_not_the_graph_steps_rows(tmp_path):
    from chat_nextseek.agents.followup import _stored_rows
    graph_rows = [{"uuid": f"TIS-{i}", "Tissue": "liver" if i % 3 == 0 else "lung"} for i in range(1000)]
    filtered = [r for r in graph_rows if r["Tissue"] == "liver"]
    bundle = _plan_bundle([_graph_step(graph_rows, total=36622, truncated=True), _filter_step(filtered, 1)])
    bundle["graph_result"] = {"ok": True, "data": graph_rows, "count": 1000, "total": 36622, "truncated": True}
    assert _stored_rows(bundle) == filtered
    _reads_capped_and_refuses(bundle, tmp_path)


def _intersect_bundle(counts):
    first = [{"uid": f"S-{i}", "Tissue": "lung"} for i in range(500)]
    second = [{"uid": f"S-{i}", "Tissue": "lung"} for i in range(500)]
    bundle = _plan_bundle([_search_step(first, counts[0]), _search_step(second, counts[1])])
    bundle["step_results"]["intersection"] = {
        "ok": True, "tool": "intersection",
        "output": {"data": [{"uid": r["uid"], "Tissue": "lung"} for r in first], "count": 500}}
    bundle["plan"] = {"steps": [{"step_id": 1, "tool": "new_search", "combine_mode": "intersect"},
                                {"step_id": 2, "tool": "new_search", "combine_mode": "intersect"}]}
    return bundle


def test_an_intersection_of_capped_searches_is_capped(tmp_path):
    _reads_capped_and_refuses(_intersect_bundle((9000, 7000)), tmp_path)
    no_plan = _intersect_bundle((9000, 500))
    del no_plan["plan"]
    _reads_capped_and_refuses(no_plan, tmp_path)


def test_an_intersection_of_whole_searches_is_whole():
    from chat_nextseek.agents.followup import describe_stored_result
    described = describe_stored_result(_intersect_bundle((500, 500)))
    assert described["capped"] is False and described["total"] == 500


# ---------------------------------------------------------------- a plan's stored query is the step the user saw

GRAPH_A = "MATCH (s:T_TIS) RETURN s.uuid AS uuid, s.Tissue AS Tissue"
GRAPH_B = "MATCH (s:T_RNA) RETURN s.uuid AS uuid, s.Tissue AS Tissue"


def _tissue_rows(n, prefix="TIS"):
    return [{"uuid": f"{prefix}-{i}", "Tissue": "liver" if i % 3 == 0 else "lung"} for i in range(n)]


def _planned_graph_step(rows, *, cypher, total=None, truncated=False):
    step = _graph_step(rows, total=len(rows) if total is None else total, truncated=truncated)
    step["output"]["graph_plan"] = {"cypher": cypher, "parameters": {}}
    return step


def _plan_with(steps, plan_steps=None):
    bundle = _plan_bundle(steps)
    first_graph = next((s["output"]["graph_plan"] for s in steps if s.get("tool") == "graph_query"), None)
    bundle["graph_plan"] = first_graph  # as the plan-mode builder keeps it: the FIRST graph step's
    if plan_steps is not None:
        bundle["plan"] = {"steps": plan_steps}
    return bundle


def _seed_mode(bundle, tmp_path, monkeypatch):
    """The seam's scoping of a seeded run_new_query, called as run_followup calls it."""
    from chat_nextseek.agents.followup import _all_uids, _stored_query
    monkeypatch.setattr(orch, "graph_agent",
                        lambda *a, **k: GraphAgentPlan(cypher="MATCH (s) WHERE s.uuid IN $uids RETURN count(s) AS n",
                                                       context_mode="catalog"))
    monkeypatch.setattr(orch, "tool_neo4j_query", lambda *a, **k: {
        "ok": True, "count": 1, "total": 1, "truncated": False, "data": [{"n": 1}], "cypher": "x",
        "submitted_cypher": "x", "parameters": {}, "counters": {}, "scope": {"decision": "proven"}})
    got = {}
    _seam(bundle, tmp_path, [dict(source="stored", where=None, group_by=None, code=None)],
          before=lambda run_query: got.setdefault("q", run_query(
              question="how many", seed_uids=_all_uids(bundle), stored_query=_stored_query(bundle), scoped=True)))
    return got["q"]["seed_mode"]


def test_graph_then_filter_has_no_stored_query_and_is_capped(tmp_path, monkeypatch):
    from chat_nextseek.agents.followup import describe_stored_result
    rows = _tissue_rows(1000)
    kept = [r for r in rows if r["Tissue"] == "liver"]
    bundle = _plan_with([_planned_graph_step(rows, cypher=GRAPH_A, total=36622, truncated=True),
                         _filter_step(kept, 1)])
    described = describe_stored_result(bundle)
    assert described["stored_query"] is None and described["stored_query_rebuildable"] is False
    assert described["capped"] is True and described["rows_stored"] == 334
    assert _seed_mode(bundle, tmp_path, monkeypatch) == "uids"
    assert compute_over_rows(rows=kept, total=None, complete=False, where=None, group_by=None, code=None)["ok"] is False


def test_an_empty_filter_over_a_whole_graph_step_is_not_rebuilt_as_the_unfiltered_set(tmp_path, monkeypatch):
    from chat_nextseek.agents.followup import describe_stored_result
    bundle = _plan_with([_planned_graph_step(_tissue_rows(60), cypher=GRAPH_A), _filter_step([], 1)])
    described = describe_stored_result(bundle)
    assert described["stored_query"] is None and described["rows_stored"] == 0
    assert _seed_mode(bundle, tmp_path, monkeypatch) == "none"
    [p], _ = _seam(bundle, tmp_path, [dict(source="stored", where=None, group_by=["Tissue"], code=None)])
    assert p["ok"] is False and p["needs_query"] is True


def test_an_intersection_that_includes_a_graph_step_has_no_stored_query(tmp_path, monkeypatch):
    from chat_nextseek.agents.followup import describe_stored_result
    rows = _tissue_rows(500)
    bundle = _plan_with([_planned_graph_step(rows, cypher=GRAPH_A), _search_step(rows, 500)],
                        plan_steps=[{"step_id": 1, "tool": "graph_query", "combine_mode": "intersect"},
                                    {"step_id": 2, "tool": "new_search", "combine_mode": "intersect"}])
    bundle["step_results"]["intersection"] = {"ok": True, "tool": "intersection",
                                              "output": {"data": rows[:200], "count": 200}}
    described = describe_stored_result(bundle)
    assert described["stored_query"] is None and described["capped"] is False and described["total"] == 200
    assert _seed_mode(bundle, tmp_path, monkeypatch) == "uids"
    [p], _ = _seam(bundle, tmp_path, [dict(source="stored", where=None, group_by=["Tissue"], code=None)])
    assert p["ok"] is True and p["count"] == 200


def test_a_later_graph_step_has_its_own_stored_query_unless_it_derives_from_another(tmp_path, monkeypatch):
    from chat_nextseek.agents.followup import describe_stored_result
    first = _planned_graph_step(_tissue_rows(60), cypher=GRAPH_A)
    later = _planned_graph_step(_tissue_rows(1000, "RNA"), cypher=GRAPH_B, total=5000, truncated=True)
    independent = _plan_with([first, later])
    described = describe_stored_result(independent)
    assert described["stored_query"]["cypher"] == GRAPH_B and described["stored_query_rebuildable"] is True
    assert _seed_mode(independent, tmp_path, monkeypatch) == "stored_query"
    dependent = _plan_with([first, later], plan_steps=[{"step_id": 1, "tool": "graph_query"},
                                                      {"step_id": 2, "tool": "graph_query", "depends_on": 1}])
    assert describe_stored_result(dependent)["stored_query"] is None
    assert _seed_mode(dependent, tmp_path, monkeypatch) == "uids"


def test_a_search_seeded_from_a_capped_graph_step_inherits_its_cap(tmp_path, monkeypatch):
    seeded =[{"uid": f"TIS-{i}", "Tissue": "liver"} for i in range(250)]
    bundle = _plan_with([_planned_graph_step(_tissue_rows(1000), cypher=GRAPH_A, total=36622, truncated=True),
                         _search_step(seeded, 250)],
                        plan_steps=[{"step_id": 1, "tool": "graph_query"},
                                    {"step_id": 2, "tool": "new_search",
                                     "input_mapping": {"uids": {"from_step": 1, "field": "uids"}}}])
    described = _reads_capped_and_refuses(bundle, tmp_path)
    assert described["stored_query"] is None and described["rows_stored"] == 250
    assert _seed_mode(bundle, tmp_path, monkeypatch) == "uids"


def test_a_search_seeded_from_a_capped_search_inherits_its_cap(tmp_path, monkeypatch):
    first = [{"uid": f"TIS-{i}", "Tissue": "lung"} for i in range(1000)]
    bundle = _plan_with([_search_step(first, 36622), _search_step(first[:250], 250)],
                        plan_steps=[{"step_id": 1, "tool": "new_search"},
                                    {"step_id": 2, "tool": "refine_last_search", "depends_on": 1}])
    assert _reads_capped_and_refuses(bundle, tmp_path)["stored_query"] is None
    assert _seed_mode(bundle, tmp_path, monkeypatch) == "uids"


def test_a_single_graph_step_keeps_its_own_rebuildable_stored_query(tmp_path, monkeypatch):
    from chat_nextseek.agents.followup import describe_stored_result
    bundle = _plan_with([_planned_graph_step(_tissue_rows(1000), cypher=GRAPH_A, total=36622, truncated=True)])
    described = describe_stored_result(bundle)
    assert described["stored_query"]["cypher"] == GRAPH_A and described["stored_query_rebuildable"] is True
    assert described["capped"] is True and described["total"] == 36622
    assert _seed_mode(bundle, tmp_path, monkeypatch) == "stored_query"

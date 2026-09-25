"""The follow-up loop's queries get the graph turn's safeguards and its reviewer (loop gap L4).

A graph turn runs its Cypher through a retry loop (one more try on a Cypher error, one on a
result that matched nothing) and then through the graph reviewer. The follow-up loop's
``run_new_query`` ran its query once and handed the model whatever came back, unreviewed.
Both now run through ``orchestrator._run_graph_with_retries``, and every loop query's
payload carries ``review``: Tier 1 (``review_tier1``) plus two checks only a follow-up has,

* ``premise``: the user states the earlier set's size, and the stored result says otherwise
  ("how many of these 1,206 mouse sample records ..." about a result that held 745);
* ``binding``: the user refers back ("of those") and the loop is about a stored result that
  is not the newest one.

No Tier 2 count runs inside the loop: the loop's model can run a relaxed query itself, and a
count per loop query would stack the reviewer's time budget. One catalog provider serves the
whole loop turn. A review that fails is an ok review that records the error.

Every agent and tool is stubbed; no model, Neo4j or network call is made.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from chat_nextseek import graph_review as gr
from chat_nextseek import graph_review_counts as counts
from chat_nextseek import orchestrator as orch
from chat_nextseek.agents.followup import build_followup_tool_schemas
from chat_nextseek.graph_retry import RETRY_CHANGED_ANSWER_NOTE
from chat_nextseek.graph_review import Check, DictCatalog, check_binding, check_premise
from chat_nextseek.graph_scope import SCOPE_ATTR, SCOPE_PARAM, GraphScope
from chat_nextseek.schemas import EntityAgentOutput
from chat_nextseek.schemas.graph import GraphAgentPlan
from chat_nextseek.schemas.router import ParserPlan

MEMBER = GraphScope.for_projects([2], source="test")

PREMISE_Q = "how many of these 1,206 mouse sample records have transcriptomic data?"
MICE = [f"MUS-CC-{i}" for i in range(745)]

SEEDED_CYPHER = ("MATCH (m:Sample) WHERE m.uuid IN $uids MATCH (d:Sample)-[:DERIVED_FROM*1..]->(m) "
                 "RETURN d.type AS type, count(DISTINCT d) AS n")
PLAIN_CYPHER = "MATCH (s:T_NHP) RETURN count(s) AS n"

CATALOG = {
    "T_PAT.Classification": [["Non-converter", 57], ["Converter", 32], ["Reverter", 9]],
    "T_IMG.FileType": [["tiff", 1306], ["tif", 212], ["TIF", 38]],
}
CONVERTER_CYPHER = ("MATCH (s:T_PAT) WHERE toLower(toString(s.Classification)) CONTAINS 'convert' "
                    "RETURN s.id AS id, s.Classification AS Classification")
CONVERTER_ROWS = [{"id": i, "Classification": c}
                  for i, c in enumerate(["Non-converter"] * 57 + ["Converter"] * 32 + ["Reverter"] * 9)]
TIFF_CYPHER = "MATCH (s:T_IMG) WHERE toLower(s.FileType) CONTAINS $term RETURN count(s) AS n"


# --------------------------------------------------------------------------- #
# Results as the tool returns them, and a stored bundle
# --------------------------------------------------------------------------- #

def _ok(cypher, params, rows, *, total=None):
    return {"ok": True, "data": rows, "count": len(rows), "total": len(rows) if total is None else total,
            "truncated": False, "limit": None, "cypher": cypher + " /* scoped */", "submitted_cypher": cypher,
            "parameters": {**dict(params or {}), SCOPE_PARAM: [2]}, "counters": {},
            "scope": {"decision": "proven"}}


def _err(cypher, params, message="Neo.ClientError.Statement.SyntaxError: Variable `x` not defined"):
    return {"ok": False, "error": message, "data": None, "cypher": cypher + " /* scoped */",
            "submitted_cypher": cypher, "parameters": {**dict(params or {}), SCOPE_PARAM: [2]},
            "scope": {"decision": "proven"}}


def _bundle(bundle_id=1, uids=MICE):
    """A complete graph result the follow-up is about: 745 CC mice, every UID stored."""
    cypher = "MATCH (s:T_MUS) WHERE s.Strain CONTAINS 'CC' RETURN s.uuid AS uid"
    return {"id": bundle_id, "mode": "graph_query", "user_query": "collaborative cross mice",
            "graph_plan": {"cypher": cypher, "parameters": {}},
            "graph_result": {"ok": True, "count": len(uids), "total": len(uids),
                             "data": [{"uid": u} for u in uids]}}


# --------------------------------------------------------------------------- #
# The seam: _run_followup_agent's run_query, with the graph stubbed
# --------------------------------------------------------------------------- #

@pytest.fixture
def seam(monkeypatch, tmp_path):
    """``run(plans, results, calls=...)``: run ``calls(run_query)`` inside ``_run_followup_agent``.

    The graph agent hands out ``plans`` in order (the last one repeats); Neo4j answers ``results`` in order and
    fails the test if asked once more than scripted. The catalog is a ``DictCatalog`` over ``CATALOG`` patched in for
    ``live_values``; ``run_tier2`` and the count tool record any call."""

    def run(plans, results, *, calls, user_text="q", bundle=None, session=None, catalog=None,
            live_values=None):
        seen = SimpleNamespace(agent=[], neo4j=[], live_values=[], tier2=[], counts=[], payloads=[])
        plans = list(plans)
        queue = list(results)

        def agent(config, question, entity, plan, retry_context=None, refine_context=None):
            seen.agent.append({"question": question, "retry_context": retry_context,
                               "refine_context": refine_context})
            return plans.pop(0) if len(plans) > 1 else plans[0]

        def neo4j(config, cypher, params=None):
            seen.neo4j.append({"cypher": cypher, "params": params})
            assert queue, "Neo4j was asked once more than the test scripted"
            return queue.pop(0)

        def _live_values(config, **kwargs):
            seen.live_values.append(kwargs)
            return DictCatalog(CATALOG if catalog is None else catalog)

        def _run_tier2(config, inp, review, **kwargs):
            seen.tier2.append(kwargs)
            return review

        def _count_tool(*a, **k):
            seen.counts.append(k)
            return {"ok": True, "data": [], "count": None, "total": 1, "scope": {"decision": "proven"}}

        monkeypatch.setattr(orch, "graph_agent", agent)
        monkeypatch.setattr(orch, "tool_neo4j_query", neo4j)
        monkeypatch.setattr(orch, "live_values", live_values or _live_values)
        monkeypatch.setattr(orch, "run_tier2", _run_tier2)
        monkeypatch.setattr(counts, "tool_neo4j_query", _count_tool)

        def fake_followup(config, *, user_text, bundle, run_query, log_dir):
            seen.payloads.extend(calls(run_query))
            return {"reply": "x", "caveats": [], "queries": [], "tool_calls": []}

        monkeypatch.setattr(orch, "run_followup", fake_followup)
        config = SimpleNamespace(MODEL_MODE="test", **{SCOPE_ATTR: MEMBER})
        orch._run_followup_agent(config, session={} if session is None else session, user_text=user_text,
                                 bundle=_bundle() if bundle is None else bundle, log_dir=str(tmp_path))
        return seen

    return run


def _plan(cypher, parameters=None):
    return GraphAgentPlan(cypher=cypher, parameters=dict(parameters or {}), context_mode="catalog")


def _one(question="downstream types of the 745 collaborative cross mice", seed=MICE):
    return lambda run_query: [run_query(question=question, seed_uids=list(seed))]


# --------------------------------------------------------------------------- #
# The graph turn's safeguards: one retry on a Cypher error, one on zero rows
# --------------------------------------------------------------------------- #

def test_a_loop_query_that_errors_is_retried_once_with_the_error(seam):
    rows = [{"type": "TIS", "n": 25936}, {"type": "DNA", "n": 766}]
    seen = seam([_plan(SEEDED_CYPHER)],
                [_err(SEEDED_CYPHER, {}), _ok(SEEDED_CYPHER, {}, rows)], calls=_one())

    assert len(seen.agent) == 2 and len(seen.neo4j) == 2
    assert seen.agent[0]["retry_context"] is None
    assert "Variable `x` not defined" in seen.agent[1]["retry_context"]
    assert seen.agent[1]["refine_context"] == seen.agent[0]["refine_context"], "the retry keeps the loop's scope"
    [payload] = seen.payloads
    assert payload["ok"] is True and payload["count"] == 2
    assert payload["rows"] == rows


def test_a_loop_query_that_matched_nothing_gets_one_more_go(seam):
    rows = [{"type": "TIS", "n": 25936}]
    seen = seam([_plan(SEEDED_CYPHER)],
                [_ok(SEEDED_CYPHER, {}, []), _ok(SEEDED_CYPHER, {}, rows)], calls=_one())

    assert len(seen.neo4j) == 2
    assert seen.agent[1]["retry_context"], "the zero-row retry context reached the graph agent"
    [payload] = seen.payloads
    assert payload["count"] == 1 and payload["rows"] == rows
    assert payload["retry_note"] == RETRY_CHANGED_ANSWER_NOTE, "a number found by a changed filter says so"


def test_a_second_zero_keeps_the_first_result_and_stops(seam):
    seen = seam([_plan("FIRST $uids"), _plan("SECOND $uids")],
                [_ok("FIRST $uids", {}, []), _ok("SECOND $uids", {}, [])], calls=_one())

    assert len(seen.neo4j) == 2, "zero rows are retried once, not twice"
    [payload] = seen.payloads
    assert payload["count"] == 0 and payload["ok"] is True
    assert "retry_note" not in payload


def test_a_first_time_answer_carries_no_retry_note(seam):
    seen = seam([_plan(SEEDED_CYPHER)], [_ok(SEEDED_CYPHER, {}, [{"type": "TIS", "n": 1}])], calls=_one())
    assert len(seen.neo4j) == 1
    assert "retry_note" not in seen.payloads[0]


def test_a_scope_refusal_is_not_retried(seam):
    refused = {"ok": False, "error": "Query refused for its project scope. Reasons: not allowed.", "data": None,
               "cypher": PLAIN_CYPHER, "submitted_cypher": PLAIN_CYPHER, "parameters": {},
               "scope": {"decision": "refused", "codes": ["label_not_allowed"], "reasons": ["not allowed"]}}
    seen = seam([_plan(PLAIN_CYPHER)], [refused], calls=_one())
    assert len(seen.agent) == 1 and len(seen.neo4j) == 1
    assert seen.payloads[0]["ok"] is False


def test_every_attempt_binds_the_seed_when_it_names_uids(seam):
    """The retry is a new statement; it is bound the same way, and uids_applied is the kept statement's."""
    rows = [{"type": "TIS", "n": 3}]
    seen = seam([_plan(SEEDED_CYPHER)], [_err(SEEDED_CYPHER, {}), _ok(SEEDED_CYPHER, {}, rows)], calls=_one())

    assert [call["params"]["uids"] for call in seen.neo4j] == [MICE, MICE]
    assert seen.payloads[0]["uids_applied"] == 745


def test_a_kept_statement_that_does_not_name_uids_reports_none_applied(seam):
    unscoped = "MATCH (d:Sample) RETURN d.type AS type, count(*) AS n"
    seen = seam([_plan(SEEDED_CYPHER), _plan(unscoped)],
                [_err(SEEDED_CYPHER, {}), _ok(unscoped, {}, [{"type": "TIS", "n": 9}])], calls=_one())

    assert "uids" in seen.neo4j[0]["params"] and "uids" not in (seen.neo4j[1]["params"] or {})
    assert seen.payloads[0]["uids_applied"] is None


def test_the_graph_turn_and_the_loop_run_through_one_helper(monkeypatch, tmp_path, seam):
    real = orch._run_graph_with_retries
    questions = []

    def spy(config, question, *args, **kwargs):
        questions.append(question)
        return real(config, question, *args, **kwargs)

    monkeypatch.setattr(orch, "_run_graph_with_retries", spy)
    seam([_plan(SEEDED_CYPHER)], [_ok(SEEDED_CYPHER, {}, [{"type": "TIS", "n": 1}])],
         calls=_one(question="loop question"))

    monkeypatch.setattr(orch, "graph_agent", lambda *a, **k: _plan(PLAIN_CYPHER))
    monkeypatch.setattr(orch, "tool_neo4j_query", lambda config, cy, params=None: _ok(cy, params, [{"n": 725}]))
    monkeypatch.setattr(orch, "chatter_agent_answer", lambda *a, **k: "reply")
    monkeypatch.setattr(orch, "append_turn", lambda *a, **k: None)
    orch._execute_graph_turn(
        config=SimpleNamespace(MODEL_MODE="test", **{SCOPE_ATTR: MEMBER}), session={}, user_text="graph question",
        entity_result=EntityAgentOutput(), plan=ParserPlan(mode="graph_query", intent_summary="q"),
        log_dir=str(tmp_path), artifact_store=SimpleNamespace(register_path=lambda **k: None,
                                                              write_json=lambda **k: None),
        send_event=lambda *a, **k: None, debug_payload={}, t_total_start=0.0,
    )
    assert questions == ["loop question", "graph question"]


# --------------------------------------------------------------------------- #
# The reviewer inside the loop
# --------------------------------------------------------------------------- #

def test_the_payload_carries_a_review_with_a_verdict(seam):
    seen = seam([_plan(PLAIN_CYPHER)], [_ok(PLAIN_CYPHER, {}, [{"n": 725}])], calls=_one())
    review = seen.payloads[0]["review"]
    assert review["verdict"] == "ok"
    assert review["error"] is None
    names = [c["name"] for c in review["checks"]]
    assert "breakage" in names and "premise" in names and "binding" in names
    json.dumps(seen.payloads[0])  # the payload goes back to the model as JSON


def test_the_loop_review_reads_the_result_like_a_graph_turn(seam):
    """98 'converters', 57 of them stored as Non-converter: Tier 1 names the split for the loop too."""
    seen = seam([_plan(CONVERTER_CYPHER)], [_ok(CONVERTER_CYPHER, {}, CONVERTER_ROWS)],
                calls=_one(question="show samples for human subjects who convert to Mtb infection positive",
                           seed=[]))
    review = seen.payloads[0]["review"]
    assert review["verdict"] == "suggest"
    assert "Non-converter 57" in review["disclosure"]


def test_a_failed_loop_query_gets_the_breakage_note(seam):
    """An error is retried as a graph turn retries it, up to GRAPH_MAX_TRIES statements in all."""
    seen = seam([_plan(PLAIN_CYPHER)], [_err(PLAIN_CYPHER, {})] * orch.GRAPH_MAX_TRIES, calls=_one())
    assert len(seen.neo4j) == orch.GRAPH_MAX_TRIES
    review = seen.payloads[0]["review"]
    assert review["verdict"] == "note"
    assert "failed" in review["disclosure"]


def test_the_users_wrong_count_of_the_earlier_set_is_disclosed_first(seam):
    seen = seam([_plan(SEEDED_CYPHER)], [_ok(SEEDED_CYPHER, {}, [{"type": "RNA", "n": 402}])], calls=_one(),
                user_text=PREMISE_Q)
    review = seen.payloads[0]["review"]
    premise = next(c for c in review["checks"] if c["name"] == "premise")
    assert premise["fired"] is True
    assert premise["detail"] == "the earlier result had 745, not 1,206"
    assert review["verdict"] == "suggest"
    assert review["disclosure"].startswith("The earlier result had 745, not 1,206.")


def test_the_premise_is_checked_on_an_unseeded_query_too(seam):
    """The user's words are about the earlier set whatever the loop's model runs."""
    seen = seam([_plan(PLAIN_CYPHER)], [_ok(PLAIN_CYPHER, {}, [{"n": 725}])], calls=_one(seed=[]),
                user_text=PREMISE_Q)
    premise = next(c for c in seen.payloads[0]["review"]["checks"] if c["name"] == "premise")
    assert premise["fired"] is True


def test_a_follow_up_about_an_older_result_that_says_those_is_disclosed(seam):
    history = [_bundle(1), {"id": 2, "mode": "graph_query"}, {"id": 3, "mode": "graph_query"}]
    seen = seam([_plan(SEEDED_CYPHER)], [_ok(SEEDED_CYPHER, {}, [{"type": "RNA", "n": 402}])], calls=_one(),
                user_text="Of those, how many are female?", bundle=history[0],
                session={"results_history": history})
    review = seen.payloads[0]["review"]
    binding = next(c for c in review["checks"] if c["name"] == "binding")
    assert binding["fired"] is True
    assert review["verdict"] == "suggest"
    assert review["disclosure"]


def test_a_follow_up_about_the_newest_result_is_quiet(seam):
    history = [{"id": 1, "mode": "graph_query"}, _bundle(2)]
    seen = seam([_plan(SEEDED_CYPHER)], [_ok(SEEDED_CYPHER, {}, [{"type": "RNA", "n": 402}])], calls=_one(),
                user_text="Of those, how many are female?", bundle=history[1],
                session={"results_history": history})
    review = seen.payloads[0]["review"]
    assert not next(c for c in review["checks"] if c["name"] == "binding")["fired"]
    assert review["verdict"] == "ok"


def test_the_loops_own_question_naming_the_set_size_is_not_a_premise(seam):
    """The loop's model writes a standalone question ("Of the 745 ..."); its answer is part of that set, so a
    different number is the point, not a wrong premise. Tier 1's premise_count is left to the premise check."""
    seen = seam([_plan(PLAIN_CYPHER)], [_ok(PLAIN_CYPHER, {}, [{"n": 300}])],
                calls=_one(question="Of the 745 collaborative cross mice, how many are female?"))
    review = seen.payloads[0]["review"]
    tier1 = next(c for c in review["checks"] if c["name"] == "premise_count")
    assert tier1["fired"] is False and tier1["detail"].startswith("skipped")
    assert review["verdict"] == "ok"


def test_no_count_variant_runs_inside_the_loop(seam):
    """stem_miss has a Tier 2 variant; the graph turn would count every spelling, the loop does not."""
    seen = seam([_plan(TIFF_CYPHER, {"term": "tiff"})], [_ok(TIFF_CYPHER, {"term": "tiff"}, [{"n": 1306}])],
                calls=_one(question="How many TIFF images are there?", seed=[]))
    review = seen.payloads[0]["review"]
    assert next(c for c in review["checks"] if c["name"] == "stem_miss")["fired"] is True
    assert seen.tier2 == [] and seen.counts == []
    assert review["variants"] == []


def test_the_turn_debug_records_each_loop_querys_review(tmp_path):
    """debug.followup.queries is what a run review reads; it names each query's verdict."""
    session = {"results_history": [_bundle(1)], "bundle_seq": 1}

    def fake_followup(config, *, user_text, bundle, run_query, log_dir):
        result = run_query(question="downstream types of the collaborative cross mice", seed_uids=list(MICE))
        return {"reply": "402 RNA samples.", "caveats": [],
                "queries": [{"question": "q", "seeded": True, "result": result}],
                "tool_calls": ["run_new_query", "answer"]}

    plan = ParserPlan(mode="ask_about_last_results", intent_summary="q", target_result_id=1)
    with patch.object(orch.pipeline_agent, "is_active", return_value=False), \
            patch.object(orch, "_ensure_query_log_dir", return_value=str(tmp_path)), \
            patch.object(orch, "shortlist_catalog", return_value=([], [], {})), \
            patch.object(orch, "entity_agent", return_value=EntityAgentOutput()), \
            patch.object(orch, "parser_agent", return_value=plan), \
            patch.object(orch, "graph_agent", lambda *a, **k: _plan(SEEDED_CYPHER)), \
            patch.object(orch, "tool_neo4j_query",
                         lambda config, cy, params=None: _ok(cy, params, [{"type": "RNA", "n": 402}])), \
            patch.object(orch, "live_values", lambda config, **k: DictCatalog(CATALOG)), \
            patch.object(orch, "run_followup", fake_followup), \
            patch.object(orch, "memory_agent_answer", return_value="from the stored result"), \
            patch.object(orch, "append_turn"), \
            patch.object(orch, "_artifacts_for", MagicMock(return_value=None)):
        payload = orch.run_query(session, SimpleNamespace(MODEL_MODE="test", MIN_SAMPLETYPES=[], MIN_ASSAYS=[]),
                                 PREMISE_Q, None)

    [query] = payload["debug"]["followup"]["queries"]
    assert query["review_verdict"] == "suggest", "the user's 1,206 against the stored 745"


def test_one_catalog_provider_serves_the_whole_loop_turn(seam):
    def two(run_query):
        return [run_query(question="first", seed_uids=list(MICE)),
                run_query(question="second", seed_uids=list(MICE))]

    seen = seam([_plan(CONVERTER_CYPHER)],
                [_ok(CONVERTER_CYPHER, {}, CONVERTER_ROWS), _ok(CONVERTER_CYPHER, {}, CONVERTER_ROWS)], calls=two)
    assert seen.live_values == [{}], "one provider, with the default cold budget, for every query of the turn"
    assert all(p["review"]["verdict"] == "suggest" for p in seen.payloads)


# --------------------------------------------------------------------------- #
# A failing review never breaks the loop
# --------------------------------------------------------------------------- #

def _boom(*a, **k):
    raise RuntimeError("boom")


@pytest.mark.parametrize("broken", ["review_tier1", "check_premise", "check_binding", "_review_input"])
def test_a_review_that_raises_is_an_ok_review_and_the_payload_is_unchanged(seam, monkeypatch, broken):
    rows = [{"type": "TIS", "n": 25936}]
    plans, results = [_plan(SEEDED_CYPHER)], [_ok(SEEDED_CYPHER, {}, rows)]
    good = seam(plans, results, calls=_one(), user_text=PREMISE_Q).payloads[0]
    monkeypatch.setattr(orch, broken, _boom)
    bad = seam(plans, [_ok(SEEDED_CYPHER, {}, rows)], calls=_one(), user_text=PREMISE_Q).payloads[0]

    assert bad["review"]["verdict"] == "ok"
    assert "boom" in bad["review"]["error"]
    assert {k: v for k, v in bad.items() if k != "review"} == {k: v for k, v in good.items() if k != "review"}


def test_a_catalog_provider_that_cannot_be_built_is_an_ok_review(seam):
    seen = seam([_plan(SEEDED_CYPHER)], [_ok(SEEDED_CYPHER, {}, [{"type": "TIS", "n": 1}])], calls=_one(),
                live_values=_boom)
    review = seen.payloads[0]["review"]
    assert review["verdict"] == "ok" and "boom" in review["error"]
    assert seen.payloads[0]["ok"] is True


# --------------------------------------------------------------------------- #
# check_premise and check_binding on their own
# --------------------------------------------------------------------------- #

def test_the_premise_check_fires_on_the_briefs_example():
    check = check_premise(PREMISE_Q, stored_total=745)
    assert isinstance(check, Check)
    assert check.name == "premise" and check.fired is True
    assert check.detail == "the earlier result had 745, not 1,206"


@pytest.mark.parametrize("text,total", [
    ("how many of these 745 mouse sample records have transcriptomic data?", 745),   # the number matches
    ("how many of these 1,206 mouse sample records have transcriptomic data?", None),  # no stored total
    ("how many of these mouse sample records have transcriptomic data?", 745),       # no number stated
    ("which of those have more than 100 samples below them?", 745),                  # a threshold, not the set
])
def test_the_premise_check_is_quiet(text, total):
    check = check_premise(text, stored_total=total)
    assert check.name == "premise" and check.fired is False


def test_the_premise_check_reads_other_ways_of_naming_the_set():
    check = check_premise("Can you search all the 4,095 Sequencing Data (D.SEQ) files for 'ABC'?", stored_total=6705)
    assert check.fired and check.detail == "the earlier result had 6,705, not 4,095"


def test_the_binding_check_fires_on_an_older_result_referred_back_to():
    check = check_binding(target_bundle_id=1, newest_bundle_id=3, user_text="Of those, how many are female?")
    assert isinstance(check, Check)
    assert check.name == "binding" and check.fired is True
    assert check.detail


@pytest.mark.parametrize("kwargs", [
    dict(target_bundle_id=3, newest_bundle_id=3, user_text="Of those, how many are female?"),   # the newest
    dict(target_bundle_id=1, newest_bundle_id=3, user_text="How many HeLa samples do we have?"),  # no cue
    dict(target_bundle_id=1, newest_bundle_id=None, user_text="Of those, how many are female?"),  # unknown
    dict(target_bundle_id=None, newest_bundle_id=3, user_text="Of those, how many are female?"),
])
def test_the_binding_check_is_quiet(kwargs):
    check = check_binding(**kwargs)
    assert check.name == "binding" and check.fired is False


def test_the_binding_check_uses_the_routers_cue():
    from NessieAI.router.followup import followup_cue
    text = "and how many are female?"
    assert bool(followup_cue(text)) is check_binding(target_bundle_id=1, newest_bundle_id=2, user_text=text).fired


@pytest.mark.parametrize("cue", [None, _boom])
def test_a_cue_check_that_cannot_run_counts_as_referring_back(monkeypatch, cue):
    """Fail closed, as the suggestion chips do: a text that cannot be checked is treated as referring back."""
    monkeypatch.setattr(gr, "_router_followup_cue", lambda: cue)
    check = check_binding(target_bundle_id=1, newest_bundle_id=3, user_text="How many HeLa samples do we have?")
    assert check.fired is True


# --------------------------------------------------------------------------- #
# The model is told what the review means
# --------------------------------------------------------------------------- #

PROMPT_SENTENCE = ("If a tool result's review says the user's number or referent differs from the stored result, "
                   "answer about the stored result and say so in the first sentence.")


def test_the_prompt_says_to_answer_about_the_stored_result_first():
    prompt = " ".join((Path(orch.__file__).parent / "prompts" / "followup_agent.txt").read_text().split())
    assert PROMPT_SENTENCE in prompt


def test_the_query_tool_describes_its_review():
    tools = {t["name"]: t for t in build_followup_tool_schemas()}
    assert "review" in tools["run_new_query"]["description"]

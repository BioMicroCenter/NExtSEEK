"""What the chatter is actually handed before it writes the reply.

The chatter is the last step of a turn and the only voice the user hears, and until
now nothing tested the prompt it is built from. Three production-shaped failures are
pinned here.

* The ``Keywords:`` line has been dead since it was written. ``chatter.py`` read
  ``entity_result["filters"]["keywords"]``; ``EntityAgentOutput``
  (``schemas/entity.py``) carries ``keywords`` at the top level and has no ``filters``
  field at all, so every turn in every mode rendered ``(none)``.

* A reply can misreport what was asked because the writer was never told what ran.
  B7 (wesselr 462) asked for the PAT samples under ``MDL-250912LAU-1`` and got 1,904
  lineage records, none of them PAT. B13 (mplaster 501/502) was answered by Cypher
  whose own explanation said the ``CC`` filter could not be applied, and the reply
  still called the 731 a subset of the CC mice. In both, the gap between what the
  user asked for and what the query constrained was computed, sat in the arguments
  this function already receives, and was thrown away at the prompt boundary.

* A retry that changes the answer must be disclosed. ``_execute_graph_turn`` records
  ``graph_retry_changed_answer`` when the first query matched nothing and a changed
  filter produced a number; the comment on it says the user must not be told that
  number without being told the filter changed. Nothing carried it to the chatter.
"""
from __future__ import annotations

import json

import pytest

from chat_nextseek.agents import chatter as chatter_mod
from chat_nextseek.schemas.entity import EntityAgentOutput
from chat_nextseek.schemas.router import ParserPlan


class _StubConfig:
    """Only the attributes chatter_agent_answer touches."""

    CHATTER_SYSTEM_PROMPT = "SYSTEM PROMPT"
    LOG_DIR = ""

    def get_agent_model(self, agent_label):
        return (object(), "stub-model", None)


@pytest.fixture
def captured(monkeypatch):
    """Run the chatter with the LLM replaced, and hand back the prompt it built."""
    box: dict = {}

    def _fake_call_llm_text(config, *, messages, model_name, client, agent_label,
                            temperature=0, thinking_budget=None, usage_label=None):
        box["messages"] = messages
        box["user_content"] = messages[-1]["content"]
        return "stub reply"

    monkeypatch.setattr(chatter_mod, "call_llm_text", _fake_call_llm_text)
    return box


def _entity(**kw):
    return EntityAgentOutput(**kw).model_dump()


def _plan(**kw):
    return ParserPlan(**kw).model_dump()


# --------------------------------------------------------------------------
# 1. The dead Keywords line.
# --------------------------------------------------------------------------

def test_entity_keywords_reach_the_prompt(captured):
    """``keywords`` is top level on EntityAgentOutput, not under a ``filters`` key."""
    chatter_mod.chatter_agent_answer(
        _StubConfig(),
        "Find me mice treated with NDMA",
        _entity(keywords=["NDMA"]),
        _plan(mode="new_search", target_endpoint="/nextseek_api/samples/advanced_search/"),
        {"endpoint": "/nextseek_api/samples/advanced_search/", "method": "POST",
         "requestBody": {"filter_searchText": "NDMA"}},
        {"ok": True, "data": {"total": 12, "rows": [{"uid": "MUS-1"}]}},
        {"ok": True, "data": {"total": 12, "rows": [{"uid": "MUS-1"}]}},
        log_dir="",
    )

    assert "- Keywords: NDMA" in captured["user_content"]


def test_a_turn_with_no_keywords_still_says_none(captured):
    chatter_mod.chatter_agent_answer(
        _StubConfig(),
        "How many samples are there",
        _entity(),
        _plan(mode="new_search", target_endpoint="/nextseek_api/samples/advanced_search/"),
        {"endpoint": "/nextseek_api/samples/advanced_search/", "method": "POST", "requestBody": {}},
        {"ok": True, "data": {"total": 12, "rows": []}},
        {"ok": True, "data": {"total": 12, "rows": []}},
        log_dir="",
    )

    assert "- Keywords: (none)" in captured["user_content"]


# --------------------------------------------------------------------------
# 2. D1: what the chatter is allowed to know about the query that ran.
#
# Measured first, because it inverts the premise. `chatter.py` says "The LLM never
# sees endpoint names, Cypher, requestBody, or filter operators" and the per-turn
# instructions forbid describing retrieval. On the REST path that was never true:
# `tool_nextseek_api_request` returns `{ok, url, status_code, method, query, body,
# data}` and `slim_api_result_for_llm` passes everything but `data` through verbatim,
# so the prompt carried the full URL, the verb, `page_size` and the requestBody field
# names. The model was handed the mechanics and told not to mention them.
#
# So the resolution is not "widen or stay blind". It is: take the raw plumbing out of
# the prompt, which is what the code always claimed, and put in a structured
# description of what the query constrained, which is what the failures needed.
# --------------------------------------------------------------------------

_PLUMBING = ("/nextseek_api/", "advanced_search", "filter_searchText", "filter_sampletype",
             "page_size", "127.0.0.1", "requestBody", "queryParameters")


def _rest_turn(captured, *, entity, plan, api_plan, full=None):
    from chat_nextseek.helpers.results import slim_api_result_for_llm

    full = full or {
        "ok": True, "url": "http://127.0.0.1:8000/nextseek_api/samples/advanced_search/",
        "status_code": 200, "method": "POST", "query": {"page_size": 1000},
        "body": api_plan.get("requestBody", {}),
        "data": {"total": 12, "rows": [{"uid": "MUS-250101LAU-1"}]},
    }
    slim = slim_api_result_for_llm(full, api_plan={"queryParameters": {}})
    chatter_mod.chatter_agent_answer(
        _StubConfig(), "Find me mice treated with NDMA", entity, plan, api_plan, slim, full,
        None, log_dir="",
    )
    return captured["user_content"]


def test_the_rest_prompt_no_longer_carries_the_raw_plumbing(captured):
    """It carried the URL, the verb, page_size and the requestBody field names."""
    text = _rest_turn(
        captured,
        entity=_entity(keywords=["NDMA"]),
        plan=_plan(mode="new_search", target_endpoint="/nextseek_api/samples/advanced_search/",
                   filters={"sampletype_code": "MUS", "keywords": ["NDMA"]}),
        api_plan={"endpoint": "/nextseek_api/samples/advanced_search/", "method": "POST",
                  "requestBody": {"filter_searchText": "NDMA", "filter_sampletype": "MUS"}},
    )

    for leak in _PLUMBING:
        assert leak not in text, f"{leak!r} is still in the chatter's prompt"


def test_the_disclosure_flags_survive_the_scrub(captured):
    """Scrubbing the envelope must not take the hard-won cap disclosures with it."""
    from chat_nextseek.helpers.results import slim_api_result_for_llm

    full = {"ok": True, "url": "http://h/nextseek_api/samples/advanced_search/",
            "status_code": 200, "method": "POST", "query": {}, "body": {},
            "data": {"total": 2057, "rows": [{"uid": f"MUS-{i}"} for i in range(1000)]}}
    slim = slim_api_result_for_llm(
        full, api_plan={"queryParameters": {},
                        "retry_substituted_search": {"original": "a b", "used": "a", "label": "SINGLE"}})
    chatter_mod.chatter_agent_answer(
        _StubConfig(), "how many", _entity(), _plan(mode="new_search"),
        {"endpoint": "/e", "method": "POST", "requestBody": {}}, slim, full, None, log_dir="",
    )
    text = captured["user_content"]

    assert "rows_returned" in text
    assert "total_matching" in text
    assert "result_capped" in text
    assert "search_text_substituted" in text


def test_the_prompt_says_what_the_query_constrained(captured):
    text = _rest_turn(
        captured,
        entity=_entity(keywords=["NDMA"], sampletypes=[{"code": "MUS", "name": "Mouse"}]),
        plan=_plan(mode="new_search", target_endpoint="/nextseek_api/samples/advanced_search/",
                   filters={"sampletype_code": "MUS", "keywords": ["NDMA"]}),
        api_plan={"endpoint": "/nextseek_api/samples/advanced_search/", "method": "POST",
                  "requestBody": {"filter_searchText": "NDMA", "filter_sampletype": "MUS"}},
    )

    assert "What the query actually did:" in text
    assert "keyword search over sample records" in text
    constrained = text.split("- Constrained by:", 1)[1].splitlines()[0]
    assert "MUS" in constrained and "NDMA" in constrained


def test_a_dropped_filter_reaches_the_prompt_as_not_applied(captured):
    """B13: the Cypher never carried CC and the reply called the 731 CC mice."""
    chatter_mod.chatter_agent_answer(
        _StubConfig(),
        "how many of these 1,206 CC mouse records have transcriptomic data",
        _entity(sampletypes=[{"code": "MUS", "name": "Mouse"}], keywords=["CC"]),
        _plan(mode="graph_query"),
        graph_plan={
            "cypher": "MATCH (s:Sample {SampleType:'MUS'})<-[:DERIVED_FROM*1..6]-(d) "
                      "WHERE d.Assay IN ['A.GEX'] RETURN count(DISTINCT s)",
            "explanation": "the 'CC' keyword filter cannot be applied in the graph",
        },
        graph_result={"ok": True, "count": 1, "total": 731, "data": [{"n": 731}]},
        log_dir="",
    )
    text = captured["user_content"]

    assert "NOT APPLIED" in text
    assert "CC" in text.split("NOT APPLIED", 1)[1].splitlines()[0]
    assert "cannot be applied in the graph" in text


def test_the_graph_prompt_still_withholds_the_cypher(captured):
    """The half of D1 that was real, and stays."""
    chatter_mod.chatter_agent_answer(
        _StubConfig(), "how many mice", _entity(),
        _plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:Sample) WHERE s.Organ = 'Lung' RETURN count(s)",
                    "explanation": "counts lung samples"},
        graph_result={"ok": True, "count": 1, "total": 16841, "data": [{"n": 16841}]},
        log_dir="",
    )
    text = captured["user_content"]

    assert "MATCH (" not in text
    assert "RETURN count" not in text
    assert "DERIVED_FROM" not in text


def test_a_reporter_turn_makes_no_claim_about_dropped_filters(captured):
    chatter_mod.chatter_agent_answer(
        _StubConfig(), "summarise MetNet", _entity(projects=["MetNet"], keywords=["mice"]),
        _plan(mode="reporter", report_mode="summary"),
        reporter_summary={"project": "MetNet", "total_rows": 705},
        log_dir="",
    )
    text = captured["user_content"]

    assert "NOT APPLIED" not in text
    assert "aggregated project report" in text


def test_a_caller_note_is_disclosed(captured):
    """`graph_retry_changed_answer`: the first query found nothing and the filter moved."""
    chatter_mod.chatter_agent_answer(
        _StubConfig(), "how many GBM tissues", _entity(),
        _plan(mode="graph_query"),
        graph_plan={"cypher": "MATCH (s:Sample) RETURN count(s)", "explanation": ""},
        graph_result={"ok": True, "count": 1, "total": 12, "data": [{"n": 12}]},
        query_notes=["The first query matched nothing; this number comes from a changed filter."],
        log_dir="",
    )

    assert "changed filter" in captured["user_content"]


# --------------------------------------------------------------------------
# 3. The fallback replies are user-facing text too.
# --------------------------------------------------------------------------

def test_the_provider_failure_reply_does_not_print_an_endpoint(monkeypatch):
    """These three strings go straight to the user with `- endpoint: /nextseek_api/...`."""
    from chat_nextseek.llm_clients import LLMFatalError

    def _boom(*a, **k):
        raise LLMFatalError("every provider refused")

    monkeypatch.setattr(chatter_mod, "call_llm_text", _boom)

    reply = chatter_mod.chatter_agent_answer(
        _StubConfig(), "Find me mice", _entity(),
        _plan(mode="new_search", target_endpoint="/nextseek_api/samples/advanced_search/",
              intent_summary="find mice"),
        {"endpoint": "/nextseek_api/samples/advanced_search/", "method": "POST", "requestBody": {}},
        {"ok": True, "data": {"total": 705, "rows": []}},
        {"ok": True, "data": {"total": 705, "rows": []}},
        log_dir="",
    )

    body = reply.split("**Debug info**", 1)[0]
    assert "/nextseek_api/" not in body
    assert "705" in body, "the finished result must still reach the user"


# --------------------------------------------------------------------------
# 4. The prompt must describe its own inputs.
#
# `chatter_agent.txt` told the model "You receive: parser plan, API plan, and the
# NExtSEEK REST API result" and, for the graph mode, "You receive: the Cypher query
# plan (cypher + explanation) and the Neo4j result". It received none of the plans.
# Whoever read the prompt next reasoned from a false model of the agent.
# --------------------------------------------------------------------------

def _prompt_text():
    from pathlib import Path

    import chat_nextseek

    return (Path(chat_nextseek.__file__).resolve().parent
            / "prompts" / "chatter_agent.txt").read_text(encoding="utf-8")


def test_the_prompt_no_longer_claims_it_receives_the_plans():
    text = _prompt_text()

    assert "You receive: parser plan, API plan" not in text
    assert "the Cypher query plan" not in text


def test_every_section_the_prompt_names_is_really_in_the_built_prompt(captured):
    """The two halves cannot drift: each heading is asserted on both sides."""
    headings = ("What the user asked for", "What the query actually did", "Result statistics")
    text = _prompt_text()

    built = _rest_turn(
        captured,
        entity=_entity(keywords=["NDMA"]),
        plan=_plan(mode="new_search", target_endpoint="/nextseek_api/samples/advanced_search/"),
        api_plan={"endpoint": "/nextseek_api/samples/advanced_search/", "method": "POST",
                  "requestBody": {"filter_searchText": "NDMA"}},
    )

    for heading in headings:
        assert heading in text, f"the prompt does not name the {heading!r} block it is given"
        assert heading in built, f"the prompt names {heading!r} but no turn builds it"


def test_the_prompt_carries_the_not_applied_rule():
    """The gap disclosure needs the same first-sentence mandate as a substituted search."""
    text = _prompt_text()

    assert "NOT APPLIED" in text
    assert "first sentence" in text


def test_the_prompt_still_forbids_naming_the_mechanics():
    text = _prompt_text().lower()

    assert "endpoint" in text and "cypher" in text
    assert "http method" in text or "verb" in text


# --------------------------------------------------------------------------
# Pilot A v2 (2026-09-18): the writer saw too little of a right result.
#
# * Scientist duplicates: the query returned all 216 stored names with counts; the
#   writer was handed the first 20 and said the top 20 show no duplicates.
# * A Scientist-by-type breakdown: the rows carried type codes only and the writer invented
#   names for them ("Mass Spectrometry Peptide" for D.MSP).
# * Lung spellings: three rows, one per spelling; the reply listed them and never
#   gave the total the question asked for.
# --------------------------------------------------------------------------

class _CatalogConfig(_StubConfig):
    MIN_SAMPLETYPES = [
        {"SampleType": "D.MSP", "Name": "Mass Spectrometry Data"},
        {"SampleType": "BAC", "Name": "Bacteria Sample"},
        {"SampleType": "TIS", "Name": "Tissue Sample"},
    ]


def _graph_turn(captured, *, question, rows, total=None, entity=None, config=None,
                cypher="MATCH (s:Sample) RETURN s.Scientist AS value, count(*) AS n"):
    chatter_mod.chatter_agent_answer(
        config or _StubConfig(), question, entity or _entity(), _plan(mode="graph_query"),
        graph_plan={"cypher": cypher, "parameters": {}, "explanation": ""},
        graph_result={"ok": True, "count": len(rows), "total": total if total is not None else len(rows),
                      "truncated": False, "data": rows},
        log_dir="",
    )
    return captured["user_content"]


def test_a_value_list_reaches_the_writer_whole(captured):
    rows = [{"scientist": f"Person {i}", "n": 1000 - i} for i in range(216)]
    text = _graph_turn(captured, question="Which Scientist entries are duplicates?", rows=rows)

    assert '"Person 215"' in text
    assert "all 216 rows" in text


def test_a_sample_list_is_still_previewed_at_twenty_rows(captured):
    rows = [{"id": i, "uuid": f"TIS-200901ENG-{i}", "type": "TIS"} for i in range(500)]
    text = _graph_turn(captured, question="Find tissue samples", rows=rows,
                       cypher="MATCH (s:T_TIS) RETURN s.id AS id, s.uuid AS uuid, s.type AS type")

    assert '"TIS-200901ENG-19"' in text
    assert '"TIS-200901ENG-20"' not in text


def test_a_value_list_too_large_to_send_whole_says_it_is_partial(captured):
    rows = [{"value": f"spelling number {i:05d}", "n": 1} for i in range(5000)]
    text = _graph_turn(captured, question="Which values does Notes hold?", rows=rows)

    assert '"spelling number 04999"' not in text
    assert "of 5000 rows" in text


def test_type_codes_in_the_rows_come_with_their_catalog_names(captured):
    rows = [{"type": "D.MSP", "n": 218}, {"type": "BAC", "n": 3}]
    text = _graph_turn(captured, question="One scientist's samples by type", rows=rows, config=_CatalogConfig(),
                       cypher="MATCH (s:Sample) RETURN s.type AS type, count(*) AS n")

    assert "D.MSP = Mass Spectrometry Data" in text
    assert "BAC = Bacteria Sample" in text
    assert "TIS = Tissue Sample" not in text


def test_a_breakdown_carries_its_total(captured):
    rows = [{"value": "Lung", "n": 16841}, {"value": "lung", "n": 3361}, {"value": "LUNG", "n": 2532}]
    text = _graph_turn(captured, question="How many tissue samples have Organ set to lung?", rows=rows)

    assert "22,734" in text


def test_a_single_count_row_gets_no_sum_line(captured):
    text = _graph_turn(captured, question="How many samples?", rows=[{"n": 1084754}],
                       cypher="MATCH (s:Sample) RETURN count(*) AS n")

    assert "Sum of" not in text


def test_an_assay_the_question_never_named_raises_no_not_applied_line(captured):
    text = _graph_turn(
        captured, question="Find me samples associated with cd8 depletion",
        rows=[{"id": 1, "uuid": "TIS-201214SHA-1", "type": "TIS"}],
        entity=_entity(assays=[{"code": "Antibody Treatment", "name": "Antibody Treatment"}]),
        cypher="MATCH (s:Sample) WHERE toLower(s.search_text) CONTAINS 'cd8' RETURN s.id AS id",
    )

    assert "NOT APPLIED" not in text

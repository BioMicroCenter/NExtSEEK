"""A lab code scopes a query only when the entity agent emitted it from a matched lab record.

OD4 of `docs/superpowers/specs/2026-09-18-projects-labs-context.md`: the entity agent emits
`lab_codes` only from SEEK lab records it matched, and a person name that matches no lab is a
`Scientist` value, never a guessed code. The parser LLM writes its own `filters.lab_codes` and
echoes ENTITY_RESULT into `resolved`, and the summary reporter used to fall back to the plan's
codes whenever the entity agent's list was empty. None of those may add a code the entity
agent did not match. Every code and UID here is invented.
"""
from __future__ import annotations

import types
from unittest.mock import MagicMock, patch

from chat_nextseek import orchestrator
from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.helpers.lab_code import clamp_lab_codes
from chat_nextseek.reports.runners import run_reporter_summary
from chat_nextseek.schemas import (
    EntityAgentOutput,
    MultiParserPlan,
    ParserCandidate,
    ParserFilters,
    ParserPlan,
)


# --------------------------------------------------------------------------
# the clamp
# --------------------------------------------------------------------------

def test_clamp_keeps_only_matched_codes_in_the_plans_order():
    assert clamp_lab_codes(["QZX", "ash", "ASH", "WHT"], ["WHT", "ASH"]) == ["ASH", "WHT"]


def test_clamp_with_nothing_matched_keeps_nothing():
    assert clamp_lab_codes(["QZX", "ASH"], []) == []
    assert clamp_lab_codes(["ASH"], None) == []


def test_clamp_ignores_what_is_not_a_code_string():
    assert clamp_lab_codes(None, ["ASH"]) == []
    assert clamp_lab_codes(["ASH", 3, None, " "], ["ASH"]) == ["ASH"]


# --------------------------------------------------------------------------
# the parser's plan
# --------------------------------------------------------------------------

def test_the_parsers_codes_are_clamped_to_the_entity_agents():
    plan = ParserPlan(
        mode="new_search",
        filters=ParserFilters(lab_codes=["QZX", "ASH"], keywords=["RNA"]),
        resolved=EntityAgentOutput(lab_codes=["QZX", "ASH"], keywords=["RNA"]),
    )

    out = orchestrator._clamp_lab_codes_to_entity(plan, EntityAgentOutput(lab_codes=["ASH"]))

    assert out.filters.lab_codes == ["ASH"]
    assert out.resolved.lab_codes == ["ASH"]
    # Nothing but the codes changes.
    assert out.filters.keywords == ["RNA"]
    assert out.resolved.keywords == ["RNA"]
    assert out.mode == "new_search"


def test_a_scientist_the_parser_turned_into_a_code_reaches_no_query():
    """The entity agent matched no lab and made the name a scientist; the parser LLM, taught
    the retired first-three-letters rule, still wrote a code for it."""
    entity = EntityAgentOutput(scientists=["Dana Example"], keywords=["Dana Example"])
    plan = ParserPlan(
        mode="new_search",
        filters=ParserFilters(lab_codes=["DAN"], keywords=["Dana Example"]),
        resolved=EntityAgentOutput(lab_codes=["DAN"]),
    )

    out = orchestrator._clamp_lab_codes_to_entity(plan, entity)

    assert out.filters.lab_codes == []
    assert out.resolved.lab_codes == []
    assert out.filters.keywords == ["Dana Example"]


def test_the_entity_result_may_be_a_dict():
    plan = ParserPlan(filters=ParserFilters(lab_codes=["QZX", "ASH"]))
    out = orchestrator._clamp_lab_codes_to_entity(plan, {"lab_codes": ["ASH"]})
    assert out.filters.lab_codes == ["ASH"]


def test_every_multi_parser_candidate_is_clamped():
    plan = MultiParserPlan(
        resolved=EntityAgentOutput(lab_codes=["QZX"]),
        candidates=[
            ParserCandidate(mode="new_search", filters=ParserFilters(lab_codes=["QZX", "ASH"])),
            ParserCandidate(mode="graph_query", filters=ParserFilters(lab_codes=["QZX"])),
        ],
    )

    out = orchestrator._clamp_lab_codes_to_entity(plan, EntityAgentOutput(lab_codes=["ASH"]))

    assert out.resolved.lab_codes == []
    assert [c.filters.lab_codes for c in out.candidates] == [["ASH"], []]


def _run_query_with(entity_out, parser_plan):
    session = {"results_history": [], "last_files": []}
    config = MagicMock()
    config.API_USER = "u"
    config.API_PASS = "p"
    shortlist = ([], [], {"sampletype_codes": [], "assay_codes": [], "sampletype_ranks": {},
                          "assay_ranks": {}, "enabled": False, "fallback_reason": None})
    with patch("chat_nextseek.orchestrator.pipeline_agent.is_active", return_value=False), \
         patch("chat_nextseek.orchestrator._ensure_query_log_dir", return_value="/tmp/log"), \
         patch("chat_nextseek.orchestrator.ArtifactStore"), \
         patch("chat_nextseek.orchestrator.append_turn"), \
         patch("chat_nextseek.orchestrator.entity_agent", return_value=entity_out), \
         patch("chat_nextseek.orchestrator.parser_agent", return_value=parser_plan), \
         patch("chat_nextseek.orchestrator.fix_sample_endpoint", side_effect=lambda d: d), \
         patch("chat_nextseek.orchestrator.shortlist_catalog", return_value=shortlist):
        orchestrator.run_query(session=session, config=config, user_text="RNA from the lab")
    return session["last_debug"]["parser_plan"]


def test_run_query_clamps_the_plan_before_anything_reads_it():
    dumped = _run_query_with(
        EntityAgentOutput(lab_codes=["ASH"]),
        ParserPlan(mode="unsupported", notes="n",
                   filters=ParserFilters(lab_codes=["QZX", "ASH"]),
                   resolved=EntityAgentOutput(lab_codes=["QZX"])),
    )
    assert dumped["filters"]["lab_codes"] == ["ASH"]
    assert dumped["resolved"]["lab_codes"] == []


# --------------------------------------------------------------------------
# the summary reporter
# --------------------------------------------------------------------------

UIDS = ["TIS-240612ASH-1-PUB", "DNA-240612ASH-2-PUB", "MUS-200901WHT-23-PUB"]


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *a, **k):
        return None

    def fetchall(self):
        return self._rows

    def fetchone(self):
        return {}

    def close(self):
        return None


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    def cursor(self, **k):
        return _FakeCursor(self._rows)


def _summary(tmp_path, **kw):
    rows = [{"project_id": 1, "sample_id": i, "uuid": u} for i, u in enumerate(UIDS)]
    # The runners refuse a config without a scope; scope is not what these tests are about.
    config = types.SimpleNamespace(_db_conn=_FakeConn(rows), _connect_db=lambda **k: None,
                                   GRAPH_SCOPE=GraphScope.admin("test"))
    plan = types.SimpleNamespace(
        project=None, years=[], month_range=None, day_range=None, summary_mode="samples",
        reporter_context=types.SimpleNamespace(lab_codes=["ASH"]),
    )
    result, _saved, _summary = run_reporter_summary(config, plan, tmp_path, **kw)
    return result


def test_an_empty_list_from_the_entity_agent_is_not_replaced_by_the_plans_codes(tmp_path):
    """The orchestrator passes the entity agent's list, which is empty exactly when no lab
    matched. That is an answer, not a missing argument."""
    result = _summary(tmp_path, lab_codes=[])
    assert (result.get("scope") or {}).get("kind") != "lab"
    assert result["uuids_saved"] == len(UIDS)


def test_the_plans_codes_still_fill_in_when_the_caller_passes_none(tmp_path):
    result = _summary(tmp_path)
    assert result["scope"]["kind"] == "lab"
    assert result["uuids_saved"] == 2

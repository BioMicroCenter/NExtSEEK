"""The turn's debug record carries the parser decision (raw mode, final mode, guardrails, llm_ms)."""
from __future__ import annotations

from unittest.mock import patch

from chat_nextseek import orchestrator
from chat_nextseek.schemas import EntityAgentOutput, ParserPlan


class _Config:
    MIN_SAMPLETYPES: list = []
    MIN_ASSAYS: list = []


def _run(parser):
    with patch.object(orchestrator.pipeline_agent, "is_active", return_value=False), \
            patch.object(orchestrator, "_ensure_query_log_dir", return_value="/tmp/log"), \
            patch.object(orchestrator, "ArtifactStore"), \
            patch.object(orchestrator, "shortlist_catalog", return_value=([], [], {})), \
            patch.object(orchestrator, "entity_agent", return_value=EntityAgentOutput()), \
            patch.object(orchestrator, "parser_agent", side_effect=parser), \
            patch.object(orchestrator, "append_turn"):
        return orchestrator.run_query({}, _Config(), "hello")


def test_the_debug_record_carries_the_parser_decision():
    def parser(session, config, text, entity, decision=None):
        decision.update(raw_mode="new_search", final_mode="unsupported", guardrails_changed=["x"], llm_ms=12)
        return ParserPlan(mode="unsupported")

    d = _run(parser)["debug"]
    assert d["parser_decision"] == {"raw_mode": "new_search", "final_mode": "unsupported",
                                    "guardrails_changed": ["x"], "llm_ms": 12}
    assert d["parser_plan"]["mode"] == "unsupported"


def test_a_parser_that_records_nothing_leaves_none():
    assert _run(lambda *a, **k: ParserPlan(mode="unsupported"))["debug"]["parser_decision"] is None

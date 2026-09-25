"""A turn the AI models could not answer tells the user so in plain words.

Operator ruling 2026-09-25 (fix 5): when an ``LLMFatalError`` ends an NS turn because the
models did not answer (a 503, an empty body, a 429, a timeout or a connection error on
the second model as well, or a tool loop that ran out of attempts), the reply is the
approved plain text rather than "**The request could not be completed.**" and the raw
provider message. The ``query_error`` data carries that text as ``error``,
``reason = "model_unavailable"``, the raw message as ``detail`` and the move in
``model_fallback``; the raw message stays in the debug payload (``fatal_error``) and in
the chat log's ``error`` field. A fatal error that is not unavailability keeps its old
reply and event.

Every agent is stubbed; no model is called.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chat_nextseek import failure_replies
from chat_nextseek import orchestrator as orch
from chat_nextseek.llm_clients import LLMFatalError

# Operator-approved wording, pinned literally.
TRIED_TWO = ("The AI models we use were unavailable (we tried a second one as well), so I could not finish your "
             "question. Please ask again in a few minutes.")
NO_SECOND = ("The AI models we use were unavailable, so I could not finish your question. Please ask again in a "
             "few minutes.")

CREDS = {"api_user": "someone", "api_pass": "secret"}
MOVE = {"agent": "entity", "from": "gemini-3.5-flash", "to": "us.anthropic.claude-sonnet-4-6", "reason": "timeout"}
RAW = "All provider fallbacks exhausted: agent 'entity': 503 UNAVAILABLE"


class _Config:
    MIN_SAMPLETYPES: list = []
    MIN_ASSAYS: list = []
    MODEL_MODE = "test"


def _run(entry, fatal):
    events: list[tuple[str, dict]] = []
    turns: list[dict] = []

    def entity(*args, **kwargs):
        raise fatal

    with patch.object(orch.pipeline_agent, "is_active", return_value=False), \
            patch.object(orch, "_ensure_query_log_dir", return_value="/tmp/log"), \
            patch.object(orch, "ArtifactStore", return_value=MagicMock()), \
            patch.object(orch, "shortlist_catalog", return_value=([], [], {})), \
            patch.object(orch, "entity_agent", entity), \
            patch.object(orch, "append_turn", lambda session, **kw: turns.append(kw)):
        payload = entry({}, _Config(), "how many mice", lambda e, d: events.append((e, d)),
                        credentials=CREDS, graph_scope={"is_admin": True, "project_ids": []})
    return payload, events, turns


ENTRIES = [orch.run_query, orch.run_query_plan]
IDS = ["run_query", "run_query_plan"]


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_unavailable_after_a_second_model_says_so(entry):
    fatal = LLMFatalError(RAW, agent="entity", unavailable=True, model_fallback=[MOVE])

    payload, events, turns = _run(entry, fatal)

    assert payload["reply"] == TRIED_TWO
    (error,) = [d for e, d in events if e == "query_error"]
    assert error == {"error": TRIED_TWO, "reason": "model_unavailable", "detail": RAW, "agent": "entity",
                     "fatal": True, "model_fallback": [MOVE]}
    assert payload["debug"]["fatal_error"] == RAW
    (turn,) = turns
    assert turn["assistant_reply"] == TRIED_TWO
    assert turn["error"] == RAW
    assert turn["status"] == "error"


@pytest.mark.parametrize("entry", ENTRIES, ids=IDS)
def test_unavailable_with_no_second_model_drops_the_parenthesis(entry):
    fatal = LLMFatalError(RAW, agent="entity", unavailable=True)

    payload, events, turns = _run(entry, fatal)

    assert payload["reply"] == NO_SECOND
    (error,) = [d for e, d in events if e == "query_error"]
    assert error["error"] == NO_SECOND and error["reason"] == "model_unavailable"
    assert error["detail"] == RAW and error["model_fallback"] == []
    assert turns[0]["assistant_reply"] == NO_SECOND


@pytest.mark.parametrize("entry, heading", [(orch.run_query, "**The request could not be completed.**"),
                                            (orch.run_query_plan, "**The planner pipeline was stopped.**")],
                         ids=IDS)
def test_a_fatal_that_is_not_unavailability_keeps_its_old_reply(entry, heading):
    raw = "Unrecoverable LLM error: agent 'entity', model 'm': 400 malformed request"
    fatal = LLMFatalError(raw, agent="entity")

    payload, events, turns = _run(entry, fatal)

    assert payload["reply"] == f"{heading}\n\n{raw}"
    (error,) = [d for e, d in events if e == "query_error"]
    assert error == {"error": raw, "agent": "entity", "fatal": True}
    assert turns[0]["assistant_reply"] == payload["reply"]


def test_the_texts_are_the_approved_ones():
    assert failure_replies.MODELS_UNAVAILABLE_TRIED_TWO_REPLY == TRIED_TWO
    assert failure_replies.MODELS_UNAVAILABLE_REPLY == NO_SECOND
    assert failure_replies.MODEL_UNAVAILABLE_REASON == "model_unavailable"
    for text in (TRIED_TWO, NO_SECOND, failure_replies.PLANNER_TIMEOUT_REPLY,
                 failure_replies.PLANNER_UNUSABLE_REPLY):
        assert "\u2014" not in text, "the approved texts carry no em dash"

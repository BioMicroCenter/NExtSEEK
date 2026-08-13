"""extra_state writes during a CC turn must merge, not clobber.

Every write to ChatSession.extra_state rewrites the whole JSON column. The CC
turn holds a ChatSession object loaded at turn start; meanwhile the agent's
nextseek-pipeline op seeds extra_state.pipeline_agent via a nested query/async
request on the SAME session (a different ORM object). A read-modify-write
against the stale object dropped that seed, so the router gate saw the pipeline
inactive on the next turn and re-routed to CC, resetting the wizard.

Ported from v3-full-integration as Wave 1 of the dev/v3 reconciliation, adapted
to dev's chat_log schema: since 20b61e1 a CC entry's ``turn_id`` is a sequential
int and the run UUID is preserved as ``cc_run_id``.
"""
from django.contrib.auth.models import User
from django.test import TestCase

from nextseek_api.assistant.models_db import ChatSession
from nextseek_api.cc_assistant.cc_turn_complete import TurnCompletePayload
from nextseek_api.services.cc_assistant import (
    _append_cc_turn_complete,
    _merge_extra_state,
)


def _seed_concurrently(cs, extra_state):
    """Write directly to the DB row, leaving the caller's object stale."""
    ChatSession.objects.filter(pk=cs.pk).update(extra_state=extra_state)


class MergeExtraStateTests(TestCase):
    databases = {"default"}

    def test_merge_preserves_concurrently_seeded_key(self):
        user = User.objects.create_user("ccm1", password="p")
        cs = ChatSession.objects.create(user=user, extra_state={})
        _seed_concurrently(cs, {"pipeline_agent": {"active": True}})

        # cs is still the stale object (extra_state == {})
        _merge_extra_state(cs, cc_session_id="claude-123")

        cs.refresh_from_db()
        assert cs.extra_state["cc_session_id"] == "claude-123"
        assert cs.extra_state["pipeline_agent"]["active"] is True

    def test_merge_overwrites_only_the_named_key(self):
        user = User.objects.create_user("ccm3", password="p")
        cs = ChatSession.objects.create(user=user, extra_state={})
        _seed_concurrently(cs, {"cc_session_id": "old", "keep": "me"})

        _merge_extra_state(cs, cc_session_id="new")

        cs.refresh_from_db()
        assert cs.extra_state["cc_session_id"] == "new"
        assert cs.extra_state["keep"] == "me"


class CcTurnCompleteMergeTests(TestCase):
    databases = {"default"}

    def test_append_cc_turn_preserves_concurrent_pipeline_seed(self):
        user = User.objects.create_user("ccm4", password="p")
        cs = ChatSession.objects.create(user=user, extra_state={})
        _seed_concurrently(
            cs, {"pipeline_agent": {"active": True, "pipeline_key": "scrnaseq"}}
        )

        payload = TurnCompletePayload(
            chat_session=cs,  # stale object: extra_state still {}
            user_query="submit scrnaseq",
            assistant_reply="proposed the run",
            ts="2026-07-11T00:00:00",
            artifacts=None,
            cc_traces=[],
            turn_id="cc-uuid-1",  # the run UUID, not the chat_log entry id
            cc_session_id="claude-1",
            raw_jsonl=b"{}",
        )
        _append_cc_turn_complete(payload)

        cs.refresh_from_db()
        # the concurrently-seeded key survives...
        assert cs.extra_state["pipeline_agent"]["active"] is True
        assert cs.extra_state["pipeline_agent"]["pipeline_key"] == "scrnaseq"
        # ...and the CC turn is still recorded, under dev's schema: sequential
        # int turn_id, run UUID preserved as cc_run_id.
        entry = cs.extra_state["chat_log"][-1]
        assert entry["mode"] == "cc"
        assert entry["cc_run_id"] == "cc-uuid-1"
        assert isinstance(entry["turn_id"], int)

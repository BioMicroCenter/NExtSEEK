"""granular._graph_schema: the live graph schema, read-only, with no model call.

FREE tests: the chat_nextseek projection is patched, so nothing reaches Neo4j or a
model. They prove the op forwards only what it was asked for, that a comma-separated
``types`` argument is split rather than passed through whole, and that the answer's
``source`` (catalog or fallback) survives to the caller, because a caller that cannot
tell the two apart is back to describing a graph that may not exist.
"""
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from NessieAI.ns.granular import run_op
from NessieAI.ns.write_gate import (
    READ_CLASS_OPS,
    SIDECAR_OPS,
    build_gate,
    load_allowlist_from_entries,
)

LIVE = {
    "source": "catalog",
    "schema_version": "1.2",
    "catalog_hash": "abc123",
    "synced_at": "2026-09-17T00:00:00Z",
    "sample_types": 109,
    "resolved_types": ["TIS"],
    "unknown_types": [],
    "schema": "# NExtSEEK graph schema",
    "vocabulary": "INVESTIGATION TITLES (Investigation.title):\n'Impact'",
    "unavailable_reason": None,
    "fallback_fetched_at": None,
}


class GraphSchemaOpTests(SimpleTestCase):
    def setUp(self):
        self.config = SimpleNamespace()
        self.gate = build_gate(load_allowlist_from_entries([]))

    def _run(self, args):
        return run_op("graph-schema", args, config=self.config, session=None,
                      write_gate=self.gate)

    def test_it_returns_the_live_projection_unchanged(self):
        with patch("chat_nextseek.portable.graph_schema_snapshot", return_value=LIVE) as snap:
            out = self._run({})
        snap.assert_called_once_with(self.config, types=[], question="")
        self.assertEqual(out, LIVE)

    def test_types_are_split_and_the_question_is_forwarded(self):
        with patch("chat_nextseek.portable.graph_schema_snapshot", return_value=LIVE) as snap:
            self._run({"types": "TIS, D.SEQ ,,MUS", "query": "which assays"})
        snap.assert_called_once_with(self.config, types=["TIS", "D.SEQ", "MUS"],
                                     question="which assays")

    def test_a_fallback_answer_reaches_the_caller_as_a_fallback(self):
        fallback = dict(LIVE, source="fallback", schema_version=None, catalog_hash=None,
                        unavailable_reason="the graph has no GraphMeta node",
                        fallback_fetched_at="2026-08-21T00:00:00Z")
        with patch("chat_nextseek.portable.graph_schema_snapshot", return_value=fallback):
            out = self._run({})
        self.assertEqual(out["source"], "fallback")
        self.assertIn("GraphMeta", out["unavailable_reason"])
        self.assertEqual(out["fallback_fetched_at"], "2026-08-21T00:00:00Z")

    def test_no_model_and_no_cypher_agent_is_involved(self):
        with patch("chat_nextseek.portable.graph_schema_snapshot", return_value=LIVE), \
             patch("chat_nextseek.portable.entity_agent") as ent, \
             patch("chat_nextseek.portable.parser_agent") as par, \
             patch("chat_nextseek.portable.graph_agent") as gr:
            self._run({})
        ent.assert_not_called()
        par.assert_not_called()
        gr.assert_not_called()

    def test_the_gate_knows_the_label_and_treats_it_as_read_class(self):
        self.assertIn("graph-schema", SIDECAR_OPS)
        self.assertIn("graph-schema", READ_CLASS_OPS)
        self.assertIsNone(self.gate("graph-schema", None, None, False))

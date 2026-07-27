"""results_history must survive concurrent turns in the same session.

A turn reads the whole ``results_history`` JSON column at start and writes the
whole thing back at the end. gunicorn runs several sync workers and each turn
runs in a daemon thread, so two turns in one session can both read the same
snapshot — and the second write erases the first turn's bundle. Recall then
resolves follow-up questions against whichever bundle survived.
"""
from django.test import TestCase

from nextseek_api.assistant.session_adapter import DictSessionAdapter


class MergeHistoryTests(TestCase):
    merge = staticmethod(DictSessionAdapter._merge_history)

    def test_keeps_a_concurrent_turns_bundle(self):
        stored = [{"id": 1, "user_query": "mice"}, {"id": 2, "user_query": "nhp"}]
        cached = [{"id": 1, "user_query": "mice"}, {"id": 3, "user_query": "ndma"}]
        merged = self.merge(stored, cached)
        self.assertEqual([b["id"] for b in merged], [1, 2, 3])

    def test_this_turns_version_wins_on_conflict(self):
        stored = [{"id": 1, "reply": "stale"}]
        cached = [{"id": 1, "reply": "fresh"}]
        self.assertEqual(self.merge(stored, cached), [{"id": 1, "reply": "fresh"}])

    def test_order_is_stable(self):
        stored = [{"id": 5}, {"id": 6}]
        cached = [{"id": 7}]
        self.assertEqual([b["id"] for b in self.merge(stored, cached)], [5, 6, 7])

    def test_bundles_without_an_id_are_preserved(self):
        merged = self.merge([{"no_id": True}], [{"id": 1}])
        self.assertEqual(len(merged), 2)

    def test_non_dict_entries_are_dropped(self):
        self.assertEqual(self.merge(["junk", None], [{"id": 1}]), [{"id": 1}])

    def test_empty_sides(self):
        self.assertEqual(self.merge([], []), [])
        self.assertEqual(self.merge([], [{"id": 1}]), [{"id": 1}])
        self.assertEqual(self.merge([{"id": 1}], []), [{"id": 1}])

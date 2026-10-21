"""Size guard: big chat data goes to a file, the row keeps a link (SIZE-GUARD-DESIGN.md).

The bound under test is MySQL's max_allowed_packet (64 MB on dev and prod): no SQL parameter of a chat save may
come near it, whatever one turn carries, and every reader still sees the whole value.
"""
import gzip
import json
import logging
import os
import re
import time

from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings

from nextseek_api.assistant import spill
from nextseek_api.assistant.models_db import ChatSession, QueryTask
from nextseek_api.assistant.session_adapter import DictSessionAdapter

MB = 1024 * 1024
BOUND = spill.SPILL_COLUMN_BYTES + 64 * 1024


def _blob(tag, n):
    """n bytes of JSON text that differ per tag, so two blobs never share a file."""
    return (tag + "-") * (n // (len(tag) + 1))


class _Params:
    """Capture the size of every string SQL parameter a block sends."""

    def __enter__(self):
        self.sizes = []
        self._cm = connection.execute_wrapper(self._wrap)
        self._cm.__enter__()
        return self

    def __exit__(self, *exc):
        return self._cm.__exit__(*exc)

    def _wrap(self, execute, sql, params, many, context):
        for p in params or ():
            if isinstance(p, (str, bytes)):
                self.sizes.append(len(p))
        return execute(sql, params, many, context)

    @property
    def biggest(self):
        return max(self.sizes or [0])


class SpillBase(TestCase):
    databases = {"default"}

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name
        ov = override_settings(CHAT_SPILL_DIR=self.dir)
        ov.enable()
        self.addCleanup(ov.disable)
        self.user = User.objects.create_user("u", password="pw")

    def files(self):
        return [os.path.join(d, f) for d, _, fs in os.walk(self.dir) for f in fs]

    def raw(self, table, col, pk_col, pk):
        with connection.cursor() as c:
            c.execute(f"SELECT {col} FROM {table} WHERE {pk_col} = %s", [str(pk).replace("-", "") if table == "assistant_chat_session" else pk])
            return c.fetchone()[0]


class SizeBoundTests(SpillBase):
    def test_a_20_mb_api_result_full_saves_small_and_reads_back_equal(self):
        sess = ChatSession.objects.create(user=self.user)
        ad = DictSessionAdapter(sess)
        bundle = {"id": 1, "reply": "hi", "api_result_full": {"rows": _blob("a", 20 * MB)}}
        ad["results_history"] = [bundle]
        ad["last_debug"] = {"api_result_full": bundle["api_result_full"], "route": "x"}
        with _Params() as p:
            ad.save()
        self.assertLess(p.biggest, BOUND)
        got = ChatSession.objects.get(pk=sess.pk)
        self.assertEqual(got.results_history, [bundle])
        self.assertEqual(got.last_debug["api_result_full"], bundle["api_result_full"])
        self.assertEqual(got.last_debug["route"], "x")
        self.assertEqual(len(self.files()), 1)  # the same data in two places is one file

    def test_thirty_geo_turns_in_one_chat_stay_under_the_bound(self):
        sess = ChatSession.objects.create(user=self.user)
        want = []
        worst = 0
        for i in range(30):
            ad = DictSessionAdapter(ChatSession.objects.get(pk=sess.pk))
            ad.reload()
            bundle = {"id": i + 1, "reply": f"r{i}", "reporter_result": _blob(f"rr{i}", MB),
                      "api_result_full": _blob(f"ar{i}", MB)}
            want.append(bundle)
            ad["results_history"] = ad["results_history"] + [bundle]
            ad["last_debug"] = {"reporter_result": _blob(f"d{i}", MB)}
            with _Params() as p:
                ad.save()
            worst = max(worst, p.biggest)
        self.assertLess(worst, BOUND)
        self.assertEqual(ChatSession.objects.get(pk=sess.pk).results_history, want)

    def test_query_task_debug_spills_and_reply_stays_inline(self):
        sess = ChatSession.objects.create(user=self.user)
        result = {"reply": "the answer", "total_cost_usd": 0.5, "debug": {"api_result_full": _blob("q", 5 * MB)}}
        with _Params() as p:
            t = QueryTask.objects.create(session=sess, user=self.user, query="q", result=result)
        self.assertLess(p.biggest, 256 * 1024)
        self.assertEqual(QueryTask.objects.get(pk=t.pk).result, result)
        stored = json.loads(self.raw("assistant_query_task", "result", "id", t.pk))
        self.assertEqual(stored["reply"], "the answer")
        self.assertIn("$spill", json.dumps(stored))
        # collect.py reads through .values(); that must hydrate too
        self.assertEqual(QueryTask.objects.filter(pk=t.pk).values("result")[0]["result"], result)


class FieldTests(SpillBase):
    def test_a_row_with_no_links_reads_unchanged(self):
        sess = ChatSession.objects.create(user=self.user, extra_state={"a": [1, 2, {"b": "c"}]})
        self.assertEqual(ChatSession.objects.get(pk=sess.pk).extra_state, {"a": [1, 2, {"b": "c"}]})
        self.assertEqual(self.files(), [])

    def test_the_same_value_in_three_places_is_one_file(self):
        big = {"rows": _blob("same", MB)}
        out = spill.spill({"a": big, "b": big, "c": [big]})
        self.assertEqual(len(self.files()), 1)
        self.assertEqual(spill.hydrate(out), {"a": big, "b": big, "c": [big]})

    def test_a_small_value_comes_back_unchanged(self):
        v = {"a": "b" * 1000}
        self.assertIs(spill.spill(v), v)

    def test_a_link_with_a_bad_path_or_extra_keys_is_left_as_data(self):
        for bad in ({"$spill": "../../etc/passwd", "bytes": 1},
                    {"$spill": "ab/" + "0" * 64 + ".json.gz", "bytes": 1, "x": 1},
                    {"$spill": "AB/" + "0" * 64 + ".json.gz", "bytes": 1}):
            self.assertEqual(spill.hydrate({"k": bad}), {"k": bad})

    def test_a_missing_file_reads_as_none_with_an_error_log(self):
        link = {"$spill": "ab/" + "c" * 0 + "0" * 64 + ".json.gz", "bytes": 5}
        with self.assertLogs(spill.logger, level="ERROR") as cm:
            self.assertEqual(spill.hydrate({"k": link}), {"k": None})
        self.assertIn("0" * 64, cm.output[0])

    def test_a_column_of_many_mid_size_values_goes_whole(self):
        v = [{"id": i, "x": _blob(f"m{i}", 100 * 1024)} for i in range(60)]  # each under 256 KB, 6 MB together
        out = spill.spill(v)
        self.assertLess(len(json.dumps(out)), 1024)
        self.assertEqual(spill.hydrate(out), v)


def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


class SweepTests(SpillBase):
    def test_sweep_removes_the_unlinked_old_file_and_keeps_the_rest(self):
        linked = spill.spill({"k": _blob("L", MB)})["k"]["$spill"]
        inner = spill.spill({"k": _blob("I", MB)})["k"]["$spill"]
        # a file that holds a link to `inner`, itself linked from a row
        outer = spill.spill({"deep": {"link": {"$spill": inner, "bytes": 1}, "pad": _blob("P", MB)}})
        outer_path = outer["deep"]["$spill"] if "$spill" in outer["deep"] else None
        orphan = spill.spill({"k": _blob("O", MB)})["k"]["$spill"]
        fresh = spill.spill({"k": _blob("F", MB)})["k"]["$spill"]
        sess = ChatSession.objects.create(user=self.user)
        with spill.spilling_off():
            ChatSession.objects.filter(pk=sess.pk).update(
                results_history=[{"a": {"$spill": linked, "bytes": 1}}, outer])
        for p in (linked, inner, orphan, outer_path):
            if p:
                _age(os.path.join(self.dir, p), 3 * 86400)
        counts = spill.sweep()
        left = {os.path.relpath(f, self.dir) for f in self.files()}
        self.assertIn(linked, left)
        self.assertIn(inner, left)       # linked only from inside another file
        self.assertIn(fresh, left)       # unlinked but new
        self.assertNotIn(orphan, left)
        self.assertEqual(counts["deleted"], 1)

    def test_dry_run_deletes_nothing(self):
        orphan = spill.spill({"k": _blob("O", MB)})["k"]["$spill"]
        _age(os.path.join(self.dir, orphan), 3 * 86400)
        self.assertEqual(spill.sweep(dry_run=True)["deleted"], 1)
        self.assertEqual(len(self.files()), 1)


class CommandTests(SpillBase):
    def _old_row(self):
        sess = ChatSession.objects.create(user=self.user)
        with spill.spilling_off():
            ChatSession.objects.filter(pk=sess.pk).update(
                results_history=[{"id": 1, "api_result_full": _blob("old", 2 * MB)}])
        return sess

    def test_rewrite_dry_run_writes_nothing_then_rewrite_and_inline_round_trip(self):
        sess = self._old_row()
        before = self.raw("assistant_chat_session", "results_history", "session_id", sess.pk)
        out = []
        call_command("chat_spill", "--rewrite", "--dry-run", stdout=_W(out))
        self.assertEqual(self.files(), [])
        self.assertIn("1", "".join(out))
        self.assertEqual(self.raw("assistant_chat_session", "results_history", "session_id", sess.pk), before)
        call_command("chat_spill", "--rewrite", stdout=_W([]))
        after = self.raw("assistant_chat_session", "results_history", "session_id", sess.pk)
        self.assertLess(len(after), 1024)
        self.assertEqual(len(self.files()), 1)
        call_command("chat_spill", "--inline", stdout=_W([]))
        self.assertEqual(self.raw("assistant_chat_session", "results_history", "session_id", sess.pk), before)

    def test_inline_refuses_a_row_at_or_over_60_mb(self):
        sess = ChatSession.objects.create(user=self.user)
        with spill.spilling_off():
            link = spill.spill({"k": _blob("h", 2 * MB)})
        ChatSession.objects.filter(pk=sess.pk).update(extra_state={"x": {"$spill": link["k"]["$spill"], "bytes": 1}})
        with override_settings():
            spill.INLINE_MAX_BYTES, old = 1 * MB, spill.INLINE_MAX_BYTES
            try:
                call_command("chat_spill", "--inline", stdout=_W([]))
            finally:
                spill.INLINE_MAX_BYTES = old
        self.assertIn("$spill", self.raw("assistant_chat_session", "extra_state", "session_id", sess.pk))


class _W:
    def __init__(self, sink):
        self.sink = sink

    def write(self, s):
        self.sink.append(s)

    def flush(self):
        pass

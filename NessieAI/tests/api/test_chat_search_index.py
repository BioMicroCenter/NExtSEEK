"""The session search index (NessieAI/ns/chat_search_index.py index_chat) against the database."""
import json
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase

from NessieAI.cc.cc_transcript_store import compress
from NessieAI.ns import chat_search_index
from NessieAI.ns.chat_search_index import RESULT_CAP, index_chat, index_chat_quietly
from nextseek_api.assistant.models_db import CCSessionTranscript, ChatSearchEntry, ChatSession


def ns_turn(tid, q="find mice", reply="Found TIS-220831FLY-26.", bundle_id=None, **kw):
    return {"turn_id": tid, "user_query": q, "assistant_reply": reply, "bundle_id": bundle_id, **kw}


def cc_turn(tid, run_id, q="analyze", reply="Done."):
    return {"turn_id": tid, "user_query": q, "assistant_reply": reply, "cc_run_id": run_id}


def transcript(*blocks_by_record):
    return "\n".join(json.dumps(r) for r in blocks_by_record).encode()


class IndexBase(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("alice", password="p")

    def chat(self, log, history=None):
        return ChatSession.objects.create(user=self.user, extra_state={"chat_log": log},
                                          results_history=history or [])

    def rows(self, cs, **kw):
        return list(ChatSearchEntry.objects.filter(session=cs, **kw).order_by("id")
                    .values_list("turn_id", "source", "kind", "key", "record_type", "text"))


class IndexChatTests(IndexBase):
    def test_ns_turn_rows(self):
        bundle = {"id": 7, "graph_result": {"rows": [{"uid": "TIS-220831FLY-26"}, {"uid": "MUS-220101ABC-1"}]}}
        cs = self.chat([ns_turn(1, bundle_id=7, key_entities={"uids": ["NHP-220630FLY-9"], "projects": ["GBM"]})],
                       [bundle])
        index_chat(cs)
        self.assertEqual(self.rows(cs), [
            (1, "record", "uid", "NHP-220630FLY-9", "NHP", "question"),
            (1, "record", "uid", "TIS-220831FLY-26", "TIS", "answer"),
            (1, "record", "uid", "MUS-220101ABC-1", "MUS", "result"),
            (1, "record", "project", "GBM", "", ""),
            (1, "record", "turn", "", "", "2"),
        ])
        self.assertTrue(all(u == self.user.pk for u in ChatSearchEntry.objects.values_list("user_id", flat=True)))

    def test_cc_turn_steps_and_result_ids(self):
        cs = self.chat([cc_turn(1, "run-1")])
        CCSessionTranscript.objects.create(chat_session=cs, cc_session_id="s", turn_id="run-1", blob=compress(transcript(
            {"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Querying."},
                {"type": "tool_use", "name": "Bash", "input": {"command": "nextseek-graph-schema"}}]}},
            {"type": "user", "message": {"content": [{"type": "tool_result", "content": "MUS-220101ABC-1"}]}},
        )))
        index_chat(cs)
        self.assertEqual(self.rows(cs, source="steps"), [
            (1, "steps", "agent_text", "", "", "Querying."),
            (1, "steps", "tool_call", "", "", "Bash: nextseek-graph-schema"),
        ])
        self.assertIn((1, "record", "uid", "MUS-220101ABC-1", "MUS", "result"), self.rows(cs))

    def test_index_is_idempotent(self):
        cs = self.chat([ns_turn(1), ns_turn(2)])
        index_chat(cs)
        first = self.rows(cs)
        self.assertEqual(index_chat(cs), 0)
        self.assertEqual(self.rows(cs), first)

    def test_new_turn_indexed_old_ones_untouched(self):
        cs = self.chat([ns_turn(1)])
        index_chat(cs)
        ids_before = set(ChatSearchEntry.objects.filter(session=cs).values_list("id", flat=True))
        cs.extra_state = {"chat_log": [ns_turn(1), ns_turn(2, reply="MUS-220101ABC-5")]}
        cs.save()
        index_chat(cs)
        self.assertTrue(ids_before <= set(ChatSearchEntry.objects.filter(session=cs).values_list("id", flat=True)))
        self.assertIn((2, "record", "uid", "MUS-220101ABC-5", "MUS", "answer"), self.rows(cs))

    def test_window_prunes(self):
        cs = self.chat([ns_turn(1), ns_turn(2)])
        index_chat(cs)
        cs.extra_state = {"chat_log": [ns_turn(2), ns_turn(3)]}  # turn 1 left the 50-turn window
        cs.save()
        index_chat(cs)
        self.assertEqual(sorted({r[0] for r in self.rows(cs)}), [2, 3])

    def test_results_capped_with_total(self):
        uids = [f"TIS-220831FLY-{i}" for i in range(RESULT_CAP + 120)]
        cs = self.chat([ns_turn(1, reply="ok", bundle_id=1)], [{"id": 1, "graph_result": uids}])
        index_chat(cs)
        self.assertEqual(len(self.rows(cs, kind="uid")), RESULT_CAP)
        self.assertEqual(self.rows(cs, kind="turn")[0][5], str(RESULT_CAP + 120))

    def test_hidden_and_malformed_turns_skipped(self):
        cs = self.chat([{"turn_id": 1, "user_query": "q"}, "x", {"turn_id": "2", "user_query": "q", "assistant_reply": "a"},
                        ns_turn(3)])
        index_chat(cs)
        self.assertEqual({r[0] for r in self.rows(cs)}, {3})

    def test_corrupt_blob_skipped(self):
        cs = self.chat([cc_turn(1, "run-1", reply="MUS-220101ABC-7")])
        CCSessionTranscript.objects.create(chat_session=cs, cc_session_id="s", turn_id="run-1", blob=b"not zstd")
        with self.assertLogs("NessieAI.ns.chat_search_index", level="WARNING"):
            index_chat(cs)
        self.assertIn((1, "record", "uid", "MUS-220101ABC-7", "MUS", "answer"), self.rows(cs))

    def test_bundles_from_memory_need_no_read(self):
        cs = self.chat([ns_turn(1, reply="ok", bundle_id=4)])
        index_chat(cs, chat_log=cs.extra_state["chat_log"], bundles=[{"id": 4, "graph_result": "MUS-220101ABC-8"}])
        self.assertIn((1, "record", "uid", "MUS-220101ABC-8", "MUS", "result"), self.rows(cs))

    def test_deleting_the_chat_deletes_its_rows(self):
        cs = self.chat([ns_turn(1)])
        index_chat(cs)
        cs.delete()
        self.assertEqual(ChatSearchEntry.objects.count(), 0)

    def test_quiet_index_never_raises(self):
        cs = self.chat([ns_turn(1)])
        with mock.patch.object(chat_search_index, "index_chat", side_effect=RuntimeError("db down")), \
                self.assertLogs("NessieAI.ns.chat_search_index", level="WARNING"):
            index_chat_quietly(cs)


class BackfillCommandTests(IndexBase):
    def run_cmd(self, *args):
        from io import StringIO
        from django.core.management import call_command
        out = StringIO()
        call_command("chat_search_index", *args, stdout=out, stderr=StringIO())
        return out.getvalue()

    def test_dry_run_counts_and_writes_nothing(self):
        self.chat([ns_turn(1), ns_turn(2)])
        out = self.run_cmd("--dry-run")
        self.assertIn("1 indexed", out)
        self.assertIn("dry run", out)
        self.assertEqual(ChatSearchEntry.objects.count(), 0)

    def test_rebuild_equals_a_fresh_index(self):
        a = self.chat([ns_turn(1)])
        b = self.chat([ns_turn(1, reply="MUS-220101ABC-1")])
        index_chat(a)
        fresh = sorted(ChatSearchEntry.objects.values_list("session_id", "turn_id", "kind", "key", "text"))
        ChatSearchEntry.objects.filter(session=a).update(text="stale")
        self.run_cmd("--rebuild")
        index_chat(b)  # already done by the rebuild: writes nothing
        got = sorted(ChatSearchEntry.objects.filter(session=a).values_list("session_id", "turn_id", "kind", "key", "text"))
        self.assertEqual(got, fresh)
        self.assertTrue(ChatSearchEntry.objects.filter(session=b, key="MUS-220101ABC-1").exists())

    def test_user_scope(self):
        other = User.objects.create_user("bob", password="p")
        self.chat([ns_turn(1)])
        ChatSession.objects.create(user=other, extra_state={"chat_log": [ns_turn(1)]})
        self.run_cmd("--user", str(other.pk))
        self.assertEqual(set(ChatSearchEntry.objects.values_list("user_id", flat=True)), {other.pk})

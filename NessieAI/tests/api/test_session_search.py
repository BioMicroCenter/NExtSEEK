"""Search over a user's saved chats (nextseek_api/assistant/session_search.py).

What a person types finds the chat by its title, a question asked in it, or an answer given in it; never another
user's chat unless the caller searches every user's; never a spill link read as text.
"""
import os
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase, override_settings
from django.utils import timezone

from nextseek_api.assistant import spill
from nextseek_api.assistant.models_db import ChatSession
from nextseek_api.assistant.session_search import SOURCES, make_snippet, parse_terms, search_sessions


def turn(tid, q, a="ok"):
    return {"turn_id": tid, "user_query": q, "assistant_reply": a, "mode": "x", "ts": "2026-10-20T00:00:00Z"}


class SearchBase(TestCase):
    databases = {"default"}

    def setUp(self):
        self.a = User.objects.create_user("alice", password="p")
        self.b = User.objects.create_user("bob", password="p")

    def chat(self, user=None, title=None, log=None, **kw):
        extra = {"chat_log": log} if log is not None else {}
        return ChatSession.objects.create(user=user or self.a, title=title, extra_state=extra, **kw)

    def ids(self, res):
        return [str(r["session_id"]) for r in res["results"]]


class ParseTests(TestCase):
    def test_terms_lowercased_stripped_capped(self):
        self.assertEqual(parse_terms('  NDMA, "liver"  (mice) '), ["ndma", "liver", "mice"])
        self.assertEqual(len(parse_terms(" ".join(f"w{i}" for i in range(20)))), 8)
        self.assertEqual(parse_terms(" ,, "), [])

    def test_snippet_window_and_markdown(self):
        text = "| col | **bold** # head ---\n" + "x " * 250 + "the NDMA hit " + "y " * 250
        snip = make_snippet(text, ["ndma"])
        self.assertLessEqual(len(snip), 162)
        self.assertTrue(snip.startswith("…") and snip.endswith("…"))
        self.assertIn("NDMA", snip)
        for mark in ("|", "**", "#"):
            self.assertNotIn(mark, snip)

    def test_snippet_short_text_whole(self):
        self.assertEqual(make_snippet("Find **NDMA** mice", ["ndma"]), "Find NDMA mice")

    def test_snippet_markdown_links_keep_their_text(self):
        text = "Found [D.SEQ-231101GRI-1-PUB](/seek/samples/D.SEQ-231101GRI-1-PUB/) and ![plot](/m/x.png) here"
        self.assertEqual(make_snippet(text, ["d.seq"]), "Found D.SEQ-231101GRI-1-PUB and plot here")
        self.assertEqual(make_snippet("see [a [b] c](u) and [Foo](/w/Foo_bar)", ["foo"]), "see a [b] c and Foo")


class SearchTests(SearchBase):
    def test_a_term_only_in_a_link_address_does_not_match(self):
        link = "[TIS-1](/seek/sampletree/uid=TIS-1/) " + "word " * 60  # long: the cut-snippet path
        hit = self.chat(log=[turn(1, "show it", link)])
        self.assertEqual(search_sessions(self.a.pk, "sampletree")["results"], [])
        res = search_sessions(self.a.pk, "tis-1")["results"]
        self.assertEqual(self.ids({"results": res}), [str(hit.session_id)])
        self.assertTrue(res[0]["snippet"].startswith("TIS-1 word"))

    def test_other_users_chats_never_returned(self):
        mine = self.chat(log=[turn(1, "NDMA liver mice")])
        self.assertEqual(search_sessions(self.b.pk, "ndma")["results"], [])
        self.assertEqual(self.ids(search_sessions(self.a.pk, "ndma")), [str(mine.session_id)])

    def test_title_beats_question_beats_answer(self):
        ans = self.chat(title="x", log=[turn(1, "q", "NDMA in the answer")])
        q = self.chat(title="y", log=[turn(1, "NDMA question")])
        t = self.chat(title="NDMA title", log=[turn(1, "other")])
        ChatSession.objects.filter(pk=ans.pk).update(updated_at=timezone.now() + timedelta(days=1))
        res = search_sessions(self.a.pk, "ndma")
        self.assertEqual(self.ids(res), [str(t.session_id), str(q.session_id), str(ans.session_id)])
        self.assertEqual([r["matched_in"] for r in res["results"]], ["title", "question", "answer"])

    def test_all_terms_required_in_one_place(self):
        split = self.chat(log=[turn(1, "ndma"), turn(2, "liver")])
        both = self.chat(log=[turn(1, "nothing"), turn(2, "the ndma mice", "from the liver")])
        res = search_sessions(self.a.pk, "ndma liver")
        self.assertEqual(self.ids(res), [str(both.session_id)])
        self.assertEqual(res["results"][0]["matched_in"], "answer")
        self.assertEqual(res["results"][0]["turn_id"], 2)
        self.assertNotIn(str(split.session_id), self.ids(res))

    def test_word_prefix_not_substring(self):
        seq = self.chat(log=[turn(1, "Show RNA-seq samples")])
        addr = self.chat(log=[turn(1, "my address")])
        self.assertEqual(self.ids(search_sessions(self.a.pk, "SEQ")), [str(seq.session_id)])
        self.assertEqual(self.ids(search_sessions(self.a.pk, "add")), [str(addr.session_id)])  # typing on
        self.assertEqual(search_sessions(self.a.pk, "dress")["results"], [])  # never mid-word

    def test_regex_characters_are_literal(self):
        hit = self.chat(log=[turn(1, "SRA for D.SEQ-230512FOR-288-PUB")])
        self.chat(log=[turn(1, "SRA for DXSEQ-230512FOR-288-PUB")])
        self.assertEqual(self.ids(search_sessions(self.a.pk, "d.seq-230512for")), [str(hit.session_id)])
        self.assertEqual(search_sessions(self.a.pk, "(liver c++")["results"], [])

    def test_one_result_per_chat_with_match_count(self):
        self.chat(log=[turn(1, "ndma a"), turn(2, "ndma b"), turn(3, "x", "ndma c"), turn(4, "none")])
        res = search_sessions(self.a.pk, "ndma")
        self.assertEqual(res["total"], 1)
        self.assertEqual(res["results"][0]["match_count"], 3)
        self.assertEqual(res["results"][0]["turn_id"], 1)

    def test_turn_the_chat_hides_is_not_a_hit(self):
        # turn_rows hides an entry with no reply, no preview and no bundle (PD-6): a hit there opens nowhere.
        self.chat(log=[turn(1, "first"), {"turn_id": 2, "user_query": "ndma", "mode": "x"}])
        self.assertEqual(search_sessions(self.a.pk, "ndma")["results"], [])
        shown = self.chat(log=[{"turn_id": 1, "user_query": "ndma", "bundle_id": 4}])
        self.assertEqual(self.ids(search_sessions(self.a.pk, "ndma")), [str(shown.session_id)])

    def test_word_start_is_unicode_aware(self):
        self.chat(log=[turn(1, "a naïve question")])
        self.assertEqual(search_sessions(self.a.pk, "ve")["results"], [])
        self.assertEqual(len(search_sessions(self.a.pk, "naïve")["results"]), 1)

    def test_underscore_starts_a_word(self):
        hit = self.chat(log=[turn(1, "open counts_by_type.xlsx")])
        self.assertEqual(self.ids(search_sessions(self.a.pk, "type")), [str(hit.session_id)])

    def test_repeated_source_counts_once(self):
        self.chat(log=[turn(1, "ndma")])
        self.assertEqual(search_sessions(self.a.pk, "ndma", sources=["chats", "chats"])["total"], 1)

    def test_reply_preview_used_when_no_full_reply(self):
        hit = self.chat(log=[{"turn_id": 1, "user_query": "q", "assistant_reply_preview": "found NDMA"}])
        self.assertEqual(self.ids(search_sessions(self.a.pk, "ndma")), [str(hit.session_id)])

    def test_hit_shape(self):
        self.chat(title="T", log=[turn(1, "ndma")])
        res = search_sessions(self.a.pk, "ndma")
        self.assertEqual(set(res), {"query", "terms", "sources", "total", "results", "facets"})
        self.assertEqual(res["sources"], ["chats"])
        hit = res["results"][0]
        self.assertEqual(set(hit), {"source", "session_id", "title", "updated_at", "owner", "turn_id",
                                    "matched_in", "snippet", "match_count"})
        self.assertEqual(hit["source"], "chat")
        self.assertIsNone(hit["owner"])

    def test_untitled_chat_reads_new_chat(self):
        self.chat(log=[turn(1, "ndma")])
        self.assertEqual(search_sessions(self.a.pk, "ndma")["results"][0]["title"], "New chat")

    def test_corrupt_chat_log_skipped(self):
        self.chat(log="x")
        self.chat(log=[1, None, {"turn_id": 2, "user_query": None, "assistant_reply": 5}])
        good = self.chat(log=[turn(1, "ndma")])
        self.assertEqual(self.ids(search_sessions(self.a.pk, "ndma")), [str(good.session_id)])

    def test_legacy_chat_found_by_title_only(self):
        old = ChatSession.objects.create(user=self.a, title="Old",
                                         results_history=[{"id": 1, "user_query": "ndma"}])
        self.assertEqual(search_sessions(self.a.pk, "ndma")["results"], [])
        self.assertEqual(self.ids(search_sessions(self.a.pk, "old")), [str(old.session_id)])

    def test_limit(self):
        for i in range(30):
            self.chat(log=[turn(1, f"ndma {i}")])
        res = search_sessions(self.a.pk, "ndma", limit=20)
        self.assertEqual(len(res["results"]), 20)
        self.assertEqual(res["total"], 30)

    def test_since_days(self):
        old = self.chat(log=[turn(1, "ndma")])
        ChatSession.objects.filter(pk=old.pk).update(updated_at=timezone.now() - timedelta(days=40))
        self.assertEqual(search_sessions(self.a.pk, "ndma", since_days=30)["results"], [])
        self.assertEqual(len(search_sessions(self.a.pk, "ndma", since_days=90)["results"]), 1)
        self.assertEqual(len(search_sessions(self.a.pk, "ndma")["results"]), 1)

    def test_all_users_carries_owner(self):
        self.chat(user=self.a, log=[turn(1, "ndma")])
        self.chat(user=self.b, log=[turn(1, "ndma")])
        res = search_sessions(self.a.pk, "ndma", all_users=True)
        self.assertEqual(sorted(r["owner"] for r in res["results"]), ["alice", "bob"])
        with self.assertRaises(TypeError):
            search_sessions(None, "ndma")

    def test_sources_registry(self):
        self.assertEqual(set(SOURCES), {"chats", "records", "steps"})

    def test_empty_terms_return_nothing(self):
        self.chat(title="anything", log=[turn(1, "q")])
        self.assertEqual(search_sessions(self.a.pk, " ,, ")["results"], [])


class SpilledSearchTests(SearchBase):
    """A value the size guard moved to a file is searched as its content, never as the link."""

    def setUp(self):
        super().setUp()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        ov = override_settings(CHAT_SPILL_DIR=self.dir)
        ov.enable()
        self.addCleanup(ov.disable)

    def raw_extra_state(self, cs):
        with connection.cursor() as c:
            c.execute("SELECT extra_state FROM assistant_chat_session WHERE session_id = %s", [cs.session_id.hex])
            return c.fetchone()[0]

    def test_spilled_reply_is_found(self):
        long_reply = "padding " * 40 + "the zebrafish answer"
        with mock.patch.object(spill, "SPILL_VALUE_BYTES", 64):
            cs = self.chat(log=[turn(1, "q", long_reply)])
        self.assertIn("$spill", self.raw_extra_state(cs))
        self.assertEqual(self.ids(search_sessions(self.a.pk, "zebrafish")), [str(cs.session_id)])
        self.assertEqual(search_sessions(self.a.pk, "spill")["results"], [])

    def test_whole_column_spill_is_found(self):
        long_reply = "padding " * 40 + "the zebrafish answer"
        with mock.patch.object(spill, "SPILL_VALUE_BYTES", 64), mock.patch.object(spill, "SPILL_COLUMN_BYTES", 64):
            cs = self.chat(log=[turn(1, "q", long_reply)])
        raw = self.raw_extra_state(cs)
        self.assertIn("$spill", raw)
        self.assertNotIn("chat_log", raw)
        self.assertEqual(self.ids(search_sessions(self.a.pk, "zebrafish")), [str(cs.session_id)])

    def test_missing_spill_file_still_found_by_title(self):
        with mock.patch.object(spill, "SPILL_VALUE_BYTES", 64):
            cs = self.chat(title="Zebrafish chat", log=[turn(1, "q", "padding " * 40)])
        for d, _, fs in os.walk(self.dir):
            for f in fs:
                os.remove(os.path.join(d, f))
        with self.assertLogs("nextseek_api.assistant.spill", level="ERROR"):
            res = search_sessions(self.a.pk, "zebrafish")
        self.assertEqual(self.ids(res), [str(cs.session_id)])


class Part2SearchTests(SearchBase):
    """Records and agent steps, through the index the turn's end writes (NessieAI/ns/chat_search_index.py)."""

    def indexed(self, user=None, title=None, log=None, history=None, transcripts=()):
        from NessieAI.cc.cc_transcript_store import compress
        from NessieAI.ns.chat_search_index import index_chat
        from nextseek_api.assistant.models_db import CCSessionTranscript
        cs = ChatSession.objects.create(user=user or self.a, title=title, extra_state={"chat_log": log or []},
                                        results_history=history or [])
        for run_id, raw in transcripts:
            CCSessionTranscript.objects.create(chat_session=cs, cc_session_id="s", turn_id=run_id, blob=compress(raw))
        index_chat(cs)
        return cs

    def steps(self, text, command):
        import json
        return json.dumps({"type": "assistant", "message": {"content": [
            {"type": "text", "text": text}, {"type": "tool_use", "name": "Bash", "input": {"command": command}}]}}).encode()

    def test_a_record_id_finds_the_chat_that_showed_it(self):
        hit = self.indexed(log=[turn(1, "q", "ok") | {"bundle_id": 1}],
                           history=[{"id": 1, "graph_result": ["TIS-220831FLY-26", "TIS-220831FLY-27"]}])
        self.indexed(log=[turn(1, "q", "MUS-220101ABC-1")])
        res = search_sessions(self.a.pk, "tis-220831fly-26", sources=["chats", "records"])
        self.assertEqual(self.ids(res), [str(hit.session_id)])
        r = res["results"][0]
        self.assertEqual((r["source"], r["matched_in"], r["turn_id"]), ("record", "record", 1))
        self.assertIn("TIS-220831FLY-26", r["snippet"])

    def test_record_snippet_says_how_many_more(self):
        uids = [f"TIS-220831FLY-{i}" for i in range(10)]
        self.indexed(log=[turn(1, "q", "ok") | {"bundle_id": 1}], history=[{"id": 1, "graph_result": uids}])
        snip = search_sessions(self.a.pk, "tis", sources=["records"])["results"][0]["snippet"]
        self.assertEqual(snip, "TIS-220831FLY-0, TIS-220831FLY-1, TIS-220831FLY-2 and 7 more")

    def test_record_snippet_says_when_results_were_capped(self):
        from NessieAI.ns.chat_search_index import RESULT_CAP
        uids = [f"TIS-220831FLY-{i}" for i in range(RESULT_CAP + 20)]
        self.indexed(log=[turn(1, "q", "ok") | {"bundle_id": 1}], history=[{"id": 1, "graph_result": uids}])
        snip = search_sessions(self.a.pk, "tis", sources=["records"])["results"][0]["snippet"]
        self.assertTrue(snip.endswith(f"(first {RESULT_CAP} of {RESULT_CAP + 20} results searched)"), snip)

    def test_steps_only_when_asked(self):
        cs = self.indexed(log=[{"turn_id": 1, "user_query": "analyze", "assistant_reply": "Done.", "cc_run_id": "r1"}],
                          transcripts=[("r1", self.steps("Reading the graph schema.", "nextseek-graph-schema"))])
        self.assertEqual(search_sessions(self.a.pk, "schema", sources=["chats", "records"])["results"], [])
        res = search_sessions(self.a.pk, "schema", sources=["chats", "records", "steps"])
        self.assertEqual(self.ids(res), [str(cs.session_id)])
        self.assertEqual((res["results"][0]["source"], res["results"][0]["matched_in"]), ("steps", "steps"))

    def test_merge_keeps_the_best_tier_and_adds_counts(self):
        cs = self.indexed(title="Schema chat",
                          log=[{"turn_id": 1, "user_query": "analyze", "assistant_reply": "Done.", "cc_run_id": "r1"}],
                          transcripts=[("r1", self.steps("graph schema read", "nextseek-graph-schema"))])
        res = search_sessions(self.a.pk, "schema", sources=["chats", "steps"])
        self.assertEqual(res["total"], 1)
        r = res["results"][0]
        self.assertEqual((str(r["session_id"]), r["matched_in"], r["match_count"]), (str(cs.session_id), "title", 1))

    def test_tiers_answer_above_record_above_steps(self):
        steps = self.indexed(log=[{"turn_id": 1, "user_query": "q", "assistant_reply": "a", "cc_run_id": "r1"}],
                             transcripts=[("r1", self.steps("NHP work", "x"))])
        rec = self.indexed(log=[turn(1, "q", "ok") | {"bundle_id": 1}], history=[{"id": 1, "graph_result": "NHP-220630FLY-9"}])
        ans = self.indexed(log=[turn(1, "q", "the NHP answer")])
        res = search_sessions(self.a.pk, "nhp", sources=["chats", "records", "steps"])
        self.assertEqual(self.ids(res), [str(ans.session_id), str(rec.session_id), str(steps.session_id)])

    def test_filters_narrow_and_need_no_words(self):
        tis = self.indexed(log=[turn(1, "liver", "TIS-220831FLY-26") | {"key_entities": {"projects": ["GBM"]}}])
        mus = self.indexed(log=[turn(1, "liver", "MUS-220101ABC-1") | {"key_entities": {"projects": ["IMPACT"]}}])
        self.assertEqual(self.ids(search_sessions(self.a.pk, "liver", sources=["chats", "records"], record_type="TIS")),
                         [str(tis.session_id)])
        self.assertEqual(self.ids(search_sessions(self.a.pk, "", sources=["chats", "records"], project="IMPACT")),
                         [str(mus.session_id)])
        self.assertEqual(self.ids(search_sessions(self.a.pk, "", sources=["records"], record_type="TIS",
                                                  where="answer")), [str(tis.session_id)])
        self.assertEqual(search_sessions(self.a.pk, "", sources=["records"], record_type="TIS", where="result")["results"],
                         [])

    def test_filter_only_scoped(self):
        self.indexed(user=self.b, log=[turn(1, "q", "TIS-220831FLY-26")])
        self.assertEqual(search_sessions(self.a.pk, "", sources=["records"], record_type="TIS")["results"], [])
        self.assertEqual(len(search_sessions(self.a.pk, "", sources=["records"], record_type="TIS",
                                             all_users=True)["results"]), 1)

    def test_facets_count_chats_and_equal_direct_counts(self):
        self.indexed(log=[turn(1, "q", "TIS-220831FLY-26 TIS-220831FLY-27") | {"key_entities": {"projects": ["GBM"]}}])
        self.indexed(log=[turn(1, "q", "TIS-220831FLY-28 MUS-220101ABC-1")])
        self.indexed(user=self.b, log=[turn(1, "q", "TIS-220831FLY-29")])
        res = search_sessions(self.a.pk, "", sources=["records"], facets_only=True)
        self.assertEqual(res["results"], [])
        self.assertEqual(res["facets"], {"record_type": {"TIS": 2, "MUS": 1}, "project": {"GBM": 1},
                                         "where": {"answer": 2}})

    def test_facets_follow_the_matches(self):
        self.indexed(log=[turn(1, "liver", "TIS-220831FLY-26")])
        self.indexed(log=[turn(1, "brain", "MUS-220101ABC-1")])
        self.assertEqual(search_sessions(self.a.pk, "liver", sources=["chats", "records"], facets=True)["facets"]["record_type"],
                         {"TIS": 1})

    def test_counts_only_when_asked(self):
        self.indexed(log=[turn(1, "liver", "TIS-220831FLY-26")])
        res = search_sessions(self.a.pk, "liver", sources=["chats", "records"])
        self.assertEqual(res["facets"], {"record_type": {}, "project": {}, "where": {}})

    def test_no_query_joins_the_chat_table_to_the_index(self):
        # dev 10-26: a join from the index to assistant_chat_session (legacy latin1, ~1 GB) table-scanned the chats
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        cs = self.indexed(log=[{"turn_id": 1, "user_query": "q", "assistant_reply": "TIS-220831FLY-26", "cc_run_id": "r1"}],
                          transcripts=[("r1", self.steps("graph schema read", "nextseek-graph-schema"))])
        with CaptureQueriesContext(connection) as ctx:
            for kw in ({"sources": ["steps"]}, {"sources": ["steps"], "since_days": 30},
                       {"sources": ["records"], "record_type": "TIS", "since_days": 30, "facets": True}):
                q = "graph" if kw["sources"] == ["steps"] else ""
                self.assertEqual(self.ids(search_sessions(self.a.pk, q, **kw)), [str(cs.session_id)], kw)
        joined = [q["sql"] for q in ctx.captured_queries
                  if "assistant_chat_search_entry" in q["sql"] and "assistant_chat_session" in q["sql"]]
        self.assertEqual(joined, [])

    def test_facets_count_what_the_combined_filter_returns(self):
        # review 10-26: the question names a MUS, the answer a TIS; with where=answer, MUS must not be offered
        self.indexed(log=[turn(1, "q", "TIS-220831FLY-26") | {"key_entities": {"uids": ["MUS-220101ABC-1"]}}])
        res = search_sessions(self.a.pk, "", sources=["records"], where="answer", facets=True)
        self.assertEqual(res["facets"]["record_type"], {"TIS": 1})
        self.assertEqual(search_sessions(self.a.pk, "", sources=["records"], where="answer", record_type="MUS")["results"], [])
        only = search_sessions(self.a.pk, "", sources=["records"], record_type="MUS", facets_only=True)
        self.assertEqual(only["facets"]["where"], {"question": 1})


class ExactFirstTests(SearchBase):
    """A whole-word match ranks above a prefix match, whatever the source (dev 10-26: a full ID that is also the
    start of a longer ID, TIS-...-2 inside TIS-...-26, lost its own chat to chats showing the longer one)."""

    indexed = Part2SearchTests.indexed
    steps = Part2SearchTests.steps

    def test_exact_id_in_results_beats_a_longer_id_in_a_newer_answer(self):
        mine = self.indexed(log=[turn(1, "q", "ok") | {"bundle_id": 1}], history=[{"id": 1, "graph_result": "TIS-220831FLY-2"}])
        other = self.indexed(log=[turn(1, "q", "see TIS-220831FLY-26")])
        ChatSession.objects.filter(pk=other.pk).update(updated_at=timezone.now() + timedelta(days=1))
        self.assertEqual(self.ids(search_sessions(self.a.pk, "tis-220831fly-2", sources=["chats", "records"]))[0],
                         str(mine.session_id))

    def test_exact_record_beats_a_prefix_record(self):
        mine = self.indexed(log=[turn(1, "q", "ok") | {"bundle_id": 1}], history=[{"id": 1, "graph_result": "TIS-220831FLY-2"}])
        other = self.indexed(log=[turn(1, "q", "ok") | {"bundle_id": 1}], history=[{"id": 1, "graph_result": "TIS-220831FLY-27"}])
        ChatSession.objects.filter(pk=other.pk).update(updated_at=timezone.now() + timedelta(days=1))
        res = search_sessions(self.a.pk, "tis-220831fly-2", sources=["records"])
        self.assertEqual(self.ids(res), [str(mine.session_id), str(other.session_id)])

    def test_whole_word_beats_a_word_that_starts_the_same(self):
        whole = self.chat(log=[turn(1, "q", "the liver samples")])
        prefix = self.chat(log=[turn(1, "liverpool samples")])
        ChatSession.objects.filter(pk=prefix.pk).update(updated_at=timezone.now() + timedelta(days=1))
        self.assertEqual(self.ids(search_sessions(self.a.pk, "liver")), [str(whole.session_id), str(prefix.session_id)])

    def test_exact_steps_beat_prefix_steps(self):
        whole = self.indexed(log=[{"turn_id": 1, "user_query": "q", "assistant_reply": "a", "cc_run_id": "r1"}],
                             transcripts=[("r1", self.steps("read the graph", "x"))])
        prefix = self.indexed(log=[{"turn_id": 1, "user_query": "q", "assistant_reply": "a", "cc_run_id": "r2"}],
                              transcripts=[("r2", self.steps("read the graphs", "x"))])
        ChatSession.objects.filter(pk=prefix.pk).update(updated_at=timezone.now() + timedelta(days=1))
        self.assertEqual(self.ids(search_sessions(self.a.pk, "graph", sources=["steps"])),
                         [str(whole.session_id), str(prefix.session_id)])

    def test_a_type_code_is_a_whole_word_of_its_ids(self):
        rec = self.indexed(log=[turn(1, "q", "ok") | {"bundle_id": 1}], history=[{"id": 1, "graph_result": "NHP-220630FLY-9"}])
        prefix = self.indexed(log=[turn(1, "q", "ok") | {"bundle_id": 1}], history=[{"id": 1, "graph_result": "NHPX-220630FLY-9"}])
        ChatSession.objects.filter(pk=prefix.pk).update(updated_at=timezone.now() + timedelta(days=1))
        self.assertEqual(self.ids(search_sessions(self.a.pk, "nhp", sources=["records"])),
                         [str(rec.session_id), str(prefix.session_id)])

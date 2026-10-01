"""The CC ``aggregate`` op: counts and breakdowns, one to four parts, in parallel, inside the caller's projects.

The op resolves the vocabulary once over the question and its parts, then runs the graph op's own chain once per
part on a small thread pool: the parser, the graph agent with the aggregate brief, the Neo4j tool, at most one
retry, and the graph_search fallback on a scope refusal. It answers what finished by its internal deadline and
names the rest ``timed_out``. Every agent, the Neo4j tool and the REST call are faked here: no model, database or
server is reached, and the clock is faked where the deadline matters.
"""
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from chat_nextseek.cypher_scope import Scoped, scope_cypher
from chat_nextseek.graph_scope import GraphScope
from chat_nextseek.helpers.tools.neo4j import SCOPE_REFUSED
from chat_nextseek.helpers.uid_check import CHECK_CYPHER
from chat_nextseek.schemas import GraphAgentPlan, ParserPlan
from NessieAI.ns import aggregate
from NessieAI.ns.granular import OpValidationError, run_op

GRAPH_SEARCH = "/nextseek_api/samples/graph_search/"
BREAKDOWN = "MATCH (s:T_NHP) RETURN s.Species AS species, count(DISTINCT s) AS n ORDER BY n DESC"
COUNT = "MATCH (s:T_TIS) RETURN count(DISTINCT s) AS n"


def _ok(rows, **extra):
    return {"ok": True, "data": rows, "count": len(rows), "total": len(rows), "truncated": False,
            "scope": {"decision": "proven", "project_ids": [2, 13]}, **extra}


def _refused(codes, reasons=("line 1, column 1: not allowed",)):
    return {"ok": False, "error": f"{SCOPE_REFUSED} Reasons: {'; '.join(reasons)}.", "data": None,
            "scope": {"decision": "refused", "project_ids": [2, 13], "codes": list(codes), "reasons": list(reasons)}}


class Fakes:
    """The agents and the Neo4j tool, scripted per part question; every call is recorded (thread-safe)."""

    def __init__(self, cyphers, results, *, parser_hook=None):
        self.cyphers = cyphers          # part question -> [cypher for call 1, call 2, ...]
        self.results = results          # substring of a statement -> [result for run 1, run 2, ...]
        self.parser_hook = parser_hook
        self.lock = threading.Lock()
        self.entity_calls, self.parser_calls, self.graph_calls, self.executed = [], [], [], []
        self.write_gate = MagicMock()
        self.build = MagicMock(return_value=SimpleNamespace(
            endpoint=GRAPH_SEARCH, method="POST", requestBody={"sample_type": "T_NHP"}, queryParameters=None,
            model_dump=lambda: {"endpoint": GRAPH_SEARCH, "method": "POST"}))
        self.rest = MagicMock(return_value={"ok": True, "status_code": 200, "data": {
            "total": 704, "rows": [{"uuid": "NHP-1"}], "rows_missing": 0, "sample_types": ["NHP"]}})

    def entity(self, config, query):
        with self.lock:
            self.entity_calls.append(query)
        return SimpleNamespace(model_dump=lambda: {"sampletypes": [{"code": "NHP"}]})

    def parser(self, session, config, query, entity_out):
        with self.lock:
            self.parser_calls.append(query)
        if self.parser_hook:
            self.parser_hook(query)
        return ParserPlan(mode="graph_query", target_endpoint=None)

    def graph(self, config, query, entity_out, parser_plan, retry_context=None, refine_context=None):
        with self.lock:
            self.graph_calls.append({"query": query, "retry_context": retry_context, "refine_context": refine_context})
            n = sum(1 for c in self.graph_calls if c["query"] == query)
        script = self.cyphers[query]
        return GraphAgentPlan(cypher=script[min(n, len(script)) - 1], parameters={})

    def neo4j(self, config, cypher, params):
        with self.lock:
            self.executed.append(cypher)
            if cypher == CHECK_CYPHER:
                return self.results["UID_CHECK"](params)
            for key, script in self.results.items():
                if key in cypher:
                    n = sum(1 for c in self.executed if key in c)
                    return script[min(n, len(script)) - 1]
        raise AssertionError(f"no scripted result for {cypher!r}")

    def run(self, query, parts=None, config=None):
        args = {"query": query, "parts": json.dumps(parts) if parts is not None else ""}
        with patch("chat_nextseek.portable.entity_agent", side_effect=self.entity), \
             patch("chat_nextseek.portable.parser_agent", side_effect=self.parser), \
             patch("chat_nextseek.portable.graph_agent", side_effect=self.graph), \
             patch("chat_nextseek.portable.api_agent_build_request", self.build), \
             patch("chat_nextseek.helpers.tool_nextseek_api_request", self.rest):
            return run_op("aggregate", args, config=config or SimpleNamespace(), session=SimpleNamespace(),
                          write_gate=self.write_gate, neo4j_exec=self.neo4j)


SPECIES = [{"species": "Macaca mulatta", "n": 351}, {"species": "Macaca fascicularis", "n": 327},
           {"species": "rhesus", "n": 13}, {"species": None, "n": 13}]


class PartsTests(SimpleTestCase):
    def test_no_parts_means_the_question_is_the_one_part(self):
        q = "What species are the NHP samples?"
        fakes = Fakes({q: [BREAKDOWN]}, {"T_NHP": [_ok(SPECIES)]})

        out = fakes.run(q)

        self.assertEqual(fakes.entity_calls, [q])
        self.assertEqual(fakes.parser_calls, [q])
        self.assertEqual(len(out["parts"]), 1)
        part = out["parts"][0]
        self.assertEqual((part["part"], part["question"], part["status"]), (1, q, "ok"))
        self.assertEqual(out["question"], q)
        self.assertTrue(out["complete"])
        self.assertEqual(out["deadline_s"], aggregate.OP_DEADLINE_S)
        json.dumps(out)  # crosses the wire as JSON

    def test_bad_parts_are_a_validation_error_before_any_agent_runs(self):
        fakes = Fakes({}, {})
        for raw in ("not json", '{"a": 1}', '[1, "x"]', '["", "x"]', "[]", json.dumps(["a"] * 5),
                    json.dumps(["x" * (aggregate.MAX_PART_CHARS + 1)])):
            with self.subTest(parts=raw), self.assertRaises(OpValidationError):
                with patch("chat_nextseek.portable.entity_agent", side_effect=fakes.entity):
                    run_op("aggregate", {"query": "q", "parts": raw}, config=SimpleNamespace(),
                           session=SimpleNamespace(), write_gate=MagicMock(), neo4j_exec=fakes.neo4j)
        self.assertEqual(fakes.entity_calls, [])

    def test_the_entity_agent_runs_once_and_the_parser_and_graph_agent_once_per_part(self):
        parts = ["How many samples have no parent?", "How many samples have no children?", "How many TIS samples?"]
        fakes = Fakes({parts[0]: ["MATCH (s:Sample) WHERE NOT (s)-[:DERIVED_FROM]->() RETURN count(s) AS roots"],
                       parts[1]: ["MATCH (s:Sample) WHERE NOT ()-[:DERIVED_FROM]->(s) RETURN count(s) AS leaves"],
                       parts[2]: [COUNT]},
                      {"AS roots": [_ok([{"roots": 5}])], "AS leaves": [_ok([{"leaves": 7}])],
                       "T_TIS": [_ok([{"n": 40}])]})

        out = fakes.run("Roots, leaves and tissue", parts)

        self.assertEqual(len(fakes.entity_calls), 1)
        for text in parts:
            self.assertIn(text, fakes.entity_calls[0])
        self.assertEqual(sorted(fakes.parser_calls), sorted(parts))
        self.assertEqual(sorted(c["query"] for c in fakes.graph_calls), sorted(parts))
        self.assertEqual([p["part"] for p in out["parts"]], [1, 2, 3])
        self.assertEqual([p["question"] for p in out["parts"]], parts)
        self.assertEqual([p["sum_of_group_counts"] for p in out["parts"]], [5, 7, 40])

    def test_parts_run_concurrently(self):
        parts = ["part one", "part two", "part three"]
        barrier = threading.Barrier(3, timeout=5)
        fakes = Fakes({p: [COUNT] for p in parts}, {"T_TIS": [_ok([{"n": 1}])]},
                      parser_hook=lambda query: barrier.wait())

        out = fakes.run("three at once", parts)

        self.assertEqual([p["status"] for p in out["parts"]], ["ok", "ok", "ok"])

    def test_the_brief_reaches_every_graph_agent_call(self):
        parts = ["How many TIS samples?", "Break NHP samples down by species."]
        fakes = Fakes({parts[0]: [COUNT], parts[1]: [BREAKDOWN, BREAKDOWN]},
                      {"T_TIS": [_ok([{"n": 3}])], "T_NHP": [_ok([]), _ok([])]})

        fakes.run("tissue and species", parts)

        self.assertEqual(len(fakes.graph_calls), 3)  # one part retried once on zero rows
        for call in fakes.graph_calls:
            self.assertIn(aggregate.AGGREGATE_BRIEF, call["refine_context"])
            self.assertIn("tissue and species", call["refine_context"])  # the whole question, as context

    def test_a_one_part_question_gets_the_brief_without_a_context_line(self):
        q = "How many TIS samples?"
        fakes = Fakes({q: [COUNT]}, {"T_TIS": [_ok([{"n": 3}])]})

        fakes.run(q)

        self.assertEqual(fakes.graph_calls[0]["refine_context"], aggregate.AGGREGATE_BRIEF)


class ShapeTests(SimpleTestCase):
    def _one(self, cypher, result, q="q"):
        fakes = Fakes({q: [cypher]}, {"MATCH": [result]})
        out = fakes.run(q)
        return out, out["parts"][0], fakes

    def test_a_breakdown_carries_its_groups_sum_and_missing_value_bucket(self):
        out, part, fakes = self._one(BREAKDOWN, _ok(SPECIES))

        self.assertEqual(part["kind"], "breakdown")
        self.assertEqual(part["columns"], ["species", "n"])
        self.assertEqual(part["groups"], SPECIES)
        self.assertEqual(part["group_count"], 4)
        self.assertEqual(part["sum_of_group_counts"], 704)
        self.assertTrue(part["groups_may_overlap"])
        self.assertNotIn("sum", part)
        self.assertEqual(part["null_group"], 13)
        self.assertFalse(part["truncated"])
        self.assertEqual(part["scope"], {"decision": "proven", "project_ids": [2, 13]})
        self.assertEqual(part["attempts"], [{"reason": "initial", "ok": True, "rows": 4}])
        self.assertIsNone(part["fallback"])

    def test_a_breakdown_whose_groups_overlap_never_calls_its_sum_a_sample_total(self):
        # One sample with two assays counts in both groups: 5 is not the number of samples, and nothing says it is.
        by_assay = [{"assay": "RNA-seq", "n": 3}, {"assay": "WES", "n": 2}]
        out, part, _ = self._one("MATCH (s:T_TIS)-[:IN_ASSAY]->(a:Assay) RETURN a.title AS assay, "
                                 "count(DISTINCT s) AS n ORDER BY n DESC", _ok(by_assay))

        self.assertEqual(part["kind"], "breakdown")
        self.assertEqual(part["sum_of_group_counts"], 5)
        self.assertTrue(part["groups_may_overlap"])
        self.assertNotIn("sum", part)
        self.assertNotIn("sum", json.dumps(out["notes"]))

    def test_one_group_cannot_overlap(self):
        _, part, _ = self._one(BREAKDOWN, _ok([{"species": "Macaca mulatta", "n": 351}]))

        self.assertEqual((part["kind"], part["sum_of_group_counts"]), ("breakdown", 351))
        self.assertFalse(part["groups_may_overlap"])

    def test_the_op_appends_a_row_cap_the_tool_sees(self):
        _, part, fakes = self._one(BREAKDOWN, _ok(SPECIES))

        ran = [c for c in fakes.executed if c != CHECK_CYPHER]
        self.assertEqual(ran, [BREAKDOWN + "\nLIMIT 1001"])
        self.assertEqual(part["cypher"], BREAKDOWN + "\nLIMIT 1001")

    def test_a_statement_with_its_own_limit_is_left_alone(self):
        for cypher in (BREAKDOWN + " LIMIT 10", BREAKDOWN + " LIMIT $top", BREAKDOWN + " SKIP 5 LIMIT 10;"):
            with self.subTest(cypher=cypher):
                _, _, fakes = self._one(cypher, _ok(SPECIES))
                self.assertEqual(fakes.executed, [cypher])

    def test_a_trailing_limit_followed_by_a_comment_is_the_statements_own(self):
        for cypher in (BREAKDOWN + " LIMIT 10 // top ten", BREAKDOWN + " LIMIT 10 /* top ten */",
                       BREAKDOWN + "\nLIMIT $top; // the user's top n\n", BREAKDOWN + " LIMIT 10 // a\n// b"):
            with self.subTest(cypher=cypher):
                self.assertEqual(aggregate.cap_rows(cypher, {}), cypher)

    def test_a_limit_inside_a_comment_or_a_string_is_not_one(self):
        for cypher in (BREAKDOWN + " // LIMIT 10", BREAKDOWN + " /* LIMIT 10 */",
                       "MATCH (s:T_NHP) WHERE s.Note = 'LIMIT 10' RETURN count(DISTINCT s) AS n"):
            with self.subTest(cypher=cypher):
                self.assertEqual(aggregate.cap_rows(cypher, {}), cypher + "\nLIMIT 1001")

    def test_a_terminator_before_a_trailing_comment_goes(self):
        self.assertEqual(aggregate.cap_rows(BREAKDOWN + "; // by species", {}),
                         BREAKDOWN + " // by species\nLIMIT 1001")
        self.assertEqual(aggregate.cap_rows("MATCH (s:T_NHP) WHERE s.Note = 'a;b' RETURN count(s) AS n;", {}),
                         "MATCH (s:T_NHP) WHERE s.Note = 'a;b' RETURN count(s) AS n\nLIMIT 1001")

    def test_every_capped_statement_still_proves(self):
        # The prover refused the old double LIMIT as syntax, and a scope refusal sent the part to the fallback with
        # a note saying it could not be confirmed to stay within the user's projects.
        scope = GraphScope.for_projects([2, 13], source="test")
        for cypher in (BREAKDOWN, BREAKDOWN + " LIMIT 10 // top ten", BREAKDOWN + " // LIMIT 10",
                       BREAKDOWN + " LIMIT 10 /* top ten */;", BREAKDOWN + "; // by species"):
            with self.subTest(cypher=cypher):
                out = scope_cypher(aggregate.cap_rows(cypher, {}), {}, scope)
                self.assertIsInstance(out, Scoped, getattr(out, "reasons", out))

    def test_a_single_all_numeric_row_is_a_count(self):
        _, part, _ = self._one(COUNT, _ok([{"n": 40}]))

        self.assertEqual((part["kind"], part["sum_of_group_counts"], part["groups"]), ("count", 40, [{"n": 40}]))
        self.assertFalse(part["groups_may_overlap"])
        self.assertIsNone(part["null_group"])

    def test_two_numbers_in_one_row_are_a_count_with_no_single_sum(self):
        _, part, _ = self._one("MATCH (s:Sample) RETURN count(s) AS roots, count(s) AS leaves",
                               _ok([{"roots": 5, "leaves": 7}]))

        self.assertEqual(part["kind"], "count")
        self.assertIsNone(part["sum_of_group_counts"])
        self.assertIsNone(part["groups_may_overlap"])

    def test_one_numeric_group_is_a_breakdown_not_a_count(self):
        # Every mouse aged 5 weeks: one group whose value is a number, then the brief's count column n.
        _, part, _ = self._one("MATCH (s:T_MUS) RETURN s.Age AS age, count(DISTINCT s) AS n ORDER BY n DESC",
                               _ok([{"age": 5, "n": 10}]))

        self.assertEqual(part["kind"], "breakdown")
        self.assertEqual((part["sum_of_group_counts"], part["null_group"], part["group_count"]), (10, 0, 1))
        self.assertFalse(part["groups_may_overlap"])

    def test_records_are_returned_as_rows_and_flagged(self):
        rows = [{"uid": "TIS-1", "Organ": "lung"}, {"uid": "TIS-2", "Organ": "Lung"}]
        out, part, _ = self._one("MATCH (s:T_TIS) RETURN s.uuid AS uid, s.Organ AS Organ", _ok(rows))

        self.assertEqual(part["kind"], "rows")
        self.assertEqual(part["groups"], rows)
        self.assertIsNone(part["sum_of_group_counts"])
        self.assertIsNone(part["groups_may_overlap"])
        self.assertTrue(any("records" in note for note in out["notes"]))

    def test_a_truncated_breakdown_reports_the_true_group_count(self):
        rows = [{"uid": f"TIS-{i}", "n": 1} for i in range(1001)]
        out, part, _ = self._one("MATCH (s:T_TIS) RETURN s.uuid AS uid, count(s) AS n",
                                 _ok(rows, truncated=True, total=1500, limit=1001))

        self.assertEqual(part["kind"], "breakdown")
        self.assertEqual(len(part["groups"]), aggregate.GROUP_CAP)
        self.assertTrue(part["truncated"])
        self.assertEqual(part["group_count"], 1500)
        self.assertEqual(part["sum_of_group_counts"], 1000)
        self.assertTrue(any("1,000 of 1,500 groups" in note for note in out["notes"]))

    def test_an_empty_breakdown_is_empty_not_an_error(self):
        _, part, _ = self._one(BREAKDOWN, _ok([]))

        self.assertEqual(part["status"], "empty")
        self.assertEqual((part["groups"], part["sum_of_group_counts"], part["group_count"]), ([], 0, 0))
        self.assertFalse(part["groups_may_overlap"])

    def test_a_query_error_is_an_error_part(self):
        failure = {"ok": False, "error": "Neo.ClientError: bad", "data": None,
                   "scope": {"decision": "proven", "project_ids": [2]}}
        _, part, _ = self._one(BREAKDOWN, failure)

        self.assertEqual(part["status"], "error")
        self.assertIn("bad", part["error"])

    def test_a_graph_agent_with_no_cypher_is_an_error_part(self):
        _, part, fakes = self._one("", _ok([]))

        self.assertEqual(part["status"], "error")
        self.assertEqual(fakes.executed, [])


class RetryTests(SimpleTestCase):
    def test_a_zero_that_is_zero_again_keeps_the_first_result(self):
        q = "How many HeLa samples?"
        first, second = COUNT + " // first", COUNT + " // second"
        fakes = Fakes({q: [first, second]}, {"// first": [_ok([{"n": 0}])], "// second": [_ok([{"n": 0}])]})

        out = fakes.run(q)

        part = out["parts"][0]
        self.assertEqual(len(fakes.graph_calls), 2)
        self.assertIn("matched 0 records", fakes.graph_calls[1]["retry_context"])
        self.assertIn(first, fakes.graph_calls[1]["retry_context"])
        self.assertNotIn("LIMIT 1001", fakes.graph_calls[1]["retry_context"])
        self.assertEqual(part["status"], "empty")
        self.assertTrue(part["cypher"].startswith(first))
        self.assertEqual([a["reason"] for a in part["attempts"]], ["initial", "zero_rows"])
        self.assertFalse(part["attempts"][1]["kept"])
        self.assertFalse(any("changed filter" in note for note in out["notes"]))

    def test_a_retry_that_finds_something_is_kept_and_disclosed(self):
        q = "How many HeLa samples?"
        first, second = COUNT + " // first", COUNT + " // second"
        fakes = Fakes({q: [first, second]}, {"// first": [_ok([{"n": 0}])], "// second": [_ok([{"n": 4}])]})

        out = fakes.run(q)

        part = out["parts"][0]
        self.assertEqual((part["status"], part["sum_of_group_counts"]), ("ok", 4))
        self.assertTrue(part["cypher"].startswith(second))
        self.assertTrue(part["attempts"][1]["kept"])
        self.assertTrue(any("changed filter" in note for note in out["notes"]))

    def test_a_uid_with_a_pub_suffix_is_checked_and_noted(self):
        q = "How many samples derive from TIS-230830ENG-1-PUB?"
        fakes = Fakes({q: [COUNT]}, {
            "UID_CHECK": lambda params: {"ok": True, "data": [
                {"uid": "TIS-230830ENG-1-PUB", "exact": False, "base_uuid": "TIS-230830ENG-1", "suffixed": []}]},
            "T_TIS": [_ok([{"n": 938}])]})

        out = fakes.run(q)

        self.assertEqual(fakes.executed[0], CHECK_CYPHER)
        self.assertIn("stored as TIS-230830ENG-1", fakes.graph_calls[0]["refine_context"])
        self.assertTrue(any("stores that sample as TIS-230830ENG-1" in note for note in out["notes"]))

    def test_a_call_subquery_refusal_gets_one_repair_then_the_fallback(self):
        q = "How many NHP samples by species, and how many in total?"
        call = "CALL { MATCH (s:T_NHP) RETURN count(s) AS n } RETURN n"
        fakes = Fakes({q: [call, call]}, {"CALL {": [_refused(["call_subquery"], ["a CALL subquery is not allowed"])]})

        out = fakes.run(q)

        part = out["parts"][0]
        self.assertEqual(len(fakes.graph_calls), 2)
        repair = fakes.graph_calls[1]["retry_context"]
        self.assertIn("a CALL subquery is not allowed", repair)
        self.assertIn("CALL", repair)
        self.assertEqual([a["reason"] for a in part["attempts"]], ["initial", "shape_repair"])
        self.assertEqual(part["status"], "fallback")
        self.assertEqual(part["sum_of_group_counts"], 704)
        self.assertFalse(part["groups_may_overlap"])  # graph_search counts each sample once
        self.assertEqual(part["groups"], [])
        self.assertEqual(part["fallback"]["total"], 704)
        self.assertNotIn("rows", part["fallback"])  # no sample rows cross the wire
        fakes.write_gate.assert_called_once_with("api-read", GRAPH_SEARCH, "POST", False)
        self.assertTrue(any("could not be broken down" in note for note in out["notes"]))

    def test_a_real_call_refusal_with_its_follow_on_syntax_code_is_repaired(self):
        # The prover skips a refused CALL subquery, so every name bound inside it then reads as unbound: a real
        # CALL refusal always carries "syntax" as well (measured against cypher_scope.scope_cypher).
        q = "Species of NHP samples"
        call = "CALL { MATCH (s:T_NHP) RETURN s } RETURN s.Species AS species, count(s) AS n"
        fakes = Fakes({q: [call, BREAKDOWN]}, {"CALL {": [_refused(
            ["call_subquery", "syntax"], ["line 1, column 1: a CALL subquery is not allowed",
                                          "line 1, column 42: the name s is not bound here"])],
            "T_NHP": [_ok(SPECIES)]})

        part = fakes.run(q)["parts"][0]

        self.assertEqual(len(fakes.graph_calls), 2)
        self.assertEqual((part["status"], part["sum_of_group_counts"]), ("ok", 704))

    def test_a_syntax_refusal_alone_gets_no_repair(self):
        q = "Species of NHP samples"
        fakes = Fakes({q: ["MATCH (s:T_NHP RETURN s"]}, {"T_NHP": [_refused(["syntax"])]})

        part = fakes.run(q)["parts"][0]

        self.assertEqual(len(fakes.graph_calls), 1)
        self.assertEqual(part["status"], "fallback")

    def test_a_repair_that_proves_is_the_answer(self):
        q = "Species of NHP samples"
        call = "CALL { MATCH (s:T_NHP) RETURN s } RETURN s.Species AS species, count(s) AS n"
        fakes = Fakes({q: [call, BREAKDOWN]}, {"CALL {": [_refused(["call_subquery"])], "T_NHP": [_ok(SPECIES)]})

        part = fakes.run(q)["parts"][0]

        self.assertEqual((part["status"], part["sum_of_group_counts"]), ("ok", 704))
        self.assertIsNone(part["fallback"])
        fakes.rest.assert_not_called()

    def test_an_unjoined_node_refusal_gets_no_repair(self):
        q = "Studies of NHP samples"
        bad = "MATCH (st:Study) RETURN st.title AS study, count(st) AS n"
        fakes = Fakes({q: [bad]}, {"Study": [_refused(["unjoined_node"])]})

        part = fakes.run(q)["parts"][0]

        self.assertEqual(len(fakes.graph_calls), 1)
        self.assertEqual(part["status"], "fallback")

    def test_a_fallback_that_fails_leaves_the_part_refused(self):
        q = "Studies of NHP samples"
        fakes = Fakes({q: ["MATCH (st:Study) RETURN count(st) AS n"]}, {"Study": [_refused(["unjoined_node"])]})
        fakes.rest.return_value = {"ok": False, "status_code": 422, "data": {"detail": "bad filter"}}

        out = fakes.run(q)

        part = out["parts"][0]
        self.assertEqual(part["status"], "refused")
        self.assertIn("422", part["error"])
        self.assertTrue(any("could not be answered" in note for note in out["notes"]))


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class DeadlineTests(SimpleTestCase):
    def test_parts_that_miss_the_deadline_come_back_timed_out_on_time(self):
        parts = ["fast part", "slow part"]
        clock = _Clock()
        fast_done, release, slow_finished = threading.Event(), threading.Event(), threading.Event()

        def hook(query):
            if query == "slow part":
                fast_done.wait(5)
                time.sleep(0.5)  # the fast part has only its fake query and its shaping left: let it finish
                clock.now += aggregate.OP_DEADLINE_S + 1  # the slow part outlives the op's deadline
                release.wait(10)
                slow_finished.set()
                raise RuntimeError("released after the op answered")

        def graph(config, query, entity_out, parser_plan, retry_context=None, refine_context=None):
            fast_done.set()
            return GraphAgentPlan(cypher=COUNT, parameters={})

        fakes = Fakes({}, {"T_TIS": [_ok([{"n": 2}])]}, parser_hook=hook)
        fakes.graph = graph
        wall = time.monotonic()
        with patch("NessieAI.ns.aggregate._monotonic", clock):
            out = fakes.run("fast and slow", parts)
        try:
            self.assertLess(time.monotonic() - wall, 5.0)
            self.assertFalse(out["complete"])
            self.assertEqual([p["status"] for p in out["parts"]], ["ok", "timed_out"])
            self.assertEqual(out["parts"][1]["question"], "slow part")
            self.assertTrue(any("did not finish" in note for note in out["notes"]))
        finally:
            release.set()
            slow_finished.wait(5)

    def test_no_retry_or_fallback_starts_with_less_than_the_minimum_left(self):
        q = "How many HeLa samples?"
        clock = _Clock()
        fakes = Fakes({q: [COUNT, COUNT]}, {"T_TIS": [_ok([{"n": 0}])]},
                      parser_hook=lambda query: setattr(clock, "now", clock.now + aggregate.OP_DEADLINE_S
                                                        - aggregate.MIN_REMAINING_S + 1))
        with patch("NessieAI.ns.aggregate._monotonic", clock):
            part = fakes.run(q)["parts"][0]

        self.assertEqual(len(fakes.graph_calls), 1)
        self.assertEqual(part["status"], "empty")

    def test_a_late_refusal_hands_the_fallback_plan_back_unrun(self):
        q = "Studies of NHP samples"
        clock = _Clock()
        fakes = Fakes({q: ["MATCH (st:Study) RETURN count(st) AS n"]}, {"Study": [_refused(["unjoined_node"])]},
                      parser_hook=lambda query: setattr(clock, "now", clock.now + aggregate.OP_DEADLINE_S
                                                        - aggregate.MIN_REMAINING_S + 1))
        with patch("NessieAI.ns.aggregate._monotonic", clock):
            part = fakes.run(q)["parts"][0]

        self.assertEqual(part["status"], "refused")
        self.assertFalse(part["fallback"]["ran"])
        self.assertEqual(part["fallback"]["parser_plan"]["target_endpoint"], GRAPH_SEARCH)
        fakes.build.assert_not_called()

    def test_no_part_starts_when_the_vocabulary_step_leaves_too_little_time(self):
        parts = ["part one", "part two"]
        clock = _Clock()
        fakes = Fakes({p: [COUNT] for p in parts}, {"T_TIS": [_ok([{"n": 1}])]})
        entity = fakes.entity

        def slow_entity(config, query):
            clock.now += aggregate.OP_DEADLINE_S - aggregate.MIN_REMAINING_S + 1
            return entity(config, query)

        fakes.entity = slow_entity
        with patch("NessieAI.ns.aggregate._monotonic", clock):
            out = fakes.run("two parts, late", parts)

        self.assertEqual(fakes.parser_calls, [])  # no model call is spent on a part that cannot finish
        self.assertFalse(out["complete"])
        self.assertEqual([p["status"] for p in out["parts"]], ["timed_out", "timed_out"])
        self.assertTrue(any("vocabulary" in note for note in out["notes"]))

    def test_a_part_that_raises_is_an_error_part_and_the_others_still_answer(self):
        parts = ["good part", "bad part"]

        def hook(query):
            if query == "bad part":
                raise RuntimeError("model unavailable")

        fakes = Fakes({"good part": [COUNT]}, {"T_TIS": [_ok([{"n": 9}])]}, parser_hook=hook)

        out = fakes.run("good and bad", parts)

        self.assertEqual([p["status"] for p in out["parts"]], ["ok", "error"])
        self.assertIn("model unavailable", out["parts"][1]["error"])
        self.assertTrue(out["complete"])

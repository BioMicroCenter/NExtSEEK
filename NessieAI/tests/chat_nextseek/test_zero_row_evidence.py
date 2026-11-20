"""zero_row_evidence: what the graph holds for a zero-row statement's filters, read for the retry.

Every read is faked: ``count_of``, ``rows_of`` and ``live_values`` are patched on graph_review_counts.
"""
from __future__ import annotations

import pytest

from chat_nextseek import graph_review_counts as g2
from chat_nextseek.graph_review import in_list_filters

CFG = object()


class _Values:
    def __init__(self, table):
        self.table, self.asked = table, []

    def values(self, label, attr):
        self.asked.append((label, attr))
        return self.table.get(f"{label}.{attr}")


@pytest.fixture
def fakes(monkeypatch):
    box = {"counts": [], "rows": [], "configs": [], "values": _Values({}), "total": 9, "rows_out": []}

    def count_of(config, cypher, parameters, *, timeout_s=5):
        box["configs"].append(config)
        box["counts"].append(cypher)
        return {"ok": True, "total": box["total"], "error": None, "elapsed_ms": 1}

    def rows_of(config, cypher, parameters, *, timeout_s=5):
        box["configs"].append(config)
        box["rows"].append(cypher)
        return {"ok": True, "rows": box["rows_out"], "error": None, "elapsed_ms": 1}

    def live_values(config, *, max_cold=8, budget_s=3.0):
        box["configs"].append(config)
        return box["values"]

    monkeypatch.setattr(g2, "count_of", count_of)
    monkeypatch.setattr(g2, "rows_of", rows_of)
    monkeypatch.setattr(g2, "live_values", live_values)
    return box


STATUS = ("MATCH (s:T_QQQ) WHERE s.StatusFoo IS NOT NULL AND toLower(trim(toString(s.StatusFoo))) IN $pos "
          "RETURN s.id AS id")
EXISTS_WORD = (
    "MATCH (d:T_D_ZZZ) WHERE EXISTS { MATCH (d)-[:DERIVED_FROM*1..12]->(t:T_TTT)\n"
    "  WHERE toLower(toString(t.Organ)) CONTAINS 'kidney'\n"
    "    AND (toLower(t.search_text) CONTAINS 'scrap' OR toLower(toString(t.Kind)) CONTAINS 'scrap') }\n"
    "RETURN d.id AS id")


def test_in_list_filters_reads_literal_and_param_lists():
    assert in_list_filters("MATCH (s:T_QQQ) WHERE toLower(trim(toString(s.A))) IN $p RETURN s", {"p": ["X", "y"]}) == [
        ("s", "A", "x"), ("s", "A", "y")]
    assert in_list_filters("MATCH (s) WHERE s.B IN ['u', 'v'] RETURN s", {}) == [("s", "B", "u"), ("s", "B", "v")]


def test_a_whole_value_list_on_a_coded_field_reads_its_stored_values(fakes):
    fakes["values"] = _Values({"T_QQQ.StatusFoo": [("1", 7), ("0", 5)]})
    facts, calls = g2.zero_row_evidence(CFG, STATUS, {"pos": ["positive", "pos"]})
    assert len(facts) == 1
    assert "`StatusFoo`" in facts[0] and "'1'" in facts[0] and "'0'" in facts[0]
    assert "7" not in facts[0] and "5" not in facts[0]
    assert calls[0]["kind"] == "values"


def test_a_contains_filter_on_a_long_list_shows_only_the_values_holding_the_term(fakes):
    vals = [(f"other{i}", 100 - i) for i in range(58)] + [("formalin", 3), ("neutral formal", 2)]
    fakes["values"] = _Values({"T_RRR.Fixative": vals})
    cy = "MATCH (s:T_RRR) WHERE toLower(toString(s.Fixative)) CONTAINS 'formal' RETURN s.id AS id"
    facts, _ = g2.zero_row_evidence(CFG, cy, {})
    assert len(facts) == 1
    assert "'formalin'" in facts[0] and "'neutral formal'" in facts[0] and "other" not in facts[0]


def test_a_free_text_word_that_empties_the_set_is_counted_without_it(fakes):
    facts, calls = g2.zero_row_evidence(CFG, EXISTS_WORD, {})
    assert len(fakes["counts"]) == 1
    counted = fakes["counts"][0]
    assert "kidney" in counted and "scrap" not in counted
    assert counted.count("(") == counted.count(")") and counted.count("{") == counted.count("}")
    assert any("'scrap'" in f and "9" in f for f in facts)
    call = next(c for c in calls if c["kind"] == "without_text")
    assert call["term"] == "scrap" and call["total"] == 9


def test_an_excluded_word_is_never_dropped(fakes):
    cy = ("MATCH (d:T_D_ZZZ) WHERE EXISTS { MATCH (d)-[:DERIVED_FROM*1..12]->(t:T_TTT) "
          "WHERE toLower(toString(t.Organ)) CONTAINS 'kidney' AND NOT toLower(t.search_text) CONTAINS 'scrap' } "
          "RETURN d.id AS id")
    _, calls = g2.zero_row_evidence(CFG, cy, {})
    assert not [c for c in calls if c["kind"] == "without_text"]


def test_a_lone_text_search_is_not_counted_without_its_only_filter(fakes):
    cy = "MATCH (s:T_QQQ) WHERE toLower(s.search_text) CONTAINS 'scrap' RETURN count(*) AS n"
    _, calls = g2.zero_row_evidence(CFG, cy, {})
    assert not [c for c in calls if c["kind"] == "without_text"]


def test_a_field_test_that_holds_on_other_types_is_reported(fakes):
    fakes["rows_out"] = [{"type": "A.XYZ", "n": 2}, {"type": "M.ABC", "n": 1}]
    cy = ("MATCH (s:T_QQQ)-[:IN_STUDY]->(st:Study) WHERE st.title = $t AND "
          "toLower(toString(s.Scientist)) CONTAINS toLower($who) RETURN count(DISTINCT s) AS n")
    facts, calls = g2.zero_row_evidence(CFG, cy, {"t": "S", "who": "Ann"})
    assert any("A.XYZ 2" in f and "`Scientist`" in f for f in facts)
    assert [c["kind"] for c in calls if c["kind"] == "elsewhere"] == ["elsewhere"]


def test_a_search_text_test_is_not_looked_up_elsewhere(fakes):
    cy = ("MATCH (s:T_QQQ)-[:IN_STUDY]->(st:Study) WHERE st.title = $t AND "
          "toLower(s.search_text) CONTAINS 'scrap' RETURN count(DISTINCT s) AS n")
    _, calls = g2.zero_row_evidence(CFG, cy, {"t": "S"})
    assert not [c for c in calls if c["kind"] == "elsewhere"]


def test_reads_stop_at_the_budget_and_nothing_raises(fakes, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(g2, "count_of", boom)
    monkeypatch.setattr(g2, "rows_of", boom)
    monkeypatch.setattr(g2, "live_values", boom)
    ticks = iter([0.0] + [100.0] * 50)
    monkeypatch.setattr(g2, "_clock", lambda: next(ticks))
    facts, calls = g2.zero_row_evidence(CFG, EXISTS_WORD, {})
    assert facts == []
    # and with time left, a raising reader still gives no fact and no exception
    monkeypatch.setattr(g2, "_clock", lambda: 0.0)
    facts, calls = g2.zero_row_evidence(CFG, EXISTS_WORD, {})
    assert facts == [] and all(c["ok"] is False for c in calls)


def test_every_read_goes_through_the_callers_config(fakes):
    fakes["rows_out"] = [{"type": "A.XYZ", "n": 2}]
    fakes["values"] = _Values({"T_TTT.Organ": [("kidney", 3)]})
    g2.zero_row_evidence(CFG, EXISTS_WORD, {})
    g2.zero_row_evidence(CFG, "MATCH (s:T_QQQ) WHERE toLower(toString(s.Scientist)) CONTAINS 'ann' AND s.Kind = 'k' "
                              "RETURN s.id AS id", {})
    assert fakes["configs"] and all(c is CFG for c in fakes["configs"])


def test_a_union_is_left_alone(fakes):
    cy = "MATCH (s:T_QQQ) WHERE s.A = 'x' RETURN s.id AS id UNION MATCH (s:T_RRR) WHERE s.A = 'x' RETURN s.id AS id"
    assert g2.zero_row_evidence(CFG, cy, {}) == ([], [])

"""A complete stored result is read directly: its rows when there are few, a summary when more.

``read_stored_result`` used to return counts and a few example UIDs, never the rows. So a
follow-up that the stored rows could answer ("which labs are those from?" about a complete
40-row result that selected the lab) still had to run a new query, and paid a graph round
trip and a model iteration for data already on disk (loop gap L3).

Now, when the stored copy is complete (the same ``capped`` rule the note uses):

* 50 rows or fewer come back under ``rows``, bounded to about 6,000 characters the way
  ``preview_rows`` bounds a new query's rows, and ``rows_truncated`` says when the bound
  cut them, so a partial list is never read as the whole;
* more rows come back as ``column_summary``: per column, how many distinct values it has
  and its five most common values with their counts, computed over every stored row;
* a capped copy gets neither, so the loop still has to query.

Every agent and tool is stubbed; no model, Neo4j or network call is made.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chat_nextseek import orchestrator as orch
from chat_nextseek.agents.followup import (
    FOLLOWUP_AGENT_KEY,
    FOLLOWUP_ROWS_CHARS,
    FOLLOWUP_ROWS_MAX,
    build_followup_tool_schemas,
    describe_stored_result,
    preview_rows,
    run_followup,
)

STORED_CYPHER = ("MATCH (s:Sample) WHERE s.type = $type "
                 "RETURN s.uuid AS uid, s.lab AS lab, s.type AS type LIMIT 5000")


def _records(n, *, lab=lambda i: ("LAB-A", "LAB-B", "LAB-C")[i % 3]):
    return [{"uid": f"TIS-{i:04d}", "lab": lab(i), "type": "TIS"} for i in range(n)]


def _graph_bundle(rows, *, total=None, truncated=False, bundle_id=9):
    """A graph turn's bundle as the orchestrator writes it. ``total`` defaults to the rows."""
    return {
        "id": bundle_id, "mode": "graph_query", "user_query": "tissue samples",
        "graph_plan": {"cypher": STORED_CYPHER, "parameters": {"type": "TIS"}},
        "graph_result": {"ok": True, "count": len(rows),
                         "total": len(rows) if total is None else total,
                         "truncated": truncated, "data": rows},
    }


def _rest_bundle(rows, *, total):
    return {
        "id": 4, "mode": "new_search", "user_query": "tissue samples",
        "api_plan": {"endpoint": "/nextseek_api/samples/advanced_search/", "requestBody": {"type": "TIS"}},
        "api_result_slim": {"data": {"total": total}},
        "memory_payload": {"data": rows},
    }


def _compact(value):
    return json.dumps(value, separators=(",", ":"), default=str)


# --------------------------------------------------------------------------- #
# 50 rows or fewer: the rows themselves
# --------------------------------------------------------------------------- #

def test_a_small_complete_result_returns_its_rows():
    rows = _records(12)
    described = describe_stored_result(_graph_bundle(rows))

    assert described["capped"] is False
    assert described["rows"] == rows
    assert described["rows_shown"] == 12
    assert described["rows_truncated"] is False
    assert "column_summary" not in described, "the rows say it all"


@pytest.mark.parametrize("n,has_rows", [(1, True), (FOLLOWUP_ROWS_MAX, True),
                                        (FOLLOWUP_ROWS_MAX + 1, False), (400, False)])
def test_fifty_rows_is_the_line_between_rows_and_a_summary(n, has_rows):
    described = describe_stored_result(_graph_bundle(_records(n)))

    assert FOLLOWUP_ROWS_MAX == 50
    assert ("rows" in described) is has_rows
    assert ("column_summary" in described) is (not has_rows)


def test_rows_that_do_not_fit_are_cut_and_the_payload_says_so():
    """R2: the bound is the one a new query's rows get, and a cut list must never pass for
    the whole: rows_truncated and rows_shown say how many of rows_stored are there."""
    rows = [{"uid": f"TIS-{i:04d}", "description": "x" * 900} for i in range(30)]
    described = describe_stored_result(_graph_bundle(rows))
    shown = described["rows"]

    assert 0 < len(shown) < 30
    assert shown == rows[:len(shown)], "the head, in order"
    assert shown == preview_rows(rows), "the same bound as run_new_query's rows"
    assert len(_compact(shown)) <= FOLLOWUP_ROWS_CHARS == 6_000
    assert described["rows_shown"] == len(shown)
    assert described["rows_stored"] == 30
    assert described["rows_truncated"] is True


def test_an_aggregate_s_one_row_is_returned_with_its_values():
    """A count stores one row holding the number; it is complete, so it is shown as is."""
    bundle = {
        "id": 2, "mode": "graph_query", "user_query": "how many mouse samples",
        "graph_plan": {"cypher": "MATCH (s:Sample) WHERE s.type = 'MUS' RETURN count(s) AS n",
                       "parameters": {}},
        "graph_result": {"ok": True, "count": 1, "total": 1, "data": [{"n": 705}]},
    }
    described = describe_stored_result(bundle)

    assert described["aggregate_values"] == {"n": 705}
    assert described["total"] == 705
    assert described["rows"] == [{"n": 705}]


def test_a_result_that_kept_no_rows_returns_neither():
    described = describe_stored_result(_rest_bundle([], total=705))

    assert described["rows_stored"] == 0
    assert "rows" not in described
    assert "column_summary" not in described


# --------------------------------------------------------------------------- #
# More than 50 rows: a per-column summary over every stored row
# --------------------------------------------------------------------------- #

def test_a_larger_complete_result_returns_a_column_summary_over_every_row():
    """LAB-B appears only after row 100, well past any preview: the summary saw every row."""
    rows = _records(160, lab=lambda i: "LAB-A" if i < 100 else "LAB-B")
    described = describe_stored_result(_graph_bundle(rows))
    summary = described["column_summary"]

    assert "rows" not in described, "no rows once there are more than 50"
    assert set(summary) == {"uid", "lab", "type"}
    assert summary["type"] == {"distinct": 1, "top": [["TIS", 160]]}
    assert summary["lab"] == {"distinct": 2, "top": [["LAB-A", 100], ["LAB-B", 60]]}
    assert summary["uid"]["distinct"] == 160
    assert len(summary["uid"]["top"]) == 5
    assert described["columns_omitted"] == 0


def _labs(counts):
    """Rows whose lab values are interleaved, not grouped or sorted, in the given counts."""
    pending = dict(counts)
    rows = []
    while any(pending.values()):
        for lab in list(pending):
            if pending[lab]:
                rows.append({"uid": f"TIS-{len(rows):04d}", "lab": lab})
                pending[lab] -= 1
    return rows


def test_top_lists_five_values_most_common_first_with_ties_ordered_by_value():
    counts = {"LAB-F": 30, "LAB-C": 20, "LAB-E": 20, "LAB-A": 20, "LAB-D": 5, "LAB-B": 5, "LAB-G": 1}
    rows = _labs(counts)
    summary = describe_stored_result(_graph_bundle(rows))["column_summary"]

    assert summary["lab"] == {
        "distinct": 7,
        "top": [["LAB-F", 30], ["LAB-A", 20], ["LAB-C", 20], ["LAB-E", 20], ["LAB-B", 5]],
    }


def test_the_summary_does_not_depend_on_row_order():
    rows = _labs({"LAB-F": 30, "LAB-C": 20, "LAB-E": 20, "LAB-A": 20, "LAB-D": 5, "LAB-B": 5})
    forward = describe_stored_result(_graph_bundle(rows))["column_summary"]
    backward = describe_stored_result(_graph_bundle(list(reversed(rows))))["column_summary"]

    assert _compact(forward["lab"]) == _compact(backward["lab"])
    assert forward["uid"] == backward["uid"], "all-distinct columns tie everywhere, so ordered by value"


def test_lists_and_dicts_are_counted_by_a_stable_serialisation():
    """Key order inside a dict does not make two values different; list order does."""
    rows = []
    for i in range(60):
        rows.append({
            "uid": f"TIS-{i:04d}",
            "meta": {"b": 1, "a": 2} if i % 2 else {"a": 2, "b": 1},
            "tags": ["a", "b"] if i < 40 else ["b", "a"],
        })
    summary = describe_stored_result(_graph_bundle(rows))["column_summary"]

    assert summary["meta"] == {"distinct": 1, "top": [['{"a":2,"b":1}', 60]]}
    assert summary["tags"] == {"distinct": 2, "top": [['["a","b"]', 40], ['["b","a"]', 20]]}


def test_a_column_of_mixed_types_is_counted_without_merging_or_crashing():
    """Neo4j returns null for a missing property and a property can be a number on one node
    and text on another. null counts as a value, so every column accounts for every row, a
    key missing from a row counts as null, and True, 1 and "1" stay three values."""
    values = [None, True, 1, "1", "LAB-A", 2.5]
    rows = [{"uid": f"TIS-{i:04d}", "lab": values[i % 6]} for i in range(60)]
    for row in rows[:10]:
        if row["lab"] is None:
            del row["lab"]
    forward = describe_stored_result(_graph_bundle(rows))["column_summary"]["lab"]
    backward = describe_stored_result(_graph_bundle(list(reversed(rows))))["column_summary"]["lab"]

    assert forward["distinct"] == 6
    assert [count for _, count in forward["top"]] == [10] * 5
    assert [None, 10] in forward["top"], "a missing key counts as null"
    assert _compact(forward) == _compact(backward)


def test_an_integer_too_large_for_a_float_is_still_counted():
    """JSON integers have no size limit; turning one into a float to test it overflows."""
    rows = [{"uid": f"TIS-{i:04d}", "n": 10 ** 400 if i < 40 else 7} for i in range(60)]
    summary = describe_stored_result(_graph_bundle(rows))["column_summary"]
    assert summary["n"] == {"distinct": 2, "top": [[10 ** 400, 40], [7, 20]]}


def test_the_summary_is_bounded_and_says_how_many_columns_it_left_out():
    """R3: a wide result with long values cannot grow the payload without limit. A column
    that does not fit is left out whole rather than cut, and the count of those is given."""
    rows = [{f"col{c:02d}": f"{c:02d}-{i:03d}-" + "v" * 80 for c in range(40)} for i in range(60)]
    described = describe_stored_result(_graph_bundle(rows))
    summary = described["column_summary"]

    assert len(_compact(summary)) <= 6_000
    assert described["columns_omitted"] > 0
    assert len(summary) + described["columns_omitted"] == 40
    for name, entry in summary.items():
        assert entry["distinct"] == 60
        assert all(len(value) == len("00-000-") + 80 for value, _ in entry["top"]), "never clipped"


def test_a_column_too_wide_to_fit_does_not_crowd_out_the_ones_after_it():
    rows = [{"notes": f"{i:03d}" + "n" * 3_000, "lab": ("LAB-A", "LAB-B")[i % 2]} for i in range(60)]
    described = describe_stored_result(_graph_bundle(rows))

    assert "notes" not in described["column_summary"]
    assert described["column_summary"]["lab"] == {"distinct": 2, "top": [["LAB-A", 30], ["LAB-B", 30]]}
    assert described["columns_omitted"] == 1


# --------------------------------------------------------------------------- #
# A capped copy gets neither: complete and capped are the note's rule, not a new one
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("bundle", [
    _graph_bundle(_records(20), total=250),
    _graph_bundle(_records(120), total=5_000),
    _graph_bundle(_records(30), total=30, truncated=True),
    _rest_bundle(_records(20), total=250),
], ids=["graph-few-of-many", "graph-many-of-more", "truncated-flag", "rest"])
def test_a_capped_copy_returns_neither_rows_nor_a_summary(bundle):
    described = describe_stored_result(bundle)

    assert described["capped"] is True
    assert "rows" not in described
    assert "column_summary" not in described


@pytest.mark.parametrize("bundle", [
    _graph_bundle(_records(20)),
    _graph_bundle(_records(20), total=250),
    _graph_bundle(_records(120)),
    _graph_bundle(_records(120), total=5_000),
    _graph_bundle(_records(30), truncated=True),
    _rest_bundle(_records(20), total=20),
    _rest_bundle(_records(20), total=250),
], ids=["small", "small-capped", "large", "large-capped", "truncated", "rest", "rest-capped"])
def test_the_stored_rows_are_shown_exactly_when_the_copy_is_not_capped(bundle):
    """R1: one definition. Whatever the note calls capped shows nothing; the rest shows."""
    described = describe_stored_result(bundle)
    shows = "rows" in described or "column_summary" in described
    assert shows is (not described["capped"])


def test_the_stored_query_fields_are_unchanged_alongside_the_rows():
    """R4: Tasks 13 and 20 read these."""
    described = describe_stored_result(_graph_bundle(_records(12)))

    assert described["stored_query"] == {"cypher": STORED_CYPHER, "parameters": {"type": "TIS"}}
    assert described["stored_query_rebuildable"] is True
    assert described["note"] == "The stored copy holds every row of this result."


# --------------------------------------------------------------------------- #
# The loop serves them, and the model is told what they are for
# --------------------------------------------------------------------------- #

class _ScriptedClient:
    provider = "bedrock"

    def __init__(self, script):
        self.script = list(script)
        self.turns: list[dict] = []

    def chat_with_tools(self, *, messages, tools, system, model, **kwargs):
        self.turns.append({"messages": json.loads(json.dumps(messages, default=str)), "tools": tools})
        return self.script.pop(0)


class _Cfg:
    LOG_DIR = "/tmp"
    _CATALOG_KEY = "default"
    _THINKING_BUDGET_MAP = {None: None}
    AGENT_MODEL_CATALOG: dict = {}
    MODEL_MODE = "test"

    def __init__(self, client):
        self._client = client
        self.LLM_CLIENT = client
        self.LLM_MODEL = "m"
        self.LLM_CLIENTS = {"anth": client}

    def get_agent_model(self, label):
        assert label == FOLLOWUP_AGENT_KEY
        return self._client, "us.anthropic.claude-opus-4-7", None

    def _load_prompt(self, name):
        return "SYSTEM PROMPT"


def _tool_use(name, payload, tid="t1"):
    return {"stop_reason": "tool_use",
            "content": [{"type": "tool_use", "id": tid, "name": name, "input": payload}]}


def _served(bundle):
    """What read_stored_result hands the model, through the real loop."""
    client = _ScriptedClient([
        _tool_use("read_stored_result", {}),
        _tool_use("answer", {"text": "LAB-A, LAB-B and LAB-C.", "caveats": []}),
    ])
    queries = []
    out = run_followup(_Cfg(client), user_text="which labs are those from?", bundle=bundle,
                       run_query=lambda **kw: queries.append(kw) or {})
    assert queries == [], "answered without a new query"
    assert out["tool_calls"] == ["read_stored_result", "answer"]
    return json.loads(client.turns[1]["messages"][-1]["content"][0]["content"])


def test_the_loop_serves_a_small_result_s_rows():
    rows = _records(9)
    assert _served(_graph_bundle(rows))["rows"] == rows


def test_the_loop_serves_a_larger_result_s_summary():
    served = _served(_graph_bundle(_records(90)))
    assert served["column_summary"]["lab"] == {
        "distinct": 3, "top": [["LAB-A", 30], ["LAB-B", 30], ["LAB-C", 30]]}
    assert "rows" not in served


def _read_description():
    tools = {t["name"]: t for t in build_followup_tool_schemas()}
    return " ".join(tools["read_stored_result"]["description"].split())


def test_the_tool_description_says_the_rows_and_summary_answer_without_a_new_query():
    description = _read_description()

    assert "does NOT return the rows" not in description
    assert "rows" in description and "column_summary" in description
    assert "without a new query" in description
    assert "rows_truncated" in description
    assert "neither" in description and "capped" in description, "a capped copy has neither"
    assert "5 most common" in description and "distinct" in description
    assert "—" not in description


def test_the_tool_description_keeps_the_stored_query_fields():
    description = _read_description()
    assert "stored_query" in description and "stored_query_rebuildable" in description


def test_the_prompt_says_a_complete_copy_can_answer_from_its_rows():
    prompt = " ".join((Path(orch.__file__).parent / "prompts" / "followup_agent.txt").read_text().split())
    sentences = [s for s in prompt.split(". ") if "column_summary" in s]

    assert sentences, "the prompt names the summary"
    assert any("without a new query" in s for s in sentences)
    assert any("capped" in s for s in sentences)
    assert all("—" not in s for s in sentences)

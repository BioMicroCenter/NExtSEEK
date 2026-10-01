"""Which rows a memory follow-up actually reads (T9).

A graph turn's only on-disk artifact is its debug JSON, and that file holds a 20-row slice
of the result (``orchestrator._write_graph_debug``). The bundle carries every row inline.
``_load_memory_json_payload`` sorted the file ahead of the bundle, so a follow-up that
reached this loader answered a question about 250 rows from 20 of them, and told the user
there were 20 "rather than 250" -- contradicting the turn before it.

The REST path never had this: it writes its rows whole, and the loader still prefers an API
artifact, which is the richest payload there is.

The primary follow-up path (``agents/followup.py``) reads the bundle already. This is the
fallback it degrades to when there is no tool-capable model, the loop is exhausted, or
anything raises, and that fallback was still wrong.
"""
from __future__ import annotations

from chat_nextseek.agents.memory import _load_memory_json_payload


def _rows(n: int) -> list[dict]:
    return [{"uuid": f"D.SEQ-2209{i:02d}SHA-{i}", "type": "D.SEQ"} for i in range(n)]


def test_the_bundle_rows_beat_the_twenty_row_debug_file(tmp_path):
    debug = tmp_path / "graph_debug_20260802_190937.json"
    debug.write_text('{"neo4j_output": {"count": 250, "data_preview": []}}', encoding="utf-8")
    bundle = {
        "id": 4, "mode": "graph_query",
        "graph_result": {"ok": True, "count": 250, "total": 250, "data": _rows(250)},
        "files": [{"label": "Graph query debug JSON", "path": str(debug)}],
    }

    label, payload, _files = _load_memory_json_payload(bundle)

    assert label == "bundle graph_result rows"
    assert len(payload["data"]["rows"]) == 250, "all of them, not the file's preview"


def test_a_graph_turn_with_no_rows_still_falls_through_to_its_files(tmp_path):
    """A count-only query keeps no rows, so the file is all there is."""
    debug = tmp_path / "graph_debug_20260802_190937.json"
    debug.write_text('{"neo4j_output": {"count": 1}}', encoding="utf-8")
    bundle = {
        "id": 5, "mode": "graph_query",
        "graph_result": {"ok": True, "count": 1, "total": 1, "data": []},
        "files": [{"label": "Graph query debug JSON", "path": str(debug)}],
    }

    label, _payload, _files = _load_memory_json_payload(bundle)

    assert label != "bundle graph_result rows"


def test_an_api_payload_still_wins(tmp_path):
    """The REST path writes its rows whole; nothing about it changes."""
    bundle = {
        "id": 6, "mode": "new_search",
        "memory_payload": {"data": {"rows": [{"uid": "TIS-1"}]}},
        "graph_result": {"data": _rows(3)},
    }

    label, _payload, _files = _load_memory_json_payload(bundle)

    assert label == "bundle memory_payload"

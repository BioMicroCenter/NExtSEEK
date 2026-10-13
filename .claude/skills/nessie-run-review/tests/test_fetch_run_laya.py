"""fetch_run.py carries the laya block and the router's elapsed seconds (JevLevROUTING, SPEC 7).

Both are read off whichever progress entry carries them, never `$[0]`: a laya turn may put another
event first, and a `$[0]` read would show a laya turn as one that never ran laya.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_run.py"


def _load():
    spec = importlib.util.spec_from_file_location("fetch_run_under_test", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_turn_query_reads_laya_and_router_elapsed_by_key_not_by_position():
    remote = _load().REMOTE
    for key in ("laya", "router_elapsed_s", "attempted_route"):
        line = next(l for l in remote.splitlines() if f"'{key}'," in l or f"'{key}'" in l and l.rstrip().endswith(","))
        assert f"progress,'\\$[*].data.{key}'" in line, line
        assert "\\$[0].data." + key not in line


def test_the_pulled_turn_keeps_laya_and_the_elapsed_untouched():
    turns = _load().price_turns([{"laya": {"mode": "shadow", "gate": "pass"}, "router_elapsed_s": 0.31}])
    assert turns[0]["laya"] == {"mode": "shadow", "gate": "pass"} and turns[0]["router_elapsed_s"] == 0.31


def test_the_turn_query_reads_the_parser_decision_off_the_debug_object():
    remote = _load().REMOTE
    line = next(l for l in remote.splitlines() if "'parser_decision'" in l)
    assert "parser_decision" in line and "$[0].parser_decision" in line

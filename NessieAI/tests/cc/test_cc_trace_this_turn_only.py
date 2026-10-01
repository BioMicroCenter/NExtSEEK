"""The trace under a Container-CC reply holds that turn's calls only (CC-RERUN-FINDINGS fix 2).

``--resume`` appends every turn of a chat to one session jsonl, and the trace used to be parsed
from that whole file. So the "Search details" of a resumed turn listed every tool call of every
earlier CC turn too: task r5-637 ran no tool at all in 8.4 s, and its trace showed turn 1's
``nextseek-graph`` call. That is what looked like "it reruns the previous searches". The trace is
now built from the records this turn appended (``captured.turn``), the same slice the transcript
row already stores.

Harness: the fake docker client of ``test_cc_error_turn_transcript`` (the real attach socket and
translator over a scripted stdout, an "agent" appending to a real session jsonl); the DB is real
because ``on_turn_complete`` writes the chat_log and the transcript row.
"""
from __future__ import annotations

import json

import pytest

from NessieAI.cc.turn import _append_cc_turn_complete
from NessieAI.tests.cc.test_cc_error_turn_transcript import (  # noqa: F401 - harness is a fixture
    INIT,
    RUN1,
    RUN2,
    SUCCESS_RESULT,
    _frames_script,
    harness,
)

pytestmark = pytest.mark.django_db


def _records(*records: dict) -> bytes:
    return b"".join((json.dumps(r) + "\n").encode() for r in records)


def _prompt(text: str) -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def _bash(tool_id: str, command: str) -> dict:
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}]}}


def _read(tool_id: str, path: str) -> dict:
    return {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": tool_id, "name": "Read", "input": {"file_path": path}}]}}


def _result(tool_id: str) -> dict:
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": tool_id, "is_error": False, "content": "ok"}]}}


def _say(text: str) -> dict:
    return {"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}


TURN_1 = _records(
    _prompt("Find NHP samples with flow and sequencing data"),
    _bash("t1", "nextseek-graph --query 'NHP samples with flow and sequencing data'"),
    _result("t1"),
    _say("There are 73."),
)
TURN_2 = _records(
    _prompt("Which species are among those?"),
    _read("t2", "/data/previous_turns/MANIFEST.md"),
    _result("t2"),
    _bash("t3", "python3 -c 'import csv' /data/previous_turns/turn-01/samples.csv"),
    _result("t3"),
    _say("47, 22 and 4."),
)


def _trace(events):
    (done,) = [d for e, d in events if e == "query_complete"]
    (trace,) = done["cc_traces"]
    return trace


def test_a_resumed_turns_trace_holds_only_its_own_calls(harness):
    harness.run(script=_frames_script([INIT, SUCCESS_RESULT], append_to=harness.session_path,
                                      appended=TURN_1),
                run_id=RUN1, on_turn_complete=_append_cc_turn_complete)
    first = _trace(harness.events)
    assert [s["detail"] for s in first["steps"] if s["kind"] != "text"] == [
        "nextseek-graph --query 'NHP samples with flow and sequencing data'"]

    harness.events.clear()
    harness.run(script=_frames_script([INIT, SUCCESS_RESULT], append_to=harness.session_path,
                                      appended=TURN_2),
                run_id=RUN2, on_turn_complete=_append_cc_turn_complete)
    second = _trace(harness.events)

    calls = [s["detail"] for s in second["steps"] if s["kind"] != "text"]
    assert calls == ["/data/previous_turns/MANIFEST.md",
                     "python3 -c 'import csv' /data/previous_turns/turn-01/samples.csv"]
    assert not any("nextseek-graph" in (s.get("detail") or "") for s in second["steps"]), (
        "the second turn's trace replays the first turn's graph call")
    assert second["tools_used"] == {"Read": 1, "Bash": 1}
    assert [s["text"] for s in second["steps"] if s["kind"] == "text"] == ["47, 22 and 4."]
    # Counted over this turn's records only: 6 lines, 3 of them user records (the prompt
    # and two tool results).
    assert second["transcript_line_count"] == 6
    assert second["turn_count"] == 3
    # Every step's line is a line of this turn's own transcript row.
    assert [s["line"] for s in second["steps"]] == [2, 4, 6]
    assert all(s["status"] == "ok" for s in second["steps"] if s["kind"] != "text")


def test_a_first_turns_trace_is_unchanged(harness):
    """Turn 1 of a chat: the slice IS the whole file, so nothing moves."""
    harness.run(script=_frames_script([INIT, SUCCESS_RESULT], append_to=harness.session_path,
                                      appended=TURN_1),
                on_turn_complete=_append_cc_turn_complete)
    trace = _trace(harness.events)
    assert trace["transcript_line_count"] == 4
    assert trace["tools_used"] == {"Bash": 1}

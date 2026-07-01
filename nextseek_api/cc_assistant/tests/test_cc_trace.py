"""Hermetic trace extraction from a fixture jsonl. orjson + TypeAdapter."""
from pathlib import Path

from nextseek_api.cc_assistant import cc_summary
from nextseek_api.cc_assistant.cc_trace import extract_trace, CCTrace

FIX = Path(__file__).parent / "fixtures" / "cc_transcript_sample.jsonl"
MULTI = Path(__file__).parent / "fixtures" / "cc_transcript_multitool.jsonl"


def _parsed():
    return cc_summary.parse_transcript(FIX.read_bytes())


def test_envelope_counts_reuse_parsed_transcript():
    p = _parsed()
    t = extract_trace(p, cc_session_id="sess-1", ts="2026-06-30T00:00:00Z",
                      files_created=["report.md"], files_modified=[])
    assert isinstance(t, CCTrace)
    assert t.schema_version == "3/trace-v1"
    assert t.transcript_line_count == p.line_count == 6
    assert t.turn_count == p.turn_count          # user-role record count (reused)


def test_steps_have_granular_kind_line_and_tools_tally():
    t = extract_trace(_parsed(), cc_session_id="s", ts="t",
                      files_created=["report.md"], files_modified=[])
    kinds = [s.kind for s in t.steps]
    assert kinds == ["text", "bash", "write"]
    bash = next(s for s in t.steps if s.kind == "bash")
    write = next(s for s in t.steps if s.kind == "write")
    assert (bash.tool, bash.detail, bash.line) == ("Bash", "ls /data/input", 2)
    assert (write.tool, write.detail, write.line) == ("Write", "/data/scratch/report.md", 4)
    assert t.tools_used == {"Bash": 1, "Write": 1}


def test_action_from_diff_and_status_from_tool_result():
    t = extract_trace(_parsed(), cc_session_id="s", ts="t",
                      files_created=["report.md"], files_modified=[])
    bash = next(s for s in t.steps if s.kind == "bash")
    write = next(s for s in t.steps if s.kind == "write")
    assert write.action == "created"             # report.md is in files_created (basename match)
    assert bash.status == "ok"                    # paired tool_result is_error=false
    assert write.status == "error"                # paired tool_result is_error=true
    # modified-action branch (covers `elif base in modified_base`, else dips <95%):
    t2 = extract_trace(_parsed(), cc_session_id="s", ts="t",
                       files_created=[], files_modified=["report.md"])
    w2 = next(s for s in t2.steps if s.kind == "write")
    assert w2.action == "modified"               # same basename via files_modified


def test_unknown_record_type_does_not_crash():
    t = extract_trace(_parsed(), cc_session_id="s", ts="t",
                      files_created=[], files_modified=[])
    assert isinstance(t, CCTrace)                 # the "summary" line tolerated (_Other)


def test_result_meta_is_surfaced_and_distinct_from_turn_count():
    t = extract_trace(_parsed(), cc_session_id="s", ts="t",
                      files_created=[], files_modified=[],
                      result_meta={"num_turns": 9, "duration_ms": 1234, "cost_usd": 0.07})
    assert t.num_turns == 9 and t.duration_ms == 1234 and t.cost_usd == 0.07
    assert t.num_turns != t.turn_count            # internal turns != user-message records


def test_multitool_trace_kinds():
    p = cc_summary.parse_transcript(MULTI.read_bytes())
    t = extract_trace(p, cc_session_id="s", ts="t", files_created=[], files_modified=[])
    tool_kinds = {s.kind for s in t.steps if s.kind != "text"}
    assert tool_kinds == {"read", "tool"}

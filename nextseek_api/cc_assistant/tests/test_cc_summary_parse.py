"""Hermetic: transcript parsing + actions view. No Django/Docker/network."""
import orjson

from nextseek_api.cc_assistant import cc_summary


def _line(obj) -> bytes:
    return orjson.dumps(obj)


def _transcript(*objs) -> bytes:
    return b"\n".join(_line(o) for o in objs) + b"\n"


def test_line_count_and_turn_count():
    raw = _transcript(
        {"type": "user", "message": {"role": "user", "content": "hello"}},
        {"type": "assistant", "message": {"role": "assistant", "content": "hi"}},
        {"type": "user", "message": {"role": "user", "content": "again"}},
    )
    p = cc_summary.parse_transcript(raw)
    assert p.line_count == 3
    assert p.turn_count == 2


def test_blank_and_malformed_lines_counted_not_crash():
    raw = b'{"type":"user","message":{"role":"user","content":"a"}}\n' \
          b'not json at all\n' \
          b'\n' \
          b'{"type":"assistant","message":{"role":"assistant","content":"b"}}\n'
    p = cc_summary.parse_transcript(raw)
    assert p.line_count == 4
    assert any(r.get("_type") == "unparsed" for r in p.records)


def test_actions_view_numbers_bash_and_writes():
    raw = _transcript(
        {"type": "user", "message": {"role": "user", "content": "do it"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la /data"}}]}},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Write", "input": {"file_path": "/data/scratch/x.py"}}]}},
    )
    view = cc_summary.build_actions_view(cc_summary.parse_transcript(raw), truncate_chars=500)
    assert "L1 user: do it" in view
    assert "L2 bash: ls -la /data" in view
    assert "L3 write: /data/scratch/x.py" in view


def test_actions_view_truncates_tool_result():
    big = "X" * 2000
    raw = _transcript(
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": big}]}},
    )
    view = cc_summary.build_actions_view(cc_summary.parse_transcript(raw), truncate_chars=50)
    assert "X" * 50 in view
    assert "X" * 51 not in view

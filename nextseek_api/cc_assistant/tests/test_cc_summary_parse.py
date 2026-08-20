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


def test_parse_non_object_json_and_more_tool_lines():
    raw = b'[1,2,3]\n{"type":"assistant","message":{"role":"assistant","content":[' \
          b'{"type":"tool_use","name":"Read","input":{"file_path":"/a"}},' \
          b'{"type":"tool_use","name":"Skill","input":{"skill":"x"}},' \
          b'{"type":"tool_use","name":"Other","input":{}}]}}\n'
    p = cc_summary.parse_transcript(raw)
    assert p.records[0].get("_type") == "unparsed"
    view = cc_summary.build_actions_view(p, truncate_chars=80)
    assert "read: /a" in view
    assert "skill:" in view
    assert "tool[Other]" in view
    assert cc_summary._truncate("abc", 0) == "abc"
    assert cc_summary._content_text([{"type": "text", "text": " hi "}]) == "hi"
    assert cc_summary._stringify(["a", {"text": "b"}]) == "a b"
    assert cc_summary._quote_str({"value": "q"}) == "q"
    assert cc_summary._quote_str(type("Q", (), {"value": "v"})()) == "v"
    assert cc_summary.verify_quote(p, 0, 1, "x") is False
    assert cc_summary.verify_quote(p, 1, 1, "") is False

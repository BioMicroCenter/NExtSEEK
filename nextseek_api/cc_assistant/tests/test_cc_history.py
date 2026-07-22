"""cc_history: unified chat_log -> compact conversation block, + CC prompt prefix (#8)."""
from nextseek_api.cc_assistant import cc_history


def _turn(q, *, count=None, uids=None, reply=None):
    t = {"user_query": q}
    if count is not None or uids is not None:
        t["result_summary"] = {"count": count, "first_uids": uids or []}
    if reply is not None:
        t["assistant_reply_preview"] = reply
    return t


def test_empty_or_bad_log_returns_empty():
    assert cc_history.build_conversation_history(None) == ""
    assert cc_history.build_conversation_history([]) == ""
    assert cc_history.build_conversation_history("nope") == ""


def test_formats_count_and_capped_uids():
    log = [_turn("Find me sequencing data associated with non human primates",
                 count=139, uids=["NHP-1", "NHP-2", "NHP-3", "NHP-4"])]
    out = cc_history.build_conversation_history(log)
    assert 'user asked: "Find me sequencing data associated with non human primates"' in out
    assert "139 result(s)" in out
    assert "NHP-1, NHP-2, NHP-3" in out
    assert "NHP-4" not in out  # examples capped at 3


def test_falls_back_to_reply_preview_when_no_count():
    out = cc_history.build_conversation_history([_turn("what can you do", reply="I can search samples")])
    assert "I can search samples" in out


def test_caps_to_last_n_turns():
    log = [_turn(f"q{i}", count=i) for i in range(10)]
    out = cc_history.build_conversation_history(log, max_turns=3)
    assert out.count("user asked") == 3
    assert '"q9"' in out and '"q0"' not in out


def test_cross_route_turns_both_rendered():
    log = [_turn("ns query", count=5), _turn("cc query", reply="did some analysis")]
    out = cc_history.build_conversation_history(log)
    assert '"ns query"' in out and '"cc query"' in out


def test_cc_prompt_with_history():
    assert cc_history.cc_prompt_with_history("hello", "") == "hello"
    out = cc_history.cc_prompt_with_history("counts of those", "- user asked: \"x\"")
    assert out.startswith("counts of those")
    assert "Earlier in this conversation" in out
    assert '- user asked: "x"' in out

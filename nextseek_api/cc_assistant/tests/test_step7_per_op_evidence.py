"""Hermetic tests for Gate 3D per-op forced-CC evidence (amendment 2026-07-05)."""
from __future__ import annotations

import pytest

from nextseek_api.cc_assistant import step7_per_op_evidence as ev


def _bash(line, cmd, status="ok"):
    return {"line": line, "kind": "bash", "detail": cmd, "status": status}


# --- extract_op_invocation ------------------------------------------------

def test_finds_op_invocation_in_bash_step():
    steps = [
        {"line": 1, "kind": "text", "text": "let me run the report"},
        _bash(2, "/app/plugins/nextseek/bin/nextseek-report --mode published --project 'Published Data'"),
    ]
    inv = ev.extract_op_invocation(steps, "nextseek-report")
    assert inv.invoked is True
    assert inv.invocation_line == 2
    assert inv.invocation_status == "ok"


def test_missing_op_is_not_invoked():
    steps = [_bash(1, "ls /data/scratch")]
    inv = ev.extract_op_invocation(steps, "nextseek-report")
    assert inv.invoked is False
    assert inv.invocation_line is None


def test_prefix_op_does_not_match_longer_op():
    # A bash step invoking nextseek-api-write must NOT be read as nextseek-api-read
    # nor should the (nonexistent) 'nextseek-api' prefix match either.
    steps = [_bash(1, "nextseek-api-write --parser-plan '{}'")]
    assert ev.extract_op_invocation(steps, "nextseek-api-write").invoked is True
    assert ev.extract_op_invocation(steps, "nextseek-api-read").invoked is False


def test_api_read_not_matched_by_api_write_step():
    steps = [_bash(1, "nextseek-api-read --parser-plan '{}'")]
    assert ev.extract_op_invocation(steps, "nextseek-api-read").invoked is True
    assert ev.extract_op_invocation(steps, "nextseek-api-write").invoked is False


def test_only_bash_steps_count_as_invocation():
    steps = [{"line": 1, "kind": "read", "detail": "nextseek-report notes"}]
    assert ev.extract_op_invocation(steps, "nextseek-report").invoked is False


def test_command_v_existence_check_is_not_an_invocation():
    # Regression (2026-07-05): the agent ran `command -v nextseek-parse
    # nextseek-api-write; nextseek-parse ...` — the op only appears as an
    # ARGUMENT to `command -v` (existence check), never as the executable.
    steps = [_bash(1, 'command -v nextseek-parse nextseek-api-write; echo ---; nextseek-parse --query "x"')]
    assert ev.extract_op_invocation(steps, "nextseek-api-write").invoked is False
    # nextseek-parse IS actually invoked in the same line.
    assert ev.extract_op_invocation(steps, "nextseek-parse").invoked is True


def test_which_and_type_and_echo_args_are_not_invocations():
    for probe in ("which nextseek-graph", "type nextseek-graph", "echo nextseek-graph"):
        assert ev.extract_op_invocation([_bash(1, probe)], "nextseek-graph").invoked is False


def test_op_as_executable_with_path_prefix_counts():
    steps = [_bash(1, '/app/plugins/nextseek/bin/nextseek-report --mode published --project "Published Data"')]
    assert ev.extract_op_invocation(steps, "nextseek-report").invoked is True


def test_op_after_pipe_or_and_counts():
    steps = [_bash(1, 'cd /tmp && nextseek-graph --query "lineage"')]
    assert ev.extract_op_invocation(steps, "nextseek-graph").invoked is True


def test_unknown_op_raises():
    with pytest.raises(ValueError):
        ev.extract_op_invocation([], "nextseek-bogus")


# --- evaluate_op_row (pass/fail conditions) -------------------------------

def _good_inv(op, status="ok"):
    return ev.OpInvocation(op=op, invoked=True, invocation_line=3,
                           invocation_detail=f"{op} --query x", invocation_status=status)


def _row(**over):
    base = dict(
        op="nextseek-report", cc_run_id="r1", cc_session_id="s1",
        is_error=False, cost_usd=0.12, invocation=_good_inv("nextseek-report"),
        answer_excerpt="here is your report", transport="sidecar",
    )
    base.update(over)
    return ev.evaluate_op_row(**base)


def test_clean_row_has_no_problems():
    assert _row().problems == []


def test_zero_cost_is_a_problem():
    r = _row(cost_usd=0.0)
    assert any("cost_usd" in p for p in r.problems)


def test_error_turn_is_a_problem():
    assert any("is_error" in p for p in _row(is_error=True).problems)


def test_not_invoked_is_review_flag_not_a_failure():
    # Option 1: the EXPECTED op not being invoked is LLM routing (CC resolved the
    # query via a different valid NS-path op), NOT a code bug — a review flag that
    # keeps the E2E green, never a red ``problems`` entry.
    r = _row(invocation=ev.OpInvocation(op="nextseek-report", invoked=False))
    assert r.needs_review is True
    assert any("not invoked" in n for n in r.review_notes)
    assert r.problems == []  # no hard failure on the invocation ground


def test_review_flag_does_not_hide_a_real_failure():
    # A genuine code bug (errored turn) is still RED even when the op wasn't invoked.
    r = _row(is_error=True, invocation=ev.OpInvocation(op="nextseek-report", invoked=False))
    assert any("is_error" in p for p in r.problems)
    assert r.needs_review is True


def test_empty_answer_is_a_problem():
    assert any("answer" in p for p in _row(answer_excerpt="  ").problems)


def test_invocation_error_status_fails_non_write_ops():
    r = _row(op="nextseek-report", invocation=_good_inv("nextseek-report", status="error"))
    assert any("status=error" in p for p in r.problems)


def test_api_write_refusal_status_error_is_allowed():
    # api-write reaching the shim and being refused (exit 5 -> tool_result error)
    # is the pinned non-mutating shape; it must NOT be a problem, but it must
    # still have a positive cost (the decide-and-refuse Bedrock turn).
    r = _row(op="nextseek-api-write", cost_usd=0.09,
             invocation=_good_inv("nextseek-api-write", status="error"))
    assert r.problems == []


def test_api_write_zero_cost_still_fails():
    r = _row(op="nextseek-api-write", cost_usd=0.0,
             invocation=_good_inv("nextseek-api-write", status="error"))
    assert any("cost_usd" in p for p in r.problems)


# --- assert_fresh_sessions ------------------------------------------------

def test_distinct_sessions_pass():
    rows = [_row(op=o, cc_run_id=f"r{i}", cc_session_id=f"s{i}",
                 invocation=_good_inv(o))
            for i, o in enumerate(("nextseek-report", "nextseek-graph"))]
    assert ev.assert_fresh_sessions(rows) == []


def test_shared_session_is_a_violation():
    rows = [
        _row(op="nextseek-report", cc_run_id="r1", cc_session_id="dup",
             invocation=_good_inv("nextseek-report")),
        _row(op="nextseek-graph", cc_run_id="r2", cc_session_id="dup",
             invocation=_good_inv("nextseek-graph")),
    ]
    v = ev.assert_fresh_sessions(rows)
    assert v and "cc_session_id" in v[0]


def test_shared_run_id_is_a_violation():
    rows = [
        _row(op="nextseek-report", cc_run_id="dup", cc_session_id="s1",
             invocation=_good_inv("nextseek-report")),
        _row(op="nextseek-graph", cc_run_id="dup", cc_session_id="s2",
             invocation=_good_inv("nextseek-graph")),
    ]
    v = ev.assert_fresh_sessions(rows)
    assert v and "cc_run_id" in v[0]


def test_bin_ops_are_the_eight_decomposed_ops():
    assert len(ev.BIN_OPS) == 8
    assert "nextseek-api-write" in ev.BIN_OPS
    assert "nextseek-query" not in ev.BIN_OPS  # disabled per-op amendment


# --- estimate_cost_from_transcript (timeout cost recovery) ----------------

def _tx(*frames) -> bytes:
    import json
    return ("\n".join(json.dumps(f) for f in frames)).encode("utf-8")


def test_estimate_sums_usage_at_opus48_rates():
    # message.usage nested shape (real CC transcript), two assistant frames.
    raw = _tx(
        {"type": "assistant", "message": {"usage": {
            "input_tokens": 1000, "output_tokens": 500,
            "cache_creation_input_tokens": 2000, "cache_read_input_tokens": 400000}}},
        {"type": "assistant", "message": {"usage": {
            "input_tokens": 0, "output_tokens": 100,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}},
    )
    # 1000*5e-6 + 600*25e-6 + 2000*6.25e-6 + 400000*0.5e-6
    #   = 0.005 + 0.015 + 0.0125 + 0.20 = 0.2325
    assert ev.estimate_cost_from_transcript(raw) == pytest.approx(0.2325, abs=1e-6)


def test_estimate_reads_top_level_usage_too():
    raw = _tx({"usage": {"input_tokens": 1_000_000}})  # $5.00 at input rate
    assert ev.estimate_cost_from_transcript(raw) == pytest.approx(5.0, abs=1e-6)


def test_estimate_empty_or_no_usage_is_zero():
    assert ev.estimate_cost_from_transcript(b"") == 0.0
    assert ev.estimate_cost_from_transcript(b'not json\n{"type":"text"}\n') == 0.0


def test_estimate_skips_unparsable_lines_but_keeps_valid_usage():
    raw = b'garbage\n' + _tx({"message": {"usage": {"output_tokens": 1_000_000}}})
    assert ev.estimate_cost_from_transcript(raw) == pytest.approx(25.0, abs=1e-6)  # $25/1M out


def test_cost_source_defaults_to_result_frame_and_is_emitted():
    r = _row()
    assert r.cost_source == "claude_code_result"
    assert r.to_dict()["cost_source"] == "claude_code_result"


def test_cost_source_estimate_is_propagated_and_emitted():
    r = _row(cost_source="usage_estimate_on_timeout")
    assert r.to_dict()["cost_source"] == "usage_estimate_on_timeout"


# --- R6: positive-evidence + backend-error pattern net (folded in 2026-07-08) ---

def test_backend_unreachable_reply_is_a_hard_failure():
    r = _row(answer_excerpt="I was unable to reach the NExtSEEK backend (Connection refused).")
    assert r.problems


def test_http_5xx_reply_is_a_hard_failure():
    r = _row(answer_excerpt="The API returned a 502 Bad Gateway and I could not complete the request.")
    assert r.problems


def test_normal_answer_is_not_flagged():
    r = _row(answer_excerpt="There are 42 samples of type M.Mice in study S-Demo.")
    assert r.problems == []


def test_invoked_non_ok_status_fails_positive_evidence():
    r = _row(op="nextseek-report", invocation=_good_inv("nextseek-report", status=None))
    assert any("not 'ok'" in p or "positive evidence" in p for p in r.problems)


def test_answer_with_incidental_number_is_not_a_backend_error():
    r = _row(answer_excerpt="Found 502 samples of M.Mice across 3 studies.")
    assert r.problems == []


def test_diverse_backend_error_phrasings_all_fail():
    for phrasing in (
        "The service is unavailable right now.",
        "I couldn't reach the NExtSEEK API (read timed out).",
        "The backend did not respond.",
        "Received an internal server error from the API.",
    ):
        assert _row(answer_excerpt=phrasing).problems, phrasing


def test_real_transcript_success_propagates_ok_and_passes():
    steps = [_bash(1, "/app/plugins/nextseek/bin/nextseek-report --mode published", status="ok")]
    inv = ev.extract_op_invocation(steps, "nextseek-report")
    assert inv.invocation_status == "ok"
    row = ev.evaluate_op_row(
        op="nextseek-report", cc_run_id="r", cc_session_id="s", is_error=False,
        cost_usd=0.10, invocation=inv, answer_excerpt="here is your report",
        transport="sidecar",
    )
    assert row.problems == []

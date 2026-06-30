"""Hermetic: evidence verification + fingerprint/change-detection."""
import pytest

from nextseek_api.cc_assistant import cc_summary

types = pytest.importorskip("dmac_assistant.router.baml_client.types")


def _parsed(*text_lines):
    raw = ("\n".join(text_lines) + "\n").encode("utf-8")
    return cc_summary.parse_transcript(raw), raw


def test_verify_quote_present_in_range():
    parsed, _ = _parsed('{"type":"user","message":{"content":"build BANANA-42"}}',
                        '{"type":"assistant","message":{"content":"ok"}}')
    assert cc_summary.verify_quote(parsed, 1, 1, "BANANA-42") is True


def test_verify_quote_absent_returns_false():
    parsed, _ = _parsed('{"type":"user","message":{"content":"hello"}}')
    assert cc_summary.verify_quote(parsed, 1, 1, "GOODBYE") is False


def test_verify_quote_spanning_two_lines():
    parsed, _ = _parsed("alpha", "bravo")
    assert cc_summary.verify_quote(parsed, 1, 2, "alpha\nbravo") is True


def test_verify_quote_out_of_range_false():
    parsed, _ = _parsed("only one")
    assert cc_summary.verify_quote(parsed, 5, 9, "x") is False
    assert cc_summary.verify_quote(parsed, 1, 1, "") is False


def test_fingerprint_and_change_detection():
    raw1 = b'{"a":1}\n'
    fp1 = cc_summary.fingerprint(raw1)
    assert fp1["line_count"] == 1 and len(fp1["hash"]) == 64
    assert cc_summary.is_changed(None, fp1) is True
    assert cc_summary.is_changed(fp1, fp1) is False
    fp2 = cc_summary.fingerprint(raw1 + b'{"b":2}\n')
    assert cc_summary.is_changed(fp1, fp2) is True


def test_apply_grounding_sets_verified_flags_keeps_unverified():
    parsed, _ = _parsed('{"type":"user","message":{"content":"token ZEBRA-9 here"}}')
    good = types.EvidenceRef(
        line_start=1, line_end=1,
        quote=types.Checked(value="ZEBRA-9", checks={}),
        verified=False,
    )
    bad = types.EvidenceRef(
        line_start=1, line_end=1,
        quote=types.Checked(value="NOPE", checks={}),
        verified=False,
    )
    item = types.MemoryItem(category=types.MemoryCategory.Fact, statement="s",
                            evidence=[good, bad], confidence=types.Confidence.Low)
    summ = types.SessionSummary(
        chat_session_id="S1", claude_session_id=None, transcript_path="/t.jsonl",
        transcript_line_count=1, turn_count=1, chat_model="m", gist="g", items=[item],
        writer="baml_gemini", summary_model="gemini-flash", schema_version="1c/v1",
        generated_at="2026-06-29T00:00:00Z")
    out = cc_summary.apply_grounding(summ, parsed)
    ev = out.items[0].evidence
    assert ev[0].verified is True and ev[1].verified is False
    assert len(ev) == 2

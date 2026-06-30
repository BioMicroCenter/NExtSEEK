"""Hermetic: deterministic markdown renderer + pointer block + fresh_session."""
from nextseek_api.cc_assistant import cc_memory


def _summary(gist, items):
    return {"gist": gist, "items": items}


def _meta(sid, ts, gist, items):
    return cc_memory.SessionMeta(session_id=sid, updated_at=ts, fingerprint=None,
                                 summary=_summary(gist, items),
                                 transcript_path=f"/h/{sid}.jsonl", changed=False)


def _item(cat, stmt, conf, verified=True):
    return {"category": cat, "statement": stmt, "confidence": conf,
            "evidence": [{"line_start": 1, "line_end": 1, "quote": "q",
                          "verified": verified}]}


def test_fresh_session_renders_empty():
    win = [_meta("A", 10, "did A", [_item("decision", "chose X", "high")])]
    assert cc_memory.render_memory(win, fresh_session=True,
                                   transcripts_mount="/home/user/.cc-memory/transcripts") == ""


def test_empty_window_renders_empty():
    assert cc_memory.render_memory([], fresh_session=False,
                                   transcripts_mount="/m") == ""


def test_groups_by_category_and_lists_pointer_block():
    win = [
        _meta("A", 20, "worked on plots", [
            _item("decision", "use seaborn", "high"),
            _item("todo", "add legend", "medium")]),
        _meta("B", 10, "data cleaning", [
            _item("artifact", "wrote clean.py", "high")]),
    ]
    md = cc_memory.render_memory(win, fresh_session=False,
                                 transcripts_mount="/home/user/.cc-memory/transcripts")
    assert "Decision" in md and "Todo" in md and "Artifact" in md
    assert "use seaborn" in md and "wrote clean.py" in md
    assert "/home/user/.cc-memory/transcripts/A.jsonl" in md
    assert "/home/user/.cc-memory/transcripts/B.jsonl" in md
    assert "worked on plots" in md and "data cleaning" in md


def test_unverified_items_are_flagged():
    win = [_meta("A", 10, "g", [_item("fact", "maybe true", "low", verified=False)])]
    md = cc_memory.render_memory(win, fresh_session=False, transcripts_mount="/m")
    assert "unverified" in md.lower()


def test_render_is_deterministic():
    win = [_meta("A", 10, "g", [_item("decision", "d", "high")])]
    a = cc_memory.render_memory(win, fresh_session=False, transcripts_mount="/m")
    b = cc_memory.render_memory(win, fresh_session=False, transcripts_mount="/m")
    assert a == b

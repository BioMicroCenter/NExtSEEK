"""Widened criteria from the 2026-10-21 dev run: right replies that the old regexes refused."""
import json
import re
from pathlib import Path

CORPUS = json.loads((Path(__file__).resolve().parents[1] / "corpus.json").read_text(encoding="utf-8"))


def _cases():
    out = {}
    stack = [CORPUS["families"]]
    while stack:
        o = stack.pop()
        if isinstance(o, dict):
            if "id" in o and "turns" in o:
                out[o["id"]] = o
            stack.extend(o.values())
        elif isinstance(o, list):
            stack.extend(o)
    return out


def _regexes(case_id):
    return [c["value"] for t in _cases()[case_id]["turns"] for c in t["pass_criteria"] if c["op"] == "matches_re"]


def test_fresh_session_accepts_plain_denials():
    first = _regexes("memory.fresh_session_has_no_history")[0]
    for reply in (
        "I don't remember anything from your earlier chats.",
        "I do not remember earlier chats.",
        "Each turn starts fresh.",
        "I have no saved notes of past sessions.",
    ):
        assert re.search(first, reply), reply
    assert not re.search(first, "In our last session you asked about samples.")


def test_supplied_mapping_accepts_nothing_has_been_uploaded():
    rx = _regexes("batch.apply_a_supplied_mapping")[1]
    for reply in ("Nothing has been uploaded.", "nothing was uploaded", "Nothing can be uploaded from this chat."):
        assert re.search(rx, reply), reply
    assert not re.search(rx, "I uploaded the sheet.")

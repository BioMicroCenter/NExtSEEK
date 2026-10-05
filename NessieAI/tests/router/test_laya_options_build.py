"""JevLevROUTING U3: build_options.py (SPEC s3, test 6)."""
import hashlib
import importlib.util
import json
import pathlib

import pytest

from NessieAI.router import laya_common

REPO = pathlib.Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("laya_build_options", REPO / "scripts/laya/build_options.py")
bo = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bo)


def words(text):
    return len(text.split())


def _tree(root, cc_desc="CC desc.", counter_rule=True):
    d = root / "NessieAI/dmac_assistant"
    (d / "baml_src").mkdir(parents=True)
    (d / "build_context").mkdir(parents=True)
    caps = {"routes": [
        {"route_name": "nextseek_query", "description": "NS one. NS two.", "best_for": "NS best. More.",
         "not_for": "Not intended for: A thing.; Another thing.; Third."},
        {"route_name": "container_cc", "description": cc_desc + " Tail.", "best_for": "CC best.",
         "not_for": "CC not. More."}]}
    (d / "build_context/route_capabilities.json").write_text(json.dumps(caps))
    (d / "baml_src/router.baml").write_text(
        "head\n\n    If nothing matches, trivia,\n    gossip - select `unrelated`. Do NOT route.\n\n"
        "    A question is NOT `unrelated` merely because X. It is a catalog.\n\ntail\n")
    return root


def test_first_sentence_handles_list_separators_and_abbrev_free_text():
    assert bo.first_sentence("A b.  C d.") == "A b."
    assert bo.first_sentence("Not intended for: One.; Two.; Three.") == "Not intended for: One."
    assert bo.first_sentence("no stop") == "no stop"
    assert bo.first_sentence("Use file I/O, e.g. code. Next.") == "Use file I/O, e.g. code."


def test_build_takes_first_sentences_in_fixed_order(tmp_path):
    out = bo.build(_tree(tmp_path), count=lambda t: 0)
    assert [o["key"] for o in out["options"]] == ["nextseek_query", "container_cc", "unrelated"]
    texts = {o["key"]: o["text"] for o in out["options"]}
    assert texts["nextseek_query"] == "NS one. NS best. Not intended for: A thing."
    assert texts["container_cc"] == "CC desc. CC best. CC not."
    assert texts["unrelated"] == ("If nothing matches, trivia, gossip - select `unrelated`. "
                                  "A question is NOT `unrelated` merely because X.")
    assert out["question_id"] == "route" and out["prompt"].startswith("Which engine")
    assert out["options_hash"] == laya_common.options_hash(out)
    assert set(out["source_hashes"]) == set(bo.SOURCES)


def test_over_budget_drops_not_for_then_best_for_from_the_end(tmp_path, monkeypatch):
    root = _tree(tmp_path)
    monkeypatch.setattr(bo, "HEAD_BUDGET", 22)
    # counted in words so the numbers are hand-checkable: 31 in full, 24 without not_for, 22 without cc best_for
    full = sum(words(o["text"]) for o in bo.build(root, count=lambda t: 0)["options"])
    assert full == 31
    out = bo.build(root, count=words)
    t = {o["key"]: o["text"] for o in out["options"]}
    assert t["container_cc"] == "CC desc."                 # its not_for and best_for went
    assert t["nextseek_query"] == "NS one. NS best."       # not_for gone, best_for kept
    assert sum(words(x) for x in t.values()) <= 22


def test_fails_rather_than_cutting_a_description(tmp_path, monkeypatch):
    monkeypatch.setattr(bo, "HEAD_BUDGET", 5)
    with pytest.raises(SystemExit, match="not cutting a description"):
        bo.build(_tree(tmp_path), count=words)


def test_checked_in_options_equal_a_rebuild():
    live = json.loads((REPO / bo.OUT).read_text(encoding="utf-8"))
    assert bo.build(REPO) == live                          # drift test: rebuild equals the checked-in file
    assert (REPO / bo.OUT).read_text(encoding="utf-8") == bo.dumps(live)
    assert bo.main(["--check"]) == 0


def test_source_hashes_match_the_current_files():
    live = json.loads((REPO / bo.OUT).read_text(encoding="utf-8"))
    for rel, h in live["source_hashes"].items():
        assert hashlib.sha256((REPO / rel).read_bytes()).hexdigest() == h
    assert live["options_hash"] == laya_common.options_hash(live)
    assert len(live["options"]) == 3

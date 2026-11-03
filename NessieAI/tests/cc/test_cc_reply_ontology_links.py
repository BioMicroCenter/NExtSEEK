"""A house-ontology link in a Container-CC reply points at that entry's real url,
whatever the agent printed. The engine fixes it before the reply is stored or sent."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from NessieAI.cc import cc_engine

REPO = Path(__file__).resolve().parents[3]
REAL = REPO / "NessieAI/chat_nextseek/src/chat_nextseek/context/ONTOLOGIES.json"
INDEX = [
    {"title": "Thing One", "file": "ontology_one.json", "url": "https://real.example/ontology_one.json"},
    {"title": "Thing Two", "file": "ontology_two.json", "url": "https://real.example/ontology_two.json"},
]
fix = lambda t: cc_engine.fix_ontology_links(t, INDEX)  # noqa: E731


def test_invented_host_is_fixed_by_title():
    assert fix("Mapped with [Thing One](https://invented.example/x).") == \
        "Mapped with [Thing One](https://real.example/ontology_one.json)."


def test_wrong_path_is_fixed_by_file_name_and_title_is_case_and_space_folded():
    assert fix("[the list](https://github.com/o/r/ontology_two.json)") == \
        "[the list](https://real.example/ontology_two.json)"
    assert fix("[ thing   ONE ](/nope)") == "[ thing   ONE ](https://real.example/ontology_one.json)"


def test_real_link_and_other_links_are_untouched():
    text = ("[Thing One](https://real.example/ontology_one.json) "
            "[tree](/dmac/users/p/u/output/tree.html) [NCBI](https://ncbi.example/x)")
    assert fix(text) == text


def test_no_link_and_non_string_unchanged():
    assert fix("plain Thing One text") == "plain Thing One text"
    assert fix(None) is None


def test_two_ontologies_in_one_reply():
    out = fix("[Thing One](http://a) and [Thing Two](http://b)")
    assert out == ("[Thing One](https://real.example/ontology_one.json) and "
                   "[Thing Two](https://real.example/ontology_two.json)")


def test_every_real_entry_title_maps_to_its_url():
    entries = json.loads(REAL.read_text(encoding="utf-8"))["ontologies"]
    assert entries
    for e in entries:
        assert cc_engine.fix_ontology_links(f"[{e['title']}](https://bad.example/z)") == \
            f"[{e['title']}]({e['url']})"


def test_an_unreadable_index_leaves_links_and_logs_a_warning(tmp_path, monkeypatch, caplog):
    missing = tmp_path / "ONTOLOGIES.json"
    monkeypatch.setattr(cc_engine, "_ONTOLOGIES_JSON", missing)
    with caplog.at_level(logging.WARNING, logger=cc_engine.logger.name):
        assert cc_engine.fix_ontology_links("[Thing One](http://a)") == "[Thing One](http://a)"
    [record] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert str(missing) in record.getMessage()


# --- the link line is written when a harmonization table names an ontology but omits the link ---
# Fixture: dev 11-02 task 323003d3 (batch.apply_a_supplied_mapping); its cells held
# "Mouse genotypes (house list)": the title plus a suffix, no term id.
_GENO = {"title": "Mouse genotypes", "file": "ontology_mouse_genotype.json",
         "url": "https://real.example/ontology_mouse_genotype.json"}
_STORED = (
    "I haven't built the update sheet yet. 169 mouse samples match your key.\n\n"
    "| Before | Samples | After | Ontology |\n|---|---|---|---|\n"
    "| RGA | 91 | RaDR+/+; GPT+/+; Aag -/- | Mouse genotypes (house list) |\n"
    "| RGATG | 78 | RaDR+/+; GPT+/+; AagTg -/+ | Mouse genotypes (house list) |\n\n"
    "- **Left unchanged:** 23 mice.\n"
)
LINE = "Matched against [Mouse genotypes](https://real.example/ontology_mouse_genotype.json)."


def test_link_line_inserted_above_table_from_stored_reply():
    out = cc_engine.fix_ontology_links(_STORED, INDEX + [_GENO])
    assert out.count(LINE) == 1
    assert out.index(LINE) < out.index("| Before |")
    assert out.replace(LINE + "\n\n", "") == _STORED  # nothing else changed


def test_link_line_not_added_when_link_present_or_no_column_or_unknown_title():
    idx = INDEX + [_GENO]
    linked = _STORED.replace("169 mouse", "[Mouse genotypes](https://real.example/ontology_mouse_genotype.json) 169 mouse")
    assert cc_engine.fix_ontology_links(linked, idx) == linked
    no_col = _STORED.replace("Ontology", "Source")
    assert cc_engine.fix_ontology_links(no_col, idx) == no_col
    assert cc_engine.fix_ontology_links(_STORED, INDEX) == _STORED


def test_link_line_is_idempotent():
    once = cc_engine.fix_ontology_links(_STORED, INDEX + [_GENO])
    assert cc_engine.fix_ontology_links(once, INDEX + [_GENO]) == once

"""The entity step is told the catalog's Tags are the alias list (F15).

`mixed.rnaseq_files_from_mice` failed its entity criterion on all three prompt variants: the
answer and the Cypher were right, but "mouse" stayed a free keyword instead of resolving to the
Mouse code. It also fed a false caveat, because a keyword the query never wrote as a word looked
like a dropped constraint.

The aliases were never missing. Every row of the sample-type catalog carries `Tags`, the Mouse
row's include "mice", "murine", "collaborative cross" and "CC", and that catalog is what the
entity agent is handed (`config.MIN_SAMPLETYPES`, exported as min_sampletypes_db.json). The
prompt simply told it to search Name and Description, so the one field holding the common names
was the one field it was not told to read.
"""
from __future__ import annotations

import json
from pathlib import Path

NESSIE = Path(__file__).resolve().parents[2]
PACKAGE = NESSIE / "chat_nextseek" / "src" / "chat_nextseek"
PROMPT = (PACKAGE / "prompts" / "entity_agent.txt").read_text(encoding="utf-8")
CATALOG = json.loads((PACKAGE / "context" / "min_sampletypes_db.json").read_text(encoding="utf-8"))
SOURCE = json.loads((NESSIE.parent / "context" / "sample_types.json").read_text(encoding="utf-8"))


def _tags(rows: list, code: str, key: str) -> str:
    row = next((r for r in rows if r.get(key) == code), None)
    return str((row or {}).get("Tags") or "").lower()


def test_the_prompt_tells_the_agent_to_search_tags():
    flat = " ".join(PROMPT.split())
    assert "Name, Description AND `Tags`" in flat
    assert "`Tags` is the alias list" in flat
    assert "Read Tags before deciding a word is a keyword rather than a type" in flat


def test_the_prompt_does_not_present_its_examples_as_the_whole_list():
    flat = " ".join(PROMPT.split())
    assert "These examples are not the list: the catalog's `Tags` are" in flat
    assert "A term found in Tags is a specific catalog concept, not a generic word" in flat


def test_the_catalog_the_agent_reads_actually_carries_tags():
    """If the export ever drops Tags, the prompt above is pointing at nothing."""
    assert CATALOG, "the entity agent's sample-type catalog is not empty"
    with_tags = [r for r in CATALOG if str(r.get("Tags") or "").strip()]
    assert len(with_tags) == len(CATALOG), "every row carries Tags"


def test_the_terms_the_failing_questions_used_are_in_the_source():
    """The words researchers actually typed, against the row they should resolve to.

    Read from the hand-owned source: that is where the curation happens.
    """
    tags = _tags(SOURCE, "MUS", "sample_type")
    for term in ("mouse", "mice", "murine", "collaborative cross", "cc"):
        assert term in tags, term


def test_the_export_carries_the_same_tags_as_the_source():
    """Was a strict xfail: the curated tags stopped at the database and never reached here.

    The chain is context/sample_types.json -> scripts/context_gen.py -> dmac.sample_types_context
    in MySQL -> the daily export -> min_sampletypes_db.json, and the write had not been run since
    the Mouse row gained "mice", "collaborative cross" and "CC". `--emit exports` now writes this
    file from the same curated source the database write is generated from, so the two cannot
    drift apart without a failing test here and in test_context_exports.py.
    """
    missing = {}
    for src in SOURCE:
        code = src.get("sample_type")
        src_tags = {t.strip().lower() for t in str(src.get("Tags") or "").split(",") if t.strip()}
        out_tags = {t.strip().lower() for t in _tags(CATALOG, code, "SampleType").split(",") if t.strip()}
        if code and out_tags and src_tags - out_tags:
            missing[code] = sorted(src_tags - out_tags)
    assert not missing, f"tags curated but not exported: {missing}"

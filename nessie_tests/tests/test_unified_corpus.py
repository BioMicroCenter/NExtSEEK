"""The unified corpus must resolve to EXACTLY what the three-file corpus resolved to.

This is the whole safety property of the migration. `merged()` is the only thing
any caller sees, so if the resolved list is identical variant-for-variant then no
run, report or assertion can tell the difference. Everything else in the migration
is bookkeeping.
"""
import json
import pathlib

from nessie_tests import corpus
from nessie_tests.scripts import build_corpus

ROOT = pathlib.Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "overlay.json"
RETIRED = ROOT / "retired.json"
UNIFIED = ROOT / "corpus.json"


def test_corpus_json_exists_and_parses():
    assert UNIFIED.is_file(), "run: python -m nessie_tests.scripts.build_corpus"
    json.loads(UNIFIED.read_text(encoding="utf-8"))


def test_unified_holds_every_definition_from_all_three_sources():
    """383 defined variants: 366 base + 17 overlay-only. Retired ones are KEPT."""
    payload = json.loads(UNIFIED.read_text(encoding="utf-8"))
    ids = {v["id"] for fam in payload["families"].values() for v in fam["variants"]}

    base_ids = {v.id for v in corpus.load_base()}
    overlay_ids = {v.id for v in corpus.load_overlay(OVERLAY)}
    assert ids == base_ids | overlay_ids
    assert len(ids) == 383


def test_every_id_is_defined_exactly_once():
    """The dual-definition trap the three-file layout allowed (15 ids lived in both
    overlay.json and retired.json) is structurally impossible here. Editing the
    wrong copy silently had no effect; there is now only one copy."""
    payload = json.loads(UNIFIED.read_text(encoding="utf-8"))
    ids = [v["id"] for fam in payload["families"].values() for v in fam["variants"]]
    assert len(ids) == len(set(ids)), \
        f"duplicated ids: {sorted({i for i in ids if ids.count(i) > 1})}"


def test_retired_ids_carry_their_full_retirement_record():
    payload = json.loads(UNIFIED.read_text(encoding="utf-8"))
    retired = [v for fam in payload["families"].values()
               for v in fam["variants"] if v["status"] == "retired"]
    assert len(retired) == 100
    for v in retired:
        rec = v["retirement"]
        assert rec and rec["reason"] and rec["retired_on"] and rec["decided_by"], v["id"]


def test_the_four_policy_blocks_survive_verbatim():
    """They stay BLOCKS rather than being baked into variants, because
    test_floor_ops and test_inline_route_assertions both depend on telling what the
    author wrote from what the floor added."""
    unified = json.loads(UNIFIED.read_text(encoding="utf-8"))
    overlay = json.loads(OVERLAY.read_text(encoding="utf-8"))
    for block in ("criterion_rewrites", "route_policy", "family_floor", "consistency_groups"):
        assert unified[block] == overlay[block], block


def test_rebuilding_is_deterministic():
    """The script must be re-runnable and byte-stable.

    Compared as SERIALISED JSON, not as dicts: `==` on dicts ignores key order, so
    a generator that emitted family blocks in a different order every run would
    pass a dict comparison while producing a file that diffs against itself. The
    docstring in build_corpus.py claims the output is "stable enough to diff", and
    that is the property this pins.
    """
    once = build_corpus.build(corpus._BASE_CATALOG, OVERLAY, RETIRED)
    twice = build_corpus.build(corpus._BASE_CATALOG, OVERLAY, RETIRED)
    assert json.dumps(once) == json.dumps(twice)


def test_the_committed_file_matches_what_the_generator_produces():
    """Pins the ARTIFACT to its generator, which nothing else in this task does.

    Without it a hand-edited or stale corpus.json passes every other test here as
    long as the id set, the retirement count and the four policy blocks survive --
    queries, criteria and turn bodies are unchecked until Task 2's content gate.
    """
    built = build_corpus.build(corpus._BASE_CATALOG, OVERLAY, RETIRED)
    assert built == json.loads(UNIFIED.read_text(encoding="utf-8")), (
        "corpus.json is out of step with build_corpus.py: regenerate with "
        "`python -m nessie_tests.scripts.build_corpus`, or explain the drift.")

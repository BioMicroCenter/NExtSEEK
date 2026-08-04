"""corpus.json is the corpus, and has to be self-consistent on its own terms.

Until 2026-08-04 this file's job was to prove the unified corpus resolved to
EXACTLY what the three-file corpus resolved to. It did, variant-for-variant, and
Task 3 then switched `merged()` over. The comparison tests went with the switch:
they compared against sources nothing reads any more, and the generator pin went
too because from Task 4 `corpus.json` is HAND-OWNED, so `build(...) == corpus.json`
would fail on correct work. What survives is everything that reads only the
unified file: the counts, the duplicate-id guard, the retirement records, and the
guarantee that a retired definition is still loadable.
"""
import collections
import json
import pathlib

import pytest

from nessie_tests import corpus

ROOT = pathlib.Path(__file__).resolve().parents[1]
UNIFIED = ROOT / "corpus.json"
CORPUS = UNIFIED


def test_corpus_json_exists_and_parses():
    assert UNIFIED.is_file(), "run: python -m nessie_tests.scripts.build_corpus"
    json.loads(UNIFIED.read_text(encoding="utf-8"))


def test_unified_holds_every_definition():
    """383 = 366 adopted from catalog.json + 17 that only ever existed in the
    overlay. Retired ones are KEPT, which is why this is 383 and not 283."""
    payload = json.loads(UNIFIED.read_text(encoding="utf-8"))
    ids = {v["id"] for fam in payload["families"].values() for v in fam["variants"]}
    assert len(ids) == 383


def test_every_retired_definition_is_still_loadable():
    """Retirement is not deletion: the definition is kept so reinstating is a data
    edit, not a code change. After Task 3 nothing else compares those 100 bodies
    against anything, so this is what stops one rotting unnoticed until the day
    someone reinstates it."""
    retired = [v for v in corpus.load_all_definitions(CORPUS)
               if corpus.variant_meta(CORPUS)[v.id]["status"] == "retired"]
    assert len(retired) == 100
    for v in retired:
        assert v.turns, f"{v.id} has no turns"
        assert all(t.query for t in v.turns), f"{v.id} has an empty query"


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


def test_load_unified_returns_the_active_variants_only():
    active = corpus.load_unified(UNIFIED)
    assert len(active) == 283


def test_load_all_definitions_returns_active_plus_retired():
    assert len(corpus.load_all_definitions(UNIFIED)) == 383


def test_unified_resolution_preserves_turn_count():
    assert sum(len(v.turns) for v in corpus.merged_from_unified(UNIFIED)) == 314


@pytest.mark.parametrize("legacy", ["overlay.json", "retired.json"])
def test_a_superseded_file_is_refused_rather_than_resolving_to_nothing(legacy):
    """The failure mode that made this worth a guard is SILENCE.

    Both superseded files still have a `families` block and stay on disk by
    operator ruling, but no variant in either carries `status`, and
    `_to_variants` keeps only `status == "active"`. So `merged(overlay.json)`
    returned `[]` — a zero-case run, no error, for any stale script, README line
    or muscle-memory invocation. A wrong path must be loud.
    """
    path = ROOT / legacy
    assert path.is_file(), "the superseded files are kept on disk by design"
    with pytest.raises(ValueError, match="not a v2 unified corpus"):
        corpus.merged(path)


def test_the_hand_written_annotations_survived_adoption():
    """`_why` and its dated siblings are prose a human wrote next to a case, and
    `Variant` has six fields with `extra="ignore"` — so nothing loaded through the
    model can tell you they went missing. Task 1's generator emitted a fixed key
    set and silently dropped all 37 of them, taking two tests with it, including
    the one guarding that the DELETE case (which targets a REAL uid) warns it can
    destroy real data. Counted here so a regenerate that loses them again says so.
    """
    payload = json.loads(UNIFIED.read_text(encoding="utf-8"))
    variants = [v for fam in payload["families"].values() for v in fam["variants"]]
    counts = collections.Counter(k for v in variants for k in v if k.startswith("_"))
    assert counts == {"_why": 35, "_why_superseded_2026_08_03": 1, "_2026_07_28": 1}


def test_fingerprint_is_over_the_unified_corpus_only():
    """It hashed catalog + overlay. Overlay is gone, and corpus.json is now the
    whole corpus, so hashing it alone is both sufficient and honest.

    Fingerprints from runs before the migration will NOT match ones after it. That
    is correct: the corpus file genuinely changed, and a diff tool must say so
    rather than silently mis-pair cases across the boundary.
    """
    from nessie_tests import runner
    assert runner.corpus_fingerprint(UNIFIED) == corpus.sha256_of(UNIFIED)


def test_variant_meta_covers_every_definition():
    meta = corpus.variant_meta(UNIFIED)
    assert len(meta) == 383
    assert meta["repro.cypher_uid_dot"]["status"] == "retired"
    assert meta["green.mus_ndma"]["status"] == "active"
    assert meta["green.mus_ndma"]["is_bayesian"] is False

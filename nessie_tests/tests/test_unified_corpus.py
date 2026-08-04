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


def _key(v):
    """Everything about a variant that any caller can observe."""
    return (v.family, v.id, v.name, sorted(v.tags), sorted(v.requires_env),
            [(t.label, t.query, [(c.field, c.op, c.value) for c in t.pass_criteria])
             for t in v.turns])


def test_load_unified_returns_the_active_variants_only():
    active = corpus.load_unified(UNIFIED)
    assert len(active) == 283


def test_load_all_definitions_returns_active_plus_retired():
    assert len(corpus.load_all_definitions(UNIFIED)) == 383


def test_unified_resolves_to_the_same_content_as_the_three_file_corpus():
    """THE safety property: same 283 variants, same criteria, same everything.

    Compared SORTED BY ID, not in list order. Global flat order is genuinely
    unreproducible here and that is a property of the old format, not a defect in
    the new one: `corpus.merged` appends overlay-only variants at the end of the
    WHOLE list, so `writes_unsupported` occupies merged() idx 247-249 (its base
    members) AND 281-282 (its overlay-only members), with refine_and_recall in
    between. A JSON object cannot hold two blocks of one name, and a block
    flattens to one contiguous run. Measured 2026-08-04: 2 active variants are
    genuinely displaced, 31 more are their shift cascade.

    Nothing reads global order but the loop that runs the cases. The ordering that
    IS consumed -- `corpus.sample` buckets on v.family then draws with rng.sample
    -- is within-family, and the next test pins it.
    """
    old = corpus.merged(OVERLAY)
    new = corpus.merged_from_unified(UNIFIED)
    assert len(new) == len(old) == 283
    assert {v.id for v in new} == {v.id for v in old}
    assert sorted(_key(v) for v in new) == sorted(_key(v) for v in old)


def test_within_family_order_is_preserved_because_sampling_consumes_it():
    """`corpus.sample` draws `rng.sample(vs, k)` from each family's list, so a
    reordering inside a family silently changes every seeded case set even though
    the corpus content is identical. This is the ordering that actually matters."""
    def by_family(vs):
        out = {}
        for v in vs:
            out.setdefault(v.family, []).append(v.id)
        return out
    assert by_family(corpus.merged_from_unified(UNIFIED)) == by_family(corpus.merged(OVERLAY))


def test_seeded_sampling_is_unchanged_by_the_migration():
    """The end-to-end consequence of the previous two, stated as the thing an
    operator would actually notice: the same seed picks the same cases."""
    for seed in (0, 6):
        a = [v.id for v in corpus.sample(corpus.merged_from_unified(UNIFIED), 0.1, seed)]
        b = [v.id for v in corpus.sample(corpus.merged(OVERLAY), 0.1, seed)]
        assert a == b, f"seed {seed} selects a different case set after migration"


def test_unified_resolution_preserves_turn_count():
    assert sum(len(v.turns) for v in corpus.merged_from_unified(UNIFIED)) == 314


def test_variant_meta_covers_every_definition():
    meta = corpus.variant_meta(UNIFIED)
    assert len(meta) == 383
    assert meta["repro.cypher_uid_dot"]["status"] == "retired"
    assert meta["green.mus_ndma"]["status"] == "active"
    assert meta["green.mus_ndma"]["is_bayesian"] is False

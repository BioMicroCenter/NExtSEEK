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
    # `is_bayesian` used to be pinned False on this variant, which was a pin on
    # the value every variant carried at adoption. Task 4 curates the flag and
    # green.mus_ndma is in the selection, so the pin became a pin on a curation
    # decision. Replaced with the invariant that actually has to hold: the key is
    # a real bool on every definition, never null and never absent.
    assert all(isinstance(m["is_bayesian"], bool) for m in meta.values())


VALID_BEHAVIORS = {"AnswerDirectly", "GenerateArtifact", "ClarifyIfAmbiguous",
                   "UsePriorContext", "StateUnsupportedBoundary", "RefuseUnsafeOnly"}
VALID_KINDS = {"GEO_XLSX", "SRA_PACKAGE", "PRIDE_PACKAGE", "NFCORE_RNASEQ_CSV",
               "NFCORE_SCRNASEQ_CSV", "SVG_CHART", "UNKNOWN_FILE", "NONE_EXPECTED"}


def test_every_declared_family_has_defaults():
    """Keyed by DECLARED family, not by the nesting block.

    The file mirrors the source catalog's nesting, so a block is not always a
    family: `search_advanced` holds two variants declaring `routing_lab`, and
    `graph_query` holds one declaring `routing_graph`. Block-keyed defaults would
    hand all three the wrong values.

    (The plan cites `graph_stale` and `reporting_artifacts` as the example. Those
    blocks do not appear in corpus.json at all -- every member overrides a base id
    and `build_corpus.py` pins it to its BASE position -- so the example is
    restated here against blocks that exist. The argument is unchanged.)
    """
    payload = json.loads(UNIFIED.read_text(encoding="utf-8"))
    declared = {v["family"] for fam in payload["families"].values()
                for v in fam["variants"]}
    for name in declared:
        d = payload["family_defaults"].get(name)
        assert d, f"{name} has no defaults"
        assert d["expected_behavior"] in VALID_BEHAVIORS, name
        assert d["artifact_kind"] in VALID_KINDS, name
        assert isinstance(d["artifact_expected"], bool), name
        assert isinstance(d["hibayes_subtype"], str) and d["hibayes_subtype"], name


def test_defaults_cover_the_retired_only_families_too():
    """16 declared families, not 14.

    The task-4 brief's defaults table lists 14, which is the count of families
    with an ACTIVE variant. `test_every_declared_family_has_defaults` above scans
    every DEFINITION, retired included, and two families are retired-only:
    `nessie_repro` (4 repro cases, all retired 2026-08-03) and `routing_graph`
    (its one variant went with the GBM retirement). Shipping only the 14 makes
    that test fail on a KeyError-shaped assertion, so the map carries 16. This
    test names the two so the next person does not read the extra rows as slop
    and delete them.
    """
    payload = json.loads(UNIFIED.read_text(encoding="utf-8"))
    active = {v["family"] for fam in payload["families"].values()
              for v in fam["variants"] if v.get("status") == "active"}
    declared = {v["family"] for fam in payload["families"].values()
                for v in fam["variants"]}
    assert len(active) == 14
    assert declared - active == {"nessie_repro", "routing_graph"}
    # `_`-prefixed keys are the file's annotation convention, not families.
    # `corpus.load_family_defaults` strips them so a consumer can iterate.
    assert {k for k in payload["family_defaults"] if not k.startswith("_")} == declared
    assert set(corpus.load_family_defaults(UNIFIED)) == declared


def test_hibayes_meta_falls_back_to_the_family_default():
    m = corpus.hibayes_meta("green.mus_ndma", UNIFIED)
    assert m["expected_behavior"] == "AnswerDirectly"
    assert m["artifact_expected"] is False


def test_reporting_overrides_prove_the_override_mechanism_works():
    """The whole reason per-variant override exists: `reporting` spans two
    behaviours. Reporter-Summary answers directly; Report-GEO builds a file. A
    single family label is wrong for part of the family."""
    geo = corpus.hibayes_meta("report.i_need_to_submit_these_samples", UNIFIED)
    assert geo["hibayes_subtype"] == "Report-GEO"
    assert geo["expected_behavior"] == "GenerateArtifact"
    assert geo["artifact_expected"] is True
    assert geo["artifact_kind"] == "GEO_XLSX"


def test_every_reporting_deposit_variant_overrides_its_family_default():
    """The override set is complete, not just the one the test above samples.

    Every `reporting` variant whose query names GEO, SRA or PRIDE is a deposit
    request and must resolve to GenerateArtifact. Left on the family default it
    would be scored as a chat answer, which is the exact mis-labelling the
    defaults-plus-override design exists to prevent.
    """
    kinds = {"GEO": ("Report-GEO", "GEO_XLSX"), "SRA": ("Report-SRA", "SRA_PACKAGE"),
             "PRIDE": ("Report-PRIDE", "PRIDE_PACKAGE")}
    seen = collections.Counter()
    for v in corpus.load_all_definitions(UNIFIED):
        if v.family != "reporting":
            continue
        hit = [k for k in kinds if k in v.turns[0].query.upper()]
        if not hit:
            continue
        subtype, kind = kinds[hit[0]]
        m = corpus.hibayes_meta(v.id, UNIFIED)
        assert m["expected_behavior"] == "GenerateArtifact", v.id
        assert m["artifact_expected"] is True, v.id
        assert m["hibayes_subtype"] == subtype, v.id
        assert m["artifact_kind"] == kind, v.id
        seen[hit[0]] += 1
    assert seen == {"GEO": 11, "SRA": 7, "PRIDE": 6}
    # 24, not 23: `report.build_be_an_sra_submission_for` is RETIRED and still
    # gets the override, because a retired definition stays loadable and
    # `hibayes_meta` resolves it exactly like an active one.


def test_no_override_uses_an_invalid_enum_value():
    meta = corpus.variant_meta(UNIFIED)
    for vid, m in meta.items():
        if m["expected_behavior"] is not None:
            assert m["expected_behavior"] in VALID_BEHAVIORS, vid
        if m["artifact_kind"] is not None:
            assert m["artifact_kind"] in VALID_KINDS, vid


def test_a_none_override_means_inherit_rather_than_null():
    """The distinction the whole resolver turns on.

    Almost every variant stores `null` for all four keys, and `hibayes_meta` has
    to read that as "take the family's value", never as "this variant's value is
    None". If it ever returned the stored null through, 260 of the 283 active
    variants would resolve to no behaviour at all and every one of them would be
    unscoreable.
    """
    raw = corpus.variant_meta(UNIFIED)["advanced.basic_ndma"]
    assert raw["expected_behavior"] is None and raw["hibayes_subtype"] is None
    resolved = corpus.hibayes_meta("advanced.basic_ndma", UNIFIED)
    assert resolved["expected_behavior"] == "AnswerDirectly"
    assert resolved["hibayes_subtype"] == "Search-Basic"


def test_hibayes_meta_resolves_against_the_declared_family_not_the_block():
    """routing.lab_ooc_kamm_count sits in the `search_advanced` block and declares
    `routing_lab`. Block-keyed defaults would hand it Search-Basic."""
    m = corpus.hibayes_meta("routing.lab_ooc_kamm_count", UNIFIED)
    assert m["hibayes_subtype"] == "Reporter-Summary"


def test_hibayes_meta_raises_on_an_unknown_id():
    with pytest.raises(KeyError):
        corpus.hibayes_meta("no.such.variant", UNIFIED)


def test_bayesian_selection_is_nonempty_active_and_family_balanced():
    ids = corpus.bayesian_ids(UNIFIED)
    assert 100 <= len(ids) <= 150, f"selection is {len(ids)}; spec asks for 100-150"
    active = {v.id for v in corpus.load_unified(UNIFIED)}
    assert set(ids) <= active, "a retired variant cannot be selected"
    fams = {v.family for v in corpus.load_unified(UNIFIED) if v.id in set(ids)}
    all_fams = {v.family for v in corpus.load_unified(UNIFIED)}
    assert fams == all_fams, f"families with no bayesian variant: {sorted(all_fams - fams)}"


def test_bayesian_selection_is_in_corpus_order_and_has_no_duplicates():
    """`bayesian_ids` IS the --bayesian run order, so it has to be stable."""
    ids = corpus.bayesian_ids(UNIFIED)
    assert len(ids) == len(set(ids))
    order = [v.id for v in corpus.load_unified(UNIFIED)]
    assert ids == [i for i in order if i in set(ids)]


def test_bayesian_ids_drops_a_flagged_variant_that_was_later_retired(tmp_path):
    """Retirement is a flag flip, not a deletion, so a retired definition KEEPS the
    `is_bayesian` it was curated with -- and `scripts/build_corpus.py` carries that
    forward on purpose, so un-retiring restores the curation. Without the status
    filter in `bayesian_ids` the retired case would stay in the paid run.

    Tested against a synthetic payload rather than by asserting no such row exists
    in corpus.json today: that would only restate the current data, and would fail
    the day someone retires a selected case rather than telling them the guard held.
    """
    p = tmp_path / "corpus.json"
    p.write_text(json.dumps({
        "version": 2,
        "families": {"f": {"description": "", "variants": [
            {"id": "a", "family": "f", "name": "a", "tags": [], "requires_env": [],
             "turns": [], "status": "active", "is_bayesian": True},
            {"id": "b", "family": "f", "name": "b", "tags": [], "requires_env": [],
             "turns": [], "status": "retired", "is_bayesian": True},
        ]}},
    }), encoding="utf-8")
    assert corpus.bayesian_ids(p) == ["a"]


def test_bayesian_selection_takes_the_whole_refine_and_recall_family():
    """The family where NS and CC differ most, and the reason the row unit is a
    variant rather than a turn. Sampling it would defeat the point."""
    ids = set(corpus.bayesian_ids(UNIFIED))
    rr = [v.id for v in corpus.load_unified(UNIFIED) if v.family == "refine_and_recall"]
    assert rr and set(rr) <= ids


def test_bayesian_selection_includes_the_two_job_launching_pipeline_cases():
    """Spec risk R2, accepted deliberately: each ends on a literal `submit` turn,
    so a paired NS/CC run launches four real jobs. Pinned here so nobody quietly
    drops them to make a run cheaper without reopening the decision."""
    ids = set(corpus.bayesian_ids(UNIFIED))
    assert {"pipeline.end_to_end_emit", "pipeline.happy_path_scrnaseq"} <= ids

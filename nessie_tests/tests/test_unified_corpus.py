"""corpus.json is the corpus, and has to be self-consistent on its own terms.

Until 2026-08-04 this file's job was to prove the unified corpus resolved to
EXACTLY what the three-file corpus resolved to. It did, variant-for-variant, and
Task 3 then switched `merged()` over. The comparison tests went with the switch:
they compared against sources nothing reads any more.

The GENERATOR PIN went with them too, on the premise that `corpus.json` is
hand-owned from Task 4 so `build(...) == corpus.json` would fail on correct work.
That premise turned out to be false FOR THE CARRIED CATEGORIES. Task 4's
carry-forward makes the generator read `family_defaults`, the non-null per-variant
HiBayes overrides, `is_bayesian` and a RETIREMENT back out of the committed file,
so the equality holds byte-identically over an edit to any of those four -- and
while the pin was absent, a rebuild silently resurrected a retirement recorded
only in `corpus.json` AND brought it back flagged for the paid run. The pin is
restored below; it would have caught that on its own. The other six edit
categories a curator makes (un-retiring, the four policy blocks, a variant body,
a hand-added variant, a family description, a hand-added `_why`) do NOT survive a
rebuild, and the pin's failure message is where that list is spelled out.

What this file covers: the counts, the duplicate-id guard, the retirement
records, the guarantee that a retired definition is still loadable, the HiBayes
defaults-and-override resolution, and the `is_bayesian` selection.
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
    assert UNIFIED.is_file(), "nessie_tests/corpus.json is missing; it is hand-owned and nothing regenerates it"
    json.loads(UNIFIED.read_text(encoding="utf-8"))


def test_unified_holds_every_definition():
    """408 = 366 adopted from catalog.json + 17 that only ever existed in the
    overlay + 25 added by the 2026-08-06 additive pass. Retired ones are KEPT,
    which is why this is 408 and not 308.

    Was 383. The 2026-08-06 pass extended an ALREADY-GRADED set rather than
    rebuilding it: 16 atlas variants were read, repaired and promoted (so they
    leave the atlas set and enter the curated one) and 9 were written fresh.
    Nothing was retired, deselected or edited, because every one of those voids
    a human grade that has already been paid for.
    """
    payload = json.loads(UNIFIED.read_text(encoding="utf-8"))
    ids = {v["id"] for fam in payload["families"].values() for v in fam["variants"]
           if v.get("origin") != "atlas"}
    assert len(ids) == 470  # 408 -> 470: 2026-08-06 question set: 58 authored, 6 retired, 76 deselected, 4 promoted out of the atlas set.


def test_every_retired_definition_is_still_loadable():
    """Retirement is not deletion: the definition is kept so reinstating is a data
    edit, not a code change. After Task 3 nothing else compares those 100 bodies
    against anything, so this is what stops one rotting unnoticed until the day
    someone reinstates it."""
    retired = [v for v in corpus.load_all_definitions(CORPUS)
               if corpus.variant_meta(CORPUS)[v.id]["status"] == "retired"]
    assert len(retired) == 105  # 100 -> 105: 2026-08-06 question set: +58 new variants, +6 retirements, 76 deselections (is_bayesian only, still active).
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
    assert len(retired) == 105  # 100 -> 105: 2026-08-06 question set: +58 new variants, +6 retirements, 76 deselections (is_bayesian only, still active).
    for v in retired:
        rec = v["retirement"]
        assert rec and rec["reason"] and rec["retired_on"] and rec["decided_by"], v["id"]


def test_load_unified_returns_the_active_variants_only():
    # 283 -> 308: +25 from the 2026-08-06 additive pass (16 promoted out of the
    # atlas set into the curated one, 9 written fresh). Nothing was removed.
    active = corpus.curated(corpus.load_unified(UNIFIED))
    assert len(active) == 365  # 308 -> 365: 2026-08-06 question set: 58 authored, 6 retired, 76 deselected, 4 promoted out of the atlas set.


def test_load_all_definitions_returns_active_plus_retired():
    assert len(corpus.curated(corpus.load_all_definitions(UNIFIED))) == 470  # 2026-08-06 question set: 58 authored, 6 retired, 76 deselected, 4 promoted out of the atlas set.


def test_unified_resolution_preserves_turn_count():
    # 314 -> 343. The 25 additions carry 29 turns between them: several are
    # genuinely multi-turn, and three atlas variants were REPAIRED on promotion
    # because they had a multi-turn script flattened into one literal query.
    assert sum(len(v.turns) for v in corpus.curated(corpus.merged_from_unified(UNIFIED))) == 413  # 343 -> 413: 2026-08-06 question set: 58 authored, 6 retired, 76 deselected, 4 promoted out of the atlas set. 15 of the 58 additions are multi-turn, and 3 edits split a flattened script into real turns.


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
    # `_atlas` is machine provenance ({capability, assertion}) on the generated
    # variants, under ONE key so it cannot drown the signal this test exists for:
    # that the hand-written notes are still here.
    #
    # 2026-08-06: `_why` 35 -> 43 and a new `_promoted_2026_08_06` key on 17.
    # Promotion out of the atlas set does NOT strip `_atlas` -- the machine
    # provenance is a true record of where the question came from, and deleting it
    # would make a promoted variant indistinguishable from one a human wrote. So
    # `_atlas` counts variants ever generated (80, +1 for a fresh variant modelled
    # on one), while the `atlas` TAG counts those still unreviewed (63).
    # 2026-08-06 question set: `_why` 43 -> 101 (one per authored variant), plus
    # `_added_2026_08_06_qset` 58, `_edited_2026_08_06_qset` 86,
    # `_deselected_2026_08_06_qset` 76 and `_promoted_2026_08_06_qset` 4. Every
    # deselection and every retirement records a written reason; that is what makes
    # the selection reviewable without a diff.
    assert counts == {"_why": 101, "_why_superseded_2026_08_03": 1,
                      "_2026_07_28": 1, "_atlas": 80,
                      "_promoted_2026_08_06": 17, "_added_2026_08_06": 8,
                      "_added_2026_08_06_qset": 58, "_edited_2026_08_06_qset": 86,
                      "_deselected_2026_08_06_qset": 76,
                      "_promoted_2026_08_06_qset": 4}


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
    curated_ids = {v.id for v in corpus.curated(corpus.load_all_definitions(UNIFIED))}
    assert len({k for k in meta if k in curated_ids}) == 470  # 2026-08-06 question set: 58 authored, 6 retired, 76 deselected, 4 promoted out of the atlas set.
    assert meta["repro.cypher_uid_dot"]["status"] == "retired"
    assert meta["green.mus_ndma"]["status"] == "active"
    # `is_bayesian` used to be pinned False on this variant, which was a pin on
    # the value every variant carried at adoption. Task 4 curates the flag and
    # green.mus_ndma is in the selection, so the pin became a pin on a curation
    # decision. Replaced with the invariant that actually has to hold: the key is
    # a real bool on every definition, never null and never absent.
    assert all(isinstance(m["is_bayesian"], bool) for m in meta.values())


# dmac-assistant/tools/hibayes/expected_behavior.py FAMILIES_22. Not ours to
# extend: `expected_behavior_rule` raises KeyError on anything outside it.
CANONICAL_22 = frozenset({
    "Edge", "Graph-Assay", "Graph-Count", "Graph-Lineage", "Graph-Study", "Memory",
    "Report-GEO", "Report-NFCORE", "Report-PRIDE", "Report-SRA", "Reporter-Summary",
    "Retrieve", "SampleTree", "Search-Attribute", "Search-Basic", "Search-MultiAssay",
    "Search-Refine", "System-Capabilities", "System-Entity", "Unsupported",
    "Write-Create", "Write-Update"})

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

    (Historical: `graph_stale` and `reporting_artifacts` never appeared in
    corpus.json, because every member overrode a base id and the since-deleted
    generator pinned it to its BASE position. The 2026-08-04 remap made block ==
    family everywhere, so the divergence this guards against no longer exists in
    the file -- but the resolution order still keys on the declared family, and
    this keeps it that way.)
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
        # A NULL subtype is deliberate, not missing. `hibayes_subtype` is a foreign
        # key into dmac-assistant/tools/hibayes/expected_behavior.py FAMILIES_22,
        # kept so these rows concatenate with upstream runs (design decision D5).
        # Eight of the 28 families test surface upstream has no label for, and
        # inventing one would put those rows in a stratum of size ours-only; the
        # posterior pools them on `task_family` instead. What is NOT allowed is a
        # value outside the 22: `expected_behavior_rule` raises KeyError on those.
        st = d["hibayes_subtype"]
        assert st is None or st in CANONICAL_22, f"{name}: {st!r} is not one of the canonical 22"


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
    # Curated only: the 79 atlas variants populate 10 families that were empty, so
    # counting them here would erase the very gap this test records.
    cur = [v for fam in payload["families"].values() for v in fam["variants"]
           if v.get("origin") != "atlas"]
    active = {v["family"] for v in cur if v.get("status") == "active"}
    declared = {v["family"] for v in cur}
    # 13 -> 21. The 2026-08-06 pass promoted atlas variants in 8 previously
    # atlas-only families into the curated set, so those families now hold an
    # active CURATED variant. The gap this test records shrank by exactly 8.
    assert len(active) == 25, sorted(active)  # 21 -> 25: the question set covers
    # pipeline_output_reingest, session_lifecycle, cross_session_memory and
    # entity_write for the first time.
    # pipeline_output_reingest and entity_write hold one RETIRED variant each and
    # no active one. They are not slop: a retired definition stays loadable and
    # `hibayes_meta` resolves it exactly like an active one.
    # Was {"pipeline_output_reingest", "entity_write"}; both now hold active,
    # selected variants, so the retired-only set is EMPTY. The invariant this test
    # exists for is unchanged and is asserted below: every declared family resolves,
    # active or not.
    assert declared - active == set()
    # `_`-prefixed keys are the file's annotation convention, not families.
    # `corpus.load_family_defaults` strips them so a consumer can iterate.
    # Defaults cover every family in the block, not just the ones holding a
    # variant today. 15 of the 28 start empty by design -- that gap is the whole
    # point of the 2026-08-04 remap -- and a family without defaults would fail
    # the moment its first case is written.
    blocks = set(payload["families"])
    assert len(blocks) == 28, sorted(blocks)
    assert declared <= blocks
    assert {k for k in payload["family_defaults"] if not k.startswith("_")} == blocks
    assert set(corpus.load_family_defaults(UNIFIED)) == blocks


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

    Every `submission_package` variant whose query names GEO, SRA or PRIDE is a deposit
    request and must resolve to GenerateArtifact. Left on the family default it
    would be scored as a chat answer, which is the exact mis-labelling the
    defaults-plus-override design exists to prevent.
    """
    kinds = {"GEO": ("Report-GEO", "GEO_XLSX"), "SRA": ("Report-SRA", "SRA_PACKAGE"),
             "PRIDE": ("Report-PRIDE", "PRIDE_PACKAGE")}
    seen = collections.Counter()
    for v in corpus.curated(corpus.load_all_definitions(UNIFIED)):
        if v.family != "submission_package":
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
    """Before 2026-08-04 a block was not always a family: routing.lab_ooc_kamm_count
    sat in the `search_advanced` block and declared `routing_lab`, so block-keyed
    defaults handed it the wrong values. The 28-family remap made block == family
    everywhere, which dissolves that hazard rather than fixing it.

    So this now pins the stronger property: no variant declares a family other
    than the block it sits in. `hibayes_meta` keys on the declared family, and the
    day those two diverge again it must still be the declared one that wins.
    """
    payload = json.loads(UNIFIED.read_text(encoding="utf-8"))
    for block, body in payload["families"].items():
        for v in body["variants"]:
            assert v["family"] == block, f"{v['id']}: declares {v['family']}, sits in {block}"
    m = corpus.hibayes_meta("routing.lab_ooc_kamm_count", UNIFIED)
    assert m["hibayes_subtype"] == "Search-Basic"


def test_hibayes_meta_raises_on_an_unknown_id():
    with pytest.raises(KeyError):
        corpus.hibayes_meta("no.such.variant", UNIFIED)


def test_bayesian_selection_is_nonempty_active_and_family_balanced():
    """Every family that CAN be measured under --bayesian is represented.

    "Can be measured" is the qualifier, and it was added when the three
    `route_gate` variants left the selection (see
    `test_no_route_gate_variant_is_selected_for_the_paid_paired_run`). A family
    whose every active variant is a route gate has nothing left to contribute,
    and `nessie_route` is exactly that family: all 3 of its active variants are
    gates. Demanding it be represented would demand re-flagging one of the three
    cases this fix removed.

    The exemption is DERIVED from the tags rather than spelled as a family name,
    so it evaporates on its own the day a non-gate variant joins `nessie_route`.
    """
    ids = corpus.bayesian_ids(UNIFIED)
    # 100-150 was the bound while the selection WAS the study: one run, priced as
    # a whole. From 2026-08-06 the selection is an already-paid 127 plus a 25
    # variant extension, and the study is assembled from two runs at the CSV
    # layer. The upper bound moves to 160 to admit that, and no further: it is
    # still the thing that stops a selection growing without anyone pricing it.
    # Re-running all 152 would REPAY for the 127 -- see the delta-run runbook.
    assert 100 <= len(ids) <= 160, f"selection is {len(ids)}; spec asks for 100-160"
    active = [v for v in corpus.load_unified(UNIFIED)]
    assert set(ids) <= {v.id for v in active}, "a retired variant cannot be selected"
    # Curated only. The atlas set is `is_bayesian: false` by design (unreviewed
    # questions must not enter a PAID paired run), so the families it alone
    # populates have no bayesian member and never should until someone reads them.
    active = corpus.curated(active)
    fams = {v.family for v in active if v.id in set(ids)}
    measurable = {v.family for v in active if "route_gate" not in v.tags}
    assert fams == measurable, \
        f"families with no bayesian variant: {sorted(measurable - fams)}"
    gate_only = {v.family for v in active} - measurable
    assert gate_only == set(), (
        f"a gate-only family appeared: {sorted(gate_only)}. Confirm it really has "
        f"nothing measurable in it before widening this.\n"
        f"There were none as of the 2026-08-04 remap: `nessie_route` was a "
        f"provenance grouping rather than an intent, so its 3 gates were filed by "
        f"question content (sample_search, graph_traversal, unsupported) and kept "
        f"their `route_gate` tag. Each landed in a family with plenty of "
        f"measurable variants, which is why the exemption now covers nothing.")


def test_no_route_gate_variant_is_selected_for_the_paid_paired_run():
    """A `route_gate` variant in `bayesian_ids` is money spent to measure nothing.

    Three of them were selected (`route.ns_advanced`, `route.unrelated`,
    `route.ns_plain_study_membership`) until 2026-08-04. What that cost, in order:

      1. `runner.run_case` sets `case_tier = "route" if is_gate else tier`, so a
         gate variant is driven route-only whatever `--bayesian` asks for, and
         its ONLY pass_criteria are `route` assertions.
      2. `bayesian.run_paired` passes `strip_route_criteria=True`, which removes
         exactly those. Under a FORCED route a `route` assertion tests the
         harness's own request body, not the product, so stripping is right.
      3. 1 and 2 together leave zero criteria, so every arm returns
         `no_assertions`, which `runner._is_real_failure` counts as a REAL
         FAILURE. Six guaranteed red arms per run, every run.
      4. And they are not even cheap. Route-tier polling is a CLIENT-side stop:
         `http_driver.drive` breaks at `route_decided` while the SERVER runs the
         turn to completion. The 3 `cc` arms are billed as full Opus turns whose
         `total_cost_usd` is never read back -- so their spend is invisible to
         `--max-usd`, which is the one control that is supposed to stop the run.

    Selection is `is_bayesian` in corpus.json and nothing else, by design, so
    this cannot be enforced by a filter in `bayesian.py` without creating the
    second selection source that design forbids. It is enforced here instead:
    re-flag any gate variant and this test goes red before the money does.
    """
    gates = {v.id for v in corpus.load_unified(UNIFIED) if "route_gate" in v.tags}
    assert gates, "fixture drifted: no active route_gate variants left to guard"
    assert not (gates & set(corpus.bayesian_ids(UNIFIED))), (
        f"route_gate variants are selected for --bayesian: "
        f"{sorted(gates & set(corpus.bayesian_ids(UNIFIED)))}. Each evaluates zero "
        f"criteria under strip_route_criteria and returns no_assertions, and its cc "
        f"arm is billed as a full turn whose cost --max-usd never sees. Clear "
        f"`is_bayesian` in corpus.json rather than filtering in bayesian.py.")


def test_bayesian_selection_is_in_corpus_order_and_has_no_duplicates():
    """`bayesian_ids` IS the --bayesian run order, so it has to be stable."""
    ids = corpus.bayesian_ids(UNIFIED)
    assert len(ids) == len(set(ids))
    order = [v.id for v in corpus.load_unified(UNIFIED)]
    assert ids == [i for i in order if i in set(ids)]


def test_bayesian_ids_drops_a_flagged_variant_that_was_later_retired(tmp_path):
    """Retirement is a flag flip, not a deletion, so a retired definition KEEPS the
    `is_bayesian` it was curated with, so un-retiring restores the curation in one
    edit. Without the status filter in `bayesian_ids` the retired case would stay in
    the paid run.

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


def test_every_deselected_refine_and_recall_member_records_why():
    """Was `..._takes_the_whole_refine_and_recall_family`.

    The original intent was "do not let a seeded RNG choose which of these run",
    and taking the family WHOLE was how that was expressed. That held while whole
    and distinct were the same thing. They are not: the 2026-08-06 study found ten
    of the seventeen selected `followup_over_results` members opened with the SAME
    seed question, and four of the nine `search_refinement` members shared both a
    seed and a refinement. Taking the family whole meant paying for the same
    opening turn ten times.

    The intent survives and is what is asserted now: nothing leaves this selection
    by accident. Every deselected member must carry a WRITTEN reason, so the only
    way out of the paid run is a decision somebody recorded.
    """
    ids = set(corpus.bayesian_ids(UNIFIED))
    halves = ("followup_over_results", "search_refinement")
    payload = json.loads(UNIFIED.read_text(encoding="utf-8"))
    members = [v for fam in payload["families"].values() for v in fam["variants"]
               if v["family"] in halves and v["status"] == "active"
               and v.get("origin") != "atlas"]
    assert members, "guard: no members means this proves nothing"
    selected = [v for v in members if v["id"] in ids]
    assert selected, "the whole family left the paid selection"
    for half in halves:
        assert any(v["family"] == half for v in selected), half
    unexplained = [v["id"] for v in members
                   if v["id"] not in ids and not v.get("_deselected_2026_08_06_qset")]
    assert not unexplained, (
        f"deselected without a written reason: {sorted(unexplained)}")


def test_no_selected_variant_ends_on_a_bare_submit_turn():
    """Was `..._includes_the_two_job_launching_pipeline_cases`, which PINNED the two
    variants ending on a literal `submit`.

    That pin was correct for a corpus in which nobody had checked what `submit`
    reaches. It does reach: `PIPELINE_LAUNCH_MODE=LURIA` and a populated `LURIAKEY`
    are both live in `docker/nextseek.env`, and `submit_to_luria` ssh+sbatches on
    MIT Luria. Neither arm actually launched in the 2026-08-06 run, but both
    refused for question-specific reasons (a degenerate single-group cohort, and a
    UID that does not exist) rather than because anything stopped them.

    So the decision was reopened, which is exactly what the old test asked for, and
    reversed. Launch behaviour is still measured — `pipeline.describe_before_submitting`
    asks for the full cohort and reference genome and carries a negative guard that
    fails if the reply claims a job was queued — but no selected variant hands the
    system the word `submit` with a build already staged.
    """
    ids = set(corpus.bayesian_ids(UNIFIED))
    merged = {v.id: v for v in corpus.merged(UNIFIED)}
    offenders = [vid for vid in ids
                 if (merged[vid].turns[-1].query or "").strip().lower().rstrip(".!")
                 in {"submit", "yes, submit it", "go ahead and submit", "submit it"}]
    assert not offenders, (
        f"selected variants ending on a bare submit turn: {sorted(offenders)}. "
        f"The deployed stack has a live LURIAKEY and submit_to_luria really sbatches.")


# --- Fix round 2026-08-04: the generator pin, the hand overrides, and typing ---


# id -> the keys it overrides and the values they must resolve to. Every entry is
# a decision someone argued for; without this table each one is a single null
# away from silently reverting to its family default and nothing would fail.
HAND_OVERRIDES = {
    # Single-cell, not bulk. NFCORE_SCRNASEQ_CSV appears nowhere else in the
    # corpus, so one reverted null erases the value entirely.
    "pipeline.happy_path_scrnaseq": {"artifact_kind": "NFCORE_SCRNASEQ_CSV"},
    "pipeline.build_a_single_cell_rna_seq_pi": {"artifact_kind": "NFCORE_SCRNASEQ_CSV"},
    # Capability questions inside an artifact-generating family.
    "pipeline.question_submode": {"expected_behavior": "AnswerDirectly",
                                  "artifact_expected": False,
                                  "artifact_kind": "NONE_EXPECTED"},
    "pipeline.reject_non_directive": {"expected_behavior": "AnswerDirectly",
                                      "artifact_expected": False,
                                      "artifact_kind": "NONE_EXPECTED"},
    # Anaphoric request with no context: correct behaviour is to ASK. On the
    # family default this scored as a failure for doing the right thing.
    "pipeline.build_an_nfcore_samplesheet_fo": {"expected_behavior": "ClarifyIfAmbiguous",
                                                "artifact_expected": False,
                                                "artifact_kind": "NONE_EXPECTED"},
    # Read-only exports: nothing unsafe is being refused. `Write-Export` was not
    # among the canonical 22 in dmac-assistant/tools/hibayes/expected_behavior.py,
    # so expected_behavior_rule raised KeyError on it. Corrected 2026-08-04.
    "write.download_all_samples_from_the": {"hibayes_subtype": "Unsupported",
                                            "expected_behavior": "StateUnsupportedBoundary"},
    "write.export_all_metadata_for_nhp_22": {"hibayes_subtype": "Unsupported",
                                             "expected_behavior": "StateUnsupportedBoundary"},
    # Destructive writes: propose and confirm, never perform. `Write-Delete` was
    # likewise outside the 22; upstream maps every Write-* to RefuseUnsafeOnly,
    # so Write-Update carries the identical behaviour.
    "write.update_scientist_must_confirm_first": {"hibayes_subtype": "Write-Update",
                                                  "expected_behavior": "RefuseUnsafeOnly"},
    "write.delete_sample_must_confirm_first": {"hibayes_subtype": "Write-Update",
                                               "expected_behavior": "RefuseUnsafeOnly"},
    # Route-gate members that are not plain NS searches.
    "route.unrelated": {"hibayes_subtype": "Unsupported",
                        "expected_behavior": "StateUnsupportedBoundary"},
    "route.ns_plain_study_membership": {"hibayes_subtype": "Graph-Count"},
}


def test_every_hand_override_still_overrides():
    """The 24 mechanical `reporting` deposits have a sweep test. These do not.

    Measured before this existed: reverting `pipeline.happy_path_scrnaseq` to the
    family's RNASEQ default, reverting `pipeline.question_submode` to
    GenerateArtifact, and reverting all seven `writes_unsupported` subtypes to
    Write-Create each survived the entire suite.
    """
    for vid, expected in HAND_OVERRIDES.items():
        resolved = corpus.hibayes_meta(vid, UNIFIED)
        for key, want in expected.items():
            assert resolved[key] == want, f"{vid}.{key}: {resolved[key]!r} != {want!r}"


def test_the_clarify_behaviour_is_actually_used():
    """`ClarifyIfAmbiguous` was in the enum and used ZERO times across all 383
    definitions, which is what let the one variant that demands a clarification
    sit on GenerateArtifact/artifact_expected=true -- scoring correct behaviour
    as failure on both arms.

    The corpus holds exactly one criterion that demands a clarification-shaped
    reply. If a second is ever written, it needs this behaviour too.
    """
    meta = corpus.variant_meta(UNIFIED)
    users = {vid for vid, m in meta.items() if m["expected_behavior"] == "ClarifyIfAmbiguous"}
    assert "pipeline.build_an_nfcore_samplesheet_fo" in users
    # The atlas pass wrote a second on 2026-08-04, and this test is why it carries
    # the label: an anaphoric "which project should I upload this to" would
    # otherwise score as a failure for correctly asking.
    assert "entity.which_project_should_i_upload_th" in users

    import re
    clar = re.compile(r"clarify|specify|which|don't have|no .*(pinned|prior|previous)", re.I)
    demanding = {
        v.id for v in corpus.load_all_definitions(UNIFIED)
        for t in v.turns for c in t.pass_criteria
        if c.field in ("last_reply", "ui_text.assistant_reply")
        and c.op == "matches_re" and isinstance(c.value, str) and clar.search(c.value)
    }
    assert demanding <= users, (
        f"criteria demand a clarification but are not labelled ClarifyIfAmbiguous: "
        f"{sorted(demanding - users)}")


def test_the_reporting_sweep_reads_every_turn_not_just_the_first():
    """The deposit sweep keys on the query text. Today every `reporting` variant
    is single-turn, so reading `turns[0]` is equivalent -- but it is equivalent by
    accident, and a two-turn deposit case would escape the sweep silently. This
    pins the assumption so the day it breaks, it breaks here.
    """
    multi = [v.id for v in corpus.load_all_definitions(UNIFIED)
             if v.family == "reporting" and len(v.turns) != 1]
    assert not multi, (
        f"reporting is no longer single-turn ({multi}); the deposit sweep and its "
        f"test must scan every turn, not just turns[0]")


VALID_SUBTYPES = {
    "Search-Basic", "Search-Refine", "Retrieve", "SampleTree", "Graph-Lineage",
    "Graph-Count", "Reporter-Summary", "System-Capabilities", "Unsupported",
    "Report-GEO", "Report-SRA", "Report-PRIDE", "Report-NFCORE",
    "Write-Create", "Write-Update", "Write-Delete", "Write-Export",
}


def test_variant_level_metadata_is_typed_not_just_the_defaults():
    """Only `family_defaults` was type-checked, so a variant could carry
    `artifact_expected: "yes"` and ship green -- and `export_stage_b` does
    `bool(...)` on it, which turns any non-empty string into True. A typo would
    have flipped a case's expectation with nothing to catch it.
    """
    for vid, m in corpus.variant_meta(UNIFIED).items():
        assert isinstance(m["is_bayesian"], bool), f"{vid}: is_bayesian"
        for key in ("hibayes_subtype", "expected_behavior", "artifact_kind"):
            assert m[key] is None or isinstance(m[key], str), f"{vid}: {key}"
        assert m["artifact_expected"] is None or isinstance(m["artifact_expected"], bool), \
            f"{vid}: artifact_expected is {m['artifact_expected']!r}, must be a bool or null"
        if m["hibayes_subtype"] is not None:
            assert m["hibayes_subtype"] in VALID_SUBTYPES, f"{vid}: {m['hibayes_subtype']}"


def test_resolved_artifact_expectation_and_kind_agree():
    """`artifact_expected: true` with `artifact_kind: NONE_EXPECTED` is
    incoherent in both directions, and neither half was guarded. Checked on the
    RESOLVED value, because an override can supply one half and the family
    default the other -- which is exactly how the incoherent pair would arise.
    """
    for v in corpus.load_all_definitions(UNIFIED):
        m = corpus.hibayes_meta(v.id, UNIFIED)
        if m["artifact_expected"]:
            assert m["artifact_kind"] != "NONE_EXPECTED", \
                f"{v.id} expects an artifact but declares no kind"
        else:
            assert m["artifact_kind"] == "NONE_EXPECTED", \
                f"{v.id} expects no artifact but declares kind {m['artifact_kind']}"
        assert m["expected_behavior"] != "GenerateArtifact" or m["artifact_expected"], \
            f"{v.id} is GenerateArtifact but artifact_expected is false"


def test_the_hibayes_key_set_has_exactly_one_definition():
    """`_META_KEYS` says it is "THE ONE PLACE" a metadata key is registered, and
    three separate spellings of the HiBayes subset quietly undercut that: this
    module's tuple, the generator's `HIBAYES_KEYS`, and a literal re-listing inside
    `_variant_dict`. Two of the three went with `scripts/build_corpus.py` on
    2026-08-04, leaving `corpus._HIBAYES_KEYS` as the single definition -- which is
    what this now pins.
    """
    assert set(corpus._HIBAYES_KEYS) <= set(corpus._META_KEYS)
    # And the generator really emits every one of them on every definition.
    payload = json.loads(UNIFIED.read_text(encoding="utf-8"))
    for fam in payload["families"].values():
        for v in fam["variants"]:
            missing = [k for k in corpus._HIBAYES_KEYS if k not in v]
            assert not missing, f"{v['id']} is missing {missing}"


def test_the_atlas_set_is_additive_and_inert():
    """The 79 generated variants, asserted on their own terms.

    `corpus.curated` filters them out of every measurement in this suite, which is
    only defensible if something else pins them. This is that something.

    ADDITIVE: they never displace a curated variant, so `merged()` is exactly the
    curated corpus plus this set.

    INERT where it matters: `is_bayesian` is false on all 79, because the paid
    paired run must not spend money on questions nobody has read. The 2026-07-30
    review retired 100 variants, most of them bad questions or near-duplicates,
    and generating 79 more straight into a paid run would repeat that at cost.

    They are NOT inert in the free tiers -- they run, and 11 families that held no
    active variant at all now hold one. That gap was the point of the whole
    2026-08-04 remap, so closing part of it is the deliverable, not a side effect.
    """
    defs = corpus.load_all_definitions(UNIFIED)
    atlas = [v for v in defs if "atlas" in v.tags]
    # 79 -> 63. On 2026-08-06, 16 were READ, ground-truthed, repaired where wrong
    # and promoted into the curated set, which is exactly the exit `corpus.curated`
    # documents ("promoting an atlas variant to reviewed is a tag flip"). The
    # invariant below is unchanged and is the one that matters: whatever remains
    # unreviewed stays out of the PAID selection.
    # 79 -> 63 -> 59. The 2026-08-06 question set read, ground-truthed and
    # selected four more of them, which is an origin AND tag flip; `_atlas`
    # provenance is kept on all 80 so where the question came from is still on
    # the record.
    assert len(atlas) == 59

    assert all(v.turns and len(v.turns) == 1 for v in atlas), "one turn each"
    assert all(len(v.turns[0].pass_criteria) >= 1 for v in atlas), "every one asserts something"

    meta = corpus.variant_meta(UNIFIED)
    assert not [v.id for v in atlas if meta[v.id]["is_bayesian"]], \
        "an unreviewed atlas variant is in the PAID paired selection"
    assert all(meta[v.id]["status"] == "active" for v in atlas)
    assert all(meta[v.id]["origin"] == "atlas" for v in atlas)

    # Additive: curated + atlas is the whole corpus, with nothing displaced.
    assert len(corpus.curated(defs)) + len(atlas) == len(defs)
    assert len({v.id for v in atlas} & {v.id for v in corpus.curated(defs)}) == 0

    # Every one carries its provenance back to the capability it came from.
    payload = json.loads(UNIFIED.read_text(encoding="utf-8"))
    raw = {v["id"]: v for fam in payload["families"].values() for v in fam["variants"]}
    for v in atlas:
        note = raw[v.id].get("_atlas")
        assert note and note.get("capability") and note.get("assertion"), v.id

    # The families it newly populates. If this shrinks, someone reviewed some and
    # flipped the tag, which is the intended direction; if it grows, a generator
    # ran again without anyone deciding to.
    newly = {v.family for v in atlas} - {v.family for v in corpus.curated(corpus.merged(UNIFIED))}
    # 11 -> 3 -> 1, the direction the comment above names as intended.
    #
    # `cross_session_memory` and `entity_write` left the set on 2026-08-06 when the
    # question set covered them with curated variants. Neither hazard was waved
    # away: `entity_write` is covered by three formulations that WITHHOLD consent
    # (a read-only schema inspection, an explicit dry run, and a registration that
    # cannot complete because the required fields are absent), and
    # `cross_session_memory` is covered only for what forcing can actually reach —
    # within-chat recall and the `fresh_session` guarantee — with the cross-ENGINE
    # recall behind issues #36/#37/#38 recorded on the variants as out of reach.
    #
    # `engine_routing` remains, and remains for the original reason: asserting a
    # route the harness itself forced is tautological.
    assert newly == {"engine_routing"}, sorted(newly)

from pathlib import Path
from nessie_tests import corpus

CORPUS = Path(__file__).resolve().parents[1] / "corpus.json"


def test_base_loads_and_is_tagged():
    """`load_base` is no longer a corpus source — it reads the vendored catalog,
    which corpus.json was adopted from and which Task 5's drift test watches."""
    base = corpus.load_base()
    assert len(base) >= 300
    assert all("base" in v.tags for v in base)


def test_every_definition_carries_its_origin_as_a_tag():
    """`origin` is stamped onto `tags` by `_to_variants`, so nothing downstream
    had to learn a new field when the three files became one. 72 of the 408
    definitions came from the overlay rather than from the vendored catalog.

    Both numbers moved together on 2026-08-06 and for one reason: 25 variants
    changed origin from `atlas` to `overlay`. 16 were generated variants that a
    human then read, ground-truthed and repaired, and 9 were written fresh
    alongside them. `overlay` is where hand-owned definitions live, so promotion
    out of the machine-generated set IS the move from one tag to the other.
    """
    defs = corpus.load_all_definitions(CORPUS)
    for v in defs:
        # "atlas" is the third origin, added 2026-08-04: generated variants, one
        # per expressible capability assertion. See corpus.curated.
        assert {"base", "overlay", "atlas"} & set(v.tags), f"{v.id} carries no origin tag"
    assert len([v for v in defs if "overlay" in v.tags]) == 134  # 72 -> 134: 2026-08-06 question set: 58 authored, 6 retired, 76 deselected, 4 promoted out of the atlas set.
    assert len([v for v in defs if "atlas" in v.tags]) == 59  # 63 -> 59: four atlas
    # variants were read, ground-truthed and put in the paid selection, which
    # is an origin AND tag flip. `_atlas` provenance is kept on all 80.


def test_merged_is_exactly_the_active_definitions():
    """The old arithmetic was base + overlay - overrides - retired, because a
    variant could be defined in up to three files at once. There is one
    definition per id now, so the whole of resolution is a status filter."""
    all_defs = corpus.load_all_definitions(CORPUS)
    meta = corpus.variant_meta(CORPUS)
    retired = {vid for vid, m in meta.items() if m["status"] == "retired"}
    merged = corpus.curated(corpus.merged(CORPUS))
    all_defs = corpus.curated(all_defs)
    assert len(merged) == len(all_defs) - len(retired) == 365  # 308 -> 365: 2026-08-06 question set: 58 authored, 6 retired, 76 deselected, 4 promoted out of the atlas set.
    assert len({v.id for v in merged}) == len(merged)  # no duplicate ids
    assert not ({v.id for v in merged} & retired)


def test_the_strengthened_refine_and_recall_cases_assert_outcomes_not_labels():
    """Guard against regressing to mode-label-only assertions.

    Every refine/recall case in the 2026-07-24 run passed while answering from
    the wrong result bundle, because the only criterion was parser_plan.mode. The
    fix was an overlay override of the imported variant; post-migration those are
    just the refine_and_recall definitions whose origin is `overlay`.
    """
    # `refine_and_recall` split on 2026-08-04 into the two parser modes its
    # variants already asserted: ask_about_last_results -> followup_over_results,
    # refine_last_search -> search_refinement. The guard covers both halves.
    refrec = [v for v in corpus.merged(CORPUS)
              if v.family in ("followup_over_results", "search_refinement")
              and "overlay" in v.tags]
    assert refrec, "expected the strengthened refine_and_recall cases to still exist"
    for v in refrec:
        fields = {c.field for turn in v.turns for c in turn.pass_criteria}
        assert fields - {"parser_plan.mode"}, f"{v.id} still only asserts the classifier label"


def test_select_scope_specific_keeps_route_gate():
    merged = corpus.merged(CORPUS)
    specific = corpus.select(merged, scope="specific")
    assert specific and all("route_gate" in v.tags for v in specific)


def test_select_by_family_and_variant():
    merged = corpus.merged(CORPUS)
    fam = corpus.select(merged, family="sample_search")
    assert fam and all(v.family == "sample_search" for v in fam)
    one = corpus.select(merged, variant_id="advanced.basic_ndma")
    assert len(one) == 1 and one[0].id == "advanced.basic_ndma"


# --------------------------------------------------------------------------- #
# T3.5 — the per-family criterion floor.
#
# Of 381 merged variants, 108 asserted plan shape only and 194 asserted only
# plumbing (api_ok / neo4j_ok say a request COMPLETED, not that it returned
# anything). 79% of the corpus could not detect a wrong answer, and hand-editing
# 300 variants is not viable.
# --------------------------------------------------------------------------- #

from pathlib import Path as _P
from e2e.catalog import PassCriterion, Turn, Variant

_CORP = _P(__file__).resolve().parents[1] / "corpus.json"

FLOOR = {
    "exclude_tag": "no_floor",
    "floors": {
        "graph_query": [
            {"field": "neo4j_ok", "op": "true", "value": None},
            {"field": "graph_result.count", "op": "gte", "value": 1},
        ],
    },
}


def _variant(family, *criteria, tags=(), turns=1):
    ts = [Turn(label=f"t{i}", query="q",
               pass_criteria=[PassCriterion(**c) for c in (criteria if i == turns - 1 else [])])
          for i in range(turns)]
    return Variant(family=family, id="v", name="n", tags=list(tags), turns=ts)


def _fields(v, turn=-1):
    return [c.field for c in v.turns[turn].pass_criteria]


def test_the_floor_is_added_to_a_bare_variant():
    v = _variant("graph_query", {"field": "parser_plan.mode", "op": "eq", "value": "graph_query"})
    corpus.apply_family_floor([v], FLOOR)
    assert "graph_result.count" in _fields(v)
    assert "neo4j_ok" in _fields(v)


def test_the_floor_lands_on_the_last_turn_only():
    v = _variant("graph_query", {"field": "parser_plan.mode", "op": "eq", "value": "x"}, turns=3)
    corpus.apply_family_floor([v], FLOOR)
    assert _fields(v, 0) == [] and _fields(v, 1) == []
    assert "graph_result.count" in _fields(v, 2)


def test_an_existing_bound_on_the_same_field_is_not_duplicated_or_contradicted():
    """A deliberate `count lte 25` must not gain a conflicting floor entry."""
    v = _variant("graph_query", {"field": "graph_result.count", "op": "lte", "value": 25})
    corpus.apply_family_floor([v], FLOOR)
    assert _fields(v).count("graph_result.count") == 1
    assert _fields(v)[0] == "graph_result.count"
    assert "neo4j_ok" in _fields(v), "the other floor fields still apply"


def test_no_floor_opts_a_variant_out_entirely():
    """Required wherever ZERO is the correct answer."""
    v = _variant("graph_query", {"field": "parser_plan.mode", "op": "eq", "value": "x"},
                 tags=["no_floor"])
    corpus.apply_family_floor([v], FLOOR)
    assert _fields(v) == ["parser_plan.mode"]


def test_an_unlisted_family_gets_nothing():
    v = _variant("refine_and_recall", {"field": "parser_plan.mode", "op": "eq", "value": "x"})
    corpus.apply_family_floor([v], FLOOR)
    assert _fields(v) == ["parser_plan.mode"]


# -------------------------------------------------- against the real corpus


def test_refine_and_recall_never_gets_a_row_count_floor():
    """api_result_meta.row_count exists ONLY on new_search turns. A row-count floor on
    a refine or recall turn would fail every one of them for a field that is absent
    by design."""
    merged = corpus.merged(_CORP)
    floors = corpus.load_family_floor(_CORP).get("floors", {})

    assert "refine_and_recall" not in floors, "the family must not be floored at all"
    for v in merged:
        if v.family != "refine_and_recall":
            continue
        # A hand-authored row-count bound on a SEED turn is fine — a seed is a
        # new_search. What must never happen is one appearing on the final refine or
        # recall turn, which is where the floor would have put it.
        last = [c.field for c in v.turns[-1].pass_criteria]
        assert "api_result_meta.row_count" not in last, v.id


def test_the_zero_is_correct_cases_are_all_opted_out():
    """Every case whose right answer is zero must carry no_floor, or the floor breaks it."""
    merged = {v.id: v for v in corpus.merged(_CORP)}
    # The three GBM cases that used to be listed here were retired on 2026-07-30
    # (the study does not exist), so tree.missing_uid, which asserts a 404, is the
    # only remaining case in the active corpus whose right answer is nothing.
    for vid in ("tree.missing_uid",):
        assert "no_floor" in merged[vid].tags, f"{vid} would be broken by the floor"


def test_the_floor_actually_moves_the_needle():
    OUTCOME = ("api_result_meta.row_count", "graph_result.count", "graph_result.total",
               "reporter_result", "last_reply", "api_artifact", "bundle",
               "api_result_meta.status_code",
               # The 2026-07-28 floor rewrite renamed what it injects. These assert
               # the outcome was OBSERVED and DISCLOSED rather than asserting its
               # value, which is what made the old floor false-fail correct answers.
               # 2026-08-03 collapsed the two engine-specific `*_outcome_observed`
               # floors into one `outcome_observed`, so the floor asserts that an
               # outcome exists rather than which engine produced it. The engine-
               # specific names stay listed: a case may still assert one by hand.
               "outcome_observed", "api_outcome_observed", "graph_outcome_observed",
               "graph_truncation_disclosed", "report_produced_output")
    merged = corpus.merged(_CORP)
    weak = [v for v in merged
            if not any(c.field.startswith(OUTCOME) for t in v.turns for c in t.pass_criteria)]

    assert len(weak) / len(merged) < 0.35, (
        f"{len(weak)}/{len(merged)} variants still cannot detect a wrong answer"
    )


def test_every_floored_family_exists_in_the_corpus():
    """A floor keyed on a misspelled family silently does nothing."""
    families = {v.family for v in corpus.merged(_CORP)}
    for fam in corpus.load_family_floor(_CORP).get("floors", {}):
        assert fam in families, f"family_floor targets unknown family {fam!r}"


# --------------------------------------------------------------------------- #
# Figures quoted in the docs, recomputed.
#
# Every number below appears as PROSE in README.md, output-skill/SKILL.md or
# output-skill/REFERENCE.md, and two of them had already gone false three commits
# after being written — inside the branch that was written to stop exactly that.
# The rule this section encodes: a number a triager will act on either gets
# recomputed by a test that names the file to update, or it does not get written
# down.
#
# The dangerous ones are the injection counts. SKILL.md and REFERENCE.md both
# used to say routing was asserted "only" on the `route_gate` variants, and that
# "nothing is injected" — so a triager seeing a `route` criterion on any of the
# other 280 cases would conclude it was harness residue and discount a real
# misroute.
# --------------------------------------------------------------------------- #

_DOCS = "nessie_tests/README.md, output-skill/SKILL.md and output-skill/REFERENCE.md"


def _prepolicy():
    """The resolved corpus with retirement and rewrites applied, but no injection.

    Mirrors `corpus.merged_from_unified` up to but NOT including
    `apply_route_policy`. `load_unified` already applies the retirement filter,
    which is the whole of what the old base/overlay/retired merge did here.
    """
    out = corpus.load_unified(CORPUS)
    return corpus.apply_criterion_rewrites(out, corpus.load_criterion_rewrites(CORPUS))


# The 8 families added on 2026-08-06 that have NO `route_policy.families` entry,
# and therefore get no route criterion injected.
#
# This is a GAP, pinned here rather than papered over. `route_policy` is operator-
# settled ("Settled by the operator 2026-07-28: bulk export is CC's job"), and
# guessing an engine for a family nobody has ruled on would turn a free-tier run
# red while asserting something no one decided. It does not affect the paired run
# these families were added for: `--bayesian` FORCES the route and strips route
# criteria on both arms, so a route assertion there is tautological anyway.
#
# To close it, add each family to `route_policy.families` in corpus.json and drop
# it from this set. The set is asserted exactly, so a NEW family cannot join the
# corpus without a route expectation and go unnoticed.
# 8 -> 12. The 2026-08-06 question set covers four more families for the first
# time (cross_session_memory, entity_write, pipeline_output_reingest,
# session_lifecycle) and none of them has a `route_policy.families` entry, because
# route policy is OPERATOR-settled and guessing an engine for a family nobody has
# ruled on would turn the free tier red while asserting something no one decided.
# Pinned as an exact set so a new family cannot join the corpus without a route
# expectation and go unnoticed. It does not affect the paired run: `--bayesian`
# forces the route and strips route criteria on both arms.
_ROUTE_EXPECTATION_UNSETTLED = {
    "artifact_delivery", "batch_upload_preparation", "cc_sandbox_contract",
    "cross_session_memory", "entity_write", "harmonization",
    "pipeline_output_reingest", "retrieval_path_selection", "session_lifecycle",
    "turn_delivery_and_trace", "turn_limits_and_failure", "vocabulary_resolution",
}


def test_every_resolved_variant_carries_a_route_criterion():
    merged = corpus.merged(CORPUS)
    merged = corpus.curated(merged)
    expected = [v for v in merged if v.family not in _ROUTE_EXPECTATION_UNSETTLED]
    with_route = [v for v in expected
                  if any(c.field == "route" for t in v.turns for c in t.pass_criteria)]

    assert len(with_route) == len(expected) == 320, (  # 288 -> 320: 2026-08-06 question set: 58 authored, 6 retired, 76 deselected, 4 promoted out of the atlas set.
        f"{len(with_route)} of {len(expected)} carry a route criterion — update {_DOCS}")

    # The exemption is real and bounded: nothing outside those 8 families may skip
    # a route criterion, and every one of the 8 must actually still lack one --
    # otherwise the exemption has gone stale and is hiding a settled policy.
    unsettled = {v.family for v in merged
                 if not any(c.field == "route" for t in v.turns for c in t.pass_criteria)}
    assert unsettled == _ROUTE_EXPECTATION_UNSETTLED, (
        f"route-criterion gap moved: {sorted(unsettled)} — either a family gained a "
        f"route_policy entry (drop it from the set) or a new one lost its criterion")


def test_only_three_variants_are_route_gate():
    """The number the "asserted only on the route_gate variants" claim rested on."""
    gates = sorted(v.id for v in corpus.merged(CORPUS) if "route_gate" in v.tags)

    assert gates == ["route.ns_advanced", "route.ns_plain_study_membership",
                     "route.unrelated"], f"route_gate set changed — update {_DOCS}"


def test_the_route_policy_injects_the_number_the_docs_quote():
    """273 injected + 15 written inline = 288, the route-carrying set above.

    268 -> 273: 5 of the 25 variants added on 2026-08-06 landed in families that
    already have a `route_policy` entry, so they get an injection like any other.
    The other 20 are in the 8 families with no settled route expectation and are
    exempt — see `_ROUTE_EXPECTATION_UNSETTLED`.
    """
    spec = corpus.load_route_policy(CORPUS)
    families = (spec or {}).get("families") or {}
    overrides = (spec or {}).get("overrides") or {}
    pre = _prepolicy()
    injected = [v for v in pre
                if (overrides.get(v.id) or families.get(v.family)) and v.turns
                and not any(c.field == "route" for c in v.turns[0].pass_criteria)]
    inline = [v for v in pre
              if v.turns and any(c.field == "route" for c in v.turns[0].pass_criteria)]

    injected = corpus.curated(injected)
    inline = corpus.curated(inline)
    assert len(injected) == 305, f"{len(injected)} injected — update {_DOCS}"  # 273 -> 305: 2026-08-06 question set: 58 authored, 6 retired, 76 deselected, 4 promoted out of the atlas set.
    assert len(inline) == 15, f"{len(inline)} inline — update {_DOCS}"


def test_the_family_floor_injects_the_numbers_the_docs_quote():
    """`apply_family_floor` adds a criterion only where the last turn lacks that
    FIELD, so the count is nothing like "every variant in a floored family"."""
    spec = corpus.load_family_floor(CORPUS)
    floors = (spec or {}).get("floors") or {}
    skip_tag = (spec or {}).get("exclude_tag", "no_floor")
    pre = corpus.apply_route_policy(_prepolicy(), corpus.load_route_policy(CORPUS))

    per_field, variants = {}, 0
    for v in corpus.curated(pre):
        floor = floors.get(v.family)
        if not floor or skip_tag in v.tags or not v.turns:
            continue
        already = {c.field for c in v.turns[-1].pass_criteria}
        added = [c["field"] for c in floor if c["field"] not in already]
        if added:
            variants += 1
        for f in added:
            per_field[f] = per_field.get(f, 0) + 1

    # Moved 203 -> 207 with the 28-family remap on 2026-08-04, for three reasons:
    # 11 assay searches left sample_search for graph_traversal (so they now take
    # graph_truncation_disclosed as well, 36 -> 47), and green.* plus routing.*
    # landed in floored families where their old ones had no floor (146 -> 150).
    # 207 -> 210: the family floor reaches 3 of the 25 variants added
    # 2026-08-06; the other 22 are in families the floor does not cover.
    assert variants == 226, f"{variants} variants floored — update {_DOCS}"  # 210 -> 226: 2026-08-06 question set: 58 authored, 6 retired, 76 deselected, 4 promoted out of the atlas set.
    # 2026-08-06: +3 outcome_observed and +1 graph_truncation_disclosed, from the
    # 3 added variants the floor reaches.
    # 153/57/48 -> 168/58/52. 2026-08-06 question set: 58 authored, 6 retired, 76 deselected, 4 promoted out of the atlas set.
    assert per_field == {"outcome_observed": 168, "report_produced_output": 58,
                         "graph_truncation_disclosed": 52}, (
        f"{per_field} — update {_DOCS}")


def test_no_resolved_variant_is_tagged_known_fail():
    """README says so, and says it about `known_fail` specifically — the 283 carry
    plenty of other tags, including the injected route criterion above."""
    assert not [v.id for v in corpus.merged(CORPUS) if "known_fail" in v.tags]


def test_samplesheet_csv_is_asserted_the_number_of_times_the_docs_quote():
    """`resolve_artifact`'s docstring and SKILL.md both cite this to explain why a
    bare artifact label is never resolved against the filesystem."""
    hits = [(v.id, c.field) for v in corpus.merged(CORPUS)
            for t in v.turns for c in t.pass_criteria
            if "samplesheet.csv" in f"{c.field} {c.value}"]

    assert len(hits) == 3, f"{hits} — update evaluate.py:resolve_artifact and SKILL.md"
    assert {vid for vid, _ in hits} == {"pipeline.end_to_end_emit",
                                        "pipeline.happy_path_scrnaseq"}

from pathlib import Path
from nessie_tests import corpus

OVERLAY = Path(__file__).resolve().parents[1] / "overlay.json"


def test_base_loads_and_is_tagged():
    base = corpus.load_base()
    assert len(base) >= 300
    assert all("base" in v.tags for v in base)


def test_overlay_loads_and_is_tagged():
    ov = corpus.load_overlay(OVERLAY)
    assert all("overlay" in v.tags for v in ov)


def test_merged_is_base_plus_overlay_minus_overrides_minus_retired():
    base, ov = corpus.load_base(), corpus.load_overlay(OVERLAY)
    overrides = corpus.overridden_ids(OVERLAY)
    retired = corpus.load_retired_ids()
    merged = corpus.merged(OVERLAY)
    assert len(merged) == len(base) + len(ov) - len(overrides) - len(retired)
    assert len({v.id for v in merged}) == len(merged)  # no duplicate ids


def test_overlay_variant_overrides_the_base_variant_of_the_same_id(monkeypatch):
    """An overlay entry may REPLACE a base entry, keeping its id.

    This is how a weak imported expectation gets strengthened without editing
    the vendored catalog: same case, same id, better criteria.
    """
    from e2e.catalog import Variant, Turn
    base = [
        Variant(family="refine_and_recall", id="refrec.x", name="n", tags=["base"],
                turns=[Turn(label="m", query="q",
                            pass_criteria=[{"field": "parser_plan.mode", "op": "eq", "value": "x"}])]),
        Variant(family="other", id="keep.me", name="n", tags=["base"], turns=[Turn(label="m", query="q")]),
    ]
    strong = Variant(family="refine_and_recall", id="refrec.x", name="n", tags=["overlay"],
                     turns=[Turn(label="m", query="q",
                                 pass_criteria=[{"field": "api_ok", "op": "true"}])])
    monkeypatch.setattr(corpus, "load_base", lambda: base)
    monkeypatch.setattr(corpus, "load_overlay", lambda p: [strong])

    merged = corpus.merged(OVERLAY)
    assert [v.id for v in merged] == ["refrec.x", "keep.me"]  # base ordering preserved
    replaced = merged[0]
    assert replaced.turns[0].pass_criteria[0].field == "api_ok"


def test_overridden_refine_and_recall_cases_assert_outcomes_not_labels():
    """Guard against regressing to mode-label-only assertions.

    Every refine/recall case in the 2026-07-24 run passed while answering from
    the wrong result bundle, because the only criterion was parser_plan.mode.
    """
    merged = {v.id: v for v in corpus.merged(OVERLAY)}
    # An overridden id may since have been retired, in which case it is no longer
    # in the merged corpus and there is nothing here to guard.
    overridden = [merged[i] for i in corpus.overridden_ids(OVERLAY) if i in merged]
    refrec = [v for v in overridden if v.family == "refine_and_recall"]
    assert refrec, "expected the overlay to strengthen the refine_and_recall cases"
    for v in refrec:
        fields = {c.field for turn in v.turns for c in turn.pass_criteria}
        assert fields - {"parser_plan.mode"}, f"{v.id} still only asserts the classifier label"


def test_select_scope_specific_keeps_route_gate():
    merged = corpus.merged(OVERLAY)
    specific = corpus.select(merged, scope="specific")
    assert specific and all("route_gate" in v.tags for v in specific)


def test_select_by_family_and_variant():
    merged = corpus.merged(OVERLAY)
    fam = corpus.select(merged, family="search_advanced")
    assert fam and all(v.family == "search_advanced" for v in fam)
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

_OV = _P(__file__).resolve().parents[1] / "overlay.json"

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


# ------------------------------------------------- against the real overlay


def test_refine_and_recall_never_gets_a_row_count_floor():
    """api_result_meta.row_count exists ONLY on new_search turns. A row-count floor on
    a refine or recall turn would fail every one of them for a field that is absent
    by design."""
    merged = corpus.merged(_OV)
    floors = corpus.load_family_floor(_OV).get("floors", {})

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
    merged = {v.id: v for v in corpus.merged(_OV)}
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
    merged = corpus.merged(_OV)
    weak = [v for v in merged
            if not any(c.field.startswith(OUTCOME) for t in v.turns for c in t.pass_criteria)]

    assert len(weak) / len(merged) < 0.35, (
        f"{len(weak)}/{len(merged)} variants still cannot detect a wrong answer"
    )


def test_every_floored_family_exists_in_the_corpus():
    """A floor keyed on a misspelled family silently does nothing."""
    families = {v.family for v in corpus.merged(_OV)}
    for fam in corpus.load_family_floor(_OV).get("floors", {}):
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
    """The resolved corpus with retirement and rewrites applied, but no injection."""
    raw = corpus.load_overlay(OVERLAY)
    by_id = {v.id: v for v in raw}
    out = [by_id.pop(v.id, v) for v in corpus.load_base()]
    out += [v for v in raw if v.id in by_id]
    retired = corpus.load_retired_ids()
    out = [v for v in out if v.id not in retired]
    return corpus.apply_criterion_rewrites(out, corpus.load_criterion_rewrites(OVERLAY))


def test_every_resolved_variant_carries_a_route_criterion():
    merged = corpus.merged(OVERLAY)
    with_route = [v for v in merged
                  if any(c.field == "route" for t in v.turns for c in t.pass_criteria)]

    assert len(with_route) == len(merged) == 283, (
        f"{len(with_route)} of {len(merged)} carry a route criterion — update {_DOCS}")


def test_only_three_variants_are_route_gate():
    """The number the "asserted only on the route_gate variants" claim rested on."""
    gates = sorted(v.id for v in corpus.merged(OVERLAY) if "route_gate" in v.tags)

    assert gates == ["route.ns_advanced", "route.ns_plain_study_membership",
                     "route.unrelated"], f"route_gate set changed — update {_DOCS}"


def test_the_route_policy_injects_the_number_the_docs_quote():
    """268 injected + 15 written inline = the 283 above."""
    spec = corpus.load_route_policy(OVERLAY)
    families = (spec or {}).get("families") or {}
    overrides = (spec or {}).get("overrides") or {}
    pre = _prepolicy()
    injected = [v for v in pre
                if (overrides.get(v.id) or families.get(v.family)) and v.turns
                and not any(c.field == "route" for c in v.turns[0].pass_criteria)]
    inline = [v for v in pre
              if v.turns and any(c.field == "route" for c in v.turns[0].pass_criteria)]

    assert len(injected) == 268, f"{len(injected)} injected — update {_DOCS}"
    assert len(inline) == 15, f"{len(inline)} inline — update {_DOCS}"


def test_the_family_floor_injects_the_numbers_the_docs_quote():
    """`apply_family_floor` adds a criterion only where the last turn lacks that
    FIELD, so the count is nothing like "every variant in a floored family"."""
    spec = corpus.load_family_floor(OVERLAY)
    floors = (spec or {}).get("floors") or {}
    skip_tag = (spec or {}).get("exclude_tag", "no_floor")
    pre = corpus.apply_route_policy(_prepolicy(), corpus.load_route_policy(OVERLAY))

    per_field, variants = {}, 0
    for v in pre:
        floor = floors.get(v.family)
        if not floor or skip_tag in v.tags or not v.turns:
            continue
        already = {c.field for c in v.turns[-1].pass_criteria}
        added = [c["field"] for c in floor if c["field"] not in already]
        if added:
            variants += 1
        for f in added:
            per_field[f] = per_field.get(f, 0) + 1

    assert variants == 203, f"{variants} variants floored — update {_DOCS}"
    assert per_field == {"outcome_observed": 146, "report_produced_output": 57,
                         "graph_truncation_disclosed": 36}, (
        f"{per_field} — update {_DOCS}")


def test_no_resolved_variant_is_tagged_known_fail():
    """README says so, and says it about `known_fail` specifically — the 283 carry
    plenty of other tags, including the injected route criterion above."""
    assert not [v.id for v in corpus.merged(OVERLAY) if "known_fail" in v.tags]


def test_samplesheet_csv_is_asserted_the_number_of_times_the_docs_quote():
    """`resolve_artifact`'s docstring and SKILL.md both cite this to explain why a
    bare artifact label is never resolved against the filesystem."""
    hits = [(v.id, c.field) for v in corpus.merged(OVERLAY)
            for t in v.turns for c in t.pass_criteria
            if "samplesheet.csv" in f"{c.field} {c.value}"]

    assert len(hits) == 3, f"{hits} — update evaluate.py:resolve_artifact and SKILL.md"
    assert {vid for vid, _ in hits} == {"pipeline.end_to_end_emit",
                                        "pipeline.happy_path_scrnaseq"}

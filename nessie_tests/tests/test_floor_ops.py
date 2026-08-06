"""Ops the family floor needs so a minimum assertion cannot false-fail a correct answer.

The 2026-07-28 run failed 10 of 15 cases on criteria this harness added, not on
product behaviour. Three floor assertions were wrong as universals:

- ``graph_result.count gte 1`` demands rows from questions whose correct answer is
  zero. 14 GBM graph variants and 3 search variants have zero as ground truth.
- ``graph_not_truncated true`` can never hold for a question with more than 5,000
  legitimate rows, and one such question (10,688) is in the corpus.
- ``reporter_result.ok true`` is produced only by ``run_reporter_summary``; the 24
  SRA/GEO/PRIDE generation variants go through ``generate_report_outputs`` and can
  never satisfy it.

The fix is to assert what the floor is actually for: that the outcome was OBSERVED,
that truncation was DISCLOSED, and that a report produced one of its two possible
result shapes.

2026-08-03 adds a fourth: the floor must not mandate an ENGINE either. See the
``outcome_observed`` section below.
"""
import ast
import json
import pathlib
import re

import pytest

from nessie_tests import evaluate
from nessie_tests.route_observer import RouteObservation

OBS = RouteObservation("nextseek_query", None, "baml", "", "graph_query", "graph_query")


def _payload(debug):
    return {"status": "completed", "progress": [
        {"event": "route_decided",
         "data": {"route": "nextseek_query", "source": "baml", "reasoning": ""}},
        {"event": "query_complete", "data": {"reply": "ok", "debug": debug}},
    ]}


# ── `*_outcome_observed`: the outcome was SEEN, whatever its value ────────────
#
# These are computed booleans rather than a new criterion op because PassCriterion
# in the vendored e2e/catalog.py pins `op` to a Literal, so a new op cannot be named
# from the corpus at all. A computed field asserted with the existing `true` op gets
# the same semantics with zero vendored edits.
#
# `gte 1` was the wrong assertion (an honest zero is an answer, not a failure) and so
# is `nonempty` (it is bool(actual), which rejects 0 too). The distinction the floor
# exists to make is "the answer is zero" versus "the criterion is blind".

def _observed(debug, key):
    return evaluate.augment_debug(debug, OBS)[key]


def test_graph_outcome_observed_on_an_honest_zero():
    """A count of 0 is an ANSWER. 14 GBM graph variants have zero as ground truth."""
    assert _observed({"graph_result": {"count": 0, "total": 0}}, "graph_outcome_observed") is True


def test_graph_outcome_observed_on_a_normal_result():
    assert _observed({"graph_result": {"count": 705}}, "graph_outcome_observed") is True


def test_graph_outcome_not_observed_when_the_query_produced_nothing():
    """No count at all means the criterion saw nothing and must not report success."""
    assert _observed({"graph_result": {}}, "graph_outcome_observed") is False


def test_api_outcome_observed_on_an_honest_zero():
    assert _observed({"api_result_meta": {"row_count": 0}}, "api_outcome_observed") is True


def test_api_outcome_observed_on_a_recalled_bundle_followup():
    """`retrieve.then_inspect` ends on a follow-up that answers from a recalled
    bundle, so it legitimately has no row_count of its own. It is the only
    multi-turn variant in any floored family, and the floor lands on the LAST turn."""
    assert _observed(
        {"api_result_meta": {"ok": True, "source_mode": "new_search"}},
        "api_outcome_observed") is True


def test_api_outcome_not_observed_when_the_call_reported_neither():
    assert _observed({"api_result_meta": {"ok": True}}, "api_outcome_observed") is False


def test_api_outcome_not_observed_when_there_was_no_call():
    assert _observed({}, "api_outcome_observed") is False


# ── `graph_truncation_disclosed`: a cap is fine, a HIDDEN cap is not ──────────
#
# `graph_not_truncated` asserts a cap never happened, which is unsatisfiable for any
# question with more than 5,000 legitimate rows. `graph.tissue_cell_impact` has
# 10,688 and can never pass it. What actually matters is that a capped result SAYS
# so and reports the real total, which is exactly what the 2026-07-28 total probe
# made possible.

def _disclosed(graph):
    return evaluate.augment_debug({"graph_result": graph}, OBS)["graph_truncation_disclosed"]


def test_complete_result_is_disclosed():
    assert _disclosed({"count": 705, "total": 705, "truncated": False}) is True


def test_capped_result_reporting_its_real_total_is_disclosed():
    """The 10,688-of-5,000 case. Truncated, honest, and must pass the floor."""
    assert _disclosed({"count": 5000, "total": 10688, "truncated": True}) is True


def test_capped_result_hiding_its_total_is_not_disclosed():
    """The 2026-07-27 silent 5,000 cap: truncated with no total to compare against."""
    assert _disclosed({"count": 5000, "truncated": True}) is False


def test_capped_result_claiming_total_equals_count_is_not_disclosed():
    """`truncated` and `total == count` contradict each other; trust neither."""
    assert _disclosed({"count": 5000, "total": 5000, "truncated": True}) is False


def test_non_graph_turn_is_disclosed_so_the_criterion_is_inert_on_rest():
    assert _disclosed({}) is True


# ── `report_produced_output`: reporting has TWO result shapes, not one ────────
#
# orchestrator.py routes reporter_mode == "report_generation" to
# generate_report_outputs (returns saved_files) and everything else to
# run_reporter_summary (returns reporter_result.ok). The floor asserted only the
# second, so all 24 SRA/GEO/PRIDE generation variants carry a criterion their code
# path cannot satisfy. Assert the disjunction: the turn produced ONE of the two.

def _produced(debug):
    return evaluate.augment_debug(debug, OBS)["report_produced_output"]


def test_summary_report_that_ran_produced_output():
    assert _produced({"reporter_result": {"ok": True, "rows_returned": 149}}) is True


def test_generation_report_that_wrote_files_produced_output():
    """SRA/GEO/PRIDE: reporter_result carries no `ok`, saved_files is the evidence."""
    assert _produced({
        "reporter_result": {"reports": [{"uid": "D.SEQ-230512FOR-288-PUB"}]},
        "report_saved_files": {"sra_submission": "/app/outputs/x/files/sra.xlsx"},
    }) is True


def test_reporter_that_produced_neither_is_a_failure():
    """The 2026-07-27 masked pass: ValueError('Unknown project SRP'), no files."""
    assert _produced({"reporter_result": {"ok": False, "error": "Unknown project 'SRP'"}}) is False


def test_reporter_with_no_result_at_all_is_a_failure():
    assert _produced({"reporter_result": {}, "report_saved_files": {}}) is False


# ── `outcome_observed`: the floor asserts an OUTCOME, not a particular ENGINE ─
#
# `apply_family_floor` keys on `v.family`, and the family names are ENGINE-shaped:
# `search_advanced` means "the REST advanced_search endpoint", `graph_query` means
# "Cypher". But the parser is free to answer the same question with either engine,
# and in the 2026-08-03 seed-6 run three `search_advanced` cases routed NS
# correctly, answered correctly via the graph, and went red anyway — on `api_ok`
# and `api_outcome_observed`, both of which are False on a graph turn by
# construction. The operator's note on all three was "this was correct".
#
# `apply_family_floor` runs at corpus-BUILD time and appends static criteria, so it
# cannot know which engine will run. The seam has to be evaluation time. Hence one
# derived boolean that is the DISJUNCTION of the three engine-specific ones.
#
# The engine-specific booleans stay: the floor stops MANDATING an engine, it does
# not stop a case that genuinely needs one from asserting it by hand.

def _outcome(debug):
    return _observed(debug, "outcome_observed")


def test_outcome_observed_when_the_graph_answered():
    """The seed-6 shape: a `search_advanced` case the parser sent to Cypher."""
    assert _outcome({"graph_result": {"count": 408, "total": 408}}) is True


def test_outcome_observed_when_the_rest_call_answered():
    assert _outcome({"api_result_meta": {"row_count": 705}}) is True


def test_outcome_observed_when_a_recalled_bundle_answered():
    assert _outcome({"api_result_meta": {"source_mode": "recall"}}) is True


def test_outcome_observed_when_a_report_produced_output():
    assert _outcome({"reporter_result": {"ok": True}}) is True
    assert _outcome({"report_saved_files": {"sra_submission": "/app/outputs/x.xlsx"}}) is True


def test_outcome_observed_on_an_honest_zero_from_either_engine():
    """Zero is an ANSWER. Widening the floor must not smuggle back `gte 1`."""
    assert _outcome({"graph_result": {"count": 0, "total": 0}}) is True
    assert _outcome({"api_result_meta": {"row_count": 0}}) is True


# ── THE VACUITY GUARD ─────────────────────────────────────────────────────────
#
# This change WEAKENS the floor, so the test that it did not weaken into nothing is
# the actual deliverable. A disjunction of three booleans is one edit away from
# being true unconditionally, and a floor that is always true is worse than no
# floor: it reports green.

def test_a_turn_that_produced_no_outcome_at_all_still_fails_the_floor():
    assert _outcome({}) is False


def test_plumbing_that_merely_completed_is_not_an_outcome():
    """`api_ok` / `neo4j_ok` say a request COMPLETED, not that it returned anything.
    That distinction is the entire reason the floor exists; it must survive the
    widening."""
    assert _outcome({"api_result_meta": {"ok": True}, "graph_result": {"ok": True}}) is False


def test_a_reporter_that_produced_neither_shape_is_not_an_outcome():
    assert _outcome({"reporter_result": {"ok": False, "error": "Unknown project 'SRP'"},
                     "report_saved_files": {}}) is False


def test_the_seed6_bedrock_outage_turn_still_fails_the_floor():
    """`graph.what_mice_are_in_the_impact_st` in the 2026-08-03 run: the provider
    chain gave up before the parser ran, so there is no plan, no result and no
    reply but an error string. Nothing about widening the floor may make that green
    — it is scored `error` by the outage detector, and it must ALSO still fail on
    the merits."""
    assert _outcome({"graph_result": {}, "api_result_meta": None}) is False


def test_outcome_observed_is_exactly_the_disjunction_of_the_three():
    """Exhaustive over all eight combinations. Pins the semantics rather than one
    example of it, so a future edit that ORs in a fourth, laxer signal fails here."""
    import itertools
    on = {
        "graph": {"graph_result": {"count": 3}},
        "api": {"api_result_meta": {"row_count": 3}},
        "report": {"reporter_result": {"ok": True}},
    }
    for combo in itertools.product((False, True), repeat=3):
        debug = {}
        for flag, key in zip(combo, ("graph", "api", "report")):
            if flag:
                debug.update(on[key])
        computed = evaluate.augment_debug(debug, OBS)
        expected = (computed["graph_outcome_observed"]
                    or computed["api_outcome_observed"]
                    or computed["report_produced_output"])
        assert computed["outcome_observed"] is expected, combo
        assert computed["outcome_observed"] is any(combo), combo


def test_the_engine_specific_booleans_are_still_available_to_assert_by_hand():
    """A case that genuinely must use one engine still can. Removing these would
    turn "the floor no longer mandates an engine" into "nobody can assert one"."""
    computed = evaluate.augment_debug({"graph_result": {"count": 1}}, OBS)
    assert computed["graph_outcome_observed"] is True
    assert computed["api_outcome_observed"] is False
    assert computed["report_produced_output"] is False


# ── the floor spec must actually USE them, measured on the real corpus ────────

from nessie_tests import corpus  # noqa: E402

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "corpus.json"

# Families where the parser may legitimately pick either engine. `reporting` is NOT
# one of them: a report that produced no output is a real failure whatever ran.
# Post-2026-08-04 names. search_tree and search_parents_by_child merged into
# lineage_tree; graph_query became graph_traversal; reporting split, and both
# halves keep report_produced_output.
ENGINE_FLEXIBLE = ("sample_search", "sample_retrieve", "lineage_tree",
                   "graph_traversal")


def _floor_criteria(variant_id, merged):
    v = next(v for v in merged if v.id == variant_id)
    return {(c.field, c.op) for t in v.turns for c in t.pass_criteria}


def _floor_fields():
    floors = corpus.load_family_floor(CORPUS).get("floors", {})
    return [(fam, c) for fam, crits in floors.items() for c in crits]


def test_the_floor_never_demands_a_nonzero_answer():
    """`gte 1` on a count is unsatisfiable wherever zero is the ground truth, and
    the floor applies structurally to every variant in a family. It put that
    criterion on 163 variants, including 14 GBM graph variants and one literally
    named zero_result_zebrafish.

    Scoped to the floor SPEC on purpose: a hand-authored `gte 1` on a case whose
    ground truth really is nonzero (green.mus_ndma = 195) is correct and must stay.
    """
    bad = [(fam, c) for fam, c in _floor_fields() if c["op"] in ("gte", "lte")]
    assert bad == [], f"the floor still asserts a VALUE, not observability: {bad}"


def test_the_floor_never_demands_absence_of_truncation():
    """10,688 rows against a 5,000 cap can never satisfy `graph_not_truncated`."""
    bad = [(fam, c) for fam, c in _floor_fields() if c["field"] == "graph_not_truncated"]
    assert bad == [], f"the floor still asserts absence of truncation: {bad}"


def test_the_floor_never_demands_a_summary_only_field_of_generation_reports():
    """`reporter_result.ok` is set by run_reporter_summary only; the 24 SRA/GEO/PRIDE
    variants go through generate_report_outputs and can never satisfy it."""
    bad = [(fam, c) for fam, c in _floor_fields() if c["field"] == "reporter_result.ok"]
    assert bad == [], f"the floor still asserts reporter_result.ok: {bad}"


def test_the_computed_floor_fields_are_all_populated_by_augment_debug():
    """A floor keyed on a field nothing populates fails every variant silently.

    Only covers the fields this harness COMPUTES; `api_ok` / `neo4j_ok` come from
    the live turn's own debug payload via resolve_field, not from augment_debug.
    """
    computed = evaluate.augment_debug({}, OBS)
    for field in ("outcome_observed", "api_outcome_observed", "graph_outcome_observed",
                  "graph_truncation_disclosed", "report_produced_output"):
        assert field in computed, f"augment_debug no longer sets {field}"

    floor_computed = {c["field"] for _, c in _floor_fields()
                      if c["field"].endswith(("_observed", "_disclosed", "_output"))}
    assert floor_computed <= set(computed), (
        f"the floor asserts computed fields augment_debug never sets: "
        f"{floor_computed - set(computed)}")


def test_tissue_cell_impact_asserts_its_real_total_not_absence_of_truncation():
    """The only hand-written criterion left that no correct answer can satisfy.

    Ground truth is 10,688 rows against a 5,000 cap, so this result is truncated and
    always will be. The total probe added this wave turned that total into a real,
    checkable number, so assert THAT instead. The floor's graph_truncation_disclosed
    still covers the case that actually matters: a cap that hides its total.
    """
    v = next(v for v in corpus.merged(CORPUS) if v.id == "graph.tissue_cell_impact")
    crits = {(c.field, c.op, c.value) for t in v.turns for c in t.pass_criteria}
    assert ("graph_not_truncated", "true", None) not in crits
    assert ("graph_result.total", "gte", 10000) in crits, sorted(crits)


def test_the_floor_still_asserts_something_on_every_family_it_covers():
    """Loosening must not become removing: each floored family keeps an outcome
    assertion, just one its correct answers can satisfy."""
    merged = corpus.merged(CORPUS)
    expected = {
        "graph_traversal": {"outcome_observed", "graph_truncation_disclosed"},
        "sample_search": {"outcome_observed"},
        "sample_retrieve": {"outcome_observed"},
        "lineage_tree": {"outcome_observed"},
        "project_summary_report": {"report_produced_output"},
        "submission_package": {"report_produced_output"},
    }
    assert set(expected) == set(corpus.load_family_floor(CORPUS).get("floors", {})), (
        "a family gained or lost a floor without this pin being updated")
    for family, must_have in expected.items():
        sample = [v for v in merged if v.family == family and "no_floor" not in v.tags]
        assert sample, f"no un-tagged variants in {family}"
        fields = {c.field for t in sample[0].turns for c in t.pass_criteria}
        assert must_have & fields, f"{family} lost its floor: {sample[0].id} has {sorted(fields)}"


def test_the_floor_never_mandates_a_particular_engine():
    """The whole point of the 2026-08-03 change.

    `api_ok` resolves from `api_result_meta.ok` and `neo4j_ok` from
    `graph_result.ok`, so on a turn the parser answered with the OTHER engine each
    is False no matter how right the answer was. As a floor entry on an
    engine-flexible family, either one is an engine mandate wearing a plumbing
    check's clothes — which is precisely how three correct seed-6 answers went red.

    `reporting` is deliberately out of scope: it keeps `report_produced_output`.
    """
    floors = corpus.load_family_floor(CORPUS).get("floors", {})
    bad = [(fam, c["field"]) for fam in ENGINE_FLEXIBLE
           for c in floors.get(fam, []) if c["field"] in ("api_ok", "neo4j_ok")]
    assert bad == [], f"the floor still mandates an engine: {bad}"


def test_every_engine_flexible_family_floors_on_outcome_observed():
    floors = corpus.load_family_floor(CORPUS).get("floors", {})
    for fam in ENGINE_FLEXIBLE:
        fields = {c["field"] for c in floors.get(fam, [])}
        assert "outcome_observed" in fields, f"{fam} floors on {sorted(fields)}"


def test_graph_query_keeps_its_truncation_disclosure_floor():
    """`graph_truncation_disclosed` is NOT engine-mandating — it returns True for a
    non-graph turn by design, so it stays inert on a REST-answered turn — and it
    catches a hidden cap, which is a real defect class. It stays."""
    floors = corpus.load_family_floor(CORPUS).get("floors", {})
    fields = {c["field"] for c in floors.get("graph_traversal", [])}
    assert "graph_truncation_disclosed" in fields, fields


# ── how the floor lands on a real variant, and how it does not ────────────────


def _inline_fields(variant_id):
    """Fields the CASE asserts itself on its LAST turn, before any floor is applied.

    One thing this has to mirror exactly, or `_floor_added` lies:
    `apply_family_floor` decides from `v.turns[-1]` ONLY, so a field asserted on
    an EARLIER turn does not suppress the floor entry. `tree.then_ask_about` is
    exactly that shape — `api_ok` on turn 1, floor on turn 2 — and collecting
    across all turns would have under-reported it.

    It also had to mirror the base/overlay override rule until 2026-08-04. There
    is one definition per id now, so the case's own text is simply its definition.
    """
    src = {v.id: v for v in corpus.load_all_definitions(CORPUS)}[variant_id]
    return {c.field for c in src.turns[-1].pass_criteria} if src.turns else set()


def _floor_added(variant):
    """The criteria the FLOOR put on this variant's last turn."""
    floors = corpus.load_family_floor(CORPUS).get("floors", {})
    floor_fields = {c["field"] for c in floors.get(variant.family, [])}
    inline = _inline_fields(variant.id)
    return [c for c in variant.turns[-1].pass_criteria
            if c.field in floor_fields and c.field not in inline]


def _evaluate(criteria, debug, *, last_reply="ok"):
    passed, results, _ = evaluate.evaluate_turn(
        _payload(debug), criteria, OBS, last_reply=last_reply)
    return passed, {r["field"]: r["passed"] for r in results}


# ── what the 2026-08-03 change actually cost, measured over the whole corpus ──
#
# The floor spec named `api_ok` / `neo4j_ok` on five families, but a floor entry is
# a NO-OP wherever the case already asserts that field on its last turn, so the
# spec diff massively overstates the change. Measured, dropping them removed a
# criterion from FOUR variants and nothing else.
#
# This is pinned rather than described because it is the only place the real blast
# radius is written down, and because a future floor edit that changes it should
# have to say so out loud.

_CURATED_IDS = {v.id for v in corpus.curated(corpus.merged(CORPUS))}

# The 25 variants added on 2026-08-06 to extend the already-graded set, identified
# by the provenance markers the pass stamped on every one of them.
#
# Excluded from the measurements in this file for exactly the reason the atlas set
# is (see `_CURATED_IDS`'s use below): each one is evidence about the floor as it
# met the corpus somebody had already written. Folding in variants added afterwards
# would leave every assertion passing while quietly making it evidence for a
# different corpus. The additions are covered by the corpus-level tests instead.
_ADDED_2026_08_06 = {
    v["id"]
    for fam in json.loads(CORPUS.read_text(encoding="utf-8"))["families"].values()
    for v in fam["variants"]
    if "_promoted_2026_08_06" in v or "_added_2026_08_06" in v
}

RETIRED_FLOOR = {
    "sample_search": ["api_ok", "api_outcome_observed"],
    "sample_retrieve": ["api_ok", "api_outcome_observed"],
    "lineage_tree": ["api_ok", "api_outcome_observed"],
    "graph_traversal": ["neo4j_ok", "graph_outcome_observed", "graph_truncation_disclosed"],
    "project_summary_report": ["report_produced_output"],
    "submission_package": ["report_produced_output"],
}

# The complete set of variants the retired floor entries would inject into TODAY.
# Everywhere else the entry is inert because the case asserts the field itself.
#
# The first four are the original measurement: those are the variants that lost a
# criterion when api_ok/neo4j_ok left the floor. The last two joined the set the
# moment their overlay OVERRIDE landed later the same day — dropping the inline
# `api_ok` is what took them out of the "asserts it itself" bucket, so a floor
# still carrying api_ok would now reach them. They did not lose anything from the
# floor change; they lost it from the override, which is the point of listing them
# separately rather than quietly widening the set.
LOST_API_OK = {
    "advanced.find_me_nhp_samples_from_study_2",
    "retrieve.mixed_valid_invalid",
    "pbct.no_match",
    "tree.then_ask_about",
    "advanced.find_me_d_seq_samples_in_proje",
} | {
    # Gained on 2026-08-04. `nessie_green` and `routing_lab` had no floor of their
    # own; the remap filed these three into `sample_search`, which floors.
    "green.global_count",
    "routing.lab_ooc_kamm_casual",
    "routing.lab_ooc_kamm_count",
}

# `neo4j_ok` was injected into ZERO variants when this was first measured, because
# every `graph_query` member asserted it inline. The 2026-08-04 remap moved 11
# assay-phrased searches out of `search_advanced` into `graph_traversal` -- assay
# lives only on DERIVED_FROM, so REST cannot filter on it -- and none of the 11
# asserts `neo4j_ok` in its own text. `advanced.find_me_sequencing_files_assoc`
# moved with them, which is why it left LOST_API_OK above.
#
# This measures a RETIRED floor, so the shift changes no live assertion. It is
# pinned because the set is the evidence for "the swap added, it did not trade".
LOST_NEO4J_OK = {
    "advanced.find_mass_spectrometry_data_of",
    "advanced.find_me_cd8_antibodies_associa",
    "advanced.find_me_fibrin_imaging_data",
    "advanced.find_me_gpt_assay_data",
    "advanced.find_me_sequencing_files_assoc",
    "advanced.find_tissues_that_underwent_sh",
    "advanced.nextseq_instrument",
    "advanced.show_me_analyzed_sequencing_da",
    "advanced.show_me_flow_cytometry_data_wi",
    "advanced.show_me_tis_samples_that_have",
    "advanced.what_proteomics_data_exists_in",
}


def _pre_floor_corpus():
    """`corpus.merged` up to but NOT including `apply_family_floor`.

    Mirrors `corpus.merged_from_unified`. The equality assertion in
    `test_the_retired_floor_entries_were_inert_almost_everywhere` re-derives the
    real corpus from this, so the mirror cannot silently drift out of step.
    """
    out = corpus.load_unified(CORPUS)
    out = corpus.apply_criterion_rewrites(out, corpus.load_criterion_rewrites(CORPUS))
    return corpus.apply_route_policy(out, corpus.load_route_policy(CORPUS))


def _floor_added_under(spec_fields):
    """{variant id: fields this floor spec would ADD to its last turn}."""
    spec = {"exclude_tag": "no_floor",
            "floors": {fam: [{"field": f, "op": "true", "value": None} for f in fields]
                       for fam, fields in spec_fields.items()}}
    variants = _pre_floor_corpus()
    before = {v.id: {c.field for c in v.turns[-1].pass_criteria} if v.turns else set()
              for v in variants}
    after = corpus.apply_family_floor(variants, spec)
    return {v.id: {c.field for c in v.turns[-1].pass_criteria} - before[v.id]
            for v in after if v.turns}


def test_the_retired_floor_entries_were_inert_almost_everywhere():
    """`neo4j_ok` was floor-added to ZERO variants and `api_ok` to exactly four.

    Both counts moved on 2026-08-04 with the 28-family remap, for one reason: 11
    assay-phrased searches left `search_advanced` for `graph_traversal`. See
    LOST_NEO4J_OK. The argument is unchanged -- the swap added criteria rather
    than trading them away -- only the variants it lands on.

    Every other variant in those five families already asserted the field in its own
    text, where `apply_family_floor` leaves it alone. So removing them from the floor
    is not the broad loosening the spec diff suggests — it removed one criterion from
    four cases.
    """
    # Curated only: the retired floor is a historical measurement over the corpus
    # somebody wrote, and 79 atlas variants landing in floored families would
    # rewrite the evidence set without changing what it is evidence for.
    added = _floor_added_under(RETIRED_FLOOR)
    added = {vid: f for vid, f in added.items()
             if vid in _CURATED_IDS and vid not in _ADDED_2026_08_06}
    assert {vid for vid, f in added.items() if "neo4j_ok" in f} == LOST_NEO4J_OK
    assert {vid for vid, f in added.items() if "api_ok" in f} == LOST_API_OK

    # The mirror above must reproduce the real corpus, or none of this is measuring
    # the corpus the harness actually runs.
    rebuilt = corpus.apply_family_floor(_pre_floor_corpus(),
                                        corpus.load_family_floor(CORPUS))
    real = {v.id: sorted((c.field, c.op) for t in v.turns for c in t.pass_criteria)
            for v in corpus.merged(CORPUS)}
    assert {v.id: sorted((c.field, c.op) for t in v.turns for c in t.pass_criteria)
            for v in rebuilt} == real


def test_search_tree_got_stricter_not_looser_on_all_but_one_variant():
    """`search_tree` floored on `api_ok` ALONE, so it is the one family where the
    swap could have traded a criterion away rather than added one. It did not,
    almost everywhere: 12 of its 13 floored variants assert `api_ok` inline on the
    last turn, keep it, and NEWLY gain `outcome_observed`.

    `tree.then_ask_about` is the single genuine trade — its `api_ok` sits on turn 1,
    not the floored last turn, so the old floor did inject there and now injects
    `outcome_observed` instead. It was not in the seed-6 run, so unlike the rest of
    this change that one swap has no stored evidence behind it.
    """
    merged = {v.id: v for v in corpus.merged(CORPUS)}
    # `search_tree` merged into `lineage_tree` with `search_parents_by_child` on
    # 2026-08-04. The 13 measured here are the tree.* half; the pbct.* half floored
    # on api_ok + api_outcome_observed and never carried the trade risk.
    floored = [v for v in merged.values()
               if v.family == "lineage_tree" and v.id.startswith("tree.")
               and "no_floor" not in v.tags and "atlas" not in v.tags
               and v.id not in _ADDED_2026_08_06]
    assert len(floored) == 13, [v.id for v in floored]

    traded = {v.id for v in floored if "api_ok" not in _inline_fields(v.id)}
    assert traded == {"tree.then_ask_about"}

    for v in floored:
        fields = {c.field for c in v.turns[-1].pass_criteria}
        assert "outcome_observed" in fields, v.id
        if v.id not in traded:
            assert "api_ok" in fields, f"{v.id} lost its inline api_ok"


def test_a_search_advanced_case_answered_by_the_graph_satisfies_its_floor():
    """`advanced.find_me_nhp_samples_from_study_2` is the seed-6 case whose ONLY
    failures were the two floor entries. With the floor engine-agnostic, a graph
    answer satisfies it."""
    v = next(v for v in corpus.merged(CORPUS) if v.id == "advanced.find_me_nhp_samples_from_study_2")
    floor = _floor_added(v)
    assert {c.field for c in floor} == {"outcome_observed"}, [c.field for c in floor]
    passed, _ = _evaluate(floor, {"graph_result": {"count": 408, "total": 408,
                                                   "truncated": False}})
    assert passed


def test_a_search_advanced_case_that_produced_nothing_still_fails_its_floor():
    """The same variant, same floor, an empty turn. If this passes, the floor is
    decorative."""
    v = next(v for v in corpus.merged(CORPUS) if v.id == "advanced.find_me_nhp_samples_from_study_2")
    passed, _ = _evaluate(_floor_added(v), {})
    assert not passed


def test_a_graph_query_case_answered_by_rest_satisfies_its_floor():
    """The mirror image, and it is not hypothetical:
    `graph.what_investigations_exist_in_t` answered from
    /nextseek_api/investigations/ in the seed-6 run and went red on `neo4j_ok`.

    Scoped to the FLOOR, and that case is NOT fixed by this change: its `neo4j_ok`,
    `parser_plan.mode` and `graph_cypher` are all inline, all still red in the
    manifest, and it still fails overall on its inline `neo4j_ok`. What this test
    shows is only that the floor no longer piles a second, engine-shaped failure on
    top of them."""
    v = next(v for v in corpus.merged(CORPUS) if v.id == "graph.what_investigations_exist_in_t")
    floor = _floor_added(v)
    assert {c.field for c in floor} == {"outcome_observed", "graph_truncation_disclosed"}
    # `graph_truncation_disclosed` is True on a turn with no graph_result at all,
    # which is exactly why it is safe to keep on an engine-flexible family.
    passed, per_field = _evaluate(floor, {"api_result_meta": {"row_count": 7}})
    assert passed, per_field


# ── replay against the STORED 2026-08-03 seed-6 run ───────────────────────────
#
# The three cases this change exists for. Replaying them settles what the fix does
# and — just as importantly — what it does NOT do.
#
# All three routed `nextseek_query` correctly, all three were answered by the graph
# engine (1,765 / 408 / 1,858 rows), and the operator's note on each was that the
# answer was correct. All three went red.
#
# `advanced.find_me_nhp_samples_from_study_2` goes green from the FLOOR change
# alone. Its overlay override REPLACES the base variant, and that override asserts
# route, graph_not_truncated and last_reply only — so BOTH criteria it failed on
# (`api_ok`, `api_outcome_observed`) came from the floor, and both are gone.
#
# The other two could NOT be reached by any floor change, and the version of this
# block written when the floor went engine-agnostic said so: they were BASE
# variants asserting `api_ok true`, `parser_plan.mode eq new_search` and
# `api_plan.endpoint contains advanced_search` in their own text, and
# `apply_family_floor` skips a floor entry whose field the case already asserts.
# They satisfied the floor and still failed.
#
# That is no longer where they stand. Later the same day both got an overlay
# override of their own (the "2026-08-03 (b)" block at the end of this file), which
# is what drops the three inline mandates — so all three cases are now green, but
# for two different reasons, and the tests below keep those reasons apart:
# `SEED6_FLOOR_ONLY` is fixed by the floor, `OVERRIDDEN_2026_08_03B` needed the
# case rewritten. Collapsing them into one "all green" claim would lose the fact
# that the floor change on its own was not enough.

_EVIDENCE = pathlib.Path("/home/cdemu/nessie-run-seed6b")
_MANIFEST = _EVIDENCE / "manifest.json"
_TURNS = _EVIDENCE / "turns.json"

requires_seed6b = pytest.mark.skipif(
    not (_MANIFEST.exists() and _TURNS.exists()),
    reason="stored 2026-08-03 seed-6 run evidence is not on this host")

# The one case of the three the floor change fixed by itself.
SEED6_FLOOR_ONLY = "advanced.find_me_nhp_samples_from_study_2"

# id -> the criteria it asserts IN ITS OWN TEXT that were red in that run, under
# the corpus as it resolves TODAY.
#
# All three are empty, and two of them only became empty when their override
# landed. What they were red on before that is not lost: it is
# `REST_MANDATES` at the end of this file, and
# `test_the_base_catalog_still_writes_the_rest_mandates_this_override_removes`
# holds it against the vendored catalog rather than against this comment.
SEED6_GRAPH_ANSWERED = {
    "advanced.find_me_sequencing_files_assoc": set(),
    "advanced.find_me_d_seq_samples_in_proje": set(),
    "advanced.find_me_nhp_samples_from_study_2": set(),
}


def _seed6_entry(vid):
    entries = json.loads(_MANIFEST.read_text(encoding="utf-8"))["entries"]
    return next(e for e in entries if e["id"] == vid)


def _reply_prefix(reply):
    """The leading ASCII run of a manifest-recorded reply.

    The manifest trims a long reply with a literal ``…[trimmed]``, and turns.json
    stores the same replies with their em dash mangled to U+FFFD, so neither the
    full string nor a fixed-length slice compares equal across the two files.
    Stopping at the first non-ASCII character sidesteps both; the rstrip drops the
    space the trim marker is preceded by, which is otherwise the one character that
    does not match.
    """
    ascii_run = ""
    for ch in reply:
        if not ch.isascii():
            break
        ascii_run += ch
    ascii_run = ascii_run.rstrip()
    assert len(ascii_run) >= 24, f"reply prefix too short to disambiguate: {ascii_run!r}"
    return ascii_run


def _seed6_debug(entry, variant):
    """The turn's observed debug, recovered from the stored run.

    Matched on the variant's query AND on the reply the manifest recorded for that
    entry: the run replayed one query twice (once during a Bedrock outage), so the
    query alone is ambiguous and picking "the last one" would be cherry-picking.
    """
    recorded = next(o["observed"] for o in entry["observations"] if o["field"] == "last_reply")
    prefix = _reply_prefix(recorded)
    rows = [r for r in json.loads(_TURNS.read_text(encoding="utf-8"))
            if r.get("q") == variant.turns[-1].query
            and (r.get("reply") or "").startswith(prefix)]
    assert len(rows) == 1, f"{entry['id']}: {len(rows)} stored turns match"
    return {"graph_result": rows[0].get("gmeta") or {},
            "api_result_meta": rows[0].get("ameta")}, rows[0].get("reply")


@requires_seed6b
@pytest.mark.parametrize("vid", sorted(SEED6_GRAPH_ANSWERED))
def test_seed6_graph_answered_case_now_satisfies_its_floor(vid):
    variant = next(v for v in corpus.merged(CORPUS) if v.id == vid)
    entry = _seed6_entry(vid)
    assert entry["status"] == "failed" and entry["engine"] == "graph_query"
    assert entry["route"] == "nextseek_query", "these routed correctly; only the engine differed"

    debug, reply = _seed6_debug(entry, variant)
    assert debug["api_result_meta"] is None, "graph-answered: there is no REST result"
    assert debug["graph_result"].get("count") is not None, "the graph DID answer"

    floor = _floor_added(variant)
    assert floor, f"{vid} would have no floor at all"
    passed, per_field = _evaluate(floor, debug, last_reply=reply)
    assert passed, f"{vid} still fails the floor: {per_field}"


@requires_seed6b
@pytest.mark.parametrize("vid", sorted(SEED6_GRAPH_ANSWERED))
def test_seed6_no_inline_criterion_of_these_three_still_mandates_an_engine(vid):
    """The honest half, and it changed twice in one day.

    When the floor went engine-agnostic this test asserted the opposite for two of
    the three: the floor no longer mandated an engine, but the CASE still did, and
    they still failed on exactly that. The 2026-08-03 (b) override is what emptied
    those sets — see `test_the_rest_mandates_are_gone_from_the_resolved_variant`,
    which asserts the removal directly rather than inferring it from a stored run,
    and `test_the_base_catalog_still_writes_the_rest_mandates_this_override_removes`,
    which keeps the record of what was removed.

    So this now says: nothing either case writes for itself was red in that run.
    """
    entry = _seed6_entry(vid)
    inline_red = {f.split(":", 1)[-1] for f in entry["failed_criteria"]} & _inline_fields(vid)
    assert inline_red == SEED6_GRAPH_ANSWERED[vid], (
        f"{vid}: inline failures are {sorted(inline_red)}")


@requires_seed6b
def test_the_floor_only_case_goes_fully_green_without_its_case_being_rewritten():
    """`..._study_2` failed on `api_ok` and `api_outcome_observed` and on nothing
    else, and its overlay text asserts neither — both were floor entries. It is the
    one case of the three the floor change takes all the way to green on its own.
    Its two siblings needed their cases overridden as well, which is what keeps this
    test worth running separately from
    `test_seed6_the_overridden_cases_now_pass_on_the_real_evidence`."""
    entry = _seed6_entry(SEED6_FLOOR_ONLY)
    # From the manifest, not from the OBS fixture: the `route eq nextseek_query`
    # criterion below resolves off `augment_debug`, which takes route from OBS, so
    # without this the green would be partly fixture-supplied.
    assert entry["route"] == "nextseek_query"
    assert {f.split(":", 1)[-1] for f in entry["failed_criteria"]} == {
        "api_ok", "api_outcome_observed"}
    assert not ({"api_ok", "api_outcome_observed"} & _inline_fields(SEED6_FLOOR_ONLY))

    variant = next(v for v in corpus.merged(CORPUS) if v.id == SEED6_FLOOR_ONLY)
    debug, reply = _seed6_debug(entry, variant)
    passed, per_field = _evaluate(variant.turns[-1].pass_criteria, debug, last_reply=reply)
    assert passed, per_field


@requires_seed6b
def test_the_seed6_turn_that_produced_no_outcome_is_still_red_under_the_new_floor():
    """`graph.what_mice_are_in_the_impact_st`: the Bedrock chain gave up before the
    parser ran. Replayed through the WIDENED floor it must still fail. This is the
    vacuity guard measured against real evidence rather than a fixture."""
    variant = next(v for v in corpus.merged(CORPUS)
                   if v.id == "graph.what_mice_are_in_the_impact_st")
    entry = _seed6_entry("graph.what_mice_are_in_the_impact_st")
    debug, reply = _seed6_debug(entry, variant)
    assert not debug["graph_result"] and debug["api_result_meta"] is None

    passed, per_field = _evaluate(_floor_added(variant), debug, last_reply=reply)
    assert not passed, per_field
    assert per_field["outcome_observed"] is False


# ── 2026-08-03 (b): the two siblings the floor change could not reach ─────────
#
# `apply_family_floor` only ADDS a criterion when the field is absent from the
# variant's LAST turn — it can never relax an INLINE one. So making the floor
# engine-agnostic could only ever fix a case whose REST mandates came FROM the
# floor. `advanced.find_me_nhp_samples_from_study_2` was that case.
#
# Its two siblings write the mandates in their own text:
#
#     parser_plan.mode   eq       new_search
#     api_plan.endpoint  contains advanced_search
#     api_ok             true
#
# and no floor change can touch those. Both went red on every run while the
# product answered correctly. In the stored seed-6 run both routed
# `nextseek_query`, both were answered by the graph (1,765 and 1,858 rows), and
# the operator reviewed both and wrote "also correct".
#
# Both cases live ONLY in the vendored base catalog, which is out of bounds, so
# the fix is an overlay OVERRIDE: an overlay variant whose id matches a base one
# replaces it in place, keeping base ordering and id (`corpus.merged`). Same
# mechanism `..._study_2` already uses, and it must not grow the corpus.
#
# What the overrides assert instead:
#
#   route eq nextseek_query   the engine was never the question; the route is.
#                             Written out inline even though the search_advanced
#                             route policy would add it, so the resolved criterion
#                             is visible at the case. Same choice `..._study_2`
#                             makes.
#   graph_not_truncated       inert on a REST turn by design (`_graph_not_truncated`
#                             returns True with no graph_result), so it mandates no
#                             engine — but a silently capped graph answer fails it.
#   last_reply matches_re     a UID of the right type came back, OR the answer was
#                             an honest negative. A confident answer about the
#                             wrong entity fails this, which is the point.
#   entity_sampletype_codes   kept on the D.SEQ case only. It was observed
#                             `['D.SEQ']` and PASSED on the graph turn, so it is
#                             already engine-agnostic.
#
# Deliberately NO count assertion. Ground truth for these two numbers is unsettled
# (issue #33 — the same intent splits REST 139 vs a graph 250-cap), so asserting a
# value would be inventing one.

REST_MANDATES = {"parser_plan.mode", "api_plan.endpoint", "api_ok"}

# id -> its query, so a replay cannot silently match some other turn.
OVERRIDDEN_2026_08_03B = {
    "advanced.find_me_sequencing_files_assoc":
        "Find me sequencing files associated with Short Read Sequencing",
    "advanced.find_me_d_seq_samples_in_proje":
        "Find me D.SEQ samples in project IMPACT",
}


def _resolved(vid):
    """The variant's LAST-turn criteria as the harness will actually run them."""
    return next(v for v in corpus.merged(CORPUS) if v.id == vid).turns[-1].pass_criteria


@pytest.mark.parametrize("vid", sorted(OVERRIDDEN_2026_08_03B))
def test_the_base_catalog_still_writes_the_rest_mandates_this_override_removes(vid):
    """The record of WHY these two were red, held against the vendored file rather
    than against a comment. If the base catalog is ever revendored without them the
    override becomes dead weight, and that is worth hearing about."""
    base = {v.id: v for v in corpus.load_base()}[vid]
    inline = {c.field for t in base.turns for c in t.pass_criteria}
    assert REST_MANDATES <= inline, sorted(REST_MANDATES - inline)


@pytest.mark.parametrize("vid", sorted(OVERRIDDEN_2026_08_03B))
def test_the_rest_mandates_are_gone_from_the_resolved_variant(vid):
    """The fix itself: no criterion the harness runs still demands the REST engine."""
    v = next(v for v in corpus.merged(CORPUS) if v.id == vid)
    fields = {c.field for t in v.turns for c in t.pass_criteria}
    assert not (REST_MANDATES & fields), sorted(REST_MANDATES & fields)
    assert v.turns[-1].query == OVERRIDDEN_2026_08_03B[vid]


def test_the_two_overrides_replace_in_place_and_do_not_grow_the_corpus():
    """An override REPLACES; it must not append a second case asking the same
    question, which would double the cost of every full run and let the base
    variant keep failing next to its replacement."""
    merged = corpus.curated(corpus.merged(CORPUS))
    # 280 -> 283 on 2026-08-03: the create/update/delete refusal coverage came
    # back (one reinstated, two authored). This is the ONLY hardcoded corpus size
    # in the suite, so it is the one place that has to move.
    assert len(merged) == 308
    ids = [v.id for v in merged]
    base_ids_all = {v.id for v in corpus.load_base()}
    defs = {v.id: v for v in corpus.load_all_definitions(CORPUS)}
    for vid in OVERRIDDEN_2026_08_03B:
        assert ids.count(vid) == 1, vid
        # What "override" means post-migration: one definition, carrying the
        # overlay origin, under an id the vendored catalog also defines. Before
        # 2026-08-04 this asked `corpus.overridden_ids`, which computed the same
        # intersection across two files.
        assert vid in base_ids_all, vid
        assert "overlay" in defs[vid].tags, vid

    # "In place" means base ORDER is kept: every non-retired base id appears in
    # merged() in the order the vendored catalog declares it.
    #
    # RELATIVE order, not a prefix. It was a prefix while `corpus.merged`
    # appended overlay-only variants to the end of the WHOLE list; corpus.json
    # appends them to the end of their own BLOCK, so `writes_unsupported`'s two
    # overlay-only members now sit mid-list rather than at 281-282. Measured
    # 2026-08-04: 2 active variants genuinely displaced, 31 more the shift
    # cascade. Nothing reads global order but the loop that runs the cases;
    # `corpus.sample` buckets on family, and within-family order is unchanged.
    retired = {vid for vid, m in corpus.variant_meta(CORPUS).items()
               if m["status"] == "retired"}
    base_ids = [v.id for v in corpus.load_base() if v.id not in retired]
    # GLOBAL base order stopped holding on 2026-08-04: the 28-family remap regroups
    # variants by their new family, so anything that moved family (the 12 assay
    # searches to graph_traversal, say) necessarily moves in the flat list. What the
    # remap DID preserve is relative order WITHIN a family, which is the property
    # `corpus.sample` depends on, so that is what this pins now.
    fam_of = {v.id: v.family for v in corpus.merged(CORPUS)}
    base_set = set(base_ids)
    for fam in sorted(set(fam_of.values())):
        got = [i for i in ids if i in base_set and fam_of.get(i) == fam]
        want = [i for i in base_ids if fam_of.get(i) == fam]
        assert got == want, f"{fam}: base order not preserved within the family"


# ── the new criteria replayed against the STORED seed-6 run ───────────────────

def _seed6_entities(entry):
    """`entity_result` recovered from the manifest's OWN recorded observation.

    `entity_sampletype_codes` resolves off `entity_result.sampletypes`
    (`e2e/criteria.py`), and turns.json stores the parser and graph plans but not
    the entity block — so a debug rebuilt from turns.json alone cannot answer that
    criterion. The manifest did record the resolved value, as the repr of the list
    the run saw, so the codes below come from the run and not from here.

    Returns None when the run never resolved that field, so a caller cannot
    mistake a missing observation for an empty one.
    """
    obs = next((o for o in entry["observations"]
                if o["field"] == "entity_sampletype_codes"), None)
    if obs is None:
        return None
    return {"sampletypes": [{"code": c} for c in ast.literal_eval(str(obs["observed"]))]}


def _seed6_debug_with_entities(entry, variant):
    debug, reply = _seed6_debug(entry, variant)
    entities = _seed6_entities(entry)
    if entities is not None:
        debug["entity_result"] = entities
    return debug, reply


@requires_seed6b
@pytest.mark.parametrize("vid", sorted(OVERRIDDEN_2026_08_03B))
def test_seed6_the_overridden_cases_now_pass_on_the_real_evidence(vid):
    """Every criterion the override writes is satisfied by what the run ACTUALLY
    observed — not by a fixture shaped to agree with it."""
    entry = _seed6_entry(vid)
    assert entry["status"] == "failed" and entry["engine"] == "graph_query"
    assert entry["route"] == "nextseek_query", "these routed correctly; only the engine differed"
    # What it was red on: the three inline mandates, plus the floor's
    # api_outcome_observed that the 2026-08-03 floor change already retired.
    was_red = {f.split(":", 1)[-1] for f in entry["failed_criteria"]}
    assert was_red == REST_MANDATES | {"api_outcome_observed"}, sorted(was_red)

    variant = next(v for v in corpus.merged(CORPUS) if v.id == vid)
    debug, reply = _seed6_debug_with_entities(entry, variant)
    assert debug["api_result_meta"] is None, "graph-answered: there is no REST result"

    passed, per_field = _evaluate(variant.turns[-1].pass_criteria, debug, last_reply=reply)
    assert passed, per_field
    # Not a rubber stamp: the mandates are GONE, not merely satisfied.
    assert not (REST_MANDATES & set(per_field)), sorted(REST_MANDATES & set(per_field))


# ── the vacuity guard: these criteria must still be able to FAIL ──────────────
#
# Fixture-driven on purpose, so it keeps running on a checkout with no stored run.
# Every shape below is one the product could really produce.

# A graph turn that answered correctly, in the shape `augment_debug` consumes.
GOOD_DEBUG = {"graph_result": {"ok": True, "count": 1765, "total": 1765, "truncated": False},
              "entity_result": {"sampletypes": [{"code": "D.SEQ"}]}}

GOOD_REPLY = {
    "advanced.find_me_sequencing_files_assoc":
        "A total of 1,765 Sequencing Data (D.SEQ) files are associated with Short "
        "Read Sequencing.\n\n- D.SEQ-230512FOR-287-PUB",
    "advanced.find_me_d_seq_samples_in_proje":
        "A total of 1,858 Sequencing Data (D.SEQ) samples match project IMPACT.\n\n"
        "* `D.SEQ-220823SHA-9-PUB`",
}


@pytest.mark.parametrize("vid", sorted(OVERRIDDEN_2026_08_03B))
def test_the_new_criteria_pass_a_correctly_shaped_graph_answer(vid):
    """The control. Without it the four negatives below could all be passing for
    some reason other than the one they name."""
    passed, per_field = _evaluate(_resolved(vid), GOOD_DEBUG, last_reply=GOOD_REPLY[vid])
    assert passed, per_field


@pytest.mark.parametrize("vid", sorted(OVERRIDDEN_2026_08_03B))
def test_the_new_criteria_still_fail_an_empty_answer(vid):
    passed, per_field = _evaluate(_resolved(vid), GOOD_DEBUG, last_reply="")
    assert not passed
    assert per_field["last_reply"] is False


@pytest.mark.parametrize("vid", sorted(OVERRIDDEN_2026_08_03B))
def test_the_new_criteria_still_fail_a_confident_answer_about_the_wrong_entity(vid):
    """The shape the reply regex exists for: fluent, non-empty, plausible — and
    about mice. No UID of the right type and no disclaimer, so it must go red."""
    passed, per_field = _evaluate(
        _resolved(vid), GOOD_DEBUG,
        last_reply="A total of 12 Mouse (A.MUS) records were returned for that request.")
    assert not passed
    assert per_field["last_reply"] is False


@pytest.mark.parametrize("vid", sorted(OVERRIDDEN_2026_08_03B))
def test_the_new_criteria_still_fail_a_silently_capped_graph_answer(vid):
    """`graph_not_truncated` is inert on a REST turn, which is why it is safe here —
    but on a graph turn that hit its LIMIT it is the whole reason it was kept."""
    debug = {**GOOD_DEBUG,
             "graph_result": {"ok": True, "count": 5000, "total": 12000, "truncated": True}}
    passed, per_field = _evaluate(_resolved(vid), debug, last_reply=GOOD_REPLY[vid])
    assert not passed
    assert per_field["graph_not_truncated"] is False


@pytest.mark.parametrize("vid", sorted(OVERRIDDEN_2026_08_03B))
def test_the_new_criteria_still_fail_a_turn_that_went_to_the_wrong_route(vid):
    """`route` is the one thing these cases still pin absolutely."""
    cc = RouteObservation("container_cc", None, "baml", "", None, None)
    payload = {"status": "completed", "progress": [
        {"event": "route_decided", "data": {"route": "container_cc", "source": "baml"}},
        {"event": "query_complete", "data": {"reply": "ok", "debug": GOOD_DEBUG}}]}
    passed, results, _ = evaluate.evaluate_turn(
        payload, _resolved(vid), cc, last_reply=GOOD_REPLY[vid])
    per_field = {r["field"]: r["passed"] for r in results}
    assert not passed
    assert per_field["route"] is False


def test_the_d_seq_case_still_fails_when_a_different_sample_type_came_back():
    """The one criterion kept from the base case. It PASSED on the graph turn, so it
    survived the override — and it still has teeth."""
    vid = "advanced.find_me_d_seq_samples_in_proje"
    debug = {**GOOD_DEBUG, "entity_result": {"sampletypes": [{"code": "A.MUS"}]}}
    passed, per_field = _evaluate(_resolved(vid), debug, last_reply=GOOD_REPLY[vid])
    assert not passed
    assert per_field["entity_sampletype_codes"] is False


# ── 2026-08-03 (c): the `could not` escape hatch, deleted ────────────────────
#
# All three `advanced.*` reply guards carried a `|could not` branch. It was added
# to tolerate a reply like
#
#     **The request could not be completed.** All provider fallbacks exhausted ...
#
# so a provider outage would not read as a product failure. Task 3 made that
# unnecessary: `evaluate.classify_turn_status` returns `error` for an outaged turn
# BEFORE any criterion is scored, and `runner` sets `outage=True` alongside it,
# which exempts the entry from the gate. The branch could no longer save an
# outage — it could only let a NON-outage zero-result non-answer score green,
# which is precisely the failure these three guards exist to catch.
#
# The honest-negative branches (`no samples`, `no sequencing`, `no files`) stay.
# The point was never to forbid a negative answer; it was to forbid a confident
# answer about the wrong entity, and to stop "I could not do that" counting as one.

COULD_NOT_GUARDED = (
    "advanced.find_me_nhp_samples_from_study_2",
    "advanced.find_me_sequencing_files_assoc",
    "advanced.find_me_d_seq_samples_in_proje",
)

# Per case, an honest negative the guard must still accept. These are the branch
# the deletion had to leave alone.
HONEST_NEGATIVE = {
    "advanced.find_me_nhp_samples_from_study_2":
        "No samples matched the IMPACT study.",
    "advanced.find_me_sequencing_files_assoc":
        "No sequencing files are associated with Short Read Sequencing.",
    "advanced.find_me_d_seq_samples_in_proje":
        "No samples of that type are in project IMPACT.",
}

# The exact shape the deleted branch existed to tolerate, and two plainer
# non-answers that were riding on it.
NON_ANSWERS = (
    "**The request could not be completed.**\n\nAll provider fallbacks exhausted "
    "� agent 'graph_planner': provider bedrock returned 503",
    "I could not complete that request.",
    "The search could not be run against the graph.",
)


def _reply_guard(vid):
    """The one `last_reply matches_re` this case runs."""
    return next(c.value for c in _resolved(vid)
                if c.field == "last_reply" and c.op == "matches_re")


@pytest.mark.parametrize("vid", COULD_NOT_GUARDED)
@pytest.mark.parametrize("reply", NON_ANSWERS)
def test_a_zero_result_non_answer_no_longer_scores_green(vid, reply):
    passed, per_field = _evaluate(_resolved(vid), GOOD_DEBUG, last_reply=reply)
    assert not passed
    assert per_field["last_reply"] is False


@pytest.mark.parametrize("vid", COULD_NOT_GUARDED)
def test_an_honest_negative_is_still_accepted(vid):
    """The deletion must not turn "there are none" into a failure. That would be a
    guard that only a non-empty answer can satisfy, on questions whose correct
    answer might legitimately be zero."""
    passed, per_field = _evaluate(_resolved(vid), GOOD_DEBUG,
                                  last_reply=HONEST_NEGATIVE[vid])
    assert per_field["last_reply"] is True, HONEST_NEGATIVE[vid]
    assert passed, per_field


@pytest.mark.parametrize("vid", COULD_NOT_GUARDED)
def test_the_deleted_branch_is_really_gone_from_the_resolved_criterion(vid):
    """Held against the criterion the harness runs, not against the overlay text,
    so re-adding it through a rewrite or an override would also fail."""
    assert "could not" not in _reply_guard(vid)


def test_an_outage_is_classified_before_any_criterion_is_scored():
    """Why the branch is obsolete rather than merely unfashionable: the reply it
    was written for never reaches the reply guard at all."""
    outage = NON_ANSWERS[0]
    assert evaluate.classify_turn_status(True, outage) == "error"
    assert evaluate.classify_turn_status(False, outage) == "error"


@requires_seed6b
@pytest.mark.parametrize("vid", COULD_NOT_GUARDED)
def test_seed6_the_tightened_guard_still_passes_the_reply_the_run_produced(vid):
    """The one that stops this being a tightening nobody checked. All three of
    these answered correctly in the stored seed-6 run; if the deletion had broken
    any of them, this is where it would show."""
    entry = _seed6_entry(vid)
    recorded = next(o["observed"] for o in entry["observations"]
                    if o["field"] == "last_reply")

    assert re.search(_reply_guard(vid), str(recorded), re.IGNORECASE), (
        f"{vid}: the tightened guard rejects the reply the run actually produced")

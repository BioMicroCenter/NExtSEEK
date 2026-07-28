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
"""
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


# ── the floor spec must actually USE them, measured on the real corpus ────────

import pathlib  # noqa: E402

from nessie_tests import corpus  # noqa: E402

OVERLAY = pathlib.Path(__file__).resolve().parents[1] / "overlay.json"


def _floor_criteria(variant_id, merged):
    v = next(v for v in merged if v.id == variant_id)
    return {(c.field, c.op) for t in v.turns for c in t.pass_criteria}


def _floor_fields():
    floors = corpus.load_family_floor(OVERLAY).get("floors", {})
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
    for field in ("api_outcome_observed", "graph_outcome_observed",
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
    v = next(v for v in corpus.merged(OVERLAY) if v.id == "graph.tissue_cell_impact")
    crits = {(c.field, c.op, c.value) for t in v.turns for c in t.pass_criteria}
    assert ("graph_not_truncated", "true", None) not in crits
    assert ("graph_result.total", "gte", 10000) in crits, sorted(crits)


def test_the_floor_still_asserts_something_on_every_family_it_covers():
    """Loosening must not become removing: each floored family keeps an outcome
    assertion, just one its correct answers can satisfy."""
    merged = corpus.merged(OVERLAY)
    expected = {
        "graph_query": {"graph_outcome_observed", "graph_truncation_disclosed"},
        "search_advanced": {"api_outcome_observed"},
        "search_retrieve": {"api_outcome_observed"},
        "search_parents_by_child": {"api_outcome_observed"},
        "reporting": {"report_produced_output"},
    }
    for family, must_have in expected.items():
        sample = [v for v in merged if v.family == family and "no_floor" not in v.tags]
        assert sample, f"no un-tagged variants in {family}"
        fields = {c.field for t in sample[0].turns for c in t.pass_criteria}
        assert must_have & fields, f"{family} lost its floor: {sample[0].id} has {sorted(fields)}"

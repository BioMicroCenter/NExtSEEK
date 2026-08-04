from pathlib import Path
from nessie_tests import corpus

CORPUS = Path(__file__).resolve().parents[1] / "corpus.json"


def _overlay_origin():
    """The 47 definitions that came from overlay.json rather than the vendored
    catalog. `origin` is stamped onto `tags`, so this is the post-migration
    spelling of the old `corpus.load_overlay(OVERLAY)`; retired ones included,
    exactly as the overlay file included them."""
    return [v for v in corpus.load_all_definitions(CORPUS) if "overlay" in v.tags]


def test_every_route_gate_case_asserts_route():
    gate = [v for v in _overlay_origin() if "route_gate" in v.tags]
    assert len(gate) >= 3
    for v in gate:
        fields = {c.field for t in v.turns for c in t.pass_criteria}
        assert "route" in fields, f"{v.id} missing a route assertion"


def test_has_cc_unrelated_and_green_families():
    ov = _overlay_origin()
    # `nessie_route` and `nessie_green` were provenance groupings, not intents, and
    # the 2026-08-04 remap dissolved them: every member was filed by question
    # content and kept its `route_gate` / `green` tag. So assert on the tags, which
    # are what actually carried the meaning, rather than on the family names.
    tags = {t for v in ov for t in v.tags}
    assert {"route_gate", "green"} <= tags
    assert {v.family for v in ov if "route_gate" in v.tags} <= {
        "sample_search", "graph_traversal", "unsupported",
        "pipeline_launch", "pipeline_output_reingest", "entity_write"}
    routes = {c.value for v in ov for t in v.turns for c in t.pass_criteria if c.field == "route"}
    assert {"container_cc", "unrelated", "nextseek_query"} <= routes


# --------------------------------------------------------------------------- #
# T3.4 — the ten masked cases. Each of these passed the 2026-07-27 run while the
# underlying answer was wrong, because the criteria asserted the PLAN or the
# PLUMBING and never the OUTCOME.
# --------------------------------------------------------------------------- #

import re
from pathlib import Path as _Path
from nessie_tests import corpus as _corpus

_CORPUS = _Path(__file__).resolve().parents[1] / "corpus.json"


def _merged():
    return {v.id: v for v in _corpus.merged(_CORPUS)}


def _retired():
    """Variants that left the active corpus but kept their definition.

    The GBM cases were retired on 2026-07-30 because the study does not exist,
    so their criteria no longer run. The assertions below still hold them to
    account: if any of them is ever reinstated, the guards it carries must
    already be sound rather than needing to be rediscovered.
    """
    meta = _corpus.variant_meta(_CORPUS)
    return {v.id: v for v in _corpus.load_all_definitions(_CORPUS)
            if meta[v.id]["status"] == "retired"}


def _fields(variant):
    return {(c.field, c.op) for t in variant.turns for c in t.pass_criteria}


def _value(variant, field, op):
    return next(c.value for t in variant.turns for c in t.pass_criteria
                if c.field == field and c.op == op)


def test_the_nih_report_asserts_that_the_report_actually_ran():
    """Highest-value criterion in the set: this raised ValueError in BOTH runs and passed both."""
    v = _merged()["report.build_me_a_full_nih_report_for"]
    assert ("reporter_result.ok", "true") in _fields(v)
    assert ("reporter_result.samples.uuids_saved", "gte") in _fields(v)


def test_the_kamm_rppr_band_is_two_sided():
    """`uuids_saved lte 45000` was satisfied by the observed 0."""
    v = _merged()["report.put_together_an_annual_progres"]
    assert _value(v, "reporter_result.samples.uuids_saved", "gte") >= 1
    assert _value(v, "reporter_result.samples.uuids_saved", "lte") <= 20000
    assert ("reporter_result.samples.labs_table", "nonempty") in _fields(v)


def test_the_metnet_study_count_is_bounded_below_the_whole_catalog():
    """Task 792 returned 51 — every Study node. Truth is 10."""
    v = _merged()["graph.find_me_studies_in_metnet"]
    assert _value(v, "graph_result.count", "lte") <= 25
    assert ("graph_not_truncated", "true") in _fields(v)


def test_the_gbm_count_rejects_the_unfiltered_fingerprint():
    """50,161 is every sample in any study — the missing-WITH signature."""
    v = _retired()["routing.gbm_study_count"]
    guard = _value(v, "last_reply", "matches_re")
    for bad in ("There are 50,161 samples.", "50161 matched", "Found 45,000 rows"):
        assert not re.search(guard, bad, re.IGNORECASE), f"guard let {bad!r} through"
    for good in ("GBM has 0 samples.", "Found 408 samples.", "line one\nand 4500 rows"):
        assert re.search(guard, good, re.IGNORECASE), f"guard falsely rejected {good!r}"


def test_the_pbmc_gbm_case_asserts_the_query_not_a_nonzero_count():
    """The operator confirmed the zero is honest — never assert a nonzero count here."""
    v = _retired()["graph.what_pbmcs_tissues_in_the_gbm"]
    fields = _fields(v)
    assert ("graph_result.parameters.project", "matches_re") in fields
    assert ("graph_result.parameters.child_type", "eq") in fields
    assert ("graph_cypher", "contains") in fields
    assert not any(f == "graph_result.count" for f, _ in fields), (
        "a count bound here would false-fail an honest zero"
    )
    assert "no_floor" in v.tags, "the T3.5 floor would impose count gte 1 and break it"


def test_the_assay_case_asserts_the_real_total():
    v = _merged()["sys.show_me_all_assays_i_have_acce"]
    assert _value(v, "api_result_meta.row_count", "gte") >= 300
    assert _value(v, "last_reply", "mentions") == "324"


def test_the_uid_lineage_repro_is_engine_agnostic_and_not_known_fail():
    """A count bound would false-fail whichever engine it does not name."""
    v = _retired()["repro.cypher_uid_dot"]
    assert _value(v, "last_reply", "mentions") == "D.SEQ-220823SHA"
    assert "known_fail" not in v.tags, "it genuinely regressed; it should go red, not be excused"


def test_the_gbm_nhp_case_requires_an_honest_negative():
    v = _retired()["advanced.find_me_nhp_samples_from_study"]
    guards = [c.value for t in v.turns for c in t.pass_criteria if c.op == "matches_re"]

    def passes(reply):
        return all(re.search(g, reply, re.IGNORECASE) for g in guards)

    assert passes("There are no NHP samples in the GBM study.")
    assert passes("GBM does not exist as a study or investigation.")
    assert not passes("I found 89 NHP samples in GBM."), "the fabricated 89 must fail"
    assert not passes("Found 408 samples."), "a confident non-zero count must fail"


def test_a_known_fail_sibling_pins_the_ns_graph_path():
    """The route-agnostic sibling lets CC paper over the server-side defect."""
    v = _retired()["advanced.find_me_nhp_samples_from_study_ns_graph"]
    assert ("route", "eq") in _fields(v)
    assert "known_fail" in v.tags


def test_the_write_case_rejects_budget_abort_language():
    """Read from the ACTIVE corpus since 2026-08-03: this case was reinstated as
    the create leg of the restored write/delete refusal coverage, so its guards
    now run for real rather than being held to account in absentia."""
    v = _merged()["write.create_me_investigation_testin"]
    guards = [c.value for t in v.turns for c in t.pass_criteria if c.op == "matches_re"]

    def passes(reply):
        return all(re.search(g, reply, re.IGNORECASE) for g in guards)

    assert passes("Created investigation 'Testing Investigation. Still Testing' (id 42).")
    assert passes("Shall I proceed with creating that investigation?")
    assert not passes("I hit the attempt budget and stopped.")


def test_every_negative_guard_is_dotall():
    """matches_re has no DOTALL. Without (?s), `^(?!.*X).*$` fails on ANY multi-line
    reply — clean or not — so the guard can never go green."""
    offenders = []
    for v in _overlay_origin():
        for t in v.turns:
            for c in t.pass_criteria:
                if c.op == "matches_re" and isinstance(c.value, str) and "(?!" in c.value:
                    if not c.value.startswith("(?s)"):
                        offenders.append(f"{v.id}: {c.value}")
    assert not offenders, "negative guards missing the (?s) prefix: " + "; ".join(offenders)


def test_the_global_count_matches_the_seeded_database():
    """The corpus asserted 50,889; the DB reports 50,886."""
    v = _merged()["green.global_count"]
    guard = _value(v, "last_reply", "matches_re")
    assert re.search(guard, "There are 50,886 samples in the database.")
    assert not re.search(guard, "There are 50,161 samples in the database.")


# --------------------------------------------------------------------------- #
# C1 part 2 — the blast radius of the vacuity guard, measured over the RESOLVED
# corpus rather than reasoned about. `no_assertions` is a real failure, so any
# variant it lands on flips from green to gate-failing the moment it ships.
# --------------------------------------------------------------------------- #

from nessie_tests import evaluate as _evaluate  # noqa: E402

# The four NS outcome fields, skipped only when the observed route is
# container_cc. Assuming a variant routes CC is the WORST case for this analysis,
# which is the right direction for a blast-radius measurement.
def _skipped_now(c):
    return _evaluate.is_unobservable(c.field, c.op)


def _skipped_if_cc(c):
    return _evaluate.is_unobservable(c.field, c.op, route=_evaluate.CC_ROUTE)


def _vacuous_variants(predicate):
    return sorted(v.id for v in _corpus.merged(_CORPUS)
                  if all(predicate(c) for t in v.turns for c in t.pass_criteria))


def test_no_variant_in_the_corpus_asserts_nothing_at_all():
    """Zero variants flip green -> no_assertions, in either the current or the CC world.

    If this ever fails, a variant has been added (or weakened) to the point where
    it tests nothing, and it will now say so loudly instead of reporting green.
    """
    # Deliberately NOT pinned to a corpus SIZE. That is pinned once already, in
    # test_floor_ops.py, and a legitimate corpus addition must not break a test
    # whose subject is vacuity for an unrelated reason.
    assert _vacuous_variants(_skipped_now) == []
    assert _vacuous_variants(_skipped_if_cc) == []


def test_the_turns_that_evaluate_nothing_are_a_known_named_set():
    """Per-TURN vacuity, which the case-level status deliberately does not flag.

    These turns really do assert nothing evaluable, but each belongs to a case
    whose other turns assert four real criteria — so the case is not vacuous and
    must not be labelled as such. Recorded here so the residue is visible and a
    NEW vacuous turn has to be acknowledged rather than appearing silently.
    """
    def turns_where(predicate):
        return sorted(f"{v.id}:{t.label}" for v in _corpus.merged(_CORPUS) for t in v.turns
                      if t.pass_criteria and all(predicate(c) for c in t.pass_criteria))

    assert turns_where(_skipped_now) == [
        "pipeline.activation_rnaseq:trigger_pipeline",
        "pipeline.end_to_end_emit:issue_directive",
    ]
    # ...plus one more, but only if that turn routes container_cc.
    assert turns_where(_skipped_if_cc) == [
        "pipeline.activation_rnaseq:trigger_pipeline",
        "pipeline.end_to_end_emit:issue_directive",
        "tree.then_ask_about:follow_up",
    ]


def test_every_such_case_still_asserts_real_criteria_on_another_turn():
    """The justification for the case-level rule, not an assumption about it."""
    merged = {v.id: v for v in _corpus.merged(_CORPUS)}
    for vid in ("pipeline.activation_rnaseq", "pipeline.end_to_end_emit",
                "tree.then_ask_about"):
        real = [c.field for t in merged[vid].turns for c in t.pass_criteria
                if not _skipped_if_cc(c)]
        assert len(real) >= 4, f"{vid} asserts only {real}"

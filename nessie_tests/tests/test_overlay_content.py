from pathlib import Path
from nessie_tests import corpus

OVERLAY = Path(__file__).resolve().parents[1] / "overlay.json"


def test_every_route_gate_case_asserts_route():
    ov = corpus.load_overlay(OVERLAY)
    gate = [v for v in ov if "route_gate" in v.tags]
    assert len(gate) >= 3
    for v in gate:
        fields = {c.field for t in v.turns for c in t.pass_criteria}
        assert "route" in fields, f"{v.id} missing a route assertion"


def test_has_cc_unrelated_and_green_families():
    ov = corpus.load_overlay(OVERLAY)
    fams = {v.family for v in ov}
    assert {"nessie_route", "nessie_green"} <= fams
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

_OVERLAY = _Path(__file__).resolve().parents[1] / "overlay.json"


def _merged():
    return {v.id: v for v in _corpus.merged(_OVERLAY)}


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
    v = _merged()["routing.gbm_study_count"]
    guard = _value(v, "last_reply", "matches_re")
    for bad in ("There are 50,161 samples.", "50161 matched", "Found 45,000 rows"):
        assert not re.search(guard, bad, re.IGNORECASE), f"guard let {bad!r} through"
    for good in ("GBM has 0 samples.", "Found 408 samples.", "line one\nand 4500 rows"):
        assert re.search(guard, good, re.IGNORECASE), f"guard falsely rejected {good!r}"


def test_the_pbmc_gbm_case_asserts_the_query_not_a_nonzero_count():
    """The operator confirmed the zero is honest — never assert a nonzero count here."""
    v = _merged()["graph.what_pbmcs_tissues_in_the_gbm"]
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
    v = _merged()["repro.cypher_uid_dot"]
    assert _value(v, "last_reply", "mentions") == "D.SEQ-220823SHA"
    assert "known_fail" not in v.tags, "it genuinely regressed; it should go red, not be excused"


def test_the_gbm_nhp_case_requires_an_honest_negative():
    v = _merged()["advanced.find_me_nhp_samples_from_study"]
    guards = [c.value for t in v.turns for c in t.pass_criteria if c.op == "matches_re"]

    def passes(reply):
        return all(re.search(g, reply, re.IGNORECASE) for g in guards)

    assert passes("There are no NHP samples in the GBM study.")
    assert passes("GBM does not exist as a study or investigation.")
    assert not passes("I found 89 NHP samples in GBM."), "the fabricated 89 must fail"
    assert not passes("Found 408 samples."), "a confident non-zero count must fail"


def test_a_known_fail_sibling_pins_the_ns_graph_path():
    """The route-agnostic sibling lets CC paper over the server-side defect."""
    v = _merged()["advanced.find_me_nhp_samples_from_study_ns_graph"]
    assert ("route", "eq") in _fields(v)
    assert "known_fail" in v.tags


def test_the_write_case_rejects_budget_abort_language():
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
    for v in _corpus.load_overlay(_OVERLAY):
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

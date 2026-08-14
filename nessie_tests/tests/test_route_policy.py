"""Writes, exports and open-ended analysis are Container-CC's job, so the corpus
must assert the ROUTE rather than an NS-internal parser mode.

23 variants across `unsupported` and `writes_unsupported` assert
`parser_plan.mode == "unsupported"`. When the router sends the turn to
container_cc or unrelated, NS never runs, so there IS no parser plan and the
criterion resolves to None and fails unconditionally. Three of the fifteen
seed-0 failures were exactly this (unsup.weather, write.export_all_metadata,
write.download_all_samples).

The policy was settled by the operator on 2026-07-28: bulk export is CC's job.
That matches what the corpus already blesses in its route_gate family —
route.cc_write_investigation ("writes are deliberately CC's, not an NS
'unsupported'") and route.cc_open_ended_analysis. It also matches the three
variants a previous wave already converted by hand, which carry exactly the
shape this rule produces: a `route` criterion and a `last_reply`, no
`parser_plan.mode`.

Applied structurally rather than as 23 hand-written overrides for the same reason the
family floor is: the correction belongs in one reviewable place.
"""
import pathlib

from nessie_tests import corpus

CORPUS = pathlib.Path(__file__).resolve().parents[1] / "corpus.json"


def _crits(variant):
    return {(c.field, c.op, c.value) for t in variant.turns for c in t.pass_criteria}


def _by_id():
    return {v.id: v for v in corpus.merged(CORPUS)}


def _by_id_incl_retired():
    """Active corpus plus retired variants with the same policy applied.

    The 2026-07-30 review retired most of `writes_unsupported`, but the policy
    these tests guard is about the RULE, not about which cases happen to be
    active today. Applying it to the retired definitions too keeps the guarantee
    meaningful: reinstating any of them still yields `route eq container_cc`
    rather than an unobservable `parser_plan.mode`.
    """
    out = _by_id()
    meta = corpus.variant_meta(CORPUS)
    retired = corpus.apply_route_policy(
        [v for v in corpus.load_all_definitions(CORPUS)
         if meta[v.id]["status"] == "retired"],
        corpus.load_route_policy(CORPUS))
    for v in retired:
        out.setdefault(v.id, v)
    return out


def test_no_cc_routed_family_asserts_an_ns_internal_parser_mode():
    """`parser_plan.mode` is unobservable on a turn that never reaches NS."""
    offenders = [v.id for v in corpus.merged(CORPUS)
                 if v.family in ("unsupported", "writes_unsupported")
                 for t in v.turns for c in t.pass_criteria
                 if c.field == "parser_plan.mode"]
    assert offenders == [], f"{len(offenders)} variants still assert an NS mode: {offenders[:6]}"


def test_bulk_export_is_asserted_as_container_cc():
    """The operator's call: a bulk export is CC's job, not an NS refusal."""
    for vid in ("write.export_all_metadata_for_nhp_22",
                "write.export_all_metadata_for_nhp_22_2",
                "write.download_all_samples_from_the"):
        assert ("route", "eq", "container_cc") in _crits(_by_id_incl_retired()[vid]), vid


def test_resource_writes_are_asserted_as_container_cc():
    """Already blessed by route.cc_write_investigation in the route_gate family."""
    for vid in ("write.create_investigation_testing_4",
                "write.register_a_new_mouse_sample_wi",
                "write.update_the_scientist_field_on"):
        assert ("route", "eq", "container_cc") in _crits(_by_id_incl_retired()[vid]), vid


def test_open_ended_analysis_is_asserted_as_container_cc():
    """Already blessed by route.cc_open_ended_analysis."""
    for vid in ("unsup.make_me_a_bar_chart_of_sample",
                "unsup.compare_gene_expression_betwee"):
        assert ("route", "eq", "container_cc") in _crits(_by_id()[vid]), vid


def test_off_topic_stays_unrelated_not_cc():
    """Weather is not lab work. Run 3 routed it `unrelated` and that is correct;
    sending it to CC would burn a container on a question CC cannot answer either."""
    assert ("route", "eq", "unrelated") in _crits(_by_id()["unsup.weather"])


def test_domain_knowledge_may_be_either_but_must_not_be_a_data_query():
    """"Explain what NDMA is" names something real in the database but asks for
    textbook chemistry. unrelated and container_cc are both defensible; treating it
    as a nextseek_query data search is not. Asserted as an alternation rather than
    pinning a policy that has not been decided."""
    crits = _crits(_by_id()["unsup.domain_chemistry"])
    assert ("route", "matches_re", "(unrelated|container_cc)") in crits


def test_a_route_assertion_alone_cannot_tell_a_working_turn_from_a_dead_one():
    """The 2026-07-29 run: task 957 errored with a null reply and scored GREEN.

    write.export_all_metadata_for_nhp_22 asserted only `route eq container_cc`.
    The route WAS container_cc, so the single criterion held while the turn hit
    error_max_budget_usd and produced nothing. Every rule-converted variant needs
    an outcome assertion next to the route.
    """
    for v in corpus.merged(CORPUS):
        if v.family not in ("unsupported", "writes_unsupported"):
            continue
        # A route gate asserts the ROUTE and deliberately nothing else; that is the
        # whole point of the tag, and `family_floor` already exempts them. Two
        # landed in `unsupported` when the 2026-08-04 remap dissolved nessie_route.
        if "route_gate" in v.tags:
            continue
        fields = {c.field for t in v.turns for c in t.pass_criteria}
        assert "last_reply" in fields, (
            f"{v.id} asserts the route and nothing about the answer, so a dead "
            f"turn scores green. Has: {sorted(fields)}")


def test_the_outcome_assertion_is_not_double_written():
    """The three hand-converted variants already carry last_reply; adding a second
    nonempty check would be noise, and would overwrite a stricter matches_re."""
    v = _by_id_incl_retired()["write.create_me_investigation_testin"]
    nonempty = [c for t in v.turns for c in t.pass_criteria
                if c.field == "last_reply" and c.op == "nonempty"]
    assert len(nonempty) == 1, f"duplicated last_reply nonempty: {len(nonempty)}"


def test_the_hand_converted_variants_are_left_untouched():
    """Three variants a previous wave already converted must not be double-written."""
    for vid in ("unsup.is_treatment_a_significantly_b",
                "write.create_me_investigation_testin"):
        routes = [c for c in _crits(_by_id_incl_retired()[vid]) if c[0] == "route"]
        assert len(routes) == 1, f"{vid} has {routes}"

from NessieAI.tests.pipeline_scenarios.scorer import Outcome, score
from NessieAI.tests.pipeline_scenarios.stories import Story


def _story(**kw):
    base = dict(id="s", family="bulk_rna", question="q", cohort="c", route="nextseek_query",
                acceptable=[["rnaseq"]], must_not=["scrnaseq"], launch={"built": True, "must_match": {}},
                why={"route": "r", "pipelines": "p"})
    return Story(**{**base, **kw})


def _built(key="rnaseq", params=None, selection=None):
    return Outcome(route="nextseek_query", pipeline_key=key, launch_params=params or {"genome": "GRCh38"},
                   selection=selection or {"verdict": "chosen", "pipelines": [key]})


def test_a_wrong_route_fails():
    assert score(_story(), Outcome(route="container_cc")).grade == "FAIL"


def test_a_must_not_offer_fails_even_when_only_forked():
    o = Outcome(route="nextseek_query", selection={"verdict": "fork", "pipelines": ["rnaseq", "scrnaseq"]})
    s = score(_story(), o)
    assert s.grade == "FAIL" and "scrnaseq" in s.reasons[0]


def test_a_must_not_dropped_pipeline_counts_as_offered():
    o = Outcome(route="nextseek_query", selection={"verdict": "out_of_scope", "pipelines": [],
                                                   "dropped": ["scrnaseq"]})
    assert score(_story(), o).grade == "FAIL"


def test_launch_expected_but_not_built_fails():
    o = Outcome(route="nextseek_query", selection={"verdict": "chosen", "pipelines": ["rnaseq"]},
                pipeline_key="rnaseq", replies=["I need the strandedness."])
    s = score(_story(), o)
    assert s.grade == "FAIL" and "not built" in s.reasons[0]


def test_a_built_acceptable_launch_passes():
    assert score(_story(), _built()).grade == "PASS"


def test_an_unacceptable_but_allowed_pipeline_is_partial():
    assert score(_story(), _built("rnasplice")).grade == "PARTIAL"


def test_a_must_match_mismatch_is_partial():
    s = score(_story(launch={"built": True, "must_match": {"genome": "GRCh38"}}), _built(params={"genome": "GRCm39"}))
    assert s.grade == "PARTIAL" and "genome" in s.reasons[0]


def test_must_match_includes_reads_a_comma_list():
    st = _story(acceptable=[["sarek"]], must_not=[], launch={"built": True,
                "must_match": {"tools": {"includes": ["haplotypecaller"]}}})
    assert score(st, _built("sarek", {"tools": "haplotypecaller,strelka"})).grade == "PASS"
    assert score(st, _built("sarek", {"tools": "strelka"})).grade == "PARTIAL"


def test_asking_first_when_a_single_choice_was_expected_is_partial():
    o = _built(selection={"verdict": "fork", "pipelines": ["rnaseq", "rnasplice"]})
    assert score(_story(), o).grade == "PARTIAL"


def test_a_fork_is_fine_when_the_story_accepts_the_pair():
    o = _built(selection={"verdict": "fork", "pipelines": ["rnaseq", "rnasplice"]})
    assert score(_story(acceptable=[["rnaseq"], ["rnaseq", "rnasplice"]]), o).grade == "PASS"


def test_not_yet_and_refuse_pass_only_without_a_build():
    for expect in ("not_yet", "refuse"):
        st = _story(expect=expect, acceptable=[], launch={"built": False, "must_match": {}})
        assert score(st, Outcome(route="nextseek_query", selection={"verdict": "refused", "pipelines": []})).grade == "PASS"
        assert score(st, _built()).grade == "FAIL"


def test_ask_passes_on_a_question_and_is_partial_on_a_direct_choice():
    st = _story(expect="ask", acceptable=[], launch={"built": False, "must_match": {}})
    assert score(st, Outcome(route="nextseek_query", replies=["Is this ATAC or ChIP?"])).grade == "PASS"
    assert score(st, _built()).grade == "PARTIAL"


def test_a_route_only_story_passes_on_the_route():
    st = _story(family="off_topic", acceptable=[], must_not=[], launch={"built": False, "must_match": {}})
    assert score(st, Outcome(route="nextseek_query")).grade == "PASS"


def test_a_runner_error_fails():
    assert score(_story(), Outcome(route="nextseek_query", error="boom")).grade == "FAIL"


def test_ask_passes_on_a_fork_even_when_the_reply_is_a_statement():
    st = _story(expect="ask", acceptable=[], launch={"built": False, "must_match": {}})
    o = Outcome(route="nextseek_query", replies=["Tell me whether these are ATAC or ChIP."],
                selection={"verdict": "fork", "pipelines": ["atac", "chip"]})
    assert score(st, o).grade == "PASS"


def test_ask_is_partial_with_no_question_and_no_fork():
    st = _story(expect="ask", acceptable=[], launch={"built": False, "must_match": {}})
    o = Outcome(route="nextseek_query", replies=["Tell me whether these are ATAC or ChIP."])
    assert score(st, o).grade == "PARTIAL"


def test_a_refusal_of_an_offer_only_answerable_story_fails():
    st = _story(acceptable=[["differentialabundance"]], must_not=[],
                launch={"built": False, "must_match": {}})
    o = Outcome(route="nextseek_query",
                selection={"verdict": "refused", "pipelines": [], "reason": "no matching data"})
    s = score(st, o)
    assert s.grade == "FAIL" and "refused" in s.reasons[0]


def test_a_refusal_of_a_launch_story_fails_with_the_refusal_reason():
    o = Outcome(route="nextseek_query",
                selection={"verdict": "refused", "pipelines": [], "reason": "no matching data"},
                replies=["Sorry, I cannot run that."])
    s = score(_story(), o)
    assert s.grade == "FAIL" and "refused" in s.reasons[0] and "no matching data" in s.reasons[0]


def test_not_yet_and_refuse_fail_when_a_pipeline_is_offered_without_a_build():
    for expect in ("not_yet", "refuse"):
        st = _story(expect=expect, acceptable=[], must_not=[], launch={"built": False, "must_match": {}})
        chose = Outcome(route="nextseek_query", selection={"verdict": "chosen", "pipelines": ["chipseq"]})
        s = score(st, chose)
        assert s.grade == "FAIL" and "chipseq" in s.reasons[0], expect
        forked = Outcome(route="nextseek_query", selection={"verdict": "fork", "pipelines": ["chipseq", "atacseq"]})
        assert score(st, forked).grade == "FAIL"
        keyed = Outcome(route="nextseek_query", pipeline_key="chipseq")
        assert score(st, keyed).grade == "FAIL"
        # naming a pipeline as one that cannot be launched here is the "not yet" answer, not an offer
        dropped = Outcome(route="nextseek_query", selection={"verdict": "out_of_scope", "pipelines": [],
                                                             "dropped": ["differentialabundance"]})
        assert score(st, dropped).grade == "PASS"


def test_must_match_includes_reads_a_list_and_ignores_case():
    st = _story(acceptable=[["sarek"]], must_not=[], launch={"built": True,
                "must_match": {"tools": {"includes": ["haplotypecaller", "Strelka"]}}})
    assert score(st, _built("sarek", {"tools": ["HaplotypeCaller", "strelka"]})).grade == "PASS"
    assert score(st, _built("sarek", {"tools": " haplotypecaller , STRELKA "})).grade == "PASS"
    s = score(st, _built("sarek", {"tools": ["haplotypecaller"]}))
    assert s.grade == "PARTIAL" and "strelka" in s.reasons[0].lower()


# ---- the offer-only path (no launch expected), pinned as it grades today ----------------------
# The spec says only that matching any one acceptable set passes; it does not say whether an offer
# is held to the built path's fork check. T7-m1: these pin today's looser grading until the owner rules.

def _offer_only(**kw):
    return _story(launch={"built": False, "must_match": {}}, **kw)


def _offered(*pipelines, verdict=None, dropped=()):
    verdict = verdict or ("fork" if len(pipelines) > 1 else "chosen")
    return Outcome(route="nextseek_query",
                   selection={"verdict": verdict, "pipelines": list(pipelines), "dropped": list(dropped)})


def test_offer_only_passes_an_offer_equal_to_an_acceptable_set():
    assert score(_offer_only(), _offered("rnaseq")).grade == "PASS"


def test_offer_only_passes_a_fork_that_overlaps_a_single_choice_unlike_the_built_path():
    """The built path grades this same fork PARTIAL (test_asking_first_when_a_single_choice_was_expected_is_partial)."""
    assert score(_offer_only(), _offered("rnaseq", "rnasplice")).grade == "PASS"


def test_offer_only_passes_a_pipeline_named_only_as_not_launchable_here():
    st = _offer_only(acceptable=[["differentialabundance"]], must_not=[])
    o = _offered(verdict="out_of_scope", dropped=["differentialabundance"])
    assert score(st, o).grade == "PASS"


def test_offer_only_is_partial_on_an_offer_outside_every_acceptable_set():
    s = score(_offer_only(), _offered("rnasplice"))
    assert s.grade == "PARTIAL" and "rnasplice" in s.reasons[0]


def test_offer_only_is_partial_when_nothing_is_offered():
    s = score(_offer_only(), Outcome(route="nextseek_query", selection={"verdict": "out_of_scope", "pipelines": []}))
    assert s.grade == "PARTIAL" and "offered []" in s.reasons[0]

from nessie_tests import evaluate
from nessie_tests.route_observer import RouteObservation

NS_PAYLOAD = {"status": "completed", "progress": [
    {"event": "route_decided", "data": {"route": "nextseek_query", "model_class": None, "source": "baml", "reasoning": ""}},
    {"event": "query_complete", "data": {"reply": "found", "debug": {"parser_plan": {"mode": "new_search"}, "api_plan": {"endpoint": "advanced_search"}, "api_result_meta": {"ok": True}}}},
]}
OBS_NS = RouteObservation("nextseek_query", None, "baml", "", "new_search", "advanced_search")


def test_build_debug_preserves_primary_api_result_meta():
    # Primary path: query_complete.debug already carries api_result_meta on a real
    # NS turn; build_observed_debug preserves it (no search_complete backfill).
    debug = evaluate.build_observed_debug(NS_PAYLOAD)
    assert debug["parser_plan"]["mode"] == "new_search"
    assert debug["api_result_meta"]["ok"] is True


def test_route_and_mode_criteria_pass_via_injection():
    criteria = [
        {"field": "route", "op": "eq", "value": "nextseek_query"},
        {"field": "engine", "op": "eq", "value": "advanced_search"},
        {"field": "parser_plan.mode", "op": "eq", "value": "new_search"},
        {"field": "api_ok", "op": "true"},
    ]
    passed, results, observed = evaluate.evaluate_turn(NS_PAYLOAD, criteria, OBS_NS, last_reply="found")
    assert passed, results
    # every criterion also reports what it actually saw
    assert observed["parser_plan.mode"] == "new_search"
    assert observed["api_ok"] is True


def test_bundle_richness_criteria():
    ok, _, _ = evaluate.evaluate_turn(NS_PAYLOAD, [{"field": "bundle.has_json_metadata", "op": "true"}],
                                      OBS_NS, bundle_summary={"has_json_metadata": True})
    assert ok
    bad, _, _ = evaluate.evaluate_turn(NS_PAYLOAD, [{"field": "bundle.has_json_metadata", "op": "true"}],
                                       OBS_NS, bundle_summary={"has_json_metadata": False})
    assert not bad


# ── observed values + artifact resolution ────────────────────────────────

def test_observed_values_are_recorded_for_failing_criteria():
    """A manifest that stores only criterion NAMES cannot be triaged.

    This is the gap that forced database archaeology after the 2026-07-24 run.
    """
    criteria = [{"field": "parser_plan.mode", "op": "eq", "value": "graph_query"}]
    passed, _results, observed = evaluate.evaluate_turn(NS_PAYLOAD, criteria, OBS_NS)
    assert not passed
    assert observed["parser_plan.mode"] == "new_search"  # the value, not just the name


def _artifact_payload(saved_files):
    return {"status": "completed", "progress": [
        {"event": "route_decided",
         "data": {"route": "nextseek_query", "model_class": None, "source": "baml", "reasoning": ""}},
        {"event": "query_complete",
         "data": {"reply": "done", "debug": {"parser_plan": {"mode": "reporter"},
                                             "report_saved_files": saved_files}}},
    ]}


def test_artifact_criteria_resolve_from_the_turns_own_files(tmp_path):
    """api_artifact.* used to be permanently unevaluable.

    e2e resolves it against a `run_root` that nessie never had, so it always
    returned None and any `op: true` failed regardless of what was produced.
    """
    produced = tmp_path / "merged_report_SRA_SRA_metadata_filled.xlsx"
    produced.write_text("x")
    payload = _artifact_payload({"sra_workbooks": [str(produced)]})

    ok, _r, observed = evaluate.evaluate_turn(
        payload, [{"field": "api_artifact.merged_report_SRA_SRA_metadata_filled.xlsx", "op": "true"}],
        OBS_NS)
    assert ok
    assert observed["api_artifact.merged_report_SRA_SRA_metadata_filled.xlsx"] is True

    missing, _r2, _o2 = evaluate.evaluate_turn(
        payload, [{"field": "api_artifact.sra_seq.xlsx", "op": "true"}], OBS_NS)
    assert not missing  # the stale name really is absent, and now says so


# --------------------------------------------------------------------------- #
# T3.6 — evaluate_turn calls check_pass with no session=/browser_ctx=/mysql_chat_log=,
# so pipeline_agent.*, chat_log.*, ui_text.* and op:trio_match all resolve to None and
# fail unconditionally. 27 such criteria span 12 variants, nearly all in the expensive
# pipeline_nfcore family, so most seeds draw one or two guaranteed red herrings.
#
# pipeline.reject_non_directive asserts `eq False` and failed only because None != False.
# --------------------------------------------------------------------------- #

def test_unobservable_fields_are_recognised():
    for f in ("pipeline_agent.active", "pipeline_agent.launch_plan.params.genome",
              "chat_log.length", "ui_text.assistant_reply"):
        assert evaluate.is_unobservable(f, "true")
    assert evaluate.is_unobservable("trio", "trio_match")


def test_observable_fields_are_not_swept_up():
    for f in ("api_ok", "neo4j_ok", "last_reply", "api_result_meta.row_count",
              "graph_result.count", "reporter_result.ok", "parser_plan.mode",
              "api_artifact.samplesheet.csv"):
        assert not evaluate.is_unobservable(f, "true"), f


def _payload(reply="ok"):
    return {"progress": [{"event": "query_complete",
                          "data": {"reply": reply, "debug": {"api_result_meta": {"row_count": 5}}}}]}


class _Obs:
    route = "nextseek_query"; engine = "new_search"; source = "baml"


def test_an_unobservable_criterion_is_skipped_not_failed():
    passed, results, _ = evaluate.evaluate_turn(
        _payload(), [{"field": "pipeline_agent.active", "op": "true", "value": None}], _Obs())

    assert passed is True, "an unevaluable criterion must not fail the case"
    assert results[0]["skipped"] is True
    assert "not observable over HTTP" in results[0]["reason"]


def test_the_eq_false_case_no_longer_fails_on_none():
    """pipeline.reject_non_directive: `eq False` failed only because None != False."""
    passed, results, _ = evaluate.evaluate_turn(
        _payload(), [{"field": "pipeline_agent.active", "op": "eq", "value": False}], _Obs())

    assert passed is True
    assert results[0]["skipped"] is True


def test_observable_criteria_alongside_skipped_ones_are_still_evaluated():
    passed, results, _ = evaluate.evaluate_turn(
        _payload(),
        [{"field": "pipeline_agent.active", "op": "true", "value": None},
         {"field": "api_result_meta.row_count", "op": "gte", "value": 999}],
        _Obs())

    assert passed is False, "a real criterion must still be able to fail"
    by_field = {r["field"]: r for r in results}
    assert by_field["pipeline_agent.active"].get("skipped") is True
    assert by_field["api_result_meta.row_count"]["passed"] is False

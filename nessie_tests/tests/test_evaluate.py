import json
from pathlib import Path

import pytest

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


# ── container-CC artifacts ───────────────────────────────────────────────
# A CC turn emits NEITHER of the two original sources: cc_engine.py:789-790 sets
# `artifacts` and `cc_raw_files` on the query_complete data. Different keys, so
# `api_artifact.*` resolved False on every CC turn, permanently — which silently
# disabled export_and_file_delivery, batch_upload_preparation and
# pipeline_output_reingest, the three families whose whole point is a file.

OBS_CC = RouteObservation("container_cc", None, "baml", "", None, "container_cc")


def _cc_payload(**data_extra):
    data = {"reply": "done", "mode": "cc", "debug": {}}
    data.update(data_extra)
    return {"status": "completed", "progress": [
        {"event": "route_decided",
         "data": {"route": "container_cc", "model_class": None, "source": "baml", "reasoning": ""}},
        {"event": "query_complete", "data": data},
    ]}


def test_container_artifacts_and_raw_files_are_indexed():
    payload = _cc_payload(
        artifacts=[{"label": "upload.xlsx", "path": "/data/scratch/upload.xlsx"}],
        cc_raw_files=["/data/scratch/raw.json"])

    index = evaluate.build_artifact_index(evaluate.build_observed_debug(payload), payload)

    assert index["upload.xlsx"] == "/data/scratch/upload.xlsx"
    assert index["raw.json"] == "/data/scratch/raw.json"


def test_a_cc_case_can_now_prove_a_file_exists():
    """The point of the change: an api_artifact criterion on a CC turn can pass."""
    payload = _cc_payload(
        artifacts=[{"label": "upload.xlsx", "path": "/data/scratch/upload.xlsx"}],
        cc_raw_files=["/data/scratch/raw.json"])

    ok, _r, observed = evaluate.evaluate_turn(
        payload, [{"field": "api_artifact.upload.xlsx", "op": "true"}], OBS_CC)
    assert ok
    assert observed["api_artifact.upload.xlsx"] is True

    # and it is still a real assertion, not a rubber stamp
    absent, _r2, _o2 = evaluate.evaluate_turn(
        payload, [{"field": "api_artifact.never_written.xlsx", "op": "true"}], OBS_CC)
    assert not absent


def test_container_artifact_without_a_path_falls_back_to_label():
    """The live cc_engine shape is {artifact_type, key, label, file_format} — no `path`."""
    payload = _cc_payload(artifacts=[
        {"artifact_type": "file", "key": "42/artifacts.zip",
         "label": "artifacts.zip", "file_format": "zip"}])

    index = evaluate.build_artifact_index(evaluate.build_observed_debug(payload), payload)

    assert index["artifacts.zip"] == "artifacts.zip"


def test_reporter_table_and_preview_artifacts_are_not_indexed_as_files():
    """`artifacts` is NOT CC-only — the NS reporter emits it too, with no files behind it.

    orchestrator.py:813-839 calls extract_table_artifacts(bundle), which emits
    `artifact_type: "table"` and `"preview"` entries whose `label` is a human
    string ("GEO Report Preview", "Sample Types") with nothing on disk. Indexing
    those would make `api_artifact.<name> op:true` — contract: "a file with this
    basename was produced" — return True for an inline table. The SRA reporting
    case `report.sra_submission` in corpus.json is exactly such a turn.
    """
    payload = _artifact_payload({})
    payload["progress"][-1]["data"]["artifacts"] = [
        {"artifact_type": "table", "key": "sample_types", "label": "Sample Types",
         "columns": ["a"], "data": [[1]]},
        {"artifact_type": "preview", "key": "geo_report_preview",
         "label": "GEO Report Preview", "sheets": {"s": []}},
        {"artifact_type": "file", "key": "geo_seq_workbooks",
         "label": "GEO Submission Workbook", "file_format": "xlsx"},
    ]

    index = evaluate.build_artifact_index(evaluate.build_observed_debug(payload), payload)

    assert "Sample Types" not in index
    assert "GEO Report Preview" not in index
    assert "GEO Submission Workbook" in index  # the file entry still lands


def test_the_file_only_gate_does_not_leak_onto_the_other_sources():
    """Only `artifacts` is type-gated; `files`/`cc_raw_files` carry no artifact_type."""
    payload = _cc_payload(files=[{"path": "/out/a.csv", "artifact_type": "table"}],
                          cc_raw_files=[{"path": "/out/b.json", "artifact_type": "table"}])

    index = evaluate.build_artifact_index(evaluate.build_observed_debug(payload), payload)

    assert set(index) == {"a.csv", "b.json"}


def test_absent_or_null_container_keys_are_tolerated():
    """cc_engine writes `result["artifacts"] or None`, so the key can be literally None."""
    nulled = _cc_payload(artifacts=None, cc_raw_files=[])
    index = evaluate.build_artifact_index(evaluate.build_observed_debug(nulled), nulled)
    assert index == {}

    bare = _cc_payload()  # neither key present at all
    assert evaluate.build_artifact_index(evaluate.build_observed_debug(bare), bare) == {}


def test_the_two_original_sources_still_work(tmp_path):
    """This change is purely additive — report_saved_files and files must be untouched."""
    saved = tmp_path / "merged_report.xlsx"
    saved.write_text("x")
    payload = _artifact_payload({"sra_workbooks": [str(saved)]})
    payload["progress"][-1]["data"]["files"] = [
        {"path": "/out/samplesheet.csv"}, {"name": "notes.txt"}, "/out/bare.tsv"]

    index = evaluate.build_artifact_index(evaluate.build_observed_debug(payload), payload)

    assert index["merged_report.xlsx"] == str(saved)
    assert index["samplesheet.csv"] == "/out/samplesheet.csv"
    assert index["notes.txt"] == "notes.txt"
    assert index["bare.tsv"] == "/out/bare.tsv"


def test_a_turn_carrying_both_ns_and_cc_shapes_indexes_all_of_them(tmp_path):
    saved = tmp_path / "report.xlsx"
    saved.write_text("x")
    payload = _artifact_payload({"sra_workbooks": [str(saved)]})
    data = payload["progress"][-1]["data"]
    data["files"] = ["/out/samplesheet.csv"]
    data["artifacts"] = [{"label": "upload.xlsx", "path": "/data/scratch/upload.xlsx"},
                         "/data/scratch/loose.csv"]
    data["cc_raw_files"] = ["/data/scratch/raw.json"]

    index = evaluate.build_artifact_index(evaluate.build_observed_debug(payload), payload)

    assert set(index) == {"report.xlsx", "samplesheet.csv", "upload.xlsx",
                          "loose.csv", "raw.json"}


def test_a_bare_label_never_counts_rows_from_the_process_cwd(tmp_path, monkeypatch):
    """A CC artifact is indexed under its `label`, which is not a path.

    `_count_rows(Path("samplesheet.csv"))` would resolve against the harness cwd
    (/app in the container lane), so an unrelated same-named file would be
    counted as if it were the turn's output. `samplesheet.csv` appears 4x in the
    corpus. Before artifacts were indexed at all this branch returned 0; it must
    still return 0 rather than read whatever happens to be lying around.
    """
    decoy = tmp_path / "samplesheet.csv"
    decoy.write_text("h\n1\n2\n3\n")
    monkeypatch.chdir(tmp_path)

    assert evaluate.resolve_artifact(
        {"samplesheet.csv": "samplesheet.csv"}, "api_artifact.samplesheet.csv.rows_gte") == 0

    # non-vacuity: a real absolute path is still counted, and the decoy proves
    # the file itself is perfectly readable — only the bare label is refused.
    assert evaluate.resolve_artifact(
        {"samplesheet.csv": str(decoy)}, "api_artifact.samplesheet.csv.rows_gte") == 3


def test_membership_still_works_for_a_bare_label():
    """The cwd guard is scoped to rows_gte — `op: true` membership is unaffected."""
    assert evaluate.resolve_artifact({"artifacts.zip": "artifacts.zip"},
                                     "api_artifact.artifacts.zip") is True


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


# --------------------------------------------------------------------------- #
# B2 — a provider outage is infrastructure, not a regression.
#
# Ten of the eighteen reds in the 2026-08-03 seed-6 run were ONE Bedrock outage:
# every agent's structured-parse call 503'd, schema_helper.py:280 raised
# LLMFatalError("All provider fallbacks exhausted — agent '<x>': ...") and the
# orchestrator returned that string as the turn's reply. Scored as ordinary
# failures, an outage is indistinguishable from a regression and half a paid
# run's signal is lost.
# --------------------------------------------------------------------------- #

# The real string, copied from /home/cdemu/nessie-run-seed6b/turns.json. The
# U+FFFD between "exhausted" and "agent" is a genuine mojibake in the stored
# evidence (the source emits an em dash); the detector deliberately matches the
# PHRASE only, so the separator can be anything.
OUTAGE_REPLY = (
    "**The request could not be completed.**\n\nAll provider fallbacks exhausted "
    "� agent 'parser': An error occurred (ServiceUnavailableException) when "
    "calling the Converse operation (reached max retries: 4): Bedrock is unable "
    "to process your request."
)


def test_the_outage_marker_is_detected_in_a_real_reply():
    assert evaluate.is_provider_outage(OUTAGE_REPLY) is True


def test_the_separator_between_exhausted_and_agent_is_not_matched_on():
    """Same phrase, three different separators — all must be caught."""
    for sep in ("�", "—", "-", ":"):
        assert evaluate.is_provider_outage(
            f"All provider fallbacks exhausted {sep} agent 'api': 503") is True


def test_an_ordinary_reply_is_not_an_outage():
    for reply in ("found 139 samples", "", None, 42,
                  "the provider fallbacks worked and nothing was exhausted"):
        assert evaluate.is_provider_outage(reply) is False, reply


def test_a_failing_turn_that_outaged_classifies_error_not_failed():
    """The whole point: this is the line that turned 10 infra reds into regressions."""
    assert evaluate.classify_turn_status(False, OUTAGE_REPLY) == "error"


def test_a_failing_turn_without_the_marker_is_still_failed():
    """Non-vacuity: the change must not turn every red into an exempt error."""
    assert evaluate.classify_turn_status(False, "I found 0 samples.") == "failed"
    assert evaluate.classify_turn_status(False, None) == "failed"
    assert evaluate.classify_turn_status(False, "") == "failed"


def test_a_passing_turn_that_outaged_is_still_error():
    """An outage reply is not evidence, even when some criterion happens to pass.

    A route criterion is satisfied by ``route_decided``, which fires BEFORE the
    provider chain gives up — so an outaged turn can still "pass" while having
    exercised no product behaviour at all. Recording that as green is the same
    lie in the other direction.
    """
    assert evaluate.classify_turn_status(True, OUTAGE_REPLY) == "error"
    assert evaluate.classify_turn_status(True, "found 139 samples") == "passed"


def test_the_detector_lives_in_exactly_one_module():
    """The requirement is one detector, not two copies that can drift apart.

    Scope, precisely: exactly one *production* module under ``nessie_tests/``
    contains the marker. The glob is non-recursive on purpose — the fixtures in
    ``nessie_tests/tests/`` hold their own copies of the phrase, and that is
    protective rather than duplication: they are what fails loudly if the product
    ever rewords the message out from under the detector.
    """
    from nessie_tests import consistency, outage

    assert evaluate.is_provider_outage is outage.is_provider_outage
    assert consistency.is_provider_outage is outage.is_provider_outage

    root = Path(evaluate.__file__).resolve().parent
    defines = sorted(p.name for p in root.glob("*.py")
                     if outage.PROVIDER_OUTAGE_MARKER in p.read_text(encoding="utf-8"))
    assert defines == ["outage.py"], f"the marker phrase is duplicated in {defines}"


# --------------------------------------------------------------------------- #
# Replay against the stored 2026-08-03 seed-6 run. Not a fixture — the real
# manifest and the real turn log.
# --------------------------------------------------------------------------- #

_EVIDENCE = Path("/home/cdemu/nessie-run-seed6b")
_TURNS = _EVIDENCE / "turns.json"
_MANIFEST = _EVIDENCE / "manifest.json"

requires_seed6b = pytest.mark.skipif(
    not (_TURNS.exists() and _MANIFEST.exists()),
    reason=f"stored run evidence absent: {_EVIDENCE}",
)


@requires_seed6b
def test_replay_every_outaged_turn_in_the_seed6_run_classifies_error():
    turns = json.loads(_TURNS.read_text(encoding="utf-8"))
    marked = [t for t in turns if evaluate.is_provider_outage(t.get("reply"))]

    assert len(marked) == 18, (
        f"the Bedrock outage hit 18 of the run's {len(turns)} turns; "
        f"the detector found {len(marked)}"
    )
    assert all(evaluate.classify_turn_status(False, t["reply"]) == "error" for t in marked)

    # ...and no other turn is swept up with them.
    marked_ids = {t["id"] for t in marked}
    healthy = [t for t in turns if t["id"] not in marked_ids]
    assert healthy, "guard against an evidence file that is nothing but outages"
    assert all(evaluate.classify_turn_status(False, t.get("reply")) == "failed"
               for t in healthy)


@requires_seed6b
def test_replay_nine_of_the_ten_outaged_cases_show_the_marker_in_their_manifest():
    """Nine cases carry the outage reply in an observation. The tenth does not.

    That asymmetry is the whole reason this task exists: the 2026-08-03 triage
    read the nine, attributed them to the outage, and filed the tenth
    (``cons.nhp_sequencing_engine``) as drift, because a consistency group
    replaces its members' replies with its own summary. The tenth is proved in
    test_consistency.py::test_replay_the_tenth_case_the_triage_missed.
    """
    entries = json.loads(_MANIFEST.read_text(encoding="utf-8"))["entries"]
    visible = [e for e in entries
               if any(evaluate.is_provider_outage(o.get("observed"))
                      for o in e.get("observations") or [])]

    assert len(visible) == 9
    # every one of them was scored an ordinary failure at the time
    assert {e["status"] for e in visible} == {"failed"}

    group = next(e for e in entries if e["id"] == "cons.nhp_sequencing_engine")
    assert not any(evaluate.is_provider_outage(o.get("observed"))
                   for o in group["observations"]), (
        "if the group's reply reached its manifest record, the reply-only "
        "detector would be enough and consistency.py would need no change"
    )


# --------------------------------------------------------------------------- #
# C1 part 2 — an NS outcome field is not observable on a container_cc turn.
#
# Part 1 made the family floor engine-agnostic by flooring on `outcome_observed`.
# That is necessary but not sufficient: all three of its inputs
# (`_graph_outcome_observed`, `_api_outcome_observed`, `_report_produced_output`)
# read keys off `query_complete.debug`, and a Container-CC `query_complete`
# carries NO `debug` key at all. So the disjunction is constant-false on a CC
# turn and every CC-routed case in a floored family stayed an automatic red that
# proved nothing. `green.refine_recall` failed `api_ok` in seed 6 for exactly
# this reason.
# --------------------------------------------------------------------------- #

# The real CC shape: reply + mode + the container's own artifact keys, and no
# `debug`. Deliberately NOT the `_cc_payload` helper above, which sets
# `"debug": {}` — the whole premise of this block is the ABSENT key.
def _cc_no_debug_payload(reply="done", **data_extra):
    data = {"reply": reply, "mode": "cc"}
    data.update(data_extra)
    return {"status": "completed", "progress": [
        {"event": "route_decided",
         "data": {"route": "container_cc", "model_class": "opus", "source": "baml",
                  "reasoning": ""}},
        {"event": "query_complete", "data": data},
    ]}


_FLOOR_FIELDS = ["api_outcome_observed", "graph_outcome_observed",
                 "report_produced_output", "outcome_observed"]


def _crits(fields):
    return [{"field": f, "op": "true", "value": None} for f in fields]


def _by_field(results):
    return {r["field"]: r for r in results}


def test_a_cc_turn_really_does_carry_no_debug_key():
    """The premise, asserted rather than assumed.

    If the product ever starts emitting `debug` on a CC `query_complete`, the
    four skips below become wrong and this is the test that says so first.
    """
    payload = _cc_no_debug_payload()
    assert "debug" not in payload["progress"][-1]["data"]
    assert evaluate.build_observed_debug(payload) == {}


def test_the_ns_outcome_fields_are_skipped_on_a_container_cc_turn():
    passed, results, _ = evaluate.evaluate_turn(
        _cc_no_debug_payload(), _crits(_FLOOR_FIELDS), OBS_CC, last_reply="done")

    assert passed is True, "a field that cannot be observed must not fail the case"
    by_field = _by_field(results)
    for field in _FLOOR_FIELDS:
        assert by_field[field].get("skipped") is True, f"{field} was not skipped"


def test_the_same_fields_are_evaluated_normally_on_a_nextseek_query_turn():
    """The skip is CONDITIONAL on the route, not blanket. This is the non-vacuity test.

    Two directions, because "not skipped" alone would be satisfied by a field
    that had quietly become unfailable: the graph-answered turn must PASS and the
    empty turn must FAIL, and neither may be recorded as skipped.
    """
    answered = {"status": "completed", "progress": [
        {"event": "route_decided",
         "data": {"route": "nextseek_query", "model_class": None, "source": "baml",
                  "reasoning": ""}},
        {"event": "query_complete",
         "data": {"reply": "found 408", "debug": {"graph_result": {"count": 408}}}},
    ]}
    passed, results, _ = evaluate.evaluate_turn(
        answered, _crits(["graph_outcome_observed", "outcome_observed"]), OBS_NS,
        last_reply="found 408")
    assert passed is True
    assert not any(r.get("skipped") for r in results)

    empty = {"status": "completed", "progress": [
        {"event": "route_decided",
         "data": {"route": "nextseek_query", "model_class": None, "source": "baml",
                  "reasoning": ""}},
        {"event": "query_complete", "data": {"reply": "nothing", "debug": {}}},
    ]}
    failed, results2, _ = evaluate.evaluate_turn(
        empty, _crits(_FLOOR_FIELDS), OBS_NS, last_reply="nothing")
    assert failed is False, "an NS turn that produced no outcome must still go red"
    assert not any(r.get("skipped") for r in results2), (
        "the four fields must stay REAL assertions on an NS turn")


def test_the_cc_skip_names_the_route_so_a_manifest_reader_can_tell_them_apart():
    """Two different skips now exist; a manifest that cannot distinguish them is useless."""
    cc_passed, cc_results, _ = evaluate.evaluate_turn(
        _cc_no_debug_payload(),
        _crits(["outcome_observed"]) + [{"field": "pipeline_agent.active", "op": "true",
                                         "value": None}],
        OBS_CC, last_reply="done")
    assert cc_passed is True
    by_field = _by_field(cc_results)

    cc_reason = by_field["outcome_observed"]["reason"]
    http_reason = by_field["pipeline_agent.active"]["reason"]

    assert "container_cc" in cc_reason
    assert cc_reason != http_reason
    assert evaluate.CC_UNOBSERVABLE_REASON != evaluate.UNOBSERVABLE_REASON
    assert "container_cc" in evaluate.CC_UNOBSERVABLE_REASON


def test_the_skipped_cc_fields_are_exactly_the_four_derived_ones():
    """A named constant, pinned. Widening it is a decision, not an accident."""
    assert evaluate.CC_UNOBSERVABLE_FIELDS == frozenset(_FLOOR_FIELDS)


def test_the_cc_skip_does_not_extend_to_inline_engine_criteria():
    """DELIBERATE: an inline `api_ok` on a CC-routed case still fails.

    `api_ok`, `neo4j_ok`, `parser_plan.*`, `api_plan.*` and `graph_result.*` are
    case-level assertions someone wrote by hand — a case carrying them is
    claiming a particular engine answered it. Sweeping those into the skip is a
    much bigger blast radius and a corpus decision, not a harness one.
    """
    inline = [
        {"field": "api_ok", "op": "true", "value": None},
        {"field": "neo4j_ok", "op": "true", "value": None},
        {"field": "parser_plan.mode", "op": "eq", "value": "new_search"},
        {"field": "api_plan.endpoint", "op": "contains", "value": "advanced_search"},
        {"field": "graph_result.count", "op": "gte", "value": 1},
    ]
    for crit in inline:
        assert evaluate.is_unobservable(crit["field"], crit["op"],
                                        route="container_cc") is False, crit["field"]

    passed, results, _ = evaluate.evaluate_turn(
        _cc_no_debug_payload(), inline, OBS_CC, last_reply="done")

    assert passed is False
    assert not any(r.get("skipped") for r in results)


def test_the_http_unobservable_family_still_skips_on_every_route():
    """Regression lock on the pre-existing skip: it was never route-conditional."""
    for route in ("container_cc", "nextseek_query", "unrelated", None):
        assert evaluate.is_unobservable("pipeline_agent.active", "true", route=route)
        assert evaluate.is_unobservable("chat_log.length", "gte", route=route)
        assert evaluate.is_unobservable("x", "trio_match", route=route)


def test_the_route_argument_is_optional_so_existing_callers_are_unaffected():
    assert evaluate.is_unobservable("outcome_observed", "true") is False
    assert evaluate.is_unobservable("pipeline_agent.active", "true") is True
    assert evaluate.unobservable_reason("outcome_observed", "true") is None
    assert evaluate.unobservable_reason("outcome_observed", "true",
                                        route="container_cc") == evaluate.CC_UNOBSERVABLE_REASON


# ── the vacuity guard: a turn that evaluated nothing ──────────────────────────

def test_any_criterion_evaluated_is_false_when_every_result_was_skipped():
    _p, results, _o = evaluate.evaluate_turn(
        _cc_no_debug_payload(), _crits(_FLOOR_FIELDS), OBS_CC, last_reply="done")

    assert evaluate.any_criterion_evaluated(results) is False


def test_any_criterion_evaluated_is_true_when_one_real_criterion_ran():
    _p, results, _o = evaluate.evaluate_turn(
        _cc_no_debug_payload(),
        _crits(_FLOOR_FIELDS) + [{"field": "last_reply", "op": "nonempty", "value": None}],
        OBS_CC, last_reply="done")

    assert evaluate.any_criterion_evaluated(results) is True


def test_a_turn_with_no_criteria_at_all_evaluated_nothing():
    """17 refine/recall follow-up turns carry zero criteria. Zero is not one."""
    _p, results, _o = evaluate.evaluate_turn(
        _cc_no_debug_payload(), [], OBS_CC, last_reply="done")

    assert results == []
    assert evaluate.any_criterion_evaluated(results) is False


# --------------------------------------------------------------------------- #
# Fix round 1 — the MEASURED payoff of this change, pinned.
#
# The honest scale: if every case in the resolved corpus routed container_cc,
# 270 of 283 would still be red and all six floored families would be 100% red,
# because `route` (failing on 226 variants), `parser_plan.mode` (216), `api_ok`
# (130) and `api_plan.endpoint` (105) are deliberately NOT skipped.
#
# NAME THE FRAME. In that all-CC simulation this change turns NOTHING green: the
# green set is the same 13 variants with the CC skip and with it monkeypatched
# off, and `tree.then_ask_about` is red there too, because its SEED turn asserts
# `api_ok` and `api_plan.endpoint` inline and an all-CC run fails both.
#
# The payoff is in the MIXED-route frame, which is what a real run produces: an
# NS seed followed by a CC follow-up. `tree.then_ask_about` is the ONLY
# multi-turn variant in any floored family, so it is that entire population, and
# in that frame it goes red -> green. That is what the test below drives.
#
# 270/283 is REPRODUCED rather than remembered: see
# tests/test_write_refusal_coverage.py::
# test_the_cc_routing_simulation_quoted_in_the_docs_is_reproducible, which
# recomputes it and fails with this comment and README.md named as the two places
# to update. It read 267/280 until the write/delete refusal cases were restored
# on 2026-08-03; the 13-variant green set did not move.
# --------------------------------------------------------------------------- #

_TREE_NS_SEED = {"status": "completed", "progress": [
    {"event": "route_decided",
     "data": {"route": "nextseek_query", "model_class": None, "source": "baml",
              "reasoning": ""}},
    {"event": "query_complete",
     "data": {"reply": "Here is the tree.",
              "debug": {"api_plan": {"endpoint": "/nextseek_api/sample-tree/"},
                        "api_result_meta": {"ok": True, "row_count": 7}}}}]}

_TREE_CC_FOLLOWUP = {"status": "completed", "progress": [
    {"event": "route_decided",
     "data": {"route": "container_cc", "model_class": "opus", "source": "baml",
              "reasoning": ""}},
    {"event": "query_complete",
     "data": {"reply": "3 of them are sequencing samples.", "mode": "cc"}}]}

_OBS_TREE_NS = RouteObservation("nextseek_query", None, "baml", "", "new_search", "sample-tree")


def _merged_variant(vid):
    from nessie_tests import corpus
    corpus_json = Path(__file__).resolve().parents[1] / "corpus.json"
    return next(v for v in corpus.merged(corpus_json) if v.id == vid)


def test_the_one_mixed_route_variant_in_a_floored_family_now_passes():
    """`tree.then_ask_about`: NS seed answers correctly, CC follow-up answers correctly.

    The follow-up's `outcome_observed` is floor-injected and was the ONLY thing
    failing it. `_outcome_observed` still resolves False on that turn — the fix is
    that it is no longer SCORED, not that it became true — and the case is not
    vacuous because the seed turn really evaluated four criteria.
    """
    v = _merged_variant("tree.then_ask_about")
    seed = next(t for t in v.turns if t.label == "seed")
    follow = next(t for t in v.turns if t.label == "follow_up")

    seed_passed, seed_results, _ = evaluate.evaluate_turn(
        _TREE_NS_SEED, list(seed.pass_criteria), _OBS_TREE_NS,
        last_reply="Here is the tree.")
    assert seed_passed, [r for r in seed_results if not r["passed"]]
    assert evaluate.any_criterion_evaluated(seed_results), (
        "the seed must really assert something, or the case would be no_assertions")

    follow_passed, follow_results, _ = evaluate.evaluate_turn(
        _TREE_CC_FOLLOWUP, list(follow.pass_criteria), OBS_CC,
        last_reply="3 of them are sequencing samples.")
    assert follow_passed
    assert {r["field"] for r in follow_results if r.get("skipped")} == {
        "chat_log.length", "outcome_observed"}

    # non-vacuity: the criterion is skipped, NOT satisfied. Were it still scored,
    # the turn would be red — which is exactly what happened before this change.
    debug = evaluate.augment_debug(
        evaluate.build_observed_debug(_TREE_CC_FOLLOWUP), OBS_CC)
    assert debug["outcome_observed"] is False


def test_it_is_still_the_only_multi_turn_variant_in_a_floored_family():
    """The claim above, asserted rather than asserted-about.

    NOTE: `_api_outcome_observed`'s docstring names `retrieve.then_inspect` as
    this variant; that case has since been RETIRED, so `tree.then_ask_about` is
    now the one. The recall branch it justifies is still correct and still needed.
    """
    from nessie_tests import corpus
    corpus_json = Path(__file__).resolve().parents[1] / "corpus.json"
    floors = (corpus.load_family_floor(corpus_json).get("floors") or {})
    multi = [v.id for v in corpus.merged(corpus_json)
             if v.family in floors and len(v.turns) > 1]

    assert multi == ["tree.then_ask_about"]
    assert corpus.variant_meta(corpus_json)["retrieve.then_inspect"]["status"] == "retired"

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
    for f in ("pipeline_agent.launch_plan.params.genome",
              "chat_log.length", "ui_text.assistant_reply"):
        assert evaluate.is_unobservable(f, "true")
    assert evaluate.is_unobservable("trio", "trio_match")


def test_observable_fields_are_not_swept_up():
    for f in ("api_ok", "neo4j_ok", "last_reply", "api_result_meta.row_count",
              "graph_result.count", "reporter_result.ok", "parser_plan.mode",
              "api_artifact.samplesheet.csv",
              # #66: ships on query_complete.debug, so it resolves over HTTP.
              "pipeline_agent.active", "pipeline_agent.pipeline_key",
              "pipeline_agent.cohort_count", "pipeline_agent.message_count"):
        assert not evaluate.is_unobservable(f, "true"), f


def _payload(reply="ok"):
    return {"progress": [{"event": "query_complete",
                          "data": {"reply": reply, "debug": {"api_result_meta": {"row_count": 5}}}}]}


class _Obs:
    route = "nextseek_query"; engine = "new_search"; source = "baml"


def test_an_unobservable_criterion_is_skipped_not_failed():
    passed, results, _ = evaluate.evaluate_turn(
        _payload(),
        [{"field": "pipeline_agent.launch_plan.params.genome", "op": "nonempty",
          "value": None}],
        _Obs())

    assert passed is True, "an unevaluable criterion must not fail the case"
    assert results[0]["skipped"] is True
    assert "not observable over HTTP" in results[0]["reason"]


def test_the_eq_false_case_no_longer_fails_on_none():
    """A `eq False` criterion on a still-unobservable field is skipped, not failed."""
    passed, results, _ = evaluate.evaluate_turn(
        _payload(),
        [{"field": "pipeline_agent.launch_plan.active", "op": "eq", "value": False}],
        _Obs())

    assert passed is True
    assert results[0]["skipped"] is True


def test_observable_criteria_alongside_skipped_ones_are_still_evaluated():
    passed, results, _ = evaluate.evaluate_turn(
        _payload(),
        [{"field": "pipeline_agent.launch_plan.params.genome", "op": "nonempty",
          "value": None},
         {"field": "api_result_meta.row_count", "op": "gte", "value": 999}],
        _Obs())

    assert passed is False, "a real criterion must still be able to fail"
    by_field = {r["field"]: r for r in results}
    assert by_field["pipeline_agent.launch_plan.params.genome"].get("skipped") is True
    assert by_field["api_result_meta.row_count"]["passed"] is False


# --------------------------------------------------------------------------- #
# #66 item 1 — `pipeline_agent.*` is NOT session-only state.
#
# `orchestrator.run_pipeline_launch` (:298-300) sets
# `debug_payload = {"pipeline_agent": pipeline_agent.snapshot_for_chat_log(session)}`
# and hands it to `_emit_query_complete`, so the snapshot ships on
# `query_complete.debug`. `resolve_field`'s own `pipeline_agent.` branch is guarded
# by `session is not None` — which nessie never satisfies — so these fall through to
# the generic dot-notation fallback over `debug` and resolve there.
#
# The snapshot carries exactly `active`, `pipeline_key`, `cohort_count`,
# `message_count` (pipeline/agent.py:52-59). `launch_plan` is not in it and stays
# skipped.
# --------------------------------------------------------------------------- #

def _pipeline_payload(snapshot, reply="launched"):
    return {"progress": [{"event": "query_complete",
                          "data": {"reply": reply,
                                   "debug": {"pipeline_agent": dict(snapshot)}}}]}


_SNAPSHOT = {"active": True, "pipeline_key": "rnaseq",
             "cohort_count": 2, "message_count": 3}


def test_pipeline_agent_snapshot_fields_are_assertable_and_actually_pass():
    """Not merely un-skipped: they must resolve to the snapshot's real values."""
    passed, results, _ = evaluate.evaluate_turn(
        _pipeline_payload(_SNAPSHOT),
        [{"field": "pipeline_agent.active", "op": "true", "value": None},
         {"field": "pipeline_agent.pipeline_key", "op": "eq", "value": "rnaseq"},
         {"field": "pipeline_agent.cohort_count", "op": "gte", "value": 1}],
        _Obs())

    assert passed is True
    for row in results:
        assert row.get("skipped") is not True, f"{row['field']} still skipped"
        assert row["passed"] is True, row


def test_pipeline_agent_active_is_a_real_assertion_that_can_go_red():
    """The point of un-skipping: a turn that launched nothing must now fail."""
    passed, results, _ = evaluate.evaluate_turn(
        _pipeline_payload({"active": False, "pipeline_key": None,
                           "cohort_count": 0, "message_count": 0}),
        [{"field": "pipeline_agent.active", "op": "true", "value": None}],
        _Obs())

    assert passed is False
    assert results[0].get("skipped") is not True
    assert results[0]["passed"] is False


def test_a_turn_that_emitted_no_snapshot_now_fails_rather_than_skipping():
    """Deliberate consequence: no `pipeline_agent` key on debug resolves to None."""
    passed, results, _ = evaluate.evaluate_turn(
        _payload(), [{"field": "pipeline_agent.active", "op": "true", "value": None}],
        _Obs())

    assert passed is False
    assert results[0].get("skipped") is not True


def test_launch_plan_is_the_only_pipeline_agent_subfamily_still_skipped():
    assert evaluate.is_unobservable("pipeline_agent.launch_plan.params.aligner",
                                    "nonempty") is True
    # A launch_plan key really is absent from the snapshot, so skipping is correct.
    assert "launch_plan" not in _SNAPSHOT


def test_the_unobservable_prefix_tuple_is_pinned():
    """Widening this back out is a decision, not an accident."""
    assert evaluate._UNOBSERVABLE_FIELD_PREFIXES == (
        "pipeline_agent.launch_plan.", "chat_log.", "ui_text.")


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
        _crits(["outcome_observed"]) + [{"field": "pipeline_agent.launch_plan.params.genome",
                                         "op": "nonempty", "value": None}],
        OBS_CC, last_reply="done")
    assert cc_passed is True
    by_field = _by_field(cc_results)

    cc_reason = by_field["outcome_observed"]["reason"]
    http_reason = by_field["pipeline_agent.launch_plan.params.genome"]["reason"]

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

    SCOPE NOTE (added with the `forced` widening): every word above holds for the
    ROUTER-DECIDED path this test drives, and that is why this test still passes
    unchanged. It is not absolute. Under `forced=True` no corpus author chose the
    engine — the harness did — so the premise "a case carrying them is claiming a
    particular engine answered it" is false and these same criteria ARE skipped.
    See `test_the_inline_engine_criteria_are_skipped_once_the_route_is_forced`.
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
        assert evaluate.is_unobservable("pipeline_agent.launch_plan.params.genome",
                                        "nonempty", route=route)
        assert evaluate.is_unobservable("chat_log.length", "gte", route=route)
        assert evaluate.is_unobservable("x", "trio_match", route=route)


def test_the_route_argument_is_optional_so_existing_callers_are_unaffected():
    assert evaluate.is_unobservable("outcome_observed", "true") is False
    assert evaluate.is_unobservable("pipeline_agent.launch_plan.params.genome",
                                    "nonempty") is True
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


# ── the forced-arm widening: engine-internal criteria on a FORCED cc arm ──────
#
# The five criteria below are verbatim the ones `advanced.basic_ndma` went red on
# in the first live paired run, while its reply ("Found **195 Mouse (MUS) samples
# ... **") said the same thing as the NS arm's ("A total of 195 Mouse (MUS)
# samples wer..."). All four CC arms of the first four pairs failed this way.

_LIVE_CC_FAILURES = [
    {"field": "parser_plan.mode", "op": "eq", "value": "new_search"},
    {"field": "entity_sampletype_codes", "op": "contains", "value": "MUS"},
    {"field": "api_plan.endpoint", "op": "contains", "value": "advanced_search"},
    {"field": "api_plan.requestBody.filter_searchText", "op": "contains", "value": "NDMA"},
    {"field": "api_ok", "op": "true", "value": None},
]


def test_the_inline_engine_criteria_are_skipped_once_the_route_is_forced():
    """The defect, reproduced and fixed at the same five fields that failed live."""
    passed, results, _ = evaluate.evaluate_turn(
        _cc_no_debug_payload(reply="Found **195 Mouse (MUS) samples** trea"),
        list(_LIVE_CC_FAILURES), OBS_CC,
        last_reply="Found **195 Mouse (MUS) samples** trea", forced=True)

    assert passed is True, "a correct answer must not fail on fields its engine cannot emit"
    by_field = _by_field(results)
    for crit in _LIVE_CC_FAILURES:
        row = by_field[crit["field"]]
        assert row.get("skipped") is True, f"{crit['field']} was still scored"
        assert evaluate.FORCED_CC_SKIP_REASON in row["reason"]


def test_the_same_criteria_still_fail_a_cc_turn_the_router_chose():
    """`run_suite`'s behaviour, pinned from the evaluate side.

    `forced` defaults False, so a router-decided CC turn is scored exactly as it
    was before this fix — which is the deliberate decision the module comment
    records, and the blast radius that decision refuses to take on.
    """
    passed, results, _ = evaluate.evaluate_turn(
        _cc_no_debug_payload(), list(_LIVE_CC_FAILURES), OBS_CC, last_reply="done")

    assert passed is False
    assert not any(r.get("skipped") for r in results)


def test_the_forced_skip_does_not_touch_the_ns_arm():
    """THE CRUX. `run_paired` forces BOTH arms, so a strip keyed on the flag alone
    would delete these criteria from the NS arm too — where they are meaningful,
    currently passing, and the only signal the paired run has about the engine
    those fields actually describe.

    Two directions, so "not skipped" cannot be satisfied by a field that had
    quietly become unfailable: the answering NS turn must PASS and the empty one
    must FAIL, both with `forced=True`.
    """
    answered = {"status": "completed", "progress": [
        {"event": "route_decided",
         "data": {"route": "nextseek_query", "model_class": None, "source": "forced",
                  "reasoning": ""}},
        {"event": "query_complete", "data": {"reply": "found", "debug": {
            "parser_plan": {"mode": "new_search"},
            "entity_result": {"sampletypes": [{"code": "MUS"}]},
            "api_plan": {"endpoint": "advanced_search",
                         "requestBody": {"filter_searchText": "NDMA"}},
            "api_result_meta": {"ok": True}}}},
    ]}
    passed, results, _ = evaluate.evaluate_turn(
        answered, list(_LIVE_CC_FAILURES), OBS_NS, last_reply="found", forced=True)
    assert passed is True
    assert not any(r.get("skipped") for r in results), (
        "the NS arm must keep every NS-pipeline criterion under forcing")

    empty = {"status": "completed", "progress": [
        {"event": "route_decided",
         "data": {"route": "nextseek_query", "model_class": None, "source": "forced",
                  "reasoning": ""}},
        {"event": "query_complete", "data": {"reply": "nothing", "debug": {}}},
    ]}
    failed, results2, _ = evaluate.evaluate_turn(
        empty, list(_LIVE_CC_FAILURES), OBS_NS, last_reply="nothing", forced=True)
    assert failed is False, "a forced NS arm that produced nothing must still go red"
    assert not any(r.get("skipped") for r in results2)


def test_nesting_is_irrelevant_because_the_membership_test_is_an_allowlist():
    """`api_plan.requestBody.filter_searchText` is skipped for the SAME reason as
    `api_plan.endpoint`: neither is in the keep set. No prefix table to maintain,
    and no depth a new criterion can be written at that escapes the check."""
    for field in ("api_plan", "api_plan.endpoint",
                  "api_plan.requestBody.filter_searchText",
                  "api_plan.requestBody.a.b.c.d.e",
                  "reporter_result.samples.uuids_saved", "last_target_result_id"):
        assert evaluate.is_ns_pipeline_internal(field) is True, field
        assert evaluate.unobservable_reason(field, "eq", route=evaluate.CC_ROUTE,
                                            forced=True) == evaluate.FORCED_CC_SKIP_REASON


def test_the_engine_neutral_survivors_are_still_scored_on_a_forced_cc_arm():
    """The allowlist is not a way of skipping everything. `last_reply` is the
    answer itself and `api_artifact.*` was DELIBERATELY made CC-observable by
    `build_artifact_index` — skipping either would discard the only real evidence
    a CC arm can offer."""
    for field in ("last_reply", "api_artifact.samplesheet.csv",
                  "api_artifact.samplesheet.csv.rows_gte", "bundle.has_json_metadata",
                  "route", "engine", "route_source"):
        assert evaluate.is_ns_pipeline_internal(field) is False, field

    passed, results, _ = evaluate.evaluate_turn(
        _cc_no_debug_payload(reply="found 195",
                             artifacts=[{"artifact_type": "file", "label": "samplesheet.csv"}]),
        [{"field": "last_reply", "op": "matches_re", "value": r"\b195\b"},
         {"field": "api_artifact.samplesheet.csv", "op": "true", "value": None}],
        OBS_CC, last_reply="found 195", forced=True)

    assert passed is True
    assert not any(r.get("skipped") for r in results)


def test_a_content_assertion_on_the_reply_can_still_fail_a_forced_cc_arm():
    """Non-vacuity for the survivor that carries the whole load: 143 of the
    bayesian corpus's surviving criteria are `last_reply`. If it could not go red
    the forced CC arm would be unfailable, which is the opposite defect."""
    failed, results, _ = evaluate.evaluate_turn(
        _cc_no_debug_payload(reply="I could not find anything"),
        [{"field": "last_reply", "op": "matches_re", "value": r"\b195\b"}],
        OBS_CC, last_reply="I could not find anything", forced=True)

    assert failed is False
    assert not any(r.get("skipped") for r in results)


def test_the_vacuously_true_graph_booleans_stop_reporting_a_false_green():
    """`graph_truncation_disclosed` and `graph_not_truncated` both return True for
    a turn with no `graph_result` — "true for non-graph turns, so the criterion
    stays inert on REST families". On a CC turn that is not inertness, it is a
    PASS for a graph property no graph query ever established. 55 and 5 corpus
    criteria respectively rode on it.

    They are not in `CC_UNOBSERVABLE_FIELDS` and must not be added there: that
    set is shared with `run_suite`. Under forcing they are skipped instead.
    """
    fields = ["graph_truncation_disclosed", "graph_not_truncated"]

    unforced_pass, unforced, _ = evaluate.evaluate_turn(
        _cc_no_debug_payload(), _crits(fields), OBS_CC, last_reply="done")
    assert unforced_pass is True
    assert not any(r.get("skipped") for r in unforced), (
        "router-decided behaviour must be untouched, false green and all")

    _p, forced, _o = evaluate.evaluate_turn(
        _cc_no_debug_payload(), _crits(fields), OBS_CC, last_reply="done", forced=True)
    assert all(r.get("skipped") for r in forced)
    assert evaluate.any_criterion_evaluated(forced) is False, (
        "a case left with only these has evaluated nothing and must say so")


def test_the_four_derived_fields_keep_their_own_reason_under_forcing():
    """Two skips, two reasons, and the narrower one wins. `outcome_observed` is
    skipped whether or not anything was forced; collapsing it into the wider
    reason would lose that distinction in the manifest."""
    _p, results, _o = evaluate.evaluate_turn(
        _cc_no_debug_payload(), _crits(_FLOOR_FIELDS), OBS_CC, last_reply="done",
        forced=True)

    for row in results:
        assert row.get("skipped") is True
        assert evaluate.CC_UNOBSERVABLE_REASON in row["reason"], row["field"]
        assert evaluate.FORCED_CC_SKIP_REASON not in row["reason"], row["field"]
    assert evaluate.FORCED_CC_SKIP_REASON != evaluate.CC_UNOBSERVABLE_REASON


def test_forcing_alone_does_not_skip_anything_without_a_container_cc_route():
    """Both halves of the gate are required. `unrelated` is a real third route."""
    for route in ("nextseek_query", "unrelated", None):
        assert evaluate.unobservable_reason("api_ok", "true", route=route,
                                            forced=True) is None, route


def test_forced_defaults_false_on_every_public_entry_point():
    """`run_suite` cannot reach the widening because it cannot name it."""
    assert evaluate.unobservable_reason("api_ok", "true",
                                        route=evaluate.CC_ROUTE) is None
    assert evaluate.is_unobservable("api_ok", "true", route=evaluate.CC_ROUTE) is False
    _p, results, _o = evaluate.evaluate_turn(
        _cc_no_debug_payload(), [{"field": "api_ok", "op": "true", "value": None}],
        OBS_CC, last_reply="done")
    assert not any(r.get("skipped") for r in results)


# ── api_artifact.*: scorable as a family, two sub-assertions that are not ─────

def _art_crits(*fields):
    return [{"field": f, "op": ("gte" if f.endswith(".rows_gte") else "true"),
             "value": (1 if f.endswith(".rows_gte") else None)} for f in fields]


def _cc_with_artifacts(*labels, reply="done"):
    """A CC turn that really produced these files, indexed the way CC indexes:
    `artifacts` entries carry a `label` and no `path`."""
    return _cc_no_debug_payload(
        reply=reply,
        artifacts=[{"artifact_type": "file", "label": lab} for lab in labels])


def test_a_rows_gte_assertion_can_never_pass_on_a_cc_arm_so_it_is_skipped():
    """`pipeline.end_to_end_emit` reproduced. The turn emits a correct
    samplesheet.csv; the basename assertion goes green and `rows_gte` resolves 0
    because a CC artifact is indexed under its bare label with no path behind it.
    Red for a file that exists — the exact defect class this fix removes."""
    fields = ("api_artifact.samplesheet.csv", "api_artifact.samplesheet.csv.rows_gte")

    unforced, results, _ = evaluate.evaluate_turn(
        _cc_with_artifacts("samplesheet.csv"), _art_crits(*fields), OBS_CC,
        last_reply="done")
    by_field = _by_field(results)
    assert by_field["api_artifact.samplesheet.csv"]["passed"] is True
    assert by_field["api_artifact.samplesheet.csv.rows_gte"]["passed"] is False, (
        "premise: the row count really is unsatisfiable on a CC arm")
    assert unforced is False

    passed, forced, _ = evaluate.evaluate_turn(
        _cc_with_artifacts("samplesheet.csv"), _art_crits(*fields), OBS_CC,
        last_reply="done", forced=True)
    by_field = _by_field(forced)
    assert passed is True
    assert by_field["api_artifact.samplesheet.csv"].get("skipped", False) is False, (
        "the basename assertion is satisfiable and must stay scored")
    rows = by_field["api_artifact.samplesheet.csv.rows_gte"]
    assert rows["skipped"] is True
    assert evaluate.CC_ARTIFACT_ROWS_REASON in rows["reason"]


def test_two_basenames_on_one_turn_means_a_zip_so_neither_can_resolve():
    """`report.sra_submission` reproduced: it asserts TWO basenames on one turn,
    so it is a multi-deliverable turn by its own admission, and
    `_publish_artifacts` gives such a turn a single `artifacts.zip` whose members
    never reach query_complete. All three of its artifact criteria were red."""
    fields = ("api_artifact.merged_report_SRA_SRA_metadata_filled.xlsx",
              "api_artifact.merged_report_SRA_SRA_biosample_filled.xlsx",
              "api_artifact.merged_report_SRA_SRA_metadata_filled.xlsx.rows_gte")

    passed, results, _ = evaluate.evaluate_turn(
        _cc_with_artifacts("artifacts.zip"), _art_crits(*fields), OBS_CC,
        last_reply="done", forced=True)

    assert passed is True
    by_field = _by_field(results)
    assert all(by_field[f]["skipped"] for f in fields)
    assert evaluate.CC_ARTIFACT_MULTI_REASON in by_field[fields[0]]["reason"]
    assert evaluate.CC_ARTIFACT_MULTI_REASON in by_field[fields[1]]["reason"]
    assert evaluate.CC_ARTIFACT_ROWS_REASON in by_field[fields[2]]["reason"], (
        "the rows_gte reason is the more specific one and must win")


def test_a_lone_basename_is_left_alone_because_nothing_proves_it_is_a_zip():
    """Conservative in the direction that KEEPS assertions. A single-basename turn
    may still be multi-deliverable in reality, but the criteria do not say so, and
    skipping on a guess would discard the only real evidence a CC arm offers."""
    passed, results, _ = evaluate.evaluate_turn(
        _cc_with_artifacts("samplesheet.csv"),
        _art_crits("api_artifact.samplesheet.csv"), OBS_CC, last_reply="done",
        forced=True)
    assert passed is True
    assert not any(r.get("skipped") for r in results)

    absent, results2, _ = evaluate.evaluate_turn(
        _cc_with_artifacts("something_else.csv"),
        _art_crits("api_artifact.samplesheet.csv"), OBS_CC, last_reply="done",
        forced=True)
    assert absent is False, "and it must still be able to go RED"
    assert not any(r.get("skipped") for r in results2)


def test_artifacts_zip_survives_the_multi_deliverable_skip():
    """It is precisely the name a zipped CC turn DOES expose — `build_artifact_index`
    says outright that CC criteria must assert it for multi-file turns."""
    fields = ("api_artifact.artifacts.zip", "api_artifact.samplesheet.csv")
    passed, results, _ = evaluate.evaluate_turn(
        _cc_with_artifacts("artifacts.zip"), _art_crits(*fields), OBS_CC,
        last_reply="done", forced=True)

    by_field = _by_field(results)
    assert by_field["api_artifact.artifacts.zip"].get("skipped", False) is False
    assert by_field["api_artifact.artifacts.zip"]["passed"] is True
    assert by_field["api_artifact.samplesheet.csv"]["skipped"] is True
    assert passed is True


def test_the_ns_arm_keeps_every_artifact_criterion():
    """The NS arm is where `rows_gte` and multiple basenames are MEANINGFUL: the
    reporter writes real files to a real path and `_count_rows` can open them."""
    fields = ("api_artifact.a.csv", "api_artifact.b.csv", "api_artifact.a.csv.rows_gte")
    ns_payload = {"status": "completed", "progress": [
        {"event": "route_decided",
         "data": {"route": "nextseek_query", "model_class": None, "source": "forced",
                  "reasoning": ""}},
        {"event": "query_complete", "data": {"reply": "wrote them", "debug": {}}},
    ]}
    _p, results, _o = evaluate.evaluate_turn(
        ns_payload, _art_crits(*fields), OBS_NS, last_reply="wrote them", forced=True)

    assert not any(r.get("skipped") for r in results), (
        "a forced NS arm must keep every artifact criterion")


def test_the_artifact_skips_are_gated_on_forcing_like_everything_else():
    """`run_suite` byte-identical. Unlike the NS-pipeline skip these are harness
    observability gaps, which would arguably justify a router-decided skip too —
    deliberately not done, because that is a separate blast radius."""
    fields = ("api_artifact.samplesheet.csv.rows_gte", "api_artifact.a.csv",
              "api_artifact.b.csv")
    for f in fields:
        assert evaluate.unobservable_reason(
            f, "true", route=evaluate.CC_ROUTE,
            artifact_basenames=frozenset({"a.csv", "b.csv", "samplesheet.csv"})) is None


def test_turn_context_is_optional_and_defaults_to_keeping_the_criterion():
    """No basenames in hand reads as "no turn context", and the multi-deliverable
    branch must not fire on it — that branch is the one that skips something a
    single-deliverable turn could have satisfied."""
    assert evaluate.unobservable_reason("api_artifact.a.csv", "true",
                                        route=evaluate.CC_ROUTE, forced=True) is None
    assert evaluate.unobservable_reason(
        "api_artifact.a.csv.rows_gte", "gte",
        route=evaluate.CC_ROUTE, forced=True) == evaluate.CC_ARTIFACT_ROWS_REASON


def test_asserted_basenames_are_turn_scoped_and_strip_the_rows_gte_suffix():
    assert evaluate.asserted_artifact_basenames(_art_crits(
        "api_artifact.a.csv", "api_artifact.a.csv.rows_gte")) == frozenset({"a.csv"})
    assert evaluate.asserted_artifact_basenames(_art_crits(
        "api_artifact.a.csv", "api_artifact.b.csv")) == frozenset({"a.csv", "b.csv"})
    assert evaluate.asserted_artifact_basenames(
        [{"field": "api_ok", "op": "true", "value": None}]) == frozenset()


def test_every_forcing_only_skip_is_flagged_structurally_for_the_runner():
    """`run_case` counts these; it must not do so by matching reason text across a
    module boundary. Each forcing-only reason sets the flag; the two skips that
    happen with or without forcing do not."""
    assert evaluate.FORCED_ONLY_REASONS == frozenset({
        evaluate.FORCED_CC_SKIP_REASON, evaluate.CC_ARTIFACT_ROWS_REASON,
        evaluate.CC_ARTIFACT_MULTI_REASON})

    _p, results, _o = evaluate.evaluate_turn(
        _cc_with_artifacts("artifacts.zip"),
        _art_crits("api_artifact.a.csv", "api_artifact.b.csv",
                   "api_artifact.a.csv.rows_gte")
        + [{"field": "api_ok", "op": "true", "value": None},
           {"field": "outcome_observed", "op": "true", "value": None},
           {"field": "pipeline_agent.launch_plan.params.genome", "op": "nonempty",
            "value": None},
           # #66: no longer HTTP-unobservable, so on a FORCED cc arm it lands in
           # the forcing-only bucket like any other NS-pipeline-internal field.
           {"field": "pipeline_agent.active", "op": "true", "value": None}],
        OBS_CC, last_reply="done", forced=True)

    by_field = _by_field(results)
    for field in ("api_artifact.a.csv", "api_artifact.b.csv",
                  "api_artifact.a.csv.rows_gte", "api_ok",
                  "pipeline_agent.active"):
        assert by_field[field]["forced_skip"] is True, field
    for field in ("outcome_observed", "pipeline_agent.launch_plan.params.genome"):
        assert by_field[field]["skipped"] is True
        assert by_field[field]["forced_skip"] is False, (
            f"{field} is skipped with or without forcing and must not be counted")

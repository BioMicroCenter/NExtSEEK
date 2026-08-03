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
    case at overlay.json:198-203 is exactly such a turn.
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

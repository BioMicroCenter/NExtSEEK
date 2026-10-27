from unittest.mock import MagicMock

import pytest

from chat_nextseek.reports import outputs as outmod
from chat_nextseek.schemas import ReportCoderOutput, ReportWriterOutputGEO


def _metadata_with_n_dseq(n: int) -> dict:
    samples = [{"metadata": {"UID": f"D.SEQ-{i}", "Notes": "Sex: M"}} for i in range(n)]
    return {"data": {"data": [{"sample_type": "D.SEQ", "samples": samples}]}}


def _metadata_with_n_samples(sample_type: str, n: int) -> dict:
    samples = [{"metadata": {"UID": f"{sample_type}-{i}", "Notes": "Sex: M"}} for i in range(n)]
    return {"data": {"data": [{"sample_type": sample_type, "samples": samples}]}}


def _coder_returning_n_rows(n: int) -> ReportCoderOutput:
    # Emit exactly `n` rows regardless of how many samples are in `data`, so a
    # caller can drive a row-parity mismatch by passing n != len(metadata samples).
    code = (
        "rows = []\n"
        "for g in data['data']['data']:\n"
        "    if g.get('sample_type') == 'D.SEQ':\n"
        "        for s in g.get('samples', []):\n"
        f"            if len(rows) >= {n}:\n"
        "                break\n"
        "            rows.append({'*library name': (s.get('metadata') or {}).get('UID')})\n"
        "result = {'samples': rows}\n"
    )
    return ReportCoderOutput(extraction_code=code)


def test_large_n_uses_code_path():
    md = _metadata_with_n_dseq(25)
    writer = MagicMock(name="report_writer_fn")
    coder = MagicMock(return_value=_coder_returning_n_rows(25))
    out = outmod._produce_report_output(
        config=MagicMock(), user_query="GEO report", report_type_value="GEO",
        reporter_context={}, template_for_llm={"samples": [{}]}, metadata=md,
        report_writer_fn=writer, report_coder_fn=coder, log_dir="/tmp",
    )
    assert isinstance(out, ReportWriterOutputGEO)
    assert len(out.report.samples) == 25
    writer.assert_not_called()


def test_small_n_uses_writer_path():
    md = _metadata_with_n_dseq(5)
    writer = MagicMock(name="report_writer_fn", return_value="WRITER_OUTPUT")
    coder = MagicMock(name="report_coder_fn")
    out = outmod._produce_report_output(
        config=MagicMock(), user_query="GEO report", report_type_value="GEO",
        reporter_context={}, template_for_llm={"samples": [{}]}, metadata=md,
        report_writer_fn=writer, report_coder_fn=coder, log_dir="/tmp",
    )
    coder.assert_not_called()
    writer.assert_called_once()
    assert out == "WRITER_OUTPUT"


def test_row_parity_mismatch_retries_once_then_never_reaches_the_writer():
    """Above the threshold the all-metadata report writer is never the fallback: its input overflows on a large set
    (prod 10-14, task 39a967f9). A failed coder is asked once more with the failure, then the turn says so plainly."""
    md = _metadata_with_n_dseq(25)
    writer = MagicMock(name="report_writer_fn")
    coder = MagicMock(return_value=_coder_returning_n_rows(10))
    with pytest.raises(outmod.ReportCodePathError) as err:
        outmod._produce_report_output(
            config=MagicMock(), user_query="GEO report", report_type_value="GEO",
            reporter_context={}, template_for_llm={"samples": [{}]}, metadata=md,
            report_writer_fn=writer, report_coder_fn=coder, log_dir="/tmp",
        )
    writer.assert_not_called()
    assert coder.call_count == 2
    assert "got 10, want 25" in coder.call_args.kwargs["previous_error"]
    assert "GEO package" in err.value.reply  # no count: the metadata holds related samples too, not only the set
    assert "Error" not in err.value.reply and "LLM" not in err.value.reply


def test_a_safety_error_is_fed_back_and_the_retry_is_used():
    md = _metadata_with_n_dseq(25)
    writer = MagicMock(name="report_writer_fn")
    coder = MagicMock(side_effect=[ReportCoderOutput(extraction_code="import os\nresult = {}"),
                                   _coder_returning_n_rows(25)])
    out = outmod._produce_report_output(
        config=MagicMock(), user_query="GEO report", report_type_value="GEO",
        reporter_context={}, template_for_llm={"samples": [{}]}, metadata=md,
        report_writer_fn=writer, report_coder_fn=coder, log_dir="/tmp",
    )
    writer.assert_not_called()
    assert len(out.report.samples) == 25
    assert "previous_error" not in coder.call_args_list[0].kwargs
    assert "ReportCodeSafetyError" in coder.call_args_list[1].kwargs["previous_error"]


def test_eleven_samples_take_the_code_path():
    """Operator 10-14: more than 10 samples takes the code path (was 20)."""
    md = _metadata_with_n_dseq(11)
    writer = MagicMock(name="report_writer_fn")
    coder = MagicMock(return_value=_coder_returning_n_rows(11))
    outmod._produce_report_output(
        config=MagicMock(), user_query="GEO report", report_type_value="GEO",
        reporter_context={}, template_for_llm={"samples": [{}]}, metadata=md,
        report_writer_fn=writer, report_coder_fn=coder, log_dir="/tmp",
    )
    coder.assert_called_once()
    writer.assert_not_called()


def test_a_large_set_with_no_target_type_samples_says_so_and_builds_nothing():
    """A GEO package over 50 mice whose metadata holds no D.SEQ: neither the writer (the whole set overflowed it on
    prod 10-14) nor an empty package passed off as a success (0 rows met a 0-row parity check). A plain reply, and
    no coder call is spent on it."""
    md = _metadata_with_n_samples("MUS", 50)
    writer = MagicMock(name="report_writer_fn")
    coder = MagicMock(return_value=_coder_returning_n_rows(0))
    with pytest.raises(outmod.ReportCodePathError) as err:
        outmod._produce_report_output(
            config=MagicMock(), user_query="GEO report", report_type_value="GEO",
            reporter_context={}, template_for_llm={"samples": [{}]}, metadata=md,
            report_writer_fn=writer, report_coder_fn=coder, log_dir="/tmp",
        )
    coder.assert_not_called()
    writer.assert_not_called()
    assert "no sequencing data (D.SEQ)" in err.value.reply


def test_a_busy_slot_reruns_the_same_code_without_a_new_coder_call(monkeypatch):
    """Busy is not a fault in the code: the second attempt runs the same code again, and the model is not told its
    code failed."""
    from chat_nextseek.helpers.tools import report_code
    md = _metadata_with_n_dseq(25)
    calls = {"n": 0}
    real = report_code.execute_report_code

    def busy_once(code, metadata):
        calls["n"] += 1
        if calls["n"] == 1:
            raise report_code.ReportCodeBusyError("another report's code is already running")
        return real(code, metadata)

    monkeypatch.setattr(report_code, "execute_report_code", busy_once)
    coder = MagicMock(return_value=_coder_returning_n_rows(25))
    out = outmod._produce_report_output(
        config=MagicMock(), user_query="GEO report", report_type_value="GEO",
        reporter_context={}, template_for_llm={"samples": [{}]}, metadata=md,
        report_writer_fn=MagicMock(), report_coder_fn=coder, log_dir="/tmp",
    )
    assert len(out.report.samples) == 25
    coder.assert_called_once()


def test_busy_then_a_parity_miss_reports_the_miss_not_busy(monkeypatch):
    from chat_nextseek.helpers.tools import report_code
    md = _metadata_with_n_dseq(25)
    calls = {"n": 0}
    real = report_code.execute_report_code

    def busy_once(code, metadata):
        calls["n"] += 1
        if calls["n"] == 1:
            raise report_code.ReportCodeBusyError("another report's code is already running")
        return real(code, metadata)

    monkeypatch.setattr(report_code, "execute_report_code", busy_once)
    coder = MagicMock(return_value=_coder_returning_n_rows(3))
    with pytest.raises(outmod.ReportCodePathError) as err:
        outmod._produce_report_output(
            config=MagicMock(), user_query="GEO report", report_type_value="GEO",
            reporter_context={}, template_for_llm={"samples": [{}]}, metadata=md,
            report_writer_fn=MagicMock(), report_coder_fn=coder, log_dir="/tmp",
        )
    assert "Another report is being built" not in err.value.reply
    assert "failed twice" in err.value.reply


def test_unconfigured_coder_uses_writer():
    md = _metadata_with_n_dseq(25)
    writer = MagicMock(name="report_writer_fn", return_value="WRITER_OUTPUT")
    out = outmod._produce_report_output(
        config=MagicMock(), user_query="GEO report", report_type_value="GEO",
        reporter_context={}, template_for_llm={"samples": [{}]}, metadata=md,
        report_writer_fn=writer, report_coder_fn=None, log_dir="/tmp",
    )
    writer.assert_called_once()
    assert out == "WRITER_OUTPUT"


def _coder_returning_rows_under_key(key: str, n: int) -> "ReportCoderOutput":
    code = (
        "rows = []\n"
        "for g in data['data']['data']:\n"
        "    for s in g.get('samples', []):\n"
        "        rows.append({'id': (s.get('metadata') or {}).get('UID')})\n"
        f"result = {{'{key}': rows}}\n"
    )
    return ReportCoderOutput(extraction_code=code)


def test_sra_uses_libraries_row_key():
    md = _metadata_with_n_dseq(25)
    writer = MagicMock(name="report_writer_fn")
    coder = MagicMock(return_value=_coder_returning_rows_under_key("libraries", 25))
    out = outmod._produce_report_output(
        config=MagicMock(), user_query="SRA report", report_type_value="SRA",
        reporter_context={}, template_for_llm={"libraries": [{}]}, metadata=md,
        report_writer_fn=writer, report_coder_fn=coder, log_dir="/tmp",
    )
    writer.assert_not_called()
    assert out.report.get("libraries") and len(out.report["libraries"]) == 25


def test_pride_uses_sample_metadata_row_key():
    md = _metadata_with_n_samples("D.MSP", 25)  # PRIDE target type is D.MSP
    writer = MagicMock(name="report_writer_fn")
    coder = MagicMock(return_value=_coder_returning_rows_under_key("sample_metadata", 25))
    out = outmod._produce_report_output(
        config=MagicMock(), user_query="PRIDE report", report_type_value="PRIDE",
        reporter_context={}, template_for_llm={"sample_metadata": [{}]}, metadata=md,
        report_writer_fn=writer, report_coder_fn=coder, log_dir="/tmp",
    )
    writer.assert_not_called()
    assert out.report.get("sample_metadata") and len(out.report["sample_metadata"]) == 25


def test_sra_wrong_row_key_never_reaches_the_writer():
    # coder emits 'samples' (GEO key) for an SRA report -> parity on 'libraries' fails twice -> a plain reply
    md = _metadata_with_n_dseq(25)
    writer = MagicMock(name="report_writer_fn")
    coder = MagicMock(return_value=_coder_returning_rows_under_key("samples", 25))
    with pytest.raises(outmod.ReportCodePathError):
        outmod._produce_report_output(
            config=MagicMock(), user_query="SRA report", report_type_value="SRA",
            reporter_context={}, template_for_llm={"libraries": [{}]}, metadata=md,
            report_writer_fn=writer, report_coder_fn=coder, log_dir="/tmp",
        )
    writer.assert_not_called()


def test_code_that_fails_twice_never_reaches_the_writer():
    md = _metadata_with_n_dseq(25)
    writer = MagicMock(name="report_writer_fn")
    coder = MagicMock(return_value=ReportCoderOutput(extraction_code="import os\nresult = {}"))
    with pytest.raises(outmod.ReportCodePathError) as err:
        outmod._produce_report_output(
            config=MagicMock(), user_query="GEO report", report_type_value="GEO",
            reporter_context={}, template_for_llm={"samples": [{}]}, metadata=md,
            report_writer_fn=writer, report_coder_fn=coder, log_dir="/tmp",
        )
    writer.assert_not_called()
    assert coder.call_count == 2 and "failed twice" in err.value.reply


def test_a_report_that_cannot_start_says_so_and_never_reaches_the_writer(monkeypatch, tmp_path):
    """One report runs at a time: a second one that cannot start in time tries once more, then tells the user. It
    used to go to the report writer, whose input overflows on a large set (prod 10-14)."""
    import fcntl
    import os
    from chat_nextseek.helpers.tools import report_code
    lock = str(tmp_path / "report.lock")
    monkeypatch.setattr(report_code, "REPORT_LOCK_PATH", lock)
    monkeypatch.setattr(report_code, "REPORT_WAIT_S", 0.1)
    md = _metadata_with_n_dseq(25)
    writer = MagicMock(name="report_writer_fn")
    coder = MagicMock(return_value=_coder_returning_n_rows(25))
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        with pytest.raises(outmod.ReportCodePathError) as err:
            outmod._produce_report_output(
                config=MagicMock(), user_query="GEO report", report_type_value="GEO",
                reporter_context={}, template_for_llm={"samples": [{}]}, metadata=md,
                report_writer_fn=writer, report_coder_fn=coder, log_dir="/tmp",
            )
    finally:
        os.close(fd)
    writer.assert_not_called()
    assert "Another report is being built" in err.value.reply


def test_the_session_keeps_a_report_turns_summary_not_its_fetched_metadata():
    """Prod 10-14 (launch 20261014-2319): a GEO package over 1,641 mice was built (539 rows), then the turn could not
    be saved: the session held the fetched metadata of 26,064 samples and every protocol's text (last_debug 112 MB,
    results_history 57 MB) and MySQL refused the write, so the user got an empty reply. The files keep all of it;
    the session keeps what a reply, a follow-up and the harness read."""
    import json
    md = _metadata_with_n_samples("MUS", 26064)
    body = {"report_type": "GEO", "samples": [{"*library name": f"D.SEQ-{i}"} for i in range(539)]}
    rr = {"reports": [{"uid": None, "metadata": md,
                       "protocols": {"P.ENG-1": {"text": "x" * 200_000, "title": "P.ENG-1"}},
                       "report_writer_output": body}],
          "merged_report": {"all_samples": body}}
    slim = outmod.session_reporter_result(rr)
    assert len(json.dumps(md)) > 1_000_000
    assert len(json.dumps(slim)) < 200_000
    (entry,) = slim["reports"]
    assert "metadata" not in entry and entry["metadata_samples"] == 26064
    assert entry["protocol_ids"] == ["P.ENG-1"]
    assert "report_writer_output" not in entry and slim["merged_report"] == {"all_samples": body}
    assert rr["reports"][0]["metadata"] is md  # the caller's result (the files' source) is not changed


def test_a_summary_reporter_result_passes_through():
    rr = {"ok": True, "rows_returned": 3, "labs_table": {"ENG": 3}}
    assert outmod.session_reporter_result(rr) == rr
    assert outmod.session_reporter_result(None) is None


def test_run_query_keeps_a_big_reports_summary_in_the_session():
    """The wiring: a GEO turn over a large set stores the slim result in last_debug and in its results_history bundle
    (prod 10-14: the full one, 112 MB + 57 MB, could not be saved)."""
    import json
    from unittest.mock import patch
    from chat_nextseek import orchestrator
    from chat_nextseek.schemas import EntityAgentOutput, ParserFilters, ParserPlan
    from chat_nextseek.schemas.chat import ReporterPlan

    md = _metadata_with_n_samples("MUS", 26064)
    body = {"report_type": "GEO", "samples": [{"*library name": f"D.SEQ-{i}"} for i in range(539)]}
    rr = {"reports": [{"uid": None, "metadata": md, "protocols": {"P.ENG-1": {"text": "x" * 200_000}},
                       "report_writer_output": body}], "merged_report": {"all_samples": body}}
    plan = ParserPlan(mode="reporter", report_mode="report_generation", report_type="GEO",
                      intent_summary="GEO for those mice", filters=ParserFilters(uids=["MUS-1"]))
    rplan = ReporterPlan(reporter_mode="report_generation", report_type="GEO", uids=["MUS-1"],
                         reporter_context={"per_sample_reports": False}, notes="")
    session = {"results_history": [], "last_files": []}
    config = MagicMock()
    config.API_USER = "u"
    config.API_PASS = "p"
    with patch("chat_nextseek.orchestrator.pipeline_agent.is_active", return_value=False), \
         patch("chat_nextseek.orchestrator._ensure_query_log_dir", return_value="/tmp/log"), \
         patch("chat_nextseek.orchestrator.ArtifactStore"), \
         patch("chat_nextseek.orchestrator.entity_agent", return_value=EntityAgentOutput()), \
         patch("chat_nextseek.orchestrator.parser_agent", return_value=plan), \
         patch("chat_nextseek.orchestrator.reporter_agent", return_value=rplan), \
         patch("chat_nextseek.orchestrator.fix_sample_endpoint", side_effect=lambda d: d), \
         patch("chat_nextseek.orchestrator._report_generation_uids", return_value=(["MUS-1"], None)), \
         patch("chat_nextseek.orchestrator.generate_report_outputs",
               return_value=(rr, {"all_samples": body}, {"merged_report": "/tmp/m.json"}, "done")), \
         patch("chat_nextseek.orchestrator.shortlist_catalog",
               return_value=([], [], {"sampletype_codes": [], "assay_codes": [], "sampletype_ranks": {},
                                      "assay_ranks": {}, "enabled": False, "fallback_reason": None})):
        orchestrator.run_query(session=session, config=config, user_text="Make a GEO package for those mice.")

    stored = session["last_debug"]["reporter_result"]
    bundle = session["results_history"][-1]["reporter_result"]
    for kept in (stored, bundle):
        assert "metadata" not in kept["reports"][0] and kept["reports"][0]["metadata_samples"] == 26064
    assert session["last_debug"]["reporter_metadata"] is None
    assert len(json.dumps(session["last_debug"], default=str)) < 500_000
    assert len(json.dumps(session["results_history"], default=str)) < 500_000


def _code_path_lead_for(report_type, rows_code, n, narrative):
    md = _metadata_with_n_dseq(n)
    coder = MagicMock(return_value=ReportCoderOutput(extraction_code=rows_code, result_description=narrative))
    return outmod._produce_report_output(
        config=MagicMock(), user_query="package", report_type_value=report_type,
        reporter_context={}, template_for_llm={}, metadata=md,
        report_writer_fn=MagicMock(), report_coder_fn=coder, log_dir="/tmp",
    )


_SRA_CODE = (
    "rows = [{'*library name': (s.get('metadata') or {}).get('UID'), 'bioproject': 'PRJNA123456', 'run': 'SRR%d' % i}\n"
    "        for g in data['data']['data'] for i, s in enumerate(g['samples'])]\n"
    "result = {'libraries': rows}\n"
)
_GEO_CODE = (
    "rows = [{'library name': (s.get('metadata') or {}).get('UID'), 'series': 'GSE99999'}\n"
    "        for g in data['data']['data'] for s in g['samples']]\n"
    "result = {'samples': rows}\n"
)


def test_code_path_sra_reply_lead_comes_from_the_payload_not_the_coders_narrative():
    out = _code_path_lead_for("SRA", _SRA_CODE, 12, "SRA report body with one library row per sample")
    lead = out.narrative
    assert "SRA report body" not in lead
    assert "SRA" in lead and "12 samples" in lead
    assert "D.SEQ-0" in lead and "D.SEQ-4" in lead and "D.SEQ-5" not in lead and "7 more" in lead
    assert "PRJNA123456" in lead and "SRR0" in lead and "SRR4" in lead and "SRR5" not in lead
    assert "PRJNA PRJNA" not in lead and "SRR SRR" not in lead  # an accession is written once, not after its prefix


def test_code_path_geo_reply_lead_names_uids_and_accessions():
    out = _code_path_lead_for("GEO", _GEO_CODE, 11, "GEO body built by looping over groups")
    assert "looping over groups" not in out.narrative
    assert "D.SEQ-0" in out.narrative and "GSE99999" in out.narrative and "11 samples" in out.narrative
    assert "GSE GSE" not in out.narrative


def test_code_path_lead_reads_a_uid_the_way_the_report_metadata_does():
    # reports/metadata.py: the metadata's UID, else the sample's uuid, else its uid.
    samples = [{"uid": "D.SEQ-U"}, {"uuid": "D.SEQ-V", "metadata": {"Notes": "x"}},
               {"metadata": {"UID": "D.SEQ-W"}, "uuid": "not-this-one"}]
    md = {"data": {"data": [{"sample_type": "D.SEQ", "samples": samples}]}}
    lead = outmod._code_path_lead("SRA", md, "D.SEQ", {})
    assert "3 samples" in lead and all(u in lead for u in ("D.SEQ-U", "D.SEQ-V", "D.SEQ-W"))
    assert "not-this-one" not in lead

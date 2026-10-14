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

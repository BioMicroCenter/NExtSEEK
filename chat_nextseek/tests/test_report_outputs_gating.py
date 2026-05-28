from unittest.mock import MagicMock

from chat_nextseek.reports import outputs as outmod
from chat_nextseek.schemas import ReportCoderOutput, ReportWriterOutputGEO


def _metadata_with_n_dseq(n: int) -> dict:
    samples = [{"metadata": {"UID": f"D.SEQ-{i}", "Notes": "Sex: M"}} for i in range(n)]
    return {"data": {"data": [{"sample_type": "D.SEQ", "samples": samples}]}}


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


def test_row_parity_mismatch_falls_back_to_writer():
    md = _metadata_with_n_dseq(25)
    writer = MagicMock(name="report_writer_fn", return_value="WRITER_OUTPUT")
    coder = MagicMock(return_value=_coder_returning_n_rows(10))
    out = outmod._produce_report_output(
        config=MagicMock(), user_query="GEO report", report_type_value="GEO",
        reporter_context={}, template_for_llm={"samples": [{}]}, metadata=md,
        report_writer_fn=writer, report_coder_fn=coder, log_dir="/tmp",
    )
    writer.assert_called_once()
    assert out == "WRITER_OUTPUT"


def test_code_exec_error_falls_back_to_writer():
    md = _metadata_with_n_dseq(25)
    writer = MagicMock(name="report_writer_fn", return_value="WRITER_OUTPUT")
    coder = MagicMock(return_value=ReportCoderOutput(extraction_code="import os\nresult = {}"))
    out = outmod._produce_report_output(
        config=MagicMock(), user_query="GEO report", report_type_value="GEO",
        reporter_context={}, template_for_llm={"samples": [{}]}, metadata=md,
        report_writer_fn=writer, report_coder_fn=coder, log_dir="/tmp",
    )
    writer.assert_called_once()
    assert out == "WRITER_OUTPUT"


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

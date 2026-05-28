from chat_nextseek.schemas import ReportCoderOutput


def test_report_coder_output_defaults():
    out = ReportCoderOutput(extraction_code="result = {}")
    assert out.extraction_code == "result = {}"
    assert out.result_description == ""
    assert out.fields_used == []
    assert out.notes == ""

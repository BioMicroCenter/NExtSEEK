from chat_nextseek.schemas.router import ParserFilters


def test_parser_filters_carries_lab_codes():
    f = ParserFilters(sampletype_code="OOC", lab_codes=["KAM"])
    assert f.lab_codes == ["KAM"]


def test_parser_filters_lab_codes_default_empty():
    assert ParserFilters().lab_codes == []

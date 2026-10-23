from chat_nextseek.seqera.pairing import requested_tumour_tools, somatic_pairing_check


def _ev(verdicts):
    return {sample: {"status": {"verdict": v}} for sample, v in verdicts.items()}


PAIR = [{"patient": "P1", "sample": "T1", "status": "1"},
        {"patient": "P1", "sample": "N1", "status": "0"}]


def test_a_germline_run_of_normals_needs_nothing():
    rows = [{"patient": "P1", "sample": "N1", "status": "0"}]
    assert somatic_pairing_check("haplotypecaller", rows, _ev({"N1": "defaulted"})) == ""


def test_tumour_tools_without_a_pair_are_refused():
    msg = somatic_pairing_check("mutect2", [PAIR[0]], _ev({"T1": "corroborated"}))
    assert "mutect2" in msg and "P1" in msg


def test_a_pair_decided_by_metadata_goes_ahead():
    assert somatic_pairing_check("mutect2,strelka", PAIR,
                                 _ev({"T1": "corroborated", "N1": "corroborated"})) == ""


def test_a_defaulted_status_asks_for_the_table():
    msg = somatic_pairing_check("mutect2", PAIR, _ev({"T1": "corroborated", "N1": "defaulted"}))
    assert "| P1 | N1 | normal (not from metadata) |" in msg


def test_a_confirmed_table_goes_ahead():
    assert somatic_pairing_check("mutect2", PAIR, _ev({"T1": "corroborated", "N1": "defaulted"}),
                                 confirmed=True) == ""


def test_a_tumour_in_a_germline_run_still_needs_confirming():
    msg = somatic_pairing_check("haplotypecaller", PAIR, _ev({"T1": "defaulted", "N1": "corroborated"}))
    assert "T1" in msg


def test_lanes_of_one_sample_count_once():
    rows = PAIR + [{"patient": "P1", "sample": "T1", "status": "1", "lane": "L2"}]
    assert somatic_pairing_check("mutect2", rows,
                                 _ev({"T1": "corroborated", "N1": "corroborated"})) == ""


def test_requested_tumour_tools_ignores_germline_callers():
    assert requested_tumour_tools("haplotypecaller, Mutect2 ,strelka") == ["mutect2"]


def test_requested_tumour_tools_accepts_a_list():
    assert requested_tumour_tools(["Mutect2", " haplotypecaller", "muse"]) == ["muse", "mutect2"]
    assert requested_tumour_tools(("ascat",)) == ["ascat"]


def test_tumour_tools_with_no_rows_are_refused():
    msg = somatic_pairing_check("mutect2", [], {})
    assert msg and "mutect2" in msg

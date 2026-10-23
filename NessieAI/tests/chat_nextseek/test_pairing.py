from chat_nextseek.seqera.pairing import (OK, REFUSAL, TABLE, pairing_question, requested_tumour_tools,
                                          somatic_pairing_check)


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


def test_pairing_question_says_which_kind_it_returned():
    ev = _ev({"T1": "corroborated", "N1": "defaulted"})
    assert pairing_question("mutect2", PAIR, ev)[0] == TABLE
    assert pairing_question("mutect2", PAIR, ev, confirmed=True) == (OK, "")
    assert pairing_question("mutect2", [PAIR[0]], ev)[0] == REFUSAL
    assert pairing_question("mutect2", [], {})[0] == REFUSAL


# --- a status the metadata reads both ways (conflict) or not at all (absent) is undecided -----------

def _ev_full(verdicts):
    return {sample: {"status": v} for sample, v in verdicts.items()}


NORMALS = [{"patient": "P1", "sample": "N1", "status": "0"},
           {"patient": "P2", "sample": "N2", "status": "0"}]


def test_a_conflict_status_asks_for_the_table_even_in_a_germline_run():
    ev = _ev_full({"N1": {"verdict": "conflict", "value": None, "evidence": "ambiguous in Type"},
                   "N2": {"verdict": "corroborated", "value": "0"}})
    kind, msg = pairing_question("haplotypecaller", NORMALS, ev)
    assert kind == TABLE
    assert "| P1 | N1 | normal (metadata unclear: ambiguous in Type) |" in msg
    assert "| P2 | N2 | normal |" in msg


def test_an_absent_status_asks_for_the_table_even_in_a_germline_run():
    ev = _ev_full({"N1": {"verdict": "absent", "value": None, "evidence": "no 0/1 signal in Type"},
                   "N2": {"verdict": "corroborated", "value": "0"}})
    assert pairing_question("haplotypecaller", NORMALS, ev)[0] == TABLE


def test_a_confirmed_conflict_goes_ahead():
    ev = _ev_full({"N1": {"verdict": "conflict", "value": None, "evidence": "Type=0; Name=1"}})
    assert pairing_question("haplotypecaller", NORMALS, ev, confirmed=True) == (OK, "")


def test_a_conflict_without_evidence_text_is_still_marked_unclear():
    ev = _ev_full({"N1": {"verdict": "conflict", "value": None}})
    msg = somatic_pairing_check("haplotypecaller", NORMALS[:1], ev)
    assert "| P1 | N1 | normal (metadata unclear) |" in msg


def test_defaulted_normals_in_a_germline_run_still_need_nothing():
    ev = _ev_full({"N1": {"verdict": "defaulted", "value": "0"}, "N2": {"verdict": "defaulted", "value": "0"}})
    assert pairing_question("haplotypecaller", NORMALS, ev) == (OK, "")

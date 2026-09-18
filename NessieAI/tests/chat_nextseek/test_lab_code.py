"""The lab matcher: lab and person names resolved in code against SEEK's lab records.

Spec `docs/superpowers/specs/2026-09-18-projects-labs-context.md` section 7. Every lab
here is invented, in the shape SEEK's institution titles take
(`<CODE>-<Name> Lab (<Affiliation>)`); none is a real lab.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from chat_nextseek.helpers.lab_code import fold, resolve_labs


def _rec(code, name, affiliation, institution_id, project_ids):
    return {
        "code": code, "name": name, "affiliation": affiliation,
        "title": f"{code}-{name} Lab ({affiliation})",
        "institution_id": institution_id, "project_ids": project_ids,
    }


LABS = [
    _rec("ASB", "Ashby", "BWH", 40, [12]),
    _rec("ASH", "Ashgrove", "BWH", 41, [4, 12]),
    _rec("FEN", "Fenwick", "MIT", 43, [4]),
    _rec("FEW", "Fenwick", "Harvard", 44, []),
    _rec("LUN", "de Lune", "Northwestern", 45, [7]),
    _rec("MAR", "Marrow", "BWH", 42, [7]),
    _rec("WRA", "Wren-Ashby", "MIT", 46, [4]),
]

#: `Marrow` is a catalog word here, as it would be in any real sample type catalog.
SAMPLETYPES = [
    {"SampleType": "BMA", "ID": 1, "Name": "Bone Marrow Aspirate",
     "Description": "Cells aspirated from the bone marrow.", "Tags": "marrow, aspirate"},
    {"SampleType": "MUS", "ID": 2, "Name": "Mouse", "Description": "An experimental mouse.",
     "Tags": "mouse, murine"},
]
ASSAYS = [
    {"Name": "Flow Cytometry", "Description": "Counts cells stained with antibodies.",
     "Tags": "FACS, flow"},
]
PROJECTS = [
    {"name": "Tidewater", "entity_type": "project", "project_id": 4,
     "alternative_names": ["Hollowell Study", "TIDE"]},
]


def resolve(question, llm_labs=(), **kw):
    kw.setdefault("records", LABS)
    kw.setdefault("catalogs", SAMPLETYPES + ASSAYS)
    kw.setdefault("projects", PROJECTS)
    return resolve_labs(question, list(llm_labs), **kw)


def rules(res):
    return [(m["code"], m["rule"]) for m in res.lab_matches]


# --------------------------------------------------------------------------
# 7.2 normalisation
# --------------------------------------------------------------------------

def test_fold_applies_nfkc_apostrophes_accents_casefold_and_whitespace():
    assert fold("  Ashgrove\u2019s   LAB ") == "ashgrove's lab"
    assert fold("Ashgrove\u02bcs") == "ashgrove's"
    assert fold("\u00c9mile \ufb01ne") == "emile fine"
    assert fold("Stra\u00dfe") == "strasse"


def test_a_name_with_accents_matches_the_question_without_them():
    records = [_rec("LUC", "L\u00facio", "MIT", 50, [])]
    res = resolve("samples from the Lucio lab", records=records)
    assert res.lab_codes == ["LUC"]
    assert res.labs == ["L\u00facio"]


# --------------------------------------------------------------------------
# 7.6 worked cases, one test each
# --------------------------------------------------------------------------

def test_76_lab_phrase():
    res = resolve("RNA from the Ashgrove lab", ["Ashgrove"])
    assert res.labs == ["Ashgrove"]
    assert res.lab_codes == ["ASH"]
    assert rules(res) == [("ASH", "lab_phrase")]
    assert res.lab_matches[0]["text"] == "Ashgrove lab"


def test_76_full_name_in_an_llm_entry():
    res = resolve("samples from Jane Ashgrove", ["Jane Ashgrove"])
    assert res.lab_codes == ["ASH"]
    assert rules(res) == [("ASH", "name")]
    assert res.lab_matches[0]["text"] == "Jane Ashgrove"
    assert res.scientists == []


def test_76_last_comma_first():
    res = resolve("Ashgrove, J. samples", ["Ashgrove, J."])
    assert res.lab_codes == ["ASH"]
    assert rules(res) == [("ASH", "name")]


def test_76_possessive_found_by_the_question_scan():
    res = resolve("Ashgrove's mice", [])
    assert res.lab_codes == ["ASH"]
    assert rules(res) == [("ASH", "possessive")]
    assert res.lab_matches[0]["text"] == "Ashgrove's"


def test_76_code():
    res = resolve("ASH lab samples", ["ASH"])
    assert res.lab_codes == ["ASH"]
    assert rules(res) == [("ASH", "code")]
    assert res.labs == ["Ashgrove"]


def test_76_bone_marrow_is_nothing():
    res = resolve("bone marrow samples", [])
    assert res.lab_codes == []
    assert res.lab_matches == []
    assert res.labs == []


def test_76_catalog_word_through_a_lab_phrase():
    res = resolve("samples from the Marrow lab", ["Marrow"])
    assert res.lab_codes == ["MAR"]
    assert rules(res) == [("MAR", "lab_phrase")]


def test_76_catalog_word_the_llm_listed_wrongly_goes_to_keywords():
    res = resolve("marrow samples", ["marrow"])
    assert res.lab_codes == []
    assert res.labs == []
    assert res.keywords == ["marrow"]
    assert res.scientists == []


def test_76_shared_surname_narrowed_by_affiliation():
    res = resolve("the Fenwick lab at Harvard", ["Fenwick"])
    assert res.lab_codes == ["FEW"]
    assert res.lab_matches[0]["affiliation"] == "Harvard"
    assert res.lab_matches[0]["ambiguous"] is False


def test_76_person_who_handled_samples_is_a_scientist():
    res = resolve("samples handled by Dana Example", ["Dana Example"])
    assert res.labs == []
    assert res.lab_codes == []
    assert res.scientists == ["Dana Example"]
    assert res.keywords == ["Dana Example"]


def test_76_a_lab_seek_could_not_parse_becomes_a_scientist():
    """`OAK\u2013Oakley Lab (MIT)` is unparsed, so no Oakley record reaches LABS."""
    res = resolve("the Oakley lab", ["Oakley"])
    assert res.lab_codes == []
    assert res.labs == []
    assert res.scientists == ["Oakley"]


def test_76_unknown_code_goes_to_keywords():
    res = resolve("XYZ lab", ["XYZ"])
    assert res.lab_codes == []
    assert res.labs == []
    assert res.keywords == ["XYZ"]
    assert res.scientists == []


def test_75_the_worked_example_verbatim():
    res = resolve("RNA from the Fenwick lab, handled by Dana Example", ["Fenwick", "Dana Example"])
    assert res.labs == ["Fenwick"]
    assert res.lab_codes == ["FEN", "FEW"]
    assert res.lab_matches == [
        {"text": "Fenwick lab", "code": "FEN", "name": "Fenwick", "affiliation": "MIT",
         "project_ids": [4], "rule": "lab_phrase", "ambiguous": True},
        {"text": "Fenwick lab", "code": "FEW", "name": "Fenwick", "affiliation": "Harvard",
         "project_ids": [], "rule": "lab_phrase", "ambiguous": True},
    ]
    assert res.scientists == ["Dana Example"]
    assert res.keywords == ["Dana Example"]


# --------------------------------------------------------------------------
# M1 code
# --------------------------------------------------------------------------

@pytest.mark.parametrize("question", ["lab ASH samples", "samples with lab code ASH", "the ASH group"])
def test_m1_code_inside_a_lab_phrase_in_the_question(question):
    res = resolve(question, [])
    assert rules(res) == [("ASH", "code")]


def test_m1_code_must_be_written_in_capitals():
    assert resolve("ash lab samples", []).lab_codes == []


def test_m1_bare_code_in_the_question_needs_a_lab_phrase():
    assert resolve("ASH samples", []).lab_codes == []


def test_m1_llm_entry_code_needs_the_question_to_carry_it():
    assert rules(resolve("ASH samples", ["ASH"])) == [("ASH", "code")]
    assert rules(resolve("ASH samples", ["ASH lab"])) == [("ASH", "code")]
    assert resolve("samples from the east wing", ["ASH"]).lab_codes == []


def test_m1_never_reads_a_code_out_of_a_uid():
    """M7: a UID names a sample, not a lab scope."""
    for question in ("children of D.SEQ-221031ASH-67", "the ASH-221031FEN-2 lab samples",
                     "tree of MUS-250101ASH-3 lab"):
        res = resolve(question, ["ASH"])
        assert "ASH" not in res.lab_codes, question
        assert res.scientists == [], question
        # Nor does a code the LLM read out of the UID become a keyword: the UID scopes it.
        assert "ASH" not in res.keywords, question


# --------------------------------------------------------------------------
# M2 lab phrase
# --------------------------------------------------------------------------

def test_m2_any_case_and_found_without_the_llm():
    res = resolve("samples from the ashgrove lab", [])
    assert rules(res) == [("ASH", "lab_phrase")]
    assert res.labs == ["Ashgrove"]


@pytest.mark.parametrize("question", [
    "the Ashgrove labs", "the Ashgrove laboratory", "the Ashgrove group",
    "Ashgrove's lab", "the Ashgroves' lab",
    "the lab of Ashgrove", "the laboratory of Dr. Jane Ashgrove", "the group of Prof J. Ashgrove",
])
def test_m2_every_phrase_form(question):
    assert rules(resolve(question, [])) == [("ASH", "lab_phrase")]


def test_m2_multi_word_and_hyphenated_surnames_match_only_whole():
    assert resolve("the de Lune lab", []).lab_codes == ["LUN"]
    assert resolve("the Lune lab", []).lab_codes == []
    assert resolve("the Wren-Ashby lab", []).lab_codes == ["WRA"]
    assert resolve("the Wren lab", []).lab_codes == []


def test_m2_group_after_a_catalog_word_needs_capitals():
    """`bone marrow group` is a sample group, not the Marrow lab."""
    assert resolve("the bone marrow group vs control", []).lab_codes == []
    assert resolve("a group of marrow samples", []).lab_codes == []
    assert resolve("the Marrow group", []).lab_codes == ["MAR"]


# --------------------------------------------------------------------------
# M3 possessive or honorific
# --------------------------------------------------------------------------

def test_m3_honorific():
    assert rules(resolve("send Dr Ashgrove the list", [])) == [("ASH", "honorific")]
    res = resolve("tubes Prof. Jane Ashgrove sent", [])
    assert rules(res) == [("ASH", "honorific")]
    assert res.lab_matches[0]["text"] == "Prof. Jane Ashgrove"


def test_m3_plural_possessive():
    assert rules(resolve("the Ashgroves' mice", [])) == [("ASH", "possessive")]


def test_m3_needs_the_name_capitalised():
    assert resolve("ashgrove's mice", []).lab_codes == []
    assert resolve("dr ashgrove sent these", []).lab_codes == []


def test_m3_possessive_must_be_followed_by_a_word():
    assert resolve("are these Ashgrove's?", []).lab_codes == []


# --------------------------------------------------------------------------
# M4 name in an LLM entry
# --------------------------------------------------------------------------

def test_m4_any_case_for_a_non_catalog_name():
    assert rules(resolve("samples from jane ashgrove", ["Jane Ashgrove"])) == [("ASH", "name")]


def test_m4_first_names_initials_and_honorifics_are_ignored():
    assert rules(resolve("from J. Ashgrove", ["J. Ashgrove"])) == [("ASH", "name")]
    assert rules(resolve("from Ashgrove", ["Dr. Jane Ashgrove"])) == [("ASH", "name")]


def test_m4_the_name_must_occur_in_the_question():
    res = resolve("RNA samples from last week", ["Ashgrove"])
    assert res.lab_codes == []
    assert res.labs == []


def test_m4_llm_matches_already_found_by_the_scan_add_no_second_match():
    res = resolve("the Ashgrove lab", ["Ashgrove", "Jane Ashgrove"])
    assert rules(res) == [("ASH", "lab_phrase")]


# --------------------------------------------------------------------------
# M5 catalog word
# --------------------------------------------------------------------------

def test_m5_capitalised_mid_sentence_matches():
    assert rules(resolve("samples from Marrow last week", ["Marrow"])) == [("MAR", "name")]


def test_m5_capital_at_the_start_of_a_sentence_is_not_evidence():
    res = resolve("Marrow samples from mice", ["Marrow"])
    assert res.lab_codes == []
    assert res.keywords == ["Marrow"]


def test_m5_a_lab_phrase_the_llm_wrote_is_not_the_users():
    res = resolve("bone marrow samples", ["marrow lab"])
    assert res.lab_codes == []
    assert res.keywords == ["marrow"]


def test_m5_reads_the_catalogs_it_is_given():
    """With no catalog naming it, marrow is an ordinary surname and any case matches."""
    res = resolve("marrow samples", ["marrow"], catalogs=[])
    assert rules(res) == [("MAR", "name")]


# --------------------------------------------------------------------------
# M6 shared surname
# --------------------------------------------------------------------------

def test_m6_no_affiliation_keeps_both_and_marks_them_ambiguous():
    res = resolve("the Fenwick lab", [])
    assert res.lab_codes == ["FEN", "FEW"]
    assert [m["ambiguous"] for m in res.lab_matches] == [True, True]
    assert res.labs == ["Fenwick"]


def test_m6_affiliation_word_chooses():
    res = resolve("Fenwick's samples from MIT", [])
    assert res.lab_codes == ["FEN"]
    assert res.lab_matches[0]["ambiguous"] is False


def test_m6_both_affiliations_named_keeps_both_unambiguous():
    res = resolve("the Fenwick lab at MIT or Harvard", [])
    assert res.lab_codes == ["FEN", "FEW"]
    assert [m["ambiguous"] for m in res.lab_matches] == [False, False]


# --------------------------------------------------------------------------
# M7 never
# --------------------------------------------------------------------------

def test_m7_a_bare_surname_in_running_text_is_nothing():
    res = resolve("compare Ashgrove and control samples", [])
    assert res.lab_codes == []
    assert res.lab_matches == []


# --------------------------------------------------------------------------
# U1 to U5: LLM entries nothing matched
# --------------------------------------------------------------------------

def test_u2_a_project_alias_is_dropped():
    res = resolve("the Hollowell Study samples", ["Hollowell Study"])
    assert res.labs == []
    assert res.keywords == []
    assert res.scientists == []


def test_u4_spelled_as_in_the_question_with_the_possessive_stripped():
    res = resolve("tubes from Dana Example's bench", ["dana example's"])
    assert res.scientists == ["Dana Example"]
    assert res.keywords == ["Dana Example"]


def test_u4_needs_a_capital_in_the_question():
    res = resolve("tubes handled by dana example", ["dana example"])
    assert res.scientists == []
    assert res.keywords == ["dana example"]


def test_u4_an_institution_is_not_a_person():
    res = resolve("samples from the Riverside Core Facility", ["Riverside Core Facility"])
    assert res.scientists == []
    assert res.keywords == ["Riverside Core Facility"]


def test_u5_anything_else_goes_to_keywords():
    res = resolve("RNA from the 2020 batch", ["2020 batch"])
    assert res.scientists == []
    assert res.keywords == ["2020 batch"]


def test_llm_scientists_stay_scientists_and_reach_keywords():
    res = resolve("handled by Jane Ashgrove", [], llm_scientists=["Jane Ashgrove"])
    assert res.scientists == ["Jane Ashgrove"]
    assert res.lab_codes == []
    assert res.keywords == ["Jane Ashgrove"]


def test_existing_keywords_are_kept_and_not_duplicated():
    res = resolve("samples handled by Dana Example", ["Dana Example"],
                  llm_keywords=["RNA", "dana example"])
    assert res.keywords == ["RNA", "dana example"]
    assert res.scientists == ["Dana Example"]


# --------------------------------------------------------------------------
# Output order and de-duplication
# --------------------------------------------------------------------------

def test_matches_keep_question_order():
    res = resolve("the Marrow lab and Jane Ashgrove", ["Jane Ashgrove"])
    assert res.lab_codes == ["MAR", "ASH"]
    assert res.labs == ["Marrow", "Ashgrove"]


def test_one_match_per_record_and_text():
    res = resolve("the Ashgrove lab, then the Ashgrove lab again", [])
    assert rules(res) == [("ASH", "lab_phrase")]


# --------------------------------------------------------------------------
# No labs document
# --------------------------------------------------------------------------

@pytest.mark.parametrize("records", [None, MagicMock(), {"labs": LABS}, "labs"])
def test_unavailable_labs_pass_through(records):
    res = resolve("samples handled by Dana Example in the Ashgrove lab",
                  ["Ashgrove", "Dana Example"], records=records, llm_scientists=["Pat Sample"])
    assert res.available is False
    assert res.labs == ["Ashgrove", "Dana Example"]
    assert res.lab_codes == []
    assert res.lab_matches == []
    assert res.scientists == ["Pat Sample"]
    assert res.keywords == ["Pat Sample"]


def test_an_empty_labs_list_is_available_and_decides_not_a_lab():
    res = resolve("the Ashgrove lab", ["Ashgrove"], records=[])
    assert res.available is True
    assert res.labs == []
    assert res.scientists == ["Ashgrove"]


def test_malformed_records_are_skipped():
    records = [
        {"code": "as", "name": "Ashgrove"}, {"code": "ASHX", "name": "Ashgrove"},
        {"code": "ASH", "name": ""}, "ASH-Ashgrove Lab (BWH)", None,
        {"code": "ASH", "name": "Ashgrove", "affiliation": None, "project_ids": [4, "x", True]},
    ]
    res = resolve("the Ashgrove lab", [], records=records)
    assert res.lab_codes == ["ASH"]
    assert res.lab_matches[0]["project_ids"] == [4]
    assert res.lab_matches[0]["affiliation"] is None

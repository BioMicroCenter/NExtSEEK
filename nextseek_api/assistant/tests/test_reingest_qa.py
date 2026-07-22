"""qa_rows disposition checks for the reingest rows."""
from nextseek_api.assistant.reingest_qa import (
    CLEAN, HARD_REJECT, SOFT_FLAG, qa_rows,
)

_TYPES = {"A.SCXP", "A.ALN", "D.SEQ"}
_PARENTS = {"D.SEQ-220823SHA-1", "D.SEQ-220823SHA-2"}


def _row(parent, **extra):
    return {"json_metadata": {"Parent": parent, "Scientist": "Marie Floryan", **extra}, "assay_ids": [12]}


def test_clean_rows():
    rows = [_row("D.SEQ-220823SHA-1"), _row("D.SEQ-220823SHA-2")]
    r = qa_rows(rows, sample_type="A.SCXP", known_sampletypes=_TYPES, existing_parent_uids=_PARENTS)
    assert r.disposition == CLEAN
    assert not r.hard and not r.soft


def test_unknown_sampletype_is_hard_reject():
    r = qa_rows([_row("D.SEQ-220823SHA-1")], sample_type="A.NOPE",
                known_sampletypes=_TYPES, existing_parent_uids=_PARENTS)
    assert r.disposition == HARD_REJECT
    assert any("unknown_sampletype" in h for h in r.hard)


def test_unresolvable_parent_is_hard_reject():
    r = qa_rows([_row("D.SEQ-does-not-exist")], sample_type="A.SCXP",
                known_sampletypes=_TYPES, existing_parent_uids=_PARENTS)
    assert r.disposition == HARD_REJECT
    assert any("parent_uid_not_found" in h for h in r.hard)


def test_multiparent_semicolon_resolves():
    r = qa_rows([_row("D.SEQ-220823SHA-1;D.SEQ-220823SHA-2")], sample_type="A.SCXP",
                known_sampletypes=_TYPES, existing_parent_uids=_PARENTS)
    assert r.disposition == CLEAN


def test_blank_parent_is_hard_reject():
    r = qa_rows([_row("")], sample_type="A.SCXP",
                known_sampletypes=_TYPES, existing_parent_uids=_PARENTS)
    assert r.disposition == HARD_REJECT
    assert any("blank Parent" in h for h in r.hard)


def test_duplicate_name_is_hard_reject():
    rows = [_row("D.SEQ-220823SHA-1", Name="dup"), _row("D.SEQ-220823SHA-2", Name="dup")]
    r = qa_rows(rows, sample_type="A.SCXP", known_sampletypes=_TYPES, existing_parent_uids=_PARENTS)
    assert r.disposition == HARD_REJECT
    assert any("duplicate_name" in h for h in r.hard)


def test_placeholder_ok_surprise_sentinel_soft_flags():
    ok = qa_rows([_row("D.SEQ-220823SHA-1", ReferenceGenome="*** PLACEHOLDER: genome ***")],
                 sample_type="A.SCXP", known_sampletypes=_TYPES, existing_parent_uids=_PARENTS)
    assert ok.disposition == CLEAN  # expected placeholder does not flag

    flag = qa_rows([_row("D.SEQ-220823SHA-1", ReferenceGenome="TODO figure out")],
                   sample_type="A.SCXP", known_sampletypes=_TYPES, existing_parent_uids=_PARENTS)
    assert flag.disposition == SOFT_FLAG
    assert any("surprise_sentinel" in s for s in flag.soft)


def test_missing_required_is_soft_flag():
    r = qa_rows([_row("D.SEQ-220823SHA-1")], sample_type="A.SCXP", known_sampletypes=_TYPES,
                required_fields=["Parent", "File_PrimaryData"], existing_parent_uids=_PARENTS)
    assert r.disposition == SOFT_FLAG
    assert any("missing_required" in s and "File_PrimaryData" in s for s in r.soft)

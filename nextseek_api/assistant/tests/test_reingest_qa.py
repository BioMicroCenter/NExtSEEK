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


# ── variant parent keys (AntibodyParent, Treatment1Parent, …) ──────────────
#
# The parent gate read the literal key "Parent" only. A row whose sole ancestor
# lives in a variant key was hard-rejected as "blank Parent", and a broken
# variant token was never resolvability-checked at all.


def _bare_row(**meta):
    """A row with NO literal 'Parent' key."""
    return {"json_metadata": {"Scientist": "Marie Floryan", **meta}, "assay_ids": [12]}


def _qa(rows):
    return qa_rows(rows, sample_type="A.SCXP", known_sampletypes=_TYPES,
                   existing_parent_uids=_PARENTS)


VARIANT_KEYS = [
    "AntibodyParent", "CompensationFCSParent", "Treatment1Parent",
    "Treatment2Parent", "BacterialParent", "AntibodyPanelParent",
    "antibodyparent", "ANTIBODYPARENT",
]


def test_variant_parent_key_satisfies_the_parent_requirement():
    for key in VARIANT_KEYS:
        r = _qa([_bare_row(**{key: "D.SEQ-220823SHA-1"})])
        assert r.disposition == CLEAN, f"{key}: {r.hard}"
        assert not any("blank Parent" in h for h in r.hard), key


def test_variant_parent_key_is_resolvability_checked():
    for key in VARIANT_KEYS:
        r = _qa([_bare_row(**{key: "D.SEQ-does-not-exist"})])
        assert r.disposition == HARD_REJECT, key
        assert any("parent_uid_not_found" in h and "D.SEQ-does-not-exist" in h
                   for h in r.hard), f"{key}: {r.hard}"


def test_no_parent_key_of_any_kind_is_hard_reject():
    r = _qa([_bare_row(ReferenceGenome="GRCh38")])
    assert r.disposition == HARD_REJECT
    assert any("blank Parent" in h for h in r.hard)


def test_blank_variant_parent_alone_is_hard_reject():
    r = _qa([_bare_row(AntibodyParent="   ")])
    assert r.disposition == HARD_REJECT
    assert any("blank Parent" in h for h in r.hard)


def test_variant_parent_key_semicolon_split():
    r = _qa([_bare_row(AntibodyParent="D.SEQ-220823SHA-1;D.SEQ-220823SHA-2")])
    assert r.disposition == CLEAN

    bad = _qa([_bare_row(AntibodyParent="D.SEQ-220823SHA-1;D.SEQ-nope")])
    assert bad.disposition == HARD_REJECT
    assert any("D.SEQ-nope" in h for h in bad.hard)


def test_variant_parent_placeholder_marker_is_skipped():
    r = _qa([_bare_row(AntibodyParent="*** PLACEHOLDER: antibody parent ***")])
    assert not any("parent_uid_not_found" in h for h in r.hard)
    assert not any("blank Parent" in h for h in r.hard)


def test_literal_and_variant_parents_are_both_checked():
    r = _qa([{"json_metadata": {"Parent": "D.SEQ-220823SHA-1",
                                "Treatment1Parent": "D.SEQ-bogus"},
              "assay_ids": [12]}])
    assert r.disposition == HARD_REJECT
    assert any("D.SEQ-bogus" in h for h in r.hard)


def test_variant_parent_resolves_against_intra_batch_name():
    rows = [
        {"json_metadata": {"Parent": "D.SEQ-220823SHA-1", "Name": "in_batch_ab"},
         "assay_ids": [12]},
        _bare_row(AntibodyParent="in_batch_ab"),
    ]
    r = _qa(rows)
    assert r.disposition == CLEAN, r.hard

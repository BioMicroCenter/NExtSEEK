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


def test_blank_required_field_is_hard_reject():
    # A blank required field WILL be rejected by the server on upload, so blocking here
    # is the whole point: it used to be advisory, which let invalid workbooks through.
    r = qa_rows([_row("D.SEQ-220823SHA-1")], sample_type="A.SCXP", known_sampletypes=_TYPES,
                required_fields=["Parent", "File_PrimaryData"], existing_parent_uids=_PARENTS)
    assert r.disposition == HARD_REJECT
    assert any("missing_required" in h and "File_PrimaryData" in h for h in r.hard)


def test_placeholder_required_field_is_soft_flag():
    # An INTENTIONAL placeholder is the curator saying "I know, later" — advisory, not a
    # blocker. This split is what makes SKILL.md's "never leave it blank" load-bearing.
    r = qa_rows([_row("D.SEQ-220823SHA-1", File_PrimaryData="*** PLACEHOLDER: md5 pending ***")],
                sample_type="A.SCXP", known_sampletypes=_TYPES,
                required_fields=["Parent", "File_PrimaryData"], existing_parent_uids=_PARENTS)
    assert r.disposition == SOFT_FLAG
    assert any("placeholder_required" in s and "File_PrimaryData" in s for s in r.soft)
    assert not r.hard


def test_catalog_unavailable_degrades_loudly():
    # An empty/missing catalog must not silently report CLEAN over unchecked rows.
    r = qa_rows([_row("D.SEQ-220823SHA-1")], sample_type="A.WHATEVER",
                known_sampletypes=None, existing_parent_uids=_PARENTS)
    assert r.disposition == SOFT_FLAG
    assert any("catalog_unavailable" in s for s in r.soft)
    assert not r.hard          # unknown type cannot be judged without a catalog


def test_invented_attribute_is_soft_not_hard():
    # The snapshot catalog disagrees with production rows (live A.SCXP carries
    # Checksum_Type; the snapshot lists Checksum_PrimaryType), so a hard rule here
    # would reject valid curation. Hard enforcement belongs against the LIVE schema.
    r = qa_rows([_row("D.SEQ-220823SHA-1", Checksum_Type="md5")],
                sample_type="A.SCXP", known_sampletypes=_TYPES,
                known_attributes={"Parent", "Scientist"}, existing_parent_uids=_PARENTS)
    assert r.disposition == SOFT_FLAG
    assert any("invented_attribute" in s and "Checksum_Type" in s for s in r.soft)
    assert not r.hard


def test_parent_type_mismatch_is_soft():
    # A.SCXP declares D.SEQ parents. Hanging one off another A.* is unusual but legal
    # (re-analysis), so advise rather than block — the server's DAG check is the floor.
    r = qa_rows([_row("A.ALN-220823SHA-9")], sample_type="A.SCXP", known_sampletypes=_TYPES,
                parent_types={"D.SEQ"}, existing_parent_uids={"A.ALN-220823SHA-9"})
    assert r.disposition == SOFT_FLAG
    assert any("parent_type_mismatch" in s for s in r.soft)
    assert not r.hard


def test_intra_batch_name_resolves_as_parent_exactly():
    # A row may derive from a sibling created in the same batch. The match must be
    # EXACT: the old substring test made "D.SEQ-1" resolve against a Name of
    # "D.SEQ-12", silently accepting a parent that does not exist.
    rows = [_row("D.SEQ-220823SHA-1", Name="sibling_a"), _row("sibling_a", Name="child_b")]
    r = qa_rows(rows, sample_type="A.SCXP", known_sampletypes=_TYPES, existing_parent_uids=_PARENTS)
    assert r.disposition == CLEAN

    bad = [_row("D.SEQ-220823SHA-1", Name="sibling_alpha"), _row("sibling_a", Name="child_b")]
    r2 = qa_rows(bad, sample_type="A.SCXP", known_sampletypes=_TYPES, existing_parent_uids=_PARENTS)
    assert r2.disposition == HARD_REJECT
    assert any("parent_uid_not_found" in h for h in r2.hard)


def test_catalog_fields_projects_the_real_catalog_entry():
    from nextseek_api.assistant.reingest_qa import catalog_fields
    entry = {"Required Metadata": "UID, File_PrimaryData, Scientist, Parent",
             "Standard Metadata": "Pipeline, ReferenceGenome",
             "Possible Metadata Fields": "Notes",
             "Parent_SampleTypes": "D.SEQ"}
    f = catalog_fields(entry)
    assert f["required"] == ["File_PrimaryData", "Parent", "Scientist"]   # UID is server-minted
    assert f["known"] == {"UID", "File_PrimaryData", "Scientist", "Parent",
                          "Pipeline", "ReferenceGenome", "Notes"}
    assert f["parent_types"] == {"D.SEQ"}
    # An absent entry must disable the checks rather than assert an empty schema.
    assert catalog_fields(None) == {"required": None, "known": None, "parent_types": None}

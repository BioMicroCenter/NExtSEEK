"""granular._build_upload_xlsx: group rows by type, QA, render one workbook per type."""
import json

from nextseek_api.batch_upload.convert import parse_traditional_file
import nextseek_api.assistant.granular as g


# The real sample-type catalog is keyed by code and carries the field groups as
# comma-separated strings — the same shape as chat_nextseek's FULL_SAMPLETYPES_MAP,
# which is what the op reads off `config` in production.
_CATALOG = {
    "A.SCXP": {
        "Required Metadata": "UID, File_PrimaryData, Link_PrimaryData, Scientist, Parent, Checksum_PrimaryData",
        "Standard Metadata": "Pipeline, ReferenceGenome",
        "Possible Metadata Fields": "Notes, Checksum_PrimaryType",
        "Parent_SampleTypes": "D.SEQ",
    },
    "A.ALN": {
        "Required Metadata": "UID, File_PrimaryData, Link_PrimaryData, Scientist, Parent, Checksum_PrimaryData",
        "Standard Metadata": "Pipeline, ReferenceGenome, Aligner",
        "Possible Metadata Fields": "Notes",
        "Parent_SampleTypes": "D.SEQ",
    },
}


class _Cfg:
    """Config with the real catalog wired, as production has it."""
    FULL_SAMPLETYPES_MAP = _CATALOG


class _BareCfg:
    """Config with no catalog at all — the degraded path."""


def _complete_meta(parent, **extra):
    """Every field A.* actually requires. UID is omitted: the server mints it."""
    return {
        "Parent": parent,
        "Scientist": "Marie Floryan",
        "File_PrimaryData": "sample.count_matrix.h5",
        "Link_PrimaryData": "/net/bmc-pub10/data1/bmc/runs/r1/sample.count_matrix.h5",
        "Checksum_PrimaryData": "9320c40438931484e4b448d77410b8a1",
        **extra,
    }


def _rows(*specs, meta_fn=_complete_meta):
    return json.dumps([
        {"SampleType": st, "json_metadata": meta_fn(parent), "assay_ids": aids}
        for (st, parent, aids) in specs
    ])


def test_groups_by_type_and_renders_each(tmp_path):
    rows = _rows(
        ("A.SCXP", "D.SEQ-1", [12]),
        ("A.SCXP", "D.SEQ-2", [12]),
        ("A.ALN", "D.SEQ-1", []),
    )
    out = g._build_upload_xlsx(
        {"rows": rows, "existing_parent_uids": "D.SEQ-1,D.SEQ-2"},
        _Cfg(), None, None, None, str(tmp_path))

    import re
    # artifact KEYS must be route-safe (download route accepts only [\w]+; no dots),
    # while the file on disk keeps the readable dotted name.
    assert set(out["saved_files"]) == {"reingest_A_SCXP", "reingest_A_ALN"}
    assert all(re.fullmatch(r"[\w]+", k) for k in out["saved_files"])
    assert out["qa"]["A.SCXP"]["disposition"] == "CLEAN"
    assert out["complete"] is True
    assert out["rejected_types"] == []
    scxp_path = out["saved_files"]["reingest_A_SCXP"]
    assert scxp_path.endswith("reingest_A.SCXP.xlsx")
    # the rendered workbook round-trips through the real parser
    batch = parse_traditional_file(scxp_path)
    assert len(batch.rows) == 2
    assert all(r.SampleType == "A.SCXP" for r in batch.rows)


def test_hard_reject_type_is_skipped_and_reported(tmp_path):
    # Unresolvable parent -> HARD_REJECT -> no workbook for that type.
    rows = _rows(("A.SCXP", "D.SEQ-does-not-exist", [12]))
    out = g._build_upload_xlsx(
        {"rows": rows, "existing_parent_uids": "D.SEQ-1"},
        _Cfg(), None, None, None, str(tmp_path))
    assert out["saved_files"] == {}
    assert out["qa"]["A.SCXP"]["disposition"] == "HARD_REJECT"
    # The caller must not have to diff saved_files against qa to notice the shortfall.
    assert out["complete"] is False
    assert out["rejected_types"] == ["A.SCXP"]


def test_incomplete_rows_are_rejected_not_rendered(tmp_path):
    # THE regression this whole change exists for: rows carrying only Parent+Scientist
    # are missing 3 of the 5 attributes every A.* type requires. Before the catalog was
    # wired in, this validated CLEAN and produced a workbook that bounced at upload.
    def _thin(parent):
        return {"Parent": parent, "Scientist": "Marie Floryan"}

    rows = _rows(("A.SCXP", "D.SEQ-1", [12]), meta_fn=_thin)
    out = g._build_upload_xlsx(
        {"rows": rows, "existing_parent_uids": "D.SEQ-1"},
        _Cfg(), None, None, None, str(tmp_path))
    assert out["saved_files"] == {}
    assert out["complete"] is False
    hard = out["qa"]["A.SCXP"]["hard"]
    for missing in ("File_PrimaryData", "Link_PrimaryData", "Checksum_PrimaryData"):
        assert any(missing in h for h in hard), f"{missing} not flagged: {hard}"


def test_hallucinated_sampletype_is_rejected(tmp_path):
    # `known` used to be derived from the rows themselves, so a made-up type validated
    # itself. It is now checked against the real catalog.
    rows = _rows(("A.FOO", "D.SEQ-1", [12]))
    out = g._build_upload_xlsx(
        {"rows": rows, "existing_parent_uids": "D.SEQ-1"},
        _Cfg(), None, None, None, str(tmp_path))
    assert out["saved_files"] == {}
    assert out["rejected_types"] == ["A.FOO"]
    assert any("unknown_sampletype" in h for h in out["qa"]["A.FOO"]["hard"])


def test_missing_catalog_degrades_loudly_and_still_renders(tmp_path):
    # With no catalog we cannot judge types or required fields. Say so in the QA report
    # rather than reporting a confident CLEAN — but still produce the workbook, because
    # refusing to render would break every caller with a bare config.
    rows = _rows(("A.SCXP", "D.SEQ-1", [12]))
    out = g._build_upload_xlsx(
        {"rows": rows, "existing_parent_uids": "D.SEQ-1"},
        _BareCfg(), None, None, None, str(tmp_path))
    assert out["complete"] is True
    assert set(out["saved_files"]) == {"reingest_A_SCXP"}
    assert out["qa"]["A.SCXP"]["disposition"] == "SOFT_FLAG"
    assert any("catalog_unavailable" in s for s in out["qa"]["A.SCXP"]["soft"])


def test_bad_json_rows_raises_validation(tmp_path):
    import pytest
    with pytest.raises(g.OpValidationError):
        g._build_upload_xlsx({"rows": "not json"}, _Cfg(), None, None, None, str(tmp_path))

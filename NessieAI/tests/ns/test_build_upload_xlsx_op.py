"""granular._build_upload_xlsx: group rows by type, QA, render one workbook per type."""
import json

from nextseek_api.batch_upload.convert import parse_traditional_file
import nextseek_api.services.reingest_lookups as reingest_lookups
import NessieAI.ns.granular as g


class _Cfg:
    pass


def _rows(*specs):
    return json.dumps([
        {"SampleType": st, "json_metadata": {"Parent": parent, "Scientist": "Marie Floryan"},
         "assay_ids": aids}
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
    scxp_path = out["saved_files"]["reingest_A_SCXP"]
    assert scxp_path.endswith("reingest_A.SCXP.xlsx")
    # the rendered workbook round-trips through the real parser
    batch = parse_traditional_file(scxp_path)
    assert len(batch.rows) == 2
    assert all(r.SampleType == "A.SCXP" for r in batch.rows)


def test_hard_reject_type_is_skipped(tmp_path):
    # Unresolvable parent -> HARD_REJECT -> no workbook for that type.
    rows = _rows(("A.SCXP", "D.SEQ-does-not-exist", [12]))
    out = g._build_upload_xlsx(
        {"rows": rows, "existing_parent_uids": "D.SEQ-1"},
        _Cfg(), None, None, None, str(tmp_path))
    assert out["saved_files"] == {}
    assert out["qa"]["A.SCXP"]["disposition"] == "HARD_REJECT"


def test_bad_json_rows_raises_validation(tmp_path):
    import pytest
    with pytest.raises(g.OpValidationError):
        g._build_upload_xlsx({"rows": "not json"}, _Cfg(), None, None, None, str(tmp_path))


def test_empty_catalog_falls_back_to_permissive_known_types(tmp_path):
    # This test is NOT django_db-marked, so known_sample_types() sees an
    # unreachable table and (per context_catalog's house rule) comes back
    # empty -- "I could not find out", not "every type here is unknown". A
    # populated-vs-empty check must fall back to the old permissive
    # set(by_type) in that case, or a briefly-unavailable catalog would
    # hard-reject every sample type in every reingest.
    rows = _rows(("A.MADE-UP-TYPE", "D.SEQ-1", [12]))
    out = g._build_upload_xlsx(
        {"rows": rows, "existing_parent_uids": "D.SEQ-1"},
        _Cfg(), None, None, None, str(tmp_path))
    assert out["qa"]["A.MADE-UP-TYPE"]["disposition"] == "CLEAN"
    assert set(out["saved_files"]) == {"reingest_A_MADE_UP_TYPE"}


# --- populated-catalog wiring -----------------------------------------------
#
# The three tests above (and every other test in this file) run with an
# unreachable catalog table, so they only ever exercise the permissive
# `known = set(by_type)` fallback -- never the populated-catalog branch that
# is the entire point of reviving these checks. These monkeypatch
# `known_sample_types`/`attributes_for` on `nextseek_api.services.reingest_lookups`
# itself (where `_build_upload_xlsx` imports them from, inside the function
# body) so the catalog looks populated without touching the database.

def test_type_absent_from_populated_catalog_is_hard_rejected(tmp_path, monkeypatch):
    # Unlike the empty-catalog fallback above, a POPULATED catalog that simply
    # does not list this type must reject it -- even though the type derives
    # cleanly from the rows themselves, which is all the permissive fallback
    # ever checks.
    monkeypatch.setattr(reingest_lookups, "known_sample_types", lambda: {"A.SCXP"})
    monkeypatch.setattr(reingest_lookups, "attributes_for", lambda st: [])
    rows = _rows(("A.MADE-UP-TYPE", "D.SEQ-1", [12]))
    out = g._build_upload_xlsx(
        {"rows": rows, "existing_parent_uids": "D.SEQ-1"},
        _Cfg(), None, None, None, str(tmp_path))
    assert out["qa"]["A.MADE-UP-TYPE"]["disposition"] == "HARD_REJECT"
    assert out["saved_files"] == {}


def test_missing_catalog_required_attribute_is_hard_rejected(tmp_path, monkeypatch):
    # The type itself is known, but the catalog marks an attribute required
    # and no row supplies it.
    monkeypatch.setattr(reingest_lookups, "known_sample_types", lambda: {"A.SCXP"})
    monkeypatch.setattr(
        reingest_lookups, "attributes_for",
        lambda st: [{"title": "Checksum_PrimaryData", "required": True}])
    rows = _rows(("A.SCXP", "D.SEQ-1", [12]))
    out = g._build_upload_xlsx(
        {"rows": rows, "existing_parent_uids": "D.SEQ-1"},
        _Cfg(), None, None, None, str(tmp_path))
    assert out["qa"]["A.SCXP"]["disposition"] == "HARD_REJECT"
    assert out["saved_files"] == {}


def test_populated_catalog_with_required_attribute_present_is_clean(tmp_path, monkeypatch):
    # Same populated catalog and the same required attribute, this time
    # supplied -- proves the wiring does not reject indiscriminately once a
    # catalog is present.
    monkeypatch.setattr(reingest_lookups, "known_sample_types", lambda: {"A.SCXP"})
    monkeypatch.setattr(
        reingest_lookups, "attributes_for",
        lambda st: [{"title": "Checksum_PrimaryData", "required": True}])
    rows = json.dumps([{
        "SampleType": "A.SCXP",
        "json_metadata": {"Parent": "D.SEQ-1", "Scientist": "Marie Floryan",
                           "Checksum_PrimaryData": "abc123"},
        "assay_ids": [12],
    }])
    out = g._build_upload_xlsx(
        {"rows": rows, "existing_parent_uids": "D.SEQ-1"},
        _Cfg(), None, None, None, str(tmp_path))
    assert out["qa"]["A.SCXP"]["disposition"] == "CLEAN"
    assert set(out["saved_files"]) == {"reingest_A_SCXP"}

"""Unit tests for nextseek_api.graph_sync.context_derived.derive (pure: plain rows in, changed cells out).

The fixtures are cut from fairdata-dev's tables as read on 2026-11-23 (BAC, TIS, DNA and their assays), trimmed to
what each rule needs.
"""
from __future__ import annotations

from nextseek_api.graph_sync.context_derived import derive

SEEK_TYPES = [{"id": 7, "title": "BAC"}, {"id": 8, "title": "TIS"}, {"id": 9, "title": "DNA"},
              {"id": 12, "title": "A.NEW"}, {"id": 30, "title": "BAC"}]


def _attrs(type_id, *titles, required=()):
    return [{"sample_type_id": type_id, "title": t, "pos": i, "required": t in required} for i, t in enumerate(titles)]


SEEK_ATTRS = (_attrs(7, "UID", "Name", "Scientist", "Species", "Strain", "Lab", required=("UID", "Species"))
              + _attrs(8, "UID", "Organ") + _attrs(9, "UID", "Concentration"))


def _row(code, **cols):
    base = {"sample_type": code, "sampletype_id": None, "clade": None, "required_metadata": None,
            "standard_metadata": None, "possible_metadata_fields": None, "parent_sampletypes": None,
            "child_sampletypes": None, "associated_assay_parents": None, "associated_assay_children": None}
    base.update(cols)
    return base


def _assay(row_id, name, required=None, children=None, optional=None, internal_id=None):
    return {"id": row_id, "assay_name": name, "Required_Parent_Sample_Types": required,
            "Optional_Parent_Sample_Types": optional, "Children_Sample_Types": children,
            "internal_assay_id": internal_id}


ASSAYS = [_assay(1, "Bacterial Extraction", "TIS", "BAC", internal_id=4),
          _assay(2, "DNA Extraction", "BAC or CEL", "DNA", internal_id=5)]
INTERNAL = [{"id": 4, "internal_assay_title": "Bacterial Extraction"},
            {"id": 5, "internal_assay_title": "DNA Extraction"},
            {"id": 6, "internal_assay_title": "Library Prep"}]


def run(context, *, assays=ASSAYS, observed=(), clades=None, internal=INTERNAL, attrs=SEEK_ATTRS, types=SEEK_TYPES):
    return derive(context_rows=context, assay_rows=assays, seek_types=types, seek_attributes=attrs,
                  type_clades=clades or {}, internal_assays=internal, observed=observed)


def test_id_and_clade_follow_seek_and_the_clades_table_by_code():
    out = run([_row("BAC", sampletype_id=131, clade="Source"), _row("TIS", sampletype_id=8, clade="Source")],
              clades={7: "Source", 8: "Processed"})
    assert out.sample_types["BAC"]["sampletype_id"] == 7          # lowest id of a duplicated title wins
    assert "clade" not in out.sample_types["BAC"]
    assert out.sample_types["TIS"] == {**out.sample_types["TIS"], "clade": "Processed"}
    assert "sampletype_id" not in out.sample_types["TIS"]


def test_a_clade_missing_from_the_clades_table_keeps_the_typed_one():
    out = run([_row("TIS", sampletype_id=8, clade="Source")], clades={})
    assert "clade" not in out.sample_types.get("TIS", {})


def test_a_row_whose_code_this_box_lacks_is_left_alone_and_reported():
    row = _row("D.GONE", sampletype_id=99, required_metadata="UID, Scientist", parent_sampletypes="TIS")
    out = run([row])
    assert "D.GONE" not in out.sample_types
    assert out.report["no_seek_type"] == ["D.GONE"]


def test_metadata_membership_follows_seek_and_the_tiers_stay_hand_set():
    row = _row("BAC", sampletype_id=7, required_metadata="UID, Name, Scientist",
               standard_metadata="Species, Protocol", possible_metadata_fields="Lab, DOI")
    cells = run([row]).sample_types["BAC"]
    assert cells["required_metadata"] == "UID, Name, Scientist, Species"   # SEEK-required Species joins required
    assert cells["standard_metadata"] is None                              # Species moved up; Protocol not in SEEK
    assert cells["possible_metadata_fields"] == "Lab, Strain"              # DOI gone; Strain appended in SEEK order


def test_a_type_with_no_seek_attributes_keeps_its_metadata_lists():
    row = _row("BAC", sampletype_id=7, required_metadata="UID, Name")
    cells = run([row], attrs=[]).sample_types.get("BAC", {})
    assert "required_metadata" not in cells and "possible_metadata_fields" not in cells


def test_connection_lists_are_designed_plus_observed_and_keep_their_order():
    rows = [_row("BAC", sampletype_id=7, parent_sampletypes="TIS", child_sampletypes="RNA, DNA",
                 associated_assay_parents="Bacterial Extraction", associated_assay_children="DNA Extraction")]
    observed = [("TIS", "Library Prep", "BAC"), ("BAC", "Library Prep", "DNA")]
    cells = run(rows, observed=observed).sample_types["BAC"]
    assert "parent_sampletypes" not in cells                               # TIS: designed and observed, unchanged
    assert cells["child_sampletypes"] == "DNA"                             # RNA backed by nothing, dropped
    assert cells["associated_assay_parents"] == "Bacterial Extraction, Library Prep"
    assert cells["associated_assay_children"] == "DNA Extraction, Library Prep"


def test_alternation_and_unknown_codes_in_assay_definitions():
    assays = [_assay(2, "DNA Extraction", "BAC or CEL", "DNA, A.NOPE")]
    out = run([_row("DNA", sampletype_id=9)], assays=assays)
    assert out.sample_types["DNA"]["parent_sampletypes"] == "BAC"          # CEL is no SEEK type on this box
    assert set(out.report["unknown_codes"]) == {"A.NOPE", "CEL"}


def test_connections_carry_designed_and_observed_flags_and_no_counts():
    out = run([_row("BAC", sampletype_id=7)], observed=[("TIS", "Bacterial Extraction", "BAC"),
                                                        ("TIS", "Library Prep", "BAC")])
    assert out.connections == [
        {"parent": "BAC", "assay": "DNA Extraction", "child": "DNA", "designed": True, "observed": False},
        {"parent": "TIS", "assay": "Bacterial Extraction", "child": "BAC", "designed": True, "observed": True},
        {"parent": "TIS", "assay": "Library Prep", "child": "BAC", "designed": False, "observed": True},
    ]


def test_internal_assay_id_keeps_a_valid_link_and_fills_a_missing_one():
    assays = [_assay(1, "Bacterial Extraction", internal_id=5),      # valid id, other title: kept, reported
              _assay(2, "Library Prep", internal_id=None),           # filled by exact title
              _assay(3, "Gone Assay", internal_id=77)]               # dangling, no title match: cleared
    out = run([], assays=assays)
    assert out.assays == {2: {"internal_assay_id": 6}, 3: {"internal_assay_id": None}}
    assert out.report["assay_title_mismatch"] == [{"assay_name": "Bacterial Extraction", "internal_assay_id": 5,
                                                   "internal_assay_title": "DNA Extraction"}]


def test_needs_definition_lists_what_has_no_row():
    out = run([_row("BAC", sampletype_id=7), _row("TIS", sampletype_id=8)])
    assert out.report["needs_definition"] == {"sample_types": ["A.NEW", "DNA"], "assays": ["Library Prep"]}


def test_applying_the_changes_makes_the_next_run_empty():
    rows = [_row("BAC", sampletype_id=131, required_metadata="UID", child_sampletypes="RNA"),
            _row("DNA", sampletype_id=1)]
    first = run(rows, observed=[("BAC", "Library Prep", "DNA")])
    applied = [{**r, **first.sample_types.get(r["sample_type"], {})} for r in rows]
    second = run(applied, observed=[("BAC", "Library Prep", "DNA")])
    assert second.sample_types == {} and second.assays == {}
    assert first.report["changed_cells"]["sampletype_id"] == 2

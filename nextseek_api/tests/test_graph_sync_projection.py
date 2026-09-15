import hashlib
import json
import re
from datetime import date
from unittest.mock import MagicMock

import pytest

from nextseek_api.batch_upload import helpers, identity
from nextseek_api.batch_upload.helpers import collect_parent_tokens
from nextseek_api.batch_upload.identity import extract_identity, hash_identity
from nextseek_api.batch_upload.models import InputRowModel, NodeRow
from nextseek_api.batch_upload.neo4j_sync import enrich_parent_titles
from nextseek_api.graph_sync import projection as p


@pytest.mark.parametrize("title, label", [
    ("TIS", "T_TIS"), ("D.SEQ", "T_D_SEQ"), ("A.VCF", "T_A_VCF"), ("X Y-1", "T_X_Y_1"),
])
def test_label_for(title, label):
    assert p.label_for(title) == label


@pytest.mark.parametrize("base, vt", [
    ("Float", "float"), ("Integer", "integer"), ("Date", "date"), ("DateTime", "date"),
    ("Text", "string"), ("String", "string"), (None, "string"),
])
def test_value_type_for(base, vt):
    assert p.value_type_for(base) == vt


@pytest.mark.parametrize("value, vt, expected", [
    ("12000000", "float", (12000000.0, True)),
    ("3", "integer", (3, True)),
    ("3.0", "integer", (3, True)),
    ("n/a", "float", ("n/a", False)),
    ("2024-01-31", "date", (date(2024, 1, 31), True)),
    ("2024-01-31 10:22:00", "date", (date(2024, 1, 31), True)),
    ("1/31/2024", "date", (date(2024, 1, 31), True)),
    ("2019", "date", ("2019", False)),
    ("Lung", "string", ("Lung", True)),
    (7, "string", (7, True)),
])
def test_cast_value(value, vt, expected):
    assert p.cast_value(value, vt) == expected


@pytest.mark.parametrize("value, vt, expected", [
    # JSON numbers are cast like their string form
    (12, "float", (12.0, True)),
    (3.0, "integer", (3, True)),
    # a fractional value never truncates into an integer attribute
    (3.5, "integer", (3.5, False)),
    ("3.5", "integer", ("3.5", False)),
    # booleans are not numbers, and a number is not a date
    (True, "float", (True, False)),
    (False, "integer", (False, False)),
    (2019, "date", (2019, False)),
    # non-finite values never reach the graph as numbers
    ("1e999", "float", ("1e999", False)),
    ("1e999", "integer", ("1e999", False)),
    (float("inf"), "float", (float("inf"), False)),
    (float("inf"), "integer", (float("inf"), False)),
    # a date that does not exist fails cleanly
    ("2/30/2024", "date", ("2/30/2024", False)),
    ("2024-13-01", "date", ("2024-13-01", False)),
    ("  2024-01-31  ", "date", (date(2024, 1, 31), True)),
    # an ISO date with trailing text is not cut down to a date
    ("2024-01-31 (approx)", "date", ("2024-01-31 (approx)", False)),
    ("2024-01-31T10:22:00.000+01:00", "date", (date(2024, 1, 31), True)),
    # Neo4j integers are 64-bit: anything wider must not reach the write as an int
    ("9223372036854775807", "integer", (2 ** 63 - 1, True)),
    ("9223372036854775808", "integer", ("9223372036854775808", False)),
    (2 ** 63, "integer", (2 ** 63, False)),
    (2 ** 63, "string", (str(2 ** 63), False)),
    (2 ** 63 - 1, "string", (2 ** 63 - 1, True)),
])
def test_cast_value_edges(value, vt, expected):
    assert p.cast_value(value, vt) == expected


def test_cast_value_nan_fails():
    value, ok = p.cast_value("nan", "float")
    assert (value, ok) == ("nan", False)
    value, ok = p.cast_value(float("nan"), "float")
    assert ok is False


@pytest.mark.parametrize("value, empty", [
    (None, True), ("", True), ([], True), ({}, True), (" ", False), (0, False), ("0", False),
    (False, False), (0.0, False),
])
def test_is_empty(value, empty):
    assert p.is_empty(value) is empty


def test_key_sets():
    assert p.SYSTEM_KEYS == frozenset({"id", "uuid", "type", "title", "project_ids", "search_text", "synced_at",
                                       "source_hash", "parent_titles", "parent_title_hashes"})
    assert p.SKIPPED_METADATA_KEYS == frozenset({"UID"})


def _row(meta, **over):
    row = {"id": 5, "uuid": "TIS-220119FLY-7", "title": "t", "sample_type_id": 26,
           "json_metadata": json.dumps(meta)}
    row.update(over)
    return row


def test_project_sample_keeps_non_empty_values_verbatim_and_typed():
    meta = {"UID": "TIS-220119FLY-7", "Organ": "Lung", "Type": "PBMC", "Media supplement ": "x",
            "CellCount": "12000000", "Empty": "", "Nothing": None, "Parent": "MUS-1;MUS-2"}
    proj = p.project_sample(_row(meta), "TIS", {"CellCount": "float"}, [6, 2, 2])
    assert proj.label == "T_TIS"
    assert proj.props["id"] == 5 and proj.props["uuid"] == "TIS-220119FLY-7"
    assert proj.props["type"] == "TIS" and proj.props["project_ids"] == [2, 6]
    assert proj.props["Organ"] == "Lung" and proj.props["Type"] == "PBMC"
    assert proj.props["Media supplement "] == "x"
    assert proj.props["CellCount"] == 12000000.0
    assert "UID" not in proj.props and "Empty" not in proj.props and "Nothing" not in proj.props
    assert proj.props["Parent"] == "MUS-1;MUS-2"


def test_project_sample_shape():
    proj = p.project_sample(_row({"Organ": "Lung"}, id="9", sample_type_id="26"), "D.SEQ", {}, ["3"])
    assert isinstance(proj, p.SampleProjection)
    assert proj.id == 9 and proj.sample_type_id == 26 and proj.label == "T_D_SEQ"
    assert proj.props["id"] == 9 and proj.props["project_ids"] == [3]
    assert proj.props["title"] == "t"
    assert proj.cast_failures == []


def test_search_text_holds_values_only_one_per_line_unstripped():
    meta = {"Organ": "Lung", "Notes": " granuloma  ", "CellCount": "5"}
    proj = p.project_sample(_row(meta), "TIS", {"CellCount": "float"}, [])
    lines = proj.props["search_text"].split("\n")
    assert sorted(lines) == sorted(["Lung", " granuloma  ", "5"])
    assert "Organ" not in proj.props["search_text"]


def test_search_text_holds_the_raw_value_not_the_cast_one():
    proj = p.project_sample(_row({"When": "1/31/2024"}), "TIS", {"When": "date"}, [])
    assert proj.props["When"] == date(2024, 1, 31)
    assert proj.props["search_text"] == "1/31/2024"


def test_uid_is_not_a_property_but_is_searchable():
    # advanced_search's LIKE over json_metadata matches a term found only in the UID ("TIS-" finds every TIS
    # sample, measured on the merged data), so the keyword text carries the UID value even though uuid holds it.
    proj = p.project_sample(_row({"UID": "TIS-X-1", "Organ": "Lung"}), "TIS", {}, [])
    assert "UID" not in proj.props
    assert proj.props["search_text"].split("\n") == ["TIS-X-1", "Lung"]


def test_an_empty_uid_adds_nothing_to_search_text():
    proj = p.project_sample(_row({"UID": "", "Organ": "Lung"}), "TIS", {}, [])
    assert proj.props["search_text"] == "Lung"


def test_cast_failure_keeps_raw_string_and_is_reported():
    proj = p.project_sample(_row({"CellCount": "lots"}), "TIS", {"CellCount": "float"}, [])
    assert proj.props["CellCount"] == "lots"
    assert proj.cast_failures == ["CellCount"]


def test_non_primitive_values_become_json_strings():
    proj = p.project_sample(_row({"Tags": ["a", "b"]}), "TIS", {}, [])
    assert proj.props["Tags"] == '["a", "b"]'


def test_empty_or_missing_metadata_projects_system_keys_only():
    for raw in (None, "", "{}", "null"):
        proj = p.project_sample(_row({}, json_metadata=raw), "TIS", {}, [2])
        assert set(proj.props) == {"id", "uuid", "type", "title", "project_ids", "search_text", "source_hash"}
        assert proj.props["search_text"] == ""


def test_empty_title_is_absent():
    for title in (None, ""):
        proj = p.project_sample(_row({"Organ": "Lung"}, title=title), "TIS", {}, [])
        assert "title" not in proj.props


def test_non_object_metadata_is_refused():
    with pytest.raises(ValueError, match="sample 5"):
        p.project_sample(_row(["a"]), "TIS", {}, [])


def test_metadata_never_overwrites_a_system_property():
    # Case variants (Type, ID) are metadata and sit beside the system properties.
    proj = p.project_sample(_row({"Type": "PBMC", "ID": "7"}), "TIS", {}, [])
    assert proj.props["type"] == "TIS" and proj.props["id"] == 5
    assert proj.props["Type"] == "PBMC" and proj.props["ID"] == "7"
    # An exact system name would clobber the node's key, so it is refused loudly.
    for key in ("id", "project_ids", "search_text"):
        with pytest.raises(ValueError, match=key):
            p.project_sample(_row({key: "x"}), "TIS", {}, [])


# --- schema 1.2: the source hash (spec 10.3) -------------------------------------------------------

_VALUE_TYPES = {"Organ": "string", "CellCount": "float"}


def _hash(**over):
    args = {"row": _row({"Organ": "Lung"}), "type_title": "TIS", "value_types": dict(_VALUE_TYPES),
            "project_ids": [6, 2], "assay_ids": [31, 7]}
    args.update(over)
    return p.source_hash(args["row"], args["type_title"], args["value_types"], args["project_ids"],
                         args["assay_ids"])


def test_source_hash_is_a_sha256_hex_digest_and_repeatable():
    digest = _hash()
    assert re.fullmatch(r"[0-9a-f]{64}", digest)
    assert _hash() == digest


def test_source_hash_encoding_is_pinned():
    # Every Sample node stores this digest, and the nightly sync recomputes it from MySQL. Changing the encoding
    # makes every sample mismatch and resync, so change it only together with a schema version bump.
    row = {"id": 1, "uuid": "U-1", "title": "t", "sample_type_id": 3, "json_metadata": '{"a":1}'}
    expected = b"\x1f".join([
        b"3\x1fU-1", b"1\x1ft", b"3\x1fTIS",
        b"1\x1f2", b"1\x1fa", b"6\x1fstring", b"1\x1fb", b"5\x1ffloat",
        b'7\x1f{"a":1}',
        b"1\x1f2", b"1\x1f2", b"1\x1f6",
        b"1\x1f1", b"1\x1f9",
    ])
    assert p.source_hash(row, "TIS", {"b": "float", "a": "string"}, [6, 2], [9]) == \
        hashlib.sha256(expected).hexdigest()
    # a missing title is its own marker, never the empty string's encoding
    none_title = dict(row, title=None)
    expected_none = expected.replace(b"\x1f1\x1ft\x1f", b"\x1fN\x1f", 1)
    assert p.source_hash(none_title, "TIS", {"b": "float", "a": "string"}, [6, 2], [9]) == \
        hashlib.sha256(expected_none).hexdigest()


def test_source_hash_ignores_the_order_and_repeats_of_ids_and_value_types():
    base = _hash()
    assert _hash(project_ids=[2, 6]) == base
    assert _hash(project_ids=[6, 2, 2, 6]) == base
    assert _hash(project_ids=["6", "2"]) == base
    assert _hash(assay_ids=(7, 31)) == base
    assert _hash(assay_ids=[31, 7, 7]) == base
    assert _hash(value_types={"CellCount": "float", "Organ": "string"}) == base


@pytest.mark.parametrize("change", [
    {"row": _row({"Organ": "Lung"}, uuid="TIS-220119FLY-8")},
    {"row": _row({"Organ": "Lung"}, title="t ")},
    {"row": _row({"Organ": "Lung"}, title=None)},
    {"row": _row({"Organ": "Lung"}, title="")},
    {"type_title": "TIS "},
    {"type_title": "D.SEQ"},
    {"value_types": {"Organ ": "string", "CellCount": "float"}},
    {"value_types": {"Organ": "integer", "CellCount": "float"}},
    {"value_types": {"Organ": "string ", "CellCount": "float"}},
    {"value_types": {"Organ": "string"}},
    {"value_types": dict(_VALUE_TYPES, Notes="string")},
    # the raw bytes, not the parsed object: a whitespace-only change in the stored JSON counts
    {"row": _row({}, json_metadata='{"Organ":"Lung"}')},
    {"row": _row({"Organ": "Lung "})},
    {"row": _row({"Organ": "Lung", "Notes": "x"})},
    {"project_ids": [6, 2, 3]},
    {"project_ids": [6, 3]},
    {"project_ids": []},
    {"assay_ids": [31, 7, 8]},
    {"assay_ids": [31]},
    {"assay_ids": []},
])
def test_source_hash_changes_with_any_single_input(change):
    assert _hash(**change) != _hash()


@pytest.mark.parametrize("left, right", [
    # naive concatenation would give the same bytes for each of these pairs
    ({"row": _row({}, uuid="U-1", title="t")}, {"row": _row({}, uuid="U-1t", title="")}),
    ({"row": _row({}, title="a\x1fb"), "type_title": "c"}, {"row": _row({}, title="a"), "type_title": "b\x1fc"}),
    ({"project_ids": [1], "assay_ids": []}, {"project_ids": [], "assay_ids": [1]}),
    ({"project_ids": [12]}, {"project_ids": [1, 2]}),
    ({"value_types": {"a": "bc"}}, {"value_types": {"ab": "c"}}),
])
def test_source_hash_fields_never_run_into_each_other(left, right):
    assert _hash(**left) != _hash(**right)


def test_source_hash_reads_json_metadata_as_its_utf8_bytes():
    text = '{"Organ": "Lüng"}'
    assert _hash(row=_row({}, json_metadata=text)) == _hash(row=_row({}, json_metadata=text.encode("utf-8")))


def test_project_sample_stores_the_source_hash():
    row = _row({"Organ": "Lung", "CellCount": "5"})
    proj = p.project_sample(row, "TIS", _VALUE_TYPES, [6, 2, 2], assay_ids=[31, 7])
    assert proj.props["source_hash"] == p.source_hash(row, "TIS", _VALUE_TYPES, [6, 2], [31, 7])
    # no assay ids given is the empty assay list
    plain = p.project_sample(row, "TIS", _VALUE_TYPES, [6, 2])
    assert plain.props["source_hash"] == p.source_hash(row, "TIS", _VALUE_TYPES, [6, 2], [])


def test_project_sample_hash_follows_the_assay_ids_and_nothing_else_moves():
    row = _row({"Organ": "Lung"})
    one = p.project_sample(row, "TIS", {}, [2], assay_ids=[7])
    two = p.project_sample(row, "TIS", {}, [2], assay_ids=[8])
    assert one.props["source_hash"] != two.props["source_hash"]
    assert {k: v for k, v in one.props.items() if k != "source_hash"} == \
        {k: v for k, v in two.props.items() if k != "source_hash"}


def test_a_metadata_key_named_like_a_new_system_key_is_refused():
    for key in ("source_hash", "parent_titles", "parent_title_hashes"):
        assert key in p.SYSTEM_KEYS
        with pytest.raises(ValueError, match=key):
            p.project_sample(_row({key: "x"}), "TIS", {}, [])


# --- schema 1.2: the parent lists (spec 6, R4) ------------------------------------------------------

def test_parent_lists_uses_batch_uploads_hash_and_uid_rule():
    # imported, never copied: orphan discovery matches these hashes against hash_identity's
    assert p.hash_identity is identity.hash_identity
    assert p.UID_RE is helpers.UID_RE


def test_parent_lists_resolves_uids_keeps_names_and_drops_unresolved_uids():
    tokens = ["NHP-260225MIT-1", "Unresolved_Name", "NHP-260225MIT-9", "D.IMG-260225MIT-5"]
    ids = {"NHP-260225MIT-1": "Parent_A", "D.IMG-260225MIT-5": "image_data.tiff"}
    titles, hashes = p.parent_lists(tokens, ids)
    assert titles == ["Parent_A", "Unresolved_Name", "image_data.tiff"]
    assert hashes == [hash_identity(t) for t in titles]


@pytest.mark.parametrize("tokens, ids", [
    ([], {}),
    (["NHP-260225MIT-9"], {}),
    (["NHP-260225MIT-9"], {"NHP-260225MIT-9": None}),
    (["NHP-260225MIT-9"], {"NHP-260225MIT-9": ""}),
])
def test_parent_lists_with_nothing_resolvable_is_two_empty_lists(tokens, ids):
    assert p.parent_lists(tokens, ids) == ([], [])


# The fixtures of batch upload's TestEnrichParentTitles and TestEnrichParentTitlesVariantKeys, plus an external UID
# that MySQL does not return: (child metadata, in-batch parents as (uid, sample type, metadata), external parents
# as (uuid, stored metadata)).
_ENRICH_FIXTURES = {
    "in_batch_uid": ({"Name": "Child_Sample", "Parent": "NHP-260225MIT-1"},
                     [("NHP-260225MIT-1", "NHP", {"Name": "Parent_Sample"})], []),
    "unresolved_name": ({"Name": "Child_Sample", "Parent": "My_Parent_Name"}, [], []),
    "external_uid": ({"Name": "Child_Sample", "Parent": "NHP-260225MIT-99"},
                     [], [("NHP-260225MIT-99", {"Name": "External_Parent"})]),
    "file_based_in_batch": ({"Name": "Child_Sample", "Parent": "D.IMG-260225MIT-5"},
                            [("D.IMG-260225MIT-5", "D.IMG_files", {"File_PrimaryData": "image_data.tiff"})], []),
    "file_based_external": ({"Name": "Child_Sample", "Parent": "D.IMG-260225MIT-99"},
                            [], [("D.IMG-260225MIT-99", {"File_PrimartyData": "external_image.tiff"})]),
    "mixed": ({"Name": "Child", "Parent": "NHP-260225MIT-3;Unresolved_Name"},
              [("NHP-260225MIT-3", "NHP", {"Name": "Resolved_Parent"})], []),
    "no_parents": ({"Name": "Lonely_Sample"}, [], []),
    "variant_key": ({"Name": "Child_Sample", "Treatment1Parent": "NHP-260225MIT-1"},
                    [("NHP-260225MIT-1", "NHP", {"Name": "Treatment_Parent"})], []),
    "variant_keys_merged": ({"Name": "Child_Sample", "Parent": "NHP-260225MIT-1",
                             "Treatment1Parent": "NHP-260225MIT-3"},
                            [("NHP-260225MIT-1", "NHP", {"Name": "Parent_A"}),
                             ("NHP-260225MIT-3", "NHP", {"Name": "Parent_B"})], []),
    "variant_key_unresolved_name": ({"Name": "Child_Sample", "AntibodyParent": "My_Antibody_Parent"}, [], []),
    "external_uid_not_in_mysql": ({"Name": "Child_Sample", "Parent": "NHP-260225MIT-77"}, [], []),
}


def _enrich(child_meta, batch, external):
    node = NodeRow(sample_id=100, sample_uuid="NHP-260225MIT-50", sample_type="Blood", properties=dict(child_meta))
    models = [InputRowModel(UID="NHP-260225MIT-50", SampleType="Blood", json_metadata=json.dumps(child_meta))]
    models += [InputRowModel(UID=uid, SampleType=st, json_metadata=json.dumps(meta)) for uid, st, meta in batch]
    conn = MagicMock()
    conn.execute.return_value.fetchall.return_value = [(uuid, json.dumps(meta)) for uuid, meta in external]
    enrich_parent_titles([node], models, sql_conn=conn)
    return node.parent_titles, node.parent_title_hashes


@pytest.mark.parametrize("name", sorted(_ENRICH_FIXTURES))
def test_parent_lists_match_enrich_parent_titles(name):
    child_meta, batch, external = _ENRICH_FIXTURES[name]
    # The identities as enrich_parent_titles resolves them: an in-batch parent with its sample type, an external one
    # from its stored metadata by uuid alone.
    ids = {uid: extract_identity(meta, uid=uid, sample_type=st) for uid, st, meta in batch}
    ids.update({uuid: extract_identity(meta, uid=uuid) for uuid, meta in external})
    assert p.parent_lists(collect_parent_tokens(child_meta), ids) == _enrich(child_meta, batch, external)


def test_project_sample_writes_the_parent_lists_when_given():
    row = _row({"Organ": "Lung", "Parent": "NHP-260225MIT-1"})
    lists = p.parent_lists(["NHP-260225MIT-1"], {"NHP-260225MIT-1": "Parent_A"})
    proj = p.project_sample(row, "TIS", {}, [2], parent_lists=lists)
    assert proj.props["parent_titles"] == ["Parent_A"]
    assert proj.props["parent_title_hashes"] == [hash_identity("Parent_A")]
    assert proj.props["Parent"] == "NHP-260225MIT-1"


def test_given_parent_lists_are_always_written_even_empty():
    # Given means computed: an empty pair is written as two empty lists so the writer can clear a node's stale lists,
    # which it cannot tell apart from "not computed" when the keys are absent.
    proj = p.project_sample(_row({"Organ": "Lung"}), "TIS", {}, [2], parent_lists=([], []))
    assert proj.props["parent_titles"] == [] and proj.props["parent_title_hashes"] == []


def test_parent_lists_not_given_leave_both_keys_absent():
    proj = p.project_sample(_row({"Organ": "Lung", "Parent": "NHP-260225MIT-1"}), "TIS", {}, [2])
    assert "parent_titles" not in proj.props and "parent_title_hashes" not in proj.props


def test_parent_lists_are_not_part_of_the_source_hash():
    # The hash is of the sample's own row and links (spec 10.3); a parent's identity lives in the parent's row.
    row = _row({"Organ": "Lung", "Parent": "NHP-260225MIT-1"})
    with_lists = p.project_sample(row, "TIS", {}, [2], parent_lists=(["Parent_A"], [hash_identity("Parent_A")]))
    without = p.project_sample(row, "TIS", {}, [2])
    assert with_lists.props["source_hash"] == without.props["source_hash"]


def test_project_sample_copies_the_given_parent_lists():
    titles, hashes = ["Parent_A"], [hash_identity("Parent_A")]
    proj = p.project_sample(_row({"Organ": "Lung"}), "TIS", {}, [2], parent_lists=(titles, hashes))
    titles.append("later")
    hashes.append("later")
    assert proj.props["parent_titles"] == ["Parent_A"] and len(proj.props["parent_title_hashes"]) == 1

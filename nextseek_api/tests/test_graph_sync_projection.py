import json
from datetime import date

import pytest

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
                                       "parent_titles", "parent_title_hashes"})
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
        assert set(proj.props) == {"id", "uuid", "type", "title", "project_ids", "search_text"}
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

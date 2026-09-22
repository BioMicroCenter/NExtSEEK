"""The SampleType and Attribute catalog for graph schema v1.1 (nextseek_api/graph_sync/catalog.py)."""
import pytest

from nextseek_api.graph_sync import catalog as c


def _types():
    return [
        {"id": 10, "title": "MUS", "uuid": "u-mus", "description": "Mouse"},
        {"id": 26, "title": "TIS", "uuid": "u-tis", "description": "Tissue Sample"},
        {"id": 33, "title": "D.SEQ", "uuid": "u-dseq", "description": "Sequencing Data"},
        {"id": 12, "title": "IMG", "uuid": "u-img", "description": "Depreciated image"},
    ]


def _context():
    return {
        "TIS": {"name": "Tissue Sample", "description": "A piece of an organism.",
                "tags": "tissue, biopsy,  organ ", "parent_sampletypes": "PAV or MUS or CEL",
                "child_sampletypes": "DNA or D.SEQ, D.AD**", "clade": "Processed"},
        "MUS": {"name": "Mouse", "description": "A mouse.", "tags": "mouse",
                "parent_sampletypes": "", "child_sampletypes": None, "clade": ""},
    }


def _attr_types():
    return {7: {"id": 7, "title": "Text", "base_type": "Text"},
            3: {"id": 3, "title": "Real number", "base_type": "Float"},
            2: {"id": 2, "title": "Date", "base_type": "Date"},
            4: {"id": 4, "title": "Integer", "base_type": "Integer"}}


def _attr(aid, type_id, title, attr_type=7, **extra):
    row = {"id": aid, "sample_type_id": type_id, "title": title, "pos": aid, "required": 0,
           "is_title": 0, "sample_attribute_type_id": attr_type, "description": None}
    row.update(extra)
    return row


TYPE_TITLES = {10: "MUS", 26: "TIS", 33: "D.SEQ"}


def _by_title(rows):
    return {r["title"]: r for r in rows}


def _assert_neo4j_safe(props):
    """SET t = r needs flat values: no null, no map, lists of strings only."""
    for key, value in props.items():
        assert value is not None, key
        assert isinstance(value, (str, int, bool, list)), key
        if isinstance(value, list):
            assert value and all(isinstance(v, str) for v in value), key


# --- role_for ------------------------------------------------------------------------------------

@pytest.mark.parametrize("title, role", [
    ("Parent", "lineage"), ("MouseParent", "lineage"), ("parent_titles", "lineage"),
    ("File_Parent", "lineage"),
    ("File_PrimaryData", "file"), ("Checksum_PrimaryData", "file"), ("Link_Design", "file"),
    ("UID", "identifier"),
    ("CellCountUnits", "unit"), ("Volume_Units", "unit"), ("DoseUnit", "unit"), ("Temp_Unit", "unit"),
    ("Organ", "data"), ("uid", "data"), ("file_x", "data"), ("Unity", "data"), ("Units ", "data"),
])
def test_role_for(title, role):
    assert c.role_for(title) == role


# --- sample types --------------------------------------------------------------------------------

def test_build_sample_types_full_property_map_with_context():
    rows = c.build_sample_types(_types(), _context(), {26: "Raw", 10: "Source", 33: "Raw"}, {"IMG"})
    tis = _by_title(rows)["TIS"]
    assert tis == {
        "id": 26, "title": "TIS", "label": "T_TIS", "uuid": "u-tis", "seek_description": "Tissue Sample",
        "deprecated": False, "has_context": True, "name": "Tissue Sample",
        "summary": "A piece of an organism.", "tags": ["tissue", "biopsy", "organ"],
        "curated_parents": "MUS", "curated_children": "D.SEQ",
        # the context row's own clade wins over sample_types_clades
        "clade": "Processed",
    }
    for row in rows:
        _assert_neo4j_safe(row)


def test_curated_lists_keep_known_codes_in_order_joined_by_a_bar():
    types = _types() + [{"id": 40, "title": t, "uuid": t, "description": ""}
                        for t in ("PAV", "CEL", "DNA")]
    tis = _by_title(c.build_sample_types(types, _context(), {}, set()))["TIS"]
    # "PAV or MUS or CEL" and "DNA or D.SEQ, D.AD**": alternatives flattened, the wildcard dropped
    assert tis["curated_parents"] == "PAV | MUS | CEL"
    assert tis["curated_children"] == "DNA | D.SEQ"


def test_no_context_row_means_has_context_false_and_no_context_property():
    rows = _by_title(c.build_sample_types(_types(), _context(), {33: "Raw"}, set()))
    dseq = rows["D.SEQ"]
    assert dseq["has_context"] is False
    for key in c.CONTEXT_PROPERTIES:
        assert key not in dseq
    # the clade comes from sample_types_clades, which covers types without a context row
    assert dseq["clade"] == "Raw"
    assert "clade" not in rows["IMG"]


def test_empty_context_fields_are_absent_and_clade_falls_back():
    mus = _by_title(c.build_sample_types(_types(), _context(), {10: "Source"}, set()))["MUS"]
    assert mus["has_context"] is True
    assert mus["tags"] == ["mouse"]
    assert "curated_parents" not in mus and "curated_children" not in mus
    assert mus["clade"] == "Source"


def test_context_matches_the_title_byte_exact():
    context = {"tis": _context()["TIS"], "TIS ": _context()["TIS"]}
    tis = _by_title(c.build_sample_types(_types(), context, {}, set()))["TIS"]
    assert tis["has_context"] is False and "name" not in tis


def test_deprecated_is_membership_in_the_deprecated_titles():
    rows = _by_title(c.build_sample_types(_types(), {}, {}, {"IMG"}))
    assert rows["IMG"]["deprecated"] is True and rows["TIS"]["deprecated"] is False


def test_empty_seek_fields_are_absent():
    types = [{"id": 5, "title": "X", "uuid": None, "description": ""}]
    (row,) = c.build_sample_types(types, {}, {}, set())
    assert "uuid" not in row and "seek_description" not in row
    _assert_neo4j_safe(row)


def test_a_sample_type_without_a_title_raises():
    with pytest.raises(ValueError, match="sample type 5"):
        c.build_sample_types([{"id": 5, "title": "", "uuid": "u", "description": "d"}], {}, {}, set())


def test_label_collision_raises_naming_both_titles():
    types = [{"id": 1, "title": "D.SEQ", "uuid": "a", "description": ""},
             {"id": 2, "title": "D_SEQ", "uuid": "b", "description": ""}]
    with pytest.raises(ValueError) as err:
        c.assert_labels_unique(types)
    assert "D.SEQ" in str(err.value) and "D_SEQ" in str(err.value) and "T_D_SEQ" in str(err.value)
    with pytest.raises(ValueError):
        c.build_sample_types(types, {}, {}, set())


def test_distinct_labels_pass():
    c.assert_labels_unique(c.build_sample_types(_types(), {}, {}, set()))


# --- attributes ----------------------------------------------------------------------------------

def test_build_attributes_full_property_map():
    attrs = [_attr(101, 26, "CellCount", 3, pos=4, required=1, description="How many cells."),
             _attr(102, 26, "UID", is_title=1, required=1)]
    rows = _by_title(c.build_attributes(attrs, _attr_types(), {"CellCount": "Cells counted."}, TYPE_TITLES))
    assert rows["CellCount"] == {
        "key": "26:CellCount", "id": 101, "sample_type_id": 26, "sample_type": "TIS", "title": "CellCount",
        "pos": 4, "required": True, "is_title": False, "base_type": "Float", "value_type": "float",
        "declared": True, "seek_description": "How many cells.", "meaning": "Cells counted.", "role": "data",
        "needs_backticks": False,
    }
    uid = rows["UID"]
    assert uid["is_title"] is True and uid["role"] == "identifier" and uid["value_type"] == "string"
    assert "meaning" not in uid and "seek_description" not in uid and "unit_key" not in uid
    for row in rows.values():
        _assert_neo4j_safe(row)


@pytest.mark.parametrize("attr_type, value_type", [(3, "float"), (4, "integer"), (2, "date"), (7, "string")])
def test_value_type_follows_the_seek_base_type(attr_type, value_type):
    (row,) = c.build_attributes([_attr(1, 26, "X", attr_type)], _attr_types(), {}, TYPE_TITLES)
    assert row["value_type"] == value_type


def test_an_unknown_attribute_type_is_a_string_with_no_base_type():
    (row,) = c.build_attributes([_attr(1, 26, "X", attr_type=None)], _attr_types(), {}, TYPE_TITLES)
    assert row["value_type"] == "string" and "base_type" not in row


def test_meaning_never_matches_case_insensitively():
    attrs = [_attr(1, 26, "Organ"), _attr(2, 26, "organ")]
    rows = _by_title(c.build_attributes(attrs, _attr_types(), {"organ": "lowercase meaning"}, TYPE_TITLES))
    assert "meaning" not in rows["Organ"]
    assert rows["organ"]["meaning"] == "lowercase meaning"


def test_meaning_never_matches_a_trimmed_title():
    (row,) = c.build_attributes([_attr(1, 26, "Manufacturer ")], _attr_types(),
                                {"Manufacturer": "who made it"}, TYPE_TITLES)
    assert "meaning" not in row


def test_trailing_space_title_keeps_its_space_in_key_and_title():
    (row,) = c.build_attributes([_attr(1, 26, "Manufacturer ")], _attr_types(), {}, TYPE_TITLES)
    assert row["title"] == "Manufacturer "
    assert row["key"] == "26:Manufacturer "
    assert row["needs_backticks"] is True


@pytest.mark.parametrize("title, needs", [
    ("Organ", False), ("Cell_Count2", False), ("Catalog#", True), ("DNA Type", True), ("Bead-Lot", True),
    ("2ndDose", True), ("A.B", True),
])
def test_needs_backticks(title, needs):
    (row,) = c.build_attributes([_attr(1, 26, title)], _attr_types(), {}, TYPE_TITLES)
    assert row["needs_backticks"] is needs


def test_unit_key_set_on_units_only_when_the_measured_attribute_is_on_the_same_type():
    attrs = [_attr(1, 26, "CellCount", 3), _attr(2, 26, "CellCountUnits"),
             _attr(3, 10, "CellCountUnits"),              # MUS has no CellCount
             _attr(4, 33, "CellCount"),                   # CellCount on another type does not count
             _attr(5, 26, "Volume"), _attr(6, 26, "Volume_Units"),
             _attr(7, 26, "Dose"), _attr(8, 26, "Dose_Unit"),
             _attr(9, 26, "Units")]
    rows = c.build_attributes(attrs, _attr_types(), {}, TYPE_TITLES)
    by_id = {r["id"]: r for r in rows}
    assert by_id[2]["unit_key"] == "26:CellCount"
    assert "unit_key" not in by_id[3]
    assert by_id[6]["unit_key"] == "26:Volume"
    assert by_id[8]["unit_key"] == "26:Dose"
    assert "unit_key" not in by_id[9]
    for aid in (1, 4, 5, 7):
        assert "unit_key" not in by_id[aid]


def test_unit_key_tries_each_suffix_until_one_names_an_attribute():
    attrs = [_attr(1, 26, "Temp_"), _attr(2, 26, "Temp_Units")]
    by_id = {r["id"]: r for r in c.build_attributes(attrs, _attr_types(), {}, TYPE_TITLES)}
    assert by_id[2]["unit_key"] == "26:Temp_"


def test_duplicate_attribute_key_raises():
    attrs = [_attr(1, 26, "Organ"), _attr(2, 26, "Organ")]
    with pytest.raises(ValueError, match="26:Organ"):
        c.build_attributes(attrs, _attr_types(), {}, TYPE_TITLES)


def test_attribute_on_an_unknown_sample_type_raises():
    with pytest.raises(ValueError, match="attribute 1"):
        c.build_attributes([_attr(1, 99, "Organ")], _attr_types(), {}, TYPE_TITLES)


def test_attribute_without_a_title_raises():
    with pytest.raises(ValueError, match="attribute 1"):
        c.build_attributes([_attr(1, 26, "")], _attr_types(), {}, TYPE_TITLES)


def test_undeclared_attribute():
    row = c.undeclared_attribute(33, "D.SEQ", "Region")
    assert row == {"key": "33:Region", "sample_type_id": 33, "sample_type": "D.SEQ", "title": "Region",
                   "value_type": "string", "declared": False, "role": "data", "needs_backticks": False}
    assert "id" not in row
    assert c.undeclared_attribute(26, "TIS", "DNA Type")["needs_backticks"] is True
    _assert_neo4j_safe(row)


# --- catalog_hash --------------------------------------------------------------------------------

def _catalog():
    types = c.build_sample_types(_types(), _context(), {}, set())
    attrs = c.build_attributes([_attr(1, 26, "CellCount", 3), _attr(2, 26, "Organ"), _attr(3, 33, "UID")],
                               _attr_types(), {}, TYPE_TITLES)
    attrs.append(c.undeclared_attribute(33, "D.SEQ", "Region"))
    return types, attrs


def test_catalog_hash_is_a_sha256_hex_digest():
    digest = c.catalog_hash(*_catalog())
    assert len(digest) == 64 and int(digest, 16) >= 0


def test_catalog_hash_is_stable_under_input_order():
    types, attrs = _catalog()
    assert c.catalog_hash(types, attrs) == c.catalog_hash(list(reversed(types)), list(reversed(attrs)))


def test_catalog_hash_changes_when_one_value_type_changes():
    types, attrs = _catalog()
    before = c.catalog_hash(types, attrs)
    changed = [dict(a) for a in attrs]
    changed[0]["value_type"] = "string"
    assert c.catalog_hash(types, changed) != before


def test_catalog_hash_changes_when_declared_or_a_label_changes():
    types, attrs = _catalog()
    before = c.catalog_hash(types, attrs)
    flipped = [dict(a, declared=not a["declared"]) if a["title"] == "Region" else a for a in attrs]
    assert c.catalog_hash(types, flipped) != before
    relabeled = [dict(t, label="T_OTHER") if t["title"] == "TIS" else t for t in types]
    assert c.catalog_hash(relabeled, attrs) != before


def test_catalog_hash_ignores_descriptive_properties():
    types, attrs = _catalog()
    before = c.catalog_hash(types, attrs)
    reworded = [dict(a, meaning="new words") for a in attrs]
    assert c.catalog_hash(types, reworded) == before

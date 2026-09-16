"""The DERIVED_FROM label rule (`graph_sync/labels.py`; sync design 7.3, R5, R14, R15).

The first class is the parity proof: on one small MySQL world, where the upload sheet says nothing MySQL does not,
`labels.edge_labels` must equal what batch upload's `build_derived_from_payloads_from_db` produces for the same edges.
It calls that function, fed by a fake connection that answers its SQL from the same world.
"""
import json

import pytest
from django.test import override_settings

from nextseek_api.batch_upload import helpers
from nextseek_api.batch_upload.models import InputRowModel, RowOutcome
from nextseek_api.batch_upload.neo4j_sync import build_derived_from_payloads_from_db
from nextseek_api.graph_sync import labels

# The host settings the protocol rule reads to tell a local /sops/<id> URL from a foreign one (as
# test_neo4j_sync.py's TestDerivedFromProtocolResolution pins them).
_LOCAL = dict(SEEK_PUBLIC_URL="http://localhost:3000", SEEK_URL="http://seek:3000", ALLOWED_HOSTS=["127.0.0.1"])


# --- one MySQL world, seen by both rules --------------------------------------------------------------------------

SOPS = {
    5: "SOP Five",
    7: "P.FOR-200623-V1_x.docx",
    8: "Dup SOP",
    9: "dup sop",                    # ambiguous with 8 under MySQL's case-insensitive collation
    11: "Mixed Case SOP",
    12: "P.ISO-210101-V2_y.docx",
}
ASSAYS = {                           # SEEK `assays`: id to title
    10: "Seek Ten", 11: "Seek Eleven", 12: "Seek Twelve", 20: "Unmapped Twenty", 30: "Seek Thirty",
    40: "Seek Forty", 50: "Seek Fifty", 77: "Seek Seventy-Seven", 100: "Seek Hundred",
}
INTERNAL = {5: "Internal Five", 50: "Internal Alpha", 60: "IA 60", 70: None, 200: "Internal Two Hundred",
            300: "IA 300"}
JUNCTION = [                         # dmac.assays_internal_assays: (SEEK assay id, internal assay id)
    (10, 50), (50, 200), (100, 5), (30, 300), (30, 60), (40, 70), (11, 5), (12, 5),
]
# id: (uuid, json_metadata, SEEK assay ids in assay_assets)
SAMPLES = {
    201: ("P-1", {}, {10}),
    202: ("P-2", {}, {50, 100}),
    203: ("P-3", {}, {20}),
    204: ("P-4", {}, {10, 20}),
    205: ("P-5", {}, {77}),
    206: ("P-6", {}, {30}),
    207: ("P-7", {}, {40}),
    208: ("P-8", {}, {999}),         # 999 has an assay_assets row and no `assays` row
    209: ("P-9", {}, set()),
    210: ("P-10", {}, {11, 12}),
    101: ("C-1", {"Protocol": "/sops/5"}, {10}),
    102: ("C-2", {"Protocol": "http://127.0.0.1:8000/seek/sop/uid=P.ISO-210101-V2_y.docx/"}, {50, 100}),
    103: ("C-3", {"Protocol": "P.FOR-200623-V1_x.docx"}, {20}),
    104: ("C-4", {"Protocol": "mixed case sop"}, {10, 20}),
    105: ("C-5", {"Protocol": "Dup SOP"}, {10}),
    106: ("C-6", {"Protocol": "https://fairdomhub.org/sops/795"}, {30}),
    107: ("C-7", {"Protocol": "/sops/9999"}, {40}),
    108: ("C-8", {}, {999}),
    109: ("C-9", {"protocol": "/sops/5"}, {10}),
    110: ("C-10", {"Protocol": "No Such SOP"}, {10, 50, 100}),
    111: ("C-11", {"Protocol": "SOP Five"}, {100}),
    112: ("C-12", {"Protocol": "/seek/sop/uid=Mixed Case SOP"}, {11, 12}),
}
PARENTS = {                          # child uuid: parent uuids (C-1 is also a parent: a chain)
    "C-1": {"P-1"}, "C-2": {"P-2"}, "C-3": {"P-3"}, "C-4": {"P-4"}, "C-5": {"P-5"}, "C-6": {"P-6"},
    "C-7": {"P-7"}, "C-8": {"P-8"}, "C-9": {"P-1", "P-9"}, "C-10": {"C-1", "P-2"}, "C-11": {"P-2"},
    "C-12": {"P-10"},
}
ID_BY_UUID = {uuid: sid for sid, (uuid, _meta, _assays) in SAMPLES.items()}


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return list(self._rows)


class _FakeMySQL:
    """Answers batch upload's label SQL from the world above, as MySQL would."""

    def execute(self, sql, params):
        text, wanted = str(sql), list(params.values())
        if "FROM samples WHERE uuid IN" in text:
            return _Result([(u, ID_BY_UUID[u]) for u in wanted if u in ID_BY_UUID])
        if "SELECT id, json_metadata FROM samples" in text:
            return _Result([(i, json.dumps(SAMPLES[i][1])) for i in wanted if i in SAMPLES])
        if "FROM sops WHERE title IN" in text:
            # MySQL's default collation compares case-insensitively.
            keys = {str(t).strip().casefold() for t in wanted}
            return _Result([(i, t) for i, t in SOPS.items() if t.strip().casefold() in keys])
        if "FROM sops WHERE id IN" in text:
            return _Result([(i, SOPS[i]) for i in wanted if i in SOPS])
        if "assays_internal_assays" in text:
            return _Result([(ia, a, INTERNAL[ia]) for a, ia in JUNCTION if a in wanted])
        if "FROM assays WHERE id IN" in text:
            return _Result([(a, ASSAYS[a]) for a in wanted if a in ASSAYS])
        raise AssertionError(f"unexpected SQL: {text}")


def _resolved_assay_map():
    """What sources.resolved_assay_map() returns for this world: SEEK assay id to (internal id or None, title).

    The smallest internal id on 1:N; an assay with no mapping keeps its own title and None for the internal id.
    """
    out = {}
    for assay_id, title in ASSAYS.items():
        internal = sorted(ia for a, ia in JUNCTION if a == assay_id)
        out[assay_id] = (internal[0], INTERNAL[internal[0]]) if internal else (None, title)
    return out


def _models(overrides=None):
    overrides = overrides or {}
    return [InputRowModel(UID=uuid, SampleType="Blood", json_metadata=json.dumps(SAMPLES[ID_BY_UUID[uuid]][1]),
                          **overrides.get(uuid, {}))
            for uuid in PARENTS]


def _batch_upload_labels(models=None):
    outcomes = {uuid: RowOutcome(status="success", sample_id=ID_BY_UUID[uuid]) for uuid in PARENTS}
    assays_by_uid = {uuid: set(assays) for uuid, _meta, assays in SAMPLES.values()}
    with override_settings(**_LOCAL):
        rows = build_derived_from_payloads_from_db(
            {c: set(p) for c, p in PARENTS.items()}, _FakeMySQL(), assays_by_uid, outcomes,
            models if models is not None else _models())
    return {(r.child_id, r.parent_id): {k: getattr(r, k) for k in labels.LABEL_KEYS} for r in rows}


def _graph_sync_labels():
    assay_map, index = _resolved_assay_map(), labels.sop_title_index(SOPS)
    out = {}
    with override_settings(**_LOCAL):
        for child_uuid, parent_uuids in PARENTS.items():
            child = ID_BY_UUID[child_uuid]
            protocol = labels.resolve_protocol(labels.protocol_value_of(SAMPLES[child][1]), SOPS, index)
            for parent_uuid in parent_uuids:
                parent = ID_BY_UUID[parent_uuid]
                out[(child, parent)] = labels.edge_labels(SAMPLES[child][2], SAMPLES[parent][2], assay_map, protocol)
    return out


def _labels(assay_id=None, internal_id=None, title=None, ids=(), titles=(), protocol_id=None, protocol_title=None):
    return {"assay_id": assay_id, "internal_assay_id": internal_id, "internal_assay_title": title,
            "internal_assay_ids": list(ids), "internal_assay_titles": list(titles),
            "protocol_id": protocol_id, "protocol_title": protocol_title}


class TestParityWithBatchUpload:
    def test_every_edge_equals_batch_upload(self):
        ours, theirs = _graph_sync_labels(), _batch_upload_labels()
        assert set(ours) == set(theirs)
        assert len(ours) == 14
        for pair in sorted(theirs):
            assert ours[pair] == theirs[pair], pair

    def test_sheet_values_equal_to_mysql_change_nothing(self):
        """A sheet sop_id and assay titles that equal MySQL's give batch upload the labels MySQL alone gives."""
        sheet = {"C-11": {"sop_id": 5, "assay_ids": [100], "assay_titles": ["Internal Five"]},
                 "C-3": {"sop_id": 7}}
        assert _batch_upload_labels(_models(sheet)) == _graph_sync_labels()

    @pytest.mark.parametrize("pair, expected", [
        # one shared assay: the plural lists are [internal id], [title]
        ((101, 201), _labels(10, 50, "Internal Alpha", [50], ["Internal Alpha"], 5, "SOP Five")),
        # two shared: internal 5 (from SEEK 100) beats internal 200 (from the smaller SEEK 50); uid= URL format
        ((102, 202), _labels(100, 5, "Internal Five", [5, 200], ["Internal Five", "Internal Two Hundred"],
                             12, "P.ISO-210101-V2_y.docx")),
        # the SEEK-id fallback; a bare title
        ((103, 203), _labels(20, 20, "Unmapped Twenty", [20], ["Unmapped Twenty"], 7, "P.FOR-200623-V1_x.docx")),
        # fallback and mapped together: the fallback's id is smaller, so it wins; a title in another case
        ((104, 204), _labels(20, 20, "Unmapped Twenty", [20, 50], ["Unmapped Twenty", "Internal Alpha"],
                             11, "Mixed Case SOP")),
        # no shared assay clears to nulls and empty lists; an ambiguous title resolves to null
        ((105, 205), _labels()),
        # 1:N keeps the smallest internal id; a foreign SOP URL records no protocol
        ((106, 206), _labels(30, 60, "IA 60", [60], ["IA 60"])),
        # an untitled internal assay: None on the singular field, "" in the list; a dangling id keeps the id
        ((107, 207), _labels(40, 70, None, [70], [""], 9999, None)),
        # a shared assay with no `assays` row contributes nothing
        ((108, 208), _labels()),
        # the lower-case key
        ((109, 201), _labels(10, 50, "Internal Alpha", [50], ["Internal Alpha"], 5, "SOP Five")),
        ((109, 209), _labels(protocol_id=5, protocol_title="SOP Five")),
        # a child of a child; an unmatched title
        ((110, 101), _labels(10, 50, "Internal Alpha", [50], ["Internal Alpha"])),
        ((110, 202), _labels(100, 5, "Internal Five", [5, 200], ["Internal Five", "Internal Two Hundred"])),
        # two SEEK assays on one internal assay: one list entry, the smaller SEEK id on the singular field
        ((112, 210), _labels(11, 5, "Internal Five", [5], ["Internal Five"], 11, "Mixed Case SOP")),
    ])
    def test_the_world_covers_each_case(self, pair, expected):
        assert _graph_sync_labels()[pair] == expected


class TestEdgeLabels:
    MAP = {10: (50, "Internal Alpha"), 20: (None, "Unmapped Twenty"), 21: (None, None), 40: (70, None),
           11: (5, "Internal Five"), 12: (5, "Internal Five")}

    def test_all_seven_keys_every_time(self):
        for child, parent in [({10}, {10}), ({10}, {20}), (set(), set()), (None, None)]:
            assert tuple(labels.edge_labels(child, parent, self.MAP)) == labels.LABEL_KEYS

    def test_the_five_assay_keys_and_the_protocol_pair(self):
        assert labels.ASSAY_KEYS == ("assay_id", "internal_assay_id", "internal_assay_title",
                                     "internal_assay_ids", "internal_assay_titles")
        assert labels.PROTOCOL_KEYS == ("protocol_id", "protocol_title")
        assert labels.LABEL_KEYS == labels.ASSAY_KEYS + labels.PROTOCOL_KEYS

    def test_plural_lists_beside_the_singular_fields(self):
        got = labels.edge_labels({10}, {10, 99}, self.MAP)
        assert got["internal_assay_ids"] == [got["internal_assay_id"]] == [50]
        assert got["internal_assay_titles"] == [got["internal_assay_title"]] == ["Internal Alpha"]

    def test_empty_shared_set_clears(self):
        assert labels.edge_labels({10}, {20}, self.MAP, (5, "SOP Five")) == _labels(
            protocol_id=5, protocol_title="SOP Five")

    def test_fallback_uses_the_seek_id_and_never_a_null_title(self):
        assert labels.edge_labels({21}, {21}, self.MAP) == _labels(21, 21, "", [21], [""])

    def test_an_untitled_internal_assay_stays_none_on_the_singular_field(self):
        assert labels.edge_labels({40}, {40}, self.MAP) == _labels(40, 70, None, [70], [""])

    def test_a_tie_on_the_internal_id_takes_the_smaller_seek_id(self):
        for order in ([12, 11], [11, 12]):
            got = labels.edge_labels(order, list(reversed(order)), self.MAP)
            assert (got["assay_id"], got["internal_assay_ids"]) == (11, [5])

    def test_lists_are_new_objects(self):
        a, b = labels.edge_labels({10}, {10}, self.MAP), labels.edge_labels({10}, {10}, self.MAP)
        assert a["internal_assay_ids"] is not b["internal_assay_ids"]


class TestResolveProtocol:
    INDEX = labels.sop_title_index(SOPS)

    @pytest.mark.parametrize("value, expected", [
        ("/sops/5", (5, "SOP Five")),
        ("http://127.0.0.1:8000/seek/sop/uid=P.FOR-200623-V1_x.docx/", (7, "P.FOR-200623-V1_x.docx")),
        ("P.FOR-200623-V1_x.docx", (7, "P.FOR-200623-V1_x.docx")),
        ("  mixed CASE sop ", (11, "Mixed Case SOP")),
        ("Dup SOP", (None, None)),
        ("No Such SOP", (None, None)),
        ("https://fairdomhub.org/sops/795", (None, None)),
        ("/sops/9999", (9999, None)),
        ("", (None, None)),
        (None, (None, None)),
    ])
    def test_the_house_rule(self, value, expected):
        with override_settings(**_LOCAL):
            assert labels.resolve_protocol(value, SOPS, self.INDEX) == expected

    def test_the_index_is_optional(self):
        assert labels.resolve_protocol("P.FOR-200623-V1_x.docx", SOPS) == (7, "P.FOR-200623-V1_x.docx")

    def test_the_index_keeps_every_id_of_a_title(self):
        assert sorted(self.INDEX["dup sop"]) == [8, 9]
        assert list(self.INDEX["sop five"]) == [5]

    def test_the_parser_is_batch_uploads_not_a_copy(self):
        assert labels.parse_protocol_value is helpers.parse_protocol_value

    @pytest.mark.parametrize("meta, expected", [
        ({"Protocol": "X"}, "X"),
        ({"protocol": "Y"}, "Y"),
        ({"Protocol": "", "protocol": "Y"}, "Y"),
        ({}, ""),
        ('{"Protocol": "X"}', "X"),
        (b'{"Protocol": "X"}', "X"),
        ("", ""),
        (None, ""),
        ("not json", ""),
        ("[1, 2]", ""),
    ])
    def test_protocol_value_of(self, meta, expected):
        assert labels.protocol_value_of(meta) == expected


class _Absent:
    """Marks a property the stored edge does not carry."""


_ABSENT = _Absent()


class TestClassify:
    COMPUTED =_labels(10, 50, "Internal Alpha", [50], ["Internal Alpha"], 5, "SOP Five")

    @staticmethod
    def _stored(**changes):
        stored = {k: v for k, v in TestClassify.COMPUTED.items()}
        for key, value in changes.items():
            if value is _ABSENT:
                stored.pop(key)
            else:
                stored[key] = value
        return stored

    def test_the_class_names(self):
        assert labels.CLASSES == ("new", "equal", "plural_missing", "changed", "cleared")

    def test_equal(self):
        assert labels.classify(self._stored(), self.COMPUTED) == "equal"

    def test_an_absent_property_reads_as_null(self):
        """Neo4j stores no nulls: an edge written with null labels reads back with the keys absent."""
        computed = _labels()
        stored = {"child_id": 1, "parent_id": 2, "internal_assay_ids": [], "internal_assay_titles": []}
        assert labels.classify(stored, computed) == "equal"

    @pytest.mark.parametrize("stored", [None, {}, {"child_id": 1, "parent_id": 2},
                                        {"protocol_id": 3, "protocol_title": "Other"},
                                        {"internal_assay_ids": [99], "internal_assay_titles": ["x"]}])
    def test_new_when_no_singular_assay_field_is_stored(self, stored):
        assert labels.classify(stored, self.COMPUTED) == "new"

    def test_new_when_an_unlabelled_edge_lacks_the_empty_lists(self):
        assert labels.classify({"child_id": 1, "parent_id": 2}, _labels()) == "new"

    @pytest.mark.parametrize("absent", [("internal_assay_ids",), ("internal_assay_titles",),
                                        ("internal_assay_ids", "internal_assay_titles")])
    def test_a_missing_plural_list_on_matching_singular_fields_is_plural_missing(self, absent):
        stored = self._stored(**{k: _ABSENT for k in absent})
        assert labels.classify(stored, self.COMPUTED) == "plural_missing"

    def test_plural_missing_with_no_protocol_on_either_side(self):
        computed = _labels(10, 50, "Internal Alpha", [50], ["Internal Alpha"])
        stored = {"assay_id": 10, "internal_assay_id": 50, "internal_assay_title": "Internal Alpha"}
        assert labels.classify(stored, computed) == "plural_missing"

    @pytest.mark.parametrize("changes", [
        {"internal_assay_title": "Old Title"},                  # a rename left stale
        {"internal_assay_id": 49},
        {"assay_id": 11},
        {"internal_assay_ids": [10]},                            # a list in the SEEK id space
        {"internal_assay_ids": []},                              # a present but empty list is not a missing one
        {"internal_assay_ids": [50, 60], "internal_assay_titles": ["Internal Alpha", "IA 60"]},
        {"protocol_id": 6, "protocol_title": "SOP Six"},
        {"internal_assay_ids": _ABSENT, "protocol_title": "Another"},
    ])
    def test_changed(self, changes):
        assert labels.classify(self._stored(**changes), self.COMPUTED) == "changed"

    def test_changed_when_a_list_holds_the_same_entries_in_another_order(self):
        computed = _labels(10, 5, "A", [5, 50], ["A", "B"])
        stored = dict(computed, internal_assay_ids=[50, 5], internal_assay_titles=["B", "A"])
        assert labels.classify(stored, computed) == "changed"

    def test_changed_when_only_some_singular_fields_are_stored(self):
        stored = {"internal_assay_title": "Internal Alpha"}
        assert labels.classify(stored, self.COMPUTED) == "changed"

    def test_cleared_when_the_rule_finds_no_shared_assay(self):
        computed = _labels(protocol_id=5, protocol_title="SOP Five")
        assert labels.classify(self._stored(), computed) == "cleared"

    def test_cleared_when_the_lists_were_never_written(self):
        computed = _labels(protocol_id=5, protocol_title="SOP Five")
        stored = self._stored(internal_assay_ids=_ABSENT, internal_assay_titles=_ABSENT)
        assert labels.classify(stored, computed) == "cleared"

    def test_cleared_when_the_rule_resolves_no_protocol(self):
        """A sheet-supplied protocol MySQL never stored."""
        computed = _labels(10, 50, "Internal Alpha", [50], ["Internal Alpha"])
        assert labels.classify(self._stored(), computed) == "cleared"

    def test_a_missing_list_beside_a_cleared_protocol_is_cleared_not_plural_missing(self):
        computed = _labels(10, 50, "Internal Alpha", [50], ["Internal Alpha"])
        stored = self._stored(internal_assay_ids=_ABSENT, internal_assay_titles=_ABSENT)
        assert labels.classify(stored, computed) == "cleared"

    def test_differences_name_each_property(self):
        stored = self._stored(internal_assay_title="Old", internal_assay_ids=_ABSENT, protocol_id=6)
        assert labels.differences(stored, self.COMPUTED) == ["internal_assay_title", "internal_assay_ids",
                                                             "protocol_id"]
        assert labels.differences(self._stored(), self.COMPUTED) == []
        assert labels.differences(None, _labels()) == ["internal_assay_ids", "internal_assay_titles"]

    def test_only_the_label_keys_are_compared(self):
        stored = self._stored(child_id=1, parent_id=2, assay_title="legacy")
        assert labels.classify(stored, self.COMPUTED) == "equal"


class TestLabelMapsHash:
    ASSAY_MAP = {10: (50, "Internal Alpha"), 20: (None, "Unmapped Twenty"), 40: (70, None)}
    SOPS = {5: "SOP Five", 7: "P.FOR-200623-V1_x.docx"}

    def test_sha256_hex(self):
        digest = labels.label_maps_hash(self.ASSAY_MAP, self.SOPS)
        assert len(digest) == 64 and int(digest, 16) >= 0

    def test_stable_under_ordering(self):
        reordered_assays = dict(reversed(list(self.ASSAY_MAP.items())))
        reordered_sops = dict(reversed(list(self.SOPS.items())))
        assert labels.label_maps_hash(reordered_assays, reordered_sops) == labels.label_maps_hash(
            self.ASSAY_MAP, self.SOPS)

    @pytest.mark.parametrize("assay_map, sops", [
        ({**ASSAY_MAP, 10: (50, "Internal Alpha ")}, SOPS),      # a trailing space
        ({**ASSAY_MAP, 10: (51, "Internal Alpha")}, SOPS),
        ({**ASSAY_MAP, 20: (60, "Unmapped Twenty")}, SOPS),      # a new mapping
        ({**ASSAY_MAP, 40: (70, "")}, SOPS),                     # "" is not None
        ({**ASSAY_MAP, 41: (None, "x")}, SOPS),
        ({k: v for k, v in ASSAY_MAP.items() if k != 40}, SOPS),
        (ASSAY_MAP, {**SOPS, 5: "SOP 5"}),
        (ASSAY_MAP, {**SOPS, 8: "Dup SOP"}),
        (ASSAY_MAP, {7: "P.FOR-200623-V1_x.docx"}),
        (ASSAY_MAP, {**SOPS, 5: None}),
    ])
    def test_changes_when_any_input_changes(self, assay_map, sops):
        assert labels.label_maps_hash(assay_map, sops) != labels.label_maps_hash(self.ASSAY_MAP, self.SOPS)

    def test_the_two_maps_do_not_run_together(self):
        assert labels.label_maps_hash({}, {5: "x"}) != labels.label_maps_hash({5: (None, "x")}, {})

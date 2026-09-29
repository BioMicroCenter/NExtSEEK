"""The label verification's pure half (`graph_sync/label_check.py`; plan task V1, sync design 7.3 and 17).

Small synthetic label sets only: the canonical form and its digest, the id remap and the title key, the compact index
of declared pairs, check (a) on the three singular fields (the plural lists reported apart and never part of its pass
condition) and check (b), every edge sorted by `labels.classify` and counted per property.
"""
import pytest

from nextseek_api.graph_sync import label_check as lc
from nextseek_api.graph_sync import labels

# SEEK assay id to (internal assay id or None, title): `sources.resolved_assay_map()`'s shape.
MAP = {10: (56, "Patient Visit"), 11: (None, "Seek Eleven"), 12: (57, "Biopsy"), 13: (5, "Internal Five")}
SOP = (7, "SOP Seven")

# Declared pairs and the SEEK assays of each end, as `sources` would read them.
PAIRS = {
    (1, 2): ([10], [10], None),          # one shared assay, mapped
    (3, 4): ([10], [12], None),          # nothing shared: nulls and empty lists
    (5, 6): ([12], [12], SOP),           # a protocol
    (7, 8): ([11], [11], None),          # the SEEK-assay fallback
    (9, 10): ([10, 13], [10, 13], None),  # two shared assays: the smallest internal id wins
}


def rule(pair):
    child, parent, protocol = PAIRS[pair]
    return labels.edge_labels(child, parent, MAP, protocol)


@pytest.fixture
def index():
    built = lc.LabelIndex()
    for pair in PAIRS:
        built.add(*pair, rule(pair))
    built.freeze()
    return built


def stored(**props):
    """A stored edge as the dump gives it: Neo4j keeps no null, so a null property is simply absent."""
    base = {"child_id": 1, "parent_id": 2}
    base.update({key: value for key, value in props.items() if value is not None})
    return base


def full(pair):
    """The stored form of an edge labelled exactly as the rule says."""
    return stored(**rule(pair))


# --- canonical form and digest -----------------------------------------------------------------------------------

class TestCanonical:
    def test_absent_and_null_read_the_same(self):
        assert lc.canonical({}) == lc.canonical({key: None for key in labels.LABEL_KEYS}) == (None,) * 7

    def test_only_the_label_keys_count(self):
        assert lc.canonical(stored(assay_id=1, assay_title="legacy")) == lc.canonical({"assay_id": 1})

    def test_lists_keep_their_order(self):
        assert lc.canonical({"internal_assay_ids": [1, 2]}) != lc.canonical({"internal_assay_ids": [2, 1]})

    def test_an_absent_list_differs_from_an_empty_one(self):
        assert lc.canonical({}) != lc.canonical({"internal_assay_ids": []})

    def test_an_integral_float_reads_as_its_int(self):
        assert lc.canonical({"assay_id": 494.0}) == lc.canonical({"assay_id": 494})
        assert lc.digest(lc.canonical({"assay_id": 494.0})) == lc.digest(lc.canonical({"assay_id": 494}))

    def test_as_dict_gives_back_the_rule_shape(self):
        assert lc.as_dict(lc.canonical(rule((1, 2)))) == rule((1, 2))

    def test_digest_is_stable_and_separates_a_title(self):
        same = lc.digest(lc.canonical(rule((1, 2))))
        assert same == lc.digest(lc.canonical(dict(rule((1, 2)))))
        other = dict(rule((1, 2)), internal_assay_title="Patient visit")
        assert lc.digest(lc.canonical(other)) != same
        assert -(2 ** 63) <= same < 2 ** 63

    def test_stored_labels_drops_every_other_property(self):
        assert lc.stored_labels(stored(assay_id=3, assay_title="old", internal_assay_ids=[4])) == {
            "assay_id": 3, "internal_assay_ids": [4]}


class TestPairs:
    def test_round_trip_and_order(self):
        assert lc.decode_pair(lc.encode_pair(389935, 1308453)) == (389935, 1308453)
        assert lc.encode_pair(1, 2) < lc.encode_pair(1, 3) < lc.encode_pair(2, 0)

    @pytest.mark.parametrize("child, parent", [(-1, 2), (1, -2), (2 ** 31, 1), (1, 2 ** 31)])
    def test_refuses_an_id_outside_the_encoding(self, child, parent):
        with pytest.raises(ValueError):
            lc.encode_pair(child, parent)


# --- remap and title key -----------------------------------------------------------------------------------------

DEV = stored(assay_id=7, internal_assay_id=33, internal_assay_title="Patient Visit",
             internal_assay_ids=[], internal_assay_titles=[])


class TestRemap:
    def test_each_id_goes_through_its_own_kind(self):
        out = lc.remap_ids(DEV, assay_ids={7: 10}, internal_ids={33: 56})
        assert (out["assay_id"], out["internal_assay_id"], out["internal_assay_title"]) == (10, 56, "Patient Visit")
        assert out["internal_assay_ids"] == [] and DEV["assay_id"] == 7  # lists untouched, input not mutated

    def test_the_kinds_are_never_crossed(self):
        out = lc.remap_ids(DEV, assay_ids={33: 1}, internal_ids={7: 2})
        assert out["assay_id"] == lc.Unmapped("assay", 7)
        assert out["internal_assay_id"] == lc.Unmapped("internal_assay", 33)

    def test_an_id_with_no_remap_row_never_matches(self):
        missing = lc.remap_ids({"assay_id": 8}, assay_ids={7: 10}, internal_ids={})["assay_id"]
        assert missing != 8 and missing != 10 and missing is not None
        assert str(missing) == "unmapped assay 8"

    def test_a_null_stays_null(self):
        assert lc.remap_ids({}, assay_ids={}, internal_ids={}) == {}

    def test_title_key_takes_the_local_id_of_the_title(self):
        out = lc.key_internal_by_title(DEV, {"Patient Visit": 56})
        assert out["internal_assay_id"] == 56 and out["assay_id"] == 7

    def test_title_key_marks_an_unknown_title(self):
        out = lc.key_internal_by_title(DEV, {"Biopsy": 57})
        assert out["internal_assay_id"] == lc.Unmapped("internal_assay_title", "Patient Visit")

    def test_title_key_leaves_an_untitled_edge_alone(self):
        assert lc.key_internal_by_title({"internal_assay_id": 4}, {"x": 1}) == {"internal_assay_id": 4}


class TestComparisons:
    def test_singular_differences_ignore_the_plural_lists_and_the_protocol(self):
        assert lc.singular_differences(stored(assay_id=10, internal_assay_id=56,
                                              internal_assay_title="Patient Visit"), rule((1, 2))) == []

    def test_singular_differences_name_a_differing_title(self):
        other = stored(assay_id=10, internal_assay_id=56, internal_assay_title="Visit")
        assert lc.singular_differences(other, rule((1, 2))) == ["internal_assay_title"]

    def test_property_changes_name_each_kind(self):
        changes = lc.property_changes(stored(assay_id=11, internal_assay_title="Visit", internal_assay_ids=[9],
                                             protocol_title="gone"), rule((1, 2)))
        assert changes == {"assay_id": "changed", "internal_assay_id": "absent",
                           "internal_assay_title": "changed", "internal_assay_ids": "changed",
                           "internal_assay_titles": "absent", "protocol_title": "cleared"}

    def test_property_changes_tell_an_empty_list_from_a_value(self):
        assert lc.property_changes({}, rule((3, 4))) == {"internal_assay_ids": "absent_empty",
                                                         "internal_assay_titles": "absent_empty"}
        assert lc.property_changes(stored(internal_assay_ids=[56]), rule((3, 4)))["internal_assay_ids"] == "cleared"

    @pytest.mark.parametrize("ids, shape", [(None, "absent"), ([], "empty"), ([7], "seek_assay_id"),
                                            ([33], "internal_id"), ([33, 7], "other")])
    def test_plural_shape(self, ids, shape):
        assert lc.plural_shape(dict(DEV, internal_assay_ids=ids)) == shape


# --- the index of declared pairs ---------------------------------------------------------------------------------

class TestLabelIndex:
    def test_find_and_labels(self, index):
        assert len(index) == len(PAIRS)
        for pair in PAIRS:
            position = index.find(*pair)
            assert position >= 0 and index.pair(position) == pair and index.labels(position) == rule(pair)
        assert index.find(2, 1) == -1 and index.find(99, 100) == -1

    def test_label_maps_are_kept_once(self, index):
        assert index.distinct == len({lc.canonical(rule(pair)) for pair in PAIRS})

    def test_a_repeated_pair_is_kept_once_and_counted(self):
        built = lc.LabelIndex()
        for _ in range(3):
            built.add(1, 2, rule((1, 2)))
        built.add(0, 5, rule((3, 4)))
        built.freeze()
        assert len(built) == 2 and built.duplicates == 2 and built.find(0, 5) == 0

    def test_one_pair_with_two_label_maps_is_refused(self):
        built = lc.LabelIndex()
        built.add(1, 2, rule((1, 2)))
        built.add(1, 2, rule((3, 4)))
        with pytest.raises(ValueError):
            built.freeze()

    def test_find_needs_freeze(self):
        built = lc.LabelIndex()
        built.add(1, 2, rule((1, 2)))
        with pytest.raises(RuntimeError):
            built.find(1, 2)


# --- check (a): the singular fields ------------------------------------------------------------------------------

def singular_of(pair, **overrides):
    labels_ = rule(pair)
    out = {key: labels_[key] for key in labels.SINGULAR_ASSAY_KEYS}
    out.update(overrides)
    return stored(**out)


class TestSingularCheck:
    def test_every_edge_matching_passes(self, index):
        check = lc.SingularCheck(index, expected=len(PAIRS))
        for pair in PAIRS:
            check.edge(*pair, singular_of(pair))
        result = check.close()
        assert result["passed"] is True
        assert (result["total"], result["matched"], result["differing"]) == (5, 5, 0)

    def test_a_differing_title_fails(self, index):
        check = lc.SingularCheck(index, expected=len(PAIRS))
        for pair in PAIRS:
            check.edge(*pair, singular_of(pair, internal_assay_title="Visit") if pair == (1, 2) else singular_of(pair))
        result = check.close()
        assert result["passed"] is False and result["differing"] == 1
        assert result["differing_by_key"] == {"assay_id": 0, "internal_assay_id": 0, "internal_assay_title": 1}
        assert result["examples"][0]["kind"] == "differing" and result["examples"][0]["child"] == 1

    def test_a_remapped_id_that_matches(self, index):
        check = lc.SingularCheck(index, expected=1)
        dev = stored(assay_id=7, internal_assay_id=33, internal_assay_title="Patient Visit")
        check.edge(1, 2, lc.remap_ids(dev, assay_ids={7: 10}, internal_ids={33: 56}))
        assert check.close(in_scope=lambda c, p: (c, p) == (1, 2))["passed"] is True

    def test_a_title_keyed_match(self, index):
        check = lc.SingularCheck(index, expected=1)
        dev = lc.remap_ids(stored(assay_id=7, internal_assay_id=33, internal_assay_title="Patient Visit"),
                           assay_ids={7: 10}, internal_ids={})
        assert lc.singular_differences(dev, rule((1, 2))) == ["internal_assay_id"]
        check.edge(1, 2, lc.key_internal_by_title(dev, {"Patient Visit": 56}))
        assert check.close(in_scope=lambda c, p: (c, p) == (1, 2))["matched"] == 1

    def test_a_missing_edge_on_either_side_fails(self, index):
        check = lc.SingularCheck(index, expected=2)
        check.edge(1, 2, singular_of((1, 2)))
        check.edge(40, 41, singular_of((1, 2)))  # in the graph, not declared
        result = check.close(in_scope=lambda c, p: c < 5)  # (3, 4) declared, not in the graph; 5 and up out of scope
        assert (result["matched"], result["graph_only"], result["rule_only"], result["total"]) == (1, 1, 1, 3)
        assert result["passed"] is False
        assert {example["kind"] for example in result["examples"]} == {"graph_only", "rule_only"}

    def test_a_duplicate_edge_fails(self, index):
        check = lc.SingularCheck(index, expected=1)
        check.edge(1, 2, singular_of((1, 2)))
        check.edge(1, 2, singular_of((1, 2)))
        result = check.close(in_scope=lambda c, p: (c, p) == (1, 2))
        assert (result["matched"], result["duplicates"], result["passed"]) == (1, 1, False)

    def test_the_expected_total_is_part_of_the_pass(self, index):
        check = lc.SingularCheck(index, expected=6)
        for pair in PAIRS:
            check.edge(*pair, singular_of(pair))
        result = check.close()
        assert result["matched"] == result["total"] == 5 and result["passed"] is False

    def test_plural_lists_are_reported_apart_and_never_fail_it(self, index):
        check = lc.SingularCheck(index, expected=2)
        dev_empty = dict(singular_of((1, 2)), internal_assay_ids=[], internal_assay_titles=[])
        dev_seek = dict(singular_of((7, 8)), internal_assay_ids=[11], internal_assay_titles=["x"])
        check.edge(1, 2, dev_empty, plural=lc.plural_shape(dev_empty))
        check.edge(7, 8, dev_seek, plural="seek_assay_id")
        result = check.close(in_scope=lambda c, p: (c, p) in {(1, 2), (7, 8)})
        assert result["passed"] is True
        assert result["plural_shapes"] == {"empty": 1, "seek_assay_id": 1}

    def test_protocol_labels_are_counted_and_never_fail_it(self, index):
        check = lc.SingularCheck(index, expected=1)
        check.edge(5, 6, dict(singular_of((5, 6)), protocol_id=7))
        result = check.close(in_scope=lambda c, p: (c, p) == (5, 6))
        assert (result["stored_protocol"], result["computed_protocol"], result["passed"]) == (1, 1, True)


# --- check (b): every property, every class ----------------------------------------------------------------------

class TestClassCheck:
    def run(self, index, edges, in_scope=None, cap=10):
        check = lc.ClassCheck(index, example_cap=cap)
        for pair, props in edges:
            check.edge(*pair, props)
        return check.close(in_scope=in_scope)

    def test_each_class_as_labels_classify_says(self, index):
        cases = {
            (1, 2): (full((1, 2)), labels.EQUAL),
            (3, 4): (stored(assay_id=10, internal_assay_id=56, internal_assay_title="Patient Visit"), labels.CLEARED),
            (5, 6): (stored(**{k: rule((5, 6))[k] for k in labels.ASSAY_KEYS}), labels.CHANGED),  # protocol absent
            (7, 8): (singular_of((7, 8)), labels.PLURAL_MISSING),
            (9, 10): ({}, labels.NEW),
        }
        result = self.run(index, [(pair, props) for pair, (props, _cls) in cases.items()])
        for pair, (props, cls) in cases.items():
            assert labels.classify(props, rule(pair)) == cls
        assert result["classes"] == {cls: 1 for cls in labels.CLASSES}
        assert result["compared"] == 5 and result["rule_only"] == {"with_assay": 0, "without_assay": 0}
        assert result["by_stored"]["unlabelled"] == {labels.NEW: 1}
        assert result["per_property"][labels.CHANGED] == {"protocol_id": {"absent": 1}, "protocol_title": {"absent": 1}}
        assert result["per_property"][labels.CLEARED]["internal_assay_title"] == {"cleared": 1}
        assert result["per_property"][labels.PLURAL_MISSING] == {"internal_assay_ids": {"absent": 1},
                                                                "internal_assay_titles": {"absent": 1}}

    def test_new_is_split_by_what_the_rule_would_write(self, index):
        result = self.run(index, [((1, 2), stored()), ((3, 4), stored()), ((5, 6), stored(protocol_id=7))])
        assert result["classes"][labels.NEW] == 3  # (5, 6) has no singular field stored either: new too
        result = self.run(index, [((1, 2), stored()), ((3, 4), stored())])
        assert result["new"] == {"with_assay": 1, "protocol_only": 0, "empty": 1}

    def test_a_label_written_in_another_list_order_is_changed(self, index):
        props = full((9, 10))
        props["internal_assay_ids"] = list(reversed(props["internal_assay_ids"]))
        props["internal_assay_titles"] = list(reversed(props["internal_assay_titles"]))
        result = self.run(index, [((9, 10), props)])
        assert result["classes"][labels.CHANGED] == 1
        assert result["per_property"][labels.CHANGED] == {"internal_assay_ids": {"changed": 1},
                                                         "internal_assay_titles": {"changed": 1}}

    def test_graph_only_and_rule_only_edges(self, index):
        result = self.run(index, [((1, 2), full((1, 2))), ((40, 41), full((1, 2))), ((42, 43), stored())],
                          in_scope=lambda c, p: c != 5)
        assert result["graph_only"] == {"labelled": 1, "unlabelled": 1}
        assert result["rule_only"] == {"with_assay": 2, "without_assay": 1}  # (7, 8), (9, 10); (3, 4)
        assert result["compared"] == 1

    def test_examples_are_capped_per_class(self, index):
        edges = [((1, 2), stored()), ((7, 8), stored()), ((9, 10), stored())]
        result = self.run(index, edges, cap=2)
        assert len(result["examples"][labels.NEW]) == 2
        example = result["examples"][labels.NEW][0]
        assert example["child"] == 1 and example["computed"]["internal_assay_title"] == "Patient Visit"
        assert "internal_assay_title" in example["differs"]

    def test_a_duplicate_edge_is_counted_once_as_a_duplicate(self, index):
        result = self.run(index, [((1, 2), full((1, 2))), ((1, 2), full((1, 2)))],
                          in_scope=lambda c, p: (c, p) == (1, 2))
        assert result["compared"] == 1 and result["duplicates"] == 1

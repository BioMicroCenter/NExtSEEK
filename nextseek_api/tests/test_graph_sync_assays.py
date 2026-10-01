"""The assay layer of graph schema 1.3 (nextseek_api/graph_sync/assays.py; the spec, sections 4 and 5.2 to 5.3).
Pure: no database, no Neo4j."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from nextseek_api.graph_sync import assays as a

KNOWN = {"TIS", "CEX", "CEL", "AB", "ABP", "D.SEQ", "A.VCF", "PAT"}
INTERNAL = [{"id": 99, "title": "Patient Visit"}, {"id": 120, "title": "RNA-seq"}, {"id": 130, "title": "RNA-seq"},
            {"id": 140, "title": None}]


def _row(row_id, internal_id, **extra):
    base = {"id": row_id, "internal_assay_id": internal_id, "assay_name": None, "alternative_assay_names": None,
            "description": None, "tags": None, "parent_clade_type": None, "child_clade_type": None,
            "required_parent_sample_types": None, "optional_parent_sample_types": None,
            "children_sample_types": None}
    base.update(extra)
    return base


def _node(cat, assay_id):
    return next(n for n in cat.nodes if n["id"] == assay_id)


def test_the_role_names_are_the_contracts():
    from nextseek_graph import schema
    assert a.INPUT_TO is schema.INPUT_TO and a.OUTPUT_OF is schema.OUTPUT_OF
    assert a.REL_TYPES == ("INPUT_TO", "OUTPUT_OF")


# --- the Assay nodes -------------------------------------------------------------------------------

def test_one_node_per_internal_assay_with_the_catalog_properties():
    context = [_row(1, 99, assay_name="Patient Visit", alternative_assay_names="Clinic visit; Visit",
                    description=" A visit. ", tags="visit, clinic", parent_clade_type="Source",
                    child_clade_type="Source", required_parent_sample_types="PAT",
                    optional_parent_sample_types="AB or ABP", children_sample_types="TIS or CEX or CEL, AB")]
    cat = a.build_catalog(INTERNAL, context, KNOWN)
    assert cat.ids == [99, 120, 130, 140]
    assert _node(cat, 99) == {
        "id": 99, "title": "Patient Visit", "other_names": ["Clinic visit", "Visit"], "description": "A visit.",
        "tags": ["visit", "clinic"], "parent_clade": "Source", "child_clade": "Source", "input_types": ["PAT"],
        "optional_input_types": ["AB", "ABP"], "output_types": ["TIS", "CEX", "CEL", "AB"], "has_context": True}
    assert _node(cat, 140) == {"id": 140, "has_context": False}          # empty is absent, the title included


def test_the_assay_name_joins_the_other_names_only_when_it_differs_from_the_title():
    cat = a.build_catalog(INTERNAL, [_row(1, 120, assay_name="Bulk RNA-seq", alternative_assay_names="RNA-seq"),
                                     _row(2, 99, assay_name="Patient Visit")], KNOWN)
    assert _node(cat, 120)["other_names"] == ["Bulk RNA-seq"]
    assert "other_names" not in _node(cat, 99)


def test_other_names_split_only_on_separators_outside_parentheses():
    name = "Antibody-Dependent Functional Profiling (ADFP, systems serology)"
    assert a.split_names(f"{name}, ADFP; Serology (IgG; IgA) panel") == [
        name, "ADFP", "Serology (IgG; IgA) panel"]
    cat = a.build_catalog(INTERNAL, [_row(1, 99, alternative_assay_names=f"{name}, ADFP")], KNOWN)
    assert _node(cat, 99)["other_names"] == [name, "ADFP"]
    assert a.split_names(None) == [] and a.split_names(" , ;") == []


def test_accepted_by_and_generates_keep_each_either_or_group():
    context = [_row(1, 99, required_parent_sample_types="TIS or CEX or CEL, AB or ABP",
                    optional_parent_sample_types="PAT", children_sample_types="D.SEQ, A.VCF or D.SEQ")]
    cat = a.build_catalog(INTERNAL, context, KNOWN)
    assert cat.accepted_by == [
        {"code": "TIS", "assay_id": 99, "required": True, "group": 0},
        {"code": "CEX", "assay_id": 99, "required": True, "group": 0},
        {"code": "CEL", "assay_id": 99, "required": True, "group": 0},
        {"code": "AB", "assay_id": 99, "required": True, "group": 1},
        {"code": "ABP", "assay_id": 99, "required": True, "group": 1},
        {"code": "PAT", "assay_id": 99, "required": False, "group": 0}]
    assert cat.generates == [
        {"assay_id": 99, "code": "D.SEQ", "group": 0},
        {"assay_id": 99, "code": "A.VCF", "group": 1},
        {"assay_id": 99, "code": "D.SEQ", "group": 1}]
    assert _node(cat, 99)["output_types"] == ["D.SEQ", "A.VCF"]


def test_an_unknown_code_is_dropped_and_reported():
    cat = a.build_catalog(INTERNAL, [_row(1, 99, required_parent_sample_types="TIS or NOPE, XYZ",
                                          children_sample_types="D.SEQ")], KNOWN)
    assert _node(cat, 99)["input_types"] == ["TIS"]
    assert [r["code"] for r in cat.accepted_by] == ["TIS"]
    assert cat.reports["unknown_codes"] == {99: ["NOPE", "XYZ"]}


def test_catalog_rows_with_no_id_an_unknown_id_or_a_duplicate_are_reported_and_the_lowest_row_wins():
    context = [_row(7, 99, description="later row"), _row(3, 99, description="first row"), _row(4, None),
               _row(5, 404)]
    cat = a.build_catalog(INTERNAL, context, KNOWN)
    assert _node(cat, 99)["description"] == "first row"
    assert cat.reports["context_rows_duplicated"] == {99: [3, 7]}
    assert cat.reports["context_rows_without_internal_assay"] == [4]
    assert cat.reports["context_rows_for_unknown_internal_assay"] == [5]


def test_an_absent_catalog_gives_nodes_without_context_and_no_catalog_edges():
    cat = a.build_catalog(INTERNAL, [], KNOWN)
    assert all(n["has_context"] is False for n in cat.nodes)
    assert cat.accepted_by == [] and cat.generates == []


def test_a_title_held_by_two_ids_makes_two_nodes_and_is_reported():
    cat = a.build_catalog(INTERNAL, [], KNOWN)
    assert [n["id"] for n in cat.nodes if n.get("title") == "RNA-seq"] == [120, 130]
    assert cat.reports["duplicate_titles"] == {"RNA-seq": [120, 130]}


def test_bad_mapping_rows_are_reported():
    cat = a.build_catalog(INTERNAL, [], KNOWN, pairs=[(5, 99), (6, None), (7, 404), (8, 99)], seek_ids={5, 6, 7})
    assert cat.reports["mapping_rows_without_internal_assay"] == [6]
    assert cat.reports["mapping_rows_unknown_internal_assay"] == [[7, 404]]
    assert cat.reports["mapping_rows_unknown_seek_assay"] == [[8, 99]]
    counts = a.report_counts(cat.reports)
    assert counts["assay_report_mapping_rows_unknown_seek_assay"] == 1
    assert counts["assay_report_duplicate_titles"] == 1
    assert set(a.report_examples(cat.reports)) == set(a.REPORT_KEYS)


# --- the mapping and RUN_IN ------------------------------------------------------------------------

def test_internal_by_seek_keeps_valid_pairs_only_and_both_ids_of_a_two_way_mapping():
    pairs = [(5, 99), (5, 120), (6, None), (7, 404), (8, 99)]
    assert a.internal_by_seek(pairs, {99, 120}, {5, 6, 7}) == {5: (99, 120)}
    assert a.internal_by_seek(pairs, {99, 120}) == {5: (99, 120), 8: (99,)}


def test_run_rows_give_one_row_per_assay_and_study_and_each_of_a_two_way_mapping():
    by_seek = {5: (99, 120), 6: (99,), 7: (99,)}
    rows = a.run_rows(by_seek, [(5, 70), (6, 70), (7, 71), (8, 70), (9, None)])
    assert rows == [{"assay_id": 99, "study_id": 70, "seek_assay_ids": [5, 6]},
                    {"assay_id": 99, "study_id": 71, "seek_assay_ids": [7]},
                    {"assay_id": 120, "study_id": 70, "seek_assay_ids": [5]}]


# --- the role rule ---------------------------------------------------------------------------------

BY_SEEK = {5: (99,), 7: (99,), 9: (130,), 11: (99, 120)}


def test_a_multi_parent_child_is_an_output_and_each_parent_an_input():
    roles = a.roles_for_pairs([(3, 1), (3, 2)], {1: [5], 2: [5], 3: [5]}, BY_SEEK)
    assert roles == {3: {("OUTPUT_OF", 99): {5}}, 1: {("INPUT_TO", 99): {5}}, 2: {("INPUT_TO", 99): {5}}}


def test_a_same_type_edge_gives_each_end_its_own_role():
    roles = a.roles_for_pairs([(21, 20)], {20: [9], 21: [9]}, BY_SEEK)      # A.VCF to A.VCF in one run
    assert roles == {21: {("OUTPUT_OF", 130): {9}}, 20: {("INPUT_TO", 130): {9}}}


def test_several_runs_of_one_kind_ride_on_one_edge_and_a_sample_can_be_both():
    roles = a.roles_for_pairs([(2, 1), (3, 2)], {1: [5, 7], 2: [5, 7], 3: [7]}, BY_SEEK)
    assert roles[2] == {("OUTPUT_OF", 99): {5, 7}, ("INPUT_TO", 99): {7}}
    assert roles[1] == {("INPUT_TO", 99): {5, 7}}


def test_a_shared_pair_rides_on_one_edge_per_role_carrying_both_seek_ids():
    # the studies tool's share mode: source assay 5 (study 70) and its clone 7 (study 71), both mapped to 99
    roles = a.roles_for_pairs([(2, 1)], {1: [5, 7], 2: [5, 7]}, BY_SEEK)
    assert roles == {2: {("OUTPUT_OF", 99): {5, 7}}, 1: {("INPUT_TO", 99): {5, 7}}}
    assert a.run_rows({5: (99,), 7: (99,)}, [(5, 70), (7, 71)]) == [
        {"assay_id": 99, "study_id": 70, "seek_assay_ids": [5]},
        {"assay_id": 99, "study_id": 71, "seek_assay_ids": [7]}]


def test_a_shared_sample_whose_parent_stayed_behind_has_no_role_in_the_clone():
    # the share brought the sample into clone 7 but not its parent: membership (2, 7) has no role, counted, not failed
    roles = a.roles_for_pairs([(2, 1)], {1: [5], 2: [5, 7]}, BY_SEEK)
    assert roles == {2: {("OUTPUT_OF", 99): {5}}, 1: {("INPUT_TO", 99): {5}}}
    assert a.members_without_role([1, 2], {1: [5], 2: [5, 7]}, BY_SEEK, roles) == 1


def test_a_seek_assay_mapped_to_two_internal_assays_gives_edges_to_both():
    roles = a.roles_for_pairs([(2, 1)], {1: [11], 2: [11]}, BY_SEEK)
    assert roles == {2: {("OUTPUT_OF", 99): {11}, ("OUTPUT_OF", 120): {11}},
                     1: {("INPUT_TO", 99): {11}, ("INPUT_TO", 120): {11}}}


def test_no_role_without_a_shared_mapped_run_and_none_for_a_self_loop_or_a_legacy_id():
    roles = a.roles_for_pairs([(2, 1), (4, 3), (5, 5), ("u-7", 1), (6, None)],
                              {1: [5], 2: [7, 13], 3: [13], 4: [13], 5: [5], 6: [5]}, BY_SEEK)
    # 2 and 1 share nothing; 4 and 3 share an unmapped run; a self-loop and a non-int end are not lineage
    assert roles == {}


def test_sample_edge_rows_are_sorted_and_carry_empty_lists_for_a_sample_without_roles():
    roles = a.roles_for_pairs([(2, 1)], {1: [11], 2: [11]}, BY_SEEK)
    rows = a.sample_edge_rows({**roles, 3: {}})
    assert rows == [
        {"id": 1, "inputs": [{"assay_id": 99, "seek_assay_ids": [11]}, {"assay_id": 120, "seek_assay_ids": [11]}],
         "outputs": []},
        {"id": 2, "inputs": [], "outputs": [{"assay_id": 99, "seek_assay_ids": [11]},
                                            {"assay_id": 120, "seek_assay_ids": [11]}]},
        {"id": 3, "inputs": [], "outputs": []}]


def test_members_without_role_count_memberships_of_mapped_seek_assays_only():
    by_sample = {1: [5], 2: [5, 9], 3: [5, 13]}
    roles = a.roles_for_pairs([(2, 1)], by_sample, BY_SEEK)
    # 2 in 9 has no role; 3 in 5 has none; 3's run 13 is not mapped, so it is drift's to report, not counted here
    assert a.members_without_role([1, 2, 3], by_sample, BY_SEEK, roles) == 2


def test_unmapped_seek_assays_with_members():
    assert a.unmapped_seek_assays({5: 40, 13: 7, 14: 0}, BY_SEEK) == {13: 7}


@pytest.mark.parametrize("seek_id, rel", [(0, "INPUT_TO"), (5, "OUTPUT_OF"), ((1 << 30) - 1, "OUTPUT_OF")])
def test_a_role_code_round_trips(seek_id, rel):
    code = a.encode_role(seek_id, rel)
    assert 0 <= code < (1 << 31)
    assert a.decode_role(code) == (seek_id, rel)


@pytest.mark.parametrize("seek_id, rel", [(1 << 30, "INPUT_TO"), (-1, "INPUT_TO"), (5, "RUN_IN"), (True, "INPUT_TO")])
def test_a_role_code_refuses_what_it_cannot_pack(seek_id, rel):
    with pytest.raises(ValueError):
        a.encode_role(seek_id, rel)


def test_the_measurement_script_applies_the_same_role_rule():
    """scripts/graph_search/measure_assay_nodes.py writes the rule out again (it runs on an image without this
    module); both must give the same roles."""
    script = Path(__file__).resolve().parents[2] / "scripts" / "graph_search" / "measure_assay_nodes.py"
    spec = importlib.util.spec_from_file_location("measure_assay_nodes", script)
    measure = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(measure)
    pairs = [(3, 1), (3, 2), (21, 20), (2, 1), (4, 3), (6, 4), (7, 7)]
    by_sample = {1: (5, 7), 2: (5, 7, 11), 3: (5, 11), 4: (13,), 6: (13, 9), 20: (9,), 21: (9,), 7: (5,)}
    assert measure.role_rule(pairs, by_sample, BY_SEEK) == a.roles_for_pairs(pairs, by_sample, BY_SEEK)

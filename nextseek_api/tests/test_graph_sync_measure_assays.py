"""The read-only measurement script of graph schema 1.3 (scripts/graph_search/measure_assay_nodes.py; spec 9).

Only its pure part is tested here: the script runs in the app container of each box against live data, and imports
Django and the driver inside main(), so loading it needs neither.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "graph_search" / "measure_assay_nodes.py"


def _load():
    spec = importlib.util.spec_from_file_location("measure_assay_nodes", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


m = _load()


def test_the_valid_mapping_drops_and_reports_each_bad_row():
    by_seek, problems = m.valid_mapping(
        [(5, 99), (5, 120), (6, None), (7, 404), (8, 99)], internal_ids={99, 120}, seek_ids={5, 6, 7})
    assert by_seek == {5: (99, 120)}
    assert problems == {"without_internal_assay": [6], "unknown_internal_assay": [[7, 404]],
                        "unknown_seek_assay": [[8, 99]]}


def test_the_role_rule_gives_each_end_its_own_role():
    by_seek = {5: (99,), 7: (99,), 9: (130,)}
    assays = {1: (5, 7), 2: (5, 7), 3: (5,), 4: (9,), 5: (9,), 6: (5,)}
    # 2 has two parents in run 5; runs 5 and 7 are one kind (99); 5 -> 4 is a same-type edge in run 9;
    # 6 shares nothing with 4; a self-loop is not lineage
    roles = m.role_rule([(2, 1), (2, 3), (5, 4), (6, 4), (1, 1)], assays, by_seek)
    assert roles == {
        2: {("OUTPUT_OF", 99): {5, 7}},
        1: {("INPUT_TO", 99): {5, 7}},
        3: {("INPUT_TO", 99): {5}},
        5: {("OUTPUT_OF", 130): {9}},
        4: {("INPUT_TO", 130): {9}},
    }


def test_the_tally_counts_edges_samples_and_members_without_role():
    by_seek = {5: (99, 120), 6: (98,)}
    assays = {1: (5,), 2: (5,), 3: (5, 6), 4: (6,)}
    tally = m.RoleTally(assays, by_seek)
    for child, parent in [(2, 1), (2, 1), (3, 9)]:   # a repeated pair counts once; 9 is no member
        tally.add(child, parent)
    counts = tally.finish()
    # 2 OUTPUT_OF 99 and 120; 1 INPUT_TO 99 and 120
    assert counts["expected_sample_edges"] == 4
    assert (counts["input_to_edges"], counts["output_of_edges"]) == (2, 2)
    assert counts["samples_with_edges"] == 2
    # memberships in mapped SEEK assays without a role: 3 in 5, 3 in 6, 4 in 6
    assert counts["members_without_role"] == 3
    assert counts["mapped_seek_assays_without_any_role"] == {6: 2}


def test_the_suggested_limits_follow_the_measured_numbers():
    assert m.suggest_limits(degree_p999=40, largest_seek_membership=1_200) == {
        "PARTNER_REWRITE_MAX": 1_000, "ASSAY_REWRITE_MAX": 50_000}
    assert m.suggest_limits(degree_p999=3_456, largest_seek_membership=73_000) == {
        "PARTNER_REWRITE_MAX": 4_000, "ASSAY_REWRITE_MAX": 80_000}
    assert m.suggest_limits(degree_p999=90_000, largest_seek_membership=900_000) == {
        "PARTNER_REWRITE_MAX": 10_000, "ASSAY_REWRITE_MAX": 250_000}


def test_loading_the_script_needs_no_django_setup():
    assert m.main.__doc__ and "READ ONLY" in m.__doc__

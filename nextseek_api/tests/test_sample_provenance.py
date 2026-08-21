"""Provenance: which types feed which, and how far down the pipeline each sits."""

import math

import pandas as pd

from nextseek_api.services.sample_provenance import (
    derivation_edges,
    sample_type_depths,
)


def _prov_df():
    """Two hops, one of them from a type not itself downloaded."""
    return pd.DataFrame([
        {"uuid": "D.SEQ-1", "sample_type": "D.SEQ", "Parent": "DNA-9"},
        {"uuid": "TIS-2", "sample_type": "TIS", "Parent": "MUS-3; MUS-4"},
    ])


def test_edges_take_the_assay_from_the_neo4j_hop():
    hops = [("DNA-9", "Short Read Sequencing", "D.SEQ-1")]
    edges = derivation_edges(_prov_df(), {}, hops)
    assert edges[("DNA", "D.SEQ")] == {"Short Read Sequencing"}


def test_neo4j_hops_win_over_the_parent_column():
    """Both describe the same lineage, but only the graph edge knows which
    assay produced this particular child."""
    hops = [("DNA-9", "Short Read Sequencing", "D.SEQ-1")]
    edges = derivation_edges(_prov_df(), {"D.SEQ-1": "Something Else"}, hops)
    assert edges == {("DNA", "D.SEQ"): {"Short Read Sequencing"}}


def test_edges_fall_back_to_the_parent_column_without_hops():
    """An unreachable graph costs the assay labels, not the lineage."""
    edges = derivation_edges(_prov_df(), {}, [])
    assert edges[("DNA", "D.SEQ")] == set()
    assert ("MUS", "TIS") in edges


def test_edges_collapse_repeated_parents_into_one_hop():
    """TIS has two MUS parents; that is one relationship, not two."""
    edges = derivation_edges(_prov_df(), {})
    assert len([e for e in edges if e[0] == "MUS"]) == 1


def test_edges_ignore_a_parent_of_the_same_type():
    df = pd.DataFrame([{"uuid": "CEL-1", "sample_type": "CEL", "Parent": "CEL-0"}])
    assert derivation_edges(df, {}) == {}


def test_edges_are_empty_without_a_parent_column():
    df = pd.DataFrame([{"uuid": "MUS-1", "sample_type": "MUS", "Name": "m1"}])
    assert derivation_edges(df, {}) == {}


def test_dotted_type_codes_survive_extraction():
    """D.SEQ must not truncate to D."""
    hops = [("DNA-9", "", "D.SEQ-1")]
    assert ("DNA", "D.SEQ") in derivation_edges(_prov_df(), {}, hops)


def test_depth_counts_from_a_type_with_no_parent():
    edges = {("PAT", "PAV"): set(), ("PAV", "TIS"): set(), ("TIS", "DNA"): set()}
    assert sample_type_depths(edges) == {"PAT": 0, "PAV": 1, "TIS": 2, "DNA": 3}


def test_depth_is_the_longest_path_not_the_shortest():
    """DNA is reachable from PAT in one hop and in three. It sits at three:
    a reader wants the latest point at which the type can appear."""
    edges = {("PAT", "DNA"): set(), ("PAT", "PAV"): set(),
             ("PAV", "TIS"): set(), ("TIS", "DNA"): set()}
    assert sample_type_depths(edges)["DNA"] == 3


def test_depth_terminates_on_a_two_cycle():
    """CEL <-> D.FLOW is real in production. Depth must not recurse forever."""
    edges = {("TIS", "CEL"): set(), ("CEL", "D.FLOW"): set(), ("D.FLOW", "CEL"): set()}
    depths = sample_type_depths(edges)
    assert set(depths) == {"TIS", "CEL", "D.FLOW"}
    assert all(isinstance(d, int) for d in depths.values())


def test_depth_resolves_a_cycle_the_same_way_every_run():
    """Without a fixed visit order, which member of a cycle gets depth 0
    would depend on dict iteration."""
    edges = {("CEL", "D.FLOW"): set(), ("D.FLOW", "CEL"): set()}
    results = [sample_type_depths(edges) for _ in range(5)]
    assert all(r == results[0] for r in results)


def test_depth_omits_types_that_appear_in_no_edge():
    edges = {("PAT", "PAV"): set()}
    assert "MUS" not in sample_type_depths(edges)
    assert sample_type_depths(edges).get("MUS", math.inf) == math.inf


from nextseek_api.services.sample_provenance import build_provenance_rows


def _rows(edges):
    return build_provenance_rows(edges, sample_type_depths(edges))


def test_a_chain_reads_left_to_right_across_the_row():
    edges = {("PAT", "PAV"): {"Consent"}, ("PAV", "TIS"): {"Tissue Collection"}}
    assert _rows(edges) == [
        ["PAT", "--[Consent]-->", "PAV", "--[Tissue Collection]-->", "TIS"],
    ]


def test_a_hop_without_an_assay_gets_a_plain_arrow():
    assert _rows({("DNA", "D.SEQ"): set()}) == [["DNA", "------>", "D.SEQ"]]


def test_several_assays_on_one_hop_join_sorted():
    edges = {("TIS", "D.IMG"): {"Imaging", "Histology"}}
    assert _rows(edges)[0][1] == "--[Histology, Imaging]-->"


def test_every_hop_appears_exactly_once():
    """The whole point of a cover: no hop repeated, none dropped."""
    edges = {("PAT", "PAV"): set(), ("PAV", "TIS"): set(), ("TIS", "DNA"): set(),
             ("TIS", "D.IMG"): set(), ("DNA", "D.SEQ"): set()}
    seen = []
    for row in _rows(edges):
        types = row[::2]
        seen += list(zip(types, types[1:]))
    assert sorted(seen) == sorted(edges)


def test_chains_sort_by_the_depth_of_their_first_type():
    """Earliest-generated flows come first -- that is the ordering asked for."""
    edges = {("PAT", "PAV"): set(), ("PAV", "TIS"): set(), ("TIS", "D.IMG"): set(),
             ("D.IMG", "A.MIGR"): set(), ("TIS", "D.TITR"): set()}
    first_types = [row[0] for row in _rows(edges)]
    depths = sample_type_depths(edges)
    assert first_types == sorted(first_types, key=lambda t: (depths[t], t))
    assert first_types[0] == "PAT"


def test_a_chain_may_start_mid_pipeline():
    """A hop whose upstream was already shown starts its own chain rather than
    re-treading the prefix."""
    edges = {("PAT", "PAV"): set(), ("PAV", "TIS"): set(),
             ("TIS", "DNA"): set(), ("TIS", "D.IMG"): set()}
    rows = _rows(edges)
    assert len(rows) == 2
    assert rows[0][0] == "PAT"
    assert rows[1][0] == "TIS"


def test_a_cycle_terminates_and_visits_each_type_once_per_chain():
    edges = {("TIS", "CEL"): set(), ("CEL", "D.FLOW"): set(), ("D.FLOW", "CEL"): set()}
    rows = _rows(edges)
    for row in rows:
        types = row[::2]
        assert len(types) == len(set(types))
    seen = []
    for row in rows:
        types = row[::2]
        seen += list(zip(types, types[1:]))
    assert sorted(seen) == sorted(edges)


def test_no_edges_yields_no_rows():
    assert build_provenance_rows({}, {}) == []

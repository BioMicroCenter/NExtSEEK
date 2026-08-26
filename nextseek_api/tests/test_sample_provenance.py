"""Provenance: which types feed which, and how far down the pipeline each sits."""

import itertools
import math

import pandas as pd

from nextseek_api.services.sample_provenance import (
    SAMPLE_TYPE_RE,
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


def test_a_parent_column_hop_is_labelled_from_the_per_sample_assay():
    """Without the graph, the child's own assay link is the only label there
    is. Weaker than the edge, but better than a bare arrow."""
    edges = derivation_edges(_prov_df(), {"D.SEQ-1": "Short Read Sequencing"})
    assert edges[("DNA", "D.SEQ")] == {"Short Read Sequencing"}


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


def _nan_type_df():
    """A UID with no [A-Z] run alongside a good one.

    `sample_type` is derived exactly as write_samples_workbook derives it, so
    the bad row's type is a real NaN float rather than a hand-written None.
    """
    df = pd.DataFrame([
        {"uuid": "123-456-7", "Parent": "MUS-3"},
        {"uuid": "TIS-2", "Parent": "MUS-3"},
    ])
    df["sample_type"] = df["uuid"].astype(str).str.extract(SAMPLE_TYPE_RE, expand=False)
    return df


def test_a_uid_with_no_type_code_is_skipped_not_edged():
    """`not float('nan')` is False, so a NaN type used to sail past the guard
    and seed an edge keyed on a float. sample_type_depths then sorted a set
    mixing str and float and raised TypeError, taking the whole download with
    it -- on the degraded path, where provenance is supposed to cost nothing."""
    assert derivation_edges(_nan_type_df(), {}) == {("MUS", "TIS"): set()}


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
    """Which member of a cycle gets depth 0 depends on the order nodes are
    visited, and that order comes from a dict built by iterating `edges`.

    So the input has to vary, not the number of calls: CPython iterates a dict
    built the same way twice in one process the same way twice, which is why
    calling the function five times on one dict passed even with the sorted()
    traversal this test exists to pin removed. Feeding the same edges in every
    insertion order is what actually holds it down."""
    hops = [("TIS", "CEL"), ("CEL", "D.FLOW"), ("D.FLOW", "CEL"), ("CEL", "OOC")]
    results = [
        sample_type_depths({hop: set() for hop in order})
        for order in itertools.permutations(hops)
    ]
    assert all(r == results[0] for r in results), results


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


import re

from nextseek_api.services.sample_provenance import build_provenance_tree


def _tree(edges):
    return build_provenance_tree(edges)


def _type_of(line):
    """The sample type on a tree line, stripped of indent, connector and assays."""
    text = re.sub(r"^[│ ]*(?:├── |└── )?", "", line)
    return text.split("   ")[0].strip()


def test_a_root_is_unindented_and_its_child_is_indented():
    lines = _tree({("PAT", "PAV"): set()})
    assert lines == ["PAT", "└── PAV"]


def test_assays_render_in_brackets_after_the_type():
    lines = _tree({("PAT", "PAV"): {"Consent"}})
    assert lines[1] == "└── PAV   [Consent]"


def test_a_hop_without_an_assay_gets_no_bracket():
    assert _tree({("DNA", "D.SEQ"): set()})[1] == "└── D.SEQ"


def test_several_assays_on_one_hop_join_sorted_in_brackets():
    # NOT `test_several_assays_on_one_hop_join_sorted` -- that name is already
    # taken at line 157 by a build_provenance_rows test that Task 2 deletes.
    # Reusing it here would silently shadow that test until then.
    lines = _tree({("TIS", "D.IMG"): {"Imaging", "Histology"}})
    assert lines[1] == "└── D.IMG   [Histology, Imaging]"


def test_a_non_final_child_uses_the_tee_connector():
    edges = {("TIS", "DNA"): set(), ("TIS", "RNA"): set()}
    assert _tree(edges) == ["TIS", "├── DNA", "└── RNA"]


def test_a_grandchild_of_a_tee_keeps_the_trunk():
    """The │ must continue past a child that has siblings below it."""
    edges = {("TIS", "DNA"): set(), ("DNA", "D.SEQ"): set(), ("TIS", "RNA"): set()}
    assert _tree(edges) == ["TIS", "├── DNA", "│   └── D.SEQ", "└── RNA"]


def test_a_type_with_two_parents_is_expanded_once():
    """The DAG is not a tree: expanding every occurrence explodes the sheet."""
    edges = {("A", "X"): set(), ("B", "X"): set(), ("X", "Y"): set()}
    lines = _tree(edges)
    assert sum(1 for line in lines if _type_of(line) == "Y") == 1
    assert [line for line in lines if line.endswith("(expanded above)")]


def test_a_childless_repeat_is_shown_in_full_not_deferred():
    """(expanded above) would be noise on a leaf -- there is nothing to expand."""
    edges = {("A", "X"): set(), ("B", "X"): set()}
    assert not [line for line in _tree(edges) if line.endswith("(expanded above)")]


def test_a_cycle_terminates_and_is_marked():
    edges = {("TIS", "CEL"): set(), ("CEL", "D.FLOW"): set(), ("D.FLOW", "CEL"): set()}
    lines = _tree(edges)
    assert [line for line in lines if line.endswith("(cycle)")]


def test_every_type_in_every_hop_appears_somewhere():
    """Nothing may be silently dropped -- that would be a correctness bug."""
    edges = {("PAT", "PAV"): set(), ("PAV", "TIS"): set(), ("TIS", "DNA"): set(),
             ("TIS", "D.IMG"): set(), ("DNA", "D.SEQ"): set()}
    shown = {_type_of(line) for line in _tree(edges)}
    for parent, child in edges:
        assert parent in shown and child in shown


def test_roots_appear_unindented_in_sorted_order():
    edges = {("PAT", "PAV"): set(), ("MUS", "TIS"): set()}
    lines = _tree(edges)
    assert [line for line in lines if line and not line.startswith((" ", "│", "├", "└"))] == [
        "MUS", "PAT",
    ]


def test_root_trees_are_separated_by_a_blank_line():
    edges = {("PAT", "PAV"): set(), ("MUS", "TIS"): set()}
    assert _tree(edges) == ["MUS", "└── TIS", "", "PAT", "└── PAV"]


def test_no_edges_yields_no_lines():
    assert build_provenance_tree({}) == []


def test_a_bare_self_loop_is_not_dropped():
    """X is its own only parent, so the normal root scan finds nothing
    eligible -- the second pass must still walk X as its own root."""
    edges = {("X", "X"): set()}
    lines = _tree(edges)
    assert lines == ["X", "└── X   (cycle)"]
    shown = {_type_of(line) for line in lines}
    assert shown == {"X"}
    assert lines[-1].endswith("(cycle)")


def test_a_mutual_pair_with_no_external_root_is_not_dropped():
    """Neither A nor B has any parent outside the pair, so neither is
    eligible under the normal root rule -- both must still appear."""
    edges = {("A", "B"): set(), ("B", "A"): set()}
    lines = _tree(edges)
    assert lines == ["A", "└── B", "    └── A   (cycle)"]
    shown = {_type_of(line) for line in lines}
    assert shown == {"A", "B"}
    assert [line for line in lines if line.endswith("(cycle)")]


def test_a_detached_rootless_cycle_appears_alongside_a_normal_tree():
    """A graph can mix a properly rooted tree with a separate rootless
    component; both must be fully represented, not just the rooted one."""
    edges = {
        ("PAT", "PAV"): {"Consent"},
        ("X", "X"): set(),
    }
    lines = _tree(edges)
    shown = {_type_of(line) for line in lines}
    for parent, child in edges:
        assert parent in shown and child in shown
    assert lines == ["PAT", "└── PAV   [Consent]", "", "X", "└── X   (cycle)"]
    # The walk terminates -- a non-terminating second pass would hang the test.

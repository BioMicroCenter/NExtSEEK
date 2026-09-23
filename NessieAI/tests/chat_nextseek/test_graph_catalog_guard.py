"""The graph agent's catalog guard and whole-node guard (spec section 4.3, D13).

With the v1.1 catalog live, a Cypher property read is checked against the labels of its variable: a ``T_X``
variable may read the Sample system properties and the attributes of X that hold a value, a plain ``Sample``
variable the union over all types, other labels and relationship types their v1.1 property sets, and a variable of
unknown label everything. Returning or collecting a whole Sample node is refused (D13).

Both functions are pure: they read the Cypher text and a catalog snapshot, never Neo4j.
"""

import re
from pathlib import Path
from types import MappingProxyType

import pytest

from chat_nextseek import cypher_text
from chat_nextseek import graph_catalog as gcat
from chat_nextseek.agents.graph import (
    V11_RELATIONSHIP_PROPERTIES,
    V11_SYSTEM_PROPERTIES,
    V12_SYSTEM_PROPERTIES,
    _mask_cypher,
    catalog_unknown_properties,
    whole_node_returns,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
SCHEMA_DOC = (REPO_ROOT / "docs" / "neo4j-schema.md").read_text(encoding="utf-8")
V10_DOC = SCHEMA_DOC.split("\n## v1.1", 1)[0]
V11_DOC = SCHEMA_DOC.split("\n## v1.1", 1)[1]


def _row(title, sample_count=10):
    label = "T_" + re.sub(r"[^A-Za-z0-9_]", "_", title)
    return gcat.TypeIndexRow(title=title, label=label, name=None, clade=None, sample_count=sample_count,
                             deprecated=False, attributes_with_values=1)


SNAPSHOT = gcat.CatalogSnapshot(
    catalog_hash="h1", synced_at=None, has_usage=False,
    index=(_row("TIS"), _row("D.SEQ"), _row("MUS", 0)),
    guard=MappingProxyType({
        "T_TIS": frozenset({"Organ", "Catalog#", "Tissue Type"}),
        "T_D_SEQ": frozenset({"Sequencer", "Organ"}),
        "T_MUS": frozenset(),
    }),
)


def unknown(cypher):
    return catalog_unknown_properties(cypher, SNAPSHOT)


# --- the v1.1 constants follow the document of record ---------------------------------------------------------------


def test_mask_is_the_shared_cypher_text_mask():
    assert _mask_cypher is cypher_text.mask_cypher


def test_system_properties_are_the_v11_sample_system_properties():
    row = next(line for line in V11_DOC.splitlines() if line.startswith("| `Sample` + "))
    system = row.split("system:", 1)[1].split("metadata:", 1)[0]
    assert V11_SYSTEM_PROPERTIES == frozenset(re.findall(r"`([a-z_]+)`", system))


def test_relationship_types_are_the_v11_relationships():
    section = V11_DOC.split("### Relationships", 1)[1].split("\n###", 1)[0]
    table = [line for line in section.splitlines() if line.startswith("| `(")]
    assert set(V11_RELATIONSHIP_PROPERTIES) == {t for line in table for t in re.findall(r"\[:([A-Z_]+)", line)}
    assert "CHILD_OF" not in V11_RELATIONSHIP_PROPERTIES


def test_derived_from_keeps_its_v10_properties_and_member_of_its_v11_ones():
    row = next(line for line in V10_DOC.splitlines() if "[:DERIVED_FROM]" in line and line.startswith("| `("))
    assert V11_RELATIONSHIP_PROPERTIES["DERIVED_FROM"] == frozenset(
        re.findall(r"`([a-z_]+)`", row.split("Properties", 1)[1]))
    member = re.search(r"MEMBER_OF \{([^}]*)\}", V11_DOC).group(1)
    assert V11_RELATIONSHIP_PROPERTIES["MEMBER_OF"] == frozenset(p.strip() for p in member.split(","))


# --- per-label property guard ---------------------------------------------------------------------------------------


def test_an_attribute_the_type_does_not_hold_is_rejected():
    assert unknown("MATCH (s:T_TIS) WHERE s.Sequencer = 'x' RETURN s.id") == ["TIS.Sequencer"]


def test_the_same_attribute_passes_on_a_type_that_holds_it():
    assert unknown("MATCH (s:T_D_SEQ) WHERE s.Sequencer = 'x' RETURN s.id") == []


def test_sample_and_type_labels_together_use_the_type():
    assert unknown("MATCH (s:Sample:T_TIS) WHERE s.Sequencer = 'x' RETURN s.id") == ["TIS.Sequencer"]


def test_a_where_label_predicate_labels_the_variable():
    cypher = "MATCH (s:Sample) WHERE s:T_TIS AND s.Sequencer = 'x' RETURN s.id"
    assert unknown(cypher) == ["TIS.Sequencer"]


def test_a_type_label_in_a_parenthesised_predicate_labels_the_variable():
    cypher = "MATCH (s) WHERE (s:T_TIS OR s:T_MUS) AND s.Sequencer = 'x' RETURN s.id"
    assert unknown(cypher) == ["TIS|MUS.Sequencer"]


def test_a_plain_sample_variable_uses_the_union_over_all_types():
    assert unknown("MATCH (s:Sample) WHERE s.Sequencer = 'x' AND s.`Catalog#` = 'y' RETURN s.Organ") == []
    assert unknown("MATCH (s:Sample) WHERE s.Nope = 'x' RETURN s.id") == ["Sample.Nope"]


def test_system_properties_always_pass_even_on_a_type_with_no_attributes():
    cypher = ("MATCH (s:T_MUS) RETURN s.id, s.uuid, s.type, s.title, s.project_ids, s.search_text, "
              "s.synced_at")
    assert unknown(cypher) == []


def test_relationship_properties_are_checked_per_relationship_type():
    ok = ("MATCH (c:Sample)-[r:DERIVED_FROM]->(p:T_TIS) WHERE r.internal_assay_title = $a "
          "RETURN r.protocol_title, p.id")
    assert unknown(ok) == []
    assert unknown("MATCH (c)-[r:DERIVED_FROM]->(p) RETURN r.Organ") == ["DERIVED_FROM.Organ"]
    member = "MATCH (:Person)-[m:MEMBER_OF]->(pr:Project) WHERE m.has_left = false RETURN pr.title"
    assert unknown(member) == []
    assert unknown("MATCH (:Person)-[m:MEMBER_OF]->(:Project) RETURN m.protocol_title") == [
        "MEMBER_OF.protocol_title"]


def test_relationship_pattern_property_maps_are_checked():
    assert unknown("MATCH (c)-[:DERIVED_FROM {internal_assay_title: $a}]->(p) RETURN c.id") == []
    assert unknown("MATCH (c)-[:DERIVED_FROM {Organ: $a}]->(p) RETURN c.id") == ["DERIVED_FROM.Organ"]


def test_a_backticked_property_is_checked():
    assert unknown("MATCH (s:T_TIS) RETURN s.`Catalog#`, s.`Tissue Type`") == []
    assert unknown("MATCH (s:T_D_SEQ) RETURN s.`Catalog#`") == ["D.SEQ.Catalog#"]


def test_a_map_projection_is_checked():
    cypher = "MATCH (s:T_TIS) RETURN s {.Organ, .Sequencer, id: s.id, .`Catalog#`} AS row"
    assert unknown(cypher) == ["TIS.Sequencer"]


def test_a_node_pattern_property_map_is_checked():
    assert unknown("MATCH (s:T_TIS {Sequencer: 'x'}) RETURN s.id") == ["TIS.Sequencer"]
    assert unknown("MATCH (s:T_TIS {uuid: $u, Organ: 'Lung'}) RETURN s.Organ") == []


def test_an_unknown_type_label_is_reported():
    assert unknown("MATCH (s:T_NOPE) RETURN s.id") == [":T_NOPE"]
    assert unknown("MATCH (s:Sample) WHERE s:T_NOPE RETURN s.id") == [":T_NOPE"]


def test_function_and_procedure_names_are_not_properties():
    cypher = ("CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node, score "
              "WITH node WHERE node:T_TIS "
              "RETURN node.id, date.truncate('month', date()) AS month, apoc.coll.sum([1, 2]) AS n")
    assert unknown(cypher) == []


def test_literals_parameters_and_comments_are_ignored():
    cypher = ("MATCH (s:T_TIS) WHERE s.Organ = 'a.Sequencer' AND s.type = 'D.SEQ' "
              "AND s.Organ IN $p.Sequencer // s.Sequencer\nRETURN s.id /* s.Nope */")
    assert unknown(cypher) == []


def test_an_alias_keeps_the_labels_of_its_variable():
    assert unknown("MATCH (s:T_TIS) WITH s AS t RETURN t.Sequencer") == ["TIS.Sequencer"]


def test_other_labels_use_their_v11_property_sets():
    assert unknown("MATCH (st:Study) RETURN st.DOI, st.PMID, st.title, st.seek_study_id") == []
    assert unknown("MATCH (st:Study) RETURN st.Organ") == ["Study.Organ"]
    catalog = ("MATCH (t:SampleType)-[:HAS_ATTRIBUTE]->(a:Attribute {title: 'Organ'}) "
               "WHERE a.sample_count > 0 RETURN t.title, t.label, a.value_type")
    assert unknown(catalog) == []
    assert unknown("MATCH (p:Project) RETURN p.Organ") == ["Project.Organ"]


def test_a_variable_of_unknown_label_is_checked_against_everything():
    assert unknown("MATCH (x) WHERE x.Organ = 'y' RETURN x.id, x.DOI, x.internal_assay_title") == []
    assert unknown("MATCH (x) RETURN x.Nope") == ["x.Nope"]


def test_each_problem_is_reported_once_in_first_seen_order():
    cypher = "MATCH (s:T_TIS) WHERE s.Sequencer = 'x' AND s.Zeta = 1 RETURN s.Sequencer, s.Zeta"
    assert unknown(cypher) == ["TIS.Sequencer", "TIS.Zeta"]


def test_empty_cypher_has_no_problems():
    assert unknown("") == []
    assert whole_node_returns("") == []


# --- whole-node returns (D13) ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("cypher, expected", [
    ("MATCH (s:T_TIS) RETURN s", ["s"]),
    ("MATCH (s:T_TIS) RETURN s LIMIT 5", ["s"]),
    ("MATCH (s:Sample) RETURN collect(s) AS samples", ["s"]),
    ("MATCH (s:T_TIS) WITH collect(DISTINCT s) AS xs RETURN size(xs) AS n", ["s"]),
    ("MATCH (s:T_TIS) RETURN DISTINCT s AS sample ORDER BY s.id", ["s"]),
    ("MATCH (s:T_TIS) RETURN s.id AS id, s", ["s"]),
    ("MATCH (c)-[:DERIVED_FROM]->(p) RETURN p", ["p"]),
    ("MATCH (s)-[:IN_STUDY]->(st:Study) RETURN s, st.title", ["s"]),
    ("MATCH (s:T_TIS) RETURN s {.*}", ["s"]),
    ("MATCH (s:T_TIS) RETURN properties(s) AS props", ["s"]),
    ("MATCH (s:T_TIS) WITH s AS t RETURN t", ["t"]),
    ("CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node RETURN node", ["node"]),
    ("MATCH (s:T_TIS) RETURN *", ["s"]),
])
def test_whole_sample_node_returns_are_found(cypher, expected):
    assert whole_node_returns(cypher) == expected


@pytest.mark.parametrize("cypher", [
    "MATCH (s:T_TIS) RETURN s.id, s.Organ",
    "MATCH (s:T_TIS) RETURN count(s) AS n",
    "MATCH (s:T_TIS) RETURN count(*) AS n",
    "MATCH (st:Study) RETURN st",
    "MATCH (s:T_TIS) WHERE s.Organ = 'RETURN s' RETURN s.id",
    "MATCH (s:T_TIS) WITH s ORDER BY s.id RETURN s.uuid AS uuid, s.type AS type",
    "UNWIND $uids AS s RETURN s",
    "MATCH (s:T_TIS) CALL (s) { MATCH (s)-[:IN_STUDY]->(st:Study) RETURN st } RETURN s.id, st.title",
    "MATCH (s:T_TIS) RETURN s {.id, .Organ} AS row",
])
def test_named_properties_and_counts_are_not_whole_node_returns(cypher):
    assert whole_node_returns(cypher) == []


# --- v1.2: the sync's own properties must not be refused -------------------------------------------------------------
# The live graph is at schema 1.2. A guard that only knows v1.1 refuses correct Cypher reading the properties
# graph_sync writes, and the agent sees that as its own query being wrong: it repairs once, then is refused again.


def test_the_v12_sample_system_properties_follow_the_document_of_record():
    section = SCHEMA_DOC.split("\n## v1.2", 1)[1]
    added = set()
    for line in section.splitlines():
        if line.startswith("| `Sample`"):
            added |= set(re.findall(r"`([a-z_]+)`", line))
    assert {"source_hash", "parent_titles", "parent_title_hashes"} <= added
    assert {"source_hash", "parent_titles", "parent_title_hashes"} <= V12_SYSTEM_PROPERTIES
    assert V11_SYSTEM_PROPERTIES < V12_SYSTEM_PROPERTIES


def test_a_sample_may_read_source_hash():
    assert unknown("MATCH (s:T_TIS) WHERE s.source_hash <> '' RETURN s.id") == []


def test_a_sample_may_read_the_projection_owned_parent_titles():
    assert unknown("MATCH (s:Sample) RETURN s.parent_titles, s.parent_title_hashes") == []


def test_graphmeta_may_read_label_maps_hash():
    assert unknown("MATCH (m:GraphMeta) RETURN m.schema_version, m.label_maps_hash") == []


# --- a map literal passed to a function is not a node pattern --------------------------------------------------------
#
# Prod retest 2026-09-23, Q3/Q4: the graph prompt (661b7426) builds a UID date with
# `date({year: ..., month: ..., day: ...})`, and the guard read the `({year: ...})` inside `date(` as an anonymous
# node pattern's property map, so every UID-date question was refused with
# "properties ['node.year', 'node.month', 'node.day'] are not in the catalog".

UID_DATE_CYPHER = (
    "MATCH (s:T_TIS) WHERE s.uuid =~ '^[^-]+-[0-9]{6}[A-Za-z]{3}-.*' "
    "WITH s, substring(split(s.uuid, '-')[1], 0, 6) AS d "
    "WITH s, date({year: 2000 + toInteger(substring(d, 0, 2)), month: toInteger(substring(d, 2, 2)), "
    "day: toInteger(substring(d, 4, 2))}) AS dt "
    "RETURN toString(min(dt)) AS earliest, toString(max(dt)) AS latest"
)


def test_the_prompts_uid_date_shape_passes():
    assert unknown(UID_DATE_CYPHER) == []


@pytest.mark.parametrize("call", ["date ({year: 2020, month: 1, day: 2})", "duration({days: 3})",
                                  "point({x: 1, y: 2})", "datetime({epochMillis: 0})"])
def test_a_map_literal_argument_is_not_read_as_node_properties(call):
    assert unknown(f"MATCH (s:T_TIS) RETURN {call} AS v, s.Organ AS o") == []


def test_an_anonymous_node_pattern_is_still_checked():
    assert unknown("MATCH ({year: 1}) RETURN 1")
    assert unknown("MATCH (:T_TIS {Nope: 1}) RETURN 1")

"""
The graph scope battery: the statements the prover is judged on, shared by the unit tests and the Neo4j lane.

- TAUGHT: every Cypher shape the two graph agent prompts teach (prompts/graph_agent.txt and
  prompts/variants/v2/graph_agent.txt), completed with a RETURN where the prompt shows a fragment, as literal strings.
  Each carries the ``injected`` and ``joined`` lines the prover must report. The parameters fit the lane's fixture
  (fixture_graph.py) so the lane can run every one.
- TAUGHT_REFUSED: the one taught shape that reads the catalog, which a non-admin may not (spec decision 4).
- REFUSALS: one case per row of spec tables 5.4 and 5.5, with the codes of section 5.8.
- hidden_variants(): each expression-level construct placed directly, inside a nested EXISTS and after a WITH alias
  chain (all refuse), and inside a comment, a string literal and a backticked name (all accept).
- WRITES: statements write_clause must refuse, one per write form.
- report_statements(): the two statements reports/runners.py builds, captured through a patched tool.

Spec: docs/superpowers/specs/2026-09-18-graph-cypher-scope.md sections 5 and 11.
"""
from __future__ import annotations

from typing import Any, NamedTuple


class Case(NamedTuple):
    id: str
    cypher: str
    params: dict[str, Any]
    injected: tuple[str, ...]
    joined: tuple[str, ...] = ()


class Refusal(NamedTuple):
    id: str
    cypher: str
    codes: tuple[str, ...]
    params: dict[str, Any] = {}


STUDY_SCOPE = (
    "MATCH (s:Sample)-[:IN_STUDY]->(st:Study)\n"
    "WHERE toLower(st.title) CONTAINS toLower($project)\n"
    "   OR EXISTS { MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)\n"
    "               WHERE toLower(inv.title) CONTAINS toLower($project) }\n"
)

S = ("s: sample clause",)
ST_JOINED = ("st (Study): joined to s",)

TAUGHT: list[Case] = [
    # ------------------------------------------------------------------ the default prompt (graph_agent.txt)
    Case("default.type_label_filter",
         "MATCH (s:T_TIS) WHERE s.Organ = $organ RETURN count(*) AS n",
         {"organ": "Lung"}, S),
    Case("default.type_label_folded",
         "MATCH (s:T_TIS) WHERE toLower(s.Organ) = toLower($organ)\n"
         "RETURN s.id AS id, s.uuid AS uuid, s.type AS type\nLIMIT 5000",
         {"organ": "lung"}, S),
    Case("default.fulltext",
         "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node, score\n"
         "RETURN node.uuid AS uuid, node.type AS type, score ORDER BY score DESC LIMIT 5000",
         {"q": "alpha"}, ("node: sample clause",)),
    Case("default.type_property",
         "MATCH (s:Sample) WHERE s.type = $type RETURN count(s) AS total",
         {"type": "TIS"}, S),
    Case("default.study_or_investigation_count",
         STUDY_SCOPE + "RETURN count(DISTINCT s) AS n",
         {"project": "study"}, S, ST_JOINED + ("inv (Investigation): joined to st",)),
    Case("default.study_or_investigation_rows",
         STUDY_SCOPE + "RETURN s.uuid AS uuid, s.id AS id, s.type AS type LIMIT 5000",
         {"project": "one"}, S, ST_JOINED + ("inv (Investigation): joined to st",)),
    Case("default.breakdown_by_investigation",
         "MATCH (s:Sample)-[:IN_STUDY]->(st:Study)-[:IN_INVESTIGATION]->(inv:Investigation)\n"
         "RETURN inv.title AS project, count(DISTINCT s) AS sample_count ORDER BY sample_count DESC",
         {}, S, ST_JOINED + ("inv (Investigation): joined to st",)),
    Case("default.project_node",
         "MATCH (s:Sample)-[:IN_PROJECT]->(p:Project) WHERE toLower(p.title) CONTAINS toLower($project)\n"
         "RETURN count(DISTINCT s) AS n",
         {"project": "project"}, ("s: sample clause", "p: project clause")),
    Case("default.single_hop",
         "MATCH (child:Sample)-[r:DERIVED_FROM]->(parent:Sample)\n"
         "RETURN child.uuid AS child, r.internal_assay_title AS assay, parent.uuid AS parent LIMIT 5000",
         {}, ("child: sample clause", "parent: sample clause")),
    Case("default.multi_hop",
         "MATCH (descendant:Sample)-[:DERIVED_FROM*1..]->(ancestor:Sample)\n"
         "RETURN descendant.uuid AS descendant, ancestor.uuid AS ancestor LIMIT 5000",
         {}, ("__scope_path1: every node (descendant, ancestor)",)),
    Case("default.assay_direct_participant",
         "MATCH (a:Sample)-[r:DERIVED_FROM]->(b:Sample)\n"
         "WHERE (a.type = $type OR b.type = $type)\n"
         "  AND r.internal_assay_title = $assay\n"
         "WITH CASE WHEN a.type = $type THEN a ELSE b END AS s\n"
         "RETURN DISTINCT s.id AS id, s.uuid AS uuid, s.type AS type\n"
         "LIMIT 5000",
         {"type": "TIS", "assay": "Dissection"}, ("a: sample clause", "b: sample clause")),
    Case("default.assay_indirect_ancestor",
         "MATCH (child:Sample)-[r:DERIVED_FROM]->(assay_parent:Sample)\n"
         "WHERE r.internal_assay_title = $assay\n"
         "MATCH (assay_parent)-[:DERIVED_FROM*0..]->(ancestor:Sample)\n"
         "WHERE ancestor.type = $ancestor_type\n"
         "RETURN DISTINCT ancestor.id AS id, ancestor.uuid AS uuid, ancestor.type AS type\n"
         "LIMIT 5000",
         {"assay": "Staining", "ancestor_type": "MUS"},
         ("child: sample clause", "assay_parent: sample clause",
          "__scope_path1: every node (assay_parent, ancestor)")),
    Case("default.descendant_types_and",
         "WITH $child_types AS child_types, $parent_types AS parent_types\n"
         "MATCH (p:Sample)\n"
         "WHERE p.type IN parent_types\n"
         "MATCH (c:Sample)-[:DERIVED_FROM*1..]->(p)\n"
         "WHERE c.type IN child_types\n"
         "WITH p, collect(DISTINCT c.type) AS matched_types, child_types\n"
         "WHERE size(matched_types) = size(child_types)\n"
         "RETURN p.id AS id, p.uuid AS uuid, p.type AS type\n"
         "ORDER BY id",
         {"child_types": ["TIS", "SLD"], "parent_types": ["MUS"]},
         ("p: sample clause", "__scope_path1: every node (c, p)")),
    Case("default.count_scoped_to_study",
         STUDY_SCOPE + "RETURN count(s) AS total",
         {"project": "mixed"}, S, ST_JOINED + ("inv (Investigation): joined to st",)),
    Case("default.capped_rows_with_total",
         "MATCH (s:T_MUS)\n"
         "WITH collect(s.uuid) AS rows, count(*) AS total\n"
         "RETURN rows[0..5000] AS rows, total",
         {}, S),
    Case("default.optional_match_with_between",
         "MATCH (s:Sample)-[:IN_STUDY]->(st:Study)\n"
         "OPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)\n"
         "WITH s, st, inv\n"
         "WHERE inv IS NULL OR toLower(inv.title) CONTAINS toLower($project)\n"
         "RETURN count(DISTINCT s) AS n",
         {"project": "inv"}, S, ST_JOINED + ("inv (Investigation): joined to st",)),
    Case("default.paper_of_sample",
         "MATCH (s:Sample {uuid: $uid})-[:IN_STUDY]->(st:Study)\n"
         "WHERE coalesce(st.DOI, '') <> '' OR coalesce(st.PMID, '') <> ''\n"
         "RETURN st.title, st.DOI, st.PMID",
         {"uid": "MUS-230101AAA-1"}, S, ST_JOINED),
    Case("default.samples_of_paper",
         "MATCH (s:Sample)-[:IN_STUDY]->(st:Study)\n"
         "WHERE toLower(st.DOI) = toLower($doi)\n"
         "RETURN s.uuid AS uuid, s.id AS id, s.type AS type LIMIT 5000",
         {"doi": "10.1000/one"}, S, ST_JOINED),
    Case("default.samples_of_pmid",
         "MATCH (s:Sample)-[:IN_STUDY]->(st:Study)\n"
         "WHERE st.PMID = $pmid\n"
         "RETURN count(DISTINCT s) AS n",
         {"pmid": "1001"}, S, ST_JOINED),
    # ------------------------------------------------------------------ the v2 prompt (variants/v2/graph_agent.txt)
    Case("v2.type_label_count",
         "MATCH (s:T_SLD) RETURN count(*) AS n",
         {}, S),
    Case("v2.types_in_list",
         "MATCH (s:Sample) WHERE s.type IN $types RETURN s.type AS type, count(*) AS n ORDER BY n DESC",
         {"types": ["TIS", "SLD"]}, S),
    Case("v2.label_disjunction_test",
         "MATCH (s:Sample) WHERE s:T_SLD|T_TIS RETURN s.type AS type, count(*) AS n ORDER BY n DESC",
         {}, S),
    Case("v2.uid",
         "MATCH (s:Sample) WHERE s.uuid = $uid RETURN s.id AS id, s.uuid AS uuid, s.type AS type",
         {"uid": "TIS-230202BBB-2"}, S),
    Case("v2.uids",
         "MATCH (s:Sample) WHERE s.uuid IN $uids RETURN s.id AS id, s.uuid AS uuid, s.type AS type ORDER BY id",
         {"uids": ["MUS-230101AAA-1", "MUS-230201BBB-1", "MUS-230301CCC-1"]}, S),
    Case("v2.untyped_field_filter",
         "MATCH (s:Sample) WHERE s.Vendor IS NOT NULL AND toLower(trim(toString(s.Vendor))) = toLower($vendor)\n"
         "RETURN count(*) AS n",
         {"vendor": "acme"}, S),
    Case("v2.folded_whole_value",
         "MATCH (s:T_SLD) WHERE toLower(trim(toString(s.Stain))) = toLower($stain) RETURN count(*) AS n",
         {"stain": "H&E"}, S),
    Case("v2.bounded_name_regex",
         "MATCH (s:Sample) WHERE toLower(toString(s.Analyte)) =~ 'il-?1([^0-9].*)?' RETURN count(*) AS n",
         {}, S),
    Case("v2.codes_and_words",
         "MATCH (s:Sample) WHERE toLower(trim(toString(s.Dechlorinated))) IN ['yes', 'y', 'true']\n"
         "RETURN count(*) AS n",
         {}, S),
    Case("v2.numbered_family",
         "MATCH (s:T_MUS)\n"
         "WHERE any(v IN [s.Treatment1, s.Treatment2, s.Treatment3] WHERE v IS NOT NULL AND "
         "toLower(toString(v)) CONTAINS $t)\n"
         "RETURN count(*) AS n",
         {"t": "drug"}, S),
    Case("v2.typed_number",
         "MATCH (s:Sample) WHERE s.Concentration > $min RETURN count(*) AS n",
         {"min": 1.5}, S),
    Case("v2.string_number",
         "MATCH (s:Sample) WHERE toFloat(s.PercentNecrosis) IS NOT NULL AND toFloat(s.PercentNecrosis) >= 40\n"
         "RETURN count(*) AS n",
         {}, S),
    Case("v2.date_prefix",
         "MATCH (s:Sample) WHERE s.CollectionDate STARTS WITH '2021' RETURN count(*) AS n",
         {}, S),
    Case("v2.search_text",
         "MATCH (s:T_MUS) WHERE toLower(s.search_text) CONTAINS toLower($term) RETURN count(*) AS n",
         {"term": "mouse"}, S),
    Case("v2.search_text_all_terms",
         "MATCH (s:Sample)\n"
         "WHERE toLower(s.search_text) CONTAINS toLower($a) AND toLower(s.search_text) CONTAINS toLower($b)\n"
         "RETURN s.id AS id, s.uuid AS uuid, s.type AS type ORDER BY id LIMIT 5000",
         {"a": "mouse", "b": "alpha"}, S),
    Case("v2.fulltext_scoped",
         "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node AS s, score WHERE s:T_MUS\n"
         "RETURN s.uuid AS uuid ORDER BY score DESC LIMIT 5000",
         {"q": "alpha"}, S),
    Case("v2.lab_code",
         "MATCH (s:Sample)\n"
         "WHERE s.uuid =~ ('(?i)^[^-]+-[0-9]{6}' + $lab + '-.*')\n"
         "RETURN count(*) AS n",
         {"lab": "AAA"}, S),
    Case("v2.uid_type_prefix",
         "MATCH (s:Sample) WHERE s.uuid STARTS WITH 'SLD-' RETURN count(*) AS n",
         {}, S),
    Case("v2.scientist",
         "MATCH (s:Sample) WHERE toLower(toString(s.Scientist)) CONTAINS toLower($person) RETURN count(*) AS n",
         {"person": "ada"}, S),
    Case("v2.exists_ancestor_type",
         "MATCH (s:T_SLD)\n"
         "WHERE EXISTS { (s)-[:DERIVED_FROM*1..12]->(:T_MUS) }\n"
         "RETURN count(s) AS n",
         {}, ("s: sample clause", "__scope_path1: every node (s)")),
    Case("v2.exists_descendant_type",
         "MATCH (s:T_MUS)\n"
         "WHERE EXISTS { (s)<-[:DERIVED_FROM*1..12]-(:T_SLD) }\n"
         "RETURN count(s) AS n",
         {}, ("s: sample clause", "__scope_path1: every node (s)")),
    Case("v2.exists_both",
         "MATCH (s:T_TIS)\n"
         "WHERE EXISTS { (s)-[:DERIVED_FROM*1..12]->(:T_MUS) } AND EXISTS { (s)<-[:DERIVED_FROM*1..12]-(:T_SLD) }\n"
         "RETURN count(s) AS n",
         {}, ("s: sample clause", "__scope_path1: every node (s)", "__scope_path2: every node (s)")),
    Case("v2.exists_with_condition",
         "MATCH (s:T_SLD)\n"
         "WHERE EXISTS { (s)-[:DERIVED_FROM*1..12]->(m:T_MUS) WHERE m.Strain = $strain }\n"
         "RETURN count(s) AS n",
         {"strain": "shared"}, ("s: sample clause", "__scope_path1: every node (s, m)")),
    Case("v2.used_another_sample",
         "MATCH (c:Sample)-[:DERIVED_FROM]->(y:T_CHM) WHERE toLower(toString(y.Name)) CONTAINS toLower($name)\n"
         "RETURN count(DISTINCT c) AS n",
         {"name": "drug"}, ("c: sample clause", "y: sample clause")),
    Case("v2.derived_from_uid",
         "MATCH (d:Sample)-[:DERIVED_FROM*1..12]->(:Sample {uuid: $uid})\n"
         "RETURN DISTINCT d.uuid AS uuid, d.type AS type",
         {"uid": "MUS-230101AAA-1"}, ("__scope_path1: every node (d)",)),
    Case("v2.ancestors_of_uid",
         "MATCH (:Sample {uuid: $uid})-[:DERIVED_FROM*1..12]->(a:Sample)\n"
         "RETURN DISTINCT a.uuid AS uuid, a.type AS type",
         {"uid": "SLD-230103AAA-3"}, ("__scope_path1: every node (a)",)),
    Case("v2.count_derived_from_uid",
         "MATCH (d:Sample)-[:DERIVED_FROM*1..12]->(:Sample {uuid: $uid}) RETURN count(DISTINCT d) AS n",
         {"uid": "MUS-230201BBB-1"}, ("__scope_path1: every node (d)",)),
    Case("v2.assay_then_bounded_chain",
         "MATCH (child:Sample)-[r:DERIVED_FROM]->(parent:Sample)\n"
         "WHERE r.internal_assay_title = $assay\n"
         "MATCH (parent)-[:DERIVED_FROM*0..12]->(anc:T_MUS)\n"
         "RETURN count(DISTINCT anc) AS n",
         {"assay": "Staining"},
         ("child: sample clause", "parent: sample clause", "__scope_path1: every node (parent, anc)")),
    Case("v2.assay_contains",
         "MATCH (child:Sample)-[r:DERIVED_FROM]->(parent:Sample)\n"
         "WHERE toLower(r.internal_assay_title) CONTAINS toLower($term)\n"
         "RETURN count(DISTINCT child) AS n",
         {"term": "stain"}, ("child: sample clause", "parent: sample clause")),
    Case("v2.project_title",
         "MATCH (s:Sample)-[:IN_PROJECT]->(p:Project) WHERE toLower(p.title) = toLower($project) "
         "RETURN count(DISTINCT s) AS n",
         {"project": "project two zqf2"}, ("s: sample clause", "p: project clause")),
    Case("v2.published",
         "MATCH (s:Sample)-[:IN_STUDY]->(st:Study)\n"
         "WHERE coalesce(st.DOI, '') <> '' OR coalesce(st.PMID, '') <> ''\n"
         "RETURN count(DISTINCT s) AS n",
         {}, S, ST_JOINED),
    Case("v2.breakdown_by_type",
         "MATCH (s:Sample) RETURN s.type AS type, count(*) AS n ORDER BY n DESC",
         {}, S),
    Case("v2.distinct_values",
         "MATCH (s:T_SLD) WHERE s.Stain IS NOT NULL RETURN s.Stain AS value, count(*) AS n ORDER BY n DESC",
         {}, S),
    Case("v2.keys_of_samples",
         "MATCH (s:T_SLD) UNWIND keys(s) AS key RETURN key, count(*) AS n ORDER BY n DESC",
         {}, S),
    Case("v2.sample_list",
         "MATCH (s:T_SLD) RETURN s.id AS id, s.uuid AS uuid, s.type AS type ORDER BY id LIMIT 5000",
         {}, S),
    Case("v2.count_subquery",
         "MATCH (s:T_MUS)\n"
         "RETURN s.uuid AS uuid, COUNT { (s)<-[:DERIVED_FROM]-(:Sample) } AS children ORDER BY uuid",
         {}, ("s: sample clause", "__scope_n1: sample clause")),
]

# Accepted shapes the prompts do not teach, which pin a rule of the spec.
ACCEPTED: list[Case] = [
    Case("negative_bound_after_a_function_call",
         "MATCH (s:Sample) WHERE toFloat(s.StorageTemperature) < -70 RETURN count(*) AS n",
         {}, S),
    Case("output_alias_named_like_a_hidden_property",
         "MATCH (s:T_TIS) RETURN s.uuid AS parent_titles ORDER BY parent_titles",
         {}, S),
    Case("whole_node_return",
         "MATCH (s:T_SLD) RETURN s ORDER BY s.uuid",
         {}, S),
    Case("unlabelled_derived_from_endpoint",
         "MATCH (s:T_MUS)<-[:DERIVED_FROM]-(c) RETURN c.uuid AS uuid ORDER BY uuid",
         {}, ("s: sample clause", "c: sample clause")),
    Case("anonymous_parent_named",
         "MATCH (c:T_SLD)-[:DERIVED_FROM]->(:T_TIS) RETURN count(DISTINCT c) AS n",
         {}, ("c: sample clause", "__scope_n1: sample clause")),
    Case("optional_parent_reads_null",
         "MATCH (s:T_TIS {uuid: $uid}) OPTIONAL MATCH (s)-[:DERIVED_FROM]->(parent:Sample) "
         "RETURN s.uuid AS uuid, parent.uuid AS parent",
         {"uid": "TIS-230102AAA-2"}, ("s: sample clause", "parent: sample clause")),
    Case("model_path_name",
         "MATCH p = (s:T_SLD)-[:DERIVED_FROM*1..3]->(m:T_MUS) RETURN length(p) AS hops, m.uuid AS uuid, "
         "[n IN nodes(p) | n.uuid] AS chain",
         {}, ("p: every node (s, m)",)),
    Case("person_member_of_project",
         "MATCH (p:Person)-[:MEMBER_OF]->(proj:Project) RETURN proj.title AS project, count(p) AS people "
         "ORDER BY project",
         {}, ("proj: project clause",), ("p (Person): joined to proj",)),
    Case("investigation_in_project",
         "MATCH (inv:Investigation)-[:IN_PROJECT]->(proj:Project) RETURN inv.title AS t ORDER BY t",
         {}, ("proj: project clause",), ("inv (Investigation): joined to proj",)),
    Case("joined_to_a_reference",
         "MATCH (s:T_TIS) WITH s MATCH (s)-[:IN_STUDY]->(st:Study) RETURN st.title AS t, count(*) AS n ORDER BY t",
         {}, S, ST_JOINED),
    Case("two_samples_in_a_comma_list",
         "MATCH (a:T_MUS), (b:T_TIS) WHERE a.Strain = b.Strain RETURN count(*) AS n",
         {}, ("a: sample clause", "b: sample clause")),
    Case("fulltext_whole_node_and_path",
         "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node\n"
         "MATCH (node)-[r:DERIVED_FROM]->(parent:Sample)\n"
         "RETURN node.uuid AS child, type(r) AS rel, parent.uuid AS parent ORDER BY child, parent",
         {"q": "alpha"}, ("node: sample clause", "parent: sample clause")),
    Case("backticked_variable",
         "MATCH (`my sample`:T_TIS) RETURN `my sample`.uuid AS uuid ORDER BY uuid",
         {}, ("`my sample`: sample clause",)),
    Case("trailing_semicolon",
         "MATCH (s:T_TIS) RETURN count(*) AS n;",
         {}, S),
    Case("line_comment_ending_in_crlf",
         "MATCH (s:T_SLD) // one line\r\nRETURN s.uuid AS uuid ORDER BY uuid",
         {}, S),
]

# The one taught shape that reads the catalog, whose statistics are computed over every project (decision 4).
TAUGHT_REFUSED: list[Refusal] = [
    Refusal("v2.catalog_attributes",
            "MATCH (a:Attribute) WHERE a.sample_type = $type\n"
            "RETURN a.title, a.sample_count, a.declared ORDER BY a.title",
            ("label_not_allowed",), {"type": "MUS"}),
]

# One case per row of spec tables 5.4 and 5.5, and every code of 5.8 the prover emits.
REFUSALS: list[Refusal] = [
    Refusal("too_long", "MATCH (s:Sample) RETURN s.id AS id" + " " * 20_001, ("too_long",)),
    Refusal("too_deep", "MATCH (s:Sample) RETURN " + "(" * 40 + "1" + ")" * 40 + " AS x", ("too_deep",)),
    Refusal("lexer.non_ascii_letter", "MATCH (s:Sample) RETURN s.id AS \u00e9", ("lexer",)),
    Refusal("lexer.non_ascii_space", "MATCH (s:Sample)\u00a0RETURN s.id AS id", ("lexer",)),
    Refusal("lexer.backslash_in_backticked_name",
            "MATCH (s:Sample) WHERE s.`x\\` = 1 RETURN s.id AS id //`", ("lexer",)),
    Refusal("lexer.unterminated_string", "MATCH (s:Sample) WHERE s.x = 'abc RETURN s.id AS id", ("lexer",)),
    Refusal("lexer.unterminated_comment", "MATCH (s:Sample) /* RETURN s.id AS id", ("lexer",)),
    Refusal("lexer.carriage_return_in_line_comment", "MATCH (s:Sample) // note\r MATCH (x)\nRETURN *", ("lexer",)),
    Refusal("lexer.backticked_parameter", "MATCH (s:Sample) WHERE s.x = $`p` RETURN s.id AS id", ("lexer",)),
    Refusal("lexer.stray_character", "MATCH (s:Sample) WHERE s.x = 1 # RETURN s.id AS id", ("lexer",)),
    Refusal("syntax.no_return", "MATCH (s:Sample)", ("syntax",)),
    Refusal("syntax.filter", "MATCH (s:Sample) FILTER s.x = 1 RETURN s.id AS id", ("syntax",)),
    Refusal("syntax.let", "MATCH (s:Sample) LET y = s.x RETURN y", ("syntax",)),
    Refusal("syntax.next", "MATCH (s:Sample) RETURN s.id AS id NEXT MATCH (t:Sample) RETURN t.id AS id",
            ("syntax",)),
    Refusal("syntax.when", "WHEN true THEN MATCH (s:Sample) RETURN s.id AS id", ("syntax",)),
    Refusal("syntax.finish", "MATCH (s:Sample) FINISH", ("syntax",)),
    Refusal("syntax.two_statements", "MATCH (s:Sample) RETURN s.id AS id; MATCH (t:Sample) RETURN t.id AS id",
            ("syntax",)),
    Refusal("syntax.unknown_variable", "MATCH (s:Sample) RETURN t.id AS id", ("syntax",)),
    Refusal("syntax.write_clause", "MATCH (s:Sample) SET s.x = 1 RETURN s.id AS id", ("syntax",)),
    Refusal("syntax.parameter_map_in_pattern", "MATCH (s:Sample $props) RETURN s.id AS id", ("syntax",)),
    Refusal("reserved_name.variable", "MATCH (__scope_x:Sample) RETURN __scope_x.id AS id", ("reserved_name",)),
    Refusal("reserved_name.parameter_in_text",
            "MATCH (s:Sample) WHERE s.id IN $__scope_projects RETURN s.id AS id", ("reserved_name",)),
    Refusal("reserved_name.case_insensitive", "MATCH (s:Sample) RETURN s.id AS __Scope_id", ("reserved_name",)),
    Refusal("reserved_name.backticked", "MATCH (s:Sample) RETURN s.`__scope_x` AS x", ("reserved_name",)),
    Refusal("reserved_parameter", "MATCH (s:Sample) RETURN s.id AS id", ("reserved_parameter",),
            {"__scope_projects": [1, 2, 3]}),
    Refusal("reserved_parameter.case_insensitive", "MATCH (s:Sample) RETURN s.id AS id", ("reserved_parameter",),
            {"__SCOPE_x": 1}),
    Refusal("query_prefix.explain", "EXPLAIN MATCH (s:Sample) RETURN s.id AS id", ("query_prefix",)),
    Refusal("query_prefix.profile", "PROFILE MATCH (s:Sample) RETURN s.id AS id", ("query_prefix",)),
    Refusal("query_prefix.cypher", "CYPHER runtime=slotted MATCH (s:Sample) RETURN s.id AS id", ("query_prefix",)),
    Refusal("union", "MATCH (s:T_MUS) RETURN s.uuid AS u UNION MATCH (t:T_SLD) RETURN t.uuid AS u", ("union",)),
    Refusal("union_all", "MATCH (s:T_MUS) RETURN s.uuid AS u UNION ALL MATCH (t:T_SLD) RETURN t.uuid AS u",
            ("union",)),
    Refusal("call_subquery", "MATCH (s:Sample) CALL { MATCH (x) RETURN x } RETURN s.id AS id", ("call_subquery",)),
    Refusal("call_subquery.scoped",
            "MATCH (s:Sample) CALL (s) { MATCH (s)-->(x) RETURN x } RETURN s.id AS id", ("call_subquery",)),
    Refusal("collect_subquery",
            "MATCH (s:Sample) RETURN COLLECT { MATCH (s)<-[:DERIVED_FROM]-(c:Sample) RETURN c.uuid } AS kids",
            ("collect_subquery",)),
    Refusal("procedure.apoc_path",
            "MATCH (s:Sample {uuid: $uid}) CALL apoc.path.subgraphNodes(s, {maxLevel: 2, relationshipFilter: "
            "'DERIVED_FROM>'}) YIELD node RETURN node.uuid AS uuid", ("procedure",)),
    Refusal("procedure.apoc_spanning_tree",
            "MATCH (s:Sample) CALL apoc.path.spanningTree(s, {maxLevel: 2}) YIELD path RETURN length(path) AS n",
            ("procedure",)),
    Refusal("procedure.db", "CALL db.labels() YIELD label RETURN label", ("procedure",)),
    Refusal("procedure.fulltext_in_subquery",
            "MATCH (s:Sample) WHERE EXISTS { CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node "
            "WHERE node = s } RETURN s.uuid AS u", ("procedure",), {"q": "alpha"}),
    Refusal("procedure.fulltext_in_count_subquery",
            "MATCH (s:Sample) RETURN s.uuid AS u, COUNT { MATCH (s)-[:IN_STUDY]->(st:Study) "
            "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD node } AS n", ("procedure",),
            {"q": "alpha"}),
    Refusal("fulltext_form.yield_star",
            "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD * RETURN node.uuid AS u",
            ("fulltext_form",)),
    Refusal("fulltext_form.no_node",
            "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD score RETURN count(*) AS n",
            ("fulltext_form",)),
    Refusal("fulltext_form.options",
            "CALL db.index.fulltext.queryNodes('sample_search_text', $q, {limit: 5}) YIELD node RETURN node.uuid AS u",
            ("fulltext_form",)),
    Refusal("fulltext_form.other_index",
            "CALL db.index.fulltext.queryNodes('other_index', $q) YIELD node RETURN node.uuid AS u",
            ("fulltext_form",)),
    Refusal("fulltext_form.parameter_index",
            "CALL db.index.fulltext.queryNodes($index, $q) YIELD node RETURN node.uuid AS u", ("fulltext_form",)),
    Refusal("fulltext_form.no_yield",
            "CALL db.index.fulltext.queryNodes('sample_search_text', $q) RETURN 1 AS x", ("fulltext_form",)),
    Refusal("pattern_expression.predicate",
            "MATCH (s:Sample) WHERE (s)-[:DERIVED_FROM]->(:Sample) RETURN s.id AS id", ("pattern_expression",)),
    Refusal("pattern_expression.short_arrows",
            "MATCH (s:Sample) WHERE (s)-->() RETURN s.id AS id", ("pattern_expression",)),
    Refusal("pattern_expression.undirected_anonymous",
            "MATCH (s:Sample) WHERE (s)-[]-({title: 'x'}) RETURN s.id AS id", ("pattern_expression",)),
    Refusal("pattern_expression.spaced_arrows",
            "MATCH (s:Sample) WHERE NOT (s) < - [:DERIVED_FROM] - (:Sample) RETURN s.id AS id",
            ("pattern_expression",)),
    Refusal("pattern_expression.comprehension",
            "MATCH (s:Sample) RETURN [(s)<-[:DERIVED_FROM]-(c) | c.uuid] AS kids", ("pattern_expression",)),
    Refusal("pattern_expression.in_function",
            "MATCH (s:Sample) WHERE size((s)<--()) > 0 RETURN s.id AS id", ("pattern_expression",)),
    Refusal("pattern_expression.character_pair",
            "MATCH (s:Sample) WHERE s.x<-1 RETURN s.id AS id", ("pattern_expression",)),
    Refusal("inline_where.node", "MATCH (s:Sample WHERE s.x = 1) RETURN s.id AS id", ("inline_where",)),
    Refusal("inline_where.relationship",
            "MATCH (s:Sample)-[r:DERIVED_FROM WHERE r.x = 1]->(p:Sample) RETURN s.id AS id", ("inline_where",)),
    Refusal("label_not_allowed.sample_type", "MATCH (t:SampleType) RETURN t.title AS t", ("label_not_allowed",)),
    Refusal("label_not_allowed.graph_meta", "MATCH (g:GraphMeta) RETURN g.catalog_hash AS h", ("label_not_allowed",)),
    Refusal("label_not_allowed.orphan", "MATCH (o:OrphanSample) RETURN o.uuid AS u", ("label_not_allowed",)),
    Refusal("label_not_allowed.unknown", "MATCH (x:Unknown) RETURN x.id AS id", ("label_not_allowed",)),
    Refusal("label_not_allowed.backticked", "MATCH (a:`Attribute`) RETURN a.title AS t", ("label_not_allowed",)),
    Refusal("label_not_allowed.two_kinds", "MATCH (x:Sample:Study) RETURN x.id AS id", ("label_not_allowed",)),
    Refusal("label_not_allowed.two_kinds_across_occurrences",
            "MATCH (x:Sample)-[:IN_STUDY]->(st:Study), (x:Study) RETURN x.id AS id", ("label_not_allowed",)),
    Refusal("label_expression.disjunction_in_pattern", "MATCH (s:T_SLD|T_TIS) RETURN count(*) AS n",
            ("label_expression",)),
    Refusal("label_expression.negation", "MATCH (s:!Study) RETURN s.id AS id", ("label_expression",)),
    Refusal("label_expression.wildcard", "MATCH (s:%) RETURN s.id AS id", ("label_expression",)),
    Refusal("label_expression.dynamic", "MATCH (s:$($label)) RETURN s.id AS id", ("label_expression",)),
    Refusal("label_expression.is", "MATCH (s IS Sample) RETURN s.id AS id", ("label_expression",)),
    Refusal("unlabelled_node.alone", "MATCH (n) RETURN count(n) AS c", ("unlabelled_node",)),
    Refusal("unlabelled_node.study_end", "MATCH (s:Sample)-[:IN_STUDY]->(x) RETURN x.title AS t",
            ("unlabelled_node",)),
    Refusal("unlabelled_node.in_subquery",
            "MATCH (s:Sample) WHERE EXISTS { (s)-[:IN_STUDY]->() } RETURN s.id AS id", ("unlabelled_node",)),
    Refusal("unjoined_node.study", "MATCH (st:Study) RETURN st.title AS t", ("unjoined_node",)),
    Refusal("unjoined_node.comma_is_not_a_join",
            "MATCH (s:Sample), (st:Study) RETURN s.id AS id, st.title AS t", ("unjoined_node",)),
    Refusal("unjoined_node.study_to_investigation",
            "MATCH (st:Study)-[:IN_INVESTIGATION]->(inv:Investigation) RETURN inv.title AS t", ("unjoined_node",)),
    Refusal("unjoined_node.person", "MATCH (p:Person) RETURN count(p) AS n", ("unjoined_node",)),
    Refusal("unjoined_node.sibling_study",
            "MATCH (s:Sample)-[:IN_STUDY]->(st:Study)-[:IN_INVESTIGATION]->(inv:Investigation)"
            "<-[:IN_INVESTIGATION]-(other:Study) RETURN other.title AS t", ("unjoined_node",)),
    Refusal("unjoined_node.sibling_study_through_a_bound_name",
            "MATCH (s:Sample)-[:IN_STUDY]->(st:Study)-[:IN_INVESTIGATION]->(inv:Investigation) WITH inv "
            "MATCH (inv)<-[:IN_INVESTIGATION]-(other:Study) RETURN other.title AS t", ("unjoined_node",)),
    Refusal("unjoined_node.study_of_a_project_investigation",
            "MATCH (st:Study)-[:IN_INVESTIGATION]->(inv:Investigation)-[:IN_PROJECT]->(p:Project) "
            "RETURN st.title AS t", ("unjoined_node",)),
    Refusal("unjoined_node.study_of_a_project_investigation_from_the_project",
            "MATCH (p:Project)<-[:IN_PROJECT]-(i:Investigation)<-[:IN_INVESTIGATION]-(st:Study) "
            "RETURN st.title AS t", ("unjoined_node",)),
    Refusal("unjoined_node.person_by_a_study",
            "MATCH (s:Sample)-[:IN_STUDY]->(st:Study)-[:MEMBER_OF]-(p:Person) RETURN p.id AS id", ("unjoined_node",)),
    Refusal("relationship_type.untyped_short", "MATCH (s:Sample)-->(p:Sample) RETURN p.id AS id",
            ("relationship_type",)),
    Refusal("relationship_type.untyped_named", "MATCH (s:Sample)-[r]->(p:Sample) RETURN p.id AS id",
            ("relationship_type",)),
    Refusal("relationship_type.alternation", "MATCH (s:Sample)-[:DERIVED_FROM|IN_STUDY]->(p:Sample) RETURN p.id AS id",
            ("relationship_type",)),
    Refusal("relationship_type.of_type", "MATCH (s:Sample)-[:OF_TYPE]->(t:SampleType) RETURN t.title AS t",
            ("relationship_type", "label_not_allowed")),
    Refusal("relationship_type.has_attribute",
            "MATCH (t:SampleType)-[:HAS_ATTRIBUTE]->(a:Attribute) RETURN a.title AS t",
            ("relationship_type", "label_not_allowed")),
    Refusal("relationship_type.used_in", "MATCH (s:Sample)-[:USED_IN]->(p:Sample) RETURN p.id AS id",
            ("relationship_type",)),
    Refusal("relationship_type.untyped_variable_length", "MATCH (s:Sample)-[*1..3]->(p:Sample) RETURN p.id AS id",
            ("relationship_type",)),
    Refusal("variable_length.other_type", "MATCH (s:Sample)-[:IN_STUDY*1..2]->(st:Study) RETURN st.title AS t",
            ("variable_length",)),
    Refusal("variable_length.mixed_part",
            "MATCH (st:Study)<-[:IN_STUDY]-(s:Sample)-[:DERIVED_FROM*1..3]->(p:Sample) RETURN st.title AS t",
            ("variable_length",)),
    Refusal("path_selector.shortest_path",
            "MATCH p = shortestPath((a:Sample)-[:DERIVED_FROM*]-(b:Sample)) RETURN length(p) AS n",
            ("path_selector",)),
    Refusal("path_selector.all_shortest_paths",
            "MATCH p = allShortestPaths((a:Sample)-[:DERIVED_FROM*]-(b:Sample)) RETURN length(p) AS n",
            ("path_selector",)),
    Refusal("path_selector.shortest_keyword",
            "MATCH SHORTEST 1 (a:Sample)-[:DERIVED_FROM]-+(b:Sample) RETURN a.id AS id", ("path_selector",)),
    Refusal("path_selector.any_keyword",
            "MATCH ANY (a:Sample)-[:DERIVED_FROM]->{1,3}(b:Sample) RETURN a.id AS id", ("path_selector",)),
    Refusal("path_selector.quantified_path",
            "MATCH ((a:Sample)-[:DERIVED_FROM]->(b:Sample)){1,3} RETURN count(*) AS n", ("path_selector",)),
    Refusal("path_selector.match_mode",
            "MATCH REPEATABLE ELEMENTS (a:Sample)-[:DERIVED_FROM]->(b:Sample) RETURN a.id AS id",
            ("path_selector",)),
    Refusal("path_selector.in_expression",
            "MATCH (a:Sample), (b:Sample) RETURN length(shortestPath((a)-[:DERIVED_FROM*]-(b))) AS n",
            ("path_selector",)),
    Refusal("hidden_property.access", "MATCH (s:Sample) RETURN s.parent_titles AS p", ("hidden_property",)),
    Refusal("hidden_property.backticked", "MATCH (s:Sample) RETURN s.`parent_titles` AS p", ("hidden_property",)),
    Refusal("hidden_property.projection", "MATCH (s:Sample) RETURN s {.uuid, .parent_title_hashes} AS m",
            ("hidden_property",)),
    Refusal("hidden_property.pattern_key", "MATCH (s:Sample {parent_titles: $x}) RETURN s.id AS id",
            ("hidden_property",)),
    Refusal("hidden_property.in_predicate", "MATCH (s:Sample) WHERE $t IN s.parent_titles RETURN s.id AS id",
            ("hidden_property",)),
    Refusal("dynamic_property.parameter", "MATCH (s:Sample) RETURN s[$k] AS v", ("dynamic_property",)),
    Refusal("dynamic_property.string", "MATCH (s:Sample) RETURN s['parent_titles'] AS v", ("dynamic_property",)),
    Refusal("dynamic_property.variable", "MATCH (s:Sample) WITH s, 'uuid' AS k RETURN s[k] AS v",
            ("dynamic_property",)),
    Refusal("whole_properties.function", "MATCH (s:Sample) RETURN properties(s) AS p", ("whole_properties",)),
    Refusal("whole_properties.star_projection", "MATCH (s:Sample) RETURN s {.*} AS p", ("whole_properties",)),
    Refusal("function_not_allowed.apoc_convert", "MATCH (s:Sample) RETURN apoc.convert.toJson(s) AS j",
            ("function_not_allowed",)),
    Refusal("function_not_allowed.apoc_node", "MATCH (s:Sample) RETURN apoc.node.degree(s) AS d",
            ("function_not_allowed",)),
    Refusal("function_not_allowed.apoc_sort_nodes_by_a_named_property",
            "MATCH (s:Sample) RETURN apoc.coll.sortNodes(collect(s), $k) AS n", ("function_not_allowed",),
            {"k": "uuid"}),
    Refusal("function_not_allowed.apoc_sort_maps_by_a_named_property",
            "MATCH (s:Sample) RETURN apoc.coll.sortMaps(collect(s), $k) AS n", ("function_not_allowed",),
            {"k": "uuid"}),
    Refusal("function_not_allowed.apoc_sort_multi_by_named_properties",
            "MATCH (s:Sample) RETURN apoc.coll.sortMulti(collect(s), [$k]) AS n", ("function_not_allowed",),
            {"k": "uuid"}),
    Refusal("function_not_allowed.exists", "MATCH (s:Sample) WHERE exists(s.x) RETURN s.id AS id",
            ("function_not_allowed",)),
    Refusal("function_not_allowed.db", "MATCH (s:Sample) RETURN db.nameFromElementId(elementId(s)) AS d",
            ("function_not_allowed",)),
    Refusal("function_not_allowed.vector", "MATCH (s:Sample) RETURN vector.similarity.cosine([1.0], [1.0]) AS v",
            ("function_not_allowed",)),
    Refusal("function_not_allowed.unknown", "RETURN randomUUID() AS u", ("function_not_allowed",)),
]

# --------------------------------------------------------------------------- #
# The same constructs, hidden
# --------------------------------------------------------------------------- #

# (code, a predicate over the variable {v}) for constructs that live in an expression. New nodes are named q, which
# no placement binds: in the nested placement x and y are bound by the enclosing EXISTS, so a (x) there is a reference.
HIDDEN_EXPRESSIONS: list[tuple[str, str]] = [
    ("pattern_expression", "({v})-[:DERIVED_FROM]->(:Sample)"),
    ("pattern_expression", "size([({v})<-[:DERIVED_FROM]-(c) | c.uuid]) > 0"),
    ("collect_subquery", "size(COLLECT {{ MATCH ({v})<-[:DERIVED_FROM]-(c:Sample) RETURN c.uuid }}) > 0"),
    ("call_subquery", "EXISTS {{ MATCH ({v})-[:DERIVED_FROM]->(c:Sample) CALL {{ RETURN 1 AS one }} }}"),
    ("hidden_property", "$t IN {v}.parent_titles"),
    ("hidden_property", "{v} {{.parent_title_hashes}} IS NOT NULL"),
    ("dynamic_property", "{v}[$k] = 1"),
    ("whole_properties", "size(keys(properties({v}))) > 0"),
    ("whole_properties", "{v} {{.*}} IS NOT NULL"),
    ("function_not_allowed", "apoc.node.degree({v}) > 0"),
    ("function_not_allowed", "exists({v}.x)"),
    ("path_selector", "length(shortestPath(({v})-[:DERIVED_FROM*]-({v}))) > 0"),
    ("label_not_allowed", "EXISTS {{ MATCH ({v})-[:DERIVED_FROM]->(a:Attribute) }}"),
    ("unlabelled_node", "EXISTS {{ MATCH ({v})-[:IN_STUDY]->(q) }}"),
    ("unjoined_node", "EXISTS {{ MATCH (st:Study) WHERE st.title = {v}.title }}"),
    ("relationship_type", "EXISTS {{ MATCH ({v})-->(q:Sample) }}"),
    ("variable_length", "EXISTS {{ MATCH ({v})-[:IN_STUDY*1..2]->(st:Study) }}"),
    ("label_expression", "EXISTS {{ MATCH ({v})-[:DERIVED_FROM]->(q:T_A|T_B) }}"),
    ("inline_where", "EXISTS {{ MATCH ({v})-[:DERIVED_FROM]->(q:Sample WHERE q.y = 1) }}"),
]

# Clause-level constructs (code, the text) for the comment, literal and backticked-name placements.
HIDDEN_CLAUSES: list[tuple[str, str]] = [
    ("union", "UNION ALL MATCH (n) RETURN n"),
    ("call_subquery", "CALL { MATCH (n) RETURN n }"),
    ("procedure", "CALL apoc.path.subgraphNodes(s, {maxLevel: 3}) YIELD node"),
    ("query_prefix", "PROFILE"),
    ("syntax", "NEXT MATCH (n) RETURN n"),
    ("fulltext_form", "CALL db.index.fulltext.queryNodes('sample_search_text', $q) YIELD *"),
]


def _cypher_string(text: str) -> str:
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _backticked(text: str) -> str:
    return "`" + text.replace("`", "``") + "`"


def hidden_variants() -> list[tuple[str, str, tuple[str, ...]]]:
    """(id, cypher, expected codes); () means the statement must be accepted."""
    out: list[tuple[str, str, tuple[str, ...]]] = []
    for k, (code, template) in enumerate(HIDDEN_EXPRESSIONS):
        real = template.format(v="s")
        out.append((f"expr{k}.{code}.direct", f"MATCH (s:Sample) WHERE {real} RETURN s.id AS id", (code,)))
        nested = template.format(v="y")
        out.append((f"expr{k}.{code}.nested_exists",
                    "MATCH (s:Sample) WHERE EXISTS { MATCH (s)-[:DERIVED_FROM]->(x:Sample) WHERE EXISTS { "
                    f"MATCH (x)-[:DERIVED_FROM]->(y:Sample) WHERE {nested} }} }} RETURN s.id AS id", (code,)))
        chained = template.format(v="u")
        out.append((f"expr{k}.{code}.with_chain",
                    f"MATCH (s:Sample) WITH s AS t WITH t AS u WHERE {chained} RETURN u.id AS id", (code,)))
        out.append((f"expr{k}.{code}.block_comment",
                    f"MATCH (s:Sample) /* WHERE {real} */ RETURN s.id AS id", ()))
        out.append((f"expr{k}.{code}.line_comment",
                    f"MATCH (s:Sample) // WHERE {real}\nRETURN s.id AS id", ()))
        out.append((f"expr{k}.{code}.string_literal",
                    f"MATCH (s:Sample) WHERE s.note = {_cypher_string(real)} RETURN s.id AS id", ()))
        out.append((f"expr{k}.{code}.backticked_name",
                    f"MATCH (s:Sample) RETURN s.id AS {_backticked(real)}", ()))
    for k, (code, text) in enumerate(HIDDEN_CLAUSES):
        out.append((f"clause{k}.{code}.block_comment",
                    f"MATCH (s:Sample) /* {text} */ RETURN s.id AS id", ()))
        out.append((f"clause{k}.{code}.line_comment",
                    f"MATCH (s:Sample) // {text}\nRETURN s.id AS id", ()))
        out.append((f"clause{k}.{code}.string_literal",
                    f"MATCH (s:Sample) WHERE s.note = {_cypher_string(text)} RETURN s.id AS id", ()))
        out.append((f"clause{k}.{code}.backticked_name",
                    f"MATCH (s:Sample) RETURN s.id AS {_backticked(text)}", ()))
    # After a WITH alias chain, the clause-level constructs still refuse.
    out += [
        ("clause.union.with_chain",
         "MATCH (s:Sample) WITH s AS t WITH t AS u RETURN u.id AS id UNION MATCH (x:Sample) RETURN x.id AS id",
         ("union",)),
        ("clause.call_subquery.with_chain",
         "MATCH (s:Sample) WITH s AS t WITH t AS u CALL { MATCH (n) RETURN n } RETURN u.id AS id",
         ("call_subquery",)),
        ("clause.procedure.with_chain",
         "MATCH (s:Sample) WITH s AS t WITH t AS u CALL apoc.path.subgraphNodes(u, {maxLevel: 3}) YIELD node "
         "RETURN node.uuid AS uuid", ("procedure",)),
        ("clause.unjoined.with_chain",
         "MATCH (s:Sample) WITH s AS t WITH t AS u MATCH (st:Study) RETURN u.id AS id, st.title AS t",
         ("unjoined_node",)),
        ("clause.joined_to_alias.with_chain",
         "MATCH (s:Sample) WITH s AS t WITH t AS u MATCH (u)-[:IN_STUDY]->(st:Study) RETURN st.title AS t", ()),
    ]
    return out


# --------------------------------------------------------------------------- #
# Writes: refused by write_clause before the prover, for every caller
# --------------------------------------------------------------------------- #

WRITES: list[str] = [
    "CREATE (n:Sample {uuid: 'W1', project_ids: [1]}) RETURN n.uuid AS u",
    "MERGE (n:Sample {uuid: 'W2'}) RETURN n.uuid AS u",
    "MATCH (s:Sample) SET s.Organ = 'X' RETURN count(*) AS n",
    "MATCH (s:Sample) SET s:Extra RETURN count(*) AS n",
    "MATCH (s:Sample) REMOVE s.Organ RETURN count(*) AS n",
    "MATCH (s:Sample) DELETE s",
    "MATCH (s:Sample) DETACH DELETE s",
    "LOAD CSV FROM 'file:///x.csv' AS row CREATE (:Sample {uuid: row[0]})",
    "MATCH (s:Sample) FOREACH (x IN [1] | SET s.flag = x)",
    "MATCH (s:Sample) CALL (s) { SET s.flag = 1 } IN TRANSACTIONS RETURN count(*) AS n",
    "CALL apoc.create.node(['Sample'], {uuid: 'W3'}) YIELD node RETURN node",
    "CALL db.createLabel('Extra')",
    "CREATE INDEX extra_index FOR (n:Sample) ON (n.flag)",
    "INSERT (n:Sample {uuid: 'W5'})",
    "MATCH (s:Sample) WHERE s.`x\\` = 1 CREATE (n:Sample {uuid: 'W4'}) RETURN 1 //`",
]


# --------------------------------------------------------------------------- #
# The report runners' two statements, captured through a patched tool
# --------------------------------------------------------------------------- #

def report_statements(tmp_dir) -> list[tuple[str, dict]]:
    """Run both report builders with the Neo4j tool patched; return every (cypher, parameters) they would run."""
    from types import SimpleNamespace
    from unittest import mock

    from chat_nextseek.reports import runners

    captured: list[tuple[str, dict]] = []

    def fake_tool(config, cypher, parameters=None):
        captured.append((cypher, dict(parameters or {})))
        return {"ok": True, "data": [], "count": 0}

    config = SimpleNamespace(is_umbrella_published_project=None)
    with mock.patch.object(runners, "tool_neo4j_query", fake_tool), \
            mock.patch.object(runners, "_resolve_report_scope", lambda cfg, project: ("project", 2)), \
            mock.patch.object(runners, "live_db_conn", lambda *a, **k: None):
        runners._neo4j_investigation_sample_uuids(config, "Inv one", years=[2023],
                                                   month_range=("2023-01", "2023-03"))
        runners._neo4j_investigation_sample_uuids(config, "Inv two")
        try:
            runners.run_project_published_report(config, project="Inv", years=[2023],
                                                  day_range=("2023-01-01", "2023-12-31"), outputs_root=tmp_dir)
        except Exception:
            pass  # only the statement it would have run matters here
    return captured

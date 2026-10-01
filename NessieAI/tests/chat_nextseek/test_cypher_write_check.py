"""
D3: the Cypher write check runs on masked text and allows exactly one procedure.

The old check was a regex over the raw text. It refused the read-only fulltext
procedure `db.index.fulltext.queryNodes` (graph_search's own index) and any literal
containing SET or DELETE (`'Data Set'`), and it let `CALL apoc.refactor.*` through.
`write_clause` masks literals, backticked names, parameters and comments first, so
only real clauses are seen, and refuses every procedure but the fulltext one.

The check is the first line only: `tool_neo4j_query` also runs every statement in a
READ transaction, so the server refuses a write the check misses
(test_neo4j_read_mode.py).
"""
from __future__ import annotations

import pytest

from chat_nextseek.cypher_text import ALLOWED_PROCEDURES, mask_cypher, write_clause


def test_exactly_one_procedure_is_allowed():
    assert ALLOWED_PROCEDURES == frozenset({"db.index.fulltext.queryNodes"})


# --------------------------------------------------------------------------- #
# Refused, with the offending clause named
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "cypher, clause",
    [
        ("CREATE (n:Sample {id: 1})", "CREATE"),
        ("MERGE (n:Sample {id: 1}) RETURN n.id", "MERGE"),
        ("MATCH (s:Sample) SET s.Organ = 'Lung'", "SET"),
        ("MATCH (s:Sample) DELETE s", "DELETE"),
        ("MATCH (s:Sample) DETACH DELETE s", "DETACH DELETE"),
        ("MATCH (s:Sample)\nDETACH\n  DELETE s", "DETACH DELETE"),
        ("MATCH (s:Sample) REMOVE s.Organ", "REMOVE"),
        ("DROP INDEX sample_search", "DROP"),
        ("LOAD CSV FROM 'file:///x.csv' AS row RETURN row", "LOAD CSV"),
        ("MATCH (s:Sample) FOREACH (x IN [1] | SET s.n = x)", "FOREACH"),
        ("SHOW USERS", "SHOW"),
        ("USE system MATCH (n) RETURN count(n)", "USE"),
        ("GRANT TRAVERSE ON GRAPH * TO reader", "GRANT"),
        # lower case is still a clause
        ("match (s:Sample) set s.x = 1", "SET"),
        ("match (s) detach delete s", "DETACH DELETE"),
    ],
)
def test_write_and_admin_clauses_are_refused(cypher, clause):
    assert write_clause(cypher) == clause


@pytest.mark.parametrize(
    "cypher, clause",
    [
        (
            "MATCH (a:Sample {id: 1}), (b:Sample {id: 2}) "
            "CALL apoc.refactor.mergeNodes([a, b]) YIELD node RETURN node.id",
            "CALL apoc.refactor.mergeNodes",
        ),
        ("CALL dbms.listConfig()", "CALL dbms.listConfig"),
        ("CALL db.labels()", "CALL db.labels"),
        ("CALL db.labels", "CALL db.labels"),
        ("CALL db.index.fulltext.createNodeIndex('x', ['Sample'], ['search_text'])",
         "CALL db.index.fulltext.createNodeIndex"),
        # a longer name that merely starts with the allowed one
        ("CALL db.index.fulltext.queryNodesX('x', 'y')", "CALL db.index.fulltext.queryNodesX"),
        ("CALL apoc.periodic.iterate('MATCH (n) RETURN n', 'DETACH DELETE n', {})",
         "CALL apoc.periodic.iterate"),
    ],
)
def test_every_procedure_but_the_fulltext_one_is_refused(cypher, clause):
    assert write_clause(cypher) == clause


@pytest.mark.parametrize(
    "cypher",
    [
        "CALL `db`.`labels`()",
        "CALL `apoc.refactor.mergeNodes`([a, b]) YIELD node RETURN node.id",
        "CALL `db.index.fulltext.queryNodes`('sample_search', 'lung') YIELD node RETURN node.id",
        "CALL db.`index`.fulltext.queryNodes('sample_search', 'lung') YIELD node RETURN node.id",
        "MATCH (s) CALL `dbms`.listConfig() YIELD name RETURN name",
    ],
)
def test_a_backticked_procedure_name_is_refused(cypher):
    """Masking blanks backticked names, so the name cannot be checked: refuse it."""
    clause = write_clause(cypher)
    assert clause is not None
    assert clause.startswith("CALL")


@pytest.mark.parametrize(
    "cypher, clause",
    [
        ("CALL () { CREATE (n:Sample {id: 1}) RETURN n } RETURN n.id", "CREATE"),
        ("MATCH (s:Sample) CALL (s) { SET s.flag = true } RETURN s.id", "SET"),
        ("CALL { MATCH (s:Sample) DETACH DELETE s }", "DETACH DELETE"),
        ("CALL { MATCH (s) CALL db.labels() YIELD label RETURN label } RETURN label", "CALL db.labels"),
    ],
)
def test_a_subquery_body_is_checked(cypher, clause):
    assert write_clause(cypher) == clause


def test_the_first_offending_clause_is_named():
    assert write_clause("MATCH (s) SET s.a = 1 WITH s DELETE s") == "SET"


def test_call_in_transactions_is_refused():
    """Batched writes; it cannot run inside a READ transaction anyway."""
    cypher = "MATCH (s:Sample) CALL (s) { RETURN s.id AS id } IN TRANSACTIONS OF 10 ROWS RETURN id"
    assert write_clause(cypher) == "IN TRANSACTIONS"


# --------------------------------------------------------------------------- #
# Passed
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "cypher",
    [
        # graph_search's own fulltext index: read-only, and now legal
        "CALL db.index.fulltext.queryNodes('sample_search', $q) YIELD node, score "
        "RETURN node.id, score LIMIT 10",
        "CALL db.index.fulltext.queryNodes(\"sample_search\", 'lung AND mouse') YIELD node "
        "WHERE node:T_TIS RETURN count(node) AS n",
        # subqueries, old and new import syntax
        "CALL () { MATCH (s:T_TIS) RETURN s.id AS id } RETURN count(id) AS n",
        "MATCH (s:Sample) CALL (s) { MATCH (s)-[:DERIVED_FROM]->(p) RETURN count(p) AS n } RETURN s.id, n",
        "MATCH (s:Sample) CALL (s, t) { RETURN 1 AS one } RETURN s.id",
        "CALL { MATCH (s:Sample) RETURN s.id AS id } RETURN id LIMIT 5",
        "CALL {\n  MATCH (s:Sample) RETURN s.id AS id\n  UNION\n  MATCH (s:OrphanSample) RETURN s.id AS id\n} RETURN id",
        # the total probe's own shape
        "CALL () {\nMATCH (s:Sample) RETURN DISTINCT s.id AS id\n}\nRETURN count(*) AS __total",
        # literals, backticked properties and comments that contain keywords
        "MATCH (s:Sample) WHERE s.type = 'Data Set' RETURN s.id",
        "MATCH (s:Sample) WHERE s.Notes CONTAINS \"CREATE a DELETE\" RETURN s.id",
        "MATCH (s:Sample) WHERE s.`SET_ID` IS NOT NULL RETURN s.`SET_ID`",
        "MATCH (s:Sample) WHERE s.`Set` = 'x' RETURN s.`Delete Flag`",
        "MATCH (s:Sample) // DELETE nothing here\nRETURN count(s)",
        "MATCH (s:Sample) /* DETACH DELETE s; CALL db.labels() */ RETURN count(s)",
        # a property whose bare name is a keyword
        "MATCH (s:Sample) WHERE s.Set = 'x' AND s.use IS NULL RETURN s.show",
        # names that merely contain a keyword
        "MATCH (s:Sample) WHERE s.created_at > $d RETURN s.SET_ID, s.Merged, s.REMOVED",
        "MATCH (s:T_SET) RETURN s.id",
        # ordinary reads
        "MATCH (s:Sample)-[:IN_STUDY]->(st:Study)\nOPTIONAL MATCH (st)-[:IN_INVESTIGATION]->(inv:Investigation)\n"
        "WITH s, st, inv WHERE toLower(st.title) CONTAINS toLower($project)\n"
        "RETURN DISTINCT s.id AS id, s.uuid AS uuid, s.type AS type ORDER BY id SKIP 0 LIMIT 250",
        "MATCH (s:Sample {type: 'D.SEQ'}) WHERE s.uuid IN $uids RETURN s.id",
        "MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample) WHERE r.internal_assay_title = 'RNA extraction' "
        "RETURN p.type, c.type, count(*) AS n",
        "MATCH (s:T_TIS) RETURN date.truncate('month', s.Collection_date) AS m, count(*) AS n",
        "MATCH (s:Sample) WHERE EXISTS { MATCH (s)-[:DERIVED_FROM]->(:T_NHP) } RETURN count(s) AS n",
        "MATCH (s:Sample) RETURN s {.id, .uuid, .Organ} LIMIT 5",
        "RETURN $create AS x, $set AS y",
        "",
    ],
)
def test_reads_pass(cypher):
    assert write_clause(cypher) is None


def test_none_passes():
    assert write_clause(None) is None


def test_the_check_masks_the_text_itself():
    """Callers pass raw text; masking an already masked text changes nothing."""
    cypher = "MATCH (s) WHERE s.type = 'Data Set' RETURN s.id"
    assert write_clause(cypher) is None
    assert write_clause(mask_cypher(cypher)) is None
    assert mask_cypher(mask_cypher(cypher)) == mask_cypher(cypher)


# --------------------------------------------------------------------------- #
# mask_cypher: moved from agents/graph.py::_mask_cypher, behaviour unchanged
# --------------------------------------------------------------------------- #

MASK_SAMPLES = [
    "MATCH (s:Sample) WHERE s.type = 'D.SEQ' RETURN s.id",
    'MATCH (s) WHERE s.Organ = "Lung" AND s.x = $param_1 RETURN s.`Catalog#`',
    "MATCH (s) // a comment with 'quotes'\nRETURN s /* block\ncomment */ LIMIT 5",
    "MATCH (s) WHERE s.Notes = 'it\\'s' RETURN s.id",
    "MATCH (s) WHERE s.a = 'unterminated",
    "/* unterminated block",
    "",
]


@pytest.mark.parametrize("cypher", MASK_SAMPLES)
def test_mask_keeps_every_offset(cypher):
    assert len(mask_cypher(cypher)) == len(cypher)


def test_mask_blanks_literals_backticks_params_and_comments():
    cypher = "MATCH (s) WHERE s.`Set` = 'Data Set' AND s.b = $delete // DROP\nRETURN s.id /* SET */"
    masked = mask_cypher(cypher)
    for gone in ("Set", "Data", "delete", "DROP", "SET"):
        assert gone not in masked
    assert masked.startswith("MATCH (s) WHERE s.")
    assert "RETURN s.id" in masked


@pytest.mark.parametrize("cypher", MASK_SAMPLES)
def test_mask_matches_the_graph_agent_mask(cypher):
    """agents/graph.py keeps `_mask_cypher` as an alias of this function."""
    from chat_nextseek.agents.graph import _mask_cypher

    assert mask_cypher(cypher) == _mask_cypher(cypher)

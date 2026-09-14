"""Every Cypher statement the graph schema v1.1 writer sends (docs/neo4j-schema.md, section "v1.1").

Module constants, so tests assert on the text and a reader sees the whole write surface in one place. Values always
travel as parameters. The only text built from data is a budget index, whose label must match the ``T_`` rule and
whose property name is backtick-quoted by ``quote``.

Statements that use dynamic labels (``SET s:$(...)``) or scoped subqueries (``CALL (s) { ... }``) start with
``CYPHER 25``.
"""
from __future__ import annotations

import hashlib
import re

# --- schema --------------------------------------------------------------------------------------

CONSTRAINTS_V11 = [
    "CREATE CONSTRAINT sample_id_unique IF NOT EXISTS FOR (s:Sample) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT sample_type_id_unique IF NOT EXISTS FOR (t:SampleType) REQUIRE t.id IS UNIQUE",
    "CREATE CONSTRAINT sample_type_title_unique IF NOT EXISTS FOR (t:SampleType) REQUIRE t.title IS UNIQUE",
    "CREATE CONSTRAINT sample_type_label_unique IF NOT EXISTS FOR (t:SampleType) REQUIRE t.label IS UNIQUE",
    "CREATE CONSTRAINT attribute_key_unique IF NOT EXISTS FOR (a:Attribute) REQUIRE a.key IS UNIQUE",
    "CREATE CONSTRAINT attribute_id_unique IF NOT EXISTS FOR (a:Attribute) REQUIRE a.id IS UNIQUE",
    "CREATE CONSTRAINT project_id_unique IF NOT EXISTS FOR (p:Project) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT person_id_unique IF NOT EXISTS FOR (p:Person) REQUIRE p.id IS UNIQUE",
    "CREATE CONSTRAINT study_id_unique IF NOT EXISTS FOR (s:Study) REQUIRE s.id IS UNIQUE",
    "CREATE CONSTRAINT investigation_id_unique IF NOT EXISTS FOR (i:Investigation) REQUIRE i.id IS UNIQUE",
    "CREATE INDEX sample_uuid IF NOT EXISTS FOR (s:Sample) ON (s.uuid)",
    "CREATE INDEX sample_type IF NOT EXISTS FOR (s:Sample) ON (s.type)",
    "CREATE INDEX study_seek_study_id IF NOT EXISTS FOR (s:Study) ON (s.seek_study_id)",
]
# v1.0's batch-upload writer asks for a unique Sample.uuid; v1.1 stores MySQL's duplicate uuids, so a graph that
# carries that constraint (the dev box's does) would refuse the sample pass.
DROP_V10_CONSTRAINTS = [
    "DROP CONSTRAINT sample_uuid_unique IF EXISTS",
]
FULLTEXT_INDEX = "sample_search_text"
FULLTEXT = ("CREATE FULLTEXT INDEX sample_search_text IF NOT EXISTS "
            "FOR (s:Sample) ON EACH [s.search_text]")
INDEX_STATES = "SHOW INDEXES YIELD name, state, populationPercent RETURN name, state, populationPercent"
GS_INDEX_NAMES = "SHOW INDEXES YIELD name WHERE name STARTS WITH 'gs_' RETURN name"
DROP_INDEX = "DROP INDEX {name} IF EXISTS"
BUDGET_INDEX = "CREATE INDEX {name} IF NOT EXISTS FOR (s:`{label}`) ON (s.{prop})"

_LABEL_RE = re.compile(r"T_[A-Za-z0-9_]+")
_INDEX_NAME_RE = re.compile(r"gs_[A-Za-z0-9_]+")


def quote(name: str) -> str:
    """A property name as a Cypher identifier: backticks around it, any backtick inside doubled."""
    return "`" + name.replace("`", "``") + "`"


def budget_index(label: str, title: str) -> tuple[str, str]:
    """The name and CREATE statement of the range index on ``(:label).title``.

    The name is ``gs_<label>_<first 10 hex of sha1(title)>``. Raises ValueError for a label outside the ``T_`` rule,
    the one place text from data could otherwise reach a statement unquoted.
    """
    if not _LABEL_RE.fullmatch(label or ""):
        raise ValueError(f"not a sample type label: {label!r}")
    name = f"gs_{label}_{hashlib.sha1(title.encode('utf-8')).hexdigest()[:10]}"
    return name, BUDGET_INDEX.format(name=name, label=label, prop=quote(title))


def drop_index(name: str) -> str:
    """DROP for one of the writer's own ``gs_`` indexes; raises ValueError for any other name."""
    if not _INDEX_NAME_RE.fullmatch(name or ""):
        raise ValueError(f"not a graph_sync budget index: {name!r}")
    return DROP_INDEX.format(name=name)


# --- ghosts and orphans --------------------------------------------------------------------------

SAMPLE_IDS = "MATCH (s:Sample) RETURN s.id AS id"
DUPLICATE_SAMPLE_IDS = """
MATCH (s:Sample) WHERE s.id IS NOT NULL
WITH s.id AS id, count(*) AS nodes
WHERE nodes > 1
RETURN id
"""
NODES_FOR_IDS = """
MATCH (s:Sample) WHERE s.id IN $ids
RETURN s.id AS id, elementId(s) AS element_id, s.uuid AS uuid
"""
SAMPLES_WITHOUT_ID = "MATCH (s:Sample) WHERE s.id IS NULL RETURN elementId(s) AS element_id"
DELETE_GHOSTS = """
UNWIND $element_ids AS eid
MATCH (s:Sample) WHERE elementId(s) = eid
DETACH DELETE s
RETURN count(*) AS n
"""
RELABEL_ORPHANS = """
MATCH (s:Sample) WHERE s.id IN $ids
SET s:OrphanSample
REMOVE s:Sample
RETURN count(s) AS n
"""
RELABEL_ORPHANS_BY_ELEMENT_ID = """
UNWIND $element_ids AS eid
MATCH (s:Sample) WHERE elementId(s) = eid
SET s:OrphanSample
REMOVE s:Sample
RETURN count(s) AS n
"""

# --- CHILD_OF ------------------------------------------------------------------------------------

CHILD_OF_COUNT = "MATCH ()-[r:CHILD_OF]->() RETURN count(r) AS n"
CHILD_OF_PAIRS = """
MATCH (c)-[:CHILD_OF]->(p)
RETURN DISTINCT c.uuid AS child_uuid, p.uuid AS parent_uuid
"""
DELETE_CHILD_OF_BATCH = """
MATCH ()-[r:CHILD_OF]->()
WITH r LIMIT $batch
DELETE r
RETURN count(*) AS deleted
"""

# --- the catalog ---------------------------------------------------------------------------------

SAMPLE_TYPE_TITLE_CONFLICTS = """
UNWIND $rows AS r
MATCH (t:SampleType {title: r.title})
WHERE t.id IS NOT NULL AND t.id <> r.id
RETURN t.title AS title, t.id AS graph_id, r.id AS mysql_id
"""
BACKFILL_SAMPLE_TYPE_ID = """
UNWIND $rows AS r
MATCH (t:SampleType {title: r.title}) WHERE t.id IS NULL
SET t.id = r.id
"""
MERGE_SAMPLE_TYPES = """
UNWIND $rows AS r
MERGE (t:SampleType {id: r.id})
SET t = r
"""
SAMPLE_TYPES_NOT_IN = """
MATCH (t:SampleType) WHERE t.id IS NULL OR NOT t.id IN $ids
RETURN t.title AS title
"""
MERGE_ATTRIBUTES = """
UNWIND $rows AS r
MERGE (a:Attribute {key: r.key})
SET a = r
WITH a, r
MATCH (t:SampleType {id: r.sample_type_id})
MERGE (t)-[:HAS_ATTRIBUTE]->(a)
RETURN count(*) AS linked
"""
DELETE_GONE_ATTRIBUTES = "MATCH (a:Attribute) WHERE NOT a.key IN $keys DETACH DELETE a"
SET_ATTRIBUTE_COUNTS = """
UNWIND $rows AS r
MATCH (a:Attribute {key: r.key})
SET a.sample_count = r.count
RETURN count(a) AS n
"""
ZERO_ATTRIBUTE_COUNTS = """
MATCH (a:Attribute) WHERE NOT a.key IN $keys
SET a.sample_count = 0
RETURN count(a) AS n
"""
SET_SAMPLE_TYPE_COUNTS = """
MATCH (t:SampleType)
SET t.sample_count = COUNT { (t)<-[:OF_TYPE]-(:Sample) },
    t.attribute_count = COUNT { (t)-[:HAS_ATTRIBUTE]->(:Attribute) }
RETURN count(t) AS n
"""

# --- projects, people, investigations ------------------------------------------------------------

MERGE_PROJECTS = """
UNWIND $rows AS r
MERGE (p:Project {id: r.id})
SET p = r
"""
DELETE_GONE_PROJECTS = "MATCH (p:Project) WHERE NOT p.id IN $ids DETACH DELETE p"
DELETE_MEMBER_OF = "MATCH (:Person)-[m:MEMBER_OF]->() DELETE m"
DELETE_GONE_PEOPLE = "MATCH (pe:Person) WHERE NOT pe.id IN $ids DETACH DELETE pe"
MERGE_PEOPLE = "UNWIND $ids AS id MERGE (:Person {id: id})"
MERGE_MEMBER_OF = """
UNWIND $rows AS r
MATCH (pe:Person {id: r.person_id})
MATCH (p:Project {id: r.project_id})
MERGE (pe)-[m:MEMBER_OF]->(p)
SET m.has_left = r.has_left, m.time_left_at = r.time_left_at
RETURN count(m) AS linked
"""
MERGE_INVESTIGATIONS = """
UNWIND $rows AS r
MERGE (i:Investigation {id: r.id})
SET i.title = r.title, i.description = r.description, i.project_id = r.project_id
"""
DELETE_INVESTIGATION_IN_PROJECT = "MATCH (:Investigation)-[e:IN_PROJECT]->(:Project) DELETE e"
MERGE_INVESTIGATION_IN_PROJECT = """
UNWIND $rows AS r
MATCH (i:Investigation {id: r.investigation_id})
MATCH (p:Project {id: r.project_id})
MERGE (i)-[:IN_PROJECT]->(p)
RETURN count(*) AS linked
"""

# --- samples -------------------------------------------------------------------------------------

# One row per sample: {id, label, sample_type_id, props}. The property map is replaced whole (a key deleted from
# MySQL leaves the node), except batch upload's parent_titles and parent_title_hashes, which MySQL does not hold.
# OF_TYPE and IN_PROJECT are rebuilt from the row. The two counting subqueries always return one row, so a sample
# whose type or project node is missing is still written and shows up as a shortfall in the counts.
WRITE_SAMPLES = """
CYPHER 25
UNWIND $rows AS r
MERGE (s:Sample {id: r.id})
WITH s, r, s.parent_titles AS pt, s.parent_title_hashes AS pth
SET s = r.props
SET s.parent_titles = pt, s.parent_title_hashes = pth, s.synced_at = datetime()
SET s:$(r.label)
WITH s, r, [l IN labels(s) WHERE l STARTS WITH 'T_' AND l <> r.label] AS stale
REMOVE s:$(stale)
WITH s, r
CALL (s) { MATCH (s)-[o:OF_TYPE|IN_PROJECT]->() DELETE o }
CALL (s, r) {
  MATCH (t:SampleType {id: r.sample_type_id})
  MERGE (s)-[:OF_TYPE]->(t)
  RETURN count(t) AS typed
}
CALL (s, r) {
  UNWIND r.props.project_ids AS pid
  MATCH (p:Project {id: pid})
  MERGE (s)-[:IN_PROJECT]->(p)
  RETURN count(p) AS linked
}
RETURN count(s) AS written, sum(typed) AS typed, sum(linked) AS linked
"""

# --- lineage and SEEK studies --------------------------------------------------------------------

# How existing DERIVED_FROM edges record their endpoints: an int (sample id) or a str (uuid).
DERIVED_FROM_ID_FORM = """
MATCH ()-[e:DERIVED_FROM]->() WHERE e.child_id IS NOT NULL
RETURN e.child_id AS child_id LIMIT 1
"""
# Rows are [child id, parent id]. An existing edge and its properties are kept; a new one records its endpoints in
# the form existing edges use ($by_uuid).
WRITE_MISSING_LINEAGE = """
UNWIND $rows AS r
MATCH (c:Sample {id: r[0]})
MATCH (p:Sample {id: r[1]})
MERGE (c)-[e:DERIVED_FROM]->(p)
ON CREATE SET e.child_id = CASE WHEN $by_uuid THEN c.uuid ELSE c.id END,
              e.parent_id = CASE WHEN $by_uuid THEN p.uuid ELSE p.id END
RETURN count(e) AS matched
"""
# Samples already placed in a paper-level Study (one with no seek_study_id); SEEK studies are not added to them.
SAMPLES_IN_PAPER_STUDIES = """
MATCH (s:Sample)-[:IN_STUDY]->(st:Study) WHERE st.seek_study_id IS NULL
RETURN DISTINCT s.id AS id
"""
MERGE_SEEK_STUDIES = """
UNWIND $rows AS r
MERGE (st:Study {seek_study_id: r.study_id})
SET st.title = r.title
WITH st, r WHERE r.investigation_id IS NOT NULL
MATCH (i:Investigation {id: r.investigation_id})
MERGE (st)-[:IN_INVESTIGATION]->(i)
"""
MERGE_SEEK_IN_STUDY = """
UNWIND $rows AS r
MATCH (s:Sample {id: r.sample_id})
MATCH (st:Study {seek_study_id: r.study_id})
MERGE (s)-[:IN_STUDY]->(st)
RETURN count(*) AS linked
"""

# --- GraphMeta -----------------------------------------------------------------------------------

WRITE_GRAPHMETA = """
MERGE (m:GraphMeta)
SET m.schema_version = $schema_version, m.catalog_hash = $catalog_hash, m.synced_at = datetime()
"""

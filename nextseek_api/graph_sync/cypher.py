"""Every Cypher statement the graph schema v1.2 writer sends (docs/neo4j-schema.md, sections "v1.1" and "v1.2").

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
# The deletion rule (the sync design, section 9). A node graph_sync never wrote (no synced_at: a v1.0 graph-only
# node, which may carry lineage MySQL never had) becomes an OrphanSample: Sample, every T_ label, OF_TYPE and
# IN_PROJECT go, orphaned_at is set, its properties and DERIVED_FROM stay. One body, matched by id or by element id;
# a node carrying synced_at is never matched here (retire_samples deletes it instead).
ORPHAN_SWAP = """
CALL (s) { MATCH (s)-[o:OF_TYPE|IN_PROJECT]->() DELETE o }
WITH s, [l IN labels(s) WHERE l STARTS WITH 'T_'] AS types
SET s:OrphanSample, s.orphaned_at = datetime()
REMOVE s:Sample
REMOVE s:$(types)
RETURN count(s) AS n
"""
RELABEL_ORPHANS = """
CYPHER 25
MATCH (s:Sample) WHERE s.id IN $ids AND s.synced_at IS NULL""" + ORPHAN_SWAP
RELABEL_ORPHANS_BY_ELEMENT_ID = """
CYPHER 25
UNWIND $element_ids AS eid
MATCH (s:Sample) WHERE elementId(s) = eid AND s.synced_at IS NULL""" + ORPHAN_SWAP
# The live Sample nodes of ids MySQL no longer holds: what the retire archive records, and which rule applies. An
# existing OrphanSample carries no Sample label, so it is not read and stays as it is.
RETIRE_CANDIDATES = """
UNWIND $ids AS id
MATCH (s:Sample {id: id})
RETURN elementId(s) AS element_id, s.id AS id, s.uuid AS uuid, s.type AS type,
       s.synced_at IS NOT NULL AS synced, COUNT { (s)--() } AS incident_edges
"""
# A node graph_sync wrote mirrors a row that is gone; it is deleted with its edges once the archive holds it.
DELETE_RETIRED = """
UNWIND $element_ids AS eid
MATCH (s:Sample) WHERE elementId(s) = eid AND s.synced_at IS NOT NULL
DETACH DELETE s
RETURN count(*) AS n
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
# MySQL leaves the node). parent_titles and parent_title_hashes come from the props when the projection supplies them
# (schema 1.2: projection-owned); a row without them keeps the node's own. OF_TYPE and IN_PROJECT are rebuilt from
# the row. The two counting subqueries always return one row, so a sample whose type or project node is missing is
# still written and shows up as a shortfall in the counts.
WRITE_SAMPLES = """
CYPHER 25
UNWIND $rows AS r
MERGE (s:Sample {id: r.id})
WITH s, r,
     CASE WHEN 'parent_titles' IN keys(r.props) THEN r.props.parent_titles ELSE s.parent_titles END AS pt,
     CASE WHEN 'parent_title_hashes' IN keys(r.props) THEN r.props.parent_title_hashes
          ELSE s.parent_title_hashes END AS pth
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
# Every DERIVED_FROM between two Sample nodes (gate G check 1 reads the same pattern). An OrphanSample no longer
# carries Sample, so an edge touching one is neither read nor deleted here.
DERIVED_FROM_BETWEEN_SAMPLES = """
MATCH (c:Sample)-[e:DERIVED_FROM]->(p:Sample)
RETURN c.id AS child_id, p.id AS parent_id, c.uuid AS child_uuid, p.uuid AS parent_uuid,
       properties(e) AS props, elementId(e) AS element_id
"""
DELETE_UNDECLARED_DERIVED_FROM = """
UNWIND $element_ids AS eid
MATCH (:Sample)-[e:DERIVED_FROM]->(:Sample)
WHERE elementId(e) = eid
DELETE e
RETURN count(*) AS deleted
"""
# The DERIVED_FROM edges of the children $ids to Sample parents, in the stream form above; a targeted sync archives
# and deletes the ones MySQL no longer declares, with DELETE_UNDECLARED_DERIVED_FROM.
DERIVED_FROM_OF_CHILDREN = """
UNWIND $ids AS id
MATCH (c:Sample {id: id})-[e:DERIVED_FROM]->(p:Sample)
RETURN c.id AS child_id, p.id AS parent_id, c.uuid AS child_uuid, p.uuid AS parent_uuid,
       properties(e) AS props, elementId(e) AS element_id
"""

# --- DERIVED_FROM labels (schema 1.2) -------------------------------------------------------------

# What graph_sync writes on a DERIVED_FROM edge, always all seven together (the sync design, section 7.3): the five
# assay properties and the protocol pair. The three singular assay fields decide whether an edge is labelled.
EDGE_SINGULAR_ASSAY_KEYS = ("assay_id", "internal_assay_id", "internal_assay_title")
EDGE_LABEL_KEYS = EDGE_SINGULAR_ASSAY_KEYS + ("internal_assay_ids", "internal_assay_titles", "protocol_id",
                                              "protocol_title")
_EDGE_LABEL_MAP = "e {" + ", ".join("." + key for key in EDGE_LABEL_KEYS) + "}"
_EDGE_LABEL_LIST = "[" + ", ".join(f"'{key}'" for key in EDGE_LABEL_KEYS) + "]"

# Every DERIVED_FROM between two Sample nodes with an end among $ids, either direction, and its seven label
# properties (null when absent) as `stored`. UNION, not UNION ALL, returns an edge between two of the ids once.
EDGES_INCIDENT = """
UNWIND $ids AS id
MATCH (c:Sample {id: id})-[e:DERIVED_FROM]->(p:Sample)
RETURN c.id AS child_id, p.id AS parent_id, elementId(e) AS element_id,
       {stored} AS stored
UNION
UNWIND $ids AS id
MATCH (c:Sample)-[e:DERIVED_FROM]->(p:Sample {id: id})
RETURN c.id AS child_id, p.id AS parent_id, elementId(e) AS element_id,
       {stored} AS stored
""".replace("{stored}", _EDGE_LABEL_MAP)

# Rows are {child_id, parent_id, labels} (and `stored` for the approved write). Each edge between the two Sample
# nodes passes its guard or is left alone; the guard is a WHERE inside the statement, so a label another writer set
# after the caller's read is never overwritten. A written edge gets all seven label properties (a null removes one)
# and loses the legacy assay_title. `matched` counts edges found, `written` those past the guard, `pairs` the rows
# that found an edge at all.
_EDGE_LABEL_HEAD = """
CYPHER 25
UNWIND $rows AS r
MATCH (:Sample {id: r.child_id})-[e:DERIVED_FROM]->(:Sample {id: r.parent_id})
CALL (e, r) {"""
_EDGE_LABEL_ASSAY_SET = """
  SET e.assay_id = r.labels.assay_id,
      e.internal_assay_id = r.labels.internal_assay_id,
      e.internal_assay_title = r.labels.internal_assay_title,
      e.internal_assay_ids = r.labels.internal_assay_ids,
      e.internal_assay_titles = r.labels.internal_assay_titles"""
_EDGE_LABEL_TAIL = """
  REMOVE e.assay_title
  RETURN count(*) AS w
}
RETURN count(e) AS matched, sum(w) AS written, count(DISTINCT [r.child_id, r.parent_id]) AS pairs
"""
# Approved mode replaces every value, the protocol pair included.
_EDGE_LABEL_SET = _EDGE_LABEL_ASSAY_SET + """,
      e.protocol_id = r.labels.protocol_id,
      e.protocol_title = r.labels.protocol_title""" + _EDGE_LABEL_TAIL
# Default mode keeps a stored protocol. An edge can carry a protocol and no assay label (V1 measured 402 of them in
# production), and R5 forbids removing or replacing a stored label without approval, so the protocol pair is written
# only where nothing is stored.
_EDGE_LABEL_SET_NEW = _EDGE_LABEL_ASSAY_SET + """,
      e.protocol_id = coalesce(e.protocol_id, r.labels.protocol_id),
      e.protocol_title = coalesce(e.protocol_title, r.labels.protocol_title)""" + _EDGE_LABEL_TAIL
# The default: a new label only, on an edge whose three singular assay fields are all null (R14).
WRITE_EDGE_LABELS_NEW = _EDGE_LABEL_HEAD + """
  WITH e, r WHERE e.assay_id IS NULL AND e.internal_assay_id IS NULL AND e.internal_assay_title IS NULL""" + \
    _EDGE_LABEL_SET_NEW
# With the operator's approval: any label, but only where all seven stored values still equal those the caller read
# (`r.stored`), a null equal only to a null.
WRITE_EDGE_LABELS_CHANGED = _EDGE_LABEL_HEAD + """
  WITH e, r WHERE all(k IN {keys}
                      WHERE (e[k] IS NULL AND r.stored[k] IS NULL) OR coalesce(e[k] = r.stored[k], false))""" \
    .replace("{keys}", _EDGE_LABEL_LIST) + _EDGE_LABEL_SET

# --- source hashes -------------------------------------------------------------------------------

# One keyset page of (id, source_hash), ordered by id over the Sample.id index. Only numeric ids compare with
# $after, so a legacy node with any other id is left to the full sync.
SAMPLE_HASHES_PAGE = """
MATCH (s:Sample) WHERE s.id > $after
RETURN s.id AS id, s.source_hash AS source_hash
ORDER BY s.id LIMIT $limit
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

# Named properties, never a replace, so a value a statement does not name (label_maps_hash here) is kept.
WRITE_GRAPHMETA = """
MERGE (m:GraphMeta)
SET m.schema_version = $schema_version, m.catalog_hash = $catalog_hash, m.synced_at = datetime()
"""
WRITE_GRAPHMETA_WITH_LABEL_MAPS = """
MERGE (m:GraphMeta)
SET m.schema_version = $schema_version, m.catalog_hash = $catalog_hash, m.label_maps_hash = $label_maps_hash,
    m.synced_at = datetime()
"""
READ_GRAPHMETA = "MATCH (m:GraphMeta) RETURN properties(m) AS props"

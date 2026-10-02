"""Every Cypher statement the graph schema writer sends (docs/neo4j-schema.md, sections "v1.1", "v1.2" and "v1.3").

Module constants, so tests assert on the text and a reader sees the whole write surface in one place. Values always
travel as parameters. The only text built from data is a budget index, whose label must match the ``T_`` rule and
whose property name is backtick-quoted by ``quote``.

Statements that use dynamic labels (``SET s:$(...)``) or scoped subqueries (``CALL (s) { ... }``) start with
``CYPHER 25``.
"""
from __future__ import annotations

import hashlib
import re

from nextseek_graph import schema

# --- schema --------------------------------------------------------------------------------------

# The schema DDL, rendered from the contract's 1.1 triples (nextseek_graph/schema.py), one template per shape. The
# variable is the label's first letter, lowercased (SampleType's is t), so every statement reads as it always has.
_UNIQUE_DDL = "CREATE CONSTRAINT {name} IF NOT EXISTS FOR ({var}:{label}) REQUIRE {var}.{prop} IS UNIQUE"
_RANGE_DDL = "CREATE INDEX {name} IF NOT EXISTS FOR ({var}:{label}) ON ({var}.{prop})"
_FULLTEXT_DDL = "CREATE FULLTEXT INDEX {name} IF NOT EXISTS FOR ({var}:{label}) ON EACH [{var}.{prop}]"


def _ddl_var(label: str) -> str:
    return "t" if label == schema.SAMPLE_TYPE else label[0].lower()


def _ddl(template: str, name: str, label: str, prop: str) -> str:
    return template.format(name=name, var=_ddl_var(label), label=label, prop=prop)


CONSTRAINTS_V11 = ([_ddl(_UNIQUE_DDL, *triple) for triple in schema.UNIQUE_CONSTRAINTS_V11]
                   + [_ddl(_RANGE_DDL, *triple) for triple in schema.RANGE_INDEXES_V11])
# v1.0's batch-upload writer asks for a unique Sample.uuid; v1.1 stores MySQL's duplicate uuids, so a graph that
# carries that constraint (the dev box's does) would refuse the sample pass.
DROP_V10_CONSTRAINTS = [
    "DROP CONSTRAINT sample_uuid_unique IF EXISTS",
]
FULLTEXT_INDEX = schema.FULLTEXT_INDEX
FULLTEXT = _ddl(_FULLTEXT_DDL, schema.FULLTEXT_INDEX, *schema.FULLTEXT_INDEX_ON)
INDEX_STATES = "SHOW INDEXES YIELD name, state, populationPercent RETURN name, state, populationPercent"
GS_INDEX_NAMES = "SHOW INDEXES YIELD name WHERE name STARTS WITH 'gs_' RETURN name"
DROP_INDEX = "DROP INDEX {name} IF EXISTS"
BUDGET_INDEX = "CREATE INDEX {name} IF NOT EXISTS FOR (s:`{label}`) ON (s.{prop})"

_INDEX_NAME_RE = re.compile(re.escape(schema.BUDGET_INDEX_PREFIX) + r"[A-Za-z0-9_]+")


def quote(name: str) -> str:
    """A property name as a Cypher identifier: backticks around it, any backtick inside doubled."""
    return "`" + name.replace("`", "``") + "`"


def budget_index(label: str, title: str) -> tuple[str, str]:
    """The name and CREATE statement of the range index on ``(:label).title``.

    The name is ``gs_<label>_<first 10 hex of sha1(title)>``. Raises ValueError for a label outside the ``T_`` rule,
    the one place text from data could otherwise reach a statement unquoted.
    """
    if not schema.is_type_label(label):
        raise ValueError(f"not a sample type label: {label!r}")
    name = f"{schema.BUDGET_INDEX_PREFIX}{label}_{hashlib.sha1(title.encode('utf-8')).hexdigest()[:10]}"
    return name, BUDGET_INDEX.format(name=name, label=label, prop=quote(title))


def drop_index(name: str) -> str:
    """DROP for one of the writer's own ``gs_`` indexes; raises ValueError for any other name."""
    if not _INDEX_NAME_RE.fullmatch(name or ""):
        raise ValueError(f"not a graph_sync budget index: {name!r}")
    return DROP_INDEX.format(name=name)


# --- paged whole-graph reads (writer.read_sample_pages) ------------------------------------------

# How many Samples the next keyset page after $after holds (at most $limit) and its last id, over the Sample.id index.
# Only numeric ids compare with $after.
SAMPLE_ID_PAGE_END = """
MATCH (c:Sample) WHERE c.id > $after
WITH c.id AS id ORDER BY id LIMIT $limit
RETURN count(id) AS n, max(id) AS last
"""

# --- ghosts and orphans --------------------------------------------------------------------------

# Every Sample id, read a page at a time (writer.read_sample_pages).
SAMPLE_IDS = "MATCH (c:Sample) WHERE {page} RETURN c.id AS id"
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
# node, which may carry lineage MySQL never had) becomes an OrphanSample: Sample, every T_ label, OF_TYPE,
# IN_PROJECT, INPUT_TO and OUTPUT_OF go, orphaned_at is set, its properties and DERIVED_FROM stay. One body, matched
# by id or by element id; a node carrying synced_at is never matched here (retire_samples deletes it instead).
ORPHAN_SWAP = """
CALL (s) { MATCH (s)-[o:OF_TYPE|IN_PROJECT]->() DELETE o }
CALL (s) { MATCH (s)-[r:INPUT_TO|OUTPUT_OF]->(:Assay) DELETE r }
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

# A SampleType holding a MySQL title under another id. A node whose id SEEK no longer has and that no Sample reaches is
# not a conflict: the catalog step deletes it first (SAMPLE_TYPES_GONE), so a type deleted and recreated in SEEK under
# the same title passes. $ids are every MySQL sample type id.
SAMPLE_TYPE_TITLE_CONFLICTS = """
UNWIND $rows AS r
MATCH (t:SampleType {title: r.title})
WHERE t.id IS NOT NULL AND t.id <> r.id AND (t.id IN $ids OR EXISTS { (t)<-[:OF_TYPE]-(:Sample) })
RETURN t.title AS title, t.id AS graph_id, r.id AS mysql_id
"""
# The SampleType nodes whose id SEEK no longer has and that no Sample reaches, with what their archive records.
SAMPLE_TYPES_GONE = """
MATCH (t:SampleType) WHERE t.id IS NOT NULL AND NOT t.id IN $ids AND NOT EXISTS { (t)<-[:OF_TYPE]-(:Sample) }
RETURN elementId(t) AS element_id, t.id AS id, t.title AS title, t.label AS label,
       [(t)-[:HAS_ATTRIBUTE]->(a:Attribute) | a.key] AS attribute_keys
ORDER BY id
"""
# Delete those types and their Attribute nodes, only while still no Sample reaches them.
DELETE_SAMPLE_TYPES = """
CYPHER 25
UNWIND $element_ids AS eid
MATCH (t:SampleType) WHERE elementId(t) = eid AND NOT EXISTS { (t)<-[:OF_TYPE]-(:Sample) }
CALL (t) {
  MATCH (t)-[:HAS_ATTRIBUTE]->(a:Attribute)
  DETACH DELETE a
}
DETACH DELETE t
RETURN count(*) AS deleted
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
# A declared attribute starts at sample_count 0 and only a full sync counted it: a by-id sync that writes a sample
# carrying one of these counts it (targeted._attribute_counts), so Nessie's catalog lists it and its guard allows it.
ATTRIBUTES_AT_ZERO = """
UNWIND $type_ids AS tid
MATCH (t:SampleType {id: tid})-[:HAS_ATTRIBUTE]->(a:Attribute)
WHERE coalesce(a.sample_count, 0) = 0
RETURN tid AS type_id, a.key AS key, a.title AS title
"""
# Rows are {type_id, key, title}: the attribute's sample_count becomes the number of its type's samples carrying it.
SET_ATTRIBUTE_COUNTS_FROM_TYPE = """
CYPHER 25
UNWIND $rows AS r
MATCH (t:SampleType {id: r.type_id})-[:HAS_ATTRIBUTE]->(a:Attribute {key: r.key})
SET a.sample_count = COUNT { (t)<-[:OF_TYPE]-(s:Sample) WHERE s[r.title] IS NOT NULL }
RETURN sum(CASE WHEN a.sample_count > 0 THEN 1 ELSE 0 END) AS raised
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
# The Project nodes among these ids: a by-id sync writes a missing one before it links a sample or an investigation.
PROJECT_IDS_PRESENT = """
UNWIND $ids AS id
MATCH (p:Project {id: id})
RETURN p.id AS id
"""
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
# A Study holds its Investigation while SEEK still has its study ($study_ids, SEEK's study ids) or when it is a
# graph-only paper (no seek_study_id). SEEK deletes an investigation after its studies and a Study node is never
# deleted here, so the node of a gone SEEK study holds nothing; it loses its IN_INVESTIGATION with the Investigation.
_INVESTIGATION_HELD = """EXISTS { (i)<-[:IN_INVESTIGATION]-(st:Study)
         WHERE st.seek_study_id IS NULL OR st.seek_study_id IN $study_ids }"""
# Investigation nodes whose id SEEK no longer has: those no Study holds, with what their archive records, are deleted;
# the others are counted.
INVESTIGATIONS_GONE = """
MATCH (i:Investigation) WHERE i.id IS NOT NULL AND NOT i.id IN $ids
RETURN elementId(i) AS element_id, i.id AS id, i.title AS title,
       [(i)-[:IN_PROJECT]->(p:Project) | p.id] AS project_ids,
       """ + _INVESTIGATION_HELD + """ AS held
ORDER BY id
"""
DELETE_INVESTIGATIONS = """
UNWIND $element_ids AS eid
MATCH (i:Investigation) WHERE elementId(i) = eid AND NOT """ + _INVESTIGATION_HELD + """
DETACH DELETE i
RETURN count(*) AS deleted
"""
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
# still written and shows up as a shortfall in the counts; its source_hash is then left null, so the nightly
# reconcile reads it as changed and syncs it again rather than treating the half-linked node as current.
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
SET s.source_hash = CASE WHEN typed = 0 OR linked < size(coalesce(r.props.project_ids, [])) THEN null
                         ELSE s.source_hash END
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
# Every DERIVED_FROM between two Sample nodes, read a page of child ids at a time (writer.read_sample_pages; gate G
# check 1 reads the same pattern). An OrphanSample no longer carries Sample, so an edge touching one is neither read
# nor deleted here.
DERIVED_FROM_BETWEEN_SAMPLES = """
MATCH (c:Sample) WHERE {page}
MATCH (c)-[e:DERIVED_FROM]->(p:Sample)
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
EDGE_SINGULAR_ASSAY_KEYS = schema.DERIVED_FROM_SINGULAR_ASSAY_KEYS
EDGE_LABEL_KEYS = schema.DERIVED_FROM_LABEL_KEYS
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

# --- SEEK studies and IN_STUDY (docs/neo4j-schema.md, v1.2 "Study nodes and IN_STUDY") ----------------------------

# A SEEK study's node is found by seek_study_id. Its title, description and IN_INVESTIGATION become SEEK's on every
# path that writes it: a null description removes the property, and every IN_INVESTIGATION to another Investigation
# (or every one, when SEEK names none) is deleted before the link to SEEK's is merged. The caller writes the
# Investigation node first (writer.write_study_investigations); a row whose Investigation node is still missing is
# counted in investigation_missing, never skipped silently.
_SEEK_STUDY_FOLLOWS = """
SET st.title = r.title, st.description = r.description
WITH st, r
CALL (st, r) {
  MATCH (st)-[old:IN_INVESTIGATION]->(i:Investigation)
  WHERE r.investigation_id IS NULL OR coalesce(i.id <> r.investigation_id, true)
  DELETE old
}
CALL (st, r) {
  OPTIONAL MATCH (i:Investigation {id: r.investigation_id})
  FOREACH (_ IN CASE WHEN i IS NULL THEN [] ELSE [1] END | MERGE (st)-[:IN_INVESTIGATION]->(i))
  RETURN CASE WHEN r.investigation_id IS NOT NULL AND i IS NULL THEN 1 ELSE 0 END AS missing
}
RETURN count(st) AS n, sum(missing) AS investigation_missing
"""
# Rows are {study_id, title, description, investigation_id}: the node of every SEEK study named, made when missing.
MERGE_SEEK_STUDIES = """
CYPHER 25
UNWIND $rows AS r
MERGE (st:Study {seek_study_id: r.study_id})""" + _SEEK_STUDY_FOLLOWS

# Each IN_STUDY of a Sample: the edge's element id, its Study's two keys and the Study's Investigations.
_SAMPLE_STUDIES = ("[(s)-[e:IN_STUDY]->(st:Study) | "
                   "{element_id: elementId(e), seek_study_id: st.seek_study_id, id: st.id, "
                   "investigations: [(st)-[:IN_INVESTIGATION]->(i:Investigation) | {id: i.id, title: i.title}]}] "
                   "AS studies")
SAMPLE_STUDIES_OF = """
UNWIND $ids AS id
MATCH (s:Sample {id: id})
RETURN s.id AS id, """ + _SAMPLE_STUDIES
# One keyset page of every Sample, ordered by id over the Sample.id index (numeric ids only, as SAMPLE_HASHES_PAGE).
SAMPLE_STUDIES_PAGE = """
MATCH (s:Sample) WHERE s.id > $after
WITH s ORDER BY s.id LIMIT $limit
RETURN s.id AS id, """ + _SAMPLE_STUDIES

# Rows are {sample_id, study_ids, withhold, paper, remove}: SEEK's studies of the sample, the ones not to link it to
# (a paper sample's studies of its paper's own investigation, writer.paper_split), whether the caller read it as a
# paper sample, and the element ids of the IN_STUDY edges the caller archived for removal. An edge is deleted only if
# it is still an IN_STUDY from that sample to a SEEK-keyed Study outside study_ids. Then a link to each SEEK study's
# node not withheld is merged; a sample that became a paper sample after the caller's read (paper false, but an
# IN_STUDY to a Study with no seek_study_id now) withholds every study, so a race can only withhold, never over-link.
# An IN_STUDY to a Study with no seek_study_id is never touched. A row whose sample is not a Sample node is skipped
# (samples counts the rest).
REPLACE_SEEK_IN_STUDY = """
CYPHER 25
UNWIND $rows AS r
MATCH (s:Sample {id: r.sample_id})
CALL (s, r) {
  UNWIND r.remove AS eid
  MATCH (s)-[e:IN_STUDY]->(st:Study)
  WHERE elementId(e) = eid AND st.seek_study_id IS NOT NULL AND NOT st.seek_study_id IN r.study_ids
  DELETE e
  RETURN count(*) AS removed
}
WITH s, r, removed, EXISTS { (s)-[:IN_STUDY]->(p:Study) WHERE p.seek_study_id IS NULL } AS paper
WITH s, r, removed, paper, CASE WHEN paper AND NOT r.paper THEN r.study_ids ELSE r.withhold END AS withhold
CALL (s, r, withhold) {
  UNWIND [sid IN r.study_ids WHERE NOT sid IN withhold] AS sid
  OPTIONAL MATCH (st:Study {seek_study_id: sid})
  WITH s, st, st IS NOT NULL AND NOT EXISTS { (s)-[:IN_STUDY]->(st) } AS new
  FOREACH (_ IN CASE WHEN new THEN [1] ELSE [] END | MERGE (s)-[:IN_STUDY]->(st))
  RETURN sum(CASE WHEN new THEN 1 ELSE 0 END) AS added, sum(CASE WHEN st IS NULL THEN 1 ELSE 0 END) AS missing
}
RETURN count(s) AS samples, sum(removed) AS removed, sum(added) AS added,
       sum(CASE WHEN paper THEN 1 ELSE 0 END) AS paper_samples,
       sum(CASE WHEN paper THEN added ELSE 0 END) AS paper_added,
       sum(size(withhold)) AS withheld,
       sum(missing) AS studies_missing
"""
# A MERGE on seek_study_id matches every node holding it: the full sync, --studies and the reconcile refuse a graph
# where this returns a row.
STUDY_SEEK_ID_DUPLICATES = """
MATCH (st:Study) WHERE st.seek_study_id IS NOT NULL
WITH st.seek_study_id AS seek_study_id, count(*) AS nodes
WHERE nodes > 1
RETURN seek_study_id, nodes ORDER BY seek_study_id
"""
# IN_STUDY from anything that is not a Sample (an OrphanSample keeps its own): left as it is, counted apart.
ORPHAN_IN_STUDY = """
MATCH (x)-[e:IN_STUDY]->(:Study) WHERE NOT x:Sample
RETURN count(e) AS n
"""

# Every Study node with what the merge's selection and gate G read: its properties, its Investigations, the IN_STUDY
# it receives (from any node, and from Samples), and every other relationship type it holds besides its incoming
# IN_STUDY and outgoing IN_INVESTIGATION. Tens to hundreds of rows.
STUDY_NODES = """
MATCH (st:Study)
RETURN elementId(st) AS element_id, properties(st) AS props,
       [(st)-[:IN_INVESTIGATION]->(i:Investigation) | {element_id: elementId(i), id: i.id, title: i.title}]
         AS investigations,
       COUNT { (st)<-[:IN_STUDY]-() } AS in_study,
       COUNT { (st)<-[:IN_STUDY]-(:Sample) } AS sample_in_study,
       [(st)-[r]-() WHERE NOT (type(r) = 'IN_STUDY' AND endNode(r) = st)
                     AND NOT (type(r) = 'IN_INVESTIGATION' AND startNode(r) = st) | type(r)] AS other_relationships
ORDER BY element_id
"""
# The distinct nodes with an IN_STUDY to one Study, found by element id.
STUDY_SOURCES = """
MATCH (st:Study) WHERE elementId(st) = $element_id
MATCH (x)-[:IN_STUDY]->(st)
WITH DISTINCT x
RETURN elementId(x) AS element_id, labels(x) AS labels, x.id AS id
ORDER BY element_id
"""

# The next batch of distinct sources with an IN_STUDY to the seek-keyed node K, and whether each already has one to L.
STUDY_SOURCES_BATCH = """
MATCH (k:Study) WHERE elementId(k) = $k
MATCH (x)-[:IN_STUDY]->(k)
WITH DISTINCT x
ORDER BY elementId(x)
LIMIT $limit
RETURN elementId(x) AS element_id, labels(x) AS labels, x.id AS id,
       EXISTS { MATCH (x)-[:IN_STUDY]->(l:Study) WHERE elementId(l) = $l } AS on_l
"""
# Move these sources' IN_STUDY from K to L, whatever their label: MERGE the link to L, then delete every edge to K.
# MERGE yields one row per edge a source already holds to L (parallel edges), so the count is of distinct sources.
MOVE_IN_STUDY = """
UNWIND $sources AS xid
MATCH (x) WHERE elementId(x) = xid
MATCH (k:Study) WHERE elementId(k) = $k
MATCH (l:Study) WHERE elementId(l) = $l
MATCH (x)-[e:IN_STUDY]->(k)
WITH x, l, collect(e) AS edges
MERGE (x)-[:IN_STUDY]->(l)
FOREACH (e IN edges | DELETE e)
RETURN count(DISTINCT x) AS moved
"""
# The merge's last step, one transaction: only while K (when there is one) holds nothing but one IN_INVESTIGATION, L
# is still the legacy node of $study_id, and no node but K carries seek_study_id $study_id. For a
# merge_other_investigation, L's IN_INVESTIGATION moves to $new_investigation, which must be K's Investigation. K's
# IN_INVESTIGATION is deleted, then K with a plain DELETE: a link to K that another writer commits after the check
# makes the transaction fail instead of going with K. L gains seek_study_id, and a DOI or PMID that is '' goes.
FINISH_STUDY_MERGE = """
CYPHER 25
MATCH (l:Study) WHERE elementId(l) = $l AND l.seek_study_id IS NULL AND l.id = $study_id
  AND NOT EXISTS { MATCH (o:Study) WHERE o.seek_study_id = $study_id AND elementId(o) <> coalesce($k, '') }
OPTIONAL MATCH (k:Study) WHERE elementId(k) = $k
WITH l, k
WHERE (($k IS NULL AND k IS NULL)
       OR (k IS NOT NULL AND NOT EXISTS { (k)<-[:IN_STUDY]-() }
           AND COUNT { (k)--() } = COUNT { (k)-[:IN_INVESTIGATION]->() }
           AND COUNT { (k)-[:IN_INVESTIGATION]->() } <= 1))
  AND ($new_investigation IS NULL
       OR EXISTS { MATCH (k)-[:IN_INVESTIGATION]->(i:Investigation) WHERE elementId(i) = $new_investigation })
CALL (l) {
  MATCH (l)-[old:IN_INVESTIGATION]->(i)
  WHERE $new_investigation IS NOT NULL AND elementId(i) <> $new_investigation
  DELETE old
}
CALL (l) {
  MATCH (i:Investigation) WHERE elementId(i) = $new_investigation
  MERGE (l)-[:IN_INVESTIGATION]->(i)
}
CALL (k) {
  MATCH (k)-[e:IN_INVESTIGATION]->()
  DELETE e
}
FOREACH (_ IN CASE WHEN k IS NULL THEN [] ELSE [1] END | DELETE k)
SET l.seek_study_id = $study_id
FOREACH (_ IN CASE WHEN l.DOI = '' THEN [1] ELSE [] END | REMOVE l.DOI)
FOREACH (_ IN CASE WHEN l.PMID = '' THEN [1] ELSE [] END | REMOVE l.PMID)
RETURN count(l) AS merged
"""

# Undo, step 1, one transaction: while L is the only node carrying seek_study_id $study_id, restore L's journaled
# properties (seek_study_id goes with them) and its journaled IN_INVESTIGATION, and re-create K with its journaled
# properties and IN_INVESTIGATION when the journal holds one. Neo4j hands a freed element id to a new node, so each
# Investigation is matched by its journaled element id AND id; one that matches neither is not linked. Returns no
# row when L is not in that state; otherwise how many IN_INVESTIGATION L and the new K hold after it.
UNMERGE_STUDY_NODES = """
CYPHER 25
MATCH (l:Study) WHERE elementId(l) = $l AND l.seek_study_id = $study_id
  AND NOT EXISTS { MATCH (o:Study {seek_study_id: $study_id}) WHERE o <> l }
SET l = $l_props
WITH l
CALL (l) {
  MATCH (l)-[old:IN_INVESTIGATION]->()
  DELETE old
}
CALL (l) {
  MATCH (i:Investigation) WHERE elementId(i) = $l_investigation
    AND (i.id = $l_investigation_id OR (i.id IS NULL AND $l_investigation_id IS NULL))
  MERGE (l)-[:IN_INVESTIGATION]->(i)
}
CALL () {
  UNWIND CASE WHEN $k_props IS NULL THEN [] ELSE [$k_props] END AS kp
  CREATE (k:Study)
  SET k = kp
  WITH k
  CALL (k) {
    MATCH (i:Investigation) WHERE elementId(i) = $k_investigation
      AND (i.id = $k_investigation_id OR (i.id IS NULL AND $k_investigation_id IS NULL))
    MERGE (k)-[:IN_INVESTIGATION]->(i)
  }
  RETURN collect(elementId(k)) AS new_k, sum(COUNT { (k)-[:IN_INVESTIGATION]->() }) AS k_investigations
}
RETURN elementId(l) AS l, new_k, COUNT { (l)-[:IN_INVESTIGATION]->() } AS l_investigations, k_investigations
"""
# Undo: what each journaled source's element id names now ($element_ids), so a source a new node replaced is told
# apart from one that is gone. Read-only.
UNDO_SOURCE_NODES = """
UNWIND $element_ids AS eid
OPTIONAL MATCH (x) WHERE elementId(x) = eid
RETURN eid AS element_id, x IS NOT NULL AS found, x.id AS id, coalesce(labels(x), []) AS labels
"""
# Undo, step 2: rows are {source, id, labels, on_both}, a source matched by its element id AND its journaled id and
# labels (a type label aside, which a sample type's rename changes), since Neo4j hands a freed element id to a new
# node. Only a source that still links to L goes back: one journaled "on both" gets its edge to K and keeps its edge
# to L; one journaled "only on K" gets its edge to K and loses its edges to L. A source whose link to L a later
# removal took, its archive not given, is skipped either way, which keeps it true to SEEK.
UNMERGE_MOVE_BACK = """
UNWIND $rows AS r
MATCH (x) WHERE elementId(x) = r.source AND (x.id = r.id OR (x.id IS NULL AND r.id IS NULL))
  AND all(label IN r.labels WHERE label IN labels(x))
MATCH (k:Study) WHERE elementId(k) = $k
MATCH (l:Study) WHERE elementId(l) = $l
OPTIONAL MATCH (x)-[e:IN_STUDY]->(l)
WITH x, k, r, collect(e) AS on_l
WHERE size(on_l) > 0
MERGE (x)-[:IN_STUDY]->(k)
FOREACH (e IN CASE WHEN r.on_both THEN [] ELSE on_l END | DELETE e)
RETURN count(DISTINCT x) AS restored
"""
# Re-create archived IN_STUDY links: rows are {sample_id, study_id, seek_study_id} as in_study_removed.tsv holds
# them. A Study is found by id when the archive names one, else by seek_study_id on a node with no id; a sample or a
# Study that is gone restores nothing. The count is of rows that found their link's two ends, not of MERGE's rows.
RESTORE_IN_STUDY = """
UNWIND $rows AS r
MATCH (s:Sample {id: r.sample_id})
OPTIONAL MATCH (a:Study {id: r.study_id})
OPTIONAL MATCH (b:Study {seek_study_id: r.seek_study_id}) WHERE r.study_id IS NULL AND b.id IS NULL
WITH r, s, coalesce(a, b) AS st WHERE st IS NOT NULL
MERGE (s)-[:IN_STUDY]->(st)
RETURN count(DISTINCT r) AS restored
"""

# --- gate G's reads of the small tables (family 14) and of IN_PROJECT (check 2) ------------------------------------

GRAPH_PROJECTS = "MATCH (p:Project) RETURN p.id AS id, p.title AS title"
GRAPH_INVESTIGATIONS = """
MATCH (i:Investigation)
RETURN i.id AS id, i.title AS title, [(i)-[:IN_PROJECT]->(p:Project) | p.id] AS project_ids,
       """ + _INVESTIGATION_HELD + """ AS held
"""
GRAPH_MEMBER_OF = """
MATCH (pe:Person)-[m:MEMBER_OF]->(p:Project)
RETURN pe.id AS person_id, p.id AS project_id, m.has_left AS has_left
"""
# Per Project, the Samples linked to it by IN_PROJECT; and the IN_PROJECT edges a Sample's project_ids do not name.
IN_PROJECT_DEGREES = "MATCH (p:Project) RETURN p.id AS id, COUNT { (p)<-[:IN_PROJECT]-(:Sample) } AS n"
IN_PROJECT_EXTRA = """
MATCH (s:Sample)-[:IN_PROJECT]->(p:Project) WHERE NOT p.id IN coalesce(s.project_ids, [])
RETURN count(*) AS n
"""

# --- the studies tool's paper studies (graph_sync/paper_studies.py; the tool spec, 7.6 and 7.7) ---------------

# The IN_STUDY edges from these samples to one graph-only paper Study (an `id`, no `seek_study_id`).
PAPER_IN_STUDY_OF = """
UNWIND $ids AS id
MATCH (s:Sample {id: id})-[e:IN_STUDY]->(st:Study {id: $paper_id})
WHERE st.seek_study_id IS NULL
RETURN s.id AS sample_id, elementId(e) AS element_id
ORDER BY sample_id
"""
# Deletes an archived edge only while it is still an IN_STUDY to that paper Study.
DELETE_PAPER_IN_STUDY = """
UNWIND $element_ids AS eid
MATCH (:Sample)-[e:IN_STUDY]->(st:Study)
WHERE elementId(e) = eid AND st.id = $paper_id AND st.seek_study_id IS NULL
DELETE e
RETURN count(*) AS deleted
"""
# Graph-only paper Study nodes (an `id`, no `seek_study_id`) that hold no IN_STUDY and nothing but their
# IN_INVESTIGATION, each with what restoring it needs. A SEEK study's node is never one of them: Study nodes of SEEK
# studies are not deleted (the studies release).
EMPTY_PAPER_STUDY_NODES = """
UNWIND $ids AS id
MATCH (st:Study {id: id})
WHERE st.seek_study_id IS NULL AND NOT EXISTS { (st)<-[:IN_STUDY]-() }
  AND COUNT { (st)--() } = COUNT { (st)-[:IN_INVESTIGATION]->() }
RETURN st.id AS study_id, elementId(st) AS element_id, properties(st) AS props,
       [(st)-[:IN_INVESTIGATION]->(i) | i.id] AS investigation_ids
"""
DELETE_EMPTY_PAPER_STUDY_NODES = """
UNWIND $element_ids AS eid
MATCH (st:Study)
WHERE elementId(st) = eid AND st.seek_study_id IS NULL AND NOT EXISTS { (st)<-[:IN_STUDY]-() }
  AND COUNT { (st)--() } = COUNT { (st)-[:IN_INVESTIGATION]->() }
DETACH DELETE st
RETURN count(*) AS deleted
"""
# Restores, each a no-op for what already exists. The node restore uses a scoped CALL subquery, hence CYPHER 25.
RESTORE_PAPER_STUDY_NODES = """
CYPHER 25
UNWIND $rows AS r
OPTIONAL MATCH (existing:Study {id: r.study_id})
WITH r, existing WHERE existing IS NULL
CREATE (st:Study)
SET st = r.props
WITH st, r
CALL (st, r) {
  UNWIND r.investigation_ids AS iid
  MATCH (i:Investigation {id: iid})
  MERGE (st)-[:IN_INVESTIGATION]->(i)
}
RETURN count(st) AS restored
"""
RESTORE_PAPER_IN_STUDY = """
UNWIND $rows AS r
MATCH (s:Sample {id: r.sample_id})
MATCH (st:Study {id: r.study_id})
WHERE st.seek_study_id IS NULL AND NOT EXISTS { (s)-[:IN_STUDY]->(st) }
CREATE (s)-[:IN_STUDY]->(st)
RETURN count(*) AS restored
"""


# --- the studies tool's share check (a share's samples in the graph, read only; tool spec 16.8) ----------------------

# Per id: whether the Sample exists, carries the project in project_ids and an IN_PROJECT to it, has an IN_STUDY to the
# SEEK study's node, and is a paper sample (an IN_STUDY to a Study with no seek_study_id).
SHARE_CHECK = """
UNWIND $ids AS id
OPTIONAL MATCH (s:Sample {id: id})
RETURN id,
       s IS NOT NULL AS found,
       coalesce($project_id IN s.project_ids, false) AS has_project,
       s IS NOT NULL AND EXISTS { (s)-[:IN_PROJECT]->(:Project {id: $project_id}) } AS in_project,
       s IS NOT NULL AND EXISTS { (s)-[:IN_STUDY]->(:Study {seek_study_id: $study_id}) } AS in_study,
       s IS NOT NULL AND EXISTS { (s)-[:IN_STUDY]->(p:Study) WHERE p.seek_study_id IS NULL } AS paper
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


# --- the assay layer (schema 1.3) ------------------------------------------------------------------

# The constraint step applies these with CONSTRAINTS_V11; gate G reads their names from the contract. Never a gs_
# name: the index budget drops those. Rendered from the contract's 1.3 groups by the module's DDL templates, the same
# way CONSTRAINTS_V11 is rendered from the 1.1 groups; the writer test pins the two statements' exact text.
ASSAY_CONSTRAINTS = tuple([_ddl(_UNIQUE_DDL, *triple) for triple in schema.UNIQUE_CONSTRAINTS_V13]
                          + [_ddl(_RANGE_DDL, *triple) for triple in schema.RANGE_INDEXES_V13])
# One row per internal assay; the property map is replaced whole (catalog facts only).
MERGE_ASSAYS = """
UNWIND $rows AS r
MERGE (a:Assay {id: r.id})
SET a = r
RETURN count(a) AS written
"""
# An Assay whose id left internal_assays: every edge the assay layer gives it, a batch at a time, then the node.
DELETE_GONE_ASSAY_EDGES = """
MATCH (a:Assay) WHERE NOT a.id IN $ids
MATCH (a)-[r:INPUT_TO|OUTPUT_OF|RUN_IN|ACCEPTED_BY|GENERATES]-(:Sample|Study|SampleType)
WITH r LIMIT $batch
DELETE r
RETURN count(*) AS deleted
"""
DELETE_GONE_ASSAYS = """
MATCH (a:Assay) WHERE NOT a.id IN $ids
DETACH DELETE a
RETURN count(*) AS deleted
"""
# RUN_IN replaced whole: rows are {assay_id, study_id, seek_assay_ids}; the Study nodes exist first
# (writer.write_seek_study_nodes). A merged Study node is found by seek_study_id like any other. The delete runs once
# even when $rows is empty.
REPLACE_ASSAY_RUNS = """
CYPHER 25
CALL () { MATCH (:Assay)-[old:RUN_IN]->(:Study) DELETE old }
UNWIND $rows AS r
MATCH (a:Assay {id: r.assay_id})
MATCH (st:Study {seek_study_id: r.study_id})
CREATE (a)-[:RUN_IN {seek_assay_ids: r.seek_assay_ids}]->(st)
RETURN count(*) AS linked
"""
# ACCEPTED_BY and GENERATES replaced whole, from the curated catalog: $accepted rows are {code, assay_id, required,
# group}, $generates rows {assay_id, code, group}; a code is a SampleType title.
REPLACE_ASSAY_CATALOG_EDGES = """
CYPHER 25
CALL () { MATCH (:SampleType)-[old:ACCEPTED_BY]->(:Assay) DELETE old }
CALL () { MATCH (:Assay)-[old:GENERATES]->(:SampleType) DELETE old }
CALL () {
  UNWIND $accepted AS r
  MATCH (t:SampleType {title: r.code})
  MATCH (a:Assay {id: r.assay_id})
  CREATE (t)-[:ACCEPTED_BY {required: r.required, group: r.group}]->(a)
  RETURN count(*) AS accepted
}
CALL () {
  UNWIND $generates AS r
  MATCH (a:Assay {id: r.assay_id})
  MATCH (t:SampleType {title: r.code})
  CREATE (a)-[:GENERATES {group: r.group}]->(t)
  RETURN count(*) AS generates
}
RETURN accepted, generates
"""
# A chunk of samples' INPUT_TO and OUTPUT_OF, replaced whole in one statement: rows are {id, inputs, outputs}, each
# edge {assay_id, seek_assay_ids}. An empty row deletes the sample's edges. The counting subqueries always return one
# row, so a sample whose Assay node is missing is still reached and shows up as a shortfall.
REPLACE_SAMPLE_ASSAY_EDGES = """
CYPHER 25
UNWIND $rows AS r
MATCH (s:Sample {id: r.id})
CALL (s) { MATCH (s)-[old:INPUT_TO|OUTPUT_OF]->(:Assay) DELETE old }
CALL (s, r) {
  UNWIND r.inputs AS e
  MATCH (a:Assay {id: e.assay_id})
  CREATE (s)-[:INPUT_TO {seek_assay_ids: e.seek_assay_ids}]->(a)
  RETURN count(a) AS inputs
}
CALL (s, r) {
  UNWIND r.outputs AS e
  MATCH (a:Assay {id: e.assay_id})
  CREATE (s)-[:OUTPUT_OF {seek_assay_ids: e.seek_assay_ids}]->(a)
  RETURN count(a) AS outputs
}
RETURN count(s) AS samples, sum(inputs) AS inputs, sum(outputs) AS outputs,
       sum(size(r.inputs) + size(r.outputs)) AS expected
"""
# Reads. The (SEEK id, Assay id) pairs RUN_IN holds, and those the sample edges hold (sync_assays step 3).
RUN_IN_PAIRS = """
MATCH (a:Assay)-[r:RUN_IN]->(:Study)
UNWIND r.seek_assay_ids AS seek_assay_id
RETURN DISTINCT seek_assay_id, a.id AS assay_id
"""
SAMPLE_ASSAY_EDGE_PAIRS = """
MATCH (:Sample)-[r:INPUT_TO|OUTPUT_OF]->(a:Assay)
UNWIND r.seek_assay_ids AS seek_assay_id
RETURN DISTINCT seek_assay_id, a.id AS assay_id
"""
SAMPLES_CARRYING_SEEK_ASSAYS = """
MATCH (s:Sample)-[r:INPUT_TO|OUTPUT_OF]->(:Assay)
WHERE any(x IN r.seek_assay_ids WHERE x IN $seek_ids)
RETURN DISTINCT s.id AS id
"""
# Every DERIVED_FROM between two Sample nodes with an end among $ids, as (child, parent), once each: what the role
# rule reads for these samples. An edge touching an OrphanSample is not lineage here.
LINEAGE_PAIRS_INCIDENT = """
UNWIND $ids AS id
MATCH (c:Sample {id: id})-[:DERIVED_FROM]->(p:Sample)
RETURN c.id AS child_id, p.id AS parent_id
UNION
UNWIND $ids AS id
MATCH (c:Sample)-[:DERIVED_FROM]->(p:Sample {id: id})
RETURN c.id AS child_id, p.id AS parent_id
"""
# How many DERIVED_FROM edges each Sample of $ids has, either direction (the relationship count, cheap): a partner
# above targeted.PARTNER_REWRITE_MAX is handed to the loop.
SAMPLE_LINEAGE_DEGREES = """
UNWIND $ids AS id
MATCH (s:Sample {id: id})
RETURN s.id AS id, COUNT { (s)-[:DERIVED_FROM]-() } AS degree
"""
# One keyset page of the Sample ids holding an INPUT_TO or OUTPUT_OF, over the Sample.id index: the full sync
# deletes the edges of those that no longer have a role.
SAMPLE_IDS_WITH_ASSAY_EDGES_PAGE = """
MATCH (s:Sample) WHERE s.id > $after AND EXISTS { (s)-[:INPUT_TO|OUTPUT_OF]->(:Assay) }
RETURN s.id AS id
ORDER BY s.id LIMIT $limit
"""

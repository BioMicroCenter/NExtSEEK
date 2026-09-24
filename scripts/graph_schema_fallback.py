#!/usr/bin/env python3
"""Regenerate the graph agent's committed fallback files from a live graph, read only.

When the live catalog cannot be read, the graph agent falls back to three committed files in
``NessieAI/chat_nextseek/src/chat_nextseek/context/`` (``ChatConfig.NEO4J_SCHEMA``, ``PROTOCOL_SCHEMA`` and
``ASSAY_SAMPLE_CONNECTIONS``). The config stopped refreshing them from Neo4j in 689f00be, so this program is how they
change now:

- ``neo4j_schema.json``: node labels, relationship types, each label's property names, each relationship type's
  property names, the relationship patterns between the labels an agent queries, and the vocabulary. On a fallback
  turn the whole file is the schema message, and the type-blind property guard allows exactly the property names
  it lists, so ``Sample`` lists the system properties and every attribute title that holds a value somewhere: with
  fewer, the guard refuses every metadata question.
- ``neo4j_protocol_schema.json``: the DERIVED_FROM protocol titles.
- ``neo4j_assay-sample-conn.json``: each (assay, parent type, child type) a DERIVED_FROM edge carries, with every
  assay an edge names (``internal_assay_title`` and the plural ``internal_assay_titles``).

Only edges between two ``Sample`` nodes count, and ``T_`` labels and ``GraphMeta`` are left out, as the live
vocabulary does (``graph_catalog.VOCAB_EDGES``). Every statement runs in a READ transaction. Lists are sorted, so a
rerun on an unchanged graph differs only in ``fetched_at``. The files describe the graph they were read from: say
which one in the commit.

Run it inside the app container, which has the driver and the Neo4j environment, then copy the files out::

    docker exec -i nextseek /app/.venv/bin/python - --out /tmp/graph-fallback < scripts/graph_schema_fallback.py
    docker cp nextseek:/tmp/graph-fallback/. NessieAI/chat_nextseek/src/chat_nextseek/context/
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from neo4j import READ_ACCESS, GraphDatabase, unit_of_work

try:  # the guard's own list, so the fallback and the guard name the same system properties
    from chat_nextseek.agents.graph import V12_SYSTEM_PROPERTIES
except ImportError:  # outside the app image: the same set, docs/neo4j-schema.md v1.1 and v1.2
    V12_SYSTEM_PROPERTIES = frozenset({"id", "uuid", "type", "title", "project_ids", "search_text", "synced_at",
                                       "source_hash", "parent_titles", "parent_title_hashes"})

TIMEOUT_S = 300
NOT_QUERIED = ("GraphMeta",)  # graph_sync's bookkeeping node; no question reads it

META = "MATCH (m:GraphMeta) RETURN m.schema_version AS version, m.catalog_hash AS hash, m.synced_at AS synced"
LABELS = "CALL db.labels() YIELD label RETURN label ORDER BY label"
REL_TYPES = "CALL db.relationshipTypes() YIELD relationshipType RETURN relationshipType AS t ORDER BY t"
ATTRIBUTE_TITLES = "MATCH (a:Attribute) WHERE a.sample_count > 0 RETURN DISTINCT a.title AS k ORDER BY k"
PATTERNS = """
MATCH (a)-[r:`{t}`]->(b) WHERE NOT a:OrphanSample AND NOT b:OrphanSample
RETURN DISTINCT [l IN labels(a) WHERE NOT l STARTS WITH 'T_'][0] AS start,
                [l IN labels(b) WHERE NOT l STARTS WITH 'T_'][0] AS end
""".strip()
VOCAB = {
    "investigation_titles": "MATCH (i:Investigation) WHERE i.title IS NOT NULL RETURN DISTINCT i.title AS v",
    "project_titles": "MATCH (p:Project) WHERE p.title IS NOT NULL RETURN DISTINCT p.title AS v",
    "study_titles": "MATCH (s:Study) WHERE s.title IS NOT NULL RETURN DISTINCT s.title AS v",
    "sampletype_titles": "MATCH (t:SampleType) WHERE NOT coalesce(t.deprecated, false) RETURN t.title AS v",
    "internal_assay_titles": """
MATCH (:Sample)-[r:DERIVED_FROM]->(:Sample)
UNWIND [r.internal_assay_title] + coalesce(r.internal_assay_titles, []) AS v
WITH v WHERE v IS NOT NULL RETURN DISTINCT v
""".strip(),
}
PROTOCOLS = """
MATCH (:Sample)-[r:DERIVED_FROM]->(:Sample) WHERE r.protocol_title IS NOT NULL
RETURN DISTINCT r.protocol_title AS v
""".strip()
CONNECTIONS = """
MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample)
UNWIND [r.internal_assay_title] + coalesce(r.internal_assay_titles, []) AS assay
WITH assay, p.type AS parent_type, c.type AS child_type WHERE assay IS NOT NULL
RETURN DISTINCT assay, parent_type, child_type
""".strip()


def read(session, statement: str, **params) -> list[dict]:
    @unit_of_work(timeout=TIMEOUT_S)
    def work(tx):
        return [record.data() for record in tx.run(statement, params)]
    return session.execute_read(work)


def keys_of(session, pattern: str) -> list[str]:
    return sorted(row["k"] for row in read(session, f"MATCH {pattern} UNWIND keys(x) AS k RETURN DISTINCT k"))


def build(session) -> dict[str, dict]:
    meta = (read(session, META) or [{}])[0]
    fetched_at = datetime.now(timezone.utc).isoformat()
    labels = [row["label"] for row in read(session, LABELS)
              if not row["label"].startswith("T_") and row["label"] not in NOT_QUERIED]
    rel_types = [row["t"] for row in read(session, REL_TYPES)]

    node_properties = {}
    for label in labels:
        if label == "Sample":
            titles = {row["k"] for row in read(session, ATTRIBUTE_TITLES)}
            node_properties[label] = sorted(set(V12_SYSTEM_PROPERTIES) | titles)
        else:
            node_properties[label] = keys_of(session, f"(x:`{label}`)")
    relationship_properties = {t: keys_of(session, f"()-[x:`{t}`]->()") for t in rel_types}

    patterns = []
    for t in rel_types:
        for row in read(session, PATTERNS.format(t=t)):
            if row["start"] in labels and row["end"] in labels:
                patterns.append({"start": row["start"], "type": t, "end": row["end"]})
    patterns.sort(key=lambda p: (p["type"], p["start"], p["end"]))

    # No published_studies block: every title is already in study_titles and DOI and PMID are Study properties,
    # while the pairs would add about 10 KB to a message that carries this whole file.
    vocabulary = {name: sorted(row["v"] for row in read(session, statement)) for name, statement in VOCAB.items()}

    schema = {
        "fetched_at": fetched_at,
        "generated_by": "scripts/graph_schema_fallback.py",
        "schema_version": meta.get("version"),
        "catalog_hash": meta.get("hash"),
        "graph_synced_at": str(meta.get("synced")) if meta.get("synced") is not None else None,
        "node_labels": labels,
        "relationship_types": rel_types,
        "node_properties": node_properties,
        "relationship_properties": relationship_properties,
        "relationship_patterns": patterns,
        "vocabulary": vocabulary,
        "graph_topology": [f"{p['start']} -[:{p['type']}]-> {p['end']}" for p in patterns],
    }
    protocols = {"fetched_at": fetched_at,
                 "protocol_titles": sorted(row["v"] for row in read(session, PROTOCOLS))}
    connections = {"fetched_at": fetched_at,
                   "connections": sorted(read(session, CONNECTIONS),
                                         key=lambda c: (c["assay"], c["parent_type"] or "", c["child_type"] or ""))}
    return {"neo4j_schema.json": schema, "neo4j_protocol_schema.json": protocols,
            "neo4j_assay-sample-conn.json": connections}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, type=Path, help="directory the three files are written to")
    args = parser.parse_args()
    uri = os.environ.get("NEO4J_URI") or "neo4j://neo4j:7687"
    auth = (os.environ.get("NEO4J_USER") or "neo4j", os.environ["NEO4J_PASSWORD"])
    database = os.environ.get("NEO4J_DATABASE") or "neo4j"
    with GraphDatabase.driver(uri, auth=auth) as driver:
        with driver.session(database=database, default_access_mode=READ_ACCESS) as session:
            files = build(session)
    args.out.mkdir(parents=True, exist_ok=True)
    for name, payload in files.items():
        (args.out / name).write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"wrote {args.out / name}: {len(json.dumps(payload, indent=2, ensure_ascii=False)):,} bytes",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

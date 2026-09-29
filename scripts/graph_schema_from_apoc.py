#!/usr/bin/env python3
"""Prototype, not wired into the product: build the graph's STRUCTURAL schema from APOC's meta procedures and
compare it with what Nessie uses today.

The question it answers: could APOC build the graph schema instead of the hand-kept files and the catalog the sync
writes? It reads, and only reads:

- ``apoc.meta.stats``: label and relationship-type counts from the count store (no scan);
- ``apoc.meta.schema``: per label, a count, the property keys with their types, and the relationships with their
  direction and the labels at the other end, from a sample of nodes (``--full`` scans every node);
- ``apoc.meta.nodeTypeProperties`` / ``relTypeProperties``: per label combination, each property's observed types and
  how many sampled nodes carried it (``--full`` observes every node);
- the catalog graph_sync writes, ``(:SampleType)-[:HAS_ATTRIBUTE]->(:Attribute)``, which is what
  ``graph_catalog.GUARD`` reads for the graph agent's per-label property sets.

It then compares APOC's view with ``prompts/graph_schema_structure.txt`` (the hand-written structure every graph turn
reads), ``context/min_graph_schema.json`` (the parser's view of the graph), the guard's hand-kept property sets in
``agents/graph.py`` (``V11_NODE_PROPERTIES``, ``V11_RELATIONSHIP_PROPERTIES``, ``V12_SYSTEM_PROPERTIES``) and the
catalog, and writes one JSON document with the timings.

Safety: every statement runs in a READ transaction with a timeout, and no path procedure is called. apoc.meta walks
nodes and their relationships one at a time, so its memory is the size of its answer; the full scans take tens of
seconds (measured 2026-09-17 on the local 1.2 graph: nodeTypeProperties 48 s, schema 12 s), which is why they are
behind ``--full``.

Run it in a throwaway container of the app image on the stack's network, from the repository root::

    docker run --rm --network nextseek_default -e NEO4J_URI=neo4j://neo4j -e NEO4J_USER=neo4j \\
      -e NEO4J_PASSWORD -v "$PWD":/src:ro -v "$OUT":/out -w /src nextseek-nextseek:latest \\
      /app/.venv/bin/python scripts/graph_schema_from_apoc.py --out /out/apoc_schema.json [--full]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "NessieAI" / "chat_nextseek" / "src" / "chat_nextseek"
STRUCTURE_TXT = PACKAGE / "prompts" / "graph_schema_structure.txt"
MIN_GRAPH_SCHEMA = PACKAGE / "context" / "min_graph_schema.json"

STATS = ("CALL apoc.meta.stats() YIELD labelCount, relTypeCount, propertyKeyCount, nodeCount, relCount, labels, "
         "relTypesCount RETURN labelCount, relTypeCount, propertyKeyCount, nodeCount, relCount, labels, relTypesCount")
SCHEMA = "CALL apoc.meta.schema($config) YIELD value RETURN value"
NODE_TYPE_PROPERTIES = ("CALL apoc.meta.nodeTypeProperties($config) YIELD nodeType, nodeLabels, propertyName, "
                        "propertyTypes, mandatory, propertyObservations, totalObservations "
                        "RETURN nodeType, nodeLabels, propertyName, propertyTypes, mandatory, propertyObservations, "
                        "totalObservations")
REL_TYPE_PROPERTIES = ("CALL apoc.meta.relTypeProperties($config) YIELD relType, propertyName, propertyTypes, "
                       "mandatory, propertyObservations, totalObservations "
                       "RETURN relType, propertyName, propertyTypes, mandatory, propertyObservations, totalObservations")
CATALOG = ("MATCH (t:SampleType) OPTIONAL MATCH (t)-[:HAS_ATTRIBUTE]->(a:Attribute) "
           "RETURN t.title AS title, t.label AS label, t.sample_count AS sample_count, "
           "collect(a {.title, .value_type, .sample_count, .declared}) AS attributes")


def _read(session, statement: str, params: dict | None, timeout: float) -> tuple[list[dict], float]:
    from neo4j import unit_of_work

    @unit_of_work(timeout=timeout)
    def work(tx):
        return [record.data() for record in tx.run(statement, params or {})]

    start = time.perf_counter()
    rows = session.execute_read(work)
    return rows, round(time.perf_counter() - start, 3)


def _type_labels(node_type: str) -> list[str]:
    return re.findall(r"`([^`]+)`", node_type)


def per_label_keys(rows: list[dict]) -> dict[str, dict]:
    """nodeTypeProperties rows -> {T_label: {keys: {name: {types, observed}}, observed_nodes}} for Sample node types."""
    out: dict[str, dict] = {}
    for row in rows:
        labels = _type_labels(row["nodeType"])
        type_labels = [label for label in labels if label.startswith("T_")]
        key = type_labels[0] if len(type_labels) == 1 else ":".join(labels)
        entry = out.setdefault(key, {"keys": {}, "observed_nodes": 0, "node_type": row["nodeType"]})
        entry["observed_nodes"] = max(entry["observed_nodes"], row.get("totalObservations") or 0)
        if row.get("propertyName"):
            entry["keys"][row["propertyName"]] = {"types": row.get("propertyTypes") or [],
                                                  "observed": row.get("propertyObservations") or 0}
    return out


def structure_relationships(text: str) -> set[tuple[str, str, str]]:
    """(source label, TYPE, target label) triples the hand-written structure names, e.g. ('Sample', 'OF_TYPE', ...)."""
    triples = set()
    for chain in re.finditer(r"\([^()]*\)(?:-\[[^\]]*\]->\([^()]*\))+", text):
        seq = []
        for node, rel in re.findall(r"\(([^()]*)\)|-\[:?(\w+)[^\]]*\]->", chain.group(0)):
            if rel:
                seq.append(rel)
            else:
                label = re.search(r":\s*(\w+)", node)
                seq.append(label.group(1) if label else "?")
        for i in range(0, len(seq) - 2, 2):
            if "?" not in (seq[i], seq[i + 2]):
                triples.add((seq[i], seq[i + 1], seq[i + 2]))
    return triples


def apoc_relationships(schema: dict) -> set[tuple[str, str, str]]:
    """(source label, TYPE, target label) from apoc.meta.schema, for the labels the structure talks about."""
    triples = set()
    for label, entry in schema.items():
        if entry.get("type") != "node":
            continue
        for rel_type, rel in (entry.get("relationships") or {}).items():
            if rel.get("direction") != "out":
                continue
            for other in rel.get("labels") or []:
                triples.add((label, rel_type, other))
    return triples


def compare(result: dict) -> dict:
    sys.path.insert(0, str(PACKAGE.parent))
    from chat_nextseek.agents.graph import (V11_NODE_PROPERTIES, V11_RELATIONSHIP_PROPERTIES,  # noqa: E402
                                            V12_SYSTEM_PROPERTIES)

    catalog = {row["label"]: row for row in result["catalog"] if row.get("label")}
    comparison: dict = {}
    for pass_name in ("sampled", "full"):
        keys = result.get(f"node_type_properties_{pass_name}")
        if keys is None:
            continue
        per_type, missed_total, filled_total, missed_counts = {}, 0, 0, []
        for label, row in sorted(catalog.items()):
            filled = {a["title"]: a for a in row["attributes"] if a and (a.get("sample_count") or 0) > 0}
            seen = set(keys.get(label, {}).get("keys", {})) - V12_SYSTEM_PROPERTIES
            missed = sorted(set(filled) - seen)
            extra = sorted(seen - set(filled))
            filled_total += len(filled)
            missed_total += len(missed)
            missed_counts += [filled[t].get("sample_count") or 0 for t in missed]
            if missed or extra:
                per_type[label] = {"samples": row.get("sample_count"), "filled": len(filled), "missed": missed,
                                   "missed_sample_counts": [filled[t].get("sample_count") for t in missed],
                                   "extra_in_apoc": extra}
        comparison[f"catalog_vs_{pass_name}"] = {
            "catalog_filled_attributes": filled_total, "missed_by_apoc": missed_total,
            "types_with_a_miss": sum(1 for v in per_type.values() if v["missed"]),
            "missed_sample_count_max": max(missed_counts) if missed_counts else 0,
            "per_type": per_type}

    full = result.get("node_type_properties_full") or result.get("node_type_properties_sampled") or {}
    mixed, type_mismatch = [], []
    for label, row in sorted(catalog.items()):
        for a in row["attributes"]:
            if not a or not (a.get("sample_count") or 0):
                continue
            observed = full.get(label, {}).get("keys", {}).get(a["title"])
            if not observed:
                continue
            types = sorted(set(observed["types"]))
            if len(types) > 1:
                mixed.append({"label": label, "attribute": a["title"], "apoc_types": types,
                              "catalog_value_type": a.get("value_type")})
            elif a.get("value_type") and types and not _type_agrees(a["value_type"], types[0]):
                type_mismatch.append({"label": label, "attribute": a["title"], "apoc_types": types,
                                      "catalog_value_type": a.get("value_type")})
    comparison["value_types"] = {"mixed_in_apoc": mixed, "disagree": type_mismatch}

    schema = result["schema_sampled"]
    live_labels = {k for k, v in schema.items() if v.get("type") == "node"}
    live_rels = {k for k, v in schema.items() if v.get("type") == "relationship"}
    structure_text = STRUCTURE_TXT.read_text(encoding="utf-8")
    named = structure_relationships(structure_text)
    live = {t for t in apoc_relationships(schema) if not t[0].startswith("T_") and not t[2].startswith("T_")}
    comparison["structure_txt"] = {
        "labels_named": sorted(set(re.findall(r"\(:(\w+)", structure_text))),
        "relationships_named_not_live": sorted(map(list, named - live)),
        "relationships_live_not_named": sorted(map(list, live - named)),
    }
    min_schema = json.loads(MIN_GRAPH_SCHEMA.read_text(encoding="utf-8"))
    comparison["min_graph_schema_json"] = {
        "labels": [n.get("label") for n in min_schema.get("node_types", [])],
        "relationships": [r.get("type") for r in min_schema.get("relationships", [])],
        "live_labels_not_type": sorted(label for label in live_labels if not label.startswith("T_")),
        "live_relationship_types": sorted(live_rels),
        "type_labels_live": sum(1 for label in live_labels if label.startswith("T_")),
    }
    guard_nodes = {}
    for label, props in V11_NODE_PROPERTIES.items():
        live_props = set((schema.get(label) or {}).get("properties") or {})
        guard_nodes[label] = {"live_not_in_guard": sorted(live_props - props),
                              "guard_not_live": sorted(props - live_props)}
    guard_rels = {}
    for rel_type, props in V11_RELATIONSHIP_PROPERTIES.items():
        live_props = set((schema.get(rel_type) or {}).get("properties") or {})
        guard_rels[rel_type] = {"live_not_in_guard": sorted(live_props - props),
                                "guard_not_live": sorted(props - live_props)}
    sample_live = set((schema.get("Sample") or {}).get("properties") or {})
    comparison["guard_sets"] = {"nodes": guard_nodes, "relationships": guard_rels,
                                "system_properties_not_on_sample": sorted(V12_SYSTEM_PROPERTIES - sample_live)}
    return comparison


def _type_agrees(value_type: str, apoc_type: str) -> bool:
    expected = {"float": {"Double", "Float"}, "integer": {"Long", "Integer"}, "date": {"Date", "LocalDate"},
                "string": {"String"}}
    return apoc_type in expected.get(value_type, {apoc_type})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--out", required=True, help="where to write the JSON result")
    parser.add_argument("--full", action="store_true", help="also scan every node (tens of seconds)")
    parser.add_argument("--timeout", type=float, default=110.0, help="per-statement transaction timeout, seconds")
    args = parser.parse_args(argv)

    from neo4j import GraphDatabase

    uri, user, password = (os.environ.get("NEO4J_URI", "neo4j://neo4j"), os.environ.get("NEO4J_USER", "neo4j"),
                           os.environ.get("NEO4J_PASSWORD"))
    if not password:
        print("NEO4J_PASSWORD is not set", file=sys.stderr)
        return 2
    result: dict = {"timings_s": {}}
    with GraphDatabase.driver(uri, auth=(user, password)) as driver, driver.session() as session:
        def read(name, statement, params=None):
            rows, seconds = _read(session, statement, params, args.timeout)
            result["timings_s"][name] = seconds
            print(f"{name}: {seconds} s, {len(rows)} rows", file=sys.stderr)
            return rows

        result["stats"] = read("apoc.meta.stats", STATS)[0]
        result["schema_sampled"] = read("apoc.meta.schema (sampled)", SCHEMA, {"config": {}})[0]["value"]
        result["node_type_properties_sampled"] = per_label_keys(
            read("apoc.meta.nodeTypeProperties (sampled)", NODE_TYPE_PROPERTIES, {"config": {}}))
        result["rel_type_properties"] = read("apoc.meta.relTypeProperties (sampled)", REL_TYPE_PROPERTIES,
                                             {"config": {}})
        if args.full:
            result["schema_full_sample_labels"] = {
                k: len(v.get("properties") or {}) for k, v in read(
                    "apoc.meta.schema (full)", SCHEMA, {"config": {"sample": -1}})[0]["value"].items()}
            result["node_type_properties_full"] = per_label_keys(
                read("apoc.meta.nodeTypeProperties (full)", NODE_TYPE_PROPERTIES, {"config": {"sample": -1}}))
        result["catalog"] = read("catalog (SampleType, Attribute)", CATALOG)
    result["comparison"] = compare(result)
    Path(args.out).write_text(json.dumps(result, indent=1, default=str, sort_keys=True), encoding="utf-8")
    summary = {k: {kk: vv for kk, vv in v.items() if kk != "per_type"} for k, v in result["comparison"].items()
               if k.startswith("catalog_vs_")}
    print(json.dumps({"timings_s": result["timings_s"], **summary}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

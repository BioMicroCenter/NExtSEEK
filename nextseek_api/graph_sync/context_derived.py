"""The live columns of ``dmac.sample_types_context`` and ``dmac.assay_context``, and ``sample_type_connections``.

``derive`` is pure, like ``catalog.py``: plain rows in, the cells that differ out. ``sync_context`` is the one writer:
it reads SEEK, the dmac tables and the graph's observed connections, calls ``derive``, and writes the changed cells and
the connections table in one transaction. The graph_sync drain runs it for an outbox ``context`` row; the nightly
reconcile and the weekly full sync run it after their samples step; ``manage.py graph_sync --context [--dry-run]``
runs it by hand.

Rules this module keeps:

- **Joined by code and title, never by id.** A context row is the SEEK sample type whose title equals its
  ``sample_type`` (the lowest id when a title repeats); every id is recomputed on each box.
- **Hand columns are never written.** Name, description, Tags, repository fields, every assay_context text column and
  the designed connections (required, optional parents, children) belong to people. The live columns are
  ``sampletype_id``, ``clade``, the three metadata lists, the four connection lists, and ``internal_assay_id``.
- **No row is added or deleted.** A SEEK type or internal assay with no context row is reported as
  ``needs_definition``; a context row whose code this box lacks is left alone and reported as ``no_seek_type``.
- **Tiers are hand-set, membership is SEEK's.** A title SEEK no longer has leaves its list; a SEEK-required title
  joins ``required_metadata``; every other SEEK title not in a tier is ``possible_metadata_fields``. A type with no
  SEEK attributes keeps its lists.
- **Connections are designed (assay_context) plus observed (graph DERIVED_FROM), flagged, never counted** (ruling
  2026-10-30). Only codes that are SEEK titles on this box are kept; the rest are ``unknown_codes``.
- **Clade follows the Clades page** (``sample_types_clades``) where the type has an entry, else stays as typed.
- **A valid internal_assay_id is kept**, even when its title differs from ``assay_name`` (reported as
  ``assay_title_mismatch``); a missing or dangling one is filled from the exact title, or cleared.
- **Small diffs.** A rewritten list keeps the order of the entries that stay and appends new ones (sorted, or in
  SEEK's attribute order for the metadata lists). An empty list is NULL. A cell is written only when its parsed list
  changes, so spacing never counts as a change.
"""
from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from nextseek_api.services.context_catalog import parse_alternation, parse_list

log = logging.getLogger(__name__)

METADATA_COLUMNS = ("required_metadata", "standard_metadata", "possible_metadata_fields")
CONNECTION_COLUMNS = ("parent_sampletypes", "child_sampletypes", "associated_assay_parents",
                      "associated_assay_children")
CODE_COLUMNS = ("parent_sampletypes", "child_sampletypes")
# The live columns graph_sync's catalog reads into SampleType nodes: a change here queues a catalog sync.
GRAPH_READ_COLUMNS = ("clade", "parent_sampletypes", "child_sampletypes")

# Observed (assay, parent type, child type) triples: the statement scripts/graph_schema_fallback.py CONNECTIONS and the
# ETL's remote_export.py run. One aggregate the server computes; 10 s over 1.8 M edges on dev (2026-11-23).
OBSERVED_CYPHER = """
MATCH (c:Sample)-[r:DERIVED_FROM]->(p:Sample)
UNWIND [r.internal_assay_title] + coalesce(r.internal_assay_titles, []) AS assay
WITH assay, p.type AS parent_type, c.type AS child_type WHERE assay IS NOT NULL
RETURN DISTINCT assay, parent_type, child_type
""".strip()


class _AnyCode:
    """A ``known`` set holding every non-blank code, so ``parse_alternation`` returns every token it reads."""

    def __contains__(self, code) -> bool:
        return bool(code)


def _codes(raw) -> list[str]:
    """Every code a curator column names, in order, once each, alternation flattened."""
    return list(dict.fromkeys(c for group in parse_alternation(raw, _AnyCode()) for c in group))


def _merge(existing: list[str], wanted: Iterable[str], order: list[str] | None = None) -> list[str]:
    """``existing`` entries still wanted, in their order, then the new ones (in ``order``, else sorted)."""
    wanted = set(wanted)
    kept = [x for x in dict.fromkeys(existing) if x in wanted]
    new = wanted - set(kept)
    tail = [x for x in order if x in new] if order is not None else sorted(new)
    return kept + tail


def _cell(values: list[str]):
    return ", ".join(values) if values else None


@dataclass
class Derived:
    sample_types: dict[str, dict] = field(default_factory=dict)   # code -> {column: new value}, changed cells only
    assays: dict[int, dict] = field(default_factory=dict)         # assay_context row id -> {"internal_assay_id": v}
    connections: list[dict] = field(default_factory=list)         # parent, assay, child, designed, observed
    report: dict = field(default_factory=dict)


def derive(*, context_rows, assay_rows, seek_types, seek_attributes, type_clades, internal_assays,
           observed) -> Derived:
    out = Derived()
    changed: Counter = Counter()
    unknown: set[str] = set()

    seek_id: dict[str, int] = {}
    for t in sorted(seek_types, key=lambda t: t["id"]):
        seek_id.setdefault(t["title"], t["id"])
    attrs: dict[int, list[dict]] = defaultdict(list)
    for a in seek_attributes:
        attrs[a["sample_type_id"]].append(a)

    def known(code) -> bool:
        if code in seek_id:
            return True
        if code:
            unknown.add(code)
        return False

    flags: dict[tuple[str, str, str], list[bool]] = {}
    for a in assay_rows:
        parents = _codes(a.get("Required_Parent_Sample_Types")) + _codes(a.get("Optional_Parent_Sample_Types"))
        children = _codes(a.get("Children_Sample_Types"))
        parents = [p for p in dict.fromkeys(parents) if known(p)]
        children = [c for c in children if known(c)]
        for p in parents:
            for c in children:
                flags.setdefault((p, a["assay_name"], c), [False, False])[0] = True
    for p, assay, c in observed:
        if assay and known(p) and known(c):
            flags.setdefault((p, assay, c), [False, False])[1] = True
    out.connections = [{"parent": p, "assay": a, "child": c, "designed": d, "observed": o}
                       for (p, a, c), (d, o) in sorted(flags.items())]
    lists = {col: defaultdict(set) for col in CONNECTION_COLUMNS}
    for p, assay, c in flags:
        lists["parent_sampletypes"][c].add(p)
        lists["child_sampletypes"][p].add(c)
        lists["associated_assay_parents"][c].add(assay)
        lists["associated_assay_children"][p].add(assay)

    no_seek_type = []
    for row in context_rows:
        code = row["sample_type"]
        sid = seek_id.get(code)
        if sid is None:
            no_seek_type.append(code)
            continue
        cells = {}
        if row.get("sampletype_id") != sid:
            cells["sampletype_id"] = sid
        clade = type_clades.get(sid)
        if clade and row.get("clade") != clade:
            cells["clade"] = clade

        titles = [a["title"] for a in attrs.get(sid, [])]
        if titles:
            present = set(titles)
            required_seek = [a["title"] for a in attrs[sid] if a["required"]]
            old = {col: parse_list(row.get(col)) for col in METADATA_COLUMNS}
            req = _merge(old["required_metadata"], (present & set(old["required_metadata"])) | set(required_seek),
                         titles)
            std = _merge(old["standard_metadata"], present & set(old["standard_metadata"]) - set(req), titles)
            pos = _merge(old["possible_metadata_fields"], present - set(req) - set(std), titles)
            for col, new in zip(METADATA_COLUMNS, (req, std, pos)):
                if new != old[col]:
                    cells[col] = _cell(new)

        for col in CONNECTION_COLUMNS:
            old = _codes(row.get(col)) if col in CODE_COLUMNS else parse_list(row.get(col))
            new = _merge(old, lists[col].get(code, ()))
            if new != old:
                cells[col] = _cell(new)
        if cells:
            out.sample_types[code] = cells
            changed.update(cells.keys())

    title_by_id = {r["id"]: r["internal_assay_title"] for r in internal_assays}
    id_by_title: dict[str, int] = {}
    for r in sorted(internal_assays, key=lambda r: r["id"]):
        id_by_title.setdefault(r["internal_assay_title"], r["id"])
    mismatch = []
    for a in assay_rows:
        cur = a.get("internal_assay_id")
        if cur in title_by_id:
            if title_by_id[cur] != a["assay_name"]:
                mismatch.append({"assay_name": a["assay_name"], "internal_assay_id": cur,
                                 "internal_assay_title": title_by_id[cur]})
            continue
        new = id_by_title.get(a["assay_name"])
        if new != cur:
            out.assays[a["id"]] = {"internal_assay_id": new}
            changed["internal_assay_id"] += 1

    out.report = {
        "changed_cells": dict(changed),
        "rows_changed": {"sample_types": len(out.sample_types), "assays": len(out.assays)},
        "connections": {"total": len(out.connections),
                        "designed": sum(c["designed"] for c in out.connections),
                        "observed": sum(c["observed"] for c in out.connections)},
        "no_seek_type": sorted(no_seek_type),
        "unknown_codes": sorted(unknown),
        "assay_title_mismatch": mismatch,
        "assay_changes": [{"id": a["id"], "assay_name": a["assay_name"], **out.assays[a["id"]]}
                          for a in assay_rows if a["id"] in out.assays],
        "needs_definition": needs_definition(seek_id, context_rows, internal_assays,
                                             [{**a, **out.assays.get(a["id"], {})} for a in assay_rows]),
    }
    return out


def needs_definition(seek_titles, context_rows, internal_assays, assay_rows) -> dict:
    """The SEEK sample types with no context row, and the internal assays no assay_context row names (by id or by
    title): what the admin page lists as needing a definition."""
    codes = {r["sample_type"] for r in context_rows}
    ids = {a.get("internal_assay_id") for a in assay_rows}
    names = {a["assay_name"] for a in assay_rows}
    return {"sample_types": sorted(set(seek_titles) - codes),
            "assays": sorted(r["internal_assay_title"] for r in internal_assays
                             if r["id"] not in ids and r["internal_assay_title"] not in names)}


# --- the writer ---------------------------------------------------------------------------------------------------

def _dict_rows(conn, sql: str) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(sql)
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]


def read_inputs(driver, db) -> dict:
    """Everything ``derive`` needs, from SEEK, the dmac tables and the graph."""
    from nextseek_api.graph_sync import sources, writer

    dmac = sources._dmac()
    cols = ", ".join(("sample_type", "sampletype_id", "clade") + METADATA_COLUMNS + CONNECTION_COLUMNS)
    observed = writer._run(driver, db, OBSERVED_CYPHER, read=True)
    return {
        "context_rows": _dict_rows(dmac, f"SELECT {cols} FROM sample_types_context ORDER BY id"),
        "assay_rows": _dict_rows(dmac, "SELECT id, assay_name, Required_Parent_Sample_Types, "
                                       "Optional_Parent_Sample_Types, Children_Sample_Types, internal_assay_id "
                                       "FROM assay_context ORDER BY id"),
        "seek_types": sources.sample_types(),
        "seek_attributes": sources.sample_attributes(),
        "type_clades": sources.type_clades(),
        "internal_assays": _dict_rows(dmac, "SELECT id, internal_assay_title FROM internal_assays ORDER BY id"),
        "observed": [(r["parent_type"], r["assay"], r["child_type"]) for r in writer._records(observed)],
    }


def write(result: Derived) -> None:
    """The changed cells and the whole connections table, in one dmac transaction."""
    from django.conf import settings
    from django.db import transaction

    from nextseek_api.graph_sync.models_db import SampleTypeConnectionRow as Row

    alias = settings.NEXTSEEK_DATABASE
    with transaction.atomic(using=alias):
        from django.db import connections
        with connections[alias].cursor() as cur:
            for code, cells in result.sample_types.items():
                sets = ", ".join(f"`{col}` = %s" for col in cells)
                cur.execute(f"UPDATE sample_types_context SET {sets} WHERE sample_type = %s",
                            [*cells.values(), code])
            for row_id, cells in result.assays.items():
                cur.execute("UPDATE assay_context SET internal_assay_id = %s WHERE id = %s",
                            [cells["internal_assay_id"], row_id])
        Row.objects.using(alias).all().delete()
        Row.objects.using(alias).bulk_create(
            [Row(parent_type=c["parent"], assay=c["assay"], child_type=c["child"],
                                  designed=c["designed"], observed=c["observed"]) for c in result.connections])


def sync_context(driver, db, *, dry_run: bool = False, run_dir=None, inputs: dict | None = None) -> dict:
    """Read, derive, write (unless ``dry_run``), then queue the graph syncs the change needs. Returns the report."""
    from nextseek_api.graph_sync import hooks

    result = derive(**(inputs if inputs is not None else read_inputs(driver, db)))
    report = {"kind": "context", **result.report, "status": "dry_run" if dry_run else "ok",
              "changes": {"sample_types": result.sample_types}}
    if dry_run:
        return report
    write(result)
    if any(col in cells for cells in result.sample_types.values() for col in GRAPH_READ_COLUMNS):
        hooks.enqueue("catalog", "*")
    if result.assays:
        hooks.enqueue("assay_map", "*")
    log.info("graph_sync context: %s", report["changed_cells"])
    return report

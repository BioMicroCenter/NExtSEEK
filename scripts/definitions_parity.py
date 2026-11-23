#!/usr/bin/env python3
"""Parity of two definitions bundles: the ETL's old ``remote_export.py`` output against
``GET /nextseek_api/definitions/export/``, field for field. Standard library only; reads two JSON files.

    python3 scripts/definitions_parity.py OLD.json NEW.json [--expected CONTEXT_DRY_RUN.json] [--json]

Compared: every column of every ``sample_types`` row (by ``sample_type``) and ``assays`` row (by position and
``assay_name``), the observed ``connections`` as a set, every ``attributes`` field (by ``key``) and ``graph_meta``.
Not compared: ``exported_at``, ``host`` (the old container's name) and the keys only the new export has. The old
``sample_count`` of each attribute is compared as the new ``has_values`` (count above zero), since the export
carries no counts.

``--expected`` takes the report of ``manage.py graph_sync --context --dry-run --json`` run just before the step
wrote: a cell it lists as a change (``changes.sample_types``, ``assay_changes``) is an expected difference when the
new value is the one it named. Two kinds are reported apart, never failed, because they move between two pulls on
their own: an attribute's ``sample_count``, and ``graph_meta.catalog_hash`` / ``synced_at`` (the catalog sync the
step queues rewrites them). Exit 0 when nothing else differs, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys

VOLATILE_META = ("catalog_hash", "synced_at")


def _cells(kind, key, old_row, new_row, expected, out):
    for col in sorted(set(old_row) | set(new_row)):
        a, b = old_row.get(col), new_row.get(col)
        if a == b:
            continue
        item = {"kind": kind, "key": key, "column": col, "old": a, "new": b}
        allowed = expected.get((kind, key), {})
        (out["expected"] if col in allowed and allowed[col] == b else out["unexpected"]).append(item)


def _has_values(attr: dict, new: dict) -> dict:
    """The old bundle's ``sample_count`` as the new export's ``has_values``, when the new export carries no counts."""
    if "sample_count" in attr and any("has_values" in a for a in new.get("attributes", [])):
        attr = dict(attr)
        attr["has_values"] = bool(attr.pop("sample_count") or 0)
    return attr


def compare(old: dict, new: dict, expected_report: dict | None = None) -> dict:
    out = {"unexpected": [], "expected": [], "volatile": []}
    expected = {}
    for code, cells in ((expected_report or {}).get("changes", {}).get("sample_types") or {}).items():
        expected[("sample_types", code)] = cells
    for change in (expected_report or {}).get("assay_changes") or []:
        expected[("assays", change["assay_name"])] = {"internal_assay_id": change["internal_assay_id"]}

    old_st = {r["sample_type"]: r for r in old.get("sample_types", [])}
    new_st = {r["sample_type"]: r for r in new.get("sample_types", [])}
    for code in sorted(set(old_st) | set(new_st)):
        if code not in old_st or code not in new_st:
            out["unexpected"].append({"kind": "sample_types", "key": code, "only_in": "old" if code in old_st
                                      else "new"})
            continue
        _cells("sample_types", code, old_st[code], new_st[code], expected, out)

    old_as, new_as = old.get("assays", []), new.get("assays", [])
    if [a.get("assay_name") for a in old_as] != [a.get("assay_name") for a in new_as]:
        out["unexpected"].append({"kind": "assays", "key": "order", "old": len(old_as), "new": len(new_as)})
    else:
        for a, b in zip(old_as, new_as):
            _cells("assays", a.get("assay_name"), a, b, expected, out)

    triple = lambda c: (c.get("assay"), c.get("parent_type"), c.get("child_type"))  # noqa: E731
    old_c, new_c = {triple(c) for c in old.get("connections", [])}, {triple(c) for c in new.get("connections", [])}
    for t in sorted(old_c - new_c, key=str):
        out["unexpected"].append({"kind": "connections", "key": t, "only_in": "old"})
    for t in sorted(new_c - old_c, key=str):
        out["unexpected"].append({"kind": "connections", "key": t, "only_in": "new"})

    old_at = {a["key"]: _has_values(a, new) for a in old.get("attributes", [])}
    new_at = {a["key"]: a for a in new.get("attributes", [])}
    for key in sorted(set(old_at) | set(new_at)):
        if key not in old_at or key not in new_at:
            out["unexpected"].append({"kind": "attributes", "key": key, "only_in": "old" if key in old_at else "new"})
            continue
        for field in sorted(set(old_at[key]) | set(new_at[key])):
            a, b = old_at[key].get(field), new_at[key].get(field)
            if a != b:
                bucket = out["volatile"] if field == "sample_count" else out["unexpected"]
                bucket.append({"kind": "attributes", "key": key, "column": field, "old": a, "new": b})

    old_m, new_m = old.get("graph_meta") or {}, new.get("graph_meta") or {}
    for field in sorted(set(old_m) | set(new_m)):
        if old_m.get(field) != new_m.get(field):
            bucket = out["volatile"] if field in VOLATILE_META else out["unexpected"]
            bucket.append({"kind": "graph_meta", "key": field, "old": old_m.get(field), "new": new_m.get(field)})
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("old")
    parser.add_argument("new")
    parser.add_argument("--expected", help="the context step's --dry-run --json report")
    parser.add_argument("--json", action="store_true", help="print the whole result as JSON")
    args = parser.parse_args(argv)
    load = lambda path: json.load(open(path, encoding="utf-8"))  # noqa: E731
    result = compare(load(args.old), load(args.new), load(args.expected) if args.expected else None)
    if args.json:
        print(json.dumps(result, indent=1, ensure_ascii=False, default=str))
    else:
        print(f"unexpected {len(result['unexpected'])}, expected {len(result['expected'])}, "
              f"volatile {len(result['volatile'])}")
        for item in result["unexpected"][:50]:
            print("  UNEXPECTED", json.dumps(item, ensure_ascii=False, default=str))
    return 1 if result["unexpected"] else 0


if __name__ == "__main__":
    sys.exit(main())

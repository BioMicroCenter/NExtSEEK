"""scripts/definitions_parity.py: the old ETL bundle against the new export, field for field."""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "definitions_parity.py"
_spec = importlib.util.spec_from_file_location("definitions_parity", SCRIPT)
parity = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(parity)

OLD = {
    "exported_at": "2026-11-23T17:11:58+00:00", "host": "df23dc318a10",
    "sample_types": [{"sample_type": "BAC", "sampletype_id": 131, "child_sampletypes": "RNA, DNA", "name": "B"}],
    "assays": [{"assay_name": "DNA Extraction", "internal_assay_id": None}],
    "connections": [{"assay": "DNA Extraction", "parent_type": "BAC", "child_type": "DNA"}],
    "attributes": [{"key": "7:UID", "title": "UID", "required": True, "sample_count": 10}],
    "graph_meta": {"schema_version": "1.3", "catalog_hash": "a", "synced_at": "t1"},
}


def test_an_identical_export_is_parity_whatever_only_the_new_one_adds():
    new = {**copy.deepcopy(OLD), "exported_at": "later", "host": "", "licensed_connections": [],
           "needs_definition": {}}
    assert parity.compare(OLD, new) == {"unexpected": [], "expected": [], "volatile": []}


def test_a_difference_the_dry_run_named_is_expected_and_any_other_is_not():
    new = copy.deepcopy(OLD)
    new["sample_types"][0].update(sampletype_id=7, child_sampletypes="DNA", name="Bacteria")
    new["assays"][0]["internal_assay_id"] = 5
    dry = {"changes": {"sample_types": {"BAC": {"sampletype_id": 7, "child_sampletypes": "DNA"}}},
           "assay_changes": [{"id": 1, "assay_name": "DNA Extraction", "internal_assay_id": 5}]}
    out = parity.compare(OLD, new, dry)
    assert {(d["key"], d["column"]) for d in out["expected"]} == {("BAC", "sampletype_id"),
                                                                 ("BAC", "child_sampletypes"),
                                                                 ("DNA Extraction", "internal_assay_id")}
    assert [(d["key"], d["column"]) for d in out["unexpected"]] == [("BAC", "name")]   # a hand column moved


def test_connections_attributes_and_counts():
    new = copy.deepcopy(OLD)
    new["connections"] = [{"assay": "Library Prep", "parent_type": "BAC", "child_type": "DNA"}]
    new["attributes"][0]["sample_count"] = 11
    new["graph_meta"]["catalog_hash"] = "b"
    out = parity.compare(OLD, new)
    assert {d.get("only_in") for d in out["unexpected"]} == {"old", "new"}
    assert {(d["kind"], d["key"]) for d in out["volatile"]} == {("attributes", "7:UID"),
                                                                ("graph_meta", "catalog_hash")}


def test_the_cli_exits_1_on_an_unexpected_difference(tmp_path):
    import json
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    old.write_text(json.dumps(OLD))
    changed = copy.deepcopy(OLD)
    changed["sample_types"][0]["name"] = "X"
    new.write_text(json.dumps(changed))
    assert parity.main([str(old), str(old)]) == 0
    assert parity.main([str(old), str(new)]) == 1


# --- the golden file: today's dev bundle, loaded into the tables, comes back from the new export unchanged --------

import json

import pytest
from django.db import connection

from nextseek_api.graph_sync import sources
from nextseek_api.graph_sync.models_db import SampleTypeConnectionRow
from nextseek_api.services.definitions_export import build_export

GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "definitions_golden_slice.json").read_text())


def _create(table: str, rows: list[dict]) -> None:
    cols = list(rows[0])
    kind = lambda c: "INTEGER" if any(isinstance(r.get(c), int) for r in rows) else "TEXT"  # noqa: E731
    with connection.cursor() as cur:
        cur.execute(f"DROP TABLE IF EXISTS {table}")
        cur.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY, "
                    + ", ".join(f'"{c}" {kind(c)}' for c in cols) + ")")
        for n, row in enumerate(rows, 1):
            cur.execute(f"INSERT INTO {table} (id, {', '.join(chr(34) + c + chr(34) for c in cols)}) "
                        f"VALUES (%s, {', '.join(['%s'] * len(cols))})", [n, *(row[c] for c in cols)])


@pytest.mark.django_db
def test_the_new_export_reproduces_the_old_bundle_field_for_field(monkeypatch):
    _create("sample_types_context", GOLDEN["sample_types"])
    _create("assay_context", GOLDEN["assays"])
    _create("internal_assays", [{"internal_assay_title": "unused"}])
    for c in GOLDEN["connections"]:
        SampleTypeConnectionRow.objects.create(parent_type=c["parent_type"], assay=c["assay"],
                                               child_type=c["child_type"], observed=True)
    monkeypatch.setattr(sources, "sample_types", lambda: [])
    as_new = [{**{k: v for k, v in a.items() if k != "sample_count"}, "has_values": bool(a.get("sample_count"))}
              for a in GOLDEN["attributes"]]          # what the new statement returns: no counts
    new = build_export(graph_reader=lambda q: {"meta": [GOLDEN["graph_meta"]], "attributes": as_new})

    assert parity.compare(GOLDEN, new) == {"unexpected": [], "expected": [], "volatile": []}


def test_a_count_becomes_has_values_and_only_a_wrong_one_differs():
    new = copy.deepcopy(OLD)
    new["attributes"] = [{k: v for k, v in OLD["attributes"][0].items() if k != "sample_count"} | {"has_values": True}]
    assert parity.compare(OLD, new)["unexpected"] == []
    new["attributes"][0]["has_values"] = False
    assert [d["column"] for d in parity.compare(OLD, new)["unexpected"]] == ["has_values"]

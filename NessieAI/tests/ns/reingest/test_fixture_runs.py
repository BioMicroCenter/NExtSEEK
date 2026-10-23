"""One test per committed run fixture (spec §8.3).

Each fixture is a staged copy of a Nessie-launched -profile test run, or nf-core's own published evidence
imported by scripts/reingest/import_evidence.py (spec 2026-10-22, G-7 amended), reduced to
text files plus an inventory listing. The test harvests it, maps it, and compares
the row shape with the committed snapshot. A new release is a new folder; its
snapshot diff shows what moved.
"""
import json
import os
from pathlib import Path

import pytest

from NessieAI.ns.reingest import harvest, mapper, maps

# harvest_local looks the run up in the PipelineRun table, so it needs the test DB.
pytestmark = pytest.mark.django_db

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "reingest_runs"
ATTRS = json.loads((Path(__file__).resolve().parents[1] / "fixtures"
                    / "sampletype_attributes.json").read_text())["sample_types"]
FIXTURES = sorted(p for p in ROOT.glob("*/*") if (p / "inventory.json").is_file())
UPDATE = os.environ.get("REINGEST_UPDATE_SNAPSHOTS") == "1"


def test_there_are_fixtures():
    assert FIXTURES, f"no run fixtures under {ROOT}"


def _shape(result) -> dict:
    by_rule: dict[str, dict] = {}
    for row in result.rows:
        if not row.rule_id:
            continue
        entry = by_rule.setdefault(row.rule_id, {"sample_type": row.sample_type,
                                                 "rows": 0, "attributes": set()})
        entry["rows"] += 1
        entry["attributes"] |= set(row.attributes)
    return {
        "rules": {rid: {**e, "attributes": sorted(e["attributes"])}
                  for rid, e in sorted(by_rule.items())},
        "warnings": sorted(result.warnings),
    }


@pytest.mark.parametrize("folder", FIXTURES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_fixture(folder):
    pipeline = folder.parent.name
    inventory = json.loads((folder / "inventory.json").read_text())
    run = harvest.harvest_local(str(folder), inventory=inventory, run_dir="/RUN")
    pipeline_map = maps.load(pipeline)

    _, version_warnings = maps.effective(pipeline_map, run.pipeline.version)
    assert version_warnings == [], (
        f"{folder.name}: no rule set covers this release; widen applies_to_versions "
        f"or add a versions override: {version_warnings}")

    result = mapper.apply(run, pipeline_map,
                          context={"has_attribute": lambda st, a: a in ATTRS.get(st, ())})
    misses = [w for w in result.warnings if "matched no file" in w]
    assert misses == [], f"{folder.name}: non-optional rules matched nothing: {misses}"

    shape = _shape(result)
    snapshot = folder / "snapshot.json"
    if UPDATE or not snapshot.is_file():
        snapshot.write_text(json.dumps(shape, indent=1, sort_keys=True) + "\n")
        if not UPDATE:
            pytest.fail(f"wrote a new snapshot for {folder}; review and commit it")
    assert shape == json.loads(snapshot.read_text())


import fnmatch

# Files the harvest reads itself, or fixture bookkeeping: no map has to claim them.
_HARVEST_OWNED = ("pipeline_info/*", "multiqc/*", "samplesheet.csv", "launch_record.json",
                  "EVIDENCE.txt", "SYNTHETIC.txt", "inventory.json", "snapshot.json")


@pytest.mark.parametrize("folder", FIXTURES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_file_is_claimed_or_ruled_out(folder):
    """For a map that claims every file: each file in the run is a rule's candidate or
    companion, or is named in unmapped_files with a reason (spec 2026-10-22 §7)."""
    pipeline_map = maps.load(folder.parent.name)
    if not pipeline_map.claims_every_file:
        pytest.skip(f"{folder.parent.name} does not claim every file")
    paths = [e["path"] for e in json.loads((folder / "inventory.json").read_text())]
    rules = list(pipeline_map.outputs) + [r for o in pipeline_map.versions for r in o.outputs]
    patterns = [p for rule in rules for p in [*rule.candidates, *(c.glob for c in rule.companions)]]
    patterns += [u.glob for u in pipeline_map.unmapped_files] + list(_HARVEST_OWNED)
    expanded = [e for p in patterns for e in maps.expand_braces(p)]
    unclaimed = [p for p in paths if not any(fnmatch.fnmatch(p, g) for g in expanded)]
    assert unclaimed == [], f"{folder}: files no rule claims and no ruling explains: {unclaimed}"

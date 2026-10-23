"""One test per committed run fixture (spec §8.3).

Each fixture is a staged copy of a Nessie-launched -profile test run, or nf-core's own published evidence
imported by scripts/reingest/import_evidence.py (G-7 in NessieAI/ns/reingest_maps/README.md), reduced to
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
    companion, or is named in unmapped_files with a reason (`claims_every_file` in
    NessieAI/ns/reingest_maps/README.md)."""
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


def _rule_and_ruling_overlaps(paths, pipeline_map) -> list[str]:
    """Files a rule's candidate or companion matches that an unmapped_files glob
    also rules out. The mapper drops a ruled-out file before any rule sees it,
    so an overlap silently disables part of a rule."""
    rules = list(pipeline_map.outputs) + [r for o in pipeline_map.versions for r in o.outputs]
    rule_globs = [e for rule in rules for p in [*rule.candidates, *(c.glob for c in rule.companions)]
                  for e in maps.expand_braces(p)]
    ruled = [e for u in pipeline_map.unmapped_files for e in maps.expand_braces(u.glob)]
    return [p for p in paths if any(fnmatch.fnmatch(p, g) for g in rule_globs)
            and any(fnmatch.fnmatch(p, g) for g in ruled)]


@pytest.mark.parametrize("folder", FIXTURES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_file_is_both_read_by_a_rule_and_ruled_out(folder):
    """The file-level twin of test_no_key_is_both_ruled_out_and_read_by_a_rule."""
    paths = [e["path"] for e in json.loads((folder / "inventory.json").read_text())]
    both = _rule_and_ruling_overlaps(paths, maps.load(folder.parent.name))
    assert both == [], f"{folder}: read by a rule and ruled out: {both}"


def test_the_overlap_check_catches_a_ruled_out_candidate():
    m = maps.PipelineMap.model_validate({
        "map_version": 2, "pipeline": "nf-core/x",
        "outputs": [{"id": "v", "sample_type": "A.VCF", "candidates": ["vc/*/*.vcf.gz"]}],
        "unmapped_files": [{"glob": "vc/*/*.g.vcf.gz", "why": "a gVCF ruled out for this check"}]})
    assert _rule_and_ruling_overlaps(["vc/S1/S1.g.vcf.gz", "vc/S1/S1.vcf.gz"], m) == ["vc/S1/S1.g.vcf.gz"]

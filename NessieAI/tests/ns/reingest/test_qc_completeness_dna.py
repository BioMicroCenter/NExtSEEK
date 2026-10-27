"""QC completeness for the atacseq, chipseq and methylseq maps, against a real run.

Their QC was mapped without a real run of the pinned releases (README "Maps written
from the docs alone"; methylseq's fixtures are nf-test snapshots, which hold no MultiQC
values), and every QC key they read carries "unverified until a real run" in its note.
These tests wait for a fixture that holds the run's MultiQC tables, and skip until one
exists. Once it does, they fail until the map is complete for it:

- every metric the harvest reads is mapped or ruled out (nothing reaches the review queue);
- every QC rule reads a key or derived value the run actually has;
- no QC note still says the key is unverified.
"""
import json
from pathlib import Path

import pytest

from NessieAI.ns.reingest import harvest, mapper, maps

pytestmark = pytest.mark.django_db

PIPELINES = ("atacseq", "chipseq", "methylseq")
ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "reingest_runs"
ATTRS = json.loads((Path(__file__).resolve().parents[1] / "fixtures"
                    / "sampletype_attributes.json").read_text())["sample_types"]
UNVERIFIED = "unverified until a real run"


def real_runs(pipeline: str) -> list[Path]:
    """Fixture folders of `pipeline` that hold MultiQC data tables: a Nessie-launched
    -profile test run, or AWS results that have them. A snapshot import has none."""
    folders = sorted(p for p in (ROOT / pipeline).glob("*") if (p / "inventory.json").is_file())
    return [f for f in folders if any(f.glob("multiqc*/**/*_data/multiqc_*"))]


def _real_run_or_skip(pipeline: str) -> list[Path]:
    runs = real_runs(pipeline)
    if not runs:
        pytest.skip(f"no {pipeline} fixture with MultiQC tables yet: its QC keys stay "
                    f"{UNVERIFIED} (add one with scripts/reingest/make_fixture.sh or "
                    "import_evidence.py aws)")
    return runs


def _resolved(folder: Path):
    """The run harvested with every sample resolved to a D.SEQ, so the backfill rows exist."""
    inventory = json.loads((folder / "inventory.json").read_text())
    uids: dict[str, str] = {}
    run = harvest.harvest_local(
        str(folder), inventory=inventory, run_dir="/RUN",
        lookup_by_fastq=lambda path, types=None: [uids.setdefault(path, f"D.SEQ-TEST-{len(uids) + 1}")],
        sample_type_lookup=lambda found: {u: "D.SEQ" for u in found})
    result = mapper.apply(run, maps.load(folder.parent.name),
                          context={"has_attribute": lambda st, a: a in ATTRS.get(st, ())})
    return run, result


@pytest.mark.parametrize("pipeline", PIPELINES)
def test_every_metric_the_run_has_is_mapped_or_ruled_out(pipeline):
    for folder in _real_run_or_skip(pipeline):
        _, result = _resolved(folder)
        assert result.unmapped == [], f"{folder.name}: keys for the review queue: {result.unmapped}"


@pytest.mark.parametrize("pipeline", PIPELINES)
def test_every_qc_rule_reads_something_the_run_has(pipeline):
    for folder in _real_run_or_skip(pipeline):
        run, _ = _resolved(folder)
        for attribute, rule in maps.load(pipeline).qc_attributes.items():
            keys = [rule.from_key, *rule.alternates]
            if all(k.startswith(("$params.", "$outputs.")) for k in keys):
                continue
            found = [k for k in keys for s in run.samples
                     if (k[len("$derived."):] in s.derived if k.startswith("$derived.")
                         else k in s.metrics)]
            assert found, f"{folder.name}: {attribute} reads {keys}, which no sample has"


@pytest.mark.parametrize("pipeline", PIPELINES)
def test_no_qc_note_is_still_unverified(pipeline):
    _real_run_or_skip(pipeline)
    left = [a for a, rule in maps.load(pipeline).qc_attributes.items() if UNVERIFIED in rule.note]
    assert left == [], (f"{pipeline}: a real run's fixture exists; check these keys against it "
                        f"and drop '{UNVERIFIED}' from their notes: {left}")


def test_a_snapshot_fixture_is_not_a_real_run():
    """The committed methylseq fixtures are snapshot imports, so the tests above skip."""
    assert (ROOT / "methylseq" / "4.2.0" / "inventory.json").is_file()
    assert ROOT / "methylseq" / "4.2.0" not in real_runs("methylseq")

"""nf-core/raredisease's case-level VCFs are parented by every sample of their case (G-7 fixture).

raredisease joint-genotypes a family: `call_snv/genome/<case_id>_snv.vcf.gz` and its SV and
mitochondrial twins hold every sample whose samplesheet `case_id` names that case. The
committed 3.1.2 fixture (nf-core's nf-test snapshot at the tag) has one trio, `justhusky`;
here its three samples are resolved to D.SEQ parents with a fake lookup, so the rows show the
parents a real upload would carry. The snapshot shows the paths raredisease publishes, which
differ from its output docs: the merged SV VCF is `call_sv/<case_id>_sv.vcf.gz`, not
`call_sv/genome/<case_id>_sv_merge.vcf.gz`.
"""
import json
from pathlib import Path

import pytest

from NessieAI.ns.reingest import harvest, mapper, maps

pytestmark = pytest.mark.django_db

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "reingest_runs" / "raredisease" / "3.1.2"
ATTRS = json.loads((Path(__file__).resolve().parents[1] / "fixtures"
                    / "sampletype_attributes.json").read_text())["sample_types"]
UIDS = {"slowlycivilbuck": "D.SEQ-TEST-MOTHER", "hugelymodelbat": "D.SEQ-TEST-PROBAND"}


def _apply():
    def lookup(path, types=None):
        return [uid for name, uid in UIDS.items() if f"_{name}_" in path]
    inventory = json.loads((FIXTURE / "inventory.json").read_text())
    run = harvest.harvest_local(str(FIXTURE), inventory=inventory, run_dir="/RUN",
                                lookup_by_fastq=lookup,
                                sample_type_lookup=lambda uids: {u: "D.SEQ" for u in uids})
    return mapper.apply(run, maps.load("raredisease"),
                        context={"has_attribute": lambda st, a: a in ATTRS.get(st, ())})


def test_each_case_vcf_is_one_row_parented_by_the_cases_resolved_samples():
    rows = {r.rule_id: r for r in _apply().rows if r.rule_id.startswith("vcf_")}
    assert set(rows) == {"vcf_snv", "vcf_mitochondria", "vcf_sv"}
    for row in rows.values():
        parents = set(row.attributes["Parent"].value.split(";"))
        # earlycasualcaiman's reads are a spring file, which the fake lookup does not resolve.
        assert parents == set(UIDS.values()), (row.rule_id, parents)


def test_the_snv_row_is_the_case_vcf_with_its_index_as_companion():
    row = next(r for r in _apply().rows if r.rule_id == "vcf_snv")
    assert row.attributes["File_PrimaryData"].value.endswith("justhusky_snv.vcf.gz")
    # A.VCF has no File_SecondaryData, so the index is named in Notes.
    assert "justhusky_snv.vcf.gz.tbi" in row.attributes["Notes"].value


def test_each_sample_gets_its_alignment():
    aln = [r for r in _apply().rows if r.rule_id == "aln"]
    assert sorted(r.nfcore_sample for r in aln) == ["earlycasualcaiman", "hugelymodelbat",
                                                    "slowlycivilbuck"]

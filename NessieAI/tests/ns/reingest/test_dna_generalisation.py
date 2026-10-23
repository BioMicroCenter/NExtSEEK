"""Reingest changes the DNA pipelines need, pinned on sarek-shaped inputs."""
import pytest
from pydantic import ValidationError

from NessieAI.ns.reingest import harvest, manifest, maps, uid_resolve

pytestmark = pytest.mark.django_db

ROWS = [
    {"patient": "P1", "sample": "S1", "lane": "L1", "fastq_1": "/net/a/S1_L1_1.fq.gz", "fastq_2": "/net/a/S1_L1_2.fq.gz"},
    {"patient": "P1", "sample": "S1", "lane": "L2", "fastq_1": "/net/a/S1_L2_1.fq.gz", "fastq_2": "/net/a/S1_L2_2.fq.gz"},
    {"patient": "P1", "sample": "S2", "lane": "L1", "spring_1": "/net/a/S2_1.spring", "spring_2": "/net/a/S2_2.spring"},
]
UIDS = {"/net/a/S1_L1_1.fq.gz": ["D.SEQ-X-1-PUB"], "/net/a/S1_L2_1.fq.gz": ["D.SEQ-X-2-PUB"],
        "/net/a/S2_1.spring": ["D.SEQ-X-3-PUB"]}


def _lookup(path):
    return UIDS.get(path, [])


def test_lanes_of_one_sample_resolve_as_multirun_with_each_lanes_parent():
    """sarek writes one row per lane; with no launch record they still resolve by path."""
    out = [o for o in uid_resolve.resolve(ROWS, "/RUN/no-launch-record", _lookup) if o[0] == "S1"]
    assert {o[2] for o in out} == {manifest.RESOLUTION_MULTIRUN}
    assert set(out[0][3]) == {"D.SEQ-X-1-PUB", "D.SEQ-X-2-PUB"}


def test_a_spring_input_resolves_by_path_without_a_launch_record():
    out = {o[0]: o for o in uid_resolve.resolve(ROWS, "/RUN/no-launch-record", _lookup)}
    assert out["S2"][1] == "D.SEQ-X-3-PUB"
    assert out["S2"][2] == manifest.RESOLUTION_FASTQ_EXACT


STATS = {
    "S1.recal": {"mosdepth-median_coverage": 31.0, "samtools_flagstat_stats-reads_mapped_percent": 99.1},
    "S1.md": {"mosdepth-median_coverage": 30.0, "gatk4_markduplicates_mark_duplicates-PERCENT_DUPLICATION": 7.2},
    "S1.haplotypecaller.filtered": {"bcftools_stats-tstv": 2.0},
}


def test_suffix_rows_join_the_sample_best_first():
    m = harvest._sample_metrics("S1", STATS, sample_names={"S1"}, claimed=set(),
                                row_suffixes=(".recal", ".md"))
    assert m["mosdepth-median_coverage"] == 31.0
    assert m["gatk4_markduplicates_mark_duplicates-PERCENT_DUPLICATION"] == 7.2
    assert "bcftools_stats-tstv" not in m


def test_no_suffixes_keeps_todays_behaviour():
    assert harvest._sample_metrics("S1", STATS, sample_names={"S1"}, claimed=set()) == {}


def test_a_suffix_row_that_is_itself_a_sample_is_not_folded():
    m = harvest._sample_metrics("S1", STATS, sample_names={"S1", "S1.md"}, claimed=set(),
                                row_suffixes=(".md",))
    assert m == {}


def test_map_accepts_the_new_fields():
    m = maps.PipelineMap(pipeline="nf-core/x", map_version=2, qc_row_suffixes=[".recal"],
                         claims_every_file=True,
                         unmapped_files=[{"glob": "reports/*", "why": "tool reports, read through MultiQC"}])
    assert m.qc_row_suffixes == [".recal"] and m.unmapped_files[0].glob == "reports/*"


def test_unmapped_files_need_a_why():
    with pytest.raises(ValidationError):
        maps.PipelineMap(pipeline="nf-core/x", map_version=2, unmapped_files=[{"glob": "a/*"}])

"""scripts/reingest/import_evidence.py: nf-core's own evidence into a reingest fixture."""
import importlib.util
import json
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[4] / "scripts" / "reingest" / "import_evidence.py"
_spec = importlib.util.spec_from_file_location("import_evidence", SCRIPT)
ie = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ie)

LISTING = b"""<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
<Contents><Key>sarek/results-abc/prof/multiqc/</Key><Size>0</Size></Contents>
<Contents><Key>sarek/results-abc/prof/multiqc/multiqc_data/multiqc_general_stats.txt</Key><Size>900</Size></Contents>
<Contents><Key>sarek/results-abc/prof/multiqc/multiqc_data/multiqc_sources.txt</Key><Size>900</Size></Contents>
<Contents><Key>sarek/results-abc/prof/preprocessing/recalibrated/S1/S1.recal.cram</Key><Size>5000000000</Size></Contents>
<Contents><Key>sarek/results-abc/prof/pipeline_info/params_2026-01-01.json</Key><Size>300</Size></Contents>
<NextContinuationToken>tok/1</NextContinuationToken>
</ListBucketResult>"""


def test_s3_listing_parses_keys_sizes_and_the_next_token():
    entries, token = ie.parse_s3_listing(LISTING)
    assert token == "tok/1"
    assert {"key": "sarek/results-abc/prof/preprocessing/recalibrated/S1/S1.recal.cram",
            "size": 5000000000} in entries


def test_aws_inventory_drops_folders_and_is_relative_to_the_profile():
    entries, _ = ie.parse_s3_listing(LISTING)
    inv = ie.aws_inventory(entries, "sarek/results-abc/prof/")
    paths = [e["path"] for e in inv]
    assert "multiqc/" not in paths
    assert "preprocessing/recalibrated/S1/S1.recal.cram" in paths


def test_only_multiqc_tables_and_run_info_are_downloaded():
    entries, _ = ie.parse_s3_listing(LISTING)
    wanted = ie.wanted_aws_files(ie.aws_inventory(entries, "sarek/results-abc/prof/"))
    assert "multiqc/multiqc_data/multiqc_general_stats.txt" in wanted
    assert "multiqc/multiqc_data/multiqc_sources.txt" not in wanted
    assert "pipeline_info/params_2026-01-01.json" in wanted
    assert not any(p.endswith(".cram") for p in wanted)


def test_samplesheet_paths_are_rewritten_under_run():
    text = ("patient,sample,lane,fastq_1,fastq_2\n"
            "p,test,1,https://raw.githubusercontent.com/nf-core/test-datasets/x/test_1.fastq.gz,"
            "${projectDir}/tests/test_2.fastq.gz\n")
    out = ie.localise_samplesheet(text)
    assert "/RUN/inputs/test_1.fastq.gz" in out and "/RUN/inputs/test_2.fastq.gz" in out
    assert "https://" not in out and "projectDir" not in out


def test_versions_yml_from_a_snapshot():
    snap = {"-profile test": {"content": [3, {"Workflow": {"nf-core/sarek": "v3.10.0"}}, ["a/b.txt"]]}}
    assert ie.versions_yml_from_snapshot(snap, "sarek") == "Workflow:\n  nf-core/sarek: v3.10.0\n"


def test_params_keep_scalars_and_drop_paths():
    params = {"genome": "GATK.GRCh38", "outdir": "s3://bucket/x", "tools": "strelka",
              "input": "/mnt/x.csv", "wes": False, "igenomes_base": "s3://ngi-igenomes/"}
    assert ie.evidence_params(params) == {"genome": "GATK.GRCh38", "tools": "strelka", "wes": False,
                                          "outdir": "/RUN", "input": "/RUN/samplesheet.csv"}


def test_write_fixture_lays_it_out_like_make_fixture(tmp_path):
    dest = tmp_path / "sarek" / "3.10.0"
    ie.write_fixture(dest, pipeline="sarek", version="3.10.0",
                     inventory=[{"path": "variant_calling/x.vcf.gz", "bytes": 0}],
                     samplesheet="patient,sample\np,test\n", params={"outdir": "/RUN"},
                     versions_yml="Workflow:\n  nf-core/sarek: v3.10.0\n",
                     files={"multiqc/multiqc_data/multiqc_general_stats.txt": b"Sample\n"},
                     evidence="from a test")
    assert json.loads((dest / "inventory.json").read_text()) == [{"path": "variant_calling/x.vcf.gz", "bytes": 0}]
    assert json.loads((dest / "launch_record.json").read_text())["cohort"] == []
    assert (dest / "pipeline_info" / "nf_core_sarek_software_mqc_versions.yml").is_file()
    assert (dest / "pipeline_info" / "params_evidence.json").is_file()
    assert (dest / "multiqc" / "multiqc_data" / "multiqc_general_stats.txt").is_file()
    assert (dest / "EVIDENCE.txt").read_text().startswith("from a test")

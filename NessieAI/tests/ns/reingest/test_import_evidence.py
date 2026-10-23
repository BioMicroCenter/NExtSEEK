"""scripts/reingest/import_evidence.py: nf-core's own evidence into a reingest fixture."""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

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
                     inventory=[{"path": "variant_calling/x.vcf.gz", "bytes": 0},
                                {"path": "multiqc/multiqc_data/multiqc_general_stats.txt", "bytes": 0}],
                     samplesheet="patient,sample\np,test\n", params={"outdir": "/RUN"},
                     versions_yml="Workflow:\n  nf-core/sarek: v3.10.0\n",
                     files={"multiqc/multiqc_data/multiqc_general_stats.txt": b"Sample\n"},
                     evidence="from a test")
    # a file written to disk is not also listed in the inventory
    assert json.loads((dest / "inventory.json").read_text()) == [{"path": "variant_calling/x.vcf.gz", "bytes": 0}]
    assert json.loads((dest / "launch_record.json").read_text())["cohort"] == []
    assert (dest / "pipeline_info" / "nf_core_sarek_software_mqc_versions.yml").is_file()
    assert (dest / "pipeline_info" / "params_evidence.json").is_file()
    assert (dest / "multiqc" / "multiqc_data" / "multiqc_general_stats.txt").is_file()
    assert (dest / "EVIDENCE.txt").read_text().startswith("from a test")


# ---- from_snapshot / from_aws / install_fixture with a fake fetch ----

SANITIZE = SCRIPT.parent / "sanitize_fixture.py"
SHEET = "patient,sample,lane,fastq_1,fastq_2\np,test,1,https://x.org/a/r1.fastq.gz,https://x.org/a/r2.fastq.gz\n"
SNAP = {"-profile test": {"content": [3, {"Workflow": {"nf-core/sarek": "v3.10.0"}},
                                       ["variant_calling/x.vcf.gz", "multiqc/multiqc_report.html"]]},
        "-profile test -stub": {"content": [["stub/only.txt"]]}}
RAW = ie.RAW


def snapshot_fetch(config):
    urls = {f"{RAW}/sarek/3.10.0/tests/default.nf.test.snap": json.dumps(SNAP).encode(),
            f"{RAW}/sarek/3.10.0/conf/test.config": config.encode(),
            f"{RAW}/sarek/3.10.0/tests/sheet.csv": SHEET.encode(),
            "https://sheets.test/other.csv": SHEET.replace("r1", "o1").encode()}
    return lambda url: urls[url]


TEST_CONFIG = """params {
    config_profile_name = 'Test profile'
    input = "${projectDir}/tests/sheet.csv"
    genome = 'GATK.GRCh38'
    outdir = '/somewhere/out'
}
"""


def sanitize_ok(folder):
    done = subprocess.run([sys.executable, str(SANITIZE), str(folder), "/RUN"],
                          capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_from_snapshot_builds_a_sanitizer_clean_fixture(tmp_path):
    built = ie.from_snapshot("sarek", "3.10.0", "default", snapshot_fetch(TEST_CONFIG))
    assert built["folder"] == "3.10.0"
    assert [e["path"] for e in built["inventory"]] == ["multiqc/multiqc_report.html", "variant_calling/x.vcf.gz"]
    assert "/RUN/inputs/r1.fastq.gz" in built["samplesheet"]
    assert built["params"]["genome"] == "GATK.GRCh38" and built["params"]["outdir"] == "/RUN"
    dest = tmp_path / "3.10.0"
    ie.write_fixture(dest, pipeline="sarek", version="3.10.0",
                     **{k: v for k, v in built.items() if k != "folder"})
    assert "://" not in (dest / "EVIDENCE.txt").read_text()
    sanitize_ok(dest)


def test_snapshot_mode_honours_samplesheet_url():
    built = ie.from_snapshot("sarek", "3.10.0", "default", snapshot_fetch(TEST_CONFIG),
                             samplesheet_url="https://sheets.test/other.csv")
    assert "/RUN/inputs/o1.fastq.gz" in built["samplesheet"]


def test_unresolved_input_asks_for_samplesheet_url():
    config = "params {\n    input = \"${params.pipelines_testdata_base_path}sarek/s.csv\"\n}\n"
    with pytest.raises(SystemExit) as err:
        ie.from_snapshot("sarek", "3.10.0", "default", snapshot_fetch(config))
    assert "--samplesheet-url" in str(err.value)


def _listing(keys):
    body = "".join(f"<Contents><Key>sarek/results-abc/test_full/{k}</Key><Size>10</Size></Contents>" for k in keys)
    return ('<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            + body + "</ListBucketResult>").encode()


def aws_fetch():
    keys = ["multiqc/multiqc_data/multiqc_general_stats.txt", "multiqc/multiqc_data/multiqc_sources.txt",
            "pipeline_info/params_2026-01-01.json", "pipeline_info/params_2026-02-02.json",
            "pipeline_info/nf_core_sarek_software_mqc_versions.yml",
            "preprocessing/recalibrated/S1/S1.recal.cram"]
    blobs = {
        "multiqc_general_stats.txt": b"Sample\tx\n",
        "params_2026-01-01.json": json.dumps({"genome": "old"}).encode(),
        "params_2026-02-02.json": json.dumps({"genome": "GATK.GRCh38", "outdir": "s3://b/o",
                                              "wes": True}).encode(),
        "nf_core_sarek_software_mqc_versions.yml": b"Workflow:\n  nf-core/sarek: v3.10.0\n"}

    def fetch(url):
        if url == f"{ie.API}/sarek/commits/3.10.0":
            return json.dumps({"sha": "abc"}).encode()
        if "list-type=2" in url:
            assert "continuation-token" not in url
            return _listing(keys)
        if url == f"{RAW}/sarek/3.10.0/conf/test_full.config":
            return TEST_CONFIG.encode()
        if url == f"{RAW}/sarek/3.10.0/tests/sheet.csv":
            return SHEET.encode()
        return blobs[url.rsplit("/", 1)[-1]]
    return fetch


def test_from_aws_names_the_folder_and_keeps_the_inventory_honest(tmp_path):
    built = ie.from_aws("sarek", "3.10.0", "test_full", None, aws_fetch())
    assert built["folder"] == "3.10.0-aws-test_full"
    assert list(built["files"]) == ["multiqc/multiqc_data/multiqc_general_stats.txt"]
    assert built["params"] == {"genome": "GATK.GRCh38", "wes": True,
                               "outdir": "/RUN", "input": "/RUN/samplesheet.csv"}
    assert built["versions_yml"] == "Workflow:\n  nf-core/sarek: v3.10.0\n"
    dest = tmp_path / "f"
    ie.write_fixture(dest, pipeline="sarek", version="3.10.0",
                     **{k: v for k, v in built.items() if k != "folder"})
    listed = [e["path"] for e in json.loads((dest / "inventory.json").read_text())]
    assert listed == ["multiqc/multiqc_data/multiqc_sources.txt",
                      "preprocessing/recalibrated/S1/S1.recal.cram"]
    assert "://" not in (dest / "EVIDENCE.txt").read_text()
    sanitize_ok(dest)


def test_install_fixture_moves_a_clean_fixture_into_place(tmp_path):
    built = ie.from_snapshot("sarek", "3.10.0", "default", snapshot_fetch(TEST_CONFIG))
    dest = tmp_path / "out" / "sarek" / "3.10.0"
    assert ie.install_fixture(dest, pipeline="sarek", version="3.10.0", built=built) == 0
    assert (dest / "inventory.json").is_file()


def test_install_fixture_refuses_what_the_sanitizer_refuses(tmp_path, capsys):
    built = ie.from_snapshot("sarek", "3.10.0", "default", snapshot_fetch(TEST_CONFIG))
    built["evidence"] += " see https://example.org/x"
    dest = tmp_path / "out" / "sarek" / "3.10.0"
    assert ie.install_fixture(dest, pipeline="sarek", version="3.10.0", built=built) == 1
    assert not dest.exists()
    assert "refusing" in capsys.readouterr().err

from pathlib import Path
from chat_nextseek.seqera.emitter import emit_nfcore_artifacts, emit_launch_artifacts
from chat_nextseek.seqera.ena import ENAResolution, ENARun

TOWER = {"access_token": "t", "workspace": "w", "compute_env": "ce", "work_bucket": "/bucket"}


def _rows():
    return [{"sample": "S1", "accession": "SRR1", "strandedness": "auto"}]


def _resolutions():
    return [ENAResolution(accession="SRR1", runs=[ENARun(run_accession="SRR1", fastq_1="ftp://a_1.fq.gz",
            fastq_2="ftp://a_2.fq.gz", layout="PAIRED")], missing=False, reason="")]


def test_emit_nfcore_still_writes_params_and_launch(tmp_path):
    res = emit_nfcore_artifacts(
        tmp_path, pipeline="rnaseq", samplesheet_rows=_rows(), resolutions=_resolutions(),
        launch_plan={"run_name": "r1", "params": {"aligner": "hisat2"}}, tower_env=TOWER)
    assert (tmp_path / "samplesheet.csv").exists()
    assert (tmp_path / "params.yml").exists()
    assert (tmp_path / "launch.yml").exists()
    params_text = (tmp_path / "params.yml").read_text()
    assert "aligner" in params_text and "input" in params_text and "outdir" in params_text


def test_emit_launch_artifacts_alone_writes_yamls(tmp_path):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,accession\nS1,SRR1\n", encoding="utf-8")
    res = emit_launch_artifacts(
        tmp_path, pipeline="rnaseq", samplesheet_path=sheet,
        launch_plan={"run_name": "r1", "params": {"aligner": "star_salmon", "genome": "GRCm39"}},
        tower_env=TOWER, excluded=[])
    assert (tmp_path / "params.yml").exists()
    assert (tmp_path / "launch.yml").exists()
    assert res.launch_entry is not None
    assert res.launch_entry["pipeline"] == "https://github.com/nf-core/rnaseq"
    params_text = (tmp_path / "params.yml").read_text()
    assert "star_salmon" in params_text and "GRCm39" in params_text

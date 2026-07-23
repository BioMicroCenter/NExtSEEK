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


def test_emit_launch_does_not_inject_default_genome(tmp_path):
    # configure_run owns the genome decision via the bundle registry. The emitter must
    # NOT silently stamp the catalog's default_genome (human GRCh38 for rnaseq) when params
    # lack one — that produced a wrong-species genome on mouse data (verification finding).
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,accession\nS1,SRR1\n", encoding="utf-8")
    emit_launch_artifacts(
        tmp_path, pipeline="rnaseq", samplesheet_path=sheet,
        launch_plan={"run_name": "r1", "params": {"aligner": "star_salmon"}},  # no genome
        tower_env=TOWER, excluded=[])
    params_text = (tmp_path / "params.yml").read_text()
    assert "genome" not in params_text  # no default genome injected; configure_run is the only source


def test_emit_prefers_local_fastq_paths_over_ena_and_writes_lf(tmp_path):
    # Option A: curated local paths (File_PrimaryData=R1, File_SecondaryData=R2) win over the
    # synthesized ENA URL; ENA is the fallback only when they're absent. Plus: LF line endings.
    rows = [{"sample": "S1", "accession": "SRR1", "strandedness": "auto"},
            {"sample": "S2", "accession": "SRR2", "strandedness": "auto"}]
    resolutions = [
        ENAResolution(accession="SRR1", missing=False, reason="", runs=[ENARun(
            run_accession="SRR1", fastq_1="ftp://ena1_1.fq.gz", fastq_2="ftp://ena1_2.fq.gz", layout="PAIRED")]),
        ENAResolution(accession="SRR2", missing=False, reason="", runs=[ENARun(
            run_accession="SRR2", fastq_1="ftp://ena2_1.fq.gz", fastq_2="ftp://ena2_2.fq.gz", layout="PAIRED")]),
    ]
    # Messy real-world metadata: the fastq PATH lives in Link_*, File_* holds the bare accession,
    # Checksum_* holds hashes. The emitter must pick by value (a fastq path), field-name-agnostic.
    acc_meta = {"SRR1": {
        "Link_PrimaryData": "/net/luria/S1_1.fastq.gz",     # R1 path -> should win
        "Link_SecondaryData": "/net/luria/S1_2.fastq.gz",   # R2 path
        "File_PrimaryData": "SRR1",                         # bare accession -> must be skipped
        "Checksum_PrimaryData": "abc123;def456",            # hashes -> must be skipped
    }}  # SRR2 has no usable path anywhere -> ENA fallback
    emit_nfcore_artifacts(tmp_path, pipeline="rnaseq", samplesheet_rows=rows,
                          resolutions=resolutions, accession_metadata=acc_meta,
                          launch_plan=None, tower_env={})
    lines = (tmp_path / "samplesheet.csv").read_text().splitlines()
    srr1 = next(l for l in lines if l.startswith("S1,"))
    # SRR1: picked the Link_* paths by value; the accession/checksum fields were skipped
    assert srr1.split(",")[1] == "/net/luria/S1_1.fastq.gz"
    assert srr1.split(",")[2] == "/net/luria/S1_2.fastq.gz"
    assert "ftp://ena1_1.fq.gz" not in "\n".join(lines)
    # SRR2: nothing usable -> ENA fallback
    assert any(l.startswith("S2,") and "ftp://ena2_1.fq.gz" in l for l in lines)
    # CRLF fix: plain LF
    assert b"\r" not in (tmp_path / "samplesheet.csv").read_bytes()

def test_fastq_from_meta_picks_path_skips_accession_and_checksum():
    from chat_nextseek.seqera.emitter import _fastq_from_meta
    meta = {"File_PrimaryData": "SRR9", "Checksum_PrimaryData": "a;b",
            "Link_PrimaryData": "/net/x/SRR9_1.fastq.gz",
            "Link_SecondaryData": "https://ebi/SRR9_2.fastq.gz"}
    assert _fastq_from_meta(meta, "primary") == "/net/x/SRR9_1.fastq.gz"   # path, not accession/hash
    assert _fastq_from_meta(meta, "secondary") == "https://ebi/SRR9_2.fastq.gz"  # url ok when no local
    assert _fastq_from_meta({"File_PrimaryData": "SRR9"}, "primary") == ""  # bare accession -> nothing
    # prefer a local path over a URL when both are present under the same hint
    meta2 = {"Link_PrimaryData": "https://ebi/x_1.fastq.gz", "File_PrimaryData": "/net/x_1.fastq.gz"}
    assert _fastq_from_meta(meta2, "primary") == "/net/x_1.fastq.gz"


def test_multi_run_accession_keeps_per_run_ena_urls(tmp_path):
    """A curated path must NOT be stamped across every run of a multi-run accession.

    accession_metadata is keyed per ACCESSION, so it cannot distinguish the runs
    beneath it. Applying it to each run emitted N identical fastq rows and threw
    away every run's own ENA URL. Guard for that: when an accession fans out to
    more than one run, the per-run URLs win.
    """
    rows = [{"sample": "S1", "accession": "SRR1", "strandedness": "auto"}]
    resolutions = [ENAResolution(accession="SRR1", missing=False, reason="", runs=[
        ENARun(run_accession="RUN1", fastq_1="ftp://r1_1.fq.gz", fastq_2="ftp://r1_2.fq.gz", layout="PAIRED"),
        ENARun(run_accession="RUN2", fastq_1="ftp://r2_1.fq.gz", fastq_2="ftp://r2_2.fq.gz", layout="PAIRED"),
    ])]
    acc_meta = {"SRR1": {"Link_PrimaryData": "/net/luria/S1_1.fastq.gz",
                         "Link_SecondaryData": "/net/luria/S1_2.fastq.gz"}}
    emit_nfcore_artifacts(tmp_path, pipeline="rnaseq", samplesheet_rows=rows,
                          resolutions=resolutions, accession_metadata=acc_meta,
                          launch_plan=None, tower_env={})
    lines = (tmp_path / "samplesheet.csv").read_text().splitlines()
    body = [l for l in lines if l.startswith("S1,")]
    assert len(body) == 2, body
    assert body[0].split(",")[1] == "ftp://r1_1.fq.gz"
    assert body[1].split(",")[1] == "ftp://r2_1.fq.gz"
    # the curated path must not have clobbered either run
    assert "/net/luria/S1_1.fastq.gz" not in "\n".join(lines)


import yaml
from pathlib import Path
from chat_nextseek.seqera.emitter import emit_luria_launch_artifacts


def test_emit_luria_launch_artifacts_writes_params_and_minimal_launch(tmp_path):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,fastq_1\nD.SEQ-1,/net/bmc-x/1.fastq.gz\n")
    res = emit_luria_launch_artifacts(
        tmp_path, pipeline="rnaseq", samplesheet_path=sheet,
        launch_plan={"run_name": "myrun", "pipeline_revision": "3.18.0",
                     "params": {"aligner": "star_salmon", "genome": "GRCm39"}})
    launch = yaml.safe_load(Path(res.saved_files["launch"]).read_text())
    assert launch["launch"][0]["name"] == "myrun"
    assert launch["launch"][0]["pipeline"].endswith("nf-core/rnaseq")  # catalog repo is the full github URL; the submitter normalizes it
    assert launch["launch"][0]["revision"] == "3.18.0"
    params = yaml.safe_load(Path(res.saved_files["params"]).read_text())
    assert params["aligner"] == "star_salmon" and params["genome"] == "GRCm39"

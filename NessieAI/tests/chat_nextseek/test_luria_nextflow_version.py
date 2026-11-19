"""Per-pipeline Nextflow versions, commit-SHA pins and the --input slot in the Luria run script.

A catalog entry may name `nextflow_version` (nf-core/eager 2.5.3 is DSL1, which Nextflow
removed after 22.10.x). It reaches run.sh only as `NXF_VER=<x.y.z>` in front of the
pipeline's own `nextflow run`, never exported, so the fetchngs pre-stage keeps the
launcher's version. None of this has been run on Luria."""
import pytest
import yaml

import chat_nextseek.luria.submitter as sub
from chat_nextseek.luria.run_script import (
    nextflow_supports_resource_limits, render_executor_config, render_luria_config,
    render_run_script, validate_nextflow_version, validate_revision,
)
from chat_nextseek.seqera.catalog import NFCORE_PIPELINE_CATALOG

SHA = "d9264b35ed88b648baf157d76bd9b261344fb585"


def _render(**over):
    kw = dict(job_name="j", pipeline="nf-core/rnaseq", revision="3.18.0", run_dir="/w/runs/j",
              work_dir="/w/work/j", singularity_cache="/w/singularity_cache", genome="GRCh38",
              resources={})
    kw.update(over)
    return render_run_script(**kw)


@pytest.mark.parametrize("good", ["22.10.8", "23.04.0", "24.10.4"])
def test_a_plain_release_number_is_a_valid_nextflow_version(good):
    assert validate_nextflow_version(good) == good


@pytest.mark.parametrize("bad", ["22.10", "latest", "22.10.8; rm -rf /", "22.10.8\n", "v22.10.8",
                                 "22.10.8-edge", "$(id)", "", "1.2.3.4"])
def test_anything_else_is_refused(bad):
    with pytest.raises(ValueError):
        validate_nextflow_version(bad)


def test_no_version_leaves_the_launcher_alone():
    out = _render()
    assert "\nNXF_VER=" not in out and "export NXF_VER" not in out
    assert "\nnextflow run nf-core/rnaseq -r 3.18.0 -profile singularity" in out


def test_a_version_prefixes_only_the_pipelines_own_run():
    out = _render(nextflow_version="22.10.8", needs_fetch=True, fastq_cache="/w/fastq_cache")
    assert "\nNXF_VER=22.10.8 nextflow run nf-core/rnaseq -r 3.18.0 -profile singularity" in out
    assert "export NXF_VER" not in out
    # the fetchngs pre-stage runs on the launcher's own version
    assert "    nextflow run nf-core/fetchngs" in out
    assert "NXF_VER=22.10.8 nextflow run nf-core/fetchngs" not in out


def test_an_invalid_version_never_reaches_the_script():
    with pytest.raises(ValueError):
        _render(nextflow_version="22.10.8 && curl x")


def test_a_commit_sha_is_a_valid_revision_and_reaches_dash_r():
    assert validate_revision(SHA) == SHA
    assert f"nextflow run nf-core/scdownstream -r {SHA} " in _render(pipeline="nf-core/scdownstream",
                                                                      revision=SHA)


def test_the_input_slot_defaults_to_the_csv_sheet_and_takes_a_validated_value():
    assert "--input samplesheet.csv \\" in _render()
    assert "--input samplesheet.tsv \\" in _render(input_arg="samplesheet.tsv")
    assert "--input /net/bmc/lab/all.fa.gz \\" in _render(input_arg="/net/bmc/lab/all.fa.gz")
    with pytest.raises(ValueError):
        _render(input_arg="x.csv; rm -rf /")


# --- the generated config, by Nextflow version -----------------------------------------------

def test_resource_limits_need_nextflow_24_04():
    assert nextflow_supports_resource_limits(None) is True          # the launcher (26.04)
    assert nextflow_supports_resource_limits("24.04.0") is True
    assert nextflow_supports_resource_limits("23.10.1") is False
    assert nextflow_supports_resource_limits("22.10.8") is False


def test_the_default_config_is_unchanged():
    out = render_executor_config({"cpus": 16})
    assert "process.resourceLimits = [cpus: 16, memory: '120 GB']" in out
    assert "max_cpus" not in out


def test_an_old_nextflow_gets_the_legacy_max_params_instead_of_resource_limits():
    out = render_executor_config({"cpus": 16, "time": "48:00:00"}, nextflow_version="22.10.8")
    assert "process.resourceLimits =" not in out
    assert "executor {\n    cpus = 16\n    memory = '120 GB'\n}" in out
    assert "params {\n    max_cpus = 16\n    max_memory = '120 GB'\n    max_time = '48h'\n}" in out


def test_the_luria_config_threads_the_version_and_keeps_the_shell_list():
    out = render_luria_config("/w/refs", resources={"cpus": 8}, nextflow_version="22.10.8")
    assert "process.shell = ['/bin/bash', '-euo', 'pipefail']" in out
    assert "process.resourceLimits =" not in out and "max_cpus = 8" in out


# --- the submitter reads both from the catalog -----------------------------------------------

LE = {"user": "u", "host": "luria.mit.edu", "key": "/k", "working_path": "/work"}


def _submit(tmp_path, monkeypatch, entry, revision):
    monkeypatch.setitem(NFCORE_PIPELINE_CATALOG, "toy", entry)
    sheet = tmp_path / "samplesheet.csv"
    sheet.write_text("sample,fastq_1\nD.SEQ-1,/net/bmc/r_R1.fastq.gz\n")
    launch = tmp_path / "launch.yml"
    launch.write_text(yaml.safe_dump({"launch": [{"name": "r", "pipeline": "nf-core/toy",
                                                  "revision": revision}]}))
    staged = {}
    monkeypatch.setattr(sub, "prepare_key", lambda k: "/tmp/key")
    monkeypatch.setattr(sub, "ssh_run", lambda env, cmd, **kw: "Submitted batch job 9")

    def scp(env, src, dst, **kw):
        if dst.endswith(("/run.sh", "/luria.config")):
            staged[dst.rsplit("/", 1)[1]] = open(src).read()
    monkeypatch.setattr(sub, "scp_file", scp)
    assert sub.submit_luria(launch, luria_env=LE, samplesheet_local=str(sheet))
    return staged


def test_the_submitter_renders_the_catalogs_nextflow_version(tmp_path, monkeypatch):
    staged = _submit(tmp_path, monkeypatch, {"nextflow_version": "22.10.8",
                                             "reference_cli_flags": []}, "2.5.3")
    assert "\nNXF_VER=22.10.8 nextflow run nf-core/toy -r 2.5.3 " in staged["run.sh"]
    assert "process.resourceLimits =" not in staged["luria.config"]


def test_the_submitter_passes_a_commit_pin_to_dash_r(tmp_path, monkeypatch):
    staged = _submit(tmp_path, monkeypatch, {"reference_cli_flags": []}, SHA)
    assert f"nextflow run nf-core/toy -r {SHA} " in staged["run.sh"]
    assert "\nNXF_VER=" not in staged["run.sh"]

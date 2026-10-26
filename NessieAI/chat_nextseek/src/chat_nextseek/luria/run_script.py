"""Render the Luria SLURM run.sh from a fixed template + validated slots.

The template scaffold (SBATCH directives, module loads, conda activate, the
nextflow invocation) is fixed; only bounded, validated slots are substituted.
`cpus` (the only LLM-proposed resource that reaches the template) is validated
against a strict allow-list; `pipeline`/`revision`/`genome` are allow-listed
fail-closed (`genome` is the species-resolved iGenomes key, NOT hardcoded);
`run_dir`/`work_dir`/`singularity_cache` come from trusted config
(LURIA_WORKING_PATH + a sanitized run name), not LLM free-text.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# Operator-specific slots. Both land inside a shell script executed on Luria, so
# both are allow-listed fail-closed like every other slot in this module.
#
# LURIA_CONDA_ENV defaults to the env currently provisioned on Luria; a different
# operator must set it to their own. LURIA_MAIL_USER has NO default on purpose --
# defaulting it would mail another operator's runs to whoever was hardcoded, so
# when unset the SBATCH mail directives are omitted entirely.
_CONDA_ENV_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_MAIL_USER_RE = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,10}")
_DEFAULT_CONDA_ENV = "cdemu_nfcore"


def resolve_conda_env(env: dict | None = None) -> str:
    """Conda env activated on the compute node. Invalid -> ValueError (fail-closed)."""
    env = env if env is not None else os.environ
    value = (env.get("LURIA_CONDA_ENV") or _DEFAULT_CONDA_ENV).strip()
    if not _CONDA_ENV_RE.fullmatch(value):
        raise ValueError(f"invalid LURIA_CONDA_ENV {value!r}")
    return value


def render_mail_directives(env: dict | None = None) -> str:
    """SBATCH mail directives, or "" when LURIA_MAIL_USER is unset."""
    env = env if env is not None else os.environ
    value = (env.get("LURIA_MAIL_USER") or "").strip()
    if not value:
        return ""
    if not _MAIL_USER_RE.fullmatch(value):
        raise ValueError(f"invalid LURIA_MAIL_USER {value!r}")
    return f"#SBATCH --mail-type=END\n#SBATCH --mail-user={value}"

DEFAULT_RESOURCES = {"partition": "bcc", "time": "48:00:00", "cpus": "16"}

# Job memory when the caller names none: 8 GB per CPU. nf-core's base.config labels ask for
# 6 GB per CPU (process_low 2/12 GB, process_medium 6/36 GB, process_high 12/72 GB), and 8 GB
# keeps that ratio CPU-bound once Nextflow's own share is taken off (16 CPUs: --mem=128G,
# executor 120 GB >= 16 x 6 GB). Capped below the one bcc node seen (96 CPUs, 376.6 GB), which
# 64 x 8 GB would exceed. Luria's partition memory policy (DefMemPerCPU, MaxMemPerCPU) is not
# established here. Under 4 GB a job cannot hold Nextflow's JVM and a task, so it is invalid.
_MEM_PER_CPU_GB = 8
_MAX_DEFAULT_MEM_GB = 320
_MIN_MEM_MB = 4 * 1024

_RES_PATTERNS = {
    "partition": re.compile(r"[A-Za-z0-9_-]{1,32}"),
    "time": re.compile(r"\d{1,3}:\d{2}:\d{2}"),
    "mem": re.compile(r"\d{1,4}[GM]"),
}
_JOB_NAME_STRIP = re.compile(r"[^A-Za-z0-9_.-]+")

_REVISION_RE = re.compile(r"[A-Za-z0-9._/-]{1,64}")
_PIPELINE_RE = re.compile(r"[A-Za-z0-9._:/-]{1,200}")


def validate_revision(revision: str) -> str:
    """Allow-list a pipeline revision; raise ValueError on anything shell-unsafe (fail-closed)."""
    if not revision or not _REVISION_RE.fullmatch(str(revision)):
        raise ValueError(f"invalid pipeline revision {revision!r}")
    return str(revision)


def validate_pipeline(pipeline: str) -> str:
    """Allow-list a pipeline name/URL; raise ValueError on anything shell-unsafe (fail-closed)."""
    if not pipeline or not _PIPELINE_RE.fullmatch(str(pipeline)):
        raise ValueError(f"invalid pipeline {pipeline!r}")
    return str(pipeline)


_GENOME_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def validate_genome(genome: str) -> str:
    """Allow-list an iGenomes key (e.g. GRCh38, GRCm39); raise on anything shell-unsafe (fail-closed)."""
    if not genome or not _GENOME_RE.fullmatch(str(genome)):
        raise ValueError(f"invalid genome {genome!r}")
    return str(genome)


_TEMPLATE = Path(__file__).parent / "templates" / "run.sh.tmpl"

_FETCHNGS_BLOCK_TMPL = """
# --- fetchngs pre-stage: fetch accession-only rows to the shared cache, then fill the sheet ---
# Rows with a local /net/bmc-* fastq are already filled and untouched; only rows with an
# empty fastq_1 + an SRA accession are fetched. fetchngs_helpers.py is staged beside run.sh.
CACHE="{FASTQ_CACHE}"
mkdir -p "$CACHE/fastq" "$CACHE/work"
python3 fetchngs_helpers.py ids
if [ -s ids.csv ]; then
  # Mark these accessions in use BEFORE checking the cache, under the lock the weekly
  # cleanup holds while it deletes, so it can't delete a file this run is about to reuse.
  # run.sh's exit trap releases them. Best-effort: the cleanup also refuses to delete while
  # this job is queued, so a failed claim warns and the run carries on.
  python3 fetchngs_helpers.py claim "$CACHE" "${{SLURM_JOB_ID:-}}" \\
    || echo "[FETCHNGS] could not mark the downloads in use; continuing" >&2
  # "cached" uses fill's own matcher (whole run or experiment id in fetchngs' <exp>_<run>
  # names), so the download is skipped only when fill will find every accession.
  if ! python3 fetchngs_helpers.py cached "$CACHE"; then
    # fetchngs.config: the /etc bind containerized wget needs to resolve ENA, a time limit
    # and retries fit for multi-GB downloads, and the job's SLURM allocation
    # (run_script.render_fetchngs_config says why for each).
    cat > fetchngs.config <<'FETCHNGS_CONFIG'
{FETCHNGS_CONFIG}FETCHNGS_CONFIG
    nextflow run nf-core/fetchngs -r {FETCHNGS_REVISION} -profile singularity -c fetchngs.config \\
      --input ids.csv --outdir "$CACHE" -w "$CACHE/work" -resume \\
      || {{ echo "[FETCHNGS] download failed -- see the fetchngs .nextflow.log" >&2; exit 1; }}
  fi
  python3 fetchngs_helpers.py fill "$CACHE" \\
    || {{ echo "[FETCHNGS] fill failed -- fastqs not present in $CACHE/fastq; aborting before the pipeline run" >&2; exit 1; }}
fi
"""

# --- nessie_status/: the labels the weekly cleanup (bin/nessie_cleanup.py) reads ---------
# finished.txt is written on every exit. SLURM's time limit sends TERM, which the TERM trap
# turns into exit 143 so the EXIT trap still runs (further TERMs are ignored while it
# labels). A run that fetches also releases its download claims here. NESSIE_PY is captured
# while the conda env is active: by the time the trap runs, `conda deactivate` has put the
# login python3 (possibly too old) first.
_STATUS_BLOCK_TMPL = """
# --- nessie_status/: labels the weekly cleanup (bin/nessie_cleanup.py) reads ---
# finished.txt is written on every exit, success or failure. chat_nextseek/luria/README.md
# says what each label means.
NESSIE_PY="$(command -v python3)"
mkdir -p nessie_status
nessie_finish() {{
  rc=$?
  trap - EXIT
  trap '' TERM
  if [ "$rc" -eq 0 ]; then result=succeeded; else result=failed; fi
  printf 'result: %s\\nexit_code: %s\\nfinished_at: %s\\n' "$result" "$rc" \\
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "{RUN_DIR}/nessie_status/finished.txt"
{RELEASE_LINE}  exit "$rc"
}}
trap nessie_finish EXIT
trap 'exit 143' TERM
"""

_RELEASE_LINE_TMPL = ('  [ -n "$NESSIE_PY" ] && "$NESSIE_PY" "{RUN_DIR}/fetchngs_helpers.py" release '
                      '"{FASTQ_CACHE}" "${{SLURM_JOB_ID:-}}" || true\n')


def render_status_block(*, run_dir: str, fastq_cache: str | None = None) -> str:
    """The run.sh block that creates nessie_status/ and writes finished.txt on every exit.
    With `fastq_cache` (a run that fetches), the exit also releases the run's download claims.
    `run_dir` is trusted config (LURIA_WORKING_PATH + a sanitized name), like everywhere here."""
    release = ""
    if fastq_cache:
        if not _REFS_ROOT_RE.fullmatch(str(fastq_cache)):
            raise ValueError(f"invalid fastq_cache {fastq_cache!r}")
        release = _RELEASE_LINE_TMPL.format(RUN_DIR=run_dir,
                                            FASTQ_CACHE=str(fastq_cache).rstrip("/"))
    return _STATUS_BLOCK_TMPL.format(RUN_DIR=run_dir, RELEASE_LINE=release)


_REFS_ROOT_RE = re.compile(r"[A-Za-z0-9_./-]{1,256}")

# Single source of truth for local reference genomes on Luria: genome key -> reference
# filenames under {LURIA_WORKING_PATH}/refs. Drives BOTH the luria.config genomes map and the
# explicit --fasta/--gtf CLI flags — because the map's params.genomes resolution is unreliable
# in Nextflow, submit_to_luria passes the paths directly (path > iGenomes, globally). Keys MUST
# match the igenomes_key values in reports/templates/nfcore/reference_bundles.json.
LURIA_GENOMES: dict[str, dict[str, str]] = {
    "GRCh38":  {"fasta": "GRCh38.primary_assembly.genome.fa.gz",
                "gtf":   "gencode.v46.basic.annotation.gtf.gz"},
    "GRCm39":  {"fasta": "GRCm39.primary_assembly.genome.fa.gz",
                "gtf":   "gencode.vM39.basic.annotation.gtf.gz"},
    "Mfas6.0": {"fasta": "Macaca_fascicularis.Macaca_fascicularis_6.0.dna.toplevel.fa.gz",
                "gtf":   "Macaca_fascicularis.Macaca_fascicularis_6.0.116.gtf.gz"},
    "Mmul_10": {"fasta": "Macaca_mulatta.Mmul_10.dna.toplevel.fa.gz",
                "gtf":   "Macaca_mulatta.Mmul_10.116.gtf.gz"},
}


def has_local_luria_ref(genome_key: str | None) -> bool:
    """True when genome_key has a local Luria reference registered in LURIA_GENOMES.

    Single source of truth shared by the host-side reference resolver
    (seqera.pipeline_params.build_reference_params) and the submit-side
    --fasta/--gtf injection, so the two can never disagree about whether a local
    reference exists.
    """
    return str(genome_key or "") in LURIA_GENOMES


def _ref_file(name: str, uncompressed: bool) -> str:
    """A LURIA_GENOMES filename, or its uncompressed twin (the same name without .gz),
    which is provisioned beside it on Luria for the pipelines that need it."""
    return name[:-3] if uncompressed and name.endswith(".gz") else name


def local_luria_ref_files(genome_key: str | None,
                          pipeline: str | None = None) -> dict[str, str] | None:
    """The {'fasta','gtf'} FILENAMES of the local Luria reference for genome_key,
    or None when there is no local ref. Filenames (not absolute paths) so the host
    side can name them without knowing the Luria refs_root. With `pipeline`, they are
    the files that pipeline's run is given (uncompressed for nf-core/rnavar)."""
    ref = LURIA_GENOMES.get(str(genome_key or ""))
    if not ref:
        return None
    plain = luria_launch_profile(pipeline)["uncompressed_refs"]
    return {"fasta": _ref_file(ref["fasta"], plain), "gtf": _ref_file(ref["gtf"], plain)}


_LURIA_CONFIG_HEADER = (
    "// luria.config — local reference genomes for nf-core on MIT Luria.\n"
    "// Generated from luria.run_script.LURIA_GENOMES. The params.genomes MAP resolution is\n"
    "// unreliable in Nextflow (getGenomeAttribute returns the value but the workflow assertion\n"
    "// sees empty), so submit_to_luria ALSO passes explicit --fasta/--gtf on the CLI — that is\n"
    "// what actually wires the references. This map is kept for --genome key identity.\n"
)


def genome_ref_paths(genome: str, refs_root: str, *,
                     uncompressed: bool = False) -> tuple[str | None, str | None]:
    """Absolute (fasta, gtf) for a genome key under refs_root, or (None, None) when the genome
    has no local refs registered (caller then falls back to --genome/iGenomes). `uncompressed`
    names the plain .fa/.gtf provisioned beside each .gz."""
    ref = LURIA_GENOMES.get(str(genome or ""))
    if not ref:
        return (None, None)
    root = str(refs_root or "").rstrip("/")
    return (f"{root}/{_ref_file(ref['fasta'], uncompressed)}",
            f"{root}/{_ref_file(ref['gtf'], uncompressed)}")


# How a pipeline is launched when it differs from the default, which every pipeline was
# built and tested on (rnaseq): the legacy v1 Nextflow parser and the gzipped references.
# nf-core/rnavar 1.3.0 (VERIFIED on Luria, first run 2026-10-20) needs plain files: its
# schema rejects a .gtf.gz (pattern ^\S+\.gtf$), and given a .fa.gz its GUNZIP_FASTA got no
# task, so nothing downstream ran and Nextflow still reported success (cause unknown). The
# run that worked used the v2 parser; whether rnavar needs v2 is NOT established.
_DEFAULT_LAUNCH = {"syntax_parser": "v1", "uncompressed_refs": False}
LURIA_PIPELINE_LAUNCH: dict[str, dict] = {
    "nf-core/rnavar": {"syntax_parser": "v2", "uncompressed_refs": True},
}


def luria_launch_profile(pipeline: str | None) -> dict:
    """The launch settings for a pipeline ('nf-core/rnavar', its GitHub URL, or the bare
    catalog key 'rnavar'); the defaults when it registers none."""
    name = _normalize_pipeline_name(pipeline or "")
    if name and "/" not in name:
        name = f"nf-core/{name}"
    return {**_DEFAULT_LAUNCH, **LURIA_PIPELINE_LAUNCH.get(name, {})}


def luria_params_overrides(pipeline: str | None, genome: str | None) -> dict:
    """Params the launcher adds to params.yml. Under the v2 parser a bare boolean CLI flag
    reaches nf-schema as the string "true" ("Value is [string] but should be [boolean]";
    VERIFIED on Luria with rnavar), so igenomes_ignore goes in params.yml as a YAML boolean
    whenever local refs replace iGenomes. Under v1 it stays on the CLI, as it always was."""
    if luria_launch_profile(pipeline)["syntax_parser"] == "v2" and has_local_luria_ref(genome):
        return {"igenomes_ignore": True}
    return {}


def render_luria_config(refs_root: str, resources: dict | None = None) -> str:
    """Generate luria.config (the local reference-genomes map) from LURIA_GENOMES, plus the
    executor limits for `resources` (the same dict run.sh's #SBATCH lines are filled from).
    `refs_root` (:= <LURIA_WORKING_PATH>/refs) is trusted config, path-validated fail-closed."""
    if not refs_root or not _REFS_ROOT_RE.fullmatch(str(refs_root)):
        raise ValueError(f"invalid refs_root {refs_root!r}")
    root = str(refs_root).rstrip("/")
    lines = [_LURIA_CONFIG_HEADER, "params {", "    genomes {"]
    for key, ref in LURIA_GENOMES.items():
        lines += [f"        '{key}' {{",
                  f"            fasta = '{root}/{ref['fasta']}'",
                  f"            gtf   = '{root}/{ref['gtf']}'",
                  "        }"]
    lines += ["    }", "}", ""]
    # Newer nf-core templates (rnaseq 3.22) set process.shell to a multi-line string. Nextflow
    # 26.04 pastes that string into the container launch verbatim, so bash then runs
    # `.command.run` as a program and every task exits 126 ("Permission denied"; VERIFIED on
    # Luria). A -c config outranks the pipeline's own, and this list form is what fetchngs
    # 1.12 sets and runs with in the same job.
    lines += ["process.shell = ['/bin/bash', '-euo', 'pipefail']", ""]
    return "\n".join(lines) + render_executor_config(resources)


_PROC_NAME_RE = re.compile(r"[A-Za-z0-9_]{1,64}")
_EXT_ARGS_RE = re.compile(r"[A-Za-z0-9_.,=:/ -]{1,200}")

# A -c config REPLACES a process's ext.args (Nextflow can't append across configs — VERIFIED on
# Luria: a self-referencing closure StackOverflows, a plain string clobbers). So overriding ext.args
# is only safe for processes whose PIPELINE default is empty (e.g. alevin's SIMPLEAF_QUANT). These
# processes have a NON-empty default in nf-core/scrnaseq — overriding them would drop needed flags.
# Tune them via the samplesheet instead: STAR (STAR_ALIGN) cell-calling reads `expected_cells` and
# otherwise uses STAR's built-in knee-based --soloCellFilter, so it needs no ext.args override.
_CLOBBER_UNSAFE_PROCESSES = {"STAR_ALIGN"}


def render_process_config(process_args: dict[str, str] | None) -> str:
    """Render a Nextflow process-scope config from {PROCESS_NAME: ext_args}, e.g.
    {'SIMPLEAF_QUANT': '--knee'} -> `process { withName: '.*:SIMPLEAF_QUANT' { ext.args='--knee' } }`.
    The DATA is declared by the curated per-pipeline JSON ('protocol_process_args'), NOT hardcoded
    here — this is just the renderer. Names/args validated fail-closed; raises for a process whose
    pipeline ext.args default is non-empty (would be clobbered). '' for empty input.

    (e.g. scrnaseq seqwell/dropseq declares SIMPLEAF_QUANT '--knee': 2.7.1 needs a cell-calling
    mode and its unfiltered fallback wants a barcode whitelist bead protocols lack. STAR is fine
    without an override — it tunes cell-calling via the samplesheet `expected_cells` column.)"""
    if not process_args:
        return ""
    blocks = []
    for proc, args in process_args.items():
        if not _PROC_NAME_RE.fullmatch(str(proc)):
            raise ValueError(f"invalid process name {proc!r}")
        if str(proc) in _CLOBBER_UNSAFE_PROCESSES:
            raise ValueError(
                f"process {proc!r} has a non-empty pipeline ext.args default a -c override would "
                "clobber (Nextflow can't append); tune it via the samplesheet (e.g. expected_cells) instead")
        if not _EXT_ARGS_RE.fullmatch(str(args)):
            raise ValueError(f"invalid ext.args {args!r}")
        blocks.append(f"    withName: '.*:{proc}' {{\n        ext.args = '{args}'\n    }}")
    return "\nprocess {\n" + "\n".join(blocks) + "\n}\n"


def _mem_mb(mem: str) -> int:
    """MB in a validated SLURM memory string ('128G', '16384M')."""
    return int(mem[:-1]) * (1024 if mem.endswith("G") else 1)


def validate_resources(resources: dict | None) -> dict:
    """Return a full resource dict; each field taken from `resources` only if valid, else default.
    The default `mem` follows the CPU count (_MEM_PER_CPU_GB, capped at _MAX_DEFAULT_MEM_GB)."""
    resources = resources if isinstance(resources, dict) else {}
    out = dict(DEFAULT_RESOURCES)
    for key in ("partition", "time"):
        val = resources.get(key)
        if val is not None and _RES_PATTERNS[key].fullmatch(str(val)):
            out[key] = str(val)
    try:
        cpus = int(resources.get("cpus"))
        if 1 <= cpus <= 64:
            out["cpus"] = str(cpus)
    except (TypeError, ValueError):
        pass
    mem = resources.get("mem")
    if mem is not None and _RES_PATTERNS["mem"].fullmatch(str(mem)) \
            and _mem_mb(str(mem)) >= _MIN_MEM_MB:
        out["mem"] = str(mem)
    else:
        out["mem"] = f"{min(int(out['cpus']) * _MEM_PER_CPU_GB, _MAX_DEFAULT_MEM_GB)}G"
    return out


def nextflow_executor_limits(resources: dict | None) -> dict:
    """The local executor's share of the SLURM allocation: every CPU, and the job's memory less
    a reserve for Nextflow's own JVM (1/16 of it, between 2 and 8 GB: 128G -> 120 GB)."""
    res = validate_resources(resources)
    mem_gb = _mem_mb(res["mem"]) // 1024
    reserve = min(8, max(2, mem_gb // 16))
    return {"cpus": int(res["cpus"]), "memory_gb": mem_gb - reserve}


def render_executor_config(resources: dict | None) -> str:
    """The luria.config block that holds Nextflow to the job's SLURM allocation.

    The whole run is one SLURM job with the local executor, which otherwise sizes itself to the
    node: Luria does not confine the job (nproc = 96), so `executor > cpus=96; memory=376.6 GB`.
    The first rnavar 1.3.0 run then started ~50 scattered GATK JVMs at once and ran out of the
    node's per-user process limit (pthread_create EAGAIN, 49 tasks ABORTED; VERIFIED on Luria,
    2026-10-20). resourceLimits clamps each task to the same numbers, because the local
    executor refuses a task that asks for more than it has ("Process requirement exceeds
    available CPUs"): a 200 GB process_high_memory label, or an nf-core retry asking
    attempt x CPUs, would otherwise end the run."""
    lim = nextflow_executor_limits(resources)
    cpus, mem = lim["cpus"], f"'{lim['memory_gb']} GB'"
    return ("\n// The SLURM allocation run.sh requests (#SBATCH -n / --mem), less Nextflow's own\n"
            "// memory. Without it the local executor takes the whole node.\n"
            f"executor {{\n    cpus = {cpus}\n    memory = {mem}\n}}\n"
            f"process.resourceLimits = [cpus: {cpus}, memory: {mem}]\n")


# nf-core/fetchngs 1.12.0's download processes, read from its modules at that tag: SRA_FASTQ_FTP
# (default --download_method ftp) and SRATOOLS_PREFETCH are process_low, 4 h x attempt;
# SRATOOLS_FASTERQDUMP and ASPERA_CLI are process_medium, 8 h x attempt. Only SRA_FASTQ_FTP
# retries any failure (error_retry: 2 retries); the others retry only exits 130-145 and 104.
# Six 16.5-21.1 GB human RNA-seq runs (SRR21023814-19, hlatyping, SLURM 11426306,
# 2026-10-20) fetched none in that time: an SRA_FASTQ_FTP task was killed at exactly 4 h and
# the job failed with 0 of 6 samples. wget runs with -T 60 -t 5, so a stalled transfer still
# fails within minutes; the longer limit only lets a slow, live one finish. Three attempts of
# 24, 48 and 72 h stay inside the 14-day limit the job ran under. A retry starts the file over.
_FETCHNGS_DOWNLOAD_PROCESSES = ("SRA_FASTQ_FTP", "SRATOOLS_PREFETCH", "SRATOOLS_FASTERQDUMP",
                                "ASPERA_CLI")
_FETCHNGS_DOWNLOAD_HOURS = 24
_FETCHNGS_DOWNLOAD_RETRIES = 2


def render_fetchngs_config(resources: dict | None = None) -> str:
    """fetchngs.config, which run.sh writes before the fetchngs pre-stage.

    - singularity.runOptions: fetchngs' minimal wget container has no /etc/resolv.conf and
      Luria's singularity cannot create that file mountpoint, so containerized wget can't
      resolve ENA. Nextflow ignores $SINGULARITY_BIND, so the host /etc DIRECTORY is bound
      here (VERIFIED on Luria: fetchngs then resolves ftp.sra.ebi.ac.uk).
    - the download processes get _FETCHNGS_DOWNLOAD_HOURS x attempt and retry any failure
      (see _FETCHNGS_DOWNLOAD_PROCESSES for why).
    - the executor block holds fetchngs to the SLURM allocation, as luria.config does for the
      pipeline (render_executor_config): a retried FASTERQDUMP asks attempt x 6 CPUs."""
    names = "|".join(_FETCHNGS_DOWNLOAD_PROCESSES)
    return ("singularity.runOptions = '-B /etc'\n"
            "\n// Multi-GB SRA downloads outlast fetchngs' 4 h (process_low) and 8 h\n"
            "// (process_medium) task limits.\n"
            "process {\n"
            f"    withName: '{names}' {{\n"
            f"        time = {{ {_FETCHNGS_DOWNLOAD_HOURS}.h * task.attempt }}\n"
            "        errorStrategy = 'retry'\n"
            f"        maxRetries = {_FETCHNGS_DOWNLOAD_RETRIES}\n"
            "    }\n"
            "}\n"
            + render_executor_config(resources))


def sanitize_job_name(name: str, fallback: str = "nfcore_run") -> str:
    """Reduce an arbitrary name to a SLURM-safe token."""
    # No leading "." or "-": a name shaped like the cleanup's tombstone (".nessie-deleting-...") would lose its live work folder.
    cleaned = _JOB_NAME_STRIP.sub("_", (name or "").strip())[:64].strip("_").lstrip(".-").strip("_")
    return cleaned or fallback


# Vendored, locally-patched pipeline clones on Luria that REPLACE the stock remote pipeline for a
# specific (pipeline, aligner). Keyed general so a future patched pipeline is one more entry. Needed
# where the stock nf-core pipeline can't run a case: nf-core/scrnaseq 2.7.1 STARsolo has no
# whitelist-less bead-protocol support (hardcoded --soloCBwhitelist + missing dropseq geometry), so
# `aligner=star` runs a clone patched for `--soloCBwhitelist None` + seqwell 12/8 geometry. alevin is
# unaffected (stays on the stock remote). Provision with luria/pipelines/scrnaseq_2_7_1_star/provision.sh.
LURIA_VENDORED_PIPELINES: dict[tuple[str, str], dict[str, str]] = {
    ("nf-core/scrnaseq", "star"): {
        "path": "{WORKING}/pipelines/scrnaseq-2.7.1-star-patched",
        "base": "nf-core/scrnaseq",
        "revision": "2.7.1",
    },
}

_GITHUB_PREFIX_RE = re.compile(r"^https?://github\.com/")


def _normalize_pipeline_name(pipeline: str) -> str:
    """Reduce a pipeline field ('nf-core/scrnaseq' or 'https://github.com/nf-core/scrnaseq[.git]')
    to the bare 'org/name' used as the vendored-registry key."""
    p = _GITHUB_PREFIX_RE.sub("", str(pipeline or "").strip())
    return p[:-4] if p.endswith(".git") else p


def resolve_pipeline_source(pipeline: str, aligner: str | None, revision: str,
                            working: str | None) -> tuple[str, str]:
    """Return (source, revision_flag) for the `nextflow run` invocation. For a registered
    (pipeline, aligner) vendored clone, source is the LOCAL clone path and revision_flag is '' — a
    `-r <tag>` on a local git clone would `git checkout` the tag and WIPE the patches. Otherwise
    source is the stock pipeline and revision_flag is '-r <revision>'. Falls back to stock when no
    working path is available (the clone path can't be built)."""
    entry = LURIA_VENDORED_PIPELINES.get((_normalize_pipeline_name(pipeline), str(aligner or "")))
    if entry and working:
        return entry["path"].format(WORKING=str(working).rstrip("/")), ""
    return str(pipeline), f"-r {revision}"


def render_run_script(*, job_name: str, pipeline: str, revision: str, run_dir: str,
                      work_dir: str, singularity_cache: str, genome: str,
                      resources: dict | None, refs_root: str | None = None,
                      aligner: str | None = None, working: str | None = None,
                      needs_fetch: bool = False, fastq_cache: str | None = None,
                      fetchngs_revision: str = "1.12.0") -> str:
    """Substitute the validated slots into the fixed run.sh template. When `refs_root` is given
    and `genome` has local refs registered (LURIA_GENOMES), explicit --fasta/--gtf flags are
    injected (path > iGenomes globally; the genomes-map resolution is unreliable). When
    (pipeline, aligner) has a vendored clone registered (LURIA_VENDORED_PIPELINES) and `working`
    is given, the run uses that local clone with NO `-r` (else the tag checkout wipes the patches).
    The Nextflow parser and compressed-or-plain references follow LURIA_PIPELINE_LAUNCH."""
    revision = validate_revision(revision)
    pipeline = validate_pipeline(pipeline)
    genome = validate_genome(genome)
    res = validate_resources(resources)
    profile = luria_launch_profile(pipeline)
    source, rev = resolve_pipeline_source(pipeline, aligner, revision, working)
    source = validate_pipeline(source)          # clone path or stock name; both match _PIPELINE_RE
    revision_flag = f" {rev}" if rev else ""     # leading space only when present -> no double space
    refs_flags = ""
    if refs_root:
        if not _REFS_ROOT_RE.fullmatch(str(refs_root)):
            raise ValueError(f"invalid refs_root {refs_root!r}")
        fasta, gtf = genome_ref_paths(genome, refs_root,
                                      uncompressed=profile["uncompressed_refs"])
        if fasta and gtf:
            # --igenomes_ignore: --genome otherwise also pulls iGenomes' prebuilt indexes,
            # which --fasta/--gtf do not replace, and rnaseq 3.22 aligned our GENCODE refs
            # with iGenomes' old STARIndex ("Genome version: 20201 is INCOMPATIBLE";
            # VERIFIED on Luria). Ignored, the pipeline builds its indexes from these files.
            # Under v2 it is in params.yml instead (luria_params_overrides).
            refs_flags = f"--fasta {fasta} --gtf {gtf}"
            if not luria_params_overrides(pipeline, genome).get("igenomes_ignore"):
                refs_flags += " --igenomes_ignore"
    fetchngs_block = ""
    if needs_fetch:
        if not fastq_cache or not _REFS_ROOT_RE.fullmatch(str(fastq_cache)):
            raise ValueError(f"invalid fastq_cache {fastq_cache!r}")
        fq_rev = validate_revision(fetchngs_revision)
        fetchngs_block = _FETCHNGS_BLOCK_TMPL.format(
            FASTQ_CACHE=str(fastq_cache).rstrip("/"), FETCHNGS_REVISION=fq_rev,
            FETCHNGS_CONFIG=render_fetchngs_config(res))
    mapping = {
        "JOB_NAME": sanitize_job_name(job_name),
        "CPUS": res["cpus"],
        "MEM": res["mem"],
        "PARTITION": res["partition"],
        "RUN_DIR": run_dir,
        "PIPELINE": source,
        "REVISION_FLAG": revision_flag,
        "GENOME": genome,
        "SYNTAX_PARSER": profile["syntax_parser"],
        "REFS_FLAGS": refs_flags,
        "FETCHNGS_BLOCK": fetchngs_block,
        "STATUS_BLOCK": render_status_block(
            run_dir=run_dir, fastq_cache=str(fastq_cache) if needs_fetch else None),
        "WORK_DIR": work_dir,
        "SINGULARITY_CACHE": singularity_cache,
        "MAIL_DIRECTIVES": render_mail_directives(),
        "CONDA_ENV": resolve_conda_env(),
    }
    out = _TEMPLATE.read_text(encoding="utf-8")
    for token, value in mapping.items():
        out = out.replace("{{" + token + "}}", str(value))
    return out

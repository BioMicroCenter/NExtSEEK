"""Luria-direct submitter.

Reads chat_nextseek's emitted launch.yml (the same file the Tower path reads),
renders a run.sh per launch entry, and stages four files into a per-run remote
directory: run.sh, luria.config (local reference-genome map, passed via -c),
params.yml (curated per-pipeline params, passed via -params-file), and
samplesheet.csv. CLI flags in run.sh (--input/--outdir/--genome) override
params.yml. Then `sbatch`es it over SSH. Returns one ref dict per submitted run
(SLURM job id + remote dir + log path).
"""
from __future__ import annotations

import os
import re
import tempfile
from datetime import datetime
from pathlib import Path

try:
    import yaml as _yaml
except Exception:  # pragma: no cover
    _yaml = None

from .run_script import render_run_script, render_luria_config, render_process_config, sanitize_job_name
from .fetchngs_helpers import needs_fetch_accessions
from .ssh import prepare_key, ssh_run, scp_file
from ..seqera.catalog import NFCORE_PIPELINE_CATALOG

_JOB_ID_RE = re.compile(r"Submitted batch job (\d+)")
_REQUIRED_ENV = ("user", "key", "working_path", "host")

# Params the CLI owns in run.sh (--input/--outdir/--genome); stripped from the
# per-pipeline params.yml so there is no conflicting duplicate.
_PARAMS_STRIP = ("input", "outdir", "genome")


def _build_params_yml(launch_params: dict | None) -> str:
    """YAML for `-params-file`: the curated per-pipeline params minus the keys the
    CLI supplies. Always valid YAML (at least '{}')."""
    p = {k: v for k, v in (launch_params or {}).items()
         if k not in _PARAMS_STRIP and v is not None}
    return _yaml.safe_dump(p, sort_keys=True, default_flow_style=False) if p else "{}\n"


def _write_temp(text: str, *, prefix: str, suffix: str) -> str:
    """Write text to a NamedTemporary-style temp file; caller removes it."""
    fd, path = tempfile.mkstemp(prefix=prefix, suffix=suffix)
    os.close(fd)
    Path(path).write_text(text, encoding="utf-8")
    return path


def submit_luria(launch_yml_path, *, luria_env: dict, resources: dict | None = None,
                 job_name: str | None = None, samplesheet_local: str | None = None,
                 genome: str | None = None, launch_params: dict | None = None,
                 process_args: dict | None = None, cwd=None) -> list[dict]:
    """Submit each launch.yml entry to Luria via ssh+sbatch. Returns run refs (empty on any skip)."""
    launch_path = Path(launch_yml_path).resolve()
    if not launch_path.exists():
        print(f"[LURIA][SUBMIT] launch.yml not found: {launch_path}")
        return []
    if not all(luria_env.get(k) for k in _REQUIRED_ENV):
        print("[LURIA][SUBMIT] LURIA env incomplete — skipping")
        return []
    if _yaml is None:
        raise RuntimeError("PyYAML is required to parse launch.yml for Luria submission.")

    doc = _yaml.safe_load(launch_path.read_text(encoding="utf-8")) or {}
    entries = doc.get("launch") if isinstance(doc, dict) else None
    if not entries:
        print(f"[LURIA][SUBMIT] no launch entries in {launch_path}")
        return []

    working = str(luria_env["working_path"]).rstrip("/")
    parent = launch_path.parent
    key_path = prepare_key(luria_env["key"])
    runs: list[dict] = []
    try:
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict):
                continue
            try:
                ref = _submit_one(entry, idx, parent, working, luria_env, resources, job_name, key_path,
                                  samplesheet_local, genome, launch_params, process_args)
                if ref:
                    runs.append(ref)
            except Exception as exc:
                print(f"[LURIA][SUBMIT] entry {entry.get('name', idx)!r} failed: {exc!r}")
    finally:
        try:
            os.remove(key_path)
        except OSError:
            pass
    return runs


def _sheet_needs_fetch(sheet_path: str) -> bool:
    """True when the staged samplesheet has at least one blank-fastq SRR row to fetch."""
    import csv
    try:
        with open(sheet_path, newline="") as fh:
            rows = list(csv.DictReader(fh))
    except OSError:
        return False
    return bool(needs_fetch_accessions(rows))


def _submit_one(entry, idx, parent, working, luria_env, resources, job_name, key_path,
                samplesheet_local=None, genome=None, launch_params=None, process_args=None):
    name = (entry.get("name") or f"run{idx}").strip() or f"run{idx}"
    pipeline = entry.get("pipeline")
    revision = entry.get("revision")
    if not pipeline or not revision:
        print(f"[LURIA][SUBMIT] entry {name!r} missing pipeline/revision — skipping")
        return None

    # Local samplesheet: prefer the explicit path from state; the params.yml the Tower
    # emitter writes points input at a bucket path, so we never trust it here. Fall back
    # to a samplesheet.csv co-located with the launch.yml.
    local_sheet = samplesheet_local
    if not local_sheet or not Path(local_sheet).exists():
        candidate = parent / "samplesheet.csv"
        if candidate.exists():
            local_sheet = str(candidate)
    if not local_sheet or not Path(local_sheet).exists():
        print(f"[LURIA][SUBMIT] local samplesheet not found for entry {name!r}: {local_sheet!r}")
        return None

    safe = sanitize_job_name(job_name or name)
    run_id = datetime.now().strftime("%y%m%d_%H%M%S") + f"_{idx}"
    remote_run_dir = f"{working}/runs/{safe}_{run_id}"
    work_dir = f"{working}/work/{safe}"
    cache_dir = f"{working}/singularity_cache"
    # Genome is the species-resolved iGenomes key (mouse->GRCm39, human->GRCh38) threaded from
    # configure_run; default to GRCh38 loudly rather than silently mis-aligning a non-human cohort.
    run_genome = genome or "GRCh38"
    if not genome:
        print(f"[LURIA][SUBMIT] entry {name!r}: no resolved genome — defaulting to GRCh38 "
              "(VERIFY the cohort is human before trusting results!)")

    # run.sh drives the run with explicit --fasta/--gtf for the resolved genome (path > iGenomes;
    # the params.genomes map resolution is unreliable), -params-file params.yml (curated params),
    # and -c luria.config (genomes map for --genome identity + a per-protocol SIMPLEAF_QUANT
    # --knee block for scrnaseq bead protocols). Stage run.sh + luria.config + params.yml + sheet.
    refs_root = f"{working}/refs"
    needs_fetch = _sheet_needs_fetch(local_sheet)
    fastq_cache = f"{working}/fastq_cache"
    fetchngs_rev = NFCORE_PIPELINE_CATALOG.get("fetchngs", {}).get("default_revision", "1.12.0")
    tmp_files: list[str] = []
    try:
        # Which of --genome/--fasta/--gtf this pipeline's schema declares. nf-schema
        # aborts on an unrecognised param, so an unconditional --gtf kills methylseq /
        # sarek / seqinspector, and ampliseq declares none of the three.
        pipeline_key = str(pipeline or "").rsplit("/", 1)[-1].lower()
        ref_flags = NFCORE_PIPELINE_CATALOG.get(pipeline_key, {}).get("reference_cli_flags")
        run_sh = render_run_script(
            job_name=safe, pipeline=pipeline, revision=revision, run_dir=remote_run_dir,
            work_dir=work_dir, singularity_cache=cache_dir, genome=run_genome, resources=resources,
            refs_root=refs_root, aligner=(launch_params or {}).get("aligner"), working=working,
            needs_fetch=needs_fetch, fastq_cache=fastq_cache, fetchngs_revision=fetchngs_rev,
            reference_cli_flags=ref_flags,
        )
        run_tmp = _write_temp(run_sh, prefix="run_", suffix=".sh"); tmp_files.append(run_tmp)
        # luria.config = genomes map + any curated per-protocol process ext.args (e.g. seqwell/dropseq
        # -> SIMPLEAF_QUANT --knee); process_args comes from the pipeline JSON via tool_submit_to_luria.
        luria_config = render_luria_config(refs_root) + render_process_config(process_args)
        cfg_tmp = _write_temp(luria_config, prefix="luriacfg_", suffix=".config")
        tmp_files.append(cfg_tmp)
        params_tmp = _write_temp(_build_params_yml(launch_params), prefix="params_", suffix=".yml")
        tmp_files.append(params_tmp)

        ssh_run(luria_env, f"mkdir -p {remote_run_dir} {work_dir}", key_path=key_path)
        scp_file(luria_env, run_tmp, f"{remote_run_dir}/run.sh", key_path=key_path)
        scp_file(luria_env, cfg_tmp, f"{remote_run_dir}/luria.config", key_path=key_path)
        scp_file(luria_env, params_tmp, f"{remote_run_dir}/params.yml", key_path=key_path)
        scp_file(luria_env, local_sheet, f"{remote_run_dir}/samplesheet.csv", key_path=key_path)
        if needs_fetch:
            helper = str(Path(__file__).parent / "fetchngs_helpers.py")
            scp_file(luria_env, helper, f"{remote_run_dir}/fetchngs_helpers.py", key_path=key_path)
        out = ssh_run(luria_env, f"cd {remote_run_dir} && sbatch run.sh", key_path=key_path)
    finally:
        for _tmp in tmp_files:
            try:
                os.remove(_tmp)
            except OSError:
                pass

    m = _JOB_ID_RE.search(out or "")
    job_id = m.group(1) if m else None
    return {
        "job_id": job_id,
        "remote_dir": remote_run_dir,
        "log": f"{remote_run_dir}/{safe}.out",
        "run_name": name,
    }

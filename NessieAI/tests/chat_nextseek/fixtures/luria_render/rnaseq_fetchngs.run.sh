#!/bin/bash
#SBATCH -N 1
#SBATCH -n 16
#SBATCH -p bcc

#SBATCH --job-name=run
#SBATCH --output=/net/x/runs/run_1/run.out
#SBATCH --error=/net/x/runs/run_1/run.err

module add miniconda3/v4
source /home/software/conda/miniconda3/etc/profile.d/conda.sh
conda activate cdemu_nfcore
module add singularity/3.10.4

export NXF_SINGULARITY_CACHEDIR=/net/x/singularity_cache
# Most nf-core pipelines (e.g. rnaseq 3.18.0) use the legacy Nextflow config
# idiom (def vars mixed with config statements), which the strict parser that is
# default in Nextflow 25.x+ rejects ("Variable declarations cannot be mixed with
# config statements"), so they get the v1 (legacy) parser. A pipeline registered
# in run_script.LURIA_PIPELINE_LAUNCH (nf-core/rnavar) runs on the parser recorded there.
export NXF_SYNTAX_PARSER=v1

# Luria compute nodes ship a CA bundle missing the Let's Encrypt root (ISRG Root
# X1), so singularity can't verify depot.galaxyproject.org TLS and container
# pulls die with "x509: certificate signed by unknown authority". Point it at a
# current bundle staged on shared storage ({WORKING_PATH}/certs/ca-bundle.crt,
# beside the singularity cache).
export SSL_CERT_FILE="$(dirname /net/x/singularity_cache)/certs/ca-bundle.crt"

cd /net/x/runs/run_1

# --- nessie_status/: labels the weekly cleanup (bin/nessie_cleanup.py) reads ---
# finished.txt is written on every exit, success or failure. chat_nextseek/luria/README.md
# says what each label means.
NESSIE_PY="$(command -v python3)"
mkdir -p nessie_status
nessie_finish() {
  rc=$?
  trap - EXIT
  trap '' TERM
  if [ "$rc" -eq 0 ]; then result=succeeded; else result=failed; fi
  printf 'result: %s\nexit_code: %s\nfinished_at: %s\n' "$result" "$rc" \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "/net/x/runs/run_1/nessie_status/finished.txt"
  [ -n "$NESSIE_PY" ] && "$NESSIE_PY" "/net/x/runs/run_1/fetchngs_helpers.py" release "/net/x/fastq_cache" "${SLURM_JOB_ID:-}" || true
  exit "$rc"
}
trap nessie_finish EXIT
trap 'exit 143' TERM


# --- fetchngs pre-stage: fetch accession-only rows to the shared cache, then fill the sheet ---
# Rows with a local /net/bmc-* fastq are already filled and untouched; only rows with an
# empty fastq_1 + an SRA accession are fetched. fetchngs_helpers.py is staged beside run.sh.
CACHE="/net/x/fastq_cache"
mkdir -p "$CACHE/fastq" "$CACHE/work"
python3 fetchngs_helpers.py ids
if [ -s ids.csv ]; then
  # Mark these accessions in use BEFORE checking the cache, under the lock the weekly
  # cleanup holds while it deletes, so it can't delete a file this run is about to reuse.
  # run.sh's exit trap releases them. Best-effort: the cleanup also refuses to delete while
  # this job is queued, so a failed claim warns and the run carries on.
  python3 fetchngs_helpers.py claim "$CACHE" "${SLURM_JOB_ID:-}" \
    || echo "[FETCHNGS] could not mark the downloads in use; continuing" >&2
  # "cached" uses fill's own matcher (whole run or experiment id in fetchngs' <exp>_<run>
  # names), so the download is skipped only when fill will find every accession.
  if ! python3 fetchngs_helpers.py cached "$CACHE"; then
    # fetchngs.config: the /etc bind containerized wget needs to resolve ENA, a time limit
    # and retries fit for multi-GB downloads, and the job's SLURM allocation
    # (run_script.render_fetchngs_config says why for each).
    cat > fetchngs.config <<'FETCHNGS_CONFIG'
singularity.runOptions = '-B /etc'

// Multi-GB SRA downloads outlast fetchngs' 4 h (process_low) and 8 h
// (process_medium) task limits.
process {
    withName: 'SRA_FASTQ_FTP|SRATOOLS_PREFETCH|SRATOOLS_FASTERQDUMP|ASPERA_CLI' {
        time = { 24.h * task.attempt }
        errorStrategy = 'retry'
        maxRetries = 2
    }
}

// The SLURM allocation run.sh requests (#SBATCH -n / --mem), less Nextflow's own
// memory. Without it the local executor takes the whole node.
executor {
    cpus = 16
    memory = '120 GB'
}
process.resourceLimits = [cpus: 16, memory: '120 GB']
FETCHNGS_CONFIG
    nextflow run nf-core/fetchngs -r 1.12.0 -profile singularity -c fetchngs.config \
      --input ids.csv --outdir "$CACHE" -w "$CACHE/work" -resume \
      || { echo "[FETCHNGS] download failed -- see the fetchngs .nextflow.log" >&2; exit 1; }
  fi
  python3 fetchngs_helpers.py fill "$CACHE" \
    || { echo "[FETCHNGS] fill failed -- fastqs not present in $CACHE/fastq; aborting before the pipeline run" >&2; exit 1; }
fi


# Per-pipeline scientific params come from params.yml (built from the curated
# per-pipeline JSON in reports/templates/nfcore/); local reference genomes come
# from luria.config (-c). The CLI flags below (--input/--outdir/--genome)
# override params.yml so the run uses the staged samplesheet, this run dir, and
# the species-resolved genome key.
nextflow run nf-core/rnaseq -r 3.22.2 -profile singularity \
    -c luria.config \
    -params-file params.yml \
    --input samplesheet.csv \
    --outdir . \
    --genome GRCm39 \
    --fasta /net/x/refs/GRCm39.primary_assembly.genome.fa.gz --gtf /net/x/refs/gencode.vM39.basic.annotation.gtf.gz --igenomes_ignore \
    -w /net/x/work/run \
    -resume
# The exit trap above labels the run from this script's exit status, so keep nextflow's
# status past `conda deactivate` (whose own status would otherwise be the script's).
nessie_rc=$?

conda deactivate
exit "$nessie_rc"

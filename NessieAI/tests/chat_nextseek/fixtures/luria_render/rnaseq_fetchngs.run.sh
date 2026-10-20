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

# --- fetchngs pre-stage: fetch accession-only rows to the shared cache, then fill the sheet ---
# Rows with a local /net/bmc-* fastq are already filled and untouched; only rows with an
# empty fastq_1 + an SRA accession are fetched. fetchngs_helpers.py is staged beside run.sh.
CACHE="/net/x/fastq_cache"
mkdir -p "$CACHE/fastq" "$CACHE/work"
python3 fetchngs_helpers.py ids
if [ -s ids.csv ]; then
  # "cached" uses fill's own matcher (whole run or experiment id in fetchngs' <exp>_<run>
  # names), so the download is skipped only when fill will find every accession.
  if ! python3 fetchngs_helpers.py cached "$CACHE"; then
    # fetchngs' minimal wget container has no /etc/resolv.conf and Luria's singularity cannot
    # create that file mountpoint, so containerized wget can't resolve ENA. Nextflow ignores
    # $SINGULARITY_BIND, so pass a host /etc DIRECTORY bind via singularity.runOptions in a
    # config (verified end-to-end on Luria: fetchngs then resolves ftp.sra.ebi.ac.uk).
    printf "singularity.runOptions = '-B /etc'\n" > fetchngs.config
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

conda deactivate

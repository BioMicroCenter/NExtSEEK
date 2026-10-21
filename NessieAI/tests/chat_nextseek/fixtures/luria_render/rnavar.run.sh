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
export NXF_SYNTAX_PARSER=v2

# Luria compute nodes ship a CA bundle missing the Let's Encrypt root (ISRG Root
# X1), so singularity can't verify depot.galaxyproject.org TLS and container
# pulls die with "x509: certificate signed by unknown authority". Point it at a
# current bundle staged on shared storage ({WORKING_PATH}/certs/ca-bundle.crt,
# beside the singularity cache).
export SSL_CERT_FILE="$(dirname /net/x/singularity_cache)/certs/ca-bundle.crt"

cd /net/x/runs/run_1


# Per-pipeline scientific params come from params.yml (built from the curated
# per-pipeline JSON in reports/templates/nfcore/); local reference genomes come
# from luria.config (-c). The CLI flags below (--input/--outdir/--genome)
# override params.yml so the run uses the staged samplesheet, this run dir, and
# the species-resolved genome key.
nextflow run nf-core/rnavar -r 1.3.0 -profile singularity \
    -c luria.config \
    -params-file params.yml \
    --input samplesheet.csv \
    --outdir . \
    --genome GRCh38 \
    --fasta /net/x/refs/GRCh38.primary_assembly.genome.fa --gtf /net/x/refs/gencode.v46.basic.annotation.gtf \
    -w /net/x/work/run \
    -resume
# Without this the job ends with `conda deactivate`'s status, so SLURM records a
# failed pipeline as COMPLETED 0:0.
nextflow_status=$?

conda deactivate
exit $nextflow_status

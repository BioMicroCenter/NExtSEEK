#!/bin/bash
#SBATCH -N 1
#SBATCH -n 16
#SBATCH -p bcc
#SBATCH --job-name=nfcore_scrnaseq_star
#SBATCH --output=RUN_DIR/nfcore_scrnaseq_star.out
#SBATCH --mail-user=cdemu@mit.edu
# Phase-1 validation run: STARsolo on the 6 whitelist-less seqwell samples via the vendored
# patched clone. Stage this into a fresh run dir with RUN_DIR replaced by that dir's absolute path.
module add miniconda3/v4
source /home/software/conda/miniconda3/etc/profile.d/conda.sh
conda activate cdemu_nfcore          # nextflow 24.10.6
module add singularity/3.10.4
export NXF_SYNTAX_PARSER=v1           # inert on 24.x
export SSL_CERT_FILE="/net/bmc-pub10/data1/bmc/pipeline_cd/certs/ca-bundle.crt"
cd RUN_DIR
nextflow run /net/bmc-pub10/data1/bmc/pipeline_cd/pipelines/scrnaseq-2.7.1-star-patched \
    -profile singularity \
    -params-file params.yml \
    --input samplesheet.csv \
    --outdir . \
    --fasta /net/bmc-pub10/data1/bmc/pipeline_cd/refs/Macaca_fascicularis.Macaca_fascicularis_6.0.dna.toplevel.fa.gz \
    --gtf /net/bmc-pub10/data1/bmc/pipeline_cd/refs/Macaca_fascicularis.Macaca_fascicularis_6.0.116.gtf.gz \
    -w /net/bmc-pub10/data1/bmc/pipeline_cd/work/nfcore_scrnaseq_star \
    -resume

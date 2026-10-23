#!/usr/bin/env bash
# Stage the DNA reference files Nessie's sarek runs use, once, on Luria.
# The owner runs this on Luria; Claude never does (it may only run bash -n).
#   bash stage_dna_refs.sh <refs_root>        # <refs_root> = <LURIA_WORKING_PATH>/refs
# Downloads nf-core's public iGenomes copy of the GATK GRCh38 bundle (the files
# nf-core/sarek 3.10.0's own 'GATK.GRCh38' entry names) into <refs_root>/GATK.GRCh38/,
# skips files already finished, and prints a sha256 manifest to paste back.
# About 11 GB. Names must match run_script.LURIA_GENOMES and LURIA_GENOME_RESOURCES.
set -euo pipefail
[ $# -eq 1 ] || { sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 2; }
root="${1%/}"
dest="$root/GATK.GRCh38"
base="https://ngi-igenomes.s3.amazonaws.com/igenomes/Homo_sapiens/GATK/GRCh38"
# "<path under base> <file under dest>"
files=(
  "Sequence/WholeGenomeFasta/Homo_sapiens_assembly38.fasta Homo_sapiens_assembly38.fasta"
  "Sequence/WholeGenomeFasta/Homo_sapiens_assembly38.fasta.fai Homo_sapiens_assembly38.fasta.fai"
  "Sequence/WholeGenomeFasta/Homo_sapiens_assembly38.dict Homo_sapiens_assembly38.dict"
  "Annotation/GATKBundle/dbsnp_146.hg38.vcf.gz dbsnp_146.hg38.vcf.gz"
  "Annotation/GATKBundle/dbsnp_146.hg38.vcf.gz.tbi dbsnp_146.hg38.vcf.gz.tbi"
  "Annotation/GATKBundle/Mills_and_1000G_gold_standard.indels.hg38.vcf.gz known_indels/Mills_and_1000G_gold_standard.indels.hg38.vcf.gz"
  "Annotation/GATKBundle/Mills_and_1000G_gold_standard.indels.hg38.vcf.gz.tbi known_indels/Mills_and_1000G_gold_standard.indels.hg38.vcf.gz.tbi"
  "Annotation/GATKBundle/beta/Homo_sapiens_assembly38.known_indels.vcf.gz known_indels/Homo_sapiens_assembly38.known_indels.vcf.gz"
  "Annotation/GATKBundle/beta/Homo_sapiens_assembly38.known_indels.vcf.gz.tbi known_indels/Homo_sapiens_assembly38.known_indels.vcf.gz.tbi"
  "Annotation/GATKBundle/af-only-gnomad.hg38.vcf.gz af-only-gnomad.hg38.vcf.gz"
  "Annotation/GATKBundle/af-only-gnomad.hg38.vcf.gz.tbi af-only-gnomad.hg38.vcf.gz.tbi"
  "Annotation/GATKBundle/1000g_pon.hg38.vcf.gz 1000g_pon.hg38.vcf.gz"
  "Annotation/GATKBundle/1000g_pon.hg38.vcf.gz.tbi 1000g_pon.hg38.vcf.gz.tbi"
  "Annotation/intervals/wgs_calling_regions_noseconds.hg38.bed wgs_calling_regions_noseconds.hg38.bed"
)
mkdir -p "$dest/known_indels"
for entry in "${files[@]}"; do
  src="${entry% *}"; name="${entry#* }"
  if [ -f "$dest/$name.done" ]; then echo "[stage] have $name"; continue; fi
  echo "[stage] $name"
  curl -fL --retry 5 --retry-delay 30 -o "$dest/$name.part" "$base/$src"
  mv "$dest/$name.part" "$dest/$name"
  touch "$dest/$name.done"
done
echo "[stage] sha256 manifest (paste this back):"
names=()
for entry in "${files[@]}"; do names+=("${entry#* }"); done
( cd "$dest" && sha256sum "${names[@]}" )

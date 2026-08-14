# Vendored patched nf-core/scrnaseq 2.7.1 — STARsolo for whitelist-less seqwell (Phase 1)

`provision.sh` clones nf-core/scrnaseq@2.7.1 to `{LURIA_WORKING_PATH}/pipelines/scrnaseq-2.7.1-star-patched`
and applies two surgical edits so STARsolo runs on Drop-seq/Seq-Well bead data (no barcode whitelist).
It is idempotent and self-verifying. Alevin is unaffected (it runs the untouched remote `-r 2.7.1`).

## The two edits

**1. `assets/protocols.json` — geometry via the existing `extra_args` mechanism.** 10x entries already
carry `"extra_args": "--soloUMIlen 10"`/`"12"`. Seq-Well is 12bp CB + 8bp UMI, so `star.dropseq` gets:

```json
"dropseq": { "protocol": "CB_UMI_Simple",
             "extra_args": "--soloCBstart 1 --soloCBlen 12 --soloUMIstart 13 --soloUMIlen 8 --soloBarcodeReadLength 0" }
```

`WorkflowScrnaseq.getProtocol()` returns this to the workflow as `other_10x_parameters`, injected into
the STAR command. STAR does NOT default 12/8 for `CB_UMI_Simple` (defaults ~16/10), so without this the
seqwell reads are silently misparsed.

`--soloBarcodeReadLength 0` disables STARsolo's strict check that the barcode read (R1) length equals
CB+UMI (20). These reads are 21bp (one trailing base beyond the 20bp barcode+UMI); STAR still reads CB
from positions 1-12 and UMI from 13-20 and ignores the extra base — matching what salmon alevin does
leniently (the green alevin run used the same 12+8 on these 21bp reads).

**2. `modules/local/star_align.nf` — whitelist-None for bead protocols.** Stock hardcodes
`--soloCBwhitelist <(gzip -cdf $whitelist)`; dropseq stages no whitelist so `$whitelist` is empty and STAR
dies (`CB whitelist file is empty`). The edit makes it conditional:

```groovy
def whitelist_arg = whitelist ? "--soloCBwhitelist <(gzip -cdf ${whitelist})" : "--soloCBwhitelist None"
```

`--soloCBwhitelist None` tells STARsolo to discover barcodes; cell-calling falls to `--soloCellFilter`
(CellRanger2.2 default, or `expected_cells` if set). 10x paths are unchanged (whitelist is truthy).

## Run

```bash
bash provision.sh                       # on Luria: clone + patch + verify
# then stage run_star_validation.sh into a fresh run dir (RUN_DIR replaced) with params.yml + samplesheet.csv
sbatch run.sh
```

`params.yml`: `aligner: star`, `protocol: dropseq`, `save_reference: true`. Genome via `--fasta/--gtf`
(the params.genomes map is unreliable in Nextflow). BAM is coordinate-sorted via the pipeline's
`STAR_ALIGN` `ext.args` default (`--outSAMtype BAM SortedByCoordinate`).

## Phase 2 (deferred)

Wire per-aligner pipeline-source selection + aligner-aware process config into `submit_to_luria` so
`aligner=star` runs hands-free from the chat UI. See the design spec + plan under `docs/superpowers/`.

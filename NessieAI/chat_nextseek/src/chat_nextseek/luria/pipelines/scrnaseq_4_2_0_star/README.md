# Vendored patched nf-core/scrnaseq 4.2.0: STARsolo for whitelist-less Seq-Well / Drop-seq

`provision.sh` clones nf-core/scrnaseq@4.2.0 to `{LURIA_WORKING_PATH}/pipelines/scrnaseq-4.2.0-star-patched`
and applies two surgical edits so STARsolo runs on Drop-seq/Seq-Well bead data (no barcode whitelist).
It is idempotent and self-verifying: it checks the clone sits on the 4.2.0 tag, asserts each line it
edits is exactly what stock 4.2.0 ships (a drifted file stops it), and greps the result.

`run_script.LURIA_VENDORED_PIPELINES` sends a run with `aligner: star` pinned at 4.2.0 here, with no
`-r` (a `-r` on a local clone checks out the tag and wipes the patches). Every other aligner, and a
run pinned to any other release, uses the stock remote `-r <revision>`.

Stock 4.2.0 still cannot run these beads on STAR: `assets/protocols.json` gives `star.dropseq` no
`extra_args`, and `modules/local/star_align/main.nf` always copies a whitelist and passes
`--soloCBwhitelist whitelist.uncompressed.txt`. Simpleaf is no way round it: its quant step always
passes `--unfiltered-pl` with the whitelist, which is empty here, and a `-c` ext.args cannot remove
it (not verified by a run).

## The two edits

**1. `assets/protocols.json`: geometry through the existing `extra_args` mechanism.** The 10x entries
already carry `"extra_args": "--soloUMIlen 10"`/`"12"`. Seq-Well is 12bp CB + 8bp UMI, so `star.dropseq` gets:

```json
"dropseq": { "protocol": "CB_UMI_Simple",
             "extra_args": "--soloCBstart 1 --soloCBlen 12 --soloUMIstart 13 --soloUMIlen 8 --soloBarcodeReadLength 0" }
```

`workflows/scrnaseq.nf` passes `protocol_config.get('extra_args', "")` to `STARSOLO` as
`other_10x_parameters`, which `STAR_ALIGN` puts on the STAR command line. STAR does NOT default 12/8 for
`CB_UMI_Simple` (its defaults are 16/10), so without this the Seq-Well reads are silently misparsed.

`--soloBarcodeReadLength 0` turns off STARsolo's check that the barcode read (R1) is exactly CB+UMI
(20bp) long. These reads are 21bp; STAR still takes the CB from positions 1-12 and the UMI from 13-20
and ignores the extra base.

**2. `modules/local/star_align/main.nf`: whitelist-None for bead protocols.** For dropseq the workflow
stages no whitelist (`ch_barcode_whitelist = []`), so stock dies in the copy step (`cp` with one operand)
before STAR starts. The edit adds, after the forward/reverse split:

```groovy
def whitelist_staged = whitelist ? 'true' : 'false'
def whitelist_arg = whitelist ? "--soloCBwhitelist whitelist.uncompressed.txt" : "--soloCBwhitelist None"
```

wraps the stock gunzip-or-copy block in `if ${whitelist_staged}; then ... fi`, and replaces the STAR
line `--soloCBwhitelist whitelist.uncompressed.txt` with `${whitelist_arg}`. With `None`, STARsolo
discovers barcodes; cell calling falls to `--soloCellFilter` (STAR's default, or `CellRanger2.2` with
the samplesheet's `expected_cells`). 10x runs are unchanged (the whitelist is staged).

If edit 2's checks fail, edit 1 has already been written; re-running after the cause is fixed resets
both files to stock first.

## Provision (operator step, on Luria)

```bash
bash provision.sh                       # clone + patch + verify, into the default path above
bash provision.sh /some/other/path      # or name the destination
```

Nothing provisions it automatically: until it exists on Luria, an `aligner: star` scrnaseq run fails
at `nextflow run` with the clone path missing. Then submit a Seq-Well run from chat with `aligner: star`,
`protocol: dropseq` and no `barcode_whitelist`. 4.2.0 needs Nextflow 25.10.4 or later.

The 2.7.1 clone (`scrnaseq-2.7.1-star-patched`) is no longer used by anything here.

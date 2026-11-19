# scripts/pipeline_scenarios

## What this is

The pipeline-scenario stories (`NessieAI/tests/pipeline_scenarios/`) run against cohorts
of samples. Real public cohorts cover most data kinds. For the kinds with none (human
WGS and exome, tumour and matched normal, a rare-disease trio, ancient DNA, methylation,
ATAC, ChIP, CUT&Tag, small RNA, riboseq, a species with no reference bundle, shotgun
metagenomes, single-cell count matrices, genome assemblies one per genome or combined in one
FASTA, and protein sequences), `cohorts/<name>.json` ([cohorts/README.md](cohorts/README.md)) defines a small test
cohort whose metadata the pipeline agent reads. The names are fixed by
`NessieAI/tests/pipeline_scenarios/test_cohorts.json`.

`make_test_cohorts.py <out-dir> [<cohort> ...]` turns the definitions into upload
workbooks, one folder per cohort and one workbook per level, numbered in upload order
(`1-CEL.xlsx`, `2-DNA.xlsx`, `3-D_SEQ.xlsx`; `tumour_normal` and `rare_disease_trio` add a
patient level first, and the cohorts whose pipeline reads files other than reads end on that
file's level, as `4-A_ASM.xlsx`).
Run it inside the `nextseek` container:

```bash
docker exec nextseek rm -rf /tmp/ps /tmp/ps-out
docker cp scripts/pipeline_scenarios nextseek:/tmp/ps
docker exec -w /app -e PYTHONPATH=/app nextseek uv run --no-sync python /tmp/ps/make_test_cohorts.py /tmp/ps-out
docker cp nextseek:/tmp/ps-out ./ps-out   # an untracked folder; remove it after the upload
```

## Rules

- **Local only.** Upload each cohort's workbooks in number order on the local site's upload
  page, into the local project "Nessie test cohorts". A child names its parent by `Name`,
  which the upload resolves against the earlier workbook. Never upload these to dev or prod.
- **Levels** follow the real cohorts on the stack: a source (`CEL` carries `Species`;
  `PAT` for the patient link; `MUS`, `BAC` or `CHM` where the sample is a mouse, a bacterium
  or a protein), a library (`DNA`, whose `Type` is the library, as in real cohorts) and
  `D.SEQ`. A cohort for a pipeline that reads another kind of file ends on that file's sample
  type, its parent as the sample type allows: `A.SCXP` (count matrices) and `A.ASM`
  (assemblies) below `D.SEQ`, `D.PSEQ` (protein FASTA) straight below `CHM`. Every attribute
  name is one the sample type has.
- **Tumour or normal** is read from `Type` and `Name` (the sarek `status` entry of
  `seqera/nfcore_param_atlas.json`). The pipeline merges metadata from the root down to the
  leaf and the leaf wins, so no level below the source may set its own `Type`: the
  `tumour_normal` DNA rows leave `Type` out, so the source's "Primary tumour tissue" or
  "Blood (matched normal)" survives, and the D.SEQ `Name` agrees. The patient is the shared
  `PAT` ancestor. The small-RNA D.SEQ rows carry `ExpressionKit` and `ReagentManufacturer`,
  which the smrnaseq `three_prime_adapter` entry derives the adapter from.
- **D.SEQ file names are unique across cohorts** (`NTC-<cohort>-<n>...`), because the upload matches existing sequencing samples by `File_PrimaryData`: a reused name is taken as an existing sample and the upload fails with no valid rows.
- **Metadata is untidied on purpose** (mixed case in `SequencingType` and `Species`; the kit
  named in the library's `Protocol` free text, and for small RNA also in the kit fields). `Scientist` is `Nessie Test`; the links
  point at public nf-core test-datasets folders and are not checked to exist.
- Once uploaded, the last level's UIDs go into `NessieAI/tests/pipeline_scenarios/test_cohorts.json`.
  A cohort not yet uploaded has an empty `uids` list, and the runner skips its stories.

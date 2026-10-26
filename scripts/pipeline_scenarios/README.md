# scripts/pipeline_scenarios

## What this is

The pipeline-scenario stories (`NessieAI/tests/pipeline_scenarios/`) run against cohorts
of samples. Real public cohorts cover most data kinds. For the kinds with none (human
WGS and exome, tumour and matched normal, methylation, ATAC, ChIP, small RNA, riboseq,
and a species with no reference bundle), `cohorts/<name>.json` ([cohorts/README.md](cohorts/README.md)) defines a small test
cohort whose metadata the pipeline agent reads. The nine names are fixed by
`NessieAI/tests/pipeline_scenarios/test_cohorts.json`.

`make_test_cohorts.py <out-dir> [<cohort> ...]` turns the definitions into upload
workbooks, one folder per cohort and one workbook per level, numbered in upload order
(`1-CEL.xlsx`, `2-DNA.xlsx`, `3-D_SEQ.xlsx`; `tumour_normal` adds a patient level first).
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
  `PAT` for the patient link), a library (`DNA`, whose `Type` is the library, as in real
  cohorts) and `D.SEQ`. Every attribute name is one the sample type has.
- **Tumour or normal** is read from `Type` and `Name` (the sarek `status` entry of
  `seqera/nfcore_param_atlas.json`). The pipeline merges metadata from the root down to the
  leaf and the leaf wins, so no level below the source may set its own `Type`: the
  `tumour_normal` DNA rows leave `Type` out, so the source's "Primary tumour tissue" or
  "Blood (matched normal)" survives, and the D.SEQ `Name` agrees. The patient is the shared
  `PAT` ancestor. The small-RNA D.SEQ rows carry `ExpressionKit` and `ReagentManufacturer`,
  which the smrnaseq `three_prime_adapter` entry derives the adapter from.
- **Metadata is untidied on purpose** (mixed case in `SequencingType` and `Species`; the kit
  named in the library's `Protocol` free text, and for small RNA also in the kit fields). `Scientist` is `Nessie Test`; the links
  point at public nf-core test-datasets folders and are not checked to exist.
- Once uploaded, the D.SEQ UIDs go into `NessieAI/tests/pipeline_scenarios/test_cohorts.json`.

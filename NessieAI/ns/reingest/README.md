# NessieAI/ns/reingest/

Turns a finished nf-core pipeline run into NExtSEEK rows. The run directory is read
deterministically into a `RunManifest`; a committed per-pipeline map says which raw QC key
becomes which sample attribute; anything the map does not cover becomes a reviewable
proposal rather than a guess.

The split matters: this package parses and transcribes, the map assigns meaning, and the
agent never sees a measurement it could paraphrase. `build-upload-xlsx` takes a manifest id,
not values, so no measured number round-trips through a model.

## Surface

| Module | What it does |
|---|---|
| `manifest.py` | `RunManifest`, the single structured artifact a harvest produces, plus `SampleRecord`, `PipelineInfo`, `OutputRecord` and the `RESOLUTION_*` constants. `outputs` is the file inventory; `named_outputs` is the dict a map's `$outputs.<key>` resolves against |
| `parsers.py` | format-level parsers for `pipeline_info/` and MultiQC: params, software versions (flat and per process, with conflict detection), samplesheet, execution trace, general stats |
| `derived.py` | `DERIVED_METRICS` and `compute`: the only place reingest does arithmetic on a measurement. RSeQC's `TSS_up_*`/`TES_down_*` windows are nested, so the read-distribution percentages sum the six non-nested groups |
| `harvest.py` | `harvest_local` walks an allowlisted file set into a `RunManifest`. `GENERIC_GLOBS` is staged for content; `INVENTORY_GLOBS` is a listing only. `_NAMED_OUTPUT_MATCHERS` defines every key `$outputs.*` may name |
| `launch_record.py` | `record_launch` and `read_cohort_sidecar`: persist what a launch consumed, so reingest never has to guess it back |
| `uid_resolve.py` | each nf-core sample back to its `D.SEQ`: launch record first, then fastq exact, then basename. Multi-run is rejected on purpose, and ambiguity is never guessed through |
| `maps.py` | the map schema and loader. `$`-refs are lookups into one named manifest section and nothing else — no evaluation, no attribute traversal, no reachable Python object. That is what makes a map reviewable in a diff rather than a security surface |
| `mapper.py` | `apply` joins a map to a manifest: `MapResult(rows, unmapped)`. Committed map rules beat approved database rules; `per_sample` output rules fan out one row per sample; `unmapped` is explicit, never silent |
| `proposals.py` | the approval queue's service: `record` (idempotent per `(pipeline, raw_key)`), `approved_rules`, `attribute_exists` |
| `answers.py` | the fix loop's curator answers: `parse_answers`, `validate` (an allowlist from the first QA pass's findings), `apply_answers`. Pure, no I/O |
| `build_records.py` | one JSON record per rendered workbook, keyed by the workbook's sha256 and filed under the building user. `write`, `load`, `sha256_of` |
| `upload.py` | `run`, `verify`: the batch-upload start for reviewed workbooks. `CONFIRMATION_SCOPE` is a module constant |
| `store.py` | server-side manifest cache, keyed by `run_dir` plus content digest |

Map files live beside this package in [`../reingest_maps/`](../reingest_maps/README.md), one
`<pipeline>.outputs.json` per nf-core pipeline, committed and read-only at runtime.

## The fix loop and the upload

`build-upload-xlsx` maps and QAs a run, and returns the findings. The curator answers in chat
and the op is called again with `answers`, validated against the first pass's findings. Three
kinds, each licensed only by what that run flagged:

- `fill`: set a flagged, empty, non-run-sourced cell to a value the curator gave. Measured
  cells are never writable.
- `choose`: pick one candidate for an ambiguous data file.
- `place`: put an uncovered raw metric into an existing attribute, for this run only. Never
  onto a run-sourced attribute, a data file or its checksum, or a sample that already holds a
  value for it in NExtSEEK; if those values cannot be read, the place is refused.

One bad or conflicting answer (two on one cell) refuses the whole call and nothing is applied.
New-mode and update-mode workbooks are built by separate calls; an answer for a sample type that
belongs to the other call comes back in `answers_deferred` rather than being refused, and the
curator repeats it on the other call. A type in neither is a typo and is refused.

Each workbook leaves a build record (`builds` in the result): its sha256 is the build id, with
disposition, open warnings, the manifest id, a digest of the answers applied, and the parents'
single SEEK project (or a note on why there is not one). Records are per user.

`upload-reingest` takes `build_ids` and one `confirmed_write`. `upload.verify` loads each record
for the caller, re-hashes the workbook, and refuses a changed, foreign, non-passing or
project-less build; `upload.run` then stages every workbook and re-hashes each staged copy
before any job starts. After that each workbook is its own batch-upload job through
`dispatch_batch_job` (`nextseek_api/batch_upload/README.md`), new mode first, and a job that
fails to start never stops the next. Files only, never `rows`.

Job progress is read on the batch-upload `status/<job_id>/` endpoint by the job's owner.
`nextseek-api-read` cannot poll it: the read-safe allowlist matches exact strings, so a
per-job path cannot be listed until the allowlist supports patterns.

## Invariants

- **Reingest never invents a sample attribute.** Every target must already exist on its sample
  type. `NessieAI/tests/ns/reingest/test_map_contract.py` gates the committed maps in CI;
  the superuser approve endpoint gates database rules, because the CI test cannot see them.
  A proposed attribute that does not exist is a schema request for the Attribute API, not a
  mapping problem.
- **A map is data, not code.** See `maps.resolve_ref`.
- **A human's ruling is never overwritten by a later automated sighting.** Repetition is
  evidence, not a veto: a repeat bumps `times_proposed` and never reopens a terminal row.
- **An unresolved sample's child ships with no `Parent` key at all** — never an empty string.
  The QA gate distinguishes absent (soft, ships) from blank (hard reject) and can only do so
  because this package never emits the latter.

## Running and testing

`NessieAI/tests/ns/reingest/` (engine) and `NessieAI/tests/api/` (HTTP surface); commands are
in `NessieAI/tests/README.md`. The contract test's live variants skip without a populated
catalog, so a green run in the sqlite lane checks the committed snapshot only.

## Depends on / depended on by

- Reads `nextseek_api.assistant.models_db` (`PipelineRun`) from `launch_record.py` and
  `uid_resolve.py`, and `models_db` plus `nextseek_api.services.reingest_lookups` from
  `proposals.py` — allowed back-edges, listed in `NessieAI/tests/api/test_nessie_boundaries.py`.
- Calls `nextseek_api.batch_upload.views` (`dispatch_batch_job`, `stage_workbook_copy`) from `upload.py`.
- Called by `NessieAI/ns/granular.py` (`run-harvest`, `run-checksum`, `build-upload-xlsx`, `upload-reingest`) and
  by `nextseek_api/services/reingest_proposals.py` (the superuser review endpoint).

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
| `store.py` | server-side manifest cache, keyed by `run_dir` plus content digest |

Map files live beside this package in [`../reingest_maps/`](../reingest_maps/README.md), one
`<pipeline>.outputs.json` per nf-core pipeline, committed and read-only at runtime.

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
- Called by `NessieAI/ns/granular.py` (`run-harvest`, `run-checksum`, `build-upload-xlsx`) and
  by `nextseek_api/services/reingest_proposals.py` (the superuser review endpoint).

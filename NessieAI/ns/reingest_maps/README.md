# Reingest output maps

One `<pipeline>.outputs.json` per nf-core pipeline. Committed, code-reviewed,
read-only at runtime. `NessieAI/ns/reingest/maps.py` defines the schema and
`NessieAI/tests/ns/reingest/test_map_contract.py` gates every file in CI.

## What a map may contain

- `harvest_globs` — extra files this pipeline needs, on top of the generic
  `pipeline_info/` and MultiQC allowlist in `reingest/harvest.py`.
- `outputs` — glob to SampleType, with `cardinality` `per_sample` (1 to 1) or
  `per_run` (many to 1, `Parent` semicolon-joined).
- `provenance_attributes` — a single flat dict shared by the whole map, merged
  into a row only for the output rule(s) that opt in with
  `include_provenance: true` (default `false`). A rule that does not opt in
  gets only its own `attributes`. A map with a non-empty
  `provenance_attributes` and no output rule opted in is a bug, not a
  no-op — the contract test fails it deliberately, because that block would
  otherwise be merged into nothing.
- `qc_attributes` — raw key to a sample attribute on an EXISTING sample type.
- `deliberately_unmapped` — a ruling, with a real reason. Without it the agent
  re-proposes the same key on every run and the review queue fills with noise.

`$`-prefixed values are lookups into a manifest section (`params`, `pipeline`,
`software_versions`, `outputs`, `metrics`, `derived`). There is no evaluation.
A map cannot compute; `derived_metrics` in `reingest/derived.py` is the only
place reingest does arithmetic on a measurement.

A map's `from`/ref value can be wrong in a way no test catches: the contract
test can confirm that a rule's *target* attribute exists on its sample type,
but it has no way to confirm that a raw MultiQC key or `$`-ref actually
appears in a real run's output — a wrong key just never appears on any row,
silently, forever. Verify a new `qc_attributes` entry against a real run
before committing it, or leave the key to the approval queue instead of
guessing (see "Filling in a stub" below).

## Filling in a stub

The eight non-rnaseq/scrnaseq maps carry no output rules and no
`provenance_attributes` yet — a stub has no provenance block because it has
no output rule to carry it; provenance arrives together with the first output
rule that opts in, not before. Rather than guessing a stub's QC attributes
against a pipeline nobody has run here, run one and let the mapper name what
it could not map. Those land in the approval queue at
`GET /nextseek_api/reingest-proposals/?pipeline=nf-core/<name>`, ordered by how
often they have been seen. Approve the ones that are right, then fold the
approved rows into this file in an ordinary PR and delete them from the queue.

A proposed attribute that does not exist on the sample type is NOT a mapping
problem. It is a schema request: route it through the native Attribute API, or
`dmac-curation:curate-sampletype`. Reingest never creates an attribute.

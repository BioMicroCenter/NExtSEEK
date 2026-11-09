# `NessieAI/ns/reingest/pipelines/`

The registry of `@` tokens: the computed values a map may write into a cell (the session
user, the run date, a row name, a contrast field). A `$` value is a lookup into the
manifest; an `@` value is resolved by a function here.

| File | Holds |
|---|---|
| `__init__.py` | `COMMON_TOKENS` and `PIPELINE_TOKENS`, the declared names; `token_names_for` and `tokens_for` |
| `common.py` | the resolvers of the common tokens, in `COMPUTED` |
| `methylseq.py` | nf-core/methylseq's own tokens (conversion method and methylation protocol, from its boolean params: `taps`, `em_seq`, `rrbs`, `pbat`, `single_cell` in `special_library_types`, `nomeseq` in `bismark_options`; blank when no params were read), in `COMPUTED` |
| `differentialabundance.py` | nf-core/differentialabundance's own tokens (contrast, paramset, method settings, report link, model object), in `COMPUTED` |

## Rules

- **Common tokens are for every pipeline.** A token that any map could use goes in
  `COMMON_TOKENS` and `common.COMPUTED`.
- **One module per pipeline that needs its own, and most need none.** The module is named
  for the pipeline's slug (`nf-core/differentialabundance` is `differentialabundance.py`)
  and exports `COMPUTED` only. Code shared by several pipelines (the contrasts reader, for
  one) stays in `harvest.py`, not here.
- **Names are declared in `__init__.py`.** `maps.PipelineMap` validates a map's `@` values
  against `token_names_for` without importing any resolver; `tokens_for` imports the
  pipeline's module only when the mapper resolves a value. A map may use the common tokens
  and its own pipeline's, nothing else: an unknown or foreign token fails map load.
- **A resolver takes `(run_manifest, sample, context)` and returns a string or `None`,
  never a placeholder.** `None` leaves the cell for a curator; a fabricated value would
  look measured.
- `NessieAI/tests/ns/reingest/test_pipelines.py` checks that every declared name has a
  resolver and every resolver a declared name.

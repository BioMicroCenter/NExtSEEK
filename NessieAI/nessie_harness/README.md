# `NessieAI/nessie_harness/`

## What this is

The harness code the product reads: the corpus loader (`corpus.py`, `catalog.py`), the paired-run
evidence export (`export.py`, `collect.py`, `sources.py`), and the manifest models
(`manifest.py`, `bayes_manifest.py`, `outage.py`, `turn_cost.py`). It sits outside `NessieAI/tests/`
so the engine and the generators never import the test tree. The files of the same names under
`NessieAI/tests/nessie_tests/` are aliases of these modules. `corpus.json` stays in
`NessieAI/tests/nessie_tests/` and is resolved through `NessieAI/paths.py`.

The harness itself, its runner and its tests are described in `NessieAI/tests/nessie_tests/README.md`.

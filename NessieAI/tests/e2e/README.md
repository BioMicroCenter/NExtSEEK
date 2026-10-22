# `NessieAI/tests/e2e/`

## What this is

The catalog-driven end-to-end test DSL for the chat engines. `catalog.json` lists question families
and variants, each with turns and pass criteria; a runner samples a share of them, drives the real
chat endpoint, and writes a manifest and an HTML report. It is paid (live LLM calls) and needs a
seeded instance. The pytest tests for this tool are `NessieAI/tests/chat_nextseek/test_e2e_*.py`.

## Surface

| File | What it does |
|---|---|
| `__main__.py` | the entry point and its flags |
| `catalog.json`, `catalog.py` | the variants, and the `Variant`, `Turn` and `PassCriterion` models with the loader |
| `sampler.py` | picks the variants for a run (`--ratio`, `--seed`, `--family`, `--variant`) |
| `runner.py` | runs the turns and records the results |
| `criteria.py` | evaluates pass criteria (`eq`, `contains`, `mentions`, `matches_re`, `trio_match` and the rest) |
| `manifest.py`, `report.py` | the run manifest, `--rerun` support and the HTML report |
| `import_env.py` | loads the instance and credentials |
| `playwright/` | the browser spot tests behind `--playwright` |

## Running

From the repo root; the full flag list is in the `__main__.py` docstring.

```bash
python -m NessieAI.tests.e2e --list
python -m NessieAI.tests.e2e --ratio 0.33 --seed 42
python -m NessieAI.tests.e2e --rerun outputs/e2e_<ts>/manifest.json --failed-only
```

It costs money: get the owner's approval for each run. The lane row is in `NessieAI/tests/README.md`
("Catalog E2E"). The router-aware harness in `NessieAI/tests/nessie_tests/README.md` is the other
paid runner.

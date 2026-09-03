# nessie_tests

## What this is

The router-aware end-to-end harness for the NExtSEEK assistant. It asks the
product questions the way a browser does — one POST to the async query endpoint,
then a poll loop against that task's progress endpoint
(`nessie_tests/http_driver.py:7`, `nessie_tests/http_driver.py:38-54`) — instead
of importing either engine and calling it directly. That choice is the whole
design: the top-level router is what decides which engine answers, and a
harness that imported an engine would never exercise the decision.

Two things about the directory are easy to misread from outside it.

**It is not only a test tree.** Six of its modules are imported as a library by
code that ships: a Container-CC op, a Django management command, a family-label
reader and the generator behind `route_capabilities.json`. Deleting or renaming
one is an application change, not a test change. The importers are listed under
"Depends on / depended on by" below.

**What a run observes is narrower than what a run causes.** At `--tier route`
the client stops polling the moment it sees the routing event
(`nessie_tests/http_driver.py:130-132`), and stopping the poll is all it does:
searching every non-test module of this package for a DELETE method, a cancel or
abort call, or a revoke returns nothing, so the server is never told to stop. It
has already returned 202 and is running the turn on its own thread, and its
single early return is the `unrelated` route
(`nextseek_api/services/cc_assistant.py:537-544`), so every gate routed anywhere
else runs to completion and bills for it after the harness has walked away. That
is why a route-tier run reports its spend as unmeasured instead of zero
(`nessie_tests/manifest.py:153-158`). No route is free: the router's own model
call happens on every turn and the routing event is emitted before the
`unrelated` check, not after (`nextseek_api/services/cc_assistant.py:529-537`).

The question set is forked from `chat_nextseek`, not shared with it. The
harness reads its own `corpus.json`; the upstream catalog it was adopted from
still serves its own readers, and a drift test fails if that file moves
(`nessie_tests/corpus.py:11`, `nessie_tests/tests/test_catalog_drift.py:1`).

## Surface

The surface has three different shapes, so it is described three ways.

**As an importable package** — the entry points other code actually calls:

| Module | What it is for |
|---|---|
| `nessie_tests/cli.py:83-123` | flag parsing; `nessie_tests/cli.py:10-26` documents nine exit codes |
| `nessie_tests/runner.py:361-364` | `run_suite`, one whole run; `nessie_tests/runner.py:119-120` is one case |
| `nessie_tests/corpus.py:415` | `merged`, the resolved active corpus |
| `nessie_tests/evaluate.py:665` | `evaluate_turn`, criterion scoring for one turn |
| `nessie_tests/manifest.py:153` | `cost_summary`, what a run may claim about money |
| `nessie_tests/bayesian.py:76` | `run_paired`, the paid dual-route run |
| `nessie_tests/preflight.py:66-70` | refuses a paid run whose force did not land |
| `nessie_tests/export.py:961` | paired manifest to the locked HiBayes CSVs |
| `nessie_tests/collect.py:377` | post-hoc artifact collection for a paired run |
| `nessie_tests/sources.py:407` | the container-backed reads `collect` needs |
| `nessie_tests/v4_2_verifier.py:330` | replay verifier over a delivered result set |
| `nessie_tests/bundle.py:30-36` | full-tier bundle richness; its Django import is lazy |

Invoked as `python -m nessie_tests` through `nessie_tests/__main__.py:2`, or
in-container as a Django management command
(`nextseek_api/management/commands/nessie.py:27-28`).

**As committed data.** `nessie_tests/corpus.json` is the only corpus source
there is, and `nessie_tests/corpus.py:415-425` records that the superseded
overlay files and their generator were deleted outright. `FAMILIES.json` declares the
28 code-derived task families the corpus is mapped onto
(`nessie_tests/FAMILIES.json:4-5`, `nessie_tests/scripts/remap_families.py:2`). `nessie_tests/probes/` holds three
hand-authored case files replayed by `nessie_tests/tests/test_probe_files.py:1`.
Measured 2026-09-03 against `nessie_tests/corpus.json`: `merged` resolves 424
variants over 472 turns; `curated`, which drops the unreviewed atlas set
(`nessie_tests/corpus.py:110-130`), leaves 365; `bayesian_ids` selects 149; one
consistency group is defined; and 3 variants carry the `route_gate` tag.

**As two packaged skills.** Each carries its own SKILL.md and is not restated
here. See `nessie_tests/output-skill/SKILL.md:2-3` for turning a finished run
into a triage report, and `nessie_tests/output-skill-bayesian/SKILL.md:2-3` for
the paired run's blind-grading report. A hyphen is not a Python identifier, so
the testable logic for the second one lives in the underscore-named package
beside it and the skill's script is a thin entry point
(`nessie_tests/output_skill_bayesian/__init__.py:1-11`).

**As one-off tooling.** `nessie_tests/scripts/` is a group of corpus-maintenance
programs, not a public API: they author and apply question sets
(`nessie_tests/scripts/build_qset.py:1`, `nessie_tests/scripts/apply_qset.py:1`,
`nessie_tests/scripts/qset_data.py:1`), generate variants from the capability
atlas (`nessie_tests/scripts/atlas_variants.py:2`), remap families
(`nessie_tests/scripts/remap_families.py:2`), narrow a paired selection to
what is not yet graded (`nessie_tests/scripts/delta_selection.py:1`), and render
the reviewable question-set document (`nessie_tests/scripts/build_doc.py:1`).
They read and write `nessie_tests/corpus.json` in place, so a narrowing is
run-time state to be reverted, never committed.

## Running and testing

Two lanes exist and they test different things. Both were run on 2026-09-03 and
the numbers below are that run's, not a summary of intent.

**Host lane, no container.** From the repository root:

```
uv run --no-project --with pytest --with pydantic --with requests \
  --with beautifulsoup4 --with orjson \
  python -m pytest nessie_tests/tests -q -p no:cacheprovider
```

Result on 2026-09-03: 5 failed, 1224 passed, 28 skipped in 34.05s. All five
failures are one cause, and it is environmental rather than a regression — see
CLAUDE.md for the hardcoded delivery path behind them and for why the explicit
dependency list above cannot be shortened.

**Container lane, needs Django and a database.** `nessie_tests/tests_container/`
imports Django settings and the real DRF view, so it cannot run on the host
lane. Against a running stack:

```
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nessie_tests/tests_container/ --no-migrations -q'
```

Result on 2026-09-03: 9 passed in 9.40s. This exercised the copy baked into the
image at `/app`, which is not necessarily the working tree you are editing.

**Live runs cost money and are not part of either lane.** The cheap gate and the
full pass differ only in how far the client follows the turn:

```
python -m nessie_tests --base-url http://localhost:8000 --tier route --scope specific
python -m nessie_tests --base-url http://localhost:8000 --tier full --scope all
```

The paid dual-route run is `--bayesian`, which takes its selection from the
corpus flag alone and refuses to be combined with any other selection flag
(`nessie_tests/cli.py:143-147`). It needs a superuser account, budgeting via
`--max-usd`, and it is preceded by a preflight that spends one probe turn to
prove the route force is honoured (`nessie_tests/preflight.py:70-75`).

## Depends on / depended on by

Both directions were derived on 2026-09-03 by grepping the whole worktree for
the package name, then classifying each hit; the counts below come from that
sweep rather than from recall.

**Depended on by — application code, which makes this directory load-bearing:**
- The Container-CC paired-evidence op imports four modules of it (`nextseek_api/cc_assistant/op_registry/paired_evidence.py:36-39`), so removing one breaks an op the assistant can call.
- `manage.py nessie` imports the driver and runner at call time (`nextseek_api/management/commands/nessie.py:55`), which is how a run happens inside the trusted Django process.
- Task-family labels are read straight out of the corpus file by path (`nextseek_api/cc_assistant/family_labels.py:21`), so moving `corpus.json` empties that catalog.
- The `route_capabilities` generator imports three modules and resolves the corpus by relative path (`build_tools/gen_op_surfaces/route_capabilities.py:27-29`, `build_tools/gen_op_surfaces/route_capabilities.py:39`).
- Human-grade fitting imports the paired manifest model (`nextseek_api/eval/human_grade_fit.py:704`).
- A standalone verifier script imports the replay verifier (`scripts/plan018_v4_2_verifier.py:16`).

**Depended on by — governance tooling that keys on paths, not imports:**
- The owned-surface classifier routes files by their path prefix inside this directory (`scripts/plan018_v4_9_owned_surface.py:265-274`), so renaming a file here changes how it is governed.
- Two mutation-testing contracts name `nessie_tests/v4_2_verifier.py` as the file under mutation (`scripts/plan018_v4_9_task5_mutation.py:105-106`).

**Depends on:**
- The vendored `chat_nextseek` e2e criterion DSL, reached by a `sys.path` insertion performed at import time (`nessie_tests/pathsetup.py:10-16`), which two modules trigger at module scope (`nessie_tests/corpus.py:6-9`, `nessie_tests/evaluate.py:10`).
- `pydantic`, at module scope in the two manifest models and the verifier (`nessie_tests/manifest.py:4`, `nessie_tests/bayes_manifest.py:13`, `nessie_tests/v4_2_verifier.py:15`).
- `orjson`, at module scope in the verifier only (`nessie_tests/v4_2_verifier.py:14`).
- Three heavy dependencies are deliberately lazy, imported inside the function that needs them: Django models (`nessie_tests/bundle.py:30`), `openpyxl` (`nessie_tests/evaluate.py:151`) and `zstandard` (`nessie_tests/collect.py:177`).
- The live HTTP endpoint and its progress route, by string rather than by import (`nessie_tests/http_driver.py:7`, `nessie_tests/http_driver.py:41`, `nessie_tests/http_driver.py:49`).
- The `nextseek` container and the `docker` binary, for the paired run's post-hoc reads only (`nessie_tests/sources.py:104-105`).

**What is deliberately not in those lists.** Two kinds of hit were excluded.
One: matches inside `evidence/` and `docs/`, which are dated records naming
these paths rather than code that runs them. Two: three comments that mention
the package without depending on it (`chat_nextseek/src/chat_nextseek/orchestrator.py:107-108`,
`nextseek_api/assistant/models_db.py:327`,
`nextseek_api/cc_assistant/tests/test_cc_session_metas_columns.py:207`). One hit
looks like an outbound Django dependency and is not: the line importing
`nextseek_api.assistant.models_db` at `nessie_tests/sources.py:254` sits inside
the `_CONTAINER_PY` string opened at `nessie_tests/sources.py:245`, which is
probe source injected into a container, not an import this file performs. Test
modules that import the package from elsewhere in the tree are omitted from the
inbound list on the grounds that they are tests of it rather than consumers of
it; they are in `nextseek_api/assistant/tests/test_route_capabilities.py:26-28`,
`nextseek_api/cc_assistant/tests/test_paired_evidence.py:14-15` and
`nextseek_api/eval/tests/test_task2_coverage_edges.py:11`.

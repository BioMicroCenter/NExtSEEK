# Two CI coverage gaps: the migration check and context-file health (design)

Written 2026-09-16. Covers the two issues from the 2026-09-16 triage that are **not** load-bearing for the graph 1.2
behavioural work, and so are deliberately kept out of
[`2026-09-16-graph-behaviour-tests-design.md`](2026-09-16-graph-behaviour-tests-design.md).

Companion plan: [`docs/superpowers/plans/2026-09-16-ci-coverage-gaps.md`](../plans/2026-09-16-ci-coverage-gaps.md).

## 1. Why these two are together, and why they are separate from the graph work

They share one shape: **a check the repository already decided it wanted, and never built.** Neither is a bug in
running code. Neither blocks the graph 1.2 work, and neither is blocked by it.

They are separate from each other in every other respect, so section 2 and section 3 are independent and may be
executed in either order. Part B additionally carries a dependency the operator controls (section 3.4).

## 2. Part A: the migration check does not run

### 2.1 What is missing

`manage.py makemigrations --check --dry-run` does not run clean on the base tree, so the blocking CI step in
`.github/workflows/ci-pytest.yml` carries no migration check at all. The graph-sync plan's task T8 recorded this as
a deliberate departure from its spec's CI-2 requirement, and task T1's check covers only the two `graph_sync`
models.

The cost: a model changed without its migration is invisible to CI. That is the exact class of error a migration
check exists to catch, and this repository has 21 migrations and a history of schema drift.

### 2.2 What it reports today

Measured in the Django lane at tree `410180e0`:

- Without `--skip-checks`, it exits 1 on Django system check `4_0.E001`, because `dmac/settings.py` builds
  `CSRF_TRUSTED_ORIGINS` from an empty environment value and gets a list holding one empty string.
- With `--skip-checks`, it still exits 1, reporting pending changes for Mezzanine's `blog`, `core`, `generic` and
  `pages`, a would-be `seek` 0003, and a `nextseek_api` 0022 that only renames a TurnLedger index.

### 2.3 Root cause, and what is not established

Established for the TurnLedger entry only: `nextseek_api/assistant/models_db.py` declares the index without an
explicit `name=`, while `nextseek_api/migrations/0010_turn_ledger.py` hard-codes the generated name, so Django
proposes a rename on every run.

**Not established** for the Mezzanine and `seek` entries. They were never investigated. This spec does not guess at
them, and the plan treats them as a separate question with its own decision point rather than assuming they are
harmless.

### 2.4 Decisions

| Id | Decision |
|---|---|
| **A1** | Fix the TurnLedger index by naming it. No new migration, no DDL. |
| **A2** | Investigate the Mezzanine and `seek` entries before deciding anything about them. A check that is switched on while four apps report pending changes is a check that gets ignored. |
| **A3** | The restored check runs under `--skip-checks`, because the `CSRF_TRUSTED_ORIGINS` system check is a settings artefact of the test environment and not what this check is for. Fixing that setting is a separate concern and is not required here. |
| **A4** | The check blocks only once it is clean. A red check bolted onto the blocking step on day one would be reverted by week two. |

## 3. Part B: nothing checks that the context Nessie reads is right

### 3.1 The chain, and where it is unguarded

```
dmac context tables (MySQL)  ->  context/*.json  ->  the assistant's catalogs
                             \
                              ->  graph SampleType / Attribute nodes  ->  graph_search
```

Both branches are unguarded, in different ways.

**The file branch.** `context/sample_types.json`, `assays.json`, `assay_mappings.json` and `projects.json` are what
the assistant loads (`NessieAI/chat_nextseek/src/chat_nextseek/config.py`). A validator for them exists and is
**not in the repository**: `git ls-files` matches nothing for it. It lives in an untracked working directory, so it
has never run in CI and cannot.

**The graph branch.** The graph-sync design specifies catalog drift checks under requirement CI-4, named
`drift.catalog.sample_types`, `drift.catalog.hash`, `drift.catalog.types_with_attribute_set_diff`, and three
`drift.isa.*` checks. **None exists.** `nextseek_api/graph_sync/drift.py` uses the catalog to compute sample digests
and never checks the catalog itself. Graph-sync Run 2 recorded this as an open item and no later run took it.

### 3.2 What does exist, and why it is not enough

Gate G's three `3.catalog.*` checks in `nextseek_api/graph_sync/verify.py` verify that the graph's catalog is
consistent **with the graph's own sample nodes**: no sampled sample carries a property its type does not list, no
type carries an unlisted key, no `T_` label lacks a `SampleType` node.

That is internal consistency. It cannot tell you the catalog disagrees with MySQL, and it cannot tell you a curated
row is stale, wrong, or gone.

Two further holes:

- `ci/writers.py` declares the context table writer as operator-run and external with **no hook site**. Its own note
  says the context tables are written by hand today. So a context edit tells nothing to anything; only the nightly
  reconcile or the weekly full sync carries it, and neither can say the content is wrong.
- `nextseek_api/graph_sync/catalog.py` documents that a type with no context row is a normal state, with
  `has_context` false and no context property written. Nothing counts how many types are in that state, so a type
  that silently lost its curated card is indistinguishable from one that never had one.

And the Nessie CI lane asserts routing, completion, bundle registration and debug wiring. It never asserts an answer
is correct, so a confidently wrong answer passes.

### 3.3 Decisions

| Id | Decision |
|---|---|
| **B1** | Two independent checks, not one. Authoring-time validation of the files, and runtime drift of the graph's copy. They catch different failures and neither substitutes for the other. |
| **B2** | The file validator moves into the repository and runs in `ci/gate`, which is blocking. A validator that lives in someone's working directory is not a check. |
| **B3** | The three `drift.catalog.*` checks the design already names are built as specified, and report in the CI record's `## Graph drift` section beside the sample drift that is already there. |
| **B4** | A count of types with no context row is **reported, not enforced**. A missing row is legal (catalog.py says so). The number being visible is the point; a threshold would be invented. |
| **B5** | The `drift.isa.*` checks are **out of scope here**. They cover Project, Investigation and Study nodes, which is a different surface from the catalog, and nothing in the 2026-09-16 triage pointed at them. Either amend the graph-sync spec to drop them or give them their own task, but do not smuggle them in. |

### 3.4 The dependency, and how narrow it actually is

`context/` is tracked on `fix/context-quick-fixes` and **not on `dev-graph`**, and the rewritten context is still
being finalised in another session.

**That gates the file validator only.** It has nothing to validate until those files are in the repository, and it
should be written against the finalised shape rather than the current one.

**It does not gate the drift checks.** Verified 2026-09-16: `run.build_catalog()` reads SEEK and the **dmac context
tables in MySQL**, and no module under `nextseek_api/graph_sync/` reads `context/*.json` at all. The drift checks
compare the graph's catalog against what MySQL holds, so they are independent of both the JSON files and the
rewrite.

**They are better built first.** Applying the rewritten context writes new rows into those dmac tables, and the
graph's copy goes stale the moment that happens until a sync carries it. The drift checks are exactly the alarm for
that, so having them in place before the write makes them the safety net for it rather than a retrospective audit.

So the execution order inside Part B is: the drift checks and their reporting first, and the file validator last,
once the context branch has merged. Part A has no dependency at all.

## 4. Out of scope

- The smoke discovery fix and the `seekapi` guards. Both are prerequisites for the graph 1.2 behavioural work and
  live in that plan as tasks P1 and P2.
- Fixing `CSRF_TRUSTED_ORIGINS` (decision A3).
- The `drift.isa.*` checks (decision B5).
- Asserting that an assistant answer is correct. That is an evaluation problem, not a CI check, and the retrieval
  and extraction harnesses already address it in another session.

## 5. Unverified at writing

- Whether the Mezzanine and `seek` pending-migration entries are benign. Nobody has looked (decision A2).
- What the finalised `context/` shape will be, and therefore exactly what the validator will assert once it is
  tracked.
- The cost of the three `drift.catalog.*` checks at 1.08M samples. The existing drift check's cost at that size is
  itself unmeasured, recorded as an open item from graph-sync task T12.

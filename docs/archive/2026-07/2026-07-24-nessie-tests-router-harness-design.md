# Nessie Tests: unified router-aware assistant test harness — design

Date: 2026-07-24
Branch: `dev-v3-merge` (parent `BioMicroCenter/NExtSEEK`)
Status: design, approved for spec review
Related issues: [#31](https://github.com/BioMicroCenter/NExtSEEK/issues/31) (unify testing), [#32](https://github.com/BioMicroCenter/NExtSEEK/issues/32) (child→parent traverse + thin bundles), [#33](https://github.com/BioMicroCenter/NExtSEEK/issues/33) (REST-vs-graph routing split)
Inputs: `docs/testing-review/{01-chat_nextseek-e2e-harness-review,02-cc-dmac_assistant-testing-review,03-run-history-outputs-and-db-review}.md`

## 1. Motivation

The assistant ("Nessie") is tested in three silos that never meet:

- **NS path** — `chat_nextseek/e2e` (`catalog.json`, 366 variants / 11 families, run via `uv run e2e.py`). Strong and maintained, but its default CLI tier calls `chat_nextseek.orchestrator.run_query` **in-process, below the top-level router**, so it structurally cannot test the NS-vs-CC routing decision. It asserts the NS-*internal* parser mode on ~358 variants but has no top-level `route` concept, and it even encodes the #33 contradiction (two near-identical intents assert different engines with no consistency guard).
- **CC path** — `nextseek_api/cc_assistant/scripts/{step7_gate3d_per_op.py, full_ui_e2e.py}`, `tests/cc_matrix_gate_harness.py`. Real routing is asserted **only** in the paid, gated `test_cc_realstack.py`. The per-op and matrix gates don't test routing at all. Four disjoint entry points, all paid/slow, each needing a bespoke frozen artifact.
- **UI / routing features** — route override, PROD toggle, max-turn, titling, event trace: unit-tested only, never exercised together through the router.

The reviews converge on one lever: `chat_nextseek/e2e`'s typed `Variant / Turn / PassCriterion` DSL is already reused verbatim by `full_ui_e2e.py` (`full_ui_e2e.py:140-143`), which even adds an optional per-question `route` field. So a unified, router-aware harness is an **additive extension of one existing DSL**, entered **above** the router, not a greenfield build.

## 2. Goals / non-goals

**Goals**
- One runner that drives cases through the **real top-level router** (the async HTTP endpoint), so NS-vs-CC routing and cross-mode handoff are genuinely exercised.
- **Reuse, don't duplicate**: import the `chat_nextseek/e2e` DSL + resolver + existing families/variants read-only; add new cases in a separate overlay.
- Encode the open bugs as **red** regression cases (TDD anchors) and the known-good behaviors as **green** anchors.
- Two tiers from one corpus: a cheap **route gate** (pre-merge) and a full **paid e2e** (nightly / pre-release).

**Non-goals (explicitly out of this spec)**
- The **#32/#33 fixes** — separate follow-up plans, driven red→green by the cases here.
- Any **LLM-judge / grading skill** — the oracle is the deterministic `{field, op, value}` PassCriterion DSL only.
- Modifying the vendored `chat_nextseek` snapshot (imports only; hand-edits there get overwritten on sync).

## 3. Home

A new top-level folder **`nessie_tests/`** at the repo root, sibling to `chat_nextseek/` and `nextseek_api/`. It is *not* inside `nextseek_api` (it tests across layers) and *not* inside the vendored `chat_nextseek` (it lives above that layer). It imports from `chat_nextseek/e2e` as a library.

## 4. Architecture

```
       nessie_tests/overlay catalog (NEW cases)
                   │
  chat_nextseek/e2e catalog  ──imports──►  nessie_tests runner  ──drives──►  POST /cc-assistant/query/async
  (DSL + resolver + families + 366           │                                 (the REAL top-level router)
   variants, read-only)                      │                                        │
                                             │                              observes route (ns|cc) + engine;
                              scope: --specific <set> | --all               executes turn (full tier only)
                              depth: --tier route | full                            │
                                             │                              evaluator = reused PassCriterion DSL
                                             │                                        │
                                             └───────────────────────────►  JSON manifest + HTML report
```

Data flow, one case:
1. Loader merges the imported corpus with the overlay into one list of `Variant`s.
2. Runner selects cases by scope, submits each `Turn.query` to `POST /cc-assistant/query/async`, and consumes the event stream.
3. The **route observer** records the top-level `ns|cc` decision + selected engine/mode from the stream (the same route/cost signal `poll.py` already surfaces, e.g. `poll.py:117`).
4. **Route tier**: assert the route/parse fields, then abort the turn before execution. **Full tier**: let the turn complete, then assert answers/counts and the persisted result bundle.
5. Evaluator runs each `PassCriterion` via the imported resolver; reporter writes a JSON manifest + HTML (mirroring `e2e.py`'s existing artifacts).

## 5. Components

| Component | Responsibility |
|---|---|
| **Corpus importer** | Load the `Variant/Turn/PassCriterion` schema + resolver + the 366 families/variants from `chat_nextseek/e2e` (read-only). |
| **Overlay catalog** | A `nessie_tests/` JSON file, same schema, holding the new cases (§6). Merged with the imported corpus at load. |
| **HTTP driver** | Submit a turn to the async endpoint, consume the event stream, support abort-after-route (route tier) and run-to-completion (full tier). Reuses the transport pattern `full_ui_e2e.py` / `poll.py` already use. |
| **Route observer** | Extract the top-level `route` (ns/cc), `engine` (advanced_search / graph / get_parents / …), and parser `mode` from the stream; expose them as assertable fields. |
| **Evaluator** | The imported PassCriterion DSL, unchanged, plus three additive field-families (§7). |
| **Reporter** | JSON manifest + HTML report; per-case artifacts (query, route, raw response, bundle) for triage. |

## 6. The corpus

**Imported (read-only):** all 366 existing NS variants and their families. In the full tier they run through the real stack and their existing `parser_plan.mode` assertions still apply; the route family (below) adds a free top-level `route == ns` assertion to each.

**Overlay (new cases):**
- **Route family** — the runner applies a default `route == ns` expectation to every imported NS variant (without editing the vendored catalog); new CC-intent cases (pipeline launch, reingest, free-form agentic asks) assert `route == cc`; off-topic/unrelated cases assert the decline/"unrelated" path.
- **#33 consistency pairs** — the two "NHP sequencing" phrasings run as a **consistency group** asserting *same engine* and *same count*; plus an assertion that the graph count is **not** the `LIMIT 250` sentinel (true count ~1608). Red until #33 is fixed.
- **#32a repro** — child→parent + parent-attribute aggregation ("counts of sex/species of those monkeys") returns a real, non-empty distribution. Red until #32 is fixed.
- **#32b repro** — a recalled result bundle asserts attribute fields are present (species/sex), via the bundle-richness field. Red until #32 is fixed.
- **Bonus-failure regressions** (from the run-history dig) — deterministic EOF-truncation cases (`ReporterPlan`/`APIRequestPlan`/`ParserPlan`) assert no "could not be completed"; the cypher UID/`D.SEQ`-dot defects assert non-empty/valid results. Red until fixed.
- **Green anchors** — global count 50,889; MUS+NDMA→195; `refine_last_search` 0→303; representative `system_question` answers. Green now; guard against regressions.

## 7. New assertion primitives

Two are pure resolver field-families (report 01: adding a field family needs no schema change); the third is a small runner-level construct:
1. **`route` / `engine` field family** — resolver reads the observed top-level route + selected engine/mode from the route observer. Enables `route == cc`, `engine == advanced_search`, etc.
2. **Consistency group** (runner-level, not just a field) — a case that runs N phrasings through the runner, retains their results, and asserts they agree (same engine, same count). This needs a modest runner construct to hold sibling results and compare, beyond a resolver field. Directly encodes #33; generally useful for paraphrase-stability.
3. **Bundle-richness field family** — resolver reads the persisted `results_history` bundle keys for the turn's session (table `assistant_chat_session`, `assistant/models_db.py:7`). Enables `bundle has species`, encoding #32b. (Measured shape today: advanced_search persists full `json_metadata`; `get_parents` stores only `{id,uuid,sample_type,…}`; graph stores `{id,uuid,type}` with `memory_payload=null` — so this field is what makes the thin-bundle bug assertable.)

## 8. Tiers & scoping

Two orthogonal axes:

- **Depth** (`--tier`):
  - `route` — parse/route only, abort before execution. No CC container, no full pipeline, no seed data required. Cheap enough for a **pre-merge gate**.
  - `full` — run the turn to completion; assert answers/counts/bundles. Paid + slow; **nightly / pre-release**.
- **Scope**: `--specific <set>` (curated subset: route gate + repros) vs `--all` (the whole imported + overlay corpus). Plus `--family` / `--variant` / `--rerun --failed-only` inherited from the e2e.py conventions.

Typical combinations: `--tier route --specific` = fast pre-merge; `--tier full --all` = the paid comprehensive pass.

**Assertion families by tier:** `route` / `engine` / parser-`mode` assert in **both** tiers; **count**, **bundle-richness**, and **consistency-count** assertions are **full-tier only** (the route tier aborts before a result set or a persisted bundle exists).

## 9. Seeding & baseline

- **Route tier** needs no seed data — routing is intent-driven; a bare instance still routes (bare "Published Data" returns empty but routes correctly).
- **Full tier** needs the **seeded v2 dataset** (participating project ids 2-14, per the existing deployment note) so count assertions (139, 303, 50,889, ~1608) are meaningful. The spec assumes a seeded instance is a documented prerequisite of the full tier; count-bearing cases are tagged so they're skipped (not failed) on a bare instance.

## 10. Error handling & determinism

- **LLM non-determinism**: route/parse decisions are LLM outputs. Default is single-run with exact expected route/mode. Cases flagged nondeterministic support an optional small **quorum** (run k times, assert majority) — cheap in the route tier. `--rerun --failed-only` (inherited) re-checks flakes without a full re-run.
- **Budget gating (full tier)**: reuse the CC harnesses' fail-closed cost/time gate (per-turn cap; abort the run if a total budget is exceeded) so a full pass can't run away on spend.
- **Endpoint/stack failures** are reported as case errors (distinct from assertion failures) so infra problems don't masquerade as bugs.

## 11. Testing the harness itself

Hermetic unit tests (pytest) for the new pieces, with the HTTP layer stubbed:
- route-observer parsing of representative event streams (ns / cc / unrelated),
- the three new resolver field-families,
- the consistency-group evaluator,
- loader/merge of imported + overlay catalogs, and scope selection.
These need no live stack or API budget.

## 12. Open item to confirm in planning (not a blocker)

The exact seam for a clean **route-only short-circuit** on `POST /cc-assistant/query/async`: read the early route event and abort the turn, vs. the endpoint exposing a route-only mode. Both reviews confirm the route signal is observable (`poll.py`, `full_ui_e2e`'s route gate); planning pins the precise mechanism.

## 13. Deliverables

`nessie_tests/` containing: the runner + CLI, the overlay catalog, the corpus importer, HTTP driver, route observer, the three resolver extensions, the reporter, the harness unit tests, and a README documenting the tiers, scoping, the seeded-instance prerequisite, and cadence (route gate pre-merge; full pass nightly/pre-release). No changes to `chat_nextseek/`; no assistant behavior changes.

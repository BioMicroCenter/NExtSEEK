# nessie_tests `--bayesian`: paired dual-route evaluation feeding HiBayes

**Date:** 2026-08-04
**Branch:** `feat/nessie-bayesian` (worktree off `dev-v3-merge` @ `14e50ee`)
**Status:** design approved, plan not yet written

## 1. Why

The router decides, per turn, whether a question goes to `nextseek_query` or
`container_cc`. Nothing currently tells us whether it decides *well*, because no
run has ever put the same question through both engines. Route assertions in the
corpus encode what someone believed the router should do; they are not evidence
about which engine actually answers better.

`--bayesian` produces that evidence. It runs a curated subset of the corpus
through **both** engines with the router forced out of the way, captures
everything both engines produced, grades each answer twice (once by an LLM, once
by a human, independently), and emits rows a Bayesian model can consume.

Primary target: **route policy**, meaning posterior success by engine per
question, which is ground truth for "should this have gone to CC?".
Secondary target: **engine capability by family**, with partial pooling, so
statements like "CC beats NS on reporting, NS beats CC on graph_query" carry
credible intervals rather than being anecdote.

## 2. Prior art, and why we are not reusing it wholesale

`dmac-assistant` already contains a complete HiBayes evaluation chain:

| Stage | Module | Output |
|---|---|---|
| Runtime axis | `tools/hibayes/exporter.py` | `hibayes_eval_rows.csv`, 14 locked columns |
| A | `tools/hibayes/artifact_validator.py` | `hibayes_artifact_validity.csv` |
| B | `tools/hibayes/functional_inputs.py` | `hibayes_functional_eval_inputs.csv`, 12 locked columns |
| C | `baml_src/functional_evaluator.baml` + `tools/e2e/functional_evaluator.py` | `FunctionalEvaluation`: outcome, `usefulness_score` 0-4, `primary_issue`, `needs_human_review`, `review_priority`, rationale |

It also contains `tools/e2e/run_router_batch.py` and `tools/e2e/router_dispatch.py`,
which already drive a corpus through the router and dispatch per decision.

**That driver does not test the product.** `dispatch_ns` shells out to
`docker run <image> python /opt/dmac/runner_ns.py`, and `dispatch_cc` calls
`run_headless.run_one` directly. Neither goes through
`POST /nextseek_api/cc-assistant/query/async/`, so nothing exercises sticky CC,
the sidecar, session state, bundles, `results_history`, or the real turn
lifecycle. Those are precisely what `nessie_tests` covers and precisely what a
routing decision has to live with in production.

So: **the driver is rebuilt in `nessie_tests`, the evaluation chain is reused in
spirit and Stage C is reused literally.**

## 3. Decisions

Each of these was an explicit choice with alternatives considered.

| # | Decision | Rationale |
|---|---|---|
| D1 | Target is route policy first, family capability second | Determines that pairing is required and that family is a grouping variable, not the unit |
| D2 | Grading is LLM **and** human, independently, with disagreements surfaced | Validates the grader on this corpus so future runs can lean on it without a full human pass |
| D3 | One row per variant per arm, graded on the final turn | Matches corpus structure; keeps `refine_and_recall` (25 variants), which is exactly where the engines diverge on memory |
| D4 | `nessie_tests` emits the HiBayes CSVs directly | Avoids synthesizing 18 DMAC manifest fields and faking `tool_use_summary` / `stop_reason` / `num_turns` for every NS turn |
| D5 | Rows carry **both** taxonomies: nessie `task_family`, hibayes `task_subtype` | Posterior can pool either way and rows stay concatenable with dmac-assistant runs |
| D6 | Artifact collection is a post-hoc script | Re-runnable without repaying for the suite; see R3 for the residual risk |
| D7 | Modes are interleaved per question, NS then CC | Mode would otherwise be confounded with wall-clock time; see §4 |
| D8 | One unified, hand-owned `nessie_tests/corpus.json`, adopted not generated | One file to read and edit; a drift test keeps the fork from the vendored catalog explicit |
| D9 | Architecture is `run_case()` extraction plus a sibling `bayesian.py` | Keeps `run_suite`'s accumulated correctness in one place instead of forking or bloating it |

## 4. Why interleaving is not cosmetic

Running all NS turns in one pass and all CC turns in another confounds engine
with time. A provider outage, a deploy, or a load spike during one pass becomes
a fake engine effect the model cannot separate from a real one. This is a live
failure mode, not a hypothetical: 10 of the 18 reds in the 2026-08-03 seed-6 run
were a single Bedrock outage.

Interleaving per question means both arms see the same conditions within seconds
of each other, which is the entire reason to run a paired design.

## 5. Architecture

```
nessie_tests/corpus.json   (is_bayesian: true)
        |
        v
  bayesian.py  --->  run_case(v, force_route="ns")  --+
   interleaved       run_case(v, force_route="cc")  --+
   per variant                                        |
        |                                             v
        |                                nessie_out_bayes/bayes_manifest.json
        v                                      (BayesManifest: pairs[])
  collect.py (post-hoc)
    ./outputs/<ts>_<user>/      host bind mount
    dmac-cc-users scratch       docker cp
    assistant_query_task        SQL: progress event stream
    CCSessionTranscript         SQL: zstd -> jsonl
        |
        v
  artifacts/<variant_id>/<arm>/...
        |
        v
  export.py
    hibayes_eval_rows.csv                 14 cols, ONE PER ARM
    hibayes_artifact_validity.csv         Stage A, scoped
    hibayes_functional_eval_inputs.csv    12 cols
    excluded.csv                          outages and NotAssessable, with reasons
        |
        v
  Stage C  (dmac-assistant, BAML EvaluateFunctionalUsefulness via GCPReasoner)
        |
        v
  output-skill-bayesian: report_bayes.html
    blind human grading -> reveal -> disagreement view
        |
        v
  merge_grades.py -> graded table -> HiBayes posterior
```

New modules, all under `nessie_tests/`: `bayesian.py`, `collect.py`, `export.py`,
and `output-skill-bayesian/`.

**Deviation, phase 1 as built (2026-08-04):** this section originally promised a
`bayes_corpus.py`. There is no such module. The corpus-side functions
(`bayesian_ids`, `hibayes_meta`, `load_family_defaults`, `merged_from_unified`)
landed in the existing `nessie_tests/corpus.py` instead, because splitting them
out would have meant a second reader of `corpus.json` and a second place for the
`_HIBAYES_KEYS` tuple to drift. Plans 2 and 3 already consume them as
`corpus.*`; nothing imports `bayes_corpus`.

## 6. Phase 1: the unified corpus

### 6.1 What it absorbs

| Source | Count | Note |
|---|---|---|
| `chat_nextseek/e2e/catalog.json` | 366 | vendored |
| `nessie_tests/overlay.json` | 47 | 30 override a base id, 17 new |
| `nessie_tests/retired.json` | 100 | 91 from base, 9 overlay-only |
| ~~`probes/probe-cc-2026-07-31.json`~~ | ~~13~~ | **NOT absorbed — see below** |
| **resolved active today** | **283** | of 383 defined, 314 turns |

Plus four non-variant blocks in `overlay.json`: `criterion_rewrites`,
`route_policy`, `family_floor`, `consistency_groups`.

**Deviation, phase 1 as built (2026-08-04): the probe variants were deliberately
NOT adopted.** `corpus.json` carries `origin` values `base` (336) and `overlay`
(47) and nothing else. Adopting the inline variants from the three probe files
(13 + 7 + 2) would have taken `merged()` to 296 and broken the §6.4 acceptance
gate, which is the one check that the migration changed no resolved behaviour. A
gate that the migration itself moves proves nothing, so the gate won and the
probe files keep their inline variants. They still parse — §6.3's last line holds:
nothing in the turn schema changed, and `--cases` reads them exactly as before.
Absorbing them later is a separate, deliberate curation change with its own new
expected count, not a migration step.

### 6.2 The constraint that shapes it

`chat_nextseek/e2e/catalog.json` has **16 readers**, ten inside the vendored
`chat_nextseek` (its own e2e suite, `cli.py`, `schema_helper.py`) plus
`dmac_assistant/src/dmac_assistant/config.py`. `nessie_tests` was built with
zero edits to that subpackage as an explicit property, and
`startup/scripts/sync_chat_nextseek.sh` would clobber any edit on the next
snapshot.

So the unified file becomes nessie's source of truth. It does **not** replace
`catalog.json`, which stays exactly where it is for its other ten readers.

### 6.3 Schema

`nessie_tests/corpus.json`:

```json
{
  "version": 2,
  "provenance": {
    "adopted_from": "chat_nextseek/e2e/catalog.json",
    "catalog_sha256": "<pinned at adoption>",
    "adopted_on": "2026-08-04"
  },
  "families": {
    "reporting": {
      "description": "...",
      "defaults": {
        "hibayes_subtype": "Reporter-Summary",
        "expected_behavior": "AnswerDirectly",
        "artifact_expected": false,
        "artifact_kind": "NONE_EXPECTED"
      },
      "variants": [{
        "id": "report.i_need_to_submit_these_samples",
        "name": "...",
        "tags": ["nessie", "full"],
        "requires_env": [],
        "turns": [ { "label": "...", "query": "...", "pass_criteria": [ ] } ],

        "status": "active",
        "origin": "base",
        "is_bayesian": true,
        "hibayes_subtype": "Report-GEO",
        "expected_behavior": "GenerateArtifact",
        "artifact_expected": true,
        "artifact_kind": "GEO_XLSX",
        "retirement": null
      }]
    }
  },
  "criterion_rewrites": { },
  "route_policy": { },
  "family_floor": { },
  "consistency_groups": [ ]
}
```

Field decisions:

- **`status`** (`active` / `retired`) replaces `retired.json` for every RUN path.
  `merged()` filters on it. The retirement record (`reason`, `retired_on`,
  `decided_by`) moves inline as `retirement`.

  **Correction, as built (2026-08-04): retirement is hand-owned in one direction
  only, and reinstating is NOT a one-word edit.** `build_corpus._carry_forward`
  copies `status: "retired"` (with its `retirement` record) forward, so RETIRING
  in `corpus.json` survives a rebuild. It has no branch that copies `active`
  forward, so UN-retiring does not: hand-setting `status: "active"` with
  `retirement: null` and rebuilding re-derives `retired` out of `retired.json`.
  Verified on `advanced.find_samples_of_pbmc_type_from`. Reinstatement is a
  two-file edit — flip the status here AND remove the id from `retired.json` —
  and `retired.json` therefore remains authoritative in the retire direction
  rather than being replaced "entirely".
  `test_a_rebuild_does_not_preserve_a_corpus_json_only_reinstatement` pins it.
- **`origin`** (`base` / `overlay`) preserves provenance so the drift test knows
  which variants to compare against upstream. The vocabulary has two values, not
  three: a `probe` origin was specified but never issued, because the probe
  variants were not adopted (§6.1).
- **`is_bayesian`** defaults `false`. `--bayesian` selects on it and nothing else.
- **Family `defaults` with per-variant override** is how `reporting` becomes
  honest: the family default is `Reporter-Summary` / `AnswerDirectly`, and the
  GEO/SRA/PRIDE/NFCORE members override to `GenerateArtifact`. A single family
  label is wrong for part of that family, which is the whole reason the override
  exists.
- **Policy blocks stay blocks**, not baked into variants, because
  `test_floor_ops` and `test_inline_route_assertions` depend on distinguishing
  what the author wrote from what the floor added.
- **`overridden_ids` disappears.** Overlay-replaces-base is a merge artifact;
  with one definition per id there is nothing to override.

Nothing in `PassCriterion` or the turn schema changes, so `--cases` probe files
keep parsing unchanged.

### 6.4 Acceptance gate

`corpus.merged()` must return a resolved variant list **byte-identical** to
today's before any new field is populated: 283 variants, 314 turns, unchanged
`corpus_fingerprint` semantics. The existing 813 tests are the check. Only after
that passes do `is_bayesian`, `hibayes_subtype` and the artifact fields get
filled in.

### 6.5 Drift test

A test compares `origin: "base"` variants against the live `catalog.json` and
fails when upstream gains a variant nessie has not adopted, or changes one nessie
carries. The fork becomes explicit rather than silent. It does not auto-merge;
adopting an upstream change is a deliberate edit.

### 6.6 Side benefit

This dissolves two of the five findings from the 2026-08-04 review by
construction: one loader means finding 3 (`load_overlay` seeing a
pre-retirement world) cannot recur, and one definition per id makes finding 4
(15 dual-defined variants) impossible.

A third was expected to fall out of adopting the 13 probe-cc inline variants,
which would have reduced the probe files to pure `include_ids` lists. **That did
not happen and was not attempted** — adoption breaks the §6.4 gate (§6.1). The
probe files still carry 13, 7 and 2 inline variants, so finding 1's territory is
untouched here and finding 1 keeps its own test (§10), which is what actually
covers it: `tests/test_probe_files.py` loads every committed probe and fails when
one names an id the corpus no longer has.

## 7. Phase 2 and 3: the runner

### 7.1 Extraction

`run_suite`'s per-variant body (currently `runner.py` lines 128 to 285) moves to:

```python
def run_case(v, *, tier, post_query, get_progress, bundle_reader=None,
             pace_s=0.0, force_route=None, strip_route_criteria=False,
             sleep=time.sleep, clock=time.monotonic) -> NessieManifestEntry
```

It returns exactly one entry, including both skip paths (`requires_env` unset,
non-gate at route tier), so `run_suite`'s output is unchanged. `run_suite` keeps
selection, sampling, `run_meta`, consistency groups and report writing; its loop
collapses to one call.

Everything hard-won stays in one place: the poll loop, last-write-wins on
`route`, turn-0 pinning of `route_source`, the outage-after-a-real-red rule, and
the `no_assertions` guard placed before `_apply_xpass`.

`force_route` threads one level into `http_driver.drive`, which adds it to the
POST body. That is the entire product-side surface.

### 7.2 Preflight, mandatory

`force_route` is gated on `is_staff` / `is_superuser`
(`nextseek_api/services/cc_assistant.py:245-251`), and a non-admin's value is
**silently dropped back to the router**. A 300-turn run would complete and mean
nothing.

The check is nearly free. Send one obviously out-of-scope query with
`force_route="ns"`. A forced decision is never `ROUTE_UNRELATED` by construction,
so observing `route == "unrelated"` proves the force was dropped, and the run
aborts before spending anything.

The harness default user is `demo`, which is not staff. A staff account is a
hard prerequisite.

### 7.3 Criteria under forcing

Both are driven by `strip_route_criteria=True`, which only `bayesian.py` passes.
`run_suite` leaves it `False`, so nothing about a normal run changes.

- Every criterion with `field` in `{route, engine}` is stripped before
  evaluation, on both arms, whatever its origin, including what
  `apply_route_policy` injects. Forcing the route makes a route assertion
  tautological. The stripped count is recorded per case so this is visible.
- `known_fail` produces no `xpass` promotion. That tag records an expectation
  about router-decided NS behaviour and says nothing about a forced arm. It is
  carried as a covariate.

Surviving criteria still run on both arms and land in `observations`. They are
**never** the success label. On the NS arm they are a useful prior for the
grader; on the CC arm most will skip as unobservable, which is itself
informative.

### 7.4 Orchestration

Per variant in corpus order: run the NS arm, then the CC arm, each with
`force_new` on its first turn so the pair is independent, then write the pair
and continue. Writing per pair rather than at the end is what makes resume work.

### 7.5 Manifest

`BayesManifest` wraps the existing `NessieManifestEntry` unchanged, so outage
detection, cost accounting and the observation schema all apply without a
parallel implementation.

```
run_meta   mode=bayesian, arms, corpus_fingerprint, git_sha, base_url, selected_ids
pairs[]    id, family, hibayes_subtype, ns: <NessieManifestEntry>, cc: <NessieManifestEntry>
```

### 7.6 CLI

New: `--bayesian`, `--max-usd`, `--resume <run_dir>`, `--full-timeout`, and
`--out` defaulting to `nessie_out_bayes/`.

`--max-usd` aborts cleanly at a run-level ceiling, summing observed costs and
skipping `None` rather than treating it as zero. `--resume` reads pairs already
written and skips those `(variant, arm)` combinations. Both exist because prior
CC runs died on the budget cap, then at 180s, then at 300s, and reingest has
never once run to completion.

`--bayesian` refuses to combine with `--tier`, `--scope`, `--sample`, `--seed`
or `--cases`, for the same reason `--cases` already refuses them: `is_bayesian`
is the selection, and two sources for "what ran" makes a run unexplainable.

## 8. Phase 4: the collector

### 8.1 Join keys

| Source | Key | Status |
|---|---|---|
| Event stream, final reply, cost | `task_id` on `QueryTask` | direct |
| CC artifacts + raw file list | `query_complete.data.artifacts` / `.cc_raw_files` | direct |
| CC published scratch | `<scratch>/<run_id>` on `dmac-cc-users` | `docker cp` |
| CC session transcript | `CCSessionTranscript`, zstd | direct |
| **NS `run_root`** | **none** | **gap** |

`run_root` is set into the chat_nextseek session dict at
`chat_nextseek/src/chat_nextseek/orchestrator.py:335` and never escapes.
`QueryTask` has no field for it and no event carries it.

**Fix:** emit it. Three lines in `nextseek_api/services/cc_assistant.py` read
`session["run_root_dir"]` off the adapter after the NS call and send it as an
`ns_run_root` event. This touches product code but **not** the vendored
`chat_nextseek`, so the zero-edit property survives.

**Fallback:** a timestamp join. The directory is `%y%m%d_%H%M%S_<api_user>` and
`--bayesian` is strictly sequential, so exactly one run_root falls in each
turn's window. Kept as the path for runs predating the event.

### 8.2 Output layout

```
artifacts/<variant_id>/<arm>/
    run_root/          console.txt, chat.txt, api_requests.json, prompts.json, files/
    cc_scratch/        published deliverables and raw/
    session.jsonl      decompressed CC transcript
    task.json          full progress event stream and result
collection.json        what was found, what was missing, and why
```

`collection.json` matters: an artifact that could not be collected and an
artifact that genuinely was not produced are different facts, and the grader has
to tell them apart.

## 9. Phase 5: the export

One `hibayes_eval_rows.csv` **per arm**, because `_validate_consistency` check #6
requires `is_opus` uniform within a file and NS is not Opus at all. The two files
concatenate for the model.

Column mapping, with the three that needed a decision:

- **`image`** carries the arm (`nextseek_query` / `container_cc`). It is the
  discriminator the model conditions on.
- **`tool_calls_total`** is `int`, not nullable. Emitting 0 for NS would be false,
  since NS really does issue API and graph calls. Defined as engine operation
  count: CC tool invocations on one side, executed API plus graph plan calls
  recovered from the event stream on the other.
- **`artifact_count`** is `len(artifacts)` for CC and the file count under
  `run_root/files/` for NS.

### 9.1 Outages are excluded, not scored

An outage means the fallback chain died before the product ran. Emitting it as
`is_error=true` would teach the posterior that Bedrock downtime is CC
incapability. `nessie_tests/outage.py` already holds the one definition. Excluded
rows go to `excluded.csv` with their reason, so the exclusion is auditable rather
than invisible.

### 9.2 Stage A is scoped down

Artifact validity emits `NotExpected`, `Missing`, `Unreadable`, `Valid` on
presence and readability only. Schema validation (the GEO xlsx template parity
that `artifact_validator.py` does upstream) is deferred; everything it would have
judged is emitted `Indeterminate` rather than guessed.
`aggregate_artifact_status`'s worst-status-wins rule is reused as specified.

### 9.3 Parity test, and its honest limit

The two locked column tuples are pinned in `export.py` with a comment naming
`dmac-assistant/tools/hibayes/exporter.py` as their source, and a test asserts
the emitted header matches the pinned tuple byte for byte. That catches our own
drift. It **cannot** catch upstream changing the contract, because the repos are
separate. Stated here so no one reads the test as coverage it does not provide.

## 10. Phase 6: the split report and grade merge

### 10.1 Blind then reveal

If the report shows the LLM's grade beside the transcript while the human is
grading, the agreement rate is inflated by anchoring and the disagreement set
stops meaning anything.

So: the Stage C verdict is embedded in the page but not rendered for a row until
that row has a human grade, with a global reveal only after the pass completes.
Both grades live in one file, no second load, and the blinding is a render rule
rather than a separate artifact.

### 10.2 Persistence

`report.html.tpl` already autosaves per-case notes to `localStorage` under
`"nessie-notes:" + META.notes_id` and downloads them as a JSON blob
(`templates/report.html.tpl:529-591`). The bayesian report extends that from free
text to a structured record per `(variant, arm)`: binary grade, optional note,
timestamp. Same autosave, same download, new file name.
`rehydrate_report.py`'s trick of pulling `const CASES` back out of a built report
keeps working, so a graded report stays rebuildable.

### 10.3 Layout

Three columns per question: the question and its metadata, then NS and CC side by
side, each showing final reply, per-turn trace, artifact list, cost, latency and
the surviving criterion observations.

Two known traps in the house CSS, both of which have bitten this template before
and both of which must be verified headless rather than assumed: `.reply` carries
a 78ch clamp that fights a half-width column, and nested-grid rows shrink to fit
rather than filling.

Grading is keyboard-driven. 300 grades by mouse is its own failure mode.

### 10.4 Reconciling the two grade shapes

The human emits binary. Stage C emits a 6-value `FunctionalOutcome` plus
`usefulness_score` 0 to 4. The projection is already specified upstream in DD-08:
`{FullySatisfied, AppropriateClarification, AppropriateBoundary}` maps to success,
the rest to failure. `NotAssessable` maps to neither and is excluded alongside the
outages rather than counted as a loss.

Disagreement is then binary against binary. `usefulness_score`, `primary_issue`
and `needs_human_review` ride along as covariates.

### 10.5 merge_grades.py

Takes the two per-arm `hibayes_eval_rows.csv`, the Stage C output, and the
downloaded `grades.json`. Writes one graded table carrying `human_success`,
`llm_success`, `agree`, `usefulness_score`, `primary_issue` and the runtime axis.

It fails loudly on any pair missing a human grade rather than dropping it,
because a quietly shorter table is how a partial grading pass gets read as
complete.

### 10.6 Shape

`nessie_tests/output-skill-bayesian/` as a **sibling** to the existing skill, not
a fork: `build_bayes_report.py`, `merge_grades.py`, `report_bayes.html.tpl`, and
a `SKILL.md` describing the sequence. `fetch_run.py` is reused unchanged, since
pulling task rows from the DB is identical work.

## 11. Testing

- Phase 1 is gated on `corpus.merged()` returning a byte-identical resolved list;
  the existing 813 tests are the instrument.
- `run_case()` extraction is a pure refactor already covered by those tests. Any
  behaviour change is a defect, not a feature.
- New unit coverage: `force_route` reaching the POST body; the preflight
  correctly detecting a dropped force; route and engine criteria stripped on both
  arms; `known_fail` producing no `xpass` under forcing; interleaving order;
  resume skipping exactly the completed pairs; budget abort summing `None`
  correctly; outage rows landing in `excluded.csv` rather than the eval rows;
  header parity against the pinned tuples; blind-render logic; `merge_grades`
  failing on a missing human grade.
- **Also close review finding 1** while in here: a test that loads every file in
  `nessie_tests/probes/` through `select_cases()`. It is unrelated to
  `--bayesian` but it is the same corpus surface, it is free, and it is currently
  the only defect that will actively waste a paid run.

## 12. Risks

| # | Risk | Mitigation |
|---|---|---|
| R1 | The staff-account prerequisite is missed and a whole run is meaningless | Mandatory preflight, §7.2, aborts before spend |
| R2 | Two variants really submit pipeline jobs (`pipeline.end_to_end_emit`, `pipeline.happy_path_scrnaseq`, each ending on a literal `submit` turn), so a paired run launches 4 real jobs | Accepted deliberately rather than losing the family. Flagged in the run log |
| R3 | `cc_sweep` reaps CC scratch before the post-hoc collector runs | Collector records the miss in `collection.json`; if it proves common, move CC scratch collection inline |
| R4 | Cost and wall clock: 150 forced CC turns are 150 full Opus turns, against a history of $0.50 caps and 180-300s timeouts | `--max-usd`, `--resume`, `--full-timeout`; expect tens of dollars and many hours |
| R5 | Upstream `catalog.json` or the locked HiBayes columns drift and our tests cannot see it | Drift test covers catalog; the column parity limit is stated explicitly in §9.3 rather than papered over |
| R6 | Human grading fatigue across 300 cells degrades label quality late in the pass | Keyboard-driven grading; grades carry timestamps so late-pass drift is at least measurable |

## 13. Out of scope

- Editing `chat_nextseek/e2e/catalog.json` or anything else under the vendored
  subpackage.
- Changing `PassCriterion` or the turn schema.
- The HiBayes model itself. This spec ends at the graded table.
- Full artifact schema validation (Stage A beyond presence and readability).
- Retrofitting the existing `output-skill`; the bayesian one is a sibling.

## 14. Open questions

1. Which ~150 variants get `is_bayesian: true`. The selection is a curation pass,
   not a design decision, but it should be deliberate about family balance rather
   than a top-N slice.
2. Whether `--bayesian` should ever run unattended on a schedule, which would
   change the budget and resume story from "operator watches it" to "it has to
   survive alone".

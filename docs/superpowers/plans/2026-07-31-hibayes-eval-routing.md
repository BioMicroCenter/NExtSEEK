# HiBayes × NExtSEEK Evaluation & Routing Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **V7 HARDENED — INDEPENDENT EXACT-DIFF REVIEW CLEAN; PUBLISHED ON ORIGIN/DEV (2026-08-05):** V4 below was produced only after
> re-inventorying every fetched NExtSEEK ref and worktree, the deployed service-account clone, and
> the running image. V4 supersedes every conflicting V2/V3/original code sketch, command, success
> condition, failure condition, and rollback statement. Earlier prose remains design history; it
> is not permission to bypass a V4 gate. The original 15 tasks and all V4 prerequisite work remain
> **unexecuted**. V5 hardened the retrieved V4 artifact; V6 changed the classifier source but also
> introduced unapproved assumptions. V7 records the maintainer's 2026-08-05 rulings, corrects those
> assumptions, and reconciles the latest fetched corpus/source facts. V7 was the published plan
> authority on `origin/dev` until **V8 below superseded it**. Vetting is neither implementation nor execution authorization. The preserved
> pre-V7 plan is `docs/superpowers/plans/2026-07-31-hibayes-eval-routing.pre-v7-20260805T131500-0400.md`
> (SHA-256 `77b0d0b0acb9adbde8af88981ec6bf7b2f2ea1a6a828d68895db087c80e94fcf`).

> **V8 (2026-08-07) — SUPERSEDED IN PART BY V9 BELOW; NOT YET RE-VETTED.** V8 records the maintainer's
> 2026-08-07 rulings and supersedes conflicting earlier prose for the Stage-2 corpus-growth loop,
> the eval-row schema (`EVAL_ROW_SCHEMA_VERSION` 2 → 3), the combined outcome definition and its
> total disposition mapping, stack-version identity, execution reuse, and the terminology
> corrections V8-H…V8-K. Read V8 before V2–V7 wherever they disagree. **V8 changes this artifact
> after the last vetting pass and therefore invalidates it; the plan must be re-vetted before
> execution.** V4-0 and the V5 evidence-manifest release gate are unchanged by V8.

> **V9 (2026-08-08) — SUPERSEDED IN PART BY V10 BELOW.** V9 records the maintainer's
> 2026-08-08 rulings on the **deterministic artifact axis**: who owns it, how artifact validity is
> computed, the multi-artifact unit, the required-field rule, and the projection onto the eval
> row's four-value `artifact_status`. Read V9 before V2–V8 wherever they disagree on those five
> points; V8 is otherwise unchanged and remains authority for the eval-row schema and the combined
> outcome. **V9 also changes this artifact after the last vetting pass. It does not add a second
> re-vet: V9 rides the same re-vet V8 already requires before execution.** V4-0 and the V5
> evidence-manifest release gate are unchanged by V9.

> **V10 (2026-08-10) — SUPERSEDED IN PART BY V11 BELOW; V10 EXACT-DIFF REVIEW CLEAN.** V10 records the
> maintainer's eight 2026-08-10 blocker rulings and applies the confirmed V9 re-vet oracle repairs.
> It preserves the paired estimand, greedy promotion, dynamic corpus-owned family taxonomy,
> permanent `unrelated` spend gate, four-component stack boundary, deterministic artifact axis,
> required-but-empty semantics, and every V4/V5 approval gate. It grants no implementation,
> provider, paid-run, database, deployment, activation, commit, or push authority. The preserved
> byte-for-byte V9 backup is
> `docs/superpowers/plans/2026-07-31-hibayes-eval-routing.pre-v10-20260810T112554-0400.md`
> (SHA-256 `8da83f5b6fd1b29bad448b5ca10550c655e27cf00d342d11f71a429fe4950df8`).

> **V11 (2026-08-10) — SUPERSEDED IN PART BY V12 BELOW; V11 EXACT-DIFF REVIEW CLEAN.** V11
> simplifies only the approval process: ask the maintainer directly in the active conversation when
> a decision or risky action is actually needed. It removes V5-2's separate authenticated-record,
> signature, external authority-resolver, and nonce infrastructure. It changes no product design,
> safety condition, paid cap, or action boundary and grants no implementation or operational action.
> The preserved exact V10 backup is
> `docs/superpowers/plans/2026-07-31-hibayes-eval-routing.pre-v11-20260810T115925-0400.md`
> (SHA-256 `0cc82c0fa1fec47b4d1a5976c295fca1689c6a1d18b3b6ee5b88f83d7ae3ba02`).

> **V12 (2026-08-10) — CURRENT AUTHORITY; INDEPENDENT EXACT-DIFF REVIEW CLEAN.** V12
> removes V5-1's new governance-policy STOP. NExtSEEK's existing private-instance, authenticated,
> API/project-scoped security model remains the authority; Plan 018 adds no parallel governance
> regime. It preserves narrow technical access tests and direct at-time approval for actual external
> judge payloads. The exact V11 backup is
> `docs/superpowers/plans/2026-07-31-hibayes-eval-routing.pre-v12-20260810T120551-0400.md`
> (SHA-256 `e2eaf7331a141005753aca15cf4b51f70e769effe0b891f6325ea4562d5de695`).

**Goal:** Build a Bayesian router for the NExtSEEK assistant. A forced-route experiment runs one
question corpus down the NExtSEEK path and down the Container-CC path and estimates a paired,
family-conditional route difference (V4-1 through V4-4). Policy-selected online telemetry remains a
separate route-conditional monitoring product unless a later reviewed causal-assignment strategy
authorizes comparative updating (V4-7). Routing may consult only an approved, activated paired
generation and otherwise falls back to the LLM routing function (V4-5 and V4-6).

**Architecture:** A split router feeding three stages.

**The router** is two functions (V3-B): a permanent classification call mapping a query to a declared task family or to `unrelated` — the spend gate — and a routing function selecting destination and model.

**Stage 1 — baseline (V3-C).** The `nessie_tests` harness runs the corpus under forced routes and the paired results are fitted into the prior. This is an experiment, not observation: the same question goes down both routes.

**Stage 2 — online monitoring.** `task_family` comes back from the classification call and a durable
per-turn ledger row is written alongside the existing JSON envelope; a nightly Celery task exports
ledger rows and judges only new/changed turns against a fingerprinted cache. These observations
support route-conditional monitoring and playbooks, not the paired comparative fit (V4-7).
Stage 2 also feeds Stage 1, by **supplying questions rather than outcomes**: every classified turn
whose family is not `unrelated` is a candidate for promotion into the corpus, which a later forced
paired run then executes (V8-A).

**Stage 3 — consumers.** Published posteriors feed playbook guidance first, then routing itself (V3-D), with the LLM routing function retained as the fallback.

**Tech Stack:** NExtSEEK's locked application Python (currently ≥3.14), a separately locked Python
3.12 eval image, Django + Celery (`batch_upload` queue), BAML (judge + router), MySQL,
pytest / pytest-django, Docker.

## Global Constraints

- **Historical reviewed implementation base:** `dfbccaf89010c468bdb1b9eba3d04f050fd7cb81`
  (`origin/dev` on 2026-08-04). It has moved. The 2026-08-05 all-instance refresh observed
  `origin/dev@a55b532412b57b6f61554928c2bfdc43b935fc77` and
  `origin/dev-v3-merge@d0855bd262843990fb774027c52a2e4a69726711`; the latter contains a newer
  ordinary/paired Nessie producer that is a candidate port source, not proof that V4-2 is DONE.
  V4-0 must select and record the authorized implementation base and reconcile this source before
  product work. No task may silently continue from `dfbccaf` or discard the newer producer.
- **Coverage target: 95%**, across unit, integration and live end-to-end tests.
- **No paid model call runs automatically.** Every paid path is behind an explicit opt-in env gate and is never invoked by CI or by a default test run.
- **No new credentials into the agent sandbox.** The isolation invariants are untouched by every task here.
- **Copy, do not rewrite.** Vendored evaluation logic must preserve the original's behaviour. Reformatting is acceptable; changing control flow or thresholds is not.
- **No dependency on a `dmac-assistant` checkout.** After Phase 2, every task must pass on a machine that does not have that repository.
- **The two BAML trees stay byte-identical** (`dmac_assistant/baml_src/` and `docker/cc-runtime/baml_src/`). Any edit lands in both in the same commit.
- **The capabilities file is hash-pinned by exactly one test** (`nextseek_api/cc_assistant/tests/test_f_constraint_pins.py:12,17`). A second test's docstring claims it also pins the file — it does not; ignore that docstring. **No task in this plan edits `dmac_assistant/build_context/route_capabilities.json` (V6-A), so that pin must stay green untouched.** A task that finds it needs to change that file has left this plan's scope and stops for the maintainer.
- **Classifier label-space source of truth** is the latest schema-compatible `nessie_tests` corpus
  (V7-A) — **not** `dmac_assistant/build_context/route_capabilities.json`. Every key under the
  corpus's `families` object is a classifier label; axes declared outside `families` are not labels.
  There is no second include/exclude list, fixed count, or label-selection approval layer.
- **Never commit or push without the maintainer's explicit go-ahead.** Tasks end at `git commit` on the feature branch only.
- **Hermetic test command:** `pytest nextseek_api/cc_assistant/tests/`
- **DB-backed tests run in the V2 worktree-mounted harness**, using `uv run --no-sync` and the
  canonical test DB. The running `nextseek` container is not a source harness unless provenance
  proves that exact worktree is mounted; V2-0 governs.

## V2 binding hardening amendment (2026-08-04)

### Change control, provenance, and precedence

This amendment applies the independently reviewed and claim-checked findings in:

- `_plan018-refs/reviews/PLAN018-ADVERSARIAL-RISK-REVIEW-2026-08-04.md`
- `_plan018-refs/reviews/PLAN018-GAMEABILITY-REVIEW-2026-08-04.md`
- `_plan018-refs/reviews/PLAN018-CLAIM-VERIFICATION-2026-08-04.md`

These three live in the maintainer's private state directory on the development host and are
deliberately not reproduced here: this repository is public, so specifics stay in the private notes
and the public artifact references them by name only.

The preserved pre-vetting plan is:
`docs/superpowers/plans/2026-07-31-hibayes-eval-routing.md.bak-pre-plan-vetting-20260804T113805-0400.md`
(SHA-256 `71686cfc18ae9255cb9d0479cb145aabfdbad2c28028bb5b71015dc186557847`).

Rules of precedence:

1. The approved design and user-stated decisions remain binding. This amendment adds no causal
   cross-route claim, dual routing, historical backfill, heuristic routing change, model-threshold
   change, credential exposure, or reroute authority.
2. For every task, the V2 completion contract below is mandatory in addition to any
   non-conflicting original requirement. A conflicting original snippet or command is void.
3. No task may be marked DONE from helper-only tests, string greps, a skipped live test, a stale
   evidence file, or output from source other than the task worktree.
4. No commit, push, deploy, live DB migration, paid/model call, or destructive rollback occurs
   without its existing explicit maintainer gate. This vetting pass grants none of those gates.

### V2-0 — Mandatory execution preflight (before original Task 0)

- [ ] Start from the exact base above, or stop and re-run the spec drift protocol over every file
  in the File Structure table plus `chat_nextseek/src/chat_nextseek/orchestrator.py`, generated BAML clients,
  `docker/scripts/entrypoint.sh`, and the deployment/test harness.
- [ ] Create a feature worktree only after execution authorization. Record base SHA, current SHA,
  dirty-diff SHA-256, and the pre-task full-suite result in `evidence/plan018-preflight.json`.
- [ ] Author or select a **worktree-mounted, non-live test harness**. The running `nextseek`
  container does not mount this checkout at `/app`; therefore every original
  `docker exec ... nextseek` implementation, `makemigrations`, `sqlmigrate`, coverage, or test
  command is void unless a later provenance check proves that exact worktree is mounted. The DB
  lane must identify the Django test settings, test-database name, source mount, image ID, and
  network; it must refuse the live application schema.
- [ ] Every piped evidence command runs under `set -o pipefail`. A machine-readable evidence
  sidecar records exact argv, UTC start/end, exit status, source SHA, dirty-diff hash, image ID (if
  any), DB alias/name (if any), passed/failed/skipped/xfail/deselected counts, and artifact hashes.
  Unexpected skips, xfails, deselection, collection errors, or zero-test selections fail the gate.
- [ ] Record the real baseline before Task 0. `evidence/baseline.log` and its provenance sidecar
  are created here; no later task may invent them retroactively.

**V2-0 success:** a fresh verifier can re-run the harness from the recorded worktree and reproduce
the baseline without touching the live application DB or running a paid path. **STOP** for the
maintainer if such a harness cannot be proved; never fall back to the running container by name.

### Binding durable turn/evaluation contract

The metadata-only `TurnLedger` / `EvalRow` sketches below are superseded. The durable row must
contain, or immutably reference in a non-evicting table, the complete judge/fit input captured at
turn completion:

- stable ledger PK; session FK; turn number; owner user; nullable project-scope key;
- route including `unrelated`; nullable task family; route source and family source;
- query text and final answer; completion status/error; answer-present, timeout and runtime-success
  facts; latency, cost, model identity, tool-call count;
- artifact declarations/validated facts and the trace facts required for the four-tier ladder;
- schema version, source-content SHA-256, creation time, and immutable/update provenance.

`None` family is an honest `unmatched`/`unrelated` observation: it is recorded but excluded from a
family posterior and rendered `unknown`/`TooUncertain`. Every turn has an explicit nullable family
state and independent provenance; the system never fabricates a label when classification did not
run or failed. No historical backfill is added.

The canonical fingerprint covers the normalized complete judge input plus prompt version, judge
model ID, eval schema version, evaluator source version, and source-content hash. A changed query,
answer, outcome, artifact/trace fact, or version must invalidate the cache.

Worked-example content is stricter than aggregate statistics: only a row with a non-null project
scope captured from an authorization-checked source may be an example. For V2, CC rows may use the
already resolved `cc_project_dirname`; NS-only and `unrelated` rows with no route-agnostic project
identity remain eligible for aggregate fitting but **never** for worked examples. Injection must
revalidate the current user and resolved project, fail closed, delimit examples as untrusted data,
and cap/redact their content. This preserves project scoping without inventing an NS project join.

### V2 task completion contracts

#### V2-T0 — Fixtures are staged, executable, valid, and network-free

- Remove all module-level imports of types that do not exist until Tasks 1/2/7/10. Put each
  consumer fixture beside its owning task or import lazily only after that dependency lands.
- Every `ChatSession` fixture uses a real user. Delete the invalid `user=None` and userless session
  constructions. Shared fixture interfaces must match what is actually defined; paid fixtures stay
  solely inside the opt-in live module.
- Collection is only a collection gate. Each fixture gets a semantic smoke test when its owning
  type lands, including a default-lane network-deny assertion.

**DONE only if:** collection has a recorded nonzero count and zero errors, then every currently
available fixture smoke executes successfully. A fixture body that raises must make the task red.

#### V2-T1 — Full turn snapshot model and safe migrations

- Replace the nonexistent `align_charset_for_fk` recipe with a reviewed, table-parameterized
  migration operation derived from `nextseek_api/migrations/_cc_transcript_heal.py`; translate no table-specific constant
  blindly. MySQL DDL uses the proven non-atomic migration pattern where required.
- The ledger implements the complete durable contract above, not the metadata-only sketch.
- Deliberately update/replace `test_no_new_migrations` in the same task with a guard that permits
  the enumerated Plan 018 migrations and still fails on any unplanned migration.
- Apply forward and reverse migrations only against the identified test DB; introspect exact FK,
  unique constraint, indexes, charset/collation, cascade behavior, and migration ledger. Test fresh
  and seed-derived schema shapes.

**DONE only if:** applied-schema introspection, not `sqlmigrate` text alone, proves the contract.
Rollback after data exists requires a snapshot and maintainer approval; `migrate ... 0009` is not a
routine code rollback and must never silently drop evaluation data.

#### V2-T2 — Collision translation and atomic persistence primitive

- `record_turn` accepts the complete immutable snapshot. It translates only the named duplicate
  `(session, turn_number)` constraint to `LedgerCollision`; invalid FK and every other integrity
  error propagate unchanged.
- Test outer-transaction rollback, invalid FK, sequential duplicate, and a real concurrent race.
  A row-lock/recompute-and-retry policy must preserve exactly one matching envelope+ledger pair per
  completed turn (two concurrent completions produce two distinct pairs); a swallowed collision
  that leaves only the envelope is forbidden.

#### V2-T3 — Compiled BAML/Python family contract

- Do not redeclare the existing BAML `class TaskFamily` — that type already exists in
  `dmac_assistant/baml_src/router.baml` and describes a route's advertised family, which is a
  different thing. Use a distinct `ClassifiedFamily` enum marked `@@dynamic`; its effective members
  come from every family key in the exact runtime corpus snapshot. Add it to the object returned by
  the **classification** function (see V3-B), not the routing function. No task may hardcode a
  family count, literal member list, or include/exclude layer.
- Regenerate every runtime BAML client through the repository's pinned generation path and include
  generated artifacts in the task diff. Update fallback construction, Python `RouteDecision`, and
  `route_decided` telemetry. `unrelated` has explicit family state `None/unrelated`.
- Compile/parse the BAML, assert the effective runtime enum set **equals every family key** read from
  the exact corpus snapshot — drift in either direction fails, so a family added at the source but
  missing from the effective TypeBuilder enum is caught — assert per V3-B that classification and routing are
  distinct seams and that exactly one LLM classification call occurs per turn, and exercise the
  generated typed result through the public `decide` seam. A comment/string-only edit cannot pass.

#### V2-T4 — VOID: no deterministic family fallback (V7-C)

The maintainer never approved deterministic phrase/keyword family inference. Do not create a family
labeller for heuristic or forced routes. Preserve the existing `_heuristic` source hash, routing
patterns, precedence, and route result exactly; its only output provenance is `route_source`.

- A forced paired corpus arm obtains its already-known family from the corpus/canonical row:
  `family_source="corpus"`, `route_source="forced"`.
- A successful LLM classification records `family_source="baml"`; the later routing source is
  recorded independently.
- If classification did not run or failed, record `task_family=None` and `family_source=None`.
  Routing may still fail safely to the legacy router/heuristic, but that turn supplies no
  family-specific evidence.
- `unrelated` is an explicit classifier outcome with no family; it retains the classification
  provenance and never incurs a downstream route call.

Any implementation that mines corpus phrases, embeds a keyword-family table, converts the existing
route heuristic into a classifier, or writes `family_source="forced"` violates this decision.

#### V2-T5 — Real writers, all terminal paths, one atomic row

- Replace the direct-`record_turn` factories: they are helper tests, not writer tests. Exercise the
  actual service/orchestrator persistence seams for NS success/error, CC success and every early
  error return, `unrelated`, forced, heuristic, BAML, timeout, and collision.
- Inventory every production `append_turn`, CC completion, non-answer, and early-return terminal
  path in a checked artifact. Refactor persistence at the Django boundary so the envelope update
  and complete ledger snapshot occur under one `transaction.atomic()` with session row locking;
  do not scatter partial ledger calls across twelve orchestrator branches.
- Force either side of the write to fail and assert neither commits. Assert exactly one ledger row
  and matching turn identity for every covered terminal turn. Assert `route_decided` exposes family
  state/source without query or reasoning text leakage.

#### V2-T6 — Complete, hash-manifested, behavior-preserving port

- Build a transitive manifest at source commit `dcca50c`: tools,
  `tools/e2e/functional_evaluator_models.py` (dev-box convenience copy:
  `_plan018-refs/port-source/functional_evaluator_models.py`),
  evaluator BAML/generated-client inputs, all four fit packages, configs, templates, resources,
  wrappers/entrypoints, and the upstream free tests/golden artifacts needed for parity. Each entry
  records source path/hash, destination path/hash, and the only allowed import/package rewrite.
- A verifier rejects missing/extra files and semantic diffs outside the allowlist. Run the upstream
  free corpus against source and destination and compare structured outputs/thresholds/bands.
- Use a standalone eval-image dependency manifest/lock that has no root editable path dependency
  on `chat_nextseek` or `dmac_assistant`; pin the HiBayes git commit immutably. Generate BAML in the
  image, copy all runtime package data, define a real entrypoint, build with the external checkout
  absent, and execute import plus deterministic fit smoke tests inside the image.
- Replace two-string grep with parsed import tracing/AST allowlists and a filesystem inspection for
  host paths. Phase 2 cannot close on a stub package or build-only proof.

#### V2-T7 — Versioned full export; no lossy timestamp watermark

- Export the complete durable contract in stable `(ledger_pk)` order and deterministic batches.
  Remove the lone `created_at__gt` watermark as the correctness oracle. Incrementality is driven by
  the turn-scoped content fingerprint/cache; a full scan may be optimized only with a cursor whose
  equal-time, crash/retry, late-write, and no-loss semantics are tested.
- Map successful, failed, timed-out, artifact-bearing, CC, NS, forced, and unrelated turns into the
  exact vendored evaluator inputs. Missing required source facts fail/record `not_assessable`; they
  are never defaulted to success.

#### V2-T8 — Content-bound cache and bounded failure retry

- Query cache by `(turn, fingerprint)`, with the complete fingerprint defined above. Persist
  attempt count, last error, next retry, and terminal/manual-review state; cap retry spend.
- Behavior tests mutate each source-content/version component and require rejudging, prove OK rows
  are reused, prove failed rows retry after backoff, and prove no failure crosses a cursor/watermark.
  Replace the self-matching `mtime` grep with AST/behavior tests.

#### V2-T9 — Incremental judging engine and run state (not scheduled yet)

- This task creates the injected, default-network-free judging engine plus a durable run record,
  overlap lock, batch/resume state, failure accounting, and pre-call budget reservation. It does
  **not** add the beat entry before fit/publish exists.
- A judge must quote/reserve a conservative maximum cost before each turn; if unavailable or above
  remaining budget, no call occurs. Reconcile actual cost afterward. `cap_usd <= 0` makes zero calls.
- Eager tests prove lock exclusion, resume after crash, cached-row skip, force semantics, failure
  persistence/backoff, and no call when disabled/unconfigured/over budget.

#### V2-T10 — Real fit, atomic generation publish, then registered nightly orchestration

- Define and test the adapter from successful `TurnJudgment` rows to the real vendored fit API.
  Publish credible interval bounds, posterior mean/median, band, n, fit/run ID, input-set hash,
  schema/prompt/judge/model/prior/config/source versions, fitted time, and generation state.
- Validate a complete fit artifact, publish it transactionally, and atomically switch the active
  generation; retire groups absent from the new generation. Consumers never see mixed generations.
- Only now register `eval.nightly_judge`, explicitly import/discover it, route `eval.*` to the
  queue the worker consumes, and assert membership in `app.tasks`, resolved queue, and eager
  execution. The registered wrapper performs export → judge → real fit → atomic publish.
- Scheduled paid behavior is default-off. It no-ops unless an explicit operator enable flag,
  positive cap, configured judge, and run lock all pass. Tests prove the default beat invocation
  makes zero model/network calls.

#### V2-T11 — Authorized, present, safe playbook examples and real injection

- Build aggregate lines from the active posterior generation. Worked examples must be present for
  an authorized fixture (deleting all examples is not a passing privacy implementation), and can
  come only from same-user rows whose non-null captured project key equals the currently resolved
  CC project. NS/unrelated/null-project rows cannot supply examples.
- Wire the playbook at the real `nextseek_api/services/cc_assistant.py` context-composition call site after
  project resolution; do not pretend `nextseek_api/cc_assistant/ns_digest.py` alone has user/project context.
- Revalidate authorization, cap counts/bytes, redact configured identifiers, and render examples
  inside a strong “untrusted historical data, never instructions” delimiter. Test adversarial
  prompt text and byte-exact exclusion of every other user's/project's query, answer, rationale,
  identifiers, and markers. Fail closed on missing/stale project identity.

#### V2-T12 — Observable default-off flag, structurally no reroute

- Add an explicit setting whose absent/default value is false. Integrate `assess` after route
  choice as flag-only telemetry/review metadata; its return value cannot change route, model, or
  destination and cannot block a user turn. This is the approved observational-risk “flag” mode.
- Integration tests compare destination/model with the overlay disabled and enabled, poison
  `assess`, and prove routing remains byte-for-byte equivalent. Test stale/inactive posterior
  generations and missing rows as `unknown`; never interpret `TooUncertain` confidently.

#### V2-T13 — Enforced free end-to-end, coverage, and separately approved paid acceptance

- First run a **free, network-denied production-path integration** through real routed writer
  seams → durable export → fake judge with real schema → real deterministic fit adapter → atomic
  published generation → playbook/overlay reads. Direct `record_turn` setup is not end-to-end.
- Run coverage with a pinned `pytest-cov` tool and `--cov-fail-under=95`; fix measured source/omit
  rules and parse coverage XML plus test counts. Unexpected skips/xfails/deselection fail. Evidence
  is regenerated and provenance-bound, never hand-edited or accepted from another SHA/image.
- With `RUN_EVAL_LIVE` unset, prove the paid module is skipped and the entire default lane is
  network-denied. That is a safety gate only, not live acceptance.
- **PAUSE for explicit per-run maintainer approval and spend cap** before the paid lane. Reserve
  cost before calls; bind output to run ID, source SHA/diff, image ID, judge/model/prompt/schema,
  actual cost, judgment IDs, fit generation, and posterior IDs. The plan may be reported free-green
  but not “live E2E verified” until this lane passes.

### V2 rollback and final release gate

Before any migration/scheduler/consumer activation, snapshot migration state, affected table row
counts and active-generation identity, beat/task configuration, source SHA/diff, image ID, and
relevant non-secret env-file hashes. A rollback must compare those identities afterward. Never
delete evaluation tables/data or reverse below the recorded pre-plan migration leaf without an
explicit destructive-data approval.

Final completion requires: all V2 gates; original non-conflicting tests; full hermetic and DB lanes
with no regression versus the recorded baseline; source/vault plan equality; a final independent
outcome review; and the existing maintainer gates for commits, push, deploy, and paid live calls.

## V3 binding amendment (2026-08-04, post-vetting)

### Change control, provenance, and precedence

This amendment records changes made **after** Codex `$plan-vetting` returned CLEAN on 2026-08-04.
The artifact vetted at that moment was SHA-256
`df084e2c7f89c590d77439a82da0116de06932b5a05a8923fd3a4c59a280db50`. Every V3 item below is a
deliberate change to that artifact and therefore **invalidates that vetting pass**; the plan must be
re-vetted before execution.

Provenance for these changes:

- Maintainer rulings recorded 2026-08-04: the task-family set was never intended to be fixed; the
  forced-route comparison becomes the entrypoint for the Bayesian router; the router is to be split
  into classification and routing; the model-architecture freeze is lifted for the paired design
  while band thresholds stay frozen.
- Charlie Demurjian's `_plan018-refs/corpus/corpus.json` v2, adopted 2026-08-04 from `chat_nextseek/e2e/catalog.json`
  (`catalog_sha256` `e7895264604c6fbc860746f8e0e7cae422ba29073e3b34bcfb4582b0d3baac29`), together
  with his seed-6c rerun review notes.
- `_plan018-refs/OPS-TESTING-HARNESSES.md` section 5, which records four competing family vocabularies
  (22 / 8 / 17 / 11) and states that no source of truth has been declared.

Rules of precedence:

1. V3 supersedes V2 where they conflict. V2 continues to supersede the original task prose.
2. Every V2 completion contract not amended here remains mandatory and unchanged.
3. No V3 item grants any commit, push, deploy, live-DB, paid/model, or destructive-rollback
   authorisation. All existing maintainer gates survive intact.

### V3-A — The task-family set is not frozen (applied inline)

Applied directly in V2-T3 and Task 3. V7 voids V2-T4 and Task 4's family-labelling design; the
existing heuristic remains only a route-safety fallback and never becomes a classifier.

No task may hardcode a family count or a literal member list. The classifier's effective runtime
enum is built from every key in the latest compatible corpus's `families` object, and a test proves
equality in both directions. Adding a declared family therefore needs no BAML source rewrite or
separate label-selection decision. V6-D records the mechanism as `@@dynamic` plus `TypeBuilder`.

**Rationale:** a routing system whose purpose is to absorb new capabilities cannot carry a fixed
label set. Any count appearing anywhere in this plan is an observation about one file at one moment,
never a contract (V6-C).

### V3-B — Split the router into classification and routing

**This task lands before original Task 0.** It is a prerequisite for the Bayesian routing work, and
it is independently deployable and verifiable on its own.

The single router call is separated into two functions with distinct responsibilities:

1. **Classification** — maps a user query to exactly one declared task family, or to `unrelated`.
   This function is **permanent**. It is the spend gate: `unrelated` must be rejected here so an
   out-of-scope request never reaches a route and never incurs downstream cost. It remains an LLM
   call.
2. **Routing** — given the classified query, selects the destination (NExtSEEK path or
   Container-CC), the model, and related execution parameters. This is the function a Bayesian
   router will later replace, and it **must survive as a fallback** rather than being deleted.

V2-T3's single-call requirement is superseded only to this extent: exactly one **LLM classification**
call occurs per turn. Separating the responsibilities must not add a second LLM call on the
steady-state path.

**Completion contract — DONE only if:**

- Classification and routing are separate, independently testable seams, and neither reaches into
  the other's internals.
- `unrelated` is rejected at classification, proven by a test asserting that no routing decision is
  produced and no downstream call occurs for an out-of-scope query.
- Routing behaviour is unchanged for in-scope queries: a differential test compares destination and
  selected model before and after the split across the corpus's per-family `variants` (V6-B) and
  shows equivalent routing decisions.
- The existing `_heuristic` source hash, routing patterns and their precedence are untouched. It
  remains a routing fallback only and must not infer or fabricate `task_family` (V7-C).
- Per-turn LLM call count on the steady-state path is asserted not to have increased.
- Deployed to dev and verified live that routing still works — **behind the plan's existing
  per-action maintainer gates**. This task grants no standing deploy or live-call authorisation.

**Rollback:** revert the split commit, restoring the pre-split single-call router unchanged.

### V3-C — Forced-route paired baseline

**Purpose.** Establish, per task family, the baseline probability that a question reaches a desired
outcome on the NExtSEEK path versus the Container-CC path, by running the same corpus question down
both routes. Because the question is held fixed and only the route varies, the route/question
confound present in production traffic is removed by construction. This is what makes the cross-route
comparison legitimate and what makes propensity weighting unnecessary.

**Producer status — required, not present.** Full-scope ref inspection found no `nessie_tests`
harness or `manage.py nessie` command on the execution base. The reviewed
`origin/dev-v3-merge@0aca6fc` generation contains an ordinary-Nessie harness only. V4-0 and V4-2
therefore make the reviewed port and a new NExtSEEK-owned forced paired producer explicit
prerequisites; an ordinary manifest cannot be relabelled as paired output.

**No field-level schema is pinned here, deliberately.** The ingestion models, the converter that
builds judge input, and the Bayesian row are all downstream of the harness output schema, and that
schema is still moving. Three generations were observed on 2026-08-04 and they disagree: the
committed `output-skill` example, the running dev container, and the `origin/dev-v3-merge` tip differ
in the entry status vocabulary, in whether per-turn route sources are recorded, in whether an
unevaluable criterion is flagged, and in outage handling. Writing field names into this plan before
that settles would produce a contract that executes against nothing.

A task deliverable defines the concrete pydantic models against the real emitted artifact once the
forced-route mode exists. Until then this plan states only the **facts each paired row must carry**:

- question identity and its task family;
- the **arm** — which route produced this observation, and that the route was imposed rather than
  chosen;
- the **judge outcome** for that arm, with its artifact status and failure mode;
- latency, and cost when available;
- the harness's own criteria evidence, retained for diagnosing why a losing arm lost.

**The row follows the harness and the goal, not the legacy exporter.** The standalone HiBayes tool's
existing row and manifest shapes are a **reference, not a constraint**. Where the harness emits
evidence that is genuinely useful for deciding which route is better, that evidence is carried into
the models rather than discarded to fit the older shapes.

**Cost is optional data, not an implicit utility rule.** Computing true cost on the NExtSEEK path is
non-trivial. The ingestion schema carries nullable `cost` and measured latency, but V4-4 must state
whether and how either enters operational utility. A null cost is unknown, never zero and never a
failure; populated cost does not automatically alter the winner without the approved rule.

**Prospective paired-run output shape, not an implemented claim.** The names below record V3's
proposed interface only. Full-scope verification found no paired runner or these Bayesian types on
any fetched ref or in the deployment. V4-2 may revise the concrete strict schema after the producer
is implemented and reviewed; the invariants and provenance requirements remain mandatory.

**Read `bayes_manifest.json`, never `manifest.json`.** The paired run writes
`<out_dir>/bayes_manifest.json`. A normal unpaired run writes `manifest.json`. Pointing a reader at
the wrong filename **silently returns an empty result instead of erroring** — it does not raise. The
ingestion step must therefore assert the paired filename explicitly and must fail loudly on an absent
or empty `pairs` list, rather than proceeding to fit nothing and publishing a confident posterior
built on zero observations.

```
BayesManifest
  run_meta: dict
  pairs:    list[BayesPair]

BayesPair
  id:               str                        # corpus variant id
  family:           str                        # harness snake_case family
  hibayes_subtype:  str | None
  ns:               NessieManifestEntry | None
  cc:               NessieManifestEntry | None  # either arm may be None mid-run
```

`run_meta` carries `mode`, `arms`, `corpus_fingerprint`, `git_sha`, `base_url`, `selected_ids`,
`max_usd`, `resumed`, and `superseded_runs` — a flattened oldest-first list of prior `run_meta`
blocks, so a run completed across a rebuild records every build that contributed instead of asserting
a single one.

**These paired properties are acceptance requirements, not current facts.** Pairing must be
structural: one validated pair carries two independently executed arms under one immutable question
identity, and independent runs may never be stitched together. Forced status and actual per-turn
route provenance must be machine-verifiable. Ingestion may observe either arm as pending, but a pair
does not enter the fit until both validated arms exist, and a missing arm is never a failed arm.

Each arm is a full harness entry carrying `status` (`passed` / `failed` / `skipped` / `error` /
`xpass` / `no_assertions`), `route`, `engine`, `route_source`, `cost`, `elapsed_s`,
`failed_criteria`, `observations`, `poll_errors`, `reason`, `expected_fail`, and `outage`.

**Target export shape — one file per arm, never combined.** The upstream consistency check requires
the model-identity column to be uniform within a file, and the two arms do not share it, so a single
merged CSV is invalid by construction. The column set is locked verbatim upstream and must not be
re-derived here. A Stage B input file, a separate exclusions file, and the collector's own output
accompany the per-arm files. The arm is carried by the image/identity column, valued per route.

**Both taxonomies travel together and do not map 1:1.** The harness family (snake_case) and the
HiBayes subtype label (CamelCase) are distinct fields carried side by side. Neither may be derived
from the other, and the fit must not silently collapse them into one grouping level.

**Live-run gate.** Nothing had run live when this was written (**corrected by V8-K** — a smoke run
and a full paired run have since been executed by the harness author), and forcing a route is
admin-gated, so a real paired run requires a staff account. V3-C therefore cannot be validated
against real data on those runs' basis alone. Do not fabricate a paired dataset from two independent unpaired runs, and do not treat a
dry-run or partial manifest as a baseline.

**The judge determines success — this is the core of the comparison.**

The harness's declarative criteria assert that the *mechanism* behaved: which route was taken, that
the graph responded, that a plan had the expected shape. They cannot establish that an **answer was
correct**, and the corpus's own `family_floor` note records that the large majority of variants
assert only plan shape or plumbing. A router fitted on that evidence alone would optimise for
"completed without erroring", not for "answered the user's question". Correctness comes from the BAML
judge, and "better" means *did it succeed*, not *did it avoid erroring* and not *was it fast*.

- **Each arm is judged independently.** Every question-plus-outcome is submitted on its own, NS arm
  and CC arm alike. The judge **produces** a functional outcome for that one arm; it never compares
  arms and is never shown the other arm's answer. Comparison is the model's job, downstream.
- **Three sequential calls per query, aggregated per field** (locked DD-44): plurality with a
  failure-partition tie-break for the outcome; majority with a severity tie-break for the primary
  issue; median for usefulness score; maximum for review priority; OR for needs-human-review; and
  the rationale from the first call matching the aggregate outcome. DD-44 has no confidence field.
- **An unassessable case is recorded, not scored.** The judge's abstention value is an honest result:
  it is excluded from the fit rather than silently counted as either a pass or a failure.
- **No model or provider is named by this plan.** The judge is referenced only through its BAML client
  indirection. BAML is provider-agnostic, so which model and which provider serve the judge is a
  configuration choice that may change downstream **without amending this plan**, and no task may pin
  a model ID or a provider. Model identity is *recorded* as run provenance and *included* in the
  judgment fingerprint, so swapping the judge invalidates cached verdicts instead of blending verdicts
  from two different models into one posterior.

**Spend gate — computed at run time, never written down as a constant, and counted in TURNS.**

Two independent costs must both be estimated before approval, and they are not the same quantity:

1. **Harness run spend.** A row is a *variant*; the spend behind it is *turns*. Multi-turn variants
   cost several turns each, so estimating from row count instead of turn count **understates the bill
   by roughly a quarter** on the current selection. The skew concentrates in the multi-turn families,
   whose per-row cost cells consequently look disproportionately large — that is correct arithmetic,
   not a defect to be normalised away. Estimate from `turns × arms`, never from `rows × arms`.
2. **Judge spend.** Three calls per judged unit (DD-44), across both arms.

Because the judge client is swappable, this plan states **no dollar figure**: any figure written here
would be true only of whichever model happened to be configured the day it was written. Before any
paid run, both estimates are computed against the configured clients' current published rates, summed,
and surfaced for approval under the plan's existing paid gate. The run proceeds only after that number
has been shown and approved. `run_meta.max_usd` records the cap the run was given.

**Idempotency is mandatory.** Extend the V2-T8 judgment cache so the arm forms part of its key. A
crashed, interrupted or partial run must resume from where it stopped rather than re-judging from the
start, and a re-run in which an arm's answer is unchanged must incur no new judge calls. Re-running
everything after a harness failure is unacceptable on both time and cost grounds.

**Measure inter-call disagreement before committing to three calls.** DD-44's aggregation only buys
something if the three calls sometimes differ, and nothing in the client configuration guarantees
that they will. Measure the disagreement rate on a small sample first. If the three calls agree
essentially always, the vote is decorative and the call count can drop to one, cutting judge spend by
two thirds before any full run is paid for.

**Validity requirements.**

- A case whose verdict is `policy` — the product behaved correctly and the assertion was wrong —
  must **not** count as a product failure. Verdicts are human annotations delivered alongside the run
  keyed by case `id`; the observed vocabulary is `pass`, `real`, `masked`, `policy`, `drift`,
  `notrun`, severity-ordered `real < masked < policy < drift < notrun < pass`.
- `masked` and `notrun` cases are excluded from the fit rather than scored.
- A case that evaluated **zero criteria** supplies no evidence and is excluded from the fit rather
  than scored. The harness already models this as a first-class status and already treats it as a
  real failure rather than a pass; ingestion must honour that and must never let such a case reach
  the fit as a success.
- A case whose criteria include **unevaluable** ones cannot be counted as a clean pass. An
  unevaluable criterion is recorded as having passed, which is indistinguishable from a genuine pass
  unless the flag is inspected. Ingestion must inspect it; a case carrying unevaluable criteria is
  either judged on the remaining evidence or excluded, never scored green by default.
- **Provider outages are excluded, never scored.** An arm that failed because the upstream model
  provider was down carries an outage marker and must be dropped from the fit. Emitting it as an
  error instead teaches the posterior that provider downtime is an incapability of that route, which
  is a straightforward way to poison the comparison with infrastructure noise.
- **A missing arm is not a failed arm.** Either arm may be `None` while a run is in flight. A pair
  with one arm absent contributes no paired observation and is skipped, never scored as a loss for
  the missing side.
- The corpus is a versioned, intentionally evolving input. A new run resolves the latest
  schema-compatible corpus available from the selected harness source. Its exact identity — recorded
  in `run_meta.corpus_fingerprint` alongside `git_sha` — travels with every fitted generation.
  Content drift does not invalidate the plan or unrelated cached judgments: cache identity is
  per-case and content-addressed. A partially completed/resumed run must retain its original corpus
  fingerprint and selected IDs; it cannot mix corpus versions within one run (**superseded in part
  by V8-B**, which permits reuse of unchanged arms across corpus versions under an execution cache).

**Model architecture is unfrozen for this design** (see Freeze boundaries); band thresholds are not.

**DONE only if:** a paired run over the corpus is ingested with both arms present per question; every
ingested arm carries a judge-produced functional outcome, aggregated per DD-44; the expected-cost
calculation was computed from the configured client and approved before any paid call; the cache
proved idempotent by resuming an interrupted run without re-judging completed arms; the fit produces
per-family posteriors carrying corpus identity, run identity, code commit and judge model identity;
evidence-free and unevaluable cases are provably excluded rather than scored; and a fresh reader can
reproduce the baseline from the recorded artifacts alone.

### V3-D — Posterior-driven routing with LLM fallback

**Consumers of the posterior route.** The routing function (V3-B) consults the published posterior
for the classified family and selects the route it favours.

**Fallback rule.** Routing falls back to the LLM routing function whenever the posterior is not
decisive — specifically whenever it lands in the existing `TooUncertain` band, when no posterior
exists for that family yet, or when the active generation is stale. This deliberately reuses the
already-vetted band thresholds, which remain frozen, rather than introducing a new hand-picked
margin. The existing instruction never to interpret `TooUncertain` confidently is what this
implements.

**Safety.** Posterior-driven routing is behind an explicit setting whose absent or default value is
false, consistent with V2-T12. With the flag off, routing is byte-for-byte identical to the
pre-V3-D behaviour, and a poisoned or missing posterior can never block a user turn — it falls back.

**DONE only if:** with the flag disabled, destination and model are provably unchanged; with it
enabled, a decisive posterior changes the route and a `TooUncertain` posterior demonstrably falls
back to the LLM routing function; a missing, stale or malformed posterior falls back rather than
failing the turn; and the fallback path is exercised by tests rather than only reasoned about.

## V4 full-scope replan and hardening amendment (2026-08-04)

### Why V4 is required

The V3 plan materially changed the system after the V2 review. The first V3 review pass did not
inspect every NExtSEEK generation or the deployment and is void. V4 incorporates the replacement
full-scope evidence and reviews:

- `_plan018-refs/reviews/PLAN018-V3-ALL-VERSIONS-INVENTORY-2026-08-04.md`
- `_plan018-refs/reviews/PLAN018-V3-DEPLOYED-VERSION-INVENTORY-2026-08-04.md`
- `_plan018-refs/reviews/PLAN018-V3-FULLSCOPE-ADVERSARIAL-RISK-REVIEW-2026-08-04.md`
- `_plan018-refs/reviews/PLAN018-V3-FULLSCOPE-GAMEABILITY-REVIEW-2026-08-04.md`
- `_plan018-refs/reviews/PLAN018-V3-FULLSCOPE-CLAIM-VERIFICATION-2026-08-04.md`

Those files are private maintainer evidence and are named, not reproduced, in this public plan.
The exact pre-V4 copy is preserved as
`docs/superpowers/plans/2026-07-31-hibayes-eval-routing.md.bak-pre-plan-vetting-v3-20260804T163615-0400.md`
(SHA-256 `4bac691e0449d34ac7ae7645378449d13ae99752d1f78374c77f14f56a195f4d`).

The replacement review established these facts:

1. The execution base is `origin/dev@dfbccaf89010c468bdb1b9eba3d04f050fd7cb81`, not the V2
   base named above. It contains the V3 plan but no ordinary or Bayesian `nessie_tests` producer.
2. `origin/dev-v3-merge@0aca6fc` contains the most complete reviewed ordinary-Nessie harness, but
   it does not produce forced paired arms, a `BayesManifest`, `BayesPair`, `bayes_manifest.json`,
   posterior generations, or posterior-driven routes. It is a porting source, not a satisfied
   prerequisite.
3. Other fetched generations have different 8-, 10-, or 15-family taxonomies. No fetched ref
   implements the V3 Bayesian pipeline. A branch name, filename, fixture, or manifest label is not
   evidence that it does.
4. The deployed service-account tree is a dirty `dev-v3-merge` generation at `04a20bf5`, and the
   running image matches its sampled ordinary-Nessie files. It has an older/intermediate ordinary
   schema and no Bayesian symbols. Both are evidence only and must never be used as source,
   modified, or treated as the implementation/test harness.
5. The standalone DD-44 evaluator has no confidence field. It aggregates `usefulness_score` by
   median, `review_priority` by maximum, `needs_human_review` by OR, `outcome` by plurality with a
   failure-partition tie-break, `primary_issue` by majority with a severity tie-break, and
   `rationale` from the first call matching the aggregate outcome. Every V2/V3 reference to a
   max-of-three confidence value is void.
6. There is no current migration-number collision between `dfbccaf` and `0aca6fc`; both currently
   end at `0009`. A future port can still create a collision and must run the preflight below.

The V3 architecture reverses portions of the older design (forced dual execution, separate
classification and routing, and cross-route posterior selection). Those choices are binding here
only to the extent recorded in V3 and this V4 amendment. The older design must not be used to
silently fill the contracts that V3 left undefined.

### V4 precedence and execution order

1. V4 controls whenever it conflicts with V3, V2, an original task body, a code sketch, or a DONE
   clause. V2 safety, privacy, paid-call, provenance, and per-action approval gates survive unless
   V4 explicitly strengthens them.
2. Original Tasks 0–13 are retained as historical implementation decomposition. They must be
   resequenced under V4-0 through V4-9. No original task may start merely because its old local
   prerequisites appear satisfied.
3. No product-code commit, push, deployment, live mutation, paid call, schedule activation,
   posterior activation, or destructive rollback is authorized by this document or its vetting.
4. A STOP gate requires a recorded maintainer decision. An implementation agent may not invent a
   taxonomy crosswalk, statistical threshold, spend amount, deployment target, or rollback target.
5. A DONE claim requires executable evidence from the recorded source worktree. Hand-authored
   fixtures, mocks of the unit under proof, filename greps, skipped tests, copied answers, or
   assertions against the deployed tree cannot satisfy a V4 gate.

### V4-0 — Exact base, port provenance, and isolated harness

- [ ] Fetch immediately before execution and prove that the implementation starts from the exact
  approved `origin/dev` SHA. If it differs from `dfbccaf89010c468bdb1b9eba3d04f050fd7cb81`, stop
  and repeat the all-file drift review; do not merge by assumption.
- [ ] Create an implementation worktree only after execution authorization. Record base SHA,
  dirty-diff hash, submodule state, dependency locks, and baseline test result.
- [ ] Treat `origin/dev-v3-merge@0aca6fc` as the only currently reviewed ordinary-Nessie porting
  source. Enumerate every selected commit/file and review its diff onto the exact base. Do not copy
  from the dirty deployed clone or running container. A newer source must receive the same review.
- [ ] Before creating migrations, compute the graph on the merged source. Renumber/depend on the
  actual leaf rather than assuming `0010`; prove forward migration on an empty DB and a
  production-shaped disposable snapshot.
- [ ] Build a worktree-mounted, non-live harness and record source mount, image digest, test DB,
  settings, network, and exact argv. Refuse the live application DB and the deployed `/app` tree.
- [ ] Produce a reviewed file/interface ownership map before implementation. It must assign one
  canonical owner and explicit producer/consumer contracts for the ordinary port, forced paired
  runner, strict manifests, annotation sidecar, raw attempts, aggregate results, paired fitter,
  generation store/activation, classifier, selector, ledger/export, authorization/reservation, and
  every mirrored/generated file. The retained File Structure table below is historical and
  incomplete; it cannot authorize an unowned V4 surface.
- [ ] Run commands with pipe-failure preservation and machine-readable pass/fail/skip/xfail/
  deselection counts. Unexpected non-execution or a zero-test selection fails.

**V4-0 DONE:** the exact port set and its reviewed provenance are recorded, the isolated harness
can test the approved base, the pre-change suite passes there, and no deployed or paid resource was
used. The ordinary-harness port itself occurs only in V4-2 after V4-1 approval.

### V4-1 — Corpus taxonomy compatibility and common estimand decision (STOP)

The historical 7-, 8-, 10-, and 15-family schemes are not interchangeable with the current corpus.
For classification, however, V7-B removes the selection question: every family declared by the
latest compatible corpus is canonical. Before a posterior schema is authored, prepare a decision
artifact that contains:

- every source taxonomy with source SHA and file hash;
- the corpus-declared family IDs, schema version, descriptions, aliases, renames, splits, merges,
  and tombstones, without an include/exclude column or second label list;
- a total crosswalk from historical/online/ordinary-Nessie labels into the corpus-declared IDs;
- route feasibility and explicit common-support status for every canonical family;
- counts by source and route, plus unmapped/ambiguous rows that remain errors rather than being
  silently assigned; and
- migration/compatibility rules for stored observations and published generations.

The paired target is a within-question comparison on the exact corpus snapshot recorded for that
run: for family `f`, estimate the difference in desired-outcome probability between a genuinely
forced Container-CC arm and a genuinely forced NExtSEEK arm while preserving pair identity. This
does not freeze the corpus for future runs and does not select the practical-effect threshold,
precision requirement, minimum sample, or operational winner rule.

**STOP:** the maintainer must approve the crosswalk compatibility, common-support policy, and
estimand before V4-2. Label inclusion is not part of this STOP. A newly declared corpus family is
immediately a classifier label, but without support on both routes or adequate evidence it cannot
yield a comparative route claim and remains `TooUncertain`/fallback or is reported
route-conditionally.

### V4-2 — Own and prove the ordinary and paired producers

- [ ] Port the reviewed ordinary-Nessie harness as an explicit task with source-by-source tests.
  Its existing `manifest.json` remains an ordinary run artifact and must never be renamed and
  presented as Bayesian evidence.
- [ ] Implement a NExtSEEK-owned paired producer. It must execute the same immutable question
  identity exactly once per requested arm using two unique session/execution IDs and a server-side
  route override that cannot be undone by sticky-session or downstream router state.
- [ ] Record requested route, actual route, route source, question/corpus hashes, execution/session
  IDs, source SHA/diff hash, image digest, model/client identity, timestamps, status, and raw result
  references for both arms and for every turn in a multi-turn arm. Validate every per-turn route and
  route source against the forced arm; a final-summary label is not enough.
- [ ] Reject missing, duplicate, swapped, copied, same-session, or same-execution arms;
  requested/actual route mismatches on any turn; unapproved models; changed questions; sticky
  overrides; and partial pairs. A missing arm is pending/excluded, never a loss.
- [ ] Emit a non-empty, schema-versioned `bayes_manifest.json` only from real completed paired
  executions. Validate with strict models (`extra=forbid`), canonical hashes, referential integrity,
  and conservation counts. No hand-authored `BayesManifest` fixture may prove the producer.

**V4-2 DONE:** a verifier can replay a tiny free/fake corpus end to end and prove from independent
route traces that each arm actually traversed its declared route. Mocks may test failure handling
but cannot satisfy the route-enforcement acceptance test.

### V4-3 — Judgment attempts, exact DD-44 aggregation, and outcome conservation

- [ ] Keep DD-44 at three evaluator calls unless a separately approved quantitative pilot and plan
  amendment changes it. Store every immutable canonical raw request and response payload (or a
  durable content-addressed artifact reference that is retrieved and hash-verified during replay),
  including failed attempts, with stable attempt ID, provider request ID when available, input
  fingerprint, model/client, prompt/evaluator versions, start/end timestamps, retry linkage,
  token/cost facts, status/error class, and payload hashes. A hash without retrievable bytes is not
  replay evidence.
- [ ] Implement and golden-test the actual DD-44 aggregation stated in the verified facts above.
  Do not add, infer, or route on a nonexistent confidence output.
- [ ] Define one total outcome mapping from DD-44 output to desired/not-desired/excluded. Unknown
  enum values and incomplete aggregation fail closed; they are not coerced to success.
- [ ] Inspect criterion-level `unevaluable`, zero-criteria, provider-outage, missing-arm, timeout,
  and infrastructure statuses before scoring. Publish counts for each exclusion reason.
- [ ] Own human annotations with a strict, versioned, `extra=forbid` schema. Bind each annotation
  to run, corpus, case, question hash, arm/execution, annotator authority/provenance, vocabulary
  version, timestamp, and immutable content hash; reject orphan, duplicate, stale, unauthorized, or
  conflicting annotations. The complete observed vocabulary (`pass`, `real`, `masked`, `policy`,
  `drift`, `notrun`) and every future/unknown value must have an approved total mapping to scored,
  excluded-by-reason, pending, or hard failure. Sidecar labels never override judge output silently.
- [ ] Enforce the conservation equation for every run and each arm:
  `input = scored_desired + scored_not_desired + excluded_by_reason + pending`.
  Pair inclusion additionally requires two retained arms.
- [ ] The maintainer-approved analysis contract must set minimum retained pairs and differential-
  attrition bounds. Report sensitivity bounds; do not hide a route-specific exclusion imbalance.

**V4-3 DONE:** raw-attempt replay deterministically reproduces aggregates and totals, deliberate
failure/outage/unknown-status tests fail safely, and no excluded or pending case reaches the fit.

### V4-4 — Paired model and operational decision contract (STOP)

Before fitting real evidence, write and review a versioned statistical contract defining:

- the paired likelihood or other dependence-preserving model and exact priors;
- the canonical family/corpus/common-support population to which each claim applies;
- minimum retained pairs, precision/calibration requirements, practical-effect threshold, winner/
  tie/indecisive rules, multiplicity policy, and fallback behaviour;
- whether latency and nullable cost are excluded, reported as secondary outcomes, constrained, or
  incorporated into operational utility; the units, normalization, missing-cost treatment, and
  trade-off rule must be explicit, and unknown cost can never be imputed as zero by convenience;
- treatment of missingness, outages, unevaluable criteria, differential attrition, and sensitivity
  bounds;
- holdout or cross-validation discipline plus simulation-recovery tests for null, positive,
  negative, sparse, imbalanced, and adversarial cases; and
- compatibility/staleness keys for taxonomy, corpus, producer, judge, aggregation, model, client,
  and source versions.

`TooUncertain` is an uncertainty band, not by itself an operational definition of which route a
posterior “favours.” No implementation may hardcode a route for a favourable-looking fixture or
select a winner from posterior mean alone.

**STOP:** the maintainer must approve the numeric thresholds, priors, validation criteria, and
decision rule. **V4-4 DONE** only when blinded fixtures/simulations recover their generating cases,
negative controls do not publish a winner, and an independent recomputation matches the fit.

### V4-5 — Immutable generation publication and activation

- [ ] Publish immutable candidate generations containing complete input/attempt/aggregate hashes,
  all compatibility keys, counts/exclusions, fit diagnostics, decision results, source provenance,
  creation time, and parent generation. Candidate creation and active-generation selection are
  separate transactions and permissions.
- [ ] Validate schema, hashes, taxonomy/corpus compatibility, staleness, sample/precision gates,
  and decision status before activation. Partial publication, mutable overwrite, or filename-only
  validation fails closed.
- [ ] Use atomic compare-and-swap activation with an audit row and retain the prior active
  generation for rollback. Readers use one snapshotted generation per turn.
- [ ] Preserve the V2 observational risk overlay as telemetry-only (`may_reroute=false`). V4's
  comparative selector is the sole possible posterior reroute seam.

**V4-5 DONE:** corruption, mixed generations, incompatibility, failed validation, concurrent
activation, and rollback are exercised against the real store; none can produce a partial reader
view or silently activate a candidate.

### V4-6 — Router split and explicit call-count contract

The classifier and router must be structurally separate. The classifier schema returns only a
taxonomy version plus family/unrelated classification and classification provenance; it contains no
destination or model field. The route function alone may select destination/model.

| Mode | Classification calls | LLM routing calls | Result |
|---|---:|---:|---|
| feature flag off | 0 | 1 | destination/model byte-for-byte legacy |
| flag on, unrelated | 1 | 0 | existing unrelated behaviour |
| flag on, corpus/TypeBuilder unavailable or invalid before provider transport | 0 | 1 | no family; legacy routing/heuristic safety fallback |
| flag on, provider/parse/returned-label classification failure after an attempt | 1 attempted | 1 | no family; legacy routing/heuristic safety fallback |
| flag on, compatible decisive generation | 1 | 0 | route from approved V4 decision rule |
| flag on, missing/stale/malformed/incompatible/indecisive | 1 | 1 | legacy LLM route fallback |

The post-attempt failure and last rows are intentionally two-call paths with explicit cost/latency
consequences. Pre-transport validation failures make no classifier provider call. If the two-call
paths are unacceptable, implementation stops for a revised architecture; it may not fake separation
with two thin wrappers around one route-bearing output.

- [ ] Test every row with real generated clients and separate provider-transport call tracing,
  including model/destination equivalence when off and fallback on corpus/TypeBuilder/provider/
  parse/returned-label/storage/compatibility failures. Pre-transport failures must prove zero
  classifier provider calls; post-attempt failures must prove exactly one. Classification failure
  must not fabricate a family.
- [ ] A selected route must carry generation ID and decision provenance to the ledger. Failures
  never block a turn and never silently choose a posterior route.
- [ ] Prevent sticky-session and downstream stages from overriding the audited selection unless an
  explicit safety fallback records both attempted and actual routes.

### V4-7 — Separate experimental evidence from observational monitoring

Forced paired evidence and policy-selected online traffic are different data products with distinct
schemas, lineage, storage, fit entry points, and publication labels.

- [ ] The comparative route posterior may update only from approved forced/randomized paired
  evidence under the V4 estimand. Policy-selected online outcomes must not update it unless a
  separately reviewed assignment/causal-identification strategy is approved.
- [ ] Online observations may update route-conditional quality monitoring and playbook guidance.
  They must carry assignment propensity/policy/generation where available and display selection
  caveats. They cannot claim the counterfactual route would be better.
- [ ] Add a hard schema/type boundary and tests proving online rows cannot enter the paired fitter.
  Alert on policy drift, family mix, missingness, and route-specific outcome changes.

This replaces the goal/Stage 2 sentence that says real usage updates the comparative baseline.
Allowing the routing policy to train its own comparison from its selected traffic would create a
self-confirming feedback loop.

### V4-8 — Paid-run authorization, reservation, and resumability

No live/provider call occurs before a maintainer approves an immutable run manifest hash binding:
corpus and question IDs/hashes, taxonomy, requested pairs/turns/arms, three judge calls for every
eligible completed arm scheduled for judgment (not merely the unknown post-judgment retained set),
the maximum approved retry calls, clients/models and versions, retry policy, rate source/timestamp,
per-call and worst-case total estimates, hard caps, source SHA/diff hash, image digest, schemas,
output location, and approval expiry. Retained-arm count is a post-run reconciliation field only.

- [ ] Atomically reserve budget before each provider call. Concurrency cannot overspend; retries
  consume the same run's cap; exhaustion stops cleanly. Approval is for one exact hash and cannot
  be replayed for changed inputs or a later run.
- [ ] Resume by stable arm and attempt IDs. Completed calls are never repeated; partial work remains
  pending; cache keys bind the complete judge input and every relevant version.
- [ ] Reconcile estimates, reservations, provider usage, attempts, cache hits, exclusions, and
  outputs after the run. No schedule or default command can enter this lane.

Unit/integration tests use fake providers. The first real pilot and every larger paid run are
separate approval events and cannot be bundled with implementation authorization.

### V4-9 — Coverage, deployment, and recoverable rollback

- [ ] Replace Task 13's narrow coverage surface with all new/modified V4 producer, schema, force,
  judgment, aggregation, fitter, publication, activation, classifier, router, store, task, and
  authorization modules. Meet 95% line and branch coverage per critical module and overall.
- [ ] Record test collection and mutation/fault-injection results for route enforcement, exclusion
  mapping, conservation, DD-44 aggregation, paired dependence, winner rule, hash validation,
  activation, fallback, spend reservation, and evidence provenance. Unexpected skip/xfail/
  deselection or an unchanged mutation fails.
- [ ] Keep paid and deployed live E2E outside default/CI lanes. A free/fake end-to-end must cover
  question → two genuinely forced arms → three raw judgments → aggregate → fit → candidate →
  activation → routing/fallback before any paid pilot is proposed.
- [ ] Before enabling posterior routing, bind a deploy record to source SHA/diff, immutable image
  digest, migration set, settings, schedule state, flag state, active generation, smoke checks, and
  owner. The currently running image is baseline evidence, not a rollback image.
- [ ] Create and verify a recoverable prior image and a rollback procedure that restores image,
  active generation, schedule, flag, and compatible DB state. Current assumed git/image tags are
  absent; `git revert` and the existing unchecked rollback script are not sufficient. Do not use a
  destructive reverse migration without separate approval and a verified restore.

**V4-9 DONE:** an authorized disposable deployment demonstrates forward deploy and rollback to the
recorded prior state without data loss. Production enablement remains a separate explicit gate.

### V4 completion matrix

| Before this action | Required V4 gates |
|---|---|
| classifier split/runtime label implementation | V4-0 and recorded V7 decisions |
| ledger/evaluation/posterior implementation | V4-0 and approved V4-1 |
| paired fitting implementation | V4-2, V4-3, and approved V4-4 contract |
| comparative candidate publication | V4-0 through V4-5 |
| posterior-routing implementation | V4-0 through V4-7 |
| any provider call | approved immutable V4-8 manifest and reservation controls |
| any deployment or schedule change | V4-0 through V4-9 plus separate deployment approval |
| production posterior-routing enablement | all gates, recoverable image, rollback rehearsal, and separate enablement approval |

## V5 registry re-vet hardening amendment (2026-08-04)

### Change control, evidence identity, and precedence

This amendment applies the fresh all-instance inventory, independent adversarial/gameability
reviews, and independent claim verification below. An authorized verifier must resolve these exact
bytes; a matching basename is not evidence.

| Artifact | SHA-256 |
|---|---|
| `_plan018-refs/reviews/PLAN018-V4-REGISTRY-CURRENT-ALL-INSTANCES-INVENTORY-2026-08-04.md` | `79e69222343eaf19c19a5a2590d33e8fce96b350d43444e51a7f810d8544fd8b` |
| `_plan018-refs/reviews/PLAN018-V4-REGISTRY-ADVERSARIAL-REVET-2026-08-04.md` | `aff5217d370c66b9d8d13f2dd185532ca72d187feb6433be054e5037026f40f9` |
| `_plan018-refs/reviews/PLAN018-V4-REGISTRY-GAMEABILITY-REVET-2026-08-04.md` | `7b5dcfd0400b304edb18d664656c6034e8e03a75c343df283ac20e03f7ceaae5` |
| `_plan018-refs/reviews/PLAN018-V4-REGISTRY-CLAIM-VERIFICATION-2026-08-04.md` | `62ea9953a390822a126994a346afbf6c15093d32d87be829a4e25a103009b08a` |

The exact remotely retrieved pre-V5 registry plan was preserved beside the vault target before
editing and is retained outside the vault (so it is not misclassified as a registry asset) at
`/home/taishajo/work/state/plan018/registry-nextseek--plan-018-pre-v5-backup-20260804T224551-0400.md`
(SHA-256 `a4e9fc0a53442eaf098ec01c967d65dfe2e305edd6d6015a40d554015151711f`).
The later corrective source plan is separately preserved as
`docs/superpowers/plans/2026-07-31-hibayes-eval-routing.md.bak-pre-plan-vetting-v5-20260804T224551-0400.md`
(SHA-256 `14bff9975eb350888cf0b337545bf09af10c4d49e7d1743eb1474e9558728357`).

V5 supersedes conflicting V4/V3/V2/original prose. The corrected Task 0 topology and resolvable
references already incorporated from source commit `5e9c189` are part of V5 change control; that
commit is evidence, not a second authority. Final release requires source plan, registry vault
plan, approval subject, evidence manifest, and diff-review target to name one identical final
SHA-256. Local convenience copies under `_plan018-refs/` do not by themselves establish remote
availability; the evidence manifest must carry stable registry IDs/URIs, disclosure class, source
identity, and hashes.

### Corrected current facts and closed inventory universe

The fresh scan found 7-, 8-, 10-, and 15-family committed capability taxonomies. Every earlier
closed-list reference to only 8/10/15 is void. The ref count changed during the shared-workspace
review as parallel branches appeared without adding a new source generation, demonstrating that a
handwritten count is not a closure oracle.

Before the V4-1 decision artifact is signed, generate `evidence/plan018-source-universe.json` from:

- every named local branch, fetched remote-tracking ref, and tag after an immediately preceding
  fetch, including stored refs whose remote is no longer configured;
- every registered worktree HEAD plus a content hash of dirty/untracked taxonomy- or Plan-018-
  relevant bytes;
- the deployed clone HEAD/branch/dirty-diff hash and immutable running-image digest plus sampled
  source hashes, as evidence only;
- every capability, BAML taxonomy, corpus label, ordinary-Nessie label, annotation vocabulary,
  online schema, and generated mirror that can produce or consume a family ID.

The manifest records ref name, commit, path, Git blob/file SHA-256, family IDs/count, reachability,
and equivalence group. A validator recomputes the complete named-ref/worktree/deployment set and
fails on any unvisited identity, unclassified no-taxonomy state, historical label that cannot map
to a corpus family, duplicate canonical ID, or unresolved ambiguity. V4-1 approval binds the
crosswalk contract and compatibility rules, not immutable corpus contents. Ref/source drift forces
the inventory and compatibility validator to rerun and records a new evidence hash; it makes the
approval stale only when the schema, crosswalk semantics, or common-support/estimand contract is no
longer compatible. Ordinary corpus content changes and newly declared families do not stale the
decision. Aliases to an already inventoried commit remain explicit rows, not silent drops.

### V5-1 — Internal evaluation-data governance (STOP before schema implementation)

The durable ledger, raw judge payloads, artifacts, caches, playbook examples, generations, audit
rows, and backups are internal/private operational data and are retained by default. Plan 018 does
not invent a general user-initiated erasure right or require routine deletion/recomputation. Before
Task 1 or V4-3 schema work, the maintainer must approve a versioned governance artifact that defines:

- lawful/authorized collection purpose and notice/consent or other approved basis for each data
  class, including whether provider disclosure is permitted;
- minimization/redaction before persistence and provider submission, plus fields forbidden from
  every store and log;
- reader/writer/service-role matrix, project/user scoping, encryption/key scope, audit events, and
  incident owner;
- retention/expiry or deliberate indefinite retention for ledger, raw requests/responses, failures,
  cache, artifacts, generations, logs, backups, and provider-side copies;
- access revocation, offboarding, incident/legal-hold handling, and fail-closed behavior when
  governance metadata or reader authority is incomplete; and
- a security-incident path for credential/secret exfiltration: rotate/revoke the credential,
  quarantine the original incident evidence under restricted access, remove or redact literal
  secret bytes from ordinary stores and provider payloads where feasible, and create sanitized
  fake/canary regression variants that preserve the exploit shape without preserving a live secret.

The schema binds owner/project, data class, governance-policy version, retention state, and incident
restriction state without weakening replay for retained authorized data. Negative tests use marked
cross-user/project/revoked/quarantined/backup-restored fixtures and prove that unauthorized or
quarantined bytes cannot enter ordinary exports, provider payloads, caches, playbooks, or retrievable
artifacts. A restore rehearsal reapplies access restrictions before readers or workers start.

Forced comparative evidence and observational monitoring retain separate governance. If an actual
statistical input is withdrawn, only a generation that consumed that input is deactivated and
recomputed from remaining eligible inputs; an unrelated corpus-only comparative generation is not
invalidated. Redacting literal secret text while retaining an authorized sanitized observation,
family and outcome does not by itself withdraw the statistical row. **V5-1 DONE** requires approved
policy bytes/hash, an implemented access/retention/incident inventory covering every store, and
rerunnable negative evidence; completeness or replay tests cannot waive governance failures.

### V5-2 — Authenticated, action-scoped approval records

“Recorded maintainer approval” is not satisfiable by self-authored prose. Every V4-1/V4-4 decision,
paid run, candidate activation, deployment, production enablement, destructive rollback, and data-
governance ruling uses a strict approval record containing schema version, action class, exact
subject/evidence/plan hashes, environment/target, approver identity and verified authority source,
system-of-record ID or signature, issued/expiry times, one-use nonce where the action is consumable,
and action-specific caps.

This gate is deliberately split so it does not make V4-1 circular:

- **V5-2A — decision-record gate (pre-implementation).** Before a V4-1, V4-4, or V5-1 decision
  counts, an external/durable authority resolver must validate the strict record and immutable
  subject hashes. This is decision-artifact work, not product implementation. V5-2A DONE requires
  the canonical schema, independently resolvable authority/system-of-record identity, immutable
  record, and negative validation of copied, expired, revoked, wrong-action, wrong-environment,
  wrong-target, changed-hash, and unsigned/unresolvable records. It creates no product entry point
  and consumes no action.
- **V5-2B — consumable-action enforcement (implemented only with its owning later task).** The
  production entry point for each paid run, activation, deployment, enablement, or destructive
  rollback verifies the record and atomically consumes its nonce with the action/reservation.
  Each owning task adds its own append-only audit/replay row and negative tests for expiry,
  revocation, wrong action/environment/target/hash, concurrency, and replay. An implementation go
  can never authorize any of these action classes.

Decision-only approvals are immutable and superseded only by a new linked record. V5-2A is a
prerequisite only for the decision it authenticates. Each V5-2B verifier is a prerequisite only for
its corresponding consumable action; later nonexistent entry points do not block V4-1. **V5-2 is
fully DONE** only when V5-2A and every implemented V5-2B entry point are complete, but the acyclic
action table below governs when each partial gate is required.

| Before this event | Required approval enforcement |
|---|---|
| accept V4-1/V4-4/V5-1 decision | V5-2A record for that exact decision subject |
| first provider call | paid-run V5-2B + V4-8 DONE |
| candidate activation | activation V5-2B + V4-5 DONE |
| deployment | deployment V5-2B + V5-4/V4-9 prerequisites |
| production enablement | enablement V5-2B + all production gates |
| destructive rollback | rollback V5-2B + verified restore target |

### V5-3 — Binding producer, judgment, model, store, and selector oracles

The following clauses strengthen the corresponding V4 gates:

1. **V4-2 route proof.** “Independent route trace” means server-emitted route-selection and
   dispatch events observed through a read path not populated by the paired producer. Events bind
   request/question hash, arm, unique session/execution, every turn, requested/selected/actual
   route, route source, downstream override, and completion. A no-provider acceptance test crosses
   the real authenticated override/controller, orchestrator, sticky-session guard, and dispatch
   seam. Mutations for ignored override, same session/execution, copied/swapped arms, changed
   question, missing middle-turn event, sticky/downstream override, and requested/actual mismatch
   must make the gate red.
2. **V4-3 DD-44 oracle.** Exercise all three-vote enum combinations and boundary values for outcome
   plurality/failure tie-break, primary-issue majority/severity tie-break, usefulness median,
   priority maximum, review OR, and first-matching rationale. Assert intended permutation behavior,
   incomplete/duplicate/failed-attempt handling, future-enum fail-closed behavior, and replay from
   retrieved hash-verified raw bytes through the public aggregate and fitter-admission seams. A
   mutation report must kill each aggregation operator/tie-break mutation.
3. **V4-4 model oracle.** The approved contract pins an externally owned fixture generator,
   freeze/blinding procedure, hidden-until-freeze seeds, parameter grid, repetitions, calibration/
   precision/power/type-I/wrong-sign tolerances, near-boundary negatives, sparse/imbalanced/MNAR/
   adversarial cases, and an independently implemented recomputation. Fixtures selected after
   seeing fitter output cannot satisfy DONE.
4. **V4-5 real store.** The store is the production MySQL ORM/schema and reader entry point in the
   isolated harness, with named isolation level and transaction/CAS constraints. Barrier-driven
   writer/writer and reader/writer tests observe one complete generation hash per reader and cover
   stale CAS, two activators, parent mismatch, immutable overwrite, corruption, and crash at every
   publish/activation boundary. SQLite, an in-memory repository, or sequential concurrency is not
   the real-store oracle.
5. **Comparative selector ownership.** Add a concrete selector task after approved V4-4 and V4-5.
   The V4-0 ownership map must name its production files, DB reader, classifier input, decision
   contract, generation snapshot, provenance event, fallback, sticky/safety override behavior,
   operator owner, and tests. Original Task 12 remains telemetry-only and can never satisfy this
   task. DONE requires production-seam differential tests proving a decisive compatible generation
   changes route exactly by the approved rule while every missing/stale/malformed/incompatible/
   indecisive case uses the byte-equivalent legacy fallback.
6. **Terminal writer closure.** Generate the terminal-path inventory from AST/control-flow and
   production telemetry/event sites, bind it to source/diff hashes, and fail when a new return,
   exception, callback completion, or append site appears without atomic ledger coverage. A hand-
   authored list cannot prove V2-T5.

### V4-6 binding DONE oracle — classifier/router split

Trace at the generated-client provider transport and execution-dispatch observers, not wrapper
counters. On the latest compatible corpus, cover every variant of every declared family that has
variants; report declared zero-variant families explicitly as evidence-free rather than silently
dropping them. Add unrelated, ambiguous, malformed, multi-turn/sticky, classification/TypeBuilder
failure, storage-error, and safety-fallback cases, and assert every row of V4-6's
call table, prompt/function identity, attempted/selected/actual route, destination/model, errors,
and downstream overrides. With the flag off, compare against the frozen base image/source and
require byte-equivalent destination/model/fallback behavior. Schema inspection proves the
classifier cannot represent destination/model. Mutations adding a transport call, route-bearing
classifier field, changed legacy prompt, swallowed failure, or unrecorded override must fail.

**V4-6 DONE:** the source-derived classifier/router/call-site inventory has no unvisited seam; every
row of the V4-6 call table passes through real generated clients and dispatch observation with zero unexpected skip/
xfail/deselection; evidence binds source/diff/image/corpus/schema hashes and killed mutations.

### V4-7 binding DONE oracle — experimental/observational separation

Use disjoint schema versions and discriminators, immutable paired-run lineage FKs, and DB/runtime
constraints. The production paired fitter may query only approved paired run IDs. Inject online
objects, forged discriminators, copied IDs, mixed batches, raw dictionaries, and policy-selected
provenance through every public fit/query/adapter entry point; each fails closed. Conservation
proves zero online row IDs in every paired input hash. Route-conditional monitoring remains
separately labeled and cannot call the comparative publisher/activator.

**V4-7 DONE:** a source-derived inventory covers every paired and online producer, adapter, query,
fit, publish, and activation seam; runtime/DB negative tests and boundary-removal mutations fail;
evidence binds schema/migration/source hashes with zero unexpected non-execution.

### V4-8 binding DONE oracle — authorization and spend

Use the production MySQL reservation schema and the real provider-transport boundary in the
isolated harness. Barrier-driven multi-process/multi-worker tests prove concurrent callers cannot
reserve or call beyond the approved cap. Inject crashes before reservation, after reservation,
after provider return, and before reconciliation; test broker redelivery, idempotency-key replay,
retry charging, orphan-reservation recovery, expiry, and changed manifest/rate/resolver output.
Conservation must reconcile approved maximum = available + reserved + reconciled actual + explicit
released/expired amounts, and calls = succeeded + failed + pending with stable attempt IDs. Bind the
rate table bytes/source timestamp and all resolved client/model/output identities to the approved
V5-2 record.

**V4-8 DONE:** every source-derived provider-call seam uses the same pre-transport atomic
reservation/approval verifier; locking/reservation-order/retry/idempotency mutations fail; N-way
contention, crash recovery, replay rejection, and reconciliation artifacts are independently
rerunnable. Only then may a separately approved pilot record be consumed.

### V5-4 — Evidence completeness, coverage, deployment, and rollback

- Generate the owned-module/config/migration/generated-file coverage manifest from the V4-0
  ownership map plus the exact base-to-candidate diff. Fail if any owned production or safety
  surface is absent. Enforce 95% statement and branch coverage per named critical module and
  overall, plus a maintainer-approved minimum mutation score for route enforcement, data
  governance, exclusions, aggregation, model decision, store/activation, boundary separation,
  approval/reservation, and rollback.
- Seed the disposable deployment with active/prior candidates, non-empty judgments and exclusions,
  pending/failed attempts, reservations, tombstones, post-migration rows, schedule/flag state, and
  concurrent readers/writers. Forward deploy and rollback must compare exact image, schema,
  generation, schedule, flag, DB/artifact/tombstone, network, and owner identities and preserve
  post-contract-compatible writes without data loss. An empty DB or image-only rollback cannot
  satisfy V4-9.
- Exercise an explicit **expand → migrate/backfill → contract** matrix against the production
  MySQL schema with simultaneously running old/new web and Celery images at their real entry
  points. Cover old-reader/new-writer and new-reader/old-writer directions, queued old tasks and
  broker redelivery, generation activation/snapshot reads, governance tombstones, and rollback
  after forward writes. Record the only safe rollout order. The contract/destructive step must
  refuse while any old web/worker/queued-task identity can still run; mutations that remove version
  guards or compatibility fields must turn the matrix red.
- Resolve the prior image digest independently and prove it exists before deploy. The current live
  image is baseline evidence, not a rollback artifact; the unchecked rollback script and `git
  revert` remain forbidden as complete rollback evidence.

**V5-4 DONE:** source-derived coverage/mutation manifests are complete, the free/fake E2E crosses
the real production seams, and an authorized non-empty concurrent disposable deploy/rollback
rehearsal reproduces all pre/post identities with governance tombstones effective before readers.

### Machine-readable gate completion

Each V4/V5 gate emits one schema-validated completion manifest listing every required artifact,
command argv/exit/counts, source/diff/image/DB/schema/corpus/evidence/approval hashes, mutation
results, and dependency gate IDs. A validator fails on a missing checklist item, stale hash,
unexpected skip/xfail/deselection, zero selection, unvisited source-derived seam, or unapproved
action. Prose, helper-only tests, filenames, or a partial checklist cannot mark a gate DONE.

Until the STOP decisions are recorded, the only permissible next work is decision-artifact
preparation and read-only verification. No “mostly complete” status may count an unapproved or
unexecuted gate as done.

## V6 amendment — the classifier's label space comes from the corpus (2026-08-05)

### Change control and precedence

V6 controls over V5, V4, V3, V2 and any original task body wherever they conflict on **where task
families come from**. It changes nothing else: every V2 safety, privacy, paid-call, provenance and
per-action approval gate survives, V4's STOP gates remain STOP gates, and V5's evidence-identity,
closed-inventory and source-universe requirements are untouched and still binding.

V6 is consistent with V5 rather than a competing account: V5 voids every earlier closed-list
reference to a 7/8/10/15-family taxonomy and shows that a handwritten count is not a closure oracle;
V6 says the same thing about counts and additionally names *which artifact* the classifier's labels
are drawn from. V5's `evidence/plan018-source-universe.json` inventory remains a prerequisite of the
V4-1 decision, and V6 does not shortcut it.

### V6-A — `route_capabilities.json` is not the classifier's source

Prior amendments named `dmac_assistant/build_context/route_capabilities.json` as the single source
of truth for the task-family set. That is wrong, for three independently verified reasons.

1. **It is a routing artifact, not a question taxonomy.** Its families exist to tell the router
   which of two destinations serves what; they are nested *under* a route, so a family determines
   its destination by position. A classifier that labels *what kind of question was asked* needs a
   label space that is not a restatement of the routing decision.
2. **It is hand-written, with no generator and no provenance.** Verified in both this repository and
   the standalone `dmac-assistant` repository: every reference is a reader or a test, no build step
   writes it, and its history is hand edits that add a family to change routing behaviour. It
   carries no version, no source hash and no adoption record. Issue #65 tracks giving it a
   generator; until that lands it is a hand-maintained file.
3. **It is live configuration.** `nextseek_api/cc_assistant/router.py:159` loads it and passes it to
   `RouterAgent`, and `nextseek_api/cc_assistant/tests/test_f_constraint_pins.py` pins its sha256.
   Evaluation work must not edit it, and must not require editing it.

**Therefore:** this plan neither reads nor modifies `route_capabilities.json` for classifier
purposes. The **fallback router continues to consume it exactly as today** — unchanged, unread by
the classifier, and with its hash pin intact. Earlier drafts instructed generating a classifier enum
from that file and writing an `ops` mapping into it; both instructions are withdrawn, and the
sections that carried them have been corrected or removed rather than left in place (V6-E).

### V6-B — The label space is sourced from the nessie_tests corpus (amended by V7)

The classifier's labels come from the latest schema-compatible corpus owned by `nessie_tests` in
the selected harness source. `_plan018-refs/corpus/corpus.json` is only a stale convenience copy and
must never select or pin runtime labels. The working corpus is a versioned, provenance-carrying
artifact: it records `adopted_from`, `catalog_sha256` and `adopted_on`, and states that retirement
is a `status` flip rather than a deletion.

It supplies, per family, exactly what a classifier needs and `route_capabilities.json` does not:

- a `description` — usable directly as the enum value's description;
- `variants` — real, labelled example queries, already grouped by family;
- `family_floor` — the minimum outcome assertion per family, which is the per-family success
  definition the evaluation side needs; and
- `route_policy.families` and `route_policy.overrides` — expected route per family and per case,
  so route feasibility is expressed *about* a family rather than *by nesting it under one*.

That last point matters for V4-1: it means route feasibility and common support can be stated
without a family being owned by a route.

### V6-C — SUPERSEDED: stale non-wholesale inference

The earlier V6-C text claimed a maintainer decision not to adopt all corpus families. No durable
user ruling supports that claim. It was an agent inference from the older staged corpus, where
`nessie_route`, `nessie_green`, and `nessie_repro` were test-tier groupings. The current corpus
remap removes those as families, assigns their variants by question content, and explicitly records
`route_gate`, engine, sticky state, environment and similar dimensions as axes rather than families.

V7-B therefore establishes the opposite contract: every key under the latest compatible corpus's
`families` object is a classifier label. There is no include/exclude disposition, curated allowlist,
or second taxonomy. A declared family with zero variants is still a label, but it has no empirical
support and cannot produce a confident posterior until evidence exists.

**No count is a contract — anywhere.** Not eight, not fourteen. Any number appearing in this plan is
an observation about a file at a moment. Tests assert **set equality against the source**, never a
length. A test containing a literal family count is a defect regardless of which source it reads.

### V6-D — RESOLVED: runtime-dynamic enum

Earlier drafts of this plan assumed enum members would be emitted by rewriting `.baml` source at
build time, and said so as though it were settled. It is not. BAML also supports runtime-dynamic
enums: an enum marked `@@dynamic` plus `TypeBuilder`, which adds values at
run time and supports `.description()` per value
(<https://docs.boundaryml.com/ref/baml_client/type-builder>, <https://docs.boundaryml.com/ref/baml/enum>).

The maintainer selected runtime-dynamic generation on 2026-08-05. Declare a distinct
`ClassifiedFamily` enum with `@@dynamic`; construct a `TypeBuilder` from every family key and
description in one validated corpus snapshot; and pass that builder to the classification call.
No task may emit members into `.baml`, maintain a static seed/allowlist, or fall back to a plain
unvalidated string. Generated Python clients may represent runtime additions as `str`, so the
wrapper must validate the returned value against the same snapshot before recording it.

The corpus snapshot is stable for one classification operation and its identity is recorded. A
missing/incompatible corpus or omitted/mismatched TypeBuilder fails classification safely and
records no family; it never silently falls back to static members. If the corpus is packaged inside
an image, delivering new corpus bytes still requires delivering a new image, but no BAML rewrite or
client regeneration is required solely because labels changed.

### V6-E — What this amendment changed, and one warning

Every section that named the wrong source has been corrected **in place**: the two Global
Constraints bullets, V2-T3, V2-T4, V3-A, V3-B's completion contract, the V4-6 DONE oracle, Task 3
and Task 4. Two sections were **removed outright**, because V6-A leaves them nothing to govern —
**V2-T12b** and **Task 12b**, which existed to write an `ops` mapping into `route_capabilities.json`
and update that file's hash pin in the same commit. Whether an op-level mapping is wanted at all,
what it maps from, and where it lives are part of V4-1's decision.

**Warning — illustrative family names elsewhere in this plan are not a label contract.** Some now
coincide with latest corpus families (`sample_search`, `batch_upload_preparation`); others do not.
Each executable fixture must derive a real family from the current corpus or mark the string as a
fixture-local non-contract value. No test or implementation may infer the label space from examples
in this prose; only `corpus["families"].keys()` defines it.

**The fallback router is untouched.** `nextseek_api/cc_assistant/router.py:159` continues to load
`route_capabilities.json` and pass it to `RouterAgent` exactly as it does today. V6 changes where
the *classifier's labels* come from; it changes nothing about how the existing router routes.

## V7 maintainer-ruling amendment (2026-08-05)

V7 supersedes conflicting V6/V5/V4/V3/V2/original prose for corpus mutability, classifier label
membership, enum generation, family/route provenance, non-BAML family handling, and internal-data
retention. It records explicit maintainer decisions rather than agent inference:

1. **V7-A — corpus mutability.** New work uses the latest schema-compatible harness-owned corpus. Exact hashes bind individual
   runs/generations and same-run resume; they do not freeze future corpus content.
2. **V7-B — label membership.** Every key declared under the corpus's `families` object is a classifier label. Declared axes are
   not families. No second selection layer exists.
3. **V6-D resolution.** `ClassifiedFamily` is runtime-dynamic through `@@dynamic` and `TypeBuilder`.
4. **V7-C — fallback and provenance.** The deterministic family fallback is omitted. The existing heuristic remains route-only.
   Classification and routing provenance are independent. A forced corpus arm records
   `family_source="corpus"` and `route_source="forced"`; a successful classifier records
   `family_source="baml"`; no successful classification means no family/source.
5. **V7-D — internal retention.** Internal/private evaluation data is retained by default. Data-product separation governs the
   exceptional withdrawal case; only an affected generation is deactivated and recomputed. Secret
   exfiltration response preserves restricted incident evidence and sanitized regression value while
   rotating/revoking credentials and removing live secret bytes from ordinary dissemination paths.

The 2026-08-05 latest-source observation is `origin/dev@a55b532` and
`origin/dev-v3-merge@d0855bd`; the latter's harness-owned corpus was version 2 with 28 declared
family blocks and additive `family_defaults` metadata. These counts and SHAs are observations, not
contracts. V4-0 must refresh them again immediately before execution.

“Schema-compatible” is machine-checked, not inferred from a matching version number. The corpus
adapter accepts additive top-level/family/variant annotation metadata, but requires a supported
schema version, an object-valued non-empty `families` map, a non-empty description and list-valued
`variants` for every declared family, unique valid family IDs, unique variant IDs, and every
variant's declared family to reference a key in `families`. Missing route policy/support metadata
does not remove a label; it makes comparative support unknown/`TooUncertain`. The validator emits
the corpus content hash, schema version, family-key hash and validation result. Incompatible shape
fails classification/fit preparation before any model call or partial run is started.


## V8 maintainer-ruling amendment (2026-08-07)

> **V8 records the maintainer's 2026-08-07 rulings and supersedes conflicting V7/V6/V5/V4/V3/V2
> prose** for: the Stage-2 purpose and the corpus-growth loop, the eval-row schema, the outcome
> definition and its total disposition mapping, stack-version identity, execution reuse, and the
> terminology corrections in V8-H through V8-K. Everything not named here is unchanged. V8 is not
> execution authorization: V4-0 and the V5 evidence-manifest release gate stand as written.

### V8-A — Stage 2 feeds Stage 1 by growing the corpus, not by updating the posterior

V4-7 struck the original goal sentence that let real usage update the comparative baseline, and
that removal stands: policy-selected online outcomes must never update the comparative posterior.
**V4-7 did not, however, replace it with the maintainer's actual intent, which is restored here.**

Real traffic feeds Stage 1 by **supplying questions**, not outcomes. The loop is:

1. The classifier assigns each turn a declared task family or `unrelated`.
2. Every classified turn whose family is **not** `unrelated` becomes a candidate for promotion
   into the `nessie_tests` corpus.
3. Promoted questions are executed in a subsequent **forced paired** Stage-1 run.
4. Only those forced paired outcomes update the comparative posterior.

There is no feedback loop on outcomes: route assignment in the re-run remains imposed, so the
causal contrast is unaffected. The loop is on question *selection* only, and selection is
outcome-blind by construction — promotion depends on the classifier's family assignment, never
on whether the turn succeeded.

**Promotion rule (v1): promote everything classified.** No de-duplication and no novelty filter.
The corpus is deliberately greedy for data at this stage; the same question asked ten times is
ten promotions, because padding thin families is worth more right now than corpus elegance.
A similarity-based novelty filter that prioritises unlike-anything-seen queries is explicitly
deferred as future work, and the infrastructure for it already exists in the `schema_rag`
endpoint's similarity search.

**`unrelated` is not a task family and never will be.** It is a permanent, non-negotiable
classifier outcome — the spend gate — and it is not a key under the corpus's `families` object.
The Bayesian router never sees an `unrelated` query when the classifier is functioning. No
`unrelated` turn is promoted, and no `unrelated` row reaches the fit.

### V8-B — Execution reuse: a grown corpus does not re-execute unchanged arms

**This supersedes the sentence at V3-C ("A partially completed/resumed run must retain its
original corpus fingerprint and selected IDs; it cannot mix corpus versions within one run")
insofar as that sentence forces full re-execution when the corpus grows.**

The judge cache is already per-case and content-addressed, so corpus growth does not force
re-judging. The same principle applies one layer down, to arm execution:

- [ ] Maintain an execution cache keyed **`(query_id, route, stack_id, task_family)`**. A prior
  arm execution is reusable when, and only when, all four match.
- [ ] `task_family` is part of the key because family membership selects `family_floor` and
  `expected_behavior` from the corpus's `family_defaults`: a reclassified question is scored
  against different success criteria, and the fit groups by family. Re-labelling a question
  therefore invalidates its cached arms even though its text is unchanged.
- [ ] `stack_id` is part of the key because a component upgrade is a new experiment (V8-E).
  A stack bump invalidates every cached arm; this is intended, not a defect.
- [ ] A run assembled from cached and freshly executed arms records every contributing corpus
  version, in the manner `run_meta.superseded_runs` already records contributing builds.
- [ ] Reuse must be provable: the fit's input set records, per arm, whether it was executed in
  this run or reused, and from which prior run.

### V8-C — The eval row (supersedes the Task 7 `EvalRow` dataclass)

The eight-field `EvalRow` in Task 7 carries no outcome, no cost signal, and no artifact facts,
and therefore cannot support a comparative fit. It is replaced. `EVAL_ROW_SCHEMA_VERSION`
becomes **3**. The row is one route arm of one question:

| Field | Kind | Notes |
|---|---|---|
| `query_id` | identity | pairs the two arms of the same question; without it the design is unpaired |
| `route` | grouping | `nextseek_query` \| `container_cc` — a closed set, safe as a Literal |
| `task_family` | grouping | plain string, **never** a Literal or static enum (V8-F); never `unrelated` |
| `route_source` | provenance | `forced` \| `baml` \| `sticky` \| `heuristic` — separates experiment from traffic |
| `family_source` | provenance | `corpus` \| `baml`; forced arms **must** carry `corpus` |
| `stack_id` | provenance | foreign key to `StackVersion` (V8-E) |
| `answer_provided`, `is_error`, `timed_out` | deterministic | the three flags `runtime_success` is derived from |
| `runtime_success` | deterministic | `answer_provided ∧ ¬is_error ∧ ¬timed_out`, validator-enforced |
| `failure_mode` | deterministic | `none` \| `timeout` \| `error` \| `no_answer` (V8-D) |
| `error_class` | deterministic | `none` \| `provider_outage` \| `usage_policy` \| `code_error` (V8-D) |
| `latency_seconds` | deterministic | the **only** cost signal the NExtSEEK route emits |
| `cost_usd` | deterministic | nullable; null is unknown, never zero |
| `artifact_expected` | deterministic | which families are eligible for the artifact gate |
| `artifact_status` | deterministic | `not_expected` \| `delivered_valid` \| `delivered_invalid` \| `missing` |
| `artifact_success` | deterministic | derived from `artifact_status`, validator-enforced |
| `functional_success` | **judged** | the only LLM-produced field in the row |

- [ ] The model is pydantic v2 with `extra="forbid"`. `runtime_success` and `artifact_success`
  are **derived and validator-enforced**, never independently asserted: a row whose asserted
  target disagrees with its own facts is rejected rather than silently fitted.
- [ ] A forced arm carrying `family_source != "corpus"` is rejected.

**Only `functional_success` may be produced by an LLM.** Everything else the existing standalone
tooling already computes deterministically, and re-deriving any of it through a judge call would
add stochasticity where none is needed. This is a binding constraint on implementation, not a
preference.

**Runnable form: `nextseek_api/eval/router_models_proposal.py`.** That file carries this row, the
V8-D disposition mapping, the derived-target validators, and the `(task_family, route)` binomial
aggregate the fit actually ingests, as executable pydantic v2. It is a **proposal, not the
implementation**: this plan reserves `nextseek_api/eval/export.py` for the row itself, and V4-0's
ownership map has not yet assigned a home to `StackVersion` (V8-E) or to `error_class`. Read it as
the precise contract these two sections state in prose; do not import it as product code, and do
not treat its location as an ownership decision.

### V8-D — One combined outcome, and the total disposition mapping V4-3 requires

V4-3 requires "one total outcome mapping from DD-44 output to desired/not-desired/excluded" but
never states it. **This is that mapping.** It is total; an unrecognised value fails closed.

**Success is one combined bit per arm:**

```text
success = runtime_success AND artifact_success AND functional_success
```

A family that expects no artifact cannot fail the artifact gate (`artifact_status =
not_expected` ⇒ `artifact_success = true`). Because the semantics are conjunctive, an arm that
has already failed either deterministic gate is `false` regardless of any verdict, and **must
not be sent to the judge** — the combined bit is what makes that saving sound.

**Disposition — `error_class` is read first; `failure_mode` only when `error_class` is `none`:**

| Condition | Disposition |
|---|---|
| `error_class = none` and `failure_mode = none` | scored — judge decides |
| `error_class = provider_outage` | **excluded** (unchanged from V3-C: outages must be dropped) |
| `error_class = usage_policy` | **scored 0** — a content-driven refusal is genuine route incapability |
| `error_class = code_error` | **scored 0** |
| `failure_mode = timeout` | **scored 0** |
| `failure_mode = no_answer` | **scored 0** |
| deterministic gate failed | **scored 0**, judge not called |
| gates passed but unjudged | **excluded** — never coerced to 0 |
| zero-criteria case | **excluded** (unchanged from V3-C) |
| unevaluable criteria | judged on remaining evidence, else **excluded** (unchanged) |
| missing arm | pending / **excluded**, never a loss (unchanged) |
| any unrecognised value | **fail closed** — halt, never coerce to success |

`failure_mode` is precedence-resolved over overlapping facts, not a disjoint classification:
a timeout also sets `is_error` and clears `answer_provided`. Precedence, highest first:
`timeout > error > no_answer > none`. `no_answer` is the residual — not timed out, no error,
still no answer.

- [ ] Conservation holds per group: `rows = n_total + n_excluded`, and per-reason exclusion
  counts are published as machine-readable output, not prose.

### V8-E — Stack identity is four components, referenced by key

`image` in the standalone tool meant the container agent's image. In the integrated stack the
assistant is four independently-versioned parts, and a change in any of them can move outcomes:
**NExtSEEK, the container agent, the sidecar, and SEEK.** SEEK is included because many API
endpoints query it, so a change in SEEK's code affects the NExtSEEK route indirectly.

- [ ] Define a `StackVersion` record: `stack_id`, `nextseek_image`, `container_agent_image`,
  `sidecar_image`, `seek_image`.
- [ ] The eval row carries **`stack_id` only**. The four version strings are not repeated on
  every row: they are high-cardinality, and admitting them into the row invites stratifying the
  fit by them, which fragments `n` and manufactures `TooUncertain` bands.
- [ ] The fit's group key stays `(task_family, route)`. `stack_id` is provenance and an
  execution-cache component (V8-B); it is **not** a grouping dimension.

### V8-F — The classifier enum is generated from the corpus, never hardcoded

Restating V6-D/V7-B as a binding implementation constraint, because it has been lost in
downstream reading more than once:

- [ ] `ClassifiedFamily` is declared `@@dynamic` with **no static members**. Its members are
  every key under the corpus's top-level `families` object, injected at runtime through
  `TypeBuilder` with each family's `description` attached, plus `unrelated`.
- [ ] No pydantic model, BAML type, test, or fixture may contain a literal list of family names
  or a literal family count. A `Literal[...]` of task families is a defect on sight.
- [ ] Storage-side fields stay plain strings, because the valid set is not knowable at
  model-definition time. The enum exists at the LLM boundary, not in the database.

### V8-G — `NessieManifestEntry` is named but never defined

The prospective code block types `ns` and `cc` as `NessieManifestEntry | None` but no definition
of that type appears anywhere in this plan; the twelve per-arm field names are bound only by the
prose phrase "Each arm is a full harness entry carrying …".

- [ ] The V4-2 task deliverable that defines the concrete pydantic models must define
  `NessieManifestEntry` explicitly against the real emitted artifact, or rename the annotation to
  a type it does define. An implementer must not infer the field list from prose.

### V8-H — Stage lettering is defined

"Stage B" appears exactly once in this plan and is never defined. The lettering is the standalone
tool's, and is fixed here:

- **Stage A** — artifact validity validation (`run_stage_a`; emits the artifact-validity CSV).
- **Stage B** — functional eval **input** construction (emits the judge's input CSV; reads the
  manifest, the runtime CSV and the Stage A CSV).
- **Stage C** — the judge itself (the ported `functional_evaluator`).

### V8-I — Which `families` object is the label space

The corpus contains two objects named `families`: the top-level one, and `route_policy.families`,
a strict subset. V7-B's "every key under the corpus's `families` object is a classifier label"
means the **top-level** object.

- [ ] The label-space reader must resolve the top-level `families` key. Resolving
  `route_policy.families` yields a proper subset and fails the both-directions enum-equality gate
  for a non-obvious reason.

### V8-J — The upstream that locks the per-arm export column set is named

V3-C says the per-arm export's column set "is locked verbatim upstream and must not be re-derived
here" without saying where upstream is. It is named elsewhere in this plan: the `tools/hibayes/*`
modules and the `hibayes_*` evaluation packages listed in the V4 port inventory, at the recorded
port-source commit.

- [ ] Any task consuming or producing the per-arm export cites that inventory entry as the
  column-set authority rather than treating the set as unlocatable.

### V8-K — Correction: live runs have happened

V3-C states "Nothing has run live yet." That is stale as of 2026-08-05. A smoke run and a full
paired run have both been executed by the harness author. The surrounding requirement is
unaffected: forcing a route remains admin-gated, those runs were executed by the harness author
rather than by this plan, and V3-C still cannot be marked validated on their basis alone.


## V9 maintainer-ruling amendment (2026-08-08)

> **V9 records the maintainer's 2026-08-08 rulings and supersedes conflicting V8/V7/V6/V5/V4/V3/V2
> prose** for: ownership of the artifact axis, how artifact validity is computed, the multi-artifact
> unit, the required-field rule, and the projection onto the eval row's four-value
> `artifact_status`. Everything not named here is unchanged. V9 is not execution authorization:
> V4-0 and the V5 evidence-manifest release gate stand as written.

### V9-A — Artifact validity is owned by NExtSEEK; one upstream module is replaced

The artifact axis is produced by **`nextseek_api/eval/artifact_validity.py`**, consumed by
`nextseek_api/eval/export.py` (which V8-C reserves for the eval row).

**`dmac_assistant` remains the port source for this plan.** The judge, the four fit packages, their
configs and templates, and the eval container are ported from it per Task 6. V9 narrows exactly one
thing: **`tools/hibayes/artifact_validator.py` is written fresh rather than ported**, because it
routes `task_family -> ArtifactKind` through a hardcoded dispatch, so every new report type needs a
new branch — and that design is the direct cause of the defects in V9-B. Its enum surface
(`ArtifactStatus`, `ArtifactKind`, declared in `tools/e2e/functional_evaluator_models.py`) **is**
ported unchanged, so results stay comparable with prior runs.

`artifact_success` and `artifact_status` remain deterministic per V8-C — **no LLM may produce
them**. `functional_success` stays the only judged field in the row.

### V9-B — Validation is kind-agnostic: the artifact declares its own schema

Both engines mark required fields with a leading `*` (single-valued) or `**` (multi-valued) — the
convention the upstream module already reads at `artifact_validator.py:378`. Validation **reads the
markers the artifact carries**. It does not switch on what kind of report it is.

- [ ] `ArtifactKind` is retained as a **reporting label only**. It must not appear in any
  control-flow branch that decides validity. A new report type requires no code change.
- [ ] No validator may key on filename or extension. Type is determined from **content**.
- [ ] Spreadsheet reading uses **calamine** (via `fastexcel`); `openpyxl` is prohibited on this path
  for the reason in row 4 below.
- [ ] OOXML sub-type is discriminated by **part directory** (`xl/`, `word/`, `ppt/`), never by
  `[Content_Types].xml`, which is present in every OOXML container.

**Why this is binding, not stylistic.** Four independent defects on the 2026-08-07 paired delivery,
all one root cause — an assumption about the `nextseek_query` output shape applied to
`container_cc`:

| # | Where | Effect |
|---|---|---|
| 1 | upstream path resolution (the DD-25 hazard its own docstring warns of) | all 18 CC artifact-expected arms `Missing`, 9 of them holding real deliverables |
| 2 | upstream single-file guard (`NotImplementedError`, plan-DD-03) | every multi-file deposit unvalidatable |
| 3 | upstream dispatch has no `PRIDE_PACKAGE`/`SRA_PACKAGE` branch | 9 NS arms `Indeterminate` — "no rule", recorded as a data fact |
| 4 | `openpyxl` rejects extension-stripped `.xlsx` **on the filename**, before reading a byte | 9 real CC workbooks would read as `Unreadable` |

Net effect before remediation: **zero `Valid` artifacts on either arm**, and
`report.sra_submission` scored identically (`Missing`) on the arm that delivered three workbooks and
the arm that delivered nothing.

### V9-C — The unit is a set of artifacts; worst status wins

One arm routinely emits several artifacts of mixed origin: an NS PRIDE deposit is 4 (two inline
`table` artifacts carrying `columns` + `data`, plus two files); the paired CC arm is 8 files.
Multi-artifact is the **normal case**, and the upstream single-file guard is the anomaly.

- [ ] Every artifact is validated and its per-artifact verdict **retained**, not collapsed at
  discovery time.
- [ ] The arm's status is the **worst** status across its set, on the full ten-value
  `ArtifactStatus` vocabulary, by this severity order (ascending):
  `NotExpected < Valid < Incomplete < SchemaInvalid < Unreadable < Inaccessible <
  PartialAfterFailure < Missing < RuntimeFailed < Indeterminate`.
- [ ] `Indeterminate` is deliberately **worst**: under V9-B it means the validator met a shape it
  has no rule for, which must be loud rather than absorbed.
- [ ] `NotExpected` is an **arm-level** state, never a per-artifact verdict. It is decided before the
  set is walked, from `artifact_expected`. Admitting it as a per-artifact status would let it enter
  the maximum and silently outrank a real failure in a mixed set.
- [ ] An arm expecting an artifact that produced none is `Missing` when the run succeeded and
  `RuntimeFailed` when it did not (DD-36) — different defects, different dispositions.

### V9-D — Required-but-empty is structural only

A required field is satisfied by the **key being present**. A null or empty value does not fail the
artifact.

Rationale: the engines cannot invent values NExtSEEK does not hold — the CC PRIDE deposit fills
submitter name, affiliation, lab head and project title but leaves `*submitter_email`,
`*lab_head_email` and `*submitter_pride_login` null, because the source has no such values. Failing
that would measure the database, not the engine. Content-completeness grading is deferred; if
introduced later it must be a **separate axis**, never folded into `artifact_success`.

### V9-E — The projection is total, and unmeasurable is not failure

| ten-value status | `artifact_status` (V8-C) | `artifact_success` |
|---|---|---|
| `NotExpected` | `not_expected` | **true** — a family expecting no artifact cannot fail the gate |
| `Valid` | `delivered_valid` | true |
| `Incomplete`, `SchemaInvalid`, `Unreadable`, `Inaccessible`, `PartialAfterFailure` | `delivered_invalid` | false |
| `Missing`, `RuntimeFailed` | `missing` | false |
| `Indeterminate` | — | **null → excluded** |

- [ ] `Indeterminate` maps to **excluded**, never to `false`. "We had no rule" must never be
  recorded as "the engine failed" — that confusion is the entire defect class V9-B exists to
  remove.
- [ ] **`Indeterminate` has no `artifact_status` value, by construction.** V8-C's four-value field
  is non-optional under `extra="forbid"`, so an `Indeterminate` arm **emits no eval row at all**: it
  is excluded before row construction, with reason `artifact_indeterminate`, and appears in the
  per-reason exclusion counts V8-D requires as machine-readable output. Do not widen
  `artifact_status` to carry it, and do not default it to `missing`.
- [ ] Any `Indeterminate` arm is reported explicitly at run time, never counted silently.
- [ ] The mapping is total; an unrecognised status **fails closed** per V8-D's final row
  ("any unrecognised value — fail closed") and V4-3's requirement that unknown enum values are not
  coerced to success. *(Line-number citations are deliberately avoided here: this document is
  amended in place, so any line reference rots on the next amendment.)*

### V9-F — One validator, two sources: it must ingest the next paired run unchanged

Validation logic is source-independent. Path resolution is the **only** arm-specific step, and it
lives behind an adapter:

- [ ] `LiveTurnSource` — reads a turn's `result.artifacts` (inline tables and file references) and
  `result.files`, resolving file paths against the configured outputs/scratch volume. This is the
  production path, invoked from `export.py` as rows are built.
- [ ] `ExportedRunSource` — reads an exported run directory (`raw_files/<query_id>/<arm>/`),
  resolving `output/**` for `container_cc` and `run_root/files/**` for `nextseek_query`. This is the
  path that ingests a delivered E2E run.
- [ ] Both adapters yield the same artifact records; **no validation rule may differ between them**,
  and a test must assert identical verdicts for one run expressed both ways.
- [ ] A bundle (`output/artifacts.zip`) is skipped **only** when its members are provably a subset
  of the loose tree; otherwise it carries unique content and is validated.
- [ ] `result.cc_raw_files` is empty in delivered runs and must not be relied on. Its emptiness is
  the same defect that produced the upstream `no_path_prefix` collection gap; the resolver must
  enumerate the volume rather than trust the declaration.

### V9-G — Reference results this task must reproduce

Re-validating the 2026-08-07 delivery's `set3_final` (298 arms, 36 artifact-expected) must yield:

| | `container_cc` | `nextseek_query` |
|---|---|---|
| `Valid` | 9 | 9 |
| `Missing` | 7 | 7 |
| `RuntimeFailed` | 2 | 2 |
| `NotExpected` | 131 | 131 |

- [ ] **18 of 36** artifact-expected arms `Valid`, against **0** under the superseded validator.
- [ ] Verdicts symmetric across arms — an asymmetric result indicates the resolver favours one
  engine's layout and is a failure condition, not a finding.
- [ ] Zero `Indeterminate`.

### V9-H — Where the evidence lives

All of the following are committed or staged, not local-only, so a future session or a different
machine can re-derive V9-G rather than trust it.

**Committed in this repository**, beside the V8-C router models, under `nextseek_api/eval/`:

| Path | What it is |
|---|---|
| `router_models_proposal.py` | V8-C/V8-D row and aggregate models (runnable form, per V8-C) |
| `artifact_validity_proposal.py` | **runnable reference validator for V9** — proposal, not the implementation |
| `artifact_validity_set3_final.csv` | 298 arm verdicts — the V9-G regression pins |
| `artifact_detail_set3_final.csv` | 256 per-artifact verdicts behind those arm rows |

Verified reproducible from that directory: deleting both CSVs and re-running the proposal
regenerates them byte-identical. It requires `uv run --no-project` — without it uv resolves this
repo's own dependencies and dies on `torch` (no x86_64 macOS wheel), so the script never runs and an
unchanged output file falsely reads as a clean reproduction.

### V9-I — Two consequences elsewhere in this plan

Both follow from V9 and would otherwise be discovered late, so they are stated here rather than left
to an implementer to notice.

**1. Recomputed artifact facts must invalidate cached judgments.** V2-T8's canonical fingerprint
already covers "a changed query, answer, outcome, **artifact/trace fact**, or version". V9 changes
artifact facts wholesale — 18 of 36 artifact-expected arms move from `Missing`/`Indeterminate` to
`Valid` — so any judgment cached against the superseded facts is stale.

- [ ] The judgment fingerprint must include the arm's artifact facts, not merely its identity and
  version tuple. The historical Task 8 code block hashes only session, turn, route, family and the
  three version fields; that shape cannot detect this change and must not be implemented as written.
- [ ] Re-deriving artifact validity over an already-judged run is therefore a cache-invalidating
  event for exactly the affected arms, and must not silently reuse their verdicts.

**2. The upstream 29-column artifact-validity CSV shape is superseded; the per-arm eval-row export
is not.** V3-C locks the per-arm export column set "verbatim upstream" and V8-J names that upstream.
That lock covers the **eval-row** export and is untouched by V9.

- [ ] `CSV_HEADER_29` in the superseded `artifact_validator.py` is **not** binding on
  `artifact_validity.py`. V9-A replaces that module, so its output shape is replaced with it.
- [ ] The replacement emits, per arm, the ten-value status plus its V8-C projection, and retains a
  per-artifact record (V9-C). The reference implementation's two files
  (`artifact_validity_*.csv`, V9-H) show the shape that produced the V9-G pins.
- [ ] No task may cite V8-J as authority for the artifact-validity column set. V8-J governs the
  per-arm eval-row export only.

## V10 post-re-vet hardening amendment (2026-08-10)

### Change control, authority, and precedence

V10 follows the all-instance V9 inventory, independent adversarial/gameability reviews, independent
claim verification, and the maintainer's explicit answers to all eight escalated design questions.
It supersedes conflicting V9/V8/V7/V6/V5/V4/V3/V2/original prose only for the contracts named
below. Every other prior constraint remains binding.

The exact V9 source preserved before this edit is
`docs/superpowers/plans/2026-07-31-hibayes-eval-routing.pre-v10-20260810T112554-0400.md`, SHA-256
`8da83f5b6fd1b29bad448b5ca10550c655e27cf00d342d11f71a429fe4950df8`.

The maintainer's rulings are binding design decisions:

1. Extend execution reuse with a canonical execution-input hash.
2. Dynamically construct a pydantic model from an independently represented artifact schema and
   use pydantic validation to enforce marker-declared required fields.
3. Promote every eligible occurrence automatically without mutating the on-disk corpus; persist
   exact corpus uploads as immutable, checksum-unique database records with timestamps and
   metadata. A byte-identical upload is rejected.
4. Derive `stack_id` from the four immutable image digests; model, configuration, and data
   identities remain separate provenance/reuse guards.
5. Resolve `artifact_expected` from versioned corpus `family_defaults` with an explicit
   per-variant override.
6. Add `posterior` to the shared `route_source` vocabulary.
7. `unrelated` remains a non-negotiable enum member and spend gate. The existing router's
   unrelated-enforcement prompt language and behavior must not be mutated or dropped.
8. Execute every promoted occurrence independently, even when query bytes repeat.

Confirmed factual/oracle repairs are also binding: preserve pair identity through the statistical
decision boundary; durably conserve pre-row exclusions; contain and bound artifact access; prove
bundle subsets by bytes; bind regression evidence to fresh raw inputs and exact rows; enforce
failure-mode coherence and exhaustive status projection; compare independent source adapters at
the canonical-record layer; and mechanically void conflicting historical execution snippets.

No V10 text changes a V4/V5 STOP or supplies its answer. No task may implement from the historical
body alone. Before execution, V4-0 must emit `evidence/plan018-controlling-contract.json`, mapping
every task/step to its controlling V10/V9/V8/V7/V5/V4 clause and marking every conflicting old
command or oracle `void`. The validator fails on an unmapped step, multiple active authorities, or
any attempt to execute a void command.

### V10-A — Content-bound execution reuse and independent promoted occurrences

The V8-B key is extended to:

```text
(query_id, route, stack_id, task_family, execution_input_sha256)
```

`execution_input_sha256` is SHA-256 over a canonical, versioned serialization of all resolved bytes
that can alter that arm's execution or scoring contract: the complete single-/multi-turn question,
criteria and expected behavior, resolved family defaults and per-variant overrides, independently
declared artifact schema identity, and non-secret execution parameters. The serializer name/version
and component hashes are stored beside the digest. It never hashes a mutable path, object `repr`,
timestamp, or unordered dictionary iteration.

Model/client identity, non-secret configuration hash, and data/seed identity are required separate
fields and exact-match reuse guards. They are not folded into `stack_id` and are not fit grouping
dimensions. A cache lookup is reusable only when the five-field key and every separate guard match.
Changed content under a stable `query_id` must miss the cache; a collision or inconsistent stored
component map fails closed.

Every `StackVersion` component is independently resolved to the immutable OCI digest syntax
`sha256:<64 lowercase hexadecimal characters>`; tags, container names, branch names, and aliases
such as `latest` are rejected even if they currently resolve to the same bytes. The canonical
`stack-v1` input is a length-delimited encoding of exactly these ordered named pairs:
`nextseek_image`, `container_agent_image`, `sidecar_image`, `seek_image`. `stack_id` is
`stack-v1:sha256:<hex SHA-256 of those canonical bytes>`. The derivation version, canonical tuple,
and resulting ID are stored under a uniqueness/equality constraint: the same tuple always resolves
to the same ID, and an existing ID associated with any different tuple is an integrity collision
that fails closed. Tests reject aliases and malformed digests, permute tuple order/names, mutate
each digest independently, prove same-tuple determinism, and reject an inconsistent stored record.
Model/config/data identities remain separate guards and never enter this tuple or the fit grouping.

Each promoted occurrence receives a unique immutable `promotion_id` and `query_id`. Repeated
questions are not aliases: every occurrence independently executes both forced arms. The execution
cache must never reuse an arm across two different promotion/query IDs, even when their text and
all other input bytes are equal. Tests must mutate each canonical input and each separate guard,
prove a miss, and prove a no-op executor cannot satisfy a reuse-path test.

### V10-B — Immutable corpus uploads and automatic database-backed promotion

Runtime code never edits `nessie_tests/corpus.json` or any other on-disk corpus file. Add an
immutable `CorpusVersion` database record holding the exact uploaded bytes plus:

- `sha256` with a database uniqueness constraint;
- `byte_length`;
- database-generated `created_at` in UTC;
- original source name/identifier, uploader or producing process, format/schema version, and
  provenance metadata.

Upload/import reads the on-disk file bytes once, hashes those raw bytes without parsing,
canonicalizing, reformatting, or rewriting them, and checks the database. If a row with that hash
already exists, the attempt is rejected as a duplicate, not reported as a successful upload and not
silently treated as a no-op. The unique constraint is the concurrency authority. A same-hash row
with different length or bytes is a collision/integrity incident and fails closed. Successful
retrieval returns the exact stored bytes and verifies checksum and length before parsing. Parsed
schema validity is a separate admission check and never changes the stored identity bytes.

Automatic online promotion is not a corpus-file upload. A `CorpusPromotion` row is inserted for
every classified non-`unrelated` turn, linked to the selected immutable `CorpusVersion`, source turn,
classifier snapshot, family, exact query/turn bytes, resolved criteria/defaults, artifact schema,
governance decision, and unique occurrence identity. It retains multiplicity and has no dedup or
novelty check. The next paired-run input is the selected immutable corpus version plus all eligible
linked promotion rows; assembling that input occurs in memory/database and never rewrites the
stored corpus blob or disk file.

V5-1 applies explicitly before promoted bytes are admitted or disclosed. If governance or required
runnable criteria cannot be resolved, the occurrence is still represented by one durable promotion
disposition with the exact failure reason; it is never silently dropped or invented as runnable.
Conservation is equality with multiplicity between classified source-turn IDs and exactly one of
`promoted_ready`, `promotion_failed`, or `unrelated_not_promoted`. Promotion code has no access to
route outcome, answer quality, success, posterior, or judgment fields; tests kill outcome-branch,
drop, dedup, and unrelated-admission mutants.

### V10-C — Independently declared schemas and dynamic pydantic validation

V9-B's phrase “the artifact declares its own schema” means a schema declaration independently
represented from the data records, not a schema inferred from the fields that happen to be present
in the delivered payload. The source adapter carries canonical schema bytes/schema ID and SHA-256
beside each structured artifact. A generated observational schema derived from the same payload is
not authority for omission detection.

The validator converts that declaration into a cached pydantic v2 model (`create_model`,
`TypeAdapter`, or an equivalent pydantic-native construction) keyed by schema hash:

- a `*field` becomes a required, single-valued field using its exact external name as an alias;
- a `**field` becomes a required multi-valued/list field using its exact external name as an alias;
- required fields have no default, so absence fails. Their generated pydantic types explicitly
  union the declared strict nonempty type with the approved empty representations: `None`, the
  zero-length string, and the zero-length container of the declared container kind (`[]` for a
  multi-valued/list field and `{}` for an object field). A `mode="before"` field/model validator
  checks alias presence before content validation and recognizes only those exact empty forms;
  whitespace-only or wrong-kind values are not silently treated as empty;
- configured unknown-field behavior is explicit and tested; it may not silently discard data that
  affects validation;
- JSON objects and inline tables are converted to record mappings; CSV/TSV rows and each declared
  XLSX sheet/table are converted to mappings before pydantic validation. Pydantic validates these
  structured records, not binary containers or filesystem safety.

The required set comes only from the independently carried schema. Deriving a model from the
payload's present keys/columns is forbidden because deleting a whole marked field would delete the
only evidence it was required. A malformed declaration is `SchemaInvalid`; a structured artifact
whose required schema cannot be independently resolved is `Indeterminate`, never `Valid`. An
independently declared schema with zero required fields may validate normally.

Golden and mutation tests, for every supported structured form, delete each required key/column and
require `Incomplete`, then restore it separately with `None`, `""`, and every schema-approved empty
container and require validity. Nonempty values still undergo strict declared-type validation;
their raw representation and the recognized empty form are retained for downstream axes rather
than coerced away. Tests distinguish an absent alias from each present-empty representation for
both `*` and `**` fields and kill marker stripping, schema/data join bypass, first-row-only
validation, empty-to-absent normalization, wrong-kind empty acceptance, and “nonempty means valid.”

### V10-D — Canonical artifact expectation and total artifact safety

`artifact_expected` is deterministically resolved from the selected, checksum-bound
`CorpusVersion`: begin with that task family's `family_defaults.artifact_expected`, then apply an
explicit non-null per-variant override when present. The resolver stores the corpus ID/hash,
family-default value, override value, final value, and resolver version. Missing family defaults,
unknown families, invalid override types, conflicting provenance, or caller-supplied values that
disagree with the resolver fail closed. Callers may not provide an unbound boolean, and all-false
fixtures cannot bypass the positive-family tests.

Both artifact sources must resolve only regular files beneath an allowlisted per-run root using a
no-follow, canonical containment policy. Reject absolute/traversal paths, symlink/hardlink/special-
file escapes, zip-slip members, duplicate ambiguous paths, and paths owned by another run/project.
Enforce explicit configurable maxima for file count, individual/total bytes, rows/cells, archive
members, compressed and expanded bytes/ratio, nesting, and parser time. Limit violations produce a
deterministic non-success status/reason and cannot hang or exhaust the worker.

`artifacts.zip` is a subset of the loose tree only when every safe normalized relative member path,
byte length, and SHA-256 matches a loose artifact. Basename equality is never proof. Otherwise the
unique member is independently validated. Tests cover same-name/different-byte members, duplicate
basenames, archive traversal, archive limits, and unique bundle content.

`LiveTurnSource` and `ExportedRunSource` independently construct the same canonical artifact-record
schema. A heterogeneous run represented both ways must match byte-for-byte at the canonical-record
layer and then match per-artifact and arm verdicts. Mutating either adapter alone must turn the gate
red; final-status equality alone cannot satisfy parity.

### V10-E — Durable dispositions, pair preservation, and coherent rows

Before `EvalRow` construction, every source arm produces exactly one durable disposition:
`EmittedArm` or `ExcludedArm`. `ExcludedArm` contains the immutable run/pair/query/arm FK and source
hash, route, family, stack/input identities, exclusion reason, triggering facts, and disposition
version. It is not an auxiliary hand count. Foreign-key and uniqueness constraints prevent an arm
from being both emitted and excluded or from disappearing.

Conservation is recomputed from the pre-row arm universe:

```text
all source arm IDs == emitted arm IDs DISJOINT-UNION excluded arm IDs
```

Counts are also published by run, family, route, and reason. Unknown reasons, dangling IDs,
duplicates, or a mismatch halt fit admission.

`failure_mode` is derived by trusted code from source flags using
`timeout > error > no_answer > none`; a supplied value that disagrees is rejected. Tests exhaust
all flag combinations and kill every precedence/operator branch. `error_class` is likewise bound
to source evidence; unknown or incoherent facts fail closed.

The fit admission object remains pair-addressable through the V4-4 STOP. It carries `query_id`,
pair/run identity, both route arms or their bound disposition, and all lineage hashes. The V8
`(task_family, route)` marginal aggregate in `router_models_proposal.py` is not fit input and must
not discard pair identity. V4-4 still decides the dependence-preserving likelihood/sufficient
statistics, repeated-unit treatment beyond the already-ruled independent promoted executions,
thresholds, and decision rule. Nothing in V10 chooses those reserved statistical semantics.

### V10-F — Posterior provenance and the permanent `unrelated` gate

The shared closed `route_source` vocabulary is:

```text
forced | baml | sticky | heuristic | posterior
```

A posterior-selected route records `posterior` plus the activated generation/decision provenance
required by V4-5/V4-6. Attempted, selected, actual, fallback, and safety-override facts remain
distinguishable; no route source is relabeled to fit an older enum.

`unrelated` is permanently retained in both relevant enum surfaces:

- the existing `Route` enum retains `Unrelated @alias("unrelated")` for legacy/fallback
  compatibility; and
- runtime TypeBuilder constructs the effective `ClassifiedFamily` set as exactly
  `corpus["families"].keys() union {"unrelated"}` — no other extra member is allowed.

The existing unrelated guard paragraph in both byte-identical BAML router trees must remain
byte-for-byte unchanged:

```text
If the query has no connection to NExtSEEK, the BioMicro Center lab, or
the user's research data — for example general world trivia, celebrity or
pop-culture gossip, current events, or chit-chat unrelated to the lab's
samples, studies, code, or files — select `unrelated`. Do NOT route such
queries to `container_cc` or `nextseek_query`.
```

The split classifier carries that exact paragraph without deleting or mutating it in the existing
router. An `unrelated` classification immediately emits the existing fixed canned response and
produces no routing decision, NS dispatch, CC dispatch, downstream model/provider call, posterior
lookup/fit input, or promotion. History stickiness, posterior selection, fallback, and safety
overrides cannot convert `unrelated` into an in-scope route.

Tests pin the paragraph byte-for-byte in both BAML copies, pin both copies byte-identical, assert
the exact enum equality above, assert the canned reply, and instrument every downstream seam to
prove zero calls. They specifically prove sticky history cannot override `unrelated`. No task may
weaken the exact-equality oracle to subset containment or remove/reword the current guard.

### V10-G — Artifact-status, regression, and evidence oracles

Test every singleton and every ordered pair/permutation of the ten `ArtifactStatus` values through
the public validator/export seam. Assert the full severity order, total V9-E projection, prohibition
of per-artifact `NotExpected`, empty expected/non-expected sets, and fail-closed handling of unknown
future values. Mutating any severity or projection entry must fail.

The V9-G clean counts remain unchanged, but aggregate equality alone is insufficient. The delivery
test must:

- fresh-retrieve and verify stable evidence identities before use per V5;
- bind exact V10 plan/backup, source/diff, dependency locks, raw delivery SHA-256, `MANIFEST.json`
  SHA-256, and every raw member identity;
- generate into a newly created empty output directory and reject pre-existing outputs;
- prevent product code from importing or reading the expected committed CSVs;
- compare every arm and per-artifact row by stable ID, source hash, detected type, schema hash,
  status, projection, and conservation before checking aggregates; and
- run leave-one-out and corrupt-one-byte mutations for every supported structural type, proving the
  exact affected row/arm changes and unrelated rows do not.

The registry release verifier resolves the plan from fresh-fetched `origin/main` and verifies exact
bytes/hashes. It must not trust the checked-out machine-branch vault, basename, or stale manifest
pointer. Box-local delivery paths remain evidence inputs only until V5's stable release gate is
satisfied.

### V10-H — Owned implementation tasks and local DONE conditions

V4-0 must assign exact files after selecting/reconciling the implementation base. At minimum the
ownership map includes:

| Owner | Required deliverable |
|---|---|
| V4-0 / Task 1 | `CorpusVersion`, `CorpusPromotion`, immutable stack/config/data provenance, migrations and constraints |
| Task 2 / Task 5 | automatic promotion writer, governance/disposition conservation, terminal-path coverage |
| Task 3 / V4-6 | split classifier, exact dynamic enum, preserved unrelated prompt/canned gate |
| Task 7 | coherent EvalRow export, `EmittedArm`/`ExcludedArm`, pair-preserving fit-admission object |
| Task 7b | independent artifact-schema model builder, safe sources, exhaustive validity/status oracles |
| Task 8 | five-field execution cache plus separate guards; complete judgment fingerprint |
| Task 9 | promotion/run assembly and paid reservation over exact independently executed arms |
| Task 10 / V4-4 | reviewed paired model adapter, immutable generation publication and activation |
| Task 12 / V4-6 | posterior selection with `route_source="posterior"` and exact fallback provenance |
| Task 13 / V5 | source-derived completion manifest, mutations, evidence release and rollback proof |

Local DONE requires all controlling V10 tests plus the earlier non-conflicting task conditions.
Historical Task 7's `created_at__gt` watermark test, “non-incremental export” failure, running
container commands, V8 marginal-fit aggregate, permissive publisher upsert, premature beat
registration, post-call spend arithmetic, and skipped-live-test-as-acceptance are explicitly void.
A skipped paid test proves default safety only. No task is DONE from a grep, copied count, mock-only
assertion, schema-only manifest, or test suite with unexpected skip/xfail/deselection/zero selection.

### V10 completion and release gate

V10 hardening is complete only when an independent verifier reviews only the exact V9-backup-to-V10
diff against the persisted V9 adversarial, gameability, claim-verification, all-instance inventory,
and the maintainer rulings, with zero blockers. That review does not authorize implementation.

Execution remains blocked on V4-0, V4-1, V4-4, V5-1, V5-2, stable evidence release, and every
separate paid/deploy/activation/commit/push approval. A future changed plan, corpus bytes, selected
base, proposal, raw delivery, or review finding invalidates only the evidence bound to its prior
hash; it never silently inherits a CLEAN verdict.

## V11 direct-at-time approval amendment (2026-08-10)

V11 records the maintainer's explicit choice of the simple approval process and supersedes V5-2,
V10's reference to V5-2 as an implementation blocker, and every conflicting requirement for a
separate approval schema, database, signature, external authority resolver, system-of-record ID,
expiry field, or consumable nonce.

When this plan reaches a maintainer decision or a gated action, the active agent asks the maintainer
directly **at that time**. The question must clearly state the decision/action, why it is needed,
the exact scope and material consequences, and multiple viable options with any recommendation
justified. The maintainer's explicit answer in that conversation is the approval authority. The
agent records it in the normal durable handoff as user-stated context, but the handoff is an audit
record, not a second approval mechanism.

- [ ] Approval is specific to the question actually asked. It cannot be inferred, bundled with a
  different action, carried into materially changed inputs/targets, or treated as standing
  permission. If material scope changes, ask again at the time it changes.
- [ ] V4-1 taxonomy/common-support/estimand, V4-4 statistical-contract, and V5-1 governance
  decisions require only this direct maintainer answer after their decision artifact is presented.
  No V5-2A infrastructure or external verification must be built first.
- [ ] Paid/model runs still present the exact run scope, clients, current price basis, estimated and
  maximum spend, retry allowance, and target before asking. The existing atomic budget reservation,
  hard-cap, idempotency, and reconciliation controls remain implementation requirements; they do
  not require an approval-record service or nonce.
- [ ] Candidate activation, deployment, production enablement, commit/push, live mutation, and
  destructive rollback are each asked separately when ready, with their exact target and rollback/
  impact context. Direct conversational approval replaces V5-2B's authenticated-record and nonce
  verifier; it does not merge these action boundaries or authorize them in advance.
- [ ] An agent-authored assertion that approval occurred is insufficient when the conversation does
  not contain the maintainer's explicit answer. Conversely, an explicit answer does not become
  invalid merely because no signature, external resolver, approval database, or nonce exists.

**Immediate effect:** there is no approval-mechanism implementation blocker. A new implementation
session needs only the maintainer's direct authorization to begin V4-0. It must return for the
remaining V4-1, V4-4, V5-1, paid, activation, deployment, production, commit/push, live-mutation,
and destructive-action decisions when each becomes current; it must not ask for or manufacture
those approvals prematurely.

## V12 existing-security-baseline amendment (2026-08-10)

V12 records the maintainer's correction that V5-1 designed a parallel governance regime for risks
already controlled by NExtSEEK's existing operating model. V12 supersedes V5-1 as a STOP, V11's
remaining reference to a future V5-1 decision, and every conflicting requirement to author or
approve a new Plan-018-specific lawful-basis/notice, retention matrix, encryption/key, offboarding,
legal-hold, incident-owner, or general data-governance policy before implementation.

The binding baseline is:

- NExtSEEK is a private system and users must authenticate before using Nessie.
- User-facing operations and actions traverse the existing API authorization layer, which scopes
  access by project. Plan 018 reuses that boundary and does not create a bypass.
- Historical query/run/evaluation data is internal and no non-admin user is given a new list,
  retrieve, export, search, or download surface for it.
- The two already documented admin-endpoint authorization exceptions are existing defects tracked
  separately. Plan 018 neither fixes them nor treats them as precedent; no new Plan 018 endpoint may
  reuse their permissive `IsAdminUser` pattern or expose Plan 018 history through them.
- Internal/private evaluation data remains retained by default per V7-D. Plan 018 does not add a
  general erasure workflow, retention scheduler, legal-policy engine, or separate governance store.

These are implementation invariants, not a policy-approval project:

- [ ] Reuse existing authentication and project-authorization helpers at every user-facing seam;
  do not duplicate or weaken them.
- [ ] Keep historical ledger, corpus-promotion, artifact, judgment, cache, and generation reads
  internal to explicitly trusted admin/service paths. Any new HTTP/API surface is denied to
  unauthenticated and ordinary non-admin users; tests prove cross-user/project denial and that no
  list/retrieve/export endpoint leaks another user's history.
- [ ] Do not route new Plan 018 data through either separately tracked permissive admin endpoint.
  Those issues remain independent remediation work and do not block Plan 018 implementation.
- [ ] Preserve existing secret-handling and logging rules. Literal credentials/secrets are not
  intentionally persisted in ordinary evaluation fields or sent to a judge; this is ordinary input
  hygiene, not a new governance-policy gate.
- [ ] When a real external judge/provider run is ready, show the maintainer the exact payload
  categories, target/provider, scope, and spend under V11 and ask directly at that time. Free/fake
  implementation and tests do not wait for this later action approval.
- [ ] Negative tests cover unauthenticated access, ordinary-user history access, cross-project
  access, accidental endpoint registration, and provider-payload construction. They validate the
  existing boundary; they do not require a new role system, policy database, retention service, or
  approval mechanism.

**Immediate effect:** there is no V5-1 governance-policy blocker. After direct implementation
authorization, a new session may begin V4-0. The next maintainer decision is V4-1 after the session
produces the actual taxonomy/common-support artifact; V4-4 and real provider/paid/activation/
deployment/production/commit-push/live-mutation/destructive-action decisions are asked only when
their work reaches the corresponding gate.

## Referenced artifacts (dev box)

On the specifically inventoried development box, external artifacts cited by this plan have
convenience copies under `_plan018-refs/`, so an on-box reviewer can open them without hunting the
filesystem. That directory is **untracked and locally excluded** (via the common gitdir's
`info/exclude`); it is never committed and **does not exist in a fresh clone or establish remote
availability**. This repository is public, which is why references are repo-relative rather than
absolute paths into anyone's home directory.

| Under `_plan018-refs/` | What it is | Copied from |
|---|---|---|
| `corpus/corpus.json` | Historical convenience snapshot only; never runtime/decision authority after V7 | the `NExtSEEK-dev` working clone at staging time |
| `corpus/seed-6c-ntoes.json` | seed-6 rerun review notes (per-case verdicts) | same |
| `corpus/nessie-*.html` | the two Nessie review reports | same |
| `reviews/PLAN018-*.md` | the 12 plan-018 vetting documents | the maintainer's private state directory |
| `port-source/functional_evaluator{,_models}.py` | Stage C judge port sources at `dcca50c` | the standalone `dmac-assistant` clone |
| `OPS-TESTING-HARNESSES.md` | harness inventory; section 5 records the competing family vocabularies | maintainer's work directory |
| `plan-backups/*.bak-pre-plan-vetting-*.md` | pre-vetting snapshots of this plan | `docs/superpowers/plans/` |

Copies were verified byte-identical to their sources at staging time. If a source changes, the
copy here does not follow it — re-stage before relying on one for a DONE claim. Before this plan can
be released for execution, the V5 evidence manifest must be materialized at stable registry IDs/
URIs accessible to the authorized verifier, fresh-retrieved, and hash-validated; box-local copies
alone cannot close that release gate.

### Paired-run delivery (dev box, outside the repository)

The 2026-08-07 paired E2E delivery is staged on the same box at
`~/work/NExtSEEK-dev/testquestions-2026-08-07/`, mirroring the `nessie-bayes-full-2026-08-06`
convention. It sits **outside this repository** — it is not under `_plan018-refs/`, is not covered
by the gitdir `info/exclude` above, and is not reachable from a clone.

| Under `~/work/NExtSEEK-dev/testquestions-2026-08-07/` | What it is |
|---|---|
| `testquestions.zip` | the delivery; sha256 `4e7c57a1c04015fb…`, 66,473,692 bytes; 2,432 files across `set1_toosimple`, `set2_ccbleed`, `set3_final` and `corpus/` |
| `MANIFEST.json` | per-file manifest of all 2,432 archive members — sha256, content-detected type, zip-entry dates, row counts, JSON-schema sidecar pointers |
| `build_manifest.py` | regenerates the manifest |
| `artifact_validity_set3_final.csv`, `artifact_detail_set3_final.csv` | the V9-G results, identical to the committed copies under `nextseek_api/eval/` |

All five were sha256-verified byte-identical to their laptop sources after transfer. The manifest is
the join between a file on disk and what it is: every artifact V9's Task 7b validates has an entry
there carrying its checksum, detected type and provenance date. The same caveat as above applies —
these box-local copies do not close the V5 release gate.

## File Structure

**Historical/incomplete under V4.** This retained table describes the original decomposition only.
The approved V4-0 ownership map must supersede and extend it before any implementation begins.

| Path | Responsibility |
|---|---|
| `nextseek_api/assistant/models_db.py` | **Modify.** Add `TurnLedger` model. |
| `nextseek_api/migrations/0010_turn_ledger.py` | **Historical prospective name only.** Actual filename/dependency comes from the V4-0 merged migration graph. |
| `nextseek_api/cc_assistant/turn_ledger.py` | **Create.** Single write path for ledger rows; both route writers call it. |
| `dmac_assistant/baml_src/router.baml` | **Modify.** `task_family` on the decision. |
| `docker/cc-runtime/baml_src/router.baml` | **Modify.** Byte-identical mirror. |
| `nextseek_api/cc_assistant/router.py` | **Modify.** Surface + fall back the family. |
| `nextseek_api/cc_assistant/family_labels.py` | **Create.** Validate/read the latest corpus family set and construct the runtime TypeBuilder snapshot. |
| `nextseek_api/eval/` | **Create.** Vendored evaluation package (tools + fit packages). |
| `nextseek_api/eval/export.py` | **Create.** Ledger → versioned eval rows. |
| `nextseek_api/eval/artifact_validity.py` | **Create.** Deterministic artifact axis; kind-agnostic per V9-A/V9-B. |
| `nextseek_api/eval/artifact_sources.py` | **Create.** Live-turn and exported-run artifact adapters (V9-F). |
| `nextseek_api/eval/judge_cache.py` | **Create.** Fingerprint, lookup, invalidation, partial-failure policy. |
| `nextseek_api/eval/tasks.py` | **Create.** Celery nightly task, spend cap, force path. |
| `nextseek_api/eval/publish.py` | **Create.** Posterior store writer. |
| `nextseek_api/cc_assistant/playbook.py` | **Create.** Consumer (a). |
| `nextseek_api/cc_assistant/tests/test_*.py` | **Create.** One test module per task. |

---

## Phase 1 — Online foundation (no paid calls anywhere in this phase)

### Task 0: Shared test fixtures

**V5 REPLACEMENT — the retained historical Task 0 body below is non-executable.** The original
“runs first” order, full fixture code block, Steps 1–4, collection-only success condition, and note
that dependent bodies may fail are **VOID**. They must not be pasted or used as a DONE oracle.

Task 0 now creates only `nextseek_api/cc_assistant/tests/conftest.py`, which pytest discovers
directly. At the initial/base stage it may import only types already present on the approved base.
It records the nine inherited `nextseek_api/conftest.py` fixture names and fails on a shadowing
collision. Every fixture that depends on `FamilyPosterior`, `turn_ledger`, `nextseek_api.eval`, or
another later product is added with its owning task after that product exists; no placeholder,
lazy body that raises, or module-level import of a future type is permitted.

Maintain `evidence/task00-fixture-owners.json`, mapping every planned fixture to owner task, source
file/hash, first available source SHA, and at least one semantic consumer test. At each owner task,
run collection plus those consumers under the default network-deny harness and update the manifest.
Deleting/emptying a fixture, making it raise, removing its consumer, shadowing a parent fixture, or
allowing network access must turn the gate red. Evidence binds exact argv, nonzero selected/passed
counts, zero unexpected skip/xfail/deselection, source/diff/image hashes, conftest hash, and manifest
hash.

**V5 Task 0 DONE:** the base-only conftest imports and collects on the approved base, every currently
available fixture executes through a semantic consumer, and the source-derived fixture/consumer
inventory has no unused, missing, shadowed, placeholder, or future-type fixture. The complete
planned fixture set closes incrementally with its owner tasks; collection alone never marks it
complete.

**Historical body retained below for provenance only; every command and success condition in it is
void under V5.**

**Files:**
- Create: `nextseek_api/cc_assistant/tests/conftest.py`

**Verified precondition (base `dfbccaf`).** `nextseek_api/cc_assistant/tests/conftest.py` does
**not** exist — absent on disk and in the tree on every NExtSEEK worktree — so this task creates it
rather than modifying it. `nextseek_api/cc_assistant/tests/__init__.py` is also absent, so that
directory is **not an importable package**: a `from .conftest_eval import *` style registration
would fail at collection. pytest discovers `conftest.py` per directory with no package requirement,
so the fixtures are defined directly in the new file and no separate module is introduced.

`nextseek_api/conftest.py` already exists one level up and defines `api_user`, `admin_user`,
`api_client`, `auth_client`, `admin_client`, `factory`, `mock_seek_client`, `mock_seek_auth` and
`mock_assistant_permission`. pytest walks upward, so those remain available to these tests. None of
the names below collide with them; a task that adds a colliding name must rename rather than shadow.

**Interfaces:**
- Produces: `eval_row`, `one_row`, `many_rows`, `cached_rows`, `fake_judge`, `failing_judge`,
  `live_judge`, `fit_result`, `sparse_fit_result`, `posteriors`, `sparse_posteriors`,
  `brittle_posterior`, `sparse_posterior`, `no_posteriors`, `user_a`, `judgments_two_projects`,
  `query_turn_factory`, `cc_turn_factory`, `one_real_turn`.

- [ ] **Step 1: Write the fixtures**

```python
# nextseek_api/cc_assistant/tests/conftest.py
"""Shared fixtures for the evaluation-loop tests.

`fake_judge` never performs a network call. `live_judge` is the only fixture that
can, and it is reachable solely from the RUN_EVAL_LIVE-gated module.
"""
from dataclasses import dataclass, field

import pytest
from django.contrib.auth import get_user_model

from nextseek_api.assistant.models_db import ChatSession, FamilyPosterior
from nextseek_api.cc_assistant.turn_ledger import record_turn
from nextseek_api.eval.export import EvalRow, export_rows


@pytest.fixture
def user_a(db):
    return get_user_model().objects.create(username="user_a")


@pytest.fixture
def eval_row(db):
    s = ChatSession.objects.create()
    record_turn(str(s.session_id), 1, "container_cc", "baml", "sample_search", "baml")
    return export_rows()[0]


@pytest.fixture
def one_row(eval_row):
    return [eval_row]


@pytest.fixture
def many_rows(db):
    s = ChatSession.objects.create()
    for i in range(1, 6):
        record_turn(str(s.session_id), i, "container_cc", "baml", "sample_search", "baml")
    return export_rows()


@pytest.fixture
def cached_rows(many_rows):
    from nextseek_api.eval.judge_cache import record_judgment
    v = dict(prompt_version="p1", model_id="m1", schema_version=2)
    for r in many_rows:
        record_judgment(r, verdict={"ok": True}, **v)
    return many_rows


@dataclass
class _FakeJudge:
    cost_per_call: float = 0.0
    calls: int = 0

    def __call__(self, row):
        self.calls += 1
        return {"ok": True}, self.cost_per_call


@pytest.fixture
def fake_judge():
    def _make(cost_per_call=0.0):
        return _FakeJudge(cost_per_call=cost_per_call)
    return _make


@pytest.fixture
def failing_judge():
    def _judge(row):
        raise RuntimeError("judge exploded")
    return _judge


@dataclass
class _Group:
    name: str
    route: str = "container_cc"
    posterior_mean: float = 0.97
    band: str = "Reliable"
    n_total: int = 40


@dataclass
class _FitResult:
    groups: list = field(default_factory=list)


@pytest.fixture
def fit_result():
    return _FitResult(groups=[_Group("batch_upload_preparation"), _Group("cc_sandbox_contract")])


@pytest.fixture
def sparse_fit_result():
    return _FitResult(groups=[
        _Group("cross_session_memory", route="nextseek_query", posterior_mean=0.5,
               band="TooUncertain", n_total=2)
    ])


def _posterior(**kw):
    base = dict(task_family="batch_upload_preparation", route="container_cc",
                posterior_mean=0.97, band="Reliable", n_total=40)
    base.update(kw)
    return FamilyPosterior.objects.create(**base)


@pytest.fixture
def posteriors(db):
    return [_posterior(), _posterior(task_family="cc_sandbox_contract")]


@pytest.fixture
def brittle_posterior(db):
    return _posterior(band="Brittle", posterior_mean=0.62)


@pytest.fixture
def sparse_posterior(db):
    return _posterior(task_family="cross_session_memory", route="nextseek_query",
                      band="TooUncertain", n_total=2)


@pytest.fixture
def sparse_posteriors(sparse_posterior):
    return [sparse_posterior]


@pytest.fixture
def no_posteriors(db):
    FamilyPosterior.objects.all().delete()
    return []


@pytest.fixture
def judgments_two_projects(db, user_a):
    """Two projects; only project 1 belongs to the requesting user's scope."""
    from nextseek_api.eval.judge_cache import record_judgment
    v = dict(prompt_version="p1", model_id="m1", schema_version=2)
    for project_id, marker in ((1, "PROJECT_1_STUDY"), (2, "PROJECT_2_SECRET_STUDY")):
        s = ChatSession.objects.create(user=user_a if project_id == 1 else None)
        record_turn(str(s.session_id), 1, "container_cc", "baml", "batch_upload_preparation", "baml")
        row = export_rows()[-1]
        record_judgment(row, verdict={"ok": False, "note": marker}, **v)
    return True


@pytest.fixture
def query_turn_factory(db):
    def _make(session, turn_number, query, fail=False):
        record_turn(str(session.session_id), turn_number, "nextseek_query", "heuristic", None, None)
    return _make


@pytest.fixture
def cc_turn_factory(db):
    def _make(session, turn_number, query, fail=False):
        record_turn(str(session.session_id), turn_number, "container_cc",
                    "forced" if fail else "baml", None, None)
    return _make
```

- [ ] **Step 2: Confirm no registration import is needed**

There is nothing to register. pytest loads `conftest.py` automatically for every test in that
directory and below. Do **not** add `from .conftest_eval import *` or any relative import: that
directory has no `__init__.py`, so a relative import raises at collection time and takes the whole
`cc_assistant` suite down with it.

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/ --collect-only -q 2>&1 | tail -3`
Expected: collection succeeds; no `ImportError`, no `attempted relative import`.

- [ ] **Step 3: Verify the fixtures load**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/ --collect-only -q 2>&1 | tail -5 | tee evidence/task00.log`
Expected: collection succeeds with no fixture errors

- [ ] **Step 4: Commit**

```bash
git add nextseek_api/cc_assistant/tests/conftest.py
git commit -m "test(eval): shared fixtures for the evaluation-loop test suite"
```

**Success condition:** Met only if `pytest nextseek_api/cc_assistant/tests/ --collect-only -q` exits 0 with output at `evidence/task00.log`, and no test in the suite reports `fixture '<name>' not found`.

**Note:** `eval_row`, `one_row`, `many_rows` and `cached_rows` depend on Tasks 1, 2 and 7. Write the
file now, but expect collection of those specific fixtures to fail until those tasks land — that is
why Step 3 checks collection of the suite, not execution.

**Rollback:** `git revert`.

---

### Task 1: Turn ledger model and migration

**Files:**
- Modify: `nextseek_api/assistant/models_db.py`
- Create: `nextseek_api/migrations/0010_turn_ledger.py`
- Test: `nextseek_api/cc_assistant/tests/test_turn_ledger_model.py`

**Interfaces:**
- Consumes: `ChatSession` (`models_db.py:7`), the charset helper `nextseek_api/migrations/_cc_transcript_heal.py:85-97`.
- Produces: `TurnLedger` with fields `session` (FK→ChatSession), `turn_number` (int), `route`
  (str), `route_source` (str), `task_family` (str, nullable), `family_source` (str, nullable),
  `created_at`; unique constraint `("session", "turn_number")`.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_turn_ledger_model.py
import pytest
from django.db import IntegrityError
from nextseek_api.assistant.models_db import ChatSession, TurnLedger

pytestmark = pytest.mark.django_db


def _session():
    return ChatSession.objects.create()


def test_ledger_row_is_addressable_by_session_and_turn():
    s = _session()
    TurnLedger.objects.create(
        session=s, turn_number=1, route="nextseek_query",
        route_source="baml", task_family="sample_search", family_source="baml",
    )
    row = TurnLedger.objects.get(session=s, turn_number=1)
    assert row.route == "nextseek_query"
    assert row.task_family == "sample_search"


def test_duplicate_turn_number_in_one_session_is_rejected():
    s = _session()
    TurnLedger.objects.create(session=s, turn_number=1, route="container_cc",
                              route_source="forced", task_family=None, family_source=None)
    with pytest.raises(IntegrityError):
        TurnLedger.objects.create(session=s, turn_number=1, route="container_cc",
                                  route_source="forced", task_family=None, family_source=None)


def test_same_turn_number_in_different_sessions_is_allowed():
    a, b = _session(), _session()
    TurnLedger.objects.create(session=a, turn_number=1, route="nextseek_query",
                              route_source="heuristic", task_family=None, family_source=None)
    TurnLedger.objects.create(session=b, turn_number=1, route="nextseek_query",
                              route_source="heuristic", task_family=None, family_source=None)
    assert TurnLedger.objects.filter(turn_number=1).count() == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_turn_ledger_model.py -v`
Expected: FAIL — `ImportError: cannot import name 'TurnLedger'`

- [ ] **Step 3: Add the model**

```python
# nextseek_api/assistant/models_db.py — append
class TurnLedger(models.Model):
    """Durable per-turn identity. The chat_log turn number lives only inside a JSON
    blob, so it cannot be a foreign-key target; this table is that missing row."""

    session = models.ForeignKey(
        ChatSession, on_delete=models.CASCADE, related_name="turn_ledger"
    )
    turn_number = models.IntegerField()
    route = models.CharField(max_length=64)
    route_source = models.CharField(max_length=32)
    task_family = models.CharField(max_length=128, null=True, blank=True)
    family_source = models.CharField(max_length=32, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "turn_number"], name="uniq_turn_per_session"
            )
        ]
        indexes = [models.Index(fields=["task_family", "route"])]
```

- [ ] **Step 4: Generate and harden the migration**

Run: `docker exec -w /app nextseek uv run --no-sync python manage.py makemigrations nextseek_api --name turn_ledger`

Then edit the generated `0010_turn_ledger.py` to run the charset alignment **before** the FK is created, mirroring `nextseek_api/migrations/0008_heal_cc_transcript_fk.py`:

```python
# nextseek_api/migrations/0010_turn_ledger.py — add above the CreateModel operation
from nextseek_api.migrations._cc_transcript_heal import align_charset_for_fk

operations = [
    migrations.RunPython(align_charset_for_fk, migrations.RunPython.noop),
    # ... generated CreateModel + AddConstraint follow ...
]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_turn_ledger_model.py -v 2>&1 | tee evidence/task01.log`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/assistant/models_db.py nextseek_api/migrations/0010_turn_ledger.py nextseek_api/cc_assistant/tests/test_turn_ledger_model.py
git commit -m "feat(eval): add TurnLedger table for durable per-turn identity"
```

**Success condition:** Met only if `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_turn_ledger_model.py -v` exits 0, its output is saved to `evidence/task01.log`, `nextseek_api/migrations/0010_turn_ledger.py` exists, and `docker exec -w /app nextseek uv run --no-sync python manage.py sqlmigrate nextseek_api 0010` prints a `UNIQUE` constraint on `(session_id, turn_number)`.

**Failure conditions:** migration errors 3780 (charset alignment missing/misordered); unique constraint absent from `sqlmigrate` output.

**Rollback:** `git revert` the commit; the migration is additive, so `migrate nextseek_api 0009` reverses it.

---

### Task 2: Ledger write path, called by both routes

**Files:**
- Create: `nextseek_api/cc_assistant/turn_ledger.py`
- Test: `nextseek_api/cc_assistant/tests/test_turn_ledger_writer.py`

**Interfaces:**
- Consumes: `TurnLedger` from Task 1.
- Produces: `record_turn(session_id: str, turn_number: int, route: str, route_source: str,
  task_family: str | None, family_source: str | None) -> TurnLedger`, and `LedgerCollision`
  (raised on a duplicate).

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_turn_ledger_writer.py
import pytest
from nextseek_api.assistant.models_db import ChatSession, TurnLedger
from nextseek_api.cc_assistant.turn_ledger import record_turn, LedgerCollision

pytestmark = pytest.mark.django_db


def test_record_turn_persists_a_row():
    s = ChatSession.objects.create()
    row = record_turn(str(s.session_id), 1, "nextseek_query", "baml", "sample_search", "baml")
    assert TurnLedger.objects.filter(pk=row.pk).exists()


def test_concurrent_same_turn_number_raises_collision_not_integrity_error():
    s = ChatSession.objects.create()
    record_turn(str(s.session_id), 1, "container_cc", "baml", "sample_search", "baml")
    with pytest.raises(LedgerCollision):
        record_turn(str(s.session_id), 1, "container_cc", "baml", "sample_search", "baml")


def test_null_family_is_allowed_with_a_source_recorded():
    s = ChatSession.objects.create()
    row = record_turn(str(s.session_id), 2, "container_cc", "heuristic", None, None)
    assert row.task_family is None
    assert row.route_source == "heuristic"
    assert row.family_source is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_turn_ledger_writer.py -v`
Expected: FAIL — `ModuleNotFoundError: nextseek_api.cc_assistant.turn_ledger`

- [ ] **Step 3: Write the implementation**

```python
# nextseek_api/cc_assistant/turn_ledger.py
"""Single write path for the per-turn ledger.

Both route writers call this. The turn number is assigned upstream by a
read-modify-write over session state that holds no lock, so two concurrent
completions on one session can compute the same value. The unique constraint
surfaces that as LedgerCollision rather than letting it pass silently.
"""
from django.db import IntegrityError, transaction

from nextseek_api.assistant.models_db import TurnLedger


class LedgerCollision(RuntimeError):
    """Two turns claimed the same (session, turn_number)."""


def record_turn(session_id, turn_number, route, route_source, task_family, family_source):
    try:
        with transaction.atomic():
            return TurnLedger.objects.create(
                session_id=session_id,
                turn_number=turn_number,
                route=route,
                route_source=route_source,
                task_family=task_family,
                family_source=family_source,
            )
    except IntegrityError as exc:
        raise LedgerCollision(
            f"turn {turn_number} already recorded for session {session_id}"
        ) from exc
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_turn_ledger_writer.py -v 2>&1 | tee evidence/task02.log`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/cc_assistant/turn_ledger.py nextseek_api/cc_assistant/tests/test_turn_ledger_writer.py
git commit -m "feat(eval): single write path for turn ledger rows"
```

**Success condition:** Met only if the pytest command above exits 0, output saved to `evidence/task02.log`, and the collision test demonstrably raises `LedgerCollision` rather than `IntegrityError`.

**Failure conditions:** a bare `IntegrityError` escaping `record_turn`; a write outside a transaction.

**Rollback:** `git revert`; nothing else calls this module yet.

---

### Task 3: Classification call returns the task family

**V7 decisions recorded; still BLOCKED by V4-0 execution authorization and base reconciliation.**
The label set is every family key in the latest compatible harness-owned corpus, and the generation
mechanism is runtime `@@dynamic` plus `TypeBuilder`.

**Files:**
- Create: `nextseek_api/cc_assistant/family_labels.py` (the single read seam for the approved set)
- Modify: `dmac_assistant/baml_src/router.baml`
- Modify: `docker/cc-runtime/baml_src/router.baml` (byte-identical)
- Modify: `nextseek_api/cc_assistant/router.py`
- Test: `nextseek_api/cc_assistant/tests/test_router_family.py`

**Interfaces:**
- Consumes: every key and description under the latest compatible `nessie_tests` corpus's
  `families` object. It is read through `family_labels`, never inlined, and never from
  `dmac_assistant/build_context/route_capabilities.json`.
- Produces: `family_labels.corpus_snapshot()`, `family_labels.declared_labels() -> set[str]`, and
  `family_labels.type_builder(snapshot)`; on the classification result, `task_family: str | None`
  plus `family_source: str | None` (`"baml"` after successful classification, otherwise `None`),
  taxonomy/corpus identity, and unrelated state. Routing separately produces `route_source`.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_router_family.py
import hashlib
from pathlib import Path

from nextseek_api.cc_assistant.family_labels import corpus_snapshot, declared_labels, type_builder
from nextseek_api.cc_assistant.baml_introspect import declared_family_members

_REPO = Path(__file__).resolve().parents[3]
_A = _REPO / "dmac_assistant" / "baml_src" / "router.baml"
_B = _REPO / "docker" / "cc-runtime" / "baml_src" / "router.baml"


def test_classification_decision_declares_a_family_field():
    assert "task_family" in _A.read_text()


def test_both_router_baml_copies_stay_byte_identical():
    assert hashlib.sha256(_A.read_bytes()).hexdigest() == \
           hashlib.sha256(_B.read_bytes()).hexdigest()


def test_effective_enum_equals_every_declared_corpus_family_in_both_directions():
    """Effective runtime enum equals every declared corpus family; never a count."""
    snapshot = corpus_snapshot()
    assert declared_family_members(type_builder(snapshot)) == declared_labels(snapshot)
def test_no_module_in_this_seam_reads_the_routing_capabilities_file():
    """route_capabilities.json is live routing config, not a question taxonomy."""
    for name in ("family_labels.py", "baml_introspect.py"):
        src = (_REPO / "nextseek_api" / "cc_assistant" / name).read_text()
        assert "route_capabilities" not in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest nextseek_api/cc_assistant/tests/test_router_family.py -v`
Expected: FAIL — `ModuleNotFoundError: nextseek_api.cc_assistant.family_labels`

- [ ] **Step 3: Add the family field to the classification contract**

Add a family-valued field to the decision object returned by the **classification** function
(V3-B), not by the routing function.

**Do not reuse the identifier `TaskFamily`.** `class TaskFamily` already exists at
`dmac_assistant/baml_src/router.baml:1` and describes a *route's advertised* family, which is a
different thing; V2-T3 forbids redeclaring it. Use a distinct name — `ClassifiedFamily` is the
working name — so the two types cannot be confused in the generated client.

Declare `ClassifiedFamily` with `@@dynamic` and no static family members. For one validated corpus
snapshot, add every `families` key through `TypeBuilder`, attach that family's `description`, and
pass the builder through `baml_options` on the classification call. The static `.baml` pair remains
byte-identical. `declared_family_members()` introspects the effective builder output, not source
text. The wrapper validates the returned enum/string against the same snapshot and records its
corpus/taxonomy identity. Missing builder injection, duplicate/invalid identifiers, empty
descriptions, an incompatible corpus schema, a returned value outside the snapshot, or a corpus
change within one classification operation fails classification and records no family.

- [ ] **Step 4: Surface it in the Python wrapper**

In `nextseek_api/cc_assistant/router.py`, add `task_family` and `family_source` to the decision
dataclass, populate them from the classification result on the BAML path, and set
`family_source="baml"` there. The heuristic route path remains family `None`; V7-C forbids Task 4
from filling it.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest nextseek_api/cc_assistant/tests/test_router_family.py -v 2>&1 | tee evidence/task03.log`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/cc_assistant/family_labels.py nextseek_api/cc_assistant/baml_introspect.py dmac_assistant/baml_src/router.baml docker/cc-runtime/baml_src/router.baml nextseek_api/cc_assistant/router.py nextseek_api/cc_assistant/tests/test_router_family.py
git commit -m "feat(router): return the classified task family from the classification call"
```

**Success condition:** Met only if `pytest nextseek_api/cc_assistant/tests/test_router_family.py -v` exits 0 with output at `evidence/task03.log`; `pytest nextseek_api/cc_assistant/tests/test_baml_router_schema.py -v` still exits 0 (the pre-existing byte-identity and prompt-region pins must not regress); `pytest nextseek_api/cc_assistant/tests/test_f_constraint_pins.py -v` still exits 0 **without the pinned file having been touched**; and `grep -rn "route_capabilities" nextseek_api/cc_assistant/family_labels.py nextseek_api/cc_assistant/baml_introspect.py nextseek_api/cc_assistant/tests/test_router_family.py` returns no matches.

**Failure conditions:** the two BAML copies diverging; a second LLM call on the steady-state path
(V3-B); any literal family count/member list or include/exclude layer; the new enum reusing
`TaskFamily`; labels read from `route_capabilities.json` or the stale `_plan018-refs` copy; static
enum emission; missing TypeBuilder injection; or accepting a returned label outside the exact
snapshot used for that call.

**Rollback:** `git revert`; no runtime consumer reads the field until Task 5.

---

### Task 4: OMITTED — no deterministic family for non-BAML turns (V7-C)

The maintainer never approved deterministic phrase/keyword family inference. This task creates no
file, test, routing hook, or replacement classifier. The binding behavior is:

- preserve the existing heuristic as a route-only safety fallback, byte-for-byte;
- for forced paired corpus arms, use the corpus row's declared family and record
  `family_source="corpus"` plus `route_source="forced"`;
- for successful LLM classification, validate against the exact runtime corpus snapshot and record
  `family_source="baml"`; and
- when classification did not run or failed, record `task_family=None` and `family_source=None`.

**DONE:** this omission is enforced by negative tests proving there is no family keyword/phrase
table or `family_fallback` module, the existing heuristic route behavior/source hash is unchanged,
forced corpus rows preserve both provenance dimensions, and classification failure cannot enter a
family posterior. Any inferred family on a non-classified turn fails the gate.

---

### Task 5: Both route writers record a ledger row

**Files:**
- Modify: `nextseek_api/services/cc_assistant.py` (container path turn completion)
- Modify: `chat_nextseek/src/chat_nextseek/chat_memory.py` call site in `nextseek_api/assistant/session_adapter.py` (query path)
- Test: `nextseek_api/cc_assistant/tests/test_ledger_written_on_both_routes.py`

**Interfaces:**
- Consumes: `record_turn` (Task 2), classification `task_family` / `family_source` (Task 3), and
  routing `route_source`. Task 4 produces nothing.
- Produces: one `TurnLedger` row per completed turn on either route.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_ledger_written_on_both_routes.py
import pytest
from nextseek_api.assistant.models_db import ChatSession, TurnLedger

pytestmark = pytest.mark.django_db


def test_query_route_turn_creates_a_ledger_row(query_turn_factory):
    s = ChatSession.objects.create()
    query_turn_factory(session=s, turn_number=1, query="find me all PBMCs")
    row = TurnLedger.objects.get(session=s, turn_number=1)
    assert row.route == "nextseek_query"
    assert row.family_source in {"baml", None}
    assert row.route_source in {"baml", "heuristic", "posterior", "sticky"}


def test_container_route_turn_creates_a_ledger_row(cc_turn_factory):
    s = ChatSession.objects.create()
    cc_turn_factory(session=s, turn_number=1, query="write a python script")
    row = TurnLedger.objects.get(session=s, turn_number=1)
    assert row.route == "container_cc"


def test_a_failed_turn_still_creates_a_ledger_row(cc_turn_factory):
    """Failures must not be invisible to the evaluator — that biases every rate."""
    s = ChatSession.objects.create()
    cc_turn_factory(session=s, turn_number=1, query="boom", fail=True)
    assert TurnLedger.objects.filter(session=s, turn_number=1).exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_ledger_written_on_both_routes.py -v`
Expected: FAIL — `TurnLedger.DoesNotExist`

- [ ] **Step 3: Add the ledger write to both writers**

At each site that appends a turn to the JSON envelope, call `record_turn(...)` inside the **same**
`transaction.atomic()` block as the envelope save, so the two cannot diverge. Catch
`LedgerCollision` and log it; do not fail the user's turn because of a ledger collision.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/ -k "ledger or router or family" -v 2>&1 | tee evidence/task05.log`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/services/cc_assistant.py nextseek_api/assistant/session_adapter.py nextseek_api/cc_assistant/tests/test_ledger_written_on_both_routes.py
git commit -m "feat(eval): record a ledger row on every turn, both routes"
```

**Success condition:** Met only if the pytest command exits 0, output saved to `evidence/task05.log`, the failure-path test passes (a failed turn still produces a row), and the full hermetic suite `pytest nextseek_api/cc_assistant/tests/` exits 0 with no new failures versus the pre-task baseline recorded in `evidence/baseline.log`.

**Failure conditions:** any turn type that completes without a ledger row; a ledger write outside the envelope's transaction; a user-visible error caused by a collision.

**Rollback:** `git revert`; the ledger becomes write-free again and nothing downstream exists yet.

---

## Phase 2 — Vendoring (gate: no task past here may reference a `dmac-assistant` checkout)

### Task 6: Vendor the evaluation package into this repository

**Files:**
- Create: `nextseek_api/eval/` (package: judge tooling + the four fit packages + their configs and templates)
- Modify: `pyproject.toml` (numerical dependencies)
- Create: `docker/eval/Dockerfile` (NExtSEEK-owned image, built from this repo's context)
- Test: `nextseek_api/cc_assistant/tests/test_eval_vendoring.py`

**Interfaces:**
- Produces: `nextseek_api.eval.*` importable with no `dmac_assistant` eval dependency; a `docker/eval/Dockerfile` whose build context is this repository.

**V9-A carve-out.** `tools/hibayes/artifact_validator.py` is **not** ported — it is replaced by
`nextseek_api/eval/artifact_validity.py` per V9-A, because its `task_family -> ArtifactKind`
dispatch requires a new hardcoded branch for every report type. Its enum surface (`ArtifactStatus`,
`ArtifactKind`) **is** ported unchanged so results stay comparable with prior runs. Everything else
in scope here — the judge, the four fit packages, their configs and templates, and
`docker/eval/Dockerfile` — is ported as written.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_eval_vendoring.py
import importlib
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]


def test_eval_package_is_importable():
    assert importlib.import_module("nextseek_api.eval") is not None


def test_the_dangling_exporter_reference_is_now_satisfied():
    """The vendored judge contract pins tools.hibayes.exporter.FailureMode.
    Before this task that module did not exist anywhere in the tree."""
    mod = importlib.import_module("nextseek_api.eval.exporter")
    assert hasattr(mod, "FailureMode")


def test_no_module_imports_from_a_dmac_assistant_eval_checkout():
    offenders = []
    for p in (_REPO / "nextseek_api" / "eval").rglob("*.py"):
        text = p.read_text()
        if "dmac_assistant.eval" in text or "from tools.hibayes" in text:
            offenders.append(str(p.relative_to(_REPO)))
    assert offenders == [], f"external eval imports remain: {offenders}"


def test_eval_dockerfile_builds_from_this_repo_not_a_bind_mount():
    df = (_REPO / "docker" / "eval" / "Dockerfile").read_text()
    assert "COPY nextseek_api/eval" in df
    assert "/work/src" not in df, "still expects a bind-mounted external checkout"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest nextseek_api/cc_assistant/tests/test_eval_vendoring.py -v`
Expected: FAIL — `ModuleNotFoundError: nextseek_api.eval`

- [ ] **Step 3: Copy the source in, preserving behaviour**

Copy from the `dmac-assistant` checkout **once**, verbatim, adjusting only import paths:
- `tools/hibayes/{exporter,expected_behavior,functional_inputs,enums}.py` → `nextseek_api/eval/`
  — **`artifact_validator.py` is deliberately absent from this list (V9-A).** Copying it
  reintroduces the `task_family -> ArtifactKind` dispatch V9 removes; `artifact_validity.py`
  (Task 7b) replaces it.
- `tools/e2e/functional_evaluator.py` (local copy: `_plan018-refs/port-source/functional_evaluator.py`) → `nextseek_api/eval/judge.py`
- `tools/e2e/functional_evaluator_models.py` (local copy:
  `_plan018-refs/port-source/functional_evaluator_models.py`) → `nextseek_api/eval/` —
  **required, and previously missing from this list.** The copied `enums.py` re-exports
  `ArtifactStatus`, `ArtifactKind` and `ExpectedBehavior` from it (`tools/hibayes/enums.py:22`), so
  omitting it leaves `enums.py` importing a module that does not exist in this repository. It is
  also the enum surface V9-A requires be ported unchanged.
- `src/dmac_assistant/eval/hibayes_{runtime_reliability,artifact_validity,functional_usefulness,combined_report}/` → `nextseek_api/eval/fit/` including their `config/*.yaml` and templates

Do not change thresholds, band logic, model selection, or control flow. Record the source commit in a
header comment on each copied file: `# vendored from dmac-assistant @ dcca50c — do not diverge without a spec amendment`.

- [ ] **Step 4: Add the numerical dependencies and write the image**

```dockerfile
# docker/eval/Dockerfile — NExtSEEK-owned; build context is the repo root
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN pip install --no-cache-dir uv && uv sync --frozen --group eval
COPY nextseek_api/eval /app/nextseek_api/eval
ENTRYPOINT ["uv", "run", "--no-sync", "python", "-m", "nextseek_api.eval.fit"]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest nextseek_api/cc_assistant/tests/test_eval_vendoring.py -v 2>&1 | tee evidence/task06.log`
Expected: 4 passed

- [ ] **Step 6: Prove it builds without the external checkout**

Run: `docker build -f docker/eval/Dockerfile -t nextseek-eval:dev . 2>&1 | tee evidence/task06-build.log`
Expected: exit 0

- [ ] **Step 7: Commit**

```bash
git add nextseek_api/eval docker/eval/Dockerfile pyproject.toml uv.lock nextseek_api/cc_assistant/tests/test_eval_vendoring.py
git commit -m "feat(eval): vendor the evaluation pipeline into NExtSEEK"
```

**Success condition:** Met only if `pytest nextseek_api/cc_assistant/tests/test_eval_vendoring.py -v` exits 0 with output at `evidence/task06.log`; `docker build -f docker/eval/Dockerfile .` exits 0 with output at `evidence/task06-build.log`; and `grep -rn "dmac_assistant.eval\|from tools.hibayes" nextseek_api/eval/` returns no matches.

**Failure conditions:** any import reaching outside this repo; a Dockerfile referencing `/work/src` or any host path; a copied file whose control flow or thresholds differ from the source.

**Rollback:** `git revert`; nothing imports `nextseek_api.eval` until Task 7.

---

### Task 7: Export ledger rows to the versioned eval schema

**V8-C REPLACEMENT — the eight-field `EvalRow` in the code block below is superseded and must not
be implemented as written.** It carries no outcome, no cost signal and no artifact facts, so it
cannot support a comparative fit. Implement the seventeen-field row defined in V8-C, with the
derived-target validators and the V8-D disposition mapping. The retained body below is historical:
its step order, commands and success condition still apply, but its row shape does not.

**Files:**
- Create: `nextseek_api/eval/export.py`
- Test: `nextseek_api/cc_assistant/tests/test_eval_export.py`

**Interfaces:**
- Consumes: `TurnLedger` (Task 1).
- Produces: `export_rows(since=None) -> list[EvalRow]`; `EVAL_ROW_SCHEMA_VERSION = 3` (V8-C).

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_eval_export.py
import pytest
from nextseek_api.assistant.models_db import ChatSession
from nextseek_api.cc_assistant.turn_ledger import record_turn
from nextseek_api.eval.export import export_rows, EVAL_ROW_SCHEMA_VERSION

pytestmark = pytest.mark.django_db


def test_schema_is_versioned_and_not_the_legacy_14_column_shape():
    assert EVAL_ROW_SCHEMA_VERSION >= 3  # V8-C; >= 2 could not detect the stale value


def test_every_row_carries_route_and_family_as_separate_columns():
    s = ChatSession.objects.create()
    record_turn(str(s.session_id), 1, "container_cc", "baml", "sample_search", "baml")
    row = export_rows()[0]
    assert row.route == "container_cc"
    assert row.task_family == "sample_search"


def test_forced_turns_are_distinguishable_from_router_chosen_turns():
    s = ChatSession.objects.create()
    record_turn(str(s.session_id), 1, "container_cc", "forced", "sample_search", "corpus")
    row = export_rows()[0]
    assert row.route_source == "forced"
    assert row.family_source == "corpus"


def test_export_is_incremental_by_watermark():
    s = ChatSession.objects.create()
    a = record_turn(str(s.session_id), 1, "nextseek_query", "baml", "sample_search", "baml")
    record_turn(str(s.session_id), 2, "nextseek_query", "sticky", "sample_search", "baml")
    assert len(export_rows(since=a.created_at)) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_eval_export.py -v`
Expected: FAIL — `ModuleNotFoundError: nextseek_api.eval.export`

- [ ] **Step 3: Write the implementation**

```python
# nextseek_api/eval/export.py
"""Ledger -> versioned evaluation rows.

Supersedes the legacy 14-column offline format, which was built for a headless
fixture and carries no route column at all.

HISTORICAL SHAPE — superseded by V8-C. The eight fields below are not sufficient
for a comparative fit; implement the V8-C row instead.
"""
from dataclasses import dataclass

from nextseek_api.assistant.models_db import TurnLedger

EVAL_ROW_SCHEMA_VERSION = 3  # V8-C


@dataclass(frozen=True)
class EvalRow:
    session_id: str
    turn_number: int
    route: str
    route_source: str
    task_family: str | None
    family_source: str | None
    created_at: object
    schema_version: int = EVAL_ROW_SCHEMA_VERSION


def export_rows(since=None):
    qs = TurnLedger.objects.all().order_by("created_at")
    if since is not None:
        qs = qs.filter(created_at__gt=since)
    return [
        EvalRow(
            session_id=str(r.session_id),
            turn_number=r.turn_number,
            route=r.route,
            route_source=r.route_source,
            task_family=r.task_family,
            family_source=r.family_source,
            created_at=r.created_at,
        )
        for r in qs
    ]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_eval_export.py -v 2>&1 | tee evidence/task07.log`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/eval/export.py nextseek_api/cc_assistant/tests/test_eval_export.py
git commit -m "feat(eval): versioned exporter over the turn ledger"
```

**Success condition:** Met only if the pytest command exits 0 with output at `evidence/task07.log`,
and a test proves route, route provenance, family and classification provenance are separate columns;
a forced corpus turn must retain both `route_source="forced"` and `family_source="corpus"`; and the
row's artifact fields are populated from Task 7b rather than defaulted — a row whose
`artifact_status` is absent or hardcoded fails this condition.

**Failure conditions:** route collapsed into family; forced rows indistinguishable; a non-incremental export; artifact fields defaulted rather than computed.

**Rollback:** `git revert`.

---

### Task 7b: Deterministic artifact validity (V9)

Slots between Task 7 and Task 8: the eval row cannot carry V8-C's artifact facts without it, and the
conjunctive outcome cannot gate judge calls without those facts.

**Files:**
- Create: `nextseek_api/eval/artifact_validity.py`
- Create: `nextseek_api/eval/artifact_sources.py`
- Modify: `nextseek_api/eval/export.py` (populate the row's artifact fields)
- Test: `nextseek_api/cc_assistant/tests/test_artifact_validity.py`

**Interfaces:**
- Consumes: `LiveTurnSource` | `ExportedRunSource` (V9-F).
- Produces: `validate_arm(source, arm_key) -> ArmArtifactVerdict(status, artifacts, plan_status, artifact_success)`; module constants `SEVERITY` and `PLAN_STATUS` per V9-C/V9-E.
- Reference implementation: `nextseek_api/eval/artifact_validity_proposal.py` (V9-H) — port from it; do not import it.

- [ ] **Step 1: Write the failing tests.** At minimum:
  `test_kind_never_appears_in_control_flow` (AST assertion: no branch on `ArtifactKind`);
  `test_extension_stripped_xlsx_validates` (a workbook named `all_tables__11`);
  `test_docx_is_not_read_as_a_workbook`;
  `test_worst_status_wins_across_a_mixed_set`;
  `test_required_key_present_with_null_value_is_valid`;
  `test_not_expected_passes_the_gate`;
  `test_indeterminate_yields_null_not_false`;
  `test_both_sources_agree_on_one_run`;
  `test_arm_with_no_artifacts_is_missing_when_runtime_succeeded_and_runtimefailed_otherwise`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_artifact_validity.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement** per V9-A…V9-F, replacing the reference implementation's hardcoded
  delivery path with the two source adapters.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_artifact_validity.py -v 2>&1 | tee evidence/task07b.log`
Expected: 9 passed

- [ ] **Step 5: Regression-pin against the delivered run.** Re-validate `set3_final` through
  `ExportedRunSource` and assert the V9-G table exactly (9/9 `Valid`, 7/7 `Missing`, 2/2
  `RuntimeFailed`, 131/131 `NotExpected`, zero `Indeterminate`).

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/eval/artifact_validity.py nextseek_api/eval/artifact_sources.py nextseek_api/eval/export.py nextseek_api/cc_assistant/tests/test_artifact_validity.py
git commit -m "feat(eval): kind-agnostic deterministic artifact validity"
```

**Success condition:** Met only if the pytest command exits 0 with output at `evidence/task07b.log`;
the V9-G counts reproduce exactly; `grep -n "ArtifactKind" nextseek_api/eval/artifact_validity.py`
returns no line inside a conditional; and `grep -rn "openpyxl" nextseek_api/eval/` returns nothing.

**Failure conditions:** any branch keyed on artifact kind, task family or file extension; `openpyxl`
on this path; `Indeterminate` collapsed to `false`; `not_expected` failing the gate; a validation
rule that differs between the two sources; the bundle skipped without proving subset-hood.

**Rollback:** `git revert`; `export.py` reverts to leaving artifact fields unpopulated, which fails
Task 7's own success condition — so this task cannot be silently dropped.

---

### Task 8: Judgment cache with fingerprint invalidation

**Files:**
- Create: `nextseek_api/eval/judge_cache.py`
- Modify: `nextseek_api/assistant/models_db.py` (add `TurnJudgment`)
- Create: `nextseek_api/migrations/0011_turn_judgment.py`
- Test: `nextseek_api/cc_assistant/tests/test_judge_cache.py`

**Interfaces:**
- Consumes: `TurnLedger`, `EvalRow`.
- Produces: `fingerprint(row, *, prompt_version, model_id, schema_version) -> str`; `needs_judging(rows, ...) -> list[EvalRow]`; `record_judgment(...)`; `record_failure(...)`.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_judge_cache.py
import pytest
from nextseek_api.eval.judge_cache import fingerprint, needs_judging, record_judgment, record_failure

pytestmark = pytest.mark.django_db
_V = dict(prompt_version="p1", model_id="m1", schema_version=2)


def test_fingerprint_changes_when_prompt_version_changes(eval_row):
    a = fingerprint(eval_row, **_V)
    b = fingerprint(eval_row, **{**_V, "prompt_version": "p2"})
    assert a != b


def test_fingerprint_changes_when_model_changes(eval_row):
    assert fingerprint(eval_row, **_V) != fingerprint(eval_row, **{**_V, "model_id": "m2"})


def test_already_judged_row_is_not_rejudged(eval_row):
    record_judgment(eval_row, verdict={"ok": True}, **_V)
    assert needs_judging([eval_row], **_V) == []


def test_a_failed_judgment_is_retried_not_skipped(eval_row):
    """A failure must never look like a completed judgment — that silently
    drops exactly the turns most worth looking at."""
    record_failure(eval_row, error="timeout", **_V)
    assert needs_judging([eval_row], **_V) == [eval_row]


def test_version_bump_invalidates_an_existing_judgment(eval_row):
    record_judgment(eval_row, verdict={"ok": True}, **_V)
    assert needs_judging([eval_row], **{**_V, "prompt_version": "p2"}) == [eval_row]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_judge_cache.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Add the model and migration**

```python
# nextseek_api/assistant/models_db.py — append
class TurnJudgment(models.Model):
    turn = models.ForeignKey(TurnLedger, on_delete=models.CASCADE, related_name="judgments")
    fingerprint = models.CharField(max_length=64, db_index=True)
    verdict = models.JSONField(null=True, blank=True)
    status = models.CharField(max_length=16)  # "ok" | "failed"
    error = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["turn", "fingerprint"], name="uniq_turn_fingerprint")
        ]
```

Then: `docker exec -w /app nextseek uv run --no-sync python manage.py makemigrations nextseek_api --name turn_judgment`

- [ ] **Step 4: Write the cache**

```python
# nextseek_api/eval/judge_cache.py
"""Fingerprinted judgment cache.

A fingerprint covers the row identity AND the prompt, model and schema versions,
so a version bump invalidates cleanly. A failed judgment is stored as a failure
and is re-attempted next run; it is never treated as done. There is no
mtime-based skip anywhere in this module.
"""
import hashlib
import json

from nextseek_api.assistant.models_db import TurnJudgment


def fingerprint(row, *, prompt_version, model_id, schema_version):
    payload = json.dumps(
        {
            "session": row.session_id,
            "turn": row.turn_number,
            "route": row.route,
            "family": row.task_family,
            "prompt_version": prompt_version,
            "model_id": model_id,
            "schema_version": schema_version,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def needs_judging(rows, **versions):
    out = []
    for row in rows:
        fp = fingerprint(row, **versions)
        if not TurnJudgment.objects.filter(fingerprint=fp, status="ok").exists():
            out.append(row)
    return out


def _turn_pk(row):
    from nextseek_api.assistant.models_db import TurnLedger
    return TurnLedger.objects.get(session_id=row.session_id, turn_number=row.turn_number)


def record_judgment(row, *, verdict, **versions):
    TurnJudgment.objects.update_or_create(
        turn=_turn_pk(row), fingerprint=fingerprint(row, **versions),
        defaults={"verdict": verdict, "status": "ok", "error": None},
    )


def record_failure(row, *, error, **versions):
    TurnJudgment.objects.update_or_create(
        turn=_turn_pk(row), fingerprint=fingerprint(row, **versions),
        defaults={"verdict": None, "status": "failed", "error": error},
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_judge_cache.py -v 2>&1 | tee evidence/task08.log`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/eval/judge_cache.py nextseek_api/assistant/models_db.py nextseek_api/migrations/0011_turn_judgment.py nextseek_api/cc_assistant/tests/test_judge_cache.py
git commit -m "feat(eval): fingerprinted judgment cache with fail-retry semantics"
```

**Success condition:** Met only if the pytest command exits 0 with output at `evidence/task08.log`, and specifically that `test_a_failed_judgment_is_retried_not_skipped` and `test_version_bump_invalidates_an_existing_judgment` both pass. Additionally `grep -rn "mtime\|st_mtime\|getmtime" nextseek_api/eval/judge_cache.py` must return no matches.

**Failure conditions:** a failed judgment satisfying the cache; a fingerprint omitting any version input; any mtime-based skip.

**Rollback:** `git revert`; migration is additive.

---

### Task 9: Nightly Celery task with hard spend cap and force path

**Files:**
- Create: `nextseek_api/eval/tasks.py`
- Modify: `nextseek_api/batch_upload/celery_app.py:39-44` (beat schedule)
- Test: `nextseek_api/cc_assistant/tests/test_eval_task.py`

**Interfaces:**
- Consumes: `export_rows`, `needs_judging`, `record_judgment`, `record_failure`.
- Produces: Celery task `eval.nightly_judge`; `run_judging(force: bool = False, cap_usd: float) -> RunReport`.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_eval_task.py
import pytest
from nextseek_api.eval.tasks import run_judging

pytestmark = pytest.mark.django_db


def test_job_pauses_when_the_spend_cap_is_reached(many_rows, fake_judge):
    report = run_judging(cap_usd=0.02, judge=fake_judge(cost_per_call=0.01))
    assert report.paused_on_cap is True
    assert report.judged == 2


def test_job_judges_nothing_when_everything_is_cached(cached_rows, fake_judge):
    report = run_judging(cap_usd=100.0, judge=fake_judge())
    assert report.judged == 0


def test_force_rejudges_cached_rows(cached_rows, fake_judge):
    report = run_judging(cap_usd=100.0, force=True, judge=fake_judge())
    assert report.judged > 0


def test_a_judge_exception_is_recorded_as_failure_not_swallowed(one_row, failing_judge):
    report = run_judging(cap_usd=100.0, judge=failing_judge)
    assert report.failed == 1
    assert report.judged == 0


def test_no_paid_call_happens_without_an_explicit_judge(one_row):
    """The default path must not construct a live client."""
    with pytest.raises(ValueError, match="judge"):
        run_judging(cap_usd=100.0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_eval_task.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the task**

`run_judging` must: export rows since the last watermark; filter through `needs_judging` unless
`force`; call the injected `judge` per row, accumulating cost; stop and set `paused_on_cap` when the
running total would exceed `cap_usd`; record each result via `record_judgment` or `record_failure`;
and return a `RunReport(judged, failed, skipped, cost_usd, paused_on_cap)`. It must raise
`ValueError` if no `judge` is supplied — there is no implicit live client.

- [ ] **Step 4: Register the beat entry**

```python
# nextseek_api/batch_upload/celery_app.py — inside beat_schedule
"eval-nightly-judge": {
    "task": "eval.nightly_judge",
    "schedule": crontab(hour=3, minute=0),
},
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_eval_task.py -v 2>&1 | tee evidence/task09.log`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/eval/tasks.py nextseek_api/batch_upload/celery_app.py nextseek_api/cc_assistant/tests/test_eval_task.py
git commit -m "feat(eval): nightly incremental judging task with spend cap and force path"
```

**Success condition:** Met only if the pytest command exits 0 with output at `evidence/task09.log`; `test_no_paid_call_happens_without_an_explicit_judge` passes; and `docker exec -w /app nextseek uv run --no-sync python -c "from nextseek_api.batch_upload.celery_app import app; print('eval.nightly_judge' in str(app.conf.beat_schedule))"` prints `True`.

**Failure conditions:** any code path constructing a live model client without an injected judge; a cap that is checked after the call rather than before; a swallowed judge exception.

**Rollback:** `git revert`; remove the beat entry.

---

### Task 10: Fit and publish posteriors

**Files:**
- Create: `nextseek_api/eval/publish.py`
- Modify: `nextseek_api/assistant/models_db.py` (add `FamilyPosterior`)
- Create: `nextseek_api/migrations/0012_family_posterior.py`
- Test: `nextseek_api/cc_assistant/tests/test_eval_publish.py`

**Interfaces:**
- Consumes: vendored fit package (Task 6), `TurnJudgment`.
- Produces: `publish(fit_result) -> int`; `FamilyPosterior(task_family, route, posterior_mean, band, n_total, fitted_at)`.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_eval_publish.py
import pytest
from nextseek_api.assistant.models_db import FamilyPosterior
from nextseek_api.eval.publish import publish

pytestmark = pytest.mark.django_db


def test_publish_stores_one_row_per_family_route_pair(fit_result):
    assert publish(fit_result) == len(fit_result.groups)


def test_band_and_n_are_persisted_for_consumers(fit_result):
    publish(fit_result)
    row = FamilyPosterior.objects.first()
    assert row.band in {"Reliable", "Watch", "Brittle", "TooUncertain"}
    assert row.n_total >= 0


def test_a_family_below_the_floor_is_too_uncertain(sparse_fit_result):
    publish(sparse_fit_result)
    assert FamilyPosterior.objects.get(task_family="cross_session_memory").band == "TooUncertain"


def test_republishing_replaces_rather_than_duplicates(fit_result):
    publish(fit_result)
    publish(fit_result)
    assert FamilyPosterior.objects.filter(task_family=fit_result.groups[0].name).count() == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_eval_publish.py -v`
Expected: FAIL — `ImportError: FamilyPosterior`

- [ ] **Step 3: Add the model, migration, and publisher**

`FamilyPosterior` carries `task_family`, `route`, `posterior_mean`, `band`, `n_total`, `fitted_at`,
with a unique constraint on `(task_family, route)`. `publish` upserts one row per fitted group and
returns the count. The band value comes from the vendored fit package unchanged — do not
re-implement banding here.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_eval_publish.py -v 2>&1 | tee evidence/task10.log`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/eval/publish.py nextseek_api/assistant/models_db.py nextseek_api/migrations/0012_family_posterior.py nextseek_api/cc_assistant/tests/test_eval_publish.py
git commit -m "feat(eval): publish per-family posteriors to a consumer-readable table"
```

**Success condition:** Met only if the pytest command exits 0 with output at `evidence/task10.log`, and `grep -rn "0.95\|Reliable" nextseek_api/eval/publish.py` returns no matches (banding must come from the vendored fit code, not be re-implemented in the publisher).

**Failure conditions:** banding logic duplicated in `publish.py`; duplicate rows on republish.

**Rollback:** `git revert`; migration additive.

---

## Phase 3 — Consumers (playbook first, per the maintainer's phasing ruling)

### Task 11: Container playbook consumer

**Files:**
- Create: `nextseek_api/cc_assistant/playbook.py`
- Modify: `nextseek_api/cc_assistant/ns_digest.py` (inject the playbook block)
- Test: `nextseek_api/cc_assistant/tests/test_playbook.py`

**Interfaces:**
- Consumes: `FamilyPosterior` (Task 10), `TurnJudgment` (Task 8).
- Produces: `build_playbook(user, project_ids) -> str`.

Content is aggregate statistics **plus** worked examples; example content is scoped to the
requesting user's own projects, preserving the injection channel's existing per-user scoping.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_playbook.py
import pytest
from nextseek_api.cc_assistant.playbook import build_playbook

pytestmark = pytest.mark.django_db


def test_playbook_includes_aggregate_statistics(posteriors, user_a):
    text = build_playbook(user_a, project_ids=[1])
    assert "batch_upload_preparation" in text
    assert "%" in text


def test_examples_never_come_from_another_users_projects(judgments_two_projects, user_a):
    text = build_playbook(user_a, project_ids=[1])
    assert "PROJECT_2_SECRET_STUDY" not in text


def test_a_too_uncertain_family_is_reported_as_such_not_as_a_rate(sparse_posteriors, user_a):
    text = build_playbook(user_a, project_ids=[1])
    assert "not enough data" in text.lower()


def test_playbook_makes_no_claim_about_the_other_route(posteriors, user_a):
    text = build_playbook(user_a, project_ids=[1]).lower()
    for phrase in ("would have", "instead of", "better than the other route"):
        assert phrase not in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_playbook.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`build_playbook` reads `FamilyPosterior` for aggregate lines, and `TurnJudgment` joined through
`TurnLedger` → `ChatSession` filtered to `project_ids` for worked examples. A family in the
`TooUncertain` band renders as "not enough data yet", never as a rate. No sentence may compare
routes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_playbook.py -v 2>&1 | tee evidence/task11.log`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/cc_assistant/playbook.py nextseek_api/cc_assistant/ns_digest.py nextseek_api/cc_assistant/tests/test_playbook.py
git commit -m "feat(eval): container playbook consumer, project-scoped examples"
```

**Success condition:** Met only if the pytest command exits 0 with output at `evidence/task11.log`, and both `test_examples_never_come_from_another_users_projects` and `test_playbook_makes_no_claim_about_the_other_route` pass.

**Failure conditions:** any example text sourced outside the requesting user's projects; any cross-route comparative claim; a `TooUncertain` family rendered as a rate.

**Rollback:** `git revert`; the injection block disappears and the agent's context returns to its prior shape.

---

### Task 12: Routing risk overlay (flag-gated, default off)

**Files:**
- Create: `nextseek_api/cc_assistant/risk_overlay.py`
- Test: `nextseek_api/cc_assistant/tests/test_risk_overlay.py`

**Interfaces:**
- Consumes: `FamilyPosterior`.
- Produces: `assess(route, task_family) -> RiskVerdict(level, reason, may_reroute=False)`.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_risk_overlay.py
import pytest
from nextseek_api.cc_assistant.risk_overlay import assess

pytestmark = pytest.mark.django_db


def test_brittle_family_is_flagged(brittle_posterior):
    v = assess("container_cc", "batch_upload_preparation")
    assert v.level == "high"


def test_unknown_family_falls_back_to_the_legacy_router(no_posteriors):
    v = assess("container_cc", "cc_sandbox_contract")
    assert v.level == "unknown"


def test_too_uncertain_never_produces_a_confident_verdict(sparse_posterior):
    assert assess("nextseek_query", "cross_session_memory").level == "unknown"


def test_overlay_can_never_authorise_a_reroute(brittle_posterior):
    """Option A: risk given the route taken. No counterfactual, ever."""
    assert assess("container_cc", "batch_upload_preparation").may_reroute is False


def test_overlay_is_disabled_by_default(settings, brittle_posterior):
    settings.NEXTSEEK_RISK_OVERLAY_ENABLED = False
    assert assess("container_cc", "batch_upload_preparation").level == "disabled"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_risk_overlay.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

`assess` returns `disabled` unless the feature flag is on; `unknown` when there is no posterior or
the band is `TooUncertain`; otherwise a level derived from the band. `may_reroute` is a constant
`False` on every path — there is no branch that can set it true.

- [ ] **Step 4: Run tests to verify they pass**

Run: `docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/test_risk_overlay.py -v 2>&1 | tee evidence/task12.log`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/cc_assistant/risk_overlay.py nextseek_api/cc_assistant/tests/test_risk_overlay.py
git commit -m "feat(eval): routing risk overlay, default off, no reroute authority"
```

**Success condition:** Met only if the pytest command exits 0 with output at `evidence/task12.log`, `test_overlay_can_never_authorise_a_reroute` passes, and `grep -n "may_reroute" nextseek_api/cc_assistant/risk_overlay.py` shows the field assigned only the literal `False`.

**Failure conditions:** any code path setting `may_reroute=True`; a confident verdict from a `TooUncertain` band; the overlay active by default.

**Rollback:** `git revert`; nothing consumes it while the flag is off.

---

### Task 13: Coverage gate and gated live end-to-end

**Files:**
- Create: `nextseek_api/cc_assistant/tests/test_eval_live_e2e.py`
- Test: the whole suite

- [ ] **Step 1: Write the gated live test**

Its two fixtures live **in this module**, not in the shared conftest, so no paid client is
constructible from the default fixture set.

```python
# nextseek_api/cc_assistant/tests/test_eval_live_e2e.py
import os
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_EVAL_LIVE") != "1",
    reason="paid live evaluation; set RUN_EVAL_LIVE=1 to opt in",
)


@pytest.fixture
def live_judge():
    """The only fixture in the suite that may perform a paid call.
    Import is deferred so collecting this module never builds a client."""
    from nextseek_api.eval.judge import build_live_judge
    return build_live_judge()


@pytest.fixture
def one_real_turn(db):
    from nextseek_api.assistant.models_db import ChatSession
    from nextseek_api.cc_assistant.turn_ledger import record_turn
    s = ChatSession.objects.create()
    record_turn(str(s.session_id), 1, "container_cc", "baml", "sample_search", "baml")
    return s


@pytest.mark.django_db
def test_one_real_turn_flows_ledger_to_posterior(live_judge, one_real_turn):
    from nextseek_api.eval.tasks import run_judging
    from nextseek_api.eval.publish import publish
    from nextseek_api.assistant.models_db import FamilyPosterior

    report = run_judging(cap_usd=1.00, judge=live_judge)
    assert report.judged == 1
    publish(live_judge.last_fit)
    assert FamilyPosterior.objects.exists()
```

- [ ] **Step 2: Verify it is skipped by default**

Run: `pytest nextseek_api/cc_assistant/tests/test_eval_live_e2e.py -v`
Expected: 1 skipped, 0 passed — **no paid call**

- [ ] **Step 3: Run the full suite with coverage**

Run:
```bash
docker exec -w /app nextseek uv run --no-sync python -m pytest nextseek_api/cc_assistant/tests/ \
  --cov=nextseek_api.eval --cov=nextseek_api.cc_assistant.turn_ledger \
  --cov=nextseek_api.cc_assistant.family_labels --cov=nextseek_api.cc_assistant.playbook \
  --cov=nextseek_api.cc_assistant.risk_overlay --cov-report=term --cov-report=xml:evidence/coverage.xml \
  2>&1 | tee evidence/task13.log
```
Expected: exit 0, coverage ≥ 95%

- [ ] **Step 4: Commit**

```bash
git add nextseek_api/cc_assistant/tests/test_eval_live_e2e.py evidence/coverage.xml
git commit -m "test(eval): gated live e2e and 95% coverage gate"
```

**Success condition:** Met only if the coverage command exits 0, `evidence/coverage.xml` exists, a validator confirms line coverage ≥ 95% across the listed modules, and `pytest nextseek_api/cc_assistant/tests/test_eval_live_e2e.py` with `RUN_EVAL_LIVE` unset reports **skipped** and makes no network call.

**Failure conditions:** coverage below 95%; the live test running without the opt-in; any paid call during an ordinary suite run.

**Rollback:** `git revert`.

---

## Freeze boundaries

Do not modify, in any task: the router's conversation-history contract; in-container op preference;
the heuristic router's routing semantics; the agent sandbox's isolation configuration; the Bayesian
model's **band thresholds**; any platform access-control code.

**Amended by V3-C.** The Bayesian **model architecture** is no longer frozen, because a paired
within-question design cannot necessarily be expressed in the current structure. The **band
thresholds remain frozen**, and any structural change still requires its own review.

## Non-goals restated

No backfill of historical turns into the ledger; no fix to platform access-control gaps (tracked
separately).

**Amended by V3-C / V3-D.** Three former non-goals are reversed or retired:

- *Forced dual-routing* is now the baseline mechanism (V3-C), not an excluded one.
- *Re-routing from a cross-route comparison* is the entire purpose of the Bayesian router (V3-D).
- *Propensity-weighted estimation* is **unnecessary** rather than forbidden. Forcing both arms on
  the same question removes the confound that weighting would have had to correct for; that same
  property is what licenses the cross-route comparison the original plan prohibited on
  observational data.

Still a non-goal: **exploration in the bandit sense** — deliberately routing a live user turn
against the posterior in order to gather data. Baseline evidence comes from the offline forced-route
experiment, never from degrading a real user's turn.

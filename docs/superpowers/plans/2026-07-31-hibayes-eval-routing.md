# HiBayes × NExtSEEK Evaluation & Routing Loop — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **VETTED-HARDENED V4 (2026-08-04):** V4 below was produced only after
> re-inventorying every fetched NExtSEEK ref and worktree, the deployed service-account clone, and
> the running image. V4 supersedes every conflicting V2/V3/original code sketch, command, success
> condition, failure condition, and rollback statement. Earlier prose remains design history; it
> is not permission to bypass a V4 gate. The original 15 tasks and all V4 prerequisite work remain
> **unexecuted**. Vetting is neither implementation nor execution authorization.

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

**Stage 3 — consumers.** Published posteriors feed playbook guidance first, then routing itself (V3-D), with the LLM routing function retained as the fallback.

**Tech Stack:** NExtSEEK's locked application Python (currently ≥3.14), a separately locked Python
3.12 eval image, Django + Celery (`batch_upload` queue), BAML (judge + router), MySQL,
pytest / pytest-django, Docker.

## Global Constraints

- **Verified implementation base:** `dfbccaf89010c468bdb1b9eba3d04f050fd7cb81` (`origin/dev` on
  2026-08-04). V4-0 requires a renewed full-file drift review if this base moves.
- **Coverage target: 95%**, across unit, integration and live end-to-end tests.
- **No paid model call runs automatically.** Every paid path is behind an explicit opt-in env gate and is never invoked by CI or by a default test run.
- **No new credentials into the agent sandbox.** The isolation invariants are untouched by every task here.
- **Copy, do not rewrite.** Vendored evaluation logic must preserve the original's behaviour. Reformatting is acceptable; changing control flow or thresholds is not.
- **No dependency on a `dmac-assistant` checkout.** After Phase 2, every task must pass on a machine that does not have that repository.
- **The two BAML trees stay byte-identical** (`dmac_assistant/baml_src/` and `docker/cc-runtime/baml_src/`). Any edit lands in both in the same commit.
- **The capabilities file is hash-pinned by exactly one test** (`nextseek_api/cc_assistant/tests/test_f_constraint_pins.py:12,17`). Editing that file updates that pin in the same commit. A second test's docstring claims it also pins the file — it does not; ignore that docstring.
- **Taxonomy source of truth** is the **NExtSEEK-vendored** `dmac_assistant/build_context/route_capabilities.json`, not the standalone `dmac-assistant` copy. They are forked.
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
family posterior and rendered `unknown`/`TooUncertain`; “always classified” means every turn has an
explicit family **state and source**, not that the system fabricates a label. No historical backfill
is added.

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

- Do not redeclare the existing BAML `class TaskFamily`; use a distinct exact enum/type name whose
  members are **generated** from the families declared in `dmac_assistant/build_context/route_capabilities.json` (8 at time of
  writing — the count is an observation, not a contract), and add it to the existing
  decision object returned by the **classification** function (see V3-B), not by the routing
  function. No task may hardcode a family count.
- Regenerate every runtime BAML client through the repository's pinned generation path and include
  generated artifacts in the task diff. Update fallback construction, Python `RouteDecision`, and
  `route_decided` telemetry. `unrelated` has explicit family state `None/unrelated`.
- Compile/parse the BAML, assert the generated enum set **equals** the families declared in
  `dmac_assistant/build_context/route_capabilities.json` — drift in either direction fails, so a family added to the JSON but
  missing from the generated enum is caught — assert per V3-B that classification and routing are
  distinct seams and that exactly one LLM classification call occurs per turn, and exercise the
  generated typed result through the public `decide` seam. A comment/string-only edit cannot pass.

#### V2-T4 — Route-scoped fallback metadata without heuristic-route drift

- Preserve the existing `_heuristic` source hash and routing result. Enrich family metadata after
  route selection; never alter the frozen routing patterns or their precedence.
- Generate positive cases from every capability-family example and property-test that every
  non-null family belongs to the chosen route. Include empty, ambiguous, mixed-case, punctuation,
  substring, forced, unknown-route, and unmatched cases. Mutating any family mapping must turn at
  least one test red.

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

- Build a transitive manifest at source commit `dcca50c`: tools, `_plan018-refs/port-source/functional_evaluator_models.py`,
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

#### V2-T12b — Reviewed semantic family↔op mapping, not union equality

- Generate the live public-op inventory from the existing catalog mechanism, then author a
  per-family mapping artifact with rationale for every op and every multi-family assignment.
- **PAUSE for maintainer approval of that semantic mapping diff/hash** only when the diff moves an
  op across routes, drops an op from every family, or empties a previously non-empty family.
  Adding a new family together with the ops it serves is a routine capability addition and
  proceeds without the pause. Update the file and hash pin together in one commit either way.
- Validate schema, route compatibility, non-empty intended families, approved duplicates, and
  exact inventory. Mutations that assign every op to every family, move an op across routes, empty
  a required family, invent an op, or include a helper must fail. Prove the BAML/playbook consumer
  reads the mapping; union-only tests are insufficient.

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

Applied directly in V2-T3, V2-T12b, Task 3 and Task 12b rather than carried as a separate contract.

The family set is **generated from** `dmac_assistant/build_context/route_capabilities.json`, which is
its single source of truth. No task may hardcode a family count or a literal member list. The BAML
enum and its `docker/cc-runtime` mirror are emitted by a generator, and a test proves regeneration is
idempotent and the mirror byte-identical, so a family added to the JSON cannot silently miss either
file. Adding a capability is a one-file JSON edit plus a regeneration.

Maintainer approval of the family-to-op mapping is now required only when a diff moves an op across
routes, drops an op from every family, or empties a previously non-empty family. Adding a new family
together with the ops it serves is a routine capability addition and proceeds without that pause.

**Rationale:** a routing system whose purpose is to absorb new capabilities cannot carry a fixed
label set. The count `8` is an observation about today's capabilities file, never a contract.

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
  selected model before and after the split across every capability-family example query and shows
  equivalent routing decisions.
- The existing `_heuristic` source hash, routing patterns and their precedence are untouched; V2-T4
  continues to apply in full.
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

**Live-run gate.** Nothing has run live yet, and forcing a route is admin-gated, so a real paired run
requires a staff account. V3-C therefore cannot be validated against real data until that run
happens. Do not fabricate a paired dataset from two independent unpaired runs, and do not treat a
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
- The corpus is a pinned versioned input. Its identity — recorded in `run_meta.corpus_fingerprint`
  alongside `git_sha` — travels with every fitted generation, and a corpus change invalidates cached
  judgments for affected cases.

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

### V4-1 — Canonical taxonomy and common estimand decision (STOP)

The observed 8-, 10-, and 15-family schemes are not interchangeable. Before a paired corpus or a
posterior schema is authored, prepare a decision artifact that contains:

- every source taxonomy with source SHA and file hash;
- proposed canonical family IDs, version, descriptions, aliases, renames, splits, merges, and
  tombstones;
- a total crosswalk for corpus labels, online labels, ordinary-Nessie labels, and both routes;
- route feasibility and explicit common-support status for every canonical family;
- counts by source and route, plus unmapped/ambiguous rows that remain errors rather than being
  silently assigned; and
- migration/compatibility rules for stored observations and published generations.

The paired target is a within-question comparison on a pinned corpus: for family `f`, estimate the
difference in desired-outcome probability between a genuinely forced Container-CC arm and a
genuinely forced NExtSEEK arm while preserving pair identity. This statement does not select the
practical-effect threshold, precision requirement, minimum sample, or operational winner rule.

**STOP:** the maintainer must approve the canonical comparison taxonomy, common-support policy,
and estimand before V4-2. Families without support on both routes cannot yield a comparative route
claim; they must fall back or be reported route-conditionally.

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
| flag on, compatible decisive generation | 1 | 0 | route from approved V4 decision rule |
| flag on, missing/stale/malformed/incompatible/indecisive | 1 | 1 | legacy LLM route fallback |

The last row is intentionally a two-call path with explicit cost/latency consequences. If that is
unacceptable, implementation stops for a revised architecture; it may not fake separation with two
thin wrappers around one route-bearing output.

- [ ] Test every row with real generated clients and call tracing, including model/destination
  equivalence when off and fallback on parse/storage/compatibility failures.
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
| any product implementation | V4-0 and approved V4-1 |
| paired fitting implementation | V4-2, V4-3, and approved V4-4 contract |
| comparative candidate publication | V4-0 through V4-5 |
| posterior-routing implementation | V4-0 through V4-7 |
| any provider call | approved immutable V4-8 manifest and reservation controls |
| any deployment or schedule change | V4-0 through V4-9 plus separate deployment approval |
| production posterior-routing enablement | all gates, recoverable image, rollback rehearsal, and separate enablement approval |

Until the STOP decisions are recorded, the only permissible next work is decision-artifact
preparation and read-only verification. No “mostly complete” status may count an unapproved or
unexecuted gate as done.

## Referenced artifacts (dev box)

Every external artifact this plan cites has a reference copy inside this checkout under
`_plan018-refs/`, so a reviewer can open it without hunting the filesystem. That directory is
**untracked and locally excluded** (via the common gitdir's `info/exclude`); it is a convenience
copy for review and is never committed. This repository is public, which is why references are
repo-relative rather than absolute paths into anyone's home directory.

| Under `_plan018-refs/` | What it is | Copied from |
|---|---|---|
| `corpus/corpus.json` | Charlie's nessie_tests corpus — the family taxonomy, `route_policy`, `family_floor`, `criterion_rewrites`, `consistency_groups` | the `NExtSEEK-dev` working clone |
| `corpus/seed-6c-ntoes.json` | seed-6 rerun review notes (per-case verdicts) | same |
| `corpus/nessie-*.html` | the two Nessie review reports | same |
| `reviews/PLAN018-*.md` | the 12 plan-018 vetting documents | the maintainer's private state directory |
| `port-source/functional_evaluator{,_models}.py` | Stage C judge port sources at `dcca50c` | the standalone `dmac-assistant` clone |
| `OPS-TESTING-HARNESSES.md` | harness inventory; section 5 records the competing family vocabularies | maintainer's work directory |
| `plan-backups/*.bak-pre-plan-vetting-*.md` | pre-vetting snapshots of this plan | `docs/superpowers/plans/` |

Copies were verified byte-identical to their sources at staging time. If a source changes, the
copy here does not follow it — re-stage before relying on one for a DONE claim.

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
| `nextseek_api/cc_assistant/family_fallback.py` | **Create.** Deterministic family for non-BAML turns. |
| `nextseek_api/eval/` | **Create.** Vendored evaluation package (tools + fit packages). |
| `nextseek_api/eval/export.py` | **Create.** Ledger → versioned eval rows. |
| `nextseek_api/eval/judge_cache.py` | **Create.** Fingerprint, lookup, invalidation, partial-failure policy. |
| `nextseek_api/eval/tasks.py` | **Create.** Celery nightly task, spend cap, force path. |
| `nextseek_api/eval/publish.py` | **Create.** Posterior store writer. |
| `nextseek_api/cc_assistant/playbook.py` | **Create.** Consumer (a). |
| `nextseek_api/cc_assistant/tests/test_*.py` | **Create.** One test module per task. |

---

## Phase 1 — Online foundation (no paid calls anywhere in this phase)

### Task 0: Shared test fixtures

**Runs first.** Every later task's tests consume these; without them each task would invent its own
and they would drift.

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
    record_turn(str(s.session_id), 1, "container_cc", "code_and_scripts", "baml")
    return export_rows()[0]


@pytest.fixture
def one_row(eval_row):
    return [eval_row]


@pytest.fixture
def many_rows(db):
    s = ChatSession.objects.create()
    for i in range(1, 6):
        record_turn(str(s.session_id), i, "container_cc", "code_and_scripts", "baml")
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
    return _FitResult(groups=[_Group("batch_upload_preparation"), _Group("code_and_scripts")])


@pytest.fixture
def sparse_fit_result():
    return _FitResult(groups=[
        _Group("memory_lookup", route="nextseek_query", posterior_mean=0.5,
               band="TooUncertain", n_total=2)
    ])


def _posterior(**kw):
    base = dict(task_family="batch_upload_preparation", route="container_cc",
                posterior_mean=0.97, band="Reliable", n_total=40)
    base.update(kw)
    return FamilyPosterior.objects.create(**base)


@pytest.fixture
def posteriors(db):
    return [_posterior(), _posterior(task_family="code_and_scripts")]


@pytest.fixture
def brittle_posterior(db):
    return _posterior(band="Brittle", posterior_mean=0.62)


@pytest.fixture
def sparse_posterior(db):
    return _posterior(task_family="memory_lookup", route="nextseek_query",
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
        record_turn(str(s.session_id), 1, "container_cc", "batch_upload_preparation", "baml")
        row = export_rows()[-1]
        record_judgment(row, verdict={"ok": False, "note": marker}, **v)
    return True


@pytest.fixture
def query_turn_factory(db):
    def _make(session, turn_number, query, fail=False):
        record_turn(str(session.session_id), turn_number, "nextseek_query", None, "heuristic")
    return _make


@pytest.fixture
def cc_turn_factory(db):
    def _make(session, turn_number, query, fail=False):
        record_turn(str(session.session_id), turn_number, "container_cc", None,
                    "forced" if fail else "baml")
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
- Produces: `TurnLedger` with fields `session` (FK→ChatSession), `turn_number` (int), `route` (str), `task_family` (str, nullable), `family_source` (str), `created_at`; unique constraint `("session", "turn_number")`.

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
        task_family="sample_search", family_source="baml",
    )
    row = TurnLedger.objects.get(session=s, turn_number=1)
    assert row.route == "nextseek_query"
    assert row.task_family == "sample_search"


def test_duplicate_turn_number_in_one_session_is_rejected():
    s = _session()
    TurnLedger.objects.create(session=s, turn_number=1, route="container_cc",
                              task_family=None, family_source="forced")
    with pytest.raises(IntegrityError):
        TurnLedger.objects.create(session=s, turn_number=1, route="container_cc",
                                  task_family=None, family_source="forced")


def test_same_turn_number_in_different_sessions_is_allowed():
    a, b = _session(), _session()
    TurnLedger.objects.create(session=a, turn_number=1, route="nextseek_query",
                              task_family=None, family_source="heuristic")
    TurnLedger.objects.create(session=b, turn_number=1, route="nextseek_query",
                              task_family=None, family_source="heuristic")
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
    task_family = models.CharField(max_length=128, null=True, blank=True)
    family_source = models.CharField(max_length=32)
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
- Produces: `record_turn(session_id: str, turn_number: int, route: str, task_family: str | None, family_source: str) -> TurnLedger`, and `LedgerCollision` (raised on a duplicate).

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_turn_ledger_writer.py
import pytest
from nextseek_api.assistant.models_db import ChatSession, TurnLedger
from nextseek_api.cc_assistant.turn_ledger import record_turn, LedgerCollision

pytestmark = pytest.mark.django_db


def test_record_turn_persists_a_row():
    s = ChatSession.objects.create()
    row = record_turn(str(s.session_id), 1, "nextseek_query", "sample_search", "baml")
    assert TurnLedger.objects.filter(pk=row.pk).exists()


def test_concurrent_same_turn_number_raises_collision_not_integrity_error():
    s = ChatSession.objects.create()
    record_turn(str(s.session_id), 1, "container_cc", "code_and_scripts", "baml")
    with pytest.raises(LedgerCollision):
        record_turn(str(s.session_id), 1, "container_cc", "code_and_scripts", "baml")


def test_null_family_is_allowed_with_a_source_recorded():
    s = ChatSession.objects.create()
    row = record_turn(str(s.session_id), 2, "container_cc", None, "forced")
    assert row.task_family is None
    assert row.family_source == "forced"
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


def record_turn(session_id, turn_number, route, task_family, family_source):
    try:
        with transaction.atomic():
            return TurnLedger.objects.create(
                session_id=session_id,
                turn_number=turn_number,
                route=route,
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

### Task 3: Router returns `task_family` in the same call

**Files:**
- Modify: `dmac_assistant/baml_src/router.baml`
- Modify: `docker/cc-runtime/baml_src/router.baml` (byte-identical)
- Modify: `nextseek_api/cc_assistant/router.py`
- Test: `nextseek_api/cc_assistant/tests/test_router_family.py`

**Interfaces:**
- Consumes: the families declared in `dmac_assistant/build_context/route_capabilities.json`
  (8 at time of writing; the task must read the file, never a hardcoded list).
- Produces: `RouteDecision.task_family: str | None` and `RouteDecision.family_source: str` (`"baml"` | `"heuristic"` | `"forced"`).

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_router_family.py
import hashlib
import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_A = _REPO / "dmac_assistant" / "baml_src" / "router.baml"
_B = _REPO / "docker" / "cc-runtime" / "baml_src" / "router.baml"
_CAPS = _REPO / "dmac_assistant" / "build_context" / "route_capabilities.json"


def test_router_baml_declares_task_family():
    assert "task_family" in _A.read_text()


def test_both_router_baml_copies_stay_byte_identical():
    assert hashlib.sha256(_A.read_bytes()).hexdigest() == \
           hashlib.sha256(_B.read_bytes()).hexdigest()


def test_declared_families_match_the_capabilities_file_exactly():
    caps = json.loads(_CAPS.read_text())
    expected = {f["name"] for r in caps["routes"] for f in r["task_families"]}
    text = _A.read_text()
    for name in expected:
        assert name in text, f"router.baml does not declare family {name}"
    assert len(expected) == 8
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest nextseek_api/cc_assistant/tests/test_router_family.py -v`
Expected: FAIL on `test_router_baml_declares_task_family`

- [ ] **Step 3: Add the field to the BAML contract**

Add a `task_family` field to the decision class returned by the existing route function, typed by
an enum that is **generated from** `dmac_assistant/build_context/route_capabilities.json` rather
than hand-listed.

**Superseded by V3-B.** The original instruction here read "Do **not** add a second function —
the family comes back from the same call." The router is now split into a classification
function and a routing function, and the family comes back from the **classification** call.

The capabilities file is the single source of truth for the family set. A generator reads it and
emits the enum block into `dmac_assistant/baml_src/router.baml`, then writes the byte-identical
mirror. Adding a capability is therefore a one-file JSON edit plus a regeneration — never a hand
edit of the enum in two places.

```bash
# Regenerate the enum from the capabilities file, then mirror it verbatim.
python -m nextseek_api.cc_assistant.gen_family_enum \
  --capabilities dmac_assistant/build_context/route_capabilities.json \
  --out dmac_assistant/baml_src/router.baml
cp dmac_assistant/baml_src/router.baml docker/cc-runtime/baml_src/router.baml
```

Each member's `@alias` is the family `name` verbatim from the JSON; the PascalCase identifier is
derived from it. With the current file this generates the eight members below — shown as current
output, **not** as the contract:

```
enum TaskFamily {
  SampleSearch        @alias("sample_search")
  LineageOrGraph      @alias("lineage_or_graph")
  ReportGeneration    @alias("report_generation")
  MemoryLookup        @alias("memory_lookup")
  ReporterSummary     @alias("reporter_summary")
  FileIoAndSummarization @alias("file_io_and_summarization")
  CodeAndScripts      @alias("code_and_scripts")
  BatchUploadPreparation @alias("batch_upload_preparation")
}
```

A test must prove regeneration is idempotent and that the mirror is byte-identical, so a family
added to the JSON cannot silently miss either file.

- [ ] **Step 4: Surface it in the Python wrapper**

In `nextseek_api/cc_assistant/router.py`, add `task_family` and `family_source` to the decision
dataclass, populate them from the BAML result on the BAML path, and set
`family_source="baml"` there. Leave the heuristic path's family `None` for now — Task 4 fills it.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest nextseek_api/cc_assistant/tests/test_router_family.py -v 2>&1 | tee evidence/task03.log`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add dmac_assistant/baml_src/router.baml docker/cc-runtime/baml_src/router.baml nextseek_api/cc_assistant/router.py nextseek_api/cc_assistant/tests/test_router_family.py
git commit -m "feat(router): return task_family from the classification call"
```

**Success condition:** Met only if `pytest nextseek_api/cc_assistant/tests/test_router_family.py -v` exits 0, output saved to `evidence/task03.log`, **and** `pytest nextseek_api/cc_assistant/tests/test_baml_router_schema.py -v` still exits 0 (the pre-existing byte-identity and prompt-region pins must not regress).

**Failure conditions:** the two copies diverging; a second BAML function added; any family name not matching the capabilities file verbatim.

**Rollback:** `git revert`; no runtime consumer reads the field until Task 5.

---

### Task 4: Deterministic family for non-BAML turns

**Files:**
- Create: `nextseek_api/cc_assistant/family_fallback.py`
- Modify: `nextseek_api/cc_assistant/router.py`
- Test: `nextseek_api/cc_assistant/tests/test_family_fallback.py`

**Interfaces:**
- Consumes: `dmac_assistant/build_context/route_capabilities.json`.
- Produces: `family_for(route: str, query: str) -> tuple[str | None, str]` returning `(family, source)`.

The maintainer's ruling is that a family is always classified, including on forced and heuristic
turns. On those paths there is no model call, so the family is derived deterministically from the
route's declared families and the query text; when nothing matches, the family is `None` with an
explicit source rather than a guess.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_family_fallback.py
from nextseek_api.cc_assistant.family_fallback import family_for


def test_forced_container_turn_gets_a_source_even_without_a_match():
    fam, src = family_for("container_cc", "zzzz nothing matches zzzz")
    assert fam is None
    assert src == "unmatched"


def test_query_route_keyword_maps_to_a_declared_family():
    fam, src = family_for("nextseek_query", "find me all PBMCs in the BTC study")
    assert fam == "sample_search"
    assert src == "heuristic"


def test_fallback_never_returns_a_family_from_the_other_route():
    fam, _ = family_for("nextseek_query", "write a python script to plot ages")
    assert fam != "code_and_scripts"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest nextseek_api/cc_assistant/tests/test_family_fallback.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Write the implementation**

```python
# nextseek_api/cc_assistant/family_fallback.py
"""Deterministic task_family for turns that never reach the BAML router.

Families are route-scoped: a query-route turn can only be labelled with a
query-route family. No cross-route label is ever produced, because the routes
are disjoint and a cross-route label would fabricate an observation.
"""
import json
from pathlib import Path

_CAPS = Path(__file__).resolve().parents[2] / "dmac_assistant" / "build_context" / "route_capabilities.json"

# Keyword hints per family, drawn from each family's own example_queries.
_HINTS = {
    "sample_search": ("find", "search", "which samples", "what samples"),
    "lineage_or_graph": ("lineage", "how many samples", "what assays", "study"),
    "report_generation": ("geo", "sra", "nfcore", "pride", "submission"),
    "memory_lookup": ("those results", "that result", "previous"),
    "reporter_summary": ("rppr", "summary for", "project summary"),
    "file_io_and_summarization": ("read /data", "summarize", "walk me through"),
    "code_and_scripts": ("write a python", "script", "refactor"),
    "batch_upload_preparation": ("upload sheet", "update sheet", "batch-upload", "workbook"),
}


def _families_for_route(route):
    caps = json.loads(_CAPS.read_text())
    for entry in caps["routes"]:
        if entry["route_name"] == route:
            return [f["name"] for f in entry["task_families"]]
    return []


def family_for(route, query):
    text = (query or "").lower()
    for name in _families_for_route(route):
        if any(h in text for h in _HINTS.get(name, ())):
            return name, "heuristic"
    return None, "unmatched"
```

- [ ] **Step 4: Wire it into the router's non-BAML paths**

In `nextseek_api/cc_assistant/router.py`, on the heuristic path and the forced path, call
`family_for(route, query)` and set `task_family` / `family_source` from its result. Keep
`family_source="forced"` for the forced path by overriding the returned source there, so a forced
turn stays distinguishable from ordinary heuristic traffic.

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest nextseek_api/cc_assistant/tests/test_family_fallback.py nextseek_api/cc_assistant/tests/test_router_heuristic.py -v 2>&1 | tee evidence/task04.log`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/cc_assistant/family_fallback.py nextseek_api/cc_assistant/router.py nextseek_api/cc_assistant/tests/test_family_fallback.py
git commit -m "feat(router): deterministic family label for forced and heuristic turns"
```

**Success condition:** Met only if the pytest command above exits 0, output saved to `evidence/task04.log`, and a test proves no cross-route family label is ever produced.

**Failure conditions:** a query-route turn labelled with a container-route family or vice versa; a forced turn indistinguishable from a heuristic one.

**Rollback:** `git revert`.

---

### Task 5: Both route writers record a ledger row

**Files:**
- Modify: `nextseek_api/services/cc_assistant.py` (container path turn completion)
- Modify: `chat_nextseek/src/chat_nextseek/chat_memory.py` call site in `nextseek_api/assistant/session_adapter.py` (query path)
- Test: `nextseek_api/cc_assistant/tests/test_ledger_written_on_both_routes.py`

**Interfaces:**
- Consumes: `record_turn` (Task 2), `RouteDecision.task_family` / `.family_source` (Tasks 3–4).
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
    assert row.family_source in {"baml", "heuristic", "unmatched"}


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
- `tools/hibayes/{exporter,expected_behavior,artifact_validator,functional_inputs,enums}.py` → `nextseek_api/eval/`
- `tools/e2e/functional_evaluator.py` (local copy: `_plan018-refs/port-source/functional_evaluator.py`) → `nextseek_api/eval/judge.py`
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

**Files:**
- Create: `nextseek_api/eval/export.py`
- Test: `nextseek_api/cc_assistant/tests/test_eval_export.py`

**Interfaces:**
- Consumes: `TurnLedger` (Task 1).
- Produces: `export_rows(since=None) -> list[EvalRow]`; `EVAL_ROW_SCHEMA_VERSION = 2`.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_eval_export.py
import pytest
from nextseek_api.assistant.models_db import ChatSession
from nextseek_api.cc_assistant.turn_ledger import record_turn
from nextseek_api.eval.export import export_rows, EVAL_ROW_SCHEMA_VERSION

pytestmark = pytest.mark.django_db


def test_schema_is_versioned_and_not_the_legacy_14_column_shape():
    assert EVAL_ROW_SCHEMA_VERSION >= 2


def test_every_row_carries_route_and_family_as_separate_columns():
    s = ChatSession.objects.create()
    record_turn(str(s.session_id), 1, "container_cc", "code_and_scripts", "baml")
    row = export_rows()[0]
    assert row.route == "container_cc"
    assert row.task_family == "code_and_scripts"


def test_forced_turns_are_distinguishable_from_router_chosen_turns():
    s = ChatSession.objects.create()
    record_turn(str(s.session_id), 1, "container_cc", "code_and_scripts", "forced")
    assert export_rows()[0].family_source == "forced"


def test_export_is_incremental_by_watermark():
    s = ChatSession.objects.create()
    a = record_turn(str(s.session_id), 1, "nextseek_query", "sample_search", "baml")
    record_turn(str(s.session_id), 2, "nextseek_query", "sample_search", "baml")
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
"""
from dataclasses import dataclass

from nextseek_api.assistant.models_db import TurnLedger

EVAL_ROW_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class EvalRow:
    session_id: str
    turn_number: int
    route: str
    task_family: str | None
    family_source: str
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

**Success condition:** Met only if the pytest command exits 0 with output at `evidence/task07.log`, and a test proves route and family are separate columns and that forced turns remain distinguishable.

**Failure conditions:** route collapsed into family; forced rows indistinguishable; a non-incremental export.

**Rollback:** `git revert`.

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
    assert FamilyPosterior.objects.get(task_family="memory_lookup").band == "TooUncertain"


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
    v = assess("container_cc", "code_and_scripts")
    assert v.level == "unknown"


def test_too_uncertain_never_produces_a_confident_verdict(sparse_posterior):
    assert assess("nextseek_query", "memory_lookup").level == "unknown"


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

### Task 12b: Map the live op inventory under the declared task families

**Dependency:** after Task 3 (families declared). Land before Task 11 if the playbook is to give
op-level guidance rather than family-level only.

This is the one task that **intentionally breaks an anchor**: it edits the hash-pinned capabilities
file, and updates that pin in the same commit. Per the maintainer's ruling the mapping goes **inside**
the pinned file rather than beside it, so there is a single source of truth for the taxonomy.

**Files:**
- Modify: `dmac_assistant/build_context/route_capabilities.json`
- Modify: `nextseek_api/cc_assistant/tests/test_f_constraint_pins.py:12` (the pin constant)
- Test: `nextseek_api/cc_assistant/tests/test_family_op_mapping.py`

**Interfaces:**
- Produces: each `task_families[]` entry gains `ops: list[str]`, naming the live `nextseek-*` bins
  that serve that family.

- [ ] **Step 1: Write the failing test**

```python
# nextseek_api/cc_assistant/tests/test_family_op_mapping.py
import json
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_CAPS = _REPO / "dmac_assistant" / "build_context" / "route_capabilities.json"
_BIN = _REPO / "docker" / "cc-runtime" / "build_context" / "plugins" / "nextseek" / "bin"


def _declared_ops():
    caps = json.loads(_CAPS.read_text())
    return {op for r in caps["routes"] for f in r["task_families"] for op in f.get("ops", [])}


def _live_bins():
    return {p.name for p in _BIN.iterdir() if p.is_file() and not p.name.startswith("_")}


def test_every_family_declares_an_ops_list():
    caps = json.loads(_CAPS.read_text())
    for r in caps["routes"]:
        for f in r["task_families"]:
            assert "ops" in f, f"family {f['name']} has no ops list"


def test_no_declared_op_is_missing_from_the_live_bin_inventory():
    missing = _declared_ops() - _live_bins()
    assert missing == set(), f"declared ops that do not exist as bins: {sorted(missing)}"


def test_every_live_bin_is_claimed_by_at_least_one_family():
    """Prevents silent drift as bins are added — the count moved 15 -> 17 unnoticed once."""
    unclaimed = _live_bins() - _declared_ops()
    assert unclaimed == set(), f"live bins claimed by no family: {sorted(unclaimed)}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest nextseek_api/cc_assistant/tests/test_family_op_mapping.py -v`
Expected: FAIL — `family sample_search has no ops list`

- [ ] **Step 3: Add the ops lists**

Add an `ops` array to each `task_families` entry — however many are declared — assigning each of the live public
`nextseek-*` bins to exactly the families it serves. Every live bin must be claimed at least once and
no invented op names may appear — the tests above enforce both directions.

- [ ] **Step 4: Update the hash pin in the same commit**

```bash
NEW=$(shasum -a 256 dmac_assistant/build_context/route_capabilities.json | cut -d' ' -f1)
python3 - "$NEW" <<'PY'
import re, sys, pathlib
p = pathlib.Path("nextseek_api/cc_assistant/tests/test_f_constraint_pins.py")
s = p.read_text()
p.write_text(re.sub(r'CAPABILITIES_SHA256 = "[0-9a-f]{64}"',
                    f'CAPABILITIES_SHA256 = "{sys.argv[1]}"', s))
print("pin updated ->", sys.argv[1])
PY
```

- [ ] **Step 5: Run both test modules to verify they pass**

Run: `pytest nextseek_api/cc_assistant/tests/test_family_op_mapping.py nextseek_api/cc_assistant/tests/test_f_constraint_pins.py -v 2>&1 | tee evidence/task12b.log`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add dmac_assistant/build_context/route_capabilities.json nextseek_api/cc_assistant/tests/test_f_constraint_pins.py nextseek_api/cc_assistant/tests/test_family_op_mapping.py
git commit -m "feat(taxonomy): map live nextseek ops under the declared task families"
```

**Success condition:** Met only if the pytest command above exits 0 with output at
`evidence/task12b.log`; the recomputed sha256 of the capabilities file equals the constant now in
`nextseek_api/cc_assistant/tests/test_f_constraint_pins.py`; and both directions of the drift check pass (no declared op missing from
the bin inventory, no live bin unclaimed).

**Failure conditions:** the pin left stale (that test goes red); an invented op name; a live bin left
unclaimed; the mapping placed in a second file instead of the pinned one.

**Rollback:** `git revert` — restores both the file and its pin together, which is why they must be
one commit.

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
    record_turn(str(s.session_id), 1, "container_cc", "code_and_scripts", "baml")
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
  --cov=nextseek_api.cc_assistant.family_fallback --cov=nextseek_api.cc_assistant.playbook \
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

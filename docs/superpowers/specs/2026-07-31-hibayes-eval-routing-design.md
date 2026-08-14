# HiBayes × NExtSEEK — evaluation and routing feedback loop (design spec)

**Status:** design-approved, not implemented
**Date:** 2026-07-31
**Anchor commit:** `9edd36958b6be06098d2cbdd8a5e3a0561e6623d` (`origin/dev`)
**Companion clone:** `dmac-assistant` `main` @ `dcca50c187890dc93659e5594810179793bb94eb`

Every file:line reference in this document was verified against the anchor commit. If the tip has
moved, re-verify before relying on a line number — see **Drift protocol** at the end.

---

## 1. Problem

The NExtSEEK integrated assistant routes each turn to one of two paths — the native NExtSEEK query
pipeline or the sandboxed Container-CC agent — and then forgets how it went. There is no measurement
loop. Separately, a Bayesian evaluator exists in `dmac-assistant` that scores an offline corpus and
produces per-family reliability posteriors, but it is offline, it is not in this repository, and its
inputs are a headless fixture rather than live traffic.

The goal is to close that loop: judge real turns on a schedule, fit posteriors over them, and feed
the result back into (a) guidance for the container agent and (b) a risk overlay on routing.

## 2. Goals

1. An offline, scheduled job that evaluates real `ChatSession` turn data — never in the chat hot path.
2. **Incremental** judging: re-judge only new or changed turns, with fingerprint-based invalidation
   and an explicit force path. No mtime-only skip that can silently drop failures.
3. The BAML router classifies **`task_family`** alongside its existing route decision, in one call.
4. Posteriors inform **routing risk** — honestly, as risk *given the route taken*.
5. The existing router remains the fallback; the Bayesian layer is an overlay and gate, not a
   replacement.
6. A consumer that gives the container agent family-level context on what has worked and failed.

## 3. Non-goals

- **No causal claim about the route not taken.** See §9.
- **No change to Component F.** The router's conversation-history contract is already implemented and
  is composed with, not redesigned.
- **No change to in-container op preference.** The prefer-NExtSEEK-query-first work governs which op
  the agent reaches for *inside* a container turn; it is orthogonal to routing and to this work.
- **No replacement of the heuristic router fallback.**
- **No new credentials into the agent sandbox.** The isolation invariants are untouched.
- **Not a model-architecture change** to the Bayesian fit — the existing hierarchical model and its
  banding are preserved.

## 4. What already exists (verified at the anchor)

| Fact | Where |
|---|---|
| Router returns route + model class + reasoning; **no** `task_family` | `dmac_assistant/baml_src/router.baml` |
| Conversation-history contract already implemented | same file; call site in `nextseek_api/services/cc_assistant.py` |
| A **shared per-turn envelope already spans both routes** — a documented contract naming both writers | `chat_nextseek/src/chat_nextseek/chat_memory.py:32-35` |
| The query-path writer stamps route and status on every turn | `chat_nextseek/src/chat_nextseek/chat_memory.py:213-214` |
| Container-only trace object: 11 structural fields, **no** success/validity/judgment field | `nextseek_api/cc_assistant/cc_trace.py:32-44` |
| Session id is a real UUID primary key | `nextseek_api/assistant/models_db.py:8` |
| The product's turn number lives **only inside a JSON blob**, computed in Python | `chat_nextseek/src/chat_nextseek/chat_memory.py:49-71` |
| A column named `turn_id` exists but holds a task run UUID, not the chat turn number | `nextseek_api/assistant/models_db.py:83`; stated at `nextseek_api/cc_assistant/cc_turn_complete.py:22-24` |
| Chat log FIFO-evicts at 50 entries | `chat_nextseek/src/chat_nextseek/chat_memory.py:25,246-247` |
| Celery beat exists, with one periodic entry today | `nextseek_api/batch_upload/celery_app.py` |
| The Stage C judge's BAML contract **is already vendored**, in two byte-identical copies | `dmac_assistant/baml_src/functional_evaluator.baml`, `docker/cc-runtime/baml_src/functional_evaluator.baml` |
| …and it carries a locked reuse rule pointing at `tools/hibayes/exporter.py`, **which does not exist in this tree** | `dmac_assistant/baml_src/functional_evaluator.baml:27` |
| The Python evaluation packages, the eval Dockerfile, its shell wrappers and its Make targets are **all absent** | — |
| NExtSEEK already vendors `dmac_assistant` as an editable path dependency | `pyproject.toml:139` |
| Exactly **one** test pins the capabilities file by hash | `nextseek_api/cc_assistant/tests/test_f_constraint_pins.py:12,17` |

> A second test's docstring claims it also pins that file. It does not — the file contains no such
> assertion and its hashing import is unused. Treat the docstring as inaccurate.

## 5. Architecture

```
L1  ONLINE  (per turn, in-request)
    router → { route, task_family, model_class, reasoning }   ← one BAML call
    writers → per-turn envelope + per-turn ledger row

L2  OFFLINE (nightly, Celery beat)
    export → judge (incremental, cached) → fit → publish posteriors

L3  CONSUMERS
    (a) container playbook  — ships first
    (b) routing risk overlay — ships second, gated
```

### L1 — online

- **Router.** `RouterDecision` gains `task_family`, returned by the **same** call that returns the
  route. Both copies of the BAML tree must move together; nothing enforces that today, so the change
  set must touch both and a test must assert they stay identical.
- **Family is always classified**, including on forced and heuristic turns, so every row carries a
  label rather than a hole.
- **Persistence.** A new per-turn ledger row is written in the same transaction as the existing
  envelope write.

### L2 — offline

Nightly Celery beat task, incremental, with a hard spend cap that pauses the job when reached, plus
an operator-invoked force path. Stages: export rows → judge new/changed turns → fit → publish.

### L3 — consumers

- **Playbook (first).** Family-level guidance injected into the container agent's context. Carries
  aggregate statistics **and** worked examples; example content is scoped to the requesting user's
  own projects.
- **Overlay (second).** Reads posteriors and may flag, gate, or require review on a risky
  route+family. It may not re-route on a comparison between routes. See §9.

## 6. Turn identity and the ledger

**A foreign key cannot target the product's turn number**, because that number is a key inside a
JSON document, not a column. The session id *is* a real primary key and a valid FK target.

Therefore this design introduces a **per-turn ledger table**:

- FK to the session; an integer turn number; a uniqueness constraint on the pair.
- Written by **both** route writers, in the same transaction as the envelope write.
- It is the join target for judgments and the export.

This is deliberately the more expensive option. It buys three things the cheap alternative does not:
a real foreign key as required; survival past the 50-entry eviction horizon; and immunity to a
future renumbering of the JSON turn ids, which has already happened once in production.

**Two implementation notes.**
1. A new FK onto the session table on a seed-derived database must replicate the existing
   charset-alignment step, or creation fails outright. The mechanism already exists in-tree —
   reuse it (`nextseek_api/migrations/_cc_transcript_heal.py:85-97`).
2. There is currently **no locking** around the read-modify-write that assigns turn numbers, so two
   concurrent completions on one session can compute the same value. The ledger's uniqueness
   constraint surfaces this rather than hiding it; the collision-handling policy is a task-level
   decision, not left implicit.

## 7. Extended row schema and taxonomy

The exported row is **versioned**. It supersedes the 14-column offline format, which was built for a
headless fixture and has no route column at all.

Success is recorded as a **four-tier ladder**, not one bit:

| Tier | Meaning | Source |
|---|---|---|
| 1 | Routing success — the turn reached the intended path | online, already stamped |
| 2 | Runtime — completed without error, timeout, turn cap or spend cap | online, already stamped |
| 3 | Judge verdict | offline, Stage C |
| 4 | Artifact and trace analysis (e.g. tool-choice correctness) | offline, net-new |

Tiers 1–2 already have a durable home on both routes. Tiers 3–4 do not, on either, and are net-new
work — that is where the real cost sits.

**Taxonomy source of truth:** the eight task families declared in the capabilities file — five on the
query route, three on the container route, with no overlap.

> **The capabilities file is forked.** The standalone `dmac-assistant` copy and the NExtSEEK-vendored
> copy differ, including the name of one container-route family. **The vendored copy is
> authoritative** — it is the one the router loads and the one the hash pin asserts against. The
> spec's taxonomy is the vendored copy's.

Mapping the live op inventory underneath these families **edits the pinned file**, which turns
exactly one guard red. That guard is updated as part of the change, with the new hash.

## 8. Offline job

- **Schedule:** nightly, incremental, hard spend cap, pause-on-cap, plus a manual force path.
- **Judgment cache:** a database table is the source of truth; any CSV is derived from it. Each row
  carries a fingerprint over the inputs *and* the prompt, model and schema versions, so a version
  bump invalidates cleanly. Partial failure is recorded as failure, never as "skip".
- **Fit:** the existing hierarchical model and its four bands are preserved unchanged.
- **Publish:** posteriors land in a database table that consumers read.

**Judge model is not a design constraint.** It is a BAML client name and can be switched — including
to an Anthropic client on Bedrock — without touching this design. Do not build anything that assumes
a specific provider.

## 9. Statistical claims and non-claims

**What this system may claim.** For a route and family, the probability that a turn *on that route*
succeeds, with a credible interval and a band. This is risk **given the route taken**.

**What it may not claim.** That the other route would have done better. The router chooses the route
by inspecting the query, so route and difficulty are entangled in the logs; the two routes' turns are
not comparable populations. The families make this sharper still — they are route-disjoint, so
"route" is close to a relabelling of "family", and for many pairs the counterfactual has no referent
at all (the query route cannot execute code).

**Both routes have a router-free bypass**, so a few cross-route observations will accumulate.
Neither bypass is an experiment — whoever invokes them is self-selecting. Record them; do not treat
them as evidence about what the router should do.

**Report route and family as separate columns** even though route is redundant for ordinary traffic,
so bypass rows stay visibly distinguishable.

**Banding is a policy setting, not a measurement.** The top band keys on a posterior mean of 0.95.
The model pools partially across families, and this pipeline's own prior probe records a family with
a **perfect record being dragged into the worst band** at the library default — the prior scale was
raised specifically to prevent that. How much pooling applies is a tuned constant. Consequence: a
family's band depends on its own sample size, the rest of the fleet, and that constant. If a lower
bar is acceptable for a given family, the thresholds must say so — and on the axis the overlay reads
first they are hardcoded literals, not configuration.

**Expect the overlay to be quiet for a long time.** There is a hard floor below three observations,
and the existing corpus contributes nothing (no route column, disjoint taxonomy, and it is a
generated artifact rather than committed data). Every observation must be collected fresh. This is
why the playbook consumer ships first.

## 10. Porting strategy

The evaluation code is **vendored into this repository**, following the pattern already proven here
for the assistant package. A NExtSEEK-owned container remains for the heavy numerical dependencies —
it is a dependency-isolation tool, not a substitute for integration.

This is required, not stylistic: the existing eval image contains no application source, every
invocation path mounts a `dmac-assistant` checkout, and there is no registry-publish path anywhere.
On a machine without that checkout, none of it runs.

**Vendoring also repairs an existing break.** The judge's BAML contract is already in-tree and points
at a Python module that is not. Bringing that module in closes a dangling reference rather than
creating new coupling.

**Acceptance bar (user-stated, binding):**
1. Ported logic preserves the original's core behaviour — copy, do not rewrite.
2. The result is natively part of this codebase — no import from, or reference to, a separate
   `dmac-assistant` checkout or image.
3. It builds and runs on a machine that does not have `dmac-assistant` at all.

Scope note: the port is larger than a Python file count suggests — the Dockerfile, the shell
wrappers and the Make targets are absent too, not only the packages.

## 11. Privacy, retention and threats

- **Isolation is not weakened.** No new credentials reach the agent sandbox; the segmented network
  and scratch-only write model are unchanged.
- **Spend.** The nightly job runs under a hard cap and pauses when it is reached. Paid evaluation
  runs are gated and never automatic.
- **Judge inputs.** The judge receives query and answer text. Judgments are retained. During
  development this runs against the development instance, whose data is public.
- **Playbooks** carry aggregate statistics plus worked examples; example content is scoped to the
  requesting user's own projects, preserving the existing per-user scoping of the injection channel.
  This requires the playbook store to be project-aware.
- **Access-control constraint.** Not all read paths in the surrounding platform enforce
  project-scoped authorization uniformly. This design must not assume they do, and must not widen
  any existing exposure. Specifics are tracked privately in the maintainer's session-state notes and
  are deliberately omitted here; consult those before relying on any scoping assumption.
- **Before this runs against non-public data,** revisit unbounded retention and re-check the judge
  provider arrangement.

## 12. Test strategy

- **Coverage target: 95%**, across unit, integration and live end-to-end tests.
- **Hermetic unit tests** — no database, no spend — for the router schema change, the family
  classifier fallback, the fingerprint and invalidation logic, the export shaping, and the ledger
  writers.
- **Container/DB-backed integration tests** for the migration, the FK and uniqueness constraint,
  concurrent turn-number collisions, and the Celery task's cap and pause behaviour.
- **Paid live end-to-end is gated** behind an explicit opt-in and is never run automatically.
- **Pinning tests:** the two BAML trees stay byte-identical; the capabilities hash pin is updated in
  the same change that edits the file.
- Vetting of the resulting plan is performed by the maintainer via an external review flow.

## 13. Coordination

| Neighbour | Relationship |
|---|---|
| Router conversation-history work (Component F) | **Compose.** Already implemented; the family field is added to the same decision object. Do not redesign it. |
| Prefer-NExtSEEK-query-first | **Orthogonal.** In-container op preference, explicitly not router behaviour. Do not conflate. |
| Op-catalog source-of-truth work | **Depends.** The family↔op mapping should consume that catalog rather than hand-maintaining a parallel list. |
| Attribute viewset work in flight | **Avoid.** Different subsystem; no shared files expected. |

## 14. Out of scope

Exploration or forced dual-routing for causal identification; propensity-weighted estimation;
re-routing driven by cross-route comparison; changes to the heuristic fallback's routing semantics;
changes to Component F; changes to in-container op preference; a model-architecture change to the
Bayesian fit; fixing the platform's access-control gaps; migrating historical turns into the ledger.

## 15. Drift protocol

This spec is anchored to `9edd369`. Before executing against it:

1. Confirm the tip. If it has moved, diff the anchor list in §4 over the range.
2. Any anchor whose file changed must be re-verified and this spec amended before that task runs.
3. The capabilities hash pin is expected to change *because of* this work; that is the one
   intentional anchor break, and it is updated in the same change.
4. If the vendored and standalone capabilities copies have converged or diverged further, re-state
   which is authoritative before touching the taxonomy.

**Provenance.** Decisions behind this spec are recorded in `DECISION-LOG-round1.md` and
`DECISION-LOG-round2.md`; supporting research and its adversarial review record are in
`ROUND2-RESEARCH-SYNTHESIS.md` and `.vetting/defect-lineage.md`, alongside the decision reports.

# Handoff 1 — Harness & corpus truthfulness

*Read this before `docs/2026-08-03-nessie-hardening-plan-1-harness-corpus.md`.
It is the context the plan assumes. Nothing here needs re-deriving; every number
below was verified on 2026-08-03 against the tree at `9b7954a`.*

---

## What you are picking up

Nessie is the AI assistant embedded in NExtSEEK. `nessie_tests/` is the eval harness
that drives real questions at a deployed instance over HTTP and scores the progress
stream. You own the harness and the corpus. You are not fixing the product.

On 2026-07-31 a 20% sample ran live (seed 6, 57 cases, 66 turns, $1.48). It reported
**39 green, 18 red**. The triage of those 18 is the origin of this plan:

| bucket | count | meaning |
|---|---|---|
| provider outage | **10** | one Bedrock incident. No signal about anything. |
| family-vs-engine defect | 3 | product was right, harness demanded the wrong engine |
| corpus/router policy | 3 | product was right, assertion was stale |
| stale criterion | 1 | |
| real product defect | **1** | |

So the headline number was almost entirely instrument error. **Your job is to make the
next run's number mean something.** Do not optimise for a pass rate; several of these
changes will make cases stop failing, and the tests that prove they did not stop
*asserting* are the deliverable.

---

## Environment

**Repo:** `/home/cdemu/code/dmac/docker/dev-v3-merge` — this clone, branch `dev-v3-merge`.
Cut your worktree from it.

> There is a second checkout at `/home/cdemu/code/dmac/docker` (branch
> `feat/luria-launch-mode`) and a standalone `chat_nextseek` at
> `/home/cdemu/code/chat_nextseek` (branch `cd-dev`, divergent). **Neither is yours.**
> Do not edit, sync or reason from them.

**Test lane.** From the repo root:

```bash
uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 \
  python -m pytest nessie_tests/tests -q -p no:cacheprovider
```

**Baseline: `160 passed`.** Three ways this bites:

- plain `uv run pytest` fails outright — `mysqlclient` will not build on the host
- omitting `--with beautifulsoup4` gives **12 phantom failures** that present as
  `AssertionError: assert 'error' == 'failed'`, not as an import error, because the
  runner catches the `ModuleNotFoundError` and records the case as `error`
- `nessie_tests/README.md` and the 2026-07-24 plan both say tests must run in-container.
  That was true once. The host lane works and is a second faster; task 9 documents it.

**Corpus sanity check:** `corpus.merged(Path('nessie_tests/overlay.json'))` returns
**280** active variants. Calling `merged()` with no argument returns 274 and applies no
overlay, no route policy and no floor — that is not the corpus and it will mislead you.

---

## Stored evidence — use this instead of running anything

Every seed-6 turn is recoverable, so no paid re-run is needed for any task in this plan.

| path | what it is |
|---|---|
| `/home/cdemu/nessie-run-seed6b/manifest.json` | 57 case records with per-criterion observations |
| `/home/cdemu/nessie-run-seed6b/turns.json` | 66 turn records including full reply text |
| `/home/cdemu/nessie-run-seed6b/triage.json` | the human verdicts and per-case notes |
| `/home/cdemu/nessie-seed6b-review.html` | the rendered review |

`turns.json` is a flat list; each entry has `q`, `id`, `src`, `mode`, `reply`, `gmeta`,
`ameta`. `manifest.json` case records do **not** carry the reply text, which is exactly
why the tenth outage case was missed. Cross-reference by query string.

---

## Traps, each of which has already caught someone

**1. `overlay.json` must be edited as text.** A previous session round-tripped it through
`json.dumps(indent=1)` twice, producing a 1,819-line diff for a one-line change, and had
to redo it surgically both times. Use the Edit tool. Same for `retired.json`.

**2. An inline route criterion silently beats its `route_policy` override.**
`apply_route_policy` appends a route criterion only `if "route" not in present`
(`corpus.py:191`). So `green.refine_recall` and `green.global_count` each carry a
`route_policy.overrides` entry saying `container_cc` that **never fires**, because both
variants have an inline `route eq nextseek_query` on turn 0. Two separate review documents
recommended "fix it in `route_policy.overrides`", and that recommendation is wrong. Always
check the **resolved** criterion through `corpus.merged(overlay_path)`, never the raw JSON.

**3. The consistency group hides the reply.** `cons.nhp_sequencing_engine` reports
`count could not be resolved for 2 of 2 queries` and its manifest record carries no reply
at all. Its two turns (`turns.json` ids 1054, 1055) both died on the Bedrock outage. A
careful reviewer filed it as `drift` on the strength of that message. Your outage detector
must run before the consistency summary is composed, or you will reproduce the same
mis-triage.

**4. Weakening an assertion is the risk in tasks 4 and 5.** Both make failing cases pass.
Both are correct. Both are one step from making the floor vacuous. Every such task in the
plan has an explicit "still fails when nothing was produced" test — do not skip it, and do
not let a CC case whose criteria are *all* skipped report `passed`.

**5. Do not compare anything to the 07-28 or 07-29 runs.** Seed 6 shares only 16 of 56
variants with seed 0, the corpus has since lost 101 variants and gained 280 route
assertions, and `route_capabilities.json` changed live between them. Cross-boundary
comparisons are invalid and have already produced one wrong conclusion in a handoff.

---

## Cross-plan constraints

Two other agents are working in parallel worktrees off the same branch. The split is by
**file ownership** precisely so you never conflict. You own `nessie_tests/` entirely.

**Do not assert `route_source eq baml` on any turn after the first.** Plan 2 is adding
sticky routing, which introduces a new `route_source` value, `"sticky"`, on any follow-up
turn that the router would have sent to NS after a CC turn. Nothing in the corpus asserts
that today (verified), and your new write cases in task 8 must not start.

**Task 8 and plan 3 are a TDD pair, deliberately unsynchronised.** You add a delete-refusal
case; plan 3 closes the delete path in the product. The case is most meaningful RED before
that fix and GREEN after. It only exercises against a live stack, so the proof happens
post-merge, post-rebuild. Write the case to be honest about today's behaviour and let the
operator observe the transition.

---

## Definition of done

1. Host lane green, new count recorded.
2. Replayed against the stored seed-6 evidence:
   - **10** cases classify `error` (the triage found 9; the tenth is
     `cons.nhp_sequencing_engine`)
   - the three `advanced.*` cases satisfy the floor **without editing the cases**
   - `green.refine_recall`'s resolved seed criterion is `container_cc`
3. A turn that produced no outcome still fails the floor.
4. A CC-routed turn records NS outcome fields as `skipped`, and a case with nothing left
   to assert does not report `passed`.
5. The corpus contains a delete-intent case.
6. One commit per task, scope `nessie`.
7. **Do not merge. Do not push.** The orchestrating session reviews all three diffs
   together, because the seams between them are what a single-plan reviewer cannot see.

---

## If you get blocked

- **A test needs the container.** It should not. Everything in this plan is pure logic
  over stored fixtures. If you find yourself wanting a live stack, you have drifted into
  another plan's territory — stop and say so.
- **A change wants a file outside `nessie_tests/`.** Stop. That is the conflict this split
  exists to prevent. Note it in your completion note and leave it.
- **The evidence files are missing.** Guard the replay tests with `skipif` and note it.
  Do not fabricate fixtures that claim to be run evidence.
- **You disagree with the design.** Say so in the completion note with the evidence. Two of
  the four source documents behind this work contained claims that were false at HEAD, and
  they were caught by someone checking rather than by someone complying. That is the
  expected behaviour, not an exception.

---

## Background reading, in order

1. `docs/2026-08-03-nessie-hardening-design.md` — §3 (corrections), §5 (B2), §6 (C1-C3), §7 (D1-D2)
2. `/home/cdemu/nessie-seed6b-review.html` — the run this plan is reacting to
3. `docs/nessie-corpus-review-findings-2026-07-30.md` — why only 6% of the corpus could
   ever catch a wrong answer, which is the standing context for all of it
4. `nessie_tests/output-skill/SKILL.md` — the triage vocabulary (real / drift / policy /
   masked / notrun / pass) used throughout

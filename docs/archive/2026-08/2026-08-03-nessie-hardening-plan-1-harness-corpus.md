# Plan 1 — Harness & corpus truthfulness

> **For agentic workers:** REQUIRED SUB-SKILL: use `superpowers:executing-plans` or
> `superpowers:subagent-driven-development` to work this task-by-task. Steps use
> `- [ ]` checkboxes. Read `docs/archive/2026-08/2026-08-03-nessie-hardening-handoff-1-harness-corpus.md`
> **first** — it carries the environment, the traps and the cross-plan constraints.

**Design:** `docs/archive/2026-08/2026-08-03-nessie-hardening-design.md` (tracks B2, C1, C2, C3, D1-overlay, D2)
**Branch:** cut a worktree from `dev-v3-merge`
**Owns:** everything under `nessie_tests/`. Nothing else. If a task seems to need a
file outside `nessie_tests/`, stop — it belongs to plan 2 or 3.

**Goal:** make the instrument tell the truth. Today the harness cannot distinguish an
infrastructure outage from a test failure, mandates an engine the parser is free not to
use, cannot see a file a container produced, prints `$0` for turns that cost real money,
and has lost all coverage of write refusal. Every one of those produced a wrong verdict
in the seed-6 run.

**Non-goal:** running anything against a live stack. Every task here is verifiable on the
host in under a second.

---

## Global constraints

- **Never re-serialise `nessie_tests/overlay.json`.** It is hand-formatted compactly.
  A previous session round-tripped it through `json.dumps(indent=1)` twice and produced
  a 1,819-line diff for a one-line change. Edit it as **text**, with the Edit tool.
  The same applies to `retired.json`.
- **Do not touch anything outside `nessie_tests/`.** In particular
  `nextseek_api/services/cc_assistant.py` and `chat_nextseek/` belong to other plans and
  editing them creates the merge conflict this split exists to avoid.
- **Test lane (host, fast).** This exact invocation, from the repo root:
  ```bash
  uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 \
    python -m pytest nessie_tests/tests -q -p no:cacheprovider
  ```
  **Baseline is `160 passed`.** Plain `uv run pytest` fails (mysqlclient will not build on
  the host). Omitting `beautifulsoup4` yields 12 *phantom* failures that look like real
  assertion failures (`assert 'error' == 'failed'`) rather than an import error. If you see
  12 failures, you forgot a `--with`.
- **Stored evidence, no paid runs.** The seed-6 run is on disk and is the regression
  corpus for this plan:
  - `/home/cdemu/nessie-run-seed6b/manifest.json` — 57 case records
  - `/home/cdemu/nessie-run-seed6b/turns.json` — 66 turn records with replies
  - `/home/cdemu/nessie-run-seed6b/triage.json` — the human verdicts
- **Commit after every task.** Conventional commits, scope `nessie`. Do not push.
- **TDD.** Every task writes the failing test first. Several of these fixes weaken an
  assertion; the test that proves it did not weaken into vacuity is the point.

---

## Task 1 — Establish the baseline

- [ ] Run the test lane above. Record the exact count. It must be `160 passed`.
- [ ] `git log --oneline -1` and record the SHA you branched from.
- [ ] Confirm `python3 -c "import sys;sys.path.insert(0,'.');from pathlib import Path;from nessie_tests import corpus;print(len(corpus.merged(Path('nessie_tests/overlay.json'))))"` prints **280**.

Do not proceed until all three match. If they do not, the worktree is not what this plan
assumes and you should stop and report.

---

## Task 2 — C2: let `evaluate.py` see a container artifact

**Problem.** No CC case can prove a file exists. `build_artifact_index`
(`nessie_tests/evaluate.py:60-76`) collects from `debug["report_saved_files"]` and
`query_complete["files"]`. A CC turn emits neither: `cc_engine.py:789-790` sets
`data["artifacts"]` and `data["cc_raw_files"]`. Different keys, so `api_artifact.*`
resolves false on every CC turn, permanently.

This is the highest-value change in the plan. It gates `export_and_file_delivery`,
`batch_upload_preparation` and `pipeline_output_reingest` — the three families that
produce files and the three that have never been routed to — and it gates the CC probe
run in the post-merge phase.

- [ ] Write a failing test in `nessie_tests/tests/test_evaluate.py`: a synthetic payload
      whose `query_complete` carries
      `{"artifacts": [{"label": "upload.xlsx", "path": "/data/scratch/upload.xlsx"}],
        "cc_raw_files": ["/data/scratch/raw.json"]}`
      and assert `build_artifact_index` returns both basenames.
- [ ] Extend `build_artifact_index` to also collect from `query_complete["artifacts"]`
      (each entry's `path`, falling back to `label`, matching the existing dict/str
      tolerance) and `query_complete["cc_raw_files"]`.
- [ ] Add a test proving the two existing sources (`report_saved_files`, `files`) still
      work, so this is additive.
- [ ] Add a test for the mixed case: a turn carrying both NS and CC shapes indexes all of them.
- [ ] Full lane green. Commit: `fix(nessie): index container artifacts so a CC case can prove a file exists`.

---

## Task 3 — B2: score a provider outage as `error`, not `failed`

**Problem.** Ten of the eighteen seed-6 reds were one Bedrock outage and were scored as
ordinary failures. Every outage therefore reads as a regression and half a run's signal
is lost. The marker is a reply containing `All provider fallbacks exhausted`.

**The subtle half.** The 2026-08-03 triage attributed nine cases and missed the tenth,
because `cons.nhp_sequencing_engine` is a **consistency group**: it replaces its members'
replies with its own summary, `count could not be resolved for 2 of 2 queries`. A careful
human reviewer read that and filed it as drift. Its two turns (ids 1054 and 1055 in
`turns.json`) both carry the outage marker. **If your fix does not catch that case, it is
not finished.**

- [ ] Write failing tests in `nessie_tests/tests/test_evaluate.py`:
      a turn whose `last_reply` carries the marker classifies `error`, not `failed`.
- [ ] Write a failing test in `nessie_tests/tests/test_consistency.py`: a group whose
      member turns carry the marker reports `error`, and specifically **not** a
      count-resolution failure. The outage check must run *before* the count-resolution
      message is composed.
- [ ] Implement in `nessie_tests/evaluate.py` and `nessie_tests/consistency.py`. Put the
      detector in one place and call it from both; do not duplicate the regex.
- [ ] Update `nessie_tests/runner.py` so `error` entries are excluded from the pass/fail
      headline and reported on their own line. An outage must not be able to fail the gate.
- [ ] Write a replay test against the stored evidence: load
      `/home/cdemu/nessie-run-seed6b/turns.json`, feed the 18 marker-carrying turns through
      the classifier, and assert every one classifies `error`. Guard the test with
      `pytest.mark.skipif` on the file's absence so it does not break a clean checkout.
- [ ] Full lane green. Commit: `fix(nessie): score a provider outage as error, not failure`.

---

## Task 4 — C1 part 1: make the floor engine-agnostic

**Problem.** `corpus.apply_family_floor` (`nessie_tests/corpus.py:45`) attaches a floor
based on `v.family`, which is engine-shaped (`search_advanced` implies REST), but the
parser may legitimately answer with a different engine. Three seed-6 cases routed NS
correctly, answered correctly via graph, and went red for it. The operator's notes on all
three read "this was correct" / "also correct" / "also correct".

**Constraint that shapes the fix.** `apply_family_floor` runs at corpus-**build** time and
appends static `PassCriterion` objects. It cannot know which engine ran. The three floor
fields are already derived at **evaluation** time (`evaluate.py:117-119`), and that is the
seam to use.

- [ ] Write a failing test: `outcome_observed` is true when any of
      `api_outcome_observed` / `graph_outcome_observed` / `report_produced_output` is true,
      and false when none is.
- [ ] Add `_outcome_observed(debug)` and wire `debug["outcome_observed"]` into
      `build_observed_debug` alongside the existing three at `evaluate.py:117-119`.
- [ ] Write a failing test: a `search_advanced` variant whose observed turn carries a
      `graph_result.count` satisfies its floor.
- [ ] In `nessie_tests/overlay.json`'s `family_floor` block, repoint the floors for the
      engine-flexible families — `search_advanced`, `search_retrieve`,
      `search_parents_by_child`, `search_tree`, `graph_query` — at `outcome_observed`.
      **Edit as text.** Leave `reporting` on `report_produced_output`: a report that
      produced no output is a real failure whatever engine ran.
- [ ] **The regression that matters.** Write a test asserting a turn that produced *no*
      outcome at all still fails the floor. This change weakens the assertion, and the
      whole point is that it must not weaken into vacuity.
- [ ] Keep the engine-specific derived fields available. A case that genuinely must use one
      engine can still assert it by hand; the floor stops *mandating* an engine, it does
      not stop anyone asserting one deliberately.
- [ ] Add a replay test against `/home/cdemu/nessie-run-seed6b/manifest.json`: the three
      cases `advanced.find_me_sequencing_files_assoc`,
      `advanced.find_me_nhp_samples_from_study_2` and
      `advanced.find_me_d_seq_samples_in_proje` satisfy the floor without editing the cases.
- [ ] Full lane green. Commit: `fix(nessie): floor asserts an outcome, not a particular engine`.

---

## Task 5 — C1 part 2: do not apply an NS floor to a CC turn

**Problem.** Part 1 alone is not enough. All three inputs to `outcome_observed` are
constant-false on a `container_cc` turn, because a CC `query_complete` carries no `debug`
key at all. So every CC-routed case in a floored family is still an automatic red that
proves nothing. `green.refine_recall` failed `api_ok` in seed 6 for exactly this reason.

**Mechanism.** `evaluate.py:282` already has an unobservable-criteria skip that records a
criterion as `skipped` with a reason rather than failing it. Extend it.

- [ ] Write a failing test: on a turn whose observed route is `container_cc`, the fields
      `api_outcome_observed`, `graph_outcome_observed`, `report_produced_output` and
      `outcome_observed` are recorded `skipped`, not `failed`.
- [ ] Write a failing test: on a turn whose route is `nextseek_query`, those same fields
      are still evaluated normally. The skip must be conditional on the route, not blanket.
- [ ] Implement by extending the `is_unobservable` path at `evaluate.py:282`. Give the skip
      a distinct reason string naming the route, so a reader of a manifest can tell this
      skip from the pre-existing `pipeline_agent.*` one.
- [ ] **Guard against vacuity again.** A CC case whose criteria are *all* skipped must not
      report `passed`. If the existing machinery would let that happen, report
      `no-assertions` or fail. Write the test for whichever behaviour you implement.
- [ ] Full lane green. Commit: `fix(nessie): skip NS outcome fields on a container_cc turn`.

---

## Task 6 — C3: stop printing a cost the harness cannot observe

**Problem.** `nessie_tests/runner.py:126-128` states that route_gate cases "never execute a
real turn/launch, even in a full run". They do. `services/cc_assistant.py:560` starts the
turn on a daemon thread and returns 202; the only early return is `ROUTE_UNRELATED`
(`:352-366`); both the NS and CC branches fall through into full execution.
`http_driver.py:96-98` breaks the **client** poll loop only, and there is no cancel, abort
or DELETE anywhere. Cost is read off `query_complete` (`runner.py:151-153`), which
route-tier polling never observes, so the run prints `$0`.

A route-tier run therefore costs roughly one full Opus turn per non-`unrelated` gate and
reports nothing. Only the `unrelated` gate is genuinely free.

- [ ] Rewrite the comment at `runner.py:126-128` to describe what the code does, citing
      `cc_assistant.py:352-366` and `http_driver.py:96-98`. State plainly that only the
      `unrelated` route is free.
- [ ] Change the summary so an unobservable cost prints `unmeasured` rather than `$0.00`.
      Do not invent a number.
- [ ] Write a test: a manifest whose entries have `cost is None` renders `unmeasured` and
      does not sum to `$0.00`.
- [ ] Full lane green. Commit: `fix(nessie): do not report an unobservable route-tier cost as $0`.

---

## Task 7 — D1: flip `green.refine_recall`'s seed assertion

**Problem.** The case asserts `route eq nextseek_query` on its seed turn. The router sent
it to `container_cc`, citing `ambiguous_study_resolution`, and produced the best answer in
the entire seed-6 run: 15 keyword matches, most flagged as substring artifacts, with the
2 genuine `Cohort='4 week'` samples separated out. That matches ground truth exactly
(2 as `'4 week'`, 237 as `'4wk'`). The operator ruled the router is right.

**The trap.** Do **not** fix this in `route_policy.overrides`. That override is already
`container_cc` and it is **inert**: `apply_route_policy` appends a route criterion only
`if "route" not in present` (`corpus.py:191`), and this variant carries an **inline**
route criterion. The inline one wins. The edit is at `nessie_tests/overlay.json:65`.

- [ ] Write a failing test asserting the *resolved* criterion — via
      `corpus.merged(overlay_path)`, not by reading the JSON — is `container_cc` for
      `green.refine_recall` turn 0.
- [ ] Edit the inline criterion at `overlay.json:65` from `nextseek_query` to
      `container_cc`. **As text.**
- [ ] Leave the `route_policy.overrides` entry alone. It is now consistent with the inline
      one rather than contradicting it.
- [ ] Add a regression test covering the general trap: for every variant carrying an inline
      route criterion, the resolved route must equal the inline value, and where a
      `route_policy.overrides` entry also exists the two must agree. This catches the whole
      class of inert-override bugs rather than this one instance. Expect it to surface
      `green.global_count`, whose override is `container_cc` while its inline criterion and
      its own `50,88[0-9]` reply assertion both say `nextseek_query` — resolve that by
      **deleting the inert override**, not by changing the inline criterion.
- [ ] Full lane green. Commit: `fix(nessie): assert the route green.refine_recall actually takes`.

---

## Task 8 — D2: restore write and delete refusal coverage

**Problem, and it is ours.** The 2026-08-03 retirement pass removed 17 `write.*` variants
as near-duplicates. `writes_unsupported` now holds **2 active variants** —
`write.download_all_samples_from_the` and `write.export_all_metadata_for_nhp_22` — and
**neither is a write**. There is no test that the assistant refuses to create, update or
delete, and there never was one for delete.

This matters more because plan 3 is closing a real mutation path (`DELETE
/nextseek_api/samples/{uid}/` is reachable from the NS REST corridor today). Nothing in the
corpus would notice that path opening again.

- [ ] Reinstate **one** create case from `retired.json` rather than authoring a new one —
      `write.create_me_investigation_testin` is the natural pick, since its historical
      failure mode is documented (two passing runs asserted contradictory facts about
      whether `POST /investigations/` exists). Reinstating is a data edit: remove the id
      from `retired.json`, as text.
- [ ] Author **one update case** and **one delete case**. Keep it to three total. Do not
      recreate the fifteen near-duplicates that were just retired.
- [ ] Each case asserts: `route eq container_cc`, `last_reply nonempty`, a positive guard
      that the reply asks for confirmation, and a **negative guard** that the reply does
      not claim the mutation happened. The negative guard is the real assertion. Model it
      on the existing pattern in `nessie_tests/probes/probe-cc-2026-07-31.json`
      (`cc.write_asks_before_creating`).
- [ ] Do **not** assert `route_source eq baml` on any turn after the first. See the
      cross-plan constraint in the handoff: plan 2 introduces `route_source: "sticky"`.
- [ ] Add a test asserting the corpus contains at least one delete-intent case, so this
      coverage cannot be silently retired again.
- [ ] Update `retired.json`'s reason field for the reinstated id, or remove the entry
      cleanly — do not leave a dangling record.
- [ ] Full lane green, and the active corpus count changes from 280 to 283. Update any test
      that hardcodes 280.
- [ ] Commit: `test(nessie): restore write and delete refusal coverage`.

---

## Task 9 — Documentation owned by this plan

- [ ] `nessie_tests/README.md`: the known-fail section is stale. All four `nessie_repro`
      variants are retired, so the merged corpus contains only `cons.nhp_sequencing_engine`
      with that tag. Correct the count and the policy description. Also correct any variant
      count to the post-task-8 number.
- [ ] `nessie_tests/output-skill/SKILL.md`: the gotcha list is a 2026-07-24 snapshot. It
      claims there is no xpass detection (there is, `runner.py:239-251`) and a 30s socket
      timeout (it is 120s). Fix both.
- [ ] Add the host test-lane invocation and its `160 passed` baseline to
      `nessie_tests/README.md`. It is not written down anywhere and it cost this session
      four attempts to rediscover.
- [ ] Commit: `docs(nessie): correct stale harness docs and record the host test lane`.

---

## Task 10 — Final verification

- [ ] Full lane green, with the new tests. Record the new count.
- [ ] Replay against the stored seed-6 evidence and record, in the PR body or a note:
      - how many cases now classify `error` (expected: **10**, not the 9 the triage found)
      - that the three `advanced.*` cases satisfy the floor unedited
      - that `green.refine_recall`'s seed criterion now matches the observed route
- [ ] `git log --oneline` shows one commit per task, all scoped `nessie`.
- [ ] Write a short completion note listing anything you could not do and why.
- [ ] Do **not** merge and do **not** push. The orchestrating session reviews all three
      diffs together before any merge.

---

## What this plan deliberately does not do

- Does not run the CC probe. That needs a rebuilt dev box and real money, and it is gated
  on task 2 landing. Post-merge, operator-driven.
- Does not touch `docker/nextseek.env` (gitignored) or any timeout. Post-merge.
- Does not change `route_capabilities.json`. That is plan 2's file, even though the two
  `sys.*` routing calls it fixes are corpus-adjacent.
- Does not fix the `report.what_kamm_samples_were_uploade` empty `reporter_context`. That
  is a product defect in the reporter, not a harness one, and it is unassigned.

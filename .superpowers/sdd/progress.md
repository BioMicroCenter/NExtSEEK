# SDD ledger — plan: docs/superpowers/plans/2026-08-28-sample-type-requirements.md

Spec: docs/superpowers/specs/2026-08-28-sample-type-requirements-design.md
Branch: feat/sample-type-requirements (stacked on feat/download-templates-page)
Base commit before Task 1: 37303abe
Test baseline (pre-existing, unrelated): 41 failed, 2003 passed, 2 skipped, 3 errors
  -> named failures in .superpowers/sdd/baseline-failures.txt
startup/tests baseline (established during Task 2, ALSO pre-existing): 1 failed, 377 passed, 1 skipped, 16 errors
  -> 16 errors are KeyError: 'ATTRIBUTE_TEST_DB_HOST' (unset env var for a DB-lane fixture)
  -> 1 failure is test_apply_all_chains_column_fixups_and_managed_indexes: it mocks apply_column_fixups
     but not compose_exec, so apply_all shells out to a real docker compose in a nonexistent /repo.
     VERIFIED pre-existing by restoring the base-commit registry and reproducing it identically.
Prior ledger: progress-2026-08-27-download-templates.md (that plan complete, 20 commits)

| Task | Status | Evidence |
|---|---|---|
| 1 Classification rule | complete | commit 3e0e223e, review clean (spec OK, quality approved). 14 passed. Reviewer mutation-tested the MAX_SET-before-append ordering: the append-then-reject mutant flips test_parents_that_never_reach_the_floor_yield_nothing, so the suite genuinely catches it. Also confirmed `support` is the all-parents denominator (a chosen-only bug would show coverage 1.0 and fail the PAV tolerance). My brief said 13 passed; actual is 14 — plan corrected. |
| 2 Table + DDL fixup | complete | commit 99054a84, 9 passed (container lane). Implementer correctly BLOCKED on a LATENT DEFECT in startup's own suite: three tests called apply_table_fixups against the REAL KNOWN_TABLE_FIXUPS with mock reply queues sized for exactly one entry, so the first added table breaks them. Scoped them to [TABLE_FIX] via patch.object, matching the pattern already used at line 82 with a fixture left unused at line 19 since it was written. Not caused by this feature; the registry was always going to grow. Table created in the running stack, 8 cols, child_code UNI, empty as expected pre-Task-4. |
| 3 Unmanaged model | complete | commit 4de7d155, 5 passed. Implementer correctly refused to report a pass on my brief's Step 5, whose criterion ('must NOT propose creating it') was WRONG. Verified independently: makemigrations proposes CreateModel for Sample_attributes_unique, Sample_types_context AND Session_state - every pre-existing unmanaged model. Django emits CreateModel for unmanaged models to track state but migrate no-ops it via Options.can_migrate. Plan corrected; the real invariant is managed=False, which test_is_unmanaged asserts. FIX PASS (commit 95ba0459): reviewer mutation-proved my brief's tests checked field NAMES only — swapping coverage to FloatField, assay_titles to null=False and source to max_length=999 left all 5 passing. Added test_field_shapes_match_the_ddl pinning type/precision/length/nullability against the DDL; mutation now caught. 6 passed. |
| 4 Management command | complete | commits 7b8c2283, 6a7b9b1d; 9 passed. Review found TWO Importants, both mutation-proven: (a) the Neo4j driver was never closed — 4 other call sites use `with GraphDatabase.driver(...)`; (b) mutating `count +=` to `count =` (last-row-wins instead of summing) left ALL 7 tests passing, because nothing asserted the numeric support of a merged pair. Now asserts D.SEQ support==2055 and PAV support==6027; mutation caught (assert 292 == 2055). Verified myself: delete() at line 106 is outside the try ending at 72, so a graph failure still cannot empty the table. |
| 5 load_requirements | complete | commit 39a589d9, 41 passed (35 pre-existing untouched, verified from the diff having no '-' lines). Reviewer mutation-tested both risky spots: `<=` -> `&` correctly fails the drop-rule test; the per-row JSON skip -> `return {}` correctly fails the malformed-row test. Two Minors folded into T6. |
| 6 View wiring | complete | commit 2f98d7f6; 14 passed + 1 EXPECTED fail (tpl-requirements-data belongs to T7's template tag — reviewer confirmed the tag is genuinely absent, not snuck in), and 43 passed in test_template_catalog. ZERO findings. Reviewer independently re-ran both T5 mutations: removing float() fails the new Decimal test (which asserts isinstance, not value — Decimal('0.98') == 0.98 is True, so a value assertion would have been fooled); narrowing the except to ValueError alone fails the parent_codes=None test. Both T5 minors now closed. |
| 7 Chips + one-of prompt | complete | commits 697cfaf9, bc9f9153, 78f08052; 16 passed. TWO real defects found and fixed: (a) Clear assigned .checked=false without firing change, so the autoAdded cleanup never ran and stale 'required by' badges survived a Clear (implementer found it themselves); (b) IMPORTANT, reviewer reproduced in Node — renderRequirements snapshotted `picked` once, so two selected types sharing one unmet parent re-attributed it to whichever iterated LAST, plus a redundant render. Reachable via 'add all'. Fixed with a live isChecked() read. Reviewer independently proved recursion terminates even for CYCLIC requirement data: check(p,true) only fires when p is unpicked, so the checked set grows monotonically and is bounded by catalog size. |
| 8 Live run + docs | complete | commit d0217feb. Implementer correctly BLOCKED first: my dispatch claimed the container held this branch's code, but it had been rebuilt from the PREVIOUS branch before Tasks 1-7 existed. They also correctly refused to fix it by switching the other checkout's branch (that checkout holds unrelated work); the right fix was `docker compose up -d --build nextseek` from THIS worktree, since build: . is relative to the invocation dir. I rebuilt and verified, then resumed. Live run: 30 requirements, matching the design's predicted 19 single + 11 disjunctive exactly. Step 1 checkpoint matched the tuned values with ZERO drift (D.SEQ->DNA 100% n=2057, PAV->NHP,PAT 98% n=6027, CEX->NHP 100% n=367). Idempotent (rerun = 30 rows, not 60). Regression diff EMPTY; 2003 -> 2044 passed. |

## Minor findings rolled up for the final review
- T7 Minor (accepted, pre-existing pattern): none of the picker's JavaScript has automated coverage. Every behaviour was verified by real click() + DOM readback against a throwaway harness. No JS test harness exists in this repo for this template.
- T7 Minor (noted, not fixed): a chain re-render does wasted work — ticking A.ALN triggers 3 full render() passes, each building a chip list from a snapshot that is stale the moment recursion adds another code. Not a problem at realistic depth.
- T5 Minor: the Decimal->float conversion has ZERO coverage — reviewer removed `float()` entirely and all 41 still passed, because every test passes plain floats through the mock. Real failure path: a Decimal reaching `json_script` in T6 raises at render. FOLDED INTO TASK 6.
- T5 Minor: the `TypeError` branch of the JSON except (e.g. `parent_codes=None`) is defensive but unexercised. FOLDED INTO TASK 6.
- T4 Minor (documented, not fixable in isolation): `test_seek_title_suffixes_collapse_to_one_assay` passes even if the merge loop de-dupes on the RAW title before stripping — `classify()` runs its own dedup over selected parents and absorbs the bug. The command's order is correct; a comment now warns whoever next touches `classify()`.
- T4 ⚠️ unverified until Task 8: all 9 tests mock the driver entirely, so `count(DISTINCT c)` semantics and the real duplicate-suffix rows are untested against the live graph.
- T2 Minor: the explanatory comment about registry scoping appears only above the first of the three re-scoped tests; the other two get a bare `patch.object`.
- Pre-flight (known, deliberate, same pattern the suggestion strip already uses): Task 7 mandates a JS mirror of `type_requirements.classify()`. A reviewer will correctly flag the duplication. The plan documents it and cross-references both sides; the alternative is a server round trip per checkbox tick.

## FINAL WHOLE-BRANCH REVIEW (opus) — NOT READY -> fixed in 7ba358c5 + 9e7c5aa6
Reviewer ran the REAL inline script under Node against a DOM stub, driving it with the LIVE 30-row
requirements table. Three Criticals, two Importants, all reproduced:
 C1 renderRequirements wrote the DOM AFTER a recursion that invalidated its own `needs` array, so the
    outer stale frame clobbered the inner correct one. Wiped real prompts on 11 of the 19 single-parent
    requirements (tick D.SEQ -> DNA added, but "DNA needs one of BAC/TIS/RNA" erased), and showed FALSE
    prompts via add-all. Fixed by collecting auto-adds, applying them, and returning before the DOM write.
 C2 An auto-added chip could NOT be removed - it snapped straight back. Directly violated the user's
    "partially enable / removable" decision; the exact user the spec refused to dead-end was dead-ended.
    Fixed with a per-child declined set.
 C3 load_requirements 500'd the whole page on a malformed row: the try wrapped only json.loads, leaving
    float(coverage) and set(parents) outside. Spec reserves source='curator' for HAND-WRITTEN rows, so the
    first curator row with a NULL coverage would have taken the page down.
 I1 Recursion termination was contingent, not structural: a requirement naming a code with no checkbox
    gave RangeError and killed the whole picker IIFE. Guarded only in Python, consumed in JS.
 I2 autoAdded cleanup was one level deep - untick A.ALN and you were left holding DNA alone, badged
    "required by D.SEQ", a type never picked credited to one no longer selected.
Minors also fixed: coverage was dead payload end-to-end (removed, which closed 2 of C3's 3 failure modes);
a test docstring asserted json_script cannot serialise a Decimal, which is FALSE (DjangoJSONEncoder renders
it as a string); the COVERAGE_FLOOR boundary was unpinned (>= -> > left all 14 tests green); the table write
was non-transactional; datetime.now() was naive under USE_TZ.

KEY PROCESS FINDING: manual click-and-read verification missed all three Criticals. A Node harness now lifts
the inline script out of the template verbatim and runs 16 cases against a DOM stub. It SKIPS in the
container (no node in the stack image, deliberately per CLAUDE.md) and runs on the host:
    node seek/tests/js/cases.js
    python3 -m pytest seek/tests/test_templates_js.py -p no:django -p no:cacheprovider -q -o addopts=""
Final state: container 237 passed / 18 skipped; host 18 passed; regression diff against baseline EMPTY.

MERGE HAZARD flagged by the reviewer: do NOT squash-merge feat/download-templates-page first. A squash
drops the ancestry link, after which merging this branch replays all 20 of its commits as new work and
conflicts against itself. Merge this branch alone (it contains both), or merge the lower one with a real
merge commit. `git merge-tree dev <head>` is currently clean.

# SDD ledger — plan: docs/2026-08-21-download-provenance-order-and-house-vocabularies-plan.md

Worktree: /Users/jps/Documents/MIT/NExtSEEK-readme-columns
Branch: feat/download-readme-columns
Base commit before Task 1: 25d2abd2

| Task | Status | Evidence |
|---|---|---|
| 1 Provenance edges + depth | complete | commits 25d2abd..dd44b6e, review clean (spec OK, quality approved) |
| 2 Chain cover | complete | commit ee1af98, review clean; reviewer fuzzed 3000 random graphs, coverage+termination hold |
| 3 Flow sheet | complete | commit 87f86c1, review clean. Implementer deviated (kept 1 of the 9 "superseded" tests as new coverage); reviewer validated by mutation - it is the ONLY test covering the Parent-fallback assay-labelling branch across 1798 tests. Brief was wrong, implementer right. |
| 4 Generation order | complete | commit 8f72be9, review clean. Width test added (closes T3 Minor). Brief Step 2 was WRONG - claimed test_a_type_with_no_lineage_sorts_last passed pre-change; it could not have. Planning artifact, not an impl defect. |
| 5 Dropdown extent | complete | commit a3057aa, review clean, zero findings. Reviewer confirmed the new test is not vacuous (fixture keeps 2 governed columns) and 500 spare rows cost ~nothing (one sqref string per rule, not per-cell). |
| 6 House vocabularies | complete | commit f471553, review clean. 84 instruments / 34 filetypes verified against the committed file; field_map fully resolves; no dupes; Illumina renames applied. |
| 7 Verification | complete | commit cad4ada. Full suite 41 failed/3 errors = baseline exactly, nothing new. Workbook sheets: README, How this data flowed, A.ADCD, A.ADCP. Ran against the PEER-RENAMED table+code; passed. |

## Minor findings rolled up for the final review
- T1 Minor: `import re` now dead in `nextseek_api/services/sample_workbook.py:12`. FOLDED INTO TASK 3 dispatch (that task rewrites the file anyway).
- T1 Minor: `test_depth_resolves_a_cycle_the_same_way_every_run` (test_sample_provenance.py) calls the function 5x on the same dict in one process, so CPython's deterministic dict order makes it pass even without the `sorted()` traversal it is meant to pin. Weak regression net; the longest-path and cycle-termination tests carry the real weight. Came from the plan, not the implementer.
- T2 Minor: `test_sample_provenance.py:96` has a second mid-file import from a module already imported at the top of the file. Cosmetic; fold into the top import block.
- T2 Minor: `sample_provenance.py:105` `depths.get(node, len(depths))` fallback is unreachable for any node in `edges`. Defensive-only, harmless.
- T2 Minor: `sample_provenance.py:92-94` docstring cites 129 hops -> 71 chains / widest 13. These figures WERE verified against the live local graph during planning (2026-08-21) but nothing in the repo re-checks them, so they can go stale silently.
- T3 Minor: `sample_workbook.py:371,376` spell the same parity test two ways (`column % 2 == 1` vs `column % 2`).
- T3 Minor: `_write_readme(..., has_flow_sheet: bool = False)` default is unnecessary; the one caller always passes it, and a forgetful future caller gets a flow sheet with no README pointer.
- T3 Minor: flow sheet column widths untested -> RESOLVED in Task 4 (test_the_flow_sheet_uses_its_own_alternating_widths; reviewer confirmed it fails if widths are swapped or dropped).
- T3 Minor: `test_no_flow_sheet_when_there_is_no_lineage` does not assert the README pointer is also absent; only incidentally caught by an unrelated cell-position assertion.
- T3 Minor: the four new workbook tests patch `load_assay_titles` but not `load_derivation_hops`, relying on Neo4j being unreachable in the runner. Matches the file's existing pattern.
- T3 Minor: `docs/sample-download-workflow.md:123+` still describes the old sheet layout and has stale line-number citations. Pre-existing drift, widened slightly.

## RETRACTED: "the test suite mutates tracked files"
Two subagents (T3 reviewer, T4 implementer) reported that running the tests rewrites
`startup/seed/sql/sample_attributes_description.sql` and three neo4j context JSONs, and BOTH
reverted the file. That was a MISATTRIBUTION. A concurrent Claude session was editing that exact
file in this shared worktree at the time.

Verified 2026-08-21 on a clean tree: `./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py`
-> 57 passed, `git status` shows ZERO changed files. Tests do not mutate the SQL script.

CONSEQUENCE: my subagents destroyed the peer session's uncommitted rewrite of that file, twice.
Peer notified. Do NOT report "tests mutate tracked files" to the user - it is not true for the
single-file runs, and the SQL portion was never the tests at all.
- T4 Minor: `write_samples_workbook` is accreting phases (load context -> load lineage -> prepare -> sort -> build -> write) with no named seam. Extraction candidate if another phase is ever added.
- T6 Minor: the `variants` block is partial, not exhaustive - it documents 12 of 84 instrument terms and never records the `Illumina NextSeq 1000` rename. Undercuts its stated purpose of making a later normalisation pass mechanical.
- T6 Minor: dropping the old `platform` vocabulary is UNDOCUMENTED in-repo (only in a subagent report that does not ship). FOLDED INTO TASK 7.

## CORRECTION to my own T6 review dispatch
I told the reviewer "this JSON was the only place in the repo holding the platform term list".
FALSE. The identical 17-term GEO platform vocabulary also lives verbatim in
`chat_nextseek/src/chat_nextseek/reports/templates/SRA.json` and `GEO-updated.json` - which are
the templates that would actually consume it to resolve ANN-8. Dropping it from the workbook
vocab file loses nothing. Do not repeat the false claim.
- T7 finding (process, not code): the running `nextseek` container image was built 2026-08-20 20:43,
  BEFORE every commit on this branch - `sample_provenance.py` was not inside it. A first workbook
  generation showed NO flow sheet, which looks exactly like "unreachable graph" or "no lineage" but
  was neither (Neo4j resolved 12,018 hops when queried directly). `./startup.sh rebuild` fixed it.
  NOTE: `./scripts/run_tests.sh` mounts the checkout over /app so TESTS were always on current code;
  only `docker exec nextseek` ran stale code. Anyone hand-verifying via docker exec must rebuild first.

## FINAL WHOLE-BRANCH REVIEW (opus) + FIX WAVE — COMPLETE
Reviewed a0f5dc00..cad4ada8 (11 commits). Found 1 Important REAL BUG the per-task reviews could
not see, because each saw only one task's diff:

  A malformed UID 500s the entire download. `str.extract` yields NaN (a float) for a UID with no
  [A-Z] run; the guard `if not child_type` let it through because `not float('nan')` is False;
  `sorted(parents)` then raised TypeError comparing float to str. It fired on the DEGRADED path
  (Neo4j down + a Parent column) and before any sheet was written, so nothing at all came out.
  Fixed in 1bb749d0 with an isinstance guard + 2 regression tests, re-verified independently.

Fix wave: 1bb749d0, ac15e6c3, 1359510b, 175df430, 816f359f. All 6 findings CLOSED and
independently re-verified (reviewer reconstructed the crash input itself, and mutation-tested
that the rewritten determinism test now genuinely fails without `sorted()`). 90 tests passing.

Verdict: READY TO MERGE.

### Deferred to issues, NOT fixed (need the user's call)
1. Silent Neo4j degradation. "Graph unreachable" and "these samples have no lineage" produce
   byte-comparable workbooks - no flow sheet, no pointer, alphabetical tabs, and the only trace
   is a traceback in logs/django.log. Provenance is the point of the feature; it degrades
   silently. A one-line README note when both the graph and the fallback come up empty closes it.
2. The 5s Neo4j bound does not bound what its docstring claims. `connection_timeout` bounds TCP
   connect and `max_transaction_retry_time` bounds retries; NEITHER bounds server-side query
   execution, so a graph that connects fast and answers slowly is unbounded. Fold into issue #110.

### Left as acceptable
`_write_vocabulary_sheet` writes the vocabulary NAME without _safe_cell_value (names are
repo-controlled, unreachable today); chain width is unbounded (107 cells on a synthetic 400-hop
graph - harmless, Excel takes 16384 columns); rank()'s dead fallback; the two parity spellings;
some new tests hit the real ORM and log a caught traceback.

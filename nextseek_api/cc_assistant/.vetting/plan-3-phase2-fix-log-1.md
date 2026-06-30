# PLAN-3 Phase 2 Hardener Fix Log (iteration 1)

## Defects addressed

| ID | Fix |
|----|-----|
| C-1 | Added Task 11a CC `chat_log` turn writer + projection `entry.get("artifacts")` |
| C-2/C-3 | Relocated persist + live `cc_traces` emit to `cc_engine.run_cc_turn` with `on_turn_complete` callback |
| C-4 | Task 9 Step 3b: explicit `celery_app.py` import for `cc_upload_tasks` |
| C-5 | Task 11 failure policy: re-raise in dev; Task 13 reload gate required |
| I-1 | Added Task 9b upload list endpoint |
| I-4 | `CCStreamTranslator` confirmed in Task 5 |
| I-6 | `files_created`/`files_modified` in `_publish_artifacts` return dict |
| I-7 | Tasks 6+8 atomic on `query_complete` handler |
| I-9/I-10 | Celery import from `celery_app`; `zstandard` in Task 1 pyproject step |
| G-1 | Documented shallow-copy-safe `cc_traces` append pattern in Task 11 |

## Self-verification

Re-read PLAN-3 after edits. Cross-checked locked SPEC-3 E1-E10 — no contradictions introduced.
Locked-design alignment: verified (no spec file edits).

Defects unresolved: none at plan level.

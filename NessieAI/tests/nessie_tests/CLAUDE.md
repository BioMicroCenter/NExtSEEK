# Working in `NessieAI/tests/nessie_tests/`

See `NessieAI/tests/nessie_tests/README.md` for what the harness is, its module surface
and its dependency edges. This file is only the things that cost someone a day. Every
command is in `NessieAI/tests/README.md`.

## Invariants

- Nothing injects a default route expectation, and reinstating one would make deliberately Container-CC-routed cases such as open-ended analysis report as product regressions across the whole corpus (`NessieAI/tests/nessie_tests/runner.py:32-45`).
- The set naming which route sources count as a real decision is an allowlist and must stay one; flipping it to a denylist would silently trust any source added later, so keyword-fallback and forced turns would start counting as routing evidence (`NessieAI/tests/nessie_tests/runner.py:14-29`).
- Three criteria are stripped from every forced arm, because asserting them under forcing tests the harness's own request body instead of the product and manufactures a pass on each arm that happens to agree (`NessieAI/tests/nessie_tests/runner.py:98-110`).
- Spend that was never observed is reported as unmeasured and never as a zero; collapsing the two hands an operator a confident total for a run that in fact billed for turns it stopped watching (`NessieAI/tests/nessie_tests/manifest.py:215-242`).
- A case's cost is the sum of every turn's router and engine cost, never one turn's: reading the last turn's value dropped every earlier turn of a multi-turn case. A part that did not run (the router on a forced turn, the engine on an `unrelated` one) is the only missing part that does not make the turn partial (`NessieAI/tests/nessie_tests/turn_cost.py:112-151`).
- A case whose criteria all skipped is recorded as having asserted nothing and counted a real failure, so that corpus drift can never present itself as a green run (`NessieAI/tests/nessie_tests/evaluate.py:589-594`, `NessieAI/tests/nessie_tests/runner.py:601-611`).
- Per-case isolation depends on a flag the server defaults to off, so it is sent on every single turn; dropping it lets one user's earlier cases prime later ones and the 2026-08-06 paired runs measured exactly that contamination (`NessieAI/tests/nessie_tests/http_driver.py:75-102`).
- One corpus file is the only source there is, and the loader rejects a superseded overlay by version rather than quietly resolving it to nothing (`NessieAI/tests/nessie_tests/corpus.py:415-425`).
- The unreviewed atlas-generated variants are filtered out before any measurement is taken; folding them in would keep every test passing while making each measurement evidence for less than it claims (`NessieAI/tests/nessie_tests/corpus.py:110-130`).
- A paid paired run is gated behind a preflight that spends one probe turn to prove the route force actually landed, because an unproven force yields a whole run in which both arms silently ran the same engine (`NessieAI/tests/nessie_tests/preflight.py:66-79`).
- This package is imported by shipping code, not only by its tests. Three engine-to-harness imports are frozen (`NessieAI/CLAUDE.md` "Boundary"), `manage.py nessie` imports the driver and runner, and the live router reads `corpus.json` for its family labels. Renaming `corpus.py`, `export.py`, `runner.py`, `bayes_manifest.py` or `corpus.json` is an application change.

## Landmines

- The host lane's explicit dependency list must include orjson. Omit it and one module fails to import, pytest interrupts collection, and **zero tests run** while the shell still shows a plausible-looking short summary (`NessieAI/tests/nessie_tests/tests/test_v4_2_set3_replay.py:7`).
- Five host-lane tests fail on every machine except one: they resolve a delivered zip through an absolute path under another developer's home directory, so a clean checkout reads as broken when nothing is (`NessieAI/tests/nessie_tests/v4_2_verifier.py:20-21`). Expect exactly those five; anything else red is yours.
- One maintenance script sources its helpers from a scratch directory belonging to a finished agent session, so it executes and prints empty results rather than failing loudly (`NessieAI/tests/nessie_tests/scripts/reverify.sh:3-5`).
- Counts written into docstrings here go stale silently. The resolved-corpus docstring states a variant count that `merged` no longer returns (`NessieAI/tests/nessie_tests/corpus.py:416`); take every count from the code.
- The paid run's own help text advertises a selection size the flag no longer selects (`NessieAI/tests/nessie_tests/cli.py:106-112`), so a budget estimate taken from the help is low. Count the selection before a paid run.
- Line citations written into this package's docstrings point into a file that has since moved, so following one lands on unrelated code: the server's early return for `unrelated` is in `_run` in `NessieAI/cc/turn.py` and the poll-loop break is at `NessieAI/tests/nessie_tests/http_driver.py:173-175`, not where `NessieAI/tests/nessie_tests/manifest.py:244-250` says.
- A route-tier gate is the cheap lane, not a free one, and budgeting it at zero is wrong in two directions at once: the router's model call happens on every turn before anything is skipped, and each non-`unrelated` gate is left running to completion on the server (`NessieAI/cc/turn.py:321-334`).
- A non-superuser's route force is discarded server-side without an error, so a paired run launched from an ordinary account produces a full set of arms in which the router, not the harness, chose every engine (`NessieAI/router/policy.py:104-112`).
- This directory carries no packaging or pytest configuration of its own (no `pyproject.toml`, `setup.py`, `setup.cfg`, `pytest.ini` or `tox.ini`). So a bare `pytest` invocation from inside it resolves the repository-root project instead, which depends on mysqlclient and dies in a C build rather than naming the real problem (the root config is `pyproject.toml:146-148`).
- There is no `conftest.py` here, and none may be added at `NessieAI/tests/` itself: that keeps the host lane Django-free. The e2e criterion DSL is imported by package (`NessieAI/tests/nessie_tests/corpus.py:7-8`, `NessieAI/tests/nessie_tests/evaluate.py:7`), and `NessieAI/tests/api/test_nessie_boundaries.py` fails on any bare `e2e` or `pathsetup` import. Never reintroduce a `sys.path` insertion to reach it.
- The container lane runs whatever was baked into the image at `/app`, not your working tree (`nextseek_api/management/commands/nessie.py:21-24`), so a green result there is not evidence about uncommitted edits. Copy the tree in first, with the command in `NessieAI/tests/README.md` "Harness container lane". The old form, copying `nessie_tests` to `/app/`, succeeds and tests stale code.
- Skill directories here are hyphenated and therefore unimportable, which means anything placed under one cannot be unit tested; the sibling skill's two scripts rotted undetected for exactly that reason (`NessieAI/tests/nessie_tests/output_skill_bayesian/__init__.py:1-11`).
- The corpus-maintenance scripts rewrite `NessieAI/tests/nessie_tests/corpus.json` in place, so a narrowed selection left uncommitted-but-unreverted silently changes what every later run and every count-checking test sees (`NessieAI/tests/nessie_tests/scripts/delta_selection.py:1-8`).

## Test command

See `NessieAI/tests/README.md` ("Harness unit tests" for the host lane, "Harness container
lane" for the database-backed tests, and the route and full tier rows for live runs).

## See also

- See `NessieAI/tests/nessie_tests/README.md` for the lanes explained, the two entry points and the paid run's flags.
- See `NessieAI/tests/nessie_tests/output-skill/SKILL.md:2-3` for triaging a finished run into a report.
- See `NessieAI/tests/nessie_tests/output-skill-bayesian/SKILL.md:2-3` for the paired run's grading flow.
- See `NessieAI/docs/nessie-question-set-2026-08-06.md:1` for the question set and its ground truth.
- See `NessieAI/router/README.md` for the route decision this harness observes, and `NessieAI/cc/README.md` for the CC engine.
- See the repository-root `CLAUDE.md` for stack-wide build and test conventions.

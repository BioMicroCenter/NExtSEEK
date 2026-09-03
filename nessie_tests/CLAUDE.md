# nessie_tests — working notes

See README.md for what the harness is, its module surface and its dependency
edges. This file is only the things that cost someone a day.

## Invariants

- Nothing injects a default route expectation, and reinstating one would make deliberately Container-CC-routed cases such as open-ended analysis report as product regressions across the whole corpus (`nessie_tests/runner.py:32-45`).
- The set naming which route sources count as a real decision is an allowlist and must stay one; flipping it to a denylist would silently trust any source added later, so keyword-fallback and forced turns would start counting as routing evidence (`nessie_tests/runner.py:14-29`).
- Three criteria are stripped from every forced arm, because asserting them under forcing tests the harness's own request body instead of the product and manufactures a pass on each arm that happens to agree (`nessie_tests/runner.py:98-110`).
- Spend that was never observed is reported as unmeasured and never as a zero; collapsing the two hands an operator a confident total for a run that in fact billed for turns it stopped watching (`nessie_tests/manifest.py:138-158`).
- A case whose criteria all skipped is recorded as having asserted nothing and counted a real failure, so that corpus drift can never present itself as a green run (`nessie_tests/evaluate.py:592-597`, `nessie_tests/runner.py:601-611`).
- Per-case isolation depends on a flag the server defaults to off, so it is sent on every single turn; dropping it lets one user's earlier cases prime later ones and the 2026-08-06 paired runs measured exactly that contamination (`nessie_tests/http_driver.py:75-102`).
- One corpus file is the only source there is, and the loader rejects a superseded overlay by version rather than quietly resolving it to nothing (`nessie_tests/corpus.py:415-425`).
- The unreviewed atlas-generated variants are filtered out before any measurement is taken; folding them in would keep every test passing while making each measurement evidence for less than it claims (`nessie_tests/corpus.py:110-130`).
- A paid paired run is gated behind a preflight that spends one probe turn to prove the route force actually landed, because an unproven force yields a whole run in which both arms silently ran the same engine (`nessie_tests/preflight.py:66-79`).

## Landmines

- The host lane's explicit dependency list must include orjson. Omit it and one module fails to import, pytest interrupts collection, and **zero tests run** while the shell still shows a plausible-looking short summary (`nessie_tests/tests/test_v4_2_set3_replay.py:7`).
- Five host-lane tests fail on every machine on earth except one: they resolve a delivered zip through an absolute path under another developer's home directory, so a clean checkout reads as broken when nothing is (`nessie_tests/v4_2_verifier.py:20-21`).
- One maintenance script sources its helpers from a scratch directory belonging to a finished agent session, so it executes and prints empty results rather than failing loudly (`nessie_tests/scripts/reverify.sh:3-5`).
- Counts written into docstrings here go stale silently. The resolved-corpus docstring claims 283 variants; measured 2026-09-03 it returns 424, and the curated subset the guard tests actually use is 365 (`nessie_tests/corpus.py:416`).
- The paid run's own help text advertises a selection of 127 variants; measured 2026-09-03 the flag selects 149, so budget estimates taken from the help are low by a fifth (`nessie_tests/cli.py:106-112`).
- Line citations written into this package's docstrings point into a file that has since moved, so following one lands on unrelated code: the routing event is emitted at `nextseek_api/services/cc_assistant.py:531` and the poll-loop break is at `nessie_tests/http_driver.py:130-132`, not where `nessie_tests/manifest.py:165-179` says.
- Do not copy the citation style used in that same docstring block. It abbreviates a second reference to one file as a bare colon and line number with the path left off, which the documentation verifier rejects outright, because a citation naming no file can never be checked and so looks sourced while being unverifiable (`nessie_tests/manifest.py:179`).
- A route-tier gate is the cheap lane, not a free one, and budgeting it at zero is wrong in two directions at once: the router's model call happens on every turn before anything is skipped, and each non-`unrelated` gate is left running to completion on the server (`nextseek_api/services/cc_assistant.py:529-537`).
- A non-superuser's route force is discarded server-side without an error, so a paired run launched from an ordinary account produces a full set of arms in which the router, not the harness, chose every engine (`nextseek_api/services/cc_assistant.py:366-374`).
- This directory carries no packaging or pytest configuration of its own: a find over it for pyproject.toml, setup.py, setup.cfg, pytest.ini and tox.ini returns nothing. So a bare pytest invocation from inside it resolves the repository-root project instead, which depends on mysqlclient and dies in a C build rather than naming the real problem (verified 2026-09-03; the root config is `pyproject.toml:146-148`).
- There is exactly one conftest, at the package root, and a find for that filename anywhere beneath this directory returns only it — nothing under tests/ or tests_container/. It exists solely to run the path insertion that makes the vendored criterion DSL importable, so deleting it breaks collection everywhere at once (`nessie_tests/conftest.py:1-3`).
- The container lane runs whatever was baked into the image at `/app`, not your working tree, so a green result there is not evidence about uncommitted edits (`nextseek_api/management/commands/nessie.py:21-24`).
- Skill directories here are hyphenated and therefore unimportable, which means anything placed under one cannot be unit tested; the sibling skill's two scripts rotted undetected for exactly that reason (`nessie_tests/output_skill_bayesian/__init__.py:1-11`).
- The corpus-maintenance scripts rewrite `nessie_tests/corpus.json` in place, so a narrowed selection left uncommitted-but-unreverted silently changes what every later run and every count-checking test sees (`nessie_tests/scripts/delta_selection.py:1-8`).

## Test command

```
uv run --no-project --with pytest --with pydantic --with requests \
  --with beautifulsoup4 --with orjson \
  python -m pytest nessie_tests/tests -q -p no:cacheprovider
```

Run from the repository root on 2026-09-03: 5 failed, 1224 passed, 28 skipped
in 34.05s. The five are the foreign absolute path noted above and are expected
until that constant is parameterised; anything else red is yours. The
database-backed lane is separate and documented with its own result in
README.md.

## See also

- See README.md for the two lanes, the live-run commands and the paid run's flags.
- See `nessie_tests/output-skill/SKILL.md:2-3` for triaging a finished run into a report.
- See `nessie_tests/output-skill-bayesian/SKILL.md:2-3` for the paired run's grading flow.
- See `docs/nessie-question-set-2026-08-06.md:1` for the question set and its ground truth.
- See `nextseek_api/cc_assistant/CLAUDE.md:1` for the engine this harness points at.
- See the repository-root CLAUDE.md for stack-wide build and test conventions.

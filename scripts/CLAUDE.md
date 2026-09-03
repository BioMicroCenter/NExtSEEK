# scripts — what will bite you

## Invariants

- This directory must never gain an `__init__.py`: the bare sibling imports
  README.md counts stop resolving the moment it becomes a real package, because
  pytest then puts the parent directory on the path instead of this one.
  Measured 2026-09-03 by bind-mounting a copy carrying an empty `__init__.py`
  over it with everything else identical, collection falls from 111 tests and 0
  errors to 42 tests and 6 errors, each of them a `ModuleNotFoundError`
  (`scripts/test_plan018_verifier_support.py:8`).
- The repo-root `evidence/` directory is committed, load-bearing state that
  these programs read back, so clearing it or moving it does not free space, it
  turns green gates red and destroys the only record of what they measured
  (`scripts/plan018_v4_5_verifier.py:73-76`).
- A verifier's `--sidecar` and `--log` arguments are the ONLY thing standing
  between a run and a write into that committed directory, because both default
  into it; run one without them and you have modified tracked files
  (`scripts/plan018_v4_5_verifier.py:35-36`).
- Overriding them is not always possible: one sibling parses no arguments at all,
  so it ignores `--sidecar`, ignores `--help`, and writes to a hardcoded path,
  which under a read-only mount is an `OSError` instead, observed 2026-09-03; a
  grep of that file for `add_argument` returns nothing and its only argparse
  trace is an unused import (`scripts/plan018_v4_4_verifier.py:5`,
  `scripts/plan018_v4_4_verifier.py:107-108`).
- Any new `test_*.py` file added here is collected by the repo-wide pytest walk
  and lands on a GitHub runner, so a scratch harness dropped in this directory
  becomes a CI result (`.github/workflows/ci-pytest.yml:63`).
- `scripts/validate_issue.py` and `scripts/validate_viewset_conventions.py` are
  the declared single source of truth for their taxonomies, so editing a
  constant in either silently changes what test modules elsewhere assert, and
  README.md's dependency section is where those modules are enumerated
  (`nextseek_api/cc_assistant/tests/test_issue_conventions_guard.py:23-24`).
- `scripts/validate_issue.py:214` scans a draft for secrets on the grounds that
  the repository is public, so filing an issue without running it is how a key
  reaches a public tracker.

## Landmines

- `scripts/run_tests.sh` cannot run from a fresh worktree and fails for two
  independent reasons, so fix one and you hit the other: it exits 1 when
  `dmac/local_settings.py` is absent, and that file is gitignored
  (`scripts/run_tests.sh:37-41`).
- Its second gate is a `cd` into a compose directory that defaults to a path
  under the caller's home, which does not exist on a machine that keeps its
  checkout elsewhere; under `set -e` the script dies there
  (`scripts/run_tests.sh:18-20`).
- A read-only container mount over this checkout dies at import, before any test
  runs, because Django creates two directories at settings-import time
  (`dmac/settings.py:498-499`); pre-create them, or hand the mount a writable
  overlay, exactly as the gate lane does at `ci/gate/live_routes.py:16`.
- The hardcoded developer home directories README.md counts cost this boundary
  two red tests, measured 2026-09-03, both tripping the same absent credential
  file that no flag can relocate (`scripts/plan018_v4_9_task8_deploy.py:41`,
  `scripts/plan018_v4_9_task8_deploy.py:3225-3230`).
- The larger damage is outside this directory: the consumer module that binds
  the attribute lane goes 34 failed, 14 passed on a machine that is not that
  developer's, every failure a `FileNotFoundError` on the same missing manifest
  (`nextseek_api/tests/test_attribute_api_harness.py:13-14`).
- Two of the files carrying such a path explode at IMPORT time rather than at run
  time, because they execute an out-of-repo module at module scope, so even
  loading one to introspect it raises
  (`scripts/validate_attribute_api_evidence.py:18-26`,
  `scripts/select_attribute_evidence.py:5-11`).
- Adding a migration turns Plan 018 gates red for a reason that has nothing to do
  with the change, because the leaf is pinned in committed evidence that still
  names `0019` (`evidence/plan018-migration-leaf.json:4`) while
  `nextseek_api/migrations/0020_assayregistrationjob.py:9-11` declares itself its
  child; measured 2026-09-03, that one mismatch is the sole reported error of
  both the V4-7 and the V4-8 verifier.
- Running the group F tests from a git worktree mounted into a container makes
  twelve of them error in fixture setup rather than at an assertion, because the
  fixture clones the checkout with `check=True` and a worktree's `.git` pointer
  file resolves outside the mount
  (`scripts/test_plan018_v4_9_owned_surface.py:47-49`).
- `scripts/seed_issue_labels.sh:4` is outward-facing: running it mutates labels
  on the public GitHub repository, and `--force` means it overwrites colours and
  descriptions rather than skipping what already exists
  (`scripts/seed_issue_labels.sh:7`).
- `scripts/test_batch_upload_e2e.py:33-35` targets a deployed shared host with
  hardcoded demo credentials, so running it writes samples into someone else's
  instance rather than into localhost.
- That same file calls `django.setup()` at module scope
  (`scripts/test_batch_upload_e2e.py:25-26`), so the repo-wide pytest walk
  imports it for zero collected tests.
- `scripts/post_uv_sync.sh` is dead: a case-insensitive grep for its stem over
  the whole worktree, excluding `.git`, `.superpowers` and this documentation
  pair, returns no line outside that file, and it targets an install layout that
  the container image does not use (`scripts/post_uv_sync.sh:5-9`).
- `scripts/nessie` is dead the same way: a fixed-string grep for its repo-relative
  path over the worktree, excluding `.git`, `.superpowers` and this directory,
  returns only two inventory rows inside a generated manifest
  (`evidence/plan018-v4-9-owned-surface.json:15184`), never an invocation.
- Eight of the 21 Plan 018 programs have neither a self-test nor an executing
  caller, so breaking one produces no failing check and nobody finds out until
  somebody runs it by hand: they are the seven `plan018_v4_[2-8]_verifier.py`
  files and `scripts/plan018_v4_9_functional_e2e.py:1`. Established 2026-09-03
  by matching the twelve `test_plan018_*.py` subjects against the 21 programs,
  and by grepping the worktree for each of the eight names outside this
  documentation pair, where the only in-directory hit is a membership set
  rather than a call
  (`scripts/plan018_v4_9_owned_surface.py:333`).
- One group F failure is absent from the CI baseline and is therefore newer than
  it: `test_checked_in_global_report_reproduces_and_validates` reports two stale
  `startup/` source hashes, a grep of the baseline for `global_coverage` returns
  no line, and the baseline's own header dates it to 2026-09-01
  (`ci/pytest-baseline.txt:19`).
- A bare `scripts/<name>` string in a document may not refer to this directory
  at all, so a consumer count taken from such strings will be too high: a `find`
  for directories named `scripts`, excluding `.git`, `node_modules` and `.venv`
  trees, returns ten on 2026-09-03, and the entrypoint the docker docs mean is
  `docker/scripts/entrypoint.sh:1`.

## Test command

    docker run --rm -i --network none --tmpfs /src/schema_rag \
      -e LOG_DIR=/tmp/nextseek-logs -e DJANGO_SETTINGS_MODULE=dmac.test_settings \
      -e PYTHONDONTWRITEBYTECODE=1 -v "$PWD":/src:ro -w /src \
      nextseek-nextseek:latest /app/.venv/bin/python -m pytest scripts -q

Run 2026-09-03 from this worktree: 111 collected, 91 passed, 8 failed, 12 errors
in 1.20s. Nineteen of those twenty red identifiers are already declared at
`ci/pytest-baseline.txt:288-306`; the twentieth is the stale-hash failure named
above. The `--tmpfs` flag is what stands in for the pre-created directories, and
the host route is not an option here: the pinned `mysqlclient` will not build
outside the image.

## See also

- See README.md in this directory for the seven purpose groups, what each reads
  and writes, and the full dependency edges in both directions.
- See `ci/README.md:180` for how the route-registry gate consumes the dumper.
- See `docs/ISSUE-CONVENTIONS.md:8-12` for the issue taxonomy these validators own.
- See `.claude/skills/nextseek-viewset/SKILL.md:18` for when to run the ViewSet validator.
- See `nextseek_api/batch_upload/README.md:248-250` for the live E2E program's context.

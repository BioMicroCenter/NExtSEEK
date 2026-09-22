# scripts: what will bite you

## Invariants

- Any new `test_*.py` file added here is collected by the repo-wide pytest walk and lands
  on a GitHub runner, so a scratch harness dropped in this directory becomes a CI result
  (`.github/workflows/ci-pytest.yml:72`).
- `scripts/validate_issue.py` and `scripts/validate_viewset_conventions.py` are the
  declared single source of truth for their taxonomies, so editing a constant in either
  silently changes what test modules elsewhere assert; README.md's dependency section is
  where those modules are listed
  (`nextseek_api/tests/repo_guards/test_issue_conventions_guard.py:23-25`).
- `scripts/validate_issue.py:214` scans a draft for secrets on the grounds that the
  repository is public, so filing an issue without running it is how a key reaches a
  public tracker.
- The codemod's `RULES` table spells module names with `/` between segments, so that the
  tree-wide greps for the old dotted names never match this file
  (`scripts/nessieai_codemod.py:85-86`). Keep new rules in that form.
- The codemod never points a reference at `NessieAI/history/`: a module that moved there
  is kept and reported, because history is not importable
  (`scripts/nessieai_codemod.py:47-48`).

## Landmines

- `scripts/run_tests.sh` cannot run from a fresh worktree and fails for two independent
  reasons, so fix one and you hit the other: it exits 1 when `dmac/local_settings.py` is
  absent, and that file is gitignored (`scripts/run_tests.sh:37-41`).
- Its second gate is a `cd` into a compose directory that defaults to a path under the
  caller's home, which does not exist on a machine that keeps its checkout elsewhere;
  under `set -e` the script dies there (`scripts/run_tests.sh:20`,
  `scripts/run_tests.sh:43`).
- A read-only container mount over this checkout dies at import, before any test runs,
  because Django creates two directories at settings-import time
  (`dmac/settings.py:507-508`); pre-create them, or hand the mount a writable overlay,
  exactly as the gate lane does at `ci/gate/live_routes.py:16`.
- Several group D programs, and the Django test module that drives them, hardcode one
  developer's home directory as their state root. On any other machine
  `nextseek_api/tests/test_attribute_api_harness.py` fails most of its tests on that
  missing root (`nextseek_api/tests/test_attribute_api_harness.py:13-14`).
- Two of the files carrying such a path explode at IMPORT time rather than at run time,
  because they execute an out-of-repo module at module scope, so even loading one to
  introspect it raises (`scripts/validate_attribute_api_evidence.py:18-26`,
  `scripts/select_attribute_evidence.py:5-11`).
- `scripts/seed_issue_labels.sh:4` is outward-facing: running it mutates labels on the
  public GitHub repository, and `--force` means it overwrites colours and descriptions
  rather than skipping what already exists (`scripts/seed_issue_labels.sh:7`).
- `scripts/test_batch_upload_e2e.py:33-35` targets a deployed shared host with hardcoded
  demo credentials, so running it writes samples into someone else's instance rather than
  into localhost.
- That same file calls `django.setup()` at module scope
  (`scripts/test_batch_upload_e2e.py:25-26`), so the repo-wide pytest walk imports it for
  zero collected tests, and `pytest scripts` on its own exits 5 ("no tests collected").
- The codemod needs `libcst`, which neither the host Python nor the app image carries. Run
  it as `uv run scripts/nessieai_codemod.py`, which reads the PEP 723 header
  (`scripts/nessieai_codemod.py:2-5`); the first run needs the network or a warm uv cache,
  and a plain `python scripts/nessieai_codemod.py` dies on the import.
- The codemod rewrites module names, not paths: `parents[N]` anchors and path joins are
  left for a human, and so is a moved module named only inside a string of the form
  `"from <kept package> import <moved module>"`. Read its report after every run
  (`scripts/nessieai_codemod.py:33-66`).
- A bare `scripts/<name>` string in a document may not refer to this directory at all, so
  a consumer count taken from such strings will be too high: `docker/scripts/`,
  `NessieAI/tests/nessie_tests/scripts/` and `NessieAI/tests/cc/scripts/` are other
  directories of that name, and the entrypoint the docker docs mean is
  `docker/scripts/entrypoint.sh:1`.

## Test command

Nothing here has a test of its own. Run the test modules that load these programs, in a
throwaway container over a read-only mount of the checkout, after pre-creating the two
directories Django makes at settings import:

    mkdir -p schema_rag/duckdb schema_rag/embedding_models
    docker run --rm -i --network none \
      -e LOG_DIR=/tmp/nextseek-logs -e DJANGO_SETTINGS_MODULE=dmac.test_settings \
      -e PYTHONDONTWRITEBYTECODE=1 -v "$PWD":/src:ro -w /src \
      nextseek-nextseek:latest /app/.venv/bin/python -m pytest \
        nextseek_api/tests/test_viewset_conventions.py \
        nextseek_api/tests/test_viewset_conventions_schema.py \
        nextseek_api/tests/repo_guards/test_issue_conventions_guard.py \
        nextseek_api/tests/repo_guards/test_validate_issue.py \
        nextseek_api/tests/test_attribute_api_harness.py -q -p no:cacheprovider

The host route is not an option: the pinned `mysqlclient` does not build outside the
image. Expect the attribute harness to fail on any machine without its developer's state
root (Landmines, above); everything else in that selection is expected to pass.

## See also

- See README.md in this directory for the seven purpose groups, what each reads and writes,
  and the dependency edges in both directions.
- See `ci/README.md` for how the route-registry gate consumes the dumper.
- See `docs/ISSUE-CONVENTIONS.md` for the issue taxonomy these validators own.
- See `.claude/skills/nextseek-viewset/SKILL.md` for when to run the ViewSet validator.
- See `nextseek_api/batch_upload/README.md` for the live E2E program's context.
- See `NessieAI/history/INDEX.md` for the Plan 018 tooling that used to live here.

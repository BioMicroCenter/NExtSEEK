# scripts

## What this is

`scripts/` holds one-off programs, not a package: the repo-convention validators, one
test wrapper, a seed regenerator, the attribute-API verification lane, a live
batch-upload program, and the NessieAI codemod. `git ls-files scripts` lists them. There
is no `__init__.py`, and the CI plan that proposed adding one
(`docs/archive/2026-09/2026-09-01-ci-increment-1-skeleton-and-safety.md:389`) was never
carried out.

What left with the NessieAI move: the Plan 018 evidence gates, their self-tests and the
repo-root evidence tree they read and wrote are archived in `NessieAI/history/plan018/`
(`NessieAI/history/INDEX.md`). The `nessie` harness wrapper is now
`NessieAI/tests/nessie_tests/scripts/nessie`, and `post_uv_sync.sh` is retired to
`NessieAI/history/retired/scripts/`.

Almost nothing here is imported the ordinary way. The one exception is
`nextseek_api/tests/test_attribute_api_db_lane.py:43`, which imports
`scripts.freeze_attribute_baseline` through the namespace-package spelling. Everything
else that consumes a file here loads it from an explicit path, for example
`nextseek_api/tests/test_viewset_conventions.py:15-21`.

The whole directory ships inside the app image at `/app/scripts`: the repo-root
`Dockerfile` copies the checkout into `/app`, and `.dockerignore` names nothing in this
directory. That is why the container lanes can run these files at all.

## Surface

The surface is not a set of entry points behind a package boundary. It is seven purpose
groups, each defined by what it reads and what it writes.

| Group | Files | Reads | Writes |
|---|---|---|---|
| A. Repo-convention validators | `validate_issue.py`, `validate_viewset_conventions.py`, `seed_issue_labels.sh`, `dump_routes.py` | repo source, `docs/ISSUE-CONVENTIONS.md` | stdout, GitHub labels |
| B. Test wrapper | `run_tests.sh` | this checkout | a pytest run inside the stack image |
| C. Seed regeneration | `generate_assay_context_seed.py` | a committed JSON export | `startup/seed/sql/assay_context.sql` |
| D. Attribute-API verification lane | `attribute_api_test.sh`, `attribute_pytest_reporter.py`, `freeze_attribute_baseline.py`, `run_attribute_coverage.py`, `run_attribute_mutants.py`, `select_attribute_chunk_defaults.py`, `select_attribute_evidence.py`, `validate_attribute_api_evidence.py` | an out-of-repo state root | an out-of-repo evidence root |
| E. Live batch-upload E2E | `test_batch_upload_e2e.py` | the SEEK database, a deployed host | Neo4j, the upload API |
| F. NessieAI codemod | `nessieai_codemod.py` | every tracked `*.py` outside `NessieAI/history/` | those files, in place |
| G. graph_search lane | `graph_search/` (see [its README](graph_search/README.md)) | the scratch MySQL, a throwaway Neo4j, seeds outside the repository | throwaway `gs-*` containers, reports outside the repository |

**A. Repo-convention validators.** `scripts/validate_issue.py:4-6` and
`scripts/validate_viewset_conventions.py:4-6` each declare themselves the single source of
truth for a taxonomy that other surfaces are drift-guarded against; the second documents
its exit codes at `scripts/validate_viewset_conventions.py:8` and prints a clean-run line
at `scripts/validate_viewset_conventions.py:492`. `scripts/seed_issue_labels.sh:7-18`
pushes those issue labels to GitHub through `gh label create --force`.
`scripts/dump_routes.py:2-7` is only the command line around the resolver walk it imports
at `scripts/dump_routes.py:26-27`.

**B. Test wrapper.** `scripts/run_tests.sh:44-47` mounts the checkout over `/app` in the
stack image and runs pytest against it, defaulting to `nextseek_api/tests`
(`scripts/run_tests.sh:22`).

**C. Seed regeneration.** `scripts/generate_assay_context_seed.py:2-8` rebuilds a
committed SQL seed from a committed JSON export, and the generated file names it back at
`startup/seed/sql/assay_context.sql:3`.

**D. Attribute-API verification lane.** `scripts/attribute_api_test.sh:4-5` dispatches
twelve named lanes, several of which shell out to `scripts/run_attribute_coverage.py` and
`scripts/run_attribute_mutants.py` (`scripts/attribute_api_test.sh:19-22`). Both that
script (`scripts/attribute_api_test.sh:206`) and the coverage driver
(`scripts/run_attribute_coverage.py:308`) load `attribute_pytest_reporter` as a pytest
plugin by dotted name (`scripts/attribute_pytest_reporter.py:35`); its session hook links
one `node-results.json` per run into a root named by an environment variable
(`scripts/attribute_pytest_reporter.py:44-49`). `scripts/freeze_attribute_baseline.py:2`
writes the frozen baseline and `scripts/select_attribute_chunk_defaults.py:106-107` writes
the content-addressed selection pointer that `scripts/select_attribute_evidence.py:5-11`
and `scripts/validate_attribute_api_evidence.py:28` then check.

**E. Live batch-upload E2E.** `scripts/test_batch_upload_e2e.py:2-6` posts a real
spreadsheet at a running deployment and then checks Neo4j; it defines no `test_` function,
so it contributes zero cases to collection. `nextseek_api/batch_upload/README.md:248-250`
describes it as a standalone program.

**F. NessieAI codemod.** `scripts/nessieai_codemod.py` rewrites Python imports and dotted
module strings from the pre-move layout to `NessieAI.*`, and it is kept so that a branch
cut before the move can be rebased and re-run through the same map
(`scripts/nessieai_codemod.py:6-15`). `--diff` previews, a bare run rewrites in place, and
`--check` exits 1 while anything is left (`scripts/nessieai_codemod.py:630-634`). It
reports every reference it keeps, finds archived or cannot resolve, and lists what it
never rewrites at `scripts/nessieai_codemod.py:33-66`. Run it with `uv run`, which reads
its PEP 723 header to fetch `libcst` (`scripts/nessieai_codemod.py:2-5`).

**G. graph_search lane.** `scripts/graph_search/lane.sh` runs the graph_search proof of
concept's throwaway lane: the scratch MySQL, a memory-capped Neo4j and the app image over
a read-only mount of this checkout, with secrets and memory caps read from a work
directory outside the repository. The folder's other scripts (the TCGA merge, graph
load and dump, parity, the benchmark) run through it. Its README is the reference.

## Running and testing

This directory has no test lane of its own. The repo-wide pytest lane still names
`scripts` as a collection root (`.github/workflows/ci-pytest.yml:72`), but no file here
defines a test: `scripts/test_batch_upload_e2e.py` is imported for zero collected tests,
so `pytest scripts` on its own collects nothing and exits 5. The programs are exercised
from outside, and CLAUDE.md gives the one command that runs those tests:

- group A's two validators, by the test modules listed under "Depended on by", below;
- group D, by `nextseek_api/tests/test_attribute_api_harness.py` and
  `nextseek_api/tests/test_attribute_api_db_lane.py`;
- `scripts/dump_routes.py`, by nothing directly: it shares its resolver walk with the
  blocking route gate (`ci/gate/live_routes.py:3-6`).

Groups B, C, E, F and G are run by hand. `scripts/validate_viewset_conventions.py` with no
arguments exits 0 and prints its clean-run line when the tree has no violations.
`scripts/run_tests.sh` refuses to start from a fresh worktree, for two separate reasons;
see CLAUDE.md.

## Depends on / depended on by

Depends on:

- Django, imported at module scope by two programs
  (`scripts/freeze_attribute_baseline.py:14`, `scripts/test_batch_upload_e2e.py:25-26`),
  so neither loads without Django settings.
- The `docker` binary and a built stack image, for the wrapper
  (`scripts/run_tests.sh:44-47`) and for parts of group D
  (`scripts/attribute_api_test.sh:217`).
- The `gh` binary and network access to github.com, for the label seeder
  (`scripts/seed_issue_labels.sh:7`).
- The `git` binary, which the codemod asks for the tracked file list
  (`scripts/nessieai_codemod.py:614-615`).
- One developer's home directory, hardcoded as the state root of several group D programs;
  `grep -rlE '/(home|Users)/' scripts` lists them, and
  `scripts/validate_attribute_api_evidence.py:18-26` is one.
- Third-party packages beyond the Django stack: `coverage`
  (`scripts/run_attribute_coverage.py:12`), `yaml` and `pydantic`
  (`scripts/validate_issue.py:28-29`), `requests` (`scripts/test_batch_upload_e2e.py:28`),
  and `libcst`, which only the codemod needs and which neither the host Python nor the app
  image carries.

Depended on by:

- The GitHub pytest job, which names this directory as a collection root
  (`.github/workflows/ci-pytest.yml:72`).
- The route-registry gate, which documents `scripts/dump_routes.py` as one of the two
  callers of its resolver walk (`ci/gate/live_routes.py:3-6`); `ci/README.md` records that
  the dumper is the only file outside `ci/` that imports anything from it
  (`ci/README.md:274-277`).
- Three Django test modules that load `scripts/validate_viewset_conventions.py` from an
  explicit path (`nextseek_api/tests/test_viewset_conventions.py:15-21`,
  `nextseek_api/tests/test_viewset_conventions_schema.py:12-16`,
  `nextseek_api/assay_registration/tests/test_views.py:685`).
- Two repo guards that load `scripts/validate_issue.py` the same way
  (`nextseek_api/tests/repo_guards/test_issue_conventions_guard.py:23-25`,
  `nextseek_api/tests/repo_guards/test_validate_issue.py:13-15`); the first also reads
  `scripts/seed_issue_labels.sh` and checks its labels against the validator.
- One test module that binds three group D files by path and puts this directory on
  `sys.path` so that their own sibling import resolves
  (`nextseek_api/tests/test_attribute_api_harness.py:15-22`), and one that reaches group D
  through the dotted namespace-package spelling
  (`nextseek_api/tests/test_attribute_api_db_lane.py:43`).
- The committed ViewSet skill, which tells an author to run the conventions validator
  before finishing (`.claude/skills/nextseek-viewset/SKILL.md:18`), and
  `docs/ISSUE-CONVENTIONS.md`, which names the issue validator and the label seeder as the
  taxonomy's source and its downstream (`docs/ISSUE-CONVENTIONS.md:8-12`).
- Group B, reached only by prose: `nextseek_api/README.md` "Running and testing" and the
  root `CLAUDE.md` "Build and test" name it, with the preconditions it fails on.
- Not a consumer: a `scripts/<name>` string that names a file in another directory called
  `scripts`, such as `docker/scripts/entrypoint.sh:1`, the harness wrapper under
  `NessieAI/tests/nessie_tests/scripts/` or the operator-run gate scripts under
  `NessieAI/tests/cc/scripts/`. See CLAUDE.md for why that is easy to miscount.

# scripts

## What this is

`scripts/` is a pile of 50 one-off programs, not a package. Counted 2026-09-03 by
listing the directory and splitting on extension: 44 `.py`, five `.sh`, and one
extensionless wrapper, `scripts/nessie`. All 50 are tracked, checked 2026-09-03
with `git ls-files` over this directory, whose output was exactly those 50 paths
and nothing else. Every count below is over that same set of 50 program files,
never over this documentation pair. There is no
`__init__.py`: a `find` for that name directly inside this directory returns
nothing, and the CI plan that proposed adding one
(`docs/superpowers/plans/2026-09-01-ci-increment-1-skeleton-and-safety.md:389`)
was never carried out.

Almost nothing here is imported the ordinary way. A repo-wide search for a line
whose first token is `from scripts` or `import scripts`, over every `.py` file
outside `.git`, `.superpowers`, `node_modules` and `.venv`, returns exactly two
hits: `nextseek_api/tests/test_attribute_api_db_lane.py:43` and
`scripts/test_plan018_v4_9_task8_coverage.py:6`. Everything else that consumes a
file here loads it from an explicit path, for example
`nextseek_api/tests/test_viewset_conventions.py:15-21`.

The whole directory ships inside the app image at `/app/scripts`: grepping the
repo-root Dockerfile for lines beginning `COPY` or `ADD` returns exactly one, a
whole-tree copy into `/app`, and a grep of `.dockerignore` for the string
`scripts` returns zero matches. That is why the container lanes below can run
these files at all.

Roughly two thirds of the directory is frozen evidence tooling from one finished
plan. See CLAUDE.md for the traps that history leaves behind.

## Surface

The surface here is not a set of entry points behind a package boundary. It is
seven purpose groups, each defined by what it reads and what it writes; a
50-row per-file table would tell a reader nothing. Membership below is by
filename prefix and sums to 50.

| Group | Files | Reads | Writes |
|---|---|---|---|
| A. Repo-convention validators | 4 | repo source, `docs/ISSUE-CONVENTIONS.md` | stdout, GitHub labels |
| B. Test and bring-up wrappers | 3 | this checkout | a container-side report |
| C. Seed regeneration | 1 | a committed JSON export | `startup/seed/sql/assay_context.sql` |
| D. Attribute-API verification lane | 8 | an out-of-repo state root | an out-of-repo evidence root |
| E. Plan 018 V4 evidence gates | 21 | repo source and `evidence/` | `evidence/` |
| F. Self-tests for group E | 12 | the group E modules, `evidence/` | nothing |
| G. Live batch-upload E2E | 1 | the SEEK database, a deployed host | Neo4j, the upload API |

**A. Repo-convention validators.** `scripts/validate_issue.py:4-6` and
`scripts/validate_viewset_conventions.py:4-6` each declare themselves the single
source of truth for a taxonomy that other surfaces are drift-guarded against;
the second documents its exit codes at
`scripts/validate_viewset_conventions.py:8` and prints a clean-run line at
`scripts/validate_viewset_conventions.py:492`.
`scripts/seed_issue_labels.sh:7-18` pushes those issue labels to GitHub through
`gh label create --force`. `scripts/dump_routes.py:2-7` is only the command line
around the resolver walk it imports at `scripts/dump_routes.py:26-27`.

**B. Test and bring-up wrappers.** `scripts/run_tests.sh:44-47` mounts the
checkout over `/app` in the stack image and runs pytest against it, defaulting to
`nextseek_api/tests` (`scripts/run_tests.sh:22`). `scripts/nessie:13` runs the
harness through `manage.py nessie` in the container and copies the report out at
`scripts/nessie:18-19`. `scripts/post_uv_sync.sh:5-9` re-links two report
templates into a `/opt/NExtSEEK` virtualenv.

**C. Seed regeneration.** `scripts/generate_assay_context_seed.py:2-8` rebuilds a
committed SQL seed from a committed JSON export, and the generated file names it
back at `startup/seed/sql/assay_context.sql:3`.

**D. Attribute-API verification lane.** `scripts/attribute_api_test.sh:4-5`
dispatches twelve named lanes, several of which shell out to
`scripts/run_attribute_coverage.py` and `scripts/run_attribute_mutants.py`
(`scripts/attribute_api_test.sh:19-22`). Both that script
(`scripts/attribute_api_test.sh:206`) and the coverage driver
(`scripts/run_attribute_coverage.py:308`) load `attribute_pytest_reporter` as a
pytest plugin by dotted name (`scripts/attribute_pytest_reporter.py:35`); its
session hook links one `node-results.json` per run into a root named by an
environment variable (`scripts/attribute_pytest_reporter.py:44-49`).
`scripts/freeze_attribute_baseline.py:2` writes the frozen baseline and
`scripts/select_attribute_chunk_defaults.py:106-107` writes the
content-addressed selection pointer that `scripts/select_attribute_evidence.py:5-11`
and `scripts/validate_attribute_api_evidence.py:28` then check.

**E. Plan 018 V4 evidence gates.** Each verifier is a standalone CLI that
accumulates named checks (`scripts/plan018_v4_5_verifier.py:42-45`) and exits 1
if any of them failed (`scripts/plan018_v4_5_verifier.py:181`). What it checks is
source-shaped rather than behavioural: that a migration file is present
(`scripts/plan018_v4_5_verifier.py:47-48`), or that a token still occurs in a
module's text (`scripts/plan018_v4_5_verifier.py:87-88`). Seventeen of the 50
program files name an `evidence/plan018` path, counted 2026-09-03 with a
recursive fixed-string grep.
`scripts/plan018_verifier_support.py:1` carries the AST and JUnit helpers they
share, and `scripts/plan018_lane_m_mysql.sh:2-7` is the disposable-MySQL lane
whose recipe authority is itself a committed sidecar.

**F. Self-tests for group E.** Twelve `test_plan018_*.py` modules, collected by
the repo-wide pytest walk. Six import their subject by bare module name, counted
2026-09-03 by grepping this directory's test modules for a line beginning
`import plan018` or `from plan018`; one of the six is
`scripts/test_plan018_v4_9_task5_mutation.py:6`.

**G. Live batch-upload E2E.** `scripts/test_batch_upload_e2e.py:2-6` posts a real
spreadsheet at a running deployment and then checks Neo4j; it defines no `test_`
function, so it contributes zero cases to collection.
`nextseek_api/batch_upload/README.md:248-250` describes it as a standalone
program.

## Running and testing

This directory has no test lane of its own: none of its 50 program files is a
`conftest.py`, a `pyproject.toml` or a runner, and no such file exists here under
any other name either. What exercises it instead is the repo-wide
pytest lane, which names `scripts` as one of six collection roots at
`.github/workflows/ci-pytest.yml:63`, and whose known-failing identifiers for
this directory are recorded at `ci/pytest-baseline.txt:288-306`. See CLAUDE.md
for the one command and what it really printed.

Groups A, B and G are run by hand. Group D is driven both by hand and by
`nextseek_api/tests/test_attribute_api_harness.py:15-17`. Group E has no lane of its
own: group F reaches its modules by import and everything else is by hand.

Measured 2026-09-03 against the built app image, running six of the seven
standalone `plan018_v4_[2-8]` verifiers with their output redirected out of
`evidence/`, the seventh being excluded because it writes there regardless: 6
invocations, 2 ok, 4 failed. `scripts/plan018_v4_5_verifier.py` reported a PASS
gate on 22 of 22 checks and `scripts/plan018_v4_6_verifier.py` reported a PASS
gate on 28, while `scripts/plan018_v4_2_verifier.py`,
`scripts/plan018_v4_3_verifier.py`, `scripts/plan018_v4_7_verifier.py` and
`scripts/plan018_v4_8_verifier.py` each exited 1.

Running `scripts/validate_viewset_conventions.py` with no arguments in that same
image, also 2026-09-03, exits 1 and prints six violation lines against the
current tree, so a red run of that validator is the expected state today rather
than a signal that you broke something.

`scripts/run_tests.sh` refuses to start from a fresh worktree, for two separate
reasons; see CLAUDE.md.

## Depends on / depended on by

The two directions have different shapes. Inbound, this directory depends far
more on absolute filesystem paths and external binaries than on Python imports.
Outbound, it is consumed by test modules that load a file by path, by CI
configuration that names the directory, and by prose that tells a human to run a
command.

Depends on:

- `evidence/` at the repo root is load-bearing committed input, not scratch: a
  `find` for regular files beneath it returns 245 on 2026-09-03, a grep of
  `.gitignore` for the string `evidence` returns no line, and group E addresses
  artifacts inside it as constants
  (`scripts/plan018_v4_9_global_coverage.py:14`).
- Five modules import `nextseek_api` at module scope and so need Django settings
  before they will even load (`scripts/plan018_v4_3_verifier.py:16`,
  `scripts/plan018_v4_4_verifier.py:18-21`, `scripts/plan018_v4_8_verifier.py:22`,
  `scripts/plan018_v4_9_task7_recovery.py:18`,
  `scripts/plan018_v4_9_task8_deploy.py:33`).
- The many `from nextseek_api …` lines further down that last file, 147 KB of it,
  are NOT imports it performs: they are the body of a Python source string it
  returns for execution inside a disposable container
  (`scripts/plan018_v4_9_task8_deploy.py:1632-1645`).
- Two modules import Django itself at module scope
  (`scripts/freeze_attribute_baseline.py:14`,
  `scripts/test_batch_upload_e2e.py:25-26`).
- One verifier depends on the sibling harness package rather than on the Django
  app (`scripts/plan018_v4_2_verifier.py:16`).
- The `docker` binary and a built stack image are a hard requirement for 13 of
  the 50 program files, counted 2026-09-03 by grepping them for a `docker`
  subcommand in a shell line or a quoted `docker` argv token; the four wrappers
  among them are `scripts/run_tests.sh:44-47`, `scripts/nessie:13`,
  `scripts/plan018_lane_m_mysql.sh:29` and
  `scripts/attribute_api_test.sh:217`.
- The `git` binary is required by the ownership gate and cannot be substituted,
  because every call is made with `check=True`
  (`scripts/plan018_v4_9_owned_surface.py:105-106`).
- The `gh` binary and network access to github.com are required by the label
  seeder (`scripts/seed_issue_labels.sh:7`).
- Twelve of the 50 program files hardcode one developer's home directory, 38
  occurrences in all, counted 2026-09-03 with a recursive grep for that home path
  over those files only; two of them are
  `scripts/validate_attribute_api_evidence.py:18-26` and
  `scripts/plan018_v4_9_task8_deploy.py:41`.
- Third-party packages beyond the Django stack are needed by individual members:
  `orjson` (`scripts/plan018_v4_2_verifier.py:10`), `coverage`
  (`scripts/run_attribute_coverage.py:12`), `pydantic`
  (`scripts/validate_issue.py:29`), `yaml`
  (`scripts/test_plan018_v4_9_task8_deploy.py:14`) and `requests`
  (`scripts/test_batch_upload_e2e.py:28`).

Depended on by:

- The GitHub pytest job names this directory as a collection root, which is what
  puts group F on a runner at all (`.github/workflows/ci-pytest.yml:63`).
- The route-registry gate documents `scripts/dump_routes.py` as one of two
  callers of its resolver walk (`ci/gate/live_routes.py:5-6`), and the CI README
  records that the dumper is the only file outside that boundary importing
  anything from it (`ci/README.md:180-182`).
- Three Django test modules load `scripts/validate_viewset_conventions.py` from
  an explicit path (`nextseek_api/tests/test_viewset_conventions.py:15-21`,
  `nextseek_api/tests/test_viewset_conventions_schema.py:12-16`,
  `nextseek_api/assay_registration/tests/test_views.py:685`).
- Two more load `scripts/validate_issue.py` the same way
  (`nextseek_api/cc_assistant/tests/test_issue_conventions_guard.py:23-24`,
  `nextseek_api/cc_assistant/tests/test_validate_issue.py:13-14`).
- One test module binds three group D files by path and puts this directory on
  `sys.path` so that their own sibling import resolves
  (`nextseek_api/tests/test_attribute_api_harness.py:15-22`).
- One test module reaches group D through the dotted namespace-package spelling
  instead (`nextseek_api/tests/test_attribute_api_db_lane.py:43`).
- The committed ViewSet skill tells an author to run the conventions validator
  before finishing (`.claude/skills/nextseek-viewset/SKILL.md:18`), and the issue
  conventions doc names both the issue validator and the label seeder as the
  taxonomy's source and its downstream
  (`docs/ISSUE-CONVENTIONS.md:8-12`).
- Group E has no consumer outside this directory except prose: grepping the
  worktree for the literal `scripts/plan018` with `.git`, `.superpowers`, `node_modules`, this directory
  and `evidence/` excluded returns 16 files on 2026-09-03, every one of them
  Markdown prose or a `CITATIONS.txt` list rather than an invocation, of which
  `nextseek_api/eval/README.md:237` is representative.
- Group B is reached only by prose too, and the documentation that names it
  already records both of the preconditions it fails on
  (`nextseek_api/README.md:134-137`).
- Excluded from the list above: every hit inside `evidence/`, which is this
  directory's own output rather than a consumer of it, and every `scripts/<name>`
  token naming a file that is not in this directory, such as the entrypoint the
  docker docs mean, which is `docker/scripts/entrypoint.sh:1`.
- See CLAUDE.md for why that second class of hit is easy to miscount.

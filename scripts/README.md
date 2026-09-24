# scripts

## What this is

`scripts/` holds one-off programs, not a package: the repo-convention validators, one
test wrapper, the context generator, the attribute-API verification lane, a live
batch-upload program, and the NessieAI codemod. `git ls-files scripts` lists them. There
is no `__init__.py`, and the CI plan that proposed adding one
(`docs/archive/2026-09/2026-09-01-ci-increment-1-skeleton-and-safety.md:389`) was never
carried out.

What left with the NessieAI move: the Plan 018 evidence gates, their self-tests and the
repo-root evidence tree they read and wrote are archived in `NessieAI/history/plan018/`
(`NessieAI/history/INDEX.md`). The `nessie` harness wrapper is now
`NessieAI/tests/nessie_tests/scripts/nessie`, and `post_uv_sync.sh` is retired to
`NessieAI/history/retired/scripts/`.

`generate_assay_context_seed.py` still writes the installer's
`startup/seed/sql/assay_context.sql` from a committed JSON export, and `context_gen.py`
writes only the held `*.curated.sql` seeds, so no file has two writers. The old
generator retires when the curated seeds are switched on (group C). Until then its
file's `CREATE TABLE` is the independent fixture `NessieAI/tests/api/test_context_gen.py`
checks the generator's assay columns against.

Almost nothing here is imported the ordinary way. The one exception is
`nextseek_api/tests/test_attribute_api_db_lane.py:43`, which imports
`scripts.freeze_attribute_baseline` through the namespace-package spelling. Everything
else that consumes a file here loads it from an explicit path, for example
`nextseek_api/tests/test_viewset_conventions.py:15-21`.

The whole directory ships inside the app image at `/app/scripts`: the repo-root
`Dockerfile` copies the checkout into `/app`, and `.dockerignore` names nothing in this
directory. That is why the container lanes can run these files at all.

## Surface

The surface is not a set of entry points behind a package boundary. It is nine purpose
groups, each defined by what it reads and what it writes.

| Group | Files | Reads | Writes |
|---|---|---|---|
| A. Repo-convention validators | `validate_issue.py`, `validate_viewset_conventions.py`, `seed_issue_labels.sh`, `dump_routes.py` | repo source, `docs/ISSUE-CONVENTIONS.md` | stdout, GitHub labels |
| B. Test wrapper | `run_tests.sh` | this checkout | a pytest run inside the stack image |
| C. The context generator | `context_gen.py`, `generate_assay_context_seed.py` | `context/*.json`; a committed JSON export | update SQL for a live database, the held `startup/seed/sql/*_context.curated.sql` seeds, the generated investigation block in `capabilities.md`, the committed JSON context exports in `NessieAI/chat_nextseek/src/chat_nextseek/context/`; the installer's `startup/seed/sql/assay_context.sql` |
| D. Attribute-API verification lane | `attribute_api_test.sh`, `attribute_pytest_reporter.py`, `freeze_attribute_baseline.py`, `run_attribute_coverage.py`, `run_attribute_mutants.py`, `select_attribute_chunk_defaults.py`, `select_attribute_evidence.py`, `validate_attribute_api_evidence.py` | an out-of-repo state root | an out-of-repo evidence root |
| E. Live batch-upload E2E | `test_batch_upload_e2e.py` | the SEEK database, a deployed host | Neo4j, the upload API |
| F. NessieAI codemod | `nessieai_codemod.py` | every tracked `*.py` outside `NessieAI/history/` | those files, in place |
| G. graph_search lane | `graph_search/` (see [its README](graph_search/README.md)) | the scratch MySQL, a throwaway Neo4j, seeds outside the repository | throwaway `gs-*` containers, reports outside the repository |
| H. APOC schema prototype | `graph_schema_from_apoc.py` | a live Neo4j with APOC (read only), the committed graph schema files | one JSON file you name |
| I. Download API parity | `sample_retrieve_parity.py` | a live stack's MySQL and Neo4j, read only | stdout, and one JSON-lines file you name |

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

**C. The context generator.** The five JSON files Nessie reads are exports of three MySQL
tables, rewritten in place once per UTC day by `_fetch_context_files_from_db`
(`NessieAI/chat_nextseek/src/chat_nextseek/config.py:717-725`), so editing an export
changes nothing that survives a day. `context/` is the hand-owned source and
`scripts/context_gen.py` is the only way it reaches a database. It emits three things:

```
python scripts/context_gen.py --emit update --table all --out /tmp/context.sql
python scripts/context_gen.py --emit seed --table all
python scripts/context_gen.py --emit capabilities --counts /tmp/counts-local.json
```

`--emit update` writes one re-runnable script for a live database. Its schema part adds
missing columns, widens narrower ones and moves text columns to utf8mb4, removing nothing.
Then every row change of every section runs in ONE transaction, and checks at its end
(row counts, a digest of every curated value, every assay's internal assay link, every
mapping operation's post-condition) decide whether it commits. On any problem the client
stops at `ERROR 1231 ... context_gen REFUSED ...`, the full list printed above it, and
nothing was committed. A second run changes no row. `assay_context` rows are linked to
`internal_assays` by title at apply time, never by the curated number, so a stack whose
internal assays are numbered differently is refused rather than mislinked.
`projects_context` is keyed on `(name, entity_type)`, because a project and an
investigation may share a name: the schema part turns the live table's `PRIMARY KEY (name)`
into the pair and drops the old held seed's unique key on `name`, each step conditional
on the shape it finds, and the keys part adds `uq_projects_context_name_type` where the
primary key is `id`. `pi` is display prose that nothing parses; `present_on` is a curated
key the generator reads and never writes (`context/README.md`). Nothing here
connects to a database: the operator applies the SQL, after a restore-tested backup, and
never with `mysql --force` (the commit is conditional, so `--force` rolls back too, but it
exits 0). `context/README.md` owns the source conventions and the review gate.

**The JSON exports have two readers, and only one sees a database.** `--emit exports`
writes `sampletypes_db.json`, `min_sampletypes_db.json`, `assays_db.json`, `min_assays_db.json`
and `projects_db.json` from `context/`, field for field with `map_sampletype`,
`map_sampletype_min`, `map_assay`, `map_assay_min` and `map_project` in `config.py`. Inside the
**app** `_fetch_context_files_from_db` rewrites them from the context tables once per UTC day, so
the app converges on the database and these copies are only its fallback. The **cc-agent** does
not: its Dockerfile COPYs three of them out of the checkout at build time
(`startup/lib/layout.py::CANONICAL_CONTEXT_FILES`), the container holds no database connection,
and its own `MANIFEST.md` sends the agent to `min_sampletypes_db.json` to map a kind of sample to
its code. So run `--emit exports` in the same change as `--emit update` and rebuild both images:
the stack-health check `cc-agent context` compares the checkout with the image, so two stale
copies of one file read as green and nothing reports the drift. Two deliberate differences from
the runtime export, both because nothing here connects to anything: a project row gets no `labs`
key (the runtime reads those from SEEK's institutions), and row order is the curated file's.

**The curated seeds are held.** `--emit seed` writes
`startup/seed/sql/{sample_types_context,assay_context,projects_context}.curated.sql`, and
no install step reads them: `startup/steps/schema_fixups.py` still registers the
pre-generator `assay_context.sql` and the empty `projects_context.sql`, so `install` and
`reset` load exactly what they loaded before this generator existed. The files are
committed so the review sees what a fresh install would get, and
`test_seed_matches_the_committed_files_shape` keeps them byte-identical to `--emit seed`.
Switching them on after the content is signed off is one reviewed commit: point the
`assay_context` and `projects_context` fixups at the `.curated.sql` files, add a
`sample_types_context` fixup, retire `generate_assay_context_seed.py` with its output, and
invert `test_the_curated_seeds_are_held_until_sign_off`.

Apply it with the charset named, even though the file names it too:

```
mysql --default-character-set=utf8mb4 -u<user> -p <database> < /tmp/context.sql
```

The client default is `auto`, which resolves to **latin1** wherever no UTF-8 locale is
set — which is the case inside the `db` container, so every apply path that does not say
otherwise double-encodes each non-ASCII value. Both emitted artifacts open with
`SET NAMES utf8mb4;` for that reason, and the flag above is the belt to its braces.

Do not hand-apply a `startup/seed/sql/*_context.curated.sql` file to a stack that already has the
table: `CREATE TABLE IF NOT EXISTS` skips, so the unique key is never created and the
INSERTs land on top of the rows already there. `--emit update` is what brings an existing
instance to the curated content.

It also owns `capabilities.md`'s "Known Projects and Investigations" list, as a marked
`<!-- BEGIN CONTEXT-GEN:investigations -->` block built from the `context/projects.json`
rows whose `entity_type` is `investigation`. `render_capabilities_text` writes one bullet
per row, sorted by name: the exact title in bold, a colon, its `research_focus`, the names
people use in brackets, and `(not on every instance: loaded on local and dev only)` for a
row whose `present_on` lists instances. Names and a short description only: a baked sample
count rots the day the next sync runs. It needs no graph. `check_investigation_counts`
holds the refusals that do, and `--emit capabilities` runs both and writes nothing unless
both pass; the counts only refuse, they never change the text.

The counts come from `manage.py graph_sync --investigation-counts --instance <profile>
--json`, one file per instance, each passed with its own `--counts`. A file names the
instance it was measured on and enumerates every Investigation title in that graph with its
nodes and samples. On an instance a row is on (every instance, unless its `present_on` says
otherwise) its title must hold samples; on an instance its `present_on` leaves out, the
title must be absent, and an empty node there is refused as the confident zero it would
answer. A title that holds samples and that no row names is refused as well, unless
`--ignore-investigation TITLE` names it. The flat `{title: count}` shape and drift's stat
are refused: neither says where it was measured, and neither can tell an absent
investigation from an empty one. `catalog.assistant_investigations` in
`nextseek_api/graph_sync/drift.py` stays the runtime backstop, with the same absent-versus-
empty rule for a name the block marks. The markers must be exactly one BEGIN then one END,
in the section under the exact H2 heading drift keys on, with no heading or `---` line
between them, and `replace_capabilities_block` refuses otherwise: reversed or duplicated
markers duplicated text, an END placed too low deleted the sections after it, and a block
outside drift's section leaves drift no names, so its check *passes* with the backstop off.

Regenerating the block does **not** make `route_capabilities.json` stale, contrary to an
earlier note here: the NS projection reads only the three required H2 sections, so the
projection comes out byte for byte identical
(`test_regenerating_the_block_leaves_the_ns_projection_identical`). The step that carries
a new list to the agent is the image COPY and rebuild.

The markers are placed around the section in `capabilities.md` (task 6.15c): the BEGIN
line directly under the heading's blank line, the END line directly after the outro, with
nothing between the heading and BEGIN. What sits between them is the generator's.

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
so it contributes zero cases to collection. `nextseek_api/batch_upload/README.md:230-232`
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

**H. APOC schema prototype.** `scripts/graph_schema_from_apoc.py` is a prototype, not
wired into the product: it builds the graph's structural schema from `apoc.meta.stats`,
`apoc.meta.schema` and `apoc.meta.nodeTypeProperties` (`--full` scans every node), reads
the catalog graph_sync writes, and compares both with `graph_schema_structure.txt`,
`min_graph_schema.json` and the guard's property sets in `agents/graph.py`. Every
statement is a READ transaction with a timeout, and it calls no path procedure. Its
docstring has the `docker run` line.

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

Group C is tested three ways. `NessieAI/tests/api/test_context_gen.py` needs no
database: it re-derives every expected column from files the generator does not own, and
runs the plain row statements on an in-memory SQLite to check the curated values come back
field for field. `NessieAI/tests/api/test_context_gen_mysql.py` is the real lane: it
applies the update (twice) and each seed file to a throwaway `mysql:8.0` over a
production-shaped pre-state, including drift, a differently numbered stack, a mid-apply
failure and `--force`. It starts a container, so it runs only with `CONTEXT_GEN_MYSQL=1`
and skips otherwise. `ci/gate/test_context_capabilities_markers.py` keeps the markers in
the committed `capabilities.md` well formed, in the blocking gate.

Groups B, E, F and G are run by hand. `scripts/validate_viewset_conventions.py` with no
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
- `NessieAI/tests/api/test_context_gen.py`, which imports `scripts.context_gen` through
  the namespace-package spelling.
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

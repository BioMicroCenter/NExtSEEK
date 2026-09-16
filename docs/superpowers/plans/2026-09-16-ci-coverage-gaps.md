# CI Coverage Gaps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore the migration check to blocking CI, and give the context the assistant reads two checks it has
never had: authoring-time validation of the files and runtime drift of the graph's copy.

**Architecture:** Two independent parts. Part A (C1 to C3) repairs and restores the migration check. Part B (C4 to
C7) tracks the context validator into CI and builds the three catalog drift checks the graph-sync design already
specifies. Part A has no dependencies. **Part B starts only after the `context/` branch has merged into
`dev-graph`.**

**Tech Stack:** Python 3.14, Django, pytest, GitHub Actions, the neo4j Python driver.

**Spec:** [`docs/superpowers/specs/2026-09-16-ci-coverage-gaps-design.md`](../specs/2026-09-16-ci-coverage-gaps-design.md)

## Global Constraints

- The Django lane for every test run in this plan:
  ```bash
  cd /home/cdemurjian/code/dmac/docker/wt-gs-sync
  mkdir -p schema_rag/duckdb schema_rag/embedding_models
  docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
    -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
    /app/.venv/bin/python -m pytest <paths> -q -p no:cacheprovider
  ```
- **Before starting any container, check `graph-search/.gs-bench-running` and stop if it exists.**
- A check joins the blocking step only once it is clean (decision A4). Never add a red check to a blocking lane.
- No em dashes or en dashes in added lines or commit messages.
- Commit messages end with the two trailer lines this repo uses.

---

# Part A: the migration check

### Task C1: Name the TurnLedger index so Django stops proposing a rename

**Files:**
- Modify: `nextseek_api/assistant/models_db.py`
- Test: `nextseek_api/tests/repo_guards/test_migration_check.py` (create)

**Interfaces:**
- Produces: `makemigrations --check --dry-run --skip-checks` no longer names `nextseek_api`.

- [ ] **Step 1: Read the name the migration hard-codes**

```bash
cd /home/cdemurjian/code/dmac/docker/wt-gs-sync
grep -n 'task_fa\|AddIndex\|models.Index' nextseek_api/migrations/0010_turn_ledger.py
grep -n 'indexes' -A4 nextseek_api/assistant/models_db.py
```

Record the exact index name the migration creates. It is the value Django generated, and it is the value the model
must declare.

- [ ] **Step 2: Write the failing test**

Create `nextseek_api/tests/repo_guards/test_migration_check.py`:

```python
"""The model's index name must match the one its migration created.

Without an explicit name Django regenerates one on every makemigrations run and proposes a rename, which
is why the blocking CI step carries no migration check (graph-sync task T8, spec CI-2 departure).
"""
from nextseek_api.assistant import models_db


def test_the_turn_ledger_index_declares_the_name_its_migration_created():
    names = [getattr(i, "name", None) for i in models_db.TurnLedger._meta.indexes]
    assert all(names), (
        "a TurnLedger index has no explicit name, so Django proposes a rename on every "
        f"makemigrations run: {names}"
    )
```

- [ ] **Step 3: Run it and watch it fail**

Run the Django lane over `nextseek_api/tests/repo_guards/test_migration_check.py`.
Expected: FAIL, the name is `None`.

- [ ] **Step 4: Add the name to the model**

In `nextseek_api/assistant/models_db.py`, give the index the exact name from Step 1:

```python
        indexes = [models.Index(fields=["task_family", "route"], name="<the name from step 1>")]
```

Use the literal string, not a computed one. No migration is generated and no DDL runs, because the name now matches
what the database already has.

- [ ] **Step 5: Confirm the test passes and no migration appears**

Run the Django lane over the new test, then:

```bash
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python manage.py makemigrations --check --dry-run --skip-checks nextseek_api
```

Expected: exit 0 for `nextseek_api`. If it still reports a change, the name does not match; go back to Step 1.

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/assistant/models_db.py nextseek_api/tests/repo_guards/test_migration_check.py
git commit -m "fix(nextseek_api): declare the TurnLedger index name its migration created"
```

---

### Task C2: Establish what the Mezzanine and seek entries are

**Files:**
- Create: `docs/superpowers/plans/2026-09-16-ci-coverage-gaps-findings.md` (findings only, no code)

**Interfaces:**
- Produces: a decision on whether the whole-app check can be switched on, or only a per-app one.

**This task is investigation. It may conclude that the whole-app check cannot be restored yet, and that is a valid
outcome, not a failure.** Decision A2 exists because a check that is red on day one gets ignored by week two.

- [ ] **Step 1: Capture exactly what is reported**

```bash
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python manage.py makemigrations --check --dry-run --skip-checks -v 2 \
  2>&1 | tee /tmp/makemigrations-after-c1.txt
```

- [ ] **Step 2: Classify each remaining app**

For `blog`, `core`, `generic`, `pages` and `seek`, answer three questions and write the answer down with its
evidence: what change is proposed, does the database already have it, and would generating the migration be safe.

For the Mezzanine apps specifically, check whether the installed Mezzanine version ships migrations that this
project's Django version regenerates differently. That is the common cause and it is a vendor-version question, not
a defect in this repository.

- [ ] **Step 3: Write the findings**

Record, per app: the proposed operation, whether it is cosmetic or structural, and the recommendation. Where the
answer is not established, say so in those words rather than guessing.

- [ ] **Step 4: Decide the scope of the restored check**

Two outcomes are acceptable:
- **whole-app**, if every remaining entry turns out to be resolvable or genuinely absent;
- **per-app**, restricted to `nextseek_api` and `seek`, if the Mezzanine entries are a vendor artefact.

Pick one, and record why. Task C3 implements whichever was chosen.

- [ ] **Step 5: Commit the findings**

```bash
git add docs/superpowers/plans/2026-09-16-ci-coverage-gaps-findings.md
git commit -m "docs(ci): what makemigrations reports after the TurnLedger index was named"
```

---

### Task C3: Restore the migration check to the blocking step

**Files:**
- Modify: `.github/workflows/ci-pytest.yml`
- Modify: `ci/CLAUDE.md` (the Landmines note explaining why there was no check)
- Test: `ci/gate/test_blocking_lanes.py`

**Interfaces:**
- Consumes: C1's fix and C2's decision.

- [ ] **Step 1: Prove the check is clean before wiring it**

Run the exact command C2 chose (whole-app or per-app) and confirm it exits 0. **If it does not, stop.** Decision A4
forbids adding a red check.

- [ ] **Step 2: Write the failing test**

```python
def test_the_blocking_job_runs_a_migration_check():
    """Graph-sync task T8 left this out because the command was not clean; C1 and C2 made it clean."""
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci-pytest.yml").read_text(encoding="utf-8")
    assert "makemigrations --check --dry-run" in workflow, (
        "the blocking step carries no migration check, so a model changed without its migration "
        "is invisible to CI"
    )
```

- [ ] **Step 3: Run it and watch it fail**

Expected: FAIL, the workflow has no such step.

- [ ] **Step 4: Add the step**

Add a step to `.github/workflows/ci-pytest.yml` beside the other two blocking steps, carrying the same `if:` guard
and the same three environment variables they use, running the command C2 chose. Update the comment above the gate
step, which currently says `ci/gate` holds two checks.

- [ ] **Step 5: Replace the Landmines note**

`ci/CLAUDE.md` explains why no migration check runs. Replace that explanation with what is true now: the check runs
under `--skip-checks`, the `CSRF_TRUSTED_ORIGINS` system check is still unfixed and is why, and, if C2 chose
per-app, which apps are excluded and on what evidence.

- [ ] **Step 6: Run the gate suite and commit**

```bash
# Django lane over ci/gate
git add .github/workflows/ci-pytest.yml ci/CLAUDE.md ci/gate/test_blocking_lanes.py
git commit -m "test(ci): the blocking job checks for missing migrations again"
```

---

# Part B: context health

**Part B does not start until `context/` is tracked on `dev-graph`.** Check first:

```bash
git ls-tree --name-only origin/dev-graph context/
```

An empty result means the context branch has not merged and Part B has nothing to validate. Stop and report that.

### Task C4: Track the context validator into the repository

**Files:**
- Create: `scripts/validate_context.py` (moved from the untracked working directory)
- Create: `ci/gate/test_validate_context.py`
- Modify: `scripts/README.md`

**Interfaces:**
- Produces: `python3 scripts/validate_context.py` runs against the tracked `context/` files and exits 0 or 1.

- [ ] **Step 1: Read the existing validator in full before moving it**

It is 237 lines and reads `context/*.json` plus a dated production pull. Note every path it assumes.

- [ ] **Step 2: Make its inputs repository-relative**

The version in the working directory defaults to a sibling worktree path and reads a dated production pull from
outside the repository. Neither can be true of a tracked script. Rework it so `context/` resolves from the
repository root, and so the production cross-check is **optional**: present it as a flag that is skipped when the
pull is absent, because that data is real and does not enter the repository.

- [ ] **Step 3: Write the failing gate test**

```python
def test_the_tracked_context_files_validate():
    """The assistant loads these four files; nothing checked them before this gate existed."""
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "validate_context.py")],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
```

- [ ] **Step 4: Run it**

Run the Django lane over `ci/gate/test_validate_context.py`. If it fails, **the context files have a real problem**
and that is the gate doing its job. Report what it found rather than weakening the assertion.

- [ ] **Step 5: Document it**

Add a `scripts/README.md` paragraph naming what the validator checks and the optional production cross-check.

- [ ] **Step 6: Commit**

```bash
git add scripts/validate_context.py ci/gate/test_validate_context.py scripts/README.md
git commit -m "test(ci): validate the tracked context files in the blocking gate"
```

---

### Task C5: `drift.catalog.sample_types` and `drift.catalog.hash`

**Files:**
- Modify: `nextseek_api/graph_sync/drift.py`
- Test: `nextseek_api/tests/test_graph_sync_drift.py`

**Interfaces:**
- Produces: two checks in the drift result, in the same shape the existing checks use: a name, an expected value, an
  actual value and a detail.

- [ ] **Step 1: Read how an existing check reports**

`_check_detection` and `_check_freshness` in `drift.py` and `_check` in `verify.py` define the shape. Match it
exactly; a new shape breaks the CI record's renderer.

- [ ] **Step 2: Write the failing tests**

```python
class TestTheCatalogIsCompared:
    def test_a_sample_type_missing_from_the_graph_is_reported(self, fake_driver):
        """drift.catalog.sample_types: MySQL has a type the graph's catalog does not."""
        result = drift.drift_check(fake_driver, "neo4j", ...)
        names = [c["name"] for c in result["checks"]]
        assert "drift.catalog.sample_types" in names

    def test_a_changed_catalog_hash_is_reported(self, fake_driver):
        """drift.catalog.hash: the catalog built from MySQL now hashes differently from GraphMeta's."""
        result = drift.drift_check(fake_driver, "neo4j", ...)
        names = [c["name"] for c in result["checks"]]
        assert "drift.catalog.hash" in names
```

- [ ] **Step 3: Run them and watch them fail**

Expected: FAIL, neither name is present.

- [ ] **Step 4: Implement both checks**

`drift.py` already builds the catalog from MySQL through `run.build_catalog()` for the sample digests, so both
checks reuse that object and add no second read:
- `drift.catalog.sample_types`: the count of types in the built catalog against the count of `SampleType` nodes,
  with the differing titles as the detail, capped like the other checks.
- `drift.catalog.hash`: the built catalog's hash against `GraphMeta.catalog_hash`, which the status endpoint already
  exposes.

- [ ] **Step 5: Run the drift suite**

Run the Django lane over `nextseek_api/tests/test_graph_sync_drift.py`.
Expected: PASS, 33 or more.

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/graph_sync/drift.py nextseek_api/tests/test_graph_sync_drift.py
git commit -m "feat(graph_sync): compare the catalog against MySQL in the drift check"
```

---

### Task C6: `drift.catalog.types_with_attribute_set_diff` and the context coverage count

**Files:**
- Modify: `nextseek_api/graph_sync/drift.py`
- Test: `nextseek_api/tests/test_graph_sync_drift.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_a_type_whose_attribute_set_differs_is_reported(self, fake_driver):
    """drift.catalog.types_with_attribute_set_diff: the graph's declared attributes for a type are not
    the set MySQL declares."""
    result = drift.drift_check(fake_driver, "neo4j", ...)
    assert "drift.catalog.types_with_attribute_set_diff" in [c["name"] for c in result["checks"]]


def test_types_without_a_context_row_are_counted_but_not_failed(self, fake_driver):
    """A type with no context row is legal (catalog.py says so). The number is reported so a type that
    silently lost its curated card is visible; it is not a threshold."""
    result = drift.drift_check(fake_driver, "neo4j", ...)
    assert "types_without_context" in result["stats"]
    assert "types_without_context" not in [c["name"] for c in result["checks"]]
```

- [ ] **Step 2: Run them and watch them fail**

Expected: FAIL, neither the check nor the stat exists.

- [ ] **Step 3: Implement the check and the stat**

The check compares, per type, the attribute titles the built catalog declares against the `Attribute` nodes the
graph links by `HAS_ATTRIBUTE`, reporting the count of types that differ with a capped detail. The stat counts types
whose catalog entry has `has_context` false. **The stat is a stat, not a check** (decision B4): it appears in
`stats` and never in `checks`, so it can never fail a build.

- [ ] **Step 4: Run the drift suite and the blocking lane**

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/graph_sync/drift.py nextseek_api/tests/test_graph_sync_drift.py
git commit -m "feat(graph_sync): report attribute-set drift per type and context coverage"
```

---

### Task C7: Surface the catalog drift in the CI record and document it

**Files:**
- Modify: `startup/ci/runner.py` (the `## Graph drift` section)
- Modify: `nextseek_api/graph_sync/README.md`
- Modify: `startup/CLAUDE.md`, `startup/README.md`
- Test: `startup/tests/test_validate_graph_drift.py`

- [ ] **Step 1: Write the failing test**

```python
def test_the_record_names_a_failing_catalog_check():
    """A catalog check that fails must appear in the record; a drift section that only ever says
    'no drift' teaches the reader to skip it."""
    row = runner._graph_drift_row(result_with_failing_catalog_check())
    assert "catalog" in row[2]
```

- [ ] **Step 2: Run it and watch it fail**

- [ ] **Step 3: Render the failing check names in the record**

The `## Graph drift` section currently carries a name, a pass flag and a detail. Extend the detail so a failing
check is named rather than summarised as a bare failure.

- [ ] **Step 4: Document the new checks**

`nextseek_api/graph_sync/README.md` describes what the drift check covers; add the catalog checks and the context
coverage stat. `startup/CLAUDE.md` and `startup/README.md` do not mention `check_graph_drift` or the record's
`## Graph drift` section at all, which was already an open item from graph-sync Run 4; fix that here.

- [ ] **Step 5: Run the startup lane**

```bash
cd /home/cdemurjian/code/dmac/docker/wt-gs-sync/startup
uv run --project . --group test python -m pytest tests/ -q \
  -p no:nextseek_api.attributes.tests.attribute_fixtures \
  --ignore=tests/test_schema_fixups.py --ignore=tests/test_schema_fixups_coverage.py
```

Expected: PASS, 595 or more. Both `--ignore` flags are required; the second is not in `startup/CLAUDE.md`'s recipe
but the module it excludes errors on a module-scope driver import.

- [ ] **Step 6: Commit**

```bash
git add startup/ci/runner.py nextseek_api/graph_sync/README.md startup/CLAUDE.md startup/README.md \
  startup/tests/test_validate_graph_drift.py
git commit -m "feat(startup): name a failing catalog check in the CI record's graph drift section"
```

---

## Self-review notes

- **Spec coverage:** A1 maps to C1, A2 to C2, A3 and A4 to C3. B2 maps to C4, B3 to C5 and C6, B4 to C6's stat, and
  the reporting half of B3 to C7. B5's exclusion is honoured: no task builds `drift.isa.*`.
- **C2 can conclude that Part A cannot finish.** That is a real outcome. The plan says so rather than assuming the
  Mezzanine entries will turn out to be benign.
- **Part B's gate is real.** If `context/` is not on `dev-graph`, C4 has nothing to validate and C5 to C7 are
  measuring a catalog built from context that is about to be replaced.
- **Known soft spot:** C5 and C6's test bodies use the drift suite's existing fake-driver fixtures, which are named
  in `nextseek_api/tests/test_graph_sync_drift.py` and not repeated here; the implementer reads that module first.

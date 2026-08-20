# Download README Column Definitions — Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every column in a downloaded sample workbook is indexed on the README sheet under the tab it belongs to, showing a plain-English meaning where a reviewed one exists.

**Architecture:** All rendering stays in the one shared writer, `nextseek_api/services/sample_workbook.py`, which both download paths already call. Definitions come from a new `dmac.sample_fields_context` table read through a Django model, keyed on the `field_name` string with an optional per-sample-type override row. The flat README table becomes one section per tab, built by a pure function and rendered with openpyxl.

**Tech Stack:** Python 3.14, Django (models only, no migration), pandas 3.0.2, openpyxl 3.1.5, pytest, MySQL 8.0, Docker Compose.

## Scope

This plan covers **Phase 1 only** — the mechanism. It is shippable on its own: with an empty definitions table the README is already more complete than today, because every column gets indexed under its tab.

**Phase 2 is deliberately excluded and needs its own plan:** `scripts/draft_field_definitions.py`, the xlsx review round trip, `load_field_definitions`, `field_definitions_report`, and the `config.py` export registration. It is excluded because `nextseek_api/management/` does not exist yet on this branch, and because the drafting step's model choice, prompt shape, and cost need a design pass this spec does not make.

## Global Constraints

- Work happens in the worktree `/Users/jps/Documents/MIT/NExtSEEK-readme-columns` on branch `feat/download-readme-columns`, based on `origin/main` @ `ffc3cb60`.
- **Do not run `uv` on the host.** The repo pins `mysqlclient`, which will not build on macOS. Tests run inside the stack image — Task 1 builds the wrapper for this. If `uv.lock` shows as modified, `git checkout -- uv.lock`.
- **No Django migration.** `sample_types_context` has none — no file under `seek/migrations/` references it. Tables in this family are created out-of-band in SQL and the model simply maps them. Follow that.
- **Join on the `field_name` string, never an id.** `sampletype_id` does not agree with `sample_types.id` across instances.
- **`sample_type = ''` means the global definition.** The column is `NOT NULL DEFAULT ''`, never nullable: MySQL treats NULLs as distinct inside a unique index, so a nullable scope column would silently accept two conflicting global definitions for one field.
- **Fail soft.** A missing, unreachable, or malformed definitions table logs and yields blank meanings. It must never cost the user their download.
- Every commit message uses conventional-commit style with a module scope, e.g. `feat(download): …`.
- The design this implements is [`2026-08-19-download-readme-column-definitions-design.md`](2026-08-19-download-readme-column-definitions-design.md).

---

### Task 1: Test runner wrapper

Nothing else in this plan can be verified until tests can run against worktree code. `uv run pytest` fails on the host (mysqlclient), and the running `nextseek` container serves code baked into its image, not this checkout. This wrapper mounts the checkout over `/app` and reuses the image's Linux virtualenv.

**Files:**
- Create: `scripts/run_tests.sh`

**Interfaces:**
- Consumes: nothing.
- Produces: `./scripts/run_tests.sh [pytest targets…]`, defaulting to `nextseek_api/tests`. Every later task uses it as its test command.

- [ ] **Step 1: Copy the gitignored settings overlay into the worktree**

`dmac/local_settings.py` is gitignored, so a fresh worktree lacks it and Django will not import.

```bash
cp /Users/jps/Documents/MIT/NExtSEEK/dmac/local_settings.py \
   /Users/jps/Documents/MIT/NExtSEEK-readme-columns/dmac/local_settings.py
```

- [ ] **Step 2: Write the wrapper**

Create `scripts/run_tests.sh`:

```bash
#!/usr/bin/env bash
# Run THIS checkout's Python tests inside the stack image.
#
# Why not `uv run pytest`? The repo pins mysqlclient, which does not build on a
# bare macOS host. The stack image already has every dependency, so we mount
# this checkout over /app and reuse the image's virtualenv:
#
#   -v "$HERE":/app   this checkout's code, instead of the code baked into the image
#   -v /app/.venv     anonymous volume, keeps the image's Linux venv visible
#                     through the bind mount above
#
# Arguments are passed straight through to pytest as discrete argv entries
# (via "$@", never re-joined into a string), so multi-word args like
# -k "two words" survive intact.
#
# Compose runs from NEXTSEEK_COMPOSE_DIR because docker/*.env are gitignored and
# so are absent from a fresh worktree.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_DIR="${NEXTSEEK_COMPOSE_DIR:-$HOME/Documents/MIT/NExtSEEK}"

if [ $# -eq 0 ]; then set -- nextseek_api/tests; fi

if [ ! -f "$HERE/dmac/local_settings.py" ]; then
  echo "missing $HERE/dmac/local_settings.py (gitignored)" >&2
  echo "copy it: cp $COMPOSE_DIR/dmac/local_settings.py $HERE/dmac/" >&2
  exit 1
fi

cd "$COMPOSE_DIR"
exec docker compose run --rm --no-deps \
  -v "$HERE":/app -v /app/.venv \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings \
  nextseek /app/.venv/bin/python -m pytest "$@" -q
```

- [ ] **Step 3: Make it executable and run the existing suite**

```bash
chmod +x scripts/run_tests.sh
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py
```

Expected: `21 passed` (plus 3 pydantic deprecation warnings, which are pre-existing and unrelated).

- [ ] **Step 4: Commit**

```bash
git add scripts/run_tests.sh
git commit -m "chore(test): run the suite against a worktree inside the stack image"
```

---

### Task 2: The table and its model

**Files:**
- Create: `startup/seed/sql/sample_fields_context.sql`
- Modify: `seek/models.py` (add after the `Sample_types_context` class, which ends with `db_table = "sample_types_context"`)
- Test: `nextseek_api/tests/test_sample_fields_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `seek.models.Sample_fields_context`, with fields `field_name` (str), `sample_type` (str), `meaning` (str), mapped to table `sample_fields_context` on the `default` (dmac) database. Task 3 imports it.

- [ ] **Step 1: Write the failing test**

Create `nextseek_api/tests/test_sample_fields_model.py`:

```python
"""The definitions model maps a table this repo creates in SQL, not in a migration."""

from django.conf import settings

from seek.models import Sample_fields_context


def test_model_maps_the_definitions_table():
    assert Sample_fields_context._meta.db_table == "sample_fields_context"


def test_model_reads_the_nextseek_database():
    """The router at seek/dbrouters.py routes on this attribute."""
    assert Sample_fields_context._DATABASE == settings.NEXTSEEK_DATABASE


def test_model_carries_the_three_content_fields():
    names = {f.name for f in Sample_fields_context._meta.get_fields()}
    assert {"field_name", "sample_type", "meaning"} <= names


def test_scope_column_defaults_to_empty_string_not_null():
    """'' is the global scope. A nullable column would let MySQL accept two
    conflicting global rows for one field, because NULLs compare distinct
    inside a unique index."""
    field = Sample_fields_context._meta.get_field("sample_type")
    assert field.null is False
    assert field.default == ""
```

- [ ] **Step 2: Run it to verify it fails**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_fields_model.py
```

Expected: FAIL, `ImportError: cannot import name 'Sample_fields_context' from 'seek.models'`.

- [ ] **Step 3: Add the model**

In `seek/models.py`, immediately after the `Sample_types_context` class (the one whose `Meta` sets `db_table = "sample_types_context"`), add:

```python
class Sample_fields_context(models.Model):
    """Plain-English meaning per metadata field, for the download README.

    Joined on the `field_name` string, never on an id: ids do not agree across
    instances. `sample_type` is the scope — '' is the definition used for every
    tab, a sample type code overrides it for that tab only.
    """
    _DATABASE = NEXTSEEK_DATABASE

    field_name = models.CharField(max_length=255)
    sample_type = models.CharField(max_length=32, default="")
    meaning = models.TextField(default=None, null=True)

    def __unicode__(self):
        return self.field_name

    class Meta:
        db_table = "sample_fields_context"
        unique_together = ("field_name", "sample_type")
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_fields_model.py
```

Expected: `4 passed`.

- [ ] **Step 5: Write the DDL**

Create `startup/seed/sql/sample_fields_context.sql`:

```sql
-- Per-field definitions shown on the download workbook's README sheet.
--
-- Created out-of-band, like sample_types_context: no Django migration
-- references either table. Apply to the `dmac` database, then regenerate the
-- seed with `./startup.sh dump-db` so fresh installs carry it.
--
-- sample_type is the scope. '' is the definition used on every tab; a sample
-- type code overrides it for that tab only. It is NOT NULL DEFAULT '' rather
-- than nullable on purpose: MySQL treats NULLs as distinct inside a unique
-- index, so a nullable column would accept two conflicting global rows for the
-- same field and uk_field_scope would never fire.
CREATE TABLE IF NOT EXISTS `sample_fields_context` (
  `id`          int NOT NULL AUTO_INCREMENT,
  `field_name`  varchar(255) NOT NULL,
  `sample_type` varchar(32)  NOT NULL DEFAULT '',
  `meaning`     text,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_field_scope` (`field_name`, `sample_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

- [ ] **Step 6: Apply it to the local database and confirm the shape**

```bash
set -a && . /Users/jps/Documents/MIT/NExtSEEK/docker/db.env && set +a
docker exec -i seek-mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" dmac \
  < startup/seed/sql/sample_fields_context.sql
docker exec seek-mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e \
  "DESCRIBE dmac.sample_fields_context;"
```

Expected: four rows — `id`, `field_name`, `sample_type`, `meaning` — with `sample_type` showing `NO` under Null and an empty-string Default.

- [ ] **Step 7: Commit**

```bash
git add startup/seed/sql/sample_fields_context.sql seek/models.py nextseek_api/tests/test_sample_fields_model.py
git commit -m "feat(download): sample_fields_context table and model"
```

---

### Task 3: The definitions loader

**Files:**
- Modify: `nextseek_api/services/sample_workbook.py`
- Test: `nextseek_api/tests/test_sample_workbook.py`

**Interfaces:**
- Consumes: `seek.models.Sample_fields_context` from Task 2.
- Produces: `load_sample_field_context(pairs)` — takes an iterable of `(sample_type, field_name)` tuples and returns `dict[tuple[str, str], str]` keyed by those same pairs, with precedence already resolved so callers never reimplement the fallback. Task 5 calls it.

- [ ] **Step 1: Write the failing tests**

Append to `nextseek_api/tests/test_sample_workbook.py`:

```python
@patch(f"{_MOD}.Sample_fields_context")
def test_field_context_uses_the_global_row(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"field_name": "Sex", "sample_type": "", "meaning": "Sex at birth."},
    ]
    assert load_sample_field_context([("MUS", "Sex")]) == {("MUS", "Sex"): "Sex at birth."}


@patch(f"{_MOD}.Sample_fields_context")
def test_field_context_prefers_a_sample_type_override(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"field_name": "Name", "sample_type": "", "meaning": "Submitter's identifier."},
        {"field_name": "Name", "sample_type": "MUS", "meaning": "The animal's ear-tag ID."},
    ]
    assert load_sample_field_context([("MUS", "Name")]) == {
        ("MUS", "Name"): "The animal's ear-tag ID."
    }


@patch(f"{_MOD}.Sample_fields_context")
def test_field_context_override_does_not_leak_to_other_sample_types(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"field_name": "Name", "sample_type": "", "meaning": "Submitter's identifier."},
        {"field_name": "Name", "sample_type": "MUS", "meaning": "The animal's ear-tag ID."},
    ]
    result = load_sample_field_context([("MUS", "Name"), ("TIS", "Name")])
    assert result[("TIS", "Name")] == "Submitter's identifier."


@patch(f"{_MOD}.Sample_fields_context")
def test_field_context_returns_blank_for_an_undefined_field(mock_model):
    mock_model.objects.filter.return_value.values.return_value = []
    assert load_sample_field_context([("MUS", "Genotype")]) == {("MUS", "Genotype"): ""}


@patch(f"{_MOD}.Sample_fields_context")
def test_field_context_coerces_a_null_meaning_to_a_blank(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"field_name": "Sex", "sample_type": "", "meaning": None},
    ]
    assert load_sample_field_context([("MUS", "Sex")]) == {("MUS", "Sex"): ""}


@patch(f"{_MOD}.Sample_fields_context")
def test_field_context_does_not_query_for_an_empty_pair_list(mock_model):
    assert load_sample_field_context([]) == {}
    mock_model.objects.filter.assert_not_called()


@patch(f"{_MOD}.Sample_fields_context")
def test_field_context_survives_a_missing_table(mock_model):
    """A download must not fail because the definitions table is absent."""
    mock_model.objects.filter.side_effect = RuntimeError("no such table")
    assert load_sample_field_context([("MUS", "Sex")]) == {}
```

Add `load_sample_field_context` and `Sample_fields_context` to the import block at the top of the file, which currently reads:

```python
from nextseek_api.services.sample_workbook import (
    CONTEXTDB_URL,
    build_readme_rows,
    load_sample_field_context,
    load_sample_type_context,
    write_samples_workbook,
)
```

- [ ] **Step 2: Run to verify it fails**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py
```

Expected: FAIL, `ImportError: cannot import name 'load_sample_field_context'`.

- [ ] **Step 3: Implement the loader**

In `nextseek_api/services/sample_workbook.py`, change the model import from:

```python
from seek.models import Sample_types_context
```

to:

```python
from seek.models import Sample_fields_context, Sample_types_context
```

Then add, directly below `load_sample_type_context`:

```python
def load_sample_field_context(
    pairs: Iterable[tuple[str, str]],
) -> dict[tuple[str, str], str]:
    """Resolve (sample_type, field_name) -> meaning against sample_fields_context.

    Precedence per pair: a row scoped to that sample type, else the global row
    (`sample_type == ''`), else blank. Resolving here means no caller has to
    reimplement the fallback.
    """
    wanted = [(st or "", fn) for st, fn in pairs if fn]
    if not wanted:
        return {}
    try:
        rows = list(
            Sample_fields_context.objects.filter(
                field_name__in=sorted({fn for _, fn in wanted})
            ).values("field_name", "sample_type", "meaning")
        )
    except Exception:
        # A missing or unreachable table must not cost the user their download;
        # every meaning then renders blank.
        logger.exception("sample_fields_context lookup failed; meanings will be blank")
        return {}

    global_by_field: dict[str, str] = {}
    scoped: dict[tuple[str, str], str] = {}
    for row in rows:
        meaning = row.get("meaning") or ""
        code = row.get("sample_type") or ""
        name = row.get("field_name")
        if code:
            scoped[(code, name)] = meaning
        else:
            global_by_field[name] = meaning

    return {
        (code, name): scoped.get((code, name), global_by_field.get(name, ""))
        for code, name in wanted
    }
```

- [ ] **Step 4: Run to verify it passes**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py
```

Expected: `28 passed` (21 existing + 7 new).

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/services/sample_workbook.py nextseek_api/tests/test_sample_workbook.py
git commit -m "feat(download): resolve per-field definitions with sample-type override"
```

---

### Task 4: Build the README sections

A pure function, so section structure is testable without writing a file. `build_readme_rows` stays in place for now — Task 5 removes it once the writer switches over, keeping every task green.

**Files:**
- Modify: `nextseek_api/services/sample_workbook.py`
- Test: `nextseek_api/tests/test_sample_workbook.py`

**Interfaces:**
- Consumes: `load_sample_field_context` from Task 3.
- Produces: `build_readme_blocks(sheets, context_by_code, meaning_by_pair)` returning `list[dict]`, each dict `{"code": str, "name": str, "description": str, "columns": list[tuple[str, str]]}`, in the order `sheets` was given. Task 5 renders it.

- [ ] **Step 1: Write the failing tests**

Append to `nextseek_api/tests/test_sample_workbook.py`:

```python
def test_blocks_carry_the_sample_type_name_and_description():
    blocks = build_readme_blocks([("MUS", ["UID"])], CONTEXT, {})
    assert blocks[0]["code"] == "MUS"
    assert blocks[0]["name"] == "Mouse"
    assert blocks[0]["description"] == "A mouse sample."


def test_blocks_keep_sheet_order_not_alphabetical_order():
    """The README is meant to be read beside the tab, left to right."""
    blocks = build_readme_blocks([("MUS", ["UID", "Sex", "Genotype"])], CONTEXT, {})
    assert [name for name, _ in blocks[0]["columns"]] == ["UID", "Sex", "Genotype"]


def test_blocks_follow_the_order_the_sheets_were_given():
    blocks = build_readme_blocks([("TIS", ["UID"]), ("MUS", ["UID"])], CONTEXT, {})
    assert [b["code"] for b in blocks] == ["TIS", "MUS"]


def test_blocks_attach_the_resolved_meaning():
    meanings = {("MUS", "Sex"): "Sex at birth."}
    blocks = build_readme_blocks([("MUS", ["Sex"])], CONTEXT, meanings)
    assert blocks[0]["columns"] == [("Sex", "Sex at birth.")]


def test_a_column_with_no_definition_is_listed_with_a_blank():
    """The README always indexes every column, so a gap is visible not silent."""
    blocks = build_readme_blocks([("MUS", ["Genotype"])], CONTEXT, {})
    assert blocks[0]["columns"] == [("Genotype", "")]


def test_an_undocumented_sample_type_still_gets_a_block():
    blocks = build_readme_blocks([("ZZZ", ["UID"])], CONTEXT, {})
    assert blocks[0] == {"code": "ZZZ", "name": "", "description": "", "columns": [("UID", "")]}
```

Add `build_readme_blocks` to the import block at the top of the test file.

- [ ] **Step 2: Run to verify it fails**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py
```

Expected: FAIL, `ImportError: cannot import name 'build_readme_blocks'`.

- [ ] **Step 3: Implement the builder**

In `nextseek_api/services/sample_workbook.py`, add below `build_readme_rows`:

```python
COLUMN_TABLE_HEADER = ["Column", "Meaning"]


def build_readme_blocks(
    sheets: Iterable[tuple[str, Iterable[str]]],
    context_by_code: Mapping[str, Mapping[str, str]],
    meaning_by_pair: Mapping[tuple[str, str], str],
) -> list[dict]:
    """One block per sheet, in the order the sheets will be written.

    `sheets` is (sample_type code, its columns in sheet order). Columns are kept
    in that order rather than sorted so the README can be read beside the tab.
    An undocumented sample type still gets a block, and a column with no
    definition is still listed, so the README always indexes the whole workbook.
    """
    blocks = []
    for code, columns in sheets:
        entry = context_by_code.get(code) or {}
        blocks.append({
            "code": code,
            "name": entry.get("name", "") or "",
            "description": entry.get("description", "") or "",
            "columns": [
                (column, meaning_by_pair.get((code, column), "") or "")
                for column in columns
            ],
        })
    return blocks
```

- [ ] **Step 4: Run to verify it passes**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py
```

Expected: `34 passed` (28 + 6 new).

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/services/sample_workbook.py nextseek_api/tests/test_sample_workbook.py
git commit -m "feat(download): build README sections per sample type"
```

---

### Task 5: Render the sections and switch the writer over

This task changes what the README looks like, so it also rewrites the six `build_readme_rows` tests and the two layout tests that pin the old flat table, and deletes `build_readme_rows` itself.

**Files:**
- Modify: `nextseek_api/services/sample_workbook.py`
- Test: `nextseek_api/tests/test_sample_workbook.py`

**Interfaces:**
- Consumes: `build_readme_blocks` (Task 4), `load_sample_field_context` (Task 3).
- Produces: the final README layout. No new public names.

- [ ] **Step 1: Delete the superseded tests and rewrite the one that survives**

From `nextseek_api/tests/test_sample_workbook.py` delete exactly these seven tests, which assert the flat table that no longer exists. These are every test that references `build_readme_rows`, plus the one that pins the old row-3 header:

1. `test_readme_rows_start_with_the_header`
2. `test_readme_rows_carry_name_and_description`
3. `test_readme_rows_are_sorted_by_code`
4. `test_undocumented_code_is_listed_with_blanks`
5. `test_readme_rows_deduplicate_codes`
6. `test_readme_rows_drop_blank_codes`
7. `test_readme_table_starts_at_row_3`

Then remove `build_readme_rows` from the test file's import block.

One more test asserts the old layout but must be **rewritten, not deleted** — it is the only coverage of the undocumented-sample-type path through a real workbook. Replace `test_workbook_still_written_when_context_table_is_empty` with:

```python
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value={})
def test_workbook_still_written_when_context_table_is_empty(_ctx, _fields, tmp_path):
    """With no sample-type context the heading is the bare code and the
    description row is empty, but the section is still there."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["A3"].value == "MUS"
    assert ws["A4"].value in (None, "")
```

- [ ] **Step 2: Write the failing layout tests**

Append to `nextseek_api/tests/test_sample_workbook.py`:

```python
@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_section_heading_is_bold_at_a3(_ctx, _fields, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["A3"].value == "MUS — Mouse"
    assert ws["A3"].font.bold is True


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_description_sits_under_the_heading(_ctx, _fields, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["A4"].value == "A mouse sample."


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_column_table_is_indented_into_b_and_c(_ctx, _fields, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert [ws["B6"].value, ws["C6"].value] == ["Column", "Meaning"]
    assert ws["B6"].font.bold is True
    assert ws["B7"].value == "Name"


@patch(f"{_MOD}.load_sample_field_context",
       return_value={("MUS", "Name"): "The animal's ear-tag ID."})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_shows_the_resolved_meaning(_ctx, _fields, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["C7"].value == "The animal's ear-tag ID."


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_the_second_section_starts_after_a_blank_row(_ctx, _fields, tmp_path):
    """MUS has two columns (Name, Sex) at rows 7-8, so TIS heads row 10."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["A9"].value is None
    assert ws["A10"].value == "TIS — Tissue"


@patch(f"{_MOD}.load_sample_field_context", return_value={})
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_only_lists_columns_that_survive_the_empty_drop(_ctx, _fields, tmp_path):
    """TIS's Sex is empty in the fixture, so the TIS sheet drops it and the
    README must not claim a column the researcher cannot see.

    TIS heads row 10, description 11, blank 12, table header 13, so its single
    surviving column sits at B14."""
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["B14"].value == "Name"
    assert ws["B15"].value is None


@patch(f"{_MOD}.Sample_fields_context")
@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_workbook_is_complete_when_the_definitions_table_is_missing(_ctx, mock_model, tmp_path):
    """Losing sample_fields_context costs meanings, never the download.

    This is the only Task 5 test that exercises the real loader rather than
    mocking it out, so it is what actually proves the fail-soft path end to end.
    """
    mock_model.objects.filter.side_effect = RuntimeError("no such table")
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    wb = load_workbook(out)
    assert wb.sheetnames == ["README", "MUS", "TIS"]
    ws = wb["README"]
    assert ws["B7"].value == "Name"          # still indexed
    assert ws["C7"].value in (None, "")      # meaning blank


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_field_lookup_is_asked_only_for_columns_actually_written(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    with patch(f"{_MOD}.load_sample_field_context", return_value={}) as lookup:
        write_samples_workbook(_df(), str(out))
    asked = set(lookup.call_args[0][0])
    assert ("TIS", "Sex") not in asked
    assert ("MUS", "Sex") in asked
```

- [ ] **Step 3: Run to verify it fails**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py
```

Expected: FAIL — `ws["A3"].value` is `"Sample Type"`, not `"MUS — Mouse"`.

- [ ] **Step 4: Render sections and restructure the writer**

In `nextseek_api/services/sample_workbook.py`, add the openpyxl font import below the pandas import:

```python
from openpyxl.styles import Font
```

Delete `build_readme_rows` entirely — `build_readme_blocks` replaces it.

Replace `_write_readme` with:

```python
def _write_readme(book, blocks: list[dict]) -> None:
    ws = book.create_sheet(README_SHEET, 0)
    ws["A1"] = README_LINK_TEXT
    ws["A1"].hyperlink = CONTEXTDB_URL
    ws["A1"].style = "Hyperlink"
    # Row 2 is left blank to separate the link from the first section.
    row = 3
    for block in blocks:
        heading = f"{block['code']} — {block['name']}" if block["name"] else block["code"]
        ws.cell(row=row, column=1, value=heading).font = Font(bold=True)
        row += 1
        ws.cell(row=row, column=1, value=block["description"])
        row += 2  # description, then a blank line before the column table
        for column, label in enumerate(COLUMN_TABLE_HEADER, start=2):
            ws.cell(row=row, column=column, value=label).font = Font(bold=True)
        row += 1
        for name, meaning in block["columns"]:
            ws.cell(row=row, column=2, value=name)
            ws.cell(row=row, column=3, value=meaning)
            row += 1
        row += 1  # blank line between sections
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["B"].width = 34
    ws.column_dimensions["C"].width = 100
```

Replace `write_samples_workbook` with:

```python
def write_samples_workbook(parsed_df, output_path, context_by_code=None) -> None:
    """Write README as sheet 1, then one sheet per sample type.

    `parsed_df` must carry a `uuid` column; `sample_type` is derived here so the
    extraction regex lives in exactly one place. Sheets are prepared before the
    README is built, because the README must describe the columns that survive
    the all-empty drop rather than everything in the frame.
    """
    df = parsed_df.copy()
    df["sample_type"] = df["uuid"].astype(str).str.extract(SAMPLE_TYPE_RE, expand=False)

    codes = df["sample_type"].dropna().unique().tolist()
    if context_by_code is None:
        context_by_code = load_sample_type_context(codes)

    prepared = []
    for sample_type, sample_type_df in df.groupby("sample_type"):
        frame = sample_type_df.drop(columns=["uuid", "sample_type"])
        frame = frame.replace("", pd.NA)
        frame = frame.dropna(axis=1, how="all")
        prepared.append((sample_type, frame))

    sheets = [(code, list(frame.columns)) for code, frame in prepared]
    meaning_by_pair = load_sample_field_context(
        [(code, column) for code, columns in sheets for column in columns]
    )
    blocks = build_readme_blocks(sheets, context_by_code, meaning_by_pair)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        book = writer.book
        # pandas removes openpyxl's default sheet, but guard in case that changes.
        if "Sheet" in book.sheetnames:
            del book["Sheet"]
        _write_readme(book, blocks)

        for code, frame in prepared:
            frame.to_excel(writer, sheet_name=code, index=False)
```

- [ ] **Step 5: Run the full file to verify everything passes**

```bash
./scripts/run_tests.sh nextseek_api/tests/test_sample_workbook.py
```

Expected: `35 passed` (34 after Task 4, minus the 7 deleted, plus the 8 new).

- [ ] **Step 6: Run the wider suite for regressions**

Both download call sites (`seek/views.py:1236`, `seek/dbtable_sample.py:944`) delegate to this writer, so check the `seek` tests too.

```bash
./scripts/run_tests.sh nextseek_api seek
```

Expected: no new failures. Record any pre-existing failures rather than fixing them here.

- [ ] **Step 7: Commit**

```bash
git add nextseek_api/services/sample_workbook.py nextseek_api/tests/test_sample_workbook.py
git commit -m "feat(download): README lists every column under the tab it belongs to"
```

---

### Task 6: Document the rollout and the seed gap

The feature renders blank meanings anywhere the table is missing. That is by design. Folding the table into the seed dump turned out to be impossible from a normal checkout (see Step 2), so this task documents the manual install path instead, and records the gap the way `assay_context` and `projects_context` already are.

**Files:**
- Modify: `startup/seed/README.md`
- Modify: `docs/sample-download-workflow.md`

**Interfaces:**
- Consumes: `startup/seed/sql/sample_fields_context.sql` (Task 2).
- Produces: documentation only. `startup/seed/dmac.sql.gz` is deliberately NOT modified.

- [ ] **Step 1: Confirm the table is in the local dmac database**

```bash
set -a && . /Users/jps/Documents/MIT/NExtSEEK/docker/db.env && set +a
docker exec seek-mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e \
  "SELECT COUNT(*) FROM dmac.sample_fields_context;"
```

Expected: `0` — the table exists and is empty. If it errors, re-run Task 2 Step 6.

- [ ] **Step 2: Confirm the seed cannot be regenerated here, and record why**

`./startup.sh dump-db` does **not** dump the local stack. It requires
`startup/seed/regenerate/dump-source.env` — gitignored, maintainer-only
credentials for a remote host — and regenerates `dmac.sql.gz`,
`seek_production.sql.gz` and the Neo4j dump together from that source. Running
it would replace all three seeds with whatever that remote host currently holds,
which is far beyond adding one table.

Confirm the blocker rather than assuming it:

```bash
ls startup/seed/regenerate/dump-source.env
```

Expected: `No such file or directory`. If the file *does* exist, stop and report
it — the situation differs from what this plan assumes.

The table therefore ships as the standalone DDL from Task 2, applied by hand,
and a maintainer folds it into the seed on the next `dump-db` cycle. Until then
a fresh install renders blank meanings, which is the designed fail-soft
behaviour rather than a failure. `assay_context` and `projects_context` are
absent from the seed for exactly the same reason.

- [ ] **Step 3: Document the table in the seed README**

In `startup/seed/README.md`, under the `## Files` list, add after the `dmac.sql.gz` bullet:

```markdown
  The `dmac` dump does **not** yet include `sample_fields_context`, the
  per-field definitions behind the download workbook's README sheet. Its DDL
  lives at `startup/seed/sql/sample_fields_context.sql` and is applied by hand
  to an instance's `dmac` database — production included — until a maintainer
  folds the table in on the next `dump-db` cycle. Until then a fresh install
  renders the README's meanings blank, which is the designed fail-soft
  behaviour, not a failure; `assay_context` and `projects_context` are absent
  for the same reason. Neither this table nor `sample_types_context` has a
  Django migration; both are created in SQL.
```

- [ ] **Step 4: Document the rollout in the workflow doc**

Append to `docs/sample-download-workflow.md`:

```markdown
## Per-column definitions on the README sheet

The README sheet carries one section per tab, listing every column in that tab
with a plain-English meaning where one has been written. Meanings come from
`dmac.sample_fields_context`, joined on the `field_name` string, with
`sample_type = ''` as the global definition and a sample type code as a per-tab
override.

Rows are derived from the columns actually written to the workbook, after the
all-empty-column drop. A new sample type therefore gets a section the first time
anyone downloads one, and a new attribute gets a row the first time it carries a
value — nothing has to be registered.

Rolling this out to an instance:

1. Apply `startup/seed/sql/sample_fields_context.sql` to that instance's `dmac`
   database.
2. Load definitions into the table.
3. Deploy.

Steps can happen in any order. `load_sample_field_context` fails soft: if the
table is missing or unreachable it logs and every meaning renders blank, so
there is no window in which downloads break.

Definitions are written only for columns whose meaning is not self-evident. A
column judged obvious has no row and renders blank — "obvious" and
"undocumented" are deliberately not distinguished in the workbook.
```

- [ ] **Step 5a: Correct the stale call diagram in the same doc**

`docs/sample-download-workflow.md` carries a call diagram naming
`build_readme_rows`, which Task 5 deleted, with line numbers that have all
moved. `nextseek_api/services/sample_workbook.py` lines 3-5 point readers at
this doc, so the staleness is load-bearing.

Replace:

```
                 └─ write_samples_workbook(...)   nextseek_api/services/sample_workbook.py:91
                      ├─ sheet 1: README   (build_readme_rows :33, load_sample_type_context :50)
```

with:

```
                 └─ write_samples_workbook(...)   nextseek_api/services/sample_workbook.py:157
                      ├─ sheet 1: README   (build_readme_blocks :36, load_sample_type_context :63,
                      │                     load_sample_field_context :90, _write_readme :131)
```

Correct the line numbers and names only. Do not restructure the diagram.

- [ ] **Step 5: Verify the docs render and nothing else changed**

```bash
cd /Users/jps/Documents/MIT/NExtSEEK-readme-columns && git status --short
```

Expected: exactly `startup/seed/README.md` and `docs/sample-download-workflow.md` modified. `startup/seed/dmac.sql.gz` must be **unchanged** — this plan does not regenerate it.

- [ ] **Step 6: Commit**

```bash
git add startup/seed/README.md docs/sample-download-workflow.md
git commit -m "docs(startup): document the sample_fields_context rollout and seed gap"
```

---

## Done when

- `./scripts/run_tests.sh nextseek_api seek` shows no new failures.
- A download workbook opens with README as sheet 1, one bold section per tab, and every column of every tab listed beneath its section.
- Dropping the `sample_fields_context` table still produces a complete workbook, with blank meanings.
- Phase 2 is planned separately.

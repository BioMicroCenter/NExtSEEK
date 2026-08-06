# Unified Sample Download + README Sheet — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Route every sample-download control in NExtSEEK through one API, and make every downloaded workbook open on a README sheet that documents the sample types inside it.

**Architecture:** `POST /nextseek_api/admin/samples/retrieve/` becomes the single download endpoint — un-gated from `IsAdminUser` to `IsAuthenticated`, with real SEEK project resolution and a new `include_tree` flag. A new `nextseek_api/services/sample_workbook.py` owns all Excel writing; the two existing writers delegate to it, so the README cannot drift between code paths. README content comes from `dmac.sample_types_context`, joined on the sample-type code string.

**Tech Stack:** Django 4 + DRF, pydantic v2 request models, pandas 3.0.2 / openpyxl 3.1.5, MySQL 8, Neo4j, vanilla JS in Django templates, pytest + pytest-django, uv.

**Companion spec:** `docs/2026-08-06-unified-sample-download-readme-design.md`

## Global Constraints

- **uv, not pip.** `uv add <pkg>` / `uv run …`. Never hand-edit dependency pins.
- **Conventional commits with module scopes**: `feat(seek): …`, `fix(nextseek_api): …`, `docs(seek): …`.
- **Never commit secrets.** `docker/db.env`, `docker/nextseek.env`, `dmac/local_settings.py`, `startup/.instance.json` are gitignored — keep it that way.
- **Branch:** all work lands on `add-readme`. Commits stay scoped so they can be cherry-picked onto `main`.
- **`/app` in the `nextseek` container comes from the image**, not a bind mount. To test a change, `docker cp` it in — do not rebuild for the inner dev loop.
- **Test invocation** (the project's `pyproject.toml` defaults point at the *real* settings and collect from the repo root, so always pass settings and paths explicitly):
  ```
  docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
    sh -c 'cd /app && uv run pytest <paths> --no-migrations -q'
  ```
- **`static/` changes need `collectstatic`.** A rebuild alone does not serve them.
- **Database aliases:** `NEXTSEEK_DATABASE = "default"` → the `dmac` schema; `SEEK_DATABASE = "seek"` → `seek_production` (`dmac/settings.py:441-442`). `seek.dbrouters.CustomRouter` routes a model by its `_DATABASE` class attribute.
- **The contextdb URL**, used verbatim in cell A1:
  `https://github.com/BioMicroCenter/NExtSEEK/blob/main/chat_nextseek/src/chat_nextseek/context/sampletypes_db.json`

---

## File Structure

| File | Responsibility |
|---|---|
| `seek/models.py` (modify) | Add `Sample_types_context`, the ORM view of `dmac.sample_types_context`. |
| `startup/seed/dmac.sql.gz` (modify) | Ship the 101 context rows so a fresh install renders a real README. |
| `nextseek_api/services/sample_workbook.py` (create) | **Sole owner of sample-workbook Excel writing.** README construction, context lookup, sheet-per-sample-type output. |
| `nextseek_api/tests/test_sample_workbook.py` (create) | Unit tests for the above. |
| `seek/dbtable_sample.py:940` (modify) | `sampleRetrievalData` delegates to the new writer. |
| `seek/views.py:1232` (modify) | `sample_retrieval_data` delegates to the new writer. |
| `nextseek_api/models.py:1892` (modify) | Add `include_tree` to `AdminSampleRetrieveRequest`. |
| `nextseek_api/views.py:520-806` (modify) | Un-gate, fix project resolution, honour `include_tree`. |
| `nextseek_api/tests/test_views.py` (modify) | Tests for the three view changes. |
| `static/js/ns_sample_download.js` (create) | The one client-side download function. |
| 6 templates (modify) | Repoint every live control at the helper. |
| `nextseek_api/tests/test_download_call_sites.py` (create) | Regression guard: no live template still posts to a legacy download endpoint. |
| `docs/sample-download-workflow.md` (create) | The before/after map, including what is dead and why. |

---

### Task 1: `Sample_types_context` model and seed data

The context table exists on production but is absent from this repo's seed, so
the README would render empty on every fresh install. This task makes the table
real in code and in the seed.

The dump is already captured at the worktree root as `sample_types_context.sql`
(101 rows, `AUTO_INCREMENT=102`). Note `Tags` is capitalised in the database.

**Files:**
- Modify: `seek/models.py` (append after `Sample_types_clades`, which ends at line 208)
- Modify: `startup/seed/dmac.sql.gz`
- Test: `nextseek_api/tests/test_sample_types_context_model.py` (create)
- Source (deleted at the end of this task): `sample_types_context.sql`

**Interfaces:**
- Consumes: nothing.
- Produces: `seek.models.Sample_types_context`, with fields `id`, `sampletype_id`,
  `sample_type`, `name`, `description`, `required_metadata`, `standard_metadata`,
  `possible_metadata_fields`, `clade`, `sampletype_file_link`,
  `associated_assay_parents`, `associated_assay_children`, `parent_sampletypes`,
  `child_sampletypes`, `tags` (mapped to DB column `Tags`).

- [ ] **Step 1: Write the failing test**

Create `nextseek_api/tests/test_sample_types_context_model.py`:

```python
"""Sample_types_context maps dmac.sample_types_context, and the seed ships its rows."""

import gzip
from pathlib import Path

from seek.models import Sample_types_context

SEED = Path(__file__).resolve().parents[2] / "startup" / "seed" / "dmac.sql.gz"


def test_model_points_at_the_dmac_table():
    assert Sample_types_context._meta.db_table == "sample_types_context"
    # NEXTSEEK_DATABASE == "default" == the dmac schema; CustomRouter reads _DATABASE.
    assert Sample_types_context._DATABASE == "default"


def test_model_exposes_the_readme_columns():
    names = {f.name for f in Sample_types_context._meta.get_fields()}
    assert {"sample_type", "name", "description"} <= names


def test_tags_field_maps_to_the_capitalised_db_column():
    field = Sample_types_context._meta.get_field("tags")
    assert field.db_column == "Tags"


def test_seed_ships_the_context_table_and_rows():
    """Asserts what startup/seed/dmac.sql.gz actually contains.

    In-container this reads /app/startup/seed/dmac.sql.gz, so it fails loudly if
    the seed was not copied in alongside the code under test.
    """
    sql = gzip.decompress(SEED.read_bytes()).decode("utf-8", errors="replace")
    assert "CREATE TABLE `sample_types_context`" in sql
    insert = [ln for ln in sql.splitlines() if ln.startswith("INSERT INTO `sample_types_context`")]
    assert insert, "seed has the table but no rows"
    # 101 rows on production; the dump writes them as one extended INSERT.
    assert insert[0].count("),(") == 100
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
docker cp nextseek_api/tests/test_sample_types_context_model.py nextseek:/app/nextseek_api/tests/
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nextseek_api/tests/test_sample_types_context_model.py --no-migrations -q'
```

Expected: FAIL — `ImportError: cannot import name 'Sample_types_context' from 'seek.models'`.

- [ ] **Step 3: Add the model**

Append to `seek/models.py`, directly after the `Sample_types_clades` class:

```python
class Sample_types_context(models.Model):
    _DATABASE = NEXTSEEK_DATABASE

    sampletype_id = models.IntegerField(default=None, null=True)
    sample_type = models.CharField(max_length=32, default=None, null=True)
    name = models.CharField(max_length=255, default=None, null=True)
    description = models.TextField(default=None, null=True)
    required_metadata = models.TextField(default=None, null=True)
    standard_metadata = models.TextField(default=None, null=True)
    possible_metadata_fields = models.TextField(default=None, null=True)
    clade = models.CharField(max_length=64, default=None, null=True)
    sampletype_file_link = models.CharField(max_length=255, default=None, null=True)
    associated_assay_parents = models.TextField(default=None, null=True)
    associated_assay_children = models.TextField(default=None, null=True)
    parent_sampletypes = models.TextField(default=None, null=True)
    child_sampletypes = models.TextField(default=None, null=True)
    tags = models.TextField(db_column="Tags", default=None, null=True)

    def __unicode__(self):
        return self.sample_type

    class Meta:
        db_table = "sample_types_context"
```

- [ ] **Step 4: Fold the dump into the seed**

The seed is a gzipped mysqldump of the whole `dmac` schema. Append the context
table's `CREATE TABLE` + `INSERT` to it, then re-gzip.

```bash
cd "$(git rev-parse --show-toplevel)"
gzip -dc startup/seed/dmac.sql.gz > /tmp/dmac.sql
# Strip mysqldump's session preamble/postamble from the single-table dump so the
# combined file has exactly one set; keep only the DDL and data.
sed -n '/^DROP TABLE IF EXISTS `sample_types_context`/,/^UNLOCK TABLES;/p' \
  sample_types_context.sql >> /tmp/dmac.sql
gzip -c /tmp/dmac.sql > startup/seed/dmac.sql.gz
rm /tmp/dmac.sql
```

Verify the round-trip before moving on:

```bash
gzip -dc startup/seed/dmac.sql.gz | grep -c "CREATE TABLE \`sample_types_context\`"
```

Expected: `1`.

- [ ] **Step 5: Load the table into the running stack**

The local `dmac` database predates the table; tasks 2-7 need it present.

```bash
docker exec -i seek-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" dmac' < sample_types_context.sql
docker exec seek-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e "SELECT COUNT(*) FROM dmac.sample_types_context"'
```

Expected: `101`.

- [ ] **Step 6: Run the tests to verify they pass**

The seed itself is under test, so copy it in alongside the model:

```bash
docker cp seek/models.py nextseek:/app/seek/
docker cp startup/seed/dmac.sql.gz nextseek:/app/startup/seed/
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nextseek_api/tests/test_sample_types_context_model.py --no-migrations -q'
```

Expected: 4 passed.

Note on `host_only`: an earlier draft marked the seed test with it. Don't —
nothing filters the marker (`pyproject.toml:148` only registers it, and
`nextseek_api/cc_assistant/scripts/verify_host_only_allowlist.py` scans only
`nextseek_api/cc_assistant/tests/` against an allowlist path that does not exist
on this machine). The marker would not have moved the test to a host lane; it
would only have made the in-container run misleading.

- [ ] **Step 7: Delete the scratch dump and commit**

```bash
rm sample_types_context.sql
git add seek/models.py startup/seed/dmac.sql.gz nextseek_api/tests/test_sample_types_context_model.py
git commit -m "feat(seek): add Sample_types_context model and seed its 101 rows"
```

---

### Task 2: The shared workbook writer

One module owns every byte of a sample workbook. It is pure-Python and
independently testable: the README builder takes plain data, and only the
context lookup touches the ORM.

**Files:**
- Create: `nextseek_api/services/sample_workbook.py`
- Test: `nextseek_api/tests/test_sample_workbook.py`

**Interfaces:**
- Consumes: `seek.models.Sample_types_context` (Task 1).
- Produces:
  - `CONTEXTDB_URL: str`
  - `build_readme_rows(codes: Iterable[str], context_by_code: Mapping[str, Mapping[str, str]]) -> list[list[str]]`
  - `load_sample_type_context(codes: Iterable[str]) -> dict[str, dict[str, str]]`
  - `write_samples_workbook(parsed_df, output_path, context_by_code=None) -> None`
    — `parsed_df` must carry a `uuid` column; the function derives `sample_type`
    itself so the extraction regex lives in exactly one place.

- [ ] **Step 1: Write the failing tests**

Create `nextseek_api/tests/test_sample_workbook.py`:

```python
"""The one workbook writer: README sheet first, then a sheet per sample type."""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from openpyxl import load_workbook

from nextseek_api.services.sample_workbook import (
    CONTEXTDB_URL,
    build_readme_rows,
    load_sample_type_context,
    write_samples_workbook,
)

_MOD = "nextseek_api.services.sample_workbook"

CONTEXT = {
    "MUS": {"name": "Mouse", "description": "A mouse sample."},
    "TIS": {"name": "Tissue", "description": "A tissue sample."},
}


def test_readme_rows_start_with_the_header():
    rows = build_readme_rows(["MUS"], CONTEXT)
    assert rows[0] == ["Sample Type", "Name", "Description"]


def test_readme_rows_carry_name_and_description():
    rows = build_readme_rows(["MUS"], CONTEXT)
    assert rows[1] == ["MUS", "Mouse", "A mouse sample."]


def test_readme_rows_are_sorted_by_code():
    rows = build_readme_rows(["TIS", "MUS"], CONTEXT)
    assert [r[0] for r in rows[1:]] == ["MUS", "TIS"]


def test_undocumented_code_is_listed_with_blanks():
    rows = build_readme_rows(["MUS", "ZZZ"], CONTEXT)
    assert rows[2] == ["ZZZ", "", ""]


def test_readme_rows_deduplicate_codes():
    rows = build_readme_rows(["MUS", "MUS"], CONTEXT)
    assert len(rows) == 2


def test_readme_rows_drop_blank_codes():
    rows = build_readme_rows(["MUS", None, ""], CONTEXT)
    assert [r[0] for r in rows[1:]] == ["MUS"]


@patch(f"{_MOD}.Sample_types_context")
def test_load_context_maps_code_to_name_and_description(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"sample_type": "MUS", "name": "Mouse", "description": "A mouse sample."},
    ]
    assert load_sample_type_context(["MUS"]) == {
        "MUS": {"name": "Mouse", "description": "A mouse sample."}
    }


@patch(f"{_MOD}.Sample_types_context")
def test_load_context_coerces_nulls_to_empty_strings(mock_model):
    mock_model.objects.filter.return_value.values.return_value = [
        {"sample_type": "MUS", "name": None, "description": None},
    ]
    assert load_sample_type_context(["MUS"]) == {"MUS": {"name": "", "description": ""}}


@patch(f"{_MOD}.Sample_types_context")
def test_load_context_does_not_query_for_an_empty_code_list(mock_model):
    assert load_sample_type_context([]) == {}
    mock_model.objects.filter.assert_not_called()


def _df():
    return pd.DataFrame([
        {"uuid": "MUS-230101ABC-1", "Name": "m1", "Sex": "F"},
        {"uuid": "TIS-230101ABC-2", "Name": "t1", "Sex": ""},
    ])


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_is_the_first_sheet(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    assert load_workbook(out).sheetnames[0] == "README"


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_sample_type_sheets_follow_the_readme(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    assert load_workbook(out).sheetnames == ["README", "MUS", "TIS"]


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_a1_links_to_the_contextdb(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws["A1"].hyperlink.target == CONTEXTDB_URL


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_readme_table_starts_at_row_3(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert [ws.cell(3, c).value for c in (1, 2, 3)] == ["Sample Type", "Name", "Description"]
    assert ws.cell(4, 1).value == "MUS"


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_helper_columns_are_dropped_from_data_sheets(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["MUS"]
    headers = [c.value for c in ws[1]]
    assert "uuid" not in headers and "sample_type" not in headers


@patch(f"{_MOD}.load_sample_type_context", return_value=CONTEXT)
def test_all_empty_columns_are_dropped(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    assert "Sex" not in [c.value for c in load_workbook(out)["TIS"][1]]


@patch(f"{_MOD}.load_sample_type_context", return_value={})
def test_workbook_still_written_when_context_table_is_empty(_ctx, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out))
    ws = load_workbook(out)["README"]
    assert ws.cell(4, 1).value == "MUS"
    assert ws.cell(4, 2).value in (None, "")


@patch(f"{_MOD}.load_sample_type_context")
def test_supplied_context_skips_the_lookup(mock_load, tmp_path):
    out = tmp_path / "w.xlsx"
    write_samples_workbook(_df(), str(out), context_by_code=CONTEXT)
    mock_load.assert_not_called()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
docker cp nextseek_api/tests/test_sample_workbook.py nextseek:/app/nextseek_api/tests/
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nextseek_api/tests/test_sample_workbook.py --no-migrations -q'
```

Expected: collection error — `ModuleNotFoundError: No module named 'nextseek_api.services.sample_workbook'`.

- [ ] **Step 3: Write the module**

Create `nextseek_api/services/sample_workbook.py`:

```python
"""The single writer for sample-download workbooks.

Every sample download in NExtSEEK ends here, so the README sheet cannot drift
between the legacy `seek` views and the `nextseek_api` endpoint.
"""

from __future__ import annotations

from typing import Iterable, Mapping

import pandas as pd

from seek.models import Sample_types_context

import logging

logger = logging.getLogger(__name__)

CONTEXTDB_URL = (
    "https://github.com/BioMicroCenter/NExtSEEK/blob/main/"
    "chat_nextseek/src/chat_nextseek/context/sampletypes_db.json"
)

README_SHEET = "README"
README_HEADER = ["Sample Type", "Name", "Description"]
README_LINK_TEXT = "Sample type definitions: sampletypes_db.json (GitHub)"

# Sample UIDs lead with the sample-type code: "MUS-230101ABC-1", "D.SEQ-240910LAU-3".
SAMPLE_TYPE_RE = r"([A-Z]+\.[A-Z]+|[A-Z]+)"


def build_readme_rows(
    codes: Iterable[str],
    context_by_code: Mapping[str, Mapping[str, str]],
) -> list[list[str]]:
    """Header row plus one row per distinct code, sorted, blanks when undocumented."""
    seen = sorted({c for c in codes if c})
    rows = [list(README_HEADER)]
    for code in seen:
        entry = context_by_code.get(code) or {}
        rows.append([code, entry.get("name", "") or "", entry.get("description", "") or ""])
    return rows


def load_sample_type_context(codes: Iterable[str]) -> dict[str, dict[str, str]]:
    """Look up code -> {name, description} in dmac.sample_types_context.

    Joins on the `sample_type` code string, not `sampletype_id`: the id column
    does not agree with `sample_types.id` across instances.
    """
    wanted = sorted({c for c in codes if c})
    if not wanted:
        return {}
    try:
        rows = Sample_types_context.objects.filter(sample_type__in=wanted).values(
            "sample_type", "name", "description"
        )
        return {
            r["sample_type"]: {
                "name": r.get("name") or "",
                "description": r.get("description") or "",
            }
            for r in rows
        }
    except Exception:
        # A missing or unreachable context table must not cost the user their
        # download; the README then lists codes with blank name/description.
        logger.exception("sample_types_context lookup failed; README will be unpopulated")
        return {}


def _write_readme(book, rows: list[list[str]]) -> None:
    ws = book.create_sheet(README_SHEET, 0)
    ws["A1"] = README_LINK_TEXT
    ws["A1"].hyperlink = CONTEXTDB_URL
    ws["A1"].style = "Hyperlink"
    # Row 2 is left blank to separate the link from the table.
    for r, row in enumerate(rows, start=3):
        for c, value in enumerate(row, start=1):
            ws.cell(row=r, column=c, value=value)
    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 34
    ws.column_dimensions["C"].width = 100


def write_samples_workbook(parsed_df, output_path, context_by_code=None) -> None:
    """Write README as sheet 1, then one sheet per sample type.

    `parsed_df` must carry a `uuid` column; `sample_type` is derived here.
    """
    df = parsed_df.copy()
    df["sample_type"] = df["uuid"].astype(str).str.extract(SAMPLE_TYPE_RE, expand=False)

    codes = [c for c in df["sample_type"].dropna().unique().tolist()]
    if context_by_code is None:
        context_by_code = load_sample_type_context(codes)

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        book = writer.book
        # pandas removes openpyxl's default sheet, but guard in case that changes.
        if "Sheet" in book.sheetnames:
            del book["Sheet"]
        _write_readme(book, build_readme_rows(codes, context_by_code))

        for sample_type, sample_type_df in df.groupby("sample_type"):
            sample_type_df = sample_type_df.drop(columns=["uuid", "sample_type"])
            sample_type_df = sample_type_df.replace("", pd.NA)
            sample_type_df = sample_type_df.dropna(axis=1, how="all")
            sample_type_df.to_excel(writer, sheet_name=sample_type, index=False)
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
docker cp nextseek_api/services/sample_workbook.py nextseek:/app/nextseek_api/services/
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nextseek_api/tests/test_sample_workbook.py --no-migrations -q'
```

Expected: 17 passed.

- [ ] **Step 5: Commit**

```bash
git add nextseek_api/services/sample_workbook.py nextseek_api/tests/test_sample_workbook.py
git commit -m "feat(nextseek_api): one workbook writer with a README sheet"
```

---

### Task 3: Both existing writers delegate to it

Two near-identical writers exist today: a method on `DBtable_sample` used by the
DRF endpoint, and a module function in `seek/views.py` used by the legacy view.
Point both at Task 2's writer so the README appears on every path.

**Files:**
- Modify: `seek/dbtable_sample.py:940-949` (`sampleRetrievalData`)
- Modify: `seek/views.py:1232-1242` (`sample_retrieval_data`)
- Test: `nextseek_api/tests/test_sample_workbook.py` (extend)

**Interfaces:**
- Consumes: `write_samples_workbook` (Task 2).
- Produces: no signature changes. `DBtable_sample.sampleRetrievalData(children_uids, output)`
  and `seek.views.sample_retrieval_data(children_uids, output)` keep their
  existing two-argument shapes and their existing callers.

- [ ] **Step 1: Write the failing tests**

Append to `nextseek_api/tests/test_sample_workbook.py`:

```python
def test_dbtable_sample_retrieval_data_delegates_to_the_shared_writer():
    from seek.dbtable_sample import DBtable_sample

    df = pd.DataFrame([{"uuid": "MUS-1", "json_metadata": '{"Name": "m1"}'}])
    with patch("seek.dbtable_sample.write_samples_workbook") as mock_write:
        DBtable_sample().sampleRetrievalData(df, "/tmp/unused.xlsx")
    mock_write.assert_called_once()
    assert mock_write.call_args[0][1] == "/tmp/unused.xlsx"


def test_seek_views_sample_retrieval_data_delegates_to_the_shared_writer():
    from seek import views

    df = pd.DataFrame([{"uuid": "MUS-1", "json_metadata": '{"Name": "m1"}'}])
    with patch("seek.views.write_samples_workbook") as mock_write:
        views.sample_retrieval_data(df, "/tmp/unused.xlsx")
    mock_write.assert_called_once()
    assert mock_write.call_args[0][1] == "/tmp/unused.xlsx"
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker cp nextseek_api/tests/test_sample_workbook.py nextseek:/app/nextseek_api/tests/
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nextseek_api/tests/test_sample_workbook.py -k delegates --no-migrations -q'
```

Expected: FAIL — `AttributeError: <module 'seek.dbtable_sample'> does not have the attribute 'write_samples_workbook'`.

- [ ] **Step 3: Delegate from `DBtable_sample`**

In `seek/dbtable_sample.py`, add the import near the other module imports at the
top of the file:

```python
from nextseek_api.services.sample_workbook import write_samples_workbook
```

Replace the body of `sampleRetrievalData` (currently lines 940-949) with:

```python
    def sampleRetrievalData(self, children_uids, output):
        # Sheet layout, README included, is owned by
        # nextseek_api.services.sample_workbook so it cannot drift per call path.
        write_samples_workbook(self.__parse_children_uids(children_uids), output)
```

Note: `__parse_children_uids` is name-mangled. Inside the class body the call
above is correct as written.

- [ ] **Step 4: Delegate from `seek/views.py`**

Add to the imports at the top of `seek/views.py`:

```python
from nextseek_api.services.sample_workbook import write_samples_workbook
```

Replace `sample_retrieval_data` (currently lines 1232-1242) with:

```python
def sample_retrieval_data(children_uids, output):
    # Sheet layout, README included, is owned by
    # nextseek_api.services.sample_workbook so it cannot drift per call path.
    write_samples_workbook(parse_children_uids(children_uids), output)
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
docker cp seek/dbtable_sample.py nextseek:/app/seek/
docker cp seek/views.py nextseek:/app/seek/
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nextseek_api/tests/test_sample_workbook.py --no-migrations -q'
```

Expected: 19 passed.

- [ ] **Step 6: Check for a circular import**

`seek.dbtable_sample` now imports from `nextseek_api.services`, which imports
`seek.models`. Confirm Django still boots:

```bash
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run python -c "import django; django.setup(); import seek.views, seek.dbtable_sample, nextseek_api.views; print(\"imports ok\")"'
```

Expected: `imports ok`. If this fails with a circular import, move the
`write_samples_workbook` import inside each function body instead of module
level, and re-run steps 5-6.

- [ ] **Step 7: Commit**

```bash
git add seek/dbtable_sample.py seek/views.py nextseek_api/tests/test_sample_workbook.py
git commit -m "refactor(seek): route both workbook writers through the shared one"
```

---

### Task 4: Un-gate the endpoint, resolve projects, honour `include_tree`

Three changes to one view. They ship together because un-gating without the
project fix returns 404s to exactly the users the un-gating is meant to serve.

**Files:**
- Modify: `nextseek_api/models.py:1892-1900` (`AdminSampleRetrieveRequest`)
- Modify: `nextseek_api/views.py:526` (permissions), `:596-628` (project resolution), `:670-672` (tree expansion)
- Test: `nextseek_api/tests/test_views.py` (extend `TestAdminSampleViewSet`)

**Interfaces:**
- Consumes: `resolve_seek_auth(request, ["BASIC", "SESSION"]) -> (basic_tuple, extra_headers)`
  from `nextseek_api.helpers`; `SeekDB(server, username, password)` from `seek.seekdb`.
- Produces: `AdminSampleRetrieveRequest.include_tree: bool = True`. The endpoint
  accepts `{"identifiers": [...], "output_format": "json"|"excel", "include_tree": bool}`.

- [ ] **Step 1: Write the failing tests**

Append to `class TestAdminSampleViewSet` in `nextseek_api/tests/test_views.py`:

```python
    def test_endpoint_is_not_admin_gated(self):
        from nextseek_api.views import AdminSampleViewSet
        from rest_framework.permissions import IsAdminUser, IsAuthenticated

        assert IsAuthenticated in AdminSampleViewSet.permission_classes
        assert IsAdminUser not in AdminSampleViewSet.permission_classes

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("alice", "pw"), {}))
    def test_seekdb_is_built_from_the_resolved_credentials(self, mock_auth, mock_seekdb, mock_dbs):
        """SeekDB(None, None, None) never sets __server, so getCurrentUser() always
        raised and every caller fell through to an empty project list."""
        mock_seekdb.return_value = _seekdb_mock([7])
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
        ])

        req = _auth_request("post", "/", data={"identifiers": ["NHP-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        vs.admin_retrieve_samples(req)

        mock_seekdb.assert_called_once_with(None, "alice", "pw")

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("alice", "pw"), {}))
    def test_non_staff_user_gets_project_scoped_rows_not_404(self, mock_auth, mock_seekdb, mock_dbs):
        mock_seekdb.return_value = _seekdb_mock([7])
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "TIS-1", "sample_type_id": 13, "json_metadata": "{}"},
        ])

        user = MagicMock()
        user.is_authenticated = True
        user.is_staff = False
        user.is_superuser = False
        req = _auth_request("post", "/", data={"identifiers": ["TIS-1"]}, user=user)
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)

        assert resp.status_code == 200
        # The caller's real projects reached getChildrenUIDs instead of [].
        assert mock_dbs.return_value.getChildrenUIDs.call_args[0][1] == ["7"]
        assert mock_dbs.return_value.getChildrenUIDs.call_args[0][2] is False

    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_include_tree_defaults_to_true(self, mock_auth, mock_seekdb, mock_dbs):
        mock_seekdb.return_value = _seekdb_mock([1])
        mock_dbs.return_value.getChildrenUIDs.return_value = self._df([
            {"id": 1, "uuid": "NHP-1", "sample_type_id": 12, "json_metadata": "{}"},
        ])

        req = _auth_request("post", "/", data={"identifiers": ["NHP-1"]}, user=_admin_user())
        vs = self._vs()
        vs.request = req
        vs.admin_retrieve_samples(req)

        mock_dbs.return_value.getChildrenUIDs.assert_called_once()

    @patch(f"{_VIEWS}.MySQLdb")
    @patch(f"{_VIEWS}.settings")
    @patch(f"{_VIEWS}.DBtable_sample")
    @patch(f"{_VIEWS}.SeekDB")
    @patch(f"{_VIEWS}.resolve_seek_auth", return_value=(("a", "p"), {}))
    def test_include_tree_false_skips_neo4j(self, mock_auth, mock_seekdb, mock_dbs, mock_s, mock_mysql):
        _setup_settings(mock_s)
        mock_seekdb.return_value = _seekdb_mock([1])
        conn, cur = _mysql_cursor(
            fetchall_val=[(1, 12, "NHP-1", "{}")],
            description=[("id",), ("sample_type_id",), ("uuid",), ("json_metadata",)],
        )
        mock_mysql.connect.return_value = conn

        req = _auth_request(
            "post", "/",
            data={"identifiers": ["NHP-1"], "include_tree": False},
            user=_admin_user(),
        )
        vs = self._vs()
        vs.request = req
        resp = vs.admin_retrieve_samples(req)

        assert resp.status_code == 200
        mock_dbs.return_value.getChildrenUIDs.assert_not_called()
```

- [ ] **Step 2: Run them to verify they fail**

```bash
docker cp nextseek_api/tests/test_views.py nextseek:/app/nextseek_api/tests/
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nextseek_api/tests/test_views.py -k "not_admin_gated or resolved_credentials or project_scoped_rows or include_tree" --no-migrations -q'
```

Expected: 5 failed.

- [ ] **Step 3: Add `include_tree` to the request model**

In `nextseek_api/models.py`, inside `AdminSampleRetrieveRequest` (line 1892),
after the `output_format` field:

```python
    include_tree: bool = Field(
        True,
        description="Expand the selection over the Neo4j DERIVED_FROM graph to include "
                    "parent and child samples. False returns only the requested identifiers.",
    )
```

`model_config` already sets `extra='forbid'`, so the new field must be declared
here or callers passing it get a 422.

**The field alone is not enough.** `admin_retrieve_samples` hand-normalises the
body before validating, and only forwards `identifiers` and `output_format` — so
`include_tree` would silently always default to `True`. Also extract it in the
normalisation block (`views.py:568-578`):

```python
            # Form-encoded callers send "false"/"0"; pydantic coerces both.
            include_tree = body.get("include_tree", True)
```

with `include_tree = True` in the non-dict `else` branch, and pass it through:

```python
            req = AdminSampleRetrieveRequest.model_validate(
                {
                    "identifiers": identifiers,
                    "output_format": output_format,
                    "include_tree": include_tree,
                }
            )
```

- [ ] **Step 4: Un-gate the view**

In `nextseek_api/views.py`, line 526:

```python
    permission_classes = [IsAuthenticated]
```

- [ ] **Step 5: Resolve the caller's SEEK projects for real**

Replace lines 596-601 (the `seekdb = SeekDB(None, None, None)` block and its
`try/except`) with:

```python
        # SeekDB(None, None, None) takes the username-is-None branch (seek/seekdb.py:31),
        # never calls getSeekLogin(), and leaves __server None — so getCurrentUser()
        # raised TypeError and user_project_ids was ALWAYS []. Build it from the
        # credentials resolve_seek_auth already gave us instead.
        seekdb = SeekDB(None, basic_tuple[0], basic_tuple[1])
        try:
            user_projects = seekdb.getCurrentUser()['data']['relationships']['projects']['data']
            user_project_ids = list(map(lambda x: x['id'], user_projects))
        except Exception:
            logger.exception("Could not resolve SEEK projects for the caller")
            user_project_ids = []
```

**`nextseek_api/views.py` has no `logger`.** It is the only module in the app
without one, so `logger.exception` here would raise `NameError` inside an
`except` block and mask the original error. Add `import logging` to the imports
and, after the last import, `logger = logging.getLogger(__name__)`.

Then replace the long `# SECURITY, known gap` comment block at lines 602-627 with:

```python
        # Staff are still treated as admin here, so project membership does not
        # narrow their data scope — every SEEK user synced into NExtSEEK is marked
        # staff (dmac/views.py:80,97). That gap is deliberately still open; closing
        # it changes what 11 of 20 local accounts can read and would affect the
        # assistant and container-CC consumers, which is out of scope for this
        # change. The prerequisite it used to name — real project resolution —
        # is now done above, so dropping `is_staff` here is a safe one-line
        # follow-up whenever the consumer impact has been assessed.
```

Leave line 628 (`is_superuser = ...`) exactly as it is.

- [ ] **Step 6: Honour `include_tree`**

At line 670, replace:

```python
        try:
            children_uids_df = dbs.getChildrenUIDs(requested_uids, user_project_ids, is_superuser)
        except IndexError:
```

with:

```python
        try:
            if req.include_tree:
                children_uids_df = dbs.getChildrenUIDs(requested_uids, user_project_ids, is_superuser)
            else:
                # No graph expansion: fetch exactly what was asked for. Raising
                # Neo4jError reuses the existing fallback, which is precisely the
                # project-scoped MySQL query this path needs.
                raise Neo4jError("include_tree=False")
        except IndexError:
```

`Neo4jError` is already imported — `nextseek_api/views.py:21` has
`from neo4j.exceptions import AuthError, Neo4jError`. No import change needed.

- [ ] **Step 7: Run the tests to verify they pass**

```bash
docker cp nextseek_api/models.py nextseek:/app/nextseek_api/
docker cp nextseek_api/views.py nextseek:/app/nextseek_api/
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nextseek_api/tests/test_views.py --no-migrations -q'
```

Expected: the whole `test_views.py` file passes, including the pre-existing
`TestAdminSampleViewSet` tests. If `test_neo4j_fallback_non_superuser` now fails,
it is because `_seekdb_mock()` returns real project ids where the old code
always produced `[]` — update that test's assertion rather than reverting the fix.

- [ ] **Step 8: Commit**

```bash
git add nextseek_api/models.py nextseek_api/views.py nextseek_api/tests/test_views.py
git commit -m "feat(nextseek_api): un-gate sample retrieval, resolve projects, add include_tree"
```

---

### Task 5: One client-side download helper, seven controls

**Files:**
- Create: `static/js/ns_sample_download.js`
- Modify: `seek/templates/pages/samples.embed.html:51-58`
- Modify: `seek/templates/searchAdvanced.html:124`
- Modify: `seek/templates/pages/searchAdvanced_stable.embed.html:2-135`
- Modify: `seek/templates/pages/samples_stable.embed.html:39-175`
- Modify: `seek/templates/pages/searchAdvanced_newretrieval.embed.html:15-20`
- Modify: `seek/templates/newSearch.html:178-200`
- Test: `nextseek_api/tests/test_download_call_sites.py` (create)

**Interfaces:**
- Consumes: `POST /nextseek_api/admin/samples/retrieve/` with
  `{identifiers: string[], output_format: "excel", include_tree: boolean}` (Task 4).
- Produces:
  - `window.nsDownloadSamples(identifiers, options)` where
    `options = {includeTree?: boolean, filename?: string}`, returning a Promise.
  - `window.nsCollectSelectedUids(dg)` — checked rows of an EasyUI datagrid → UID strings.
  - `window.nsExtractUid(raw)` — the grids store `uid` as **anchor markup**, not a
    bare UID. Both `searchAdvanced_stable.embed.html:56-57` and
    `samples_stable.embed.html:95-96` do `row.uid.match(/(?<=>).*?(?=<)/g)[0]`
    to recover the text. This helper centralises that.

- [ ] **Step 1: Write the failing test**

Create `nextseek_api/tests/test_download_call_sites.py`:

```python
"""No template that Django actually renders may still post to a legacy download endpoint.

Orphans are excluded deliberately: nothing includes searchAdvanced_rtable or
searchAdvanced_tree, sampleSearch.html's render is commented out at
seek/views.py:412, sampleDeletion.html has no view, and .bk is a backup.
"""

from pathlib import Path

import pytest

TEMPLATES = Path(__file__).resolve().parents[2] / "seek" / "templates"

LEGACY = ("/seek/samples/download/", "/seek/admin/retrieve/")

ORPHANS = {
    "pages/searchAdvanced_rtable.embed.html",
    "pages/searchAdvanced_tree.embed.html",
    "sampleSearch.html",
    "sampleDeletion.html",
    "pages/samples_stable.embed.html.bk",
}

LIVE = [
    "pages/samples.embed.html",
    "searchAdvanced.html",
    "pages/searchAdvanced_stable.embed.html",
    "pages/samples_stable.embed.html",
    "pages/searchAdvanced_newretrieval.embed.html",
    "newSearch.html",
    "pages/samples_new_stable.embed.html",
    "pages/searchAdvanced_new_stable.embed.html",
]


@pytest.mark.host_only
@pytest.mark.parametrize("name", LIVE)
def test_live_template_has_no_legacy_download_endpoint(name):
    text = (TEMPLATES / name).read_text(encoding="utf-8", errors="replace")
    for endpoint in LEGACY:
        assert endpoint not in text, f"{name} still references {endpoint}"


@pytest.mark.host_only
def test_the_helper_script_is_loaded_where_downloads_happen():
    for name in ("pages/samples.embed.html", "searchAdvanced.html", "newSearch.html"):
        text = (TEMPLATES / name).read_text(encoding="utf-8", errors="replace")
        assert "ns_sample_download.js" in text, f"{name} does not load the helper"


@pytest.mark.host_only
def test_orphan_list_still_matches_reality():
    """If someone wires an orphan back up, this test should be revisited."""
    for name in ORPHANS:
        assert (TEMPLATES / name).exists(), f"{name} vanished; update this test"
```

- [ ] **Step 2: Run it to verify it fails**

```bash
uv run pytest nextseek_api/tests/test_download_call_sites.py -q --no-migrations
```

Expected: 8 of the 10 parametrised/named tests fail — every live template still
carries a legacy endpoint and none loads the helper.

- [ ] **Step 3: Write the helper**

Create `static/js/ns_sample_download.js`:

```javascript
/* The one sample-download client. Every download control in NExtSEEK calls this.
 *
 * Replaces three earlier paths: POST form to /seek/admin/retrieve/, $.post to
 * /seek/samples/download/ returning a link, and a bespoke fetch on the new
 * retrieval page. See docs/sample-download-workflow.md.
 */
(function (window, document) {
  "use strict";

  var ENDPOINT = "/nextseek_api/admin/samples/retrieve/";

  function getCsrfToken() {
    var match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
    return match ? decodeURIComponent(match[1]) : "";
  }

  function defaultFilename() {
    var d = new Date();
    function pad(n) { return String(n).padStart(2, "0"); }
    return "download-samples-" + d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" +
           pad(d.getDate()) + "_" + pad(d.getHours()) + "-" + pad(d.getMinutes()) + ".xlsx";
  }

  function saveBlob(blob, filename) {
    var url = window.URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    window.setTimeout(function () {
      window.URL.revokeObjectURL(url);
      document.body.removeChild(a);
    }, 0);
  }

  /**
   * @param {string[]} identifiers Sample UIDs and/or numeric SEEK ids.
   * @param {{includeTree?: boolean, filename?: string}} [options]
   * @returns {Promise<void>}
   */
  function nsDownloadSamples(identifiers, options) {
    options = options || {};
    var ids = (identifiers || []).map(function (v) { return String(v).trim(); })
                                 .filter(function (v) { return v.length > 0; });
    if (ids.length === 0) {
      window.alert("No sample in the table is selected for download.");
      return Promise.resolve();
    }

    var includeTree = options.includeTree !== false;
    var progress = null;
    if (window.jQuery && window.jQuery.messager) {
      progress = window.jQuery.messager.progress({
        title: "Please wait",
        msg: includeTree ? "Retrieving samples and all associated samples..."
                         : "Retrieving samples..."
      });
    }

    function closeProgress() {
      if (window.jQuery && window.jQuery.messager) {
        window.jQuery.messager.progress("close");
      }
    }

    return fetch(ENDPOINT, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-CSRFToken": getCsrfToken()
      },
      body: JSON.stringify({
        identifiers: ids,
        output_format: "excel",
        include_tree: includeTree
      })
    }).then(function (response) {
      if (!response.ok) {
        throw new Error("Download failed (HTTP " + response.status + ")");
      }
      return response.blob();
    }).then(function (blob) {
      closeProgress();
      saveBlob(blob, options.filename || defaultFilename());
    }).catch(function (error) {
      closeProgress();
      console.error("Sample download error:", error);
      window.alert("Sample download failed: " + error.message);
    });
  }

  /* The datagrids render the `uid` column as an anchor, so row.uid is markup
   * like `<a href="...">MUS-230101ABC-1</a>`, not a bare UID. Both legacy
   * collectors pulled the text out with the same regex; do it in one place. */
  function nsExtractUid(raw) {
    if (raw === null || raw === undefined) { return ""; }
    var s = String(raw);
    var m = s.match(/>([^<]+)</);
    return (m ? m[1] : s).trim();
  }

  /** Checked rows of an EasyUI datagrid -> UID strings. */
  function nsCollectSelectedUids(dg) {
    var rows = dg.datagrid("getRows");
    var uids = [];
    for (var i = 0; i < rows.length; i++) {
      if (rows[i].ck) {
        var uid = nsExtractUid(rows[i].uid);
        if (uid) { uids.push(uid); }
      }
    }
    return uids;
  }

  window.nsDownloadSamples = nsDownloadSamples;
  window.nsCollectSelectedUids = nsCollectSelectedUids;
  window.nsExtractUid = nsExtractUid;
})(window, document);
```

- [ ] **Step 4: Load the helper in the three rendered pages**

Add this `<script>` tag to `seek/templates/pages/samples.embed.html`,
`seek/templates/searchAdvanced.html` and `seek/templates/newSearch.html`, before
the first block that uses it:

```html
<script src="{% static 'js/ns_sample_download.js' %}"></script>
```

Each file must have `{% load static %}` at the top. Check and add if missing:

```bash
head -3 seek/templates/pages/samples.embed.html seek/templates/searchAdvanced.html seek/templates/newSearch.html
```

- [ ] **Step 5: Repoint control (g) — the sample page**

In `seek/templates/pages/samples.embed.html`, replace the `<form>` at lines
51-58 with a button that calls the helper. The UID currently rendered into the
hidden input becomes the argument:

```html
<button class="l-btn-left l-btn-icon-left" type="button"
        onclick="nsDownloadSamples(['{% for dici in report.sampleinfo %}{% if dici.attrname == 'UID' %}{{dici.attrvalue}}{% endif %}{% endfor %}'], {includeTree: true})">
    <span class="l-btn-text">Download All Samples</span>
    <span class="l-btn-icon icon-save"></span>
</button>
```

- [ ] **Step 6: Repoint control (a) — the advanced-search retrieval form**

In `seek/templates/searchAdvanced.html`, the form at line 124 posts
`retrieval_uids` as whitespace-delimited text. Replace the form's action with a
submit handler:

```html
<form id="ns_retrieval_form" onsubmit="event.preventDefault(); nsDownloadSamples(this.retrieval_uids.value.trim().split(/\s+/), {includeTree: true});">
```

Leave the form's existing inputs and submit button in place; drop the
`method="POST"` and `action="/seek/admin/retrieve/"` attributes.

- [ ] **Step 7: Repoint controls (b) and (c) — the search grids**

In `seek/templates/pages/searchAdvanced_stable.embed.html`, replace both
`downloadSamples` (line 2) and `downloadSamples0` (line 137) — each currently
dead after its unconditional `return` on the third line — and
`downloadSamples_new` (line 40) with two thin wrappers. `nsCollectSelectedUids`
comes from the shared script and already handles the anchor-markup `uid`:

```javascript
function downloadSamples(dg) {
    return nsDownloadSamples(nsCollectSelectedUids(dg), {includeTree: true});
}

function downloadSamples0(dg) {
    return nsDownloadSamples(nsCollectSelectedUids(dg), {includeTree: true});
}
```

Both keep `includeTree: true` — that is what control (c) does today, and the
spec keeps its behavior unchanged. Update the two `onclick` attributes to drop
the now-unused URL argument:

- line 371: `onclick="downloadSamples0($('#advanced_dgtable'))"`

In `seek/templates/pages/samples_stable.embed.html`, keep the live Yes/No prompt
and feed its answer to the helper. Replace `simple_downloadSamples` (line 39)
and `simple_downloadSamples_new` (line 77) with:

```javascript
function simple_downloadSamples(dg) {
    $.messager.confirm({
        title: 'Download Samples...',
        msg: 'Include all associated samples?',
        ok: 'Yes',
        cancel: 'No',
        fn: function (r) {
            nsDownloadSamples(nsCollectSelectedUids(dg), {includeTree: !!r});
        }
    });
}
```

and update line 381: `onclick="simple_downloadSamples($('#simple_dgtable'))"`.

- [ ] **Step 8: Repoint controls (d) and (e) — the newSearch grids**

In `seek/templates/newSearch.html`, replace `downloadSamples` (line 178) with:

```javascript
function downloadSamples(dg) {
    var selections = dg.datagrid("getSelections");
    return nsDownloadSamples(selections.map(function (s) { return String(s.id); }),
                             {includeTree: false});
}
```

Note these two grids differ from the `searchAdvanced.html` pair: they use
`getSelections()` rather than `getRows()` + `row.ck`, and their UID column is
named `uuid`, not `uid` (`pages/samples_new_stable.embed.html:39`). Keep sending
the numeric `s.id` — that is exactly what this function does today, and
`admin_retrieve_samples` resolves numeric SEEK ids to UUIDs at
`nextseek_api/views.py:639-663`. Do **not** substitute `nsCollectSelectedUids`
here; it reads `row.ck` and `row.uid`, neither of which these grids populate.

- [ ] **Step 9: Repoint control (f) — the new retrieval page**

In `seek/templates/pages/searchAdvanced_newretrieval.embed.html`, replace the
whole `retrieveSamples` function body (lines 9-40) with:

```javascript
	async function retrieveSamples(form) {
		const samples = new FormData(form).get("input_searchUIDs").trim().split(/\s+/)
		await nsDownloadSamples(samples, {includeTree: true, filename: "sample_retrieval.xlsx"})
	}
```

- [ ] **Step 10: Run the tests to verify they pass**

```bash
uv run pytest nextseek_api/tests/test_download_call_sites.py -q --no-migrations
```

Expected: 10 passed.

- [ ] **Step 11: Commit**

```bash
git add static/js/ns_sample_download.js seek/templates nextseek_api/tests/test_download_call_sites.py
git commit -m "feat(seek): route every sample-download control through one helper"
```

---

### Task 6: The workflow document

**Files:**
- Create: `docs/sample-download-workflow.md`

**Interfaces:**
- Consumes: the findings recorded in
  `docs/2026-08-06-unified-sample-download-readme-design.md` §"Current state".
- Produces: nothing code depends on.

- [ ] **Step 1: Write the document**

Create `docs/sample-download-workflow.md` covering, in this order:

1. **Before** — the "Live controls" and "Backends" tables copied from the design
   doc, so the pre-change state stays discoverable after the code is gone.
2. **Dead code found while mapping** — all four findings from the design doc:
   the unconditional `return` in `downloadSamples`/`downloadSamples0`, the
   commented-out `$.post` in `downloadSamples_new`, `attributeFilter` being
   empty at every live call site, and the orphaned templates
   (`searchAdvanced_rtable`, `searchAdvanced_tree`, `sampleSearch.html`,
   `sampleDeletion.html`, `.bk`).
3. **After** — one diagram in prose: control → `window.nsDownloadSamples` →
   `POST /nextseek_api/admin/samples/retrieve/` → `getChildrenUIDs` (when
   `include_tree`) or the project-scoped MySQL query → `write_samples_workbook`
   → README + sheet per sample type.
4. **What was deliberately left alone** — the Out of scope list from the design
   doc, each with its one-line reason.
5. **Seed gap** — `assay_context` and `projects_context` exist in production's
   `dmac` but are absent from `startup/seed/dmac.sql.gz`, the same gap
   `sample_types_context` had before Task 1.

- [ ] **Step 2: Verify every file:line reference in the doc still resolves**

```bash
grep -oE '[a-zA-Z0-9_/.]+\.(py|html|js):[0-9]+' docs/sample-download-workflow.md \
  | sort -u | while IFS=: read -r f l; do
      [ -f "$f" ] || { echo "MISSING FILE: $f"; continue; }
      lines=$(wc -l < "$f")
      [ "$l" -le "$lines" ] || echo "LINE OUT OF RANGE: $f:$l (file has $lines)"
    done
```

Expected: no output.

- [ ] **Step 3: Commit**

```bash
git add docs/sample-download-workflow.md
git commit -m "docs(seek): map the sample-download paths before and after unification"
```

---

### Task 7: End-to-end verification in the running stack

No rebuild: `docker cp` the changed files in, run `collectstatic`, restart. A
real deploy happens later via `./startup.sh rebuild` once the branch is ported.

**Files:** none modified. This task produces evidence, not code.

**Interfaces:**
- Consumes: everything from Tasks 1-6.
- Produces: a verified assertion that all seven controls download a workbook
  whose first sheet is a populated README.

- [ ] **Step 1: Confirm the context table is loaded**

```bash
docker exec seek-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e \
  "SELECT COUNT(*) FROM dmac.sample_types_context"'
```

Expected: `101`. If it is `0` or errors, re-run Task 1 Step 5.

- [ ] **Step 2: Push the branch code into the running container**

```bash
docker cp seek/models.py            nextseek:/app/seek/
docker cp seek/views.py             nextseek:/app/seek/
docker cp seek/dbtable_sample.py    nextseek:/app/seek/
docker cp seek/templates            nextseek:/app/seek/
docker cp nextseek_api             nextseek:/app/
docker cp static/js/ns_sample_download.js nextseek:/app/static/js/
docker compose exec nextseek uv run manage.py collectstatic --noinput
docker restart nextseek
```

- [ ] **Step 3: Confirm Django came back up**

```bash
docker logs nextseek 2>&1 | tail -30
```

Expected: gunicorn workers booted, no traceback. If there is an import error,
fix it before continuing — do not proceed to the UI.

- [ ] **Step 4: Verify the endpoint directly**

```bash
docker exec nextseek sh -c 'cd /app && uv run python - <<"PY"
import django, os
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.settings")
django.setup()
from nextseek_api.services.sample_workbook import load_sample_type_context
ctx = load_sample_type_context(["MUS", "TIS", "D.SEQ", "ZZZ"])
print("resolved:", sorted(ctx))
print("MUS name:", ctx.get("MUS", {}).get("name"))
PY'
```

Expected: `resolved:` lists the codes that exist (not `ZZZ`), and `MUS name: Mouse`.

- [ ] **Step 5: Exercise all seven controls in the browser**

Log in, then for each control confirm the file downloads, opens with `README` as
the first sheet, has the GitHub link live in A1, and lists every sample-type
sheet in the workbook with a name and description:

| Control | Where |
|---|---|
| a | `/seek/search/` → retrieval form |
| b | `/seek/search/` → simple tab → "Download samples" → answer **Yes**, then repeat and answer **No** |
| c | `/seek/search/` → advanced tab → "Download samples" |
| d | `/seek/newsearch/` → simple grid → "Download samples" |
| e | `/seek/newsearch/` → advanced grid → "Download samples" |
| f | `/seek/newsearch/` → retrieval form |
| g | any sample page → "Download All Samples" |

For control b, the **No** download must contain only the selected samples and
the **Yes** download must contain more — that is the `include_tree` flag working.

- [ ] **Step 6: Run the full affected test set once more**

```bash
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest nextseek_api/tests --no-migrations -q'
uv run pytest nextseek_api/tests/test_download_call_sites.py \
  nextseek_api/tests/test_sample_types_context_model.py -q --no-migrations
```

Expected: all pass, no regressions in the pre-existing `nextseek_api` suite.

- [ ] **Step 7: Record the result**

If every control passed, the branch is ready to port. If any control failed,
open a follow-up task with the control letter, the observed behavior, and the
relevant `docker logs nextseek` excerpt — do not mark this task complete.

---

## Self-Review

**Spec coverage.** Every section of the design doc maps to a task: §1 One API →
Task 4; §2 One workbook writer → Tasks 2 and 3; §3 Data source → Task 1; §4 Seed
→ Task 1 Steps 4-5; §5 Frontend → Task 5; §6 Workflow doc → Task 6; §7 Tests →
distributed across Tasks 1-5; §8 Verification → Task 7. The Out of scope list is
carried into Task 6 Step 1 item 4 so it survives in the repo.

**Placeholder scan.** No TBDs, no "add error handling", no "similar to Task N".
Every code step carries the actual code. Three facts that looked like open
questions were resolved against the tree before this plan was committed, and are
now stated rather than deferred: `Neo4jError` is imported at
`nextseek_api/views.py:21`; the `searchAdvanced.html` grids store `uid` as anchor
markup and need `nsExtractUid`; the `newSearch.html` grids use `getSelections()`
with a `uuid` column and keep sending numeric `s.id`. The only remaining
conditional is Task 3 Step 6's circular-import check, which carries both the
command and the fallback.

**Type consistency.** `write_samples_workbook(parsed_df, output_path, context_by_code=None)`
is defined in Task 2 and called with that signature in Task 3 and asserted with
that signature in Task 3's tests. `build_readme_rows(codes, context_by_code)`
and `load_sample_type_context(codes)` likewise. `window.nsDownloadSamples(identifiers, options)`
is defined in Task 5 Step 3 and called with that shape in Steps 5-9.
`Sample_types_context.tags` (Python) ↔ `Tags` (SQL) is stated in Task 1 and
asserted by its test.

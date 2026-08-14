# Unified sample download + README sheet

**Date:** 2026-08-06
**Branch:** `add-readme`
**Status:** approved, ready for planning

## Problem

Sample downloads reach the user through three live pages, seven controls and
three backends. The three backends produce different artifacts from overlapping
inputs, so any change to the downloaded workbook — such as adding a README —
has to be made three times or it silently applies to only some pages.

Separately, the endpoint the newest page calls is named `admin` and is gated
with `IsAdminUser`, but the gate does not mean what its name implies.

## Current state

### Live controls

**`/seek/search/` → `searchAdvanced.html`** (`seek/views.py:869`)

| | Control | Path |
|---|---|---|
| a | Retrieval form, `searchAdvanced.html:124` | `POST /seek/admin/retrieve/` |
| b | Simple tab grid, `samples_stable.embed.html:381` → `simple_downloadSamples` | Yes/No prompt is live. Yes → `/seek/admin/retrieve/`; No → `/seek/samples/download/` with `includeSampleTree=0` |
| c | Advanced tab grid, `searchAdvanced_stable.embed.html:371` → `downloadSamples0` | Always `/seek/admin/retrieve/` (see "Dead code" below) |

**`/seek/newsearch/` → `newSearch.html`** (`seek/views.py:1742`)

| | Control | Path |
|---|---|---|
| d | Simple grid, `samples_new_stable.embed.html:50` → `downloadSamples` (`newSearch.html:178`) | `POST /seek/samples/download/`, `includeSampleTree=0` |
| e | Advanced grid, `searchAdvanced_new_stable.embed.html:48` → same function | same |
| f | Retrieval form, `searchAdvanced_newretrieval.embed.html:15` | `POST /nextseek_api/admin/samples/retrieve/` |

**Sample detail page → `samples.embed.html`**

| | Control | Path |
|---|---|---|
| g | "Download All Samples" form, `samples.embed.html:51` | `POST /seek/admin/retrieve/` |

### Backends

- **`seek/views.py:1243 adminRetrieveSamples`** — `get_children_uids` expands the
  selection over Neo4j `DERIVED_FROM*0..` in both directions, fetches
  `json_metadata` from MySQL, `pd.json_normalize`s it, and writes one xlsx with
  a sheet per sample type via `sample_retrieval_data` (`seek/views.py:1232`).
  Not admin-gated: `verifySuperUser` only widens the project scope.
- **`nextseek_api/views.py:558 admin_retrieve_samples`** — the same shape, plus
  JSON output, numeric SEEK-id resolution, and a Neo4j-failure fallback.
  `permission_classes = [IsAuthenticated, IsAdminUser]`.
- **`seek/views.py:518 sampleDownload`** — the older path
  (`downloadSamples_new` / `downloadSamples_noTree`). Returns JSON carrying a
  link to a file on disk rather than the bytes. Emits a zip of per-type `.xls`
  when the selection spans sample types.

### Dead code found while mapping

The template source implies more behavior than actually runs:

1. **The advanced-search include-tree prompt never fires.** Both
   `downloadSamples` (`searchAdvanced_stable.embed.html:2`) and
   `downloadSamples0` (`:137`) set `includeSampleTree = 1` and then `return`
   unconditionally on their third line. Everything after — the
   `$.messager.confirm` prompt in the first, the whole legacy `$.post` to
   `/seek/samples/download/` in the second — is unreachable. Control (c) always
   fetches `/seek/admin/retrieve/` with the full tree.
2. **`downloadSamples_new` ignores its `url_download` argument.** The
   `$.post(url_download, …)` block at `searchAdvanced_stable.embed.html:112-120`
   is commented out; the function fetches `/seek/admin/retrieve/`
   unconditionally.
3. **`attributeFilter` is empty at every live call site.** It is a
   comma-separated header allowlist consumed at `dbtable_sample.py:2332`,
   `:4406` and `:4453`. `samples_stable.embed.html:107` hardcodes `''`;
   `searchAdvanced_stable.embed.html:67-69` computes `getTreeChecked()` into it
   and then discards it along with the commented-out POST.
4. **Orphaned templates.** Nothing includes
   `searchAdvanced_rtable.embed.html` (download button at `:28`) or
   `searchAdvanced_tree.embed.html` (`:26`). `sampleSearch.html`'s `render` is
   commented out at `seek/views.py:412` and the view redirects to
   `/seek/search/`; `sampleDeletion.html` has no view at all. Both of those
   include `samples_stable.embed.html`, so its grid is live only via
   `searchAdvanced.html`. `samples_stable.embed.html.bk` is a backup file.

Consequence: `/seek/samples/download/` is live in exactly three controls (b's
"No" branch, d, e), all with `includeSampleTree=0` and `attributeFilter=''`. The
unified API needs one new parameter, not two.

### The gate

DRF's `IsAdminUser` checks `is_staff`. `dmac/views.py:80` and `:97` set
`is_staff = 1` on every SEEK user at registration *and* at every login update,
so the "admin" endpoint already admits every SEEK-synced user — 11 of 20
accounts in the local seed.

Dropping the gate naively returns 404 rather than data. `nextseek_api/views.py:628`
reads `is_superuser or is_staff`; a non-staff caller takes the project-filtered
branch, and `user_project_ids` is always `[]` there. The comment at
`views.py:602-627` traces why: `SeekDB(None, None, None)` takes the
username-is-None branch (`seek/seekdb.py:31`) and never calls `getSeekLogin()`,
so `__server` is `None`, `getCurrentUser()` raises `TypeError`
(`seek/seekapi.py:188`), and the bare `except` swallows it. An empty project
list yields `ps.project_id IN ('')` → zero rows → 404.

## Design

### 1. One API

`POST /nextseek_api/admin/samples/retrieve/` becomes the single download API for
all seven live controls.

- `permission_classes = [IsAuthenticated]`.
- Resolve the caller's SEEK projects for real: construct `SeekDB` from the
  `basic_tuple` already resolved at `views.py:562`, so `getCurrentUser()` returns
  real projects instead of raising into the bare `except`. This is the
  prerequisite the existing comment names, and it is what `seek/views.py:1245`
  already does on the legacy path.
- Keep `is_superuser or is_staff` at `views.py:628`. Nobody who can download
  today loses access; non-staff authenticated users start receiving
  project-scoped results instead of a 404. Narrowing staff to their own projects
  is a separate, larger change — the assistant and container-CC consumers
  currently rely on unfiltered reads — and is explicitly out of scope here.
- Add `include_tree: bool = True` to `AdminSampleRetrieveRequest`. When `False`,
  skip the Neo4j `DERIVED_FROM*` expansion and fetch only the requested UIDs.
- `output_format: "json" | "excel"` is unchanged.
- `attributeFilter` is not ported. It is dead at every call site.

`seek/views.py:sampleDownload` and `seek/views.py:adminRetrieveSamples` are left
in place and are not deleted. They stop being reachable from the UI but remain
for any external caller.

### 2. One workbook writer

New module `nextseek_api/services/sample_workbook.py`:

```python
def write_samples_workbook(parsed_df, output_path): ...
```

It writes sheet 1 = README, then the existing sheet-per-sample-type output
(`groupby('sample_type')`, drop the `uuid`/`sample_type` helper columns, drop
all-empty columns). Both the DRF excel branch and
`seek/views.py:1232 sample_retrieval_data` call it, so the README cannot drift
between paths.

README sheet layout:

- **A1** — hyperlink to
  `https://github.com/BioMicroCenter/NExtSEEK/blob/main/chat_nextseek/src/chat_nextseek/context/sampletypes_db.json`
- **Row 2** — blank
- **Row 3** — header `Sample Type` | `Name` | `Description`
- **Rows 4+** — one row per sample type present in the workbook, sorted by code.

A sample type with no matching context row is still listed, with its code
filled and `Name` / `Description` blank. The README therefore always indexes
every sheet in the file, and a gap in the context table is visible rather than
silent.

### 3. Data source

The README's code / name / description come from `dmac.sample_types_context`.
New model in `seek/models.py`:

```python
class Sample_types_context(models.Model):
    _DATABASE = NEXTSEEK_DATABASE
    class Meta:
        db_table = "sample_types_context"
```

Columns as they exist on production: `id`, `sampletype_id`, `sample_type`,
`name`, `description`, `required_metadata`, `standard_metadata`,
`possible_metadata_fields`, `clade`, `sampletype_file_link`,
`associated_assay_parents`, `associated_assay_children`, `parent_sampletypes`,
`child_sampletypes`, `Tags`. Exact column types come from the production
`DESCRIBE` captured during the seed dump (task 2).

**The join key is the `sample_type` string code, not `sampletype_id`.**
`sample_retrieval_data` already derives that same code from the UID via
`str.extract(r'([A-Z]+\.[A-Z]+|[A-Z]+)')`, and `sampletype_id` does not agree
with `sample_types.id` across instances: production `sample_types_context` has
`sampletype_id` 10 = `MUS` and 13 = `D.FLOW`, while local
`seek_production.sample_types` has 10 = `DNA` and 13 = `AB`.

### 4. Seed

`sample_types_context` is absent from `startup/seed/dmac.sql.gz`, so the feature
would render an empty README on any fresh install. Dump the table from
production and fold it into the seed. This also makes the feature testable on a
developer machine.

`assay_context` and `projects_context` are missing from the seed for the same
reason. They are out of scope here; the workflow doc records the gap.

### 5. Frontend

One helper, `static/js/ns_sample_download.js`, exposing a single function that
POSTs `{identifiers, output_format: "excel", include_tree}` to the unified
endpoint and triggers the blob download. All seven live controls use it:

- Controls a, c, f, g pass `include_tree: true`.
- Control b passes the user's answer to its existing Yes/No prompt — the one
  prompt that genuinely works today.
- Controls d and e pass `include_tree: false`.

The dead include-tree prompt on the advanced-search tab (control c) is not
resurrected; control c keeps its current always-full-tree behavior. Reviving it
would be a UX change, not a refactor, and is out of scope.

The orphaned templates (`searchAdvanced_rtable.embed.html`,
`searchAdvanced_tree.embed.html`) are left untouched — nothing renders them.

`static/` changes are not picked up by a rebuild alone — `collectstatic` must
run afterwards.

### 6. Workflow doc

`docs/sample-download-workflow.md` records the tables and findings in the
"Current state" section above, plus the unified flow, so the next person does
not have to re-derive which controls, prompts and templates are dead.

### 7. Tests

- README builder: context row present; context row absent → code listed with
  blank name/description; row ordering; A1 hyperlink target.
- `include_tree=False` skips the Neo4j expansion and returns only the requested
  UIDs.
- A non-staff authenticated caller receives project-scoped rows, not a 404 —
  the specific regression the scoping fix exists to prevent.

Run in-container per the project convention:

```
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest <paths> --no-migrations -q'
```

### 8. Verification

Rebuild `nextseek`, run `collectstatic`, then exercise all seven controls across
the three live pages. Each download must open with README as sheet 1, correct
codes/names/descriptions, and a working A1 link.

## Out of scope

- Narrowing staff users to their own projects (dropping `is_staff` from
  `views.py:628`).
- Deleting `sampleDownload`, `adminRetrieveSamples`, the zip-of-xls path, or the
  orphaned templates.
- Reviving the dead include-tree prompt on the advanced-search tab.
- Seeding `assay_context` and `projects_context`.
- Porting `attributeFilter` to the unified API.

## Integration

Commits stay scoped and clean on `add-readme` so they can be cherry-picked onto
`main` after the branch is verified.

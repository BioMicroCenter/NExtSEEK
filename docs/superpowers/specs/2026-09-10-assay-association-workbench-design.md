# Assay association workbench — design

**Date:** 2026-09-10
**Status:** approved, ready for planning
**Scope:** Project A of a two-project split (see *Decomposition*)

## Problem

Internal assays are a controlled vocabulary that SEEK assays are mapped onto:
`dmac.internal_assays` holds the terms, `dmac.assays_internal_assays` joins each
`seek_production.assays` row to one of them. The mapping drives the
`internal_assay_title` shown on sample detail and search, and is stamped onto
`DERIVED_FROM` edges at batch-upload time.

The admin page that maintains this mapping (`seek/templates/internal_assays.html`,
added whole in `94f83f53`) has not kept pace with the data. On a current instance
52 assays are unmapped, and the page offers no way to find them: no filter, no
sort, no unmapped count, and titles truncate mid-word. Each row is then mapped
one combobox at a time even though the titles follow a near-mechanical
convention. Saves give no reliable confirmation.

`seek/templates/clades.html` has the identical shape — vocabulary grid,
association grid, sync button — and the identical defects.

### Observed defects

1. **No way to see what needs work.** The association grid renders every row with
   no filtering, count, or grouping. Finding the unmapped set means scrolling.
2. **No bulk path.** Dozens of rows, one combobox each, despite ~75% of titles
   resolving mechanically.
3. **Saves cannot report success.** Both `saveSelectedIntoDB` copies parse the
   response as JSON and read `obj.status`; every success path returns
   `HttpResponse({}, headers={"Refresh": 1})` — an empty body. Only the *error*
   paths return valid JSON, so the success dialog never fires.
4. **Mutations travel over `GET`** with the payload JSON-encoded in the query
   string, across all six save/delete endpoints.
5. **Stale vocabulary.** The association combobox is populated from the payload
   embedded at page render, so a newly added term is invisible until reload.
6. **Non-atomic batch writes.** `assayAssociationSave` loops per record with no
   transaction; a failure mid-batch leaves a partial write the client cannot
   detect.
7. **Duplicated client code.** Both templates define a `saveSelectedIntoDB` that
   shadows the shared one in `static/js/custom/datagrid-custom.js`.

## Goals

- Make the unmapped set visible, countable, and filterable.
- Make the mechanical majority acceptable in bulk, with the judgement cases
  clearly separated and explained.
- Make saves honest: real POSTs, real JSON, atomic batches, per-row errors.
- Do all of the above once, for both the assay and clade pages.

## Non-goals

- No change to how assays are created in SEEK.
- No new vocabulary terms, and no opinion on what the vocabulary should contain.
- No schema migration (see *Data model*).
- No automatic mapping at ingest, and no Neo4j back-fill — both are Project B.

## Decomposition

The original request covered four pain points. Three (**seeing**, **assigning**,
**trusting**) live in the admin page and its endpoints. The fourth
(**preventing**) lives in `nextseek_api/batch_upload/` and `neo4j_sync.py` —
different code, different blast radius, different tests.

They share one thing: the rule that maps an assay title to a vocabulary term is
the same rule whether it runs behind a button or unattended at ingest. That rule
is extracted as a standalone unit here, and Project B consumes it.

- **Project A (this spec)** — the workbench, plus the resolver.
- **Project B (later)** — auto-map at batch upload; re-sync Neo4j edges when a
  mapping changes after the fact. Today the graph copy of `internal_assay_title`
  goes stale on any later mapping change, since it is written only at upload.

## Architecture

Five units.

### 1. `dmac/vocab_resolver.py` — the resolver

Pure Python. No ORM, no HTTP, no Django import. Takes an upstream title, the
vocabulary, and the existing mappings; returns ranked candidates, each with a
tier and a human-readable basis.

It lives in `dmac/` beside `dbtable_internalassays.py` and the other domain
modules, so both `seek/` and `nextseek_api/` import it without a circular
dependency. Determinism is a requirement, not a nicety: identical inputs must
give identical output, ties broken by vocabulary id, so two curators always see
the same suggestion.

### 2. `static/js/custom/ns-vocab-workbench.js` — the workbench

Config-driven and domain-agnostic. Given a spec — entity label, vocabulary rows,
association rows, endpoint URLs, and whether a suggester exists — it renders the
tier groups, the counts, the inline evidence, and the bulk actions.

**The boundary test:** the resolver never imports Django, and the workbench
module file never contains the string `assay` — domain labels reach it only
through the config object each template passes in. If either fails, the boundary
is wrong.

### 3. Templates

`internal_assays.html` and `clades.html` reduce to a config object plus the
include. Both local `saveSelectedIntoDB` copies are deleted, which is most of why
this generalizes cleanly.

### 4. Views — `seek/views.py`

Six endpoints move to POST+JSON; one read-only suggestions endpoint is added.

### 5. Theme CSS

Tier styling, following the precedent set by `b966f9ae`.

## Resolver rules

Four tiers:

| Tier | Rule |
|---|---|
| `exact` | Strip disposition suffix, normalize, match a vocabulary title |
| `precedent` | Stripped title matches an already-mapped entity's stripped title; propose that term |
| `fuzzy` | Token overlap above threshold, against vocabulary titles and mapped prefixes |
| `none` | Nothing clears threshold |

**Normalization:** unicode dash variants to hyphen, punctuation to space,
collapse whitespace, casefold. This matters — some titles use an en dash rather
than a hyphen, which makes a naive prefix split silently miss them.

**Suffix families to strip:** ` - Metadata | Data Linked | Data Attached`, and
`: Training Data | Validation Data`.

**`none` is a visible state, not a skipped row.** Rows the resolver cannot judge
render in their own group with an explicit badge. A row the engine declined to
judge must never be indistinguishable from one nobody has looked at yet.

### The precedent guard

The precedent tier learns from existing mappings, and existing mappings contain
at least one clear error. A naive precedent engine would propagate that mistake
into every future assay of similar shape, at machine speed, with a confidence
badge attached. Therefore:

- every precedent suggestion carries its basis and supporting count;
- single-precedent matches (n=1) are demoted one tier;
- where precedents disagree the row becomes `conflict`: both candidates shown,
  nothing pre-selected.

### Pluggability

The suggester is optional. Clades ship with `suggester: null` — sample-type to
clade has no title convention to exploit — and the Suggested column simply does
not render. The workbench must not assume a suggester exists.

## UI

Tier-grouped worklist with inline evidence.

The association grid groups by tier, each group collapsible with a count and an
*Accept all N* action. Groups are ordered by descending confidence, so the
mechanical majority clears in one click and attention lands on the rest.

Clicking a row expands evidence **in place**, beneath it: the suggested term, the
basis, the supporting entities, and Accept / Choose other. This uses easyui's
native `view: detailview` and `expandRow` rather than custom code, and full-width
rows mean long titles stop truncating — one of the original complaints.

A docked right-hand pane was considered and rejected: it spends ~40% of the width
permanently, which truncates titles harder, and it is entirely hand-built.

## Endpoints

Bulk accept needs no new write endpoint — the association save already loops over
a list of records. That is precisely why the `GET` must go: accepting tens of
rows means tens of records of JSON in a query string.

| Endpoint | Change |
|---|---|
| `internal_assays/save` | GET → POST |
| `internal_assays/delete` | GET → POST |
| `internal_assays/assayAssociation/save` | GET → POST, atomic, per-row errors |
| `clade/save` | GET → POST |
| `clade/delete` | GET → POST |
| `clade/sampleTypes/save` | GET → POST, atomic, per-row errors |
| `admin/internal_assays/suggestions` | **new**, read-only |

Only the assay page gets a suggestions route, because only it ships a suggester.
If Clades later gains one, it adds its own route rather than parameterising this
one — the workbench treats the suggestions URL as opaque config.

**Response envelope:** `{status, msg, updated, errors[]}`. Retaining the
`status`/`msg` keys lets the shared client code read it nearly unchanged. The
`Refresh`-header pattern is removed.

**CSRF:** POST bodies are CSRF-protected. The templates already have
`{{ csrf_token }}` available. Superuser gating via `verifySuperUser` is unchanged
on every endpoint.

**Atomicity:** the record loop is wrapped in `transaction.atomic()`.

**Missing rows:** the association update fetches by `assay_id` and raises if the
row has since been removed — for example by an intervening Sync. Those become
collected per-row errors rather than a failed batch.

**Suggestions on demand:** the page fetches suggestions on load and re-fetches
after a vocabulary add, which also resolves the stale-combobox defect without a
page reload.

## Data model

**No migration.** The association tables keep `(id, entity_id, vocabulary_id)`.

*Accepted risk:* without a `source` column the resolver cannot distinguish a
human-curated mapping from one it suggested and had accepted. Accepted fuzzy
suggestions become precedents that strengthen future fuzzy suggestions of the
same shape — the engine citing its own past guesses back at the curator. The
mitigation is transparency rather than provenance: precedent requires n≥2, and
the evidence pane always names the specific entities it is leaning on, so a
self-citation is visible as one. Revisit if the vocabulary grows substantially.

## Error handling

**Degrade, never block.** If the suggestions endpoint fails, the page loads and
behaves exactly as it does today, with the Suggested column rendering
*unavailable*. This mirrors how `nextseek_api/cc_assistant/router.py` already
treats a BAML failure: a broken helper degrades its feature and never takes the
page down.

The resolver does not raise on malformed input; unparseable titles return tier
`none`.

## Testing

- **Resolver** — table-driven unit tests, no DB, no Django. Fixtures drawn from
  real data: en-dash titles, the `: Validation Data` family, punctuation variants
  that must normalize to the same term, and a deliberate precedent conflict.
- **Views** — Django test client: POST-only enforcement, CSRF, superuser gating,
  atomic rollback on a mid-batch failure, per-row error reporting.
  `seek/tests/test_admin_template_gating.py` already covers gating for both pages
  and extends rather than starts fresh.
- **Workbench JS** — no automated tests. There is no JS test infrastructure and
  the `Dockerfile` has no npm stage. The mitigation is to keep that layer thin:
  every decision is server-side, and the JS renders and posts. Stated here rather
  than left implicit.

Run per the project convention:

```
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest seek/tests dmac/tests --no-migrations -q'
```

## Rollout

This touches `static/`, so a plain rebuild will not serve the change.
`./startup.sh rebuild` runs `collectstatic` itself.

## Open questions

None blocking. Deferred by decision:

- Provenance columns (declined above; revisit if precedent self-citation becomes
  a practical problem).
- Whether Clades wants a suggester at all — it ships without one, and nothing in
  this design prevents adding one later.

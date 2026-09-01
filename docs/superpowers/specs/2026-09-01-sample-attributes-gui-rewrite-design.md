# Rewrite the Sample Attributes GUI

Date: 2026-09-01
Status: scoped for a new session. The data layer is done and proven; this is a
frontend-only rewrite.

## Goal

Replace the EasyUI datagrid at `/seek/samples/attributes/` with a purpose-built UI. The
server side needs no change: the page already runs entirely on the attributes API, and that
API is verified working on production.

**This is frontend-only.** No Python, no endpoints, no migrations. If the work starts
reaching into `nextseek_api/`, something has been misunderstood -- stop and re-read this.

## Why now

The current page was rewired onto the API on 2026-09-01 (commit 5970c479, error handling
019f8cb7) but its UI was deliberately left alone, so the risky half -- the data path -- could
be proven in isolation. It has been. What remains is cosmetic and structural, with no
server-side risk.

Two concrete reasons to rewrite rather than keep patching:

1. **EasyUI is throwing on every load.** `datagrid-filter.js:10` raises
   `TypeError: Cannot read properties of undefined (reading 'methods')` every time the page
   opens, and has for as long as anyone has looked. Dropping EasyUI on this page kills it.
2. **The grid fights the data.** The Type column's `field` is named
   `sample_attribute_type_title` but holds the NUMERIC type id once a row is edited, because
   the combobox uses `sample_attribute_type_id` as its `valueField`. Booleans arrive from the
   API as real booleans and the checkbox editors want `1`/`0`. Both are worked around today.
   A new UI should not inherit either.

## The contract the new UI codes against

All four calls are same-origin `fetch` with `credentials: 'same-origin'`. The attributes API
uses `CsrfExemptSessionAuthentication` (`nextseek_api/attributes/auth.py:15`), so **no CSRF
token and no credentials belong in the page**. Reads are open to any signed-in SEEK user;
mutations require a Django superuser and answer 403.

```
read    POST /nextseek_api/attributes/search/
        {"targets":[{"sample_type": <id>}]}
        -> {"attributes": [AttributeRecord, ...], "pagination": {...}}

create  POST  /nextseek_api/attributes/batch-create/
patch   PATCH /nextseek_api/attributes/batch-patch/
delete  POST  /nextseek_api/attributes/batch-delete/
        all accept {"targets":[...], "dry_run": bool}
```

`AttributeRecord` fields: `id, title, sample_type_id, sample_type_title,
sample_attribute_type_id, sample_attribute_type_title, required, pos, is_title, description,
unit_id, unit_title, unit_symbol, sample_controlled_vocab_id, sample_controlled_vocab_title,
linked_sample_type_id, linked_sample_type_title, created_at, updated_at`.

Identifiers (`sample_type`, `sample_attribute_type`, `unit`, `sample_controlled_vocab`,
`linked_sample_type`) each accept a database id, a numeric string, or an exact title.

A sample type never approaches the API's 500-row default page size, so one page is the whole
type. No pagination needed.

### Behaviour that must survive the rewrite

- **`dry_run` preview before every write.** Show what the server says it will do and let the
  operator confirm. This is not decoration: adding one attribute to a type with gappy
  positions renumbers every definition below the gap -- `BLD` on production emitted 68
  `position_changed` entries from a one-attribute request. Surface the `automatic_changes`
  count.
- **Errors come from two places.** Envelope-level failures (an unresolved sample type) arrive
  as top-level `errors[]`; per-target failures (a duplicate title, a bad type) arrive as
  `outcomes[].errors[]`. Read BOTH. Reading only the top level was a real bug -- the operator
  saw "HTTP 409" instead of "final title collides with an untouched sibling".
- **403 needs a human sentence.** Non-superusers can read but not write.

### Behaviour the operator relies on, handled server-side

Creating an attribute backfills that key as `""` into every sample of the type, and deleting
one removes the key. Verified on `D.FILE`: create -> `affected_samples: 1, updated_samples: 1`
and `$.MDTEST == ""`; delete -> key gone. The UI does not implement this and must not try to;
it should surface the `affected_samples` / `updated_samples` counts so the operator sees the
blast radius.

## What exists today (to be replaced)

```
seek/urls.py:32                          -> views.sampleAttributes
seek/views.py:764                        renders sampleAttributes.html, passes
                                         report.type_options and
                                         report.attribute_types_options
seek/templates/sampleAttributes.html     EasyUI tabs/layout shell
seek/templates/pages/
  samples_attributes.embed.html          sample-type combobox + getAttributes()   [CRLF]
  samples_atable.embed.html              the dg_atype datagrid + save/delete JS   [LF]
```

Both embed templates are included ONLY by `sampleAttributes.html`, so they can be replaced
freely. `getAttributes` also references `#sample_attribute`, which **does not exist on this
page** (`$('#sample_attribute').length === 0`) -- that branch is dead code, safe to drop.

Do NOT touch `static/js/custom/datagrid-custom.js`: it is shared with Clades and Internal
Assays, which still use it. The current page shadows two of its functions inline rather than
editing it, for exactly this reason.

The old `/seek/attributes/id=`, `/seek/attribute/save/` and `/seek/attribute/delete/` routes
still exist. This page no longer uses the last two; the first is still used by three search
templates. Leave all three alone.

## Open decisions for the implementing session

These are genuine choices, not omissions. Settle them with the operator before building:

1. **Chrome.** Stay inside the current NExtSEEK page frame, or become a standalone admin view?
2. **Look.** Match the newer pages, or follow whatever the house is standardising on?
3. **Framework.** Plain JS against the API is entirely sufficient here -- one table, four
   actions. Anything heavier needs a reason.
4. **Editing model.** Inline row editing like today, or a side panel / modal per attribute?
   The API is batch-capable, so multi-row edits are possible but not required.
5. **Whether the sample-type picker gains search.** There are ~101 sample types.

## Local workflow (no rebuild needed)

The fastest loop bind-mounts the worktree over `/app`, so template edits are live on refresh:

```
docker compose run --rm --no-deps -p 18000:8000 \
  -v <worktree>:/app -v /app/.venv \
  nextseek /app/.venv/bin/python manage.py runserver 0.0.0.0:8000 --noreload --insecure
```

`--insecure` serves static files. Run it from `code/dmac/docker/dev` (the compose dir);
`docker/*.env` is gitignored and only lives there. Then log in at `127.0.0.1:18000` as a local
superuser and drive the page. Cookies are per-host, not per-port, so a session from `:8000`
carries over.

`docker compose run` takes ~30-60s to create the container before Django even starts, and
nginx is not involved on port 18000.

## Verification

Drive the real page, not just the API. The four paths, all previously exercised this way:

- read -- select a type, rows match the API
- create -- preview, apply, row appears with a real id, DB confirms
- patch -- rename and toggle a flag, DB confirms both
- delete -- preview, apply, row and its json_metadata key both gone
- **rejection** -- rename an attribute to a title that already exists on the type; the page
  must block the write AND show the server's sentence
- **console must be clean** -- the whole point of dropping EasyUI

Clean up test attributes afterwards; `VIR` should return to 11.

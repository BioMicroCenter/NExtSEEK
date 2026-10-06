# Rollout: the `Contrast` attribute on A.GEX

The commit that adds one A.GEX row per nf-core/differentialabundance contrast fills a new
attribute, `Contrast` (the contrast id, e.g. `LPS_vs_Ctrl`). Code does not create it: no
`rebuild` or `install` step adds a SEEK attribute or writes the catalog. On each instance an
operator makes the two writes below, **before or together with** deploying that commit. Until
both exist, reingest treats `Contrast` as an unknown attribute and leaves it out of the
workbook.

Done so far: the local stack only.

## 1. Create the SEEK attribute

Use the native Attribute API as a Django superuser (`nextseek_api/attributes/`; admin means
`is_superuser`). Preview first; `dry_run: true` writes nothing.

```bash
curl -sS -X POST "$BASE_URL/nextseek_api/attributes/batch-create/" \
  -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  -d '{"targets":[{"sample_type":"A.GEX","attributes":[{"title":"Contrast","sample_attribute_type":"Text","required":false,"description":"Contrast id of a differential-abundance result, e.g. LPS_vs_Ctrl (target vs reference, as in the run'"'"'s contrasts file)."}]}],"dry_run":true}'
```

- **Check the preview:** exactly one `created`, on A.GEX. `affected_samples` is the instance's
  A.GEX count.
- **Then write:** repeat with `"dry_run":false`.
- **What changes:** the attribute is appended last, and every existing A.GEX sample gets a
  blank `Contrast` key in its `json_metadata`. Above 5000 affected samples the call returns 202
  and a job to poll.

## 2. Add `Contrast` to the catalog row

Reingest reads attribute lists from `dmac.sample_types_context`, not from SEEK's
`sample_attributes` (`nextseek_api/services/reingest_lookups.py` `attributes_for`).

Do **not** apply the full `scripts/context_gen.py --emit update` SQL for this. It rewrites every
sample-type row to match `context/`, which carries curation still behind the review gate in
`context/README.md`. Change only A.GEX's `possible_metadata_fields`, putting `Contrast` after
`Matrix` as `context/sample_types.json` does.

Check first, in the `dmac` database:

```sql
SELECT possible_metadata_fields FROM sample_types_context WHERE sample_type = 'A.GEX';
```

Then make the edit. It is a no-op if `Contrast` is already listed or if `Matrix` is not followed
by `MatrixDataType`; in that case edit the value by hand to the same effect.

```sql
UPDATE sample_types_context
SET possible_metadata_fields = REPLACE(possible_metadata_fields,
                                       'Matrix, MatrixDataType', 'Matrix, Contrast, MatrixDataType')
WHERE sample_type = 'A.GEX'
  AND FIND_IN_SET('Contrast', REPLACE(possible_metadata_fields, ', ', ',')) = 0;
```

## 3. Verify

Inside the app container:

```bash
docker exec -i nextseek sh -c 'cd /app && uv run --no-sync python manage.py shell -c "
from nextseek_api.services.reingest_lookups import attributes_for
from NessieAI.ns.reingest import proposals
print(any(a[\"title\"] == \"Contrast\" for a in attributes_for(\"A.GEX\")),
      proposals.attribute_exists(\"A.GEX\", \"Contrast\"))"'
```

Both values must be `True`.

The app refreshes its catalog exports from the table once a day. The cc-agent image bakes the
committed exports, which this commit already updated, so the deploy's rebuild covers it.

## Record

| Instance | Attribute created | Catalog row updated | By | Date |
|---|---|---|---|---|
| local stack | yes | yes | session operator | 2026-10-06 |
| dev | | | | |
| production | | | | |

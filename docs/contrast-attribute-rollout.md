# Rollout: the differential-abundance attributes on A.GEX

The nf-core/differentialabundance reingest rules (one A.GEX per contrast) fill six attributes
that A.GEX did not have:

| Attribute | Holds | Example |
|---|---|---|
| `Contrast` | the contrast id | `LPS_vs_Ctrl` |
| `ContrastVariable` | the samplesheet column compared | `Condition` |
| `ContrastReference` | the baseline group; a positive log2 fold change means higher in the target | `Ctrl` |
| `ContrastTarget` | the group compared against the reference | `LPS` |
| `Paramset` | the 2.0.0 paramset the result came from | `rnaseq_deseq2` |
| `MethodSettings` | the settings that shaped the result | `deseq2_test=Wald; deseq2_shrink_lfc=true; differential_max_qval=0.05` |

Code does not create them: no `rebuild` or `install` step adds a SEEK attribute or writes the
catalog. On each instance an operator makes the two writes below, **before or together with**
deploying the commit that uses them. Until both exist, reingest treats them as unknown
attributes and leaves them out of the workbook.

## 1. Create the SEEK attributes

Use the native Attribute API as a Django superuser (`nextseek_api/attributes/`; admin means
`is_superuser`). Preview first; `dry_run: true` writes nothing. Leave out any attribute the
instance already has (step 3's check lists them).

```bash
curl -sS -X POST "$BASE_URL/nextseek_api/attributes/batch-create/" \
  -H "Authorization: Token $TOKEN" -H "Content-Type: application/json" \
  -d '{"targets":[{"sample_type":"A.GEX","attributes":[
    {"title":"Contrast","sample_attribute_type":"Text","required":false,"description":"Contrast id of a differential-abundance result, e.g. LPS_vs_Ctrl."},
    {"title":"ContrastVariable","sample_attribute_type":"Text","required":false,"description":"Samplesheet column the contrast compares, e.g. Condition."},
    {"title":"ContrastReference","sample_attribute_type":"Text","required":false,"description":"Baseline group of the contrast; a positive log2 fold change means higher in the target."},
    {"title":"ContrastTarget","sample_attribute_type":"Text","required":false,"description":"Group compared against the reference, e.g. LPS."},
    {"title":"Paramset","sample_attribute_type":"Text","required":false,"description":"nf-core/differentialabundance 2.x paramset the result came from."},
    {"title":"MethodSettings","sample_attribute_type":"Text","required":false,"description":"Method, filtering and significance settings behind the result, as key=value pairs."}
  ]}],"dry_run":true}'
```

- **Check the preview:** one `created` per attribute sent, all on A.GEX. `affected_samples` is
  the instance's A.GEX count.
- **Then write:** repeat with `"dry_run":false`.
- **What changes:** the attributes are appended last, and every existing A.GEX sample gets a
  blank key for each in its `json_metadata`. Above 5000 affected samples the call returns 202 and
  a job to poll.

## 2. Add them to the catalog row

Reingest reads attribute lists from `dmac.sample_types_context`, not from SEEK's
`sample_attributes` (`nextseek_api/services/reingest_lookups.py` `attributes_for`).

Do **not** apply the full `scripts/context_gen.py --emit update` SQL for this. It rewrites every
sample-type row to match `context/`, which carries curation still behind the review gate in
`context/README.md`. Change only A.GEX's `possible_metadata_fields`, putting the six after
`Matrix` as `context/sample_types.json` does.

Check first, in the `dmac` database:

```sql
SELECT possible_metadata_fields FROM sample_types_context WHERE sample_type = 'A.GEX';
```

Then make the edit. The first statement serves an instance that has none of the six, the
second one that already has `Contrast`; each is a no-op otherwise. If neither matches (the field
list differs on that instance), edit the value by hand to the same effect.

```sql
UPDATE sample_types_context
SET possible_metadata_fields = REPLACE(possible_metadata_fields, 'Matrix, MatrixDataType',
  'Matrix, Contrast, ContrastVariable, ContrastReference, ContrastTarget, Paramset, MethodSettings, MatrixDataType')
WHERE sample_type = 'A.GEX'
  AND FIND_IN_SET('Contrast', REPLACE(possible_metadata_fields, ', ', ',')) = 0;

UPDATE sample_types_context
SET possible_metadata_fields = REPLACE(possible_metadata_fields, 'Matrix, Contrast, MatrixDataType',
  'Matrix, Contrast, ContrastVariable, ContrastReference, ContrastTarget, Paramset, MethodSettings, MatrixDataType')
WHERE sample_type = 'A.GEX'
  AND FIND_IN_SET('ContrastVariable', REPLACE(possible_metadata_fields, ', ', ',')) = 0;
```

## 3. Verify

Inside the app container:

```bash
docker exec -i nextseek sh -c 'cd /app && uv run --no-sync python manage.py shell -c "
from nextseek_api.services.reingest_lookups import attributes_for
from NessieAI.ns.reingest import proposals
names = (\"Contrast\", \"ContrastVariable\", \"ContrastReference\", \"ContrastTarget\", \"Paramset\", \"MethodSettings\")
have = {a[\"title\"] for a in attributes_for(\"A.GEX\")}
print({n: (n in have, proposals.attribute_exists(\"A.GEX\", n)) for n in names})"'
```

Every value must be `(True, True)`.

The app refreshes its catalog exports from the table once a day. The cc-agent image bakes the
committed exports, which the commit already updated, so the deploy's rebuild covers it.

## Record

| Instance | Attributes created | Catalog row updated | By | Date |
|---|---|---|---|---|
| local stack | all six | all six | session operator | 2026-10-06 |
| dev | | | | |
| production | | | | |

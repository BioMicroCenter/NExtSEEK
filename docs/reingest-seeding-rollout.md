# Rollout: what a box needs before reingest can run

Reingest (#135) is deployed on every box but refuses to file a single analysis row until the box
holds the house assays, sample types, attributes and catalog rows its maps name. No `install`,
`rebuild` or migration creates any of them. This runbook lists each one, as the code reads it,
and gives the read-only checks and the writes per box: local, then dev, then prod.

**Every write below waits for the operator's OK on the sign-off workbook, item by item and box by
box.** The dev writes happen only on that OK, followed by the first confirmed upload (§6). The prod
writes happen at a sitting, on the operator's word. A read-only check never needs a go, but it
also never turns into a write in the same sitting.

The A.GEX contrast attributes have their own runbook, [`contrast-attribute-rollout.md`](contrast-attribute-rollout.md);
this one points at it rather than repeating it.

## 1. What the code needs

### House assays

Every output rule names a house assay by title (`assay` in `NessieAI/ns/reingest_maps/*.outputs.json`).
At build time `nextseek_api.services.reingest_lookups.house_assay_title_strict` turns that name into
an `internal_assays` title, and `NessieAI.ns.reingest.assay_plan.plan_row` blocks the row with the
hard finding `HOUSE_ASSAY_MISSING` when it gets none. At upload, `create_study_assay` in
`nextseek_api/services/reingest_assays.py` looks the title up again (`internal_assay_id_strict`) and
refuses with "there is no house assay" before it creates a study assay.

How a lookup passes:

- `dmac.internal_assays` holds a row whose `internal_assay_title` equals the name byte for byte
  (the lookup compares in Python; MySQL's own comparison ignores case), **or**
- a `dmac.assay_context` row lists the name in `assay_name` or the comma-separated
  `Alternative_Assay_Names`, and exactly one name of that row's group is an `internal_assays` title.
- Two titles in one group raise `HOUSE_ASSAY_AMBIGUOUS`, also hard. `assay_context.internal_assay_id`
  is never read: its ids are production's.

| Map name | Maps (rule) | Must resolve to | Child type |
|---|---|---|---|
| `Gene Expression Analysis` | rnaseq, riboseq, rnasplice, smrnaseq, denovotranscript, differentialabundance | `internal_assays` "Gene Expression Analysis" | A.GEX |
| `Genome Alignment` | rnaseq, riboseq, rnasplice, rnafusion, rnavar | "Genome Alignment" | A.ALN |
| `HLA Typing` | hlatyping | "HLA Typing" (new in #135) | A.HLA |
| `Variant Call Filtering` | rnavar (`vcf`) | "Variant Call Filtering" (a pending `create_internal` in `context/assay_mappings.json`) | A.VCF |
| `Single Cell Expression Matrix Analysis` | scrnaseq | "Single Cell Expression Analysis", through that row's alternative name in `assay_context` | A.SCXP |

Ambiguity risks the check must rule out: `Gene Expression Analysis` lists `Gene Expression Validation Assay`
as an alternative, and `context/assay_mappings.json` still carries the `merge_internal` of that
internal assay into it. A box whose `internal_assays` holds both titles blocks every A.GEX row
until the merge is applied (with its `remap`) or the alias is removed. The same holds for
`HLA Genotyping`, `Variant Calling Analysis` and `Variant Calling` if any is an `internal_assays` title.

#135 also added five house assays that no committed map names: `Gene Fusion Detection`,
`Differential Splicing Analysis`, `Ribosome Profiling Analysis`, `Transcriptome Assembly` and
`miRNA Discovery Analysis` (rows of `context/assays.json` with production's ids, no
`create_internal` entry). Reingest runs without them. They matter to the catalog: once
`context/assays.json` holds them, `scripts/context_gen.py --emit update --table all` refuses on a box
where any of them is not an `internal_assays` title (its check that every curated title is held by
exactly one internal assay). Seeding them is the operator's call (item `A-OPT` in the workbook).

### Sample types and attributes

Two stores answer "does this type have this attribute", and reingest reads both:

- **SEEK's `sample_attributes`** decides the upload. Batch upload rejects a row whose
  `json_metadata` has any key the type lacks (`nextseek_api/batch_upload/transform.py`, step 4). In
  new mode every rule attribute reaches `json_metadata`, so a missing SEEK attribute fails every
  row of that rule.
- **The catalog row, `dmac.sample_types_context`**, read through `reingest_lookups.known_sample_types`
  and `attributes_for`. A type with no row is the hard finding `UNKNOWN_SAMPLETYPE`; a D.SEQ QC
  attribute the row does not list is parked into `Notes` (soft, `ATTRIBUTE_NOT_DEFINED`); a row
  default (`Name`, `Scientist`, `Lab`, `SampleCreationDate`) the row does not list is dropped.

What the committed maps write, and what `context/sample_types.json` (the catalog source of truth)
lacks today:

| Type | Needed by | Missing from `context/sample_types.json` | SEEK |
|---|---|---|---|
| A.HLA | hlatyping `hla_class_i` | the whole row | the whole type (28 attributes, list in the workbook) |
| D.SEQ | `qc_attributes` of every map | 18: DuplicationPercent, Strandedness, MultimappedPercent, DupRadarIntercept, MismatchPercent, GCPercent, Q30Percent, AdapterPercent, PassedFilterPercent, AssignedPercent, MRNAPercent, PseudoalignedPercent, IsomiRPercent, UMIUniquePercent, EstimatedCells, MeanReadsPerCell, ReadsInCellsPercent, ValidBarcodesPercent | the same 18 |
| A.VCF | rnavar `vcf` (an output attribute, not a QC entry) | TsTvRatio | TsTvRatio |
| A.ALN | rnaseq `aln` writes `Software` | Software | to be read; without it every rnaseq A.ALN row fails at upload |
| A.GEX | differentialabundance | none (Contrast, ContrastVariable, ContrastReference, ContrastTarget, Paramset, MethodSettings are in the file) | the six; [`contrast-attribute-rollout.md`](contrast-attribute-rollout.md) |

`NessieAI/tests/ns/fixtures/sampletype_attributes.json`, the snapshot CI checks the maps against,
already lists all of these (its `hand_edits` block says so), which is why CI is green while the boxes
are not ready.

Dataset-level meanings for the new attributes (the download workbook's README sheet) are rows of
`startup/seed/sql/sample_attributes_unique_data.sql`, a file no install step applies. They are
optional for reingest.

### What Nessie reads

The app rewrites its catalog exports (`sampletypes_db.json`, `assays_db.json`) from
`sample_types_context` and `assay_context` once a day, and reads each box's assay list from its own
`internal_assays`. The cc-agent image bakes `min_sampletypes_db.json` from the checkout, and that file
has no A.HLA until `context/sample_types.json` does. So the catalog rows above are also what Nessie
sees, and the repo change in §2 is what the cc-agent sees.

## 2. Before any box: the repo change

`context/README.md`: "Edit these files; do not edit the database tables". A hand SQL edit that the
source does not carry is undone by the next `--emit update`, which deletes every
`sample_types_context` row the file does not name (A.HLA) and rewrites every column of the rest
(the 18 D.SEQ attributes). So before the first box write, a reviewed change must:

1. add the A.HLA row and the 20 attributes above (18 D.SEQ, TsTvRatio, A.ALN Software if kept) to
   `context/sample_types.json`;
2. run `python scripts/context_gen.py --emit exports --table all` and `--emit seed --table all` in the
   same change, and the test lanes `context/README.md` names;
3. after merge, rebuild the app and the cc-agent on each box as `DEPLOYMENT.md` §3.2 says for the
   context files.

The box writes in §4 then make each table match that file for these rows only. Do **not** apply the
full `--emit update` SQL for this rollout: it rewrites every row to match `context/`, which carries
curation still behind the review gate `context/README.md` describes.

## 3. Shell helpers used below

Run on the box, from the deploy clone. `dmac_sql` and `seek_sql` log in the way startup does
(`MYSQL_ROOT_CMD` in `startup/lib/docker_ops.py`), so no password is typed or echoed. `seek_sql` needs
SEEK's database name, `MYSQL_DATABASE` in `docker/db.env`.

```bash
dmac_sql() { docker compose exec -T db sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot --default-character-set=utf8mb4 -t dmac'; }
seek_sql() { docker compose exec -T db sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysql -uroot --default-character-set=utf8mb4 -t "$MYSQL_DATABASE"'; }
app_shell() { docker compose exec -T nextseek sh -c 'cd /app && uv run --no-sync python manage.py shell'; }
```

API calls use a superuser token, as in `contrast-attribute-rollout.md`: `$BASE_URL` is the box's
NExtSEEK URL and `$TOKEN` a superuser's API token, both from the operator, never written down.

## 4. Per box

Order on every box: 4.1 checks, 4.2 backup, 4.3 house assays, 4.4 assay catalog rows, 4.5 graph,
4.6 A.HLA in SEEK, 4.7 SEEK attributes, 4.8 catalog rows, 4.9 meanings (optional), 4.10 verify.
Each write step starts with "skip what 4.1 showed present". Record each box's results in the
table at the end.

| Box | When | Notes |
|---|---|---|
| local | first, as the rehearsal | the operator's OK still applies; earlier sessions created some of these by hand, so 4.1 decides |
| dev | on the operator's OK, after local | then the acceptance upload, §6 |
| prod | at a sitting, on the operator's word, after §6 passed on dev | no acceptance upload unless the operator asks for one |

### 4.1 Read-only checks (no go needed)

House assays and their catalog rows:

```bash
dmac_sql <<'SQL'
SELECT id, internal_assay_title FROM internal_assays WHERE internal_assay_title IN
 ('Gene Expression Analysis','Gene Expression Validation Assay','Genome Alignment','HLA Typing','HLA Genotyping',
  'Variant Call Filtering','Variant Calling Analysis','Variant Calling','Single Cell Expression Analysis',
  'Single Cell Expression Matrix Analysis','Gene Fusion Detection','Differential Splicing Analysis',
  'Ribosome Profiling Analysis','Transcriptome Assembly','miRNA Discovery Analysis')
 ORDER BY internal_assay_title, id;
SELECT id, assay_name, Alternative_Assay_Names, internal_assay_id FROM assay_context WHERE assay_name IN
 ('Gene Expression Analysis','Genome Alignment','HLA Typing','Variant Call Filtering','Single Cell Expression Analysis',
  'Gene Fusion Detection','Differential Splicing Analysis','Ribosome Profiling Analysis','Transcriptome Assembly',
  'miRNA Discovery Analysis') ORDER BY assay_name;
SELECT MAX(id) AS max_internal_assay FROM internal_assays;
SELECT MAX(id) AS max_assay_context FROM assay_context;
SELECT sample_type, sampletype_id FROM sample_types_context WHERE sample_type IN ('A.HLA','D.SEQ','A.VCF','A.ALN','A.GEX','A.SCXP');
SQL
```

Then the check that asks the code itself: every house name the maps use, and every attribute they
write, against this box's catalog and SEEK. It reads only.

```bash
app_shell <<'PY'
from NessieAI.ns.reingest import maps
from nextseek_api.services import reingest_lookups as L
from seek.models import Sample_attributes, Sample_types
want, names = {}, set()
for pipeline in maps.available():
    m = maps.load(pipeline)
    for r in list(m.outputs) + [r for o in m.versions for r in o.outputs]:
        names.add(r.assay)
        s = want.setdefault(r.sample_type, set())
        s.update(r.attributes)
        if r.include_provenance:
            s.update(m.provenance_attributes)
        if r.primary_data:
            s.update(("File_PrimaryData", "Link_PrimaryData", "Checksum_PrimaryData"))
    for a, q in m.qc_attributes.items():
        want.setdefault(q.target, set()).add(a)
for n in sorted(names):
    try:
        print("assay", repr(n), "->", L.house_assay_title_strict(n))
    except ValueError as exc:
        print("assay", repr(n), "-> AMBIGUOUS", exc)
known = L.known_sample_types()
for t in sorted(want):
    catalog = {a["title"] for a in L.attributes_for(t)}
    ids = list(Sample_types.objects.filter(title=t).values_list("id", flat=True))
    seek = set(Sample_attributes.objects.filter(sample_type_id__in=ids).values_list("title", flat=True))
    print(t, "catalog row" if t in known else "NO CATALOG ROW", "| SEEK ids", ids or "NONE")
    print("  not in catalog:", sorted(want[t] - catalog))
    print("  not in SEEK:   ", sorted(want[t] - seek))
PY
```

Ready means: five `assay` lines each ending in a title (none `None`, none `AMBIGUOUS`), and for
every type a catalog row, a SEEK id and two empty lists. Anything else is a row of the workbook to
write. Also read the SEEK value type of a sibling attribute, to give the new ones the same type:

```bash
seek_sql <<'SQL'
SELECT st.title AS type, sa.title AS attribute, sat.title AS value_type, sa.required, sa.pos
  FROM sample_attributes sa JOIN sample_types st ON st.id = sa.sample_type_id
  JOIN sample_attribute_types sat ON sat.id = sa.sample_attribute_type_id
 WHERE (st.title = 'D.SEQ' AND sa.title IN ('MappedPercent','Genome'))
    OR (st.title IN ('A.VCF','A.HLA','A.ALN') AND sa.title IN ('Software','Pipeline','FilterMethod'))
 ORDER BY st.title, sa.pos;
SQL
```

### 4.2 Backup (before the first write on a box)

The `DEPLOYMENT.md` §5.3 pattern, for the five `dmac` tables this runbook touches:

```bash
docker compose exec -T db sh -c 'MYSQL_PWD="$MYSQL_ROOT_PASSWORD" exec mysqldump -uroot dmac internal_assays assays_internal_assays assay_context sample_types_context sample_attributes_unique' \
  > backup-reingest-seeding-dmac-$(date +%Y%m%d).sql
chmod 600 backup-reingest-seeding-dmac-*.sql
```

Leave out a table the box does not have (4.1 shows which); keep the file outside any checkout. SEEK-side writes (4.6, 4.7) go through SEEK and are rolled back
through it, not from this dump.

### 4.3 House assays in `internal_assays` (gated)

Only the titles 4.1 showed absent. `HLA Typing` and `Variant Call Filtering` are what the maps need;
the five others only if the operator ruled `A-OPT` in. The guard compares exactly, as the code does,
so a second run inserts nothing.

```bash
dmac_sql <<'SQL'
SET @before := (SELECT COALESCE(MAX(id), 0) FROM internal_assays);
INSERT INTO internal_assays (internal_assay_title)
SELECT t.title FROM (
  SELECT 'HLA Typing' AS title UNION ALL SELECT 'Variant Call Filtering'
  -- A-OPT, only if ruled in:
  -- UNION ALL SELECT 'Gene Fusion Detection' UNION ALL SELECT 'Differential Splicing Analysis'
  -- UNION ALL SELECT 'Ribosome Profiling Analysis' UNION ALL SELECT 'Transcriptome Assembly'
  -- UNION ALL SELECT 'miRNA Discovery Analysis'
) t
WHERE NOT EXISTS (SELECT 1 FROM internal_assays ia
                   WHERE ia.internal_assay_title = CONVERT(t.title USING utf8mb4) COLLATE utf8mb4_bin);
SELECT id, internal_assay_title FROM internal_assays WHERE id > @before;
SQL
```

A box that lacks `Gene Expression Analysis`, `Genome Alignment` or `Single Cell Expression Analysis`
is a box whose assay catalog differs from production's; stop and bring the operator the 4.1 output
rather than adding them here.

If 4.1 showed an ambiguity (`AMBIGUOUS` for `Gene Expression Analysis`), stop: the fix is either the
`remap` of SEEK assay 323 plus the `merge_internal` of `Gene Expression Validation Assay` from
`context/assay_mappings.json`, or removing that alternative name from the catalog row. That is the
operator's ruling, not a step of this runbook.

- **Verify:** rerun the `app_shell` check; the `HLA Typing` and `Variant Call Filtering` lines now end in
  their titles.
- **Roll back:** delete only what this step added, and only while no SEEK assay is tagged with it:
  ```sql
  DELETE FROM internal_assays WHERE id = <new id> AND internal_assay_title = '<title>'
    AND NOT EXISTS (SELECT 1 FROM assays_internal_assays WHERE internal_assay_id = <new id>);
  ```

### 4.4 Assay catalog rows in `assay_context` (gated)

For each of `HLA Typing`, `Variant Call Filtering` (and the five `A-OPT` names) that 4.1 showed
absent, take its INSERT from the held curated seed and run it. The INSERT is not guarded, which is
why only absent names go in.

```bash
for name in 'HLA Typing' 'Variant Call Filtering'; do
  grep -F "VALUES ('$name', " startup/seed/sql/assay_context.curated.sql
done > /tmp/reingest-assay-context.sql
wc -l /tmp/reingest-assay-context.sql            # one line per name, no more
{ echo 'SET NAMES utf8mb4;'; cat /tmp/reingest-assay-context.sql; } | dmac_sql
```

Then link those rows by title, as `context_gen` does (`_RELINK_ASSAYS`), because the seed carries
production's ids:

```bash
dmac_sql <<'SQL'
UPDATE assay_context SET internal_assay_id = (SELECT MIN(ia.id) FROM internal_assays ia
  WHERE ia.internal_assay_title = CONVERT(assay_context.assay_name USING utf8mb4) COLLATE utf8mb4_bin)
 WHERE assay_name IN ('HLA Typing','Variant Call Filtering');
SELECT assay_name, Alternative_Assay_Names, internal_assay_id FROM assay_context
 WHERE assay_name IN ('HLA Typing','Variant Call Filtering');
SQL
```

Also confirm the `Single Cell Expression Analysis` row lists `Single Cell Expression Matrix Analysis`
as an alternative name (4.1's second query); without it scrnaseq rows block.

- **Verify:** both rows present with a non-null `internal_assay_id` equal to 4.3's ids; the `app_shell`
  check unchanged or better.
- **Roll back:** `DELETE FROM assay_context WHERE id > <4.1's max_assay_context> AND assay_name IN (...)`.

### 4.5 Graph (gated)

A SQL insert into `internal_assays` queues no graph work, so the new house assays reach the graph's
Assay nodes at the next nightly reconcile. To have them now:

```bash
docker compose exec -T nextseek sh -c 'cd /app && uv run --no-sync python manage.py graph_sync --catalog --dry-run --i-mean-the-live-graph'
docker compose exec -T nextseek sh -c 'cd /app && uv run --no-sync python manage.py graph_sync --catalog --i-mean-the-live-graph'
```

- **Verify:** the dry run lists the new Assay nodes and nothing unexpected; the written run exits 0.
- **Roll back:** none needed by hand; after 4.3's rollback the next reconcile removes them.

### 4.6 The A.HLA sample type in SEEK (gated)

Only if 4.1 printed `SEEK ids NONE` for A.HLA. `POST /nextseek_api/sample_types/`
(`SampleTypeProxyViewSet.create`, `SampleTypeCreateRequest` in `nextseek_api/models.py`) creates it in
SEEK as the caller and refuses a title that exists (409).

The 28 attributes are the workbook's A.HLA list; their descriptions are the
`sample_attributes_unique_data.sql` meanings. The code does not fix the rest, so the owner fills in,
from a box that already holds A.HLA (`GET /nextseek_api/sample_types/A.HLA/` there) or by decision:
each attribute's value type, `required`, `is_title` and position, the type's description, and the
`projects` relationship for this box.

```bash
curl -sS -X POST "$BASE_URL/nextseek_api/sample_types/" -H "Authorization: Token $TOKEN" \
  -H "Content-Type: application/json" --data @a-hla-create.json
```

- **Verify:** 201 (a 202 means SEEK did not answer in time: look the type up before sending again);
  the `app_shell` check prints a SEEK id for A.HLA and an empty "not in SEEK" list.
- **Roll back:** delete the type in SEEK's own interface while it holds no samples. The proxy has no
  delete route.

### 4.7 SEEK attributes (gated)

The 18 D.SEQ attributes and TsTvRatio on A.VCF, minus what 4.1 showed present; A.ALN `Software` only
if the operator keeps it in the rnaseq map (otherwise the map change removes it); the A.GEX six per
[`contrast-attribute-rollout.md`](contrast-attribute-rollout.md) §1. Use the Attribute API,
`dry_run` first. Build the body from the committed meanings, with the value types 4.1's last query
showed (the maps' `datatype` is `number` for all but `Strandedness`, which is `string`):

```bash
NUM='<value type of MappedPercent on this box>' TEXT='<value type of Genome on this box>' python3 - > /tmp/reingest-attrs.json <<'PY'
import json, os, re
seed = open("startup/seed/sql/sample_attributes_unique_data.sql", encoding="utf-8").read()
def meaning(name):
    m = re.search(r"^ \('%s','','((?:[^']|'')*)'\)" % re.escape(name), seed, re.M)
    return m.group(1).replace("''", "'")
num, text = os.environ["NUM"], os.environ["TEXT"]
dseq = ["DuplicationPercent", "Strandedness", "MultimappedPercent", "DupRadarIntercept", "MismatchPercent",
        "GCPercent", "Q30Percent", "AdapterPercent", "PassedFilterPercent", "AssignedPercent", "MRNAPercent",
        "PseudoalignedPercent", "IsomiRPercent", "UMIUniquePercent", "EstimatedCells", "MeanReadsPerCell",
        "ReadsInCellsPercent", "ValidBarcodesPercent"]
def attr(name, kind):
    return {"title": name, "sample_attribute_type": kind, "required": False, "description": meaning(name)}
body = {"dry_run": True, "targets": [
    {"sample_type": "D.SEQ", "attributes": [attr(n, text if n == "Strandedness" else num) for n in dseq]},
    {"sample_type": "A.VCF", "attributes": [attr("TsTvRatio", num)]}]}
print(json.dumps(body, indent=1))
PY
```

Delete from the file every attribute 4.1 showed present, then:

```bash
curl -sS -X POST "$BASE_URL/nextseek_api/attributes/batch-create/" -H "Authorization: Token $TOKEN" \
  -H "Content-Type: application/json" --data @/tmp/reingest-attrs.json
```

- **Check the preview:** one `created` per attribute sent, on D.SEQ and A.VCF only; `affected_samples`
  is the box's D.SEQ and A.VCF sample counts. Then send the same body with `"dry_run": false`. Above 5000
  affected samples the answer is 202 and a job to poll at its `status_url`.
- **Verify:** the `app_shell` check's "not in SEEK" lists are empty.
- **Roll back:** `POST /nextseek_api/attributes/batch-delete/` with the same titles, `dry_run` first,
  and only while no sample holds a value for them (it rewrites every sample's `json_metadata`).

### 4.8 Catalog rows in `sample_types_context` (gated)

Each statement matches the §2 change to `context/sample_types.json`; apply it after that change is
merged, so the file and the table say the same thing.

Append each missing attribute to its row's `possible_metadata_fields`. Each statement is a no-op
when the name is already in any of the three lists:

```bash
add() { printf "UPDATE sample_types_context SET possible_metadata_fields = CONCAT_WS(', ', possible_metadata_fields, '%s') WHERE sample_type = '%s' AND FIND_IN_SET('%s', REPLACE(CONCAT_WS(',', required_metadata, standard_metadata, possible_metadata_fields), ' ', '')) = 0;\n" "$2" "$1" "$2"; }
{ for a in DuplicationPercent Strandedness MultimappedPercent DupRadarIntercept MismatchPercent GCPercent \
           Q30Percent AdapterPercent PassedFilterPercent AssignedPercent MRNAPercent PseudoalignedPercent \
           IsomiRPercent UMIUniquePercent EstimatedCells MeanReadsPerCell ReadsInCellsPercent ValidBarcodesPercent; do
    add D.SEQ "$a"; done
  add A.VCF TsTvRatio
  # add A.ALN Software      # only if kept, as in 4.7
} | dmac_sql
```

Insert the A.HLA row if 4.1 showed none. `sampletype_id` is this box's SEEK id from 4.6. The three
lists must name every SEEK attribute of the type exactly once (`context/README.md` "Conventions");
the owner confirms the split and fills the prose:

```sql
INSERT INTO sample_types_context (sample_type, sampletype_id, name, clade, description, Tags,
  required_metadata, standard_metadata, possible_metadata_fields, parent_sampletypes, child_sampletypes,
  associated_assay_parents, associated_assay_children)
SELECT 'A.HLA', <SEEK id>, '<name>', 'Analyzed', '<description>', '<tags>',
  'UID, File_PrimaryData, Link_PrimaryData, Checksum_PrimaryData, Scientist, Parent',
  'Software, Pipeline, PipelineVersion, HLA_A1, HLA_A2, HLA_B1, HLA_B2, HLA_C1, HLA_C2, HLADatabaseVersion',
  'Name, SampleCreationDate, Lab, Protocol, HLAReads, HLATypingObjective, Notes, File_SecondaryData, Link_SecondaryData, Checksum_SecondaryData, Checksum_PrimaryType, Publish_uri',
  'D.SEQ, A.ALN', NULL, 'HLA Typing', NULL
FROM DUAL WHERE NOT EXISTS (SELECT 1 FROM sample_types_context WHERE sample_type = 'A.HLA');
```

A.GEX: [`contrast-attribute-rollout.md`](contrast-attribute-rollout.md) §2.

- **Verify:** the `app_shell` check's "not in catalog" lists are empty and A.HLA prints "catalog row".
- **Roll back:** restore the changed columns from 4.2's dump, or
  `DELETE FROM sample_types_context WHERE sample_type = 'A.HLA'` for the inserted row.

### 4.9 Attribute meanings (optional, gated)

Not needed for reingest. Puts the committed meanings of the 19 QC attributes and the 9 HLA fields into
`sample_attributes_unique`, replacing older text for the same field (the `DupRadarIntercept` fix):

```bash
NAMES='DuplicationPercent|Strandedness|MultimappedPercent|DupRadarIntercept|MismatchPercent|GCPercent|Q30Percent|AdapterPercent|PassedFilterPercent|AssignedPercent|MRNAPercent|PseudoalignedPercent|IsomiRPercent|UMIUniquePercent|EstimatedCells|MeanReadsPerCell|ReadsInCellsPercent|ValidBarcodesPercent|TsTvRatio|HLA_A1|HLA_A2|HLA_B1|HLA_B2|HLA_C1|HLA_C2|HLAReads|HLATypingObjective|HLADatabaseVersion'
{ echo 'SET NAMES utf8mb4;'
  echo 'INSERT INTO sample_attributes_unique (field_name, sample_type, meaning) VALUES'
  grep -E "^ \('($NAMES)','" startup/seed/sql/sample_attributes_unique_data.sql | sed 's/[,;]$//' | paste -sd, -
  echo 'ON DUPLICATE KEY UPDATE meaning = VALUES(meaning);'
} | dmac_sql
```

- **Verify:** `SELECT field_name, LEFT(meaning, 60) FROM sample_attributes_unique WHERE field_name IN (...)`
  returns 28 rows.
- **Roll back:** from 4.2's dump.

### 4.10 Verify the box

Rerun 4.1's `app_shell` check: five resolved assays, every type with a catalog row and a SEEK id, all
lists empty. Then, as for any catalog change, wait for (or trigger) the app's daily context refresh and
confirm the box's Nessie answers "what does an A.HLA sample hold" from the new row.

## 5. Order across boxes

1. §2 repo change, reviewed and merged; app and cc-agent rebuilt on the box being seeded.
2. local: 4.1 to 4.10, then a rehearsal of §6 if a Nessie-launched run exists there.
3. dev: the same, on the operator's OK, then §6.
4. prod: the same at a sitting, on the operator's word, after §6 passed on dev.

## 6. Acceptance: the first confirmed upload on dev

Nothing has yet run `upload-reingest` through a confirmed upload that creates a study assay and
registers parents. On dev, after §4:

1. **Pick the run (owner fills).** A Nessie-launched run (it has a `PipelineRun`; `run-harvest`
   refuses any other) whose parents sit in a study of a project the uploading superuser belongs to,
   and whose study has no assay of the house type yet, so the create path runs. An rnaseq run covers
   Genome Alignment, Gene Expression Analysis and the D.SEQ update in one go; an hlatyping run is the
   only one that exercises A.HLA.
2. **Read-only first.** Back up the parents' current D.SEQ `json_metadata` (a `SELECT uuid,
   json_metadata FROM samples WHERE uuid IN (...)` on SEEK's database into a 600-mode file), and note
   the study's assays.
3. **Harvest and build in chat.** Ask Nessie to reingest the run. Expect workbooks with no hard finding:
   no `HOUSE_ASSAY_MISSING`, `HOUSE_ASSAY_AMBIGUOUS`, `UNKNOWN_SAMPLETYPE`, and the Assay sheet showing
   `to be created at upload: <house title> - Data Linked (study N)`.
4. **Upload in a later turn**, with the explicit confirmation (`upload-reingest` refuses a build from
   the same turn).
5. **Verify:** the reply lists the new UIDs and the study assay it created; that SEEK assay has one
   `assays_internal_assays` row pointing at the house assay; each new sample's parents are registered
   to it (the reply names any that were not); the D.SEQ QC values sit in their own attributes, not in
   `Notes`; batch upload's `graph:` line says `synced`; a rerun of the build reports the samples as
   already present.
6. **Roll back if it fails (owner fills the exact list from the reply):** delete the new samples with
   `DELETE /nextseek_api/samples/{uid}/`, the created study assay in SEEK, and restore the parents'
   D.SEQ values from step 2 with an update workbook.

Record the run, the build ids and the result in the table below; prod's sitting starts from it.

## Record

| Box | 4.1 read | 4.3–4.4 house assays | 4.6 A.HLA | 4.7 attributes | 4.8 catalog | §6 upload | By | Date |
|---|---|---|---|---|---|---|---|---|
| local | | | | | | n/a | | |
| dev | | | | | | | | |
| prod | | | | | | n/a | | |

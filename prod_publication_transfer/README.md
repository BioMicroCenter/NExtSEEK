# Publication records: fairdata-dev → fairdata (production)

**Status: partially applied, and no longer on the critical path.**

Three publications were transferred to production on 2026-08-26. The remaining
49 were never transferred, and nothing now depends on them: DOI and PMID live on
the samples themselves as attributes, not on publication records. See
`../sample_publication_attributes/PROD_ROLLOUT.md`.

These files are kept because they are the record of how production's three
publication records got there, and because `all_52_publications.sql` still works
if you decide you want SEEK's publication registry populated for its own sake —
browsable papers with titles, authors and abstracts. That is now a nice-to-have,
not a dependency.

## Two traps this work hit, worth not rediscovering

**`RAILS_ENV=production` does not mean the production server.** The SEEK docker
image sets it in every deployment, including a throwaway local stack. It names a
Rails config environment, not a machine. **If a command does not begin with
`ssh fairdata`, it is not touching production.**

**Ids do not survive the crossing.** Dev person 1 is `Demo Demo`; production
person 1 is a real researcher. Dev project 1 (`Published Data`) does not exist on
production at all. The SQL here carries no dev id: every foreign key is
`LAST_INSERT_ID()` or a parameter at the top of the file, and publications are
resolved by DOI on the target.

The one exception, verified: **sample ids do match** between Neo4j and SEEK MySQL
on one instance. Study, project, person and investigation ids do not.

## Creating publications

Only through SEEK's BibTeX import in the web UI, or the SQL here.
`POST /publications` returns 406 — the API is read-only for publications.

Raw SQL bypasses Rails callbacks, which cost two bugs on 2026-08-26:

- `publication_versions.visibility` left NULL made every publication return 403
  for every user, including its owner, with "This version is not available"
- a missing `--default-character-set=utf8mb4` stored `IFN-γ` as `IFN-Î³`

Both are fixed in `gen_transfer_sql.py`, which emits the charset warning in
every file it generates. Run anything here with that flag.

After inserting, rebuild the authorization lookup for the affected records only:

```bash
ssh fairdata "docker exec -i seek bundle exec rails runner \
  'Publication.find_each(&:update_lookup_table_for_all_users)' RAILS_ENV=production"
```

The full `seek:repopulate_auth_lookup_tables` enqueues every item of every type
(~166k samples on production) and returns long before the work is done. Never
run the `_sync` variant on production: it deletes each lookup table before
rebuilding, stripping authorization from every asset while it runs.

## Data decisions baked in

- **52 publications, not 62.** Ten MetNet DOIs were registered twice on dev; the
  `registered_mode=1` row is kept because it carries an abstract on all ten,
  plus journal and citation for the preprints. The BibTeX-imported duplicates
  have no abstract at all.
- **Project assignment** (`project_map.csv`) with per-row provenance: 54 from the
  `bib_by_project/` groupings, 7 by exact title match against production's
  Neo4j, 1 confirmed by the curator.

## Files

| File | |
|---|---|
| `gen_transfer_sql.py` | generates the insert SQL from the dev CSV exports |
| `pilot_prod_3_CHECKS.sql` / `_APPLY.sql` | the 3-publication pilot, checks separated so they gate rather than scroll past |
| `pilot_prod_3_publications.sql` | superseded by the split pair above |
| `all_52_publications.sql` | the full deduped set; the 3 already on production must be removed from it first |
| `project_map.csv` | dev publication id → prod project id |
| `fix_version_visibility.sql` | the 403 repair, already applied |
| `fix_pilot_encoding.sql` | the mojibake repair, already applied |
| `diagnose_403.sql` | read-only diagnostic kept for the next person who inserts SEEK rows by hand |

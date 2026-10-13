# Startup seed data

This directory ships scrubbed snapshots of the dev box's databases for fresh installs.

## What the seeds hold (refreshed 2026-10-05)

Taken from fairdata-dev at commit eb26e6ae (MySQL 8.0.46, Neo4j 2026.05.0, graph schema 1.3) with the refresh kit in
`regenerate/` (its README is the runbook), then only rows deleted and nodes detach-deleted:

| | Kept | Removed |
|---|---|---|
| Projects / investigations | "Published Data" with CSBC, Griffith, Impact, MetNet, Shoulders, SRP (51 studies, 324 assays, 65,834 samples) | the imported TCGA investigation (33 studies, 529 assays, 918,519 samples, ~1.2M DERIVED_FROM edges) and four test or stub investigations, with everything under them |
| Sample types | the catalog | D.ARR, A.MET and A.RPPA, added only for TCGA |
| Accounts | `demo` (admin) and `user` (two people, two SEEK users, two Django users) | every other SEEK user and person, and every other Django user (CI, probe and personal accounts) |
| Runtime state | none | all SEEK sessions, API tokens and queued jobs; all Django sessions; all Nessie chats, query tasks, CC transcripts and turns; the dev box's graph-sync history; SEEK's encrypted settings |

Three things a reader should know: the `dmac` tables are written utf8mb4 (R7, `tests/test_seed_charset.py`) although
dev's own `dmac` database is latin1; the graph's `SampleType.sample_count`, `Attribute.sample_count` and
`GraphMeta` hashes still count the removed samples until the installed box's first full graph sync recomputes them;
and the seed carries no graph constraints or indexes (the dump format never has), which graph sync creates.

## Files

- `dmac.sql.gz` — NExtSEEK application schema (the `dmac` MySQL database), now including
  `sample_attributes_unique` (the per-field definitions behind the download workbook's README sheet),
  `assay_context`, `projects_context` and `sample_types_context`. `dmac.sample_attributes_unique` is also registered in
  `startup/steps/schema_fixups.py`, so an install whose seed lacks it still gets the table
  (`startup/seed/sql/sample_attributes_unique.sql`). Neither that table nor `sample_types_context` has a Django
  migration; both are created in SQL.
- `sql/*.curated.sql` — the curated context seeds `scripts/context_gen.py --emit seed`
  writes from `context/`. Held: no install step reads them until the curated content is
  signed off, so an install still loads `sql/assay_context.sql` and the empty
  `sql/projects_context.sql` (`scripts/README.md` group C).
- `seek_production.sql.gz` — SEEK schema (the `seek_production` MySQL database)
- `neo4j.cypher.gz` — Neo4j graph export (sample/assay nodes + relationships)
- `filestore.tar.gz` — SEEK filestore snapshot (the content blobs the
  `seek_production` metadata points at: data files, SOPs, sample-type templates, RDF).
  Streamed into the `seek` container's `/seek/filestore` volume during install
  phase 7, after the seek container has initialized the volume. **Not in git**
  (gitignored, and hosted out of band) — it's on S3 and **downloaded on demand**
  by install / `startup seed-filestore` (sha256-verified; URL and digest in
  `startup/steps/seed_filestore.py`). If the download fails, install warn-skips it
  and SEEK metadata loads but blob downloads 404 until you seed it.

  The 2026-10-05 archive (`filestore-2026-10-05.tar.gz`, ~19 MB) holds only the files of surviving content blobs:
  no caches, and not SEEK's `secret_key_base` or `attr_encrypted` keys, which SEEK writes fresh on first boot (the
  earlier archive carried both, so every install shared one cookie-signing key). 20 SOPs and 7 data files have no
  file on dev either; their downloads 404.
  To fetch by hand:
  ```
  curl -o startup/seed/filestore.tar.gz https://nextseek.s3.us-east-2.amazonaws.com/filestore-2026-10-05.tar.gz
  ```

## Regenerating the filestore snapshot

`regenerate/refresh/run.sh filestore` rebuilds it from a box's filestore tar to match the scrubbed
`seek_production` (`regenerate/README.md` step 10). The `seek` image ships `tar`+`gzip` but **not** `unzip`, so this must be a
gzipped tar (extracted in-container with `tar -xzf -`), not a zip. To (re)load
it into a running stack without a full reinstall:

```
./startup.sh seed-filestore           # skips if assets already present
./startup.sh seed-filestore --force   # overwrite-merge regardless
```

## Test users baked in

| Username | Password | Role |
|---|---|---|
| `demo` | `demopassword` | Admin |
| `user` | `userpassword` | Regular user |

## Regenerating these dumps (maintainer only)

Follow `startup/seed/regenerate/README.md`: fetch read-only, scrub in throwaway containers, check, dump, verify.
Never run `./startup.sh dump-db` straight against a live box: the repo is public, and a raw dump carries every
account, session and token.

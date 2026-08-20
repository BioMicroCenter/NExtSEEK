# Startup seed data

This directory ships sanitized snapshots of dev databases for fresh installs.

## Files

- `dmac.sql.gz` — NExtSEEK application schema (the `dmac` MySQL database)

  The `dmac` dump does **not** yet include `sample_fields_context`, the
  per-field definitions behind the download workbook's README sheet, and will
  not until a maintainer folds the table in on the next `dump-db` cycle. A fresh
  install gets it anyway: `dmac.sample_fields_context` is registered in
  `startup/steps/schema_fixups.py`, so install runs its DDL
  (`startup/seed/sql/sample_fields_context.sql`) whenever the table is absent.
  That step runs whether or not seeds ran, so it heals an existing install on
  its next `install` too. Apply the DDL by hand only to an instance you are not
  reinstalling — production. If the table is missing regardless, the README's
  meanings render blank, which is the designed fail-soft behaviour, not a
  failure. (`assay_context` and `projects_context` are also absent from the seed,
  but for a different reason — nothing has needed them yet; see the "Seed gap"
  section of `docs/sample-download-workflow.md`.) Neither this table nor
  `sample_types_context` has a Django migration; both are created in SQL.
- `seek_production.sql.gz` — SEEK schema (the `seek_production` MySQL database)
- `neo4j.cypher.gz` — Neo4j graph export (sample/assay nodes + relationships)
- `filestore.tar.gz` — SEEK filestore snapshot (the content blobs the
  `seek_production` metadata points at: data files, SOPs, avatars, RDF, ...).
  Streamed into the `seek` container's `/seek/filestore` volume during install
  phase 7, after the seek container has initialized the volume. **Not in git**
  (~215MB, over GitHub's 100MB/file limit; forks can't host LFS) — it's hosted
  on S3 and **downloaded on demand** by install / `startup seed-filestore`
  (sha256-verified). If the download fails, install warn-skips it and SEEK
  metadata loads but blob downloads 404 until you seed it.

  S3 URL: `https://nextseek.s3.us-east-2.amazonaws.com/filestore.tar.gz`
  To fetch by hand:
  ```
  curl -o startup/seed/filestore.tar.gz https://nextseek.s3.us-east-2.amazonaws.com/filestore.tar.gz
  ```

## Regenerating the filestore snapshot

`filestore.tar.gz` is an exact tar of a populated `/seek/filestore`. To rebuild
it from a directory holding that tree (e.g. a host `filestore/` copy):

```
tar -C filestore -czf startup/seed/filestore.tar.gz .
```

The `seek` image ships `tar`+`gzip` but **not** `unzip`, so this must be a
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

See `startup/seed/regenerate/`. Requires a local `dump-source.env`
(gitignored) with the dev DB credentials. `./startup.sh dump-db` orchestrates.

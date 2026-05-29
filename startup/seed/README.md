# Startup seed data

This directory ships sanitized snapshots of dev databases for fresh installs.

## Files

- `dmac.sql.gz` — NExtSEEK application schema (the `dmac` MySQL database)
- `seek_production.sql.gz` — SEEK schema (the `seek_production` MySQL database)
- `neo4j.cypher.gz` — Neo4j graph export (sample/assay nodes + relationships)
- `filestore.tar.gz` — SEEK filestore snapshot (the content blobs the
  `seek_production` metadata points at: data files, SOPs, avatars, RDF, ...).
  Streamed into the `seek` container's `/seek/filestore` volume during install
  phase 7, after the seek container has initialized the volume. **Not committed**
  (~215MB, over GitHub's 100MB/file limit) — it's **gitignored and distributed
  out-of-band**: drop it in this directory by hand before running install.
  Optional: if absent, install skips it with a warning.

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

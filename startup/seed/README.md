# Startup seed data

This directory ships sanitized snapshots of dev databases for fresh installs.

## Files

- `dmac.sql.gz` — NExtSEEK application schema (the `dmac` MySQL database)
- `seek_production.sql.gz` — SEEK schema (the `seek_production` MySQL database)
- `neo4j.cypher.gz` — Neo4j graph export (sample/assay nodes + relationships)

## Test users baked in

| Username | Password | Role |
|---|---|---|
| `demo` | `demopassword` | Admin |
| `user` | `userpassword` | Regular user |

## Regenerating these dumps (maintainer only)

See `startup/seed/regenerate/`. Requires a local `dump-source.env`
(gitignored) with the dev DB credentials. `./startup.sh dump-db` orchestrates.

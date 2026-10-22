# Regenerating the startup seeds (maintainer only)

The three seeds in `startup/seed/` (`seek_production.sql.gz`, `dmac.sql.gz`, `neo4j.cypher.gz`) and the S3
filestore archive are snapshots of a running stack with some content removed. This folder holds the two dump
scripts that write the seeds and, in `refresh/`, the kit that gets a box's data onto this machine, removes what must
not ship, proves the result and dumps it.

**Never commit an unscrubbed dump.** The repo is public. A raw dump of any box carries every account (logins,
emails, password hashes), live sessions and API tokens, chat history and queued mail. The kit removes them; the dump
scripts on their own do not.

## The dump scripts

| Script | Writes | Notes |
|---|---|---|
| `dump_mysql.sh` | `dmac.sql.gz`, `seek_production.sql.gz` | `mysqldump --single-transaction`; SEEK's per-instance `site_base_host` setting is filtered out. Set `MYSQLDUMP` to choose the client, e.g. `docker exec <mysql container> mysqldump` (no published port needed); a MariaDB client's sandbox first line is dropped, since MySQL 8 refuses it at install |
| `dump_neo4j.py` | `neo4j.cypher.gz` | paged read transactions (a box ends any transaction over `db.transaction.timeout`), streamed to a temp file, moved into place on success. Temporal properties are written as `date(...)`, `datetime(...)`, ... and read back with their type by `startup/steps/seed.py` |

Both read `dump-source.env` (gitignored; see `dump-source.env.example`). `./startup.sh dump-db` runs both.

## The refresh, step by step (`refresh/`)

Every step but the fetch runs on this machine against throwaway containers named by `SEED_PREFIX` (default
`seedrefresh`), never a compose stack. The method is fixed: copy the box, then only `DELETE` rows and `DETACH DELETE`
nodes, with no schema change of any kind, then dump.

1. **Fetch, read only.** `BOX_SSH=<ssh host> BOX_RUN_AS=<service account> BOX_REPO=<repo on the box>
   refresh/fetch.sh RAW` streams, one ssh at a time: an inspect report, `mysqldump --single-transaction` of both
   schemas, the graph in paged read transactions (plus its constraint and index DDL), and a tar of the SEEK filestore.
   Nothing is written on the box; each stream's sha256 is compared on both ends. Ask before reading a busy box: a
   whole-graph export is a heavy read.
2. **Containers.** `NEO4J_IMAGE=neo4j:<the box's version> refresh/containers.sh work`.
3. **Load.** `refresh/run.sh load RAW` (a ~1M-node graph takes about 15 minutes, then its DDL is replayed).
4. **Before.** `refresh/run.sh before OUT`: row counts, schema, an orphan scan over every reference column (foreign
   keys, `*_id`, SEEK's `*_type`/`*_id` pairs), the graph's counts, constraints, indexes and parity with MySQL, and the
   search needles (every removed account's login and email, the target titles, `EXTRA_NEEDLES`).
5. **Build the kill sets, then review them.** `TARGETS='<investigation title>,...' [PROJECT_REGEX=...]
   [KEEP_LOGINS=demo,user] refresh/run.sh build OUT`. Roots: the target investigations (their studies, assays and
   the samples that sit only in their assays), every SEEK user and person and Django user not in `KEEP_LOGINS`, all
   login state, chat history and queued jobs, encrypted SEEK settings, rows that exist only for an account already
   gone. Then a cascade to a fixpoint: a row whose reference points at a removed row goes, except soft links
   (`contributor_id`, `policy_id`, catalog references). Policies go when only removed rows used them. Read
   `OUT/kill_build.json`; `STOP-CHECK mixed samples` must be 0 (a sample in a removed and a kept assay).
6. **Execute.** `refresh/run.sh execute OUT`: batched DELETEs, then batched DETACH DELETEs by element id.
7. **After.** `EXTRA_NEEDLES=... refresh/run.sh after OUT`. Must hold: the schema dump is byte-identical to the raw
   load; every table's `before - after` equals the executed deletes; no new orphan; graph constraints and indexes
   unchanged; graph parity clean; no hard search hit.
8. **Dump.** `refresh/run.sh dump` writes the three seeds into `startup/seed/` with the two scripts above.
9. **Verify.** `NEO4J_IMAGE=... refresh/containers.sh verify` then `refresh/run.sh verify OUT`: install's own loader
   (`startup/steps/seed.py`, only the docker transport swapped) loads the seeds into fresh containers shaped like
   install's; row counts, `information_schema` and graph counts must equal the cleaned copy, and the shipped files are
   grepped for every needle.
10. **Filestore.** `refresh/run.sh filestore RAW OUT` keeps the files of surviving content blobs, avatars, model
    images and RDF; drops `tmp/` and SEEK's two key files (`secret_key_base`, `attr_encrypted`: SEEK writes fresh
    ones on first boot; their encrypted settings were removed in step 5). Upload the archive to S3 and update
    `FILESTORE_SHA256` (and the URL if versioned) in `startup/steps/seed_filestore.py` only with the operator's go.
11. **Clean up.** `refresh/containers.sh down`; delete RAW and OUT (they hold unscrubbed data).

The last run, its rulings and its numbers are in `startup/seed/README.md`.

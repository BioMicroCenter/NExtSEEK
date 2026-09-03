# DOI and PMID as sample attributes

**Status: applied to production 2026-08-26.** This is the record of what was
done and how to verify or reverse it, not a pending procedure.

DOI and PMID are attributes of the sample, carried in `samples.json_metadata`
alongside every other attribute, and rendered by the generic attribute display
that was already live. **No code deployment was required.**

## Why not study-level DOI

All 167 production assays point at the 7 project-level studies, so
`assay_assets -> assays -> studies` distinguishes at most 7 groups for ~52
papers. One DOI per project is not a usable answer. Production's paper-level
studies exist only in Neo4j, with graph-local ids that collide with SEEK's —
recorded as finding 14 in `../../docs/archive/2026-08/2026-08-21-publication-links-design.md`.

## What was applied

| Step | File | Result on production |
|---|---|---|
| 1 | — | dropped the abandoned `dmac.sample_publications` join table |
| 2 | `01_add_attributes.sql` | DOI + PMID on all **115** sample types; attributes 2,954 → **3,184** |
| 3 | `02_catalogue.sql` | both registered in `dmac.sample_attributes_unique` |
| 4 | `backfill_publication_attributes` + `prod_pairs.tsv` | **45,825** of 166,235 samples, zero unresolvable ids |

Steps 2 and 3 are idempotent. Step 4 was gated by a dry run: all 47,229
graph-sourced pairs resolved against SEEK's `samples` table with no misses,
which also established that production's graph and database agree on sample
identity.

`prod_pairs.tsv` came from production's Neo4j — the only store holding the
paper-level grouping of samples. Nothing reads Neo4j at runtime.

It is not committed (1.8MB of generated data). Regenerate it with this Cypher
against production, one `sample_id<TAB>doi<TAB>pmid` line per row:

```cypher
MATCH (s:Sample)-[:IN_STUDY]->(st:Study)
WHERE coalesce(st.DOI,'') <> ''
RETURN s.id AS sample_id, st.DOI AS doi, coalesce(st.PMID,'') AS pmid
ORDER BY toInteger(s.id), toInteger(st.id)
```

Bolt is firewalled on the MIT hosts; use the HTTP transaction endpoint
(`https://nextseek.mit.edu/db/neo4j/tx/commit`). `nsprod/http_driver.py` in
`~/Documents/MIT/Scripts/ns-prod-create-studies` implements it.

## Two things that shape everything downstream

**Blank values are the norm.** A sample type writes *every* attribute into
`json_metadata` whether or not it was filled in
(`seek/dbtable_sample.py`). So samples uploaded from now on carry blank DOI and
PMID keys. **Any reader must treat `""`, `" "` and absent as equally "no
paper"** — the same empty-string trap the Neo4j side has, now in two places.

**Multiple papers are semicolon-separated.** 1,247 production samples appear in
more than one paper. DOIs are joined with `"; "` and PMIDs align positionally,
blank where a paper has no PubMed record. A reader taking the whole string as
one DOI will be wrong for those samples.

## Verify

```bash
ssh fairdata "docker exec -i seek-mysql sh -c 'mysql -uroot -p\"\$MYSQL_ROOT_PASSWORD\" seek_production -e \"SELECT COUNT(*) AS samples_with_doi FROM samples WHERE INSTR(json_metadata, CONCAT(CHAR(34), \\\"DOI\\\", CHAR(34))) > 0\"'"
```

Expect 45825. `MUS-200901ENG-1` should read
`DOI=10.1016/j.celrep.2021.108864`, `PMID=33730582`.

## Reverse

Steps 2 and 3:

```sql
DELETE FROM seek_production.sample_attributes WHERE title IN ('DOI','PMID');
DELETE FROM dmac.sample_attributes_unique WHERE field_name IN ('DOI','PMID') AND sample_type = '';
```

Step 4 has a clean inverse rather than needing a backup, because no sample
carried a DOI or PMID key before step 2 created the attributes: remove those two
keys from `json_metadata`. Do it in Python, not `JSON_REMOVE` — see below.

## Backfilling more samples later

```bash
uv run manage.py backfill_publication_attributes --from-file pairs.tsv        # dry run
uv run manage.py backfill_publication_attributes --from-file pairs.tsv --apply
```

`pairs.tsv` is `sample_id<TAB>doi<TAB>pmid`, one line per pair; repeated sample
ids accumulate into the semicolon list. `--from-studies` derives pairs from
`studies.doi` instead, which works on instances whose studies are paper-level.

**Never use MySQL's JSON functions on `json_metadata`.** It is a TEXT column
whose key order mirrors each sample type's column order; MySQL round-trips
through its internal JSON type, which sorts keys alphabetically and would
silently reorder every sample's metadata. The command reads and writes the text
in Python for exactly this reason.

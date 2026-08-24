# Publication links for samples (DOI / PMID)

**Date:** 2026-08-21 (revised same day — see Revision history)
**Status:** Design approved, not yet implemented
**Branch at time of writing:** `feat/nfcore-rna-atlas`

## Goal

A person looking up a sample — in NExtSEEK's sample search, on a sample's detail
page, or by asking Nessie — should be able to see which published paper that
sample appears in. The reverse must also work: "what samples were used in
\<study\>?", by paper title, DOI, or PMID.

Most samples are unpublished. The feature must say nothing about them rather
than guessing.

## Revision history

**Revision 2** replaced the data model. The first revision built publication
records in SEEK's own `publications` table, linked them to studies through the
`relationships` table, and mirrored them into Neo4j as `:Publication` nodes with
`REPORTED_IN` edges. That is gone. The DOI and PMID are now **attributes of the
study itself**, on both sides of the stack.

This removes an entity, a link table, a node label, a relationship type, the
per-sample override table, and the API that existed to curate it. Nessie's
traversal shortens from two hops to one. What remains is: put a DOI and a PMID
on each published study, and read them.

Per-sample include/exclude overrides are **deferred to a later version** (see
Deferred).

## Decisions

| Decision | Choice |
|---|---|
| Where the identifiers live | `doi` and `pmid` attributes on the study, in **both** MySQL and Neo4j |
| Sample to paper | Inherited from study membership; every sample in a published study is treated as published |
| Backfill | Extract, resolve, curator review, then write |
| Surfaces | Search column, detail page, search-by-publication, Nessie both directions |
| Per-sample overrides | Deferred |
| Sync | One command fills MySQL and Neo4j from a single reviewed source |

### Why not SEEK's publications table

SEEK ships a complete, unused `publications` table and a `relationships` link
table, and revision 1 used them. Two attributes on the study express the same
fact for this use case with far less machinery, and — decisively — a study here
*is* a paper (finding 1), so a separate publication entity was modelling a
distinction that does not exist in this data.

The cost of the simpler model is that a study can carry only one DOI, and a
paper spanning several studies is repeated on each. Both are acceptable: the
inventory below shows at most one reference per study, and a repeated DOI is
still correct.

## Findings that shaped the design

All verified against the running stack on 2026-08-21.

1. **SEEK Studies in this instance are papers.** `studies.title` holds the paper
   title for most of the 51 studies (e.g. "Organoid co-culture model of the
   cycling human endometrium..."). This is what makes attributes-on-study the
   right model rather than a separate publication entity.

2. **DOIs already exist as prose.** 29 of 51 study descriptions contain an
   extractable DOI; one more is truncated; 4 more cite a paper via a publisher or
   PMC URL with no DOI in it. Full inventory under "Backfill". No description
   carries more than one distinct paper reference.

3. **`studies` has no `doi` or `pmid` column today.** Verified with `DESCRIBE
   studies`: 15 columns, none publication-related.

4. **Adding those columns collides with nothing in SEEK 1.15.** SEEK has a `doi`
   column on 18 tables (`data_files`, `sops`, `documents`, `publications`,
   `snapshots`...) but **not** on `studies`, `investigations` or `assays`, and
   `app/models/study.rb` in the running container has zero mentions of `doi`.
   SEEK mints ISA-level DOIs against *snapshots*, not the study row.

5. **A sample can belong to more than one study.** 18 samples have two `IN_STUDY`
   edges in Neo4j. A sample can therefore inherit two papers, which is correct.

6. **Sample to Study is reachable in MySQL**, not only in the graph:
   `assay_assets (asset_type='Sample') -> assays.study_id` covers 50,401 of 51,359
   samples (98.1%). Neo4j agrees at 50,162 with `IN_STUDY`. The remaining ~958
   samples have no study and therefore no publication.

7. **Both database connections share a host.** `dmac/settings.py:29,38` — the
   `default` and `seek` connections both read `MYSQL_HOST`.

8. **The PubMed-style `[CATEGORY]` search syntax means sample type, only.**
   `seek/search.py:103` resolves the bracket term via `getSampleTypeID()` and emits
   `sample_type_id=<n>` (`search.py:126`). It cannot be reused for `[DOI]`.

9. **`neo4j_schema*.json` are generated, not authored.** `chat_nextseek/src/chat_nextseek/config.py:1574`
   introspects the live graph and overwrites them (hence `fetched_at`).
   `min_graph_schema.json` is hand-written.

10. **The graph attributes exist on dev/prod but not locally.** The local docker
    Neo4j's `Study` nodes carry exactly `title`, `description`, `id` across all 51
    studies. The `neo4j_schema_dev.json` and `_prod.json` snapshots agree but are
    dated 2026-03-26 and cannot speak to a recent change; per the maintainer, dev
    and prod already carry `doi` and `pmid` on `Study`. Every graph write in this
    design is therefore idempotent and must work whether or not the properties
    already exist.

11. **`-PUB` is the sample population, not a parallel copy.** Samples published
    through `ns-published-fdh` carry a `-PUB` UID suffix, and their `Parent*`
    references point at other `-PUB` UIDs (29,783 of 29,787 in the 260821 batch
    sheet), so the published tree is self-contained. That is not a problem: in a
    real NExtSEEK instance the `-PUB` records *are* the samples — 49,467 of 51,359
    in the seeded stack are `-PUB`-suffixed, and no non-`-PUB` sample has a `-PUB`
    twin. The originals live on FAIRDOMHub. A concern that this feature would
    attach papers to duplicate records was raised during design and does not hold.

12. **Study mapping is incomplete at the source.** The 2026-08-21
    `ns-published-fdh` run produced 68,242 published sample rows against 43 mapped
    dev studies, but 17,571 rows (26%) carried no `mapped_study_id`, and 8 of 51
    study titles did not map. Because the DOI hangs off the study, a sample with no
    study inherits no paper. This bounds what the feature can show and is a data
    problem upstream of it, not a defect in it.

13. **A Django migration cannot create the MySQL columns.** The container
    entrypoint runs a plain `migrate`, which touches only the default database, and
    `seek/dbrouters.py:allow_migrate` expresses no opinion for other schemas. DDL
    against `seek_production` must therefore be performed explicitly by the fill
    command, not by a migration that would silently never run.

## Data model

### MySQL: two columns on `seek_production.studies`

```sql
ALTER TABLE studies
  ADD COLUMN doi  VARCHAR(255) NULL,
  ADD COLUMN pmid INT          NULL;
```

Created by the fill command, guarded by an `information_schema` check, because
of finding 13. MySQL 8 has no `ADD COLUMN IF NOT EXISTS`, so the guard is a
lookup rather than a clause.

`doi` is stored lowercased; DOIs are case-insensitive by specification.

### Neo4j: two properties on `Study`

```
(:Study {id, title, description, doi, pmid})
(:Sample)-[:IN_STUDY]->(:Study)
```

No new label and no new relationship type. A sample's paper is one hop away
along an edge that already exists.

### Effective set

A sample is published in a paper if it belongs to a study whose `doi` or `pmid`
is set. Nothing else. A sample in two published studies shows both papers.

## Backfill

### Real inventory of the 51 study descriptions

Buckets are mutually exclusive and sum to 51.

| Bucket | Count | Notes |
|---|---|---|
| Clean extractable DOI | 29 | `doi.org/...`, `science.org/doi/...`, `pubs.acs.org/doi/full/...`, bioRxiv/medRxiv `/content/<doi>v1.full`, two bare DOIs with a trailing period |
| Truncated DOI | 1 | Study 31: `https://doi.org/10.3390/` with no suffix — flag, never guess |
| Paper behind a non-DOI URL | 4 | `nature.com/articles/s41596-024-01076-x#Sec43`, `cell.com/immunity/fulltext/...`, `sciencedirect.com/.../pii/...`, `ncbi.nlm.nih.gov/pmc/articles/PMC8439179/` |
| URL present, but not a paper | 2 | a GEO accession, an `omero.mit.edu` project link — must be rejected by host |
| Only an imgur figure URL | 8 | no reference of any kind |
| No URL at all | 4 | placeholders such as "Mike Chao Barcoding Placeholder" |
| `description IS NULL` | 3 | the extractor must handle NULL, not just empty |

So 33 studies yield a paper (29 + 4), 1 is flagged for manual entry, and 17
correctly yield nothing.

Only 3 descriptions mention PubMed at all, so PMIDs come almost entirely from
resolution, not extraction.

### Command

`uv run manage.py fill_study_publications --extract --out review.tsv`

Writes nothing to any database. Emits one row per candidate:

`approve, study_id, study_title, raw_match, normalized_doi, resolved_title,
journal, year, pmid, title_similarity, proposed_action, notes`

Extraction rules, each grounded in a real string above: bare `10.x/...`;
`doi.org/` prefixes; publisher `/doi/` and `/doi/full/` paths; bioRxiv/medRxiv
`/content/<doi>` with a trailing `v<N>` and optional `.full`; strip trailing
punctuation and `#fragment`. Reject `i.imgur.com`, `ncbi.nlm.nih.gov/geo/`, and
`omero.mit.edu` by host. For the 4 URL-only papers: `nature.com/articles/<id>`
maps to `10.1038/<id>`; a PMC id resolves through NCBI's ID converter; the rest
are left for manual entry.

Resolution uses Crossref (`api.crossref.org/works/{doi}`) for title, journal and
date, and NCBI's ID Converter for DOI/PMID conversion.

**`title_similarity` is the safety net.** A DOI that resolves to a paper whose
title does not resemble the study title means the description is citing someone
else's work. The curator sees a low score rather than discovering it after the
write.

The curator sets each row's `approve` column to `yes`; `--apply` ignores every
row that is not exactly `yes`, so an unreviewed file writes nothing.

`uv run manage.py fill_study_publications --apply review.tsv` writes
`studies.doi` and `studies.pmid`, then pushes the same values to the matching
Neo4j `Study` nodes. Idempotent: re-running sets the same values.

Study descriptions are never modified. The prose stays as the provenance of the
record.

## Read paths

### New module: `seek/publications.py`

`dbtable_sample.py` is already 4,000+ lines. All publication logic lives in a new
module with a small, testable surface:

```python
publications_for_samples(sample_ids)  -> {sample_id: [PublicationRef, ...]}
publications_for_sample(sample_id)    -> [dict]
resolve_study_ids(query)              -> [study_id]          # DOI, PMID, or title
publication_where_clause(query, published_only) -> str
attach_publications(rows)             -> rows                # in place
```

### Search results column

After `__retrieveRecords_advanced` returns its page of rows
(`seek/dbtable_sample.py:3858`), one additional query fetches the studies and
their DOIs for exactly those sample ids. One query per search regardless of page
size, and no change to the large SQL builder.

### Sample detail page

`seek/views.py:sample()` already assembles `report['sampledic']` for
`samples.html`. Add `report['publications']`, rendered as a block with the study
title, DOI link, and PMID link.

### Search by publication

A dedicated **Publication** filter field accepting a DOI, a PMID, or a study
title, plus a **published only** checkbox.

It must not reuse the `[CATEGORY]` bracket syntax (finding 8): `10.1101/...[DOI]`
would be parsed as a sample type named `DOI` and silently return nothing.

The filter resolves to study ids through a parameterized query, then constrains
the search by joining on those ids — integers only, never user text spliced into
SQL.

## Neo4j and Nessie

### Sync

`uv run manage.py sync_study_publications` reads `studies.doi` / `studies.pmid`
from MySQL and sets the matching properties on `Study` nodes, including setting
them to null where MySQL has none — so clearing a wrong DOI in MySQL clears it in
the graph too.

Idempotent, and safe whether or not the properties already exist on the target
instance (finding 10). `--apply` on the fill command calls the same code, so a
normal backfill needs no second step; the standalone command exists for repair
and for instances edited by other means.

### Teaching Nessie

- `neo4j_schema.json`, `_dev`, `_prod` — **do not hand-edit.** Generated by
  `config.py:1574`; a re-fetch picks up the new properties.
- `min_graph_schema.json` — hand-written. Update the `Study` node description to
  name `doi` and `pmid`, and add `graph_query_triggers` for both directions.
- `prompts/graph_agent.txt` and `context/capabilities.md` — the same, in prose.
- `config.py`'s fetch should add study DOIs to the generated `vocabulary` block,
  so the agent can map "the SureQuant paper" onto a real node.

## Access control

DOIs are public, but *which samples* a paper used is not. Sample search already
filters by `project_id` for non-supervisors (`seek/views.py:435-438`), and the
publication column rides that same query, so it inherits the filter without extra
work. There is no new endpoint in this version to get it wrong.

## Failure handling

| Failure | Behaviour |
|---|---|
| Neo4j unavailable during `--apply` | The MySQL write commits; the graph step is reported as deferred and logged. `sync_study_publications` repairs it. A curation write must never fail because the graph is down. |
| Crossref/NCBI unreachable during `--extract` | Degrades to extraction-only; rows marked `unresolved`. |
| Truncated or invalid DOI | Flagged for manual entry. Never guessed. |
| Sample with no study (~958 of them) | Empty publication cell. Correct, not an error. |
| Study with no DOI (17 of them) | Empty publication cell. Correct, not an error. |
| Columns already present | The `information_schema` guard makes the DDL a no-op. |
| Graph properties already present | `SET` is idempotent; dev and prod are expected to be in this state. |

## Testing

**Extractor.** All 30 real description strings as fixtures, including the ones
built to break it: the truncated `10.3390/`, the two trailing periods, the
`v1.full` suffixes, the `pubs.acs.org/doi/full/` path, and the GEO and omero URLs
that must be *rejected* rather than parsed.

**Read layer.** SQL text assertions (schema names, the join through
`assay_assets`, integer-only splicing) plus Python-side shaping with a stubbed
row source — including the 18 real samples belonging to two studies at once.

**Sync.** Idempotency (run twice, same values) and clearing (a DOI removed in
MySQL is removed in the graph).

**Search.** Column populated for a published study, empty for an unpublished one;
the publication filter returns exactly the samples of the matched studies.

**Nessie.** One eval case per direction.

Tests go in `seek/tests/` (alongside `test_search_pubmed_nested.py`) and
`nextseek_api/tests/`.

## Deferred

- **Per-sample include/exclude overrides.** Every sample in a published study is
  treated as appearing in its paper. A study containing samples that never made
  the paper will over-claim. Revisit once curators have seen real data and can say
  whether the false positives matter.
- **A curation UI**, and any write API. The fill command is the only writer.
- **Publication links at the Assay or Investigation level.**
- **Rewriting study descriptions** to remove the now-redundant DOI prose.

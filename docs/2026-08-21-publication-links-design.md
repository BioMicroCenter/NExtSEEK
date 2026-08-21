# Publication links for samples (DOI / PMID)

**Date:** 2026-08-21
**Status:** Design approved, not yet implemented
**Branch at time of writing:** `feat/nfcore-rna-atlas`

## Goal

A person looking up a sample — in NExtSEEK's sample search, on a sample's detail
page, or by asking Nessie — should be able to see which published paper that
sample appears in. The reverse must also work: "what samples were used in
\<study\>?", by paper title, DOI, or PMID.

Most samples are unpublished. The feature must say nothing about them rather
than guessing.

## Findings that shaped the design

All verified against the running stack on 2026-08-21, not assumed.

1. **SEEK Studies in this instance are papers.** `studies.title` holds the paper
   title for most of the 51 studies (e.g. "Organoid co-culture model of the
   cycling human endometrium…"). The publication concept already exists; it just
   isn't structured.

2. **DOIs already exist as prose.** 29 of 51 study descriptions contain an
   extractable DOI; one more is truncated; 4 more cite a paper via a publisher or
   PMC URL with no DOI in it. See "Backfill" for the exact inventory.

3. **SEEK's publications model is complete and unused.** `seek_production.publications`
   has `doi`, `pubmed_id`, `title`, `journal`, `published_date`, `citation` — and
   0 rows. `relationships` (the polymorphic link table) is also empty.

4. **SEEK's link convention**, read from the running container's source:
   `app/models/relationship.rb:26` defines `RELATED_TO_PUBLICATION = "related_to_publication"`,
   and `app/models/publication.rb:29` declares
   `has_many :studies, through: :related_relationships, source: :subject`.
   So a Study→Publication link is a `relationships` row with
   `subject_type='Study'`, `predicate='related_to_publication'`,
   `other_object_type='Publication'`.

5. **A sample can already belong to more than one study.** 18 samples have two
   `IN_STUDY` edges in Neo4j. Many-to-many is real, not hypothetical.

6. **Sample→Study is reachable in MySQL**, not only in the graph:
   `assay_assets (asset_type='Sample') → assays.study_id` covers 50,401 of 51,359
   samples (98.1%). Neo4j agrees at 50,162 with `IN_STUDY`. The remaining ~958
   samples have no study and therefore no publication.

7. **Both database connections share a host.** `dmac/settings.py:29,38` — the
   `default` and `seek` connections both read `MYSQL_HOST`, so they are always the
   same MySQL server and cross-schema SQL is structurally safe.

8. **The PubMed-style `[CATEGORY]` search syntax means sample type, only.**
   `seek/search.py:103` resolves the bracket term via `getSampleTypeID()` and emits
   `sample_type_id=<n>` (`search.py:126`). It cannot be reused for `[DOI]`.

9. **`neo4j_schema*.json` are generated, not authored.** `chat_nextseek/src/chat_nextseek/config.py:1574`
   introspects the live graph and overwrites them (hence `fetched_at`).
   `min_graph_schema.json` is hand-written.

10. **No cron jobs are registered.** `django_crontab` is in `INSTALLED_APPS`
    (`dmac/settings.py:160`) with a logger configured, but no `CRONJOBS` list
    exists anywhere in `dmac/`.

## Decisions

| Decision | Choice |
|---|---|
| Granularity | Study-level, inherited by its samples, with per-sample include/exclude overrides |
| System of record | SEEK's native `publications` + `relationships` tables |
| Backfill | Extract → resolve → curator review → write |
| Surfaces | Search column, detail page, search-by-publication, Nessie both directions |
| Override UX | API endpoints only in v1 (no curation UI) |
| Graph sync | Write-through, plus a reconcile management command |
| Effective set | Derived in MySQL; materialized only in Neo4j |

### Why derived rather than materialized in MySQL

A materialized `(sample_id, publication_id)` table would make reads trivial, but
it writes thousands of rows per paper and goes stale the moment a study gains
samples — so it needs the reconcile job *and* the write-through path, for a table
that caches a rule cheap enough to evaluate. Deriving means new samples added to
a study inherit the paper automatically.

Neo4j is the exception: there the set *is* materialized as edges, because a
one-hop pattern is far harder for Nessie's graph agent to get wrong than a
three-way join with set arithmetic.

If the search join ever measures slow, the materialized table can be added later
purely as a cache. Both give identical answers.

## Data model

### Publication record — no schema change

SEEK's `seek_production.publications`. Registration happens in SEEK's own UI,
which resolves a PMID or DOI into title/journal/date/citation. Using SEEK's own
tables means papers registered through its UI are picked up with no adapter, and
SEEK's publication pages start working as a side effect.

### Publication → Study link — no schema change

A row in `seek_production.relationships`:

```
subject_type      = 'Study'
subject_id        = <studies.id>
predicate         = 'related_to_publication'
other_object_type = 'Publication'
other_object_id   = <publications.id>
```

### Overrides — one new table, in the `dmac` schema

```sql
sample_publication_override(
  id             INT PRIMARY KEY AUTO_INCREMENT,
  sample_id      INT NOT NULL,
  publication_id INT NOT NULL,
  mode           ENUM('include','exclude') NOT NULL,
  note           TEXT NULL,
  created_by_id  INT NULL,
  created_at     DATETIME NOT NULL,
  UNIQUE KEY (sample_id, publication_id),
  KEY (publication_id)
)
```

A Django model in the `seek` app using the default `_DATABASE`, so the ordinary
`migrate` that `docker/scripts/entrypoint.sh` already runs on every container
start creates it. No `--database=seek` step to forget.

### The cross-schema join

Sample search runs its raw SQL on the `seek` connection
(`seek/dbtable_sample.py:3861`) while the override table lives in `dmac`. Because
both connections resolve to the same server (finding 7), a schema-qualified join
is safe. **The schema name must be read from
`settings.DATABASES['default']['NAME']`, never hardcoded as `dmac`** — that is
only the default value of an environment variable.

### Effective set

For a publication P:

```
samples of P's linked studies
  MINUS rows where (sample, P) has mode='exclude'
  PLUS  rows where (sample, P) has mode='include'
```

A sample in two published studies appears in both papers. That is correct
behaviour, and it is where a naive implementation double-counts.

## Backfill

### Real inventory of the 51 study descriptions

Buckets are mutually exclusive and sum to 51.

| Bucket | Count | Notes |
|---|---|---|
| Clean extractable DOI | 29 | `doi.org/…`, `science.org/doi/…`, `pubs.acs.org/doi/full/…`, bioRxiv/medRxiv `/content/<doi>v1.full`, two bare DOIs with a trailing period |
| Truncated DOI | 1 | Study 31: `https://doi.org/10.3390/` with no suffix — flag, never guess |
| Paper behind a non-DOI URL | 4 | `nature.com/articles/s41596-024-01076-x#Sec43`, `cell.com/immunity/fulltext/…`, `sciencedirect.com/…/pii/…`, `ncbi.nlm.nih.gov/pmc/articles/PMC8439179/` |
| URL present, but not a paper | 2 | a GEO accession, an `omero.mit.edu` project link — must be rejected by host |
| Only an imgur figure URL | 8 | no reference of any kind |
| No URL at all | 4 | placeholders such as "Mike Chao Barcoding Placeholder" |
| `description IS NULL` | 3 | the extractor must handle NULL, not just empty |

So 33 studies yield a paper (29 + 4), 1 is flagged for manual entry, and 17
correctly yield nothing.

Only 3 descriptions mention PubMed at all, so PMIDs come almost entirely from
resolution, not extraction.

### Command

Lives in `nextseek_api/management/commands/`, alongside the existing `nessie.py`.

```bash
uv run manage.py backfill_study_publications --extract --out review.tsv
```

Writes nothing to any database. Emits one row per candidate:

`study_id, study_title, raw_match, normalized_doi, resolved_title, journal,
year, pmid, title_similarity, proposed_action, notes`

Extraction rules, each grounded in a real string above: bare `10.x/…`;
`doi.org/` prefixes; publisher `/doi/` and `/doi/full/` paths; bioRxiv/medRxiv
`/content/<doi>` with a trailing `v<N>` and optional `.full`; strip trailing
punctuation and `#fragment`. Reject `i.imgur.com`, `ncbi.nlm.nih.gov/geo/`, and
`omero.mit.edu` by host. For the 4 URL-only papers: `nature.com/articles/<id>`
maps to `10.1038/<id>`; a PMC id resolves through NCBI's ID converter; the rest
are left for manual entry.

Resolution uses Crossref (`api.crossref.org/works/{doi}`) for title, journal and
date, and NCBI's ID Converter for DOI↔PMID.

**`title_similarity` is the safety net.** A DOI that resolves to a paper whose
title does not resemble the study title means the description is citing someone
else's work. The curator sees that as a low score rather than discovering it
after insertion.

```bash
uv run manage.py backfill_study_publications --apply review.tsv
```

The curator reviews the file and sets each row's `approve` column to `yes` or
`no`; `--apply` ignores every row that is not exactly `yes`, so an unreviewed
file inserts nothing.

Inserts only approved rows: a `publications` row plus its
`relationships` row. Idempotent on DOI — re-running never duplicates. Resolution
responses are cached to disk so `--apply` does not re-hit the network.
`--extract --offline` performs extraction with no outbound access.

Study descriptions are never modified. The prose stays as the provenance of the
record.

## Read paths

### New module: `seek/publications.py`

`dbtable_sample.py` is already 4,000+ lines. All publication logic lives in a new
module with a small, testable surface:

```python
publications_for_samples(sample_ids)  -> {sample_id: [PublicationRef, ...]}
sample_ids_for_publication(pub_id)    -> SQL fragment (derived table, not an IN list)
resolve_publication(query)            -> Publication | None   # DOI, PMID, or exact title
effective_join_sql()                  -> the study-inherit ± override join
```

### Search results column

`SAMPLE_HEADERS` (`seek/dbtable_sample.py:66`) gains `publications`.

The column is **not** a correlated subquery in the main select. After
`__retrieveRecords_advanced` returns its page of rows, one additional query
fetches publications for exactly those sample ids. One query per search
regardless of page size, no change to the large SQL builder, and the part that
could become slow is isolated enough to swap for a cache later.

### Sample detail page

`seek/views.py:sample()` already assembles `report['sampledic']` for
`samples.html`. Add `report['publications']` from the same helper, rendered as a
block with citation, DOI link, PMID link, journal, and year.

### Search by publication

A dedicated **Publication** filter field on the search page accepting a DOI, a
PMID, or a paper title, plus a **published only** checkbox.

It must not reuse the `[CATEGORY]` bracket syntax (finding 8): `10.1101/…[DOI]`
would be parsed as a sample type named `DOI` and silently return nothing.

Resolution order is deterministic: DOI (normalized, case-insensitive), then
PMID, then exact case-insensitive title. If a title matches more than one
publication the filter returns an error naming the candidates rather than picking
one — a silent wrong pick would misattribute samples to a paper.

The filter resolves to a publication, then constrains the query by joining the
derived sample set — a JOIN, not an `IN` list, because a paper covering a large
study pulls thousands of ids. Existing search syntax is untouched.

## Neo4j and Nessie

### Graph shape

```
(:Publication {seek_id, doi, pmid, title, journal, year, url})
(:Sample)-[:REPORTED_IN]->(:Publication)     # materialized effective set
(:Study) -[:REPORTED_IN]->(:Publication)     # direct link from SEEK relationships
```

One relationship type for both, because Cypher patterns carry labels —
`MATCH (s:Sample)-[:REPORTED_IN]->(p:Publication)` is unambiguous, and one type
is one less thing for the graph agent to get wrong. A query that matches
`REPORTED_IN` without a label on the source will see both Sample and Study edges;
the schema documentation must say so.

Uniqueness constraints on `Publication.seek_id` and `Publication.doi`, added to
`ensure_constraints()` in `nextseek_api/batch_upload/neo4j_sync.py:77`.

### Sync

**Write-through:** `seek/publications.py` exposes `sync_publication_to_graph(pub_id)`,
called by the API endpoints after any override or link change, so Nessie is
current immediately.

**Reconcile:** `uv run manage.py sync_publications_graph` re-derives the whole
publication subgraph from MySQL, MERGEs nodes and edges, **and deletes edges no
longer implied**. That deletion is what actually repairs drift and is the part
easiest to leave out.

**Scheduling:** this adds the first `CRONJOBS` entry to `dmac/settings.py`
(finding 10). There is no existing cron slot to drop into.

### Teaching Nessie

- `neo4j_schema.json`, `_dev`, `_prod` — **do not hand-edit.** Generated by
  `config.py:1574`; a re-fetch picks up Publication nodes once they exist.
- `min_graph_schema.json` — hand-written. Add the Publication node type, the
  `REPORTED_IN` relationship, and `graph_query_triggers` for "what samples were
  used in \<paper\>", DOIs, and PMIDs.
- `prompts/graph_agent.txt` and `context/capabilities.md` — the same, in prose.
- `config.py`'s fetch should add publication titles and DOIs to the generated
  `vocabulary` block, which is how the graph agent maps a phrase like "the
  SureQuant paper" onto a real node.

## API

Following the existing router/service split (`nextseek_api/urls.py`,
`nextseek_api/services/`), a new `services/publications.py` and
`PublicationViewSet`:

```
GET    /nextseek_api/publications/               list
GET    /nextseek_api/publications/{id}/          detail + linked studies + effective count
GET    /nextseek_api/publications/{id}/samples/  effective sample set (paginated)
POST   /nextseek_api/publications/{id}/samples/  {uids: [...], mode: include|exclude}
DELETE /nextseek_api/publications/{id}/samples/  {uids: [...]}  → revert to inherited
GET    /nextseek_api/samples/{uid}/publications/ reverse lookup
```

### Access control

DOIs are public; *which samples a paper used* is not. Sample search already
filters by `project_id` for non-supervisors (`seek/views.py:435-438`), so the
results column inherits that filtering. **The publications endpoints must apply
the same project filter explicitly**, not merely require authentication — easy to
miss, because the publication record itself looks like public data.

## Failure handling

| Failure | Behaviour |
|---|---|
| Neo4j unavailable during a curation write | MySQL write commits; response returns `graph_sync: "deferred"`; logged; reconcile repairs. Curation never fails because the graph is down — that is why reconcile exists. |
| Unknown UIDs in a POST | Atomic: nothing written, 400 naming exactly which UIDs failed. |
| `include` for an already-inherited sample | Accepted as a no-op, reported in the response. Not an error. |
| `exclude` for a sample not currently in the study | Accepted, reported as having no effect today. The override protects the sample if it joins that study later. |
| Crossref/NCBI unreachable during backfill | Degrades to extraction-only; rows marked `unresolved`. |
| Truncated or invalid DOI | Flagged for manual entry. Never guessed. |
| Sample with no study (~958 of them) | Empty publication cell. Correct, not an error. |

## Testing

**Extractor.** All 30 real description strings as fixtures, including the ones
built to break it: the truncated `10.3390/`, the two trailing periods, the
`v1.full` suffixes, the `pubs.acs.org/doi/full/` path, and the GEO and omero URLs
that must be *rejected* rather than parsed.

**Set arithmetic.** Plain inheritance; exclude; include; include of a sample that
lives in a different study; and the 18 real samples belonging to two studies at
once — the double-count case.

**Reconcile.** Idempotency (run twice, identical graph) and drift repair (plant a
stale edge, confirm removal).

**API.** Atomicity on unknown UIDs; project filtering for non-supervisors.

**Search.** Column populated for a known published study, empty for an
unpublished one; the publication filter returns exactly the effective set.

**Nessie.** One eval case per direction: "what paper are these samples from?" and
"what samples were used in \<study\>?".

Tests go in `seek/tests/` (alongside `test_search_pubmed_nested.py`) and
`nextseek_api/tests/`.

## Out of scope for v1

- A curation UI for overrides — API only, by decision.
- Publication links at the Assay or Investigation level.
- Backfilling PMIDs for papers where Crossref and NCBI disagree.
- Rewriting study descriptions to remove the now-redundant DOI prose.

# Graph 1.2: transaction bounds and behavioural tests (design)

Written 2026-09-16. Implements the operator's mandate: resolve and fully test the 1.2 schema, and resolve the actual
testing of the upload, attribute, update and related endpoint features. Behaviour, not wiring.

Companion plan: [`docs/superpowers/plans/2026-09-16-graph-behaviour-tests.md`](../plans/2026-09-16-graph-behaviour-tests.md).

## 1. The goal, and why it is not already met

The graph-sync branch (`dev-graph`, merged 2026-09-16) wired every writer to `graph_sync` and made the wiring
blocking in CI: `ci/writers.py` declares all 30 writer sites and `ci/gate/test_writer_registry.py` fails the job when
a new writer appears without a hook or a reason code. That gate is real and it passes.

It proves that a writer *calls* something. It proves nothing about the graph.

Measured 2026-09-16 on this tree: no test anywhere asserts a graph change after a write. `ci/smoke/test_write_lane.py`
checks API responses and MySQL rows; its single mention of the graph asserts that the graph step was **skipped** in a
dry run. Every graph test across all four sync runs used fakes, stubs or a throwaway database. No Cypher this branch
wrote has executed against a real Neo4j except one lane run, which failed.

This spec closes both gaps, in that order: first make a 1.2 full sync survive production memory bounds, then assert
that each write path changes the graph.

## 2. What the first real execution found

The Nessie session ran `graph_sync --full` in the throwaway lane on 2026-09-16 (reported in
`graph-search/runs/sync-lane/full12/full_sync.json`). It failed after about 4.5 minutes:

```
Neo.TransientError.General.MemoryPoolOutOfMemoryError: the allocation of an extra 2.0 MiB
would use more than the limit 512.0 MiB, db.memory.transaction.max threshold reached.
```

Exactly 60,000 samples carried `source_hash`, so 12 chunks of 5,000 landed and the 13th failed. `GraphMeta` still
read 1.1, because the version is stamped at the end, so the graph was left half written.

### 2.1 It is per-transaction, not accumulation

Settled from code and data, with no further run:

- `db.memory.transaction.max` is Neo4j 5's ceiling for a **single** transaction. Accumulation across transactions
  trips `db.memory.transaction.total.max`, which is not what fired.
- There is no outer transaction to accumulate in. `nextseek_api/graph_sync/run.py` contains no `session`, no
  `begin_transaction` and no `execute_write`. Every write goes through `writer._run` to `driver.execute_query`
  (`nextseek_api/graph_sync/writer.py:495`), which is one managed transaction per call, committed before it returns.

### 2.2 Chunk 13 is the heaviest chunk it reached

Measured on the merged data (1,084,754 samples), grouping by `ROW_NUMBER() OVER (ORDER BY id)` in blocks of 5,000,
which is the order `write_samples` reads:

| Chunk | Sample ids | json_metadata | avg/row | max row |
|---|---|---|---|---|
| 1 to 12 (all landed) | 1 to 87,748 | 7.4 to 10.5 MB | 1,500 to 2,200 B | 3 to 9 KB |
| **13 (failed here)** | **87,749 to 92,985** | **13.4 MB** | **2,813 B** | **14,368 B** |
| **34** | **251,824 to 393,699** | **18.5 MB** | **3,874 B** | 6,939 B |
| **35** | **393,700 to 398,699** | **21.7 MB** | **4,559 B** | 4,930 B |
| All 217 chunks | 1 to 1,308,453 | 1,124 MB | 1,087 B | 26,005 B |

Chunk 13 is the third heaviest of all 217. The run died on the heaviest chunk it had reached, on its own merits.

Chunk 35 is the heaviest chunk in the corpus and its samples are types `PAV` and `TIS`. TCGA as a whole is lighter
than the rest (976 B average over its 918,519 samples, against 1,698 B over the other 166,235), so the risk is
concentrated in these two chunks near row 165,000 to 175,000, which the failed run never reached.

### 2.3 The amplification is about 38x, and production bounds are tighter than the lane's

13.4 MB of source metadata required more than 512 MiB of transaction memory. At that ratio chunk 35 (21.7 MB)
projects to roughly 830 MiB. `dev-graph` now caps the live Neo4j at `db.memory.transaction.max` 1g (commit
`65782731`), so a live `--full` at the default `--chunk 5000` would land within 20 percent of the ceiling, or over
it. **Tuning to the lane's old 512m does not represent production and must not be the acceptance bar.**

### 2.4 What 1.2 added to that write

`WRITE_SAMPLES` is byte-identical to the 1.1 statement that completed the same lane in 427 s, except for two `CASE`
expressions. The deltas:

1. **Bigger rows.** `parent_titles` and `parent_title_hashes` now travel inside `r.props`
   (`projection.py:257-258`). In 1.1 the statement read them off the existing node
   (`WITH s, r, s.parent_titles AS pt`). That is decision R4, projection-owned. `source_hash` was also added, 64
   hex characters, small.
2. **Two `keys(r.props)` evaluations per row**:
   ```cypher
   CASE WHEN 'parent_titles' IN keys(r.props) THEN r.props.parent_titles ELSE s.parent_titles END
   CASE WHEN 'parent_title_hashes' IN keys(r.props) THEN r.props.parent_title_hashes ELSE s.parent_title_hashes END
   ```
   `keys()` materialises the row's whole property key list, twice per row, for every row in the transaction.

Note also that `search_text` already duplicates every raw metadata value into the props
(`projection.py:253`), so the property map is roughly twice the source metadata before 1.2 adds anything.

## 3. Decisions

| Id | Decision |
|---|---|
| **D1** | Behavioural assertions are made over **HTTP only**: `graph_search`, the graph-sync status endpoint, and the endpoint under test. No Neo4j driver. `ci/writers.py`'s header states the constraint: the smoke lane holds pytest, requests and playwright and nothing else. |
| **D2** | `graph_search` **matches in the graph and hydrates from MySQL** (`nextseek_api/graph_search/hydrate.py`). So a graph assertion is expressed as a **filter only the graph can satisfy**, never by reading a field off a returned row. A hit proves the graph holds that property with that value; zero hits proves it does not. |
| **D3** | `source_hash` is asserted through the **drift check**, which recomputes it from MySQL and compares. Clean drift after a sync proves the hashes agree; non-zero `changed` before the sync proves the hash moved. |
| **D4** | `schema_version`, `catalog_hash`, `synced_at` and `label_maps_hash` are asserted through the **status endpoint**, which carries them under `runs.drift.drift.stats.graphmeta` (observed live 2026-09-16). |
| **D5** | Stage A (memory bounds) runs **only in the throwaway lane**. Stage B (behaviour) runs against the **live local stack**, because five of the six paths are HTTP endpoints and three of them proxy to SEEK, which the lane does not have. |
| **D6** | Only batch upload syncs inline (`orchestrator.py:580` calls `targeted.sync_samples`). The other five paths only enqueue, so every loop-dependent assertion **polls the status endpoint until the outbox drains**, with a bounded timeout. A timeout fails the test and prints the outbox contents. |
| **D7** | Stage B tests **create their own objects and delete them**. They never assert against pre-existing production rows, so a failure cannot be explained by another session's data. The destructive ones stay behind `CI_WRITE_DESTRUCTIVE=1`, as `test_write_lane.py` already does. |
| **D8** | No feature is reported as working without a run and its numbers. Where a path cannot be exercised, the report says so rather than inferring it from unit tests. |
| **D9** | Stage B is **gated on the operator's first live `graph_sync --full --i-mean-the-live-graph`**, and that is gated on Stage A passing. Until the graph is at 1.2 the writer refuses every write and all six assertions would fail by design. |

## 4. Coordination constraints (in force at writing)

1. While `graph-search/.gs-bench-running` exists, start no container and do not touch the live stack. The Nessie POC
   session creates and removes it.
2. Nothing is written to the live graph until the POC session says the POC is done. The POC derives its answer key
   from the live 1.1 graph and a write underneath it voids paid work.
3. Stage A needs the host's memory. It begins only after the POC session's green light.

## 5. Stage A: the 1.2 full sync inside production bounds

**Acceptance:** one `graph_sync --full` completes against a Neo4j configured exactly like the live one
(`db.memory.transaction.max` 1g, heap 2g, page cache 2g, container 6G), writes `GraphMeta.schema_version` 1.2, and
gate G passes on the result. Wall time is recorded and compared against the 1.1 baseline of 427 s.

**Starting state.** The lane volume `gs-v11-neo4j-data` holds the POC's v1.1 graph, half written by the failed run:
60,000 samples carry `source_hash`, the catalog was rewritten to 3,530 declared attributes, the 38 undeclared
attribute nodes are gone, `GraphMeta` still reads 1.1. It must be reset before any measurement, or every number is
taken against a graph in a state no box will ever be in.

**The three probes.** `--samples` over the id ranges in section 2.2 reaches the heaviest rows in minutes rather than
an hour, and is the A/B surface for the `keys(r.props)` change.

**The candidate fixes, in order of confidence:**

1. **Chunk size chosen against chunk 35, not the average.** `writer.SAMPLE_CHUNK = 5_000` is described in the code as
   "the design's default". It was not chosen against this corpus.
2. **Remove the two `keys()` calls.** `projection.py:257-258` sets both keys or neither, and when set they are lists,
   possibly empty, never null. So `CASE WHEN r.props.parent_titles IS NOT NULL THEN ... ELSE s.parent_titles END` is
   equivalent and builds no key list. This is a hypothesis until the A/B measures it.
3. **If neither suffices:** split the statement so the property write and the edge rebuild are separate transactions.
   This is a larger change and is only reached if 1 and 2 leave chunk 35 over 1g.

## 6. Stage B: the six behavioural assertions

Each ends with an assertion that the graph changed. Endpoints are the registry's, by writer id.

| # | Path | Endpoint(s) | Writer | Graph assertion | Needs SEEK |
|---|---|---|---|---|---|
| **B1** | Batch upload lands samples | `POST /nextseek_api/batch-upload/start/` | WR-01, WR-02, WR-03, WR-04 | `graph_search` filtered on the new UID and on a distinctive attribute value returns the new samples; totals report `graph: synced (N)`, not `pending` | no |
| **B2** | Attribute create, rename, delete | `POST /nextseek_api/attributes/batch-create/`, `PATCH .../batch-patch/`, `POST .../batch-delete/` | WR-05 | after the drain, `graph_search` on the new attribute name returns the type's samples and on the old name returns none; `catalog_hash` moves | no (token auth) |
| **B3** | Sample update | `PATCH /nextseek_api/samples/{id}/` | WR-07 | `graph_search` on the new value hits and on the old value misses; drift reports the sample changed before the drain and clean after | yes |
| **B4** | Sample delete retires the node | `DELETE /nextseek_api/samples/{id}/` | WR-07, WR-13 | `graph_search` on the UID returns nothing; the run's `retired.tsv` names it | yes |
| **B5** | Membership or project change | `POST`/`PATCH /nextseek_api/users/`, `POST`/`PATCH /nextseek_api/projects/` | WR-09, WR-10 | a `graph_search` as the affected account gains or loses the samples, which is scope resolved from the graph | yes |
| **B6** | Drift clean, loop drains unaided | `GET /nextseek_api/admin/graph-sync/status/` | n/a | the outbox empties with no manual command, freshness reaches `ok`, drift passes with 0 changed and 0 missing before and after each of B1 to B5 | no |

**B2's authentication is the finding that makes it lane-capable if ever needed.**
`nextseek_api/attributes/auth.py:115` proves the SEEK person by a **MySQL lookup** when the scheme is a DRF token,
and only calls SEEK over HTTP for basic and session schemes. So B2 under token auth needs no SEEK. B1's endpoint uses
`TokenAuthentication, CsrfExemptSessionAuthentication, BasicAuthentication` with `IsAuthenticated`
(`nextseek_api/batch_upload/views.py:101`), also no SEEK.

B3, B4 and B5 proxy to SEEK by definition, so they cannot run without it, and per **D8** that is reported rather than
worked around.

## 6.1 Two prerequisites, folded in from the 2026-09-16 CI triage

Both were first written up as standalone issues. Each is load-bearing for Stage B, so each is a task here and the
issue records the public history rather than the work.

**P1, smoke route discovery picks objects the smoke account cannot load.** `ci/smoke/conftest.py` resolves
`seek_project_id` and `sample_type_id` by taking the first row of an unscoped list. Since the TCGA merge those are a
project the smoke account is not a member of and a sample type with 283,311 samples, which is why four routes went
red on 2026-09-16 at 15:26. This is not merely adjacent: **B5 needs the corrected version of exactly that lookup** to
find a project the read account belongs to. Left separate, the same logic gets written twice and only the private
copy is correct.

**P2, `seekapi` has no fetch timeout and no `None` guard.** `getPageRequests` calls `requests.get` with no timeout
and `__getHtmlpageDiv` calls `.prettify()` on a `find()` that returns `None` when the div is absent, so any SEEK
hiccup becomes a 500. B3, B4 and B5 drive SEEK-proxied endpoints against a SEEK that was killed under memory
pressure on 2026-09-16 and has only just been capped at 4G. Without this, a SEEK stumble mid-test fails a graph
assertion for a reason that has nothing to do with the graph, which is the exact failure mode this spec exists to
eliminate.

## 6.2 Found in passing: the assistant is taught investigation names that resolve to nothing

Not part of this plan's work, recorded here because it strengthens the ruling section 6.1's cause 2 needs, and
because it is the same divergence seen from the assistant's end.

`NessieAI/chat_nextseek/src/chat_nextseek/context/capabilities.md` lists eight investigation names under "Known
Projects and Investigations" and instructs: **"Use these names exactly when asking graph questions scoped to a
specific project."**

Measured by the POC session on the frozen 1.1 graph: `Griffith`, `Impact`, `Shoulders` and `SRP` hold zero studies
and zero samples, and **no investigation carries the title `GBM` at all**. The populated investigations are
`Impactb Investigation` (84,397 samples), `MIT_SRP` (55,699) and `GBM_BTC` (4,564). Only `CSBC` and `MetNet` resolve
to something populated.

**Two separate defects sit inside that, and only one is fixed by ruling on cause 2:**

- **Empty but correctly named.** `Griffith`, `Impact`, `Shoulders` and `SRP` exist as investigation titles (the
  `TestProject_250820` copies, ids 16 to 21) and are empty *because* the paper studies hang off the legacy
  investigations instead. Repairing the linkage populates them and these names start working.
- **Wrong name, independent of linkage.** `GBM` is not an investigation title in the graph at any population. No
  amount of relinking makes `GBM` resolve; the data is under `GBM_BTC`. This is a documentation error in
  `capabilities.md` and it survives every fix contemplated in section 6.1.

So the ruling on cause 2 is not only a modelling question. As things stand the assistant is instructed to use names
of which half resolve to empty nodes and one resolves to nothing, and it is told to use them exactly.

## 7. Out of scope

- Writing anything to the live graph before the operator's go.
- The V1 label decisions (55,307 stale `internal_assay_title`, 2,976 sheet-only protocols). Those are the operator's
  call at review point 2 and are not testing.
- The migration check under `dmac.test_settings` (`ISSUE-DRAFTS.md` Draft 1) and context-file health checking in CI.
  Both are covered by [`2026-09-16-ci-coverage-gaps-design.md`](2026-09-16-ci-coverage-gaps-design.md). The context
  checks in particular must be written against the finalised context shape, not the current one.

Two of the four CI issues triaged on 2026-09-16 were originally out of scope here and have been folded in, because
each is load-bearing for Stage B rather than merely adjacent. See section 6.1.

## 8. Unverified at writing

- The 38x amplification ratio is one data point from one failed run. Stage A measures it properly.
- That removing `keys()` saves anything at all. A1 to A4 measure it.
- That a full sync completes inside 1g at any chunk size. That is the whole point of Stage A.
- Whether the live local stack's SEEK can serve B3 to B5 without falling over. It was SIGKILLed at 19:30 on
  2026-09-16 under the merged data and has since been capped at 4G.

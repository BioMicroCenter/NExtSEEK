# Graph 1.2 Transaction Bounds and Behavioural Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or
> superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a 1.2 full sync complete inside production memory bounds, then prove by running them that each write
path changes the graph.

**Architecture:** Three stages, strictly ordered. Stage A works only in the throwaway lane and ends with a `--full`
that completes inside the live Neo4j's configured bounds. Stage P fixes the two 2026-09-16 CI defects that Stage B
depends on. Stage B adds a permanent behavioural write lane to `ci/smoke/` that drives real endpoints against the
live local stack and asserts the resulting graph change over HTTP.

**Gate between A and B that this plan does not control:** the operator's first live
`graph_sync --full --i-mean-the-live-graph`. Stage B cannot run before it, and it cannot run before Stage A passes.

**Tech Stack:** Python 3.14, Django, pytest, the neo4j Python driver (lane only), `requests` (smoke lane only),
Docker, `scripts/graph_search/lane.sh`.

**Spec:** [`docs/superpowers/specs/2026-09-16-graph-behaviour-tests-design.md`](../specs/2026-09-16-graph-behaviour-tests-design.md)

## Global Constraints

- `GS_WORK=/home/cdemurjian/code/dmac/docker/graph-search` for every lane command.
- Run `lane.sh` from a **clean worktree** with no `dmac/local_settings.py`. This plan assumes
  `/home/cdemurjian/code/dmac/docker/wt-gs-sync`. `docker/dev` has a rendered `local_settings.py` and dies with
  "GCP mode selected but GCP_API_KEY is not set".
- **Before starting any container, check `graph-search/.gs-bench-running` and stop if it exists.** Every time, not
  once. The Nessie POC session owns that flag.
- **Nothing writes to the live graph until the operator says so.** Stage A never touches the live Neo4j. Stage B
  begins only after the operator's first live `graph_sync --full --i-mean-the-live-graph`.
- The lane Neo4j must be configured to match the live one, from `dev-graph` commit `65782731`:
  `GS_NEO4J_TX_MAX=1g GS_NEO4J_HEAP=2g GS_NEO4J_PAGECACHE=2g GS_NEO4J_MEMORY=6G`.
- No number is reported without the run that produced it. Where a path cannot be exercised, say so.
- Commit messages end with the two trailer lines this repo uses.
- No em dashes or en dashes in added lines or commit messages.

---

# Stage A: the full sync inside production bounds

### Task A1: Reset the lane graph to a clean v1.1

**Files:**
- Run only, no repository change.

**Interfaces:**
- Produces: volume `gs-v11-neo4j-data` holding the unmodified v1.1 graph, so every Stage A measurement starts from
  the state a real box would be in.

**Why:** the volume currently holds the POC's v1.1 graph **half written** by the failed 1.2 run: 60,000 samples carry
`source_hash`, the catalog was rewritten to 3,530 declared attributes, the 38 undeclared attribute nodes are gone,
and `GraphMeta` still reads 1.1. Measuring against that measures nothing.

- [ ] **Step 1: Confirm the bench flag is absent and the POC has given the green light**

```bash
test -e /home/cdemurjian/code/dmac/docker/graph-search/.gs-bench-running && \
  { echo "BENCH RUNNING - stop here"; exit 1; } || echo "clear to proceed"
```

- [ ] **Step 2: Stop the lane Neo4j and drop the dirty volume**

```bash
export GS_WORK=/home/cdemurjian/code/dmac/docker/graph-search
cd /home/cdemurjian/code/dmac/docker/wt-gs-sync
scripts/graph_search/lane.sh neo4j-down
docker volume rm gs-v11-neo4j-data
```

- [ ] **Step 3: Restore the v1.1 archive into a fresh volume**

```bash
docker run --rm \
  -v gs-v11-neo4j-data:/data \
  -v "$GS_WORK/seeds/v11-graph-2026-09-14":/backups:ro \
  neo4j:latest \
  neo4j-admin database load neo4j --from-path=/backups --overwrite-destination=true
```

- [ ] **Step 4: Start Neo4j and prove the restore matches the recorded counts**

```bash
scripts/graph_search/lane.sh neo4j-up
scripts/graph_search/lane.sh neo4j-cypher \
  'MATCH (s:Sample) RETURN count(s) AS samples' 
scripts/graph_search/lane.sh neo4j-cypher \
  'MATCH (s:Sample) WHERE s.source_hash IS NOT NULL RETURN count(s) AS with_hash'
scripts/graph_search/lane.sh neo4j-cypher \
  'MATCH (m:GraphMeta) RETURN m.schema_version AS v'
diff <(cat "$GS_WORK/seeds/v11-graph-2026-09-14/counts.txt") /dev/null | head -20
```

Expected: the Sample count matches `counts.txt`; `with_hash` is **0**, which is the proof the half-written state is
gone; `schema_version` is `1.1`.

- [ ] **Step 5: Record the baseline**

Write `$GS_WORK/runs/sync-lane/A1-reset.json` holding the three numbers above and the timestamp. No commit.

---

### Task A2: Reconfigure the lane to production bounds and reproduce the failure

**Files:**
- Run only, no repository change.

**Interfaces:**
- Consumes: A1's clean volume.
- Produces: the measured per-transaction peak at `--chunk 5000` under a 1g ceiling, which is the number the fix has
  to beat.

- [ ] **Step 1: Bring Neo4j up with the live bounds**

```bash
export GS_WORK=/home/cdemurjian/code/dmac/docker/graph-search
export GS_NEO4J_TX_MAX=1g GS_NEO4J_HEAP=2g GS_NEO4J_PAGECACHE=2g GS_NEO4J_MEMORY=6G
cd /home/cdemurjian/code/dmac/docker/wt-gs-sync
scripts/graph_search/lane.sh neo4j-down && scripts/graph_search/lane.sh neo4j-up
```

- [ ] **Step 2: Probe chunk 13 alone, at the current default**

```bash
scripts/graph_search/lane.sh app graph_sync --samples 87749-92985 --chunk 5000 --json \
  > "$GS_WORK/runs/sync-lane/A2-chunk13-5000.json" 2>&1 || true
```

Expected: it now **passes**, because 512 MiB was the old ceiling and 830 MiB is the projection for the worst chunk,
not for chunk 13. Record whether it passed and the wall time either way.

- [ ] **Step 3: Probe chunk 35, the heaviest in the corpus**

```bash
scripts/graph_search/lane.sh app graph_sync --samples 393700-398699 --chunk 5000 --json \
  > "$GS_WORK/runs/sync-lane/A2-chunk35-5000.json" 2>&1 || true
```

Expected: this is the one that decides the work. If it fails with
`MemoryPoolOutOfMemoryError ... db.memory.transaction.max`, the default chunk is unsafe in production and Task A3
is required. If it passes, record the margin and go to A5 directly, skipping A3 and A4.

- [ ] **Step 4: Probe chunk 34**

```bash
scripts/graph_search/lane.sh app graph_sync --samples 251824-393699 --chunk 5000 --json \
  > "$GS_WORK/runs/sync-lane/A2-chunk34-5000.json" 2>&1 || true
```

- [ ] **Step 5: Record**

Write `$GS_WORK/runs/sync-lane/A2-summary.md` with a row per probe: chunk, id range, chunk size, pass or fail, wall
seconds, and the error text when it failed. No commit.

---

### Task A3: A/B the `keys(r.props)` removal

**Files:**
- Modify: `nextseek_api/graph_sync/cypher.py` (the `WRITE_SAMPLES` statement)
- Test: `nextseek_api/tests/test_graph_sync_writer.py`

**Interfaces:**
- Consumes: A2's chunk 35 measurement as the before number.
- Produces: `WRITE_SAMPLES` with no `keys()` call, semantics unchanged.

**Run this task only if A2 step 3 failed.**

- [ ] **Step 1: Write the failing test that pins the equivalence**

Add to `nextseek_api/tests/test_graph_sync_writer.py`:

```python
class TestParentListsFallBackToTheNodeWhenTheRowOmitsThem:
    """WRITE_SAMPLES keeps the node's parent lists when the projection did not supply them (schema 1.2, R4)."""

    def test_the_statement_does_not_call_keys(self):
        # keys() materialises the row's whole property key list, twice per row, for every row in the
        # transaction. The projection sets both parent keys or neither (projection.py:257-258) and never
        # sets them to null, so an IS NOT NULL test is equivalent and allocates nothing.
        assert "keys(r.props)" not in cypher.WRITE_SAMPLES

    def test_both_parent_keys_are_still_guarded(self):
        assert "r.props.parent_titles IS NOT NULL" in cypher.WRITE_SAMPLES
        assert "r.props.parent_title_hashes IS NOT NULL" in cypher.WRITE_SAMPLES
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd /home/cdemurjian/code/dmac/docker/wt-gs-sync
mkdir -p schema_rag/duckdb schema_rag/embedding_models
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest nextseek_api/tests/test_graph_sync_writer.py -q -p no:cacheprovider \
  -k ParentListsFallBack
```

Expected: FAIL, `keys(r.props)` is present.

- [ ] **Step 3: Replace the two CASE expressions**

In `nextseek_api/graph_sync/cypher.py`, inside `WRITE_SAMPLES`, replace:

```cypher
WITH s, r,
     CASE WHEN 'parent_titles' IN keys(r.props) THEN r.props.parent_titles ELSE s.parent_titles END AS pt,
     CASE WHEN 'parent_title_hashes' IN keys(r.props) THEN r.props.parent_title_hashes
          ELSE s.parent_title_hashes END AS pth
```

with:

```cypher
WITH s, r,
     CASE WHEN r.props.parent_titles IS NOT NULL THEN r.props.parent_titles ELSE s.parent_titles END AS pt,
     CASE WHEN r.props.parent_title_hashes IS NOT NULL THEN r.props.parent_title_hashes
          ELSE s.parent_title_hashes END AS pth
```

- [ ] **Step 4: Run the whole writer and projection suite**

```bash
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest nextseek_api/tests/test_graph_sync_writer.py \
  nextseek_api/tests/test_graph_sync_projection.py nextseek_api/tests/test_graph_sync_full.py \
  nextseek_api/tests/test_graph_sync_targeted.py -q -p no:cacheprovider
```

Expected: PASS, 311 tests or more, 0 failed.

- [ ] **Step 5: Re-probe chunk 35 and compare against A2**

```bash
scripts/graph_search/lane.sh app graph_sync --samples 393700-398699 --chunk 5000 --json \
  > "$GS_WORK/runs/sync-lane/A3-chunk35-5000-nokeys.json" 2>&1 || true
```

Record the before and after. **If it still fails, say so plainly and go to Task A4.** The hypothesis is then
falsified and the chunk size is the lever, not `keys()`.

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/graph_sync/cypher.py nextseek_api/tests/test_graph_sync_writer.py
git commit -m "perf(graph_sync): drop the per-row keys() calls from the sample write

The projection sets both parent keys or neither and never sets them to null,
so IS NOT NULL is equivalent and does not materialise the row's key list
twice per row for every row in the transaction."
```

---

### Task A4: Choose the chunk default against chunk 35

**Files:**
- Modify: `nextseek_api/graph_sync/writer.py:45`
- Test: `nextseek_api/tests/test_graph_sync_writer.py`

**Interfaces:**
- Consumes: A2 and A3 measurements.
- Produces: `SAMPLE_CHUNK` set to a value measured to fit chunk 35 inside 1g with margin.

**Run this task only if chunk 35 still fails after A3.**

- [ ] **Step 1: Find the largest chunk size that passes chunk 35**

Bisect. Run each and record peak and wall time:

```bash
for n in 2500 1500 1000; do
  scripts/graph_search/lane.sh app graph_sync --samples 393700-398699 --chunk "$n" --json \
    > "$GS_WORK/runs/sync-lane/A4-chunk35-$n.json" 2>&1 || true
done
```

- [ ] **Step 2: Write the test pinning the new default**

```python
def test_the_sample_chunk_is_sized_for_the_heaviest_chunk():
    """Measured 2026-09-16: the heaviest 5,000 rows in the corpus (ids 393700-398699) carry 21.7 MB of
    json_metadata and did not fit db.memory.transaction.max=1g, which is what the live neo4j is capped at
    (docker-compose.yml, dev-graph 65782731). See docs/superpowers/plans/2026-09-16-graph-behaviour-tests.md
    Task A4 for the bisection."""
    assert writer.SAMPLE_CHUNK <= 2_500
```

- [ ] **Step 3: Run it and watch it fail**

Expected: FAIL, `SAMPLE_CHUNK` is 5000.

- [ ] **Step 4: Set the new default with the measurement in the comment**

In `nextseek_api/graph_sync/writer.py:45`, replace the line, substituting the measured value for `<N>` and the
measured megabytes for `<MB>`:

```python
SAMPLE_CHUNK = <N>            # samples per write transaction. Measured 2026-09-16: 5,000 rows of the heaviest
                              # region (ids 393700-398699, <MB> MB of json_metadata) exceed the live neo4j's
                              # db.memory.transaction.max of 1g. Sized against that region, not the average.
```

- [ ] **Step 5: Run the blocking lane**

```bash
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest $(python3 ci/blocking_lanes.py | tr '\n' ' ') -q -p no:cacheprovider
```

Expected: PASS, 1446 or more, 0 failed.

- [ ] **Step 6: Commit**

```bash
git add nextseek_api/graph_sync/writer.py nextseek_api/tests/test_graph_sync_writer.py
git commit -m "fix(graph_sync): size the sample write chunk against the heaviest region"
```

---

### Task A5: Prove a real `--full` completes inside production bounds

**Files:**
- Run only, then one report file.

**Interfaces:**
- Consumes: A1's clean volume, A3 and A4's fixes.
- Produces: **the acceptance evidence for Stage A.** Without this, nothing below may start.

- [ ] **Step 1: Reset the volume again**

Repeat Task A1 steps 2 to 4. The probes in A2 to A4 wrote 1.2 rows into the graph, so a `--full` over that is not
the run a real box performs.

- [ ] **Step 2: Run the full sync, timed, at the production bounds**

```bash
export GS_NEO4J_TX_MAX=1g GS_NEO4J_HEAP=2g GS_NEO4J_PAGECACHE=2g GS_NEO4J_MEMORY=6G
time scripts/graph_search/lane.sh app graph_sync --full --json \
  > "$GS_WORK/runs/sync-lane/A5-full12.json" 2>&1
```

- [ ] **Step 3: Verify the graph reached 1.2 and is whole**

```bash
scripts/graph_search/lane.sh neo4j-cypher 'MATCH (m:GraphMeta) RETURN m.schema_version AS v, m.catalog_hash AS h'
scripts/graph_search/lane.sh neo4j-cypher \
  'MATCH (s:Sample) RETURN count(s) AS samples, count(s.source_hash) AS with_hash'
```

Expected: `schema_version` is `1.2`; `samples` equals 1,084,754; `with_hash` equals `samples`. A shortfall in
`with_hash` means the run stopped early and Stage A has **not** passed.

**Record the projection numbers, as an observation rather than a gate.** Sample completeness is already guarded
twice and neither guard needs adding:

- Any projection error refuses the whole run **before its first write**. `_preflight` collects `scan.errors` into
  `problems`, and `full_sync` raises `PreflightError` when that list is non-empty, so `_write` never runs and the
  report's status is `refused`, not `ok`. A completed run therefore implies zero projection errors.
- Gate G independently compares the graph's Sample count against MySQL's
  (`verify.py` check `4.samples.graph_count`), which A5 Step 4 already runs.

So read the numbers for the record, not to catch anything:

```bash
python3 -c "import json; r=json.load(open('$GS_WORK/runs/sync-lane/A5-full12.json')); \
print({k: r.get(k) for k in ('status','samples_read','samples_projected','projection_errors','problems')})"
```

Expected: `status` `ok`, `projection_errors` 0, `samples_projected` equal to `samples_read`. A `refused` status with
a projection problem names the sample ids in `projection_error_examples`.

- [ ] **Step 4: Run gate G on the result**

```bash
scripts/graph_search/lane.sh app graph_sync --verify --json > "$GS_WORK/runs/sync-lane/A5-gateg.json" 2>&1
```

Expected: every check passes. Record any that do not; a failing check here is a real 1.2 defect and blocks Stage B.

- [ ] **Step 5: Run the drift check and confirm it is clean**

```bash
scripts/graph_search/lane.sh app graph_sync --drift --json > "$GS_WORK/runs/sync-lane/A5-drift.json" 2>&1
```

Expected: `pass` is true, `changed` 0, `missing_in_graph` 0, `not_in_mysql` 0.

- [ ] **Step 5a: Measure the SEEK study shortfall (non-blocking)**

Measured by the POC session on 2026-09-16 against the live SEEK database and the frozen 1.1 graph: **42 of 81 SEEK
studies have no Study node**, and the graph's 39 nodes carrying `seek_study_id` is what is left. **A full sync
entrenches this rather than fixing it**, so the first real 1.2 sync must report it instead of leaving it invisible.

Two distinct causes, both established in code, and the arithmetic closes on the measured 39:

| Component | Count | Cause |
|---|---|---|
| Structurally ineligible | 40 | The full sync's only study step (`run.py:802`) is fed `sources.seek_study_links()`, an inner join through `assay_assets` on `asset_type='Sample'`. A study with no sample-bearing assay yields no row and so no node, however often the sync runs. `sources.studies()` is unfiltered but is used only as a title lookup in `_study_rekey_plan` (`run.py:661`) and never creates a node. |
| Eligible but suppressed | 2 | In `writer.write_seek_studies` the `studies.setdefault(...)` sits **below** the `if sample_id in in_paper: continue`, so a study whose samples are all in paper-level Study nodes never reaches it. The skip is meant to suppress only the IN_STUDY edge. Recoverable by moving that one line above the `if`. |
| Written | 39 | 81 minus 40 minus 2. Matches the measured graph total. |

```bash
scripts/graph_search/lane.sh neo4j-cypher \
  'MATCH (s:Study) WHERE s.seek_study_id IS NOT NULL RETURN count(s) AS keyed'
scripts/graph_search/lane.sh mysql \
  'SELECT COUNT(*) AS seek_studies FROM seek_production.studies;'
scripts/graph_search/lane.sh mysql \
  "SELECT s.id, s.title FROM seek_production.studies s WHERE NOT EXISTS (
     SELECT 1 FROM seek_production.assays a
     JOIN seek_production.assay_assets aa ON aa.assay_id = a.id AND aa.asset_type = 'Sample'
     WHERE a.study_id = s.id);"
```

Report the three components separately, not as one shortfall. A single number hides that 40 are unreachable by any
sync while 2 are recoverable by moving one line.

Also record the DERIVED_FROM total before and after this run. The POC session measured a corpus lineage statement
moving from 3,061 in August to 3,728 now on data the TCGA merge never touched, and once a live full sync runs that
becomes unattributable. Stage A resets from the 09-14 v1.1 snapshot and then syncs the same data, so a clean before
and after costs nothing here:

```bash
scripts/graph_search/lane.sh neo4j-cypher \
  'MATCH ()-[e:DERIVED_FROM]->() RETURN count(e) AS derived_from'
```

**None of this fails Stage A**: all of it predates this work, and failing acceptance on it would block the
transaction fix for unrelated defects. It is measured so the operator can decide, and so the numbers exist before
the live sync rather than after it.

- [ ] **Step 6: Write the Stage A report and commit it**

Create `graph-search/runs/sync-lane/A5-REPORT.md` (untracked, outside the repository) with: the wall time against the
1.1 baseline of 427 s, the chunk size used, every probe's number from A2 to A4, the gate G result and the drift
result. Then commit only the code changes already made in A3 and A4; the reports stay out of the repository because
they name real sample data.

---

# Stage P: prerequisites for Stage B

Both tasks come from the 2026-09-16 CI triage and both are load-bearing for Stage B (spec section 6.1). Neither
needs the 1.2 graph, so both may run as soon as the POC releases the box. P2 needs no stack at all.

### Task P1: Discovery resolves objects the smoke account can actually load

**Files:**
- Modify: `ci/smoke/conftest.py` (the `_JSONAPI_LIST_SOURCE` map and the resolver that reads it)
- Test: `ci/smoke/test_registry_contents.py`

**Interfaces:**
- Produces: `seek_project_id` is a project the smoke account belongs to, and `sample_type_id` is the type of a
  sample that account can already see. Task B5 consumes the project resolver instead of writing its own.

**Why:** four routes went red on 2026-09-16 at 15:26 because discovery took the first row of an unscoped list. The
project routes are membership-gated and denied correctly; the sample-type routes timed out on a type with 283,311
samples.

- [ ] **Step 1: Write the failing test**

Add to `ci/smoke/test_registry_contents.py`:

```python
def test_project_discovery_does_not_read_the_unscoped_list():
    """The project routes are membership-gated, so the discovered project must be one the smoke account
    belongs to. /nextseek_api/projects/ returns every project ordered by updated_at, so its first row is
    whichever project was touched last. On 2026-09-16 that was a project the account is not in and four
    routes went red against correct product behaviour."""
    source = conftest._JSONAPI_LIST_SOURCE
    assert "seek_project_id" not in source, (
        "seek_project_id must come from the caller's own memberships, not from the unscoped project list"
    )


def test_sample_type_discovery_does_not_read_the_unscoped_list():
    """The type detail pages render that type's samples, so a type with hundreds of thousands of them
    times out. Deriving the type from a sample the account can already see keeps it both visible and
    modest."""
    assert "sample_type_id" not in conftest._JSONAPI_LIST_SOURCE
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd /home/cdemurjian/code/dmac/docker/wt-gs-sync/ci/smoke
python -m pytest test_registry_contents.py -q -k discovery
```

Expected: FAIL, both keys are still in `_JSONAPI_LIST_SOURCE`.

- [ ] **Step 3: Move both keys to their own resolvers**

Remove `"seek_project_id"` and `"sample_type_id"` from `_JSONAPI_LIST_SOURCE` and add beside the other bespoke
resolvers:

```python
# The caller's own projects, which is what the membership gate in seek/views/projects.py checks. Verified
# 2026-09-16: this endpoint answers the smoke account with exactly its two memberships, where
# /nextseek_api/projects/ answers with all fourteen ordered by updated_at.
_CURRENT_PERSON = "/nextseek_api/people/current/"


def _own_project_id(api, base_url):
    r = api.get(f"{base_url}{_CURRENT_PERSON}", timeout=60)
    if r.status_code != 200:
        return None
    data = (r.json() or {}).get("data") or {}
    projects = ((data.get("relationships") or {}).get("projects") or {}).get("data") or []
    ids = sorted(int(p["id"]) for p in projects if p.get("id"))
    return str(ids[0]) if ids else None


def _visible_sample_type_id(api, base_url, sample_id):
    """The type of a sample the account can already see, so the type is both visible and of workable size."""
    if not sample_id:
        return None
    r = api.get(f"{base_url}/nextseek_api/samples/{sample_id}/", timeout=60)
    if r.status_code != 200:
        return None
    attrs = ((r.json() or {}).get("data") or {}).get("attributes") or {}
    value = attrs.get("sample_type_id")
    return str(value) if value else None
```

Then call them where the other bespoke values are resolved, passing the already-discovered `sample_id`.

- [ ] **Step 4: Run the registry tests and confirm they pass**

```bash
python -m pytest test_registry_contents.py test_registry_unit.py -q
```

Expected: PASS. `test_registry_contents.py` asserts the discovery vocabulary matches `ci/routes.py`, so a key moved
without its resolver fails here rather than mid-sweep.

- [ ] **Step 5: Run the reachability sweep against the live stack**

```bash
cd /home/cdemurjian/code/dmac/docker/wt-gs-sync/ci/smoke
python -m pytest test_reachability.py -q
```

Expected: the four routes that failed on 2026-09-16 now pass. Record which project id and sample type id were
discovered, so the change is verifiable rather than asserted.

- [ ] **Step 6: Commit**

```bash
git add ci/smoke/conftest.py ci/smoke/test_registry_contents.py
git commit -m "test(ci): discover a project the smoke account is in and a sample type it can load"
```

---

### Task P2: The SEEK page scrape survives a slow or unexpected SEEK

**Files:**
- Modify: `seek/seekapi.py` (`getPageRequests`, `__getHtmlpageDiv`)
- Test: `seek/tests/test_seekapi_page_requests.py` (create)

**Interfaces:**
- Produces: a SEEK hiccup no longer becomes a 500, so a Stage B assertion that fails means the graph is wrong.

- [ ] **Step 1: Write the failing tests**

Create `seek/tests/test_seekapi_page_requests.py`:

```python
"""getPageRequests must not turn a slow or odd SEEK response into an unhandled AttributeError.

Measured 2026-09-16: /seek/sample_types/id=142/ returned 500 with
"'NoneType' object has no attribute 'prettify'" because the fetched page carried no div#content.
"""


class TestTheScrapeSurvivesAPageWithoutTheDiv:
    def test_a_page_without_the_content_div_does_not_raise(self, seekapi):
        assert seekapi._SeekApi__getHtmlpageDiv("<html><body><p>error</p></body></html>", "content") == ""

    def test_a_page_with_no_body_does_not_raise(self, seekapi):
        assert seekapi._SeekApi__getHtmlpageDiv("", "content") == ""


class TestTheFetchIsBounded:
    def test_get_is_called_with_a_timeout(self, seekapi, monkeypatch):
        seen = {}

        def fake_get(url, **kwargs):
            seen.update(kwargs)
            raise AssertionError("stop here; the call shape is what this pins")

        monkeypatch.setattr("requests.get", fake_get)
        with pytest.raises(AssertionError):
            seekapi.getPageRequests("/sample_types/1")
        assert seen.get("timeout"), "requests.get was called with no timeout, so a slow SEEK holds the worker"
```

Adjust the private-name mangling prefix to the real class name when writing the fixture.

- [ ] **Step 2: Run them and watch them fail**

```bash
cd /home/cdemurjian/code/dmac/docker/wt-gs-sync
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest seek/tests/test_seekapi_page_requests.py -q -p no:cacheprovider
```

Expected: FAIL, `AttributeError: 'NoneType' object has no attribute 'prettify'` and a missing timeout.

- [ ] **Step 3: Guard both**

In `seek/seekapi.py`, replace the body of `__getHtmlpageDiv` with a version that returns `""` when either
`parsed_html.body` or the `find` result is `None`, and give `getPageRequests` an explicit timeout. Keep the existing
return type, a string, so no caller changes.

- [ ] **Step 4: Run the tests and the seek suite**

```bash
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest seek/tests/test_seekapi_page_requests.py seek/tests -q \
  -p no:cacheprovider --continue-on-collection-errors
```

Expected: the new tests PASS and the seek suite shows no id failing that did not fail before. Compare against a run
on `origin/dev-graph` if any id looks new.

- [ ] **Step 5: Decide what the page shows, and say so**

An empty string means the page renders without the embedded panel. If the product should instead show an explicit
panel error, that is the operator's call. Record the decision in the commit body rather than choosing silently.

- [ ] **Step 6: Commit**

```bash
git add seek/seekapi.py seek/tests/test_seekapi_page_requests.py
git commit -m "fix(seek): bound the SEEK page fetch and survive a page without the content div"
```

---

# Stage B: the six behavioural assertions

**Stage B does not start until both are true:** Stage A passed at A5, and the operator has run the first live
`graph_sync --full --i-mean-the-live-graph` so the local graph reads 1.2.

### Task B0: The behavioural lane harness

**Files:**
- Create: `ci/smoke/test_graph_behaviour.py`
- Create: `ci/smoke/graph_assert.py`
- Modify: `ci/smoke/README.md`
- Modify: `ci/smoke/pytest.ini` (register the `graphwrite` marker)

**Interfaces:**
- Produces, for every later task in Stage B:
  - `graph_holds(api, base_url, *, sample_type: str, attribute: str, value: str) -> list[str]`
    returns the UIDs `graph_search` matches for that exact attribute and value. A hit proves the graph carries that
    property with that value, because `graph_search` matches in the graph (spec D2).
  - `wait_for_drain(api, base_url, *, timeout_s: int = 300) -> dict`
    polls `GET /nextseek_api/admin/graph-sync/status/` until every outbox kind reports 0 pending and 0 claimed, then
    returns the final status body. Raises `AssertionError` naming the outbox contents on timeout (spec D6).
  - `graph_meta(api, base_url) -> dict`
    returns `runs.drift.drift.stats.graphmeta` from the status body: `schema_version`, `catalog_hash`, `synced_at`,
    `label_maps_hash` (spec D4).

- [ ] **Step 1: Write the failing test for the helpers**

Create `ci/smoke/test_graph_behaviour.py`:

```python
"""Behavioural lane: every write path ends with an assertion that the graph changed.

Opt-in twice, like test_write_lane.py: `-m graphwrite`, and the tests that mutate existing rows need
CI_WRITE_DESTRUCTIVE=1 as well. Assertions go over HTTP only (graph_search and the graph-sync status
endpoint), because this lane holds pytest, requests and playwright and nothing else.
"""
import os
import pytest

from ci.smoke.graph_assert import graph_holds, graph_meta, wait_for_drain

pytestmark = pytest.mark.graphwrite

DESTRUCTIVE = os.environ.get("CI_WRITE_DESTRUCTIVE") == "1"
destructive = pytest.mark.skipif(
    not DESTRUCTIVE,
    reason="mutates existing samples; gated behind CI_WRITE_DESTRUCTIVE=1",
)


def test_the_graph_is_at_schema_1_2(wapi, base_url):
    """Every assertion below is meaningless on a 1.1 graph: the writer refuses to write one."""
    meta = graph_meta(wapi, base_url)
    assert meta.get("schema_version") == "1.2", (
        f"the graph reads schema {meta.get('schema_version')!r}. Run "
        "`graph_sync --full --i-mean-the-live-graph` once on this box first; until then "
        "every write path enqueues and nothing drains."
    )


def test_the_outbox_drains_without_help(wapi, base_url):
    """The loop is the only thing that may empty the outbox here. No manual command is issued."""
    status = wait_for_drain(wapi, base_url, timeout_s=300)
    assert status["freshness"]["outbox"]["status"] == "ok", status["freshness"]["outbox"]
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd /home/cdemurjian/code/dmac/docker/wt-gs-sync/ci/smoke
python -m pytest test_graph_behaviour.py -m graphwrite -q
```

Expected: FAIL at import, `No module named 'ci.smoke.graph_assert'`.

- [ ] **Step 3: Write the helpers**

Create `ci/smoke/graph_assert.py`:

```python
"""Assert a graph change over HTTP, with no Neo4j driver.

graph_search matches in the graph and hydrates the returned page from MySQL
(nextseek_api/graph_search/hydrate.py), so a graph assertion is a FILTER only the graph can satisfy,
never a field read off a returned row. A hit proves the graph carries that property with that value.
"""
import time

STATUS_PATH = "/nextseek_api/admin/graph-sync/status/"
SEARCH_PATH = "/nextseek_api/samples/graph_search/"


def graph_holds(api, base_url, *, sample_type, attribute, value):
    """UIDs graph_search matches for exactly this attribute and value. Empty list means the graph does not."""
    body = {"sample_type": sample_type,
            "extensions": {"attribute_filters": [{"attribute": attribute, "value": value}]},
            "page": 1, "page_size": 200}
    r = api.post(f"{base_url}{SEARCH_PATH}", json=body, timeout=120)
    assert r.status_code == 200, f"graph_search answered {r.status_code}: {r.text[:300]}"
    return [row.get("uid") for row in (r.json().get("results") or [])]


def graph_meta(api, base_url):
    """GraphMeta as the status endpoint reports it: schema_version, catalog_hash, synced_at, label_maps_hash."""
    r = api.get(f"{base_url}{STATUS_PATH}", timeout=60)
    assert r.status_code == 200, f"status answered {r.status_code}: {r.text[:300]}"
    drift = ((r.json().get("runs") or {}).get("drift") or {}).get("drift") or {}
    return (drift.get("stats") or {}).get("graphmeta") or {}


def wait_for_drain(api, base_url, *, timeout_s=300, poll_s=5):
    """Poll until the outbox holds nothing pending or claimed. Never issues a sync command itself."""
    deadline = time.monotonic() + timeout_s
    last = None
    while time.monotonic() < deadline:
        r = api.get(f"{base_url}{STATUS_PATH}", timeout=60)
        assert r.status_code == 200, f"status answered {r.status_code}: {r.text[:300]}"
        last = r.json()
        outbox = last.get("outbox") or {}
        if not (outbox.get("pending") or {}) and not (outbox.get("claimed") or {}):
            return last
        time.sleep(poll_s)
    raise AssertionError(
        f"the outbox did not drain in {timeout_s}s, so the loop is not draining it. "
        f"outbox={(last or {}).get('outbox')}"
    )
```

- [ ] **Step 4: Register the marker**

In `ci/smoke/pytest.ini`, add to `markers`:

```ini
    graphwrite: behavioural lane; drives a real write and asserts the graph changed. Opt-in with -m graphwrite.
```

- [ ] **Step 5: Run and confirm both tests pass**

```bash
cd /home/cdemurjian/code/dmac/docker/wt-gs-sync/ci/smoke
python -m pytest test_graph_behaviour.py -m graphwrite -q
```

Expected: PASS, 2 tests. A failure on `test_the_graph_is_at_schema_1_2` means the operator's live 1.2 sync has not
run and Stage B cannot proceed.

- [ ] **Step 6: Document the lane and commit**

Add a `## The behavioural lane` section to `ci/smoke/README.md` naming the two opt-ins, the three helpers, and the
fact that assertions are filters rather than field reads.

```bash
git add ci/smoke/test_graph_behaviour.py ci/smoke/graph_assert.py ci/smoke/pytest.ini ci/smoke/README.md
git commit -m "test(ci): a behavioural lane that asserts the graph changed, over HTTP"
```

---

### Task B1: Batch upload lands samples in the graph

**Files:**
- Modify: `ci/smoke/test_graph_behaviour.py`

**Interfaces:**
- Consumes: `graph_holds`, `wait_for_drain` from B0.

**This is the only path that syncs inline** (`orchestrator.py:580` calls `targeted.sync_samples` under the lock), so
it must reach the graph without the loop.

- [ ] **Step 1: Write the failing test**

```python
@destructive
def test_a_batch_upload_puts_its_samples_in_the_graph(wapi, base_url, tmp_path):
    """WR-01, WR-02. Stage 5 writes the outbox row inside the batch transaction and stage 6 calls
    targeted.sync_samples for the job's ids, so this path does NOT wait for the loop."""
    marker = f"cismoke-{uuid.uuid4().hex[:12]}"
    workbook = build_minimal_upload_workbook(tmp_path, marker=marker)   # Step 3 writes this helper

    with open(workbook, "rb") as fh:
        r = wapi.post(f"{base_url}/nextseek_api/batch-upload/start/", files={"file": fh}, timeout=600)
    assert r.status_code in (200, 202), f"upload failed {r.status_code}: {r.text[:400]}"
    job = r.json()

    totals = job.get("totals") or {}
    assert "pending" not in str(totals.get("graph", "")), (
        f"stage 6 did not sync inline: totals.graph={totals.get('graph')!r}. "
        "Either the graph is not at 1.2 or the lock was not taken."
    )

    uids = graph_holds(wapi, base_url, sample_type="TIS", attribute="Organ", value=marker)
    assert uids, f"the graph has no sample carrying Organ={marker!r} after the upload reported success"
```

- [ ] **Step 2: Run it and watch it fail**

```bash
cd /home/cdemurjian/code/dmac/docker/wt-gs-sync/ci/smoke
CI_WRITE_DESTRUCTIVE=1 python -m pytest test_graph_behaviour.py -m graphwrite -q -k batch_upload
```

Expected: FAIL, `build_minimal_upload_workbook` is not defined.

- [ ] **Step 3: Write the workbook builder**

Add to `ci/smoke/graph_assert.py`. It must produce the 4-sheet upload format the batch endpoint accepts, carrying
exactly two rows of sample type `TIS` whose `Organ` value is the marker, so the assertion cannot be satisfied by any
pre-existing row. Derive the sheet names and the required columns from a template fetched at run time:

```python
def build_minimal_upload_workbook(tmp_path, *, marker, api=None, base_url=None, code="TIS"):
    """Two TIS rows whose Organ value is `marker`, in the template's own shape.

    The template is fetched from /nextseek_api/templates/generate/ rather than hard-coded, so a schema
    change to TIS does not silently produce a workbook the uploader rejects for the wrong reason.
    """
    ...
```

- [ ] **Step 4: Run and confirm it passes**

Expected: PASS. Record the job's totals and the number of UIDs the graph returned.

- [ ] **Step 5: Clean up what the test created**

The test must delete its own samples in a `finally`, then assert `graph_holds` returns empty, which doubles as the
first half of B4's retire assertion.

- [ ] **Step 6: Commit**

```bash
git add ci/smoke/test_graph_behaviour.py ci/smoke/graph_assert.py
git commit -m "test(ci): a batch upload lands its samples in the graph"
```

---

### Task B2: An attribute change rewrites the type's samples and moves the catalog

**Files:**
- Modify: `ci/smoke/test_graph_behaviour.py`

**Interfaces:**
- Consumes: `graph_holds`, `graph_meta`, `wait_for_drain`.

WR-05 hooks `attributes/executor.py::DjangoExecutionServices.record_commit` and only enqueues, so this test exercises
the loop as well as the attribute API.

- [ ] **Step 1: Write the failing test**

```python
@destructive
def test_an_attribute_create_reaches_the_graph_and_moves_the_catalog(wapi, base_url):
    """WR-05. The attribute API only enqueues, so the loop has to carry this one."""
    before = graph_meta(wapi, base_url)
    name = f"CiSmoke{uuid.uuid4().hex[:8]}"

    r = wapi.post(f"{base_url}/nextseek_api/attributes/batch-create/",
                  json={"sample_type": "TIS", "attributes": [{"title": name, "value_type": "String"}],
                        "dry_run": False}, timeout=300)
    assert r.status_code in (200, 202), f"create failed {r.status_code}: {r.text[:400]}"

    try:
        wait_for_drain(wapi, base_url, timeout_s=600)
        after = graph_meta(wapi, base_url)
        assert after.get("catalog_hash") != before.get("catalog_hash"), (
            "catalog_hash did not move after an attribute was declared, so the catalog was not rebuilt: "
            f"{before.get('catalog_hash')}"
        )
    finally:
        wapi.post(f"{base_url}/nextseek_api/attributes/batch-delete/",
                  json={"sample_type": "TIS", "attributes": [name], "dry_run": False}, timeout=300)
        wait_for_drain(wapi, base_url, timeout_s=600)
```

- [ ] **Step 2: Run it and watch it fail**

Expected: FAIL. Record which half failed: a `wait_for_drain` timeout means the loop is not draining; an unchanged
`catalog_hash` means the catalog rebuild did not happen.

- [ ] **Step 3: Diagnose, do not patch the test**

If it fails, the defect is in the product, not the assertion. Report the outbox contents and the status body and
stop. Do not weaken the assertion to make it pass.

- [ ] **Step 4: Add the rename and delete halves**

Two further tests in the same shape: `batch-patch` renames the attribute and `graph_holds` on the new name hits while
the old name misses; `batch-delete` removes it and both miss.

- [ ] **Step 5: Run the three together**

```bash
CI_WRITE_DESTRUCTIVE=1 python -m pytest test_graph_behaviour.py -m graphwrite -q -k attribute
```

- [ ] **Step 6: Commit**

```bash
git add ci/smoke/test_graph_behaviour.py
git commit -m "test(ci): an attribute change reaches the graph and moves the catalog"
```

---

### Task B3: A sample update changes the node and its source_hash

**Files:**
- Modify: `ci/smoke/test_graph_behaviour.py`

**Needs a live SEEK.** WR-07 is the SEEK proxy; without SEEK there is nothing to proxy to. If SEEK is unavailable,
report that and do not substitute a unit test (spec D8).

- [ ] **Step 1: Write the failing test**

```python
@destructive
def test_a_sample_update_changes_the_node_and_its_source_hash(wapi, base_url):
    """WR-07. Enqueue only, so the loop carries it. source_hash is asserted through drift, which
    recomputes it from MySQL and compares (spec D3), not by reading the node."""
    uid, sample_id = create_throwaway_sample(wapi, base_url)     # Step 3 writes this helper
    new_value = f"cismoke-{uuid.uuid4().hex[:12]}"
    try:
        r = wapi.patch(f"{base_url}/nextseek_api/samples/{sample_id}/",
                       json={"data": {"attributes": {"json_metadata": {"Organ": new_value}}}}, timeout=300)
        assert r.status_code in (200, 202), f"patch failed {r.status_code}: {r.text[:400]}"

        wait_for_drain(wapi, base_url, timeout_s=600)
        assert uid in graph_holds(wapi, base_url, sample_type="TIS", attribute="Organ", value=new_value), (
            "the graph does not carry the new value after the update drained"
        )
    finally:
        wapi.delete(f"{base_url}/nextseek_api/samples/{sample_id}/", timeout=300)
        wait_for_drain(wapi, base_url, timeout_s=600)
```

- [ ] **Step 2: Run it and watch it fail**

Expected: FAIL, `create_throwaway_sample` is not defined.

- [ ] **Step 3: Write `create_throwaway_sample`**

In `ci/smoke/graph_assert.py`: POST one TIS sample through `/nextseek_api/samples/`, wait for the drain, assert the
graph holds it, and return `(uid, sample_id)`. It fails loudly if the graph does not receive it, because every later
assertion depends on that sample existing in the graph.

- [ ] **Step 4: Run and confirm it passes**

- [ ] **Step 5: Assert the hash moved, through drift**

Add a second test that captures `drift` `changed` from the status body immediately after the PATCH and before the
drain, and asserts it counted this sample. Clean drift after the drain then proves the recomputed hash agrees.

- [ ] **Step 6: Commit**

```bash
git add ci/smoke/test_graph_behaviour.py ci/smoke/graph_assert.py
git commit -m "test(ci): a sample update changes the node and its source hash"
```

---

### Task B4: A delete retires the node by the retire rule

**Files:**
- Modify: `ci/smoke/test_graph_behaviour.py`

**Needs a live SEEK.** This exercises the branch Run 2 flagged as unverified: `ORPHAN_SWAP` doing
`REMOVE s:$(types)` with an empty list, on a Sample with no `T_` label.

- [ ] **Step 1: Write the failing test**

```python
@destructive
def test_a_delete_removes_the_node_from_the_graph(wapi, base_url):
    """WR-07, WR-13. A node graph_sync wrote carries synced_at and is archived then DETACH DELETEd; a node
    it never wrote becomes an OrphanSample instead (the deletion rule, R3)."""
    uid, sample_id = create_throwaway_sample(wapi, base_url)
    value = organ_of(wapi, base_url, sample_id)

    r = wapi.delete(f"{base_url}/nextseek_api/samples/{sample_id}/", timeout=300)
    assert r.status_code in (200, 202, 204), f"delete failed {r.status_code}: {r.text[:400]}"

    wait_for_drain(wapi, base_url, timeout_s=600)
    assert uid not in graph_holds(wapi, base_url, sample_type="TIS", attribute="Organ", value=value), (
        "the graph still returns the deleted sample, so the retire rule did not run"
    )
```

- [ ] **Step 2: Run it and watch it fail**

Expected: FAIL, `organ_of` is not defined.

- [ ] **Step 3: Write `organ_of`**

In `ci/smoke/graph_assert.py`: read the sample's `Organ` value back from `/nextseek_api/samples/{id}/` so the
assertion filters on the value the sample actually carries rather than one the test assumed.

- [ ] **Step 4: Run and confirm it passes**

- [ ] **Step 5: Assert the archive names it**

The run writes `retired.tsv`. Assert through the status endpoint that a `retire` outbox kind was processed for this
sample. If the status endpoint does not expose enough to tell, record that as a gap rather than reading the file,
which this lane cannot reach.

- [ ] **Step 6: Commit**

```bash
git add ci/smoke/test_graph_behaviour.py ci/smoke/graph_assert.py
git commit -m "test(ci): a delete retires the node by the retire rule"
```

---

### Task B5: A membership or project change moves the scope

**Files:**
- Modify: `ci/smoke/test_graph_behaviour.py`

**Needs a live SEEK.** WR-09 and WR-10.

- [ ] **Step 1: Write the failing test**

```python
@destructive
def test_a_project_change_moves_what_an_account_can_see_in_the_graph(wapi, api, base_url):
    """WR-09, WR-10. graph_search resolves scope from the graph (nextseek_api/graph_search/scope.py), so a
    membership change has to reach the graph before the smoke account's results move."""
    uid, sample_id = create_throwaway_sample(wapi, base_url)
    value = organ_of(wapi, base_url, sample_id)
    try:
        assert uid not in graph_holds(api, base_url, sample_type="TIS", attribute="Organ", value=value), (
            "the read account can already see the sample, so this test proves nothing"
        )
        add_sample_to_project(wapi, base_url, sample_id, project_id=smoke_account_project(api, base_url))
        wait_for_drain(wapi, base_url, timeout_s=600)
        assert uid in graph_holds(api, base_url, sample_type="TIS", attribute="Organ", value=value), (
            "the sample joined a project the read account is in, but the graph did not move its scope"
        )
    finally:
        wapi.delete(f"{base_url}/nextseek_api/samples/{sample_id}/", timeout=300)
        wait_for_drain(wapi, base_url, timeout_s=600)
```

- [ ] **Step 2: Run it and watch it fail**

Expected: FAIL, `add_sample_to_project` and `smoke_account_project` are not defined.

- [ ] **Step 3: Write both helpers**

`smoke_account_project` **reuses Task P1's `_own_project_id`** rather than writing the lookup again. P1 moved that
resolution onto `/nextseek_api/people/current/`, which answers with the caller's own memberships; the unscoped
`/nextseek_api/projects/` list is what made the 2026-09-16 CI run go red on project 16 and must not be used here.
`add_sample_to_project` PATCHes the sample's project set through the samples proxy.

- [ ] **Step 4: Run and confirm it passes**

- [ ] **Step 5: Commit**

```bash
git add ci/smoke/test_graph_behaviour.py ci/smoke/graph_assert.py
git commit -m "test(ci): a project change moves what an account sees in the graph"
```

---

### Task B6: Wire the lane into the rebuild and report

**Files:**
- Modify: `ci/smoke/README.md`
- Modify: `startup/cli.py` (the CI invocation), only if the operator opts the lane into rebuilds
- Create: `graph-search/runs/behaviour/REPORT.md` (untracked)

- [ ] **Step 1: Run the whole lane end to end and record every number**

```bash
cd /home/cdemurjian/code/dmac/docker/wt-gs-sync/ci/smoke
CI_WRITE_DESTRUCTIVE=1 python -m pytest test_graph_behaviour.py -m graphwrite -q \
  --junitxml=/tmp/graph-behaviour.xml
```

- [ ] **Step 2: Write the report**

One row per assertion B1 to B6: what ran, what the graph did, the numbers. Any assertion that could not run says so
and why, per spec D8. **No assertion is reported as passing without its run.**

- [ ] **Step 3: Decide the rebuild question with the operator**

The lane mutates real data, so it is opt-in twice today. Ask whether it should also run after
`./startup.sh rebuild` on local and dev. Do not wire it in unilaterally.

- [ ] **Step 4: Commit the docs**

```bash
git add ci/smoke/README.md
git commit -m "docs(ci): document the behavioural lane and what each assertion proves"
```

---

## Self-review notes

- **Spec coverage:** section 5 maps to A1 to A5; section 6.1's two prerequisites map to P1 and P2; section 6's six
  rows map to B1 to B6 with B0 as their harness;
  D1 to D4 are realised in `graph_assert.py`; D6 in `wait_for_drain`; D7 in the `destructive` marker and the
  `finally` blocks; D9 in B0's `test_the_graph_is_at_schema_1_2`.
- **Known soft spots, deliberately left:** B1 step 3's workbook builder and B5 step 3's helpers are described by
  contract rather than given in full, because both depend on the live template shape and the smoke account's
  memberships, which must be read at run time rather than assumed. Every other step carries its code.
- **A3 and A4 are conditional.** If A2 step 3 passes at `--chunk 5000` under 1g, both are skipped and the plan goes
  straight to A5. That is the good outcome and the plan should not invent work to avoid it.

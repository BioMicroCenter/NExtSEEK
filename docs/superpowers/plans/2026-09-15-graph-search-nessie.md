# graph_search follow-up 1 (Nessie on the metadata graph, evidence POC): implementation plan

> **For agentic workers:** this plan is executed by a Workflow over the task graph below. Each task is one agent with
> its own tests and a disjoint set of files; a reviewer may reject one task and approve its neighbour. Use
> superpowers:test-driven-development inside a task: write the failing tests, run them, make them pass, run the area
> again. Steps use checkbox (`- [ ]`) syntax. **Nothing is built yet.** Steps marked **OPERATOR** are run or approved
> by the operator; every paid step needs the operator's approval for that step. Revised three times on 2026-09-15 to
> the operator's rulings (spec section 3.1).

**Goal:** two answers. Group A, the contest: with the metadata in the graph, do Cypher queries outperform JSON
advanced searches when an LLM turns the user's question into the query (the graph agent against the API agent, 100
single-turn metadata and advanced-search questions)? Group B, the check: does the graph agent with its new context
still work for everything that normally routes to the graph (80 single-turn questions)? Deliver (a) the graph agent
reading the v1.1 catalog as compact text with a per-label guard, and (b) stage-by-stage evidence scored against
re-derived ground truth.

**Architecture:** a lazy, process-level catalog reader (`graph_catalog.py`) replaces the `keys(n) LIMIT 200` file
cache; a pure renderer (`graph_context.py`) turns it into variant (b); the guard in `agents/graph.py` reads the same
snapshot; every graph read runs in a READ transaction. An evaluation switch (admin request field behind an environment
flag) forces the NS parser to the graph path, the graph path with today's context, or the API path. The harness drives
the arms per question inside a throwaway venue container that runs this branch's code against the live data; a scorer
compares the arms with ground truth derived by read-only oracles.

**Tech stack:** Django 5 and DRF, pydantic v2, the `neo4j` Python driver (Neo4j Community 2026.07.1), the
`nessie_tests` harness and its e2e criteria DSL, bash and Docker for the venue.

**Spec:** `docs/superpowers/specs/2026-09-15-graph-search-nessie-design.md`. Also read the POC spec
(`docs/superpowers/specs/2026-09-14-graph-search-poc-design.md`, sections 3 to 7), `docs/neo4j-schema.md` ("v1.1"),
`NessieAI/CLAUDE.md`, `NessieAI/tests/README.md` and `NessieAI/tests/nessie_tests/README.md` before any task.

## Global constraints

- Worktree `wt-gs-nessie`, branch `feat/graph-search-nessie`, from `feat/graph-search` at `4b3e087a`. Push only this
  branch, after a scan of the diff against `origin/feat/graph-search` for emails, home paths and tokens. Never merge or
  push another branch, and never force-push.
- Never edit `nextseek_api/graph_sync/*`, `nextseek_api/graph_search/*`, `nextseek_api/services/graph_search.py`,
  `ci/routes.py`, `nextseek_api/urls.py` or `docker/scripts/entrypoint.sh` (follow-up 2 and the POC own them). Read the
  catalog through Cypher only.
- Never touch the live `nextseek` compose containers (`nextseek`, `seek`, `seek-mysql`, `neo4j`): no exec, restart,
  rebuild or recreate. The venue (`gs-nessie-venue`) is the only container that reaches the live databases, from stage
  V on, and only while `$GS_WORK/.gs-bench-running` is absent. Never `docker exec cypher-shell` into a memory-capped
  Neo4j.
- Tests run only in throwaway containers or host lanes, one container at a time (the `flock` below), memory-capped. The
  host is memory-starved: run commands in the foreground with timeouts.
- `$GS_WORK` is the operator's graph-search work directory and `$NEXTSEEK_LIVE_CHECKOUT` the checkout the live stack
  runs from, both outside this repository. Nessie evidence lives under `$GS_WORK/nessie/` (directories mode 700, files
  mode 600): the question selection (`selection.json`), the venue snapshot, ladder and B2 questions, truth files, cases
  files, run outputs, the price table. None of it is ever tracked (one ladder value and several corpus variants name
  real people).
- Public repository: no credentials, emails, personal names or home paths in tracked files. No em-dashes. Never print
  an environment value; check one by comparison.
- Stage files by name, never `git add -A`. Conventional commits with a module scope, each ending with exactly one
  trailer line:

```
Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>
```

- Numbers, verbatim: merged samples 1,084,754; Group A 100 single-turn questions (66 corpus: 57 `sample_search`, 8
  `harmonization`, 1 `retrieval_path_selection`; 25 ladder rungs; 9 B2 shapes); Group B 80 (79 corpus: 60
  `graph_traversal`, 7 `lineage_tree`, 5 `sample_search`, 4 `project_summary_report`, 1 `retrieval_path_selection`, 1
  `vocabulary_resolution`, 1 `engine_routing`; 1 B2 shape); NS turns: Group A 200, Group B 80 without arm L or 160 with
  it; render budget 32,768 bytes; K 25, then 15, 10, names only; at most 3 resolved types; meaning at most 120
  characters; a value over 60 characters is not rendered; catalog hash re-check 60 s; type details 10 minutes;
  vocabulary 1 hour; failure memory 60 s; catalog query timeout 10 s; tool query timeout 60 s; venue port
  `127.0.0.1:8010`; venue memory 6g; memory floor 8 GiB.

## Commands used throughout

```bash
# 0. The benchmark window: start no container while this prints
test -e "$GS_WORK/.gs-bench-running" && echo "benchmark window open: start no container"

# 1. Django lane: the checkout's engine source first on the path; fake drivers, no network
mkdir -p schema_rag/duckdb schema_rag/embedding_models
flock "$GS_WORK/.gs-docker.lock" docker run --rm -i --network none --memory 2g \
  -e LOG_DIR=/tmp/nextseek-logs -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONPATH=/src:/src/NessieAI/chat_nextseek/src:/src/NessieAI/dmac_assistant/src \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest <paths> -q -p no:cacheprovider

# 2. Django lane, ns/api variant: a writable copy of the working tree with the lane settings
L="$GS_WORK/nessie/lane-copy"; rm -rf "$L"; mkdir -p "$L"
tar --exclude=.git --exclude=node_modules -cf - . | tar -x -C "$L"
cp startup/dev/lane_local_settings.py "$L/dmac/local_settings.py"
mkdir -p "$L/schema_rag/duckdb" "$L/schema_rag/embedding_models"
flock "$GS_WORK/.gs-docker.lock" docker run --rm -i --network none --memory 2g \
  -e LOG_DIR=/tmp/nextseek-logs -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -e PYTHONPATH=/src:/src/NessieAI/chat_nextseek/src:/src/NessieAI/dmac_assistant/src \
  -e GCP_API_KEY=dummy -e CATALOG_FILE=/src/NessieAI/chat_nextseek/agent_model_catalog.json \
  -v "$L":/src -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest <paths> -q -p no:cacheprovider

# 3. Host harness lane (nessie_tests, truth tooling, builder, scorer)
uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 --with orjson \
  python -m pytest NessieAI/tests/nessie_tests/tests -q -p no:cacheprovider
#    Expect exactly the five machine-bound failures in test_v4_2_set3_replay.py (nessie_tests CLAUDE.md)

# 4. Container-CC hermetic lane, for the context drift guard
PYTHONPATH="$PWD:$PWD/NessieAI/dmac_assistant/src" uv run --no-project --with pytest --with orjson \
  --with 'pydantic>=2.13' --with 'baml-py==0.222.0' python -m pytest \
  NessieAI/tests/cc/test_cc_context_drift_guard.py --noconftest -p no:cacheprovider -q

# 5. Docs and conventions
python3 ci/docs_map.py
python3 scripts/validate_viewset_conventions.py

# 6. The venue (task T9 writes the script)
scripts/graph_search/nessie_venue.sh prepare | up | check | exec <python args> | logs | down

# 7. The harness inside the venue (task T7); the password comes from the environment, never a file.
#    Group A: --arms graph,api. Group B: --arms graph, or --arms graph,graph_legacy with the optional arm L.
scripts/graph_search/nessie_venue.sh exec manage.py nessie --tier full \
  --cases /venue/cases/<file>.json --force-route ns --arms <arms> \
  --user demo --password-env GS_DEMO_PASSWORD --out /venue/runs/<run-id> [--resume] [--max-turns N]

# 8. The scorer, on the host (task T8)
uv run --no-project --with pydantic python -m NessieAI.tests.nessie_tests.engine_compare \
  --run "$GS_WORK/nessie/runs/<run-id>" --group a|b --truth "$GS_WORK/nessie/truth" \
  --outputs "$GS_WORK/nessie/venue/outputs" --prices "$GS_WORK/nessie/prices.json"
```

## Task graph

```
Stage B, build (free; 9 agents)
  T1 catalog reader (A2) -------------+
  T2 renderer (A3, pure) -------------+-- T4 graph agent wiring, guard, context modes, MCP (A3, A4)
  T3 read-only (D3) ------------------+
  T5 prompt lines and the legacy prompt (A7 subset)
  T6 evaluation switch (product side) -- T7 harness: forced runs, arms, preflight -- T8 truth tooling and scorer
  T9 venue script (after T1, T2)
  == gate B: docs, lanes, push ==
Stage V, venue (free; reads the live databases; OPERATOR approves the first read)
  V1 prepare, up, check
  == gate V ==
Stage G, ground truth (free; read-only oracles through the venue; 6 agents in parallel, then 1)
  Group A: G1 sample_search, first half | G2 sample_search, second half | G3 harmonization, retrieval, ladder, B2
  Group B: G4 graph_traversal, first half | G5 graph_traversal, second half | G6 the other graph-routed questions
  G7 build the cases, the pilots and the truth summary
  == gate T: OPERATOR signs the truth and the selection ==
Stage P, paid (OPERATOR)
  P1 preflight -- P2 pilots -- (gate P) -- P3 full runs, in blocks -- P4 Group A repeats (optional)
Stage R, result (free)
  R1 score both groups -- R2 triage per arm -- R3 OPERATOR verdicts
Stage S, after the POC, with the merge work (separate approval; spec section 8.1)
  S1 scope carrier and host seam | S2 injector | S3 adversarial suite (after S2) | S4 fixed reads, lineage, rendering
```

T1, T2, T3, T5 and T6 run in parallel once this plan is approved. T4 waits for T1, T2 and T3 (and reads T5's legacy
prompt file by name); T7 for T6; T8 for T7; T9 for T1 and T2. The truth half of T8 does not depend on T7 and may start
first.

## File ownership

Paths under `NessieAI/chat_nextseek/src/chat_nextseek/` are written short; tests under `NessieAI/tests/chat_nextseek/`
are written `tests/cn/`.

| Files | Task |
|---|---|
| `graph_catalog.py` (new), `config.py`, `tests/cn/test_graph_catalog.py` (new), any test that calls a removed `config.py` method | T1 |
| `graph_context.py` (new), `prompts/graph_schema_structure.txt` (new), `tests/cn/test_graph_context.py` (new) | T2 |
| `cypher_text.py` (new), `helpers/tools/neo4j.py`, `nextseek_api/services/entity_tree.py`, `tests/cn/test_cypher_write_check.py` (new), `tests/cn/test_neo4j_read_mode.py` (new), `nextseek_api/tests/test_entity_tree_read_routing.py` (new), `tests/cn/test_neo4j_total_probe.py` (it fakes `session.run`), run but not edited: `nextseek_api/tests/test_services_entity_tree.py` | T3 |
| `agents/graph.py`, `agents/system.py`, `schemas/graph.py` (`GraphAgentPlan.context_mode`), `orchestrator.py` (one line in `_execute_graph_turn`), `NessieAI/chat_nextseek/mcp_server.py`, `tests/cn/test_graph_catalog_guard.py` (new), `tests/cn/test_graph_agent_context.py` (new), `tests/cn/test_graph_property_guard.py`, `tests/cn/test_graph_canonical_uid.py`, `tests/cn/test_graph_refine.py` | T4 |
| `prompts/graph_agent.txt`, `prompts/graph_agent_legacy.txt` (new, a verbatim copy of the base commit's `graph_agent.txt`), `context/min_graph_schema.json`, `tests/cn/test_graph_prompt_claims.py` (new) | T5 |
| `agents/parser.py`, `nextseek_api/assistant/models_api.py`, `NessieAI/cc/turn.py`, `tests/cn/test_parser_force_mode.py` (new), `NessieAI/tests/api/test_eval_parser_force_gate.py` (new) | T6 |
| `NessieAI/tests/nessie_tests/{http_driver,runner,cli,preflight}.py`, `nextseek_api/management/commands/nessie.py`, `NessieAI/tests/nessie_tests/tests/{test_http_driver,test_run_case,test_runner,test_cli,test_preflight}.py`, `NessieAI/tests/nessie_tests/tests/test_run_arms.py` (new), `NessieAI/tests/api/test_nessie_command_flags.py` (new) | T7 |
| `NessieAI/tests/nessie_tests/engine_truth.py` (new), `NessieAI/tests/nessie_tests/engine_compare.py` (new), `NessieAI/tests/nessie_tests/scripts/derive_truth.py` (new), `NessieAI/tests/nessie_tests/scripts/build_engine_cases.py` (new), `NessieAI/tests/nessie_tests/tests/{test_engine_truth,test_build_engine_cases,test_engine_compare}.py` (new) | T8 |
| `scripts/graph_search/nessie_venue.sh` (new), `scripts/graph_search/nessie_venue_check.py` (new), `scripts/graph_search/README.md` (one row) | T9 |
| `NessieAI/tests/nessie_tests/README.md` (one section), `NessieAI/tests/README.md` (one lane row), `NessieAI/chat_nextseek/CLAUDE.md` (the context-write landmine), the spec's and this plan's status lines | gate B |

---

## Stage B: the build

### Task T1: A2, the live catalog reader

**Files:** create `graph_catalog.py`, `tests/cn/test_graph_catalog.py`; modify `config.py` (drop `_fetch_neo4j_schema`,
`_ensure_neo4j_schema`, `_ensure_schema_file`, `_fetch_assay_sample_connections`,
`_ensure_assay_sample_connections`, `_fetch_protocol_schema`, `_ensure_protocol_schema`; `NEO4J_SCHEMA`,
`PROTOCOL_SCHEMA`, `ASSAY_SAMPLE_CONNECTIONS` load the committed JSON with `_load_json`; `get_config_snapshot` adds
`graph_catalog`). Keep `_is_today` (the context export uses it). Grep the tests for the removed names first and update
each caller.

**Interfaces (produced; T2, T4 and T9 consume them by these names):**

```python
SCHEMA_VERSION = "1.1"
HASH_RECHECK_S, DETAIL_TTL_S, VOCAB_TTL_S, FAILURE_MEMORY_S, QUERY_TIMEOUT_S = 60, 600, 3600, 60, 10

class CatalogUnavailable(RuntimeError): ...

@dataclass(frozen=True)
class TypeIndexRow:
    title: str; label: str; name: str | None; clade: str | None
    sample_count: int | None; deprecated: bool; attributes_with_values: int

@dataclass(frozen=True)
class AttributeRow:
    title: str; value_type: str; declared: bool; needs_backticks: bool; sample_count: int | None
    meaning: str | None; unit_key: str | None; role: str | None
    top_values: tuple = (); top_counts: tuple = ()
    num_min: float | None = None; num_max: float | None = None
    date_min: str | None = None; date_max: str | None = None

@dataclass(frozen=True)
class TypeDetail:
    title: str; label: str; name: str | None; summary: str | None; clade: str | None
    sample_count: int | None; curated_parents: str | None; curated_children: str | None
    attributes: tuple[AttributeRow, ...]; never_filled: int

@dataclass(frozen=True)
class Vocabulary:
    investigation_titles: tuple[str, ...]; project_titles: tuple[str, ...]; study_titles: tuple[str, ...]
    published_studies: tuple[dict, ...]; assay_titles: tuple[str, ...]; protocol_titles: tuple[str, ...]
    assay_connections: tuple[dict, ...]

@dataclass(frozen=True)
class CatalogSnapshot:
    catalog_hash: str; synced_at: str | None; has_usage: bool
    index: tuple[TypeIndexRow, ...]
    guard: Mapping[str, frozenset[str]]          # T_ label -> attribute titles with values

def get_snapshot(config) -> CatalogSnapshot: ...            # raises CatalogUnavailable
def get_type_details(config, titles) -> list[TypeDetail]: ...  # admin form; unknown titles ignored
def get_vocabulary(config) -> Vocabulary: ...
def cache_state(config) -> dict: ...                         # no network
def reset_cache() -> None: ...                               # tests only
```

Statements are module constants (`META`, `INDEX`, `GUARD`, `TYPES_ADMIN`, `VOCAB_*`), written from
`catalog_readback.cypher` Q1, Q3 and Q4 in the recon and checked against `docs/neo4j-schema.md` v1.1 property names.
Every one runs as `session.execute_read(fn)` where `fn` is wrapped in `neo4j.unit_of_work(timeout=QUERY_TIMEOUT_S)`.
One driver per `(NEO4J_URI, NEO4J_DATABASE)`, closed and forgotten after a failure.

- [ ] **Step 1: Write the failing tests** (a fake driver whose session records the access mode, the statement and the
  parameters, and a patched clock):
  - `get_snapshot` reads META, INDEX and GUARD once; a second call inside 60 s reads nothing; after 60 s with the same
    hash it reads META only; with a new hash it re-reads INDEX and GUARD and drops cached details.
  - two configs with different `NEO4J_URI` get separate snapshots.
  - no `GraphMeta`, `schema_version` other than `"1.1"`, a driver error, or an unset URI raise `CatalogUnavailable`;
    the failure is remembered for 60 s (the driver factory is not called again).
  - `get_type_details` queries only the requested known titles, once per (hash, title), and again after 10 minutes.
  - every statement ran through `execute_read` with a timeout; `session.run` was never called outside a transaction
    function.
  - constructing a `ChatConfig` (LLM clients and DB connects patched) and calling `get_snapshot` leaves a temporary
    `CONTEXT_DIR` exactly as it was; `NEO4J_SCHEMA` equals the committed `neo4j_schema.json`.
  - `get_config_snapshot()["graph_catalog"]` is present and the fake driver saw zero calls.
- [ ] **Step 2: Run them to see them fail** (Django lane, `NessieAI/tests/chat_nextseek/test_graph_catalog.py`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the area:** `NessieAI/tests/chat_nextseek`. Expected: the new tests pass; the rest pass as on the
  base commit.
- [ ] **Step 5: Commit** `feat(chat_nextseek): read the graph catalog live, cached on catalog_hash (A2)`.

**Acceptance:** spec section 4.1.

### Task T2: A3, the variant (b) renderer

**Files:** create `graph_context.py`, `prompts/graph_schema_structure.txt`, `tests/cn/test_graph_context.py`.

**Interfaces:** consumes T1's dataclasses by field name (tests build them with `types.SimpleNamespace`, so T2 does not
wait for T1). Produces:

```python
STRUCTURE_PATH: Path            # prompts/graph_schema_structure.txt
BUDGET_BYTES = 32_768
K_STEPS = (25, 15, 10, 0)       # 0 means names only
MAX_TYPES, MEANING_MAX, VALUE_MAX = 3, 120, 60

def resolved_type_codes(plan: dict | None, entity: dict | None, known: set[str]) -> list[str]: ...
def render_type_index(rows) -> str: ...
def render_type_section(detail, k: int) -> str: ...
def render_graph_context(snapshot, details, *, k: int = 25, budget: int = BUDGET_BYTES) -> str: ...
def render_vocabulary(vocab, question: str) -> str: ...
```

Formats: an index line is `TIS :T_TIS "Tissue Sample" clade Source, 107,412 samples, 41 attributes with values`
(`no samples` for zero); deprecated types are omitted. A section starts `### TIS :T_TIS "Tissue Sample", clade Source,
107,412 samples`, then the summary's first sentence, `Curated parents: ...`, `Curated children: ...`, the K most-filled
attributes as `- Organ [string] n=16,841 | values: "Lung" 16,841, "lung" 5,893 | <meaning, first clause>`
(backticked when `needs_backticks`, `(undeclared)` when not declared, `unit: <unit_key>`, `range a..b` for numbers and
dates, values over 60 characters skipped, the values part absent when the catalog has none), one `also filled: ...` line
of the other names, and `N declared attributes hold no value`. The structure file is the recon's draft corrected to v1.1.

- [ ] **Step 1: Write the failing tests:** index line format, deprecated omitted, zero-sample flagged; section contents
  for each rule above; `resolved_type_codes` takes the plan's `resolved.sampletypes` and `filters.sampletype_code`
  first, then the entity output, drops unknown codes, keeps at most 3, keeps order; budget: three synthetic
  190-attribute types with 120-character meanings and ten values each render within 32,768 bytes by stepping K down,
  and a single small type renders at K 25; the rendering works with no `top_values`, no meanings and `has_usage`
  false; vocabulary gating (investigation and project titles always; studies and published studies only for "study",
  "paper", "publication", "DOI", "PMID"; assay titles and connections on the existing assay words; protocol titles on
  the existing protocol words); the structure file names only labels and relationship types found in
  `docs/neo4j-schema.md`'s v1.1 section (parsed by the test), and `CHILD_OF` only in a sentence containing "does not
  exist".
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement** as pure functions (no Neo4j, no config).
- [ ] **Step 4: Run** `tests/cn/test_graph_context.py`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(chat_nextseek): render the graph context from the catalog (A3)`.

**Acceptance:** spec section 4.2 (the wiring lands in T4).

### Task T3: read-only on every Nessie graph path (D3)

**Files:** create `cypher_text.py`, `tests/cn/test_cypher_write_check.py`, `tests/cn/test_neo4j_read_mode.py`,
`nextseek_api/tests/test_entity_tree_read_routing.py`; modify `helpers/tools/neo4j.py`,
`nextseek_api/services/entity_tree.py`, `tests/cn/test_neo4j_total_probe.py`.

**Interfaces:**

```python
# cypher_text.py
ALLOWED_PROCEDURES = frozenset({"db.index.fulltext.queryNodes"})
def mask_cypher(text: str) -> str: ...        # the behaviour of agents/graph.py::_mask_cypher, moved (T4 aliases it)
def write_clause(text: str) -> str | None: ... # the offending clause, or None; works on masked text
```

`tool_neo4j_query(config, cypher, parameters=None)` keeps its signature and result shape.

- [ ] **Step 1: Write the failing tests:**
  - `write_clause` refuses `CREATE`, `MERGE`, `SET`, `DELETE`, `DETACH DELETE`, `REMOVE`, `DROP`, `LOAD CSV`,
    `FOREACH`, `CALL apoc.refactor.mergeNodes(...)`, `CALL dbms.listConfig()`, `CALL db.labels()`, `SHOW USERS`,
    `USE system`, `GRANT`, and a backticked procedure name; it passes `CALL db.index.fulltext.queryNodes(...)`,
    `CALL () { MATCH ... RETURN ... }`, `CALL { ... }`, a literal `'Data Set'`, a `` `SET_ID` `` property and a comment
    containing DELETE; a subquery body containing `CREATE` is refused.
  - `tool_neo4j_query` against a fake driver: the query and the total probe each run through `execute_read` with a
    60 s timeout; `session.run` is never called directly; a refused statement never opens a driver; the result keys are
    unchanged (`ok`, `data`, `count`, `total`, `truncated`, `limit`, `cypher`, `parameters`, `counters`).
  - `entity_tree`'s three `execute_query` calls pass READ routing (patched driver asserts the keyword).
- [ ] **Step 2: Run them to see them fail** (Django lane: `tests/cn/test_cypher_write_check.py`,
  `tests/cn/test_neo4j_read_mode.py`, `nextseek_api/tests/test_entity_tree_read_routing.py`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `NessieAI/tests/chat_nextseek`, `NessieAI/tests/ns` (ns/api variant) and
  `nextseek_api/tests/test_services_entity_tree.py`. Expected: pass as on the base commit plus the new tests.
- [ ] **Step 5: Commit** twice: `feat(chat_nextseek): run Nessie's Cypher in READ transactions behind a masked write
  check` (the chat_nextseek files) and `feat(nextseek_api): read the entity_tree graph with READ routing`.

**Acceptance:** spec section 4.5.

### Task T4: A3 wiring, the A4 guard, the context modes and the MCP resource

**Files:** modify `agents/graph.py`, `agents/system.py`, `schemas/graph.py`, `orchestrator.py` (one line),
`NessieAI/chat_nextseek/mcp_server.py`; create `tests/cn/test_graph_catalog_guard.py`,
`tests/cn/test_graph_agent_context.py`; keep `tests/cn/test_graph_property_guard.py`, `test_graph_canonical_uid.py`
and `test_graph_refine.py` green (they run with the catalog unavailable, so the fallback path applies; patch
`graph_catalog.get_snapshot` to raise where needed).

**Interfaces (consumes T1, T2, T3, and T5's `prompts/graph_agent_legacy.txt` by name):**

```python
# agents/graph.py
_mask_cypher = cypher_text.mask_cypher
V11_SYSTEM_PROPERTIES = frozenset({"id", "uuid", "type", "title", "project_ids", "search_text", "synced_at"})
V11_RELATIONSHIP_PROPERTIES: dict[str, frozenset[str]]   # from docs/neo4j-schema.md v1.1 and v1.0 DERIVED_FROM
CONTEXT_CATALOG, CONTEXT_FALLBACK, CONTEXT_LEGACY = "catalog", "fallback", "legacy"
LEGACY_PROMPT = "graph_agent_legacy.txt"
def catalog_unknown_properties(cypher: str, snapshot) -> list[str]: ...   # ["TIS.Sequencer", ...]
def whole_node_returns(cypher: str) -> list[str]: ...                      # ["s", ...]

# schemas/graph.py
class GraphAgentPlan(BaseModel):
    ...                                   # existing fields unchanged
    context_mode: str | None = None       # catalog | fallback | legacy (spec D15)

# orchestrator.py, _execute_graph_turn, next to debug_payload["graph_plan"]:
#   debug_payload["graph_context"] = graph_plan.context_mode
```

`graph_agent`: when `getattr(config, "FORCE_PARSER_MODE", None) == "graph_legacy"`, it runs the legacy mode (spec E10:
the committed JSON blocks, the legacy prompt via `config._load_prompt(LEGACY_PROMPT)`, the old guard, no whole-node
guard) whatever the catalog's state. Otherwise, when `get_snapshot` succeeds, the schema block is
`render_graph_context(snapshot, get_type_details(config, codes))` with `codes = resolved_type_codes(plan, entity,
{r.title for r in snapshot.index})`, the protocol and assay JSON blocks become `render_vocabulary(get_vocabulary(config),
user_query)`, and the catalog guard and the whole-node guard run in the existing repair loop (one repair, then the empty
plan naming each problem); on `CatalogUnavailable` everything is as today and the mode is `fallback`. Every returned
plan carries `context_mode`. `system_agent` sends the same rendering for its schema block. `mcp_server.py`'s
`neo4j-schema` resource returns the structure and index when the catalog is live, else the file.

- [ ] **Step 1: Write the failing tests:** `(s:T_TIS) WHERE s.Sequencer = 'x'` rejected when TIS lacks it, accepted on
  `(s:T_D_SEQ)` when D.SEQ has it; `WHERE s:T_TIS` labels the variable; a plain `Sample` variable uses the union;
  system properties always pass; relationship properties per type; `` s.`Catalog#` `` and `s {.Organ}` checked; an
  unknown `T_` label reported; `db.index.fulltext.queryNodes(` and `date.truncate(` are not properties; literals and
  parameters ignored; `RETURN s`, `RETURN s LIMIT 5` and `collect(s)` over a Sample variable are whole-node returns,
  `RETURN s.id, s.Organ` and `count(s)` are not; `graph_agent` repairs once and then returns the empty plan naming
  `TIS.Sequencer` (fake LLM client); `graph_agent` sends the rendering when the catalog is live (mode `catalog`), the
  JSON when it is not (mode `fallback`), and the legacy prompt and JSON under `graph_legacy` even when the catalog is
  live (mode `legacy`, no whole-node refusal); codes come from the plan first; vocabulary blocks gated as in T2;
  `_execute_graph_turn` puts `graph_context` on the debug payload (fake agent and tool); the MCP resource in both
  states.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `NessieAI/tests/chat_nextseek`. Expected: pass as on the base commit plus the new tests.
- [ ] **Step 5: Commit** `feat(chat_nextseek): give the graph agent the rendered catalog, a per-label guard and a
  legacy mode (A3, A4)`.

**Acceptance:** spec sections 4.2 (wiring) and 4.3.

### Task T5: A7 subset, the prompt lines and the legacy prompt

**Files:** modify `prompts/graph_agent.txt`, `context/min_graph_schema.json`; create `prompts/graph_agent_legacy.txt`,
`tests/cn/test_graph_prompt_claims.py`.

- [ ] **Step 1: Write the failing tests:** `graph_agent_legacy.txt` exists and equals `git show
  4b3e087a:NessieAI/chat_nextseek/src/chat_nextseek/prompts/graph_agent.txt` byte for byte (the test holds the
  base file's sha256 as a constant, so it runs without git); `graph_agent.txt` contains no "exactly three properties",
  no "does NOT store" and no claim that names or attributes live only in the REST API; it contains a rule that forbids
  returning a whole Sample node and names the alternative (`s.id`, `s.uuid`, `s.type`, named properties, `count(*)`);
  `min_graph_schema.json` still parses, its Sample description no longer says descriptive metadata is not on the node,
  and no disambiguation rule gives "do not exist on graph nodes" as a reason; its other routing rules are unchanged
  (the test compares them with a frozen copy of the base commit's list).
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Copy** the base prompt to `graph_agent_legacy.txt` first, then **edit** the two live files per spec
  section 4.4 and D14. Keep the rest of `graph_agent.txt`'s three-step structure; replace "SampleType nodes have no
  code property (only title and id)" with the v1.1 fact that `Sample.type` and the `T_` label both identify the type.
- [ ] **Step 4: Run** `tests/cn/test_graph_prompt_claims.py`, the graph tests, and the hermetic drift guard (command 4).
  Expected: all pass.
- [ ] **Step 5: Commit** `feat(chat_nextseek): stop telling the graph agent that samples carry no metadata (A7 subset)`.

**Acceptance:** spec section 4.4.

### Task T6: the evaluation switch (product side)

**Files:** modify `agents/parser.py`, `nextseek_api/assistant/models_api.py`, `NessieAI/cc/turn.py`; create
`tests/cn/test_parser_force_mode.py`, `NessieAI/tests/api/test_eval_parser_force_gate.py`.

**Interfaces:**

```python
# agents/parser.py
FORCE_NOTE_MARKER = "by the evaluation switch"          # the harness pins the same phrase (T7)
FORCE_MODES = ("graph", "graph_legacy", "api")
ADVANCED_SEARCH_PATH = "/nextseek_api/samples/advanced_search/"

def _force_parser_mode(plan: ParserPlan, force_mode: str | None) -> ParserPlan:
    """Evaluation only: force a single-turn retrieval question to the graph or the API path, deterministically.

    Runs LAST in _apply_parser_guardrails, after the LLM call, so the parser's own choice is kept in the note.
    graph, graph_legacy: new_search -> graph_query (graph_legacy also names the legacy context in the note).
    api: graph_query -> new_search on the first REST endpoint candidate, else advanced_search.
    Every other mode, and force_mode None, returns the plan unchanged (the same object).
    """

def _apply_parser_guardrails(user_query, plan, session=None, force_mode=None) -> ParserPlan: ...
# parser_agent passes force_mode=getattr(config, "FORCE_PARSER_MODE", None)
```

```python
# nextseek_api/assistant/models_api.py, QueryRequest
force_parser_mode: Optional[Literal["graph", "graph_legacy", "api"]] = Field(None, description=(
    "Admin-only and evaluation-only: force the NExtSEEK parser to the graph path, the graph path with the pre-catalog "
    "context, or the API path for a retrieval question. Ignored unless the caller is a superuser and the server "
    "process sets NEXTSEEK_EVAL_PARSER_FORCE=1."))
```

```python
# NessieAI/cc/turn.py
EVAL_PARSER_FORCE_ENV = "NEXTSEEK_EVAL_PARSER_FORCE"

def _with_parser_force(chat_config, user, req):
    mode = getattr(req, "force_parser_mode", None)
    if (mode not in ("graph", "graph_legacy", "api") or os.environ.get(EVAL_PARSER_FORCE_ENV) != "1"
            or not bool(getattr(user, "is_superuser", False))):
        return chat_config
    forced = copy.copy(chat_config)
    forced.FORCE_PARSER_MODE = mode
    return forced
# start_task: in the NS branch, run_query(adapter, _with_parser_force(chat_config, request.user, req), ...);
# the PROD identity check above it still sees the singleton.
```

The note appended to `plan.notes` reads `forced to <mode> by the evaluation switch (parser chose <mode>)`, with
`, legacy context` added for `graph_legacy`.

- [ ] **Step 1: Write the failing tests:**
  - parser (Django lane): graph and graph_legacy: `new_search` becomes `graph_query` with `target_endpoint` None and
    the filters kept. api: `graph_query` becomes `new_search` on the first REST candidate, or `advanced_search` with no
    candidate; a multi-UID lineage question that `_force_graph_for_uid_lineage` forced to graph ends on the REST path
    with both notes. `ask_about_last_results`, `system_question`, `reporter` and `unsupported` are unchanged in every
    arm. With `force_mode` None the returned plan `is` the input plan; an unknown value leaves it unchanged. The note
    contains `FORCE_NOTE_MARKER` and the parser's original mode.
  - gate (ns/api variant): `_with_parser_force` returns the same object for a non-superuser (including `is_staff`
    True), without the environment flag, and for a missing or invalid value; otherwise a copy carrying
    `FORCE_PARSER_MODE`, the original untouched.
  - `QueryRequest` accepts the three values, rejects `cypher`, defaults to None; the ViewSet conventions tests
    (`nextseek_api/tests/test_viewset_conventions.py`, `test_viewset_conventions_schema.py`) still pass.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `tests/cn/test_parser_force_mode.py`, `tests/cn/test_parser_uid_lineage_routing.py`,
  `tests/cn/test_parser_refine_without_bundle.py`, `NessieAI/tests/api` (ns/api variant, including
  `test_nessie_boundaries.py`: no new back-edge), the conventions tests, and `python3
  scripts/validate_viewset_conventions.py`.
- [ ] **Step 5: Commit** `feat(chat_nextseek): add an evaluation switch that forces the parser to the graph or API path`.

**Acceptance:** spec sections 4.6 and E2.

### Task T7: the harness, forced runs and arms

**Files:** modify `NessieAI/tests/nessie_tests/http_driver.py`, `runner.py`, `cli.py`, `preflight.py`,
`nextseek_api/management/commands/nessie.py`, and their tests; create `NessieAI/tests/nessie_tests/tests/test_run_arms.py`,
`NessieAI/tests/api/test_nessie_command_flags.py`.

**Interfaces:**

```python
# http_driver.py
def drive(..., force_route=None, force_parser_mode: str | None = None, ...) -> DriveResult  # body["force_parser_mode"]

# runner.py
ARM_PRESETS = {
    "graph":        {"force_route": "ns", "force_parser_mode": "graph"},
    "graph_legacy": {"force_route": "ns", "force_parser_mode": "graph_legacy"},
    "api":          {"force_route": "ns", "force_parser_mode": "api"},
}
def run_case(v, *, ..., force_route=None, force_parser_mode=None, strip_route_criteria=False,
             payload_dir: Path | None = None, ...) -> NessieManifestEntry
    # payload_dir: each turn's final payload is written to <payload_dir>/<variant id>/<turn label>.json:
    # {query, task_id, session_id, status, route_obs, query_complete (reply, debug, files, artifacts), elapsed_s}
def run_suite(*, ..., force_route=None, force_parser_mode=None) -> NessieManifest
    # force_route set => strip_route_criteria=True, as run_paired does
def run_arms(*, base_url, auth_header, corpus_path, cases_path, out_dir, arms: list[str],
             resume=False, max_turns=None, full_timeout_s=600.0, skip_preflight=False,
             post_query=None, get_progress=None, sleep=time.sleep, clock=time.monotonic) -> dict
    # arms: one or more ARM_PRESETS names, e.g. ["graph", "api"] (Group A) or ["graph"] / ["graph", "graph_legacy"]
    # (Group B). Per question, every arm back to back; the first arm rotates with the question index.
    # The POC's questions are single turns in fresh sessions; run_case keeps multi-turn support for later runs.
    # After every (question, arm): <out>/<arm>/manifest.json rewritten (NessieManifest), payloads under
    # <out>/<arm>/payloads/, <out>/arms.json {run_meta: {git_sha, corpus_fingerprint, cases_sha256, arms, base_url},
    # questions: [{id, family, first_arm, arms: {arm: {status, task_ids, elapsed_s}}}]}.
    # report.generate_html per arm at the end and whenever the run stops.
    # resume: skip (id, arm) already in that arm's manifest; refuse when cases_sha256 differs.
    # max_turns: stop before a question whose arms would exceed it; 0 runs the preflight and no question.
    # Preflight unless skipped: preflight.assert_force_route_works, then assert_parser_force_works(arms).

# preflight.py
PARSER_FORCE_PROBE_QUERY = "How many tissue samples are in the database?"
FORCE_NOTE_MARKER = "by the evaluation switch"
class ParserForceRejected(PreflightRefused): ...
def assert_parser_force_works(post_query, get_progress, arms, *, sleep, clock, timeout_s=600.0) -> None
    # one full forced turn per arm: route_source forced, parser_plan.notes contains the marker,
    # parser_plan.mode is graph_query for graph and graph_legacy and not graph_query for api,
    # and debug.graph_context is catalog for graph and legacy for graph_legacy.
    # The remedies name the environment flag, the superuser account and the snapshot the venue serves.
```

Flags: `cli.py` gains `--force-route {ns,cc}` and `--force-parser-mode {graph,graph_legacy,api}` for a normal run (the
operator's "expose force_route for a normal run"). `manage.py nessie` gains the same two plus `--arms <names>`
(requires `--cases` and `--force-route ns`; exclusive with `--force-parser-mode`), `--resume` and `--max-turns` (with
`--arms` only), and `--password-env NAME` (read the password from that environment variable). With `--arms`,
`manage.py nessie` calls `run_arms` and prints each arm's summary line and the arms file.

- [ ] **Step 1: Write the failing tests** (host lane, fake `post_query`/`get_progress` as in the existing tests):
  - `drive` sends `force_parser_mode` only when set.
  - `run_case` writes one payload file per turn when `payload_dir` is given, and none otherwise.
  - `run_suite(force_route="ns")` passes `force_route` to every case and strips `route`, `engine` and `route_source`.
  - `run_arms`: one, two and three arms per question, the first arm rotating; manifests and `arms.json` rewritten
    after each arm (a raise inside the second arm leaves the first arm's entry on disk); `resume` skips completed
    (id, arm) and refuses a changed cases file; `max_turns` stops before exceeding, and 0 runs only the preflight; the
    preflight runs once and its refusal stops the run before any case; an unknown arm name is refused.
  - `assert_parser_force_works`: passes on a landed force; raises on a missing marker, on the wrong mode, on the wrong
    graph context, and on a dropped `force_route`, each with its own remedy.
  - `cli.py`: the two new flags reach `run_suite`; `--force-parser-mode` without `--force-route ns` is refused; the
    existing mutual-exclusion tests still hold.
  - `manage.py nessie` (ns/api variant, `call_command` with `run_suite` and `run_arms` patched): `--arms` reaches
    `run_arms`; `--arms` without `--cases` is an error; `--password-env` reads the variable and never prints it.
- [ ] **Step 2: Run them to see them fail.**
- [ ] **Step 3: Implement.** Reuse `run_case` for every turn; add no second poll loop, outage rule or cost rule.
- [ ] **Step 4: Run** the host lane (command 3) and `NessieAI/tests/api/test_nessie_command_flags.py` (ns/api
  variant). Expected: all pass except the five machine-bound failures.
- [ ] **Step 5: Commit** `feat(nessie_tests): run each question through forced NS arms`.

**Acceptance:** a two-arm run over a two-question cases file against fakes produces two manifests, two reports, the
payloads and `arms.json`; a one-arm run produces one of each; a normal run can force a route.

### Task T8: ground-truth tooling and the scorer

One agent, two parts: the truth part first (it does not depend on T7), then the scorer (it reads T7's run layout).

**Files:** create, under `NessieAI/tests/nessie_tests/`: `engine_truth.py`, `engine_compare.py`,
`scripts/derive_truth.py`, `scripts/build_engine_cases.py`, `tests/test_engine_truth.py`,
`tests/test_build_engine_cases.py`, `tests/test_engine_compare.py`.

**Interfaces, truth part:**

```python
# engine_truth.py (pydantic, host-safe: no Django import at module scope)
class Oracle(BaseModel):
    engine: Literal["graph_search", "cypher", "sql", "measured"]
    body: dict | None = None; statement: str | None = None; params: dict = {}; source: str | None = None
class Alternate(BaseModel):
    reading: str; required_numbers: list[float] = []; required_items: list[str] = []
class Expected(BaseModel):
    kind: Literal["count", "value", "list", "set", "none"]
    value: int | float | str | list | None = None
    required_numbers: list[float] = []; required_items: list[str] = []
    alternates: list[Alternate] = []
    sampletypes: list[str] = []; attributes: list[str] = []; relationships: list[str] = []
class TruthTurn(BaseModel):
    label: str; query: str; reading: str; oracle: Oracle | None; expected: Expected
    second_oracle: Oracle | None = None; single_source: bool = False   # a graph-only fact, one oracle
    derived_at: str | None = None
class TruthQuestion(BaseModel):
    id: str; family: str; group: Literal["A", "B"]; source: Literal["corpus", "ladder", "b2"]
    turns: list[TruthTurn]                     # one turn in this POC
    scorable: bool = True; exclusion: str | None = None
    flags: list[str] = []   # changed_by_merge, interpretive, entity_vocabulary_gap, broad_match
    corpus_numbers: list[float] = []   # numbers in the corpus's old reply regexes, for the summary
    merged_into: str | None = None     # a ladder or B2 question that duplicates another question
class Fingerprint(BaseModel):
    sample_count: int; catalog_hash: str; synced_at: str | None; derived_at: str
class TruthFile(BaseModel):
    name: str; group: Literal["A", "B"]; fingerprint: Fingerprint | None = None; questions: list[TruthQuestion]

def number_patterns(n: float) -> re.Pattern: ...   # 107412 matches "107,412", "107412", "107 412"; not "1,107,412"
def reply_satisfies(reply: str, expected: Expected) -> tuple[bool, str]: ...  # primary or an alternate, and which
```

- `derive_truth.py` runs inside the venue (`nessie_venue.sh exec NessieAI/tests/nessie_tests/scripts/derive_truth.py
  --truth /venue/truth/<file>.json [--only ids] | --summary | --fingerprint-only`). Oracles: `graph_search` by POST to
  `http://127.0.0.1:8000/nextseek_api/samples/graph_search/` as `demo` (password from `GS_DEMO_PASSWORD`), `total`
  from the response; `cypher` through `session.execute_read` with the settings' Neo4j; `sql` through
  `connections["seek"]` inside `START TRANSACTION READ ONLY`, refused unless it is one statement starting with `SELECT`
  or `WITH`; `measured` copies a number from a named results file. It fills `expected.value` and, for counts,
  `required_numbers`, runs `second_oracle` when present and records any disagreement, stamps `derived_at` and the
  file's fingerprint, and never writes anything but the truth file.
- `build_engine_cases.py`:
  - `--skeleton --selection <selection.json> --group a|b --family <f> [--part a|b] --out <file>`: a truth skeleton from
    `corpus.json` for the family's kept variants in the selection file's group (turns verbatim, oracle None,
    `corpus_numbers` from the old regexes); `--part` splits a family's kept list in file order (first half, second
    half); `--family other` takes Group B's kept variants outside `graph_traversal`.
  - `--questions <file> --source ladder|b2 --group a|b --out <file>`: the same for the ladder or B2 question files.
  - `--cases --group a|b --truth <dir> --out <file> [--pilot N --pilot-out <file>]`: the group's `--cases` file, one
    block per original family, one inline variant per scorable question that is not `merged_into` another (id kept,
    tags `engine_compare` and `group:A` or `group:B`), criteria engine-neutral only (`last_reply matches_re` per
    required number, `entity_sampletype_codes contains` per expected type, `outcome_observed true`). Pilots, taken
    deterministically: Group A 10 (5 corpus `sample_search`, 2 `harmonization`, 2 ladder, 1 B2); Group B 6 (3
    `graph_traversal`, 1 `lineage_tree`, 1 study-scoped `sample_search`, 1 investigation inventory).

**Interfaces, scorer part:**

```python
RULE_A = {"margin_points": 15, "p": 0.05, "failure_margin_points": 2, "latency_ratio": 1.25, "cost_ratio": 1.5}
RULE_B1 = {"overall": 0.80, "family_floor": 0.60, "family_min_questions": 5, "max_failed": 0.05}
RULE_B2 = {"margin_points": -5, "p": 0.05}   # G within 5 points of L, and no significant L advantage

def load_run(run_dir: Path) -> Run: ...
def stage_verdicts(payload: dict, turn: TruthTurn, arm: str, outputs_root: Path) -> dict[str, str]:
    # stages: route, switch, context (graph arms), entities, parser, request, engine_value, reply;
    # verdict pass | fail | unobserved | n/a
def engine_value(payload: dict, arm: str, outputs_root: Path) -> float | None:
    # graph arms: the graph debug JSON listed in query_complete.files; a single-row, single-number data_preview is
    #   the aggregate, else neo4j_output.total / count. api: the saved result at debug.raw_json_path, its total.
    # Container paths under /venue/outputs map onto outputs_root.
def question_cost(payload: dict, outputs_root: Path, prices: dict) -> float | None:
    # the run root's llm_calls.jsonl (the parent of the files/ directory the payload paths name), priced per model.
def mcnemar_exact(b: int, c: int) -> float: ...   # two-sided; (10, 2) -> 0.0386
def verdict_a(scores: dict, rule=RULE_A) -> dict: ...   # SUPPORTED | SUPPORTED WITH COSTS | NOT SUPPORTED
def verdict_b(scores: dict, with_legacy: bool) -> dict: ...  # STILL WORKS or NOT (option 1); NO WORSE or WORSE (2)
def main(argv=None) -> int: ...   # --group a|b; writes compare.json, compare.md, questions.csv next to the run
```

Void questions (the route force did not land, or a graph arm ran `fallback` or the wrong context) are listed and
excluded for that arm. A question whose parser chose a non-retrieval mode is scored as the product behaved and listed.
A question with a provider outage on any arm is listed for rerun and excluded. A reply scored correct that also
contains another number phrased as a total is flagged `contradiction_suspect` for triage (R2), which may overturn it.
Every verdict is computed with and without alternates, and Group B's scores are reported per family.

- [ ] **Step 1: Write the failing tests (truth part):** models round-trip; `number_patterns` matches the three
  spellings and rejects a longer number, a decimal continuation and a year inside a date; `reply_satisfies` accepts an
  alternate and says which; the SQL guard refuses `UPDATE`, two statements, and `SELECT ... INTO OUTFILE`;
  `derive_truth`'s fill logic with fake executors (no Django) fills counts, records a second-oracle disagreement, keeps
  a `single_source` question without complaint, and stamps the fingerprint; the skeleton takes exactly the selection's
  kept ids for the group and splits halves stably; each group's cases file loads with `corpus.load_case_file` and
  `select_cases`, skips `merged_into` questions, and carries no inline criterion outside the engine-neutral set; the
  pilot quotas and their stability for a fixed input.
- [ ] **Step 2: Run them to see them fail** (host lane), **implement the truth part**, run again.
- [ ] **Step 3: Commit** `feat(nessie_tests): add ground-truth files, an oracle runner and the case builder`.
- [ ] **Step 4: Write the failing tests (scorer part)** with synthetic payloads, truth and outputs: each stage passes
  and fails on its own evidence; a missing file makes a stage `unobserved`, not `fail`; the context precondition voids
  a `fallback` turn; `engine_value` reads an aggregate and a total; the path mapping; `question_cost` prices a
  two-model ledger; `mcnemar_exact(10, 2)` is 0.0386 to four places and `(0, 0)` is 1.0; `verdict_a` returns each of
  its three outcomes and `verdict_b` both outcomes of each option on constructed scores; the with-and-without
  alternates split; void, non-retrieval and outage questions handled as above; `compare.md` names the first failing
  stage per question and shows Group B per family.
- [ ] **Step 5: Run them to see them fail, implement, run the host lane.**
- [ ] **Step 6: Commit** `feat(nessie_tests): score the arms against ground truth`.

**Acceptance:** a skeleton, a filled truth file (fake executors) and each group's cases file round-trip through the
harness's own loaders; spec E6, E7 and E8 hold on synthetic data.

### Task T9: the venue script

**Files:** create `scripts/graph_search/nessie_venue.sh`, `scripts/graph_search/nessie_venue_check.py`; add one row to
`scripts/graph_search/README.md` (the only line this task writes in that file; the POC owns the rest).

Skeleton (bash, `set -euo pipefail`; every secret stays in the env files; nothing prints an environment value):

```bash
: "${GS_WORK:?set GS_WORK}"
: "${NEXTSEEK_LIVE_CHECKOUT:?set NEXTSEEK_LIVE_CHECKOUT to the checkout the live stack runs from}"
V="$GS_WORK/nessie/venue"; NAME=gs-nessie-venue
PORT="${GS_VENUE_PORT:-8010}"; MEM="${GS_VENUE_MEMORY:-6g}"; FLOOR="${GS_VENUE_MIN_GIB:-8}"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
bench_check() { [[ ! -e "$GS_WORK/.gs-bench-running" ]] || { echo "benchmark window open: refusing" >&2; exit 3; }; }
mem_check() { local a; a=$(awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo)
  echo "MemAvailable ${a} GiB"; (( a >= FLOOR )) || { echo "refusing: under ${FLOOR} GiB available" >&2; exit 3; }; }
case "${1:-}" in
  prepare)  # snapshot of HEAD, rendered settings, private directories
    umask 077; rm -rf "$V/src"; mkdir -p "$V/src" "$V/outputs" "$V/logs" \
      "$GS_WORK/nessie/runs" "$GS_WORK/nessie/cases" "$GS_WORK/nessie/truth" "$GS_WORK/nessie/questions"
    git -C "$REPO" archive HEAD | tar -x -C "$V/src"; git -C "$REPO" rev-parse HEAD > "$V/SNAPSHOT"
    # settings from the tracked template; only the participating-projects line comes from the operator's file
    ... ;;
  up)       bench_check; mem_check
    docker run -d --name "$NAME" --network nextseek_default -p "127.0.0.1:${PORT}:8000" \
      --memory "$MEM" --memory-swap "$MEM" --user "$(id -u):$(id -g)" \
      --env-file "$NEXTSEEK_LIVE_CHECKOUT/docker/db.env" --env-file "$NEXTSEEK_LIVE_CHECKOUT/docker/nextseek.env" \
      -e DJANGO_ALLOWED_HOSTS="127.0.0.1 localhost" \
      -e NEXTSEEK_INTERNAL_BASE_URL=http://127.0.0.1:8000 -e NEXTSEEK_BASE_URL=http://127.0.0.1:8000 \
      -e LOG_DIR=/venue/logs -e NEXTSEEK_OUTPUTS_DIR=/venue/outputs -e HOME=/tmp \
      -e NEXTSEEK_EVAL_PARSER_FORCE=1 -e NEXTSEEK_POSTERIOR_ROUTING_ENABLED=0 -e PYTHONDONTWRITEBYTECODE=1 \
      -e PYTHONPATH=/src:/src/NessieAI/chat_nextseek/src:/src/NessieAI/dmac_assistant/src \
      -v "$V/src":/src:ro -v "$V/outputs":/venue/outputs -v "$V/logs":/venue/logs \
      -v "$GS_WORK/nessie/runs":/venue/runs -v "$GS_WORK/nessie/cases":/venue/cases:ro \
      -v "$GS_WORK/nessie/truth":/venue/truth -w /src nextseek-nextseek:latest \
      /app/.venv/bin/gunicorn dmac.wsgi --bind 0.0.0.0:8000 --workers 2 --threads 4 \
        --worker-class gthread --timeout 1200 ;;
  check)    # overrides by comparison, the endpoint answers, the catalog is live, reads are READ
    docker exec "$NAME" sh -c 'test "$NEXTSEEK_INTERNAL_BASE_URL" = http://127.0.0.1:8000 &&
      test "$NEXTSEEK_EVAL_PARSER_FORCE" = 1 && test "$NEXTSEEK_POSTERIOR_ROUTING_ENABLED" = 0 &&
      test ! -e /var/run/docker.sock && echo overrides-ok'
    ... /app/.venv/bin/python scripts/graph_search/nessie_venue_check.py ;;
  exec)     shift; docker exec -i -w /src -e GS_DEMO_PASSWORD "$NAME" /app/.venv/bin/python "$@" ;;
  logs)     docker logs --tail 200 "$NAME" ;;
  down)     docker rm -f "$NAME" ;;
  *)        echo "usage: nessie_venue.sh prepare|up|check|exec|logs|down" >&2; exit 2 ;;
esac
```

`nessie_venue_check.py` (runs inside the venue, prints only counts, sizes, timings and pass flags): Django setup; the
chat config builds; `graph_catalog.get_snapshot` succeeds with schema version 1.1 (prints the hash and type count);
`get_type_details` and `render_graph_context` for TIS, D.SEQ and A.VCF, and for PAT and PAV, each within 32,768 bytes;
`tool_neo4j_query(config, "MATCH (s:T_TIS) RETURN count(s) AS n")` answers; META, INDEX, GUARD and vocabulary timings;
worker memory at rest (`/proc/<pid>/status` of the gunicorn workers). It writes `$GS_WORK/nessie/runs/V1/venue_check.json`
through the `/venue/runs` mount.

- [ ] **Step 1: Write the check's pure parts first with tests** (a `--dry-run` that prints the `docker run` command
  with the env-file paths but no values; `bash -n` clean; `shellcheck` clean when installed).
- [ ] **Step 2: Implement** `prepare`, `up`, `check`, `exec`, `logs`, `down`. If the image's virtualenv refuses a
  non-root `--user`, fall back to the image's user and add a `chown` step in a throwaway container at `down`; record
  which in the README row.
- [ ] **Step 3: Verify without the live stack:** `prepare` builds the tree (mode 700, the settings file mode 600, no
  PROD block active); `up` refuses while the benchmark flag exists and when memory is short (simulated with
  `GS_VENUE_MIN_GIB=1000`).
- [ ] **Step 4: Commit** `feat(scripts): add the Nessie evaluation venue for graph_search follow-up 1`.

**Acceptance:** spec section 6, short of running against the live databases (that is V1).

### Gate B (before stage V; run by the workflow's gate step, not a task agent)

- [ ] **Docs.** `NessieAI/tests/nessie_tests/README.md` gains a section "Comparing the graph and API agents" (the
  flags, the arms, the payloads, the scorer, where truth and outputs live, that paid runs need approval);
  `NessieAI/tests/README.md` gains one lane-table row for the forced-arm run (PAID, approval per run);
  `NessieAI/chat_nextseek/CLAUDE.md` says the graph schema is no longer written into `context/`, the committed
  `neo4j_schema.json` is the fallback and arm L's context, and the evaluation switch is off unless the environment flag
  is set; the spec's and this plan's status lines say what is built. Commit `docs(nessie_tests): document the graph
  and API agent comparison`.
- [ ] Every touched area passes in its lane: `NessieAI/tests/chat_nextseek`, `NessieAI/tests/ns` and
  `NessieAI/tests/api` (ns/api variant), the harness host lane, the hermetic drift guard, the entity_tree tests.
  Compare failures with a run of the same paths on `origin/feat/graph-search`; any difference is explained.
- [ ] `python3 ci/docs_map.py` and `python3 scripts/validate_viewset_conventions.py` clean.
- [ ] The diff scanned for emails, home paths and tokens; `feat/graph-search-nessie` pushed (only it).

---

## Stage V: the venue

### Task V1: prepare, start and check the venue (OPERATOR approves the first read of the live databases)

- [ ] `test -e "$GS_WORK/.gs-bench-running"` prints nothing; `free -g`; `df -h /`.
- [ ] The live graph holds the UID-fix sync (the operator confirms; `load_live.sh verify` was run after it).
- [ ] `nessie_venue.sh prepare` from the pushed HEAD; `nessie_venue.sh up`; wait for gunicorn (`logs`).
- [ ] `nessie_venue.sh check`: `overrides-ok`; an unauthenticated GET of `http://127.0.0.1:8010/nextseek_api/`
  answers 401 or 403; `venue_check.json` shows the catalog live, schema 1.1, every rendering within budget, the
  TIS count, the catalog timings and worker memory.
- [ ] Record the snapshot sha, the image id and `venue_check.json` in `$GS_WORK/nessie/runs/V1/`.

**Gate V:** the catalog path is live (not the fallback) and every rendering fits; the venue writes only where section
6 of the spec says. On failure: fix in the owning task, re-push, `prepare` again.

## Stage G: ground truth

Each task owns its truth files under `$GS_WORK/nessie/truth/` and never writes a tracked file. Oracles run only
through `derive_truth.py` inside the venue (read-only by construction). The question selection is
`$GS_WORK/nessie/selection.json` (both groups' kept and excluded variants with reasons, written at planning time; gate
T reviews it).

| Task | Group | Truth files | Questions |
|---|---|---|---:|
| G1 | A | `a_sample_search_1.json` (first half of the kept list) | 29 |
| G2 | A | `a_sample_search_2.json` (second half) | 28 |
| G3 | A | `a_harmonization.json`, `a_retrieval.json`, `a_ladder.json`, `a_b2.json` | 8 + 1 + 25 + 9 |
| G4 | B | `b_graph_traversal_1.json` (first half) | 30 |
| G5 | B | `b_graph_traversal_2.json` (second half) | 30 |
| G6 | B | `b_other.json` (7 `lineage_tree`, 5 `sample_search`, 4 `project_summary_report`, 1 each of `retrieval_path_selection`, `vocabulary_resolution`, `engine_routing`), `b_b2.json` (the lineage shape) | 19 + 1 |

### Tasks G1, G2, G4, G5, G6 and G3's corpus part

- [ ] `build_engine_cases.py --skeleton --selection "$GS_WORK/nessie/selection.json" --group a|b --family <f> [--part
  a|b] --out "$GS_WORK/nessie/truth/<file>.json"` (host lane dependencies; the venue sees the same directory at
  `/venue/truth`).
- [ ] For every question: write the reading (per spec E5's default reading), the oracle (a graph_search body for
  search shapes; Cypher for aggregates, value discovery, studies, investigations, lineage, assays and protocols; SQL
  where `json_metadata`, `projects_samples` or parent tokens are the clearest source), `expected.kind`, the types,
  attribute names and relationships the question names, and flags: `changed_by_merge` (the answer differs from the
  corpus's old number), `interpretive` (with the alternate readings), `entity_vocabulary_gap` (a type or project the
  entity step cannot resolve), `broad_match` (more than 50,000 matches, run in the last block). Mark `single_source`
  where only the graph holds the fact (a paper-level Study, a DERIVED_FROM assay or protocol label).
- [ ] Exclude with a reason any question with no establishable answer.
- [ ] Run `derive_truth.py --truth /venue/truth/<file>.json`; give a `second_oracle` in another engine to every count
  flagged `changed_by_merge` or `interpretive` and to at least one in five of the rest, where MySQL holds the fact; a
  disagreement is resolved or the question is marked `interpretive`.
- [ ] Report back: counts of scorable, excluded, changed, interpretive and single-source questions, and every
  disagreement.

### G3's ladder and B2 part, and G6's B2 shape

- [ ] Write `$GS_WORK/nessie/questions/ladder.json` (the 25 compat rungs of `$GS_WORK/runs/ladder/queries_ladder.json`)
  and `b2.json` (the 9 Group A shapes and the lineage shape in `selection.json`) as natural-language questions a user
  would ask for the same body (for example `attr_organ_exact` becomes "How many tissue samples have Organ equal to
  Lung?"). Mode 600. One ladder value is a real person's name: it stays in these files only.
- [ ] Skeletons with `--questions`; oracles `measured` from `$GS_WORK/runs/ladder/results.json` (after the UID rerun)
  and `$GS_WORK/runs/B2/results.json` (the `demo` totals), each re-run as a graph_search oracle.
- [ ] A question whose meaning equals a kept corpus question gets `merged_into` that id (for example the rung
  `err_empty_text`, a type-only TIS search, and the corpus question "Find all tissue samples in the database"); its
  number stays as a cross-check on the other question's truth.

### Task G7: cases, pilots and the truth summary

- [ ] On the host: `build_engine_cases.py --cases --group a --truth "$GS_WORK/nessie/truth" --out
  "$GS_WORK/nessie/cases/group-a.json" --pilot 10 --pilot-out "$GS_WORK/nessie/cases/pilot-a.json"`, and the same with
  `--group b ... --pilot 6` into `group-b.json` and `pilot-b.json`.
- [ ] `derive_truth.py --summary` over every file: totals per group, source and family, changed, interpretive,
  single-source, excluded, merged, the fingerprint, and the `broad_match` list.

**Gate T (OPERATOR):** the operator reads the summary, the selection (both groups, kept and excluded with reasons, and
the 31 REST-routed lineage questions left out of Group B), every exclusion, every interpretive question and a sample
of the rest, and chooses Group B's option (arm L or not). Truth files are frozen from here to the end of P3; a
live-graph re-sync before then means re-deriving.

## Stage P: paid runs (OPERATOR, each step approved separately)

Before every paid step: the benchmark flag is absent; the venue is up; `derive_truth.py --fingerprint-only` matches
the truth; `$GS_WORK/nessie/prices.json` exists. `<b-arms>` is `graph` or `graph,graph_legacy` per the operator's
choice at gate T.

### P1: preflight (3 NS turns, 4 with arm L; under $1)

- [ ] Command 7 with `--cases /venue/cases/pilot-a.json --arms graph,api --max-turns 0`, then with
  `--cases /venue/cases/pilot-b.json --arms <b-arms> --max-turns 0` only when arm L is chosen (it adds the legacy
  probe). Stop rule: any refusal. The remedy is in the message (account, environment flag, snapshot, context).

### P2: pilots (26 NS turns, 32 with arm L; about $4 or $5)

- [ ] Command 7 with `--cases /venue/cases/pilot-a.json --arms graph,api --out /venue/runs/pilot-a`, and with
  `--cases /venue/cases/pilot-b.json --arms <b-arms> --out /venue/runs/pilot-b`. Then the scorer (command 8) on both.
- [ ] Stop rule: more than 2 infrastructure errors, any OOM-killed worker (`docker inspect` and the gunicorn log), any
  `fallback` graph context, any stage the scorer reports `unobserved` for a reason other than the question, or more
  than $0.30 per turn.

**Gate P (OPERATOR):** both pilots' `compare.md` reviewed; the pass rules (spec E7) accepted or changed; P3 approved
with its cap.

### P3: the full runs (254 NS turns, 328 with arm L; about $38 or $49; cap $60 or $75)

- [ ] Group A: command 7 with `--cases /venue/cases/group-a.json --arms graph,api --out /venue/runs/full-a
  --max-turns <block end>`, repeated with `--resume` in blocks of about 60 turns (the pilot's questions are skipped by
  id). Group B: the same with `group-b.json`, `--arms <b-arms>` and `--out /venue/runs/full-b`. A block runs detached
  inside the venue (`docker exec -d gs-nessie-venue sh -c '... > /venue/runs/<run>/console.log 2>&1'`) and is watched
  through `arms.json`; stopping one is `docker exec gs-nessie-venue pkill -f "manage.py nessie"` (a turn in flight
  finishes server-side).
- [ ] Between blocks: the fingerprint check, the benchmark flag, and spend so far (the scorer's cost table).
- [ ] The `broad_match` questions form the last Group A block, run only after the operator confirms the memory floor.
- [ ] Stop rule per block: more than 10% infrastructure errors, a provider outage, a changed fingerprint (void the
  block), the benchmark flag present, or the cap reached.

### P4: Group A repeats (optional; 40 questions, two more runs per arm, 160 NS turns, about $24, cap $35)

- [ ] 40 Group A questions from the full run (a stratified sample by source and family), two more runs per arm, into
  their own `--out`; the scorer reports within-arm agreement.

## Stage R: the result

- [ ] **R1:** the scorer (command 8) with `--group a` over `runs/pilot-a` plus `runs/full-a` (and the P4 run when
  there is one), and with `--group b` over `runs/pilot-b` plus `runs/full-b`.
- [ ] **R2:** triage per arm with the `nessie-run-review` output skill (`NessieAI/tests/nessie_tests/output-skill/SKILL.md`)
  for every failed or `contradiction_suspect` question, recording overturned verdicts in `triage.json` beside the run;
  re-run the scorer with the triage applied.
- [ ] **R3 (OPERATOR):** Group A's verdict and Group B's, and what follows (A6 and retiring the API agent for metadata
  questions, fixes to the graph agent's context, more evidence, the later question sets of spec section 8.2, or none).

---

## Stage S: A1, server-injected scope (after the POC, with the merge work; separate approval)

Designed in spec section 8.1. Not executed by this plan's POC workflow; listed so the merge work starts from it.

| Task | Files | Tests first |
|---|---|---|
| S1 carrier and host seam | `chat_nextseek/graph_scope.py` (new: `GraphScope`, `scope_of`, `with_graph_scope`), `nextseek_api/assistant/graph_scope.py` (new: `graph_scope_for`, `attach_graph_scope`), `nextseek_api/services/assistant.py`, `nextseek_api/services/cc_assistant.py`, `nextseek_api/services/evaluator.py`, `NessieAI/cc/turn.py`, `NessieAI/ns/retry.py` | superuser to admin, a member to their projects, `ScopeUnavailable` to None; every seam puts a scope on the copy the engine receives and never on the singleton; no new back-edge (`test_nessie_boundaries.py`) |
| S2 the injector | `chat_nextseek/cypher_scope.py` (new), `helpers/tools/neo4j.py` (non-admin queries go through it; `__scope*` parameters from the model refused) | text-level tests for every row of the spec's shape table, including the refusal fallback |
| S3 the adversarial suite | `NessieAI/tests/chat_nextseek/test_cypher_scope_adversarial.py` (new) and a lane script that starts a throwaway, memory-capped Neo4j with a two-project fixture graph plus orphans; Group B's recorded Cypher is a source of real queries | the differential oracle: each query's scoped result equals the admin result over the graph with the caller's invisible Samples removed; admin results unchanged; refusals only on the allowed list |
| S4 fixed reads, lineage, rendering | `reports/runners.py` (`run_scoped_read`), `nextseek_api/services/entity_tree.py` (lineage scoped after the query), `graph_catalog.py` and `graph_context.py` (scoped Q2 and structure-only forms), the CLI and `mcp_server.py` (refusal without a scope) | a non-member sees zero foreign rows on every path; the renderer shows no foreign counts or values |

**Gate S:** the suite passes; then the merge work may begin.

## Self-review

- Every spec decision maps to a task: R1 (stages G to R), R2 (stage S), R3 (T3), R4 and R9 (T6, T7), R5 (the task
  list), R6 (the selection, stage G), R7 (T8's Group A rule), R8 (T8's truth part, stage G), R10 (T6, T8, T9), R11
  (nine stage B agents, docs in gate B), R12 (T4's legacy mode, T5's legacy prompt, T8's Group B rules, the optional
  arm in stage P); D3 (T3), D6 to D9 (T1, T2, T4), D11 (no task), D13 (T4, T5), D14 (T5), D15 (T4); E1 to E3 (T6,
  T7), E4 and E5 (T8, stage G), E6 to E8 (T8), E9 (no change by design), E10 (T4, T5); the venue (T9, V1); paid runs
  (P1 to P4).
- No task edits a file owned by follow-up 2 or the POC; `scripts/graph_search/README.md` gets one row only.
- Names used across tasks are defined once in the task that owns their file: `CatalogSnapshot`, `TypeDetail`,
  `get_snapshot`, `get_type_details`, `render_graph_context`, `resolved_type_codes`, `mask_cypher`, `write_clause`,
  `GraphAgentPlan.context_mode`, `FORCE_NOTE_MARKER`, `_force_parser_mode`, `_with_parser_force`, `ARM_PRESETS`,
  `run_arms`, `assert_parser_force_works`, `TruthFile`, `reply_satisfies`, `engine_value`, `verdict_a`, `verdict_b`.
- Every paid step has a turn count, an estimate, a cap or a stop rule, and its own approval.

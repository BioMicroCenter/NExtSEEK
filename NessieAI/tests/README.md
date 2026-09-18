# NessieAI/tests/

Every Nessie Python test, mirrored by area, and the one home of every AI test command.
Other docs link here rather than repeat a command.

Four runtimes are involved: Django pytest in the app image, the cc-runtime uv env, Node (vitest and
Playwright), and a host uv env for the harness. So "all tests in one place" means one tree and this
one table, not one command. Never pass `NessieAI/` wholesale to pytest: name the areas.
There is no `conftest.py` at `NessieAI/tests/` itself; that keeps the harness host lane Django-free.

## Areas

| Folder | Tests |
|---|---|
| `cc/` | the CC engine, op registry, port and compose guards, step 7 gate tooling (`step7_catalog/`, `scripts/`); `NessieAI/tests/cc/step7_catalog/R26-live-gate-prereqs-runbook.md` holds the prerequisites for the live Step 7 gate; generated run bundles land in `acceptance_evidence/step7/` (`NessieAI/tests/cc/acceptance_evidence/step7/README.md`) |
| `router/` | the router, route capabilities, posterior routing; `fixtures/` holds the flag-off baseline |
| `hibayes/` | HiBayes |
| `ns/` | the granular ops and write gate |
| `api/` | the AI HTTP and websocket surface, and `test_nessie_boundaries.py` |
| `schema_rag/` | schema retrieval |
| `chat_nextseek/` | the NS engine, including `evaluator/` |
| `build_tools/` | the surface generators |
| `e2e/` | the catalog-driven end-to-end DSL (`python -m NessieAI.tests.e2e`) |
| `nessie_tests/` | the router-aware harness, its corpus and the two review playbooks |

## Lanes

| Lane | Runtime | Command | Where to run | Cost |
|---|---|---|---|---|
| Django AI suites | app image | "Django lane" block below, with one or more `NessieAI/tests/<area>` paths | host, throwaway container | free |
| CC clean lane | live app container | "CC clean lane" block below | inside `nextseek`, which runs the baked image | free |
| CC hermetic | host uv | "CC hermetic lane" block below | repo root | free |
| Graph scope lane | app image beside a throwaway Neo4j | "Graph scope lane" block below | host, over an exported tree | free |
| Harness unit tests | host uv | `uv run --no-project --with pytest --with pydantic --with requests --with beautifulsoup4 --with orjson python -m pytest NessieAI/tests/nessie_tests/tests -q -p no:cacheprovider` | repo root | free |
| Harness DB/contract tests | live app container | "Harness container lane" block below | inside `nextseek` | free |
| Harness route tier | host uv, or the app container | host: `uv run --no-project --with pydantic --with requests --with beautifulsoup4 --with orjson python -m NessieAI.tests.nessie_tests --base-url http://localhost:8000 --tier route --scope specific`; container: `docker exec nextseek uv run manage.py nessie --tier route` | repo root, or inside `nextseek` | cheap, not free: the router runs on every turn |
| Harness full tier | app container only | `docker exec nextseek uv run manage.py nessie --base-url http://localhost:8000 --tier full --scope all` | inside `nextseek` | PAID, approval per run |
| Paired `--bayesian` run | host | `NessieAI/tests/nessie_tests/output-skill-bayesian/SKILL.md` | repo root | PAID, approval per run |
| Forced NS arms (graph agent against API agent) | app image, in the evaluation venue | `scripts/graph_search/nessie_venue.sh run <name> --cases /venue/cases/<file>.json --force-route ns --arms graph,api`, then the scorer; the runbook, with turn counts, caps and stop rules, is stage P of `docs/superpowers/plans/2026-09-15-graph-search-nessie.md`, and the flags are in `NessieAI/tests/nessie_tests/README.md` "Comparing the graph and API agents" | the venue `gs-nessie-venue`, which only the operator starts | PAID, launched by the operator only |
| Catalog E2E | host | `python -m NessieAI.tests.e2e` (needs a seeded instance and live LLM credentials) | repo root | PAID |
| Real-stack acceptance | live app container | "Paid acceptance" block below | inside `nextseek` | PAID, approval per run |
| Acceptance bundle re-check | host or container | "Bundle re-check" block below | repo root | free |
| cc-runtime unit | cc-runtime uv env | `cd NessieAI/docker/cc-runtime && uv run --no-project --with pytest --with polars --with fastexcel --with xlsxwriter --with orjson --with pydantic --with httpx --with websockets python -m pytest tests/unit -q -o addopts=""` | host | free |
| Plugin `bin/` dispatch | cc-runtime uv env | `cd NessieAI/docker/cc-runtime && uv run --no-project --with pytest --with httpx --with pydantic --with websockets python -m pytest build_context/plugins/nextseek/bin/tests -q -o addopts=""` | host | free |
| Chat panel unit | Node | `cd NessieAI/chat_frontend && npm ci && npm run test` | host | free |
| Chat panel build | Node | `cd NessieAI/chat_frontend && npm run build:embedded` (type-checks, then writes `static/js/chat_assistant/`) | host | free |
| Chat panel browser | Node + Playwright | `cd NessieAI/chat_frontend && npm run test:e2e` (mock project; real-backend projects need a flag and a deployed instance) | host | free |

### Django lane

The app image's own virtualenv over a read-only mount of the checkout. The two `mkdir`s are needed
because importing `dmac.settings` creates them.

```bash
mkdir -p schema_rag/duckdb schema_rag/embedding_models
docker run --rm -i --network none -e LOG_DIR=/tmp/nextseek-logs \
  -e DJANGO_SETTINGS_MODULE=dmac.test_settings -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD":/src:ro -w /src nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest NessieAI/tests/router NessieAI/tests/hibayes -q -p no:cacheprovider
```

- The image's editable `chat_nextseek` and `dmac_assistant` installs point at the image's own copy. To test uncommitted engine source, either mount a writable copy of `NessieAI/chat_nextseek` over `/app/NessieAI/chat_nextseek`, or put the checkout's copies first on the path with `-e PYTHONPATH=/src:/src/NessieAI/chat_nextseek/src:/src/NessieAI/dmac_assistant/src`.
- `ns` and `api` need a writable copy of the checkout, mounted at `/src`, with `startup/dev/lane_local_settings.py` copied to `dmac/local_settings.py`, plus `-e GCP_API_KEY=dummy -e CATALOG_FILE=/src/NessieAI/chat_nextseek/agent_model_catalog.json`. `CATALOG_FILE` must match the mount: `startup/dev/run_full_test_lane.sh`, the script that drives this lane end to end, mounts its tree at `/work` and sets it to match.
- `schema_rag` needs the embedding-model cache, provisioned once per checkout with `startup/dev/provision_embedding_model.sh`, and `-e HF_HUB_OFFLINE=1`. Its live module runs only with `RUN_SCHEMA_RAG_LIVE=1` and egress to fairdomhub.org.
- `hibayes` has tests that need an external delivery directory or a migrated MySQL store; their MySQL lane script is archived under `NessieAI/history/plan018/`.
- CI runs these areas by name from `.github/workflows/ci-pytest.yml`, diffed against `ci/pytest-baseline.txt`.

### CC clean lane

```bash
docker exec -w /app nextseek uv run --no-sync python -m pytest NessieAI/tests/cc/ \
  --create-db -k 'not realstack' \
  --ignore=NessieAI/tests/cc/test_step7_compose_deploy.py \
  --ignore=NessieAI/tests/cc/test_cc_realstack.py
```

Never widen it to all of `nextseek_api/` in-container: that path carries hundreds of known environmental errors.

### CC hermetic lane

```bash
PYTHONPATH="$PWD:$PWD/NessieAI/dmac_assistant/src" uv run --no-project --with pytest --with orjson \
  --with 'pydantic>=2.13' --with 'baml-py==0.222.0' python -m pytest NessieAI/tests/cc/ \
  --noconftest -p no:cacheprovider -q --continue-on-collection-errors \
  --ignore=NessieAI/tests/cc/test_cc_realstack.py
```

Modules that import Django or docker error at collection under this dependency set; that is expected.
The `host_only` source-tree checks have their own lane, below.

### Source-tree hygiene (`host_only`)

Asserts on the checkout, not the image (the image strips `.gitignore` by design). It needs a
**writable** checkout copy (importing the settings creates directories) and **both** the docker CLI
and the compose plugin mounted. `--project /app` keeps uv on the image env, not the mounted checkout's.
Two of the marked modules moved to `nextseek_api/tests/repo_guards/`, so name both paths.

```bash
docker run --rm -v <WRITABLE checkout copy>:/repo -w /repo \
  -v /usr/bin/docker:/usr/local/bin/docker:ro \
  -v /usr/libexec/docker/cli-plugins:/usr/local/lib/docker/cli-plugins:ro \
  nextseek-nextseek:latest uv run --project /app --no-sync python -m pytest -m host_only \
  NessieAI/tests/cc/ nextseek_api/tests/repo_guards/ -q
```

### Harness container lane

`docker cp` is ephemeral and only for testing (`DEPLOYMENT.md` §1). The old form, copying `nessie_tests` to `/app/`, succeeds and tests stale code.

```bash
docker cp NessieAI/tests/nessie_tests nextseek:/app/NessieAI/tests/
docker exec -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  sh -c 'cd /app && uv run pytest NessieAI/tests/nessie_tests/tests_container/ --no-migrations -v'
```

### Paid acceptance

Skipped unless `RUN_REALSTACK=1`. Spends real LLM budget: get the owner's approval for each run.

```bash
docker exec -e RUN_REALSTACK=1 -e SEEK_TEST_USER=<u> -e SEEK_TEST_PASS=<p> nextseek sh -lc \
  'cd /app && uv run python manage.py test NessieAI.tests.ns.test_granular_realstack \
   --settings=dmac.test_settings_realstack --noinput --keepdb -v2'
docker exec -e RUN_REALSTACK=1 -e SEEK_TEST_USER=<u> -e SEEK_TEST_PASS=<p> nextseek sh -lc \
  'cd /app && uv run python manage.py test NessieAI.tests.cc.test_cc_realstack \
   --settings=dmac.test_settings_realstack --noinput -v2'
```

`dmac.test_settings_realstack` runs against the live MySQL, so Django creates a throwaway `test_dmac`
database there. The app's database user needs a grant on it, scoped and reversible (revoke it with
the matching `REVOKE` when you are done):

```sql
GRANT ALL ON `test_dmac`.* TO seek_db_user@'%';
```

The recorded runs are frozen in `NessieAI/history/ns/acceptance_evidence/` and `NessieAI/history/cc/`.

### Bundle re-check

Zero spend. Re-verifies a recorded acceptance run and trusts no artifact's own PASS.

```bash
docker exec nextseek uv run --no-sync python -m NessieAI.tests.cc.validate_cc_acceptance outputs/cc_acceptance/<run_id>
python3 -m NessieAI.tests.cc.validate_step7_compose_deploy <run_dir> [repo_root]
```

### Graph scope lane

Proves the project scope on the graph agent's Cypher (`NessieAI/chat_nextseek/src/chat_nextseek/cypher_scope.py`)
against a real Neo4j: a private, throwaway database on its own network, loaded with a synthetic three-project graph
(`NessieAI/tests/chat_nextseek/graph_scope/fixture_graph.py`), and pytest over
`NessieAI/tests/chat_nextseek/graph_scope/` in the app image. Nothing else is touched; the script removes its
database and network on every exit, and exits 0 with `SKIP` when docker is not available. Run it on an exported tree
(a worktree mount's `.git` pointer file breaks unrelated tests), through the host's one-heavy-step wrapper when there
is one:

```bash
git archive HEAD | tar -x -C <dir>
NessieAI/tests/chat_nextseek/graph_scope/lane.sh <dir> <outdir>            # both arms
NessieAI/tests/chat_nextseek/graph_scope/lane.sh <dir> <outdir> -k prover  # the prover arm only
```

- Output: `<outdir>/pytest-graph-scope.txt`, one summary line in `<outdir>/exit-graph-scope.txt`.
- Callers: projects {1, 3}, project {2}, no projects, admin. Statements: the taught corpus and refusal table
  (`graph_scope/battery.py`, shared with the unit tests), the report runners' statements, and a seeded generator
  (`graph_scope/generator.py`).
- Assertions: every accepted statement's rows equal what the original returns on the graph with everything the
  caller cannot see deleted; no row carries a marker the caller may not read; refusals carry their expected codes;
  admin runs the submitted text; every write is refused and the graph is unchanged; the prover never reads as a
  comment what the server runs as code.
- The tool arm (`test_tool_*`) goes through `tool_neo4j_query` and reads its result's `scope` field.
- Without `GRAPH_SCOPE_NEO4J_URI` and `GRAPH_SCOPE_NEO4J_PASSWORD` every test in the folder skips, so the Django lane
  reports them skipped.

## Tests that stay in their package

| Where | Why |
|---|---|
| `NessieAI/chat_frontend/` (vitest and Playwright) | Node resolves imports from the importing file, and `NessieAI/chat_frontend/package.json` is the only `node_modules` root |
| `NessieAI/docker/cc-runtime/tests/` | pytest takes its config from the cc-runtime project, and the tests resolve paths against that directory |
| `NessieAI/docker/cc-runtime/build_context/plugins/nextseek/bin/tests/` | baked into the image with the plugin |

## AI assertions inside core tests

These stay with the code they mostly test, and carry some AI checks:
- `nextseek_api/tests/test_migration_0009_normalize_chat_log_turn_ids.py` (tests a migration; migrations stay)
- in `nextseek_api/tests/`: `test_endpoint_descriptions_safety.py`, `test_final_coverage_sweep.py`, `test_models_coverage.py`, `test_migration_0007_structure.py`, `test_migration_0007_heal_db.py`, `test_is_staff_not_admin.py`, `test_project_export.py`, `test_cors.py`, `test_api_docs_authentication.py`, `test_viewset_conventions.py`
- `ci/smoke/test_flows.py` (the Nessie checks in the post-deploy smoke lane)
- `ci/smoke/test_nessie.py` (the Nessie CI lane: every Nessie route and chat control, then three NS questions and one CC question through the real chat page, run after an app rebuild on local and dev; PAID, so `./startup.sh rebuild --no-nessie` and `./startup.sh ci --no-nessie` skip it) and `ci/smoke/test_nessie_unit.py` (its no-stack pins); `ci/smoke/README.md` "Nessie lane" is its doc
- in `seek/tests/`: `test_navbar.py`, `test_home_dashboard.py`, `test_context_seed_tables.py`

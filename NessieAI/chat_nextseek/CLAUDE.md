# Working in `NessieAI/chat_nextseek/`

This package is edited in place. The sync script that once refreshed it from a separate
repository is retired; never run it (`NessieAI/chat_nextseek/README.md`). Rules that
span units are in `NessieAI/CLAUDE.md`.

## Invariants

Break one of these and something outside this directory stops working, usually
without an error at the point of the change.

- **Editing here is not enough to change a running instance.** This tree is
  baked into the application image (`DEPLOYMENT.md` §0), and the rebuild table in
  `DEPLOYMENT.md` §3.2 lists it among the paths that need `./startup.sh rebuild`.
  No runtime bind mount covers anything under this directory.
- **The exported symbol list is a downstream contract.** The pin at
  `NessieAI/tests/chat_nextseek/test_portable_contract.py:34-40` fails the moment the
  list drifts, and the file opens by saying a failure there is a breaking change
  for the plugin consumer (`NessieAI/tests/chat_nextseek/test_portable_contract.py:1-5`).
- **The two package `__init__` re-export shims are permanent public API.** The
  helpers shim says so at `NessieAI/chat_nextseek/src/chat_nextseek/helpers/__init__.py:3-6`
  and the agents shim at `NessieAI/chat_nextseek/src/chat_nextseek/agents/__init__.py:3-6`; dropping a
  re-export breaks importers that go through the package rather than the module,
  such as `NessieAI/ns/granular.py:88`.
- **Every Cypher reaches Neo4j through `tool_neo4j_query`, which refuses a config without a
  `GraphScope`.** The scope rides on the per-request config (`graph_scope.py`): the ViewSets
  resolve it (`plain_scope` in `nextseek_api/graph_search/scope.py`) and hand it to the
  orchestrator as `graph_scope`, and the single-operator surfaces (CLI, MCP server, app,
  evaluator) are admin only with `CHAT_NEXTSEEK_GRAPH_ADMIN=1` or `--graph-admin`. A caller who
  is not an admin runs only what `cypher_scope.scope_cypher` proves, with the scope inserted;
  a refused graph question falls back to graph_search. A new path that runs Cypher any other
  way, or builds its own config, bypasses the scope or refuses every graph query
  (spec `docs/superpowers/specs/2026-09-18-graph-cypher-scope.md`). The same scope decides
  what the catalog's vocabulary reads (`graph_catalog.get_vocabulary`, and the committed
  fallback files through `committed_schema`) and what the report runners' SQL reads
  (`reports/runners.py::_report_projects`); a new catalog read or relational report that
  skips it shows a caller other projects' records.
- **Half an identity is treated as none.** `NessieAI/chat_nextseek/src/chat_nextseek/orchestrator.py:155-164`
  refuses a credential pair with one side missing, because applying only the
  supplied half leaves the other on the service account and issues the request
  under a mixed identity.
- **A construction-time raise inside the config object stops Django booting.**
  The settings overlay builds one at module scope
  (`startup/dev/lane_local_settings.py:19`), so the missing-provider-key raise at
  `NessieAI/chat_nextseek/src/chat_nextseek/config.py:497-500` takes the whole site down,
  not just the chat panel.
- **The chat-log cap is duplicated across the boundary and must stay in step.**
  `NessieAI/chat_nextseek/src/chat_nextseek/chat_memory.py:25` sets the FIFO limit applied
  at `NessieAI/chat_nextseek/src/chat_nextseek/chat_memory.py:246-247`, and
  `NessieAI/cc/turn.py:57` hardcodes the same number with a
  comment naming this module; changing one truncates the two writers differently.
- **Seqera Tower is retired, not deleted.** The schema builder never offers it
  (`NessieAI/chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py:218-221`) and a test
  pins that (`NessieAI/tests/chat_nextseek/test_pipeline_tool_exposure.py:16-18`), but the
  dispatcher still routes the name
  (`NessieAI/chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py:639-640`). Deleting
  the dormant path as dead code destroys the re-enable and breaks nothing
  visible until someone tries to use it.
- **`handoff` is exposed unconditionally**, appended after every gated tool at
  `NessieAI/chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py:223-224`. Gate it and
  an open pipeline build traps the conversation, because it is the only way out
  that discards build state.
- **The Luria submit tool is offered only when all three env vars are set.**
  `NessieAI/chat_nextseek/src/chat_nextseek/config.py:58-60` requires user, key and
  working path together, and `NessieAI/chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py:220-221`
  keys tool exposure off that; a partially configured box silently hands the
  model a build it cannot submit.
- **This directory owns the context files the CC agent image bakes.** `capabilities.md`,
  `projects_db.json` and the four `min_*.json` catalogs named by `CANONICAL_CONTEXT_FILES`
  in `NessieAI/build_tools/gen_op_surfaces/constants.py` exist only under
  `NessieAI/chat_nextseek/src/chat_nextseek/context/`. The cc-agent Dockerfile COPYs each
  from the Compose named context `chat_nextseek` to `/app/plugins/nextseek/context/`, and
  the plugin tree keeps no copy. An edit here therefore reaches the CC agent only after a
  cc-agent rebuild, and a new file here reaches it only once it is added to that list;
  `NessieAI/tests/cc/test_cc_context_drift_guard.py` fails until the file is baked or
  declared source-only.

## Landmines

- **A cc-agent build bakes this tree as it is on disk, committed or not.** The config
  refreshes `min_sampletypes_db.json`, `min_assays_db.json` and `projects_db.json` in its
  context directory from the database once a day (`_ensure_context_files` in
  `NessieAI/chat_nextseek/src/chat_nextseek/config.py`). Run it against a checkout and
  those tracked files change in place; the next cc-agent build then ships the refreshed
  bytes, exactly as the app image build does. Check `git status` on this directory before
  a cc-agent rebuild.
- **One graph file is not baked from here.** The plugin tree keeps its own
  `min_graph_schema.json`, which differs from the one here and does reach the agent
  (`NessieAI/docker/CLAUDE.md`). `neo4j_schema.json` is no longer baked into the cc-agent
  image at all: that agent calls the `nextseek-graph-schema` op, which serves
  `graph_schema_snapshot` from this package. The copy here stays as the NS engine's
  fallback.
- **The graph schema is no longer written into `context/`.** `neo4j_schema.json`,
  `neo4j_protocol_schema.json` and `neo4j_assay-sample-conn.json` are committed files the
  config only reads (`ChatConfig.NEO4J_SCHEMA`, `PROTOCOL_SCHEMA` and
  `ASSAY_SAMPLE_CONNECTIONS`); nothing refreshes them from Neo4j any more, so a hand edit
  is the only way they change. The graph agent reads the live v1.1 catalog through
  `graph_catalog.get_snapshot`, cached per process on `GraphMeta.catalog_hash`, and falls
  back to those committed files on any catalog failure (`live_catalog_context` in
  `NessieAI/chat_nextseek/src/chat_nextseek/agents/graph.py`). `graph_schema_snapshot`, beside
  it, is the same read as a plain dict for the `graph-schema` op, and names which of the two it
  answered from. A graph turn records which
  one it read in `debug.graph_context` (`catalog` or `fallback`). The parser and the older
  property guard read the committed files either way. `_ensure_context_files` still
  rewrites the database exports of the bullet above once a day: only the Neo4j-derived
  files stopped changing.
- **The evaluation switch is off unless the process sets `NEXTSEEK_EVAL_PARSER_FORCE=1`.**
  The chat request's `force_parser_mode` (`graph` or `api`) is honoured only for a
  superuser on a process with that flag (`_with_parser_force` in `NessieAI/cc/turn.py`)
  and dropped without a word otherwise. No compose file or env template sets it; only
  the evaluation venue does (`scripts/graph_search/nessie_venue.sh`). When it lands,
  `_force_parser_mode` in `agents/parser.py` overrides the parser's choice last and says
  so in `parser_plan.notes`. Set the flag on a served instance and any superuser's
  request can overrule the parser. The same flag and gate govern `prompt_variant`
  (`v2` or `v2_apoc`, `_with_prompt_variant`): the turn runs on the prompt and context
  files in `prompts/variants/<name>/`, looked up there, then in the variant it
  `inherits`, then in the defaults (`prompt_variants.py` names every file and the
  `variant.json` keys). It needs no parser force, and the turn's debug payload records
  `prompt_variant` and `prompt_variant_files` beside `parser_plan.mode`. A variant
  directory with an unexpected file fails `test_prompt_variants.py`.
- **This directory's own `.gitignore` still governs it inside the monorepo.**
  `NessieAI/chat_nextseek/.gitignore:25` ignores any `docs/` directory and
  `NessieAI/chat_nextseek/.gitignore:33` ignores `.claude`, so a design note or a skill
  written there is invisible to `git add`. Put Nessie docs in `NessieAI/docs/`.
- **pytest's configuration comes from the repo root.** `NessieAI/chat_nextseek/pyproject.toml`
  carries no `[tool.pytest.ini_options]` table (its only `pytest` lines are the two
  dev-group dependencies at `NessieAI/chat_nextseek/pyproject.toml:50-51`), so collection
  uses the root project's block and loads the real Django settings module unless the
  lane passes the test settings, and that import calls `os.makedirs` at
  `dmac/settings.py:507-508`. Over a read-only mount without the two pre-created
  directories the entire suite fails to collect (`NessieAI/tests/README.md` "Django lane").
- **Two `NessieAI/tests/chat_nextseek/evaluator/` modules abort collection, so a plain run
  of that directory executes zero tests.** `NessieAI/tests/chat_nextseek/evaluator/test_demo_server.py:6`
  needs `pytest_asyncio`, which the stack image lacks, and
  `NessieAI/tests/chat_nextseek/evaluator/test_normalization_additional.py:5` imports a
  private orchestrator symbol that no longer exists: a grep for
  `_persist_bundle_reply` over `NessieAI/chat_nextseek/src/` returns nothing. To reach the
  rest of that directory, pass
  `--ignore=NessieAI/tests/chat_nextseek/evaluator/test_demo_server.py` and
  `--ignore=NessieAI/tests/chat_nextseek/evaluator/test_normalization_additional.py`.
- **This package's BAML client is never generated.** It is gitignored at
  `NessieAI/chat_nextseek/.gitignore:47`, and the one generation step this repo runs
  (the "Generate the BAML client" step of `.github/workflows/ci-pytest.yml`, and the root
  Dockerfile) targets only `NessieAI/dmac_assistant/baml_src`, so every module importing
  it fails on `ModuleNotFoundError`. Do not read that as a move regression.
- **An unregistered agent key degrades silently rather than raising.**
  `NessieAI/chat_nextseek/src/chat_nextseek/config.py:1338-1341` falls back through the
  `default` profile and then to the globally configured model at
  `NessieAI/chat_nextseek/src/chat_nextseek/config.py:1347`, so a new agent left out of a
  profile quietly runs on the wrong model. Only a duplicate assignment raises
  (`NessieAI/chat_nextseek/src/chat_nextseek/config.py:1324`).
- **A missing capabilities document also degrades silently.**
  `NessieAI/chat_nextseek/src/chat_nextseek/config.py:442-448` prints a note and returns an
  empty string, and `NessieAI/chat_nextseek/src/chat_nextseek/agents/system.py:56`
  substitutes placeholder prose, so the system agent answers catalog questions
  from nothing instead of failing loudly.
- **An in-source comment contradicts the code about the launch default.**
  `NessieAI/chat_nextseek/src/chat_nextseek/config.py:330-333` claims the mode defaults to
  Tower; the function it describes defaults to Luria
  (`NessieAI/chat_nextseek/src/chat_nextseek/config.py:37-44`). Believing the comment
  mispredicts which submit tool the model is handed.
- **Two tests read a source file by path and both are stale against it.** One
  reaches out of the boundary:
  `NessieAI/tests/chat_nextseek/test_generate_submission_hydration.py:102-103` stubs the
  portable module with one attribute while `NessieAI/ns/granular.py:148`, which the test
  loads by path through `NessieAI/paths.py`, imports two, so an edit in `NessieAI/ns/`
  breaks a test here. The other reads a monolith that was refactored away:
  `NessieAI/tests/chat_nextseek/evaluator/test_frozen_planner_evaluator.py:10`.
- **A retired-backend variant is still in the E2E catalog and silently vanishes
  from runs.** `NessieAI/tests/e2e/catalog.json:7806-7809` gates a Tower
  submission case on two environment variables belonging to the retired
  backend, and the sampler drops
  unsatisfied variants before sampling rather than recording them
  (`NessieAI/tests/e2e/sampler.py:23-25`), so a fully green report is not
  evidence that the catalog was covered.
- **The semantic catalog matcher downloads a model on first use.**
  `DEPLOYMENT.md` §2.3 records that nothing pre-fetches the embedding models and an
  air-gapped box fails on whichever embedding path runs first; the feature ships off by
  default (`NessieAI/chat_nextseek/.env.example:16`).
- **The Bedrock token has to be in a second file for this package's direct
  path.** `DEPLOYMENT.md` §8 lists `AWS_BEARER_TOKEN_BEDROCK` in both
  `docker/nextseek.env` and the proxy's own secret file; filling only one of the two
  leaves the other chat route dead with no automated cross-check.
- **The two largest Python modules here are `config.py` and `orchestrator.py`**, both
  under `NessieAI/chat_nextseek/src/chat_nextseek/`. Grep for the concern and read that
  region; reading either end to end burns the context the change itself needs.
- **A deployed instance is not evidence about this branch.** Code here is baked
  into the image rather than mounted (`DEPLOYMENT.md` §0) and each deploy
  keeps a tag naming the sha it was built from (`DEPLOYMENT.md` §5.2), so a
  running box can be serving an older revision of this package than the one you
  are reading, and a failure there proves nothing about this tree. <!-- UNVERIFIED: which revision any particular deployment carries is recorded nowhere in this repo -->

## Test command

See `NessieAI/tests/README.md` (the Django lane with `NessieAI/tests/chat_nextseek`; mount
a writable copy of this directory, or use the `PYTHONPATH` form, to test uncommitted
source). The evaluator flags are in "Landmines" above.

## See also

- See `NessieAI/chat_nextseek/README.md` for the module map, the three surfaces and the
  dependency edges in both directions.
- See `NessieAI/ns/README.md` for the granular per-agent ops that call this package's
  exported functions.
- See `NessieAI/router/README.md` for the router that decides whether a turn reaches this
  engine at all.
- See `DEPLOYMENT.md` §3.2 for the rebuild a change here requires.
- See `NessieAI/tests/nessie_tests/README.md` for the harness that exercises this engine
  through the live HTTP endpoint rather than by import.
- See `NessieAI/chat_nextseek/src/chat_nextseek/evaluator/README.md` for the retry-context
  evaluator.

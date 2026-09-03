# Working in `chat_nextseek/`

## Invariants

Break one of these and something outside this directory stops working, usually
without an error at the point of the change.

- **Nothing you add here survives unless it also lands upstream.** The snapshot
  refresh excludes only caches and local state
  (`startup/scripts/sync_chat_nextseek.sh:37-44`), so every other file absent
  from the canonical checkout — this one included — is deleted the next time a
  maintainer bumps the vendored copy.
- **Editing here is not enough to change a running instance.** This tree is
  baked into the application image, listed among the paths that require a
  rebuild at `DEPLOYMENT.md:47-52`; the short list of runtime bind mounts named
  in that same entry contains nothing under this directory.
- **The exported symbol list is a downstream contract.** The pin at
  `chat_nextseek/tests/test_portable_contract.py:34-40` fails the moment the
  list drifts, and the file opens by saying a failure there is a breaking change
  for the plugin consumer (`chat_nextseek/tests/test_portable_contract.py:1-5`).
- **The two package `__init__` re-export shims are permanent public API.** The
  helpers shim says so at `chat_nextseek/src/chat_nextseek/helpers/__init__.py:3-6`
  and the agents shim at `chat_nextseek/src/chat_nextseek/agents/__init__.py:3-6`; dropping a
  re-export breaks importers that go through the package rather than the module,
  such as `nextseek_api/assistant/granular.py:88`.
- **Half an identity is treated as none.** `chat_nextseek/src/chat_nextseek/orchestrator.py:155-164`
  refuses a credential pair with one side missing, because applying only the
  supplied half leaves the other on the service account and issues the request
  under a mixed identity.
- **A construction-time raise inside the config object stops Django booting.**
  The settings overlay builds one at module scope
  (`startup/dev/lane_local_settings.py:19`), so the missing-provider-key raise at
  `chat_nextseek/src/chat_nextseek/config.py:490-493` takes the whole site down,
  not just the chat panel.
- **The chat-log cap is duplicated across the boundary and must stay in step.**
  `chat_nextseek/src/chat_nextseek/chat_memory.py:25` sets the FIFO limit applied
  at `chat_nextseek/src/chat_nextseek/chat_memory.py:246-247`, and
  `nextseek_api/services/cc_assistant.py:81` hardcodes the same number with a
  comment naming this module; changing one truncates the two writers differently.
- **Seqera Tower is retired, not deleted.** The schema builder never offers it
  (`chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py:218-221`) and a test
  pins that (`chat_nextseek/tests/test_pipeline_tool_exposure.py:16-18`), but the
  dispatcher still routes the name
  (`chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py:639-640`). Deleting
  the dormant path as dead code destroys the re-enable and breaks nothing
  visible until someone tries to use it.
- **`handoff` is exposed unconditionally**, appended after every gated tool at
  `chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py:223-224`. Gate it and
  an open pipeline build traps the conversation, because it is the only way out
  that discards build state.
- **The Luria submit tool is offered only when all three env vars are set.**
  `chat_nextseek/src/chat_nextseek/config.py:57-59` requires user, key and
  working path together, and `chat_nextseek/src/chat_nextseek/pipeline/agent_tools.py:220-221`
  keys tool exposure off that; a partially configured box silently hands the
  model a build it cannot submit.
- **This directory owns the canonical capabilities document.** Surface
  generation refuses to run when the baked copy differs by a single byte
  (`build_tools/gen_op_surfaces/route_capabilities.py:231-232`), so editing it
  here without regenerating the other copy blocks that whole tool chain.

## Landmines

- **The two capabilities copies differ right now.** Measured 2026-09-03 with
  `md5sum` and `wc -c`: the canonical file named at
  `build_tools/gen_op_surfaces/constants.py:8-10` is 12128 bytes, and the baked
  copy named at `build_tools/gen_op_surfaces/constants.py:11-13` is 11747, with
  different digests. The byte-identity test is
  already carried as a known failure at `ci/pytest-baseline.txt:27`, so this is a
  standing condition, not something you just caused. The built image is
  unaffected — the canonical COPY at `docker/cc-runtime/Dockerfile:54` lands
  after the plugin tree and wins — but every caller of the surface generator
  raises until the two are reconciled.
- **There is no recorded upstream commit for this snapshot.** The sync script
  writes a marker file (`startup/scripts/sync_chat_nextseek.sh:47`), yet a `find`
  for a file named `.chat_nextseek_snapshot` anywhere under `chat_nextseek/`
  returns nothing. You cannot tell which canonical revision this copy is, so you
  cannot diff it before overwriting it.
- **This directory's own `.gitignore` still governs it inside the monorepo.**
  `chat_nextseek/.gitignore:25` ignores any `docs/` directory and
  `chat_nextseek/.gitignore:33` ignores `.claude`, so a design note or a skill
  written there is invisible to `git add` and disappears at the next refresh.
- **pytest's rootdir escapes this directory.** `chat_nextseek/pyproject.toml`
  carries no `[tool.pytest.ini_options]` table — a grep for `pytest` over that
  file matches only the two dev-group dependency lines at
  `chat_nextseek/pyproject.toml:50-51` — so collection falls through to the root
  project's block and loads the real Django settings module, whose import calls
  `os.makedirs` at `dmac/settings.py:497-499`. Over a read-only mount the entire
  suite fails to collect.
- **Two `tests/evaluator/` modules abort collection, so a plain run of that
  directory executes zero tests.** `chat_nextseek/tests/evaluator/test_demo_server.py:6`
  needs `pytest_asyncio`, absent from the stack image when measured 2026-09-03, and
  `chat_nextseek/tests/evaluator/test_normalization_additional.py:5` imports a
  private orchestrator symbol that no longer exists: a grep for
  `_persist_bundle_reply` over `chat_nextseek/src/` returns nothing.
- **This package's BAML client is never generated.** It is gitignored at
  `chat_nextseek/.gitignore:47`, and the one generation step this repo runs
  targets only the sibling router's schema
  (`.github/workflows/ci-pytest.yml:40-43`), so every module importing it fails
  on `ModuleNotFoundError`. Do not read that as a vendoring regression.
- **An unregistered agent key degrades silently rather than raising.**
  `chat_nextseek/src/chat_nextseek/config.py:1331-1334` falls back through the
  `default` profile and then to the globally configured model at
  `chat_nextseek/src/chat_nextseek/config.py:1340`, so a new agent left out of a
  profile quietly runs on the wrong model. Only a duplicate assignment raises
  (`chat_nextseek/src/chat_nextseek/config.py:1317`).
- **A missing capabilities document also degrades silently.**
  `chat_nextseek/src/chat_nextseek/config.py:435-441` prints a note and returns an
  empty string, and `chat_nextseek/src/chat_nextseek/agents/system.py:55`
  substitutes placeholder prose, so the system agent answers catalog questions
  from nothing instead of failing loudly.
- **An in-source comment contradicts the code about the launch default.**
  `chat_nextseek/src/chat_nextseek/config.py:329-332` claims the mode defaults to
  Tower; the function it describes defaults to Luria
  (`chat_nextseek/src/chat_nextseek/config.py:36-43`). Believing the comment
  mispredicts which submit tool the model is handed.
- **Two tests load a source file by path and both are stale against it.** One
  reaches out of the boundary:
  `chat_nextseek/tests/test_generate_submission_hydration.py:106-107` stubs the
  portable module with one attribute while `nextseek_api/assistant/granular.py:148`
  imports two, so an edit in the Django app breaks a test in here. The other
  stays inside it and reads a monolith that was refactored away —
  `chat_nextseek/tests/evaluator/test_frozen_planner_evaluator.py:10`.
- **A retired-backend variant is still in the E2E catalog and silently vanishes
  from runs.** `chat_nextseek/e2e/catalog.json:7806-7809` gates a Tower
  submission case on two environment variables belonging to the retired
  backend, and the sampler drops
  unsatisfied variants before sampling rather than recording them
  (`chat_nextseek/e2e/sampler.py:23-25`), so a fully green report is not
  evidence that the catalog was covered.
- **The semantic catalog matcher downloads a model on first use.**
  `DEPLOYMENT.md:185-187` records that nothing pre-fetches it and an air-gapped
  box fails on whichever embedding path runs first; the feature ships off by
  default (`chat_nextseek/.env.example:16`).
- **The Bedrock token has to be in a second file for this package's direct
  path.** `DEPLOYMENT.md:509-513` says filling only one of the two leaves the
  other chat route dead with no automated cross-check.
- **The two largest Python modules here run to
  `chat_nextseek/src/chat_nextseek/config.py:2028` and
  `chat_nextseek/src/chat_nextseek/orchestrator.py:1878`**, both last lines,
  ranked 2026-09-03 by `wc -l` over every `.py` file in this directory. Grep for
  the concern and read that region; reading either end to end burns the context
  the change itself needs.
- **A deployed instance is not evidence about this branch.** Code here is baked
  into the image rather than mounted (`DEPLOYMENT.md:47-48`) and each deploy
  pins a tag naming the sha it was built from (`DEPLOYMENT.md:354-356`), so a
  running box can be serving an older revision of this package than the one you
  are reading, and a failure there proves nothing about this tree. <!-- UNVERIFIED: which revision any particular deployment carries is recorded nowhere in this repo -->

## Test command

```
docker run --rm --network none -v <writable-scratch-copy>:/app/chat_nextseek:z \
  -w /app/chat_nextseek -e PYTHONDONTWRITEBYTECODE=1 nextseek-nextseek:latest \
  /app/.venv/bin/python -m pytest tests/ --ignore=tests/evaluator -q
```

Copy this directory somewhere writable first: the mount has to be writable and
the image's editable install (`pyproject.toml:136`) then resolves to your copy
instead of the baked one. Drop the `--ignore` and add
`--ignore=tests/evaluator/test_demo_server.py` plus
`--ignore=tests/evaluator/test_normalization_additional.py` to reach the
evaluator subdirectory; on 2026-09-03 that subdirectory reported 155 passed and
8 failed in 0.74s.

## See also

- See `chat_nextseek/README.md` for the module map, the three surfaces, the
  dependency edges in both directions, and the main lane's measured result.
- See `nextseek_api/assistant/README.md` for the granular per-agent ops that
  call this package's exported functions.
- See `nextseek_api/cc_assistant/README.md` for the router that decides whether
  a turn reaches this engine at all.
- See `DEPLOYMENT.md:284` for the rebuild command a change here requires.
- See `nessie_tests/README.md` for the harness that exercises this engine
  through the live HTTP endpoint rather than by import.
- See the repo-root `CLAUDE.md` for the snapshot-sync workflow row and the
  stack-wide conventions.

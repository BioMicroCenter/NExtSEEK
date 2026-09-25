# `NessieAI/dmac_assistant/`

## What this is

A vendored subset of the upstream `dmac-assistant` bridge, copied verbatim from
`https://github.com/tavjo/dmac-assistant` and installed into the app venv as an
editable in-tree path dependency (`pyproject.toml:139`). Upstream is a
standalone FastAPI WebSocket bridge that fronts a containerized Claude Code CLI
(`NessieAI/dmac_assistant/src/dmac_assistant/__init__.py:1-4`). NExtSEEK already owns that
transport, so the server layer was never copied over and the dependency list was
trimmed to match (`NessieAI/dmac_assistant/pyproject.toml:9-11`).

What NExtSEEK takes from the copy is the **per-turn route decision**: a
BAML-driven LLM router, the two JSON registries that feed it, and one filesystem
diff helper. It is also the one compiled BAML tree for the CC summarizer and the
HiBayes judges. Everything else arrived as a side effect of vendoring a package
rather than a module: two of its Python files,
`NessieAI/dmac_assistant/src/dmac_assistant/copier.py:41` and
`NessieAI/dmac_assistant/src/dmac_assistant/streamjson.py:29`, define entry points
nothing here reaches (no import of `dmac_assistant` anywhere in the tree names `copier`
or `streamjson`). See `NessieAI/dmac_assistant/CLAUDE.md` for the traps that leftover
code sets.

The package is not a Django app: it declares no models, no settings and no URLs,
and it does no work unless a caller in `NessieAI/router/`, `NessieAI/cc/` or
`NessieAI/hibayes/` reaches into it.

## Surface

This boundary has three surfaces of three different kinds, and the edge into each
is a different mechanism, so they are listed separately.

### 1. A Python package (edge: imports)

The public callables and one caller of each. See `NessieAI/dmac_assistant/CLAUDE.md` for
why every one of those call sites imports the way it does:

| Callable | Defined | Called from |
|---|---|---|
| `load_capabilities(path)` | `NessieAI/dmac_assistant/src/dmac_assistant/router/capabilities.py:41` | `NessieAI/router/router.py:223` |
| `load_model_class_map(path)` | `NessieAI/dmac_assistant/src/dmac_assistant/router/models.py:61` | `NessieAI/router/router.py:95` |
| `resolve_cc_model()` | `NessieAI/dmac_assistant/src/dmac_assistant/router/models.py:104` | `NessieAI/router/router.py:106` |
| `is_bedrock_model_id(value)` | `NessieAI/dmac_assistant/src/dmac_assistant/router/models.py:122` | `NessieAI/cc/cc_engine.py:509` |
| `resolve_cc_fallback_model()` | `NessieAI/dmac_assistant/src/dmac_assistant/router/models.py:131` | `NessieAI/cc/cc_engine.py:492` |
| `resolve_cc_classifier_model()` | `NessieAI/dmac_assistant/src/dmac_assistant/router/models.py:148` | `NessieAI/cc/cc_engine.py:509` |
| `RouterAgent` | `NessieAI/dmac_assistant/src/dmac_assistant/router/agent.py:100` | `NessieAI/router/router.py:224` |
| `diff_files(before, after)` | `NessieAI/dmac_assistant/src/dmac_assistant/run_tracker.py:51` | `NessieAI/cc/cc_engine.py:1852` |
| `ConfigError` | `NessieAI/dmac_assistant/src/dmac_assistant/config.py:37` | in-package only, at `NessieAI/dmac_assistant/src/dmac_assistant/router/capabilities.py:21` and `NessieAI/dmac_assistant/src/dmac_assistant/router/models.py:23` |

`RouterAgent.route()` wraps the BAML call and swallows every non-cancellation
exception into a fixed decision (`NessieAI/dmac_assistant/src/dmac_assistant/router/agent.py:118-130`)
whose `reasoning` is the sentinel `<router_unavailable>`
(`NessieAI/dmac_assistant/src/dmac_assistant/router/agent.py:22`) and whose route is
Container-CC (`NessieAI/dmac_assistant/src/dmac_assistant/router/agent.py:92-97`). NExtSEEK
constructs the agent but then calls the generated BAML function directly so it can
observe the transport, and it treats that sentinel as a routing failure rather
than as a decision (`NessieAI/router/router.py:224-231` and
`NessieAI/router/router.py:178-179`).

### 2. A BAML source tree (edge: code generation)

`NessieAI/dmac_assistant/baml_src/` is the contract; the Python that implements it is
generated, never written by hand. Six `function` blocks are declared. Each binds
its LLM client on the line directly below its own declaration, five of them the
reasoning client and one the cheap flash tier
(`NessieAI/dmac_assistant/baml_src/summarize.baml:56-57`); both clients are defined at
`NessieAI/dmac_assistant/baml_src/clients.baml:15-33`.

| BAML function | Declared | Reached from this repo |
|---|---|---|
| `RouteQuery` | `NessieAI/dmac_assistant/baml_src/router.baml:51` | `NessieAI/router/router.py:231` |
| `ClassifyQuery` | `NessieAI/dmac_assistant/baml_src/classifier.baml:15` | `NessieAI/router/router.py:205` |
| `Summarize` | `NessieAI/dmac_assistant/baml_src/summarize.baml:56` | `NessieAI/cc/cc_summary.py:275` |
| `EvaluateFunctionalUsefulness` | `NessieAI/dmac_assistant/baml_src/functional_evaluator.baml:143` | `NessieAI/hibayes/judge_human_compare.py:496` |
| `JudgeUITranscript` | `NessieAI/dmac_assistant/baml_src/judge_ui.baml:35` | only inside the agent image, `NessieAI/docker/cc-runtime/tools/e2e/judge_runner.py:120` |
| `JudgeRouterAnswer` | `NessieAI/dmac_assistant/baml_src/judge_router.baml:32` | nothing |

The last two rows are absences established the same way: grepping the whole tree
for `JudgeRouterAnswer` returns only its declaration above, and grepping for
`JudgeUITranscript` returns, besides its declaration, one caller which imports
`tools.e2e.baml_client`: the client the agent image generates from these files, not
the router client Django imports. The judge functions belong to HiBayes
(`NessieAI/hibayes/README.md` "HiBayes lives in these places").

`RouteQuery`'s prompt interpolates the registry rows one route at a time
(`NessieAI/dmac_assistant/baml_src/router.baml:57-59`), and the three destinations it may
return are the aliased members of `NessieAI/dmac_assistant/baml_src/router.baml:33-37`.

`NessieAI/dmac_assistant/baml_src/generators.baml` declares **two** codegen targets, not
one: `router_target` writes the async client into
`NessieAI/dmac_assistant/src/dmac_assistant/router/baml_client/`
(`NessieAI/dmac_assistant/baml_src/generators.baml:10-15`), and `e2e_target` writes a second,
sync client into `NessieAI/dmac_assistant/tools/e2e/baml_client/`
(`NessieAI/dmac_assistant/baml_src/generators.baml:17-22`). Both are produced by one
`baml-cli generate` run (the repo-root Dockerfile does it at lines 22-24, and CI does
the same in the "Generate the BAML client" step of `.github/workflows/ci-pytest.yml`),
and both are gitignored (`.gitignore:216` and `.gitignore:218`). Neither exists in a
fresh checkout.

The generated client exposes every declared function in both an async and a sync
form; `NessieAI/hibayes/judge_human_compare.py:481` is the one caller that imports
the sync one.

### 3. Two JSON registries read at runtime (edge: file paths)

- `NessieAI/dmac_assistant/build_context/route_capabilities.json` is the router's prompt
  data: two routes, whose `route_name` keys sit at
  `NessieAI/dmac_assistant/build_context/route_capabilities.json:4` and
  `NessieAI/dmac_assistant/build_context/route_capabilities.json:170`, each carrying its
  `tools` and `task_families` arrays. It is **generated output**, registered as a
  whole-file surface target at
  `NessieAI/build_tools/gen_op_surfaces/emit.py:209-211` under the path constant at
  `NessieAI/build_tools/gen_op_surfaces/constants.py:50`, and the generator round-trips its
  own bytes back through this package's real loader before returning them
  (`NessieAI/build_tools/gen_op_surfaces/route_capabilities.py:301-318`). Its standing
  ruling is `NessieAI/docs/dev-v5-merge-decisions.md`.
- `NessieAI/dmac_assistant/build_context/router_model_class_map.json` maps the three
  `ModelClass` members of `NessieAI/dmac_assistant/baml_src/router.baml:39-43` onto
  Bedrock-qualified model ids, plus one optional entry that is not a member,
  `opus_fallback`: the model a Container-CC turn falls back to (`--fallback-model`).
  The loader does not require it; `resolve_cc_fallback_model()` checks it, so a bad
  fallback id costs only the fallback. It is hand-maintained: it appears in no target
  tuple in `NessieAI/build_tools/gen_op_surfaces/emit.py:205-218`. Every value is validated
  against a `us.anthropic.` regex at
  `NessieAI/dmac_assistant/src/dmac_assistant/router/models.py:30` before use, and the
  design note at `NessieAI/dmac_assistant/src/dmac_assistant/router/models.py:7-9` makes
  this file the only place a model id may appear.

Both loaders take the same precedence: explicit argument, then environment
variable, then a package-relative default computed four parents up from the module
(`NessieAI/dmac_assistant/src/dmac_assistant/router/capabilities.py:25-38` and
`NessieAI/dmac_assistant/src/dmac_assistant/router/models.py:27-41`). No template,
compose file or `.env` sample in this repo assigns `DMAC_ROUTE_CAPABILITIES_FILE` or
`DMAC_ROUTER_MODEL_CLASS_MAP_FILE`, so on a clean install the package defaults run. A
box's rendered `docker/nextseek.env` is another matter: older deploy docs told operators
to set both, and the file is never re-rendered, so an existing box may still carry them.
Delete them there: a stale value beats the default and silently drops routing to the
heuristic and strips the model id from every CC turn. `./startup.sh rebuild` refuses
to run while either one names a pre-move `/app/` path (`startup/steps/validate.py`), and
`NessieAI/CLAUDE.md` "Box env" is the rule.

## Running and testing

This boundary has **no test lane of its own**: no `test_*.py` file and no `tests`
directory lives under `NessieAI/dmac_assistant`, and its `pyproject.toml` has no
`pytest` key. The router and CC suites exercise it from outside, in
`NessieAI/tests/router/` (`test_agent_history_conversion.py`,
`test_route_capabilities.py`, `test_posterior_selector.py`,
`test_router_v46_calltable.py`, `test_runtime_p0.py`,
`test_router_history_plumbing.py`) and in
`NessieAI/tests/cc/test_task12_remaining_holes.py`. Their commands are in
`NessieAI/tests/README.md`; the Django lane needs the generated client, which the app
image carries.

See `NessieAI/dmac_assistant/CLAUDE.md` for why a host lane stops at the generated client.

## Depends on / depended on by

Depends on, outside this directory:

- The generated BAML client, a build artifact rather than a repo file, regenerated from `baml_src/` by the command recorded at `.gitignore:215` and re-run by CI before every pytest job (the "Generate the BAML client" step of `.github/workflows/ci-pytest.yml`); a checkout that skips it cannot import the router at all.
- `baml-py`, pinned `~=0.222.0` at `NessieAI/dmac_assistant/pyproject.toml:19`; `uv.lock:222-223` resolves 0.222.0, which is also the version both generator blocks declare at `NessieAI/dmac_assistant/baml_src/generators.baml:13` and `NessieAI/dmac_assistant/baml_src/generators.baml:20`.
- `pydantic` and `python-dotenv` (`NessieAI/dmac_assistant/pyproject.toml:22-23`), used for the frozen models at `NessieAI/dmac_assistant/src/dmac_assistant/config.py:10` and the `.env` read at `NessieAI/dmac_assistant/src/dmac_assistant/config.py:195`.
- Nothing Django, nothing from `seek/`, nothing from `NessieAI/chat_nextseek/`: the declared dependency list is five entries long (`NessieAI/dmac_assistant/pyproject.toml:18-24`) and no Python file here imports `django`, `seek` or `chat_nextseek`, which is why the package loads in a bare interpreter.

Depended on by (non-test files; the test modules named under "Running and testing" are excluded):

- **Live routing.** `NessieAI/router/router.py:141-144` is the loader for the router half, and `NessieAI/router/router.py:92` and `NessieAI/router/router.py:104` resolve model ids.
- **Live session summary.** `NessieAI/cc/cc_summary.py:206` and `NessieAI/cc/cc_summary.py:274` pull the generated types and client.
- **Live classification.** `NessieAI/router/family_labels.py:102` takes the generated `TypeBuilder` so the family vocabulary can be injected at call time.
- **Live turn cleanup.** `NessieAI/cc/cc_engine.py:1852` imports the diff helper to decide which scratch files a turn produced.
- **Build-time generation.** `NessieAI/build_tools/gen_op_surfaces/route_capabilities.py:305` imports this package's loader to validate the bytes it is about to write.
- **Offline grading.** `NessieAI/hibayes/judge_human_compare.py:480-481` imports the generated sync client.

What the other matches are NOT:

- `NessieAI/tests/nessie_tests/FAMILIES.json:6114-6115` and `NessieAI/tests/nessie_tests/FAMILIES.json:3899` name files here as provenance strings in a corpus record, not as imports.
- `NessieAI/history/plan005/plan005_baseline.py:323-327` names boundary paths as container bind-mount sources for a mutation-testing subject tree, and `NessieAI/history/plan005/plan005_closeout_control.py:956` hashes `baml_src` into a manifest; neither imports the package, and both are frozen.
- `NessieAI/history/cc/archive/PLAN-2-multi-user-provisioning.md:969` shows an import of the copier inside a superseded plan document, which is prose, not code.
- The agent image is a second build of `baml_src/`, not an importer: `docker-compose.yml` hands the tree to its build as the named context `dmac_assistant_baml`, which is copied to `/app/baml_src/` and generated there (`NessieAI/docker/cc-runtime/Dockerfile:121-125`), so the agent never imports anything from this directory.

See `NessieAI/dmac_assistant/CLAUDE.md` for the invariants that hold these edges together.

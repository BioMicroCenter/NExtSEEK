# `dmac_assistant/`

## What this is

A vendored subset of the upstream `dmac-assistant` bridge, copied verbatim from
`https://github.com/tavjo/dmac-assistant` and installed into the app venv as an
editable in-tree path dependency (`pyproject.toml:137-139`). Upstream is a
standalone FastAPI WebSocket bridge that fronts a containerized Claude Code CLI
(`dmac_assistant/src/dmac_assistant/__init__.py:1-4`). NExtSEEK already owns that
transport, so the server layer was never copied over and the dependency list was
trimmed to match (`dmac_assistant/pyproject.toml:9-11`).

What NExtSEEK takes from the copy is the **per-turn route decision**: a
BAML-driven LLM router, the two JSON registries that feed it, and one filesystem
diff helper. Everything else arrived as a side effect of vendoring a package
rather than a module.

Counted on 2026-09-03 with `find dmac_assistant -type f`, the directory holds 9
Python files (835 lines), 8 `.baml` sources and 2 JSON registries. Two of those
nine Python files — `dmac_assistant/src/dmac_assistant/copier.py:41` and
`dmac_assistant/src/dmac_assistant/streamjson.py:29`, 183 lines between them —
define entry points nothing here reaches: grepping every `.py` file in the tree
for a line importing `dmac_assistant` yields 33 hits once the boundary's own path
prefix is dropped, and not one of them names `copier` or `streamjson`. See
`dmac_assistant/CLAUDE.md` for the traps that leftover code sets.

The package is not a Django app: it declares no models, no settings and no URLs,
and it does no work unless a caller in `nextseek_api/` reaches into it.

## Surface

This boundary has three surfaces of three different kinds, and the edge into each
is a different mechanism, so they are listed separately.

### 1. A Python package (edge: imports)

The public callables and one caller of each. See `dmac_assistant/CLAUDE.md` for
why every one of those call sites imports the way it does:

| Callable | Defined | Called from |
|---|---|---|
| `load_capabilities(path)` | `dmac_assistant/src/dmac_assistant/router/capabilities.py:41` | `nextseek_api/cc_assistant/router.py:218` |
| `load_model_class_map(path)` | `dmac_assistant/src/dmac_assistant/router/models.py:61` | `nextseek_api/cc_assistant/router.py:90` |
| `resolve_cc_model()` | `dmac_assistant/src/dmac_assistant/router/models.py:104` | `nextseek_api/cc_assistant/router.py:101` |
| `RouterAgent` | `dmac_assistant/src/dmac_assistant/router/agent.py:100` | `nextseek_api/cc_assistant/router.py:219` |
| `diff_files(before, after)` | `dmac_assistant/src/dmac_assistant/run_tracker.py:51` | `nextseek_api/cc_assistant/cc_engine.py:1852` |
| `ConfigError` | `dmac_assistant/src/dmac_assistant/config.py:37` | in-package only, at `dmac_assistant/src/dmac_assistant/router/capabilities.py:21` and `dmac_assistant/src/dmac_assistant/router/models.py:23` |

`RouterAgent.route()` wraps the BAML call and swallows every non-cancellation
exception into a fixed decision (`dmac_assistant/src/dmac_assistant/router/agent.py:118-130`)
whose `reasoning` is the sentinel `<router_unavailable>`
(`dmac_assistant/src/dmac_assistant/router/agent.py:22`) and whose route is
Container-CC (`dmac_assistant/src/dmac_assistant/router/agent.py:92-97`). NExtSEEK
constructs the agent but then calls the generated BAML function directly so it can
observe the transport, and it treats that sentinel as a routing failure rather
than as a decision (`nextseek_api/cc_assistant/router.py:219-226` and
`nextseek_api/cc_assistant/router.py:173-174`).

### 2. A BAML source tree (edge: code generation)

`dmac_assistant/baml_src/` is the contract; the Python that implements it is
generated, never written by hand. Six `function` blocks are declared. Each binds
its LLM client on the line directly below its own declaration, five of them the
reasoning client and one the cheap flash tier
(`dmac_assistant/baml_src/summarize.baml:56-57`); both clients are defined at
`dmac_assistant/baml_src/clients.baml:15-33`.

| BAML function | Declared | Reached from this repo |
|---|---|---|
| `RouteQuery` | `dmac_assistant/baml_src/router.baml:51` | `nextseek_api/cc_assistant/router.py:226` |
| `ClassifyQuery` | `dmac_assistant/baml_src/classifier.baml:15` | `nextseek_api/cc_assistant/router.py:200` |
| `Summarize` | `dmac_assistant/baml_src/summarize.baml:56` | `nextseek_api/cc_assistant/cc_summary.py:275` |
| `EvaluateFunctionalUsefulness` | `dmac_assistant/baml_src/functional_evaluator.baml:143` | `nextseek_api/eval/judge_human_compare.py:496` |
| `JudgeUITranscript` | `dmac_assistant/baml_src/judge_ui.baml:35` | only via the mirror tree, `docker/cc-runtime/tools/e2e/judge_runner.py:120` |
| `JudgeRouterAnswer` | `dmac_assistant/baml_src/judge_router.baml:32` | nothing |

The last two rows are absences established the same way: grepping the whole tree
for `JudgeRouterAnswer` returns only its declaration above and the identical mirror
copy under `docker/cc-runtime/baml_src/`, and grepping for `JudgeUITranscript`
returns, besides the two declarations, one caller which imports `tools.e2e.baml_client`
— the client built from the mirror, not from here.

`RouteQuery`'s prompt interpolates the registry rows one route at a time
(`dmac_assistant/baml_src/router.baml:57-59`), and the three destinations it may
return are the aliased members of `dmac_assistant/baml_src/router.baml:33-37`.

`dmac_assistant/baml_src/generators.baml` declares **two** codegen targets, not
one: `router_target` writes the async client into
`dmac_assistant/src/dmac_assistant/router/baml_client/`
(`dmac_assistant/baml_src/generators.baml:10-15`), and `e2e_target` writes a second,
sync client into `dmac_assistant/tools/e2e/`
(`dmac_assistant/baml_src/generators.baml:17-22`). Both are produced by one
`baml-cli generate` run — the repo-root Dockerfile does it at lines 22-24, CI does
the same at `.github/workflows/ci-pytest.yml:43` — and both are gitignored
(`.gitignore:233-234` and `.gitignore:236`). Neither exists in a fresh checkout.

The generated client exposes every declared function in both an async and a sync
form; `nextseek_api/eval/judge_human_compare.py:481` is the one caller that imports
the sync one.

### 3. Two JSON registries read at runtime (edge: file paths)

- `dmac_assistant/build_context/route_capabilities.json` is the router's prompt
  data. As of 2026-09-03 it declares exactly 2 routes —
  `dmac_assistant/build_context/route_capabilities.json:4` and
  `dmac_assistant/build_context/route_capabilities.json:170` are the only
  `route_name` keys in it — the first carrying 8 tools and 19 task families, the
  second 23 and 25, counted by loading the file and measuring each route's `tools`
  and `task_families` arrays. It is **generated
  output**, registered as a whole-file surface target at
  `build_tools/gen_op_surfaces/emit.py:226-228` under the path constant at
  `build_tools/gen_op_surfaces/constants.py:14`, and the generator round-trips its
  own bytes back through this package's real loader before returning them
  (`build_tools/gen_op_surfaces/route_capabilities.py:306-323`).
- `dmac_assistant/build_context/router_model_class_map.json` maps the three
  `ModelClass` members of `dmac_assistant/baml_src/router.baml:39-43` onto
  Bedrock-qualified model ids. It is hand-maintained: it appears in no target
  tuple in `build_tools/gen_op_surfaces/emit.py:217-235`. Every value is validated
  against a `us.anthropic.` regex at
  `dmac_assistant/src/dmac_assistant/router/models.py:30` before use, and the
  design note at `dmac_assistant/src/dmac_assistant/router/models.py:7-9` makes
  this file the only place a model id may appear.

Both loaders take the same precedence — explicit argument, then environment
variable, then a package-relative default computed four parents up from the module
(`dmac_assistant/src/dmac_assistant/router/capabilities.py:25-38` and
`dmac_assistant/src/dmac_assistant/router/models.py:27-41`). Neither
`DMAC_ROUTE_CAPABILITIES_FILE` nor `DMAC_ROUTER_MODEL_CLASS_MAP_FILE` is set
anywhere in this repo: grepping both names across the whole tree returns only
their own definitions and docstrings inside this boundary, two entries in a
preflight env collector (`nextseek_api/cc_assistant/tests/step7_preflight_collector.py:58-59`),
a superseded plan document and corpus prose — no template, no compose file, no
`.env` sample assigns either. The defaults are therefore what runs.

## Running and testing

This boundary has **no test lane of its own**. A `find dmac_assistant` for any
file named `test_*.py` or `*_test.py`, or any directory named `tests`, returns
nothing, and `dmac_assistant/pyproject.toml` contains no `pytest` key at all.
What exercises it is the Django app's suite, from outside.

The lane that actually covers this code is these two test modules, run inside the
live container so they have the generated client and a database grant:

```
docker exec -w /app -e DJANGO_SETTINGS_MODULE=dmac.test_settings nextseek \
  uv run --no-sync python -m pytest \
  nextseek_api/cc_assistant/tests/test_agent_history_conversion.py \
  nextseek_api/assistant/tests/test_route_capabilities.py --no-migrations -q
```

Run 2026-09-03: **6 failed, 16 passed, 1 warning in 0.18s**. All six failures are
one cause, raised at `build_tools/gen_op_surfaces/route_capabilities.py:232`: the
baked plugin copy of `capabilities.md` is not byte-identical to the canonical one.
That is a standing state of this branch, not a container artifact — `cmp` on the
two paths named at `build_tools/gen_op_surfaces/constants.py:8-13` reports them
differing at byte 1958 in the worktree and at the same byte inside the container.

See `dmac_assistant/CLAUDE.md` for the host lane and why it stops short.

Five further test modules import this package as well —
`nextseek_api/cc_assistant/tests/test_posterior_selector.py:122-123`,
`nextseek_api/cc_assistant/tests/test_router_v46_calltable.py:26`,
`nextseek_api/cc_assistant/tests/test_runtime_p0.py:37-39`,
`nextseek_api/cc_assistant/tests/test_task12_remaining_holes.py:978` and
`nextseek_api/cc_assistant/tests/test_router_history_plumbing.py:9`.

## Depends on / depended on by

Depends on, outside this directory:

- The generated BAML client, a build artifact rather than a repo file, regenerated from `baml_src/` by the command recorded at `.gitignore:233` and re-run by CI before every pytest job (`.github/workflows/ci-pytest.yml:43`); a checkout that skips it cannot import the router at all.
- `baml-py`, pinned `~=0.222.0` at `dmac_assistant/pyproject.toml:19`; `uv.lock:222-223` resolves 0.222.0, which is also the version both generator blocks declare at `dmac_assistant/baml_src/generators.baml:13` and `dmac_assistant/baml_src/generators.baml:20`.
- `pydantic` and `python-dotenv` (`dmac_assistant/pyproject.toml:22-23`), used for the frozen models at `dmac_assistant/src/dmac_assistant/config.py:10` and the `.env` read at `dmac_assistant/src/dmac_assistant/config.py:195`.
- Nothing Django, nothing from `seek/`, nothing from `chat_nextseek/`: the declared dependency list is five entries long (`dmac_assistant/pyproject.toml:18-24`) and grepping all nine Python files for an import statement naming `django`, `seek` or `chat_nextseek` returns no match, which is why the package loads in a bare interpreter.

Depended on by. Six non-test files carry all 16 non-test import lines, found by grepping every `.py` in the tree for a line importing `dmac_assistant` and removing the boundary's own path prefix; the 17 further import lines in the seven test modules under `nextseek_api/` are excluded from this list.

- **Live routing.** `nextseek_api/cc_assistant/router.py:136-139` is the loader for the router half, and `nextseek_api/cc_assistant/router.py:87` and `nextseek_api/cc_assistant/router.py:99` resolve model ids.
- **Live session summary.** `nextseek_api/cc_assistant/cc_summary.py:206` and `nextseek_api/cc_assistant/cc_summary.py:274` pull the generated types and client.
- **Live classification.** `nextseek_api/cc_assistant/family_labels.py:83` takes the generated `TypeBuilder` so the family vocabulary can be injected at call time.
- **Live turn cleanup.** `nextseek_api/cc_assistant/cc_engine.py:1852` imports the diff helper to decide which scratch files a turn produced.
- **Build-time generation.** `build_tools/gen_op_surfaces/route_capabilities.py:310` imports this package's loader to validate the bytes it is about to write.
- **Offline grading.** `nextseek_api/eval/judge_human_compare.py:480-481` imports the generated sync client.

What the other matches are NOT:

- `nessie_tests/FAMILIES.json:6114-6115` and `nessie_tests/FAMILIES.json:3899` name files here as provenance strings in a corpus record, not as imports.
- `build_tools/plan005_baseline.py:323-327` names boundary paths as container bind-mount sources for a mutation-testing subject tree, and `build_tools/plan005_closeout_control.py:956` hashes `baml_src` into a manifest; neither imports the package.
- `nextseek_api/cc_assistant/archive/PLAN-2-multi-user-provisioning.md:969` shows an import of the copier inside a superseded plan document, which is prose, not code.
- `docker/cc-runtime/baml_src/` is a byte-identical mirror rather than a consumer: it is copied into the agent image and generated there against its own path (`docker/cc-runtime/Dockerfile:113-117`), so the agent never imports anything from this directory.

See `dmac_assistant/CLAUDE.md` for the invariants that hold these edges together.

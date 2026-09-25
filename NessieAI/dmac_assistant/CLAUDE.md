# Working in `NessieAI/dmac_assistant/`

Vendored upstream code. Most edits here belong upstream instead; the parts this
repo depends on are narrow and the parts it does not are actively misleading.
Rules that span units (the box env, the one BAML tree both images build from) are in
`NessieAI/CLAUDE.md`.

## Invariants

- **Never hand-write anything under `router/baml_client/`.** That whole tree is
  regenerated on every image build by the repo-root Dockerfile at lines 22-24, and
  excluded from git at `.gitignore:216`, so an edit made there is destroyed by the next
  `./startup.sh rebuild` and cannot be reviewed in a diff. Change
  `NessieAI/dmac_assistant/baml_src/` instead.
- **`NessieAI/dmac_assistant/baml_src/` is the only BAML tree, and two images build
  from it.** The app image generates the router client from it, and the cc-agent image
  takes it through the Compose named context `dmac_assistant_baml` and generates the
  judge client. Rebuild both after an edit here, or the agent image keeps the old
  contract. Never add a BAML copy under `NessieAI/docker/cc-runtime/`
  (guard: `NessieAI/tests/router/test_baml_single_source.py`).
- **`build_context/route_capabilities.json` is generated, not authored.** Every
  surface target is re-rendered into a temp directory and byte-compared against the
  committed file, and a mismatch aborts with "stale bytes"
  (`NessieAI/build_tools/gen_op_surfaces/emit.py:274-278`), so a hand edit here fails the
  generated-surface check rather than taking effect. See `NessieAI/dmac_assistant/README.md`
  for which generator owns it and which registry is hand-maintained.
- **The `<router_unavailable>` sentinel must keep being read as a failure.** This
  package's own error path returns a *valid-looking* decision routed to
  Container-CC (`NessieAI/dmac_assistant/src/dmac_assistant/router/agent.py:92-97`); the
  caller only avoids sending every turn to the expensive engine because it
  compares the reasoning string first
  (`NessieAI/router/router.py:178-179`). Change that string on either
  side and a model outage silently becomes a full-rate CC bill.
- **Model ids belong in `build_context/router_model_class_map.json` and nowhere
  else.** Each of the three enum members must have a value matching the
  `us.anthropic.` pattern or the loader raises before returning
  (`NessieAI/dmac_assistant/src/dmac_assistant/router/models.py:44-57`), and the bedrock
  proxy's default allowlist holds exactly the ids a Container-CC turn names: the `opus`
  entry first, then `opus_fallback` (its `--fallback-model`) and `sonnet` (its auto-mode
  classifier) (`NessieAI/docker/bedrock-proxy/app/config.py:17-24`, guard
  `NessieAI/tests/cc/test_cc_proxy_allow_list.py`), so a model literal written anywhere
  else in the tree surfaces as a proxy rejection mid-turn rather than as a config
  error at load.
- **No non-test module outside this directory may import it at module scope.** Every
  non-test import of `dmac_assistant` outside this directory sits inside a function
  body; the loader at `NessieAI/router/router.py:141-144` is the pattern. Hoisting one to
  module scope makes a missing generated client or a broken `uv sync` a Django
  boot failure instead of a degraded route.
- **`docker` reaches the app venv through this directory alone.** The root
  `pyproject.toml` does not declare it; in `uv.lock` the only package that requests
  `docker` is the `dmac-assistant` block (`uv.lock:868-873`), which declares it at
  `NessieAI/dmac_assistant/pyproject.toml:20`. Dropping that line as
  unused-by-vendored-code breaks the module-scope `import docker` at
  `NessieAI/cc/cc_engine.py:37`, which takes the whole Container-CC engine down at import.

## Landmines

- **A box can override both registries, and a stale override is silent.** The loaders
  read `DMAC_ROUTE_CAPABILITIES_FILE` and `DMAC_ROUTER_MODEL_CLASS_MAP_FILE` before their
  package defaults. Nothing in the repo sets them, but older deploy docs told operators
  to put both in `docker/nextseek.env`, which `rebuild` never re-renders, so an existing
  box may still carry them. A stale value drops every turn to the heuristic and strips
  the model id from every CC turn (which the proxy then refuses). Delete both lines on
  every box (`NessieAI/CLAUDE.md` "Box env"); `./startup.sh rebuild` refuses to run while
  either names a pre-move path (`startup/steps/validate.py`).
- **The two model-id resolvers do not read the file the same way.**
  `NessieAI/router/router.py:92-96` hands the loader an explicit path from the build
  context directory, while `NessieAI/router/router.py:104-106` calls the no-argument
  helper that falls through to the env variable and then the package default. They agree
  only while nothing sets `DMAC_ROUTER_MODEL_CLASS_MAP_FILE`; set it and one route's
  model id moves while the other does not.
- **`config.py` is mostly unreachable here.** The only thing this repo takes from it is
  the exception class at `NessieAI/dmac_assistant/src/dmac_assistant/config.py:37`,
  imported by two sibling modules; no import of `dmac_assistant` elsewhere names
  `config`. `load_config()` cannot succeed here either: it raises immediately without
  `DMAC_USERS` (`NessieAI/dmac_assistant/src/dmac_assistant/config.py:196-198`), and
  grepping the whole tree for `DMAC_USERS`, `DMAC_CLAUDE_USERS_ROOT`,
  `DMAC_SCRATCH_ROOT`, `DMAC_DROPBOX_ROOT`, `DMAC_OUTPUT_ROOT`,
  `DMAC_CATALOG_FILE_HOST_PATH`, `DMAC_SIDECAR_STAGING_ROOT`, `DMAC_BRIDGE_PORT`
  and `DMAC_DEV_MODE` finds no occurrence outside this directory. Treat any
  reasoning that starts from `BridgeConfig` as reasoning about upstream.
- **The dev-mode catalog default points at a path that was not vendored.**
  `NessieAI/dmac_assistant/src/dmac_assistant/config.py:32-34` resolves a
  `vendor/chat_nextseek/agent_model_catalog.json` under the package, and no `vendor`
  directory exists anywhere under `NessieAI/dmac_assistant/`, so the dev-mode branch
  fails validation rather than falling back.
- **Every "see the design doc" pointer in this package is dangling.** Five documents
  cited at `NessieAI/dmac_assistant/src/dmac_assistant/__init__.py:3-4`,
  `NessieAI/dmac_assistant/src/dmac_assistant/router/agent.py:136-138`,
  `NessieAI/dmac_assistant/src/dmac_assistant/router/__init__.py:3` and
  `NessieAI/dmac_assistant/baml_src/judge_router.baml:3` do not exist in this repo:
  `dmac-assistant-sds.md`, `dmac-assistant-adrs.md`, the bridge README, the
  2026-05-13 LLM router design spec and `run_router_e2e.py`. Chasing them costs a round
  trip; the only surviving copies are in the upstream checkout a provenance record names
  at `NessieAI/tests/cc/step7_catalog/catalog_provenance.json:10`.
- **This directory's own `pyproject.toml` header is wrong about what is used.**
  `NessieAI/dmac_assistant/pyproject.toml:8-9` names the stream-json parser as one of the
  two things the integration imports at runtime. Grepping the whole tree for
  `streamjson` outside this directory finds no import: an attribution comment in the
  adapter that actually does the job (`NessieAI/cc/translate.py:15-17`), a review that
  repeats the false claim (`NessieAI/history/docs/testing-review/02-cc-dmac_assistant-testing-review.md:17`),
  and two earlier passes that already caught it and left it unfixed
  (`NessieAI/history/docs/2026-08/2026-08-03-nessie-hardening-design.md:521` and
  `NessieAI/history/docs/2026-08/2026-08-03-nessie-hardening-plan-2-resilience-routing.md:267`). Believing
  that header leads to editing a module with no callers, which is how the claim
  spread in the first place.
- **The model-class map is cached for the life of the process and never
  invalidated.** The cache is filled once at
  `NessieAI/dmac_assistant/src/dmac_assistant/router/models.py:92-96` and the loader's own
  docstring says passing a path does not refresh it
  (`NessieAI/dmac_assistant/src/dmac_assistant/router/models.py:67-68`), so editing the JSON
  inside a running container changes nothing until the worker restarts.
- **`RouterAgent` is constructed and then thrown away.** The caller builds one at
  `NessieAI/router/router.py:224` and immediately calls the generated
  function itself so the transport hooks see it, explained inline at
  `NessieAI/router/router.py:225`. Its `route()` method
  (`NessieAI/dmac_assistant/src/dmac_assistant/router/agent.py:108`) runs in no production
  path, so a fix applied there changes nothing live and only its structured
  telemetry at `NessieAI/dmac_assistant/src/dmac_assistant/router/agent.py:139-147` is lost
  by the bypass.
- **A second, unused BAML client is built on every generate.** The
  `e2e_target` block at `NessieAI/dmac_assistant/baml_src/generators.baml:17-22` emits
  `NessieAI/dmac_assistant/tools/e2e/baml_client/`, which nothing imports: grepping the
  tree for `dmac_assistant.tools` and for the `tools/e2e` path under this directory finds
  a gitignore entry, mutation-testing bind mounts and a hash-manifest prefix, never an
  import. Deleting the block is not free: the same generator file is copied into the
  agent image, whose build pre-creates the router output directory for it
  (`NessieAI/docker/cc-runtime/Dockerfile:123-125`).
- **`JudgeRouterAnswer` has no caller anywhere.** Grepping the whole tree for the
  name returns only its own declaration at
  `NessieAI/dmac_assistant/baml_src/judge_router.baml:32` and the two comments above it
  naming an upstream caller that was not vendored. Editing that file changes an LLM
  contract nothing exercises, and no test will catch a mistake in it.
- **A host lane stops at the generated client.** With only
  `NessieAI/dmac_assistant/src` on `PYTHONPATH`, importing the router fails with
  `ModuleNotFoundError: No module named 'dmac_assistant.router.baml_client'`, raised out of
  `NessieAI/dmac_assistant/src/dmac_assistant/router/agent.py:8`. That is expected on a
  checkout, not a broken environment: the client is a build artifact, so run the router
  tests in the app image (`NessieAI/tests/README.md`). The two modules that import no
  third party at all (`NessieAI/dmac_assistant/src/dmac_assistant/run_tracker.py:12-16`
  and `NessieAI/dmac_assistant/src/dmac_assistant/copier.py:10-15`) do load that way.
- **Two instances can be running different builds of this tree.** Deploys are manual
  (`DEPLOYMENT.md` intro), and the client here is baked at build time rather than read
  from disk, so a route decision observed on some other instance is evidence about that
  instance's image and nothing else. Reasoning from it lands you fixing code the box you
  are watching never ran.

## Test command

See `NessieAI/tests/README.md` (the Django lane with `NessieAI/tests/router`). This package
has no suite of its own.

## See also

- See `NessieAI/dmac_assistant/README.md` for the three surfaces, the six BAML functions
  and both directions of the dependency edge.
- See `NessieAI/router/CLAUDE.md` for the consumer's own invariants,
  including why its BAML imports are lazy.
- See `NessieAI/router/README.md` for how the route decision is used once this package
  returns it, including the overrides and the sticky-CC rule that sit above it.
- See the repo-root Dockerfile, lines 20-24, for the build-time generation step in
  its surrounding build order.

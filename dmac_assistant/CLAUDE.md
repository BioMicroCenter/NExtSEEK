# Working in `dmac_assistant/`

Vendored upstream code. Most edits here belong upstream instead; the parts this
repo depends on are narrow and the parts it does not are actively misleading.

## Invariants

- **Never hand-write anything under `router/baml_client/`.** That whole tree is
  regenerated on every image build by the repo-root Dockerfile at lines 22-24, and
  excluded from git at `.gitignore:234`, so an edit made there is destroyed by the next
  `./startup.sh rebuild` and cannot be reviewed in a diff. Change
  `dmac_assistant/baml_src/` instead.
- **`dmac_assistant/baml_src/` and `docker/cc-runtime/baml_src/` must stay
  byte-identical.** A deploy verifier hashes both `router.baml` copies
  (`scripts/plan018_v4_6_verifier.py:70-72`) and byte-compares both
  `classifier.baml` copies (`scripts/plan018_v4_6_verifier.py:74-77`); editing one
  copy alone records a failed check and ships an agent image whose judge client
  was generated from a different contract than the router's.
- **`build_context/route_capabilities.json` is generated, not authored.** Every
  surface target is re-rendered into a temp directory and byte-compared against the
  committed file, and a mismatch aborts with "stale bytes"
  (`build_tools/gen_op_surfaces/emit.py:291-295`), so a hand edit here fails the
  generated-surface check rather than taking effect. See `dmac_assistant/README.md`
  for which generator owns it and which registry is hand-maintained.
- **The `<router_unavailable>` sentinel must keep being read as a failure.** This
  package's own error path returns a *valid-looking* decision routed to
  Container-CC (`dmac_assistant/src/dmac_assistant/router/agent.py:92-97`); the
  caller only avoids sending every turn to the expensive engine because it
  compares the reasoning string first
  (`nextseek_api/cc_assistant/router.py:173-174`). Change that string on either
  side and a model outage silently becomes a full-rate CC bill.
- **Model ids belong in `build_context/router_model_class_map.json` and nowhere
  else.** Each of the three enum members must have a value matching the
  `us.anthropic.` pattern or the loader raises before returning
  (`dmac_assistant/src/dmac_assistant/router/models.py:44-57`), and the bedrock
  proxy's default allowlist is a one-element tuple holding the exact Opus id
  (`docker/bedrock-proxy/app/config.py:17-18`), so a model literal written anywhere
  else in the tree surfaces as a proxy rejection mid-turn rather than as a config
  error at load.
- **No non-test module outside this directory may import it at module scope.**
  Grepping every `.py` file in the tree for a line importing `dmac_assistant`,
  then dropping the boundary's own path prefix, yields 33 hits, of which the 16 in
  non-test files are all indented inside function bodies — the loader at
  `nextseek_api/cc_assistant/router.py:136-139` is the pattern. Hoisting one to
  module scope makes a missing generated client or a broken `uv sync` a Django
  boot failure instead of a degraded route.
- **`docker` reaches the app venv through this directory alone.** The root
  `pyproject.toml` declares 114 dependencies as of 2026-09-03, counted by parsing
  the array at `pyproject.toml:7-126`, and `docker` is not among them; scanning every
  `[[package]]` block's own dependency array in `uv.lock` for an entry naming
  `docker` returns exactly one requester, the `dmac-assistant` block at
  `uv.lock:868-873`, which declares it at `dmac_assistant/pyproject.toml:20`. Dropping
  that line as unused-by-vendored-code breaks the module-scope
  `import docker` at `nextseek_api/cc_assistant/cc_engine.py:37`, which takes the
  whole Container-CC engine down at import.
- **This directory's `CLAUDE.md` is tracked only because it is named
  explicitly.** `.gitignore:189` ignores `CLAUDE.md` everywhere and
  `.gitignore:216` carves out this one path; renaming or moving the file drops it
  out of the repo with no error.

## Landmines

- **`config.py` is 234 lines as of 2026-09-03, of which one symbol is
  reachable.** The only
  thing this repo takes from it is the exception class at
  `dmac_assistant/src/dmac_assistant/config.py:37`, imported by two sibling modules
  — none of the 33 tree-wide `dmac_assistant` import hits names `config` at all.
  `load_config()` cannot succeed here either: it raises immediately
  without `DMAC_USERS` (`dmac_assistant/src/dmac_assistant/config.py:196-198`), and
  grepping the whole tree for `DMAC_USERS`, `DMAC_CLAUDE_USERS_ROOT`,
  `DMAC_SCRATCH_ROOT`, `DMAC_DROPBOX_ROOT`, `DMAC_OUTPUT_ROOT`,
  `DMAC_CATALOG_FILE_HOST_PATH`, `DMAC_SIDECAR_STAGING_ROOT`, `DMAC_BRIDGE_PORT`
  and `DMAC_DEV_MODE` returns not one occurrence outside this directory. Treat any
  reasoning that starts from `BridgeConfig` as reasoning about upstream.
- **The dev-mode catalog default points at a path that was not vendored.**
  `dmac_assistant/src/dmac_assistant/config.py:32-34` resolves
  `dmac_assistant/vendor/chat_nextseek/agent_model_catalog.json`, and a `find` for
  a `vendor` directory anywhere under `dmac_assistant/` returns nothing, so the
  dev-mode branch fails validation rather than falling back.
- **Every "see the design doc" pointer in this package is dangling.** A `find`
  across the repo for `dmac-assistant-sds.md`, `dmac-assistant-adrs.md`,
  `docs/bridge/README.md`, `docs/superpowers/specs/2026-05-13-llm-router-design.md`
  and `tools/e2e/run_router_e2e.py` — cited at
  `dmac_assistant/src/dmac_assistant/__init__.py:3-4`,
  `dmac_assistant/src/dmac_assistant/router/agent.py:136-138`,
  `dmac_assistant/src/dmac_assistant/router/__init__.py:3` and
  `dmac_assistant/baml_src/judge_router.baml:3` — finds none of the five. Chasing
  them costs a round trip; the only surviving copies are in the upstream checkout
  a provenance record names at
  `nextseek_api/cc_assistant/acceptance_evidence/step7/catalog_provenance.json:10`.
- **This directory's own `pyproject.toml` header is wrong about what is used.**
  `dmac_assistant/pyproject.toml:8-9` names the stream-json parser as one of the
  two things the integration imports at runtime. Grepping the whole tree for
  `streamjson` and dropping this directory's own path prefix returns four matches
  and no import among them: an attribution comment in the adapter that actually
  does the job (`nextseek_api/cc_assistant/translate.py:15-17`), a review that
  repeats the false claim (`docs/testing-review/02-cc-dmac_assistant-testing-review.md:17`),
  and two earlier passes that already caught it and left it unfixed
  (`docs/archive/2026-08/2026-08-03-nessie-hardening-design.md:521` and
  `docs/archive/2026-08/2026-08-03-nessie-hardening-plan-2-resilience-routing.md:267`). Believing
  that header leads to editing a module with no callers, which is how the claim
  spread in the first place.
- **The model-class map is cached for the life of the process and never
  invalidated.** The cache is filled once at
  `dmac_assistant/src/dmac_assistant/router/models.py:92-96` and the loader's own
  docstring says passing a path does not refresh it
  (`dmac_assistant/src/dmac_assistant/router/models.py:67-68`), so editing the JSON
  inside a running container changes nothing until the worker restarts.
- **The two model-id resolvers do not read the file the same way.**
  `nextseek_api/cc_assistant/router.py:87-91` hands the loader an explicit path
  built from `BASE_DIR`, while `nextseek_api/cc_assistant/router.py:99-101` calls
  the no-argument helper that falls through to the env variable and then the
  package default. They agree today only because nothing sets
  `DMAC_ROUTER_MODEL_CLASS_MAP_FILE`; set it and one route's model id moves while
  the other does not.
- **`RouterAgent` is constructed and then thrown away.** The caller builds one at
  `nextseek_api/cc_assistant/router.py:219` and immediately calls the generated
  function itself so the transport hooks see it, explained inline at
  `nextseek_api/cc_assistant/router.py:220`. Its `route()` method
  (`dmac_assistant/src/dmac_assistant/router/agent.py:108`) runs in no production
  path, so a fix applied there changes nothing live and only its structured
  telemetry at `dmac_assistant/src/dmac_assistant/router/agent.py:139-147` is lost
  by the bypass.
- **A second, unused BAML client is built into the image on every rebuild.** The
  `e2e_target` block at `dmac_assistant/baml_src/generators.baml:17-22` emits
  `dmac_assistant/tools/e2e/baml_client/`, which nothing imports: grepping the tree
  for `dmac_assistant/tools` and for `dmac_assistant.tools` returns only a gitignore
  entry, mutation-testing bind mounts and a hash-manifest prefix, never an import.
  Deleting the block is not free: the same generator
  file is copied into the agent image, whose build pre-creates the router output
  directory for it (`docker/cc-runtime/Dockerfile:115-117`).
- **`JudgeRouterAnswer` has no caller anywhere.** Grepping the whole tree for the
  name returns only its own declaration at
  `dmac_assistant/baml_src/judge_router.baml:32`, the identical mirror declaration at
  `docker/cc-runtime/baml_src/judge_router.baml:32`, and the two comments above them
  naming an upstream caller that was not vendored. Editing that file changes an LLM
  contract nothing exercises, and no test will catch a mistake in it.
- **There is no deployment pipeline, so two instances can be running different
  builds of this tree.** The runbook says so itself and stands in for the pipeline
  that does not exist (`DEPLOYMENT.md:6-9`), and the client here is baked at build
  time rather than read from disk, so a route decision observed on some other
  instance is evidence about that instance's image and nothing else. Reasoning from
  it lands you fixing code the box you are watching never ran.

## Test command

There is no in-package suite; the host lane below is the cheapest thing that
touches this code directly, and it stops at the generated client.

```
PYTHONPATH=dmac_assistant/src uv run --no-project --with pydantic \
  --with python-dotenv --with pytest --with django --python 3.12 \
  python -m pytest nextseek_api/cc_assistant/tests/test_agent_history_conversion.py \
  --noconftest -q
```

Run 2026-09-03: **1 error, 0 tests collected, 0.07s** —
`ModuleNotFoundError: No module named 'dmac_assistant.router.baml_client'`, raised
out of `dmac_assistant/src/dmac_assistant/router/agent.py:8`. That is the expected
outcome on a checkout, not a broken environment: the client is a build artifact.
Without `--noconftest` the same command fails earlier and differently, in
`nextseek_api/conftest.py:3`, which needs a configured Django. The two modules
that import no third party at all —
`dmac_assistant/src/dmac_assistant/run_tracker.py:12-16` and
`dmac_assistant/src/dmac_assistant/copier.py:10-15` — do load under that
`PYTHONPATH` with no `--with` flags whatsoever.

## See also

- See `dmac_assistant/README.md` for the three surfaces, the six BAML functions
  and both directions of the dependency edge.
- See `nextseek_api/cc_assistant/CLAUDE.md` for the consumer's own invariants,
  including why its BAML imports are lazy.
- See `nextseek_api/cc_assistant/README.md` for how the route decision is used
  once this package returns it.
- See the repo-root `CLAUDE.md` for the router override precedence and the
  sticky-CC rule that sits above this package.
- See the repo-root Dockerfile, lines 20-24, for the build-time generation step in
  its surrounding build order.

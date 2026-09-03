# `docker/`

## What this is

Six independent Docker build contexts, the nginx configuration the stack is served
through, the runtime scripts baked into the application image, and the env template
that documents the app's own configuration. 138 files, counted with a `find` over this
directory on 2026-09-03, three of them this document, its CLAUDE.md and their shared
citation list.

It is not a Python package and nothing here is importable as one. Three of the six
contexts hold Python that runs only inside its own image, under a package name the
`COPY` line invents: `docker/bedrock-proxy/app/` becomes `app.proxy` inside the proxy
image (`docker/bedrock-proxy/Dockerfile:32`, explained at
`docker/bedrock-proxy/Dockerfile:10-13`), and `docker/ns-sidecar/app/` becomes
`sidecar.app` inside the sidecar image (`docker/ns-sidecar/Dockerfile:29-30`, with the
destination layout called load-bearing at `docker/ns-sidecar/Dockerfile:11-16`).

Three of the contexts are verbatim ports of an external `dmac-assistant` working
clone, each carrying a `PORT-EVIDENCE.json` that records the source path, the pinned
source commit, and what was and was not regenerated at port time
(`docker/cc-runtime/PORT-EVIDENCE.json:2-4`,
`docker/bedrock-proxy/PORT-EVIDENCE.json:2-4`,
`docker/ns-sidecar/PORT-EVIDENCE.json:2-4`). That provenance is machine-checked; see
`docker/CLAUDE.md` for what those checks refuse.

## Surface

The unit of surface here is a **build context**: one Dockerfile, everything its `COPY`
lines can reach beneath it, and the compose stanza or explicit `docker build -f`
invocation that names it. Two further surfaces are not images at all — configuration
mounted into a stock image, and scripts that reach the application image through the
repo-root build. Each row below is independent of every other row.

| Surface | Built or mounted by | Produces |
|---|---|---|
| `docker/cc-runtime/` | `docker-compose.yml:120-127` | `dmac-assistant:poc`, the Container-CC agent image |
| `docker/bedrock-proxy/` | `docker-compose.yml:88-91` | `nextseek-bedrock-proxy:latest` |
| `docker/ns-sidecar/` | `docker-compose.yml:164-167` | `nextseek-ns-sidecar:latest` |
| `docker/cc-runner/` | nothing | an unused lean proof image |
| `docker/eval/` | explicit `docker build -f`, repo root as context | the JAX/NumPyro eval image |
| `docker/eval-task6/` | explicit `docker build -f`, two image ARGs | a network-free Django+eval composite |
| `docker/nginx.conf`, `docker/nginx-optional/` | `docker-compose.yml:60` and `docker-compose.yml:64` | config for the stock `nginx:latest` |
| `docker/scripts/` | the repo-root build's whole-tree copy | `/app/docker/scripts/` in the app image |

### `docker/cc-runtime/` — the agent image

The largest context by far. It layers node 20 (`docker/cc-runtime/Dockerfile:18`), a
pinned Claude Code CLI (`docker/cc-runtime/Dockerfile:31`), a uv-managed CPython 3.14
with two PATH symlinks (`docker/cc-runtime/Dockerfile:37-44`) and an unprivileged
`user` account (`docker/cc-runtime/Dockerfile:46`).

Onto that it bakes the `nextseek` Claude Code plugin from
`docker/cc-runtime/build_context/plugins/nextseek/`: a manifest naming the plugin
(`docker/cc-runtime/build_context/plugins/nextseek/.claude-plugin/plugin.json:2`), two
skills, a slash command that routes to the first of them
(`docker/cc-runtime/build_context/plugins/nextseek/commands/nextseek.md:8`), a
`UserPromptSubmit` hook that resolves house vocabulary before the agent acts
(`docker/cc-runtime/build_context/plugins/nextseek/hooks/entity_preamble.sh:2-8`), a
permission allowlist installer
(`docker/cc-runtime/build_context/plugins/nextseek/scripts/setup.sh:2-7`), 20 executable
`nextseek-*` shims plus 11 private helper modules beside them (both counted by listing
that `bin/` directory on 2026-09-03), and a `context/` catalog directory whose own
manifest tells the agent to consult a file before constructing any op call
(`docker/cc-runtime/build_context/plugins/nextseek/context/MANIFEST.md:3-6`).

The image also carries the in-container agent instructions
(`docker/cc-runtime/Dockerfile:64`, symlinked into the WORKDIR at
`docker/cc-runtime/Dockerfile:80-81`), a generated table of contents over ten ingested
NExtSEEK documentation pages
(`docker/cc-runtime/docs/nextseek/README.md:3-6`) with the upstream content hash pinned
beside them (`docker/cc-runtime/docs/nextseek/.content-hash:1`), an NS-route runner and
its four sibling helper modules (`docker/cc-runtime/Dockerfile:67-75`), and a BAML judge
client generated at build time from a mirror of the router's own `baml_src`
(`docker/cc-runtime/Dockerfile:113-118`). That mirror declares two generator targets, a
router client and the e2e judge client
(`docker/cc-runtime/baml_src/generators.baml:10-22`), which is why the build creates the
router output directory it never uses.

`docker/cc-runtime/pyproject.toml:7-24` is the image's dependency manifest, installed
with `uv sync --locked` at `docker/cc-runtime/Dockerfile:97-99`; the document-extraction
dependency is an image-only extra explained at
`docker/cc-runtime/pyproject.toml:26-36`. The container entrypoint bridges env names,
scrubs a settings file, symlinks the plugin tree into the place headless Claude Code
actually discovers it, and can hold the container open for per-turn `docker exec`
(`docker/cc-runtime/container/entrypoint.sh:4-7` and
`docker/cc-runtime/container/entrypoint.sh:128-135`).

### `docker/bedrock-proxy/` — the model gateway

A single FastAPI relay that holds the institutional Bedrock bearer token in its own
environment and re-attaches it upstream, so the agent container carries no AWS
credential at all (`docker/bedrock-proxy/app/proxy.py:1-7`). The upstream host is fixed
from the region at config load, which is what removes the SSRF surface
(`docker/bedrock-proxy/app/config.py:52-54`). Hardening lives in one module docstring
(`docker/bedrock-proxy/app/proxy.py:9-29`): an exact-match allowlist evaluated on the
raw undecoded path, a body cap applied before and during the read, split timeouts
declared at `docker/bedrock-proxy/app/config.py:24-30`, and a logger that structurally
cannot receive the token. The relay is one catch-all route
(`docker/bedrock-proxy/app/proxy.py:220-221`); `/healthz` is the only exception
(`docker/bedrock-proxy/app/proxy.py:155-156`) and returns 200 before any token is
injected, which is why the image healthcheck works on a cold start
(`docker/bedrock-proxy/Dockerfile:41-44`).

### `docker/ns-sidecar/` — the per-request op broker

A WebSocket server that accepts a validated request, builds a per-user HTTP config from
credentials carried inside the frame, and dispatches to NExtSEEK's own granular
endpoints (`docker/ns-sidecar/app/server.py:1-3`). The wire contract is a shared module
(`docker/ns-sidecar/app/contract.py:3-6`) naming nine sidecar ops
(`docker/ns-sidecar/app/contract.py:14-17`). Its write gate is deliberately thin: the
endpoint allowlist was retired to NExtSEEK and only the write-confirmation flag is
checked locally (`docker/ns-sidecar/app/write_gate.py:1-3`). Artifacts are staged under
a SHA-256 of the calling user rather than the raw username
(`docker/ns-sidecar/app/staging.py:24-25`), into a per-request directory published by a
sibling completion marker (`docker/ns-sidecar/app/staging.py:31-33`). Health is a single
authenticated GET whose 401 is the healthy answer
(`docker/ns-sidecar/app/healthcheck.py:3-6`). The request/response models are a vendored
copy of NExtSEEK's, kept local so the image imports no NExtSEEK package
(`docker/ns-sidecar/app/granular_models.py:5-6`).

### The two eval contexts

`docker/eval/Dockerfile:2` starts from Python 3.12 rather than the app's interpreter,
copies one package in (`docker/eval/Dockerfile:10`) and installs the pinned JAX/NumPyro
stack (`docker/eval/Dockerfile:12-14`). `docker/eval-task6/Dockerfile:12-14` then grafts
three pure-Python packages out of a built application image into that eval image, taking
the exact deployed bytes rather than resolving them again
(`docker/eval-task6/Dockerfile:3-6`). Neither is wired into compose: grepping both
`docker-compose.yml` and `docker-compose.task8.yml` for `docker/eval` returns no match.

### nginx and the app-image scripts

`docker/nginx.conf` terminates on port 80 inside the container
(`docker/nginx.conf:37`), serves collected static from a volume
(`docker/nginx.conf:41-44`), and proxies everything else to the Django upstream through
a variable so Docker's embedded resolver re-resolves it
(`docker/nginx.conf:24` and `docker/nginx.conf:60-62`). WebSocket upgrade plumbing is
mapped at `docker/nginx.conf:30-33` and applied at `docker/nginx.conf:64-66`; the
upstream `Host` is forced to a Django-safe value at `docker/nginx.conf:73-74`. A
wildcard include (`docker/nginx.conf:54`) picks up operator drop-ins, of which one
example ships: a Neo4j Browser and HTTP Query API reverse proxy
(`docker/nginx-optional/neo4j.conf.example:53` and
`docker/nginx-optional/neo4j.conf.example:69`) that is inert until copied, because the
include pattern does not match its suffix
(`docker/nginx-optional/neo4j.conf.example:7-8`) and enabled copies are untracked by
design (`docker/nginx-optional/.gitignore:1-6`).

`docker/scripts/entrypoint.sh` is the application container's start command. It creates
the media subdirectories Django writes into (`docker/scripts/entrypoint.sh:3-7`), runs
`collectstatic` and `migrate` as fail-fast gates
(`docker/scripts/entrypoint.sh:13-18` and `docker/scripts/entrypoint.sh:47-53`) either
side of a bounded database-readiness probe
(`docker/scripts/entrypoint.sh:20-31`), then starts the selected web server
(`docker/scripts/entrypoint.sh:55-65`) and a batch-upload Celery worker
(`docker/scripts/entrypoint.sh:67-70`). `docker/scripts/db/01-ensure-nextseek-db.sh:2-7`
creates the NExtSEEK schema and its grants at first MySQL initialisation.
`docker/scripts/attribute_runtime_healthcheck.py:2-9` probes the attribute runtimes
without starting a second Django process, reading either a process plus a SQLite broker
(`docker/scripts/attribute_runtime_healthcheck.py:45-58`) or a durable heartbeat row
(`docker/scripts/attribute_runtime_healthcheck.py:22-26`).

### The baked capabilities document

`docker/cc-runtime/build_context/plugins/nextseek/context/capabilities.md` is a copy.
The canonical file it copies lives in `chat_nextseek` and is named as canonical at
`build_tools/gen_op_surfaces/constants.py:8-10`; this copy is named as the baked one at
`build_tools/gen_op_surfaces/constants.py:11-13` and is rendered as a whole-file
generated target at `build_tools/gen_op_surfaces/emit.py:220-224`. Two `COPY` lines put
it in the image: the broad plugin copy at `docker/cc-runtime/Dockerfile:51` lands this
copy, and the named-context copy at `docker/cc-runtime/Dockerfile:54` then overwrites
that same in-image path with the canonical bytes, from a build context declared at
`docker-compose.yml:124-125`. The ordering is not incidental — the generator refuses a
Dockerfile whose last writer of that path is not the canonical one
(`build_tools/gen_op_surfaces/docker_blocks.py:150-159`). See `docker/CLAUDE.md` for
what the two copies currently disagree about and what that costs.

## Running and testing

`docker/cc-runtime/` is the only surface here with tests of its own. They live in two
roots and neither is reached by the repo-root pytest configuration.

The first root is `docker/cc-runtime/tests/unit/`, eight hermetic modules: seven over the
in-image batch-upload client, runner, payload builder, models, dependencies, extractor
and shims, and one linting the skill contract, with a JSON fixture corpus beside them. Run on 2026-09-03 from
`docker/cc-runtime/` with pytest, polars, fastexcel, xlsxwriter, orjson, pydantic, httpx
and websockets supplied on the fly and the declared `addopts` overridden:
**109 passed, 35 warnings, in 1.44s**. The same suite under the declared `addopts`
(pytest-socket, pytest-cov and the rest installed) still shows 109 passed but exits 1 on
`Coverage failure: total of 0 is less than fail-under=95`; see `docker/CLAUDE.md`.

```
cd docker/cc-runtime && uv run --no-project \
  --with pytest --with polars --with fastexcel --with xlsxwriter \
  --with orjson --with pydantic --with httpx --with websockets \
  python -m pytest tests/unit -q -o addopts=""
```

The second root is `docker/cc-runtime/build_context/plugins/nextseek/bin/tests/`, a
single dispatch-pipeline module. It sits outside the `testpaths` value at
`docker/cc-runtime/pyproject.toml:64`, so it must be named explicitly. Run the same way
on 2026-09-03 with pytest, httpx, pydantic and websockets: **4 passed in 0.17s**.

Nothing under `docker/bedrock-proxy/`, `docker/ns-sidecar/`, `docker/nginx.conf`,
`docker/nginx-optional/`, `docker/scripts/`, `docker/cc-runner/`, `docker/eval/` or
`docker/eval-task6/` has a suite in this directory: a `find` for any `test_*.py` or
`conftest.py` beneath `docker/` returns only the two roots above. What exercises them
instead lives in `nextseek_api/cc_assistant/tests/` — digest drift guards, port-evidence
guards, compose-topology guards — and in `startup/tests/`. Building the images is a
separate lane again, `./startup.sh rebuild --component <name>`, documented in
`DEPLOYMENT.md:263-266`.

## Depends on / depended on by

Inbound edges here are `COPY` sources and compose mounts, not imports. Outbound edges
are path strings: other packages read files under this directory by path, and none of
them imports a module from it.

Depends on, outside this directory:

- `chat_nextseek/src/chat_nextseek/context/capabilities.md`, pulled in through the
  `chat_nextseek` named build context (`docker-compose.yml:124-125`) and copied at
  `docker/cc-runtime/Dockerfile:54`.
- `dmac_assistant/baml_src/`, mirrored here and generated against this copy's own path
  inside the image (`docker/cc-runtime/Dockerfile:113-117`); all eight `.baml` files were
  byte-identical between the two directories on 2026-09-03, compared with `cmp`.
- The repo-root build, for `docker/scripts/`, which reaches the application image only
  through it and is addressed there by absolute path (`docker-compose.yml:354`). A grep
  for `docker/scripts/entrypoint.sh` across all seven files matched by
  `find . -name 'Dockerfile*'` hits exactly one, the repo-root `Dockerfile`, whose
  repo-relative path is a bare filename this citation grammar cannot express; no
  Dockerfile beneath this directory copies `scripts/` at all.
- `startup/templates/nextseek.env.template` and `startup/templates/db.env.template`,
  which the startup CLI renders into this directory
  (`startup/steps/config.py:149-162`). The `.env` files themselves are gitignored
  (`.gitignore:169-170`).
- Two built images by tag, for the composite eval build
  (`docker/eval-task6/Dockerfile:7-8`).

Depended on by, grouped by what the consumer does with the path. Test modules are
included here rather than omitted, because the port and topology guards among them are
the only thing enforcing several of the invariants in `docker/CLAUDE.md`:

- The op registry reads the plugin `bin/` directory as its inventory root
  (`nextseek_api/cc_assistant/bin_inventory.py:19-23`) and parses one helper module out
  of it with `ast` (`nextseek_api/cc_assistant/op_registry/derive.py:11-13`), which
  `nextseek_api/cc_assistant/tests/test_op_registry_audit.py:305-310` cross-checks
  against the NExtSEEK handler table.
- Port guards hash this directory's files against literals pinned in the test itself:
  five files for the proxy
  (`nextseek_api/cc_assistant/tests/test_step7_proxy_port.py:65-71`, enforced at
  `nextseek_api/cc_assistant/tests/test_step7_proxy_port.py:128-144`), twelve for the
  sidecar (`nextseek_api/cc_assistant/tests/test_step7_sidecar_port.py:85-98`), and the
  twenty-one entries of the agent image's own manifest
  (`docker/cc-runtime/PORT-EVIDENCE.json:31-51`), enforced by size and digest at
  `nextseek_api/cc_assistant/tests/test_step7_cc_runtime_port.py:404-414`.
- Compose topology guards pin the agent build context
  (`nextseek_api/cc_assistant/tests/test_step7_compose_deploy.py:1277-1284`).
- The Django staging sweep re-derives the sidecar's user hash and names this
  directory's line range as the definition it must match
  (`nextseek_api/cc_assistant/cc_staging.py:109-113`).
- The surface generator writes into this directory and validates it: the baked
  capabilities file (`build_tools/gen_op_surfaces/emit.py:220-224`), the container
  agent instructions and the docs content-hash pin
  (`build_tools/gen_op_surfaces/constants.py:34-36`), and the marked blocks inside the
  agent Dockerfile itself (`build_tools/gen_op_surfaces/constants.py:22` names the file,
  `build_tools/gen_op_surfaces/constants.py:25-32` the four block markers).
- `chat_nextseek`'s end-to-end harness reads the two rendered env files by path
  (`chat_nextseek/e2e/import_env.py:30-31`).
- The startup CLI's image-secret gate allowlists exactly one file from this directory
  inside the built app image (`startup/steps/registry_push.py:40`) — the same file the
  repo-root `.dockerignore:44-48` re-includes after excluding the real env files.
- The assistant test corpus names three plugin `bin/` paths as the tools a task family
  is expected to use (`nessie_tests/FAMILIES.json:388-390`) — a data reference, not an
  execution path.

Not a dependency, despite matching a search for this directory's name: the many
`.vetting/` review logs and `SPEC-`/`PLAN-` documents under
`nextseek_api/cc_assistant/` quote these paths as prose history, and the JSON blobs
under `evidence/` record them as past ownership manifests. Neither reads a file. Also
excluded: `docker-compose.task8.yml`, which mounts `docker/scripts/db` and the nginx
configuration the same way the real compose file does but is a Task-6 acceptance
fixture, not the deployed stack.

See `docker/CLAUDE.md` for the invariants these edges rest on and the traps that look
like bugs.

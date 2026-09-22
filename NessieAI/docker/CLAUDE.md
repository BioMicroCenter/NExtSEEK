# Working in NessieAI/docker/

## Invariants

Each is enforced from outside this folder. Breaking one is a security regression, a silent deploy failure or a red suite.

- **Five `bedrock-proxy/` files are digest-pinned** (its Python modules and the secret-env example) inside `NessieAI/tests/cc/test_step7_proxy_port.py`. An in-place edit fails; updating the port manifest does not silence it.
- **Every `.py` under `ns-sidecar/` is digest-pinned** the same way, in `NessieAI/tests/cc/test_step7_sidecar_port.py`. Port, never rewrite.
- **`cc-runtime/PORT-EVIDENCE.json` is an enforced integrity manifest.** It records a size and digest per file and `NessieAI/tests/cc/test_step7_cc_runtime_port.py` asserts both. Change a plugin-tree catalog or an ingested doc, and update the manifest in the same commit.
- **The plugin context has one copy of each chat_nextseek context file.** `capabilities.md`, `projects_db.json` and four `min_*.json` catalogs are not in `cc-runtime/build_context/plugins/nextseek/context/`: the generated capabilities-copy block of `cc-runtime/Dockerfile` COPYs each from the Compose named context `chat_nextseek` to the same in-image path. Those COPYs stay the last writers of their paths (`validate_canonical_context_final_writers` in `NessieAI/build_tools/gen_op_surfaces/docker_blocks.py`), and the generator refuses a copy put back in the plugin tree. Edit the files in `NessieAI/chat_nextseek/src/chat_nextseek/context/`.
- **The sidecar's staging hash and the Django sweep's are one function.** `ns-sidecar/app/staging.py` is the definition and `NessieAI/cc/cc_staging.py` re-implements it. Change one alone and staged artifacts land where the sweep never looks.
- **The proxy never publishes a host port.** It authenticates no caller and attaches the institutional token to every request it relays.
- **A client `Authorization` header is dropped, never forwarded** (the hop-by-hop drop set in `bedrock-proxy/app/proxy.py`).
- **The fd-shuffle in `cc-runtime/container/runner_ns.py` stays its first executable statement.**
- **`cc-runtime/container/CLAUDE.md` stays committed.** It is a required Dockerfile `COPY` input. Only its marked blocks (`PLAN005-GEN`, `NEXTSEEK-DOCS`) are generated; everything else is hand-written.
- **`cc-runtime/` holds no BAML sources.** Its Dockerfile COPYs the Compose named context `dmac_assistant_baml` (the canonical `NessieAI/dmac_assistant/baml_src/`) to `/app/baml_src/`; a copy added here is refused by `NessieAI/tests/router/test_baml_single_source.py`. A BAML edit therefore needs a cc-agent rebuild as well as the app rebuild.

## Landmines

- **The CC agent's routing rule lives in the plugin skill, not in a context file.** `min_graph_schema.json` is no longer baked (`cc-runtime/PORT-EVIDENCE.json`, 2026-09-18): its chat_nextseek twin is the NS parser's routing prose, and the plugin copy had gone stale against the rule that every sample question goes to the `nextseek-graph` op. Change CC routing in `cc-runtime/build_context/plugins/nextseek/skills/nextseek/SKILL.md` and `cc-runtime/build_context/plugins/nextseek/context/MANIFEST.md`, and keep them in step with `cc-runtime/build_context/plugins/nextseek/context/read_safe_endpoints.json`: an endpoint the skill sends the agent to must be read-safe. `neo4j_schema.json` is no longer baked either (2026-09-17): the agent reads the deployed graph through the `nextseek-graph-schema` op, so a catalog change needs no cc-agent rebuild.
- **A bare `pytest` inside `cc-runtime/` exits 1 even when every test passes**: the declared coverage targets name trees this port lacks. Pass `-o addopts=""`.
- **`docker build` on `cc-runtime/` alone fails.** The named contexts `chat_nextseek` and `dmac_assistant_baml` exist only through compose; a manual build must pass both as `--build-context`, exactly as the generated `additional_contexts` block in `docker-compose.yml` declares them.
- **A cc-agent build bakes the chat_nextseek context files as they are on disk.** The config rewrites three of them in place when it runs against a checkout (`NessieAI/chat_nextseek/CLAUDE.md`); check `git status` there before a cc-agent rebuild.
- **The plugin `hooks/hooks.json` is inert in the image.** The container entrypoint re-registers the hook; edit that block.
- **`cc-runtime/container/runner_ns.py` ships but nothing calls it.** Do not read it as how a turn runs.
- **`cc-runtime/build_context/docs/nextseek-api/` ships empty on purpose**: a placeholder keeps its `COPY` working.
- **`ns-sidecar/app/contract.py` and the plugin's `cc-runtime/build_context/plugins/nextseek/bin/_ws_contract.py` are one contract in two copies.** `NessieAI/tests/cc/test_ws_contract_parity.py` fails when they stop being byte-identical; change both, then re-pin the sidecar copy's digest.
- **Each `PORT-EVIDENCE.json` records a named developer's home path, pinned by equality in three port tests.** You cannot scrub it without reddening them, and never copy it into a doc.
- **`bedrock-proxy/proxy-secret.env` is ignored only by the `**/proxy-secret.env` rule.** On each box it is moved by hand; `stat` and `git check-ignore -v` it after the move. While its `AWS_BEARER_TOKEN_BEDROCK` is empty, `./startup.sh rebuild` and `./startup.sh ci` stop before the Nessie CI lane on a box declaring local or dev, because the lane's CC turn cannot reach the model (`ci/smoke/README.md` "Nessie lane"); `--no-nessie` skips the lane.

## Test command

See `NessieAI/tests/README.md` ("cc-runtime" rows).

## See also

- `NessieAI/docker/README.md`: what each context ships.
- `NessieAI/docker/cc-runtime/container/CLAUDE.md`: what the agent is told.
- `NessieAI/cc/CLAUDE.md`: the host side of the sandbox.
- `docker/CLAUDE.md`: nginx, entrypoint, ports and env rules for the rest of the stack.

# Working in `docker/`

## Invariants

Each of these is enforced by something outside this directory. Breaking one is a
security regression, a silent deployment failure, or a red suite — never a refactor.

- **Five files in `docker/bedrock-proxy/` are digest-pinned** — its four Python modules
  plus the secret-env example, listed as literal SHA-256 values in the guard itself
  (`nextseek_api/cc_assistant/tests/test_step7_proxy_port.py:65-71`), so an in-place edit
  fails unconditionally and updating the port manifest to match does not silence it
  (`nextseek_api/cc_assistant/tests/test_step7_proxy_port.py:130-144`). All five still
  matched on 2026-09-03, recomputed from those literals.
- **Twelve files under `docker/ns-sidecar/` are pinned the same way**
  (`nextseek_api/cc_assistant/tests/test_step7_sidecar_port.py:85-98`), which is every
  `.py` file that directory holds — a `find` for `*.py` beneath it returns those twelve
  and nothing else. All twelve matched on 2026-09-03. Rewriting the sidecar rather than
  porting it is what this refuses.
- **`docker/cc-runtime/PORT-EVIDENCE.json` is an enforced integrity manifest, not a
  changelog.** Its inventory records a byte size and digest for 21 files
  (`docker/cc-runtime/PORT-EVIDENCE.json:31-51`), and the guard asserts both
  (`nextseek_api/cc_assistant/tests/test_step7_cc_runtime_port.py:411-414`).
  All 21 verified against the tree on 2026-09-03. Editing any catalog, any ingested
  documentation page, or the baked capabilities copy without updating that manifest in
  the same commit turns the suite red.
- **The canonical capabilities `COPY` must stay the last writer of its in-image path.**
  `docker/cc-runtime/Dockerfile:53-55` is a generated block, and the generator raises if
  any later `COPY` writes that destination
  (`build_tools/gen_op_surfaces/docker_blocks.py:154-159`). Move it above the plugin copy
  and the image ships the stale bytes instead.
- **The sidecar's staging hash and the Django sweep's must be the same function.**
  `docker/ns-sidecar/app/staging.py:24-25` is the definition the sweep names as
  authoritative, and re-implements (`nextseek_api/cc_assistant/cc_staging.py:109-113`).
  Changing one alone means staged artifacts are written to a directory the sweep never
  looks in, with no error anywhere.
- **The proxy image must never publish a host port.** `docker-compose.yml:104-105`
  states the rule for the stanza, and the two networks it does join keep it off the
  default stack network entirely (`docker-compose.yml:96-103`). The relay authenticates
  no caller: its whole request ladder is a token-presence check, a raw-path
  canonicalization, an allowlist compare and two body-size caps
  (`docker/bedrock-proxy/app/proxy.py:231-263`), after which it attaches the
  institutional bearer token unconditionally
  (`docker/bedrock-proxy/app/proxy.py:265-272`). Publishing the port hands that token's
  spending power to anyone who can reach the host.
- **A client-supplied `Authorization` header is dropped, never forwarded.** It sits in
  the hop-by-hop drop set at `docker/bedrock-proxy/app/proxy.py:58-65`, lowercased on the
  way in so no casing evades it (`docker/bedrock-proxy/app/proxy.py:53-57`). Removing
  that entry lets a caller smuggle its own credential upstream alongside the proxy's.
- **The fd-shuffle in the NS runner must remain the module's first executable
  statement.** `docker/cc-runtime/container/runner_ns.py:20-25` says so in capitals and
  explains the mechanism at `docker/cc-runtime/container/runner_ns.py:6-9`: any import
  above it that wraps `sys.stdout` sends JSONL events into the wrong channel and the
  bridge stops seeing progress.
- **`docker/cc-runtime/container/CLAUDE.md` is generated output that must stay
  committed.** It is a required build input (`docker/cc-runtime/Dockerfile:64`), carved
  out of a blanket ignore rule precisely so a clean clone can build the image
  (`.gitignore:191-194`), and refreshed by a named tool rather than by hand
  (`DEPLOYMENT.md:612-613`). Hand-editing it is overwritten on the next regeneration.
- **`docker/cc-runtime/baml_src/` and the router's copy must stay byte-identical.** A
  deploy verifier compares them, and shipping an agent image whose judge client was
  generated from a different contract than the router's is the failure it prevents —
  see `dmac_assistant/CLAUDE.md:13-18` for the verifier and its line ranges.
- **The application entrypoint refuses to serve on stale static or an unmigrated
  schema.** Both gates exit rather than continue
  (`docker/scripts/entrypoint.sh:13-18`, `docker/scripts/entrypoint.sh:47-53`), and under
  compose `restart: always` that crash-loops until fixed, which is the chosen trade
  (`docker/scripts/entrypoint.sh:9-12`). Swallowing either failure is what once masked a
  wedged migration for a week (`docker/scripts/entrypoint.sh:41-46`).
- **The nginx upstream must stay behind a variable.** `docker/nginx.conf:60-62` sets one
  so the resolver at `docker/nginx.conf:24` re-resolves per request; inlining the service
  name makes nginx cache a dead container's address after any rebuild and answer 502
  until nginx itself is restarted (`docker/nginx.conf:18-23`).
- **The upstream `Host` header must stay overridden.** `docker/nginx.conf:73-74` forces a
  Django-safe value because the compose service name contains an underscore, which
  `request.get_host()` rejects (`docker/nginx.conf:68-72`).

## Landmines

- **The baked capabilities copy and its canonical source differ right now.** Measured
  2026-09-03 with `wc -c` and `cmp`: the baked copy is 11747 bytes, the canonical named at
  `build_tools/gen_op_surfaces/constants.py:8-10` is 12128, and they first differ at byte
  1958 — where the canonical opens a Publication capability the baked file goes straight
  to Investigation
  (`docker/cc-runtime/build_context/plugins/nextseek/context/capabilities.md:35`).
  **This costs the running agent nothing**, because
  `docker/cc-runtime/Dockerfile:54` overwrites the baked path with the canonical bytes
  after `docker/cc-runtime/Dockerfile:51` has landed them. What it costs is every caller
  of the surface generator, which raises on the inequality before doing anything else
  (`build_tools/gen_op_surfaces/route_capabilities.py:229-232`), plus the equality test
  at `nextseek_api/assistant/tests/test_route_capabilities.py:283-284`. It is a standing
  condition already carried at `ci/pytest-baseline.txt:27`, not something you caused.
- **Fixing that drift is not a one-file edit.** The baked copy is one of the 21 entries
  in the port manifest, so rewriting it also requires updating its recorded size and
  digest (`docker/cc-runtime/PORT-EVIDENCE.json:31`) or the port guard goes red in place
  of the generator.
- **A bare `pytest` inside `docker/cc-runtime/` exits 1 even when every test passes.**
  The declared coverage targets at `docker/cc-runtime/pyproject.toml:63` name a `src/`
  tree and a test harness package that this port does not carry — a `find` for a
  directory named `src` or anything named `harness` anywhere beneath
  `docker/cc-runtime/` returns nothing. Measured 2026-09-03: 109 passed, then
  `Coverage failure: total of 0 is less than fail-under=95`. Pass `-o addopts=""`.
- **`docker build` on `docker/cc-runtime/` alone cannot succeed.** The build reads a
  named context that only compose supplies (`docker-compose.yml:124-125`), consumed at
  `docker/cc-runtime/Dockerfile:54`. Build it through compose, or the `COPY --from` has
  no context to resolve.
- **`docker/cc-runtime/build_context/plugins/nextseek/hooks/hooks.json` is inert in the
  shipped image.** Headless Claude Code does not load a local plugin's hook manifest, so
  the container entrypoint re-registers the same hook into user settings with `jq`
  (`docker/cc-runtime/container/entrypoint.sh:105-111`). Editing the manifest alone
  changes nothing at runtime; the entrypoint block is the live copy.
- **`docker/seek-nginx.conf` does not exist in this repo and must exist on the host.** A
  `find` for `seek-nginx.conf*` across the whole worktree outside `.git` returns nothing,
  yet `docker-compose.yml:281` bind-mounts it into the SEEK container. If it is absent
  when SEEK is recreated, Docker creates a directory there and SEEK crash-loops;
  `DEPLOYMENT.md:249-257` carries the command that renders it.
- **Never add a new published port on the dev or prod hosts.** All five published-port
  mappings bind `127.0.0.1` (`docker-compose.yml:58`, `docker-compose.yml:215`,
  `docker-compose.yml:261-262`, `docker-compose.yml:283`), and an awk pass over every
  `ports:` block in that file on 2026-09-03 found none binding a routable interface, so
  nothing here is reachable except through the operator's own edge proxy. <!-- UNVERIFIED: that only 22, 80 and 443 reach those hosts, and that SELinux confines nginx to http_port_t, is host configuration; a repo-wide grep for http_port_t returns nothing and no firewall or SELinux policy is tracked here --> The supported alternative is to
  multiplex onto an existing port: `docs/neo4j-programmatic-access.md:113-118` gives the
  bolt-over-443 recipe and its hazard, and `docker/nginx.conf:54` is the seam that lets
  you add a subpath instead of a port.
- **The shipped optional drop-in has no access control, deliberately.**
  `docker/nginx-optional/neo4j.conf.example:13-16` explains why an allow/deny here would
  match the wrong client, and `docker/nginx-optional/neo4j.conf.example:22-25` spells out
  that Neo4j Community has no read-only role, so anyone reaching the proxied paths with
  the password can delete the graph.
- **`docker/nextseek.env.example` is documentation, not the render source.** The startup
  CLI renders from `startup/templates/nextseek.env.template:9` and
  `startup/templates/nextseek.env.template:51`, and the two files have drifted: measured
  2026-09-03, the example declares two volume keys
  (`docker/nextseek.env.example:70-71`) the template omits, and omits `SEEK_PUBLIC_URL`
  and the Container-CC budget cap that the template renders. Hand-copying the example
  leaves `SEEK_PUBLIC_URL` empty (`dmac/settings.py:536`), which is the value SEEK links
  are built from.
- **`docker/cc-runner/` is not part of the build graph.** `DEPLOYMENT.md:610-611` calls
  it dead weight, its own header says it is never wired into the agent build
  (`docker/cc-runner/Dockerfile:10-13`), and a negative test pins the compose context
  away from it (`nextseek_api/cc_assistant/tests/test_step7_compose_deploy.py:1277-1284`).
  It also pins an older CLI (`docker/cc-runner/Dockerfile:22`) than the real image
  (`docker/cc-runtime/Dockerfile:31`), so reading it for current behaviour misleads.
- **`docker/eval-task6/Dockerfile:12-14` hardcodes a `python3.14` site-packages path** in
  the application image it copies out of. That matches today's floor
  (`pyproject.toml:6`); raising the app's interpreter to 3.15 breaks all three `COPY`
  lines with a path-not-found at build time and no other warning.
- **A named developer's home path is pinned by equality in three tests.**
  `docker/cc-runtime/PORT-EVIDENCE.json:2`,
  `docker/bedrock-proxy/PORT-EVIDENCE.json:2` and
  `docker/ns-sidecar/PORT-EVIDENCE.json:2` each record a path under one person's home
  directory, asserted literally at
  `nextseek_api/cc_assistant/tests/test_step7_cc_runtime_port.py:389`,
  `nextseek_api/cc_assistant/tests/test_step7_proxy_port.py:380` and
  `nextseek_api/cc_assistant/tests/test_step7_sidecar_port.py:446`. Those three files are
  the only ones under this directory carrying that path, by grep on 2026-09-03. You
  cannot scrub the string without reddening three tests, and you cannot reach the tree it
  names from any other machine, so the "ported verbatim" claim in each Dockerfile header
  is unre-verifiable here.
- **The wire-contract parity guard both copies advertise does not exist in this repo.**
  `docker/ns-sidecar/app/contract.py:5-6` and its plugin twin name a
  `test_ws_contract_parity.py` that fails on drift; a `find` for that filename anywhere
  in the worktree outside `.git` returns nothing, and a grep for `_ws_contract` across
  every `.py` file outside this directory finds only presence checks and `ast` parsing,
  never a byte comparison. The two files are identical today, verified with `cmp` on
  2026-09-03; nothing would tell you if they stopped being so.
- **`docker/cc-runtime/container/runner_ns.py` is shipped but unreached from this
  branch.** The image installs it (`docker/cc-runtime/Dockerfile:67-68`), yet a grep for
  `/opt/dmac` across the worktree outside `.git` and this directory
  returns seven hits on 2026-09-03 — a design note, two stored transcripts, two
  presence-only port assertions and one image-secret gate command — and none invokes it.
  Do not read it as evidence of how a turn runs today.
- **`docker/cc-runtime/build_context/docs/nextseek-api/` ships empty on purpose.** It
  holds only a placeholder so the `COPY` at `docker/cc-runtime/Dockerfile:62` succeeds on
  a fresh checkout; the upstream test expectation that a README is present there was
  already stale at port time (`docker/cc-runtime/PORT-EVIDENCE.json:55-56`).
- **Moving `docker/scripts/` breaks five wired consumers with no build-time error.**
  Three healthchecks reach it by absolute in-image path
  (`docker-compose.yml:354`, `docker-compose.yml:388`, `docker-compose.yml:420`), a fourth
  mount hands `docker/scripts/db` to MySQL as its init directory
  (`docker-compose.yml:219`), and the application image's own start command is a fifth
  that no Dockerfile beneath this directory supplies. The failure surfaces only at run
  time, as unhealthy containers and an app container that will not boot.
- **Two more baked catalogs differ from their canonical copies, and these ones DO reach
  the agent.** Comparing all eight shared files in that context directory against
  `chat_nextseek/src/chat_nextseek/context/` with `cmp` on 2026-09-03: `min_graph_schema.json`
  is 4165 bytes baked against 6860 canonical, and `neo4j_schema.json` 13542 against 13801;
  the other five match, capabilities.md aside. Neither has a canonical override in the Dockerfile — the broad plugin
  copy at `docker/cc-runtime/Dockerfile:51` is the only line that writes them — so the
  stale bytes are what ships. The manifest sends the agent to exactly these two before it
  decides whether a question is answerable in the graph at all
  (`docker/cc-runtime/build_context/plugins/nextseek/context/MANIFEST.md:18`), and the
  drift sits in the query triggers and node descriptions that decision is made from.
- **Refreshing the plugin catalogs is not a command you can run in this repo.** The
  manifest states they are the agent's ground truth, to be consulted rather than guessed
  from (`docker/cc-runtime/build_context/plugins/nextseek/context/MANIFEST.md:3-6`), but
  the snapshot generator's entry point lives in an external clone and its one recorded run
  was BLOCKED (`docker/cc-runtime/PORT-EVIDENCE.json:8-16`). They are hand-maintained
  here, so a sampletype or endpoint the agent needs stays invisible until someone edits
  the JSON and its digest in the port manifest together.

## Test command

The one lane this directory owns, run from `docker/cc-runtime/`:

```
uv run --no-project --with pytest --with polars --with fastexcel \
  --with xlsxwriter --with orjson --with pydantic --with httpx --with websockets \
  python -m pytest tests/unit -q -o addopts=""
```

2026-09-03: 109 passed, 35 warnings, in 1.44s. The `-o addopts=""` is not optional
here; see the coverage-gate landmine above. The second test root under the plugin's
`bin/tests/` must be named explicitly and needs httpx instead of the sheet stack:
4 passed in 0.17s on the same date.

## See also

- See `docker/README.md` for what each build context ships, how the surfaces relate,
  and the dependency map in both directions.
- See `docker/cc-runtime/container/CLAUDE.md` for the in-container agent's own
  instructions — write-safety, the plugin inventory, and the operation list.
- See `docker/cc-runtime/docs/nextseek/README.md` for the ingested NExtSEEK
  documentation set baked into the agent image.
- See `DEPLOYMENT.md:263-266` for the per-component rebuild verbs, and
  `DEPLOYMENT.md:607-609` for the two-BAML-client warning.
- See `nextseek_api/cc_assistant/CLAUDE.md` for the host side of the agent sandbox:
  network segmentation, mount subpaths, and the route decision.
- See `chat_nextseek/CLAUDE.md` for the canonical capabilities document and its own
  view of the drift.

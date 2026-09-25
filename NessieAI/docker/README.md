# NessieAI/docker/

The four AI image build contexts. The `docker/<name>` suffix is kept on purpose. nginx, the
app-container scripts and the env docs stay in the root `docker/` (`docker/README.md`).

None of this is a Python package: the Python in each context runs only inside its own image.

## Surface

| Context | Built by | Produces |
|---|---|---|
| `cc-runtime/` | compose service `cc-agent` (build target only, never runs) | `dmac-assistant:poc`, the per-turn Container-CC agent image |
| `bedrock-proxy/` | compose service `bedrock-proxy` | `nextseek-bedrock-proxy:latest` |
| `ns-sidecar/` | compose service `nextseek-sidecar` | `nextseek-ns-sidecar:latest` |
| `eval/` | an explicit `docker build -f NessieAI/docker/eval/Dockerfile .` from the repo root | the JAX/NumPyro HiBayes fit image (`NessieAI/hibayes`, plus the router and harness files `human_grade_fit.py` needs from outside it) |

Rebuild verbs: `./startup.sh rebuild --component cc-agent | bedrock-proxy | nextseek-sidecar` (`DEPLOYMENT.md` §3.2).

### cc-runtime: the agent image

- A pinned Claude Code CLI on node 22 (checked at build time with `claude --version`), a uv-managed CPython, and an unprivileged `user` account.
- The baked `nextseek` plugin under `cc-runtime/build_context/plugins/nextseek/`: `plugin.json` (identity only), two skills, the `/nextseek` command, a `UserPromptSubmit` hook, the `nextseek-*` shims in `bin/`, and a `context/` catalog directory.
- The in-container agent instructions, `cc-runtime/container/CLAUDE.md`.
- The ingested NExtSEEK docs, `cc-runtime/docs/nextseek/` (generated; do not hand-edit).
- A BAML judge client generated at build time from `NessieAI/dmac_assistant/baml_src/`, the one BAML tree, through the compose named context `dmac_assistant_baml`.
- The chat_nextseek context files (`capabilities.md`, `projects_db.json` and four `min_*.json` catalogs) from `NessieAI/chat_nextseek/src/chat_nextseek/context/`, through the compose named context `chat_nextseek`, into the plugin's `context/`. They have no copy in the plugin tree.

### bedrock-proxy: the model gateway

A FastAPI relay that holds the Bedrock bearer token and attaches it upstream, so the agent carries
no AWS credential. It allows the model paths of exactly three models (a Container-CC turn's main
model, its fallback and its auto-mode classifier's), drops any client `Authorization` header,
caps bodies and never logs the token. `/healthz` answers before any token is used.
Its secret file `bedrock-proxy/proxy-secret.env` is untracked and moved onto each box by hand.

### ns-sidecar: the per-request op broker

A WebSocket server that validates a request, builds a per-user HTTP config from credentials in
the frame, and calls NExtSEEK's granular endpoints (`nextseek_api/assistant/CONTRACT.md`).
It stages artifacts under a SHA-256 of the user, and the Django sweep in `NessieAI/cc/` moves them.

## Running and testing

`cc-runtime/tests/` and the plugin's `bin/tests/` stay in this package; their commands are in
`NessieAI/tests/README.md`. The digest, port and compose guards for all four contexts are in `NessieAI/tests/cc/`.

## Depends on / depended on by

- Depends on `NessieAI/chat_nextseek/` (the canonical context files, through a named context), `NessieAI/dmac_assistant/baml_src/` (BAML sources, through a named context), `NessieAI/hibayes/` (eval image).
- `NessieAI/build_tools/gen_op_surfaces/` writes and validates the generated blocks here: the container CLAUDE.md inventories, the Dockerfile blocks (including the canonical context-file COPYs).
- `NessieAI/cc/` spawns containers from `dmac-assistant:poc` and reads the plugin `bin/` inventory.

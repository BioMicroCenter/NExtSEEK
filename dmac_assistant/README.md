# dmac_assistant (vendored for NExtSEEK)

In-tree, vendored subset of [dmac_assistant](https://github.com/tavjo/dmac-assistant),
wired into NExtSEEK as an **additive** integration (see
`nextseek_api/cc_assistant/`). This mirrors how NExtSEEK already vendors
`chat_nextseek/` as a path dependency.

## What the NExtSEEK integration actually uses

- `dmac_assistant.router.*` — the BAML-backed LLM **router** (`RouteQuery`) that
  decides, per turn, between the deterministic NExtSEEK query pipeline
  (`chat_nextseek`, run in-process by Django) and the sandboxed
  **Container-Claude-Code** path.
- `dmac_assistant.streamjson` — the incremental Claude `stream-json` parser used
  by the Container-CC engine.

## What is intentionally NOT used

The FastAPI/uvicorn/websocket bridge (`app.py`, `auth.py`, `ws.py`), the
per-user Dropbox mount model, the `BridgeConfig`/`DMAC_USERS` store, the vanilla
HTML chat UI, and the offline HiBayes `eval/` pipeline. NExtSEEK supplies its
own Django/Channels transport, auth, chat frontend, and queue. The
`dependencies` in `pyproject.toml` are trimmed accordingly (no
fastapi/uvicorn/websockets), so importing the router + streamjson never drags
in the server layer.

## Router config

`build_context/route_capabilities.json` and `build_context/router_model_class_map.json`
are read at runtime. Because the package is pip-installed (not run from source),
point the dmac loaders at these files via the documented env overrides:

- `DMAC_ROUTE_CAPABILITIES_FILE=/app/dmac_assistant/build_context/route_capabilities.json`
- `DMAC_ROUTER_MODEL_CLASS_MAP_FILE=/app/dmac_assistant/build_context/router_model_class_map.json`

(both set in `docker/nextseek.env`).

## Server deployment — set the host-path overrides

The Container-CC artifact binds in `docker-compose.yml` default to the **author's
laptop paths** (`/Users/taishajoseph/...`):

```
- ${DMAC_HOST_SCRATCH_ROOT:-/Users/taishajoseph/dmac-dev/scratch}:/dmac/scratch
- ${DMAC_HOST_OUTPUT_ROOT:-/Users/taishajoseph/Library/CloudStorage/Dropbox/DMAC_Data/example-project}:/dmac/output
```

These are **only read when a Container-CC query actually runs** — the in-process
NExtSEEK route (data lookups, GEO/PRIDE/SRA submissions) and the chat UI work
without them, and the CC route is gated off whenever the `dmac-assistant:poc`
image is absent. So leaving them unset does **not** break or crash a deployment.

However, Docker's short-syntax bind mounts **auto-create a missing host source**,
so on a non-laptop (Linux) host with these unset, `docker compose up` will
silently create empty `/Users/taishajoseph/...` directories on the server. To
avoid that, set the three host roots to real server paths **before** bringing the
stack up — export them in the deploy shell or add them to `docker/nextseek.env`.
Note: `startup/lib/instance.py` `compose_env()` does **not** inject these, so the
`:-` laptop defaults apply unless you set them.

```
DMAC_HOST_SCRATCH_ROOT=/srv/nextseek/dmac/scratch      # per-user CC scratch (bind source)
DMAC_HOST_OUTPUT_ROOT=/srv/nextseek/dmac/output        # published-artifact root (no Dropbox on a server)
DMAC_HOST_DROPBOX_ROOT=/srv/nextseek/dmac/projects     # per-project read-only mount root
```

These are the CC sibling container's bind *sources*; they (and the rest of the
Container-CC bring-up: building/loading `dmac-assistant:poc`, `CLAUDE_CODE_USE_BEDROCK=1`,
`DMAC_CC_USER_PROJECTS`) only matter once you actually enable the Container-CC route.

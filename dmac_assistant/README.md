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

# dmac_assistant (vendored for NExtSEEK)

In-tree, vendored subset of [dmac_assistant](https://github.com/tavjo/dmac-assistant),
wired into NExtSEEK as an **additive** integration (see
`nextseek_api/cc_assistant/`). This mirrors how NExtSEEK already vendors
`chat_nextseek/` as a path dependency.

## What the NExtSEEK integration actually uses

Exactly two imports, both from `nextseek_api/cc_assistant/`:

- `dmac_assistant.router.*` — the BAML-backed LLM **router** (`RouteQuery`) that
  decides, per turn, between the deterministic NExtSEEK query pipeline
  (`chat_nextseek`, run in-process by Django) and the sandboxed
  **Container-Claude-Code** path. Imported by `router.py` and `cc_summary.py`
  (lazily, inside functions, so a vendoring hiccup cannot break Django's boot).
- `dmac_assistant.run_tracker.diff_files`, scratch-dir change detection, imported
  by `cc_engine.py`.

`dmac_assistant.streamjson` is **not** imported anywhere in NExtSEEK (grep it).
The Container-CC engine parses Claude `stream-json` with its own adapter,
`nextseek_api/cc_assistant/translate.py`, which is deliberately pure-stdlib (no
Django, no docker, no dmac imports) so it unit-tests in isolation. It cites
`streamjson.py` and `ws.py` only as the reference for the event shapes.

## What is intentionally NOT used

The FastAPI/uvicorn/websocket bridge (`app.py`, `auth.py`, `ws.py`), the
per-user Dropbox mount model, the `BridgeConfig`/`DMAC_USERS` store, the vanilla
HTML chat UI, and the offline HiBayes `eval/` pipeline. NExtSEEK supplies its
own Django/Channels transport, auth, chat frontend, and queue. The
`dependencies` in `pyproject.toml` are trimmed accordingly (no
fastapi/uvicorn/websockets), so the two imports above never drag in the server
layer.

## Router config

`build_context/route_capabilities.json` and `build_context/router_model_class_map.json`
are read at runtime. **No env wiring is required**: `nextseek_api/cc_assistant/router.py`
(`_build_context_dir`) resolves `BASE_DIR/dmac_assistant/build_context` itself and
passes each loader an explicit path.

dmac's loaders take `explicit path > env override > package default`, so the
documented overrides below only take effect if that directory is missing:

- `DMAC_ROUTE_CAPABILITIES_FILE`
- `DMAC_ROUTER_MODEL_CLASS_MAP_FILE`

Neither is set by `startup/templates/nextseek.env.template` or
`docker/nextseek.env.example`.

The BAML client itself (`src/dmac_assistant/router/baml_client/`) is **generated at
image-build time** by `baml-cli generate` in the root `Dockerfile`; only
`baml_src/` is committed.

## Server deployment (no host bind mounts)

There are no `DMAC_HOST_*` host-path overrides to set. Container-CC storage is a
single **external named Docker volume**, `dmac-cc-users`, created by
`./startup.sh install` (`startup/steps/volumes.py`), exactly like `seek-filestore`:

- the `nextseek` service mounts the whole volume at `/dmac/users`
  (`DMAC_USER_ROOT_MOUNT`) so Django can create per-user scratch/cc-state/memory
  dirs and publish artifacts;
- each per-turn CC sibling container mounts a per-user **subpath** of that same
  volume (docker-py `VolumeOptions.Subpath`), so no mount source is ever a host
  directory and no manual host `mkdir`/`chown` is needed;
- the `nextseek-sidecar` service mounts only the reserved `_staging/` subpath.

Because the sibling containers are spawned by `cc_engine` rather than by compose,
`DMAC_CC_USERS_VOLUME` must carry the same `INSTANCE_PREFIX` as the volume itself;
`docker-compose.yml` sets it on the `nextseek` service for that reason.

The legacy flat-root vars (`DMAC_HOST_SCRATCH_ROOT`, `DMAC_HOST_OUTPUT_ROOT`,
`DMAC_HOST_DROPBOX_ROOT`, `DMAC_HOST_CC_STATE_ROOT`, `DMAC_SCRATCH_MOUNT`,
`DMAC_OUTPUT_MOUNT`, `DMAC_CC_STATE_MOUNT`, `DMAC_CC_USER_PROJECTS`) are **gone**,
and `nextseek_api/cc_assistant/tests/test_cc_migration_grep_guard.py` fails the
build if any of them reappears in runtime code.

What still gates the CC route (`cc_engine.cc_runner_available`): a reachable
docker daemon, the `dmac-assistant:poc` image (`docker compose build cc-agent`
tags it), and an existing `dmac-cc-net` network. Any of those missing turns the
CC route off; the in-process NExtSEEK route and the chat UI work regardless.

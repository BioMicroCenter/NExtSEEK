# `NessieAI/docker/ns-sidecar/`

## What this is

The NS sidecar: a WebSocket server that sits between the Container-CC agent and NExtSEEK. It
validates each request frame, builds a per-user HTTP config from the credentials in the frame (Basic
auth per call, nothing in the environment), calls NExtSEEK's granular endpoints, and stages
artifacts under a hash of the user. Compose service `nextseek-sidecar`, image `nextseek-ns-sidecar:latest`.
It publishes no host port; the agent reaches it over the compose network.

## Surface

| Path | What it is |
|---|---|
| `Dockerfile` | the image build; the build context is this folder |
| `app/server.py` | the WebSocket server: accept, validate, dispatch, typed response |
| `app/contract.py`, `app/granular_models.py` | the frame and op models (the HTTP side is `nextseek_api/assistant/CONTRACT.md`) |
| `app/ops.py`, `app/ns_client.py` | the op handlers and the NExtSEEK HTTP client |
| `app/write_gate.py` | `build_gate`: the only local check, that an `api-write` carries `confirmed_write` as a strict bool; NExtSEEK enforces the rest |
| `app/staging.py` | artifact staging; the Django sweep in `NessieAI/cc/cc_staging.py` moves staged files into the user's own tree |
| `app/config.py`, `app/healthcheck.py` | env config and the compose healthcheck |
| `PORT-EVIDENCE.json` | the record of the port from the source repo, pinned by `NessieAI/tests/cc/test_step7_sidecar_port.py` |

## Running and testing

```bash
./startup.sh rebuild --component nextseek-sidecar
```

(`DEPLOYMENT.md` §3.2.) Its guards are in `NessieAI/tests/cc/` (for example `test_step7_sidecar_port.py`,
`test_sidecar_ws_max_size.py`); lanes are in `NessieAI/tests/README.md`.

## Depends on / depended on by

The plugin shim client `_sidecar_client.py` in the cc-runtime plugin `bin/` talks to it. Parent doc: `NessieAI/docker/README.md`.

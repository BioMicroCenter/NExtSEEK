# cc-spawner

The one service that mounts the Docker socket. The app asks it, over HTTP on the internal `dmac-spawner-net`
network, to run a Container-CC turn, stop one, or run one of three fixed SEEK admin scripts. It builds the container
(image, network, mounts, limits, `cap_drop ALL`, `no-new-privileges`, `auto_remove`, labels, command) from fields it
validates itself, so the app cannot choose any of those.

| File | What it is |
|---|---|
| `spawner.py` | the service: `ThreadingHTTPServer`, the `docker` SDK, the validation rules |
| [`seek_scripts/`](seek_scripts/README.md) | the Ruby for `create`, `patch`, `compensate`; each reads `ENV["NEXTSEEK_PAYLOAD"]`, never interpolated |
| `Dockerfile` | python slim, `docker==7.1.0`, runs as root inside with every capability dropped by compose |
| `tests/` | unit tests with the SDK faked; run in the Django lane (`ci/README.md` "Running and testing") |

## API

Every call but `GET /health` needs the header `X-Spawner-Token` equal to `SPAWNER_TOKEN` (401 missing, 403 wrong).

| Call | Does |
|---|---|
| `GET /health` | without a token `{"ok": bool}`; with the token adds `detail` (Docker, CC image, CC network) |
| `POST /turns` | validates the body, runs the container, writes `stdin_envelope`, closes stdin, streams stdout as a chunked response until the container exits |
| `POST /turns/{run_id}/stop` | stops the container labelled `nextseek.cc=1`; answers `{"gone": bool}` |
| `POST /seek/{create,patch,compensate}` | execs the fixed script in the SEEK container with the payload in the exec environment; answers `{exit_code, last_line, stderr}` |

## Environment

`SPAWNER_TOKEN`, `DMAC_CC_USERS_VOLUME`, `NEXTSEEK_CC_IMAGE`, `NEXTSEEK_CC_NETWORK`, `SEEK_CONTAINER`, and the CC
limits the app used to read: `NEXTSEEK_CC_MAX_TURNS`, `NEXTSEEK_CC_MEM_LIMIT`, `NEXTSEEK_CC_NANO_CPUS`,
`NEXTSEEK_CC_PIDS_LIMIT`, `NEXTSEEK_CC_FSIZE_BYTES`, and the auto-mode trust text inputs (non-secret): `NEXTSEEK_INTERNAL_BASE_URL`, `NEO4J_URI`, `NEXTSEEK_CC_AUTOMODE_GCP` (`1`/`0`; the spawner never receives the Gemini key).

## Rules

- A request cannot set the image, network, mounts, limits or privileges. Mounts are subpaths of the one users volume,
  derived from `project_dirname`, `user_id`, `cc_state_key` and `run_id`.
- `environment` keys are an allowlist that matches `cc_engine.build_agent_environment`; a test holds the two together.
- The spawner never stops a turn on its own: only `POST /turns/{run_id}/stop` or the container's own end does.
- The app side is `NessieAI/cc/spawner_client.py`. With `NEXTSEEK_SPAWNER_URL` unset or empty, Container-CC turns
  take the direct Docker path in `cc_engine.py` (one cycle); SEEK ops always use the spawner.

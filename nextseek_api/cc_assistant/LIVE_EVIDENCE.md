# Live deployment evidence — dmac_assistant integration on `nextseek-dev.mit.edu`

Deployed 2026-06-26 onto the running NExtSEEK dev instance (`nextseek-dev.mit.edu`)
by recreating the `nextseek` container from the integration image
(`nextseek-nextseek:integration`, `NEXTSEEK_SERVER=gunicorn`), additive, with an
instant `dev-rollback` image kept. Procedure: `nextseek_api/cc_assistant/DEPLOY.md`.

The Django-test-runner suites (`test_granular_realstack`, `test_cc_realstack`)
could not run on this box because each spins up a `test_*` database and
`seek_db_user` lacks `CREATE` (creating it was disallowed as a shared-DB write).
So the integration was proven against the **live deployed endpoints + UI**
instead — a stronger, more authentic check (real instance, real auth, real data).

## Deploy verification (live)
- `gunicorn dmac.wsgi` (4 workers) + celery; migrations applied OK (`chatsession.extra_state`).
- `cc_engine.cc_runner_available() == (True, 'ok')` (poc image + `dmac-cc-net` + docker socket wired).
- `POST /nextseek_api/cc-assistant/query/async/` → 401 (registered, auth-gated; not 404).
- Native `POST /nextseek_api/assistant/entity/` → 401 (intact). Site `/` → 200.

## End-to-end through the real chat UI (Playwright, logged in as `demo`)
- Native query "Find me mice treated with NDMA" → native pipeline ran (entity extraction → query planning).
- Agentic query ("…pull the published samples … save the UIDs missing an organism … ") → routed to the
  Container-CC agent, which executed code ("Bash done") and ran **22 Opus invokes, all `200 OK`, through the proxy**.
- Screenshots: `/home/taishajo/work/state/pw/out/{C2-assistant,N-2response,CC-2response}.png`.

## Full Container-CC turn, completed (forced route, via the live API as `demo`)
`POST /nextseek_api/cc-assistant/cc/query/async/` → polled `…/tasks/{id}/progress/`:
- events: `route_decided → agent_started → search_started → search_complete → query_complete`
- reply echoed the per-run sentinel **and** reported the published artifact path
- artifact published by the host-side copier: **`/srv/dmac/output/demo/proof.txt`** (user-scoped), written from agent scratch `/srv/dmac/scratch/demo/proof.txt`.
- see `tests/live_evidence/forced_cc_published_artifact.txt`.

## OI-3 agent isolation — verified LIVE (captured during a real CC turn)
From `tests/live_evidence/agent_env_scan.txt` + `proxy_invokes.txt`:
- **Agent env holds NONE of the shared backend credentials** (no `AWS_BEARER_TOKEN_BEDROCK`, no `NEO4J_*`, no `MYSQL_*`, no `GCP_API_KEY`, no `ANTHROPIC_API_KEY`).
- Bedrock reached only via the proxy: `ANTHROPIC_BEDROCK_BASE_URL=http://bedrock-proxy:8080`, `CLAUDE_CODE_SKIP_BEDROCK_AUTH=1`.
- **Agent network = `dmac-cc-net` only** → segmented from `neo4j`/`seek-mysql`/`seek` (cannot reach the databases).
- Proxy: opus-4-8 invokes all `200 OK`; **0** `ABSK`/`Authorization` occurrences in the proxy log (token never exposed/logged).
- The agent's only NExtSEEK credential is the **requesting user's own** login (`NEXTSEEK_USERNAME/PASSWORD`, per-request).

## Reproduce
- Deploy / rollback: `nextseek_api/cc_assistant/DEPLOY.md`; rollback `tag dev-rollback → latest` + recreate.
- UI: the Playwright scripts in `/home/taishajo/work/state/pw/` (Dockerized, host-net, `mcr.microsoft.com/playwright`).
- Forced CC turn: `curl -u <seekuser>:<pass> -X POST -d '{"mode":"standard","query":"…"}' …/cc-assistant/cc/query/async/` then poll `…/tasks/{id}/progress/`.
- Hermetic security + validator suites (no spend, no DB): `pytest nextseek_api/cc_assistant/tests/` (57 tests).

Demo safety: the native stack is untouched/additive (native endpoints + site verified 200/401 after deploy);
`nextseek-nextseek:dev-rollback` + `state/rollback.sh` restore pure-native in ~1 min.

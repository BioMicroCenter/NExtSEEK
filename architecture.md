# Nessie — Architecture

**Nessie** is the chat assistant embedded in NExtSEEK: a chat page inside the NExtSEEK web app, a query router, and two execution paths — an in-process pipeline ("NS") built on NExtSEEK's assistant engine `chat_nextseek`, and a per-turn sandboxed Claude Code container ("CC") — plus the deployment and security topology those paths need.

> **Provenance.** Refreshed against the worktree on branch `docs/repo-wide-refresh` at `ad226f1`, 2026-09-03, by direct code inspection. All file paths are relative to the repository root. Where a runtime value depends on a deployment env file that is not committed, the code default is stated. This document is the **system map across boundaries**; each of the 22 subsystem boundaries has its own `README.md` + `CLAUDE.md` pair, which is the authority for that boundary's internals. Where this file and a boundary pair disagree, the pair wins. See the [Directory map](#directory-map) for the links.

> **Naming decoder.** Identifiers beginning with `dmac-`/`dmac_` — the agent image `dmac-assistant:poc`, the networks `dmac-cc-net` and `dmac-cc-egress`, the volume `dmac-cc-users`, the `dmac_assistant/` Python package — all name parts of NExtSEEK's Container-CC assistant subsystem: `nextseek_api/cc_assistant/` and the `CCAssistantViewSet` are its application layer, `dmac_assistant/` is its (vendored) router package, and `docker/cc-runtime`, `docker/ns-sidecar`, `docker/bedrock-proxy` are its runtime images. It is all NExtSEEK code.

---

## TL;DR

- The chat UI lives at **`/seek/assistant/`** inside NExtSEEK (login is SEEK-credential based). The route is declared at `seek/urls.py:13` and mounted under `^seek/` by `dmac/urls.py:27`; `USE_I18N = False` (`dmac/settings.py:56`) so `i18n_patterns` adds no language prefix. A React app posts each message to a Django endpoint and reads progress by **HTTP-polling** a task endpoint; a WebSocket progress channel exists but needs the ASGI server (`docker/scripts/entrypoint.sh:61-65`).
- Every message goes through a **router** (`nextseek_api/cc_assistant/router.py:308`) that picks one of three routes — **NS**, **CC**, or **Unrelated** (canned reply, nothing executes) — declared at `nextseek_api/cc_assistant/router.py:30-32`. There are now **three** route-choosing strategies, not two: a feature-flagged comparative-posterior selector, a BAML/Gemini router, and a regex heuristic.
- **NS path**: NExtSEEK's assistant engine `chat_nextseek/` runs *in-process* in a Django worker thread — REST self-calls back into NExtSEEK's API, read-only Neo4j queries, and read-only MySQL reads.
- **CC path**: one **ephemeral Docker container per turn** (image `dmac-assistant:poc`) runs Claude Code in auto-permission mode on an **internal, gateway-less network** (`dmac-cc-net`, `docker-compose.yml:495`). The agent holds **zero AWS credentials** — model calls go through a credential-holding `bedrock-proxy` that only allows one Opus model (`docker/bedrock-proxy/app/config.py:18`). Per-turn caps: spend budget **$0.50** (`nextseek_api/cc_assistant/cc_engine.py:73`), 50 turns (`nextseek_api/cc_assistant/cc_engine.py:74`), and a wall-clock ceiling defaulting to 180 s (`nextseek_api/cc_assistant/cc_engine.py:81`).
- The CC agent's NExtSEEK "ops" are **chat_nextseek functions exposed as standalone server-side operations** — the agent container ships only thin shims; the intelligence executes inside Django.

---

## System topology

```mermaid
flowchart LR
    B["Browser - React chat UI"]
    NG["nextseek_nginx (dual-homed, 127.0.0.1:8000)"]
    subgraph DEF["compose default network"]
        DJ["nextseek - Django + worker threads + Celery"]
        DS[("MySQL / Neo4j / Solr / SEEK")]
    end
    subgraph CC["dmac-cc-net (internal: true)"]
        AG["per-turn CC agent container"]
        SC["nextseek-sidecar"]
        BP["bedrock-proxy"]
    end
    EG["dmac-cc-egress (bedrock-proxy only)"]
    AWS["AWS Bedrock"]
    VOL[("volume dmac-cc-users")]
    B -->|"HTTP"| NG
    NG --> DJ
    DJ --> DS
    DJ -->|"docker.sock - spawn per turn"| AG
    AG -->|"WS 8765 - ops"| SC
    AG -->|"model calls"| BP
    AG -->|"REST"| NG
    SC -->|"REST, basic auth"| NG
    BP --> EG --> AWS
    DJ -.->|"/dmac/users"| VOL
    AG -.->|"RO/RW subpaths"| VOL
    SC -.->|"_staging subpath only"| VOL
```

**Legend.** Solid arrows are request flows; dotted lines are volume mounts. `nextseek_nginx` is the **only service on both the default network and `dmac-cc-net`** (`docker-compose.yml:75-77`) and is the sandboxed agent's only route back into NExtSEEK. `dmac-cc-net` is declared `internal: true` (`docker-compose.yml:495`), so it has no NAT/gateway at all; `bedrock-proxy` is the only service that also joins the egress-capable `dmac-cc-egress` (`docker-compose.yml:102-103`, `docker-compose.yml:498-499`), which is how AWS stays reachable while the agent inherits no internet path. The Django container sits on the default network only, so the agent has no L3 reach to Django, MySQL, SEEK, Solr, or Neo4j. `bedrock-proxy` and `nextseek-sidecar` publish no host port. The per-turn agent container is spawned by Django via the bind-mounted Docker socket (`docker-compose.yml:44`, docker-py), not by compose.

---

## Anatomy of a turn

### 1. Front door: page, auth, submit, progress

**Page.** `seek/urls.py:13` maps `^assistant/` to `views.smartSearch`, whose definition is at **`seek/views/search.py:110`** — `seek/views.py` no longer exists; the views are a package of 11 modules whose re-exports are listed at `seek/views/__init__.py:11-20`. Unauthenticated users get an error page (`seek/views/search.py:111-113`); authenticated users get `seek/templates/smartSearch.html` (`seek/views/search.py:114`), which mounts `<div id="chat-assistant-root">` (`seek/templates/smartSearch.html:7`), sets `<meta name="chat-basename" content="/seek/assistant/">` (`seek/templates/smartSearch.html:4`), and loads the embedded React build via `{% vite_assets "src/main.embedded.tsx" ... %}` (`seek/templates/smartSearch.html:8`).

**Auth.** The Django session comes from SEEK-credential login (`dmac/views.py:110` `login_seek`): credentials are checked via `SeekDB.getSeekLogin`, stored in the session, then Django `authenticate()` + `login()` run; the bare GET renders `login.html` (`dmac/views.py:168`). Login is SEEK, not MIT SSO. The embedded frontend is same-origin cookie-based: `chat_frontend/src/lib/services/sessionAuth.ts:14` derives the API base from `window.location`, `chat_frontend/src/lib/services/sessionAuth.ts:19` derives the `ws://`/`wss://` base the same way, and `chat_frontend/src/lib/services/sessionAuth.ts:4-11` sends `X-CSRFToken` only if a `csrftoken` cookie exists.

**Submit.** `chat_frontend/src/lib/services/chatApi.ts:78` POSTs to **`/nextseek_api/cc-assistant/query/async/`**. `use_prod` selects the alternate `NEXTSEEK_CHAT_CONFIG_PROD` ChatConfig and swaps the pipeline's outbound API credentials for that config's baked-in `API_USER`/`API_PASS` (`nextseek_api/services/cc_assistant.py:501-504`); the requesting user's own credentials are captured before the swap (`nextseek_api/services/cc_assistant.py:495-496`) and are what the CC route carries.

**Server dispatch.** Both viewsets are DRF-router-registered under `/nextseek_api/`: `assistant/` → `AssistantViewSet` (`nextseek_api/urls.py:38`), `cc-assistant/` → `CCAssistantViewSet` (`nextseek_api/urls.py:40`) — additive, the new one does not replace the old. `CCAssistantViewSet.query_async` (`nextseek_api/services/cc_assistant.py:788`) validates the request and calls `_start_task(force_cc=False)`; a second endpoint `cc/query/async/` (`nextseek_api/services/cc_assistant.py:808`) forces the CC route. Auth: DRF Token, CSRF-exempt session, and Basic (`nextseek_api/services/cc_assistant.py:449`).

`_start_task` (`nextseek_api/services/cc_assistant.py:479`):

1. Resolves the `ChatSession` — a miss returns 404 (`nextseek_api/services/cc_assistant.py:481-485`).
2. Creates a `QueryTask` (status `running`) (`nextseek_api/services/cc_assistant.py:487-489`) and builds `send_event = make_db_event_callback(...)` (`nextseek_api/services/cc_assistant.py:490-491`; `nextseek_api/assistant/pipeline_adapter.py:10-16`) — every progress event is *appended to the `QueryTask.progress` JSON column*, and terminal events also set `status`/`result` (`nextseek_api/assistant/pipeline_adapter.py:30-44`).
3. Wraps the `ChatSession` in a `DictSessionAdapter` (`nextseek_api/services/cc_assistant.py:494`; `nextseek_api/assistant/session_adapter.py:17-32`), resolves the user's SEEK credentials, and spawns a **plain daemon thread**. This is *not* Celery — Celery (`batch_upload` queue) serves the file-upload endpoint (`docker/scripts/entrypoint.sh:67-70`).
4. Returns **HTTP 202 `{task_id, session_id}`** immediately.

**Progress transport — HTTP polling in practice.** The client first *attempts* a WebSocket at `ws/assistant/progress/{task_id}/` (`chat_frontend/src/lib/services/chatApi.ts:110`) and, only if it fails to open, falls back to HTTP polling at a 2 s interval (`chat_frontend/src/lib/services/chatApi.ts:12`) of `/nextseek_api/assistant/tasks/{taskId}/progress/` (`chat_frontend/src/lib/services/chatApi.ts:181` — note: the *assistant* endpoint, not cc-assistant). Which channel actually runs is decided by the web-server toggle in `docker/scripts/entrypoint.sh:61-65`: `NEXTSEEK_SERVER=gunicorn` (WSGI) cannot complete a WS handshake; anything else starts `daphne` (ASGI), which is the code default. <!-- UNVERIFIED: which server the dev and production instances actually run is a deployment-time env value; the previously recorded "gunicorn on the running instance" observation is from 2026-07-12 and was not re-checked for this refresh. -->

Under daphne, the WS leg is served by `TaskProgressConsumer` (`nextseek_api/assistant/consumers.py:18`), mounted as the single WS route by `nextseek_api/assistant/routing.py:7-11` and wired into channels' auth stack at `dmac/asgi.py:23` and `dmac/asgi.py:25-29`. There is no channel-layer broadcast: it polls the `QueryTask` row every 300 ms (`nextseek_api/assistant/consumers.py:44`), streams newly appended events, then sends a final `{event: 'done', status, result}` frame and closes (`nextseek_api/assistant/consumers.py:136-158`). **The task UUID is explicitly *not* a capability token** — a connection carrying a valid UUID but no authenticated user is rejected, and the user comes from the Django session cookie via `AuthMiddlewareStack` (`nextseek_api/assistant/consumers.py:29-41`). Separately, a **legacy synchronous SSE endpoint** survives: `POST /nextseek_api/assistant/query/` runs the NS pipeline and streams progress as `text/event-stream` (`nextseek_api/services/assistant.py:727`, `nextseek_api/services/assistant.py:825-827`) — SSE works fine under WSGI, but the embedded UI never calls it.

**Session management** (list / rename / delete / hydrate turns) uses the pre-existing `AssistantViewSet` routes: `GET|PATCH|DELETE /nextseek_api/assistant/sessions/...` (`chat_frontend/src/lib/services/chatApi.ts:312`, `chat_frontend/src/lib/services/chatApi.ts:324`, `chat_frontend/src/lib/services/chatApi.ts:343`, `chat_frontend/src/lib/services/chatApi.ts:357`).

### 2. The router

`cc_router.decide(query)` (`nextseek_api/cc_assistant/router.py:308`) chooses between **three** strategies, not two:

```mermaid
flowchart TD
    Q["user query"] --> F{"posterior routing enabled?"}
    F -->|"yes"| PS["comparative-posterior selector"]
    PS -->|"selection"| R{"route"}
    PS -->|"None"| L
    F -->|"no"| L["BAML RouteQuery - Gemini via GCPReasoner"]
    L -->|"decision"| R
    L -->|"error or unavailable sentinel"| H["regex heuristic - default NS"]
    H --> R
    R -->|"nextseek_query"| NS["NS path - in-process chat_nextseek"]
    R -->|"container_cc"| CCP["CC path - sandboxed Claude Code, always Opus"]
    R -->|"unrelated"| U["canned reply - nothing executes"]
```

- **Posterior leg**: `decide` consults `posterior_selector.posterior_routing_enabled()` first (`nextseek_api/cc_assistant/router.py:310`), a Django-settings feature flag defaulting to off (`nextseek_api/cc_assistant/posterior_selector.py:37-38`). When on, `_posterior_enabled_decide` (`nextseek_api/cc_assistant/router.py:240`) calls `select_route` (`nextseek_api/cc_assistant/posterior_selector.py:41`); a returned selection short-circuits the BAML leg entirely, and `None` falls through to it. When the flag is off, `_legacy_decide` (`nextseek_api/cc_assistant/router.py:233`) is the whole path.
- **LLM leg**: `_route_query` (`nextseek_api/cc_assistant/router.py:212`) runs the BAML function `RouteQuery` through a guarded, function-body import of `dmac_assistant.router.agent.RouterAgent` (`nextseek_api/cc_assistant/router.py:135-139`), bound to client `GCPReasoner` — provider `google-ai`, model `gemini-3.1-pro-preview`, key `GCP_API_KEY`, exponential retry with `max_retries 2` (`dmac_assistant/baml_src/clients.baml:5-22`). Any error yields `None` → heuristic. If the router package's own error fallback fires (sentinel reasoning `<router_unavailable>`, `nextseek_api/cc_assistant/router.py:33`), the sentinel is detected and the heuristic runs instead — so an unavailable LLM never silently forces the expensive CC route.
- **Heuristic leg**: a regex keyword classifier (`nextseek_api/cc_assistant/router.py:107`) defaulting to `ROUTE_NS` (`nextseek_api/cc_assistant/router.py:43-48` are the two pattern sets).
- **Model pinning**: for the CC route, `model_class` is always `'opus'` and `model_id` always comes from `resolve_cc_model()` (`dmac_assistant/src/dmac_assistant/router/models.py:104-112`), which returns the fixed `opus` entry of `dmac_assistant/build_context/router_model_class_map.json:2` → `us.anthropic.claude-opus-4-8`. Sonnet/haiku entries exist in that map but are never selected — only Opus is allowlisted by the bedrock-proxy (`docker/bedrock-proxy/app/config.py:18`), so anything else would be refused.
- **Unrelated**: emits one `query_complete` with a fixed "NExtSEEK research assistant for the MIT BioMicro Center … outside that scope" reply (`nextseek_api/cc_assistant/router.py:35-40`) — neither path runs.
- **Forced CC**: the `cc/query/async/` endpoint bypasses the router entirely (`nextseek_api/services/cc_assistant.py:808`).

### 3. NS path — the in-process engine

On `ROUTE_NS`, the worker thread calls `chat_nextseek.orchestrator.run_query` (or `run_query_plan` when `mode == 'plan'`) directly, passing the `DictSessionAdapter` and the user's own SEEK credentials.

```mermaid
sequenceDiagram
    participant B as Browser
    participant V as CCAssistantViewSet
    participant P as progress endpoint
    participant T as Worker thread
    participant E as chat_nextseek
    B->>V: POST cc-assistant/query/async
    V->>V: resolve ChatSession, create QueryTask
    V->>T: spawn daemon thread
    V-->>B: 202 task_id + session_id
    B->>P: HTTP poll for progress (2 s, repeats)
    T->>T: router decides ROUTE_NS (route_decided event)
    T->>E: run_query(session adapter, user creds, send_event)
    E->>E: REST self-call, read-only Neo4j, read-only MySQL
    E-->>T: progress events, then final reply
    T->>T: events appended to QueryTask.progress
    P-->>B: newly appended events, then final done
    T->>T: adapter.save() persists session state, auto-title
```

Key mechanics:

- **Config isolation**: the orchestrator `copy.copy()`s the shared config and sets `API_USER`/`API_PASS` on the copy, so the shared `ChatConfig` singleton is never mutated across concurrent requests (`chat_nextseek/src/chat_nextseek/orchestrator.py:196-197`).
- **Session state**: `DictSessionAdapter` (`nextseek_api/assistant/session_adapter.py:17-32`) presents the Django `ChatSession` as a dict-like session. `results_history` and `last_debug` have dedicated columns; everything else the engine writes round-trips through the `extra_state` JSON column (`nextseek_api/assistant/models_db.py:16`). `save()` takes a row lock and merges bundle history by id (`nextseek_api/assistant/session_adapter.py:89-118`).
- **Data access tools**:
  - *NExtSEEK REST self-call* — `tool_nextseek_api_request` (`chat_nextseek/src/chat_nextseek/helpers/tools/nextseek_api.py:83`) with HTTP Basic auth as the user and a 90 s timeout (`chat_nextseek/src/chat_nextseek/helpers/tools/nextseek_api.py:133`) raised to 120 s for advanced search (`chat_nextseek/src/chat_nextseek/helpers/tools/nextseek_api.py:135`). Its base URL prefers `NEXTSEEK_INTERNAL_BASE_URL` over the public one, since self-calls run inside the container where a host-published port would be unreachable (`chat_nextseek/src/chat_nextseek/config.py:27`).
  - *Neo4j* — `tool_neo4j_query` (`chat_nextseek/src/chat_nextseek/helpers/tools/neo4j.py:57`) is **read-only by construction**: a regex blocks `CREATE|MERGE|SET|DELETE|DETACH DELETE|REMOVE|DROP|CALL db.|CALL apoc.schema.|CALL apoc.periodic.|LOAD CSV` (`chat_nextseek/src/chat_nextseek/helpers/tools/neo4j.py:73`).
  - *MySQL* — the reporter's project-sample report path reads directly via `config._connect_db(...)`, issuing hand-built read-only `SELECT`s. <!-- UNVERIFIED: no regex or gate on this path was located; asserting the absence would need an exhaustive search of the reporter tree that this refresh did not run. -->

### 4. CC path — one sandboxed container per turn

On `ROUTE_CC`, the same worker thread hands off to `nextseek_api/cc_assistant/cc_engine.py`; `run_cc_turn` is the driver. One ephemeral container is spawned per turn, runs the `claude` CLI directly, and is always removed afterwards. The image's own `CMD` (`docker/cc-runtime/Dockerfile:139`) is overridden by the bridge's full command at spawn time.

```mermaid
sequenceDiagram
    participant T as Django worker thread
    participant D as Docker Engine
    participant A as CC agent container
    participant P as bedrock-proxy
    participant S as nextseek-sidecar
    participant N as nginx to Django
    T->>T: gate - docker + image + network must exist (fail closed)
    T->>T: resolve SEEK project, build memory CLAUDE.md
    T->>D: containers.run dmac-assistant:poc on dmac-cc-net
    T->>A: stdin - one stream-json user envelope, then close
    A->>P: Bedrock invoke (Opus only)
    P-->>A: model response stream
    A->>S: op call over WS (entity, parse, graph, ...)
    S->>N: POST /nextseek_api/assistant/op/ (basic auth)
    N-->>S: result envelope
    S-->>A: op result
    A-->>T: stdout stream-json frames, translated to UI events
    T->>T: sweep staging, publish artifacts, persist transcript
    T->>D: stop + remove container (always, in finally)
```

Step by step:

1. **Gate** — `cc_runner_available()` (`nextseek_api/cc_assistant/cc_engine.py:179`) requires a live Docker daemon, the agent image (`nextseek_api/cc_assistant/cc_engine.py:188-191`), *and* the `dmac-cc-net` network (`nextseek_api/cc_assistant/cc_engine.py:199-202`) to already exist; the bridge never creates the network, so a missing piece fails closed.
2. **Project resolution** — `resolve_user_project` (`nextseek_api/cc_assistant/cc_provision.py:156`) uses the *user's own* SEEK credentials, via a lazily-imported `SeekDB` (`nextseek_api/cc_assistant/cc_provision.py:150-151`); any failure rejects the turn rather than guessing.
3. **Cross-session memory** — the most-recently-changed sibling session's transcript is re-summarized (`nextseek_api/cc_assistant/cc_summary.py:278` → BAML `Summarize` at `nextseek_api/cc_assistant/cc_summary.py:275`, on client `GCPFlash`, `gemini-3.5-flash` via `GCP_API_KEY`, `dmac_assistant/baml_src/clients.baml:26-32`), then `render_memory` (`nextseek_api/cc_assistant/cc_memory.py:49`) renders a merged `CLAUDE.md` + transcript-pointer block into the memory mount.
4. **Validation first** — `user_id`, `run_id`, `project_dirname` and the cc-state key are charset/traversal-validated *before* any path interpolation, mkdir or mount; the precondition is stated at `nextseek_api/cc_assistant/cc_engine.py:951-953`.
5. **Mounts** — all CC user trees are subpaths of the **single external named volume `dmac-cc-users`** (`docker-compose.yml:530-531`), never a host bind (`docker-compose.yml:45-53`). `_build_volumes` (`nextseek_api/cc_assistant/cc_engine.py:932`) builds five: `input` RO → `/data/input` and project-wide `shared` RO → `/data/shared` (`nextseek_api/cc_assistant/cc_engine.py:960-961`), a **per-turn** scratch subtree RW → `/data/scratch` (`nextseek_api/cc_assistant/cc_engine.py:971-973`; the user-scoped scratch root is deliberately *not* mounted, `nextseek_api/cc_assistant/cc_engine.py:962-970`), per-session cc-state RW → `/home/user/.claude` (`nextseek_api/cc_assistant/cc_engine.py:975-978`), and memory transcripts RO (`nextseek_api/cc_assistant/cc_engine.py:979-983`). A preflight fails closed if any backing dir is missing (`nextseek_api/cc_assistant/cc_engine.py:988-991`).
6. **Environment** — `build_agent_environment` (`nextseek_api/cc_assistant/cc_engine.py:282`) is the single source of the agent env and injects **zero AWS or backend credentials**: Bedrock is pointed at `http://bedrock-proxy:8080` (`nextseek_api/cc_assistant/cc_engine.py:66`) with the proxy holding the token, and the only secrets are the *requesting user's own* NExtSEEK login. The NExtSEEK base URL is rewritten to the `nextseek_nginx` service host (`nextseek_api/cc_assistant/cc_engine.py:249`, rationale at `nextseek_api/cc_assistant/cc_engine.py:247` and `nextseek_api/cc_assistant/cc_engine.py:268`) because the sibling container cannot reach Django's loopback.
7. **Command** — `claude --print --input-format stream-json --output-format stream-json --verbose --permission-mode auto` (`nextseek_api/cc_assistant/cc_engine.py:140-145`; a classifier gates each tool call — explicitly *not* `--dangerously-skip-permissions`, `nextseek_api/cc_assistant/cc_engine.py:136-137`), plus `--model <opus id>`, `--max-turns` and `--max-budget-usd` (`nextseek_api/cc_assistant/cc_engine.py:759-762`; a budget of 0 omits the flag), a settings-file allowlist of trusted-infra *descriptors* — never secret values (`nextseek_api/cc_assistant/cc_engine.py:766-772`) — and `--resume` when continuing a prior CC session.
8. **Caps** — budget default **$0.50** (`nextseek_api/cc_assistant/cc_engine.py:73`), turns default **50** (`nextseek_api/cc_assistant/cc_engine.py:74`), and a wall clock clamped into `[_TIMEOUT_FLOOR, _TIMEOUT_HARD_MAX]` by `clamp_turn_timeout` (`nextseek_api/cc_assistant/cc_engine.py:103-113`). **The 180 s ceiling is no longer immovable**: `_TIMEOUT_HARD_MAX` itself reads `NEXTSEEK_CC_TIMEOUT_HARD_MAX`, defaulting to 180 (`nextseek_api/cc_assistant/cc_engine.py:81`), and the per-turn value is `min(NEXTSEEK_CC_TIMEOUT_SECONDS, _TIMEOUT_HARD_MAX)` (`nextseek_api/cc_assistant/cc_engine.py:84-85`). A watchdog thread force-stops and removes the container on overrun (`nextseek_api/cc_assistant/cc_engine.py:1172-1182`).
9. **Spawn & input** — docker-py `containers.run` on `dmac-cc-net`, detached; a stale same-name container from a crashed run is force-removed and the spawn retried (`nextseek_api/cc_assistant/cc_engine.py:919`). The user query is written to stdin as **one** stream-json envelope, then stdin closes.
10. **Streaming out** — the container's stdout is demuxed line-by-line (`nextseek_api/cc_assistant/attach.py:1-9`); `CCStreamTranslator` (`nextseek_api/cc_assistant/translate.py:62`) maps Claude's stream-json onto the frontend's vocabulary, emitting `agent_started` (`nextseek_api/cc_assistant/translate.py:132`), `search_started` (`nextseek_api/cc_assistant/translate.py:165`, `nextseek_api/cc_assistant/translate.py:174`), `search_complete` (`nextseek_api/cc_assistant/translate.py:175`, `nextseek_api/cc_assistant/translate.py:192`), `query_error` (`nextseek_api/cc_assistant/translate.py:207`) and `query_complete` (`nextseek_api/cc_assistant/translate.py:215`). There is **no token streaming** — the final answer arrives as one Markdown string in `query_complete.reply` (`nextseek_api/cc_assistant/translate.py:5-13`).
11. **Staging sweep** — `sweep_user_staging` (`nextseek_api/cc_assistant/cc_staging.py:253`) moves this turn's `.complete`-marked artifacts out of `_staging/sha256(api_user)/` into the user's own tree. The destination is derived exclusively from the current request's validated identity and the source only from `sha256(api_user)` (`nextseek_api/cc_assistant/cc_staging.py:275`, hash at `nextseek_api/cc_assistant/cc_staging.py:113`). The sidecar's compose mount is locked to the `_staging` subpath (`docker-compose.yml:179-183`), so it cannot write into a user tree by construction (`nextseek_api/cc_assistant/cc_staging.py:22-23`).
12. **Publish** — `_publish_artifacts` (`nextseek_api/cc_assistant/cc_engine.py:1841`) diffs a before/after snapshot of the scratch mount and copies changed files into the session's output tree.
13. **Persist** — the newest cc-state `.jsonl` transcript is parsed into a `CCTrace` (`nextseek_api/cc_assistant/cc_trace.py:32`) and the raw transcript stored zstd-compressed as a `CCSessionTranscript` row (`nextseek_api/cc_assistant/cc_transcript_store.py:3-4`). `settings.CC_PERSIST_STRICT` controls whether persistence failures raise or log-and-continue (`nextseek_api/cc_assistant/cc_engine.py:1314`).
14. **Teardown** — a `finally` block always attempts `container.stop(timeout=5)` then `container.remove(force=True)` (`nextseek_api/cc_assistant/cc_engine.py:1372`, `nextseek_api/cc_assistant/cc_engine.py:1376`).

---

## The op catalog — chat_nextseek pieces exposed as standalone ops

The agent image does **not** contain `chat_nextseek`; its dependency manifest is `docker/cc-runtime/pyproject.toml:7-24`. Instead the image ships a `nextseek` plugin with **20 executable `nextseek-*` op shims** in `docker/cc-runtime/build_context/plugins/nextseek/bin/` (counted by listing that directory on 2026-09-03; the same count is the registry's, discovered from disk at `nextseek_api/cc_assistant/bin_inventory.py:19-23`). The runner behind them dispatches 13 agent labels (`docker/cc-runtime/build_context/plugins/nextseek/bin/_nextseek_runner.py:478-492`). The intelligence behind the sidecar family is the NS pipeline's own agents and tools, running server-side inside Django.

**Family A — sidecar ops** (**9**, up from 7): shim → `_nextseek_runner.py` → `_sidecar_client.call_op` (WebSocket to `nextseek-sidecar:8765`, port at `docker/cc-runtime/build_context/plugins/nextseek/bin/_sidecar_client.py:68`, 16 MiB frame cap at `docker/cc-runtime/build_context/plugins/nextseek/bin/_sidecar_client.py:27`) → the sidecar (`docker/ns-sidecar/app/server.py:1-3`, a stateless forwarder whose models are a vendored copy so the image imports no NExtSEEK package, `docker/ns-sidecar/app/granular_models.py:5-6`) → `POST /nextseek_api/assistant/{op}/` with HTTP Basic auth → `AssistantViewSet._run_granular_op` (`nextseek_api/services/assistant.py:1245`) → `nextseek_api/assistant/granular.py:44-58`, executed **synchronously in the Django request cycle**. The op set is declared in three places that agree: `docker/cc-runtime/build_context/plugins/nextseek/bin/_ws_contract.py:14-17`, `docker/ns-sidecar/app/contract.py:14-17`, and the handler table at `nextseek_api/assistant/granular.py:261-271`.

| Op shim | Transport | Server-side handler | Logic executes in | Notes |
|---|---|---|---|---|
| `nextseek-entity-extract` | WS → sidecar → REST | `granular._entity` (`nextseek_api/assistant/granular.py:61`) | Django (chat_nextseek in-process) | `entity_agent` |
| `nextseek-parse` | WS → sidecar → REST | `granular._parse` (`nextseek_api/assistant/granular.py:66`) | Django | entity + parser agents |
| `nextseek-graph` | WS → sidecar → REST | `granular._graph` (`nextseek_api/assistant/granular.py:72`) | Django | entity + parser + graph agents, then **executes** the Cypher and returns `{plan, result}` |
| `nextseek-api-read` | WS → sidecar → REST | `granular._api_read` (`nextseek_api/assistant/granular.py:97`) | Django | endpoint/method allowlist gate → request builder → REST tool |
| `nextseek-api-write` | WS → sidecar → REST | `granular._api_write` (`nextseek_api/assistant/granular.py:109`) | Django | `confirmed_write is True` gate → request builder → REST tool |
| `nextseek-report` | WS → sidecar → REST | `granular._report` (`nextseek_api/assistant/granular.py:125`) | Django | reporter summary; artifacts registered as a downloadable bundle (`nextseek_api/services/assistant.py:1284-1285`) |
| `nextseek-generate-submission` | WS → sidecar → REST | `granular._generate_submission` (`nextseek_api/assistant/granular.py:136`) | Django | report outputs + report-writer agent |
| `nextseek-run-ls` | WS → sidecar → REST | `granular._run_ls` (`nextseek_api/assistant/granular.py:192`) | Django | read-only `ls -laR` over SSH under the Luria runs root; reingest input |
| `nextseek-build-upload-xlsx` | WS → sidecar → REST | `granular._build_upload_xlsx` (`nextseek_api/assistant/granular.py:214`) | Django | renders one 4-sheet upload workbook per sample type; **no NExtSEEK write** |

**Family B — batch-upload ops** (7, dispatched from `docker/cc-runtime/build_context/plugins/nextseek/bin/_batch_upload_runner.py:548-556`): shim → `_batch_upload_runner.py` → `BatchUploadClient` (`_batch_upload_client.py`: httpx, Basic auth from env) calling **plain NExtSEEK DRF REST directly** — no sidecar, no chat_nextseek. One member is the exception: `nextseek-extract-text` makes no server call at all.

| Op shim | Transport | Server-side handler | Logic executes in |
|---|---|---|---|
| `nextseek-project-resolve` | direct REST | plain DRF endpoints | agent shim + Django REST |
| `nextseek-sampletype-attrs` | direct REST | plain DRF endpoints | agent shim + Django REST |
| `nextseek-sample-search` | direct REST | plain DRF endpoints | agent shim + Django REST |
| `nextseek-assay-resolve` | direct REST | plain DRF endpoints | agent shim + Django REST |
| `nextseek-build-payload` | direct REST (schema lookups) | plain DRF endpoints | mostly agent shim |
| `nextseek-validate-upload` | direct REST | `POST /nextseek_api/batch-upload/validate/` | Django — validation only, stops before insert |
| `nextseek-extract-text` | **none — fully local** | — (no server call) | agent container only (MarkItDown + fallbacks) |

**Family C — viewset-direct** (**4**, up from 1): these bypass the sidecar and talk to the assistant ViewSet over HTTP. `nextseek-plan` runs the planner (`docker/cc-runtime/build_context/plugins/nextseek/bin/_nextseek_runner.py:166` → `_run_viewset`, `docker/cc-runtime/build_context/plugins/nextseek/bin/_nextseek_runner.py:130`); `nextseek-pipeline` hands a CC-composed cohort summary to the NS pipeline agent in the live chat session (`docker/cc-runtime/build_context/plugins/nextseek/bin/_nextseek_runner.py:428-442`); `nextseek-query` runs a single deterministic NS turn in the live session and materializes bundle rows to scratch (`docker/cc-runtime/build_context/plugins/nextseek/bin/_nextseek_runner.py:259-266`); `nextseek-recall` fetches a prior NS turn's raw rows by turn id (`docker/cc-runtime/build_context/plugins/nextseek/bin/_nextseek_runner.py:345-351`). All four require `NEXTSEEK_CHAT_SESSION_ID`.

### Write safety — layered gates

1. **Claude Code permission allowlist (L1)** — the plugin's `scripts/setup.sh` merges an allowlist into the agent's `~/.claude/settings.json` (`docker/cc-runtime/build_context/plugins/nextseek/scripts/setup.sh:15-34`): read-class ops and the batch-upload shims are permitted (api-read only with a `--parser-plan` prefix, `docker/cc-runtime/build_context/plugins/nextseek/scripts/setup.sh:18`), but **`nextseek-api-write` is not listed** — nor are `nextseek-query` or `nextseek-recall`. Invoking any of them trips an auto-mode permission prompt.
2. **Shim-local guards** — `nextseek-api-read` refuses `--confirmed-write` outright (`docker/cc-runtime/build_context/plugins/nextseek/bin/_nextseek_runner.py:173-174`); `nextseek-api-write` raises `WRITE_BLOCKED` unless `--confirmed-write` is present (`docker/cc-runtime/build_context/plugins/nextseek/bin/_nextseek_runner.py:192-193`).
3. **Sidecar gate** — deliberately thin: the endpoint allowlist was retired to NExtSEEK and only the write-confirmation flag is checked locally (`docker/ns-sidecar/app/write_gate.py:1-3`).
4. **Django gate (authoritative)** — `build_gate` (`nextseek_api/assistant/write_gate.py:78-100`): api-write requires `confirmed_write is True` (`nextseek_api/assistant/write_gate.py:86`); api-read requires `(endpoint, METHOD)` in `nextseek_api/assistant/read_safe_endpoints.json` (`nextseek_api/assistant/write_gate.py:92`); the five read-class ops pass (`nextseek_api/assistant/write_gate.py:34`); **unknown op labels are default-denied** (`nextseek_api/assistant/write_gate.py:99-100`). Violations map to `WRITE_BLOCKED` / HTTP 403 (`nextseek_api/services/assistant.py:1275-1276`).

> **Known gap.** The Django gate's `SIDECAR_OPS` still lists only the original seven (`nextseek_api/assistant/write_gate.py:29-31`), while the handler table now has nine (`nextseek_api/assistant/granular.py:261-271`). The two newest ops never call the gate they are handed (`nextseek_api/assistant/granular.py:192`, `nextseek_api/assistant/granular.py:214` both take `write_gate` and never invoke it), so they neither pass nor trip the default-deny at `nextseek_api/assistant/write_gate.py:99-100`. Both are read-only or produce a reviewable workbook rather than writing to NExtSEEK, so this is a coverage gap, not an open write path.

Additionally, the CC-runtime `container/entrypoint.sh` maps env credentials and scrubs a settings file (`docker/cc-runtime/container/entrypoint.sh:4-7`), symlinks the image-baked plugin tree into `~/.claude/plugins/local/` where headless Claude Code actually discovers it (`docker/cc-runtime/container/entrypoint.sh:73-74`, `docker/cc-runtime/container/entrypoint.sh:87`), and can hold the container open for per-turn `docker exec` (`docker/cc-runtime/container/entrypoint.sh:128-135`).

---

## Deployment & security

### Compose services (`docker-compose.yml`)

The file declares **14 services**, 2 networks and 9 volumes (`docker-compose.yml:1`, `docker-compose.yml:481`, `docker-compose.yml:502`).

| Service | Network(s) | Host port | Role & notes |
|---|---|---|---|
| `nextseek` (`docker-compose.yml:2`) | default only | — | Django + worker threads + Celery. Env from `docker/db.env` + `docker/nextseek.env` (`docker-compose.yml:17-19`). Bind-mounts `/var/run/docker.sock` (`docker-compose.yml:44`) and the volume `dmac-cc-users` at `/dmac/users` (`docker-compose.yml:53`). |
| `nextseek_nginx` (`docker-compose.yml:55`) | default **+** `dmac-cc-net` (`docker-compose.yml:75-77`) | `127.0.0.1:${NEXTSEEK_PORT:-8000}` (`docker-compose.yml:57-58`) | The only dual-homed service; the agent's only route back into NExtSEEK. |
| `bedrock-proxy` (`docker-compose.yml:88`) | `dmac-cc-net` **+** `dmac-cc-egress` (`docker-compose.yml:102-103`) | none (`docker-compose.yml:104-105`) | Credential-holding model relay (container `dmac-bedrock-proxy`, `docker-compose.yml:92`). |
| `nextseek-sidecar` (`docker-compose.yml:164`) | `dmac-cc-net` only (`docker-compose.yml:184-185`) | none | Stateless op forwarder; mounts only the reserved `_staging` subpath (`docker-compose.yml:179-183`). |
| `cc-agent` (`docker-compose.yml:120`) | `network_mode: none` (`docker-compose.yml:128`) | none | **Build-target only** (`image: dmac-assistant:poc` `docker-compose.yml:127`, `command: ["true"]` `docker-compose.yml:129`) — real agents are spawned per turn by `cc_engine.py` via docker-py. |

The remaining nine: `db` (MySQL 8.0, `127.0.0.1:3306`, `docker-compose.yml:202`/`docker-compose.yml:214-215`), `neo4j` (`127.0.0.1:7474` and `127.0.0.1:7687`, `docker-compose.yml:222`/`docker-compose.yml:260-262`), `seek` (`fairdom/seek:1.15.1`, `127.0.0.1:3000`, `docker-compose.yml:264`/`docker-compose.yml:282-283`), `seek_workers` (`docker-compose.yml:291`), `solr` (`docker-compose.yml:316`), and four **profile-gated** services that do not start by default — `attribute_mutation_worker` (`docker-compose.yml:334`, `profiles: [attributes]` `docker-compose.yml:339`), `attribute_mutation_dispatcher` (`docker-compose.yml:368`/`docker-compose.yml:373`), `attribute_mutation_recovery_scheduler` (`docker-compose.yml:403`/`docker-compose.yml:408`) and `assay_registration_worker` (`docker-compose.yml:438`, `profiles: [assay-registration]` `docker-compose.yml:443`). All nine sit on the default network only. The network `dmac-cc-net` is pinned to that literal name (`docker-compose.yml:489`) and is `internal: true` (`docker-compose.yml:495`); `dmac-cc-egress` carries only the proxy (`docker-compose.yml:498-499`); the volume `dmac-cc-users` is `external: true` (`docker-compose.yml:531`) with an instance-prefixed name (`docker-compose.yml:536`).

### Credential placement

| Component | Holds | Never holds |
|---|---|---|
| CC agent container | the requesting user's own NExtSEEK login, proxy/sidecar URLs, path mappings (`nextseek_api/cc_assistant/cc_engine.py:282`) | any AWS credential, GCP key, DB password, or other backend secret |
| `bedrock-proxy` | the institutional Bedrock bearer token (`docker/bedrock-proxy/app/proxy.py:1-7`) | user credentials |
| `nextseek` (Django) | `GCP_API_KEY` (router + summarizer LLMs), DB/Neo4j/SEEK secrets, via `docker/nextseek.env` (`docker-compose.yml:19`) | — |
| `nextseek-sidecar` | nothing of its own — builds a per-user HTTP config from credentials carried inside the request frame (`docker/ns-sidecar/app/server.py:1-3`) | ambient credentials |

### The bedrock-proxy (`docker/bedrock-proxy/app/proxy.py`)

A FastAPI relay that **drops any inbound `Authorization` header** (`docker/bedrock-proxy/app/proxy.py:54`) and attaches its own bearer token outbound (`docker/bedrock-proxy/app/proxy.py:272`). It exact-match allowlists only `GET /inference-profiles` (`docker/bedrock-proxy/app/proxy.py:138`) and `POST /model/<id>/invoke` / `invoke-with-response-stream` for each allowed model (`docker/bedrock-proxy/app/proxy.py:141-143`) — default exactly `us.anthropic.claude-opus-4-8` (`docker/bedrock-proxy/app/config.py:18`). It rejects `//`, dot-segments and percent-encoded separators on the raw undecoded path (`docker/bedrock-proxy/app/proxy.py:101`), enforces a 10 MiB body cap (`docker/bedrock-proxy/app/config.py:22`), and pins the upstream host from the region at config load, which is what removes the SSRF surface (`docker/bedrock-proxy/app/config.py:52-54`). Its access log structurally cannot emit the token or the body (`docker/bedrock-proxy/app/proxy.py:88`). Timeouts are split: connect 10 s / read 600 s / write 60 s / pool 10 s (`docker/bedrock-proxy/app/config.py:27-30`). `/healthz` is the one route exempt from token injection (`docker/bedrock-proxy/app/proxy.py:155-156`).

### API auth notes

Both assistant viewsets accept Token, session and Basic auth. `CCAssistantViewSet` requires only `IsAuthenticated` (`nextseek_api/services/cc_assistant.py:450`); `AssistantViewSet` — the surface the agent's sidecar ops and the Family-C shims terminate in — additionally enforces `UserInParticipatingProject` (`nextseek_api/services/assistant.py:421`), a cached SEEK-project-membership check defined at `nextseek_api/services/assistant.py:109`. The session class is `CsrfExemptSessionAuthentication` (`nextseek_api/services/assistant.py:140`), whose `enforce_csrf` is an unconditional no-op (`nextseek_api/services/assistant.py:152-153`) — and note that its docstring's claim that `CsrfViewMiddleware` is "disabled in this project" is **wrong**: the middleware is enabled at `dmac/settings.py:200`. The skip is per-endpoint, not global. WebSocket access, when served under daphne, requires an authenticated session cookie *and* task ownership; the UUID is not a capability token (`nextseek_api/assistant/consumers.py:29-41`).

### Caps & limits

| Limit | Value | Where |
|---|---|---|
| CC per-turn budget | `NEXTSEEK_CC_MAX_BUDGET_USD`, code default **$0.50** (0 omits the flag) | `nextseek_api/cc_assistant/cc_engine.py:73`, applied at `nextseek_api/cc_assistant/cc_engine.py:760-762` |
| CC per-turn agent turns | `NEXTSEEK_CC_MAX_TURNS`, default **50** | `nextseek_api/cc_assistant/cc_engine.py:74` |
| CC wall clock | `min(NEXTSEEK_CC_TIMEOUT_SECONDS, NEXTSEEK_CC_TIMEOUT_HARD_MAX)`, hard max default **180 s** | `nextseek_api/cc_assistant/cc_engine.py:81`, `nextseek_api/cc_assistant/cc_engine.py:84-85`, `nextseek_api/cc_assistant/cc_engine.py:103-113` |
| WS progress poll (daphne only) | 300 ms DB poll | `nextseek_api/assistant/consumers.py:44` |
| Sidecar WS frame | 16 MiB | `docker/cc-runtime/build_context/plugins/nextseek/bin/_sidecar_client.py:27` |
| Sidecar → Django HTTP | 60 s | `docker/ns-sidecar/app/ns_client.py:27` |
| NS REST tool | 90 s, 120 s for advanced search | `chat_nextseek/src/chat_nextseek/helpers/tools/nextseek_api.py:133`, `chat_nextseek/src/chat_nextseek/helpers/tools/nextseek_api.py:135` |
| Proxy request body | 10 MiB | `docker/bedrock-proxy/app/config.py:22` |
| Upload total size | `BATCH_UPLOAD_MAX_TOTAL_BYTES`, default 200 MiB | `nextseek_api/services/cc_assistant.py:862` |

### Persistence (`nextseek_api/assistant/models_db.py`)

Thirteen model classes live in this module across four `assistant_*` tables and nine `eval_*` tables. The four the chat stack writes on a turn:

| Model | Table | Declared at |
|---|---|---|
| `ChatSession` | `assistant_chat_session` | `nextseek_api/assistant/models_db.py:22` |
| `QueryTask` | `assistant_query_task` | `nextseek_api/assistant/models_db.py:64` |
| `TurnLedger` | `assistant_turn_ledger` | `nextseek_api/assistant/models_db.py:90` |
| `CCSessionTranscript` | `assistant_cc_transcript` | `nextseek_api/assistant/models_db.py:341` |

`CCAssistantViewSet` also exposes ownership-checked endpoints for file upload (`nextseek_api/services/cc_assistant.py:848`), upload status (`nextseek_api/services/cc_assistant.py:895`) and listing (`nextseek_api/services/cc_assistant.py:914`), artifact download (`nextseek_api/services/cc_assistant.py:929`) and transcript streaming (`nextseek_api/services/cc_assistant.py:974`).

### Agent image (`docker/cc-runtime/Dockerfile`)

Pins `@anthropic-ai/claude-code@2.1.163` (≥ 2.1.158 required for auto mode on Bedrock, `docker/cc-runtime/Dockerfile:30-31`), runs as a non-root uid-1001 user (`docker/cc-runtime/Dockerfile:46`, `docker/cc-runtime/Dockerfile:132`), bakes a `CLAUDE.md` at `/app/CLAUDE.md` (`docker/cc-runtime/Dockerfile:64`) symlinked into the agent home (`docker/cc-runtime/Dockerfile:80-81`), and sets `WORKDIR /home/user` (`docker/cc-runtime/Dockerfile:133`) so Claude Code discovers it. `chat_nextseek` is deliberately absent — the image installs only its own dependency manifest (`docker/cc-runtime/pyproject.toml:7-24`) via `uv sync --locked` (`docker/cc-runtime/Dockerfile:97-99`).

---

## Directory map

Every boundary below owns a committed `README.md` + `CLAUDE.md` pair, written against source with citations verified line by line. Those pairs — not this file — are the authority for what is inside each boundary. This map exists to say **where a thing lives and which pair to open**, nothing more.

| Boundary | Role in Nessie |
|---|---|
| [`api_app/`](api_app/README.md) | the original REST API app, kept after the surface moved |
| [`build_tools/`](build_tools/README.md) | generators for the committed-but-generated op surfaces |
| [`chat_frontend/`](chat_frontend/README.md) | the React chat UI (embedded entry `src/main.embedded.tsx`) |
| [`chat_nextseek/`](chat_nextseek/README.md) | the deterministic NS engine — orchestrator, agents, tools, config |
| [`ci/`](ci/README.md) | the single route declaration and the gates over it |
| [`dmac/`](dmac/README.md) | the Django project package: settings, root URLconf, ASGI, SEEK login |
| [`dmac_assistant/`](dmac_assistant/README.md) | the vendored router package: BAML clients, model-class map |
| [`docker/`](docker/README.md) | six build contexts, nginx config, the app image's entrypoint |
| [`nessie_tests/`](nessie_tests/README.md) | the router-aware end-to-end harness |
| [`nextseek_api/`](nextseek_api/README.md) | the app owning every `/nextseek_api/` URL |
| [`nextseek_api/assay_registration/`](nextseek_api/assay_registration/README.md) | batch assay membership registration |
| [`nextseek_api/assistant/`](nextseek_api/assistant/README.md) | shared library: ORM models, wire contract, granular ops, WS consumer |
| [`nextseek_api/attributes/`](nextseek_api/attributes/README.md) | the native attribute API |
| [`nextseek_api/batch_upload/`](nextseek_api/batch_upload/README.md) | the bulk sample-ingest pipeline |
| [`nextseek_api/cc_assistant/`](nextseek_api/cc_assistant/README.md) | the route decision and the per-turn CC sandbox |
| [`nextseek_api/eval/`](nextseek_api/eval/README.md) | the HiBayes evaluation pipeline behind posterior routing |
| [`nextseek_api/schema_rag/`](nextseek_api/schema_rag/README.md) | OpenAPI → per-session DuckDB retrieval |
| [`nextseek_api/services/`](nextseek_api/services/README.md) | the ViewSet and service layer, including both chat viewsets |
| [`scripts/`](scripts/README.md) | one-off operational programs, not a package |
| [`seek/`](seek/README.md) | the SEEK-schema mirror app and most server-rendered pages |
| [`startup/`](startup/README.md) | the bring-up CLI and the data bring-up installs |
| [`themes/`](themes/README.md) | the server-rendered chrome (and the dead repo-root `templates/`) |

Two cross-boundary facts worth carrying here:

- `chat_nextseek/` and `dmac_assistant/` are first-party, in-tree packages installed as **editable** path dependencies (`pyproject.toml:136`, `pyproject.toml:139`, declared as project dependencies at `pyproject.toml:121` and `pyproject.toml:125`), so the running venv imports this repository's source directly rather than a divergent site-packages copy (`pyproject.toml:134-135`).
- `nextseek_api/services/` has **no `__init__.py`** and resolves as a PEP 420 namespace package, which is why a tool that walks it by import can come back empty. (Established by listing the directory on 2026-09-03: no `__init__.py`, while every other package directory under `nextseek_api/` has one. The consequences are written up in `nextseek_api/services/README.md`.)

---

## Glossary

- **Nessie** — the whole assistant: chat UI + router + NS and CC execution paths.
- **NS route** (`nextseek_query`) — the in-process path: `chat_nextseek` runs inside a Django worker thread (`nextseek_api/cc_assistant/router.py:30`).
- **CC route** (`container_cc`) — the Container-CC path: one sandboxed Claude Code container per turn, always Opus (`nextseek_api/cc_assistant/router.py:31`).
- **chat_nextseek** — NExtSEEK's assistant engine; powers both the NS route and the server side of the agent's sidecar ops.
- **dmac_assistant** — the vendored router package: BAML functions (`RouteQuery`, `Summarize`), LLM clients (`dmac_assistant/baml_src/clients.baml:15`, `dmac_assistant/baml_src/clients.baml:26`), model-class map.
- **BAML** — the typed prompt/function layer used to call the routing and summarization LLMs (both Gemini via `GCP_API_KEY`).
- **posterior routing** — the third, feature-flagged route strategy that consults an evaluation posterior before the BAML router (`nextseek_api/cc_assistant/posterior_selector.py:37`).
- **auto mode** — Claude Code `--permission-mode auto` (`nextseek_api/cc_assistant/cc_engine.py:144`): a classifier gates each tool call; not the same as skipping permissions.
- **stream-json** — Claude Code's line-delimited JSON stdin/stdout protocol; translated to UI events by `nextseek_api/cc_assistant/translate.py:62`.
- **dmac-cc-net** — the internal, gateway-less Docker network holding the agent, sidecar and bedrock-proxy (`docker-compose.yml:495`); nginx is its only bridge to NExtSEEK.
- **dmac-cc-egress** — the egress-capable network carrying only `bedrock-proxy`, so AWS stays reachable while the agent does not (`docker-compose.yml:498-499`).
- **dmac-cc-users** — the single external named volume holding all per-user/per-project CC trees (`docker-compose.yml:530-531`).
- **sidecar** (`nextseek-sidecar`) — stateless WS-to-REST forwarder for the agent's nine chat_nextseek-backed ops; can write only to `_staging` (`docker-compose.yml:179-183`).
- **bedrock-proxy** — the only component in the CC subsystem holding an AWS credential; Opus-only, path-allowlisted model relay.
- **staging sweep** — the trusted Django-side move of agent-produced artifacts out of `_staging/sha256(user)/` (`nextseek_api/cc_assistant/cc_staging.py:253`).
- **cc-state** — the per-session `.claude` directory mounted RW into the agent (`nextseek_api/cc_assistant/cc_engine.py:975-978`), enabling `--resume` and providing the raw turn transcript.
- **QueryTask** — the DB row that doubles as the progress event log (`nextseek_api/assistant/models_db.py:64`); both the WS consumer and the HTTP polling endpoint read from it.
- **cc_session_id** — Claude's own in-container session UUID, distinct from Nessie's `ChatSession` id.

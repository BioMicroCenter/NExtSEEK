"""Endpoint description constants for CC Assistant ViewSet OpenAPI copy."""

CC_ASSISTANT_QUERY_ASYNC_DESC = """
**SUMMARY:** Router-dispatched async CC assistant query.

**USE WHEN:** The user wants a chat turn routed between the deterministic NExtSEEK
pipeline and the sandboxed Container-Claude-Code agent.

**ACCEPTS:** JSON body with a `query` string (see QueryRequest).

**RETURNS:** HTTP 202 with `task_id` and `session_id`; stream progress on
`ws/assistant/progress/{task_id}/`.

**TRIGGER PHRASES:** routed async query, cc assistant query, router query

**EXAMPLES:**
- 'Find me mice treated with NDMA' (routed query body)
"""

CC_ASSISTANT_CC_QUERY_ASYNC_DESC = """
**SUMMARY:** Force the Container-Claude-Code route (bypass the router).

**USE WHEN:** You need a sandboxed claude container turn without BAML routing.

**ACCEPTS:** JSON body with a `query` string (see QueryRequest).

**RETURNS:** HTTP 202 with `task_id` and `session_id`; progress on the assistant
websocket (poll fallback available on tasks/{task_id}/progress/).

**TRIGGER PHRASES:** force container cc, cc query async, bypass router

**EXAMPLES:**
- 'Find me mice treated with NDMA' (force-CC query body)
"""

CC_ASSISTANT_TASK_PROGRESS_DESC = """
**SUMMARY:** Poll a routed or CC task's progress (HTTP fallback).

**USE WHEN:** The websocket progress channel is unavailable and you need the
same TaskProgressResponse shape as the legacy assistant.

**ACCEPTS:** Path parameter `task_id` (UUID) for a task owned by the caller.

**RETURNS:** HTTP 200 TaskProgressResponse with status, progress events, and
result when terminal.

**TRIGGER PHRASES:** task progress poll, cc task status, query task progress

**EXAMPLES:**
- Poll task `550e8400-e29b-41d4-a716-446655440000` while status is running
"""

NESSIE_QUERY_ASYNC_DESC = (
    "**SUMMARY:** Submit a chat turn and let the router pick the engine. Returns a task id immediately; progress arrives over "
    "`ws/assistant/progress/{task_id}/` or the polling fallback.\n\n"
    "**USE WHEN:** Any normal Nessie turn. The BAML router decides between the deterministic NExtSEEK pipeline and the sandboxed "
    "Container-Claude-Code agent, and two guards may still redirect an NS-bound turn: an open pipeline wizard keeps it on NExtSEEK, and a "
    "chat whose previous turn ran on CC keeps it on CC.\n\n"
    "**DO NOT USE WHEN:** The caller wants a specific engine regardless of the query — post to `cc/query/async/` to force Container-CC, or "
    "send `force_route` as a superuser.\n\n"
    "**ACCEPTS:** A `QueryRequest`: `query` (required), `mode`, optional `session_id` (must belong to the caller), `force_new`, and the "
    "superuser-only `force_route` and `max_turn_length_s`. `extra='forbid'`, so an unknown key is a 422. With no `session_id` the "
    "turn always opens a new chat, never the caller's most recent one, so `force_new` changes nothing here; send the returned "
    "`session_id` to continue a chat.\n\n"
    "**RETURNS:** `202` with `{task_id, session_id}`.\n\n"
    "**ERROR CODES:** `401` when unauthenticated; `404` when `session_id` names a session the caller does not own; `422` on an invalid body.\n\n"
    "**TRIGGER PHRASES:** ask nessie, chat turn, run a query, send a message to the assistant\n\n"
    "**EXAMPLES:**\n"
    "- 'Find me mice treated with NDMA'\n"
    "- 'Which of those samples are CD8 depleted?'\n"
)

NESSIE_CC_QUERY_ASYNC_DESC = (
    "**SUMMARY:** Submit a chat turn pinned to the Container-Claude-Code engine, bypassing the router entirely.\n\n"
    "**USE WHEN:** The turn must run on the sandboxed agent regardless of what the router would choose — typically operator debugging, or a "
    "task known to need the agent's shell and filesystem.\n\n"
    "**DO NOT USE WHEN:** A normal turn — use `query/async/` and let the router choose. Forcing the agent costs a container run.\n\n"
    "**ACCEPTS:** The same `QueryRequest` as `query/async/`.\n\n"
    "**RETURNS:** `202` with `{task_id, session_id}`. The turn's `route_decided` event carries `source=\"forced\"`.\n\n"
    "**ERROR CODES:** `401` when unauthenticated; `404` when `session_id` names a session the caller does not own; `422` on an invalid body.\n\n"
    "**NOTE:** This route is open to any authenticated user, deliberately, and is covered by a test that pins it that way. The superuser gate "
    "on `force_route` therefore constrains nothing for the `cc` value: the same forced decision is one URL away. Do not 'fix' the asymmetry by "
    "gating this route without replacing the capability.\n\n"
    "**TRIGGER PHRASES:** force container cc, run this on the agent, bypass the router\n\n"
    "**EXAMPLES:**\n"
    "- 'Force this onto the Container-CC agent: summarise the run directory'\n"
)

NESSIE_TASK_PROGRESS_DESC = (
    "**SUMMARY:** Poll one chat turn's progress events and, once terminal, its result.\n\n"
    "**USE WHEN:** The websocket is unavailable or a non-browser client is driving a turn. The progress list is append-only, so a caller "
    "tracks how many events it has already seen and reads the tail.\n\n"
    "**DO NOT USE WHEN:** A browser client that can hold `ws/assistant/progress/{task_id}/` open — the websocket is the primary channel.\n\n"
    "**ACCEPTS:** `task_id` as a path parameter.\n\n"
    "**RETURNS:** `200` with `{task_id, session_id, status, progress[], result}`. `result` is populated only once `status` is `completed` or "
    "`error`.\n\n"
    "**ERROR CODES:** `401` when unauthenticated; `404` when the task does not exist, or belongs to another user and the caller is not a "
    "Django superuser.\n\n"
    "**TRIGGER PHRASES:** poll task, is my query done, task progress, check turn status\n\n"
    "**EXAMPLES:**\n"
    "- 'Is task 4a5c12ad-9063-4df1-8439-e201b36bedaf finished yet?'\n"
)

NESSIE_UPLOAD_DESC = (
    "**SUMMARY:** Stage one or more files into the caller's Container-CC input directory, asynchronously.\n\n"
    "**USE WHEN:** The agent needs local files to work on — a samplesheet, a set of reads, a metadata workbook.\n\n"
    "**DO NOT USE WHEN:** Registering samples in NExtSEEK — that is the batch-upload surface, not this one.\n\n"
    "**ACCEPTS:** `multipart/form-data` with one or more `file` parts. The batch is capped by `BATCH_UPLOAD_MAX_TOTAL_BYTES` (200 MB by "
    "default), every filename is validated, and a name repeated within one batch is rejected.\n\n"
    "**RETURNS:** `202` with `{job_id, status}`. Poll `uploads/{job_id}/` for progress.\n\n"
    "**ERROR CODES:** `400` when no file is sent or a filename repeats; `413` when the batch exceeds the cap; `503` when the caller's SEEK "
    "project cannot be resolved.\n\n"
    "**TRIGGER PHRASES:** upload a file, stage inputs, send files to the agent\n\n"
    "**EXAMPLES:**\n"
    "- 'Upload these FASTQs so the agent can see them'\n"
)

NESSIE_UPLOAD_LIST_DESC = (
    "**SUMMARY:** List the files currently staged in the caller's Container-CC input directory.\n\n"
    "**USE WHEN:** Confirming what the agent can see before asking it to work on something.\n\n"
    "**ACCEPTS:** No request body.\n\n"
    "**RETURNS:** `200` with `{files: [...]}`.\n\n"
    "**ERROR CODES:** `503` when the caller's SEEK project cannot be resolved.\n\n"
    "**TRIGGER PHRASES:** what files are staged, list my uploads, what can the agent see\n\n"
    "**EXAMPLES:**\n"
    "- 'What have I uploaded so far?'\n"
)

NESSIE_UPLOAD_STATUS_DESC = (
    "**SUMMARY:** Poll one staged upload job.\n\n"
    "**USE WHEN:** After `POST uploads/` returns a `job_id`, to watch it finish.\n\n"
    "**ACCEPTS:** `job_id` as a path parameter.\n\n"
    "**RETURNS:** `200` with `{job_id, state, meta, result}`. `state` is the Celery state; `meta` carries progress while running and the error "
    "string on failure.\n\n"
    "**ERROR CODES:** `404` when the job does not exist, or belongs to another user and the caller is not a Django superuser.\n\n"
    "**TRIGGER PHRASES:** upload progress, is my upload done, job status\n\n"
    "**EXAMPLES:**\n"
    "- 'Has my file upload finished?'\n"
)

NESSIE_ARTIFACTS_DESC = (
    "**SUMMARY:** Download a file the Container-CC agent produced for one chat session.\n\n"
    "**USE WHEN:** Retrieving an output the agent wrote — a workbook, a report, a plot.\n\n"
    "**DO NOT USE WHEN:** Fetching a NExtSEEK-engine turn's artifact — that lives under "
    "`assistant/sessions/{session_id}/bundles/{bundle_id}/artifacts/{artifact_key}/`.\n\n"
    "**ACCEPTS:** `session` as a path parameter, plus `?key=` naming one artifact, or `?key=all&turn_id=` to receive that turn's whole output "
    "directory as a zip. Keys are containment-checked against the session's artifact directory.\n\n"
    "**RETURNS:** `200` with the file as an attachment, or a `application/zip` stream for `key=all`.\n\n"
    "**ERROR CODES:** `404` when the session, key or file does not exist, or the session belongs to another user and the caller is not a "
    "Django superuser; `503` when the caller's SEEK project cannot be resolved.\n\n"
    "**TRIGGER PHRASES:** download the output, get the agent's file, fetch artifact, download all outputs\n\n"
    "**EXAMPLES:**\n"
    "- 'Download the workbook the agent generated'\n"
)

NESSIE_TRANSCRIPT_DESC = (
    "**SUMMARY:** Download one Container-CC turn's Claude Code transcript as jsonl.\n\n"
    "**USE WHEN:** Diagnosing what the agent actually did on a turn — which tools it called, and in what order.\n\n"
    "**DO NOT USE WHEN:** You do not yet know the turn id. `GET nessie/sessions/{session_id}/debug/` lists every stored transcript for a "
    "session with its size, which is the way to find one.\n\n"
    "**ACCEPTS:** `session` and `turn` as path parameters, plus `?cc_session_id=` when several transcripts share a turn id.\n\n"
    "**RETURNS:** `200` with `application/x-ndjson` as an attachment, decompressed under `CC_TRANSCRIPT_MAX_BYTES`.\n\n"
    "**ERROR CODES:** `400` when several rows match and no `cc_session_id` disambiguates them; `404` when the session or transcript does not "
    "exist, or the session belongs to another user and the caller is not a Django superuser.\n\n"
    "**TRIGGER PHRASES:** agent transcript, what did the agent do, claude code jsonl, turn transcript\n\n"
    "**EXAMPLES:**\n"
    "- 'Show me the agent transcript for turn 3 of this session'\n"
)

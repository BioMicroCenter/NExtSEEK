"""
LLM-optimized endpoint description constants for the Assistant ViewSet.

Follows the project pattern: SUMMARY > USE WHEN > ACCEPTS > RETURNS > TRIGGER PHRASES > EXAMPLES
"""

ASSISTANT_ME_DESC = (
    "**SUMMARY:** Return the authenticated user's username and admin status.\n\n"
    "**USE WHEN:** The client needs to display who is logged in or check admin privileges.\n\n"
    "**ACCEPTS:** No request body. Requires authentication (Basic, Session, or Token).\n\n"
    "**RETURNS:** `{username, is_admin}`.\n\n"
    "**TRIGGER PHRASES:** who am I, current user, my profile\n\n"
    "**EXAMPLES:**\n"
    "- `GET /nextseek_api/assistant/me/`\n"
)

ASSISTANT_SESSION_CREATE_DESC = (
    "**SUMMARY:** Create a new chat session for the authenticated user.\n\n"
    "**USE WHEN:** Starting a new conversation with the NExtSEEK assistant.\n\n"
    "**ACCEPTS:** No request body. Requires authentication.\n\n"
    "**RETURNS:** `{session_id, created_at}` with HTTP 201.\n\n"
    "**TRIGGER PHRASES:** new session, start conversation, create chat\n\n"
    "**EXAMPLES:**\n"
    "- `POST /nextseek_api/assistant/sessions/`\n"
)

ASSISTANT_SESSION_DETAIL_DESC = (
    "**SUMMARY:** Get metadata for an existing chat session.\n\n"
    "**USE WHEN:** The client needs session info (query count, creation time).\n\n"
    "**ACCEPTS:** `session_id` as a path parameter (UUID). Requires session ownership.\n\n"
    "**RETURNS:** `{session_id, created_at, query_count, has_results}`.\n\n"
    "**TRIGGER PHRASES:** session info, session details, session status\n\n"
    "**EXAMPLES:**\n"
    "- `GET /nextseek_api/assistant/sessions/abc123-def456/`\n"
)

ASSISTANT_QUERY_DESC = (
    "**SUMMARY:** Submit a natural language query and receive streamed SSE responses "
    "as the multi-agent pipeline processes it.\n\n"
    "**USE WHEN:** The user types a question about NExtSEEK data.\n\n"
    "**ACCEPTS:** `{query}` (required) and optionally `{session_id}` (UUID).\n"
    "- If `session_id` is omitted, the server reuses the user's most recently updated session "
    "or auto-creates a new one.\n"
    "- If `session_id` is provided, it must belong to the authenticated user.\n\n"
    "**RETURNS:** `text/event-stream` with events: `agent_started`, `agent_complete`, "
    "`query_complete`, `query_error`. The `query_complete` event includes `session_id` "
    "so the client can reference it in future requests.\n\n"
    "**TRIGGER PHRASES:** ask question, search data, query NExtSEEK, find samples\n\n"
    "**EXAMPLES:**\n"
    '- `POST /nextseek_api/assistant/query/` with `{"query": "Find me mice treated with NDMA"}`\n'
    '- `POST /nextseek_api/assistant/query/` with `{"session_id": "...", "query": "Which of those are CD8 depleted?"}`\n'
)

ASSISTANT_BUNDLE_DOWNLOAD_DESC = (
    "**SUMMARY:** Download a specific result bundle from a chat session.\n\n"
    "**USE WHEN:** The user wants to download results from a previous query.\n\n"
    "**ACCEPTS:** `session_id` and `bundle_id` as path parameters. Optional `?format=json`.\n\n"
    "**RETURNS:** JSON bundle data with query results, API plans, and debug info.\n\n"
    "**TRIGGER PHRASES:** download results, get bundle, export query results\n\n"
    "**EXAMPLES:**\n"
    "- `GET /nextseek_api/assistant/sessions/abc123/bundles/1/`\n"
)

ASSISTANT_TEST_CASES_DESC = (
    "**SUMMARY:** List all configured test cases for the NExtSEEK assistant.\n\n"
    "**USE WHEN:** An admin wants to see available test prompts for validation.\n\n"
    "**ACCEPTS:** No request body. Requires admin authentication.\n\n"
    "**RETURNS:** `{total, test_cases: [{id, prompt}, ...]}`.\n\n"
    "**TRIGGER PHRASES:** test cases, test prompts, validation cases\n\n"
    "**EXAMPLES:**\n"
    "- `GET /nextseek_api/assistant/test-cases/`\n"
)

ASSISTANT_QUERY_ASYNC_DESC = (
    "**SUMMARY:** Submit a natural language query asynchronously. Returns a task ID "
    "immediately so the client can subscribe to real-time progress updates via "
    "WebSocket or polling.\n\n"
    "**USE WHEN:** The frontend needs real-time progress updates (agent stepper) "
    "while the multi-agent pipeline runs.\n\n"
    "**ACCEPTS:** `{query}` (required) and optionally `{session_id}` (UUID).\n"
    "- If `session_id` is omitted, the server reuses the most recently updated session "
    "or auto-creates a new one.\n\n"
    "**RETURNS:** `{task_id, session_id}` with HTTP 202. The pipeline runs in the background.\n\n"
    "**PROGRESS:** Connect via WebSocket `ws://host/ws/assistant/progress/{task_id}/` "
    "or poll `GET /assistant/tasks/{task_id}/progress/`.\n\n"
    "**TRIGGER PHRASES:** async query, submit query async, background query, task progress\n\n"
    "**EXAMPLES:**\n"
    '- `POST /nextseek_api/assistant/query/async/` with `{"query": "Find me mice treated with NDMA"}`\n'
)

ASSISTANT_TASK_PROGRESS_DESC = (
    "**SUMMARY:** Get the current progress and result of an async query task.\n\n"
    "**USE WHEN:** Polling for progress updates on a query submitted via "
    "`POST /assistant/query/async/`.\n\n"
    "**ACCEPTS:** `task_id` as a path parameter (UUID). Requires task ownership.\n\n"
    "**RETURNS:** `{task_id, session_id, status, progress, result}`.\n"
    "- `status`: `pending` | `running` | `completed` | `error`\n"
    "- `progress`: array of `{event, data}` objects (agent_started, agent_complete, etc.)\n"
    "- `result`: final payload (only set when status is completed or error)\n\n"
    "**TRIGGER PHRASES:** task progress, poll query status, async task status\n\n"
    "**EXAMPLES:**\n"
    "- `GET /nextseek_api/assistant/tasks/abc123-def456/progress/`\n"
)

ASSISTANT_SESSIONS_LIST_DESC = """
List the current user's chat sessions, ordered by most-recently-updated first.
Returns at most 50 rows. Each row carries an id, display title (or "New chat"
if untitled), timestamps, the count of completed query bundles, and an 80-char
preview derived from the first user query in the session's results_history.
"""

ASSISTANT_SESSION_DELETE_DESC = """
Permanently delete a chat session and all associated QueryTask rows.
Returns 204 on success, 404 if the session is missing, 403 if the
caller does not own it.
"""

ASSISTANT_SESSION_PATCH_DESC = """
Rename a chat session. Body: {"title": <string, 1..200 chars>}.
The title is trimmed; empty-after-trim is rejected with 422.
Returns the updated row in SessionListItem shape.
"""

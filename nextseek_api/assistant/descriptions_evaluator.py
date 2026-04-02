"""
LLM-optimized endpoint description constants for the Evaluator ViewSet.

Follows the project pattern: SUMMARY > USE WHEN > ACCEPTS > RETURNS > EXAMPLES
"""

EVALUATOR_RETRY_CONTEXT_BY_TASK_DESC = (
    "**SUMMARY:** Get normalized retry context for a specific async query task.\n\n"
    "**USE WHEN:** The evaluator client needs to inspect a current or recent run "
    "identified by its task_id, and potentially prepare a retry.\n\n"
    "**ACCEPTS:** `task_id` as a path parameter (UUID). Admin-only.\n\n"
    "**RETURNS:** Normalized evaluator payload with sections: `lookup`, `run`, "
    "`routing`, `retry_context`, and `raw` (embedded source payloads).\n\n"
    "**EXAMPLES:**\n"
    "- `GET /nextseek_api/evaluator/tasks/abc123-def456/retry-context/`\n"
)

EVALUATOR_RETRY_CONTEXT_BY_BUNDLE_DESC = (
    "**SUMMARY:** Get normalized retry context for a historical bundle "
    "within a chat session.\n\n"
    "**USE WHEN:** The evaluator client needs to inspect a persisted result bundle "
    "identified by session_id and bundle_id.\n\n"
    "**ACCEPTS:** `session_id` (UUID) and `bundle_id` (int) as path parameters. "
    "Admin-only.\n\n"
    "**RETURNS:** Same normalized evaluator payload as the task-based endpoint.\n\n"
    "**EXAMPLES:**\n"
    "- `GET /nextseek_api/evaluator/sessions/abc123/bundles/1/retry-context/`\n"
)

EVALUATOR_RUNS_LIST_DESC = (
    "**SUMMARY:** List and filter historical query task runs for evaluator analysis.\n\n"
    "**USE WHEN:** The evaluator client needs to browse, search, or filter past runs "
    "for retry analysis and debugging.\n\n"
    "**ACCEPTS:** Query parameters for filtering: `session_id`, `task_id`, `status`, "
    "`has_bundle`, `created_after`, `created_before`, `user_id`. "
    "Supports pagination via `page` and `page_size`. Admin-only.\n\n"
    "**RETURNS:** Paginated list of `{task_id, session_id, status, query, has_bundle, "
    "user_id, created_at}` summary rows.\n\n"
    "**EXAMPLES:**\n"
    "- `GET /nextseek_api/evaluator/runs/`\n"
    "- `GET /nextseek_api/evaluator/runs/?status=completed&has_bundle=true`\n"
    "- `GET /nextseek_api/evaluator/runs/?user_id=1&created_after=2026-01-01`\n"
)

EVALUATOR_RETRY_EXECUTE_DESC = (
    "**SUMMARY:** Submit a retry query through the existing assistant pipeline, "
    "reusing the original chat session.\n\n"
    "**USE WHEN:** The evaluator client has inspected a run via the retry-context "
    "endpoints and wants to submit a corrected or refined query.\n\n"
    "**ACCEPTS:** JSON body with `query` (required), `mode` (required), and source "
    "identifiers: either `task_id` OR `session_id` + `bundle_id`. Admin-only.\n\n"
    "**RETURNS:** `{task_id, session_id, source_task_id, source_session_id, "
    "source_bundle_id, credential_source}` with HTTP 202.\n\n"
    "**NOTE:** Retry uses the admin caller's Basic auth credentials for SEEK API calls. "
    "If the admin authenticates via Token auth, the system falls back to service account "
    "credentials (API_USER/API_PASS env vars). The `credential_source` field indicates "
    "which credentials were used.\n\n"
    "**EXAMPLES:**\n"
    '- `POST /nextseek_api/evaluator/retry/` with '
    '`{"task_id": "abc123", "query": "Find mice treated with NDMA", "mode": "standard"}`\n'
)

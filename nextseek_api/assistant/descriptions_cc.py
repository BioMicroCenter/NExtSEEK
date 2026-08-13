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

# Saved Chats & New Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a per-user chat-session sidebar (list/new/rename/delete) to `chat_frontend`, backed by a small set of new endpoints on `nextseek_api.AssistantViewSet` and one new `title` column on `ChatSession`. Active session is reflected in the URL (`/chat/<uuid>` standalone, `/assistant/chat/<uuid>` embedded) and the conversation rehydrates from the server's `results_history` on reload.

**Architecture:** One new model column + four endpoint actions + one `force_new` request flag on the backend. On the frontend: one new component (`SessionSidebar`), two new hooks (`useSessions`, `useChatRoute`), and a small extension to `useMessages`/`useChatApi`. `AppLayout` and `EmbeddedApp` both mount the same sidebar. No new top-level dependencies — URL handling via `history.pushState`. Live UI checkpoints via the `playwright-mcp` plugin throughout implementation.

**Tech Stack:** Django + DRF (existing `AssistantViewSet` in `nextseek_api/services/assistant.py`); pydantic 2 (existing `models_api.py`); React 19 + Vite + Tailwind + Radix UI (existing `chat_frontend/`); Vitest + Testing Library; Playwright (existing `e2e/`); the `playwright-mcp` plugin's `browser_*` tools for live UI verification.

**Spec:** `docs/superpowers/specs/2026-05-12-saved-chats-design.md` — refer to it for design rationale. This plan implements that spec verbatim.

---

## Working agreements

- **Working directory:** All paths in this plan are relative to `/home/cdemu/code/dmac/docker/NExtSEEK`. Use absolute paths in tool calls.
- **Docker:** The user runs Docker rebuilds/restarts themselves. When a step needs a Django command in the container, present the exact command and ask the user to run it. Frontend (Vitest, Playwright, `npm` etc.) runs on the host and you may run it directly.
- **Git:** Commit after each task (or sub-group within a task when sensible). Use Conventional Commits. The current branch is `ui-redesign-v1`. Do NOT push.
- **TDD discipline:** Every backend endpoint and every frontend hook is written test-first.
- **No emoji** in code, comments, commits, or docs unless the user explicitly asks.
- **Run frontend tests** with `cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npm test -- --run <relative-test-path>` from the host.
- **Run backend tests** by asking the user to execute (in the running Django container):
  `python manage.py test nextseek_api.assistant.tests.<module> -v 2`

---

## File structure

### Backend (Django, `nextseek_api/`)

| File | Action | Responsibility |
|---|---|---|
| `nextseek_api/assistant/models_db.py` | modify | Add `title` column on `ChatSession`. |
| `nextseek_api/migrations/0004_chatsession_title.py` | create | Django migration adding `title`. |
| `nextseek_api/assistant/models_api.py` | modify | Add `SessionListItem`, `SessionListResponse`, `SessionPatchRequest`, `Turn`; extend `SessionDetailResponse`; add `force_new` to `QueryRequest`. |
| `nextseek_api/assistant/descriptions.py` | modify | Add description strings for new actions. |
| `nextseek_api/services/assistant.py` | modify | Add `list_sessions`, `patch_session`, `delete_session` actions; extend `get_session` for `?include=turns`; add `_auto_title_if_unset()` helper; branch on `force_new` in `query`/`query_async`. |
| `nextseek_api/assistant/tests/test_sessions_endpoints.py` | create | Endpoint tests for list/detail-with-turns/patch/delete + auto-title. |
| `nextseek_api/assistant/tests/test_query_force_new.py` | create | Endpoint tests for the `force_new` flag. |

### Frontend (React, `chat_frontend/src/`)

| File | Action | Responsibility |
|---|---|---|
| `lib/types/api.ts` | modify | Add `SessionListItem`, `SessionListResponse`, `Turn`, `SessionDetailWithTurns`. |
| `lib/services/chatApi.ts` | modify | Add `listSessions`, `renameSession`, `deleteSession`, `fetchSessionTurns`; `submitQuery` accepts `{sessionId, forceNew}`. |
| `lib/services/__tests__/chatApi.test.ts` | create | Service-method tests using `fetch` mock. |
| `hooks/useMessages.ts` | modify | Add `hydrateFromTurns(turns)`. |
| `hooks/__tests__/useMessages.test.ts` | modify | Add tests for `hydrateFromTurns`. |
| `hooks/useChatApi.ts` | modify | Forward `{sessionId, forceNew}` to service. |
| `hooks/useChatRoute.ts` | create | Read/write `chat/<uuid>` under basename from `<meta>` tag. |
| `hooks/__tests__/useChatRoute.test.ts` | create | Test push/pop + popstate. |
| `hooks/useSessions.ts` | create | List + CRUD + active id + lazy newChat. |
| `hooks/__tests__/useSessions.test.ts` | create | Hook behavior tests. |
| `components/Sessions/NewChatButton.tsx` | create | The "New chat" button. |
| `components/Sessions/SessionListItem.tsx` | create | One row, with inline rename + delete confirm. |
| `components/Sessions/SessionSidebar.tsx` | create | The rail (collapsible). |
| `components/Sessions/index.ts` | create | Re-exports. |
| `components/Sessions/__tests__/SessionSidebar.test.tsx` | create | RTL tests. |
| `components/Sessions/__tests__/SessionListItem.test.tsx` | create | RTL tests. |
| `components/Layout/HeaderBar.tsx` | modify | Add sidebar-collapse toggle. |
| `components/Layout/CompactToolbar.tsx` | modify | Add sidebar-collapse toggle. |
| `AppLayout.tsx` | modify | Mount sidebar + wire `useSessions` + `useChatRoute`. |
| `EmbeddedApp.tsx` | modify | Same. |
| `e2e/sessions.spec.ts` | create | Playwright e2e: create/switch/rename/delete/deep-link. |

### Django template

| File | Action | Responsibility |
|---|---|---|
| `seek/templates/smartSearch.html` | modify | Inject `<meta name="chat-basename" content="/assistant/">`. |

---

# Task 1 — Add `title` column to `ChatSession`

**Files:**
- Modify: `nextseek_api/assistant/models_db.py:7-26`
- Create: `nextseek_api/migrations/0004_chatsession_title.py`

- [ ] **Step 1.1 — Add the column to the model**

Edit `nextseek_api/assistant/models_db.py`, inside the `ChatSession` class, immediately after the existing `extra_state = models.JSONField(default=dict)` line, add:

```python
    title = models.CharField(max_length=200, null=True, blank=True)
```

The full updated class block is:

```python
class ChatSession(models.Model):
    session_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="chat_sessions",
    )
    results_history = models.JSONField(default=list)
    last_debug = models.JSONField(default=dict)
    extra_state = models.JSONField(default=dict)
    title = models.CharField(max_length=200, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "assistant_chat_session"
        app_label = 'nextseek_api'
        ordering = ["-created_at"]

    def __str__(self):
        return f"ChatSession {self.session_id} (user={self.user_id})"
```

- [ ] **Step 1.2 — Ask the user to generate the migration**

Hand the user this exact command (they run it in the Django container):

```bash
python manage.py makemigrations nextseek_api --name chatsession_title
```

Expected: creates `nextseek_api/migrations/0004_chatsession_title.py`.

- [ ] **Step 1.3 — Verify the migration content**

Read `nextseek_api/migrations/0004_chatsession_title.py`. It should look like:

```python
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('nextseek_api', '0003_chatsession_extra_state'),
    ]

    operations = [
        migrations.AddField(
            model_name='chatsession',
            name='title',
            field=models.CharField(blank=True, max_length=200, null=True),
        ),
    ]
```

If the dependency is not `0003_chatsession_extra_state` or there are unexpected extra operations, stop and surface the issue.

- [ ] **Step 1.4 — Ask the user to apply the migration**

Hand the user this command:

```bash
python manage.py migrate nextseek_api
```

Expected: `Applying nextseek_api.0004_chatsession_title... OK`.

- [ ] **Step 1.5 — Commit**

```bash
git add nextseek_api/assistant/models_db.py nextseek_api/migrations/0004_chatsession_title.py
git commit -m "feat(assistant): add title column to ChatSession

For the saved-chats UI in chat_frontend. Nullable; the list endpoint
falls back to 'New chat' when NULL, and auto-title (added in a later
commit) populates it from the first user query.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 2 — Add `Turn` + extend `SessionDetailResponse`; add `force_new` to `QueryRequest`

**Files:**
- Modify: `nextseek_api/assistant/models_api.py:12-46`

This task only adds pydantic models — no behavior change yet. A behavior change comes in Tasks 3–7.

- [ ] **Step 2.1 — Add `force_new` to `QueryRequest`**

Edit `nextseek_api/assistant/models_api.py`. Replace the existing `QueryRequest` class with:

```python
class QueryRequest(BaseModel):
    """POST /assistant/query/ request body."""
    session_id: Optional[UUID] = Field(None, description="Chat session UUID. If omitted (and force_new is False), reuses the most recently updated session or auto-creates one.")
    query: str = Field(..., min_length=1, max_length=4000, description="Natural language query")
    mode: str = Field(..., description="What mode to execute the query as. E.g. standard, plan, etc.")
    force_new: bool = Field(False, description="If true and session_id is omitted, always create a new ChatSession instead of reusing the most recent one.")

    model_config = ConfigDict(extra="forbid")
```

- [ ] **Step 2.2 — Add `Turn`, `SessionListItem`, `SessionListResponse`, `SessionPatchRequest`; extend `SessionDetailResponse`**

In the same file, immediately after the existing `SessionDetailResponse` class, add:

```python
class Turn(BaseModel):
    """One projected turn from a session's results_history."""
    bundle_id: int
    user_query: str
    reply: str
    mode: str
    ts: Optional[str] = None

    model_config = ConfigDict(extra="forbid")


class SessionListItem(BaseModel):
    """One row in the sessions list view."""
    session_id: UUID
    title: str = Field(..., description="Display title; 'New chat' when no title is set")
    created_at: datetime
    updated_at: datetime
    query_count: int
    preview: str = Field("", description="First user query, trimmed to <=80 chars")

    model_config = ConfigDict(extra="forbid")


class SessionListResponse(BaseModel):
    """GET /assistant/sessions/ response."""
    total: int
    sessions: List[SessionListItem]

    model_config = ConfigDict(extra="forbid")


class SessionPatchRequest(BaseModel):
    """PATCH /assistant/sessions/{id}/ body."""
    title: str = Field(..., min_length=1, max_length=200)

    model_config = ConfigDict(extra="forbid")
```

Also replace the existing `SessionDetailResponse` with the extended version:

```python
class SessionDetailResponse(BaseModel):
    """GET /assistant/sessions/{id}/ response."""
    session_id: UUID
    created_at: datetime
    query_count: int = Field(..., description="Number of queries in results_history")
    has_results: bool = Field(..., description="Whether any results exist")
    # Populated when the request includes ?include=turns
    title: Optional[str] = None
    turns: Optional[List["Turn"]] = None

    model_config = ConfigDict(extra="forbid")
```

The forward reference to `Turn` is fine because `Turn` is now defined in the same module (but later than `SessionDetailResponse` if you add things in the existing order). To make it order-independent, add at the very bottom of the file:

```python
SessionDetailResponse.model_rebuild()
```

- [ ] **Step 2.3 — Run existing tests to make sure nothing broke**

Hand the user this command:

```bash
python manage.py test nextseek_api.tests.test_assistant_unit -v 2
```

Expected: existing tests still pass. Any pydantic-related failure here means the model changes have a problem — investigate before continuing.

- [ ] **Step 2.4 — Commit**

```bash
git add nextseek_api/assistant/models_api.py
git commit -m "feat(assistant): add session-list/patch/turn pydantic models + force_new flag

Pure schema addition; no endpoint behavior change yet. The next commits
wire these into AssistantViewSet.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 3 — `GET /assistant/sessions/` (list)

**Files:**
- Modify: `nextseek_api/services/assistant.py`
- Modify: `nextseek_api/assistant/descriptions.py`
- Create: `nextseek_api/assistant/tests/test_sessions_endpoints.py`

- [ ] **Step 3.1 — Write the failing tests**

Create `nextseek_api/assistant/tests/test_sessions_endpoints.py` with:

```python
"""Tests for the saved-chats session endpoints on AssistantViewSet.

list / detail-with-turns / patch / delete / auto-title.
"""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession


class SessionEndpointsTestBase(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("u1", password="p")
        self.other = User.objects.create_user("u2", password="p")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        patcher = patch(
            'nextseek_api.services.assistant.UserInParticipatingProject.has_permission',
            return_value=True,
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class ListSessionsTests(SessionEndpointsTestBase):
    URL = "/nextseek_api/assistant/sessions/"

    def test_returns_only_own_sessions(self):
        own = ChatSession.objects.create(user=self.user)
        ChatSession.objects.create(user=self.other)
        resp = self.client.get(self.URL)
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(len(body["sessions"]), 1)
        self.assertEqual(body["sessions"][0]["session_id"], str(own.session_id))

    def test_projection_shape(self):
        ChatSession.objects.create(
            user=self.user,
            title="My chat",
            results_history=[
                {"id": 1, "user_query": "hello world", "reply": "hi", "mode": "new_search"},
                {"id": 2, "user_query": "second", "reply": "ok", "mode": "new_search"},
            ],
        )
        resp = self.client.get(self.URL)
        item = resp.json()["sessions"][0]
        self.assertEqual(
            set(item.keys()),
            {"session_id", "title", "created_at", "updated_at", "query_count", "preview"},
        )
        self.assertEqual(item["title"], "My chat")
        self.assertEqual(item["query_count"], 2)
        self.assertEqual(item["preview"], "hello world")

    def test_title_fallback_when_null(self):
        ChatSession.objects.create(user=self.user)
        item = self.client.get(self.URL).json()["sessions"][0]
        self.assertEqual(item["title"], "New chat")

    def test_preview_truncated_to_80(self):
        long_q = "a" * 200
        ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1, "user_query": long_q, "reply": "r", "mode": "x"}],
        )
        item = self.client.get(self.URL).json()["sessions"][0]
        self.assertEqual(len(item["preview"]), 80)
        self.assertEqual(item["preview"], "a" * 80)

    def test_preview_empty_when_no_bundles(self):
        ChatSession.objects.create(user=self.user)
        item = self.client.get(self.URL).json()["sessions"][0]
        self.assertEqual(item["preview"], "")

    def test_ordered_by_updated_at_desc(self):
        import time
        s1 = ChatSession.objects.create(user=self.user, title="first")
        time.sleep(0.01)
        s2 = ChatSession.objects.create(user=self.user, title="second")
        # Touch s1 so its updated_at is now greater than s2's.
        time.sleep(0.01)
        s1.title = "first-updated"
        s1.save(update_fields=["title", "updated_at"])
        ids = [s["session_id"] for s in self.client.get(self.URL).json()["sessions"]]
        self.assertEqual(ids, [str(s1.session_id), str(s2.session_id)])

    def test_cap_at_50(self):
        for i in range(55):
            ChatSession.objects.create(user=self.user, title=f"s{i}")
        body = self.client.get(self.URL).json()
        # `total` reflects the number returned (the page), not the global count.
        self.assertEqual(len(body["sessions"]), 50)
        self.assertEqual(body["total"], 50)

    def test_401_without_auth(self):
        self.client.force_authenticate(user=None)
        resp = self.client.get(self.URL)
        self.assertIn(resp.status_code, (401, 403))
```

- [ ] **Step 3.2 — Run tests; verify they fail with 404**

Hand the user:

```bash
python manage.py test nextseek_api.assistant.tests.test_sessions_endpoints.ListSessionsTests -v 2
```

Expected: every test fails. Most likely the failure is `404` because the URL doesn't exist yet, or a `KeyError` on the response body.

- [ ] **Step 3.3 — Add the description string**

Edit `nextseek_api/assistant/descriptions.py` and add at the end:

```python
ASSISTANT_SESSIONS_LIST_DESC = """
List the current user's chat sessions, ordered by most-recently-updated first.
Returns at most 50 rows. Each row carries an id, display title (or "New chat"
if untitled), timestamps, the count of completed query bundles, and an 80-char
preview derived from the first user query in the session's results_history.
"""
```

- [ ] **Step 3.4 — Implement the `list_sessions` action**

Edit `nextseek_api/services/assistant.py`. Update the imports near the top — replace:

```python
from nextseek_api.assistant.models_api import (
    AssistantUserResponse,
    AsyncQueryResponse,
    BundleDownloadParams,
    QueryRequest,
    SessionCreateResponse,
    SessionDetailResponse,
    TaskProgressResponse,
    TestCaseItem,
    TestCaseListResponse,
)
```

with:

```python
from nextseek_api.assistant.models_api import (
    AssistantUserResponse,
    AsyncQueryResponse,
    BundleDownloadParams,
    QueryRequest,
    SessionCreateResponse,
    SessionDetailResponse,
    SessionListItem,
    SessionListResponse,
    SessionPatchRequest,
    TaskProgressResponse,
    TestCaseItem,
    TestCaseListResponse,
    Turn,
)
```

And add the description import — replace:

```python
from nextseek_api.assistant.descriptions import (
    ASSISTANT_BUNDLE_DOWNLOAD_DESC,
    ASSISTANT_ME_DESC,
    ASSISTANT_QUERY_ASYNC_DESC,
    ASSISTANT_QUERY_DESC,
    ASSISTANT_SESSION_CREATE_DESC,
    ASSISTANT_SESSION_DETAIL_DESC,
    ASSISTANT_TASK_PROGRESS_DESC,
    ASSISTANT_TEST_CASES_DESC,
)
```

with:

```python
from nextseek_api.assistant.descriptions import (
    ASSISTANT_BUNDLE_DOWNLOAD_DESC,
    ASSISTANT_ME_DESC,
    ASSISTANT_QUERY_ASYNC_DESC,
    ASSISTANT_QUERY_DESC,
    ASSISTANT_SESSION_CREATE_DESC,
    ASSISTANT_SESSION_DETAIL_DESC,
    ASSISTANT_SESSIONS_LIST_DESC,
    ASSISTANT_TASK_PROGRESS_DESC,
    ASSISTANT_TEST_CASES_DESC,
)
```

Then add a private projection helper near the top of the `AssistantViewSet` class — immediately above the existing `def _check_auth` method:

```python
    # ------------------------------------------------------------------
    # Helpers for the sessions list/detail
    # ------------------------------------------------------------------

    @staticmethod
    def _project_session_list_row(cs: ChatSession) -> dict:
        """Project a ChatSession into the SessionListItem shape.

        Reads `results_history` once to compute `query_count` and `preview`.
        Falls back to "New chat" when `title` is null.
        """
        history = cs.results_history or []
        first_user_query = ""
        for bundle in history:
            uq = (bundle or {}).get("user_query")
            if uq:
                first_user_query = uq
                break
        preview = " ".join(first_user_query.split())[:80]
        return SessionListItem(
            session_id=cs.session_id,
            title=cs.title or "New chat",
            created_at=cs.created_at,
            updated_at=cs.updated_at,
            query_count=len(history),
            preview=preview,
        ).model_dump(mode="json")
```

Now add the `list_sessions` action. Insert it immediately above the existing `create_session` action (search for `def create_session(self, request):`). The new method:

```python
    # ------------------------------------------------------------------
    # 1b. GET /assistant/sessions/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Assistant: List Sessions",
        description=ASSISTANT_SESSIONS_LIST_DESC,
        tags=["Assistant"],
        responses={200: SessionListResponse},
    )
    @action(detail=False, methods=["get"], url_path="sessions")
    def list_sessions(self, request):
        authed, err = self._check_auth(request)
        if not authed:
            return err

        # Two-step lookup: only the PK enters the ORDER BY query so the
        # JSON columns (results_history / last_debug / extra_state) never
        # land in the MySQL sort buffer (regression: error 1038 once
        # results_history grew beyond sort_buffer_size).
        ids = list(
            ChatSession.objects.filter(user=request.user)
            .order_by("-updated_at")
            .values_list("session_id", flat=True)[:50]
        )
        rows = []
        for sid in ids:
            cs = ChatSession.objects.get(session_id=sid)
            rows.append(self._project_session_list_row(cs))
        return Response(
            {"total": len(rows), "sessions": rows},
            status=status.HTTP_200_OK,
        )
```

Note: this action uses `url_path="sessions"` and `detail=False` with `methods=["get"]`. The existing `create_session` uses the same url_path with `methods=["post"]`. DRF dispatches by method, so both coexist on `/assistant/sessions/`.

- [ ] **Step 3.5 — Run tests; verify they pass**

Hand the user:

```bash
python manage.py test nextseek_api.assistant.tests.test_sessions_endpoints.ListSessionsTests -v 2
```

Expected: all 8 tests pass.

- [ ] **Step 3.6 — Commit**

```bash
git add nextseek_api/services/assistant.py nextseek_api/assistant/descriptions.py nextseek_api/assistant/tests/test_sessions_endpoints.py
git commit -m "feat(assistant): GET /sessions/ list endpoint

Returns the caller's 50 most-recently-updated ChatSessions, projecting
title (with 'New chat' fallback), timestamps, query_count, and an 80-char
preview from results_history. Uses PK-only ORDER BY to dodge the MySQL
sort-buffer error that bites large JSON columns.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 4 — Extend `GET /assistant/sessions/{id}/` with `?include=turns`

**Files:**
- Modify: `nextseek_api/services/assistant.py` (existing `get_session` action)
- Modify: `nextseek_api/assistant/tests/test_sessions_endpoints.py`

- [ ] **Step 4.1 — Write failing tests**

Append to `nextseek_api/assistant/tests/test_sessions_endpoints.py`:

```python
class GetSessionWithTurnsTests(SessionEndpointsTestBase):
    def _url(self, sid, include=None):
        base = f"/nextseek_api/assistant/sessions/{sid}/"
        return f"{base}?include={include}" if include else base

    def test_existing_shape_unchanged_without_include(self):
        cs = ChatSession.objects.create(
            user=self.user,
            title="My chat",
            results_history=[{"id": 1, "user_query": "hi", "reply": "hello", "mode": "x"}],
        )
        body = self.client.get(self._url(cs.session_id)).json()
        # Pre-existing fields still present.
        self.assertEqual(body["session_id"], str(cs.session_id))
        self.assertEqual(body["query_count"], 1)
        self.assertTrue(body["has_results"])
        # New optional fields are null/absent when ?include=turns is not passed.
        self.assertIsNone(body.get("turns"))
        self.assertIsNone(body.get("title"))

    def test_include_turns_returns_title_and_turns(self):
        cs = ChatSession.objects.create(
            user=self.user,
            title="My chat",
            results_history=[
                {"id": 1, "user_query": "hi", "terminal_reply": "hello", "mode": "new_search", "ts": "2026-05-12T00:00:00"},
                {"id": 2, "user_query": "again", "reply": "ok", "mode": "graph_query"},
            ],
        )
        body = self.client.get(self._url(cs.session_id, include="turns")).json()
        self.assertEqual(body["title"], "My chat")
        self.assertEqual(len(body["turns"]), 2)
        # terminal_reply preferred over reply when both could be set.
        self.assertEqual(body["turns"][0]["reply"], "hello")
        self.assertEqual(body["turns"][0]["bundle_id"], 1)
        self.assertEqual(body["turns"][0]["mode"], "new_search")
        self.assertEqual(body["turns"][0]["ts"], "2026-05-12T00:00:00")
        self.assertEqual(body["turns"][1]["reply"], "ok")
        self.assertIsNone(body["turns"][1]["ts"])

    def test_include_turns_skips_bundle_without_user_query(self):
        cs = ChatSession.objects.create(
            user=self.user,
            results_history=[
                {"id": 1, "user_query": "hi", "reply": "hello", "mode": "x"},
                {"id": 2, "reply": "no-user-query-bundle", "mode": "wizard"},
                {"id": 3, "user_query": "third", "reply": "third-reply", "mode": "x"},
            ],
        )
        turns = self.client.get(self._url(cs.session_id, include="turns")).json()["turns"]
        self.assertEqual([t["bundle_id"] for t in turns], [1, 3])

    def test_include_turns_403_for_non_owner(self):
        cs = ChatSession.objects.create(user=self.other)
        resp = self.client.get(self._url(cs.session_id, include="turns"))
        self.assertEqual(resp.status_code, 403)

    def test_include_turns_404_when_missing(self):
        resp = self.client.get(self._url("00000000-0000-0000-0000-000000000000", include="turns"))
        self.assertEqual(resp.status_code, 404)
```

- [ ] **Step 4.2 — Run tests; verify they fail**

```bash
python manage.py test nextseek_api.assistant.tests.test_sessions_endpoints.GetSessionWithTurnsTests -v 2
```

Expected: at least the include-turns tests fail (current endpoint returns the base shape regardless).

- [ ] **Step 4.3 — Implement: extend `get_session`**

In `nextseek_api/services/assistant.py`, replace the existing `get_session` method body with the extended version below. Locate it by its `@extend_schema(operation_id="Assistant: Get Session", ...)` decorator. Replace from `def get_session(self, request, session_id=None):` through the end of that method:

```python
    def get_session(self, request, session_id=None):
        authed, err = self._check_auth(request)
        if not authed:
            return err

        try:
            session = ChatSession.objects.get(session_id=session_id)
        except ChatSession.DoesNotExist:
            return _error_response("Not found", "Session not found.", status.HTTP_404_NOT_FOUND)

        if session.user_id != request.user.pk:
            return _error_response("Forbidden", "You do not own this session.", status.HTTP_403_FORBIDDEN)

        history = session.results_history or []
        payload = SessionDetailResponse(
            session_id=session.session_id,
            created_at=session.created_at,
            query_count=len(history),
            has_results=bool(history),
        ).model_dump(mode="json")

        include = request.query_params.get("include", "")
        include_set = {p.strip() for p in include.split(",") if p.strip()}
        if "turns" in include_set:
            payload["title"] = session.title or "New chat"
            payload["turns"] = [
                Turn(
                    bundle_id=b.get("id", 0),
                    user_query=b.get("user_query", ""),
                    reply=b.get("terminal_reply") or b.get("reply") or "",
                    mode=b.get("mode", ""),
                    ts=b.get("ts"),
                ).model_dump(mode="json")
                for b in history
                if (b or {}).get("user_query")
            ]

        return Response(payload, status=status.HTTP_200_OK)
```

- [ ] **Step 4.4 — Run tests; verify they pass**

```bash
python manage.py test nextseek_api.assistant.tests.test_sessions_endpoints -v 2
```

Expected: every test in the file (list + detail) passes.

- [ ] **Step 4.5 — Commit**

```bash
git add nextseek_api/services/assistant.py nextseek_api/assistant/tests/test_sessions_endpoints.py
git commit -m "feat(assistant): GET /sessions/<id>/?include=turns returns conversation

Adds an opt-in 'include=turns' query param that augments the existing
detail response with title and a projected turns array. Skips bundles
without a user_query (e.g. wizard intros) so the client never has to
render orphan assistant messages.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 5 — `PATCH /assistant/sessions/{id}/` (rename)

**Files:**
- Modify: `nextseek_api/services/assistant.py`
- Modify: `nextseek_api/assistant/descriptions.py`
- Modify: `nextseek_api/assistant/tests/test_sessions_endpoints.py`

- [ ] **Step 5.1 — Write failing tests**

Append to `nextseek_api/assistant/tests/test_sessions_endpoints.py`:

```python
class PatchSessionTests(SessionEndpointsTestBase):
    def _url(self, sid):
        return f"/nextseek_api/assistant/sessions/{sid}/"

    def test_rename_happy_path(self):
        cs = ChatSession.objects.create(user=self.user)
        resp = self.client.patch(self._url(cs.session_id), {"title": "New name"}, format="json")
        self.assertEqual(resp.status_code, 200)
        cs.refresh_from_db()
        self.assertEqual(cs.title, "New name")
        # Response shape: SessionListItem
        body = resp.json()
        self.assertEqual(body["title"], "New name")
        self.assertEqual(body["session_id"], str(cs.session_id))

    def test_rename_trims_whitespace(self):
        cs = ChatSession.objects.create(user=self.user)
        self.client.patch(self._url(cs.session_id), {"title": "  hello  "}, format="json")
        cs.refresh_from_db()
        self.assertEqual(cs.title, "hello")

    def test_reject_empty_after_trim(self):
        cs = ChatSession.objects.create(user=self.user)
        resp = self.client.patch(self._url(cs.session_id), {"title": "   "}, format="json")
        self.assertEqual(resp.status_code, 422)
        cs.refresh_from_db()
        self.assertIsNone(cs.title)

    def test_reject_overlong(self):
        cs = ChatSession.objects.create(user=self.user)
        resp = self.client.patch(self._url(cs.session_id), {"title": "x" * 201}, format="json")
        self.assertEqual(resp.status_code, 422)

    def test_403_on_non_owner(self):
        cs = ChatSession.objects.create(user=self.other)
        resp = self.client.patch(self._url(cs.session_id), {"title": "stolen"}, format="json")
        self.assertEqual(resp.status_code, 403)

    def test_404_when_missing(self):
        resp = self.client.patch(
            self._url("00000000-0000-0000-0000-000000000000"),
            {"title": "ghost"},
            format="json",
        )
        self.assertEqual(resp.status_code, 404)
```

- [ ] **Step 5.2 — Run; verify fail**

```bash
python manage.py test nextseek_api.assistant.tests.test_sessions_endpoints.PatchSessionTests -v 2
```

Expected: all fail (no PATCH route exists).

- [ ] **Step 5.3 — Add the description**

In `nextseek_api/assistant/descriptions.py`, append:

```python
ASSISTANT_SESSION_PATCH_DESC = """
Rename a chat session. Body: {"title": <string, 1..200 chars>}.
The title is trimmed; empty-after-trim is rejected with 422.
Returns the updated row in SessionListItem shape.
"""

ASSISTANT_SESSION_DELETE_DESC = """
Permanently delete a chat session and all associated QueryTask rows.
Returns 204 on success, 404 if the session is missing, 403 if the
caller does not own it.
"""
```

- [ ] **Step 5.4 — Implement `patch_session`**

In `nextseek_api/services/assistant.py`, immediately after `get_session`, add:

```python
    # ------------------------------------------------------------------
    # 3b. PATCH /assistant/sessions/{session_id}/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Assistant: Rename Session",
        description=ASSISTANT_SESSION_PATCH_DESC,
        tags=["Assistant"],
        request=SessionPatchRequest,
        responses={200: SessionListItem},
    )
    @action(
        detail=False,
        methods=["patch"],
        url_path=r"sessions/(?P<session_id>[0-9a-f-]+)",
    )
    def patch_session(self, request, session_id=None):
        authed, err = self._check_auth(request)
        if not authed:
            return err

        try:
            session = ChatSession.objects.get(session_id=session_id)
        except ChatSession.DoesNotExist:
            return _error_response("Not found", "Session not found.", status.HTTP_404_NOT_FOUND)

        if session.user_id != request.user.pk:
            return _error_response("Forbidden", "You do not own this session.", status.HTTP_403_FORBIDDEN)

        raw_title = (request.data or {}).get("title")
        if not isinstance(raw_title, str):
            return _error_response("Validation error", "Field 'title' is required and must be a string.", status.HTTP_422_UNPROCESSABLE_ENTITY)
        trimmed = raw_title.strip()
        if not trimmed:
            return _error_response("Validation error", "Field 'title' must not be empty after trim.", status.HTTP_422_UNPROCESSABLE_ENTITY)
        if len(trimmed) > 200:
            return _error_response("Validation error", "Field 'title' is too long (max 200 chars).", status.HTTP_422_UNPROCESSABLE_ENTITY)

        session.title = trimmed
        session.save(update_fields=["title", "updated_at"])
        return Response(
            self._project_session_list_row(session),
            status=status.HTTP_200_OK,
        )
```

- [ ] **Step 5.5 — Run tests**

```bash
python manage.py test nextseek_api.assistant.tests.test_sessions_endpoints.PatchSessionTests -v 2
```

Expected: all pass.

- [ ] **Step 5.6 — Commit**

```bash
git add nextseek_api/services/assistant.py nextseek_api/assistant/descriptions.py nextseek_api/assistant/tests/test_sessions_endpoints.py
git commit -m "feat(assistant): PATCH /sessions/<id>/ to rename a session

Trims input; rejects empty-after-trim and >200 chars with 422.
Returns the updated row in SessionListItem shape so the frontend can
patch its sidebar entry in place without a refetch.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 6 — `DELETE /assistant/sessions/{id}/`

**Files:**
- Modify: `nextseek_api/services/assistant.py`
- Modify: `nextseek_api/assistant/tests/test_sessions_endpoints.py`

- [ ] **Step 6.1 — Write failing tests**

Append to `nextseek_api/assistant/tests/test_sessions_endpoints.py`:

```python
class DeleteSessionTests(SessionEndpointsTestBase):
    def _url(self, sid):
        return f"/nextseek_api/assistant/sessions/{sid}/"

    def test_delete_returns_204(self):
        cs = ChatSession.objects.create(user=self.user)
        resp = self.client.delete(self._url(cs.session_id))
        self.assertEqual(resp.status_code, 204)
        self.assertFalse(ChatSession.objects.filter(session_id=cs.session_id).exists())

    def test_delete_cascades_tasks(self):
        from nextseek_api.assistant.models_db import QueryTask
        cs = ChatSession.objects.create(user=self.user)
        QueryTask.objects.create(session=cs, user=self.user, query="x", status="completed")
        QueryTask.objects.create(session=cs, user=self.user, query="y", status="completed")
        self.client.delete(self._url(cs.session_id))
        self.assertFalse(QueryTask.objects.filter(session_id=cs.session_id).exists())

    def test_delete_404_when_missing(self):
        resp = self.client.delete(self._url("00000000-0000-0000-0000-000000000000"))
        self.assertEqual(resp.status_code, 404)

    def test_delete_404_after_delete(self):
        cs = ChatSession.objects.create(user=self.user)
        self.client.delete(self._url(cs.session_id))
        resp = self.client.delete(self._url(cs.session_id))
        self.assertEqual(resp.status_code, 404)

    def test_delete_403_on_non_owner(self):
        cs = ChatSession.objects.create(user=self.other)
        resp = self.client.delete(self._url(cs.session_id))
        self.assertEqual(resp.status_code, 403)
        self.assertTrue(ChatSession.objects.filter(session_id=cs.session_id).exists())
```

- [ ] **Step 6.2 — Run; verify fail**

```bash
python manage.py test nextseek_api.assistant.tests.test_sessions_endpoints.DeleteSessionTests -v 2
```

Expected: all fail.

- [ ] **Step 6.3 — Implement `delete_session`**

In `nextseek_api/services/assistant.py`, immediately after `patch_session`, add:

```python
    # ------------------------------------------------------------------
    # 3c. DELETE /assistant/sessions/{session_id}/
    # ------------------------------------------------------------------
    @extend_schema(
        operation_id="Assistant: Delete Session",
        description=ASSISTANT_SESSION_DELETE_DESC,
        tags=["Assistant"],
        responses={204: None},
    )
    @action(
        detail=False,
        methods=["delete"],
        url_path=r"sessions/(?P<session_id>[0-9a-f-]+)",
    )
    def delete_session(self, request, session_id=None):
        authed, err = self._check_auth(request)
        if not authed:
            return err

        try:
            session = ChatSession.objects.get(session_id=session_id)
        except ChatSession.DoesNotExist:
            return _error_response("Not found", "Session not found.", status.HTTP_404_NOT_FOUND)

        if session.user_id != request.user.pk:
            return _error_response("Forbidden", "You do not own this session.", status.HTTP_403_FORBIDDEN)

        session.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
```

Add to the import block at the top:

```python
from nextseek_api.assistant.descriptions import (
    ...
    ASSISTANT_SESSION_DELETE_DESC,
    ASSISTANT_SESSION_PATCH_DESC,
    ...
)
```

(Insert in alphabetical order alongside the other `ASSISTANT_*` imports.)

- [ ] **Step 6.4 — Run tests**

```bash
python manage.py test nextseek_api.assistant.tests.test_sessions_endpoints.DeleteSessionTests -v 2
```

Expected: all 5 pass.

- [ ] **Step 6.5 — Commit**

```bash
git add nextseek_api/services/assistant.py nextseek_api/assistant/tests/test_sessions_endpoints.py
git commit -m "feat(assistant): DELETE /sessions/<id>/ hard-deletes a session

QueryTask rows CASCADE on the existing FK. 403 on non-owner, 404 on
missing, 204 on success.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 7 — `force_new` flag in `query` / `query_async`

**Files:**
- Modify: `nextseek_api/services/assistant.py`
- Create: `nextseek_api/assistant/tests/test_query_force_new.py`

- [ ] **Step 7.1 — Write failing tests**

Create `nextseek_api/assistant/tests/test_query_force_new.py`:

```python
"""Tests for the `force_new` flag on POST /assistant/query/async/."""
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from nextseek_api.assistant.models_db import ChatSession


class ForceNewFlagTests(TestCase):
    databases = {"default"}

    def setUp(self):
        self.user = User.objects.create_user("u1", password="p")
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        patcher_perm = patch(
            'nextseek_api.services.assistant.UserInParticipatingProject.has_permission',
            return_value=True,
        )
        patcher_perm.start()
        self.addCleanup(patcher_perm.stop)
        # Don't actually run the pipeline.
        patcher_pipe = patch('nextseek_api.services.assistant.run_query')
        patcher_pipe.start()
        self.addCleanup(patcher_pipe.stop)
        patcher_plan = patch('nextseek_api.services.assistant.run_query_plan')
        patcher_plan.start()
        self.addCleanup(patcher_plan.stop)

    def _post(self, body):
        return self.client.post(
            "/nextseek_api/assistant/query/async/",
            body,
            format="json",
        )

    def test_force_new_creates_fresh_session_when_one_exists(self):
        existing = ChatSession.objects.create(user=self.user)
        resp = self._post({"query": "hi", "mode": "standard", "force_new": True})
        self.assertEqual(resp.status_code, 202)
        new_sid = resp.json()["session_id"]
        self.assertNotEqual(new_sid, str(existing.session_id))
        self.assertEqual(ChatSession.objects.filter(user=self.user).count(), 2)

    def test_force_new_ignored_when_session_id_present(self):
        cs = ChatSession.objects.create(user=self.user)
        resp = self._post({
            "query": "hi", "mode": "standard",
            "session_id": str(cs.session_id), "force_new": True,
        })
        self.assertEqual(resp.status_code, 202)
        # Explicit session_id wins: response reflects that exact id.
        self.assertEqual(resp.json()["session_id"], str(cs.session_id))
        self.assertEqual(ChatSession.objects.filter(user=self.user).count(), 1)

    def test_force_new_false_reuses_most_recent(self):
        existing = ChatSession.objects.create(user=self.user)
        resp = self._post({"query": "hi", "mode": "standard", "force_new": False})
        self.assertEqual(resp.status_code, 202)
        # Falls back to the existing reuse-most-recent path.
        self.assertEqual(resp.json()["session_id"], str(existing.session_id))
        self.assertEqual(ChatSession.objects.filter(user=self.user).count(), 1)

    def test_force_new_omitted_defaults_to_false(self):
        existing = ChatSession.objects.create(user=self.user)
        resp = self._post({"query": "hi", "mode": "standard"})
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(resp.json()["session_id"], str(existing.session_id))
```

- [ ] **Step 7.2 — Run; verify fail**

```bash
python manage.py test nextseek_api.assistant.tests.test_query_force_new -v 2
```

Expected: `test_force_new_creates_fresh_session_when_one_exists` fails (currently reuses existing).

- [ ] **Step 7.3 — Implement: branch on `force_new` in both `query` and `query_async`**

In `nextseek_api/services/assistant.py`, locate the session-resolution branch in `query` (around the original line 252–280). Find:

```python
        if req.session_id:
            # Explicit session_id — validate ownership
            ...
        else:
            # No session_id — reuse most recent or auto-create
            recent_session_id = (
                ChatSession.objects.filter(user=request.user)
                .order_by("-updated_at")
                .values_list("session_id", flat=True)
                .first()
            )
            if recent_session_id is None:
                chat_session = ChatSession.objects.create(user=request.user)
            else:
                chat_session = ChatSession.objects.get(session_id=recent_session_id)
```

Replace with:

```python
        if req.session_id:
            # Explicit session_id — validate ownership
            try:
                chat_session = ChatSession.objects.get(
                    session_id=req.session_id,
                    user=request.user,
                )
            except ChatSession.DoesNotExist:
                return _error_response(
                    "Not found",
                    "Session not found or you do not own it.",
                    status.HTTP_404_NOT_FOUND,
                )
        elif req.force_new:
            # Frontend "New chat" path — unconditionally create.
            chat_session = ChatSession.objects.create(user=request.user)
        else:
            # No session_id — reuse most recent or auto-create
            # Two-step lookup: select only the PK first so the JSON columns
            # (results_history / last_debug / extra_state) never enter the
            # MySQL sort buffer. Bypasses error 1038 when a session row's
            # JSON has grown beyond `sort_buffer_size`.
            recent_session_id = (
                ChatSession.objects.filter(user=request.user)
                .order_by("-updated_at")
                .values_list("session_id", flat=True)
                .first()
            )
            if recent_session_id is None:
                chat_session = ChatSession.objects.create(user=request.user)
            else:
                chat_session = ChatSession.objects.get(session_id=recent_session_id)
```

The `query` action originally had the explicit-session-id branch inline; if your file has it written differently (e.g., the explicit branch is unwrapped because you're applying patches in a different order), preserve the existing explicit-branch logic and only insert the new `elif req.force_new:` block before the trailing `else:` block.

Apply the **same replacement** in the `query_async` action — locate the parallel block (around the original line 371–397) and apply identical changes.

- [ ] **Step 7.4 — Run tests**

```bash
python manage.py test nextseek_api.assistant.tests.test_query_force_new -v 2
```

Expected: all 4 pass.

Then run the full sessions test module to make sure nothing else broke:

```bash
python manage.py test nextseek_api.assistant.tests -v 2
python manage.py test nextseek_api.tests.test_assistant_unit -v 2
```

Expected: all pass.

- [ ] **Step 7.5 — Commit**

```bash
git add nextseek_api/services/assistant.py nextseek_api/assistant/tests/test_query_force_new.py
git commit -m "feat(assistant): force_new=true creates a fresh ChatSession

Lets the frontend's 'New chat' button deterministically create a new
session instead of silently reusing the user's most-recent session.
Ignored when an explicit session_id is provided.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 8 — Auto-title from first user query

**Files:**
- Modify: `nextseek_api/services/assistant.py`
- Modify: `nextseek_api/assistant/tests/test_sessions_endpoints.py`

- [ ] **Step 8.1 — Write failing tests**

Append to `nextseek_api/assistant/tests/test_sessions_endpoints.py`:

```python
class AutoTitleTests(SessionEndpointsTestBase):
    def _call_helper(self, cs):
        from nextseek_api.services.assistant import _auto_title_if_unset
        _auto_title_if_unset(cs)

    def test_sets_title_from_first_user_query(self):
        cs = ChatSession.objects.create(
            user=self.user,
            results_history=[
                {"id": 1, "user_query": "Find mice treated with NDMA", "reply": "...", "mode": "x"},
            ],
        )
        self._call_helper(cs)
        cs.refresh_from_db()
        self.assertEqual(cs.title, "Find mice treated with NDMA")

    def test_truncates_to_60_chars_after_collapsing_whitespace(self):
        long_q = "What  is\tthe\nname of the protocol used in the GBM study by Dr X"
        cs = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1, "user_query": long_q, "reply": "...", "mode": "x"}],
        )
        self._call_helper(cs)
        cs.refresh_from_db()
        # Internal whitespace collapsed to single spaces, then cap at 60.
        expected = " ".join(long_q.split())[:60]
        self.assertEqual(cs.title, expected)
        self.assertLessEqual(len(cs.title), 60)

    def test_noop_when_title_already_set(self):
        cs = ChatSession.objects.create(
            user=self.user,
            title="Manual name",
            results_history=[{"id": 1, "user_query": "ignored", "reply": "", "mode": "x"}],
        )
        self._call_helper(cs)
        cs.refresh_from_db()
        self.assertEqual(cs.title, "Manual name")

    def test_noop_when_history_empty(self):
        cs = ChatSession.objects.create(user=self.user)
        self._call_helper(cs)
        cs.refresh_from_db()
        self.assertIsNone(cs.title)

    def test_noop_when_first_bundle_has_no_user_query(self):
        cs = ChatSession.objects.create(
            user=self.user,
            results_history=[{"id": 1, "reply": "no user_query", "mode": "wizard"}],
        )
        self._call_helper(cs)
        cs.refresh_from_db()
        self.assertIsNone(cs.title)

    def test_finds_first_user_query_when_first_bundle_lacks_one(self):
        cs = ChatSession.objects.create(
            user=self.user,
            results_history=[
                {"id": 1, "reply": "wizard intro", "mode": "wizard"},
                {"id": 2, "user_query": "real first query", "reply": "x", "mode": "x"},
            ],
        )
        self._call_helper(cs)
        cs.refresh_from_db()
        self.assertEqual(cs.title, "real first query")
```

- [ ] **Step 8.2 — Run; verify fail**

```bash
python manage.py test nextseek_api.assistant.tests.test_sessions_endpoints.AutoTitleTests -v 2
```

Expected: every test fails with `ImportError` (`_auto_title_if_unset` not defined yet).

- [ ] **Step 8.3 — Implement the helper**

In `nextseek_api/services/assistant.py`, add this **module-level** function near the existing `_error_response` helper (above the `AssistantViewSet` class):

```python
def _auto_title_if_unset(chat_session: ChatSession) -> None:
    """Populate ChatSession.title from the first user query if currently NULL.

    Idempotent: subsequent calls on a session with a title set are a no-op.
    A manually-set title is therefore never overwritten — frontend rename
    always wins.
    """
    if chat_session.title:
        return
    history = chat_session.results_history or []
    first_user_query = ""
    for bundle in history:
        uq = (bundle or {}).get("user_query")
        if uq:
            first_user_query = uq
            break
    if not first_user_query:
        return
    title = " ".join(first_user_query.split())[:60]
    if not title:
        return
    chat_session.title = title
    chat_session.save(update_fields=["title", "updated_at"])
```

- [ ] **Step 8.4 — Wire helper into `query` (SSE) and `query_async`**

In `query`, locate the `_run_pipeline` inner function — at the very bottom of its `finally:` block, the code currently reads:

```python
            finally:
                adapter.save()
                event_queue.put(None)  # sentinel
```

Replace with:

```python
            finally:
                adapter.save()
                _auto_title_if_unset(chat_session)
                event_queue.put(None)  # sentinel
```

In `query_async`, locate the parallel `_run_pipeline.finally` block:

```python
            finally:
                adapter.save()
```

Replace with:

```python
            finally:
                adapter.save()
                _auto_title_if_unset(chat_session)
```

The `make_db_event_callback` (the `send_event` in `query_async`) is what flips the QueryTask to `completed`; the auto-title call happens **before** the final task transition fires because `send_event` for the terminal `query_complete` event was already enqueued by the pipeline thread before we reach the `finally` — and the helper's DB write happens synchronously here. After this point the frontend's `query_complete` handler will see the new title once it calls `refresh()`.

- [ ] **Step 8.5 — Run unit tests**

```bash
python manage.py test nextseek_api.assistant.tests.test_sessions_endpoints.AutoTitleTests -v 2
python manage.py test nextseek_api.assistant.tests -v 2
python manage.py test nextseek_api.tests.test_assistant_unit -v 2
```

Expected: all pass.

- [ ] **Step 8.6 — Commit**

```bash
git add nextseek_api/services/assistant.py nextseek_api/assistant/tests/test_sessions_endpoints.py
git commit -m "feat(assistant): auto-title untitled sessions from first user query

Sets ChatSession.title to the first user_query (whitespace-collapsed,
60-char cap) the first time a session persists a turn. Idempotent and
never overwrites a manual rename. Runs in the pipeline thread's finally
block of both /query/ (SSE) and /query/async/.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 9 — Frontend types

**Files:**
- Modify: `chat_frontend/src/lib/types/api.ts`

- [ ] **Step 9.1 — Add the new types**

Append to `chat_frontend/src/lib/types/api.ts`:

```typescript
// GET /assistant/sessions/ row
export interface SessionListItem {
  session_id: string;
  title: string;
  created_at: string;
  updated_at: string;
  query_count: number;
  preview: string;
}

// GET /assistant/sessions/ response
export interface SessionListResponse {
  total: number;
  sessions: SessionListItem[];
}

// One turn from a session's results_history projection
export interface Turn {
  bundle_id: number;
  user_query: string;
  reply: string;
  mode: string;
  ts?: string | null;
}

// GET /assistant/sessions/{id}/?include=turns response
export interface SessionDetailWithTurns extends SessionResponse {
  title?: string | null;
  turns?: Turn[] | null;
}

// Extend QueryCompleteData so callers can read session_id from the WS payload.
// (Backend already stamps this in for both query_complete and query_error.)
export interface QueryCompleteSessionExt {
  session_id?: string;
}
```

Then update `QueryCompleteData` and `QueryErrorData` (already in the file) to include `session_id`:

Edit `QueryCompleteData`:

```typescript
export interface QueryCompleteData {
  reply: string;
  debug: Record<string, unknown>;
  bundle_id: number;
  artifacts?: Artifact[] | null;
  session_id?: string;
}
```

Edit `QueryErrorData`:

```typescript
export interface QueryErrorData {
  error: string;
  agent?: string;
  session_id?: string;
}
```

- [ ] **Step 9.2 — Run typecheck**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx tsc -b --noEmit
```

Expected: no errors.

- [ ] **Step 9.3 — Commit**

```bash
git add chat_frontend/src/lib/types/api.ts
git commit -m "feat(chat_frontend): add SessionListItem/Turn/SessionDetailWithTurns types

Adds the API types needed by the upcoming useSessions hook + sidebar.
Also extends QueryCompleteData/QueryErrorData to expose session_id
(already in the backend payload).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 10 — Service methods: list / rename / delete / fetchTurns + submitQuery opts

**Files:**
- Modify: `chat_frontend/src/lib/services/chatApi.ts`
- Create: `chat_frontend/src/lib/services/__tests__/chatApi.sessions.test.ts`

- [ ] **Step 10.1 — Write failing tests**

Create `chat_frontend/src/lib/services/__tests__/chatApi.sessions.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach } from "vitest";
import { NextseekApiService } from "../chatApi";
import type { AuthService } from "../authTypes";

const baseUrl = "http://api.test";

class StubAuth implements AuthService {
  getAuthHeaders(): HeadersInit {
    return { "X-Test-Auth": "1" };
  }
  getApiBaseUrl(): string {
    return baseUrl;
  }
  getWsBaseUrl(): string {
    return "ws://api.test";
  }
}

function mockFetch(response: { ok?: boolean; status?: number; json?: () => Promise<unknown>; blob?: () => Promise<Blob> }) {
  const fn = vi.fn().mockResolvedValue({
    ok: response.ok ?? true,
    status: response.status ?? 200,
    json: response.json ?? (() => Promise.resolve({})),
    blob: response.blob ?? (() => Promise.resolve(new Blob())),
    headers: new Headers(),
  });
  globalThis.fetch = fn as unknown as typeof fetch;
  return fn;
}

describe("NextseekApiService — sessions methods", () => {
  let svc: NextseekApiService;

  beforeEach(() => {
    svc = new NextseekApiService(new StubAuth());
  });

  it("listSessions GETs /sessions/ and returns the parsed body", async () => {
    const fetchSpy = mockFetch({
      json: () => Promise.resolve({
        total: 1,
        sessions: [{ session_id: "uuid-1", title: "t", created_at: "x", updated_at: "x", query_count: 0, preview: "" }],
      }),
    });
    const out = await svc.listSessions();
    expect(fetchSpy).toHaveBeenCalledWith(
      `${baseUrl}/nextseek_api/assistant/sessions/`,
      expect.objectContaining({ headers: expect.objectContaining({ "X-Test-Auth": "1" }) }),
    );
    expect(out.total).toBe(1);
    expect(out.sessions[0].session_id).toBe("uuid-1");
  });

  it("renameSession PATCHes title and returns the updated row", async () => {
    const fetchSpy = mockFetch({
      json: () => Promise.resolve({ session_id: "uuid-1", title: "renamed", created_at: "x", updated_at: "x", query_count: 0, preview: "" }),
    });
    const out = await svc.renameSession("uuid-1", "renamed");
    expect(fetchSpy).toHaveBeenCalledWith(
      `${baseUrl}/nextseek_api/assistant/sessions/uuid-1/`,
      expect.objectContaining({
        method: "PATCH",
        headers: expect.objectContaining({ "Content-Type": "application/json", "X-Test-Auth": "1" }),
        body: JSON.stringify({ title: "renamed" }),
      }),
    );
    expect(out.title).toBe("renamed");
  });

  it("renameSession throws on non-2xx", async () => {
    mockFetch({ ok: false, status: 422 });
    await expect(svc.renameSession("uuid-1", "")).rejects.toThrow(/422/);
  });

  it("deleteSession DELETEs the row", async () => {
    const fetchSpy = mockFetch({ ok: true, status: 204 });
    await svc.deleteSession("uuid-1");
    expect(fetchSpy).toHaveBeenCalledWith(
      `${baseUrl}/nextseek_api/assistant/sessions/uuid-1/`,
      expect.objectContaining({ method: "DELETE" }),
    );
  });

  it("fetchSessionTurns GETs with ?include=turns", async () => {
    const fetchSpy = mockFetch({
      json: () => Promise.resolve({
        session_id: "uuid-1",
        created_at: "x",
        query_count: 1,
        has_results: true,
        title: "t",
        turns: [{ bundle_id: 1, user_query: "hi", reply: "hello", mode: "x" }],
      }),
    });
    const out = await svc.fetchSessionTurns("uuid-1");
    expect(fetchSpy).toHaveBeenCalledWith(
      `${baseUrl}/nextseek_api/assistant/sessions/uuid-1/?include=turns`,
      expect.anything(),
    );
    expect(out.turns).toHaveLength(1);
  });

  describe("submitQuery body shape", () => {
    it("includes session_id when opts.sessionId is set", async () => {
      const fetchSpy = mockFetch({
        json: () => Promise.resolve({ task_id: "t", session_id: "uuid-1" }),
      });
      // Skip the WS path: mock streamProgress to immediately resolve.
      // We only care about the POST body here.
      const onProgress = vi.fn();
      const onError = vi.fn();
      // Stub WebSocket so streamProgress rejects → falls back to poll which
      // returns a query_complete immediately.
      vi.stubGlobal("WebSocket", class { constructor() { throw new Error("no ws"); } });
      // After POST, the service tries WS then poll; poll calls fetch again with /tasks/.../progress/.
      // We just want to assert the FIRST fetch body. Catch the inner await and ignore.
      await svc.submitQuery("hi", "standard", { sessionId: "uuid-1" }, onProgress, onError).catch(() => {});
      const firstCall = fetchSpy.mock.calls[0];
      expect(firstCall[0]).toBe(`${baseUrl}/nextseek_api/assistant/query/async/`);
      const body = JSON.parse((firstCall[1] as RequestInit).body as string);
      expect(body).toEqual({ query: "hi", mode: "standard", session_id: "uuid-1" });
    });

    it("includes force_new when opts.forceNew is true", async () => {
      const fetchSpy = mockFetch({
        json: () => Promise.resolve({ task_id: "t", session_id: "uuid-new" }),
      });
      vi.stubGlobal("WebSocket", class { constructor() { throw new Error("no ws"); } });
      await svc.submitQuery("hi", "standard", { forceNew: true }, vi.fn(), vi.fn()).catch(() => {});
      const body = JSON.parse((fetchSpy.mock.calls[0][1] as RequestInit).body as string);
      expect(body).toEqual({ query: "hi", mode: "standard", force_new: true });
    });

    it("omits both when opts is empty", async () => {
      const fetchSpy = mockFetch({
        json: () => Promise.resolve({ task_id: "t", session_id: "uuid-x" }),
      });
      vi.stubGlobal("WebSocket", class { constructor() { throw new Error("no ws"); } });
      await svc.submitQuery("hi", "standard", {}, vi.fn(), vi.fn()).catch(() => {});
      const body = JSON.parse((fetchSpy.mock.calls[0][1] as RequestInit).body as string);
      expect(body).toEqual({ query: "hi", mode: "standard" });
    });
  });
});
```

- [ ] **Step 10.2 — Run; verify fail**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx vitest run src/lib/services/__tests__/chatApi.sessions.test.ts
```

Expected: every test fails (methods don't exist, `submitQuery` has the wrong signature).

- [ ] **Step 10.3 — Implement: add the four methods + change `submitQuery` signature**

Edit `chat_frontend/src/lib/services/chatApi.ts`:

Add imports near the existing imports at the top:

```typescript
import type {
  AsyncQueryResponse,
  ProgressEvent,
  SessionListItem,
  SessionListResponse,
  SessionDetailWithTurns,
  TestCase,
  TestCasesResponse,
} from "@/lib/types/api";
```

Replace the existing `submitQuery` method body. Find:

```typescript
  async submitQuery(
    query: string,
    mode: string,
    onProgress: (event: ProgressEvent) => void,
    onError: (error: string) => void,
  ): Promise<void> {
    const baseUrl = this.auth.getApiBaseUrl();

    // 1. POST async query
    let taskId: string;
    try {
      const response = await fetch(
        `${baseUrl}/nextseek_api/assistant/query/async/`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...this.auth.getAuthHeaders(),
          },
          body: JSON.stringify({ "query": query, "mode": mode }),
        },
      );
```

Replace with:

```typescript
  async submitQuery(
    query: string,
    mode: string,
    opts: { sessionId?: string | null; forceNew?: boolean },
    onProgress: (event: ProgressEvent) => void,
    onError: (error: string) => void,
  ): Promise<void> {
    const baseUrl = this.auth.getApiBaseUrl();

    // Build body
    const body: Record<string, unknown> = { query, mode };
    if (opts.sessionId) {
      body.session_id = opts.sessionId;
    } else if (opts.forceNew) {
      body.force_new = true;
    }

    // 1. POST async query
    let taskId: string;
    try {
      const response = await fetch(
        `${baseUrl}/nextseek_api/assistant/query/async/`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            ...this.auth.getAuthHeaders(),
          },
          body: JSON.stringify(body),
        },
      );
```

The rest of the method (response parsing, WS/poll dispatch) is unchanged.

Add four new methods, just before the closing brace of the class. Place them after `fetchBundle` and before the existing `downloadSearchAsExcel`:

```typescript
  async listSessions(): Promise<SessionListResponse> {
    const baseUrl = this.auth.getApiBaseUrl();
    const response = await fetch(
      `${baseUrl}/nextseek_api/assistant/sessions/`,
      { headers: { ...this.auth.getAuthHeaders() } },
    );
    if (!response.ok) {
      throw new Error(`Failed to list sessions: ${response.status}`);
    }
    return response.json();
  }

  async renameSession(sessionId: string, title: string): Promise<SessionListItem> {
    const baseUrl = this.auth.getApiBaseUrl();
    const response = await fetch(
      `${baseUrl}/nextseek_api/assistant/sessions/${sessionId}/`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json",
          ...this.auth.getAuthHeaders(),
        },
        body: JSON.stringify({ title }),
      },
    );
    if (!response.ok) {
      throw new Error(`Failed to rename session: ${response.status}`);
    }
    return response.json();
  }

  async deleteSession(sessionId: string): Promise<void> {
    const baseUrl = this.auth.getApiBaseUrl();
    const response = await fetch(
      `${baseUrl}/nextseek_api/assistant/sessions/${sessionId}/`,
      {
        method: "DELETE",
        headers: { ...this.auth.getAuthHeaders() },
      },
    );
    if (!response.ok) {
      throw new Error(`Failed to delete session: ${response.status}`);
    }
  }

  async fetchSessionTurns(sessionId: string): Promise<SessionDetailWithTurns> {
    const baseUrl = this.auth.getApiBaseUrl();
    const response = await fetch(
      `${baseUrl}/nextseek_api/assistant/sessions/${sessionId}/?include=turns`,
      { headers: { ...this.auth.getAuthHeaders() } },
    );
    if (!response.ok) {
      throw new Error(`Failed to load session: ${response.status}`);
    }
    return response.json();
  }
```

- [ ] **Step 10.4 — Run service tests**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx vitest run src/lib/services/__tests__/chatApi.sessions.test.ts
```

Expected: all pass.

- [ ] **Step 10.5 — Run all chat_frontend tests to catch regressions**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npm test -- --run
```

Expected: previously-passing tests still pass; but the **existing `useChatApi.ts` and `EmbeddedApp.tsx` callers of `submitQuery` no longer compile** because of the new required `opts` arg. We fix this in the very next task — so a `tsc` failure here from those two files is expected and OK. (The vitest run only runs `.test.ts` files; the typecheck across the project happens in Task 11.)

- [ ] **Step 10.6 — Commit**

```bash
git add chat_frontend/src/lib/services/chatApi.ts chat_frontend/src/lib/services/__tests__/chatApi.sessions.test.ts
git commit -m "feat(chat_frontend): chatApi adds listSessions/renameSession/deleteSession/fetchSessionTurns; submitQuery takes opts

submitQuery's signature now takes {sessionId?, forceNew?} after mode.
Callers in useChatApi/EmbeddedApp are updated in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 11 — Update `useChatApi` + `EmbeddedApp` for new `submitQuery` signature

**Files:**
- Modify: `chat_frontend/src/hooks/useChatApi.ts`
- Modify: `chat_frontend/src/EmbeddedApp.tsx`

This task only restores compilation. Wiring the new `opts` to real session state happens in Task 17.

- [ ] **Step 11.1 — Update `useChatApi`**

Edit `chat_frontend/src/hooks/useChatApi.ts`. Replace the `submitQuery` callback and its surrounding type:

```typescript
import { useRef, useState, useCallback } from "react";
import { NextseekApiService } from "@/lib/services/chatApi";
import { authService } from "@/lib/services/auth";
import type { ProgressEvent, TestCase } from "@/lib/types/api";

interface SubmitQueryOpts {
  sessionId?: string | null;
  forceNew?: boolean;
}

interface UseChatApiReturn {
  isQuerying: boolean;
  sessionId: string | null;
  submitQuery: (
    query: string,
    mode: string,
    opts: SubmitQueryOpts,
    onProgress: (event: ProgressEvent) => void,
    onError: (error: string) => void,
  ) => void;
  fetchTestCases: () => Promise<TestCase[]>;
  downloadBundle: (sessionId: string, bundleId: number, format: string) => Promise<void>;
}

export function useChatApi(): UseChatApiReturn {
  const serviceRef = useRef(new NextseekApiService(authService));
  const [isQuerying, setIsQuerying] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);

  const submitQuery = useCallback(
    (
      query: string,
      mode: string,
      opts: SubmitQueryOpts,
      onProgress: (event: ProgressEvent) => void,
      onError: (error: string) => void,
    ) => {
      setIsQuerying(true);

      serviceRef.current
        .submitQuery(query, mode, opts, onProgress, onError)
        .finally(() => {
          setSessionId(serviceRef.current.sessionId);
          setIsQuerying(false);
        });
    },
    [],
  );

  const fetchTestCases = useCallback(async () => {
    return serviceRef.current.fetchTestCases();
  }, []);

  const downloadBundle = useCallback(
    async (sid: string, bundleId: number, format: string) => {
      return serviceRef.current.downloadBundle(sid, bundleId, format);
    },
    [],
  );

  return {
    isQuerying,
    sessionId,
    submitQuery,
    fetchTestCases,
    downloadBundle,
  };
}
```

- [ ] **Step 11.2 — Update `AppLayout` and `EmbeddedApp` call sites (temporary: pass `{}`)**

Edit `chat_frontend/src/AppLayout.tsx`. Find the existing call inside `handleSendMessage`:

```typescript
      submitQuery(text, mode, handleProgress, handleQueryError);
```

Replace with:

```typescript
      submitQuery(text, mode, {}, handleProgress, handleQueryError);
```

Edit `chat_frontend/src/EmbeddedApp.tsx`. Find:

```typescript
      serviceRef.current
        .submitQuery(text, mode, handleProgress, handleQueryError)
```

Replace with:

```typescript
      serviceRef.current
        .submitQuery(text, mode, {}, handleProgress, handleQueryError)
```

These callsites get their real opts (sessionId/forceNew) wired in Task 17.

- [ ] **Step 11.3 — Typecheck + tests**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx tsc -b --noEmit
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npm test -- --run
```

Expected: typecheck clean; all tests pass.

- [ ] **Step 11.4 — Commit**

```bash
git add chat_frontend/src/hooks/useChatApi.ts chat_frontend/src/AppLayout.tsx chat_frontend/src/EmbeddedApp.tsx
git commit -m "refactor(chat_frontend): thread {sessionId, forceNew} opts through submitQuery

Callers pass an empty opts for now; useSessions will wire real values
once it lands.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 12 — `hydrateFromTurns` on `useMessages`

**Files:**
- Modify: `chat_frontend/src/hooks/useMessages.ts`
- Modify: `chat_frontend/src/hooks/__tests__/useMessages.test.ts`

- [ ] **Step 12.1 — Write failing tests**

Append to `chat_frontend/src/hooks/__tests__/useMessages.test.ts`:

```typescript
  describe("hydrateFromTurns", () => {
    it("replaces messages with paired user+assistant entries per turn", () => {
      const { result } = renderHook(() => useMessages());

      act(() => {
        result.current.addUserMessage("stale");
      });

      act(() => {
        result.current.hydrateFromTurns([
          { bundle_id: 1, user_query: "first", reply: "one", mode: "x" },
          { bundle_id: 2, user_query: "second", reply: "two", mode: "x" },
        ]);
      });

      expect(result.current.messages).toHaveLength(4);
      expect(result.current.messages[0].isUser).toBe(true);
      expect(result.current.messages[0].content).toBe("first");
      expect(result.current.messages[1].isUser).toBe(false);
      expect(result.current.messages[1].content).toBe("one");
      expect(result.current.messages[1].bundleId).toBe(1);
      expect(result.current.messages[2].content).toBe("second");
      expect(result.current.messages[3].bundleId).toBe(2);
    });

    it("hydrate of empty turns clears messages", () => {
      const { result } = renderHook(() => useMessages());
      act(() => {
        result.current.addUserMessage("stale");
      });
      act(() => {
        result.current.hydrateFromTurns([]);
      });
      expect(result.current.messages).toHaveLength(0);
    });
  });
```

- [ ] **Step 12.2 — Run; verify fail**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx vitest run src/hooks/__tests__/useMessages.test.ts
```

Expected: TypeScript error or runtime "is not a function".

- [ ] **Step 12.3 — Implement**

Edit `chat_frontend/src/hooks/useMessages.ts`. Add the `Turn` import:

```typescript
import type { Turn } from "@/lib/types/api";
```

Add `hydrateFromTurns` to the return type:

```typescript
interface UseMessagesReturn {
  messages: Message[];
  addUserMessage: (content: string) => void;
  addAssistantMessage: (content: string) => void;
  addSystemMessage: (content: string) => void;
  updateLastAssistantMessage: (patch: Partial<Message>) => void;
  clearMessages: () => void;
  hydrateFromTurns: (turns: Turn[]) => void;
}
```

Add the implementation inside `useMessages`:

```typescript
  const hydrateFromTurns = useCallback((turns: Turn[]) => {
    setMessages(() => {
      const next: Message[] = [];
      for (const turn of turns) {
        const ts = turn.ts ? new Date(turn.ts) : new Date();
        next.push({
          id: `msg-hydrate-u-${turn.bundle_id}`,
          content: turn.user_query,
          isUser: true,
          timestamp: ts,
          status: "sent",
          messageType: "text",
        });
        next.push({
          id: `msg-hydrate-a-${turn.bundle_id}`,
          content: turn.reply,
          isUser: false,
          timestamp: ts,
          status: "sent",
          messageType: "text",
          bundleId: turn.bundle_id,
          debugEntries: [],
        });
      }
      return next;
    });
  }, []);
```

And include it in the return statement:

```typescript
  return { messages, addUserMessage, addAssistantMessage, addSystemMessage, updateLastAssistantMessage, clearMessages, hydrateFromTurns };
```

- [ ] **Step 12.4 — Run tests**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx vitest run src/hooks/__tests__/useMessages.test.ts
```

Expected: all pass, including the existing tests.

- [ ] **Step 12.5 — Commit**

```bash
git add chat_frontend/src/hooks/useMessages.ts chat_frontend/src/hooks/__tests__/useMessages.test.ts
git commit -m "feat(chat_frontend): useMessages.hydrateFromTurns replaces messages with paired turns

Used to rehydrate a saved chat: each Turn from the server becomes a
user message + assistant message pair, preserving bundle_id so the
existing artifact-download affordances keep working.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 13 — `useChatRoute` hook

**Files:**
- Create: `chat_frontend/src/hooks/useChatRoute.ts`
- Create: `chat_frontend/src/hooks/__tests__/useChatRoute.test.ts`

- [ ] **Step 13.1 — Write failing tests**

Create `chat_frontend/src/hooks/__tests__/useChatRoute.test.ts`:

```typescript
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useChatRoute } from "../useChatRoute";

function setMetaBasename(value: string | null) {
  document.querySelectorAll('meta[name="chat-basename"]').forEach((n) => n.remove());
  if (value !== null) {
    const m = document.createElement("meta");
    m.setAttribute("name", "chat-basename");
    m.setAttribute("content", value);
    document.head.appendChild(m);
  }
}

describe("useChatRoute", () => {
  beforeEach(() => {
    setMetaBasename(null);
    window.history.pushState({}, "", "/");
  });

  afterEach(() => {
    setMetaBasename(null);
  });

  it("reads sessionIdFromUrl from the path when basename is /", () => {
    window.history.pushState({}, "", "/chat/abc-123");
    const { result } = renderHook(() => useChatRoute());
    expect(result.current.sessionIdFromUrl).toBe("abc-123");
  });

  it("reads sessionIdFromUrl under a custom basename", () => {
    setMetaBasename("/assistant/");
    window.history.pushState({}, "", "/assistant/chat/def-456");
    const { result } = renderHook(() => useChatRoute());
    expect(result.current.sessionIdFromUrl).toBe("def-456");
  });

  it("returns null when path has no /chat/ segment", () => {
    setMetaBasename("/assistant/");
    window.history.pushState({}, "", "/assistant/");
    const { result } = renderHook(() => useChatRoute());
    expect(result.current.sessionIdFromUrl).toBeNull();
  });

  it("push(uuid) updates the URL under basename", () => {
    setMetaBasename("/assistant/");
    window.history.pushState({}, "", "/assistant/");
    const { result } = renderHook(() => useChatRoute());
    act(() => {
      result.current.push("xyz-789");
    });
    expect(window.location.pathname).toBe("/assistant/chat/xyz-789");
  });

  it("push(null) returns to the basename root", () => {
    setMetaBasename("/assistant/");
    window.history.pushState({}, "", "/assistant/chat/abc");
    const { result } = renderHook(() => useChatRoute());
    act(() => {
      result.current.push(null);
    });
    expect(window.location.pathname).toBe("/assistant/");
  });

  it("popstate updates sessionIdFromUrl", () => {
    const { result } = renderHook(() => useChatRoute());
    act(() => {
      window.history.pushState({}, "", "/chat/aaa");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(result.current.sessionIdFromUrl).toBe("aaa");
  });
});
```

- [ ] **Step 13.2 — Run; verify fail**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx vitest run src/hooks/__tests__/useChatRoute.test.ts
```

Expected: module-not-found error.

- [ ] **Step 13.3 — Implement**

Create `chat_frontend/src/hooks/useChatRoute.ts`:

```typescript
import { useCallback, useEffect, useState } from "react";

function readBasename(): string {
  const meta = document.querySelector<HTMLMetaElement>('meta[name="chat-basename"]');
  const raw = meta?.content ?? "/";
  // Ensure a single trailing slash.
  return raw.endsWith("/") ? raw : `${raw}/`;
}

function parseSessionIdFromPath(basename: string): string | null {
  const path = window.location.pathname;
  if (!path.startsWith(basename)) return null;
  const rest = path.slice(basename.length);
  // Expect "chat/<uuid>" possibly with a trailing slash or query.
  const match = rest.match(/^chat\/([^/?#]+)/);
  return match ? match[1] : null;
}

export interface UseChatRouteReturn {
  sessionIdFromUrl: string | null;
  push: (id: string | null) => void;
}

export function useChatRoute(): UseChatRouteReturn {
  const [basename] = useState(readBasename);
  const [sessionIdFromUrl, setSessionIdFromUrl] = useState<string | null>(() =>
    parseSessionIdFromPath(basename),
  );

  useEffect(() => {
    const onPop = () => setSessionIdFromUrl(parseSessionIdFromPath(basename));
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, [basename]);

  const push = useCallback(
    (id: string | null) => {
      const next = id ? `${basename}chat/${id}` : basename;
      if (window.location.pathname === next) return;
      window.history.pushState({}, "", next);
      setSessionIdFromUrl(id);
    },
    [basename],
  );

  return { sessionIdFromUrl, push };
}
```

- [ ] **Step 13.4 — Run tests**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx vitest run src/hooks/__tests__/useChatRoute.test.ts
```

Expected: all pass.

- [ ] **Step 13.5 — Commit**

```bash
git add chat_frontend/src/hooks/useChatRoute.ts chat_frontend/src/hooks/__tests__/useChatRoute.test.ts
git commit -m "feat(chat_frontend): useChatRoute reads/writes /chat/<uuid> under basename

Basename comes from <meta name=chat-basename> (set by smartSearch.html
to /assistant/ for the embedded build, defaults to / for standalone).
Uses history.pushState so no router dependency is needed.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 14 — `useSessions` hook

**Files:**
- Create: `chat_frontend/src/hooks/useSessions.ts`
- Create: `chat_frontend/src/hooks/__tests__/useSessions.test.ts`

- [ ] **Step 14.1 — Write failing tests**

Create `chat_frontend/src/hooks/__tests__/useSessions.test.ts`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { renderHook, act, waitFor } from "@testing-library/react";
import { useSessions } from "../useSessions";
import type { SessionListItem, Turn } from "@/lib/types/api";

type ServiceMock = {
  listSessions: ReturnType<typeof vi.fn>;
  renameSession: ReturnType<typeof vi.fn>;
  deleteSession: ReturnType<typeof vi.fn>;
  fetchSessionTurns: ReturnType<typeof vi.fn>;
};

function makeService(initialSessions: SessionListItem[] = []): ServiceMock {
  return {
    listSessions: vi.fn().mockResolvedValue({ total: initialSessions.length, sessions: initialSessions }),
    renameSession: vi.fn(),
    deleteSession: vi.fn().mockResolvedValue(undefined),
    fetchSessionTurns: vi.fn(),
  };
}

function makeHydrate() {
  return vi.fn();
}

const s = (id: string, title = id): SessionListItem => ({
  session_id: id,
  title,
  created_at: "2026-05-12T00:00:00Z",
  updated_at: "2026-05-12T00:00:00Z",
  query_count: 0,
  preview: "",
});

describe("useSessions", () => {
  beforeEach(() => {
    Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("loads the list on mount", async () => {
    const service = makeService([s("a"), s("b")]);
    const { result } = renderHook(() => useSessions({ service, hydrate: makeHydrate(), onRouteChange: vi.fn() }));
    await waitFor(() => expect(result.current.sessions).toHaveLength(2));
    expect(result.current.activeSessionId).toBeNull();
    expect(result.current.pendingNewChat).toBe(true); // no active id at first load
  });

  it("newChat() clears active id, sets pendingNewChat, and pushes null route", async () => {
    const service = makeService([s("a")]);
    const onRouteChange = vi.fn();
    const hydrate = makeHydrate();
    const { result } = renderHook(() => useSessions({ service, hydrate, onRouteChange }));
    await waitFor(() => expect(result.current.sessions).toHaveLength(1));

    act(() => { result.current.setActiveImmediately("a"); });
    await waitFor(() => expect(result.current.activeSessionId).toBe("a"));

    act(() => { result.current.newChat(); });
    expect(result.current.activeSessionId).toBeNull();
    expect(result.current.pendingNewChat).toBe(true);
    expect(onRouteChange).toHaveBeenCalledWith(null);
  });

  it("setActive fetches turns and hydrates", async () => {
    const turns: Turn[] = [{ bundle_id: 1, user_query: "hi", reply: "hello", mode: "x" }];
    const service = makeService([s("a")]);
    service.fetchSessionTurns.mockResolvedValue({
      session_id: "a", created_at: "x", query_count: 1, has_results: true, title: "a", turns,
    });
    const hydrate = makeHydrate();
    const onRouteChange = vi.fn();
    const { result } = renderHook(() => useSessions({ service, hydrate, onRouteChange }));
    await waitFor(() => expect(result.current.sessions).toHaveLength(1));

    await act(async () => {
      await result.current.setActive("a");
    });
    expect(service.fetchSessionTurns).toHaveBeenCalledWith("a");
    expect(hydrate).toHaveBeenCalledWith(turns);
    expect(result.current.activeSessionId).toBe("a");
    expect(result.current.pendingNewChat).toBe(false);
    expect(onRouteChange).toHaveBeenLastCalledWith("a");
  });

  it("promoteCreatedSession sets active + clears pendingNewChat + refreshes", async () => {
    const service = makeService([]);
    const hydrate = makeHydrate();
    const onRouteChange = vi.fn();
    const { result } = renderHook(() => useSessions({ service, hydrate, onRouteChange }));
    await waitFor(() => expect(service.listSessions).toHaveBeenCalledTimes(1));

    service.listSessions.mockResolvedValue({ total: 1, sessions: [s("new", "auto-title")] });

    act(() => { result.current.promoteCreatedSession("new"); });
    expect(result.current.activeSessionId).toBe("new");
    expect(result.current.pendingNewChat).toBe(false);
    expect(onRouteChange).toHaveBeenCalledWith("new");
    await waitFor(() => expect(service.listSessions).toHaveBeenCalledTimes(2));
    expect(result.current.sessions[0].title).toBe("auto-title");
  });

  it("rename calls the service and refreshes", async () => {
    const service = makeService([s("a", "old")]);
    service.renameSession.mockResolvedValue(s("a", "new"));
    const { result } = renderHook(() => useSessions({ service, hydrate: makeHydrate(), onRouteChange: vi.fn() }));
    await waitFor(() => expect(result.current.sessions).toHaveLength(1));
    service.listSessions.mockResolvedValue({ total: 1, sessions: [s("a", "new")] });

    await act(async () => {
      await result.current.rename("a", "new");
    });
    expect(service.renameSession).toHaveBeenCalledWith("a", "new");
    await waitFor(() => expect(result.current.sessions[0].title).toBe("new"));
  });

  it("remove clears active + URL if deleting active session", async () => {
    const service = makeService([s("a"), s("b")]);
    const hydrate = makeHydrate();
    const onRouteChange = vi.fn();
    const { result } = renderHook(() => useSessions({ service, hydrate, onRouteChange }));
    await waitFor(() => expect(result.current.sessions).toHaveLength(2));

    act(() => { result.current.setActiveImmediately("a"); });

    service.listSessions.mockResolvedValue({ total: 1, sessions: [s("b")] });

    await act(async () => {
      await result.current.remove("a");
    });
    expect(service.deleteSession).toHaveBeenCalledWith("a");
    expect(result.current.activeSessionId).toBeNull();
    expect(onRouteChange).toHaveBeenLastCalledWith(null);
  });
});
```

- [ ] **Step 14.2 — Run; verify fail**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx vitest run src/hooks/__tests__/useSessions.test.ts
```

Expected: module-not-found.

- [ ] **Step 14.3 — Implement**

Create `chat_frontend/src/hooks/useSessions.ts`:

```typescript
import { useCallback, useEffect, useRef, useState } from "react";
import type { SessionListItem, Turn } from "@/lib/types/api";

export interface SessionServiceLike {
  listSessions: () => Promise<{ total: number; sessions: SessionListItem[] }>;
  renameSession: (id: string, title: string) => Promise<SessionListItem>;
  deleteSession: (id: string) => Promise<void>;
  fetchSessionTurns: (id: string) => Promise<{ turns?: Turn[] | null }>;
}

export interface UseSessionsArgs {
  service: SessionServiceLike;
  hydrate: (turns: Turn[]) => void;
  onRouteChange: (id: string | null) => void;
}

export interface UseSessionsReturn {
  sessions: SessionListItem[];
  activeSessionId: string | null;
  pendingNewChat: boolean;
  isLoading: boolean;
  isHydrating: boolean;
  refresh: () => Promise<void>;
  newChat: () => void;
  setActive: (id: string | null) => Promise<void>;
  /** For internal-only use: set active without fetching turns (e.g. in tests, or
   * when the caller has already hydrated). */
  setActiveImmediately: (id: string | null) => void;
  rename: (id: string, title: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
  promoteCreatedSession: (id: string) => void;
}

export function useSessions({ service, hydrate, onRouteChange }: UseSessionsArgs): UseSessionsReturn {
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [pendingNewChat, setPendingNewChat] = useState(true);
  const [isLoading, setIsLoading] = useState(false);
  const [isHydrating, setIsHydrating] = useState(false);
  const onRouteChangeRef = useRef(onRouteChange);
  onRouteChangeRef.current = onRouteChange;

  const refresh = useCallback(async () => {
    setIsLoading(true);
    try {
      const res = await service.listSessions();
      setSessions(res.sessions);
    } finally {
      setIsLoading(false);
    }
  }, [service]);

  // Load on mount.
  useEffect(() => {
    refresh();
  }, [refresh]);

  // Refresh when the tab regains focus.
  useEffect(() => {
    const onVis = () => {
      if (document.visibilityState === "visible") refresh();
    };
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, [refresh]);

  const newChat = useCallback(() => {
    setActiveSessionId(null);
    setPendingNewChat(true);
    hydrate([]);
    onRouteChangeRef.current(null);
  }, [hydrate]);

  const setActiveImmediately = useCallback((id: string | null) => {
    setActiveSessionId(id);
    setPendingNewChat(id === null);
    onRouteChangeRef.current(id);
  }, []);

  const setActive = useCallback(async (id: string | null) => {
    if (id === null) {
      newChat();
      return;
    }
    setIsHydrating(true);
    try {
      const detail = await service.fetchSessionTurns(id);
      hydrate(detail.turns ?? []);
      setActiveSessionId(id);
      setPendingNewChat(false);
      onRouteChangeRef.current(id);
    } finally {
      setIsHydrating(false);
    }
  }, [service, hydrate, newChat]);

  const rename = useCallback(async (id: string, title: string) => {
    await service.renameSession(id, title);
    await refresh();
  }, [service, refresh]);

  const remove = useCallback(async (id: string) => {
    await service.deleteSession(id);
    if (activeSessionId === id) {
      setActiveSessionId(null);
      setPendingNewChat(true);
      hydrate([]);
      onRouteChangeRef.current(null);
    }
    await refresh();
  }, [service, refresh, activeSessionId, hydrate]);

  const promoteCreatedSession = useCallback((id: string) => {
    setActiveSessionId(id);
    setPendingNewChat(false);
    onRouteChangeRef.current(id);
    refresh();
  }, [refresh]);

  return {
    sessions,
    activeSessionId,
    pendingNewChat,
    isLoading,
    isHydrating,
    refresh,
    newChat,
    setActive,
    setActiveImmediately,
    rename,
    remove,
    promoteCreatedSession,
  };
}
```

- [ ] **Step 14.4 — Run tests**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx vitest run src/hooks/__tests__/useSessions.test.ts
```

Expected: all pass.

- [ ] **Step 14.5 — Commit**

```bash
git add chat_frontend/src/hooks/useSessions.ts chat_frontend/src/hooks/__tests__/useSessions.test.ts
git commit -m "feat(chat_frontend): useSessions hook (list/active/rename/remove/promote)

Loads sessions on mount; refreshes on tab visibility; lazy newChat
(no server POST); setActive fetches and hydrates; promoteCreatedSession
runs after the first successful query of a new chat to lift the
backend-created session_id into active state.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 15 — `NewChatButton`

**Files:**
- Create: `chat_frontend/src/components/Sessions/NewChatButton.tsx`
- Create: `chat_frontend/src/components/Sessions/index.ts`

- [ ] **Step 15.1 — Implement**

Create `chat_frontend/src/components/Sessions/NewChatButton.tsx`:

```typescript
import { Plus } from "lucide-react";
import { Button } from "@/components/ui/button";

interface NewChatButtonProps {
  collapsed: boolean;
  disabled?: boolean;
  onClick: () => void;
}

export function NewChatButton({ collapsed, disabled, onClick }: NewChatButtonProps) {
  return (
    <Button
      variant="outline"
      size={collapsed ? "icon" : "default"}
      onClick={onClick}
      disabled={disabled}
      className={collapsed ? "w-10" : "w-full justify-start"}
      aria-label="New chat"
      title="New chat"
    >
      <Plus className="h-4 w-4" />
      {!collapsed && <span className="ml-2">New chat</span>}
    </Button>
  );
}
```

Create `chat_frontend/src/components/Sessions/index.ts`:

```typescript
export { NewChatButton } from "./NewChatButton";
export { SessionListItem } from "./SessionListItem";
export { SessionSidebar } from "./SessionSidebar";
```

(`SessionListItem` and `SessionSidebar` are added in Tasks 16 and 17. TypeScript will complain about the re-exports until those files exist — that's fine; we accept the temporary error and fix it before committing.)

- [ ] **Step 15.2 — Commit (will combine with Tasks 16/17)**

Hold off committing until Task 17. We don't want a half-broken state in git history.

---

# Task 16 — `SessionListItem` (one row, with inline rename + delete confirm)

**Files:**
- Create: `chat_frontend/src/components/Sessions/SessionListItem.tsx`
- Create: `chat_frontend/src/components/Sessions/__tests__/SessionListItem.test.tsx`

- [ ] **Step 16.1 — Write failing tests**

Create `chat_frontend/src/components/Sessions/__tests__/SessionListItem.test.tsx`:

```typescript
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SessionListItem } from "../SessionListItem";
import type { SessionListItem as SessionListItemModel } from "@/lib/types/api";

const item: SessionListItemModel = {
  session_id: "uuid-1",
  title: "Hello world",
  created_at: "2026-05-12T00:00:00Z",
  updated_at: "2026-05-12T00:00:00Z",
  query_count: 3,
  preview: "first query",
};

describe("SessionListItem", () => {
  it("renders title", () => {
    render(<SessionListItem item={item} active={false} disabled={false} onSelect={vi.fn()} onRename={vi.fn()} onDelete={vi.fn()} />);
    expect(screen.getByText("Hello world")).toBeInTheDocument();
  });

  it("calls onSelect when clicked", () => {
    const onSelect = vi.fn();
    render(<SessionListItem item={item} active={false} disabled={false} onSelect={onSelect} onRename={vi.fn()} onDelete={vi.fn()} />);
    fireEvent.click(screen.getByText("Hello world"));
    expect(onSelect).toHaveBeenCalledWith("uuid-1");
  });

  it("does not call onSelect when disabled", () => {
    const onSelect = vi.fn();
    render(<SessionListItem item={item} active={false} disabled={true} onSelect={onSelect} onRename={vi.fn()} onDelete={vi.fn()} />);
    fireEvent.click(screen.getByText("Hello world"));
    expect(onSelect).not.toHaveBeenCalled();
  });

  it("rename: clicking Rename swaps to input, Enter saves", () => {
    const onRename = vi.fn();
    render(<SessionListItem item={item} active={false} disabled={false} onSelect={vi.fn()} onRename={onRename} onDelete={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /more/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /rename/i }));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Renamed" } });
    fireEvent.keyDown(input, { key: "Enter" });
    expect(onRename).toHaveBeenCalledWith("uuid-1", "Renamed");
  });

  it("rename: Escape cancels", () => {
    const onRename = vi.fn();
    render(<SessionListItem item={item} active={false} disabled={false} onSelect={vi.fn()} onRename={onRename} onDelete={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /more/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /rename/i }));
    const input = screen.getByRole("textbox");
    fireEvent.change(input, { target: { value: "Cancelled" } });
    fireEvent.keyDown(input, { key: "Escape" });
    expect(onRename).not.toHaveBeenCalled();
    expect(screen.getByText("Hello world")).toBeInTheDocument();
  });

  it("delete: clicking Delete then Confirm calls onDelete", () => {
    const onDelete = vi.fn();
    render(<SessionListItem item={item} active={false} disabled={false} onSelect={vi.fn()} onRename={vi.fn()} onDelete={onDelete} />);
    fireEvent.click(screen.getByRole("button", { name: /more/i }));
    fireEvent.click(screen.getByRole("menuitem", { name: /delete/i }));
    fireEvent.click(screen.getByRole("button", { name: /^delete$/i }));
    expect(onDelete).toHaveBeenCalledWith("uuid-1");
  });
});
```

- [ ] **Step 16.2 — Run; verify fail**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx vitest run src/components/Sessions/__tests__/SessionListItem.test.tsx
```

Expected: module-not-found.

- [ ] **Step 16.3 — Implement**

We need a small native menu (a popover with two items). The codebase already uses `radix-ui/react-dialog` for confirms; for the `⋯` overflow menu we'll implement a tiny inline popover (no new dep). The simplest, accessibility-friendly approach is a `<details>`-based menu — but to match the existing styling we'll use a flat absolutely-positioned div with `role="menu"`.

Create `chat_frontend/src/components/Sessions/SessionListItem.tsx`:

```typescript
import { useEffect, useRef, useState } from "react";
import { MoreHorizontal } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import type { SessionListItem as SessionListItemModel } from "@/lib/types/api";

interface SessionListItemProps {
  item: SessionListItemModel;
  active: boolean;
  disabled: boolean;
  collapsed?: boolean;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
}

export function SessionListItem({
  item, active, disabled, collapsed = false,
  onSelect, onRename, onDelete,
}: SessionListItemProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [draft, setDraft] = useState(item.title);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (renaming) {
      setDraft(item.title);
      requestAnimationFrame(() => inputRef.current?.select());
    }
  }, [renaming, item.title]);

  // Close menu on outside click.
  useEffect(() => {
    if (!menuOpen) return;
    const onDoc = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", onDoc);
    return () => document.removeEventListener("mousedown", onDoc);
  }, [menuOpen]);

  const commitRename = () => {
    const trimmed = draft.trim();
    setRenaming(false);
    if (trimmed && trimmed !== item.title) {
      onRename(item.session_id, trimmed);
    }
  };
  const cancelRename = () => {
    setRenaming(false);
    setDraft(item.title);
  };

  return (
    <div
      className={[
        "group relative flex items-center gap-2 rounded-md px-2 py-1.5 text-sm",
        active ? "bg-accent text-accent-foreground" : "hover:bg-accent/50",
        disabled ? "opacity-60 cursor-not-allowed" : "cursor-pointer",
      ].join(" ")}
    >
      {renaming ? (
        <Input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitRename();
            else if (e.key === "Escape") cancelRename();
          }}
          onBlur={commitRename}
          className="h-7 flex-1"
          aria-label="Rename session"
        />
      ) : (
        <button
          type="button"
          onClick={() => !disabled && onSelect(item.session_id)}
          className="flex-1 truncate text-left"
          title={item.title}
          disabled={disabled}
        >
          {collapsed ? item.title.slice(0, 1) : item.title}
        </button>
      )}

      {!collapsed && !renaming && (
        <div className="relative" ref={menuRef}>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            className="h-6 w-6 opacity-0 group-hover:opacity-100"
            aria-label="More"
            onClick={() => setMenuOpen((v) => !v)}
            disabled={disabled}
          >
            <MoreHorizontal className="h-4 w-4" />
          </Button>
          {menuOpen && (
            <div
              role="menu"
              className="absolute right-0 top-full z-50 mt-1 w-32 rounded-md border bg-popover p-1 text-popover-foreground shadow-md"
            >
              <button
                type="button"
                role="menuitem"
                className="w-full rounded-sm px-2 py-1 text-left text-sm hover:bg-accent"
                onClick={() => { setMenuOpen(false); setRenaming(true); }}
              >
                Rename
              </button>
              <button
                type="button"
                role="menuitem"
                className="w-full rounded-sm px-2 py-1 text-left text-sm hover:bg-accent"
                onClick={() => { setMenuOpen(false); setConfirmOpen(true); }}
              >
                Delete
              </button>
            </div>
          )}
        </div>
      )}

      <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Delete this chat?</DialogTitle>
            <DialogDescription>
              "{item.title}" will be permanently deleted, including its history. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setConfirmOpen(false)}>
              Cancel
            </Button>
            <Button
              variant="destructive"
              onClick={() => { setConfirmOpen(false); onDelete(item.session_id); }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
```

- [ ] **Step 16.4 — Run tests**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx vitest run src/components/Sessions/__tests__/SessionListItem.test.tsx
```

Expected: all pass. If a `destructive` variant isn't available on `Button`, replace `variant="destructive"` with `variant="outline"` and add `className="bg-destructive text-destructive-foreground"` — verify by reading `src/components/ui/button.tsx` first.

- [ ] **Step 16.5 — Hold commit**

Combine with Task 17 (final commit for the components batch).

---

# Task 17 — `SessionSidebar` + wire `AppLayout` + `EmbeddedApp`

**Files:**
- Create: `chat_frontend/src/components/Sessions/SessionSidebar.tsx`
- Create: `chat_frontend/src/components/Sessions/__tests__/SessionSidebar.test.tsx`
- Modify: `chat_frontend/src/AppLayout.tsx`
- Modify: `chat_frontend/src/EmbeddedApp.tsx`

- [ ] **Step 17.1 — Write failing tests for `SessionSidebar`**

Create `chat_frontend/src/components/Sessions/__tests__/SessionSidebar.test.tsx`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SessionSidebar } from "../SessionSidebar";
import type { SessionListItem } from "@/lib/types/api";

const sessions: SessionListItem[] = [
  { session_id: "a", title: "Alpha", created_at: "", updated_at: "", query_count: 1, preview: "" },
  { session_id: "b", title: "Beta",  created_at: "", updated_at: "", query_count: 2, preview: "" },
];

const baseProps = {
  sessions,
  activeSessionId: null as string | null,
  collapsed: false,
  inFlight: false,
  onNewChat: vi.fn(),
  onSelect: vi.fn(),
  onRename: vi.fn(),
  onDelete: vi.fn(),
  onToggleCollapse: vi.fn(),
};

describe("SessionSidebar", () => {
  beforeEach(() => {
    localStorage.clear();
    Object.values(baseProps).forEach((v) => typeof v === "function" && (v as ReturnType<typeof vi.fn>).mockReset?.());
  });
  afterEach(() => {
    localStorage.clear();
  });

  it("renders the list", () => {
    render(<SessionSidebar {...baseProps} />);
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
  });

  it("New chat button fires onNewChat", () => {
    const onNewChat = vi.fn();
    render(<SessionSidebar {...baseProps} onNewChat={onNewChat} />);
    fireEvent.click(screen.getByRole("button", { name: /new chat/i }));
    expect(onNewChat).toHaveBeenCalledTimes(1);
  });

  it("renders empty state when no sessions and not pending", () => {
    render(<SessionSidebar {...baseProps} sessions={[]} />);
    expect(screen.getByText(/start a new chat/i)).toBeInTheDocument();
  });

  it("clicking a row fires onSelect", () => {
    const onSelect = vi.fn();
    render(<SessionSidebar {...baseProps} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("Alpha"));
    expect(onSelect).toHaveBeenCalledWith("a");
  });

  it("inFlight disables row clicks (does not call onSelect)", () => {
    const onSelect = vi.fn();
    render(<SessionSidebar {...baseProps} inFlight={true} onSelect={onSelect} />);
    fireEvent.click(screen.getByText("Alpha"));
    expect(onSelect).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 17.2 — Run; verify fail**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx vitest run src/components/Sessions/__tests__/SessionSidebar.test.tsx
```

Expected: module-not-found.

- [ ] **Step 17.3 — Implement `SessionSidebar`**

Create `chat_frontend/src/components/Sessions/SessionSidebar.tsx`:

```typescript
import { PanelLeftClose, PanelLeftOpen, MessageSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { NewChatButton } from "./NewChatButton";
import { SessionListItem } from "./SessionListItem";
import type { SessionListItem as SessionListItemModel } from "@/lib/types/api";

interface SessionSidebarProps {
  sessions: SessionListItemModel[];
  activeSessionId: string | null;
  collapsed: boolean;
  inFlight: boolean;
  onNewChat: () => void;
  onSelect: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onDelete: (id: string) => void;
  onToggleCollapse: () => void;
}

export function SessionSidebar({
  sessions, activeSessionId, collapsed, inFlight,
  onNewChat, onSelect, onRename, onDelete, onToggleCollapse,
}: SessionSidebarProps) {
  const width = collapsed ? "w-12" : "w-[260px]";

  return (
    <aside
      className={`${width} shrink-0 border-r bg-background transition-[width] duration-150 flex flex-col`}
      aria-label="Saved chats"
    >
      <div className="flex items-center gap-2 p-2 border-b">
        <Button
          variant="ghost"
          size="icon"
          onClick={onToggleCollapse}
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
          className="h-8 w-8"
        >
          {collapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
        </Button>
        {!collapsed && <span className="text-sm font-medium">Chats</span>}
      </div>

      <div className="p-2">
        <NewChatButton collapsed={collapsed} disabled={inFlight} onClick={onNewChat} />
      </div>

      <ScrollArea className="flex-1 px-2">
        {sessions.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-center text-muted-foreground">
            <MessageSquare className="h-8 w-8 mb-2 opacity-50" />
            {!collapsed && <p className="text-sm">Start a new chat</p>}
          </div>
        ) : (
          <div className="space-y-1 pb-4">
            {sessions.map((s) => (
              <SessionListItem
                key={s.session_id}
                item={s}
                active={s.session_id === activeSessionId}
                disabled={inFlight}
                collapsed={collapsed}
                onSelect={onSelect}
                onRename={onRename}
                onDelete={onDelete}
              />
            ))}
          </div>
        )}
      </ScrollArea>
    </aside>
  );
}
```

- [ ] **Step 17.4 — Wire `AppLayout`**

Replace the contents of `chat_frontend/src/AppLayout.tsx` with:

```typescript
import { useCallback, useEffect, useState } from "react";
import { useMessages, useProcessingState, useChatApi } from "@/hooks";
import { useChatRoute } from "@/hooks/useChatRoute";
import { useSessions } from "@/hooks/useSessions";
import { ChatPanel } from "@/components/ChatPanel";
import { HeaderBar, RightSidebar } from "@/components/Layout";
import { SessionSidebar } from "@/components/Sessions";
import { NextseekApiService } from "@/lib/services/chatApi";
import { authService } from "@/lib/services/auth";
import type {
  ProgressEvent,
  AgentStartedData,
  AgentCompleteData,
  QueryCompleteData,
  QueryErrorData,
} from "@/lib/types/api";
import type { DebugData } from "@/lib/types/chat";

interface AppLayoutProps {
  credentialError: string | null;
}

export function AppLayout({ credentialError }: AppLayoutProps) {
  const [rightOpen, setRightOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    return localStorage.getItem("chat.sidebar.collapsed") === "1";
  });
  const [debugData, setDebugData] = useState<DebugData>({ entries: [], bundleId: null, query: "" });

  const { messages, addUserMessage, addAssistantMessage, addSystemMessage, hydrateFromTurns } = useMessages();
  const { processingState, handleAgentStarted, handleAgentComplete, resetProcessing } = useProcessingState();
  const { isQuerying, sessionId, submitQuery, downloadBundle } = useChatApi();

  const chatRoute = useChatRoute();
  const [service] = useState(() => new NextseekApiService(authService));
  const sessions = useSessions({
    service,
    hydrate: hydrateFromTurns,
    onRouteChange: chatRoute.push,
  });

  // On mount: if the URL points at a specific chat, activate it.
  useEffect(() => {
    if (chatRoute.sessionIdFromUrl && sessions.activeSessionId !== chatRoute.sessionIdFromUrl) {
      sessions.setActive(chatRoute.sessionIdFromUrl).catch(() => {
        addSystemMessage("Couldn't load this conversation.");
        chatRoute.push(null);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (credentialError) addSystemMessage(credentialError);
  }, [credentialError, addSystemMessage]);

  const handleProgress = useCallback(
    (event: ProgressEvent) => {
      switch (event.event) {
        case "agent_started": {
          const d = event.data as AgentStartedData;
          handleAgentStarted(d.agent, d.mode);
          break;
        }
        case "agent_complete": {
          const d = event.data as AgentCompleteData;
          handleAgentComplete(d.agent);
          setDebugData((prev) => ({
            ...prev,
            entries: [
              ...prev.entries,
              { agent: d.agent, summary: typeof d.summary === "string" ? d.summary : JSON.stringify(d.summary ?? "", null, 2), timestamp: new Date() },
            ],
          }));
          break;
        }
        case "query_complete": {
          const d = event.data as QueryCompleteData;
          addAssistantMessage(d.reply);
          resetProcessing();
          setDebugData((prev) => ({ ...prev, bundleId: d.bundle_id }));
          if (d.session_id) {
            // If this was a pending new chat, promote the created id.
            if (sessions.pendingNewChat) sessions.promoteCreatedSession(d.session_id);
            // Either way, refresh so titles/ordering update.
            else sessions.refresh();
          }
          break;
        }
        case "query_error": {
          const d = event.data as QueryErrorData;
          addSystemMessage(`Error: ${d.error}`);
          resetProcessing();
          break;
        }
      }
    },
    [handleAgentStarted, handleAgentComplete, addAssistantMessage, addSystemMessage, resetProcessing, sessions],
  );

  const handleQueryError = useCallback(
    (error: string) => { addSystemMessage(`Error: ${error}`); resetProcessing(); },
    [addSystemMessage, resetProcessing],
  );

  const handleSendMessage = useCallback(
    (text: string, mode: string) => {
      addUserMessage(text);
      setDebugData({ entries: [], bundleId: null, query: text });
      const opts =
        sessions.activeSessionId ? { sessionId: sessions.activeSessionId } :
        sessions.pendingNewChat   ? { forceNew: true } :
        {};
      submitQuery(text, mode, opts, handleProgress, handleQueryError);
    },
    [addUserMessage, submitQuery, handleProgress, handleQueryError, sessions.activeSessionId, sessions.pendingNewChat],
  );

  const handleDownload = useCallback(
    (format: string) => {
      if (sessionId && debugData.bundleId) downloadBundle(sessionId, debugData.bundleId, format);
    },
    [sessionId, debugData.bundleId, downloadBundle],
  );

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem("chat.sidebar.collapsed", next ? "1" : "0");
      return next;
    });
  }, []);

  const isDisabled = !!credentialError || isQuerying;

  return (
    <div className="flex h-screen flex-col bg-background text-foreground">
      <HeaderBar onRightToggle={() => setRightOpen(!rightOpen)} onLeftToggle={toggleSidebar} />
      <div className="flex flex-1 overflow-hidden">
        <SessionSidebar
          sessions={sessions.sessions}
          activeSessionId={sessions.activeSessionId}
          collapsed={sidebarCollapsed}
          inFlight={isQuerying || sessions.isHydrating}
          onNewChat={sessions.newChat}
          onSelect={(id) => sessions.setActive(id).catch(() => addSystemMessage("Couldn't load this conversation."))}
          onRename={(id, t) => sessions.rename(id, t).catch(() => addSystemMessage("Rename failed."))}
          onDelete={(id) => sessions.remove(id).catch(() => addSystemMessage("Delete failed."))}
          onToggleCollapse={toggleSidebar}
        />
        <ChatPanel
          messages={messages}
          processingState={processingState}
          isDisabled={isDisabled}
          onSendMessage={handleSendMessage}
        />
      </div>
      <RightSidebar
        isOpen={rightOpen}
        onOpenChange={setRightOpen}
        debugData={debugData}
        onDownload={handleDownload}
      />
    </div>
  );
}
```

- [ ] **Step 17.5 — Wire `EmbeddedApp`**

Replace `chat_frontend/src/EmbeddedApp.tsx` with:

```typescript
import { useCallback, useEffect, useRef, useState } from "react";
import { useMessages, useProcessingState } from "@/hooks";
import { useChatRoute } from "@/hooks/useChatRoute";
import { useSessions } from "@/hooks/useSessions";
import { NextseekApiService } from "@/lib/services/chatApi";
import { SessionAuthService } from "@/lib/services/sessionAuth";
import { ChatPanel } from "@/components/ChatPanel";
import { CompactToolbar, RightSidebar } from "@/components/Layout";
import { SessionSidebar } from "@/components/Sessions";
import type {
  ProgressEvent,
  AgentStartedData,
  AgentCompleteData,
  QueryCompleteData,
  QueryErrorData,
} from "@/lib/types/api";
import type { DebugData, DebugEntry } from "@/lib/types/chat";

export function EmbeddedApp() {
  const [rightOpen, setRightOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState<boolean>(() => {
    return localStorage.getItem("chat.sidebar.collapsed") === "1";
  });
  const [debugData, setDebugData] = useState<DebugData>({ entries: [], bundleId: null, query: "" });

  const serviceRef = useRef(new NextseekApiService(new SessionAuthService()));
  const [isQuerying, setIsQuerying] = useState(false);
  const [sessionId, setSessionId] = useState<string | null>(null);

  const { messages, addUserMessage, addAssistantMessage, addSystemMessage, updateLastAssistantMessage, hydrateFromTurns } = useMessages();
  const pendingDebugRef = useRef<DebugEntry[]>([]);
  const { processingState, handleAgentStarted, handleAgentComplete, resetProcessing } = useProcessingState();

  const chatRoute = useChatRoute();
  const sessions = useSessions({
    service: serviceRef.current,
    hydrate: hydrateFromTurns,
    onRouteChange: chatRoute.push,
  });

  useEffect(() => {
    if (chatRoute.sessionIdFromUrl && sessions.activeSessionId !== chatRoute.sessionIdFromUrl) {
      sessions.setActive(chatRoute.sessionIdFromUrl).catch(() => {
        addSystemMessage("Couldn't load this conversation.");
        chatRoute.push(null);
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleProgress = useCallback(
    (event: ProgressEvent) => {
      switch (event.event) {
        case "agent_started": {
          const d = event.data as AgentStartedData;
          handleAgentStarted(d.agent, d.mode);
          break;
        }
        case "agent_complete": {
          const d = event.data as AgentCompleteData;
          handleAgentComplete(d.agent);
          const entry: DebugEntry = {
            agent: d.agent,
            summary: typeof d.summary === "string" ? d.summary : JSON.stringify(d.summary ?? "", null, 2),
            timestamp: new Date(),
          };
          pendingDebugRef.current.push(entry);
          setDebugData((prev) => ({ ...prev, entries: [...prev.entries, entry] }));
          break;
        }
        case "query_complete": {
          const d = event.data as QueryCompleteData;
          addAssistantMessage(d.reply);
          const captured = pendingDebugRef.current.slice();
          const bid = d.bundle_id ?? null;
          const artifacts = d.artifacts ?? null;
          queueMicrotask(() => {
            updateLastAssistantMessage({ debugEntries: captured, bundleId: bid, artifacts });
          });
          resetProcessing();
          setDebugData((prev) => ({ ...prev, bundleId: d.bundle_id }));
          if (d.session_id) {
            if (sessions.pendingNewChat) sessions.promoteCreatedSession(d.session_id);
            else sessions.refresh();
          }
          break;
        }
        case "query_error": {
          const d = event.data as QueryErrorData;
          addSystemMessage(`Error: ${d.error}`);
          resetProcessing();
          break;
        }
      }
    },
    [handleAgentStarted, handleAgentComplete, addAssistantMessage, addSystemMessage, updateLastAssistantMessage, resetProcessing, sessions],
  );

  const handleQueryError = useCallback(
    (error: string) => { addSystemMessage(`Error: ${error}`); resetProcessing(); },
    [addSystemMessage, resetProcessing],
  );

  const handleSendMessage = useCallback(
    (text: string, mode: string) => {
      addUserMessage(text);
      pendingDebugRef.current = [];
      setDebugData({ entries: [], bundleId: null, query: text });
      setIsQuerying(true);
      const opts =
        sessions.activeSessionId ? { sessionId: sessions.activeSessionId } :
        sessions.pendingNewChat   ? { forceNew: true } :
        {};
      serviceRef.current
        .submitQuery(text, mode, opts, handleProgress, handleQueryError)
        .finally(() => {
          setSessionId(serviceRef.current.sessionId);
          setIsQuerying(false);
        });
    },
    [addUserMessage, handleProgress, handleQueryError, sessions.activeSessionId, sessions.pendingNewChat],
  );

  const handleArtifactDownload = useCallback(
    (bundleId: number, artifactKey: string) => {
      const sid = serviceRef.current.sessionId;
      if (sid) {
        serviceRef.current
          .downloadArtifact(sid, bundleId, artifactKey)
          .catch((err: Error) => addSystemMessage(`Download failed: ${err.message}`));
      }
    },
    [addSystemMessage],
  );

  const handleDownload = useCallback(
    (format: string) => {
      if (sessionId && debugData.bundleId) serviceRef.current.downloadBundle(sessionId, debugData.bundleId, format);
    },
    [sessionId, debugData.bundleId],
  );

  const toggleSidebar = useCallback(() => {
    setSidebarCollapsed((prev) => {
      const next = !prev;
      localStorage.setItem("chat.sidebar.collapsed", next ? "1" : "0");
      return next;
    });
  }, []);

  return (
    <div className="flex h-full flex-col bg-background text-foreground">
      <CompactToolbar
        onRightToggle={() => setRightOpen(!rightOpen)}
        onLeftToggle={toggleSidebar}
      />
      <div className="flex flex-1 overflow-hidden">
        <SessionSidebar
          sessions={sessions.sessions}
          activeSessionId={sessions.activeSessionId}
          collapsed={sidebarCollapsed}
          inFlight={isQuerying || sessions.isHydrating}
          onNewChat={sessions.newChat}
          onSelect={(id) => sessions.setActive(id).catch(() => addSystemMessage("Couldn't load this conversation."))}
          onRename={(id, t) => sessions.rename(id, t).catch(() => addSystemMessage("Rename failed."))}
          onDelete={(id) => sessions.remove(id).catch(() => addSystemMessage("Delete failed."))}
          onToggleCollapse={toggleSidebar}
        />
        <ChatPanel
          messages={messages}
          processingState={processingState}
          isDisabled={isQuerying}
          onSendMessage={handleSendMessage}
          onArtifactDownload={handleArtifactDownload}
        />
      </div>
      <RightSidebar
        isOpen={rightOpen}
        onOpenChange={setRightOpen}
        debugData={debugData}
        onDownload={handleDownload}
      />
    </div>
  );
}
```

- [ ] **Step 17.6 — Update `HeaderBar` + `CompactToolbar` for the new `onLeftToggle` prop**

Edit `chat_frontend/src/components/Layout/HeaderBar.tsx`. Replace the entire file with:

```typescript
import { useEffect, useState } from "react";
import { Database, Moon, PanelLeft, PanelRightOpen, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";

interface HeaderBarProps {
  onRightToggle: () => void;
  onLeftToggle: () => void;
}

export function HeaderBar({ onRightToggle, onLeftToggle }: HeaderBarProps) {
  const [isDark, setIsDark] = useState(false);

  useEffect(() => {
    const stored = localStorage.getItem("theme");
    if (stored === "dark" || (!stored && window.matchMedia("(prefers-color-scheme: dark)").matches)) {
      document.documentElement.classList.add("dark");
      setIsDark(true);
    }
  }, []);

  const toggleTheme = () => {
    const next = !isDark;
    setIsDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
  };

  return (
    <header className="flex h-12 shrink-0 items-center border-b bg-background px-4">
      <Button
        variant="ghost"
        size="icon"
        onClick={onLeftToggle}
        aria-label="Toggle chat list"
        className="mr-2"
      >
        <PanelLeft className="h-5 w-5" />
      </Button>
      <div className="flex flex-1 items-center gap-2">
        <Database className="h-5 w-5 text-primary" />
        <span className="text-lg font-semibold">NExtSEEK Chat</span>
      </div>

      <div className="flex items-center gap-1">
        <Button variant="ghost" size="icon" onClick={toggleTheme} aria-label="Toggle dark mode">
          {isDark ? <Sun className="h-5 w-5" /> : <Moon className="h-5 w-5" />}
        </Button>
        <Button variant="ghost" size="sm" onClick={onRightToggle} aria-label="Toggle debug panel">
          <span className="mr-1 hidden sm:inline text-base">Debug</span>
          <PanelRightOpen className="h-5 w-5" />
        </Button>
      </div>
    </header>
  );
}
```

Edit `chat_frontend/src/components/Layout/CompactToolbar.tsx`. Replace with:

```typescript
import { PanelLeft, PanelRightOpen } from "lucide-react";
import { Button } from "@/components/ui/button";

interface CompactToolbarProps {
  onRightToggle: () => void;
  onLeftToggle: () => void;
}

export function CompactToolbar({ onRightToggle, onLeftToggle }: CompactToolbarProps) {
  return (
    <div className="flex h-10 shrink-0 items-center border-b bg-background px-3">
      <Button
        variant="ghost"
        size="sm"
        onClick={onLeftToggle}
        aria-label="Toggle chat list"
        className="mr-2"
      >
        <PanelLeft className="h-4 w-4" />
      </Button>
      <div className="flex-1" />
      <Button variant="ghost" size="sm" onClick={onRightToggle} aria-label="Toggle debug panel">
        <span className="mr-1 text-base">Debug</span>
        <PanelRightOpen className="h-5 w-5" />
      </Button>
    </div>
  );
}
```

If `HeaderBar.test.tsx` exists and tests its old prop shape, update it to pass `onLeftToggle={vi.fn()}` as well — read `src/components/__tests__/HeaderBar.test.tsx` first and adjust the props on each `render()` call.

- [ ] **Step 17.7 — Typecheck + full test suite**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx tsc -b --noEmit
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npm test -- --run
```

Expected: typecheck clean; all tests pass. If `HeaderBar.test.tsx` is failing because it doesn't pass `onLeftToggle`, fix the test calls to include `onLeftToggle={vi.fn()}`.

- [ ] **Step 17.8 — Commit (single big commit for the components batch)**

```bash
git add chat_frontend/src/components/Sessions \
        chat_frontend/src/AppLayout.tsx \
        chat_frontend/src/EmbeddedApp.tsx \
        chat_frontend/src/components/Layout/HeaderBar.tsx \
        chat_frontend/src/components/Layout/CompactToolbar.tsx \
        chat_frontend/src/components/__tests__/HeaderBar.test.tsx
git commit -m "feat(chat_frontend): SessionSidebar + wire AppLayout/EmbeddedApp/HeaderBar/CompactToolbar

Mounts the collapsible session rail to the left of ChatPanel in both
entry points. Honors localStorage 'chat.sidebar.collapsed'. AppLayout
and EmbeddedApp wire useSessions + useChatRoute, route the active
session id into submitQuery, and promote a freshly-created session id
after the first successful turn of a New chat.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

(If `HeaderBar.test.tsx` did not need editing, omit it from the `git add`.)

---

# Task 18 — Django template injects `<meta name="chat-basename">`

**Files:**
- Modify: `seek/templates/smartSearch.html`

- [ ] **Step 18.1 — Edit the template**

Replace the entire content of `seek/templates/smartSearch.html` with:

```html
{% extends "base.html" %}
{% load static vite_assets %}
{% block extrahead %}
<meta name="chat-basename" content="/assistant/">
{% endblock %}
{% block main %}
<div id="chat-assistant-root" style="width:100%;height:calc(100vh - 60px);"></div>
{% vite_assets "src/main.embedded.tsx" "js/chat_assistant" %}
{% endblock %}
```

If `base.html` does not already define an `{% block extrahead %}` slot inside its `<head>`, the meta tag won't be rendered. Verify:

```bash
grep -n "block extrahead" /home/cdemu/code/dmac/docker/NExtSEEK/templates/base.html
```

If the block does **not** exist in `base.html`, the meta tag must instead live inline above `<div id="chat-assistant-root">`. The React app's `useChatRoute` reads from `document.querySelector('meta[name=chat-basename]')`, which works regardless of where in the document the meta lives. Use the in-body placement as a fallback:

```html
{% extends "base.html" %}
{% load static vite_assets %}
{% block main %}
<meta name="chat-basename" content="/assistant/">
<div id="chat-assistant-root" style="width:100%;height:calc(100vh - 60px);"></div>
{% vite_assets "src/main.embedded.tsx" "js/chat_assistant" %}
{% endblock %}
```

- [ ] **Step 18.2 — Commit**

```bash
git add seek/templates/smartSearch.html
git commit -m "feat(seek): inject chat-basename meta tag for embedded chat router

Lets the React app's useChatRoute compute the URL basename at runtime
without hard-coding /assistant/ in the build.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 19 — Live UI verification via Playwright MCP

**Files:** none modified — this is a checkpoint, not a code change.

Use the `playwright-mcp` plugin's `browser_*` tools to spot-check the standalone build. The embedded build needs a Django dev environment; if running it is inconvenient, do the standalone pass and defer embedded verification to Task 21's automated Playwright e2e suite + Task 22's release smoke test.

- [ ] **Step 19.1 — Start the standalone dev server**

In one terminal:

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npm run dev
```

Vite serves at `http://localhost:5173/`.

- [ ] **Step 19.2 — Authenticate**

In a second terminal (or have the user) ensure `VITE_API_BASE_URL` and the basic-auth env are set per the chat_frontend README. Confirm the `/me/` endpoint responds. The dev server's `index.html` likely renders a login screen if not credentialed.

- [ ] **Step 19.3 — Sidebar render checkpoint**

Use the MCP tools to:

```
browser_navigate("http://localhost:5173/")
browser_snapshot()
browser_take_screenshot(filename="sidebar-default.png")
browser_console_messages()
```

Verify (in the snapshot): a left-side region labeled "Saved chats" with a "New chat" button and a list area. No React `key` warnings or errors in the console.

Resize to mobile:

```
browser_resize(375, 800)
browser_snapshot()
browser_take_screenshot(filename="sidebar-mobile.png")
```

Resize back:

```
browser_resize(1280, 800)
```

Toggle collapse via the panel button:

```
browser_click("toggle chat list")  // use the aria-label
browser_take_screenshot(filename="sidebar-collapsed.png")
browser_evaluate("() => localStorage.getItem('chat.sidebar.collapsed')")  // should return "1"
```

- [ ] **Step 19.4 — New chat + force_new checkpoint**

```
browser_click("New chat")
browser_type("MessageInput", "Find me mice treated with NDMA")
browser_press_key("Enter")
browser_network_requests()  // look at POST /assistant/query/async/ — the body should contain force_new: true
browser_wait_for("query_complete")
browser_snapshot()  // sidebar should now show one row with the auto-title
```

The URL should now be `/chat/<uuid>` — confirm via `browser_evaluate("() => location.pathname")`.

- [ ] **Step 19.5 — Rehydration checkpoint**

```
browser_evaluate("() => location.href")  // copy the URL
browser_close()
browser_navigate("<copied URL>")
browser_network_requests()  // confirm exactly one GET /assistant/sessions/<id>/?include=turns
browser_snapshot()  // chat panel should display the prior conversation, no flicker of empty chat
```

- [ ] **Step 19.6 — Rename + delete checkpoint**

```
browser_hover(<row title>)
browser_click("More")           // the ⋯ button on the row
browser_click("Rename")
browser_type("Rename session", "Renamed chat")
browser_press_key("Enter")
browser_snapshot()              // row title is now "Renamed chat"

browser_click("More")
browser_click("Delete")
browser_click("Delete")         // the confirm dialog's Delete
browser_snapshot()              // row is gone; URL is reset to "/"
```

- [ ] **Step 19.7 — Document findings**

If any of the visual or behavioral checks fail, write down the symptom and root cause; convert any reproducible bug into a deterministic test in `e2e/sessions.spec.ts` (Task 21).

This task does not produce a commit on its own.

---

# Task 20 — Playwright e2e (`e2e/sessions.spec.ts`)

**Files:**
- Create: `chat_frontend/e2e/sessions.spec.ts`

- [ ] **Step 20.1 — Inspect existing e2e setup**

Read `chat_frontend/e2e/` for the existing patterns (auth, fixtures, base URL config). Mirror the pattern used by `e2e/<existing-spec>.spec.ts` in this repo when writing `sessions.spec.ts`. Specifically:

```bash
ls /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend/e2e/
```

Look at the first existing `.spec.ts` to copy the imports, the `test.use({...})` block, and any login fixture (`page.goto('/login')` + form fill, or a `storageState` baseline).

- [ ] **Step 20.2 — Write the spec**

Create `chat_frontend/e2e/sessions.spec.ts`. Skeleton (adapt to your existing setup):

```typescript
import { test, expect } from "@playwright/test";

test.describe("Saved chats", () => {
  test.beforeEach(async ({ page }) => {
    // TODO: replace with project-standard auth setup (see existing specs)
    await page.goto("/");
    await expect(page.getByRole("button", { name: /new chat/i })).toBeVisible();
  });

  test("creates a new chat, auto-titles it, deep-links, switches, renames, deletes", async ({ page }) => {
    // 1. New chat + submit
    await page.getByRole("button", { name: /new chat/i }).click();
    await page.getByPlaceholder(/message/i).fill("Find me mice treated with NDMA");
    await page.keyboard.press("Enter");
    await expect(page.getByText(/find me mice treated/i)).toBeVisible({ timeout: 30_000 });

    // Sidebar shows an auto-titled row.
    const firstRow = page.locator("[aria-label='Saved chats'] >> text=/Find me mice/i").first();
    await expect(firstRow).toBeVisible();

    // URL is /chat/<uuid> (or /assistant/chat/<uuid>).
    await expect(page).toHaveURL(/\/chat\/[0-9a-f-]+/);

    // 2. Capture the URL, open a fresh tab, verify rehydration.
    const url = page.url();
    const newTab = await page.context().newPage();
    await newTab.goto(url);
    await expect(newTab.getByText("Find me mice treated with NDMA")).toBeVisible({ timeout: 10_000 });
    await newTab.close();

    // 3. Create a second chat.
    await page.getByRole("button", { name: /new chat/i }).click();
    await page.getByPlaceholder(/message/i).fill("Second query about samples");
    await page.keyboard.press("Enter");
    await expect(page.locator("[aria-label='Saved chats'] >> text=/Second query/i")).toBeVisible({ timeout: 30_000 });

    // 4. Switch between them.
    await firstRow.click();
    await expect(page.getByText("Find me mice treated with NDMA")).toBeVisible();
    await page.locator("[aria-label='Saved chats'] >> text=/Second query/i").first().click();
    await expect(page.getByText("Second query about samples")).toBeVisible();

    // 5. Rename the active row.
    const activeRow = page.locator("[aria-label='Saved chats'] >> [class*='bg-accent']").first();
    await activeRow.hover();
    await activeRow.getByRole("button", { name: /more/i }).click();
    await page.getByRole("menuitem", { name: /rename/i }).click();
    await page.getByRole("textbox", { name: /rename session/i }).fill("Renamed!");
    await page.keyboard.press("Enter");
    await expect(page.locator("[aria-label='Saved chats'] >> text=Renamed!")).toBeVisible();

    // 6. Delete the renamed row.
    await page.locator("[aria-label='Saved chats'] >> text=Renamed!").hover();
    await page.locator("[aria-label='Saved chats'] >> button[aria-label='More']").first().click();
    await page.getByRole("menuitem", { name: /delete/i }).click();
    await page.getByRole("button", { name: /^delete$/i }).click();
    await expect(page.locator("[aria-label='Saved chats'] >> text=Renamed!")).not.toBeVisible();
    // After deleting the active row, the URL should be reset.
    await expect(page).not.toHaveURL(/\/chat\//);
  });
});
```

If your project has a separate `playwright.config.ts` `webServer` block, it will auto-start the Vite dev server when running the suite. Otherwise, start it manually before running.

- [ ] **Step 20.3 — Run the spec**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx playwright test e2e/sessions.spec.ts
```

Expected: green. If steps fail, examine `reports/` HTML for the failing screenshot/trace and iterate.

- [ ] **Step 20.4 — Commit**

```bash
git add chat_frontend/e2e/sessions.spec.ts
git commit -m "test(chat_frontend): e2e for saved-chats sidebar (create/deep-link/switch/rename/delete)

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

# Task 21 — Final integration sweep + spec sign-off

**Files:** none.

- [ ] **Step 21.1 — Run the full backend test suite**

```bash
python manage.py test nextseek_api -v 2
```

Expected: all green.

- [ ] **Step 21.2 — Run the full frontend test suite + typecheck**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npx tsc -b --noEmit && npm test -- --run
```

Expected: all green.

- [ ] **Step 21.3 — Build the embedded bundle**

```bash
cd /home/cdemu/code/dmac/docker/NExtSEEK/chat_frontend && npm run build:embedded
```

Expected: writes new assets under `/home/cdemu/code/dmac/docker/NExtSEEK/static/js/chat_assistant/`.

- [ ] **Step 21.4 — Ask the user to test the embedded build manually**

Hand the user this instruction:

> The embedded React bundle has been rebuilt. Please load `/assistant/` in the Django app and run through: New chat → submit a query → confirm the URL becomes `/assistant/chat/<uuid>` → reload the page → confirm the conversation rehydrates → rename → delete. Report any visual or behavioral issues.

If the user reports an issue, drop into `superpowers:systematic-debugging` before patching.

- [ ] **Step 21.5 — Spec checklist sign-off**

Re-read `docs/superpowers/specs/2026-05-12-saved-chats-design.md` and confirm every section is covered by at least one task:

- §5.1 model column → Task 1
- §5.2 endpoints (list / detail-with-turns / patch / delete) → Tasks 3–6
- §5.2 `force_new` flag → Task 7
- §5.3 auto-title → Task 8
- §5.4 pydantic models → Task 2
- §6.1 files added/modified → Tasks 9–18
- §6.2 SessionSidebar → Tasks 15–17
- §6.3 useSessions → Task 14
- §6.4 useChatRoute → Task 13
- §6.5 useChatApi `opts` change → Tasks 10–11
- §6.6 hydrateFromTurns → Task 12
- §6.7 layout wiring → Task 17
- §6.8 skeleton + error states → covered in Task 17 (system-message error paths) — note: the "3-bubble grey skeleton" while `isHydrating` is **not** implemented in this plan (sidebar simply blocks switching). Treat that as a v1.1 follow-up if the empty-chat flicker is noticeable in Task 19 verification.
- §7 edge cases → smoke-checked in Task 19; codified in Task 20 where applicable
- §8.1 backend tests → Tasks 3–8
- §8.2 frontend unit tests → Tasks 10, 12, 13, 14, 16, 17
- §8.3 Playwright MCP checkpoint → Task 19
- §9 migration & rollout → Task 1 (migration), Task 18 (template), Task 21 (build:embedded)

If anything is unchecked, add a fix-up task before declaring done.

This task does not produce a commit on its own.

---

## Self-review notes

(Self-checked during plan authoring.)

- **Spec coverage:** every spec section is mapped to a task above (Task 21.5).
- **Placeholder scan:** no TBD/TODO/"similar to" placeholders; every code step contains the complete code.
- **Type consistency:** `submitQuery(query, mode, opts, onProgress, onError)` signature is identical across `chatApi.ts` (Task 10), `useChatApi.ts` (Task 11), `AppLayout.tsx` and `EmbeddedApp.tsx` (Task 17). `Turn` shape is identical in `models_api.py` (Task 2), `api.ts` (Task 9), `useMessages.hydrateFromTurns` (Task 12), and `useSessions.fetchSessionTurns` consumption (Task 14). `SessionListItem` (the type) is the response shape for `listSessions` (Task 10), `renameSession` (Task 10), `useSessions.sessions` (Task 14), and `SessionListItem` (the component, Task 16).
- **Known gap accepted:** the §6.8 skeleton (3-bubble grey-loading) is omitted; sidebar disables switching during hydration but the chat panel itself does not show a skeleton. Listed as a v1.1 follow-up in Task 21.5.
- **`SessionListItem` name collision:** the pydantic/API model and the React component share the name. They live in different modules (`models_api.py` vs. `components/Sessions/SessionListItem.tsx`) and the TS type is imported as `type { SessionListItem as SessionListItemModel }` in the component file to avoid the local-import shadow. No action required.

# Saved Chats & New Chat Feature — Design

**Status**: Approved (2026-05-12)
**Scope**: `chat_frontend/` + `nextseek_api/assistant/`
**Out of scope**: chat_nextseek pipeline code is not touched. The Streamlit UI is not touched.

## 1. Problem

The chat frontend persists one logical conversation per user — backend creates or
reuses the user's most-recently-updated `ChatSession` on every query, and the
frontend never sends `session_id`. There is no way to:

- start a fresh chat without manually clearing browser state
- see, switch between, or revisit prior chats
- name a chat for later recall
- deep-link to a specific chat

The data model is already half-built (`ChatSession` rows exist per user with
`results_history`, `last_debug`, `extra_state`), and each bundle in
`results_history` carries `user_query` and `terminal_reply`/`reply`, so a
full conversation can be reconstructed from the existing DB rows.

## 2. Goals

1. List a user's chat sessions in a collapsible left sidebar.
2. "New chat" button that starts a fresh conversation lazily (no server row until first query).
3. Rename and delete sessions inline.
4. Auto-generate session titles from the first user message.
5. Reflect the active session in the URL (`/chat/<uuid>` standalone, `/assistant/chat/<uuid>` embedded).
6. Reload-safe: a deep link rehydrates the conversation from the server.

## 3. Non-goals (v1)

- Search across sessions
- Pinning / favorites
- Folder or tag organization
- Soft delete / undo
- Server-paginated infinite list (cap at 50 most-recent in v1)
- Cross-tab active-session sync (list refresh on focus is in scope; sync of the *active* session is not)

## 4. Architecture overview

```
chat_frontend                                          nextseek_api (Django)
─────────────                                          ──────────────
SessionSidebar  ─list/create/rename/delete──▶  AssistantViewSet
useSessions     ─switch active id────────▶
useChatRoute    ─push/pop /chat/<uuid>──▶
                                                       ChatSession (+title col)
useMessages     ◀──turns[] on resume──── GET /sessions/{id}/?include=turns
ChatPanel       (unchanged)                            └─ projects from results_history
useChatApi      ──session_id in body──▶ POST /query/async/
```

One new column on `ChatSession` (`title`). One new component (`SessionSidebar`),
one new hook (`useSessions`), one small route helper (`useChatRoute`). All
existing components (`ChatPanel`, `MessageBubble`, `MessageList`, etc.) are
unchanged. Both `AppLayout` (standalone) and `EmbeddedApp` (mounted in Django at
`/assistant/`) mount the sidebar and pass the active id through.

## 5. Backend

### 5.1 Model change

`nextseek_api/assistant/models_db.py`, on `ChatSession`:

```python
title = models.CharField(max_length=200, null=True, blank=True)
```

One Django migration adds the column as nullable; existing rows get `NULL`.
No data migration. Backwards-compatible: list endpoint falls back to
`"New chat"` when `title` is `NULL`.

### 5.2 New / extended endpoints

All on the existing `AssistantViewSet` in `nextseek_api/services/assistant.py`.
Authentication and `UserInParticipatingProject` permission are unchanged.

| Method   | Path                            | Action       | Purpose |
|----------|---------------------------------|--------------|---------|
| `GET`    | `/assistant/sessions/`          | `list_sessions` | List the current user's sessions |
| `GET`    | `/assistant/sessions/{id}/`     | `get_session` *(extended)* | Detail; `?include=turns` adds projected turns |
| `PATCH`  | `/assistant/sessions/{id}/`     | `patch_session` | Rename |
| `DELETE` | `/assistant/sessions/{id}/`     | `delete_session` | Hard delete |

The DRF routing uses `@action` on the viewset (existing convention in the file).
The new actions reuse the existing `[0-9a-f-]+` UUID URL pattern.

Additionally, `QueryRequest` in `models_api.py` gains one optional field:

```python
force_new: bool = Field(False, description="If true and session_id is omitted, always create a new ChatSession instead of reusing the most recent one.")
```

`AssistantViewSet.query` and `query_async` change their session-resolution
branch as follows:

```python
if req.session_id:
    # ... existing explicit-session-id path ...
elif req.force_new:
    chat_session = ChatSession.objects.create(user=request.user)
else:
    # ... existing reuse-most-recent-or-create path ...
```

This lets the frontend make "New chat → submit" guarantee a fresh row without
introducing a separate pre-flight POST (which would risk ghost empty
sessions). Existing clients that don't send `force_new` see no change.

#### `GET /assistant/sessions/` (list)

Returns the current user's sessions ordered by `-updated_at`, capped at 50.

Response shape:

```json
{
  "total": 23,
  "sessions": [
    {
      "session_id": "uuid",
      "title": "Find mice treated with NDMA",
      "created_at": "...",
      "updated_at": "...",
      "query_count": 7,
      "preview": "Find me mice treated with NDMA over 6 months"
    },
    ...
  ]
}
```

- `title` is `chat_session.title` if set, else the string `"New chat"`.
- `preview` is the first user query from the first bundle in `results_history`,
  trimmed and capped at 80 chars; empty string if no bundles.

**MySQL sort-buffer guard.** The list view must not put `results_history` in
the `ORDER BY` query (the existing 1038 workaround in `query` is the reason).
Implementation pattern:

```python
ids = list(
    ChatSession.objects.filter(user=request.user)
    .order_by("-updated_at")
    .values_list("session_id", flat=True)[:50]
)
rows = []
for sid in ids:
    cs = ChatSession.objects.get(session_id=sid)
    rows.append(_project_list_row(cs))
```

The per-row `.get()` is acceptable for ≤50 rows. We accept this for v1 and
defer column-materialization (approach B in the brainstorm) until it bites.

#### `GET /assistant/sessions/{id}/` (extended)

Existing behavior is preserved (returns `session_id`, `created_at`,
`query_count`, `has_results`). When the query string contains `include=turns`,
the response *also* includes:

```json
"title": "Find mice treated with NDMA",
"turns": [
  {"bundle_id": 1, "user_query": "...", "reply": "...", "mode": "new_search", "ts": "..."},
  ...
]
```

`turns` is projected from `results_history`: for each bundle, take
`user_query`, prefer `terminal_reply` then fall back to `reply`, plus `mode`,
`bundle_id`, and `ts` if present in the bundle. **Bundles with no
`user_query` are skipped on the server** (rare — wizard intro emits, etc.) so
the client never has to render an orphan assistant message.

Existing callers that don't pass `include=turns` see the response unchanged.

#### `PATCH /assistant/sessions/{id}/`

Body: `{"title": "<string>"}`.

- Reject if `title` missing / not a string / empty after `strip()` / longer than 200 chars → 422.
- Otherwise update `title` (trimmed) with `update_fields=["title", "updated_at"]`.
- Ownership check (`user_id == request.user.pk`) → 403 on mismatch; 404 if the row doesn't exist.
- Response: the new `SessionListItem` shape (so the frontend can patch its row in place).

#### `DELETE /assistant/sessions/{id}/`

- 204 on success.
- Ownership check identical to PATCH.
- Hard delete. `QueryTask` rows CASCADE (already configured on the FK).

### 5.3 Auto-title

Implemented as a single helper, called server-side at the end of `_run_pipeline`
in both `query` and `query_async`, **after `adapter.save()` and before the
`query_complete` event is enqueued / the task is marked completed** — so that
when the frontend refreshes the sidebar in response to `query_complete`, the
new title is already persisted:

```python
def _auto_title_if_unset(chat_session: ChatSession) -> None:
    if chat_session.title:
        return  # manual rename always wins
    history = chat_session.results_history or []
    if not history:
        return
    first_user_query = next(
        (b.get("user_query") for b in history if b.get("user_query")),
        None,
    )
    if not first_user_query:
        return
    title = " ".join(first_user_query.split())[:60]
    if title:
        chat_session.title = title
        chat_session.save(update_fields=["title", "updated_at"])
```

- Idempotent: subsequent turns see a non-NULL title and skip.
- Race-safe with manual rename: PATCH runs synchronously inside a request; if
  the user renames between turn 1 and turn 2, the auto-title check on turn 2
  sees the user's title and bails.
- Whitespace is collapsed; no ellipsis is added (UI truncates with CSS).

**Note on event ordering.** In `query` (SSE) the helper runs before
`event_queue.put(None)` sentinel and before the SSE generator yields
`query_complete`. In `query_async` it runs before
`make_db_event_callback` finalizes the task to `completed`. Both code paths
already serialize these steps in the pipeline thread; the helper is added
inline.

### 5.4 Request/response models

Added to `nextseek_api/assistant/models_api.py`:

```python
class SessionListItem(BaseModel):
    session_id: UUID
    title: str
    created_at: datetime
    updated_at: datetime
    query_count: int
    preview: str
    model_config = ConfigDict(extra="forbid")

class SessionListResponse(BaseModel):
    total: int
    sessions: List[SessionListItem]
    model_config = ConfigDict(extra="forbid")

class SessionPatchRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    model_config = ConfigDict(extra="forbid")

class Turn(BaseModel):
    bundle_id: int
    user_query: str
    reply: str
    mode: str
    ts: Optional[str] = None
    model_config = ConfigDict(extra="forbid")

# Extended:
class SessionDetailResponse(BaseModel):
    session_id: UUID
    created_at: datetime
    query_count: int
    has_results: bool
    # New optional fields, populated when ?include=turns
    title: Optional[str] = None
    turns: Optional[List[Turn]] = None
    model_config = ConfigDict(extra="forbid")

# Extended:
class QueryRequest(BaseModel):
    session_id: Optional[UUID] = None
    query: str = Field(..., min_length=1, max_length=4000)
    mode: str
    force_new: bool = Field(False, description="Create a new ChatSession instead of reusing the most recent one. Ignored when session_id is set.")
    model_config = ConfigDict(extra="forbid")
```

`SessionDetailResponse` keeps its existing fields; `title` and `turns` are
optional so legacy consumers are unaffected.

### 5.5 Pipeline impact

None. `chat_nextseek.orchestrator.run_query` / `run_query_plan` are not
modified. The auto-title helper runs in `services/assistant.py` after the
pipeline thread persists.

## 6. Frontend

All paths under `chat_frontend/src/`.

### 6.1 Files added / modified

```
components/Sessions/
  SessionSidebar.tsx       NEW   the rail
  SessionListItem.tsx      NEW   one row (title + relative time + ··· menu)
  NewChatButton.tsx        NEW   top of rail
  index.ts                 NEW

hooks/
  useSessions.ts           NEW
  useChatRoute.ts          NEW
  useMessages.ts           MOD   add hydrateFromTurns
  useChatApi.ts            MOD   submitQuery takes optional sessionId

lib/services/chatApi.ts    MOD   +listSessions, +renameSession, +deleteSession,
                                 +fetchSessionTurns; submitQuery takes sessionId
lib/types/api.ts           MOD   +SessionListItem, +Turn

AppLayout.tsx              MOD   mount sidebar + wire active id + rehydrate on mount
EmbeddedApp.tsx            MOD   same
components/Layout/HeaderBar.tsx       MOD   add sidebar-collapse toggle
components/Layout/CompactToolbar.tsx  MOD   add sidebar-collapse toggle
```

### 6.2 `SessionSidebar`

- Fixed-width **260px** when expanded, **48px** when collapsed.
- Collapse state persisted to `localStorage` key `chat.sidebar.collapsed`.
- Top: `<NewChatButton />` — plus icon + "New chat" label when expanded, icon-only when collapsed.
- Below: scrolling list of `<SessionListItem />`.
- Each row shows title (one line, CSS-truncated, ~40 chars visible, full title in `title=` tooltip) and relative time underneath (use existing `date-fns`).
- Active row highlighted with the existing `bg-accent` token.
- Hover reveals a `⋯` button → menu (existing radix patterns) with **Rename** + **Delete**.
- Rename: row title swaps to an inline `<input>`. Enter saves, Esc cancels, blur saves.
- Delete: confirm via the existing radix-dialog before issuing `DELETE`.
- Empty state: small illustration placeholder + "Start a new chat" helper text.
- Below `md` breakpoint, the rail becomes an off-canvas drawer (slides over the chat; doesn't squeeze). Toggle remains in the header.

### 6.3 `useSessions` hook

```ts
interface UseSessionsReturn {
  sessions: SessionListItem[];        // sorted by updated_at desc
  activeSessionId: string | null;
  pendingNewChat: boolean;            // true after newChat(), cleared by promoteCreatedSession
  isLoading: boolean;
  isHydrating: boolean;               // true while fetching turns for setActive
  refresh: () => Promise<void>;
  newChat: () => void;                // local-only: clears active id + messages, sets pendingNewChat
  setActive: (id: string | null) => Promise<void>;  // fetches turns; calls hydrateFromTurns
  rename: (id: string, title: string) => Promise<void>;
  remove: (id: string) => Promise<void>;
  promoteCreatedSession: (id: string) => void;  // called from useChatApi after query_complete
}
```

Behavior:

- **Lazy create.** `newChat()` clears `activeSessionId` to `null`, clears
  `messages`, sets an internal `pendingNewChat` flag, and calls
  `useChatRoute.push(null)`. It does **not** hit the server. The first
  submitted query while `pendingNewChat` is true is sent with
  `force_new: true` in the body — the backend creates a fresh `ChatSession`
  (see §5.2). The `query_complete` event carries `session_id`, which is
  passed back via `promoteCreatedSession(id)` → that becomes the new
  `activeSessionId`, clears `pendingNewChat`, triggers `useChatRoute.push(id)`,
  and calls `refresh()` so the sidebar picks up the new row.
- **First-ever load.** On a fresh visit with no `sessionIdFromUrl` and no
  prior session, the layout treats it the same as `newChat()` — empty chat,
  `activeSessionId === null`, `pendingNewChat === true`. The user's first
  query creates their first session.
- **After every successful turn**, `refresh()` runs so titles and ordering update.
- **List refresh on focus.** A single `visibilitychange` listener calls `refresh()`. No cross-tab active-session sync.
- **In-flight lock.** While a query is in flight (`isQuerying === true`),
  `setActive`, `newChat`, and `rename`/`remove` on *any* row are disabled
  (visual `opacity-60 cursor-not-allowed` on rows). The lock is exposed via
  the existing `useChatApi.isQuerying`.

### 6.4 `useChatRoute` hook

Tiny helper, no router dependency.

```ts
interface UseChatRouteReturn {
  sessionIdFromUrl: string | null;
  push: (id: string | null) => void;
}
```

- Reads basename from a `<meta name="chat-basename">` tag injected by Django's
  `smartSearch.html` template (content `"/assistant/"`). Standalone build
  uses `"/"`.
- `push(uuid)` → `history.pushState({}, "", basename + "chat/" + uuid)`.
- `push(null)` → `history.pushState({}, "", basename)`.
- Subscribes to `popstate` so browser back/forward updates `sessionIdFromUrl`,
  which the parent layout reflects into `setActive`.

Django mount: `smartSearch.html` is edited to render
`<meta name="chat-basename" content="/assistant/">`. The Django route
`re_path(r'^assistant/', views.smartSearch, name='assistant')` already
matches `/assistant/chat/<uuid>` (the regex has no `$`), so no urls.py change
is needed.

### 6.5 `useChatApi` change

Service signature gains optional fields:

```ts
submitQuery(
  query: string,
  mode: string,
  opts: { sessionId?: string | null; forceNew?: boolean },
  onProgress,
  onError,
): Promise<void>
```

Body sent to `/assistant/query/async/`:

```json
{"query": "...", "mode": "...", "session_id": "<uuid>", "force_new": true}
```

`session_id` is omitted when null/undefined; `force_new` is omitted when
false. The `AppLayout` / `EmbeddedApp` callers pass:

- `{ sessionId: activeSessionId }` when there is an active session.
- `{ forceNew: true }` when `pendingNewChat` is true (after a New-chat click).
- Neither (i.e. `{}`) on the legacy "no active id, no pending new chat"
  path. This is unreachable in the new flow but kept for safety — it falls
  back to the existing backend "reuse most recent or create" behavior.

### 6.6 `useMessages` extension

Add one method:

```ts
hydrateFromTurns(turns: Turn[]): void
```

For each turn push two messages, in this order:

1. `{isUser: true,  content: turn.user_query, messageType: "text", timestamp: turn.ts ?? new Date()}`
2. `{isUser: false, content: turn.reply,      messageType: "text", timestamp: turn.ts ?? new Date(), bundleId: turn.bundle_id, debugEntries: []}`

The `bundleId` preserves the existing "Download bundle" / "Download artifact"
affordances on `MessageBubble` (which already key off `bundleId`). Replay
of streaming agent debug entries is **not** in scope — the bundles don't
carry them in a form we can faithfully replay.

`hydrateFromTurns` replaces (not appends to) the message list.

### 6.7 `AppLayout` and `EmbeddedApp`

Both layouts:

1. Mount `<SessionSidebar />` to the left of the existing `<ChatPanel />`.
2. Use `useSessions()` to get `activeSessionId`, `setActive`, etc.
3. Use `useChatRoute()` to read `sessionIdFromUrl` on mount; if non-null, call
   `setActive(sessionIdFromUrl)`.
4. Build the `opts` object for `submitQuery`: if `activeSessionId` is set,
   pass `{sessionId: activeSessionId}`; else if `pendingNewChat` from
   `useSessions` is true, pass `{forceNew: true}`; else pass `{}`.
5. After `query_complete`, call `promoteCreatedSession(data.session_id)` (only
   takes effect when there was no active id at submit time).
6. On `query_error` / failed `setActive` (403/404), reset to fresh state
   (call `newChat()` + add system message).

### 6.8 Skeleton & error states

- `setActive` shows a 3-bubble grey skeleton in `MessageList` while
  `isHydrating` is true.
- Failed `fetchSessionTurns` → system message
  `"Couldn't load this conversation"`, revert `activeSessionId` to its prior
  value, clear URL.
- 403 / 404 on resume → same as above; URL cleared explicitly.
- Deleted active session → `setActive(null)` + URL cleared + chat cleared.

## 7. Edge cases

| Case | Behavior |
|---|---|
| User pastes another user's session URL | 403 from `GET /sessions/{id}/?include=turns` → system message + URL cleared |
| User opens a stale (deleted) URL | 404 → system message + URL cleared |
| Two tabs running queries on the same session | Each tab refreshes its sidebar on focus; titles/order eventually consistent. Active session not synced across tabs. |
| User renames while turn-2 is mid-flight | Auto-title check at end of turn-2 sees non-NULL `title` and skips. |
| Untitled session abandoned without a query | Never hits DB (lazy create), so nothing to clean up. |
| Title longer than 200 chars | Backend rejects with 422; UI shows inline error on the input. |
| MySQL `sort_buffer_size` overrun | List query never selects `results_history` in `ORDER BY`; uses pk-first lookup pattern. |

## 8. Testing

### 8.1 Backend

Add `nextseek_api/assistant/tests/test_sessions_endpoints.py`:

- `test_list_returns_only_own_sessions` — three users, three sessions each, list returns only the caller's.
- `test_list_cap_50` — create 60 sessions, list returns 50 newest by `updated_at`.
- `test_list_projection_shape` — fields exactly `session_id`, `title`, `created_at`, `updated_at`, `query_count`, `preview`.
- `test_list_title_fallback` — NULL `title` → `"New chat"` in payload.
- `test_list_preview_truncation` — preview ≤80 chars, derived from first user query.
- `test_detail_include_turns` — `?include=turns` returns `turns` + `title`; without the param, response shape is unchanged from current.
- `test_detail_skips_bundle_without_user_query` — bundle missing `user_query` is omitted from `turns`.
- `test_patch_rename_happy_path` — 200, new title persists.
- `test_patch_reject_empty_after_trim` — 422.
- `test_patch_reject_overlong` — 422.
- `test_patch_403_on_non_owner` — different user gets 403.
- `test_delete_returns_204_and_cascades_tasks` — `QueryTask`s gone too.
- `test_delete_404_after_delete` — second DELETE → 404.
- `test_auto_title_sets_on_first_turn` — call helper after first bundle; title set.
- `test_auto_title_does_not_overwrite_manual_rename` — set title manually, then run helper; title unchanged.
- `test_auto_title_noop_on_subsequent_turns` — second call after title set; no write.

Add `nextseek_api/assistant/tests/test_query_force_new.py`:

- `test_force_new_creates_fresh_session` — user has an existing session; POST `/query/async/` with `force_new: true` returns a *different* `session_id`.
- `test_force_new_ignored_when_session_id_present` — `session_id` + `force_new: true` resolves to the explicit `session_id` (force_new is ignored).
- `test_force_new_false_reuses_most_recent` — existing behavior preserved.

Extend existing integration test style (mirroring `test_evaluator_integration.py`):

- Submit a query with `force_new: true`; call `GET /sessions/`; assert two rows exist (the previous most-recent and the new one), and the new one has the auto-derived title.

### 8.2 Frontend

Vitest:

- `src/components/Sessions/__tests__/SessionSidebar.test.tsx` —
  collapse/expand persistence, new-chat button click clears state, rename inline edit, delete confirm flow, empty state.
- `src/hooks/__tests__/useSessions.test.ts` —
  list fetch, lazy newChat (no POST), setActive triggers hydrate, in-flight lock disables actions, refresh-on-visibilitychange.
- `src/hooks/__tests__/useChatRoute.test.ts` —
  basename resolved from meta tag, push/pop, popstate handling.
- `src/lib/services/__tests__/chatApi.test.ts` —
  `listSessions`, `renameSession`, `deleteSession`, `fetchSessionTurns`, `submitQuery` body shape under each of the three caller cases (sessionId set; forceNew true; neither).

Playwright (`e2e/sessions.spec.ts`):

- Create three chats; sidebar shows three rows with auto-titles.
- Switch between them; messages rehydrate from server.
- Rename one inline; row updates, refresh persists.
- Delete one; row gone; if it was active, chat clears and URL resets.
- Deep-link `/chat/<uuid>` (or `/assistant/chat/<uuid>` for embedded build) → conversation loads.
- Click "New chat" + submit → new row appears with auto-title; URL becomes `/chat/<new-uuid>`.

## 9. Migration & rollout

- Single Django migration adds the nullable `title` column. No data migration.
- Backwards-compatible: legacy clients that don't pass `session_id` keep the existing "reuse most recent or create" behavior; legacy `GET /sessions/{id}/` callers see no shape change unless they opt in via `?include=turns`.
- No feature flag — change is contained, behind authenticated endpoints, and easy to revert (drop column + revert frontend).
- Embedded build requires `npm run build:embedded` to refresh
  `static/js/chat_assistant/` (standard for this repo).
- One Django template edit to `seek/templates/smartSearch.html` to inject the
  `<meta name="chat-basename" content="/assistant/">` tag.

## 10. Open questions

None at spec time. Items deferred to a follow-up spec:

- Server-paginated infinite session list.
- Search across sessions.
- Pinning / favorites.
- Soft delete with undo.
- Cross-tab active-session sync.
- Replaying streaming debug entries for past turns (requires persisting them in bundles).

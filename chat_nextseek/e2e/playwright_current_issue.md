# Phase E Playwright runner: WebSocket endpoint 404 in docker; runner doesn't consume REST polling fallback

## TL;DR

The Playwright runner reaches the chat UI, logs in, sends a query, and then times out at 90s waiting for a `query_complete` WebSocket frame that never arrives. The docker setup runs gunicorn-only (no daphne), so all `/ws/*` endpoints 404. The frontend correctly falls back to REST polling (`GET /nextseek_api/assistant/tasks/<id>/progress/`), but the runner only listens for WS frames and so never sees the completion event.

The smoke variant `advanced.basic_ndma` consistently fails this way after the upstream auth/routing fixes (commits `84c8c3a`, `273039e`, `9027855`) landed on `cd-dev`.

## Reproduction

```bash
# Prerequisites: docker container running cd-dev tip;
# chat_nextseek/.env populated; chromium installed inside container.
docker exec nextseek bash -lc \
  'cd /app/chat_nextseek && uv run e2e.py --playwright --spot advanced.basic_ndma'
```

Expected: `1/1 passed`.
Actual: `0/1 passed`, ~90s elapsed, `outputs/e2e_<ts>/playwright/advanced.basic_ndma/turns/main/error.txt`:

```
TimeoutError: query_complete frame did not arrive within 90.0s
```

`ws_frames.jsonl` is empty (`0 bytes`).

## Diagnostic trail (from the trace.zip)

1. `POST /nextseek_api/assistant/query/async/` → `202`, returns `task_id`.
2. Frontend opens WebSocket: `ws://nextseek_nginx/ws/assistant/progress/<task_id>/` → **404** (nginx has no `/ws/` proxy block).
3. Frontend falls back to polling: `GET /nextseek_api/assistant/tasks/<task_id>/progress/` succeeds (15+ polls in trace). Each response shape: `{"progress": [ProgressEvent, ...]}` where each event matches the WS frame shape, including the terminal `{"event": "query_complete", "data": {...}}`.
4. `WSCapture` only intercepts `page.on("websocket", ...)` frames. Polling responses go through `page.on("response", ...)` but the runner's `_on_resp` only watches for `/assistant/query/async` (to capture `session_id`); it ignores progress polls.

## Root cause

The Phase E spec assumed a working WebSocket. In the actual docker stack:

- `docker/scripts/entrypoint.sh` runs `gunicorn dmac.wsgi` (WSGI only).
- `dmac/asgi.py` IS configured (`ProtocolTypeRouter` with `websocket_urlpatterns`), but no daphne/uvicorn ASGI process is started.
- nginx (`docker/nginx.conf`) has no `location /ws/` block — even if ASGI were running, requests wouldn't reach it.

So WS 404s, frontend uses polling, runner is blind to polling.

## Proposed fix (Phase E code, no infra touch)

Extend `e2e/playwright/ws.py::WSCapture` to also accept REST polling event lists:

```python
class WSCapture:
    def __init__(self) -> None:
        # existing fields...
        self._poll_cursor = 0  # index of last polled event already processed

    def on_progress_events(self, events: list[dict]) -> None:
        """Consume a ProgressEvent list from /tasks/<id>/progress/.

        The endpoint returns a growing list on each poll; we only process
        events past the running cursor so query_complete fires exactly once.
        """
        with self._lock:
            new_events = events[self._poll_cursor:]
            self._poll_cursor = len(events)
        for ev in new_events:
            self._record_event(ev)  # extracted from _on_frame

    # existing _on_frame becomes:
    def _on_frame(self, payload: Any) -> None:
        # ...decode JSON, dispatch to _record_event
```

In `e2e/playwright/runner.py::_on_resp`, detect progress polls and feed them in:

```python
def _on_resp(resp):
    nonlocal captured_session_id
    try:
        if "/assistant/query/async" in resp.url and resp.status == 200:
            # existing session_id capture
            ...
        elif "/assistant/tasks/" in resp.url and "/progress/" in resp.url \
                and resp.status == 200:
            body = resp.json()
            events = body.get("progress") or []
            cap.on_progress_events(events)
    except Exception:
        pass
```

Add a unit test in `tests/test_e2e_playwright_ws.py` covering the polling path (mock `cap.on_progress_events([...query_complete...])` and verify `wait_for_query_complete` resolves).

## Out-of-scope follow-up (not part of this fix)

The "real" fix for WebSocket support in docker is its own track:

1. Add daphne or uvicorn to `docker/scripts/entrypoint.sh` (alongside gunicorn, on a separate port).
2. Add nginx `location /ws/ { proxy_pass http://nextseek:<asgi_port>; ... }` with the standard `Upgrade` / `Connection: upgrade` headers.
3. Verify frontend WS path works end-to-end (replacing the polling fallback as the primary).

This would improve real-user latency (push vs poll) and is what the production deploy presumably already does. The polling-consume fix above lets Phase E E2E ship today without depending on that work.

## Related commits / context

- `9027855` fix(e2e/playwright): route through nginx + rebuild embedded bundle
- `273039e` fix(e2e/playwright): drop Basic Auth header injection
- `84c8c3a` fix(e2e/playwright): drive Django login form before chat navigation
- Local-only docker config additions (in `docker/nextseek.env`, gitignored): `nextseek_nginx` added to `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` — should be ported to `docker/nextseek.env.example`.

## Acceptance criteria

- `uv run e2e.py --playwright --spot advanced.basic_ndma` reports `1/1 passed`.
- `outputs/e2e_<ts>/playwright/advanced.basic_ndma/ws_frames.jsonl` contains the progress events (now sourced from polling).
- `tests/test_e2e_playwright_ws.py` includes a polling-path test that mocks a `query_complete` event and asserts `wait_for_query_complete` resolves with the expected payload.
- Existing 7 runner tests still pass.

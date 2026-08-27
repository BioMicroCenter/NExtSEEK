"""run_variant_browser: drive one variant in a real Chromium via Playwright.

Mirrors e2e/runner.py::run_variant — same return shape, same per-turn try/except.
The Playwright stack is hoisted to module-level imports so tests can patch it.

Transport: HTTP polling, not WebSocket (2026-07-13 rework)
----------------------------------------------------------
The merged NExtSEEK stack serves the assistant over Django/gunicorn (WSGI) with
an in-memory channel layer and no ASGI websocket worker, so the frontend's
``query_complete`` websocket frame never arrives. This runner therefore captures
the ``task_id`` from the ``.../query/async`` POST response and polls
``GET /nextseek_api/cc-assistant/tasks/{task_id}/progress/`` until terminal — see
``e2e.playwright.poll`` for the capture + payload adapters and the NS/CC route
rationale. (The old ``WSCapture`` path lives on in ``ws.py`` + its own test for
reference, but is no longer wired here.)
"""
from __future__ import annotations

import base64
import json
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from e2e.catalog import Variant
from e2e.criteria import check_pass
from e2e.playwright.mysql import fetch_chat_session_row
from e2e.playwright.pages import ChatPage
from e2e.playwright.poll import (
    PollCapture,
    artifact_files,
    build_debug,
    cc_cost_from_data,
    detect_route_from_data,
    query_complete_data,
)


def _ui_url(config: Any) -> str:
    """Base URL for the chat UI.

    Default `http://nextseek_nginx` is the nginx service name within the
    docker compose network — what reachable when the runner executes inside
    the `nextseek` container via `docker exec`. nginx serves /static/ assets
    directly (no Django APPEND_SLASH redirect) and proxies the rest to
    gunicorn. For host-based usage (the Task-17 live run — the Django login
    CSRF trusts only the host-published origin), set
    NEXTSEEK_UI_URL=http://localhost:8100 on `config`.
    """
    return getattr(config, "NEXTSEEK_UI_URL", None) or "http://nextseek_nginx"


def _chat_url(config: Any) -> str:
    """The Django page that embeds the chat React app (smartSearch view)."""
    return f"{_ui_url(config).rstrip('/')}/seek/assistant/"


def _auth_header(config: Any) -> str:
    user = getattr(config, "API_USER", "demo")
    password = getattr(config, "API_PASS", "demopassword")
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def _make_get_progress(config: Any):
    """Build a ``get_progress(task_id) -> payload`` closure that polls the
    CCAssistantViewSet progress endpoint with Basic auth (same user that owns
    the task — the poll endpoint enforces ``user=request.user``). Independent of
    the frontend's dead websocket / poll-fallback logic."""
    base = _ui_url(config).rstrip("/")
    auth = _auth_header(config)

    def get_progress(task_id: str) -> dict:
        url = f"{base}/nextseek_api/cc-assistant/tasks/{task_id}/progress/"
        req = urllib.request.Request(url, headers={"Authorization": auth})
        with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310 (operator-supplied base_url)
            return json.loads(r.read().decode())

    return get_progress


def _login_django_session(page: Any, ui_url: str, user: str, password: str, *, timeout_ms: int = 15_000) -> None:
    """Drive the Django login form to establish a session cookie.

    The chat React app is embedded into the smartSearch Django template
    (`/seek/assistant/`), whose view requires `request.user.is_authenticated`.
    HTTP Basic Auth alone (used for `/nextseek_api/*` REST calls) does not
    create a Django session, so we must POST the login form first.
    """
    page.goto(f"{ui_url.rstrip('/')}/login/", timeout=timeout_ms)
    page.locator('input[name="username"]').fill(user)
    page.locator('input[name="password"]').fill(password)
    page.locator('input[name="password"]').press("Enter")
    page.wait_for_load_state("networkidle", timeout=timeout_ms)


def run_variant_browser(
    variant: Variant,
    config: Any,
    out_dir: Path,
    *,
    pace_seconds: int = 15,
    headed: bool = False,
    video: bool = False,
    timeout_s: float = 180.0,
    interval_s: float = 2.0,
    submit_timeout_ms: int = 60_000,
) -> dict:
    """Execute one variant in a real browser. Returns dict matching run_variant().

    out_dir = outputs/e2e_<ts>/playwright/<vid>/ (per-variant subdir; caller mkdirs).

    Return dict adds two fields over the classic shape so an approval-driven
    orchestrator can gate cost route-aware (NS persists no per-turn cost):
    ``route`` (``ns``|``cc``|``unknown`` — the last non-unknown turn) and
    ``cc_cost_usd`` (sum of CC-turn ``total_cost_usd``, or None if no CC turn).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    files_dir = out_dir / "files"
    files_dir.mkdir(exist_ok=True)

    ui_url = _ui_url(config)
    get_progress = _make_get_progress(config)
    poll = PollCapture()

    turn_results: list[dict] = []
    overall_passed = True
    overall_failed_criteria: list[str] = []
    elapsed_total = 0.0

    captured_session_id: str | None = None
    downloaded_artifacts: set[str] = set()
    variant_route = "unknown"
    cc_turns = 0
    cc_turns_costed = 0
    cc_cost_sum = 0.0

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        # No Basic Auth header injection on the browser context: the smartSearch
        # view uses Django session auth (established via the login form below),
        # and the React app's REST calls go through SessionAuthService cookies.
        # An Authorization header here leaks onto third-party CDN font requests
        # (CORS blocked) and triggers Django's APPEND_SLASH redirect on /static/
        # which breaks the React bundle. (The progress poll uses its own Basic
        # auth via urllib, outside the browser — see _make_get_progress.)
        context_kwargs: dict[str, Any] = {"accept_downloads": True}
        if video:
            context_kwargs["record_video_dir"] = str(out_dir)
        ctx = browser.new_context(**context_kwargs)
        ctx.tracing.start(screenshots=True, snapshots=True, sources=False)
        page = ctx.new_page()

        try:
            # Establish Django session via form login, THEN navigate to the
            # smartSearch page that embeds the chat React app.
            _login_django_session(
                page, ui_url,
                getattr(config, "API_USER", "demo"),
                getattr(config, "API_PASS", "demopassword"),
            )
            page.goto(_chat_url(config))
            chat = ChatPage(page)
            chat.open_new_chat()

            for i, turn in enumerate(variant.turns):
                if pace_seconds > 0 and i > 0:
                    time.sleep(pace_seconds)

                t0 = time.perf_counter()
                turn_dir = out_dir / "turns" / turn.label
                turn_dir.mkdir(parents=True, exist_ok=True)
                (turn_dir / "query.txt").write_text(turn.query, encoding="utf-8")

                try:
                    # Submit the query and capture task_id + session_id from the
                    # 202 response body synchronously (both routes POST to a URL
                    # matching "query/async"), then poll to a terminal payload.
                    with page.expect_response(
                        lambda r: "query/async" in r.url, timeout=submit_timeout_ms,
                    ) as resp_info:
                        chat.send_query(turn.query)
                    resp = resp_info.value
                    try:
                        body = resp.json()
                    except Exception:
                        body = {}
                    task_id = body.get("task_id")
                    if body.get("session_id"):
                        captured_session_id = body["session_id"]
                    if not task_id:
                        raise RuntimeError(
                            f"no task_id in query/async response "
                            f"(status={resp.status}, url={resp.url}, body={str(body)[:200]})"
                        )
                    payload = poll.poll_until_complete(
                        get_progress, task_id, timeout_s=timeout_s, interval_s=interval_s,
                    )
                    elapsed = time.perf_counter() - t0
                    # Persist BOTH the full poll payload (route detection +
                    # evidence) and the query_complete.data (complete.json keeps
                    # the same top-level {reply, debug[, files, artifacts]} shape
                    # the orchestrator's _turn_reply_from_artifacts / recompute
                    # read).
                    qc_data = query_complete_data(payload)
                    (turn_dir / "progress.json").write_text(
                        json.dumps(payload, indent=2, default=str), encoding="utf-8"
                    )
                    (turn_dir / "complete.json").write_text(
                        json.dumps(qc_data, indent=2, default=str), encoding="utf-8"
                    )
                except Exception as exc:
                    elapsed = time.perf_counter() - t0
                    (turn_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
                    turn_results.append({
                        "label": turn.label, "passed": False, "elapsed_s": round(elapsed, 2),
                        "route": "unknown", "error": str(exc), "criteria_results": [],
                    })
                    overall_passed = False
                    overall_failed_criteria.append(f"{turn.label}: {type(exc).__name__} {exc}")
                    elapsed_total += elapsed
                    continue

                elapsed_total += elapsed

                route = detect_route_from_data(qc_data)
                if route != "unknown":
                    variant_route = route
                if route == "cc":
                    cc_turns += 1
                    turn_cc_cost = cc_cost_from_data(qc_data)
                    if turn_cc_cost is not None:
                        cc_turns_costed += 1
                        cc_cost_sum += turn_cc_cost

                # Download artifacts by KEY (data.artifacts) — see poll.artifact_files
                # for why the un-suffixed key (not the flat data.files manifest) is
                # the button/endpoint identifier. Saves bytes to files/ for
                # api_artifact.* checks and records the identifier for
                # ui_text.artifacts.<id>.downloaded checks.
                _download_artifacts(chat, payload, turn.pass_criteria, files_dir, downloaded_artifacts)

                # Fetch MySQL chat_log if any criterion needs it
                mysql_log: list[dict] = []
                reply_text: str | None = qc_data.get("reply")
                if _needs_mysql(turn.pass_criteria) and captured_session_id:
                    mysql_log = _fetch_with_retry(config, captured_session_id)
                    (turn_dir / "mysql_chat_log.json").write_text(
                        json.dumps(mysql_log, indent=2, default=str), encoding="utf-8"
                    )

                browser_ctx = {
                    "chat_page": chat,
                    "downloaded_artifacts": downloaded_artifacts,
                }
                debug = build_debug(payload) if route == "ns" else {}
                passed, crit_results = check_pass(
                    debug,
                    turn.pass_criteria,
                    last_reply=reply_text,
                    # api_artifact.* reads <run_root>/files/; downloads are saved
                    # to the variant-level out_dir/files, so run_root is out_dir.
                    run_root=out_dir,
                    browser_ctx=browser_ctx,
                    console_text=reply_text,
                    mysql_chat_log=mysql_log if mysql_log else None,
                )

                _write_turn_artifacts(turn_dir, chat, crit_results)

                turn_results.append({
                    "label": turn.label, "passed": passed, "elapsed_s": round(elapsed, 2),
                    "route": route, "criteria_results": crit_results,
                })
                if not passed:
                    overall_passed = False
                    for cr in crit_results:
                        if not cr.get("passed"):
                            overall_failed_criteria.append(
                                f"{turn.label}: {cr.get('field')} ({cr.get('op')}={cr.get('value')!r})"
                            )

        finally:
            try:
                page.screenshot(path=str(out_dir / "screenshot.png"), full_page=True)
            except Exception:
                pass
            try:
                ctx.tracing.stop(path=str(out_dir / "trace.zip"))
            except Exception:
                pass
            try:
                ctx.close()
                browser.close()
            except Exception:
                pass

    # Persist the captured NExtSEEK chat session_id + the downloaded artifact
    # keys to raw artifacts (not just the return value) so a --validate-only
    # replay pass can recover them from disk alone, without trusting the live
    # caller's in-memory result.
    if captured_session_id:
        (out_dir / "session_id.txt").write_text(captured_session_id, encoding="utf-8")
    (out_dir / "downloaded_keys.json").write_text(
        json.dumps(sorted(downloaded_artifacts)), encoding="utf-8"
    )

    # CC cost is the EXACT per-turn result-frame total_cost_usd (== the value the
    # server persists to extra_state.cc_traces; see translate.py / cc_engine.py),
    # summed across the CC turns. Fail-closed: None when there were no CC turns,
    # or when any CC turn completed without a numeric total_cost_usd frame —
    # never silently undercount a real spend. NS turns carry no cost.
    if cc_turns == 0:
        cc_cost_usd = None
    elif cc_turns_costed == cc_turns:
        cc_cost_usd = round(cc_cost_sum, 6)
    else:
        cc_cost_usd = None

    return {
        "id": variant.id,
        "family": variant.family,
        "status": "passed" if overall_passed else "failed",
        "elapsed_s": round(elapsed_total, 2),
        "failed_criteria": overall_failed_criteria,
        "turn_results": turn_results,
        "session_id": captured_session_id,
        "route": variant_route,
        "cc_cost_usd": cc_cost_usd,
    }


# ── Helpers ──────────────────────────────────────────────────────────────


def _needs_mysql(criteria: list) -> bool:
    return any(
        (c.field.startswith("mysql_chat_log.") or c.op == "trio_match")
        for c in criteria
    )


def _download_one(chat: ChatPage, candidates: list[str | None], files_dir: Path) -> str | None:
    """Click the first artifact button matching any candidate data-filename and
    save the download bytes under the server's suggested filename in files_dir.
    Returns the server filename on success, else None."""
    page = chat.page
    for candidate in candidates:
        if not candidate or not chat.has_artifact(candidate):
            continue
        try:
            with page.expect_download(timeout=30_000) as dl_info:
                chat.click_artifact(candidate)
            download = dl_info.value
            files_dir.mkdir(parents=True, exist_ok=True)
            save_name = download.suggested_filename or candidate
            dest = files_dir / save_name
            download.save_as(str(dest))
            if dest.exists() and dest.stat().st_size > 0:
                return save_name
        except Exception:
            continue
    return None


def _download_artifacts(chat: ChatPage, payload: dict, criteria: list,
                        files_dir: Path, downloaded: set[str]) -> None:
    """Download this turn's artifacts, recording every identifier a criterion
    might reference so ``ui_text.artifacts.<id>.downloaded`` resolves.

    Pass 1 (payload-driven): every ``query_complete.data.artifacts`` file/table
    entry is downloaded by its KEY (with a ``<key>.csv`` fallback for table
    artifacts, whose button data-filename is the csv name) and the key +
    filename recorded. This is the evidence + api_artifact source.

    Pass 2 (criteria-driven safety net): any ``ui_text.artifacts.<name>.downloaded``
    whose ``<name>`` is not yet recorded is attempted by name directly, so an
    approval that names a filename rather than a key still resolves.
    """
    for f in artifact_files(payload):
        key = f.get("key")
        if not key:
            continue
        filename = f.get("filename")
        if _download_one(chat, [key, f"{key}.csv", filename], files_dir):
            downloaded.add(key)
            if filename:
                downloaded.add(filename)

    for c in criteria:
        if not c.field.startswith("ui_text.artifacts."):
            continue
        sub = c.field[len("ui_text.artifacts."):]
        if not sub.endswith(".downloaded"):
            continue
        name = sub[:-len(".downloaded")]
        if name in downloaded:
            continue
        if _download_one(chat, [name, f"{name}.csv"], files_dir):
            downloaded.add(name)


def _fetch_with_retry(config: Any, session_id: str, *, retry_after_ms: int = 500) -> list[dict]:
    log = fetch_chat_session_row(config, session_id)
    if log:
        return log
    time.sleep(retry_after_ms / 1000.0)
    return fetch_chat_session_row(config, session_id)


def _write_turn_artifacts(turn_dir: Path, chat: ChatPage, crit_results: list[dict]) -> None:
    (turn_dir / "ui_text.json").write_text(
        json.dumps({"latest_assistant_reply": chat.latest_assistant_reply()}, indent=2),
        encoding="utf-8",
    )
    for r in crit_results:
        if r.get("op") == "trio_match" and r.get("diff"):
            (turn_dir / "trio_diff.txt").write_text(r["diff"], encoding="utf-8")

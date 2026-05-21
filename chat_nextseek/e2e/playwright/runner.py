"""run_variant_browser: drive one variant in a real Chromium via Playwright.

Mirrors e2e/runner.py::run_variant — same return shape, same per-turn try/except.
The Playwright stack is hoisted to module-level imports so tests can patch it.
"""
from __future__ import annotations

import base64
import json
import time
import traceback
from pathlib import Path
from typing import Any

from playwright.sync_api import sync_playwright

from e2e.catalog import Variant
from e2e.criteria import check_pass
from e2e.playwright.mysql import fetch_chat_session_row
from e2e.playwright.pages import ChatPage
from e2e.playwright.ws import WSCapture, wait_for_query_complete


def _basic_auth_header(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
    return f"Basic {token}"


def _ui_url(config: Any) -> str:
    return getattr(config, "NEXTSEEK_UI_URL", None) or "http://localhost:8000"


def _chat_url(config: Any) -> str:
    """The Django page that embeds the chat React app (smartSearch view)."""
    return f"{_ui_url(config).rstrip('/')}/seek/assistant/"


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
    timeout_s: float = 90.0,
) -> dict:
    """Execute one variant in a real browser. Returns dict matching run_variant().

    out_dir = outputs/e2e_<ts>/playwright/<vid>/ (per-variant subdir; caller mkdirs).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts_dir = out_dir / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)

    auth_header = _basic_auth_header(
        getattr(config, "API_USER", "demo"),
        getattr(config, "API_PASS", "demopassword"),
    )
    ui_url = _ui_url(config)

    turn_results: list[dict] = []
    overall_passed = True
    overall_failed_criteria: list[str] = []
    elapsed_total = 0.0

    captured_session_id: str | None = None
    downloaded_artifacts: set[str] = set()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed)
        context_kwargs = {"extra_http_headers": {"Authorization": auth_header}}
        if video:
            context_kwargs["record_video_dir"] = str(out_dir)
        ctx = browser.new_context(**context_kwargs)
        ctx.tracing.start(screenshots=True, snapshots=True, sources=False)
        page = ctx.new_page()

        cap = WSCapture()
        cap.attach(page)

        # Capture session_id from the async-query POST response
        def _on_resp(resp):
            nonlocal captured_session_id
            try:
                if "/assistant/query/async" in resp.url and resp.status == 200:
                    body = resp.json()
                    if body.get("session_id"):
                        captured_session_id = body["session_id"]
            except Exception:
                pass
        page.on("response", _on_resp)

        try:
            # Establish Django session via form login, THEN navigate to the
            # smartSearch page that embeds the chat React app. Basic Auth
            # header (set on the context above) continues to serve REST
            # calls the React app makes to `/nextseek_api/*`.
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

                cap.reset_for_next_turn()
                t0 = time.perf_counter()
                turn_dir = out_dir / "turns" / turn.label
                turn_dir.mkdir(parents=True, exist_ok=True)
                (turn_dir / "query.txt").write_text(turn.query, encoding="utf-8")

                try:
                    chat.send_query(turn.query)
                    payload = wait_for_query_complete(cap, timeout_s=timeout_s)
                    elapsed = time.perf_counter() - t0
                    (turn_dir / "complete.json").write_text(
                        json.dumps(payload, indent=2, default=str), encoding="utf-8"
                    )
                except Exception as exc:
                    elapsed = time.perf_counter() - t0
                    (turn_dir / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
                    turn_results.append({
                        "label": turn.label, "passed": False, "elapsed_s": round(elapsed, 2),
                        "error": str(exc), "criteria_results": [],
                    })
                    overall_passed = False
                    overall_failed_criteria.append(f"{turn.label}: {type(exc).__name__} {exc}")
                    elapsed_total += elapsed
                    continue

                elapsed_total += elapsed

                # Eager click of any artifact-download required by criteria
                _maybe_download_artifacts(chat, turn.pass_criteria, artifacts_dir, downloaded_artifacts)

                # Fetch MySQL chat_log if any criterion needs it
                mysql_log: list[dict] = []
                console_text: str | None = payload.get("reply") if isinstance(payload, dict) else None
                if _needs_mysql(turn.pass_criteria) and captured_session_id:
                    mysql_log = _fetch_with_retry(config, captured_session_id)
                    (turn_dir / "mysql_chat_log.json").write_text(
                        json.dumps(mysql_log, indent=2, default=str), encoding="utf-8"
                    )

                browser_ctx = {
                    "chat_page": chat,
                    "downloaded_artifacts": downloaded_artifacts,
                }
                passed, crit_results = check_pass(
                    payload.get("debug", {}) if isinstance(payload, dict) else {},
                    turn.pass_criteria,
                    browser_ctx=browser_ctx,
                    console_text=console_text,
                    mysql_chat_log=mysql_log if mysql_log else None,
                )

                _write_turn_artifacts(turn_dir, chat, crit_results)

                turn_results.append({
                    "label": turn.label, "passed": passed, "elapsed_s": round(elapsed, 2),
                    "criteria_results": crit_results,
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
                cap.dump(out_dir / "ws_frames.jsonl")
            except Exception:
                pass
            try:
                ctx.close()
                browser.close()
            except Exception:
                pass

    return {
        "id": variant.id,
        "family": variant.family,
        "status": "passed" if overall_passed else "failed",
        "elapsed_s": round(elapsed_total, 2),
        "failed_criteria": overall_failed_criteria,
        "turn_results": turn_results,
    }


# ── Helpers ──────────────────────────────────────────────────────────────


def _needs_mysql(criteria: list) -> bool:
    return any(
        (c.field.startswith("mysql_chat_log.") or c.op == "trio_match")
        for c in criteria
    )


def _maybe_download_artifacts(chat: ChatPage, criteria: list, out_dir: Path, downloaded: set[str]) -> None:
    """Click any artifact-download buttons named by a *.downloaded criterion."""
    page = chat.page
    for c in criteria:
        if not c.field.startswith("ui_text.artifacts."):
            continue
        sub = c.field[len("ui_text.artifacts."):]
        if not sub.endswith(".downloaded"):
            continue
        filename = sub[:-len(".downloaded")]
        try:
            with page.expect_download(timeout=30_000) as dl_info:
                chat.click_artifact(filename)
            download = dl_info.value
            dest = out_dir / filename
            download.save_as(str(dest))
            if dest.exists():
                downloaded.add(filename)
        except Exception:
            pass


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

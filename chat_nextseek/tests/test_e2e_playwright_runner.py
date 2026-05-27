"""run_variant_browser contract tests — Playwright is mocked."""
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from e2e.catalog import PassCriterion, Turn, Variant


def _variant_basic_ndma():
    return Variant(
        family="search_advanced", id="advanced.basic_ndma", name="basic",
        tags=["playwright"],
        turns=[Turn(
            label="main",
            query="Find me mice treated with NDMA",
            pass_criteria=[
                PassCriterion(field="ui_text.assistant_reply", op="mentions", value="NDMA"),
            ],
        )],
    )


def _config():
    cfg = MagicMock()
    cfg.NEXTSEEK_UI_URL = "http://localhost:8000"
    cfg.API_USER = "demo"
    cfg.API_PASS = "demopassword"
    cfg._connect_db.return_value = None  # MySQL skipped for non-trio tests
    return cfg


def _stub_playwright_session(complete_payload, ui_reply):
    """Build a stub Playwright stack: browser → context → page with WS sniff support."""
    page = MagicMock()
    # ChatPage.latest_assistant_reply will be called via page.locator(...).nth(n-1).text_content()
    bubble = MagicMock()
    bubble.text_content.return_value = ui_reply
    bubbles = MagicMock()
    bubbles.count.return_value = 1
    bubbles.nth.return_value = bubble
    page.locator.return_value = bubbles

    ctx = MagicMock()
    ctx.new_page.return_value = page
    browser = MagicMock()
    browser.new_context.return_value = ctx

    pw = MagicMock()
    pw.chromium.launch.return_value = browser
    sp = MagicMock()
    sp.__enter__.return_value = pw
    sp.__exit__.return_value = False
    return sp, page, complete_payload


def test_run_variant_browser_happy_path(tmp_path):
    from e2e.playwright import runner as runner_mod

    variant = _variant_basic_ndma()
    config = _config()
    sp, page, payload = _stub_playwright_session(
        complete_payload={"reply": "NDMA mice found", "session_id": "abc123"},
        ui_reply="NDMA mice found",
    )

    # Stub WSCapture so wait_for_query_complete returns immediately
    cap = MagicMock()
    cap.frames = [{"event": "query_complete", "data": payload}]
    cap.dump = MagicMock()

    with patch("e2e.playwright.runner.sync_playwright", return_value=sp), \
         patch("e2e.playwright.runner.WSCapture", return_value=cap), \
         patch("e2e.playwright.runner.wait_for_query_complete", return_value=payload):
        result = runner_mod.run_variant_browser(variant, config, tmp_path, pace_seconds=0)

    assert result["status"] == "passed"
    assert result["id"] == "advanced.basic_ndma"
    assert (tmp_path / "ws_frames.jsonl").exists() or cap.dump.called


def test_run_variant_browser_fails_when_ui_text_missing(tmp_path):
    from e2e.playwright import runner as runner_mod

    variant = _variant_basic_ndma()  # asserts ui_text mentions "NDMA"
    config = _config()
    sp, page, payload = _stub_playwright_session(
        complete_payload={"reply": "unrelated answer", "session_id": "abc"},
        ui_reply="unrelated answer",  # does NOT contain NDMA
    )
    cap = MagicMock()
    cap.frames = [{"event": "query_complete", "data": payload}]

    with patch("e2e.playwright.runner.sync_playwright", return_value=sp), \
         patch("e2e.playwright.runner.WSCapture", return_value=cap), \
         patch("e2e.playwright.runner.wait_for_query_complete", return_value=payload):
        result = runner_mod.run_variant_browser(variant, config, tmp_path, pace_seconds=0)

    assert result["status"] == "failed"
    assert any("ui_text.assistant_reply" in c for c in result["failed_criteria"])


def test_run_variant_browser_timeout_records_exception(tmp_path):
    from e2e.playwright import runner as runner_mod

    variant = _variant_basic_ndma()
    config = _config()
    sp, page, _ = _stub_playwright_session(
        complete_payload={},
        ui_reply="",
    )
    cap = MagicMock()

    with patch("e2e.playwright.runner.sync_playwright", return_value=sp), \
         patch("e2e.playwright.runner.WSCapture", return_value=cap), \
         patch("e2e.playwright.runner.wait_for_query_complete",
               side_effect=TimeoutError("query_complete not received")):
        result = runner_mod.run_variant_browser(variant, config, tmp_path, pace_seconds=0)

    assert result["status"] == "failed"
    assert any("TimeoutError" in c or "timeout" in c.lower() for c in result["failed_criteria"])

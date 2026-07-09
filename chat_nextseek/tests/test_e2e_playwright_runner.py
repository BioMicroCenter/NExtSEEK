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


def test_run_variant_browser_returns_and_persists_session_id(tmp_path):
    """Regression test for Task 12 F4a: run_variant_browser captures
    captured_session_id from the /assistant/query/async POST response
    (page.on("response", ...) handler, :102-112) but the return dict and
    on-disk artifacts previously never surfaced it. A standalone orchestrator
    (nextseek_api/cc_assistant/scripts/full_ui_e2e.py) needs the session_id
    both live (out["session_id"]) and from raw artifacts alone for a
    --validate-only replay (out_dir/session_id.txt), since the DB-row cost
    readout (F2) is keyed on this exact id.
    """
    from e2e.playwright import runner as runner_mod

    variant = _variant_basic_ndma()
    config = _config()
    sp, page, payload = _stub_playwright_session(
        complete_payload={"reply": "NDMA mice found"},
        ui_reply="NDMA mice found",
    )

    # Real Playwright would invoke page.on("response", handler) for every
    # response; a bare MagicMock records the call but never invokes the
    # handler, so capture it here and fire it (with a fake
    # /assistant/query/async 200 response carrying session_id) exactly like
    # wait_for_query_complete's caller would observe it mid-turn.
    handlers: dict[str, object] = {}
    page.on.side_effect = lambda event, handler: handlers.__setitem__(event, handler)

    def _fire_async_query_response():
        resp = MagicMock()
        resp.url = "http://localhost:8000/nextseek_api/assistant/query/async"
        resp.status = 200
        resp.json.return_value = {"session_id": "sess-abc123"}
        handlers["response"](resp)

    def _wait_for_query_complete(_cap, timeout_s=90.0):
        _fire_async_query_response()
        return payload

    cap = MagicMock()
    cap.frames = [{"event": "query_complete", "data": payload}]

    with patch("e2e.playwright.runner.sync_playwright", return_value=sp), \
         patch("e2e.playwright.runner.WSCapture", return_value=cap), \
         patch("e2e.playwright.runner.wait_for_query_complete", side_effect=_wait_for_query_complete):
        result = runner_mod.run_variant_browser(variant, config, tmp_path, pace_seconds=0)

    assert result["status"] == "passed"
    assert result["session_id"] == "sess-abc123"
    session_id_path = tmp_path / "session_id.txt"
    assert session_id_path.exists()
    assert session_id_path.read_text(encoding="utf-8").strip() == "sess-abc123"


def test_run_variant_browser_session_id_none_when_never_captured(tmp_path):
    """No /assistant/query/async response observed (e.g. the request never
    completed) -> session_id is None in the return dict and session_id.txt is
    never written, rather than the key silently disappearing or a stale/empty
    file being created."""
    from e2e.playwright import runner as runner_mod

    variant = _variant_basic_ndma()
    config = _config()
    sp, page, payload = _stub_playwright_session(
        complete_payload={"reply": "NDMA mice found"},
        ui_reply="NDMA mice found",
    )
    cap = MagicMock()
    cap.frames = [{"event": "query_complete", "data": payload}]

    with patch("e2e.playwright.runner.sync_playwright", return_value=sp), \
         patch("e2e.playwright.runner.WSCapture", return_value=cap), \
         patch("e2e.playwright.runner.wait_for_query_complete", return_value=payload):
        result = runner_mod.run_variant_browser(variant, config, tmp_path, pace_seconds=0)

    assert result["status"] == "passed"
    assert result["session_id"] is None
    assert not (tmp_path / "session_id.txt").exists()

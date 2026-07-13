"""run_variant_browser contract tests — Playwright + the HTTP poll are mocked.

Transport is HTTP polling (2026-07-13 rework): the runner captures task_id +
session_id from the .../query/async POST response via page.expect_response, then
PollCapture.poll_until_complete polls the progress endpoint. Both are patched
here so no browser and no network are needed.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

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
    cfg.NEXTSEEK_UI_URL = "http://localhost:8100"
    cfg.API_USER = "demo"
    cfg.API_PASS = "demopassword"
    cfg._connect_db.return_value = None  # MySQL skipped for non-trio tests
    return cfg


def _ns_payload(reply, *, session_id="abc123", debug=None, artifacts=None, files=None):
    data = {"reply": reply, "debug": debug or {}}
    if artifacts is not None:
        data["artifacts"] = artifacts
    if files is not None:
        data["files"] = files
    return {"status": "completed", "session_id": session_id,
            "progress": [{"event": "query_complete", "data": data}]}


def _cc_payload(reply, *, session_id="abc123", cost=0.0):
    data = {"reply": reply, "total_cost_usd": cost, "cc_session_id": "cc-sess"}
    return {"status": "completed", "session_id": session_id,
            "progress": [{"event": "query_complete", "data": data}]}


def _stub_page(ui_reply, response_body):
    """A MagicMock page that answers latest_assistant_reply + expect_response."""
    page = MagicMock()
    bubble = MagicMock()
    bubble.text_content.return_value = ui_reply
    bubbles = MagicMock()
    bubbles.count.return_value = 1
    bubbles.nth.return_value = bubble
    page.locator.return_value = bubbles

    resp = MagicMock()
    resp.json.return_value = response_body
    resp.status = 202
    resp.url = "http://localhost:8100/nextseek_api/cc-assistant/query/async"
    exp_cm = MagicMock()
    exp_cm.__enter__.return_value = SimpleNamespace(value=resp)
    exp_cm.__exit__.return_value = False
    page.expect_response.return_value = exp_cm
    return page


def _stub_playwright(page):
    ctx = MagicMock()
    ctx.new_page.return_value = page
    browser = MagicMock()
    browser.new_context.return_value = ctx
    pw = MagicMock()
    pw.chromium.launch.return_value = browser
    sp = MagicMock()
    sp.__enter__.return_value = pw
    sp.__exit__.return_value = False
    return sp


def _run(page, payload_or_exc, variant, config, tmp_path):
    sp = _stub_playwright(page)
    poll_inst = MagicMock()
    if isinstance(payload_or_exc, BaseException):
        poll_inst.poll_until_complete.side_effect = payload_or_exc
    else:
        poll_inst.poll_until_complete.return_value = payload_or_exc
    from e2e.playwright import runner as runner_mod
    with patch("e2e.playwright.runner.sync_playwright", return_value=sp), \
         patch("e2e.playwright.runner.PollCapture", return_value=poll_inst):
        return runner_mod.run_variant_browser(variant, config, tmp_path, pace_seconds=0)


def test_run_variant_browser_happy_path(tmp_path):
    variant = _variant_basic_ndma()
    payload = _ns_payload("NDMA mice found", session_id="abc123")
    page = _stub_page("NDMA mice found", {"task_id": "t1", "session_id": "abc123"})

    result = _run(page, payload, variant, _config(), tmp_path)

    assert result["status"] == "passed"
    assert result["id"] == "advanced.basic_ndma"
    assert result["route"] == "ns"
    assert result["cc_cost_usd"] is None  # NS persists no per-turn cost
    assert (tmp_path / "turns" / "main" / "progress.json").exists()
    assert (tmp_path / "turns" / "main" / "complete.json").exists()


def test_run_variant_browser_fails_when_ui_text_missing(tmp_path):
    variant = _variant_basic_ndma()  # asserts ui_text mentions "NDMA"
    payload = _ns_payload("unrelated answer")
    page = _stub_page("unrelated answer", {"task_id": "t1", "session_id": "abc"})

    result = _run(page, payload, variant, _config(), tmp_path)

    assert result["status"] == "failed"
    assert any("ui_text.assistant_reply" in c for c in result["failed_criteria"])


def test_run_variant_browser_timeout_records_exception(tmp_path):
    variant = _variant_basic_ndma()
    page = _stub_page("", {"task_id": "t1", "session_id": "abc"})

    result = _run(page, TimeoutError("task still running after 180s"),
                  variant, _config(), tmp_path)

    assert result["status"] == "failed"
    assert any("TimeoutError" in c or "timeout" in c.lower() for c in result["failed_criteria"])


def test_run_variant_browser_missing_task_id_errors(tmp_path):
    """A 202 with no task_id (the runner cannot poll) is a hard turn error, not
    a silent pass."""
    variant = _variant_basic_ndma()
    payload = _ns_payload("NDMA mice found")
    page = _stub_page("NDMA mice found", {"session_id": "abc"})  # no task_id

    result = _run(page, payload, variant, _config(), tmp_path)

    assert result["status"] == "failed"
    assert any("task_id" in c for c in result["failed_criteria"])


def test_run_variant_browser_returns_and_persists_session_id(tmp_path):
    """session_id is captured from the .../query/async 202 response body (F4a)
    and surfaced both in the return dict and on disk (session_id.txt) so a
    --validate-only replay recovers it from artifacts alone."""
    variant = _variant_basic_ndma()
    payload = _ns_payload("NDMA mice found", session_id="sess-abc123")
    page = _stub_page("NDMA mice found", {"task_id": "t1", "session_id": "sess-abc123"})

    result = _run(page, payload, variant, _config(), tmp_path)

    assert result["status"] == "passed"
    assert result["session_id"] == "sess-abc123"
    session_id_path = tmp_path / "session_id.txt"
    assert session_id_path.exists()
    assert session_id_path.read_text(encoding="utf-8").strip() == "sess-abc123"


def test_run_variant_browser_session_id_none_when_never_captured(tmp_path):
    """A 202 carrying a task_id but no session_id -> session_id is None in the
    return dict and session_id.txt is never written."""
    variant = _variant_basic_ndma()
    payload = {"status": "completed",
               "progress": [{"event": "query_complete", "data": {"reply": "NDMA mice found", "debug": {}}}]}
    page = _stub_page("NDMA mice found", {"task_id": "t1"})  # task_id present, no session_id

    result = _run(page, payload, variant, _config(), tmp_path)

    assert result["status"] == "passed"
    assert result["session_id"] is None
    assert not (tmp_path / "session_id.txt").exists()


def test_run_variant_browser_reports_cc_route_and_cost(tmp_path):
    """A CC-shaped payload (total_cost_usd/cc_session_id, no debug) is detected
    as the CC route and its per-turn cost surfaced for the orchestrator's
    route-aware cost gate."""
    variant = _variant_basic_ndma()
    payload = _cc_payload("NDMA mice found via CC", cost=0.42)
    page = _stub_page("NDMA mice found via CC", {"task_id": "t1", "session_id": "abc123"})

    result = _run(page, payload, variant, _config(), tmp_path)

    assert result["status"] == "passed"
    assert result["route"] == "cc"
    assert result["cc_cost_usd"] == 0.42

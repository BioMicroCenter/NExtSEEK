"""Browser-tier criteria: ui_text.*, mysql_chat_log.*, trio_match op."""
from unittest.mock import MagicMock

import pytest

from e2e.catalog import PassCriterion
from e2e.criteria import check_pass, resolve_field


def _browser_ctx(latest_reply="hello", bubble_count=2, stepper_status=None, artifact_present=False):
    chat_page = MagicMock()
    chat_page.latest_assistant_reply.return_value = latest_reply
    chat_page.bubble_count.return_value = bubble_count
    chat_page.has_artifact.return_value = artifact_present
    chat_page.stepper_status.return_value = stepper_status
    return {"chat_page": chat_page}


def test_resolve_ui_text_assistant_reply():
    ctx = _browser_ctx(latest_reply="the answer")
    val = resolve_field({}, "ui_text.assistant_reply", browser_ctx=ctx)
    assert val == "the answer"


def test_resolve_ui_text_bubble_count():
    ctx = _browser_ctx(bubble_count=5)
    val = resolve_field({}, "ui_text.bubble_count", browser_ctx=ctx)
    assert val == 5


def test_resolve_ui_text_artifacts_present():
    ctx = _browser_ctx(artifact_present=True)
    val = resolve_field({}, "ui_text.artifacts.geo.xlsx", browser_ctx=ctx)
    assert val is True


def test_resolve_ui_text_artifacts_downloaded():
    ctx = _browser_ctx()
    ctx["downloaded_artifacts"] = {"geo.xlsx"}
    val = resolve_field({}, "ui_text.artifacts.geo.xlsx.downloaded", browser_ctx=ctx)
    assert val is True


def test_resolve_ui_text_stepper_status():
    ctx = _browser_ctx(stepper_status="complete")
    val = resolve_field({}, "ui_text.stepper.entity", browser_ctx=ctx)
    assert val == "complete"


def test_resolve_mysql_chat_log_length():
    log = [{"user_query": "q1"}, {"user_query": "q2"}]
    val = resolve_field({}, "mysql_chat_log.length", mysql_chat_log=log)
    assert val == 2


def test_resolve_mysql_chat_log_latest_reply():
    log = [{"assistant_reply": "first"}, {"assistant_reply": "second"}]
    val = resolve_field({}, "mysql_chat_log.latest_reply", mysql_chat_log=log)
    assert val == "second"


def test_resolve_mysql_chat_log_latest_mode():
    log = [{"mode": "new_search"}]
    val = resolve_field({}, "mysql_chat_log.latest_mode", mysql_chat_log=log)
    assert val == "new_search"


def test_check_pass_trio_match_all_equal():
    crit = [PassCriterion(field="trio", op="trio_match")]
    ctx = _browser_ctx(latest_reply="hello world")
    passed, results = check_pass(
        {}, crit,
        browser_ctx=ctx,
        console_text="hello world",
        mysql_chat_log=[{"assistant_reply": "hello world"}],
    )
    assert passed is True


def test_check_pass_trio_match_console_differs():
    crit = [PassCriterion(field="trio", op="trio_match")]
    ctx = _browser_ctx(latest_reply="hello world")
    passed, results = check_pass(
        {}, crit,
        browser_ctx=ctx,
        console_text="hello UNIVERSE",
        mysql_chat_log=[{"assistant_reply": "hello world"}],
    )
    assert passed is False
    assert any("trio" in r["reason"].lower() or "console" in r["reason"].lower()
               for r in results)


def test_check_pass_existing_criteria_unaffected():
    """Sanity: a normal in-process criterion still works without browser kwargs."""
    crit = [PassCriterion(field="parser_plan.mode", op="eq", value="new_search")]
    passed, results = check_pass({"parser_plan": {"mode": "new_search"}}, crit)
    assert passed is True

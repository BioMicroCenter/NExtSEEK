"""The service-account credential fallback must be loud and gated.

Security regression tests for the silent identity substitution closed by the
2026-08-03 hardening plan (task 4): when a request-scoped caller cannot resolve
the asking user's credentials, the turn used to proceed as whatever account
``ChatConfig`` was built with (``demo``/``demopassword`` in the shipped
template) with no warning and no failure.

Everything here is stubbed. These tests must never make an LLM or a network
call. The refusal tests deliberately stub the pipeline_agent gate so that
*without* the fix the turn runs to completion and returns ``pa-reply`` — that
way a pre-fix run fails on "the turn proceeded", not on some unrelated
explosion inside a half-populated stub config.
"""
import contextlib
import logging

import pytest
from unittest.mock import MagicMock, patch

from chat_nextseek import orchestrator


IDENTITY_MARK = "[SECURITY][IDENTITY]"
SERVICE_ACCOUNT = "demo"
SERVICE_PASSWORD = "demopassword"
PIPELINE_REPLY = "pa-reply"


class _Config:
    """Minimal ChatConfig stand-in exposing only what the identity gate reads.

    ``allow`` is omitted entirely (not set to None) when the deployment has not
    configured the setting, so the tests exercise the real ``getattr`` default.
    """

    def __init__(self, allow=..., api_user=SERVICE_ACCOUNT, api_pass=SERVICE_PASSWORD):
        self.API_USER = api_user
        self.API_PASS = api_pass
        if allow is not ...:
            self.NEXTSEEK_ALLOW_SERVICE_ACCOUNT_FALLBACK = allow


def _identity_warnings(caplog):
    return [r.getMessage() for r in caplog.records if IDENTITY_MARK in r.getMessage()]


@contextlib.contextmanager
def _stubbed_pipeline(handle_turn=None, start=None):
    """Stub run_query's pipeline_agent short-circuit and every I/O seam it uses.

    With these in place a turn that gets past the identity gate completes and
    returns PIPELINE_REPLY without touching an LLM, the network, or the DB.
    """
    snapshot = {"active": True, "pipeline_key": "rnaseq", "cohort_count": 0, "message_count": 1}
    with patch("chat_nextseek.orchestrator.pipeline_agent.is_active", return_value=True), \
            patch("chat_nextseek.orchestrator.pipeline_agent.handle_turn",
                  return_value={"action": "ask", "reply": PIPELINE_REPLY, "params": None}) as ht, \
            patch("chat_nextseek.orchestrator.pipeline_agent.start",
                  return_value={"action": "ask", "reply": PIPELINE_REPLY}) as st, \
            patch("chat_nextseek.orchestrator.pipeline_agent.snapshot_for_chat_log",
                  return_value=snapshot), \
            patch("chat_nextseek.orchestrator._ensure_query_log_dir", return_value="/tmp/log") as log_dir, \
            patch("chat_nextseek.orchestrator.ArtifactStore"), \
            patch("chat_nextseek.orchestrator.shortlist_catalog") as shortlist:
        yield {"handle_turn": ht, "start": st, "log_dir": log_dir, "shortlist": shortlist}


def _run_query(config, credentials, session=None, send_event=None):
    with _stubbed_pipeline() as mocks:
        result = orchestrator.run_query(
            session=session if session is not None else {},
            config=config,
            user_text="how many mice are in MetNet",
            send_event=send_event,
            credentials=credentials,
        )
    mocks["shortlist"].assert_not_called()  # the catalog/LLM path is never reached
    return result, mocks


def _assert_refused(result, mocks):
    """The turn must not have run at all, and must have returned a rendered refusal."""
    mocks["handle_turn"].assert_not_called()
    mocks["start"].assert_not_called()
    mocks["log_dir"].assert_not_called()
    assert result["reply"] != PIPELINE_REPLY, "the turn ran as the service account"
    assert result["debug"].get("identity_refused") is True
    assert isinstance(result["reply"], str) and result["reply"].strip()


# ---------------------------------------------------------------------------
# 1. Missing credentials + fallback ALLOWED -> warn, naming the account, proceed
# ---------------------------------------------------------------------------


def test_missing_credentials_warn_and_name_the_account_in_use(caplog):
    caplog.set_level(logging.WARNING)
    config = _Config(allow=True)

    result, mocks = _run_query(config, credentials={"api_user": None, "api_pass": None})

    assert result["reply"] == PIPELINE_REPLY, "the turn should still run when the fallback is allowed"
    mocks["handle_turn"].assert_called_once()
    warnings = _identity_warnings(caplog)
    assert warnings, "an absent per-request identity must emit a warning"
    assert SERVICE_ACCOUNT in warnings[0], "the warning must name the account actually in use"


def test_missing_credentials_warn_on_the_local_cli_surface_too(caplog):
    """credentials=None (cli.py / app.py / mcp_server.py / e2e) still warns."""
    caplog.set_level(logging.WARNING)
    config = _Config(allow=True)

    result, _ = _run_query(config, credentials=None)

    assert result["reply"] == PIPELINE_REPLY
    warnings = _identity_warnings(caplog)
    assert warnings and SERVICE_ACCOUNT in warnings[0]


def test_string_true_enables_the_fallback(caplog):
    """A config_map sourced from env carries strings, not bools."""
    caplog.set_level(logging.WARNING)
    result, _ = _run_query(_Config(allow="true"), credentials={})
    assert result["reply"] == PIPELINE_REPLY
    assert _identity_warnings(caplog)


# ---------------------------------------------------------------------------
# 2. Missing credentials + fallback DISALLOWED (the default) -> refuse
# ---------------------------------------------------------------------------


def test_missing_credentials_refused_by_default(caplog):
    """No NEXTSEEK_ALLOW_SERVICE_ACCOUNT_FALLBACK attribute at all == fail closed."""
    caplog.set_level(logging.WARNING)

    result, mocks = _run_query(_Config(), credentials={"api_user": None, "api_pass": None})

    _assert_refused(result, mocks)
    assert result["bundle_id"] is None
    assert _identity_warnings(caplog), "a refusal must still say which account was declined"


def test_refusal_is_a_renderable_reply_not_an_exception():
    """The caller must get a payload it can render, not a task crash / 500."""
    events = []

    result, mocks = _run_query(
        _Config(), credentials={}, send_event=lambda name, data: events.append((name, data)),
    )

    _assert_refused(result, mocks)
    assert events and events[-1][0] == "query_complete"
    assert events[-1][1]["reply"] == result["reply"]


def test_string_false_does_not_enable_the_fallback():
    """A config_map sourced from env carries strings; 'false' must stay closed."""
    result, mocks = _run_query(_Config(allow="false"), credentials={"api_user": "x"})
    _assert_refused(result, mocks)


def test_refusal_applies_to_run_query_plan():
    """run_query_plan carries an identical seam and an identical hole."""
    with _stubbed_pipeline() as mocks:
        result = orchestrator.run_query_plan(
            session={}, config=_Config(), user_text="q",
            credentials={"api_user": "", "api_pass": ""},
        )
    # The gate is the first statement in the function, so a log dir is proof
    # the turn proceeded.
    mocks["log_dir"].assert_not_called()
    mocks["shortlist"].assert_not_called()
    assert result["debug"].get("identity_refused") is True


def test_refusal_applies_to_run_pipeline_launch():
    """run_pipeline_launch carries an identical seam and an identical hole."""
    with _stubbed_pipeline() as mocks:
        result = orchestrator.run_pipeline_launch(
            session={}, config=_Config(), user_text="launch rnaseq",
            credentials={"api_user": None, "api_pass": None},
        )
    mocks["start"].assert_not_called()
    mocks["log_dir"].assert_not_called()
    assert result["reply"] != PIPELINE_REPLY
    assert result["debug"].get("identity_refused") is True


# ---------------------------------------------------------------------------
# 3. Full credentials -> no warning, config overridden, behaviour unchanged
# ---------------------------------------------------------------------------


def test_full_credentials_override_config_without_warning(caplog):
    caplog.set_level(logging.WARNING)
    config = _Config()  # fallback disallowed: complete credentials must still work

    result, mocks = _run_query(
        config, credentials={"api_user": "alice", "api_pass": "alice-secret"},
    )

    assert result["reply"] == PIPELINE_REPLY
    effective = mocks["handle_turn"].call_args[0][1]
    assert effective.API_USER == "alice"
    assert effective.API_PASS == "alice-secret"
    # The shared singleton must not be mutated.
    assert config.API_USER == SERVICE_ACCOUNT
    assert config.API_PASS == SERVICE_PASSWORD
    assert effective is not config
    assert not _identity_warnings(caplog)


# ---------------------------------------------------------------------------
# 4. Partial credentials count as missing (no mixed identity)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "credentials",
    [
        {"api_user": "alice", "api_pass": ""},
        {"api_user": "alice"},
        {"api_user": "", "api_pass": "alice-secret"},
        {"api_pass": "alice-secret"},
    ],
    ids=["user-empty-pass", "user-only", "empty-user-pass", "pass-only"],
)
def test_partial_credentials_are_refused_by_default(credentials):
    result, mocks = _run_query(_Config(), credentials=credentials)
    _assert_refused(result, mocks)


@pytest.mark.parametrize(
    "credentials",
    [{"api_user": "alice", "api_pass": ""}, {"api_user": "", "api_pass": "alice-secret"}],
    ids=["user-only", "pass-only"],
)
def test_partial_credentials_never_produce_a_mixed_identity(credentials, caplog):
    """Even when the fallback is allowed, neither half of a partial pair is applied."""
    caplog.set_level(logging.WARNING)

    result, mocks = _run_query(_Config(allow=True), credentials=credentials)

    assert result["reply"] == PIPELINE_REPLY
    effective = mocks["handle_turn"].call_args[0][1]
    assert effective.API_USER == SERVICE_ACCOUNT, "half a credential pair must not be applied"
    assert effective.API_PASS == SERVICE_PASSWORD
    assert _identity_warnings(caplog)


# ---------------------------------------------------------------------------
# 5. The password must never appear in any emitted diagnostic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "allow, credentials",
    [
        (True, {"api_user": None, "api_pass": None}),
        (True, {"api_user": "alice", "api_pass": ""}),
        (True, None),
        (..., {"api_user": None, "api_pass": None}),
        (..., {"api_user": "alice", "api_pass": ""}),
    ],
    ids=["allowed-none", "allowed-partial", "allowed-cli", "refused-none", "refused-partial"],
)
def test_password_never_appears_in_the_warning(allow, credentials, caplog, capsys):
    caplog.set_level(logging.WARNING)

    result, _ = _run_query(_Config(allow=allow), credentials=credentials)

    stdout = capsys.readouterr().out
    assert _identity_warnings(caplog), "every incomplete-identity turn must warn"
    for surface in (caplog.text, stdout, result["reply"], repr(result["debug"])):
        assert SERVICE_PASSWORD not in surface
        assert "alice-secret" not in surface
    # Not masked-with-length either.
    assert f"len={len(SERVICE_PASSWORD)}" not in caplog.text

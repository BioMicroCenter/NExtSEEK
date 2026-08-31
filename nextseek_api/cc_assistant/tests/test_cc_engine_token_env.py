"""The agent container's NExtSEEK credential when the caller has no password.

A user who signed in through SEEK has no password (#16, sub-project 3), and the
three container-side clients were password-only: they refused to start rather
than run unauthenticated. This is the injection half of the fix -- the container
now receives a per-user DRF token instead.

Two properties carry the weight.

**Never both.** NExtSEEK rejects competing credentials, so a container holding a
password *and* a token would have its calls refused rather than falling back to
whichever worked. The injection is exclusive, and
``test_a_password_suppresses_the_token`` pins that.

**Redacted.** ``_REDACTED_ENV_KEYS`` is what stops a credential reaching a log
line or a republished transcript. A bearer token that works non-interactively is
at least as dangerous there as the password it replaces, so forgetting to list
it would be a straight regression of an existing security property.

Pure-logic only: no Docker, no Django.
"""

import pytest

from nextseek_api.cc_assistant import cc_engine


def _env(*, api_user="researcher", api_pass=None, api_token=None):
    return cc_engine.build_agent_environment(
        source={},
        api_user=api_user,
        api_pass=api_pass,
        api_token=api_token,
        path_mappings={},
    )


# -- injection ---------------------------------------------------------------


def test_a_token_reaches_the_container_under_both_names():
    """The entrypoint maps NEXTSEEK_* onto the API_* names ChatConfig reads, so
    both are set for the same reason the password sets both."""
    env = _env(api_token="tok-abc")
    assert env["NEXTSEEK_TOKEN"] == "tok-abc"
    assert env["API_TOKEN"] == "tok-abc"


def test_a_password_caller_gets_no_token_keys():
    env = _env(api_pass="hunter2")
    assert env["NEXTSEEK_PASSWORD"] == "hunter2"
    assert "NEXTSEEK_TOKEN" not in env
    assert "API_TOKEN" not in env


def test_a_password_suppresses_the_token():
    """Never both. A container carrying two credentials has its calls refused
    outright, which is a worse failure than carrying one."""
    env = _env(api_pass="hunter2", api_token="tok-abc")
    assert env["NEXTSEEK_PASSWORD"] == "hunter2"
    assert "NEXTSEEK_TOKEN" not in env
    assert "API_TOKEN" not in env


def test_a_caller_with_neither_gets_no_credential_keys():
    env = _env()
    for key in ("NEXTSEEK_PASSWORD", "API_PASS", "NEXTSEEK_TOKEN", "API_TOKEN"):
        assert key not in env


def test_the_username_is_still_injected_alongside_a_token():
    """Output paths and staging are named after it, so it is needed even when
    the token is what authenticates."""
    env = _env(api_token="tok-abc")
    assert env["NEXTSEEK_USERNAME"] == "researcher"
    assert env["API_USER"] == "researcher"


# -- redaction ---------------------------------------------------------------


@pytest.mark.parametrize("key", ["NEXTSEEK_TOKEN", "API_TOKEN"])
def test_the_token_keys_are_redacted(key):
    """Regression guard on an existing security property, not a new one: the
    password was already redacted, and the token replaces it."""
    assert key in cc_engine._REDACTED_ENV_KEYS


def test_the_token_value_is_scrubbed_from_output():
    """The scrubber is what stops a credential reaching a log line or a
    transcript republished to a third-party model."""
    env = _env(api_token="tok-abc")
    scrub = cc_engine.transcript_scrubber(env)
    assert b"tok-abc" not in scrub(b"the token is tok-abc, apparently")


def test_the_username_is_not_scrubbed():
    """Only the left half of the pair -- redacting the username would mangle
    output paths and log lines that legitimately name the user."""
    env = _env(api_token="tok-abc")
    scrub = cc_engine.transcript_scrubber(env)
    assert b"researcher" in scrub(b"run as researcher")

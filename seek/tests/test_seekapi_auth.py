"""``SeekAPI`` credential handling: Bearer tokens, and keeping secrets out of argv.

Two changes are under test, and they were made together deliberately.

**A token provider, not a token.** A ``SeekDB`` can outlive a token's lifetime,
so a string captured at construction goes stale mid-request. The provider is a
callable invoked at the moment of each call;
``test_the_provider_is_invoked_per_call_never_cached`` is what stops someone
"optimising" that into an attribute.

**Credentials no longer appear in the command line.** The previous form
interpolated them into a string run with ``shell=True``, which put them in
``ps`` output for every user on the host and made the credential
shell-interpolated. That was already wrong for a password; it is worse for a
bearer token, which is already-authenticated, refreshable, and works
non-interactively. They now go to curl's stdin as a config file.

There were four separate injection points, not one -- ``__curlPrefix``,
``callFileAPI`` (which built its own ``curl -u``), and the two ``requests``
methods. Each has a test here, because fixing only the first leaves file upload
and both HTML/JSON paths on Basic.
"""

from unittest.mock import patch

import pytest

from seek.seekapi import SeekAPI, _curl_config_quote


def _basic():
    return SeekAPI("http://seek:3000", "alice", "s3cret")


def _token(value="at-1"):
    return SeekAPI("http://seek:3000", None, None, token_provider=lambda: value)


def _anonymous():
    return SeekAPI("http://seek:3000", None, None)


# -- nothing secret reaches the command line ---------------------------------


@pytest.mark.parametrize(
    "api,secret",
    [
        pytest.param(_basic(), "s3cret", id="password"),
        pytest.param(_token(), "at-1", id="bearer-token"),
    ],
)
def test_no_credential_appears_in_the_command_string(api, secret):
    """The command string becomes argv (and, for __queryRaw, a shell command),
    so anything in it is visible in `ps` to every user on the host."""
    command = api.apiPost()
    assert secret not in command
    assert "--config -" in command


def test_an_anonymous_client_still_builds_a_plain_curl():
    command = _anonymous().apiPost()
    assert command.startswith("curl -k ")
    assert "--config" not in command


# -- the credential reaches the child on stdin -------------------------------


def test_callCmdline_delivers_the_credential_on_stdin():
    exitcode, out, _ = _token().callCmdline("cat")
    assert exitcode == 0
    assert out.decode() == 'header = "Authorization: Bearer at-1"\n'


def test_query_raw_delivers_the_credential_on_stdin():
    raw = SeekAPI.__dict__["_SeekAPI__queryRaw"]
    assert raw(_token(), "cat") == 'header = "Authorization: Bearer at-1"\n'


def test_an_anonymous_client_runs_without_a_stdin_pipe():
    exitcode, out, _ = _anonymous().callCmdline("echo hello")
    assert exitcode == 0
    assert out.decode().strip() == "hello"


# -- config content and escaping ---------------------------------------------


def test_basic_credentials_become_a_user_line():
    assert _basic()._credentialConfig() == 'user = "alice:s3cret"\n'


def test_a_token_becomes_a_bearer_header_line():
    assert _token()._credentialConfig() == 'header = "Authorization: Bearer at-1"\n'


def test_quotes_and_backslashes_are_escaped():
    """curl recognises \\\\ and \\" inside a quoted config value; an unescaped
    quote in a password would otherwise truncate the credential or break the
    parse."""
    api = SeekAPI("http://seek:3000", 'a"b', "c\\d")
    assert api._credentialConfig() == 'user = "a\\"b:c\\\\d"\n'
    assert _curl_config_quote('x"y\\z') == 'x\\"y\\\\z'


@pytest.mark.parametrize(
    "api",
    [
        pytest.param(_anonymous(), id="no-credential"),
        pytest.param(SeekAPI("s", None, None, token_provider=lambda: None),
                     id="provider-returns-nothing"),
        pytest.param(SeekAPI("s", "alice", None), id="username-without-password"),
    ],
)
def test_no_credential_yields_no_config(api):
    """A provider with no token is not an error: the request goes out
    unauthenticated and SEEK answers 401, the same shape as any expired
    session."""
    assert api._credentialConfig() is None


# -- freshness ---------------------------------------------------------------


def test_the_provider_is_invoked_per_call_never_cached():
    """The reason it is a callable at all. Caching this would reintroduce
    exactly the stale-token bug the indirection exists to prevent."""
    calls = []

    def provider():
        calls.append(1)
        return "at-%d" % len(calls)

    api = SeekAPI("http://seek:3000", None, None, token_provider=provider)
    assert api._credentialConfig() == 'header = "Authorization: Bearer at-1"\n'
    assert api._credentialConfig() == 'header = "Authorization: Bearer at-2"\n'
    assert api.authHeaders() == {"Authorization": "Bearer at-3"}


# -- the two requests-based injection points ---------------------------------


def test_auth_headers_carry_the_bearer_token():
    assert _token().authHeaders() == {"Authorization": "Bearer at-1"}


def test_auth_headers_fall_back_to_basic():
    from nextseek_api.helpers import basic_auth_header

    assert _basic().authHeaders() == basic_auth_header(("alice", "s3cret"))


def test_get_current_user_sends_the_bearer_header():
    """nextseek_api/views.py:611 reaches this to resolve a caller's project
    scope, so it is a live path, not a helper."""
    with patch("requests.get") as get:
        get.return_value.json.return_value = {}
        _token().getCurrentUser()
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer at-1"


def test_get_page_requests_sends_the_bearer_header():
    with patch("requests.get") as get, \
         patch.object(SeekAPI, "_SeekAPI__reviseURLs", lambda self, page: page), \
         patch.object(SeekAPI, "_SeekAPI__getHtmlpageDiv", lambda self, page, div: page):
        get.return_value.text = "<html></html>"
        _token().getPageRequests("/samples")
    assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer at-1"


def test_call_file_api_no_longer_builds_its_own_basic_curl():
    """It bypassed __curlPrefix entirely, which is why it needed fixing
    separately -- and why a grep for `curl -u` had to come back empty."""
    with patch.object(SeekAPI, "callCmdline", return_value=(0, b"", b"")) as run:
        _basic().callFileAPI("http://seek:3000/data_files/1/content_blobs/2", "/tmp/x")
    command = run.call_args.args[0]
    assert "s3cret" not in command
    assert "--config -" in command
    assert "-T \"/tmp/x\"" in command

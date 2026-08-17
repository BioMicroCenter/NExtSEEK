"""Authenticated API_SCHEMA fetch (#94).

`#77` put `IsAuthenticated` on `/nextseek_api/schema/`.
`ChatConfig._load_api_schema_from_remote` fetched that URL with a bare
`requests.get(schema_url, timeout=(2, 5))` — no credentials — so against any
patched instance it got a 401, printed one bland `Non-200` line, and returned
`{}`. That `{}` is then cached under the two-attempt cap
(`test_config_api_schema_lazy.py`), and `validate_request_body` returns
`(True, None)` for an unknown endpoint, so request-body validation silently
stopped happening for the life of the process.

The credentials were already on the object the whole time:
`helpers/tools/nextseek_api.py:132` sends `(config.API_USER, config.API_PASS)`
as Basic on every REST call, and `orchestrator.py:197-198` binds them per turn
onto a `copy.copy` of the config.

Fail-soft is preserved deliberately (a 401 still yields `{}`, never a raise) —
what changes is that the credentials are sent, and that a credential failure
says so loudly. The password itself is NEVER printed: not the value, not a
mask, not a length hint (`orchestrator.py:200-201` sets that rule).
"""
from __future__ import annotations

import pytest

from chat_nextseek.config import ChatConfig

# A spec with one real POST body, so a 200 proves the whole fetch ran end to
# end rather than just that the kwarg was captured.
SPEC_YAML = """
paths:
  /nextseek_api/thing/:
    post:
      requestBody:
        content:
          application/json:
            schema:
              type: object
              required:
                - name
              properties:
                name:
                  type: string
"""

PASSWORD = "s3cr3t-never-print-me"


class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def _bare_config(user: str | None = None, password: str | None = None) -> ChatConfig:
    """A ChatConfig shell carrying only what `_load_api_schema_from_remote`
    touches (full `__init__` needs DB/network), mirroring the helper in
    `test_config_api_schema_lazy.py`.

    API_USER / API_PASS are set by `_load_config_map` from env
    (`config.py:500-501`), so an unconfigured instance has NO such attribute
    at all — not a `None` one. Left genuinely absent here when not supplied,
    which is what forces the loader to use `getattr(..., None)`.
    """
    cfg = ChatConfig.__new__(ChatConfig)
    cfg._init_api_schema_state()
    cfg.NEXTSEEK_BASE_URL = "http://127.0.0.1:9"
    cfg.CATALOG_ENDPOINT_METHODS = {}
    cfg.CATALOG_ENDPOINT_METHODS_ALL = {}
    cfg.METHOD_PRIORITY = ["GET", "POST", "PUT", "PATCH", "DELETE"]
    if user is not None:
        cfg.API_USER = user
    if password is not None:
        cfg.API_PASS = password
    return cfg


def _capture_get(monkeypatch, resp: _Resp) -> dict:
    """Patch `config.requests.get` and record the kwargs it was called with."""
    import chat_nextseek.config as config_module

    captured: dict = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return resp

    monkeypatch.setattr(config_module.requests, "get", fake_get)
    return captured


@pytest.fixture(autouse=True)
def _yaml_required():
    # The loader short-circuits with a print and `{}` when PyYAML is missing,
    # which would make every assertion below vacuously pass.
    pytest.importorskip("yaml")


class TestCredentialsAreSent:
    """The core regression: the fetch must carry the Basic credentials the
    config already holds. RED before the fix."""

    def test_credentials_present_are_sent_as_basic_auth(self, monkeypatch):
        cfg = _bare_config("u", "p")
        captured = _capture_get(monkeypatch, _Resp(200, SPEC_YAML))

        result = cfg._load_api_schema_from_remote()

        assert captured["auth"] == ("u", "p")
        # ... and the authenticated fetch actually produced a registry.
        assert "/nextseek_api/thing/" in result
        assert result["/nextseek_api/thing/"]["required_body_fields"] == ["name"]

    def test_credentials_absent_send_no_auth_and_stay_fail_soft(self, monkeypatch):
        cfg = _bare_config()  # no API_USER / API_PASS attributes at all
        captured = _capture_get(monkeypatch, _Resp(401, "Unauthorized"))

        result = cfg._load_api_schema_from_remote()  # must not raise

        assert "auth" in captured, "auth must be passed explicitly, even as None"
        assert captured["auth"] is None
        assert result == {}

    def test_half_configured_credentials_send_no_auth(self, monkeypatch):
        """A username with an empty password is not a credential — sending
        `("u", "")` would be a bogus Basic header rather than an anonymous
        request."""
        cfg = _bare_config("u", "")
        captured = _capture_get(monkeypatch, _Resp(401, "Unauthorized"))

        assert cfg._load_api_schema_from_remote() == {}
        assert captured["auth"] is None

    def test_timeout_and_url_are_unchanged(self, monkeypatch):
        """The fix adds an argument; it must not disturb the cold-boot read
        timeout cap or the URL (see `test_config_api_schema_lazy.py`)."""
        cfg = _bare_config("u", "p")
        captured = _capture_get(monkeypatch, _Resp(200, SPEC_YAML))

        cfg._load_api_schema_from_remote()

        assert captured["timeout"] == (2, 5)
        assert captured["url"] == "http://127.0.0.1:9/nextseek_api/schema/"


class TestCredentialFailureIsLoud:
    """A 401/403 still fails soft — but must no longer be silent about WHY."""

    @pytest.mark.parametrize("status", [401, 403])
    def test_auth_failure_returns_empty_and_names_the_credentials(
        self, monkeypatch, capsys, status
    ):
        cfg = _bare_config("demo", PASSWORD)
        _capture_get(monkeypatch, _Resp(status, "Unauthorized"))

        result = cfg._load_api_schema_from_remote()

        assert result == {}  # fail-soft preserved, deliberately
        out = capsys.readouterr().out
        assert str(status) in out
        assert "API_USER" in out
        assert "API_PASS" in out
        # The operator has to be told the consequence, not just the status.
        assert "validation" in out.lower()

    def test_password_is_never_printed(self, monkeypatch, capsys):
        cfg = _bare_config("demo", PASSWORD)
        _capture_get(monkeypatch, _Resp(401, "Unauthorized"))

        cfg._load_api_schema_from_remote()

        out = capsys.readouterr().out
        assert PASSWORD not in out
        # Not a mask or a length hint either (orchestrator.py:200-201).
        assert "*" * 4 not in out
        assert str(len(PASSWORD)) not in out

    def test_never_sent_is_not_reported_as_rejected(self, monkeypatch, capsys):
        """Rejected and never-sent are different failures: one means the account
        is wrong, the other means it is missing. Claiming "REJECTED ... sent as
        user '<unset>'" for a request that carried no credentials at all sends
        the reader looking for a bad password that does not exist."""
        cfg = _bare_config()  # no API_USER / API_PASS at all
        _capture_get(monkeypatch, _Resp(401, "Unauthorized"))

        assert cfg._load_api_schema_from_remote() == {}
        out = capsys.readouterr().out
        assert "NO CREDENTIALS SENT" in out
        assert "REJECTED" not in out
        assert "<unset>" not in out
        # still tells the operator what to set and what it costs
        assert "API_USER" in out and "API_PASS" in out
        assert "validation" in out.lower()

    def test_half_configured_credentials_report_never_sent(self, monkeypatch, capsys):
        """A username with no password sends nothing, so it is the never-sent
        case even though API_USER is set."""
        cfg = _bare_config("demo", "")
        _capture_get(monkeypatch, _Resp(401, "Unauthorized"))

        assert cfg._load_api_schema_from_remote() == {}
        out = capsys.readouterr().out
        assert "NO CREDENTIALS SENT" in out
        assert "REJECTED" not in out

    def test_rejection_is_not_reported_as_never_sent(self, monkeypatch, capsys):
        """The other direction: credentials WERE sent and refused."""
        cfg = _bare_config("demo", PASSWORD)
        _capture_get(monkeypatch, _Resp(401, "Unauthorized"))

        assert cfg._load_api_schema_from_remote() == {}
        out = capsys.readouterr().out
        assert "CREDENTIALS REJECTED" in out
        assert "NO CREDENTIALS SENT" not in out
        assert "'demo'" in out  # the account is named
        assert PASSWORD not in out  # the password never is

    def test_non_auth_failure_keeps_the_plain_diagnostic(self, monkeypatch, capsys):
        """The credential line must be distinct — a 500 is not a credential
        problem and must not accuse the operator's config."""
        cfg = _bare_config("demo", PASSWORD)
        _capture_get(monkeypatch, _Resp(500, "Server Error"))

        assert cfg._load_api_schema_from_remote() == {}
        out = capsys.readouterr().out
        assert "500" in out
        assert "API_USER" not in out
        assert "API_PASS" not in out

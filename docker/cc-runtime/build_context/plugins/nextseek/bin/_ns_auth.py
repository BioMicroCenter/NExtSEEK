"""How a container-side client authenticates to NExtSEEK's API.

One place, because there are four clients (``_assistant_client``,
``_batch_upload_client``, ``_nextseek_runner``, ``container/runner_ns``) and
they must agree: a container is given exactly one credential, and sending two
is worse than sending none. NExtSEEK rejects competing credentials outright
(``nextseek_api/attributes/auth.py::_reject_competing_sources``), so a client
that helpfully attached both would have its calls refused rather than falling
back to whichever worked.

Two credentials exist because of #16, sub-project 3. A user who signed in with a
password has one; a user who signed in through SEEK does not, and carries a
per-user NExtSEEK DRF token instead. ``cc_engine.build_agent_environment``
injects whichever applies, never both.

This is a **NExtSEEK** credential. The SEEK OAuth token lives server-side and
never enters a container.
"""

from __future__ import annotations

import os

import httpx


class TokenAuth(httpx.Auth):
    """Send ``Authorization: Token <key>`` instead of HTTP Basic.

    An httpx.Auth rather than a header dict so it drops into the same ``auth=``
    parameter the clients already pass a tuple to; none of them changes shape.
    """

    def __init__(self, token: str) -> None:
        self._token = token

    def auth_flow(self, request):
        request.headers["Authorization"] = f"Token {self._token}"
        yield request

    def __repr__(self) -> str:
        return "TokenAuth('<redacted>')"


def token_from_env(env=None) -> str:
    """The DRF token this container was given, if any."""
    src = os.environ if env is None else env
    return src.get("NEXTSEEK_TOKEN") or src.get("API_TOKEN") or ""


def auth_from_env(env=None):
    """The credential to hand httpx: a TokenAuth, a Basic tuple, or None.

    The token wins when both are somehow present. That should not happen --
    build_agent_environment sets one or the other -- but if it ever does, the
    per-user token is the more specific identity and picking deterministically
    beats sending both and being refused.

    Returns None when neither is set, so callers can tell "no credential" from
    "empty credential" and report CONFIG_MISSING rather than sending
    ``Authorization: Basic <base64 of ':'>`` and puzzling over a 401.
    """
    src = os.environ if env is None else env
    token = token_from_env(src)
    if token:
        return TokenAuth(token)
    user = src.get("API_USER") or ""
    password = src.get("API_PASS") or ""
    if user and password:
        return (user, password)
    return None


def have_credential(env=None) -> bool:
    """Whether this container can authenticate at all."""
    return auth_from_env(env) is not None

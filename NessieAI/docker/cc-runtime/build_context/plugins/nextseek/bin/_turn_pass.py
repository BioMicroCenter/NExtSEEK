"""The turn pass: how every direct nextseek-* tool authenticates to NExtSEEK.

Django hands each Container-CC turn a random one-turn pass in NEXTSEEK_TURN_PASS; the container never holds the
user's password. The tools send it as ``Authorization: NextseekTurn <pass>``, a scheme only NExtSEEK's
TurnPassAuthentication answers (it is never forwarded to SEEK). The server checks the pass, the route, the method
and the chat; this module only carries it. Never print the pass.
"""
from __future__ import annotations

import os
from collections.abc import Generator

import httpx

TURN_PASS_ENV = "NEXTSEEK_TURN_PASS"
SCHEME = "NextseekTurn"


def turn_pass_from_env() -> str:
    """This turn's pass, or "" when the host set none."""
    return (os.environ.get(TURN_PASS_ENV) or "").strip()


class TurnPassAuth(httpx.Auth):
    """httpx auth that sends the turn pass on every request of a client."""

    def __init__(self, turn_pass: str) -> None:
        if not turn_pass or any(character.isspace() for character in turn_pass):
            raise ValueError("a turn pass is one non-empty token")
        self._header = f"{SCHEME} {turn_pass}"

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = self._header
        yield request

    def __repr__(self) -> str:  # never show the pass in a traceback or a log line
        return "TurnPassAuth(<hidden>)"

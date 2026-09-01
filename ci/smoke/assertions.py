"""Assertions shared by more than one tier of the smoke suite.

They live here rather than in whichever test module wrote them first so that a
second consumer does not have to import a test module to reuse one. Both are
about the difference between a response that LOOKS healthy and one that is:
nginx's 502 versus the application's, and a 200 that is really the login page.

Both take an optional `label` naming the request in the failure message. A caller
that built its URL out of a discovered identifier passes the TEMPLATE path, so
that a message written to a CI log by a production run carries
`/nextseek_api/samples/{sample_id}/` rather than a real production id. Callers
that request a literal path pass nothing and get the URL, which is what they want.
"""
from __future__ import annotations

import pytest
import requests


def _where(r: requests.Response, label: str | None) -> str:
    """How to name this request in a failure message: the caller's word for it,
    or the request itself when the caller did not give one."""
    return label or f"{r.request.method} {r.request.url}"


def check_gateway(r: requests.Response, label: str | None = None) -> None:
    """Distinguish a dead stack from an application-level 502.

    nginx returns 502 as HTML when gunicorn is not answering: that is a dead
    stack and always a failure. The application also returns 502, as a JSON
    envelope, for upstream and data conditions. Treating both the same either
    misses a real outage or paints a data problem permanently red.
    """
    if r.status_code != 502:
        return
    ctype = r.headers.get("content-type", "")
    if "json" not in ctype:
        pytest.fail(
            f"gateway is down: {_where(r, label)} returned a "
            f"non-JSON 502 ({ctype or 'no content-type'}). This is nginx, not the "
            f"application: gunicorn is not answering."
        )


def assert_not_bounced(r: requests.Response, label: str | None = None) -> None:
    """Catch an authenticated request being quietly sent to the login page.

    With a label, the location is printed without its query string. A login
    bounce lands on /login/?next=<the path that was requested>, so printing it
    whole would put the discovered identifier back into the message by the other
    door. Which route bounced is what the label already says; where it went is
    /login/, and that is the whole of the claim.
    """
    location = r.headers.get("location", "")
    shown = location.split("?", 1)[0] if label else location
    assert r.status_code != 302 or "/login" not in location, (
        f"{_where(r, label)} redirected an authenticated client to "
        f"{shown}. The session is not what the test thinks."
    )

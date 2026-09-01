"""Assertions shared by more than one tier of the smoke suite.

They live here rather than in whichever test module wrote them first so that a
second consumer does not have to import a test module to reuse one. Both are
about the difference between a response that LOOKS healthy and one that is:
nginx's 502 versus the application's, and a 200 that is really the login page.
"""
from __future__ import annotations

import pytest
import requests


def check_gateway(r: requests.Response) -> None:
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
            f"gateway is down: {r.request.method} {r.request.url} returned a "
            f"non-JSON 502 ({ctype or 'no content-type'}). This is nginx, not the "
            f"application: gunicorn is not answering."
        )


def assert_not_bounced(r: requests.Response) -> None:
    """Catch an authenticated request being quietly sent to the login page."""
    assert r.status_code != 302 or "/login" not in r.headers.get("location", ""), (
        f"{r.request.url} redirected an authenticated client to "
        f"{r.headers.get('location')}. The session is not what the test thinks."
    )

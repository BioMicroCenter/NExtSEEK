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

`describe_shape` is the third of the same family and answers the other half of
the rule: what a failing response WAS, said without quoting a single byte the
application put in the body.
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


def describe_shape(r: requests.Response) -> str:
    """Say what came back without quoting any of it.

    A failure message is written to a CI log that outlives the run, and under the
    prod profile the body of a real response is production data: a sample UID, a
    person's name, a stack trace naming a path on the box. Printing `r.text[:500]`
    puts whichever of those happened to be in the first 500 bytes into that log.
    Every fact this function reports is STRUCTURAL -- a status, a content type, the
    names of the top-level JSON keys and how many entries the container-valued ones
    hold -- which is what a reader triaging the failure actually needs:

        502 application/json keys=[detail,status] len(data)=0
        200 text/html len=41273

    A JSON body's top-level KEY names are part of the contract the test is
    asserting, so they are reported. No VALUE ever is, at any depth.
    """
    ctype = r.headers.get("content-type", "") or "no content-type"
    raw = r.content or b""
    if "json" not in ctype.lower():
        return f"{r.status_code} {ctype} len={len(raw)}"
    try:
        body = r.json()
    except ValueError:
        # Claimed JSON and is not. The claim is the finding; the bytes are not.
        return f"{r.status_code} {ctype} unparseable-json len={len(raw)}"
    if isinstance(body, dict):
        keys = sorted(str(k) for k in body)
        sizes = [
            f"len({k})={len(body[k])}"
            for k in keys
            if isinstance(body.get(k), (list, dict))
        ]
        return " ".join(
            [f"{r.status_code} {ctype}", f"keys=[{','.join(keys)}]", *sizes]
        )
    if isinstance(body, list):
        return f"{r.status_code} {ctype} list len={len(body)}"
    # A bare scalar: its type, never the scalar. A JSON string body IS a value.
    return f"{r.status_code} {ctype} {type(body).__name__}"

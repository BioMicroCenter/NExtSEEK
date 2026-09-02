"""Unit tests for the shared assertions. No stack, no network, no browser.

    PYTHONDONTWRITEBYTECODE=1 uv run --no-project --with pytest --with requests \
      --with playwright pytest ci/smoke/test_assertions_unit.py -q -p no:cacheprovider

The rule under test is the redaction one. T0 builds its URLs out of identifiers
discovered at run time, and under the prod profile those are real production
identifiers; a failure message goes into a CI log that outlives the run. So a
caller may name the request itself, and what it passes is the TEMPLATE path.
Asserted here rather than trusted, because the leak it prevents is invisible in a
green run and only appears the first time something goes wrong on production.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
import requests

from ci.smoke.assertions import assert_not_bounced, check_gateway, describe_shape

# Stands in for a discovered id. Nothing may put this in a message when a label
# was given.
RESOLVED = "http://box/nextseek_api/samples/4242/"
TEMPLATE = "/nextseek_api/samples/{sample_id}/"

Failed = pytest.fail.Exception


def _response(status: int, headers: dict[str, str]) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r.headers.update(headers)
    r.request = requests.Request("GET", RESOLVED).prepare()
    return r


DEAD_GATEWAY = {"content-type": "text/html"}
BOUNCE = {"location": "/login/?next=/nextseek_api/samples/4242/"}


def test_a_json_502_is_the_application_answering_and_not_a_failure():
    check_gateway(_response(502, {"content-type": "application/vnd.api+json"}))


def test_a_non_json_502_is_a_dead_gateway():
    with pytest.raises(Failed) as e:
        check_gateway(_response(502, DEAD_GATEWAY))
    assert "gunicorn is not answering" in str(e.value)


def test_check_gateway_names_the_request_when_no_label_is_given():
    """The existing callers pass a literal path, so nothing is redacted for them."""
    with pytest.raises(Failed) as e:
        check_gateway(_response(502, DEAD_GATEWAY))
    assert f"GET {RESOLVED}" in str(e.value)


def test_check_gateway_prints_the_label_instead_of_the_resolved_url():
    with pytest.raises(Failed) as e:
        check_gateway(_response(502, DEAD_GATEWAY), label=TEMPLATE)
    message = str(e.value)
    assert TEMPLATE in message
    assert "4242" not in message, f"a discovered identifier reached the message: {message}"


def test_a_302_somewhere_other_than_the_login_page_is_not_a_bounce():
    assert_not_bounced(_response(302, {"location": "/seek/search/"}))


def test_assert_not_bounced_names_the_request_when_no_label_is_given():
    with pytest.raises(AssertionError) as e:
        assert_not_bounced(_response(302, BOUNCE))
    assert RESOLVED in str(e.value)


def test_assert_not_bounced_prints_the_label_instead_of_the_resolved_url():
    """The `next=` query string is the other door the identifier comes back
    through, so a labelled message keeps the location's path and drops its
    query."""
    with pytest.raises(AssertionError) as e:
        assert_not_bounced(_response(302, BOUNCE), label=TEMPLATE)
    message = str(e.value)
    assert TEMPLATE in message
    assert "/login/" in message, "the message no longer says where it went"
    assert "4242" not in message, f"a discovered identifier reached the message: {message}"


# --------------------------------------------------------------------------- #
# describe_shape
# --------------------------------------------------------------------------- #

# A body carrying exactly the two things that must never be printed: a real
# identifier and a sentence of application text.
SECRET_UID = "A.TIS-240101XYZ-7"
SECRET_TEXT = "no attribute definitions for sample type 34"


def _json_response(status: int, payload) -> requests.Response:
    r = _response(status, {"content-type": "application/json"})
    r._content = json.dumps(payload).encode()
    return r


def _html_response(status: int, markup: str) -> requests.Response:
    r = _response(status, {"content-type": "text/html; charset=utf-8"})
    r._content = markup.encode()
    return r


def test_describe_shape_reports_the_keys_and_the_container_lengths():
    shape = describe_shape(_json_response(502, {"status": 502, "detail": SECRET_TEXT,
                                                "data": []}))
    assert shape.startswith("502 application/json")
    assert "keys=[data,detail,status]" in shape
    assert "len(data)=0" in shape


def test_describe_shape_never_prints_a_json_value():
    shape = describe_shape(_json_response(
        200,
        {"uid": SECRET_UID, "results": {"edges": [{"uid": SECRET_UID}]}},
    ))
    assert SECRET_UID not in shape, f"a body value reached the message: {shape}"
    assert SECRET_TEXT not in shape
    # It still says enough to triage with: the keys, and how big the nested one is.
    assert "keys=[results,uid]" in shape
    assert "len(results)=1" in shape


def test_describe_shape_of_html_reports_only_a_length():
    body = f"<html><body><h1>{SECRET_TEXT}</h1><p>{SECRET_UID}</p></body></html>"
    shape = describe_shape(_html_response(500, body))
    assert SECRET_TEXT not in shape, f"body text reached the message: {shape}"
    assert SECRET_UID not in shape
    assert shape == f"500 text/html; charset=utf-8 len={len(body)}"


def test_describe_shape_of_a_body_that_claims_json_and_is_not():
    r = _response(502, {"content-type": "application/json"})
    r._content = f"<html>{SECRET_TEXT}</html>".encode()
    shape = describe_shape(r)
    assert "unparseable-json" in shape
    assert SECRET_TEXT not in shape


def test_describe_shape_of_a_bare_scalar_reports_its_type_not_its_value():
    shape = describe_shape(_json_response(200, SECRET_UID))
    assert SECRET_UID not in shape, f"a scalar body reached the message: {shape}"
    assert shape.endswith("str")


def test_describe_shape_of_a_top_level_list_reports_its_length():
    shape = describe_shape(_json_response(200, [SECRET_UID, SECRET_UID]))
    assert SECRET_UID not in shape
    assert shape.endswith("list len=2")


def test_describe_shape_survives_a_response_with_no_body_at_all():
    """A HEAD, a 204, or a torn-down connection: r.content can be None."""
    assert describe_shape(_response(204, {})) == "204 no content-type len=0"

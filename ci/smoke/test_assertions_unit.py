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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest
import requests

from ci.smoke.assertions import assert_not_bounced, check_gateway

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

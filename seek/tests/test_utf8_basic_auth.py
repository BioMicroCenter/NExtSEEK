"""Regression tests: SEEK Basic auth must be UTF-8, not Latin-1 (#52, part 2).

``requests`` encodes ``auth=(user, password)`` with Latin-1 inside
``HTTPBasicAuth.__call__``, so any credential carrying a character outside
that range raises ``UnicodeEncodeError`` during request preparation, before a
socket is ever opened. #52 fixed six sites in ``nextseek_api/seek_api.py`` and
``nextseek_api/seek_api_helpers.py``, neither of which has a live caller. The
same defect survived in ``seek/``, where it does serve traffic:

* ``seek/views.py`` -- ``templatesList()`` used to hand a real logged-in user's
  SEEK password to ``requests.get(auth=...)`` when checking project membership.
  That check was removed with the Download Templates rewrite
  (docs/superpowers/specs/2026-08-27-download-templates-page-design.md): the
  page is now login-only and makes no SEEK HTTP call, so the site is gone rather
  than unguarded. The two ``seek/seekapi.py`` sites below still carry the
  regression tests for #52.
* ``seek/seekapi.py:190`` -- ``SeekAPI.getCurrentUser()``. Also live:
  ``SeekDB.__init__`` builds a ``SeekAPI`` (``seek/seekdb.py:28,31``),
  ``SeekDB.getCurrentUser`` delegates to it (``seek/seekdb.py:267-268``), and
  ``AdminSampleViewSet.admin_retrieve_samples`` calls that to resolve the
  caller's project scope (``nextseek_api/views.py:611-613``).
* ``seek/seekapi.py:128`` -- ``SeekAPI.getPageRequests()``.

Note on the ``ö`` trap: U+00F6 IS representable in Latin-1, so a password
containing only ``ö`` does NOT reproduce the bug. The fixtures below use
``✓`` (U+2713) and ``Ω`` (U+03A9), which are not.

Two further sites named in the same sweep, ``seek/seeksession.py:49,55`` and
``seek/snapshot.py:19`` (both ``session.auth = (...)``, the same HTTPBasicAuth
path), are fixed alongside these but are NOT covered here: neither module can
be imported under Python 3 at all. ``seek/seeksession.py:4`` does
``from urllib2 import ...`` (Python 2 only) and ``seek/snapshot.py:5`` imports
``json_normalize`` from ``pandas.io.json``, removed in modern pandas. Its only
caller, ``seek/seekdb.py:383,1458``, uses the Python 2 implicit-relative
``from seeksession import SeekSession``, which also cannot resolve. There is no
importable surface to regression-test, so those two are consistency edits only.
"""

import base64
from contextlib import contextmanager
from unittest.mock import patch

import requests

# Not representable in Latin-1: this is what actually trips HTTPBasicAuth.
NON_LATIN1_USER = "jörg"
NON_LATIN1_PASSWORD = "pa55wörd-✓-Ω"


def _expected_header(user, password):
    raw = f"{user}:{password}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


@contextmanager
def _captured_send(body=b'{"data": []}', content_type="application/json"):
    """Run the real requests preparation pipeline, which is where the Latin-1
    auth encoding happens, without opening a socket."""
    sent = {}

    def fake_send(self, request, **kwargs):
        sent["request"] = request
        resp = requests.Response()
        resp.status_code = 200
        resp._content = body
        resp.headers["Content-Type"] = content_type
        resp.url = request.url
        return resp

    with patch("requests.adapters.HTTPAdapter.send", fake_send):
        yield sent


def _auth_header(sent):
    return sent["request"].headers.get("Authorization")


# ===========================================================================
# seek/seekapi.py:128, :190
# ===========================================================================


class TestSeekAPIAuth:

    def _api(self, user=NON_LATIN1_USER, password=NON_LATIN1_PASSWORD):
        from seek.seekapi import SeekAPI

        return SeekAPI("https://seek.example", user, password)

    def test_get_current_user_sends_utf8_header(self):
        """Live path: SeekDB.getCurrentUser -> SeekAPI.getCurrentUser, reached
        by AdminSampleViewSet.admin_retrieve_samples (views.py:611-613)."""
        api = self._api()
        with _captured_send(body=b'{"data": {"id": "1"}}') as sent:
            result = api.getCurrentUser()

        assert result == {"data": {"id": "1"}}
        assert _auth_header(sent) == _expected_header(NON_LATIN1_USER, NON_LATIN1_PASSWORD)

    def test_get_page_requests_sends_utf8_header(self):
        api = self._api()
        html = b'<html><body><div id="content">ok</div></body></html>'
        with _captured_send(body=html, content_type="text/html") as sent:
            api.getPageRequests("/samples/1")

        assert _auth_header(sent) == _expected_header(NON_LATIN1_USER, NON_LATIN1_PASSWORD)

    def test_ascii_credentials_still_work(self):
        api = self._api("demo", "user")
        with _captured_send(body=b'{"data": {}}') as sent:
            api.getCurrentUser()

        assert _auth_header(sent) == _expected_header("demo", "user")

    def test_no_credentials_sends_no_auth_header(self):
        """SeekDB(server, None, None) builds SeekAPI(server, None, None)."""
        api = self._api(None, None)
        with _captured_send(body=b'{"data": {}}') as sent:
            api.getCurrentUser()

        assert _auth_header(sent) is None

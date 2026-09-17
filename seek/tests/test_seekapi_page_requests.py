"""`SeekAPI.getPageRequests` must not turn a slow or odd SEEK response into a 500.

Measured 2026-09-16: `/seek/sample_types/id=142/` returned 500 with
``AttributeError: 'NoneType' object has no attribute 'prettify'``. The SEEK page came back without a
``div#content``, ``find`` returned None, and ``prettify`` was called on it. The same call also had no
request timeout, so a slow SEEK holds the worker for as long as it likes.

Two routes already sit in ``ci/routes.py`` as expected failures in this family, one of them naming
``getPageRequests`` in its failure message, so the fragility was known and absorbed rather than fixed.
"""
import pytest

from seek.seekapi import SeekAPI


@pytest.fixture
def api():
    return SeekAPI("http://seek.invalid:3000", "user", "secret")


def _div(api_obj, html, div_id="content"):
    """Call the private scrape through its mangled name, which is how the view reaches it."""
    return api_obj._SeekAPI__getHtmlpageDiv(html, div_id)


class TestTheScrapeSurvivesAPageWithoutTheDiv:
    def test_a_page_without_the_content_div_returns_empty(self, api):
        assert _div(api, "<html><body><p>Something went wrong</p></body></html>") == ""

    def test_a_page_with_no_body_returns_empty(self, api):
        assert _div(api, "") == ""

    def test_a_login_redirect_body_returns_empty(self, api):
        html = '<html><body><div id="login">Please sign in</div></body></html>'
        assert _div(api, html) == ""

    def test_the_div_is_still_returned_when_it_is_there(self, api):
        html = '<html><body><div id="content"><p>real</p></div></body></html>'
        out = _div(api, html)
        assert "real" in out and out != ""


class TestTheFetchIsBounded:
    def test_get_is_called_with_a_timeout(self, api, monkeypatch):
        """A slow SEEK must not hold the worker indefinitely."""
        seen = {}

        class _Stop(Exception):
            pass

        def fake_get(url, **kwargs):
            seen.update(kwargs)
            raise _Stop()

        import requests
        monkeypatch.setattr(requests, "get", fake_get)
        with pytest.raises(_Stop):
            api.getPageRequests("/sample_types/1")
        assert seen.get("timeout"), (
            "requests.get was called with no timeout, so a slow SEEK holds the worker: " f"{sorted(seen)}"
        )

    def test_a_non_ok_response_does_not_raise(self, api, monkeypatch):
        """SEEK answering 500 with its own error page is not a reason to raise into the view."""

        class _Resp:
            status_code = 502
            text = "<html><body><h1>502 Bad Gateway</h1></body></html>"

        import requests
        monkeypatch.setattr(requests, "get", lambda url, **kwargs: _Resp())
        assert api.getPageRequests("/sample_types/1") == ""

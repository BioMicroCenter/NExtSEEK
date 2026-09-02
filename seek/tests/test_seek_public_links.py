"""Regression tests: browser-facing SEEK links must use SEEK_PUBLIC_URL.

SEEK_HOSTNAME is a full URL (the internal docker origin, ``http://seek:3000``).
Five browser-facing sites prepended a hardcoded ``https://`` to it, emitting
``https://http://seek:3000/...`` -- which a browser normalizes to the broken
``https://http//seek:3000/...`` -- and which pointed at an internal hostname no
browser can resolve.

Browser-facing URLs must be built from settings.SEEK_PUBLIC_URL, which already
carries its own scheme (the pattern established for sops/data_files/samples).
Internal server-to-server calls keep using SEEK_URL / SEEK_HOSTNAME.
"""

import pytest
from django.template.loader import render_to_string

PUBLIC_URL = "https://fairdata-dev.mit.edu"
INTERNAL_ORIGIN = "http://seek:3000"


@pytest.fixture
def public_url(settings):
    settings.SEEK_PUBLIC_URL = PUBLIC_URL
    return PUBLIC_URL


def _assert_no_double_scheme(html):
    """The exact defect: a scheme glued in front of another scheme."""
    assert "https://http" not in html, "double scheme emitted (https:// + http://...)"
    assert INTERNAL_ORIGIN not in html, "internal docker origin leaked into browser-facing HTML"


class TestProjectsListTemplate:
    def _render(self):
        return render_to_string(
            "projectsList.html",
            {
                "projects": [
                    {
                        "id": 1,
                        "title": "P1",
                        "avatar_id": 9,
                        "stats": {"sample_count": 1, "sop_count": 2, "df_count": 3},
                    }
                ],
                "clade_data": {},
                # Both keys are supplied on purpose: the buggy template reads
                # seek_hostname (internal origin) and must stop doing so. If we
                # omitted it the template would render an empty string and the
                # double-scheme assertions would pass vacuously.
                "seek_hostname": INTERNAL_ORIGIN,
                "seek_public_url": PUBLIC_URL,
            },
        )

    def test_project_link_uses_public_url(self, public_url):
        html = self._render()
        assert f"{PUBLIC_URL}/projects/1" in html

    def test_project_link_has_no_double_scheme(self, public_url):
        _assert_no_double_scheme(self._render())

    def test_avatar_image_uses_public_url(self, public_url):
        html = self._render()
        assert f"{PUBLIC_URL}/assets/avatar-images/9-120x120.png" in html


class TestProjectPageTemplate:
    def _render(self):
        return render_to_string(
            "projectPage.html",
            {
                "avatar_id": 9,
                # See TestProjectsListTemplate._render: supplying seek_hostname
                # keeps the double-scheme assertions non-vacuous.
                "seek_hostname": INTERNAL_ORIGIN,
                "seek_public_url": PUBLIC_URL,
            },
        )

    def test_avatar_image_uses_public_url(self, public_url):
        html = self._render()
        assert f"{PUBLIC_URL}/assets/avatar-images/9-500.png" in html

    def test_avatar_image_has_no_double_scheme(self, public_url):
        _assert_no_double_scheme(self._render())


@pytest.fixture
def seek_views(settings):
    """Import seek.views with the settings its module scope requires.

    seek/views.py reads several values from the gitignored local_settings.py
    (bind-mounted at runtime, absent from the image and a fresh checkout) at
    import time. Inject the local_settings.example.py surface so this stays a
    hermetic unit test with no untracked-file dependency.
    """
    settings.ASSISTANT_PARTICIPATING_PROJECTS = {"1"}
    settings.TEST_CASES = {}
    settings.PUBLISH_URL = "https://fairdomhub.org"
    settings.PUBLISH_STATS_FILE = "/tmp/published_stats.xlsx"
    settings.SMART_SEARCH_URL = "iframe url"
    settings.SEEK_PUBLIC_URL = PUBLIC_URL
    import seek.views as views

    return views


class TestSampleRedirectViews:
    """editSample/manageSample redirect the browser to SEEK; must be public."""

    def test_edit_sample_redirects_to_public_url(self, seek_views, rf):
        resp = seek_views.editSample(rf.get("/seek/editSample/42"), 42)
        assert resp.url == f"{PUBLIC_URL}/samples/42/edit"

    def test_manage_sample_redirects_to_public_url(self, seek_views, rf):
        resp = seek_views.manageSample(rf.get("/seek/manageSample/42"), 42)
        assert resp.url == f"{PUBLIC_URL}/samples/42/manage"

    def test_redirects_have_no_double_scheme(self, seek_views, rf):
        for resp in (
            seek_views.editSample(rf.get("/x"), 7),
            seek_views.manageSample(rf.get("/x"), 7),
        ):
            assert not resp.url.startswith("https://http"), f"double scheme: {resp.url}"
            assert INTERNAL_ORIGIN not in resp.url, f"internal origin leaked: {resp.url}"

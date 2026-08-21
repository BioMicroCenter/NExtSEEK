"""The login page's password-reset link must point at SEEK, per instance.

These are SEEK accounts. Mezzanine's ``mezzanine_password_reset`` resets the
Django user, which is not the credential people sign in with, so the link sent
users somewhere that could not help them.

The replacement is derived from ``SEEK_PUBLIC_URL`` rather than hard-coded,
because that setting already differs per instance -- https://fairdata.mit.edu on
production, https://fairdata-dev.mit.edu on dev -- and a literal would send dev
users to production. Note this is NOT ``SEEK_URL``, which is the internal docker
hostname (``http://seek:3000``) and unusable from a browser.
"""

from types import SimpleNamespace

import pytest
from django.test import override_settings

from dmac.context_processors import seek_urls


@override_settings(SEEK_PUBLIC_URL="https://fairdata.mit.edu")
def test_forgot_password_url_is_built_from_the_public_seek_host():
    ctx = seek_urls(SimpleNamespace())
    assert ctx["seek_forgot_password_url"] == "https://fairdata.mit.edu/forgot_password"
    assert ctx["seek_public_url"] == "https://fairdata.mit.edu"


@override_settings(SEEK_PUBLIC_URL="https://fairdata-dev.mit.edu")
def test_dev_gets_its_own_host_not_productions():
    ctx = seek_urls(SimpleNamespace())
    assert ctx["seek_forgot_password_url"] == "https://fairdata-dev.mit.edu/forgot_password"


@override_settings(SEEK_PUBLIC_URL="https://fairdata.mit.edu/")
def test_trailing_slash_does_not_produce_a_double_slash():
    ctx = seek_urls(SimpleNamespace())
    assert ctx["seek_forgot_password_url"] == "https://fairdata.mit.edu/forgot_password"


@override_settings(SEEK_PUBLIC_URL="")
def test_unset_public_url_yields_empty_not_a_broken_link():
    """dmac/settings.py defaults SEEK_PUBLIC_URL to '' on env-less hosts, and the
    template falls back to the Mezzanine URL when this is empty. Returning
    '/forgot_password' here would render a dead same-origin link instead."""
    ctx = seek_urls(SimpleNamespace())
    assert ctx["seek_forgot_password_url"] == ""
    assert ctx["seek_public_url"] == ""


def test_processor_is_registered_so_templates_actually_receive_it():
    """A context processor that is written but not wired renders as empty string,
    which would silently take the fallback branch forever."""
    from django.conf import settings

    processors = settings.TEMPLATES[0]["OPTIONS"]["context_processors"]
    assert "dmac.context_processors.seek_urls" in processors


def test_login_template_prefers_the_seek_url():
    from pathlib import Path

    tpl = (
        Path(__file__).resolve().parents[2]
        / "themes" / "NextSeek" / "templates" / "login.html"
    )
    text = tpl.read_text(encoding="utf-8")
    assert "seek_forgot_password_url" in text, "login page no longer links SEEK's reset"
    # The Mezzanine URL must survive as the fallback, not be deleted outright.
    assert "password_reset_url" in text

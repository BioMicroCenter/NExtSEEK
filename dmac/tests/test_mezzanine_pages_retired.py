"""Mezzanine's public pages are not part of NExtSEEK (operator ruling, 2026-10-01).

Its blog, site search, account forms and local password reset answer 404; the
SEEK login answers at /accounts/login/ too; sign-out keeps working; and every
URL name Mezzanine's admin templates reverse still resolves.
"""

import pytest
from django.http import Http404
from django.test import RequestFactory
from django.urls import resolve, reverse


@pytest.mark.parametrize("path", [
    "/blog/", "/blog/feeds/rss/", "/search/", "/accounts/", "/accounts/update/",
    "/accounts/password/reset/", "/password_reset/", "/reset/done/",
])
def test_retired_pages_answer_404(path):
    match = resolve(path)
    with pytest.raises(Http404):
        match.func(RequestFactory().get(path), *match.args, **match.kwargs)


def test_account_routes_that_stay():
    assert resolve("/accounts/login/").func.__name__ == "login_seek"
    assert resolve("/accounts/signup/").func.__name__ == "signup_seek"
    assert resolve("/accounts/logout/").func.__module__ == "mezzanine.accounts.views"
    assert reverse("logout") == "/accounts/logout/"


@pytest.mark.parametrize("name", ["password_reset", "mezzanine_password_reset", "search",
                                  "set_site", "edit", "home", "profile_update"])
def test_names_mezzanine_templates_reverse_still_resolve(name):
    reverse(name)   # a NoReverseMatch here would 500 a Mezzanine admin page


def test_user_menu_offers_only_sign_out():
    from unittest.mock import MagicMock
    from django.template.loader import render_to_string
    req = RequestFactory().get("/")
    req.user = MagicMock(username="demo", is_superuser=False, is_authenticated=True)
    html = render_to_string("accounts/includes/user_panel.html", {"request": req})
    assert "Sign out" in html and 'href="/accounts/logout/?next=/"' in html
    assert "Update profile" not in html and "Profile" not in html

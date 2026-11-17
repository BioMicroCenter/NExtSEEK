"""``/seek/admin/project_members/``: a superuser-only dropdown that jumps to SEEK's own members page.

The page lists every project (sorted by name) and sends the browser to ``<SEEK_PUBLIC_URL>/projects/<id>/admin_members``.
A non-superuser gets neither the page nor the project list.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.test import RequestFactory, override_settings
from django.template.loader import render_to_string

PROJECTS = [{"id": 3, "title": "MetNet"}, {"id": 2, "title": "IMPAcTb"}, {"id": 7, "title": "alpha"}]


def _call(is_superuser, public_url="https://fairdata.mit.edu"):
    import seek.views.admin as admin
    req = RequestFactory().get("/seek/admin/project_members/")
    req.user = SimpleNamespace(is_authenticated=True, is_superuser=is_superuser, username="u")
    with override_settings(SEEK_PUBLIC_URL=public_url), \
            patch("seek.decorators._login", return_value={"status": True}), \
            patch.object(admin, "Projects") as projects:
        projects.objects.values.return_value = list(PROJECTS)
        return admin.projectMembers(req), projects


def test_superuser_gets_every_project_sorted_by_name_with_the_instance_base():
    resp, _ = _call(True)
    html = resp.content.decode()
    assert resp.status_code == 200
    assert html.index("alpha") < html.index("IMPAcTb") < html.index("MetNet")   # case-insensitive name order
    assert 'data-members-base="https://fairdata.mit.edu"' in html
    assert 'value="2"' in html and 'value="3"' in html and 'value="7"' in html


def test_the_base_follows_the_instance_not_a_hard_coded_host():
    html = _call(True, "https://fairdata-dev.mit.edu/")[0].content.decode()
    assert 'data-members-base="https://fairdata-dev.mit.edu"' in html
    assert "fairdata.mit.edu" not in html


def test_non_superuser_gets_neither_the_page_nor_the_project_list():
    resp, projects = _call(False)
    body = resp.content.decode()
    assert "MetNet" not in body and "IMPAcTb" not in body and "admin_members" not in body
    projects.objects.values.assert_not_called()


def test_the_route_is_registered():
    from django.urls import reverse
    assert reverse("projectMembers") == "/seek/admin/project_members/"


@pytest.mark.django_db
@pytest.mark.parametrize("sup", [True, False])
def test_sidebar_entry_only_for_superusers(sup):
    user = SimpleNamespace(is_authenticated=True, is_staff=True, is_superuser=sup, username="x")
    html = render_to_string("nav.embed.html", {"request": SimpleNamespace(user=user)})
    assert ("/seek/admin/project_members/" in html) is sup

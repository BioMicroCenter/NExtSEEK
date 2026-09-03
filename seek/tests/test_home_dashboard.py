"""The home dashboard: clickable tiles, fixed recent-samples column, Nessie parity, project cards."""

from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory

FAKE_PROJECTS = [
    {"id": 2, "title": "IMPAcTb", "logo": None},
    {"id": 3, "title": "MetNet", "logo": None},
]


def _home_response():
    from dmac.views import home
    req = RequestFactory().get("/")
    req.user = AnonymousUser()
    with patch("dmac.views._home_projects", return_value=FAKE_PROJECTS):
        return home(req)


class TestHomeContext:
    def test_home_projects_helper_returns_a_list(self):
        from dmac.views import _home_projects
        result = _home_projects(None)
        assert isinstance(result, list)

    def test_project_cards_render_as_links_to_their_detail_pages(self):
        html = _home_response().content.decode()
        assert 'href="/seek/projects/2/"' in html
        assert "IMPAcTb" in html


class TestHomeDashboard:
    def test_stat_tiles_are_clickable_links(self):
        html = _home_response().content.decode()
        assert 'class="dash-tile"' in html
        assert 'href="/seek/search/"' in html      # Total samples tile -> search
        assert 'href="/seek/projects/"' in html     # Active projects tile -> projects (trailing slash)

    def test_recent_samples_column_is_labelled_created_not_project(self):
        html = _home_response().content.decode()
        assert "Created" in html

    def test_nessie_button_replaces_the_talk_to_nessie_card(self):
        html = _home_response().content.decode()
        assert "nessie-btn" in html

    def test_action_boxes_point_at_the_new_destinations(self):
        html = _home_response().content.decode()
        assert 'href="/seek/sampletypes/"' in html
        assert 'href="/seek/assays/"' in html

    def test_projects_link_has_a_trailing_slash(self):
        html = _home_response().content.decode()
        assert 'href="/seek/projects"' not in html   # the no-slash bug is gone
        assert 'href="/seek/projects/"' in html


class TestHomeProjectsScoping:
    def test_superuser_sees_all_projects(self):
        with patch("seek.decorators.verifySuperUser", return_value=1), \
             patch("seek.models.Projects") as Projects:
            Projects.objects.order_by.return_value.values.return_value = [
                {"id": 2, "title": "IMPAcTb", "avatar_id": 9},
                {"id": 3, "title": "MetNet", "avatar_id": None},
            ]
            from dmac.views import _home_projects
            out = _home_projects(MagicMock())
            assert [p["id"] for p in out] == [2, 3]
            Projects.objects.filter.assert_not_called()   # unscoped for superuser

    def test_non_superuser_is_scoped_to_their_projects(self):
        with patch("seek.decorators.verifySuperUser", return_value=0), \
             patch("seek.seekdb.SeekDB") as SeekDB, \
             patch("seek.models.Projects") as Projects:
            sdb = SeekDB.return_value
            sdb.getSeekLogin.return_value = {"status": True}
            sdb.getCurrentUser.return_value = {
                "data": {"relationships": {"projects": {"data": [{"id": "2"}]}}}
            }
            Projects.objects.filter.return_value.order_by.return_value.values.return_value = [
                {"id": 2, "title": "IMPAcTb", "avatar_id": None},
            ]
            from dmac.views import _home_projects
            out = _home_projects(MagicMock())
            assert [p["id"] for p in out] == [2]
            Projects.objects.filter.assert_called_once()  # scoped by membership
            _, kwargs = Projects.objects.filter.call_args
            assert kwargs.get("id__in") == [2]

    def test_non_member_or_anonymous_sees_none(self):
        with patch("seek.decorators.verifySuperUser", return_value=0), \
             patch("seek.seekdb.SeekDB") as SeekDB:
            SeekDB.return_value.getSeekLogin.return_value = {"status": False}
            from dmac.views import _home_projects
            assert _home_projects(MagicMock()) == []

    def test_avatar_becomes_the_logo_url(self):
        from django.test import override_settings
        with override_settings(SEEK_PUBLIC_URL="https://seek.example"), \
             patch("seek.decorators.verifySuperUser", return_value=1), \
             patch("seek.models.Projects") as Projects:
            Projects.objects.order_by.return_value.values.return_value = [
                {"id": 2, "title": "IMPAcTb", "avatar_id": 9},
            ]
            from dmac.views import _home_projects
            out = _home_projects(MagicMock())
            assert out[0]["logo"] == "https://seek.example/assets/avatar-images/9-500.png"

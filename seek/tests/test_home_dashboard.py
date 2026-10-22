"""The home dashboard: clickable tiles, fixed recent-samples column, Nessie parity, project cards."""

from unittest.mock import MagicMock, patch

import pytest
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


@pytest.fixture(autouse=True)
def _fresh_home_totals():
    # home() caches its totals for an hour; each test counts afresh.
    from django.core.cache import cache
    cache.clear()


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


class TestHomeSamplesScoping:
    """Totals are for everyone; recent sample rows are for logged-in callers, limited to the caller's projects."""

    @staticmethod
    def _ctx(user):
        from dmac.views import home
        req = RequestFactory().get("/")
        req.user = user
        with patch("dmac.views._home_projects", return_value=[]), \
             patch("dmac.views.render") as render:
            home(req)
        return render.call_args[0][2]

    def test_anonymous_sees_the_totals_but_no_sample_titles(self):
        with patch("seek.models.Samples") as Samples:
            Samples.objects.count.return_value = 99
            Samples.objects.order_by.return_value.values.return_value.__getitem__.return_value = [{"id": 1}]
            ctx = self._ctx(AnonymousUser())
        assert ctx["total_samples_count"] == 99
        assert ctx["recent_samples"] == []
        Samples.objects.order_by.assert_not_called()

    def test_member_sees_only_their_projects_samples(self):
        from types import SimpleNamespace
        from nextseek_api.graph_search.scope import Scope
        user = SimpleNamespace(is_authenticated=True, is_superuser=False, username="m", pk=7)
        with patch("nextseek_api.graph_search.scope.resolve_scope",
                   return_value=Scope(is_admin=False, person_id=1, project_ids=(5,))), \
             patch("seek.models.Projects_samples") as PS, \
             patch("seek.models.Samples") as Samples:
            Samples.objects.count.return_value = 3
            scoped = Samples.objects.filter.return_value
            scoped.order_by.return_value.values.return_value.__getitem__.return_value = [{"id": 9}]
            ctx = self._ctx(user)
        PS.objects.filter.assert_called_once_with(project_id__in=(5,))
        PS.objects.filter.return_value.values.assert_called_once_with("sample_id")
        # the subquery feeds the recent-samples filter
        Samples.objects.filter.assert_any_call(id__in=PS.objects.filter.return_value.values.return_value)
        scoped.order_by.assert_called_once_with("-id")
        scoped.order_by.return_value.values.assert_called_once_with("id", "title", "created_at")
        scoped.order_by.return_value.values.return_value.__getitem__.assert_called_once_with(slice(None, 4))
        assert ctx["recent_samples"] == [{"id": 9}]
        Samples.objects.order_by.assert_not_called()

    def test_member_without_a_seek_person_sees_no_samples(self):
        from types import SimpleNamespace
        from nextseek_api.graph_search.scope import ScopeUnavailable
        user = SimpleNamespace(is_authenticated=True, is_superuser=False, username="m", pk=7)
        with patch("nextseek_api.graph_search.scope.resolve_scope", side_effect=ScopeUnavailable("x")), \
             patch("seek.models.Samples") as Samples:
            Samples.objects.count.return_value = 0
            ctx = self._ctx(user)
        assert ctx["recent_samples"] == []
        # only the global count ran: no newest-first query, no filter
        Samples.objects.order_by.assert_not_called()
        Samples.objects.filter.assert_not_called()

    def test_superuser_sees_the_newest_samples_unscoped(self):
        from types import SimpleNamespace
        user = SimpleNamespace(is_authenticated=True, is_superuser=True, username="root", pk=1)
        with patch("seek.models.Samples") as Samples:
            Samples.objects.count.return_value = 0
            Samples.objects.order_by.return_value.values.return_value.__getitem__.return_value = [{"id": 1}]
            ctx = self._ctx(user)
        assert ctx["recent_samples"] == [{"id": 1}]


class TestVisitorHero:
    """UI-001, UI-005: a visitor is not welcomed back and gets a Sign in button."""

    def test_visitor_is_not_welcomed_back_and_can_sign_in(self):
        html = _home_response().content.decode()
        hero = html[html.index('class="dash-hero"'):html.index("</header>", html.index('class="dash-hero"'))]
        assert "Welcome back" not in hero
        assert 'href="/login/?next=/"' in hero

    def test_signed_in_user_is_welcomed_back_without_a_sign_in(self):
        from dmac.views import home
        req = RequestFactory().get("/")
        req.user = MagicMock(username="demo", is_superuser=False)
        with patch("dmac.views._home_projects", return_value=[]):
            html = home(req).content.decode()
        hero = html[html.index('class="dash-hero"'):html.index("</header>", html.index('class="dash-hero"'))]
        assert "Welcome back" in hero
        assert "/login/" not in hero


class TestHomeRecentSamplesEmptyState:
    """A signed-out visitor is told to sign in to see samples; a signed-in caller with none sees the plain empty line."""

    @staticmethod
    def _html(user):
        from django.template.loader import render_to_string
        req = RequestFactory().get("/")
        req.user = user
        ctx = {"recent_samples": [], "home_projects": [], "total_samples_count": 5}
        return render_to_string("index.html", ctx, request=req)

    def test_signed_out_visitor_is_asked_to_sign_in(self):
        html = self._html(AnonymousUser())
        assert "Sign in to see samples." in html
        assert "No recent activity yet." not in html

    def test_signed_in_caller_sees_the_plain_empty_line(self):
        from types import SimpleNamespace
        html = self._html(SimpleNamespace(is_authenticated=True, is_superuser=False, username="m", pk=7))
        assert "No recent activity yet." in html
        assert "Sign in to see samples." not in html


class TestHomeTotalsCache:
    """The totals are counted once per hour, not per request; the slow weekly sample count is gone."""

    def test_totals_are_counted_once_for_many_visits(self):
        with patch("seek.models.Samples") as Samples:
            Samples.objects.count.return_value = 5
            first = TestHomeSamplesScoping._ctx(AnonymousUser())
            second = TestHomeSamplesScoping._ctx(AnonymousUser())
        assert first["total_samples_count"] == second["total_samples_count"] == 5
        assert Samples.objects.count.call_count == 1

    def test_no_weekly_sample_count_runs_or_renders(self):
        with patch("seek.models.Samples") as Samples:
            Samples.objects.count.return_value = 5
            ctx = TestHomeSamplesScoping._ctx(AnonymousUser())
        Samples.objects.filter.assert_not_called()
        assert "samples_delta_week" not in ctx

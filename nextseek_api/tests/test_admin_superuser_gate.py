"""/admin/ (Django admin and the Mezzanine media library under it) is superuser-only (SEC-H11, SEC-H19).

The users API used to set ``is_staff`` on every account it made, and Django's admin site admits any active
staff user. Staff is no longer a role here: only superusers get in, and the API stops granting staff.
"""

import importlib

from django.contrib.auth.models import User
from django.test import Client, TestCase

ADMIN_URLS = ("/admin/", "/admin/media-library/")


def _reached_page(client, url):
    """True when the final response is a 200 that is neither a login redirect nor a login form."""
    resp = client.get(url, follow=True)
    if resp.status_code != 200:
        return False
    return not any("login" in u for u, _ in resp.redirect_chain) and b'name="password"' not in resp.content


def _run_clear_staff():
    from django.apps import apps

    importlib.import_module("nextseek_api.migrations.0033_clear_staff_non_superusers").clear_staff(apps, None)


class AdminSuperuserGateTests(TestCase):
    def _client(self, **flags):
        user = User.objects.create_user(f"u{len(flags)}{id(flags)}", password="p", is_active=True, **flags)
        client = Client()
        client.force_login(user)
        return client

    def test_non_superuser_staff_does_not_reach_admin_pages(self):
        client = self._client(is_staff=True)
        for url in ADMIN_URLS:
            with self.subTest(url=url):
                self.assertFalse(_reached_page(client, url))

    def test_staff_with_a_site_permission_does_not_reach_admin_pages(self):
        """The real exposure: Mezzanine admits staff who hold a SitePermission for the site."""
        from mezzanine.core.models import SitePermission
        from mezzanine.utils.sites import current_site_id

        client = self._client(is_staff=True)
        perm = SitePermission.objects.create(user=User.objects.get(is_staff=True))
        perm.sites.add(current_site_id())
        for url in ADMIN_URLS + ("/admin/media-library/browse/",):
            with self.subTest(url=url):
                self.assertFalse(_reached_page(client, url))

    def test_anonymous_gets_404_from_admin(self):
        for url in ("/admin/", "/admin/login/"):
            with self.subTest(url=url):
                self.assertEqual(Client().get(url).status_code, 404)

    def test_superuser_still_reaches_admin_index(self):
        client = self._client(is_staff=True, is_superuser=True)
        self.assertTrue(_reached_page(client, "/admin/"))


class AdminVisitKeepsSessionTests(TestCase):
    """Mezzanine's SitePermissionMiddleware signs out a staff non-superuser who opens /admin/."""

    def _still_signed_in(self, user):
        client = Client()
        client.force_login(user)
        client.get("/admin/")
        return "_auth_user_id" in client.session

    def test_member_visiting_admin_stays_signed_in(self):
        self.assertTrue(self._still_signed_in(User.objects.create_user("m1", password="p")))

    def test_old_staff_account_stays_signed_in_once_the_migration_has_run(self):
        user = User.objects.create_user("m2", password="p", is_staff=True)
        _run_clear_staff()
        user.refresh_from_db()
        self.assertTrue(self._still_signed_in(user))

    def test_next_request_after_admin_visit_is_not_a_login_redirect(self):
        user = User.objects.create_user("m3", password="p", is_staff=True)
        _run_clear_staff()
        user.refresh_from_db()
        client = Client()
        client.force_login(user)
        client.get("/admin/")
        resp = client.get("/docs/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.wsgi_request.user.is_authenticated)


class UsersApiDoesNotGrantStaffTests(TestCase):
    def test_sync_django_user_leaves_is_staff_false(self):
        from nextseek_api.services.users import _sync_django_user

        _sync_django_user(login="newbie", email="n@x.org", password="pw", first_name="N", last_name="B",
                          is_active=True, is_superuser=False)
        self.assertFalse(User.objects.get(username="newbie").is_staff)


    def test_is_staff_follows_is_superuser_on_create_and_update(self):
        from nextseek_api.services.users import _sync_django_user

        for flag in (True, False):
            _sync_django_user(login="flip", email="f@x.org", password="pw", first_name="F", last_name="L",
                              is_active=True, is_superuser=flag)
            user = User.objects.get(username="flip")
            self.assertEqual((user.is_superuser, user.is_staff), (flag, flag))


class ClearStaffMigrationTests(TestCase):
    def test_non_superusers_lose_staff_superusers_keep_it(self):
        plain = User.objects.create_user("plain", password="p", is_staff=True)
        boss = User.objects.create_user("boss", password="p", is_staff=True, is_superuser=True)
        _run_clear_staff()
        client = Client()
        client.force_login(plain)
        client.get("/admin/")
        self.assertIn("_auth_user_id", client.session)
        plain.refresh_from_db()
        boss.refresh_from_db()
        self.assertFalse(plain.is_staff)
        self.assertTrue(boss.is_staff)

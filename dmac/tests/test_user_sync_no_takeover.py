"""A SEEK login can create or update a NExtSEEK account only through its link to that SEEK person."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth.models import User
from django.test import Client

from nextseek_api.seek_link import SeekPersonLink
from seek.seekdb import SeekDB

pytestmark = pytest.mark.django_db
P0, P1 = "old-" + "pw", "new-" + "pw"


def _accepted(username, password, person_id=7):
    return {
        "server": "http://seek:3000", "storage": "http://seek:3000", "storagetype": "SEEK", "username": username,
        "password": password, "status": True, "err": [], "noexpire": "no", "user_id": person_id,
        "userdata": {"attributes": {"first_name": "A", "last_name": "B"}},
    }


def _login(username, password, person_id=7):
    person = SimpleNamespace(email="p@example.org")
    with patch.object(SeekDB, "getSeekLogin", return_value=_accepted(username, password, person_id)), \
            patch("dmac.views.People.objects.get", return_value=person):
        return Client().post("/login/", {"username": username, "password": password})


def test_seek_login_cannot_take_over_an_unlinked_superuser():
    User.objects.create_superuser("ops", "ops@example.org", P0)
    client_response = _login("ops", P1)
    assert client_response.status_code == 200  # login page again with the refusal
    assert "_auth_user_id" not in client_response.client.session
    assert User.objects.get(username="ops").check_password(P0)


def test_linked_user_password_change_still_syncs():
    user = User.objects.create_user("u1", "u1@example.org", P0)
    SeekPersonLink.objects.create(user=user, person_id=7)
    response = _login("u1", P1)
    assert response.status_code == 302
    assert User.objects.get(username="u1").check_password(P1)


def test_link_to_another_person_is_refused():
    user = User.objects.create_user("u1", "u1@example.org", P0)
    SeekPersonLink.objects.create(user=user, person_id=99)
    response = _login("u1", P1, person_id=7)
    assert "_auth_user_id" not in response.client.session
    assert User.objects.get(username="u1").check_password(P0)


def test_new_seek_user_gets_a_link_and_no_staff_flag():
    response = _login("fresh", P1)
    assert response.status_code == 302
    user = User.objects.get(username="fresh")
    assert not user.is_staff and not user.is_superuser
    assert SeekPersonLink.objects.get(user=user).person_id == 7


def test_new_user_for_an_already_linked_person_is_refused():
    other = User.objects.create_user("renamed", "r@example.org", P0)
    SeekPersonLink.objects.create(user=other, person_id=7)
    response = _login("fresh", P1, person_id=7)
    assert "_auth_user_id" not in response.client.session
    assert not User.objects.filter(username="fresh").exists()


def test_missing_people_row_returns_a_pair():
    from dmac.views import userSynchronization

    with patch("dmac.views.People.objects.get", side_effect=Exception("none")):
        status, msg = userSynchronization(_accepted("x", P1))
    assert status == 0 and "SEEK people table" in msg


def test_backfill_links_by_login_and_skips_superusers_without_email_match():
    import importlib

    migration = importlib.import_module("nextseek_api.migrations.0028_seekpersonlink")
    User.objects.create_user("member", "m@example.org", P0)
    User.objects.create_superuser("same", "Same@Example.org", P0)
    User.objects.create_superuser("seekadmin", "other@example.org", P0)
    User.objects.create_superuser("stranger", "s@example.org", P0)
    User.objects.create_superuser("notinseek", "n@example.org", P0)
    rows = [("member", 1, "m@example.org"), ("same", 2, "same@example.org"),
            ("seekadmin", 3, "x@example.org"), ("stranger", 4, "different@example.org")]
    linked, unlinked = migration.link_accounts(User, SeekPersonLink, rows, {3})
    assert linked == 3
    assert set(SeekPersonLink.objects.values_list("user__username", flat=True)) == {"member", "same", "seekadmin"}
    assert unlinked == ["notinseek", "stranger"]


def test_backfill_never_raises_without_the_seek_tables():
    import importlib

    migration = importlib.import_module("nextseek_api.migrations.0028_seekpersonlink")
    from django.apps import apps
    from django.db import connection

    migration.backfill(apps, SimpleNamespace(connection=connection))  # SQLite has no SEEK tables: prints and skips


def test_linked_update_never_grants_staff_or_superuser():
    user = User.objects.create_user("plain", "plain@example.org", P0)
    SeekPersonLink.objects.create(user=user, person_id=7)
    _login("plain", P1)
    user.refresh_from_db()
    assert not user.is_staff and not user.is_superuser


def test_backfill_matches_a_login_that_differs_only_by_case():
    import importlib

    migration = importlib.import_module("nextseek_api.migrations.0028_seekpersonlink")
    User.objects.create_user("JSmith", "j@example.org", P0)
    User.objects.create_user("other", "o@example.org", P0)
    linked, _ = migration.link_accounts(User, SeekPersonLink, [("jsmith", 1, "j@example.org"), ("other", 2, "")], set())
    assert linked == 2


def test_backfill_one_bad_row_does_not_stop_the_rest():
    import importlib
    from unittest.mock import MagicMock

    migration = importlib.import_module("nextseek_api.migrations.0028_seekpersonlink")
    User.objects.create_user("a", "a@example.org", P0)
    User.objects.create_user("b", "b@example.org", P0)
    fake = MagicMock()
    fake.objects.filter.return_value.exists.return_value = False
    fake.objects.create.side_effect = [RuntimeError("boom"), None]
    linked, _ = migration.link_accounts(User, fake, [("a", 1, ""), ("b", 2, "")], set())
    assert linked == 1

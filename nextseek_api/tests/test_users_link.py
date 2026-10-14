"""Accounts the admin Users API creates are linked to their SEEK person, so a SEEK password change keeps syncing."""
import pytest
from django.contrib.auth.models import User

from nextseek_api.seek_link import SeekPersonLink
from nextseek_api.services import users as users_service

pytestmark = pytest.mark.django_db


def test_sync_django_user_links_the_new_account_to_its_person():
    users_service._sync_django_user(
        login="newbie", email="n@example.org", password="x" * 12, first_name="N", last_name="B",
        is_active=True, is_superuser=False, person_id=41,
    )
    assert SeekPersonLink.objects.get(user__username="newbie").person_id == 41


def test_link_person_same_pair_is_a_no_op():
    user = User.objects.create_user("again", "a@example.org", "x")
    users_service._link_person(user, 5)
    users_service._link_person(user, 5)
    assert SeekPersonLink.objects.filter(user=user).count() == 1


def test_link_person_refuses_to_repoint_an_account():
    user = User.objects.create_user("held", "h@example.org", "x")
    users_service._link_person(user, 5)
    with pytest.raises(users_service.LinkConflict):
        users_service._link_person(user, 6)
    assert SeekPersonLink.objects.get(user=user).person_id == 5


def test_link_person_refuses_a_person_linked_to_another_account():
    first = User.objects.create_user("first", "f@example.org", "x")
    second = User.objects.create_user("second", "s@example.org", "x")
    users_service._link_person(first, 5)
    with pytest.raises(users_service.LinkConflict):
        users_service._link_person(second, 5)
    assert not SeekPersonLink.objects.filter(user=second).exists()


def _patch_new_account(person_id):
    from unittest.mock import MagicMock, patch

    from nextseek_api.models import UserAdminRecord
    from nextseek_api.tests.test_services_users import _superuser, _wrap
    from rest_framework.test import APIRequestFactory

    seek_user = MagicMock(id=5, person_id=person_id, login="newbie", activation_code=None)
    record = UserAdminRecord(user_id=5, person_id=person_id, login="newbie", email="n@example.com", first_name="N",
                             last_name="E", active=True, django_is_active=True, django_is_superuser=False,
                             project_id=1, institution_id=1)
    request = _wrap(APIRequestFactory().patch("/nextseek_api/users/5/", {"email": "n@example.com"}, format="json"))
    request.user = _superuser()
    with patch.object(users_service, "Users") as users, patch.object(users_service, "People"), \
            patch.object(users_service, "run_seek_rails_runner",
                         return_value={"user_id": 5, "person_id": person_id, "login": "newbie"}), \
            patch.object(users_service, "_upsert_people_mirror"), patch.object(users_service, "_build_record",
                                                                              return_value=record), \
            patch.object(users_service.hooks, "enqueue"):
        users.objects.using.return_value.filter.return_value.first.return_value = seek_user
        return users_service.UsersViewSet().partial_update(request, uid="5")


def test_partial_update_creates_account_and_link():
    assert _patch_new_account(6).status_code == 200
    assert SeekPersonLink.objects.get(user__username="newbie").person_id == 6


def test_partial_update_link_conflict_is_409_and_leaves_no_account():
    other = User.objects.create_user("holder", "h@example.org", "x")
    SeekPersonLink.objects.create(user=other, person_id=6)
    response = _patch_new_account(6)
    assert response.status_code == 409
    assert not User.objects.filter(username="newbie").exists()

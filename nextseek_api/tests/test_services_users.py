"""Hermetic tests for the superuser-only Users admin ViewSet."""

import json
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User as DjangoUser
from pydantic import ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from nextseek_api.models import UserAdminRecord, UserCreateRequest, UserUpdateRequest
from nextseek_api.services.seek_rails_runner import SeekRailsRunnerError, SeekRailsUnavailableError
from nextseek_api.services.users import IsDjangoSuperuser, UsersViewSet, _build_record, _validate_seek_user_id


def _wrap(factory_request):
    return Request(
        factory_request,
        parsers=[JSONParser(), FormParser(), MultiPartParser()],
    )


def _superuser():
    user = MagicMock()
    user.is_authenticated = True
    user.is_superuser = True
    user.is_staff = True
    user.username = "admin"
    return user


def _staff_only():
    user = MagicMock()
    user.is_authenticated = True
    user.is_superuser = False
    user.is_staff = True
    return user


CREATE_BODY = {
    "login": "testuser",
    "password": "testpassword",
    "password_confirmation": "testpassword",
    "email": "testuser@example.com",
    "first_name": "Test",
    "last_name": "User",
    "project_id": 1,
    "institution_id": 1,
    "is_superuser": False,
    "activate": True,
}


class TestModelsAndHelpers:
    def test_create_request_validates(self):
        req = UserCreateRequest.model_validate(CREATE_BODY)
        assert req.login == "testuser"

    def test_validate_seek_user_id_numeric_only(self):
        assert _validate_seek_user_id("42") == "42"
        assert _validate_seek_user_id("abc") is None


class TestPermissions:
    def test_is_django_superuser_allows_superuser(self):
        perm = IsDjangoSuperuser()
        request = MagicMock(user=_superuser())
        assert perm.has_permission(request, None) is True

    def test_is_django_superuser_denies_staff_non_super(self):
        perm = IsDjangoSuperuser()
        request = MagicMock(user=_staff_only())
        assert perm.has_permission(request, None) is False


class TestBuildRecordHelpers:
    @patch("nextseek_api.services.users._django_flags", return_value=(True, False))
    @patch("nextseek_api.services.users._membership_for_person", return_value=(2, 3))
    @patch("nextseek_api.services.users.People")
    def test_build_record(self, mock_people, mock_mem, mock_flags):
        seek_user = MagicMock(id=1, person_id=10, login="u1", activation_code=None)
        person = MagicMock(email="a@b.com", first_name="A", last_name="B")
        mock_people.objects.using.return_value.filter.return_value.first.return_value = person
        record = _build_record(seek_user)
        assert record.login == "u1"
        assert record.project_id == 2

    @patch("nextseek_api.services.users.People")
    def test_build_record_missing_person_raises(self, mock_people):
        seek_user = MagicMock(id=1, person_id=10, login="u1", activation_code=None)
        mock_people.objects.using.return_value.filter.return_value.first.return_value = None
        with pytest.raises(LookupError):
            _build_record(seek_user)

    def test_seek_user_active_false_when_code_set(self):
        from nextseek_api.services.users import _seek_user_active

        user = MagicMock(activation_code="abc")
        assert _seek_user_active(user) is False

    @patch("nextseek_api.services.users.DjangoUser.objects.get")
    def test_django_flags(self, mock_get):
        from nextseek_api.services.users import _django_flags

        user = MagicMock(is_active=True, is_superuser=True)
        mock_get.return_value = user
        assert _django_flags("x") == (True, True)
        mock_get.side_effect = DjangoUser.DoesNotExist
        assert _django_flags("missing") == (False, False)

    @patch("nextseek_api.services.users.connections")
    def test_membership_for_person(self, mock_connections):
        from nextseek_api.services.users import _membership_for_person

        cursor = MagicMock()
        cursor.fetchone.return_value = (7, 8)
        mock_connections.__getitem__.return_value.cursor.return_value.__enter__.return_value = cursor
        assert _membership_for_person(99) == (7, 8)
        cursor.fetchone.return_value = None
        assert _membership_for_person(99) == (None, None)


class TestUsersViewSetCreate:
    @patch("nextseek_api.services.users._sync_django_user")
    @patch("nextseek_api.services.users.People")
    @patch("nextseek_api.services.users.Users")
    @patch("nextseek_api.services.users.run_seek_rails_runner")
    def test_create_success(self, mock_runner, mock_users, mock_people, mock_sync):
        mock_runner.return_value = {
            "ok": True,
            "user_id": 10,
            "person_id": 20,
            "login": "testuser",
            "email": "testuser@example.com",
            "active": True,
            "project_id": 1,
            "institution_id": 1,
        }
        mock_users.objects.using.return_value.filter.return_value.exists.return_value = False
        seek_user = MagicMock(id=10, person_id=20, login="testuser", activation_code=None)
        mock_users.objects.using.return_value.get.return_value = seek_user
        mock_people.objects.using.return_value.filter.return_value.exists.return_value = True

        with patch("nextseek_api.services.users._build_record") as mock_build:
            mock_build.return_value = UserAdminRecord(
                user_id=10,
                person_id=20,
                login="testuser",
                email="testuser@example.com",
                first_name="Test",
                last_name="User",
                active=True,
                django_is_active=True,
                django_is_superuser=False,
                project_id=1,
                institution_id=1,
            )
            factory = APIRequestFactory()
            request = _wrap(factory.post("/nextseek_api/users/", CREATE_BODY, format="json"))
            request.user = _superuser()
            response = UsersViewSet().create(request)

        assert response.status_code == 201
        mock_runner.assert_called_once()
        mock_sync.assert_called_once()

    @patch("nextseek_api.services.users.Users")
    def test_create_duplicate_login_409(self, mock_users):
        mock_users.objects.using.return_value.filter.return_value.exists.return_value = True
        factory = APIRequestFactory()
        request = _wrap(factory.post("/nextseek_api/users/", CREATE_BODY, format="json"))
        request.user = _superuser()
        response = UsersViewSet().create(request)
        assert response.status_code == 409

    def test_create_password_mismatch_422(self):
        body = dict(CREATE_BODY, password_confirmation="wrongpassword")
        factory = APIRequestFactory()
        request = _wrap(factory.post("/nextseek_api/users/", body, format="json"))
        request.user = _superuser()
        response = UsersViewSet().create(request)
        assert response.status_code == 422

    def test_create_password_too_short_422(self):
        body = dict(CREATE_BODY, password="short", password_confirmation="short")
        factory = APIRequestFactory()
        request = _wrap(factory.post("/nextseek_api/users/", body, format="json"))
        request.user = _superuser()
        response = UsersViewSet().create(request)
        assert response.status_code == 422

    @patch.object(IsDjangoSuperuser, "has_permission", return_value=True)
    @patch("nextseek_api.services.users.Users")
    def test_create_grant_superuser_forbidden_403(self, mock_users, _perm):
        mock_users.objects.using.return_value.filter.return_value.exists.return_value = False
        body = dict(CREATE_BODY, is_superuser=True)
        factory = APIRequestFactory()
        request = _wrap(factory.post("/nextseek_api/users/", body, format="json"))
        request.user = _staff_only()
        response = UsersViewSet().create(request)
        assert response.status_code == 403

    @patch("nextseek_api.services.users.People")
    @patch("nextseek_api.services.users.Users")
    @patch("nextseek_api.services.users.run_seek_rails_runner")
    def test_create_person_mirror_missing_502(self, mock_runner, mock_users, mock_people):
        mock_users.objects.using.return_value.filter.return_value.exists.return_value = False
        mock_runner.return_value = {"user_id": 1, "person_id": 99}
        mock_people.objects.using.return_value.filter.return_value.exists.return_value = False
        factory = APIRequestFactory()
        request = _wrap(factory.post("/nextseek_api/users/", CREATE_BODY, format="json"))
        request.user = _superuser()
        response = UsersViewSet().create(request)
        assert response.status_code == 502

    @patch("nextseek_api.services.users.Users")
    @patch("nextseek_api.services.users.run_seek_rails_runner")
    def test_create_runner_error_502(self, mock_runner, mock_users):
        mock_users.objects.using.return_value.filter.return_value.exists.return_value = False
        mock_runner.side_effect = SeekRailsRunnerError("validation failed", detail="bad email")
        factory = APIRequestFactory()
        request = _wrap(factory.post("/nextseek_api/users/", CREATE_BODY, format="json"))
        request.user = _superuser()
        response = UsersViewSet().create(request)
        assert response.status_code == 502

    @patch("nextseek_api.services.users.Users")
    @patch("nextseek_api.services.users.run_seek_rails_runner")
    def test_create_runner_conflict_409(self, mock_runner, mock_users):
        mock_users.objects.using.return_value.filter.return_value.exists.return_value = False
        mock_runner.side_effect = SeekRailsRunnerError("Email has already been taken", detail="taken")
        factory = APIRequestFactory()
        request = _wrap(factory.post("/nextseek_api/users/", CREATE_BODY, format="json"))
        request.user = _superuser()
        response = UsersViewSet().create(request)
        assert response.status_code == 409


    @patch("nextseek_api.services.users.Users")
    @patch("nextseek_api.services.users.run_seek_rails_runner")
    def test_create_runner_unavailable_503(self, mock_runner, mock_users):
        mock_runner.side_effect = SeekRailsUnavailableError("no docker")
        mock_users.objects.using.return_value.filter.return_value.exists.return_value = False
        factory = APIRequestFactory()
        request = _wrap(factory.post("/nextseek_api/users/", CREATE_BODY, format="json"))
        request.user = _superuser()
        response = UsersViewSet().create(request)
        assert response.status_code == 503

    def test_create_invalid_body_422(self):
        factory = APIRequestFactory()
        bad = dict(CREATE_BODY)
        bad.pop("project_id")
        request = _wrap(factory.post("/nextseek_api/users/", bad, format="json"))
        request.user = _superuser()
        response = UsersViewSet().create(request)
        assert response.status_code == 422


class TestUsersViewSetRetrieve:
    @patch("nextseek_api.services.users._build_record")
    @patch("nextseek_api.services.users.Users")
    def test_retrieve_not_found(self, mock_users, mock_build):
        mock_users.objects.using.return_value.filter.return_value.first.return_value = None
        factory = APIRequestFactory()
        request = _wrap(factory.get("/nextseek_api/users/99/"))
        request.user = _superuser()
        response = UsersViewSet().retrieve(request, uid="99")
        assert response.status_code == 404

    @patch("nextseek_api.services.users._build_record")
    @patch("nextseek_api.services.users.Users")
    def test_retrieve_success(self, mock_users, mock_build):
        seek_user = MagicMock(id=5, person_id=6, login="demo")
        mock_users.objects.using.return_value.filter.return_value.first.return_value = seek_user
        mock_build.return_value = UserAdminRecord(
            user_id=5,
            person_id=6,
            login="demo",
            email="d@example.com",
            first_name="D",
            last_name="E",
            active=True,
            django_is_active=True,
            django_is_superuser=False,
            project_id=1,
            institution_id=1,
        )
        factory = APIRequestFactory()
        request = _wrap(factory.get("/nextseek_api/users/5/"))
        request.user = _superuser()
        response = UsersViewSet().retrieve(request, uid="5")
        assert response.status_code == 200

    def test_retrieve_invalid_uid_404(self):
        factory = APIRequestFactory()
        request = _wrap(factory.get("/nextseek_api/users/abc/"))
        request.user = _superuser()
        response = UsersViewSet().retrieve(request, uid="abc")
        assert response.status_code == 404


class TestUsersViewSetPatch:
    @patch("nextseek_api.services.users._build_record")
    @patch("nextseek_api.services.users.People")
    @patch("nextseek_api.services.users.DjangoUser")
    @patch("nextseek_api.services.users.run_seek_rails_runner")
    @patch("nextseek_api.services.users.Users")
    def test_patch_deactivate(self, mock_users, mock_runner, mock_dj, mock_people, mock_build):
        seek_user = MagicMock(id=5, person_id=6, login="demo", activation_code=None)
        mock_users.objects.using.return_value.filter.return_value.first.return_value = seek_user
        mock_runner.return_value = {
            "ok": True,
            "user_id": 5,
            "person_id": 6,
            "login": "demo",
            "project_id": 1,
            "institution_id": 1,
        }
        person = MagicMock(email="d@example.com", first_name="D", last_name="E")
        mock_people.objects.using.return_value.get.return_value = person
        dj = MagicMock()
        mock_dj.objects.filter.return_value.first.return_value = dj
        mock_build.return_value = UserAdminRecord(
            user_id=5,
            person_id=6,
            login="demo",
            email="d@example.com",
            first_name="D",
            last_name="E",
            active=False,
            django_is_active=False,
            django_is_superuser=False,
            project_id=1,
            institution_id=1,
        )

        factory = APIRequestFactory()
        request = _wrap(factory.patch("/nextseek_api/users/5/", {"active": False}, format="json"))
        request.user = _superuser()
        response = UsersViewSet().partial_update(request, uid="5")

        assert response.status_code == 200
        assert dj.is_active is False
        mock_runner.assert_called_once()

    @patch("nextseek_api.services.users._build_record")
    @patch("nextseek_api.services.users.People")
    @patch("nextseek_api.services.users.DjangoUser")
    @patch("nextseek_api.services.users.Users")
    def test_patch_superuser_only_django(self, mock_users, mock_dj, mock_people, mock_build):
        seek_user = MagicMock(id=5, person_id=6, login="demo", activation_code=None)
        mock_users.objects.using.return_value.filter.return_value.first.return_value = seek_user
        mock_people.objects.using.return_value.get.return_value = MagicMock(
            email="d@example.com", first_name="D", last_name="E"
        )
        dj = MagicMock()
        mock_dj.objects.filter.return_value.first.return_value = dj
        mock_build.return_value = UserAdminRecord(
            user_id=5,
            person_id=6,
            login="demo",
            email="d@example.com",
            first_name="D",
            last_name="E",
            active=True,
            django_is_active=True,
            django_is_superuser=True,
            project_id=1,
            institution_id=1,
        )
        factory = APIRequestFactory()
        request = _wrap(factory.patch("/nextseek_api/users/5/", {"is_superuser": True}, format="json"))
        request.user = _superuser()
        response = UsersViewSet().partial_update(request, uid="5")
        assert response.status_code == 200
        assert dj.is_superuser is True

    @patch("nextseek_api.services.users.Users")
    def test_patch_empty_body_422(self, mock_users):
        seek_user = MagicMock(id=5, person_id=6, login="demo")
        mock_users.objects.using.return_value.filter.return_value.first.return_value = seek_user
        factory = APIRequestFactory()
        request = _wrap(factory.patch("/nextseek_api/users/5/", {}, format="json"))
        request.user = _superuser()
        response = UsersViewSet().partial_update(request, uid="5")
        assert response.status_code == 422

    @patch("nextseek_api.services.users.Users")
    def test_patch_project_without_institution_422(self, mock_users):
        seek_user = MagicMock(id=5, person_id=6, login="demo")
        mock_users.objects.using.return_value.filter.return_value.first.return_value = seek_user
        factory = APIRequestFactory()
        request = _wrap(factory.patch("/nextseek_api/users/5/", {"project_id": 1}, format="json"))
        request.user = _superuser()
        response = UsersViewSet().partial_update(request, uid="5")
        assert response.status_code == 422


class TestUsersViewSetList:
    @patch("nextseek_api.services.users._build_record")
    @patch("nextseek_api.services.users.Users")
    def test_list_skips_users_without_person(self, mock_users, mock_build):
        u1 = MagicMock(id=1, person_id=None)
        u2 = MagicMock(id=2, person_id=3, login="x", activation_code=None)
        u3 = MagicMock(id=3, person_id=4, login="y", activation_code=None)
        mock_users.objects.using.return_value.all.return_value.order_by.return_value = [u1, u2, u3]

        def _side_effect(user):
            if user.id == 3:
                raise LookupError("missing")
            return UserAdminRecord(
                user_id=2,
                person_id=3,
                login="x",
                email="x@example.com",
                first_name="X",
                last_name="Y",
                active=True,
                django_is_active=True,
                django_is_superuser=False,
                project_id=1,
                institution_id=1,
            )

        mock_build.side_effect = _side_effect

        factory = APIRequestFactory()
        request = _wrap(factory.get("/nextseek_api/users/"))
        request.user = _superuser()
        response = UsersViewSet().list(request)
        assert response.status_code == 200
        body = json.loads(response.content)
        assert len(body["data"]) == 1


    @patch("nextseek_api.services.users._build_record")
    @patch("nextseek_api.services.users.Users")
    def test_retrieve_lookup_error_404(self, mock_users, mock_build):
        seek_user = MagicMock(id=5, person_id=6, login="demo")
        mock_users.objects.using.return_value.filter.return_value.first.return_value = seek_user
        mock_build.side_effect = LookupError("no person")
        factory = APIRequestFactory()
        request = _wrap(factory.get("/nextseek_api/users/5/"))
        request.user = _superuser()
        response = UsersViewSet().retrieve(request, uid="5")
        assert response.status_code == 404


class TestUsersViewSetPatchExtra:
    @patch("nextseek_api.services.users.Users")
    def test_patch_invalid_uid_404(self, mock_users):
        factory = APIRequestFactory()
        request = _wrap(factory.patch("/nextseek_api/users/x/", {"active": False}, format="json"))
        request.user = _superuser()
        response = UsersViewSet().partial_update(request, uid="x")
        assert response.status_code == 404

    @patch("nextseek_api.services.users.run_seek_rails_runner")
    @patch("nextseek_api.services.users.Users")
    def test_patch_runner_unavailable_503(self, mock_users, mock_runner):
        seek_user = MagicMock(id=5, person_id=6, login="demo")
        mock_users.objects.using.return_value.filter.return_value.first.return_value = seek_user
        mock_runner.side_effect = SeekRailsUnavailableError("down")
        factory = APIRequestFactory()
        request = _wrap(factory.patch("/nextseek_api/users/5/", {"active": False}, format="json"))
        request.user = _superuser()
        response = UsersViewSet().partial_update(request, uid="5")
        assert response.status_code == 503

    @patch("nextseek_api.services.users._build_record")
    @patch("nextseek_api.services.users.People")
    @patch("nextseek_api.services.users.DjangoUser")
    @patch("nextseek_api.services.users.run_seek_rails_runner")
    @patch("nextseek_api.services.users.Users")
    def test_patch_creates_django_user_when_missing(
        self, mock_users, mock_runner, mock_dj, mock_people, mock_build
    ):
        seek_user = MagicMock(id=5, person_id=6, login="newbie", activation_code=None)
        mock_users.objects.using.return_value.filter.return_value.first.return_value = seek_user
        mock_runner.return_value = {"user_id": 5, "person_id": 6, "login": "newbie", "project_id": 1, "institution_id": 1}
        mock_people.objects.using.return_value.get.return_value = MagicMock(
            email="n@example.com", first_name="N", last_name="E"
        )
        mock_dj.objects.filter.return_value.first.return_value = None
        created = MagicMock()
        mock_dj.return_value = created
        mock_build.return_value = UserAdminRecord(
            user_id=5,
            person_id=6,
            login="newbie",
            email="n@example.com",
            first_name="N",
            last_name="E",
            active=True,
            django_is_active=True,
            django_is_superuser=False,
            project_id=1,
            institution_id=1,
        )
        factory = APIRequestFactory()
        request = _wrap(
            factory.patch(
                "/nextseek_api/users/5/",
                {"email": "n@example.com", "first_name": "N", "last_name": "E"},
                format="json",
            )
        )
        request.user = _superuser()
        response = UsersViewSet().partial_update(request, uid="5")
        assert response.status_code == 200
        created.save.assert_called_once()

    @patch.object(IsDjangoSuperuser, "has_permission", return_value=True)
    @patch("nextseek_api.services.users.Users")
    def test_patch_grant_superuser_forbidden(self, mock_users, _perm):
        seek_user = MagicMock(id=5, person_id=6, login="demo")
        mock_users.objects.using.return_value.filter.return_value.first.return_value = seek_user
        factory = APIRequestFactory()
        request = _wrap(factory.patch("/nextseek_api/users/5/", {"is_superuser": True}, format="json"))
        request.user = _staff_only()
        response = UsersViewSet().partial_update(request, uid="5")
        assert response.status_code == 403

    @patch("nextseek_api.services.users.Users")
    def test_patch_password_partial_422(self, mock_users):
        seek_user = MagicMock(id=5, person_id=6, login="demo")
        mock_users.objects.using.return_value.filter.return_value.first.return_value = seek_user
        factory = APIRequestFactory()
        request = _wrap(factory.patch("/nextseek_api/users/5/", {"password": "onlyonefield"}, format="json"))
        request.user = _superuser()
        response = UsersViewSet().partial_update(request, uid="5")
        assert response.status_code == 422

    @patch("nextseek_api.services.users.Users")
    def test_patch_institution_without_project_422(self, mock_users):
        seek_user = MagicMock(id=5, person_id=6, login="demo")
        mock_users.objects.using.return_value.filter.return_value.first.return_value = seek_user
        factory = APIRequestFactory()
        request = _wrap(factory.patch("/nextseek_api/users/5/", {"institution_id": 2}, format="json"))
        request.user = _superuser()
        response = UsersViewSet().partial_update(request, uid="5")
        assert response.status_code == 422


class TestRoutingAndSchema:
    def test_routes_registered(self):
        from django.urls import reverse

        assert reverse("nextseek_api:users-list").endswith("/users/")
        assert reverse("nextseek_api:users-detail", kwargs={"uid": "1"}).endswith("/users/1/")

    def test_schema_includes_users_without_delete(self):
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        paths = schema.get("paths", {})
        users_paths = [p for p in paths if p.endswith("/users/") or "/users/{uid}/" in p]
        assert users_paths
        for path in users_paths:
            methods = set(paths[path].keys())
            assert "delete" not in methods
            assert "get" in methods or "post" in methods or "patch" in methods

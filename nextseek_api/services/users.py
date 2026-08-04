"""Superuser-only admin ViewSet to mint and manage SEEK User logins."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings
from django.contrib.auth.models import User as DjangoUser
from django.db import connections
from django.http import HttpResponse
from pydantic import ValidationError
from rest_framework import viewsets
from rest_framework.permissions import BasePermission, IsAuthenticated
from drf_spectacular.utils import OpenApiExample, OpenApiParameter, extend_schema

from nextseek_api.endpoint_descriptions import (
    USER_CREATE_DESC,
    USER_FETCH_DESC,
    USER_LIST_DESC,
    USER_UPDATE_DESC,
)
from nextseek_api.models import (
    UserAdminRecord,
    UserCreateRequest,
    UserListResponse,
    UserSingleResponse,
    UserUpdateRequest,
)
from nextseek_api.services.seek_rails_runner import (
    SeekRailsRunnerError,
    SeekRailsUnavailableError,
    run_seek_rails_runner,
)
from seek.models import People, Users

logger = logging.getLogger(__name__)

SEEK_CREATE_RUBY = """
begin
  project = Project.find(payload['project_id'])
  institution = Institution.find(payload['institution_id'])
  person = Person.create!(
    first_name: payload['first_name'],
    last_name: payload['last_name'],
    email: payload['email']
  )
  person.add_to_project_and_institution(project, institution)
  user = User.new(
    login: payload['login'],
    password: payload['password'],
    password_confirmation: payload['password_confirmation'],
    email: person.email
  )
  user.person = person
  user.save!
  user.activate if payload.fetch('activate', true) && !user.active?
  puts({
    ok: true,
    user_id: user.id,
    person_id: person.id,
    login: user.login,
    email: person.email,
    active: user.active?,
    project_id: project.id,
    institution_id: institution.id
  }.to_json)
rescue ActiveRecord::RecordInvalid => e
  puts({ ok: false, error: e.message, detail: e.record.errors.full_messages.join('; ') }.to_json)
  exit 1
rescue => e
  puts({ ok: false, error: e.message, class: e.class.name }.to_json)
  exit 1
end
"""

SEEK_PATCH_RUBY = """
begin
  user = User.find(payload['user_id'])
  person = user.person
  raise 'User has no linked person' if person.nil?

  if payload['first_name']
    person.first_name = payload['first_name']
  end
  if payload['last_name']
    person.last_name = payload['last_name']
  end
  if payload['email']
    person.email = payload['email']
  end
  if payload['first_name'] || payload['last_name'] || payload['email']
    person.save!
  end

  if payload['password'] && payload['password_confirmation']
    user.password = payload['password']
    user.password_confirmation = payload['password_confirmation']
    user.save!
  end

  if payload.key?('active')
    if payload['active']
      user.activate unless user.active?
    elsif user.active?
      user.make_activation_code
      user.save(validate: false)
    end
  end

  if payload['project_id'] && payload['institution_id']
    project = Project.find(payload['project_id'])
    institution = Institution.find(payload['institution_id'])
    person.add_to_project_and_institution(project, institution)
  end

  wg = person.current_work_groups.first
  project_id = wg&.project_id
  institution_id = wg&.institution_id

  puts({
    ok: true,
    user_id: user.id,
    person_id: person.id,
    login: user.login,
    email: person.email,
    active: user.active?,
    project_id: project_id,
    institution_id: institution_id
  }.to_json)
rescue => e
  puts({ ok: false, error: e.message, class: e.class.name }.to_json)
  exit 1
end
"""


class IsDjangoSuperuser(BasePermission):
    message = "Django superuser privileges are required."

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        return bool(user and user.is_authenticated and user.is_superuser)


def _validate_seek_user_id(uid_or_id: str) -> Optional[str]:
    s = str(uid_or_id)
    return s if s.isdigit() else None


def _membership_for_person(person_id: int) -> Tuple[Optional[int], Optional[int]]:
    db = settings.SEEK_DATABASE
    with connections[db].cursor() as cursor:
        cursor.execute(
            """
            SELECT wg.project_id, wg.institution_id
            FROM group_memberships gm
            INNER JOIN work_groups wg ON gm.work_group_id = wg.id
            WHERE gm.person_id = %s AND (gm.has_left IS NULL OR gm.has_left = 0)
            ORDER BY gm.id DESC
            LIMIT 1
            """,
            [person_id],
        )
        row = cursor.fetchone()
    if not row:
        return None, None
    return row[0], row[1]


def _seek_user_active(seek_user: Users) -> bool:
    return seek_user.activation_code in (None, "")


def _django_flags(login: str) -> Tuple[bool, bool]:
    try:
        dj = DjangoUser.objects.get(username__exact=login)
    except DjangoUser.DoesNotExist:
        return False, False
    return bool(dj.is_active), bool(dj.is_superuser)


def _build_record(
    seek_user: Users,
    *,
    project_id: Optional[int] = None,
    institution_id: Optional[int] = None,
) -> UserAdminRecord:
    person_id = seek_user.person_id
    person = People.objects.using(settings.SEEK_DATABASE).filter(id=person_id).first()
    if person is None:
        raise LookupError(f"Person {person_id} not found for user {seek_user.id}")

    if project_id is None or institution_id is None:
        project_id, institution_id = _membership_for_person(person_id)

    django_active, django_super = _django_flags(seek_user.login)

    return UserAdminRecord(
        user_id=int(seek_user.id),
        person_id=int(person_id),
        login=seek_user.login,
        email=person.email or "",
        first_name=person.first_name or "",
        last_name=person.last_name or "",
        active=_seek_user_active(seek_user),
        django_is_active=django_active,
        django_is_superuser=django_super,
        project_id=project_id,
        institution_id=institution_id,
    )


def _sync_django_user(
    *,
    login: str,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    is_active: bool,
    is_superuser: bool,
) -> None:
    dj, created = DjangoUser.objects.get_or_create(username=login, defaults={"email": email})
    dj.email = email
    dj.first_name = first_name
    dj.last_name = last_name
    dj.is_staff = True
    dj.is_active = is_active
    dj.is_superuser = is_superuser
    dj.set_password(password)
    dj.save()


def _error_response(title: str, status: int, detail: Optional[str] = None) -> HttpResponse:
    body: Dict[str, Any] = {"errors": [{"title": title}]}
    if detail:
        body["errors"][0]["detail"] = detail
    import json

    return HttpResponse(json.dumps(body), status=status, content_type="application/json")


class UsersViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated, IsDjangoSuperuser]
    lookup_field = "uid"
    lookup_url_kwarg = "uid"
    lookup_value_regex = r"[^/]+"

    @extend_schema(
        operation_id="List Users (admin)",
        description=USER_LIST_DESC,
        responses={200: UserListResponse},
        tags=["Users (admin)"],
    )
    def list(self, request):
        records: List[UserAdminRecord] = []
        for seek_user in Users.objects.using(settings.SEEK_DATABASE).all().order_by("id"):
            if not seek_user.person_id:
                continue
            try:
                records.append(_build_record(seek_user))
            except LookupError:
                logger.warning("Skipping SEEK user %s: linked person missing", seek_user.id)
        payload = UserListResponse(data=records)
        import json

        return HttpResponse(
            json.dumps(payload.model_dump()),
            status=200,
            content_type="application/json",
        )

    @extend_schema(
        operation_id="Fetch User (admin)",
        description=USER_FETCH_DESC,
        parameters=[
            OpenApiParameter(
                name="uid",
                type=str,
                location=OpenApiParameter.PATH,
                description="SEEK user id (numeric)",
            )
        ],
        responses={200: UserSingleResponse},
        tags=["Users (admin)"],
    )
    def retrieve(self, request, uid=None):
        seek_id = _validate_seek_user_id(uid or "")
        if seek_id is None:
            return _error_response("User not found", 404)

        seek_user = Users.objects.using(settings.SEEK_DATABASE).filter(id=seek_id).first()
        if seek_user is None or not seek_user.person_id:
            return _error_response("User not found", 404)

        try:
            record = _build_record(seek_user)
        except LookupError:
            return _error_response("User not found", 404)

        import json

        return HttpResponse(
            json.dumps(UserSingleResponse(data=record).model_dump()),
            status=200,
            content_type="application/json",
        )

    @extend_schema(
        operation_id="Create User (admin)",
        description=USER_CREATE_DESC,
        request=UserCreateRequest,
        responses={201: UserSingleResponse},
        tags=["Users (admin)"],
        examples=[
            OpenApiExample(
                name="Create project member login",
                value={
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
                },
            )
        ],
    )
    def create(self, request):
        try:
            body = UserCreateRequest.model_validate(request.data)
        except ValidationError:
            return _error_response("Invalid request", 422)

        if body.password != body.password_confirmation:
            return _error_response("Invalid request", 422, detail="password confirmation mismatch")

        if len(body.password) < 10:
            return _error_response("Invalid request", 422, detail="password must be at least 10 characters")

        if body.is_superuser and not request.user.is_superuser:
            return _error_response("Forbidden", 403, detail="cannot grant is_superuser")

        if Users.objects.using(settings.SEEK_DATABASE).filter(login=body.login).exists():
            return _error_response("Conflict", 409, detail="login already exists")

        runner_payload = {
            "login": body.login,
            "password": body.password,
            "password_confirmation": body.password_confirmation,
            "email": body.email,
            "first_name": body.first_name,
            "last_name": body.last_name,
            "project_id": body.project_id,
            "institution_id": body.institution_id,
            "activate": body.activate,
        }

        try:
            result = run_seek_rails_runner(SEEK_CREATE_RUBY, runner_payload)
        except SeekRailsUnavailableError as exc:
            return _error_response("SEEK rails runner unavailable", 503, detail=str(exc))
        except SeekRailsRunnerError as exc:
            detail = exc.detail or str(exc)
            if "taken" in detail.lower() or "already" in detail.lower():
                return _error_response("Conflict", 409, detail=detail)
            return _error_response("SEEK user creation failed", 502, detail=detail)

        person_id = int(result["person_id"])
        if not People.objects.using(settings.SEEK_DATABASE).filter(id=person_id).exists():
            return _error_response(
                "Person mirror missing after SEEK create",
                502,
                detail=f"person_id={person_id}",
            )

        _sync_django_user(
            login=body.login,
            email=body.email,
            password=body.password,
            first_name=body.first_name,
            last_name=body.last_name,
            is_active=body.activate,
            is_superuser=body.is_superuser,
        )

        seek_user = Users.objects.using(settings.SEEK_DATABASE).get(id=int(result["user_id"]))
        record = _build_record(
            seek_user,
            project_id=int(result.get("project_id", body.project_id)),
            institution_id=int(result.get("institution_id", body.institution_id)),
        )
        import json

        return HttpResponse(
            json.dumps(UserSingleResponse(data=record).model_dump()),
            status=201,
            content_type="application/json",
        )

    @extend_schema(
        operation_id="Update User (admin)",
        description=USER_UPDATE_DESC,
        request=UserUpdateRequest,
        responses={200: UserSingleResponse},
        tags=["Users (admin)"],
    )
    def partial_update(self, request, uid=None):
        seek_id = _validate_seek_user_id(uid or "")
        if seek_id is None:
            return _error_response("User not found", 404)

        seek_user = Users.objects.using(settings.SEEK_DATABASE).filter(id=seek_id).first()
        if seek_user is None or not seek_user.person_id:
            return _error_response("User not found", 404)

        try:
            body = UserUpdateRequest.model_validate(request.data)
        except ValidationError:
            return _error_response("Invalid request", 422)

        if body.password is not None or body.password_confirmation is not None:
            if not body.password or not body.password_confirmation:
                return _error_response("Invalid request", 422, detail="password and password_confirmation required together")
            if body.password != body.password_confirmation:
                return _error_response("Invalid request", 422, detail="password confirmation mismatch")
            if len(body.password) < 10:
                return _error_response("Invalid request", 422, detail="password must be at least 10 characters")

        if body.is_superuser is True and not request.user.is_superuser:
            return _error_response("Forbidden", 403, detail="cannot grant is_superuser")

        if body.project_id is not None and body.institution_id is None:
            return _error_response("Invalid request", 422, detail="institution_id required when project_id is set")
        if body.institution_id is not None and body.project_id is None:
            return _error_response("Invalid request", 422, detail="project_id required when institution_id is set")

        runner_payload: Dict[str, Any] = {"user_id": int(seek_id)}
        for key in (
            "password",
            "password_confirmation",
            "email",
            "first_name",
            "last_name",
            "project_id",
            "institution_id",
            "active",
        ):
            val = getattr(body, key)
            if val is not None:
                runner_payload[key] = val

        if len([k for k in runner_payload if k != "user_id"]) == 0 and body.is_superuser is None:
            return _error_response("Invalid request", 422, detail="no updatable fields provided")

        if any(k in runner_payload for k in ("password", "email", "first_name", "last_name", "project_id", "institution_id", "active")):
            try:
                result = run_seek_rails_runner(SEEK_PATCH_RUBY, runner_payload)
            except SeekRailsUnavailableError as exc:
                return _error_response("SEEK rails runner unavailable", 503, detail=str(exc))
            except SeekRailsRunnerError as exc:
                return _error_response("SEEK user update failed", 502, detail=exc.detail or str(exc))
        else:
            result = {
                "user_id": int(seek_id),
                "person_id": seek_user.person_id,
                "login": seek_user.login,
            }

        person = People.objects.using(settings.SEEK_DATABASE).get(id=seek_user.person_id)
        django_user = DjangoUser.objects.filter(username__exact=seek_user.login).first()

        if django_user is None:
            django_user = DjangoUser(username=seek_user.login, email=person.email or "")
            django_user.is_staff = True

        if body.email:
            django_user.email = body.email
        if body.first_name:
            django_user.first_name = body.first_name
        if body.last_name:
            django_user.last_name = body.last_name
        if body.password:
            django_user.set_password(body.password)
        if body.active is not None:
            django_user.is_active = body.active
        if body.is_superuser is not None:
            django_user.is_superuser = body.is_superuser

        django_user.save()

        seek_user.refresh_from_db(using=settings.SEEK_DATABASE)
        record = _build_record(
            seek_user,
            project_id=result.get("project_id"),
            institution_id=result.get("institution_id"),
        )
        import json

        return HttpResponse(
            json.dumps(UserSingleResponse(data=record).model_dump()),
            status=200,
            content_type="application/json",
        )

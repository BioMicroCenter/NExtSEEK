from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Literal

import orjson
from django.conf import settings
from django.db import connections
from rest_framework.authentication import BaseAuthentication, BasicAuthentication, TokenAuthentication
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.permissions import BasePermission

from nextseek_api.helpers import SeekAPIClient
from nextseek_api.services.assistant import CsrfExemptSessionAuthentication as SeekSessionAuthentication

ADMIN_ROLE_TYPE_ID = 1


@dataclass(frozen=True, slots=True)
class AuthenticatedSeekPerson:
    person_id: int
    django_user_id: int
    login: str
    scheme: Literal["basic", "session", "token"]

    def to_json(self) -> dict[str, int | str]:
        """Canonical non-secret actor provenance consumed unchanged by T03/T05/T08/T09."""
        return {
            "person_id": self.person_id,
            "django_user_id": self.django_user_id,
            "login": self.login,
            "scheme": self.scheme,
        }


def _scheme(authenticator) -> Literal["basic", "session", "token"]:
    if isinstance(authenticator, BasicAuthentication):
        return "basic"
    if isinstance(authenticator, TokenAuthentication):
        return "token"
    if isinstance(authenticator, SeekSessionAuthentication):
        return "session"
    raise AuthenticationFailed("Unsupported authentication mechanism.")


@dataclass(frozen=True, slots=True)
class SelectedSeekCredential:
    scheme: Literal["basic", "session", "token"]
    authorization: str | None = None
    username: str | None = None
    password: str | None = None

    def proof_request(self):
        """A minimal request-like object containing only this selected credential."""
        meta = {"HTTP_AUTHORIZATION": self.authorization} if self.authorization else {}
        session = (
            {"server": settings.SEEK_URL, "username": self.username, "password": self.password}
            if self.scheme == "session"
            else {}
        )
        return SimpleNamespace(META=meta, COOKIES={}, session=session, method="GET")


def _reject_competing_sources(request, selected_scheme: str) -> None:
    authorization = request.META.get("HTTP_AUTHORIZATION", "")
    header_scheme = authorization.partition(" ")[0].lower() if authorization else None
    sources = set()
    if header_scheme in {"basic", "token"}:
        sources.add(header_scheme)
    if request.COOKIES.get(settings.SESSION_COOKIE_NAME):
        sources.add("session")
    if request.META.get("HTTP_X_SEEK_AUTHORIZATION"):
        sources.add("x-seek")
    if sources - {selected_scheme}:
        raise AuthenticationFailed("Conflicting authentication credentials.")


def _selected_credential(request, authenticator, local_auth) -> SelectedSeekCredential:
    scheme = _scheme(authenticator)
    _reject_competing_sources(request, scheme)
    if scheme == "token":
        key = getattr(local_auth, "key", None)
        if not isinstance(key, str) or not key:
            raise AuthenticationFailed("Selected local token is unavailable.")
        # TokenAuthentication has already proved this key against NExtSEEK's local
        # DRF token table. The bytes are secret and are never forwarded to SEEK.
        return SelectedSeekCredential("token")
    if scheme == "basic":
        value = request.META.get("HTTP_AUTHORIZATION")
        if not isinstance(value, str) or not value.startswith("Basic "):
            raise AuthenticationFailed("Selected Basic credential is unavailable.")
        return SelectedSeekCredential("basic", authorization=value)
    username, password = request.session.get("username"), request.session.get("password")
    if not isinstance(username, str) or not isinstance(password, str) or not username or not password:
        raise AuthenticationFailed("Selected session SEEK bridge is unavailable.")
    return SelectedSeekCredential("session", username=username, password=password)


def _assert_local_seek_binding(user, person_id: int) -> None:
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(
            "SELECT people.id, users.login FROM people JOIN users ON users.person_id = people.id "
            "WHERE users.login = %s LIMIT 2",
            [str(user.get_username())],
        )
        rows = cursor.fetchall()
    # Compare element-wise rather than against a list literal: some DB drivers return
    # fetchall() as a tuple of rows rather than a list, and a bare `!=` against `[...]`
    # would then always be True even for the single correct, unique row.
    if len(rows) != 1 or int(rows[0][0]) != person_id or str(rows[0][1]) != str(user.get_username()):
        raise AuthenticationFailed("Selected local identity does not match proven SEEK person.")


def _prove_seek_person(selected: SelectedSeekCredential, user) -> AuthenticatedSeekPerson:
    if selected.scheme == "token":
        with connections[settings.SEEK_DATABASE].cursor() as cursor:
            cursor.execute(
                "SELECT people.id, users.login FROM people JOIN users ON users.person_id = people.id "
                "WHERE users.login = %s LIMIT 2",
                [str(user.get_username())],
            )
            rows = cursor.fetchall()
        if len(rows) != 1 or rows[0][1] != str(user.get_username()) or int(rows[0][0]) <= 0:
            raise AuthenticationFailed("Selected local token has no unique SEEK person binding.")
        person_id = int(rows[0][0])
    else:
        try:
            body, status_code, _headers, _response = SeekAPIClient().get_current_person(selected.proof_request())
            if status_code != 200:
                raise AuthenticationFailed("SEEK rejected the supplied credentials.")
            payload = orjson.loads(body)
            data = payload.get("data")
            if not isinstance(data, dict) or data.get("type") != "people":
                raise AuthenticationFailed("SEEK returned no unambiguous current person.")
            person_id = int(data["id"])
            if person_id <= 0:
                raise AuthenticationFailed("SEEK returned an invalid current person.")
        except AuthenticationFailed:
            raise
        except (KeyError, TypeError, ValueError, orjson.JSONDecodeError) as exc:
            raise AuthenticationFailed("SEEK returned no unambiguous current person.") from exc
        _assert_local_seek_binding(user, person_id)
    identity = AuthenticatedSeekPerson(
        person_id=person_id,
        django_user_id=int(user.pk),
        login=str(user.get_username()),
        scheme=selected.scheme,
    )
    return identity


class SeekPersonAuthentication(BaseAuthentication):
    """Authenticate locally, then bind the selected scheme to one SEEK person."""

    authenticators = (TokenAuthentication, SeekSessionAuthentication, BasicAuthentication)

    def authenticate(self, request):
        for authenticator_type in self.authenticators:
            authenticator = authenticator_type()
            result = authenticator.authenticate(request)
            if result is None:
                continue
            user, django_auth = result
            selected = _selected_credential(request, authenticator, django_auth)
            identity = _prove_seek_person(selected, user)
            request.user, request.auth = user, identity
            request._attribute_seek_identity = identity
            return user, identity
        return None

    def authenticate_header(self, request) -> str:
        return 'Basic realm="SEEK"'


def authenticate_seek_person(request) -> AuthenticatedSeekPerson:
    identity = getattr(request, "auth", None)
    if not isinstance(identity, AuthenticatedSeekPerson):
        identity = getattr(request, "_attribute_seek_identity", None)
    if not isinstance(identity, AuthenticatedSeekPerson):
        raise AuthenticationFailed("Valid SEEK authentication is required.")
    return identity


def _query_admin_role(person_id: int) -> bool:
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(
            "SELECT EXISTS(SELECT 1 FROM roles WHERE person_id = %s AND role_type_id = %s)",
            [person_id, ADMIN_ROLE_TYPE_ID],
        )
        row = cursor.fetchone()
    if not row:
        return False
    return int(row[0]) == 1


def is_seek_admin(request) -> bool:
    cached = getattr(request, "_attribute_seek_admin", None)
    if cached is not None:
        return bool(cached)
    identity = authenticate_seek_person(request)
    allowed = _query_admin_role(identity.person_id)
    request._attribute_seek_admin = allowed
    return allowed


def can_view_job(request) -> bool:
    return is_seek_admin(request)


def can_cancel_job(request, creator_seek_person_id: object) -> bool:
    if not isinstance(creator_seek_person_id, int) or isinstance(creator_seek_person_id, bool) or creator_seek_person_id <= 0:
        return False
    if not is_seek_admin(request):
        return False
    return authenticate_seek_person(request).person_id == creator_seek_person_id


class SeekAuthenticated(BasePermission):
    message = "Valid SEEK authentication is required."

    def has_permission(self, request, view) -> bool:
        return isinstance(getattr(request, "auth", None), AuthenticatedSeekPerson)


class IsSeekAdmin(BasePermission):
    message = "SEEK system administrator role is required."

    def has_permission(self, request, view) -> bool:
        return isinstance(getattr(request, "auth", None), AuthenticatedSeekPerson) and is_seek_admin(request)


class CanCancelAttributeJob(BasePermission):
    message = "Only the creating SEEK administrator may cancel this job."

    def has_permission(self, request, view) -> bool:
        return isinstance(getattr(request, "auth", None), AuthenticatedSeekPerson) and is_seek_admin(request)

    def has_object_permission(self, request, view, obj) -> bool:
        return can_cancel_job(request, getattr(obj, "actor_seek_person_id", None))

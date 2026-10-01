"""The operator's SEEK session for the studies tool (tool spec 4.6, 7.3, 7.4).

No SEEK service credential exists, so the tool acts as the operator. The login name comes from ``--seek-login``; the
password from ``read_password`` only (``getpass`` at a terminal, or one line of stdin with ``--seek-password-stdin``),
never an argument, an environment variable, a journal field, a file or a log line. It lives in a ``SeekCredential``
whose ``repr`` is redacted and becomes, per call, the Basic-only request object that
``attributes.auth.SelectedSeekCredential.proof_request`` builds (no cookie, no session), which ``resolve_seek_auth``
takes first. ``SeekAPIClient._request`` logs method, path, status and size only.

``prove`` checks, before anything is written, that SEEK answers ``GET /people/current`` with one person, that the
Django user of that login is bound to that person, and that the user is a superuser (``IsSuperUser``'s predicate).

The session has its own ``SeekAPIClient`` (never the viewsets' shared one), with ``WRITE_TIMEOUT_S`` for writes. A
POST that raised (a timeout, a lost connection) is an unknown outcome: the caller resolves it by the MySQL lookups
here, never by retrying. A GET is retried once. 401, 403, 422 and 5xx raise ``SeekError``.
"""
from __future__ import annotations

import base64
import getpass
import logging
import sys
from types import SimpleNamespace

import orjson
import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connections
from rest_framework.exceptions import AuthenticationFailed

from nextseek_api.attributes.auth import SelectedSeekCredential, _assert_local_seek_binding
from nextseek_api.helpers import SeekAPIClient
from nextseek_api.permissions import IsSuperUser
from nextseek_api.studies.buckets import title_key

log = logging.getLogger(__name__)

READ_TIMEOUT_S = 20
WRITE_TIMEOUT_S = 120     # provisional: the tool spec's section 10, check 8
ADOPT_WAIT_S = 300        # provisional: the tool spec's section 10, check 8
ADOPT_POLL_S = 10
_NETWORK_ERRORS = (requests.Timeout, requests.ConnectionError)


class SeekError(Exception):
    def __init__(self, code: str, message: str, status: int | None = None):
        self.code, self.message, self.status = code, message, status
        super().__init__(f"{code}: {message}")


class SeekUnknownOutcome(Exception):
    """A write was sent and no answer came back: SEEK may or may not have made the object."""


class SeekRefused(Exception):
    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


class SeekCredential:
    __slots__ = ("login", "_password")

    def __init__(self, login: str, password: str):
        if not login or not password:
            raise SeekRefused("empty_credential", "a SEEK login name and a password are both needed")
        self.login = login
        self._password = password

    def __repr__(self) -> str:
        return f"SeekCredential(login={self.login!r}, password='<redacted>')"

    __str__ = __repr__

    def request(self):
        token = base64.b64encode(f"{self.login}:{self._password}".encode("utf-8")).decode("ascii")
        return SelectedSeekCredential("basic", authorization="Basic " + token).proof_request()


def read_password(*, from_stdin: bool, stdin=None, prompt=None) -> str:
    if from_stdin:
        line = (stdin or sys.stdin).readline()
        password = line[:-1] if line.endswith("\n") else line
        if password.endswith("\r"):
            password = password[:-1]
    else:
        password = (prompt or getpass.getpass)("SEEK password: ")
    if not password:
        raise SeekRefused("empty_password", "no password was given")
    return password


def _seek_rows(sql: str, params: list) -> list[tuple]:
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(sql, params)
        return list(cursor.fetchall())


def _body(content) -> dict:
    try:
        body = orjson.loads(content) if content else {}
    except orjson.JSONDecodeError:
        return {}
    return body if isinstance(body, dict) else {}


def _message(body: dict) -> str:
    errors = body.get("errors")
    if isinstance(errors, list) and errors:
        return "; ".join(str(e.get("detail") or e.get("title") or e) if isinstance(e, dict) else str(e)
                         for e in errors)[:1000]
    return ""


def _check(status: int, content, what: str) -> dict:
    if status in (200, 201):
        return _body(content)
    body = _body(content)
    if status == 401:
        raise SeekError("unauthorized", f"{what}: SEEK refused the login (401)", status)
    if status == 403:
        raise SeekError("forbidden", f"{what}: SEEK refused the operator's permission (403)", status)
    if status == 422:
        raise SeekError("rejected", f"{what}: SEEK rejected the payload: {_message(body)}", status)
    if status >= 500:
        raise SeekError("seek_error", f"{what}: SEEK answered {status}", status)
    raise SeekError("unexpected", f"{what}: SEEK answered {status}", status)


class SeekSession:
    def __init__(self, credential: SeekCredential, *, client_factory=SeekAPIClient,
                 write_timeout_s: float = WRITE_TIMEOUT_S, read_timeout_s: float = READ_TIMEOUT_S):
        self._credential = credential
        self._client = client_factory()
        self._write_timeout_s = write_timeout_s
        self._read_timeout_s = read_timeout_s
        self.person_id: int | None = None
        self.django_user_id: int | None = None

    @property
    def login(self) -> str:
        return self._credential.login

    def __repr__(self) -> str:
        return f"SeekSession(login={self.login!r}, person_id={self.person_id!r})"

    # --- plumbing ---
    def _read(self, call, what: str) -> dict:
        self._client.timeout_s = self._read_timeout_s
        for attempt in (1, 2):
            try:
                content, status, _headers, _resp = call(self._credential.request())
                return _check(status, content, what)
            except _NETWORK_ERRORS as exc:
                if attempt == 2:
                    raise SeekError("seek_unreachable", f"{what}: {type(exc).__name__}") from exc
                log.warning("studies: %s: %s, retrying once", what, type(exc).__name__)
        raise AssertionError("unreachable")

    def _write(self, call, what: str) -> dict:
        self._client.timeout_s = self._write_timeout_s
        try:
            content, status, _headers, _resp = call(self._credential.request())
        except _NETWORK_ERRORS as exc:
            raise SeekUnknownOutcome(f"{what}: {type(exc).__name__} after sending") from exc
        return _check(status, content, what)

    # --- the proof ---
    def prove(self) -> "SeekSession":
        self._client.timeout_s = self._read_timeout_s
        try:
            content, status, _headers, _resp = self._client.get_current_person(self._credential.request())
        except _NETWORK_ERRORS as exc:
            raise SeekRefused("seek_unreachable", f"GET /people/current: {type(exc).__name__}") from exc
        if status != 200:
            raise SeekRefused("login_rejected", f"SEEK answered {status} for the login {self.login!r}")
        data = _body(content).get("data")
        if not isinstance(data, dict) or data.get("type") != "people":
            raise SeekRefused("no_person", "SEEK returned no single current person")
        try:
            person_id = int(data["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise SeekRefused("no_person", "SEEK returned no single current person") from exc
        user = get_user_model().objects.filter(username=self.login).first()
        if user is None:
            raise SeekRefused("no_django_user", f"no NExtSEEK user has the login {self.login!r}")
        try:
            _assert_local_seek_binding(user, person_id)
        except AuthenticationFailed as exc:
            raise SeekRefused("person_not_bound", "the login's NExtSEEK user is not bound to that SEEK person") from exc
        if not IsSuperUser().has_permission(SimpleNamespace(user=user), None):
            raise SeekRefused("not_superuser", f"the login {self.login!r} is not a superuser")
        self.person_id, self.django_user_id = person_id, int(user.pk)
        return self

    # --- reads ---
    def get_study(self, study_id: int) -> dict:
        return self._read(lambda req: self._client.get_study(req, str(study_id)), f"GET /studies/{study_id}")

    def get_assay(self, assay_id: int) -> dict:
        return self._read(lambda req: self._client.get_assay(req, str(assay_id)), f"GET /assays/{assay_id}")

    # --- writes (writer WR-33) ---
    def create_study(self, payload: dict) -> int:
        body = self._write(lambda req: self._client.create_study(req, payload), "POST /studies")
        return int(body["data"]["id"])

    def create_assay(self, payload: dict) -> int:
        body = self._write(lambda req: self._client.create_assay(req, payload), "POST /assays")
        return int(body["data"]["id"])

    def delete_study(self, study_id: int) -> tuple[bool, int | None]:
        self._client.timeout_s = self._write_timeout_s
        try:
            _content, status, _headers, _resp = self._client.delete_study(self._credential.request(), str(study_id))
        except _NETWORK_ERRORS:
            return False, None
        return status in (200, 204), status

    def delete_assay(self, assay_id: int) -> tuple[bool, int | None]:
        self._client.timeout_s = self._write_timeout_s
        try:
            _content, status, _headers, _resp = self._client.delete_assay(self._credential.request(), str(assay_id))
        except _NETWORK_ERRORS:
            return False, None
        return status in (200, 204), status

    # --- MySQL lookups for adoption and rollback (titles compared in Python) ---
    def find_study(self, investigation_id: int, title: str) -> list[int]:
        rows = _seek_rows("SELECT id, title FROM studies WHERE investigation_id = %s ORDER BY id", [investigation_id])
        return [int(i) for i, t in rows if title_key(t) == title_key(title)]

    def find_assay(self, study_id: int, title: str) -> list[int]:
        rows = _seek_rows("SELECT id, title FROM assays WHERE study_id = %s ORDER BY id", [study_id])
        return [int(i) for i, t in rows if title_key(t) == title_key(title)]

    def study_assay_count(self, study_id: int) -> int:
        return int(_seek_rows("SELECT COUNT(*) FROM assays WHERE study_id = %s", [study_id])[0][0])

    def assay_link_count(self, assay_id: int) -> int:
        return int(_seek_rows("SELECT COUNT(*) FROM assay_assets WHERE assay_id = %s", [assay_id])[0][0])

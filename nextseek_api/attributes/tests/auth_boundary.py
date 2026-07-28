from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import requests
import pytest
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.middleware import SessionMiddleware
from django.db import connections
from django.test import override_settings
from django.urls import resolve
from rest_framework.authtoken.models import Token
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView
from rest_framework.response import Response

from nextseek_api.attributes.auth import SeekPersonAuthentication

SEEK_IMAGE = "sha256:8b5c12a005d8bc9fea51b0f2e03c06ab210b2348ab4ec09bffbfde74ac3499fc"
SEEK_VERSION = "1.15.1"
RUBY_SOURCES = (
    "app/models/user.rb",
    "lib/seek/roles/accessors.rb",
    "lib/seek/roles/target.rb",
    "app/models/role.rb",
    "app/models/role_type.rb",
)


@dataclass(frozen=True)
class BoundaryCredential:
    scheme: str
    case_id: str
    person_id: int | None
    headers: dict[str, str]
    cookies: dict[str, str]
    expected_identity: dict | None


class SeekAuthBoundary:
    def __init__(self, database, run_root: Path):
        self.database = database
        self.run_root = run_root
        self.base_url = os.environ["ATTRIBUTE_TEST_SEEK_URL"]
        self._boundary_path = Path(os.environ["ATTRIBUTE_TEST_RAILS_BOUNDARY"])
        self._seed_path = Path(os.environ["ATTRIBUTE_TEST_RAILS_SEED"])
        self._oracle_key = bytes.fromhex(
            Path(os.environ["ATTRIBUTE_TEST_ORACLE_VERIFY_KEY_FILE"]).read_text().strip()
        )
        boundary = json.loads(self._boundary_path.read_text())
        self._role_queries: dict[str, int] = {}
        self._role_query_args: dict[str, tuple[int, int]] = {}
        self._current_person_calls: dict[str, int] = {}
        self._last_role_query_alias: str | None = None
        self._credentials: dict[tuple[str, str], BoundaryCredential] = {}
        self.observed_image_id = boundary["image_id"]
        if self.observed_image_id != SEEK_IMAGE:
            raise RuntimeError("pinned SEEK image ID drift")
        self.observed_seek_version = boundary["seek_version"]
        self.observed_source_hashes = boundary["source_hashes"]
        if self.observed_seek_version != SEEK_VERSION:
            raise RuntimeError(f"SEEK version drift: {self.observed_seek_version}")
        if (boundary["database_uuid"] != database.database_uuid
                or boundary["server_uuid"] != database.server_identity["server_uuid"]):
            raise RuntimeError("Rails boundary identity does not match disposable database")

    def install_and_start(self):
        seed_payload = json.loads(self._seed_path.read_text())
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if requests.get(f"{self.base_url}/people/current", timeout=1).status_code in {401, 403}:
                    self._load_credentials(seed_payload)
                    return
            except requests.RequestException:
                pass
            time.sleep(.1)
        raise TimeoutError("SEEK Rails boundary did not become ready")

    def _load_credentials(self, seed_payload):
        User = get_user_model()
        with connections[self.database.django_alias].cursor() as cursor:
            cursor.execute("SELECT people.id, users.login FROM people JOIN users ON users.person_id=people.id")
            person_ids = {login: person_id for person_id, login in cursor.fetchall()}
        for login, person_id in person_ids.items():
            password = seed_payload["passwords"][login]
            user, _ = User.objects.update_or_create(username=login, defaults={"is_active": True})
            if login == "django-superuser-decoy-role":
                user.is_staff = True; user.is_superuser = True
            user.set_password(password)
            user.save(update_fields=["password", "is_staff", "is_superuser"])
            identity = {"person_id": person_id, "django_user_id": user.pk, "login": login}
            basic = "Basic " + base64.b64encode(f"{login}:{password}".encode()).decode()
            self._credentials[("basic", login)] = BoundaryCredential(
                "basic", login, person_id, {"HTTP_AUTHORIZATION": basic}, {}, {**identity, "scheme": "basic"},
            )
            Token.objects.filter(user=user).delete()
            local_key = hashlib.sha256(f"local-drf-token:{login}".encode()).hexdigest()[:40]
            if local_key == seed_payload["tokens"][login]:
                raise RuntimeError("independent local and SEEK token namespaces collided")
            token = Token.objects.create(user=user, key=local_key)
            self._credentials[("token", login)] = BoundaryCredential(
                "token", login, person_id, {"HTTP_AUTHORIZATION": f"Token {token.key}"}, {},
                {**identity, "scheme": "token"},
            )
            session = SessionStore(); session["_auth_user_id"] = str(user.pk)
            session["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
            session["_auth_user_hash"] = user.get_session_auth_hash()
            session["server"] = self.base_url; session["username"] = login; session["password"] = password
            session.save()
            self._credentials[("session", login)] = BoundaryCredential(
                "session", login, person_id, {}, {settings.SESSION_COOKIE_NAME: session.session_key},
                {**identity, "scheme": "session"},
            )
        self._credentials[("basic", "wrong-password")] = BoundaryCredential(
            "basic", "wrong-password", None,
            {"HTTP_AUTHORIZATION": "Basic " + base64.b64encode(b"valid-admin:wrong").decode()}, {}, None,
        )
        forged_user = User.objects.create_user(username="forged-token-local", password="unused")
        Token.objects.filter(user=forged_user).delete()
        Token.objects.create(user=forged_user, key="f" * 40)
        for case_id, value in (("forged-token", "f" * 40), ("revoked-token", "e" * 40)):
            if case_id == "revoked-token":
                revoked_user = User.objects.get(username="revoked-user")
                Token.objects.filter(user=revoked_user).delete()
                revoked_local = Token.objects.create(user=revoked_user, key=value)
                revoked_local.delete()
            self._credentials[("token", case_id)] = BoundaryCredential(
                "token", case_id, None, {"HTTP_AUTHORIZATION": f"Token {value}"}, {}, None,
            )
        self._credentials[("session", "revoked-session")] = BoundaryCredential(
            "session", "revoked-session", None, {}, {settings.SESSION_COOKIE_NAME: "revoked"}, None,
        )
        self._credentials[("session", "invalid-session")] = BoundaryCredential(
            "session", "invalid-session", None, {}, {settings.SESSION_COOKIE_NAME: "invalid"}, None,
        )
        self._build_conflicting_credentials()

    def _build_conflicting_credentials(self):
        admin_basic = self._credentials[("basic", "valid-admin")]
        ordinary_session = self._credentials[("session", "ordinary-user")]
        self._credentials[("mixed", "basic-session-different-person")] = BoundaryCredential(
            "mixed", "basic-session-different-person", None,
            dict(admin_basic.headers), dict(ordinary_session.cookies), None,
        )
        admin_token = self._credentials[("token", "valid-admin")]
        self._credentials[("mixed", "token-session-different-person")] = BoundaryCredential(
            "mixed", "token-session-different-person", None,
            dict(admin_token.headers), dict(ordinary_session.cookies), None,
        )
        self._credentials[("mixed", "authorization-x-seek-conflict")] = BoundaryCredential(
            "mixed", "authorization-x-seek-conflict", None,
            dict(admin_basic.headers) | {"HTTP_X_SEEK_AUTHORIZATION": admin_basic.headers["HTTP_AUTHORIZATION"]},
            {}, None,
        )
        invalid_basic = self._credentials[("basic", "wrong-password")]
        self._credentials[("mixed", "valid-plus-invalid-extra")] = BoundaryCredential(
            "mixed", "valid-plus-invalid-extra", None,
            dict(admin_basic.headers) | {"HTTP_X_EXTRA_AUTHORIZATION": invalid_basic.headers["HTTP_AUTHORIZATION"]},
            dict(ordinary_session.cookies), None,
        )
        mismatch_session = self._credentials[("session", "valid-admin")]
        ordinary = self._credentials[("session", "ordinary-user")]
        store = SessionStore()
        store["_auth_user_id"] = str(get_user_model().objects.get(username="valid-admin").pk)
        store["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
        store["server"] = self.base_url
        store["username"] = "ordinary-user"
        store["password"] = json.loads(self._seed_path.read_text())["passwords"]["ordinary-user"]
        store.save()
        self._credentials[("binding", "local-person-mismatch")] = BoundaryCredential(
            "binding", "local-person-mismatch", None, {},
            {settings.SESSION_COOKIE_NAME: store.session_key}, None,
        )

    def conflicting_credential(self, case_id):
        return self._credentials[("mixed", case_id)]

    def dispatch_binding_case(self, case_id):
        credential = self._credentials[("binding", case_id)]
        from nextseek_api.attributes.auth import IsSeekAdmin
        return self.dispatch_drf(credential, permission=IsSeekAdmin)

    def current_person_call_count(self, case_id):
        return self._current_person_calls.get(case_id, 0)

    def last_role_query_alias(self):
        return self._last_role_query_alias

    def dispatch_neighbor_session_parity(self, operation):
        from nextseek_api.attributes.auth import SeekAuthenticated
        from nextseek_api.services.assistant import CsrfExemptSessionAuthentication
        credential = self.credential("session", "valid-admin")
        class NeighborProbe(APIView):
            authentication_classes = (CsrfExemptSessionAuthentication,)
            permission_classes = (SeekAuthenticated,)
            def get(self, request):
                return Response({"authentication_class": "CsrfExemptSessionAuthentication"})
        return self._dispatch(credential, NeighborProbe.as_view())

    def run_rails_predicate_oracle(self):
        boundary = json.loads(self._boundary_path.read_text())
        rails_payload = boundary["oracle"]
        envelope = {
            "image_id": self.observed_image_id,
            "seek_version": self.observed_seek_version,
            "source_hashes": self.observed_source_hashes,
            "server_uuid": self.database.server_identity["server_uuid"],
            "database_uuid": self.database.database_uuid,
            "rows": rails_payload["rows"],
            "input_row_ids": rails_payload["input_row_ids"],
            "signature": rails_payload["signature"],
        }
        if not self.verify_oracle_signature(envelope):
            raise RuntimeError("Rails oracle signature mismatch")
        return envelope

    def verify_oracle_signature(self, envelope):
        signed = json.dumps({
            "input_row_ids": envelope["input_row_ids"], "rows": envelope["rows"],
        }, separators=(",", ":"), sort_keys=True).encode()
        expected = hmac.new(self._oracle_key, signed, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, str(envelope["signature"]))

    def dispatch_admin_for_oracle_row(self, person_id):
        credential = next(
            item for (scheme, _case), item in self._credentials.items()
            if scheme == "token" and item.person_id == person_id
        )
        from nextseek_api.attributes.auth import IsSeekAdmin
        response = self.dispatch_drf(credential, permission=IsSeekAdmin)
        query_person_id, role_type_id = self._role_query_args[credential.case_id]
        return {
            "person_id": person_id,
            "is_admin": response.status_code == 200,
            "role_query_person_id": query_person_id,
            "role_type_id": role_type_id,
        }

    def credential(self, scheme, case_id):
        return self._credentials[(scheme, case_id)]

    def case(self, case_id):
        for scheme in ("token", "basic", "session"):
            if (scheme, case_id) in self._credentials:
                return self._credentials[(scheme, case_id)]
        raise KeyError(case_id)

    def _dispatch_request(self, credential, view, request):
        from unittest.mock import patch
        from nextseek_api.helpers import SeekAPIClient
        connection = connections[self.database.django_alias]
        case_key = credential.case_id
        self._role_queries[case_key] = 0
        self._current_person_calls[case_key] = 0
        self._last_role_query_alias = None
        original_get_current = SeekAPIClient.get_current_person
        boundary = self

        def counted_get_current(self_client, proof_request):
            boundary._current_person_calls[case_key] += 1
            return original_get_current(self_client, proof_request)

        def observe(execute, sql, params, many, context):
            if " from roles " in f" {sql.lower()} ":
                self._role_queries[case_key] += 1
                if len(params) != 2:
                    raise AssertionError("role query must have constant two-parameter shape")
                self._role_query_args[case_key] = (int(params[0]), int(params[1]))
                self._last_role_query_alias = self.database.django_alias
            return execute(sql, params, many, context)
        for key, value in credential.cookies.items():
            request.COOKIES[key] = value
        if credential.scheme in {"session", "mixed", "binding"} and credential.cookies:
            SessionMiddleware(lambda _request: None).process_request(request)
            AuthenticationMiddleware(lambda _request: None).process_request(request)
        with override_settings(SEEK_URL=self.base_url), connection.execute_wrapper(observe), patch.object(
            SeekAPIClient, "get_current_person", counted_get_current
        ):
            return view(request)

    def _dispatch(self, credential, view):
        request = APIRequestFactory().get("/attribute-auth-probe", **credential.headers)
        return self._dispatch_request(credential, view, request)

    def dispatch_product(self, credential, method, path, body=None):
        factory = APIRequestFactory()
        request = getattr(factory, method.lower())(path, body or {}, format="json", **credential.headers)
        return self._dispatch_request(credential, resolve(path).func, request)

    def dispatch_product_route(self, credential, method, path, body=None):
        return self.dispatch_product(credential, method, path, body)

    def observed_authentication_class(self, path="/nextseek_api/attributes/"):
        callback = resolve(path).func
        return callback.cls.authentication_classes

    def proven_person_id(self, credential):
        from nextseek_api.attributes.auth import SeekAuthenticated
        response = self.dispatch_drf(credential, permission=SeekAuthenticated)
        if response.status_code != 200:
            raise RuntimeError("credential did not prove a SEEK person")
        return int(response.data["person_id"])

    def dispatch_drf(self, credential, *, permission):
        class Probe(APIView):
            authentication_classes = (SeekPersonAuthentication,)
            permission_classes = (permission,)
            def get(self, request):
                return Response(request.auth.to_json())
        return self._dispatch(credential, Probe.as_view())

    def dispatch_cancel(self, credential, *, creator_person_id):
        from nextseek_api.attributes.auth import CanCancelAttributeJob
        class CancelProbe(APIView):
            authentication_classes = (SeekPersonAuthentication,)
            permission_classes = (CanCancelAttributeJob,)
            def get_object(inner_self):
                obj = SimpleNamespace(actor_seek_person_id=creator_person_id)
                inner_self.check_object_permissions(inner_self.request, obj)
                return obj
            def get(inner_self, request):
                inner_self.get_object()
                return Response(status=200)
        return self._dispatch(credential, CancelProbe.as_view())

    def write_checksums(self):
        return {
            table: self.database.checksum_query(f"SELECT * FROM {table} ORDER BY id")
            for table in ("users", "people", "roles", "api_tokens")
        }

    def role_query_count(self, case):
        return self._role_queries.get(getattr(case, "case_id", case), 0)

    def decoy_flags(self, credential):
        User = get_user_model()
        user = User.objects.get(username=credential.case_id)
        with connections[self.database.django_alias].cursor() as cursor:
            cursor.execute("SELECT roles_mask FROM people WHERE id=%s", [credential.person_id])
            roles_mask = cursor.fetchone()[0]
        truth = self.run_rails_predicate_oracle()
        rails_row = next(row for row in truth["rows"] if row["person_id"] == credential.person_id)
        return {"django_is_superuser": user.is_superuser, "django_is_staff": user.is_staff,
                "roles_mask": roles_mask, "rails_is_admin": rails_row["is_admin"]}

    def close(self):
        observed = self.database.query("SELECT @@server_uuid")[0][0]
        if str(observed) != self.database.server_identity["server_uuid"]:
            raise RuntimeError("Rails boundary detached from disposable database identity")


@pytest.fixture
def seek_auth_boundary(disposable_attribute_db):
    boundary = SeekAuthBoundary(
        disposable_attribute_db, Path(os.environ["ATTRIBUTE_EVIDENCE_RUN_ROOT"]),
    )
    boundary.install_and_start()
    try:
        yield boundary
    finally:
        boundary.close()

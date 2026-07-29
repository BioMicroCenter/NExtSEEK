import json

import pytest
from nextseek_api.attributes.auth import IsSeekAdmin, SeekAuthenticated, SeekPersonAuthentication

pytestmark = pytest.mark.django_db

@pytest.mark.parametrize("case_id", [
    pytest.param("ordinary-user", id="ordinary"),
    pytest.param("project-admin", id="project-role"),
    pytest.param("programme-admin", id="programme-role"),
    pytest.param("system-admin", id="system-admin"),
    pytest.param("django-superuser-decoy-role", id="decoy-flags-mask"),
    pytest.param("revoked-user", id="missing-role"),
    pytest.param("ambiguous-role-user", id="ambiguous-role"),
])
def test_seek_admin_parity_matrix(seek_auth_boundary, case_id):
    truth = seek_auth_boundary.run_rails_predicate_oracle()
    # image/version/source facts are measured by Python with docker inspect/sha256sum;
    # the Rails process is authoritative only for the predicate rows it computed.
    assert truth["image_id"] == seek_auth_boundary.observed_image_id
    assert truth["seek_version"] == seek_auth_boundary.observed_seek_version
    assert truth["source_hashes"] == seek_auth_boundary.observed_source_hashes
    assert truth["server_uuid"] == seek_auth_boundary.database.server_identity["server_uuid"]
    assert truth["database_uuid"] == seek_auth_boundary.database.database_uuid
    assert seek_auth_boundary.verify_oracle_signature(truth)
    credential = seek_auth_boundary.credential("token", case_id)
    row = next(item for item in truth["rows"] if item["person_id"] == credential.person_id)
    observed = seek_auth_boundary.dispatch_admin_for_oracle_row(row["person_id"])
    assert observed["person_id"] == row["person_id"]
    assert observed["is_admin"] is row["is_admin"]
    assert observed["role_query_person_id"] == row["person_id"]
    assert observed["role_type_id"] == 1


def test_django_flags_and_roles_mask_never_grant(seek_auth_boundary):
    case = seek_auth_boundary.case("django-superuser-decoy-role")
    flags = seek_auth_boundary.decoy_flags(case)
    assert flags["django_is_superuser"] is True
    assert flags["django_is_staff"] is True
    assert flags["roles_mask"] not in (None, 0)
    assert flags["rails_is_admin"] is False
    response = seek_auth_boundary.dispatch_drf(case, permission=IsSeekAdmin)
    assert response.status_code == 403
    assert seek_auth_boundary.role_query_count(case) == 1


@pytest.mark.parametrize("scheme,credential_case", [
    pytest.param("basic", "wrong-password", id="basic-wrong-password"),
    pytest.param("session", "invalid-session", id="session-invalid"),
    pytest.param("session", "revoked-session", id="session-revoked"),
    pytest.param("token", "forged-token", id="token-forged"),
    pytest.param("token", "revoked-token", id="token-revoked"),
])
def test_invalid_credentials_are_401_without_role_query(seek_auth_boundary, scheme, credential_case):
    before = seek_auth_boundary.write_checksums()
    response = seek_auth_boundary.dispatch_drf(
        seek_auth_boundary.credential(scheme, credential_case), permission=IsSeekAdmin,
    )
    assert response.status_code == 401
    assert "WWW-Authenticate" in response
    assert seek_auth_boundary.role_query_count(credential_case) == 0
    assert seek_auth_boundary.write_checksums() == before
    # The same named M-AUTH-01 killer must traverse authorization after a valid
    # Rails credential. Invalid credentials prove the 401/zero-query boundary;
    # this ordinary user proves that an early-True is_seek_admin mutant changes
    # the observable result from 403 to 200.
    nonadmin = seek_auth_boundary.credential("basic", "ordinary-user")
    nonadmin_before = seek_auth_boundary.write_checksums()
    denied = seek_auth_boundary.dispatch_drf(nonadmin, permission=IsSeekAdmin)
    assert denied.status_code == 403
    assert seek_auth_boundary.role_query_count(nonadmin) == 1
    assert seek_auth_boundary.write_checksums() == nonadmin_before


def test_any_admin_reads_creator_cancels(seek_auth_boundary):
    admin = seek_auth_boundary.credential("token", "creator-admin")
    assert seek_auth_boundary.dispatch_drf(admin, permission=IsSeekAdmin).status_code == 200
    assert seek_auth_boundary.dispatch_cancel(admin, creator_person_id=admin.person_id).status_code == 200


def test_noncreator_admin_and_nonadmin_cannot_cancel(seek_auth_boundary):
    creator_id = seek_auth_boundary.case("creator-admin").person_id
    assert seek_auth_boundary.dispatch_cancel(
        seek_auth_boundary.credential("basic", "other-admin"), creator_person_id=creator_id,
    ).status_code == 403
    assert seek_auth_boundary.dispatch_cancel(
        seek_auth_boundary.credential("session", "ordinary-user"), creator_person_id=creator_id,
    ).status_code == 403


@pytest.mark.parametrize("scheme", [
    pytest.param("basic", id="basic"),
    pytest.param("session", id="session"),
    pytest.param("token", id="token"),
])
def test_real_supported_credentials_prove_person_before_permissions(seek_auth_boundary, scheme):
    credential = seek_auth_boundary.credential(scheme, "valid-admin")
    response = seek_auth_boundary.dispatch_drf(credential, permission=IsSeekAdmin)
    assert response.status_code == 200
    assert response.data == credential.expected_identity


@pytest.mark.parametrize("case_id", [
    pytest.param("basic-session-different-person", id="basic-session-different-person"),
    pytest.param("token-session-different-person", id="token-session-different-person"),
    pytest.param("authorization-x-seek-conflict", id="authorization-x-seek-conflict"),
    pytest.param("valid-plus-invalid-extra", id="valid-plus-invalid-extra"),
])
def test_mixed_credentials_fail_before_seek_or_role(seek_auth_boundary, case_id):
    credential = seek_auth_boundary.conflicting_credential(case_id)
    before = seek_auth_boundary.write_checksums()
    response = seek_auth_boundary.dispatch_drf(credential, permission=IsSeekAdmin)
    assert response.status_code == 401
    assert seek_auth_boundary.current_person_call_count(case_id) == 0
    assert seek_auth_boundary.role_query_count(case_id) == 0
    assert seek_auth_boundary.write_checksums() == before


@pytest.mark.parametrize("case_id", [
    pytest.param("local-person-mismatch", id="local-person-mismatch"),
])
def test_selected_identity_binding(seek_auth_boundary, case_id):
    before = seek_auth_boundary.write_checksums()
    response = seek_auth_boundary.dispatch_binding_case(case_id)
    assert response.status_code == 401
    assert seek_auth_boundary.current_person_call_count(case_id) == 1
    assert seek_auth_boundary.role_query_count(case_id) == 0
    assert seek_auth_boundary.write_checksums() == before


@pytest.mark.parametrize("field", [
    pytest.param("person_id", id="person-id"),
    pytest.param("django_user_id", id="django-user-id"),
    pytest.param("login", id="login"),
    pytest.param("scheme", id="scheme"),
])
def test_actor_provenance_is_one_identity(seek_auth_boundary, field):
    credential = seek_auth_boundary.credential("token", "valid-admin")
    response = seek_auth_boundary.dispatch_drf(credential, permission=SeekAuthenticated)
    assert response.status_code == 200
    assert response.data[field] == credential.expected_identity[field]


@pytest.mark.parametrize("operation", [
    pytest.param("safe-read", id="safe-read"),
    pytest.param("admin-mutation", id="admin-mutation"),
])
def test_csrf_exempt_session_route_parity(seek_auth_boundary, operation):
    import sys

    from nextseek_api.services.assistant import CsrfExemptSessionAuthentication
    from rest_framework.response import Response
    from rest_framework.views import APIView
    from rest_framework.test import APIRequestFactory

    auth_module = sys.modules["nextseek_api.attributes.auth"]
    session_auth = auth_module.SeekPersonAuthentication.authenticators[1]
    assert session_auth is CsrfExemptSessionAuthentication

    response = seek_auth_boundary.dispatch_neighbor_session_parity(operation)
    assert response.status_code == 200
    assert response.data["authentication_class"] == "CsrfExemptSessionAuthentication"
    credential = seek_auth_boundary.credential("session", "valid-admin")
    factory = APIRequestFactory()

    class SeekSessionProbe(APIView):
        authentication_classes = (SeekPersonAuthentication,)
        permission_classes = (SeekAuthenticated,)

        def post(self, request):
            return Response({"scheme": request.auth.scheme})

    request = factory.post("/attribute-auth-probe", **credential.headers)
    for key, value in credential.cookies.items():
        request.COOKIES[key] = value
    seek_response = seek_auth_boundary._dispatch_request(
        credential, SeekSessionProbe.as_view(), request,
    )
    assert seek_response.status_code == 200
    assert seek_response.data["scheme"] == "session"

def test_wrong_basic_password_and_forged_tokens_are_401(seek_auth_boundary):
    for scheme, case in (("basic", "wrong-password"), ("token", "forged-token")):
        before = seek_auth_boundary.write_checksums()
        response = seek_auth_boundary.dispatch_drf(seek_auth_boundary.credential(scheme, case), permission=IsSeekAdmin)
        assert response.status_code == 401
        assert seek_auth_boundary.role_query_count(case) == 0
        assert seek_auth_boundary.write_checksums() == before


def test_mixed_valid_and_invalid_credentials_reject_before_seek_or_role_query(seek_auth_boundary):
    test_mixed_credentials_fail_before_seek_or_role(seek_auth_boundary, "valid-plus-invalid-extra")


def test_basic_and_session_for_different_people_are_rejected_without_reselection(seek_auth_boundary):
    test_mixed_credentials_fail_before_seek_or_role(seek_auth_boundary, "basic-session-different-person")


def test_authorization_and_x_seek_authorization_conflict_is_401_before_role_query(seek_auth_boundary):
    test_mixed_credentials_fail_before_seek_or_role(seek_auth_boundary, "authorization-x-seek-conflict")


def test_local_identity_and_seek_person_mismatch_is_401_and_zero_writes(seek_auth_boundary):
    test_selected_identity_binding(seek_auth_boundary, "local-person-mismatch")


def test_project_and_programme_roles_never_grant_system_admin(seek_auth_boundary):
    for case_id in ("project-admin", "programme-admin"):
        credential = seek_auth_boundary.credential("token", case_id)
        response = seek_auth_boundary.dispatch_drf(credential, permission=IsSeekAdmin)
        assert response.status_code == 403
        assert seek_auth_boundary.role_query_count(case_id) == 1


def test_admin_role_query_uses_only_the_disposable_seek_alias(seek_auth_boundary):
    credential = seek_auth_boundary.credential("token", "system-admin")
    seek_auth_boundary.dispatch_drf(credential, permission=IsSeekAdmin)
    assert seek_auth_boundary.last_role_query_alias() == seek_auth_boundary.database.django_alias


def test_selected_identity_cache_is_request_local_and_never_cross_request(seek_auth_boundary):
    from nextseek_api.attributes.auth import SeekPersonAuthentication

    credential = seek_auth_boundary.credential("token", "system-admin")
    first = seek_auth_boundary.dispatch_drf(credential, permission=IsSeekAdmin)
    second = seek_auth_boundary.dispatch_drf(credential, permission=IsSeekAdmin)
    assert first.status_code == 200 and second.status_code == 200
    assert seek_auth_boundary.role_query_count(credential) == 2
    assert getattr(SeekPersonAuthentication, "_attribute_seek_identity", None) is None


def test_rails_oracle_tamper_is_rejected_before_parity_comparison(seek_auth_boundary):
    import sys

    auth_boundary_module = sys.modules["nextseek_api.attributes.tests.auth_boundary"]
    boundary = json.loads(seek_auth_boundary._boundary_path.read_text())
    oracle = boundary["oracle"]
    tampered = {
        "input_row_ids": oracle["input_row_ids"],
        "rows": oracle["rows"],
        "signature": "tampered",
        "oracle_verified": True,
    }
    assert auth_boundary_module.SeekAuthBoundary.verify_oracle_signature(
        seek_auth_boundary,
        tampered,
    ) is False


def test_csrf_exempt_session_matches_neighboring_api_for_read_and_admin_mutation(seek_auth_boundary):
    for operation in ("safe-read", "admin-mutation"):
        test_csrf_exempt_session_route_parity(seek_auth_boundary, operation)


def test_auth_helper_entrypoints_and_invalid_creator_guard(seek_auth_boundary):
    from rest_framework.exceptions import AuthenticationFailed
    from rest_framework.response import Response
    from rest_framework.views import APIView
    from types import SimpleNamespace

    from nextseek_api.attributes.auth import (
        SeekPersonAuthentication,
        authenticate_seek_person,
        can_cancel_job,
        can_view_job,
    )

    with pytest.raises(AuthenticationFailed):
        authenticate_seek_person(SimpleNamespace(auth=None, _attribute_seek_identity=None))

    admin = seek_auth_boundary.credential("token", "system-admin")

    class HelperProbe(APIView):
        authentication_classes = (SeekPersonAuthentication,)
        permission_classes = ()

        def get(self, request):
            identity = authenticate_seek_person(request)
            return Response({
                "can_view": can_view_job(request),
                "cancel_self": can_cancel_job(request, identity.person_id),
                "cancel_invalid": can_cancel_job(request, 0),
            })

    response = seek_auth_boundary._dispatch(admin, HelperProbe.as_view())
    assert response.status_code == 200
    assert response.data["can_view"] is True
    assert response.data["cancel_self"] is True
    assert response.data["cancel_invalid"] is False


def test_unsupported_authenticator_scheme_is_rejected():
    from rest_framework.authentication import BaseAuthentication
    from rest_framework.exceptions import AuthenticationFailed

    from nextseek_api.attributes import auth as auth_module

    class WeirdAuth(BaseAuthentication):
        pass

    with pytest.raises(AuthenticationFailed, match="Unsupported authentication mechanism"):
        auth_module._scheme(WeirdAuth())


def test_selected_token_without_key_is_rejected():
    from types import SimpleNamespace

    from rest_framework.authentication import TokenAuthentication
    from rest_framework.exceptions import AuthenticationFailed

    from nextseek_api.attributes import auth as auth_module

    request = SimpleNamespace(META={"HTTP_AUTHORIZATION": "Token abc"}, COOKIES={}, session={})
    with pytest.raises(AuthenticationFailed, match="Selected local token is unavailable"):
        auth_module._selected_credential(request, TokenAuthentication(), SimpleNamespace(key=""))


def test_selected_basic_without_prefix_is_rejected():
    from types import SimpleNamespace

    from rest_framework.authentication import BasicAuthentication
    from rest_framework.exceptions import AuthenticationFailed

    from nextseek_api.attributes import auth as auth_module

    request = SimpleNamespace(META={"HTTP_AUTHORIZATION": "Bearer x"}, COOKIES={}, session={})
    with pytest.raises(AuthenticationFailed, match="Selected Basic credential is unavailable"):
        auth_module._selected_credential(request, BasicAuthentication(), None)


@pytest.mark.parametrize(
    "status_code,body",
    [
        pytest.param(401, b"{}", id="seek-rejects-credentials"),
        pytest.param(200, b"not-json", id="seek-body-not-json"),
        pytest.param(200, b'{"data": {"type": "projects", "id": "1"}}', id="seek-wrong-type"),
        pytest.param(200, b'{"data": {"type": "people", "id": "0"}}', id="seek-invalid-person-id"),
        pytest.param(200, b'{"data": {"type": "people", "id": "not-int"}}', id="seek-unparseable-person-id"),
    ],
)
def test_seek_current_person_failures_are_rejected(seek_auth_boundary, status_code, body):
    from unittest.mock import patch

    from django.contrib.auth import get_user_model
    from rest_framework.exceptions import AuthenticationFailed

    from nextseek_api.attributes import auth as auth_module
    from nextseek_api.helpers import SeekAPIClient

    user = get_user_model().objects.get(username="system-admin")
    selected = auth_module.SelectedSeekCredential("basic", authorization="Basic x")
    with patch.object(SeekAPIClient, "get_current_person", return_value=(body, status_code, {}, None)):
        with pytest.raises(AuthenticationFailed):
            auth_module._prove_seek_person(selected, user)


def test_query_admin_role_empty_fetchone_returns_false(seek_auth_boundary):
    from unittest.mock import MagicMock, patch

    from django.conf import settings

    from nextseek_api.attributes import auth as auth_module

    cursor = MagicMock()
    cursor.fetchone.return_value = None
    context = MagicMock()
    context.__enter__.return_value = cursor
    with patch.object(auth_module.connections[settings.SEEK_DATABASE], "cursor", return_value=context):
        assert auth_module._query_admin_role(1) is False


def test_nonadmin_cannot_cancel_valid_creator_job(seek_auth_boundary):
    from rest_framework.response import Response
    from rest_framework.views import APIView

    from nextseek_api.attributes.auth import SeekPersonAuthentication, can_cancel_job

    ordinary = seek_auth_boundary.credential("token", "ordinary-user")
    creator_id = seek_auth_boundary.case("creator-admin").person_id

    class CancelProbe(APIView):
        authentication_classes = (SeekPersonAuthentication,)
        permission_classes = ()

        def get(self, request):
            return Response({"allowed": can_cancel_job(request, creator_id)})

    response = seek_auth_boundary._dispatch(ordinary, CancelProbe.as_view())
    assert response.status_code == 200
    assert response.data["allowed"] is False

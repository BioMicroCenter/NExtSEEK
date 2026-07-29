import pytest
from nextseek_api.attributes.auth import IsSeekAdmin, SeekAuthenticated

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
    response = seek_auth_boundary.dispatch_neighbor_session_parity(operation)
    assert response.status_code == 200
    assert response.data["authentication_class"] == "CsrfExemptSessionAuthentication"

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
    credential = seek_auth_boundary.credential("token", "system-admin")
    first = seek_auth_boundary.dispatch_drf(credential, permission=IsSeekAdmin)
    second = seek_auth_boundary.dispatch_drf(credential, permission=IsSeekAdmin)
    assert first.status_code == 200 and second.status_code == 200
    assert seek_auth_boundary.role_query_count(credential) == 2


def test_rails_oracle_tamper_is_rejected_before_parity_comparison(seek_auth_boundary):
    truth = seek_auth_boundary.run_rails_predicate_oracle()
    truth["signature"] = "tampered"
    assert seek_auth_boundary.verify_oracle_signature(truth) is False


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

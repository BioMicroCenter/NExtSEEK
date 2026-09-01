"""Endpoint behaviour: auth, gating, status codes, schema."""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from rest_framework.test import APIClient

URL = "/nextseek_api/assay-registrations/"
BODY = {"registrations": [{"sample_uid": "D.NHP-240115MIT-001", "assay_id": 351}]}


@pytest.fixture
def superuser(db):
    return User.objects.create_user("admin", password="x",
                                    is_staff=True, is_superuser=True)


@pytest.fixture
def staff_only(db):
    """SEEK mirrors every user with is_staff=True (dmac/views.py:80,97), so a
    staff user is the realistic non-admin caller, not an exotic one."""
    return User.objects.create_user("lab", password="x",
                                    is_staff=True, is_superuser=False)


@pytest.mark.django_db
class TestAuth:
    def test_unauthenticated_is_401(self):
        assert APIClient().post(URL, BODY, format="json").status_code == 401

    def test_authenticated_non_superuser_is_403(self, staff_only):
        client = APIClient()
        client.force_authenticate(user=staff_only)
        assert client.post(URL, BODY, format="json").status_code == 403


@pytest.mark.django_db
class TestRequestValidation:
    def test_a_malformed_body_is_422(self, superuser):
        client = APIClient()
        client.force_authenticate(user=superuser)
        response = client.post(
            URL, {"registrations": [{"sample_uid": "X"}]}, format="json")
        assert response.status_code == 422
        assert response.json()["errors"][0]["code"] == "request_validation_error"

    def test_an_unknown_field_is_422(self, superuser):
        client = APIClient()
        client.force_authenticate(user=superuser)
        response = client.post(
            URL,
            {"registrations": [{"sample_uid": "X", "assay_id": 1}],
             "update_existing": True},
            format="json")
        assert response.status_code == 422


@pytest.mark.django_db
class TestOutcomes:
    def _run(self, superuser, execution_result, graph_edges=0, graph_error=None):
        client = APIClient()
        client.force_authenticate(user=superuser)
        with patch("nextseek_api.assay_registration.service.plan_batch") as plan, \
             patch("nextseek_api.assay_registration.service.execute",
                   return_value=execution_result), \
             patch("nextseek_api.assay_registration.service.get_connection"), \
             patch("nextseek_api.assay_registration.service._neo4j",
                   return_value=(MagicMock(), "neo4j")), \
             patch("nextseek_api.assay_registration.service.recompute_for_samples",
                   side_effect=graph_error or (lambda *a, **k: graph_edges)):
            plan.return_value = MagicMock(total_rows=1,
                                          execution_mode=lambda threshold: "synchronous")
            return client.post(URL, BODY, format="json")

    def test_all_written_is_200(self, superuser):
        from nextseek_api.assay_registration.executor import ExecutionResult
        from nextseek_api.assay_registration.schemas import RegistrationCounts, RowResult
        result = ExecutionResult(
            rows=[RowResult(index=0, sample_uid="A", status="written",
                            assay_assets_id=1)],
            counts=RegistrationCounts(submitted=1, written=1),
            written_sample_ids={100}, overall_status="succeeded")
        response = self._run(superuser, result)
        assert response.status_code == 200
        assert response.json()["counts"]["written"] == 1

    def test_a_mixed_batch_is_207(self, superuser):
        from nextseek_api.assay_registration.executor import ExecutionResult
        from nextseek_api.assay_registration.schemas import (
            RegistrationCounts, RowError, RowResult)
        result = ExecutionResult(
            rows=[
                RowResult(index=0, sample_uid="A", status="written", assay_assets_id=1),
                RowResult(index=1, sample_uid="DUP", status="skipped",
                          error=RowError(code="sample_uid_not_unique", message="2 rows")),
            ],
            counts=RegistrationCounts(submitted=2, written=1, skipped=1),
            written_sample_ids={100}, overall_status="partial")
        response = self._run(superuser, result)
        assert response.status_code == 207

    def test_nothing_executable_is_409(self, superuser):
        from nextseek_api.assay_registration.executor import ExecutionResult
        from nextseek_api.assay_registration.schemas import (
            RegistrationCounts, RowError, RowResult)
        result = ExecutionResult(
            rows=[RowResult(index=0, sample_uid="DUP", status="skipped",
                            error=RowError(code="sample_uid_not_unique", message="2"))],
            counts=RegistrationCounts(submitted=1, skipped=1),
            written_sample_ids=set(), overall_status="failed")
        response = self._run(superuser, result)
        assert response.status_code == 409

    def test_a_graph_failure_does_not_lose_the_mysql_write(self, superuser):
        """The graph is DERIVED from assay_assets, so a failed recompute is a
        stale view, not an inconsistency. Rolling back a correct MySQL write to
        satisfy it would be strictly worse. Re-POSTing the same batch repairs
        it: every row comes back already_present and the recompute re-runs.
        """
        from nextseek_api.assay_registration.executor import ExecutionResult
        from nextseek_api.assay_registration.schemas import RegistrationCounts, RowResult

        def boom(*args, **kwargs):
            raise RuntimeError("bolt connection refused")

        result = ExecutionResult(
            rows=[RowResult(index=0, sample_uid="A", status="written",
                            assay_assets_id=414936)],
            counts=RegistrationCounts(submitted=1, written=1),
            written_sample_ids={100}, overall_status="succeeded")
        response = self._run(superuser, result, graph_error=boom)

        body = response.json()
        assert response.status_code == 200
        assert body["rows"][0]["status"] == "written"
        assert body["rows"][0]["assay_assets_id"] == 414936
        assert body["graph"]["status"] == "failed"
        assert "bolt" in body["graph"]["error"]


@pytest.mark.django_db
class TestSchema:
    def test_the_endpoint_and_its_models_appear_in_the_openapi_schema(self):
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        paths = schema.get("paths", {})
        assert URL in paths
        post = paths[URL]["post"]
        assert post["requestBody"]["content"]["application/json"]["examples"]
        assert "RegistrationRequest" in schema["components"]["schemas"]
        assert "RegistrationResponse" in schema["components"]["schemas"]

    def test_no_delete_method_is_exposed(self):
        from drf_spectacular.generators import SchemaGenerator

        schema = SchemaGenerator().get_schema(request=None, public=True)
        for path, operations in schema.get("paths", {}).items():
            if "assay-registrations" in path:
                assert "delete" not in operations, (
                    "the endpoint must expose no deleting method")


# ---------------------------------------------------------------------------
# Everything below is additional to the task brief's file. The brief covers
# `create` only; the two job actions, the dry-run mode, the 202 path and the
# transaction ordering the graph-failure case actually depends on were untested.
# ---------------------------------------------------------------------------

import uuid
from contextlib import contextmanager
from unittest.mock import patch as _patch
from pydantic import ValidationError as PydanticValidationError

from nextseek_api.assay_registration import jobs
from nextseek_api.assay_registration.executor import ExecutionResult
from nextseek_api.assay_registration.models_db import AssayRegistrationJob
from nextseek_api.assay_registration.schemas import (
    RegistrationCounts,
    RegistrationRequest,
    RowResult,
)

JOBS_URL = "/nextseek_api/assay-registrations/jobs/"


def _client(user):
    client = APIClient()
    client.force_authenticate(user=user)
    return client


@pytest.mark.django_db
class TestTransactionOrdering:
    """The graph-failure case only holds if the MySQL transaction closed first.

    `test_a_graph_failure_does_not_lose_the_mysql_write` asserts the response
    body, which it would also do if the recompute ran INSIDE the `with
    get_connection()` block -- there the raised RuntimeError would roll the
    write back, and the response would still say "written", because the rows
    are built from the read-back the executor already returned. The body proves
    what we reported; only the ordering proves what we kept.
    """

    def test_the_recompute_runs_after_the_write_transaction_closes(self, superuser):
        events = []

        @contextmanager
        def connection():
            events.append("conn_enter")
            try:
                yield MagicMock()
            finally:
                events.append("conn_exit")

        result = ExecutionResult(
            rows=[RowResult(index=0, sample_uid="A", status="written",
                            assay_assets_id=414936)],
            counts=RegistrationCounts(submitted=1, written=1),
            written_sample_ids={100}, overall_status="succeeded")

        def _execute(*args, **kwargs):
            events.append("execute")
            return result

        def _recompute(*args, **kwargs):
            events.append("recompute")
            raise RuntimeError("bolt connection refused")

        with _patch("nextseek_api.assay_registration.service.plan_batch") as plan, \
             _patch("nextseek_api.assay_registration.service.execute",
                    side_effect=_execute), \
             _patch("nextseek_api.assay_registration.service.get_connection",
                    side_effect=connection), \
             _patch("nextseek_api.assay_registration.service._neo4j",
                    return_value=(MagicMock(), "neo4j")), \
             _patch("nextseek_api.assay_registration.service.recompute_for_samples",
                    side_effect=_recompute):
            plan.return_value = MagicMock(
                total_rows=1, execution_mode=lambda threshold: "synchronous")
            response = _client(superuser).post(URL, BODY, format="json")

        assert events == ["conn_enter", "execute", "conn_exit", "recompute"], events
        assert response.status_code == 200
        assert response.json()["graph"]["status"] == "failed"

    def test_the_driver_is_closed_even_when_the_recompute_raises(self, superuser):
        driver = MagicMock()
        result = ExecutionResult(
            rows=[RowResult(index=0, sample_uid="A", status="written",
                            assay_assets_id=1)],
            counts=RegistrationCounts(submitted=1, written=1),
            written_sample_ids={100}, overall_status="succeeded")
        with _patch("nextseek_api.assay_registration.service.plan_batch") as plan, \
             _patch("nextseek_api.assay_registration.service.execute",
                    return_value=result), \
             _patch("nextseek_api.assay_registration.service.get_connection"), \
             _patch("nextseek_api.assay_registration.service._neo4j",
                    return_value=(driver, "neo4j")), \
             _patch("nextseek_api.assay_registration.service.recompute_for_samples",
                    side_effect=RuntimeError("boom")):
            plan.return_value = MagicMock(
                total_rows=1, execution_mode=lambda threshold: "synchronous")
            _client(superuser).post(URL, BODY, format="json")
        driver.close.assert_called_once()

    def test_no_written_samples_skips_the_graph_entirely(self, superuser):
        result = ExecutionResult(
            rows=[RowResult(index=0, sample_uid="A", status="already_present",
                            assay_assets_id=219104)],
            counts=RegistrationCounts(submitted=1, already_present=1),
            written_sample_ids=set(), overall_status="succeeded")
        with _patch("nextseek_api.assay_registration.service.plan_batch") as plan, \
             _patch("nextseek_api.assay_registration.service.execute",
                    return_value=result), \
             _patch("nextseek_api.assay_registration.service.get_connection"), \
             _patch("nextseek_api.assay_registration.service._neo4j") as neo:
            plan.return_value = MagicMock(
                total_rows=1, execution_mode=lambda threshold: "synchronous")
            response = _client(superuser).post(URL, BODY, format="json")
        assert response.json()["graph"] == {"status": "skipped", "edges_recomputed": 0,
                                            "error": None}
        neo.assert_not_called()


@pytest.mark.django_db
class TestDryRun:
    def test_a_dry_run_reports_without_writing(self, superuser):
        preview_result = ExecutionResult(
            rows=[RowResult(index=0, sample_uid="A", status="written")],
            counts=RegistrationCounts(submitted=1, written=1),
            written_sample_ids=set(), overall_status="succeeded")
        with _patch("nextseek_api.assay_registration.service.plan_batch") as plan, \
             _patch("nextseek_api.assay_registration.service.preview",
                    return_value=preview_result), \
             _patch("nextseek_api.assay_registration.service.execute") as execute, \
             _patch("nextseek_api.assay_registration.service.get_connection"), \
             _patch("nextseek_api.assay_registration.service._neo4j") as neo:
            plan.return_value = MagicMock(
                total_rows=1, execution_mode=lambda threshold: "synchronous")
            body = dict(BODY, dry_run=True)
            response = _client(superuser).post(URL, body, format="json")
        assert response.status_code == 200
        assert response.json()["mode"] == "dry_run"
        assert response.json()["graph"]["status"] == "skipped"
        execute.assert_not_called()
        neo.assert_not_called()


@pytest.mark.django_db
class TestAsynchronousPath:
    """A batch over the threshold gets a durable job and a 202.

    NOTE, deliberately asserted: the job is created in state `accepted` with
    zero processed rows, and NOTHING in this branch claims or runs it. The 202
    is a promise no worker keeps yet. These tests pin the observable facts so
    the worker task can be written against them rather than around them.
    """

    def _post(self, superuser, total_rows=6000):
        counts = RegistrationCounts(submitted=total_rows)
        with _patch("nextseek_api.assay_registration.service.plan_batch") as plan, \
             _patch("nextseek_api.assay_registration.service.preview",
                    return_value=MagicMock(counts=counts)), \
             _patch("nextseek_api.assay_registration.service.execute") as execute, \
             _patch("nextseek_api.assay_registration.service.get_connection"):
            plan.return_value = MagicMock(
                total_rows=total_rows,
                execution_mode=lambda threshold: "asynchronous")
            response = _client(superuser).post(URL, BODY, format="json")
        execute.assert_not_called()
        return response

    def test_it_returns_202_with_a_resolvable_status_url(self, superuser):
        response = self._post(superuser)
        assert response.status_code == 202
        body = response.json()
        assert body["mode"] == "asynchronous"
        assert body["status_url"] == f"{JOBS_URL}{body['job_id']}/"
        assert body["counts"]["submitted"] == 6000

        follow_up = _client(superuser).get(body["status_url"])
        assert follow_up.status_code == 200
        assert follow_up.json()["job_id"] == body["job_id"]

    def test_the_job_is_durable_and_records_the_submitted_request(self, superuser):
        body = self._post(superuser).json()
        job = AssayRegistrationJob.objects.get(job_id=body["job_id"])
        assert job.total_rows == 6000
        assert job.actor_login == "admin"
        # The NORMALISED pydantic dump, not the raw body: every optional field is
        # present and explicit. A worker replaying this row gets a document that
        # round-trips through RegistrationRequest without re-guessing defaults.
        assert job.submitted_request == {
            "registrations": [{"sample_uid": "D.NHP-240115MIT-001",
                               "assay": None, "assay_id": 351}],
            "dry_run": False,
        }
        assert RegistrationRequest.model_validate(job.submitted_request)

    def test_nothing_has_claimed_or_started_the_job(self, superuser):
        """Honest, not aspirational: no worker exists on this branch yet."""
        body = self._post(superuser).json()
        job = AssayRegistrationJob.objects.get(job_id=body["job_id"])
        assert (job.state, job.processed_rows, job.claim_owner) == ("accepted", 0, None)
        assert _client(superuser).get(body["status_url"]).json()["state"] == "accepted"

    def test_the_threshold_comes_from_settings(self, superuser, settings):
        settings.ASSAY_REGISTRATION_SYNC_ROW_THRESHOLD = 17
        seen = {}
        with _patch("nextseek_api.assay_registration.service.plan_batch") as plan, \
             _patch("nextseek_api.assay_registration.service.preview",
                    return_value=MagicMock(counts=RegistrationCounts(submitted=1))), \
             _patch("nextseek_api.assay_registration.service.get_connection"):
            def mode(threshold):
                seen["threshold"] = threshold
                return "asynchronous"
            plan.return_value = MagicMock(total_rows=18, execution_mode=mode)
            _client(superuser).post(URL, BODY, format="json")
        assert seen["threshold"] == 17


@pytest.mark.django_db
class TestJobStatus:
    @pytest.fixture
    def job(self, superuser):
        return jobs.create_job({"registrations": [], "dry_run": False},
                               superuser, total_rows=3)

    def test_an_unclaimed_job_reports_its_state(self, superuser, job):
        response = _client(superuser).get(f"{JOBS_URL}{job.job_id}/")
        assert response.status_code == 200
        assert response.json() == {
            "job_id": str(job.job_id), "state": "accepted",
            "processed_rows": 0, "total_rows": 3, "result": None,
        }

    def test_a_terminal_job_carries_the_full_per_row_report(self, superuser, job):
        report = {
            "mode": "synchronous", "overall_status": "succeeded",
            "counts": {"submitted": 1, "written": 1, "already_present": 0,
                       "skipped": 0, "failed": 0},
            "rows": [{"index": 0, "sample_uid": "A", "status": "written",
                      "sample_id": 48213, "assay_id": 351, "assay_title": None,
                      "project_id": 3, "assay_assets_id": 414936, "error": None}],
            "graph": {"status": "succeeded", "edges_recomputed": 4, "error": None},
        }
        assert jobs.claim(job, "worker-1")
        assert jobs.finish(job, "worker-1", "succeeded", report)
        response = _client(superuser).get(f"{JOBS_URL}{job.job_id}/")
        assert response.status_code == 200
        assert response.json()["state"] == "succeeded"
        assert response.json()["result"]["rows"][0]["assay_assets_id"] == 414936

    def test_an_unknown_job_is_404(self, superuser):
        response = _client(superuser).get(f"{JOBS_URL}{uuid.uuid4()}/")
        assert response.status_code == 404
        assert response.json()["errors"][0]["code"] == "job_not_found"

    def test_a_malformed_job_id_is_404_not_500(self, superuser):
        """Django's UUIDField raises django.core.exceptions.ValidationError for an
        unparseable id -- NOT ValueError and NOT ObjectDoesNotExist. Catching only
        those two turns a typo in a URL into a 500."""
        response = _client(superuser).get(f"{JOBS_URL}not-a-uuid/")
        assert response.status_code == 404
        assert response.json()["errors"][0]["submitted_identifier"] == "not-a-uuid"

    def test_a_non_superuser_cannot_read_a_job(self, staff_only, superuser, job):
        assert _client(staff_only).get(f"{JOBS_URL}{job.job_id}/").status_code == 403


@pytest.mark.django_db
class TestJobCancel:
    @pytest.fixture
    def job(self, superuser):
        return jobs.create_job({"registrations": [], "dry_run": False},
                               superuser, total_rows=3)

    def test_cancelling_an_unclaimed_job_is_202_and_terminal(self, superuser, job):
        response = _client(superuser).post(f"{JOBS_URL}{job.job_id}/cancel/")
        assert response.status_code == 202
        assert response.json()["state"] == "cancelled"
        job.refresh_from_db()
        assert job.cancellation_actor_django_user_id == superuser.id

    def test_cancelling_a_running_job_only_requests_it(self, superuser, job):
        assert jobs.claim(job, "worker-1")
        response = _client(superuser).post(f"{JOBS_URL}{job.job_id}/cancel/")
        assert response.status_code == 202
        assert response.json()["state"] == "running"
        assert jobs.is_cancelled(job)

    def test_cancelling_twice_is_409(self, superuser, job):
        assert _client(superuser).post(
            f"{JOBS_URL}{job.job_id}/cancel/").status_code == 202
        second = _client(superuser).post(f"{JOBS_URL}{job.job_id}/cancel/")
        assert second.status_code == 409
        assert second.json()["errors"][0]["code"] == "not_cancellable"

    def test_cancelling_an_unknown_job_is_404(self, superuser):
        response = _client(superuser).post(f"{JOBS_URL}{uuid.uuid4()}/cancel/")
        assert response.status_code == 404
        assert response.json()["errors"][0]["code"] == "job_not_found"

    def test_a_non_superuser_cannot_cancel(self, staff_only, superuser, job):
        assert _client(staff_only).post(
            f"{JOBS_URL}{job.job_id}/cancel/").status_code == 403
        job.refresh_from_db()
        assert job.cancellation_requested_at is None


@pytest.mark.django_db
class TestAuthEnvelope:
    """401 and 403 are declared as ErrorResponse; they have to actually be one."""

    def test_the_401_body_is_the_documented_envelope(self):
        response = APIClient().post(URL, BODY, format="json")
        assert response.status_code == 401
        assert response.json()["errors"][0]["code"] == "authentication_failed"

    def test_the_401_carries_a_www_authenticate_challenge(self):
        """RFC 9110 15.5.2 makes the challenge mandatory on a 401, and DRF
        downgrades to 403 without one. Session auth offers none, so the value
        comes from BasicAuthentication -- a credential this endpoint accepts."""
        response = APIClient().post(URL, BODY, format="json")
        assert response.headers["WWW-Authenticate"].startswith("Basic")

    def test_the_403_body_is_the_documented_envelope(self, staff_only):
        response = _client(staff_only).post(URL, BODY, format="json")
        assert response.status_code == 403
        assert response.json()["errors"][0]["code"] == "permission_denied"


class TestConventions:
    """The validator has to actually look at this module.

    EXTEND_SCHEMA_SCAN_PATHS is an explicit tuple of files, not a tree walk, so
    a new ViewSet outside nextseek_api/services/ is invisible to the AST checks
    until it is listed. Running the validator and reading "OK" would otherwise
    say nothing at all about this endpoint.
    """

    def _validator(self):
        import importlib.util
        import sys
        from pathlib import Path

        path = Path(__file__).resolve().parents[3] / "scripts" / "validate_viewset_conventions.py"
        spec = importlib.util.spec_from_file_location("_vc_assayreg", path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    def test_this_viewset_module_is_scanned(self):
        assert "nextseek_api/assay_registration/views.py" in \
            self._validator().EXTEND_SCHEMA_SCAN_PATHS

    def test_this_module_contributes_no_violations(self):
        """Scoped on purpose. The repo-wide run is red for reasons that predate
        this endpoint (cc_assistant and project_export), so a repo-wide
        assertion here would fail for someone else's code and pass for none of
        ours."""
        vc = self._validator()
        from pathlib import Path

        root = Path(__file__).resolve().parents[3]
        mine = [v for v in vc.validate_repo(root)
                if "assay_registration" in v.location
                or "ASSAY_REGISTRATION" in v.location]
        assert mine == [], vc._format_violations(mine)

    def test_the_three_descriptions_are_well_formed(self):
        vc = self._validator()
        from nextseek_api import endpoint_descriptions as ed

        for name in ("ASSAY_REGISTRATION_CREATE_DESC",
                     "ASSAY_REGISTRATION_JOB_DESC",
                     "ASSAY_REGISTRATION_JOB_CANCEL_DESC"):
            assert vc.validate_desc_text(getattr(ed, name), location=name) == []

    def test_no_grandfather_entry_was_added_for_this_endpoint(self):
        vc = self._validator()
        assert not [e for e in vc.GRANDFATHER_OPS
                    if (e.rel_path or "").startswith("nextseek_api/assay_registration")
                    or any("Assay Registration" in op or "Assay Memberships" in op
                           for op in e.operation_ids)]


@pytest.mark.django_db
class TestResponseFidelity:
    """Fields the outcome tests above never look at.

    Each of these was found by mutation: `mode="synchronous"` could be changed
    to `"dry_run"`, and `edges_recomputed=written` to `edges_recomputed=0`,
    with the whole file still green. Both are on the API surface.
    """

    def _result(self):
        return ExecutionResult(
            rows=[RowResult(index=0, sample_uid="A", status="written",
                            assay_assets_id=414936)],
            counts=RegistrationCounts(submitted=1, written=1),
            written_sample_ids={100}, overall_status="succeeded")

    def test_a_real_write_is_labelled_synchronous_not_dry_run(self, superuser):
        response = TestOutcomes()._run(superuser, self._result())
        assert response.json()["mode"] == "synchronous"

    def test_the_recomputed_relationship_count_reaches_the_response(self, superuser):
        """A count, not a reconciliation figure: one edge pair can be carried by
        several DERIVED_FROM relationships, so this may legitimately exceed the
        number of rows written. It is reported verbatim, never compared."""
        response = TestOutcomes()._run(superuser, self._result(), graph_edges=128)
        assert response.json()["graph"] == {"status": "succeeded",
                                            "edges_recomputed": 128, "error": None}


class TestNeo4jWiring:
    """`_neo4j` is patched out of every other test in this file, so nothing else
    would notice it reading settings that do not exist. The task brief's draft
    read NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD / NEO4J_DATABASE as four flat
    settings; this project has exactly one, a dict."""

    def test_it_reads_the_one_settings_dict_the_rest_of_the_repo_reads(self, settings):
        import neo4j

        settings.NEO4J_DATABASE = {"NAME": "graph-db", "URI": "neo4j://example:7687",
                                   "AUTH": ("neo4j", "secret")}
        with _patch.object(neo4j.GraphDatabase, "driver") as driver:
            from nextseek_api.assay_registration.service import _neo4j

            made, db_name = _neo4j()
        driver.assert_called_once_with("neo4j://example:7687",
                                       auth=("neo4j", "secret"))
        assert made is driver.return_value
        assert db_name == "graph-db"


class TestThreshold:
    def test_the_default_matches_the_attribute_mutation_threshold(self, settings):
        """The setting's own comment says it mirrors the attributes threshold.
        Left unasserted, the default could become 0 -- sending every batch,
        including a two-row one, down a job path no worker services yet."""
        assert settings.ASSAY_REGISTRATION_SYNC_ROW_THRESHOLD == 5000
        assert (settings.ASSAY_REGISTRATION_SYNC_ROW_THRESHOLD
                == settings.ATTRIBUTE_MUTATION_AFFECTED_ROW_THRESHOLD)
        assert isinstance(settings.ASSAY_REGISTRATION_SYNC_ROW_THRESHOLD, int)


# ---------------------------------------------------------------------------
# Fix round 1.
# ---------------------------------------------------------------------------

#: Codes the ViewSet and service emit in an error ENVELOPE rather than on a row.
#: Everything else in ERROR_CODES describes a row outcome and therefore has to
#: appear in the published description, or a caller meets it with no way to look
#: it up.
ENVELOPE_ONLY_CODES = frozenset({
    "request_validation_error", "job_not_found", "not_cancellable",
    "authentication_failed", "permission_denied",
})


@pytest.mark.django_db
class TestTheAcceptedCountsPromiseNothing:
    """A 202 must not report rows as written that nothing has written.

    `preview(plan)` labels every row in `plan.to_write` "written", so
    `counts=preview(plan).counts` answered a 25,765-row POST with
    `{"written": 25700}` before a single row existed -- and, with no worker on
    this branch, before any ever would. That is the defect this endpoint was
    built to remove, reproduced on its own new path.
    """

    def _post(self, superuser, total_rows=6000):
        with _patch("nextseek_api.assay_registration.service.plan_batch") as plan, \
             _patch("nextseek_api.assay_registration.service.preview") as preview, \
             _patch("nextseek_api.assay_registration.service.execute") as execute, \
             _patch("nextseek_api.assay_registration.service.get_connection"):
            plan.return_value = MagicMock(
                total_rows=total_rows,
                execution_mode=lambda threshold: "asynchronous")
            response = _client(superuser).post(URL, BODY, format="json")
        execute.assert_not_called()
        return response, preview

    def test_only_submitted_is_populated(self, superuser):
        response, _ = self._post(superuser)
        assert response.status_code == 202
        assert response.json()["counts"] == {
            "submitted": 6000, "written": 0, "already_present": 0,
            "skipped": 0, "failed": 0,
        }

    def test_no_projection_is_computed_at_all(self, superuser):
        """Stronger than checking the numbers: the forecast is never built, so
        it cannot leak into the 202 by a later edit."""
        _, preview = self._post(superuser)
        preview.assert_not_called()


@pytest.mark.django_db
class TestAStoredResultThatWillNotValidate:
    def test_it_is_a_server_error_not_a_404(self, superuser):
        """`_JOB_LOOKUP_FAILURES` must not contain bare `ValueError`.

        pydantic's ValidationError SUBCLASSES ValueError, and `job_status` ends
        by validating the stored terminal report, so a bare ValueError in the
        tuple answers drift between a persisted result and the response model
        with 404 "Job not found" -- for a job that exists and has finished. A
        superuser polling a completed batch would conclude it was never created.
        Unparseable stored state is a server error; it should 500 loudly.

        The trigger here is real, not contrived: RowError enforces ERROR_CODES on
        READ as well as write, so a code retired from the set breaks the status
        read of every job whose stored report carries it.
        """
        job = jobs.create_job({"registrations": [], "dry_run": False},
                              superuser, total_rows=1)
        AssayRegistrationJob.objects.filter(pk=job.pk).update(
            state="succeeded",
            terminal_result={
                "mode": "synchronous", "overall_status": "failed",
                "counts": {"submitted": 1, "written": 0, "already_present": 0,
                           "skipped": 1, "failed": 0},
                "rows": [{"index": 0, "sample_uid": "A", "status": "skipped",
                          "error": {"code": "a_code_since_retired",
                                    "message": "was declared once"}}],
                "graph": {"status": "skipped"},
            })
        with pytest.raises(PydanticValidationError):
            _client(superuser).get(f"{JOBS_URL}{job.job_id}/")


class TestAuthenticationClassesAreGuarded:
    """`force_authenticate` bypasses the authenticators entirely and the
    conventions validator never inspects them, so the project-wide "never
    TokenAuthentication" rule had no standing guard on this ViewSet. Asserting
    the class lists is the cheapest form of one.
    """

    def test_the_authenticators_are_exactly_session_then_basic(self):
        from rest_framework.authentication import BasicAuthentication

        from nextseek_api.assay_registration.views import AssayRegistrationViewSet
        from nextseek_api.services.assistant import CsrfExemptSessionAuthentication

        assert AssayRegistrationViewSet.authentication_classes == [
            CsrfExemptSessionAuthentication, BasicAuthentication]

    def test_token_authentication_is_absent(self):
        """Stated as the rule rather than as a list equality, so it still holds
        if the list is ever legitimately extended. Token auth does not work in
        this project."""
        from rest_framework.authentication import TokenAuthentication

        from nextseek_api.assay_registration.views import AssayRegistrationViewSet

        assert not any(issubclass(cls, TokenAuthentication)
                       for cls in AssayRegistrationViewSet.authentication_classes)

    def test_the_gate_is_authenticated_then_superuser_not_is_admin_user(self):
        """IsAdminUser checks is_staff, which dmac/views.py:80,97 sets on every
        SEEK user at login, so it collapses to IsAuthenticated."""
        from rest_framework.permissions import IsAdminUser, IsAuthenticated

        from nextseek_api.assay_registration.views import AssayRegistrationViewSet
        from nextseek_api.permissions import IsSuperUser

        assert AssayRegistrationViewSet.permission_classes == [
            IsAuthenticated, IsSuperUser]
        assert IsAdminUser not in AssayRegistrationViewSet.permission_classes


class TestThePublishedErrorCodes:
    def test_every_row_level_code_is_documented(self):
        """Self-maintaining, unlike the one-time edit that added
        `write_not_confirmed_by_readback`: a new row code declared in
        ERROR_CODES and left out of the description fails here. That code in
        particular is the single most important thing a row can say -- the
        insert reported no error and the row was not there on read-back."""
        from nextseek_api.assay_registration.schemas import ERROR_CODES
        from nextseek_api.endpoint_descriptions import ASSAY_REGISTRATION_CREATE_DESC

        undocumented = sorted(
            code for code in ERROR_CODES - ENVELOPE_ONLY_CODES
            if f"`{code}`" not in ASSAY_REGISTRATION_CREATE_DESC)
        assert undocumented == []

    def test_the_envelope_only_split_still_covers_every_declared_code(self):
        """Guards the guard: if a code is neither documented nor listed as
        envelope-only, the test above would quietly stop checking it."""
        from nextseek_api.assay_registration.schemas import ERROR_CODES

        assert ENVELOPE_ONLY_CODES <= ERROR_CODES


class TestTheDescriptionDoesNotOverstate:
    def test_the_asynchronous_mode_is_published(self):
        """Nothing told a client the 202's numbers were a forecast, because
        RETURNS never mentioned the asynchronous mode at all."""
        from nextseek_api.endpoint_descriptions import ASSAY_REGISTRATION_CREATE_DESC

        for token in ("202", "status_url", "job_id"):
            assert token in ASSAY_REGISTRATION_CREATE_DESC

    def test_dry_run_is_not_called_an_identical_report(self):
        """It is the same SHAPE, not the identical report: a planned row carries
        no assay_assets_id because no database has assigned one, and the graph
        block is skipped."""
        from nextseek_api.endpoint_descriptions import ASSAY_REGISTRATION_CREATE_DESC

        assert "identical report" not in ASSAY_REGISTRATION_CREATE_DESC
        assert "assay_assets_id" in ASSAY_REGISTRATION_CREATE_DESC

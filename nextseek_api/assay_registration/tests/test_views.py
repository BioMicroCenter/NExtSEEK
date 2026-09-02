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
            recompute_sample_ids={100}, overall_status="succeeded")
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
            recompute_sample_ids={100}, overall_status="partial")
        response = self._run(superuser, result)
        assert response.status_code == 207

    def test_nothing_executable_is_409(self, superuser):
        """409 is the CALLER's case: nothing they submitted was executable.

        Every row here was skipped by the resolver -- an unknown uid, an
        ambiguous title -- which is what the spec means by "no executable rows
        at all". Contrast the read-back failure below, which is ours.
        """
        from nextseek_api.assay_registration.executor import ExecutionResult
        from nextseek_api.assay_registration.schemas import (
            RegistrationCounts, RowError, RowResult)
        result = ExecutionResult(
            rows=[RowResult(index=0, sample_uid="DUP", status="skipped",
                            error=RowError(code="sample_uid_not_unique", message="2"))],
            counts=RegistrationCounts(submitted=1, skipped=1),
            recompute_sample_ids=set(), overall_status="failed")
        response = self._run(superuser, result)
        assert response.status_code == 409

    def test_a_batch_lost_at_readback_is_500_not_409(self, superuser):
        """A server-side failure must not be attributed to the caller.

        Every row was executable and every insert vanished at read-back. The
        request was fine; the write was not. 409 told the caller "no executable
        rows at all" -- the one thing that demonstrably was not the problem --
        and invited them to go and fix a body that was already correct. The
        rows carry `write_not_confirmed_by_readback` either way, so this is
        about the status LINE agreeing with the body it carries.
        """
        from nextseek_api.assay_registration.executor import ExecutionResult
        from nextseek_api.assay_registration.schemas import (
            RegistrationCounts, RowError, RowResult)
        result = ExecutionResult(
            rows=[RowResult(index=0, sample_uid="A", status="failed",
                            sample_id=100, assay_id=351,
                            error=RowError(code="write_not_confirmed_by_readback",
                                           message="not in assay_assets"))],
            counts=RegistrationCounts(submitted=1, failed=1),
            recompute_sample_ids=set(), overall_status="failed")
        response = self._run(superuser, result)
        assert response.status_code == 500
        body = response.json()
        assert body["rows"][0]["error"]["code"] == "write_not_confirmed_by_readback"
        assert body["counts"]["failed"] == 1

    def test_a_partial_batch_carrying_a_readback_failure_stays_207(self, superuser):
        """The split is inside `failed` only. Rows DID write here, and 207's
        meaning -- read the per-row report -- is unchanged by one bad row."""
        from nextseek_api.assay_registration.executor import ExecutionResult
        from nextseek_api.assay_registration.schemas import (
            RegistrationCounts, RowError, RowResult)
        result = ExecutionResult(
            rows=[
                RowResult(index=0, sample_uid="A", status="written",
                          assay_assets_id=1),
                RowResult(index=1, sample_uid="B", status="failed",
                          error=RowError(code="write_not_confirmed_by_readback",
                                         message="not in assay_assets")),
            ],
            counts=RegistrationCounts(submitted=2, written=1, failed=1),
            recompute_sample_ids={100}, overall_status="partial")
        assert self._run(superuser, result).status_code == 207

    def test_a_graph_failure_does_not_lose_the_mysql_write(self, superuser):
        """The graph is DERIVED from assay_assets, so a failed recompute is a
        stale view, not an inconsistency. Rolling back a correct MySQL write to
        satisfy it would be strictly worse. Re-POSTing the same batch repairs
        it: every row comes back already_present and the recompute re-runs --
        which is true only because `recompute_sample_ids` is written UNION
        already_present. `TestTheRePostRepairPath` below pins that leg; without
        it this docstring described a recovery the code could not perform.
        """
        from nextseek_api.assay_registration.executor import ExecutionResult
        from nextseek_api.assay_registration.schemas import RegistrationCounts, RowResult

        def boom(*args, **kwargs):
            raise RuntimeError("bolt connection refused")

        result = ExecutionResult(
            rows=[RowResult(index=0, sample_uid="A", status="written",
                            assay_assets_id=414936)],
            counts=RegistrationCounts(submitted=1, written=1),
            recompute_sample_ids={100}, overall_status="succeeded")
        response = self._run(superuser, result, graph_error=boom)

        body = response.json()
        assert response.status_code == 200
        assert body["rows"][0]["status"] == "written"
        assert body["rows"][0]["assay_assets_id"] == 414936
        assert body["graph"]["status"] == "failed"
        assert "bolt" in body["graph"]["error"]

    def test_a_failed_graph_outcome_reports_no_edge_count(self, superuser):
        """The read pass itself failed, so there is no honest figure to give.

        `edges_recomputed` is 0 here because nothing was counted, not because
        zero edges were touched. The spec promised an "affected edge count" on
        this path; it was corrected rather than invented, and this pins that the
        error string is the whole of what a failed outcome says.
        """
        from nextseek_api.assay_registration.executor import ExecutionResult
        from nextseek_api.assay_registration.schemas import RegistrationCounts, RowResult

        def boom(*args, **kwargs):
            raise RuntimeError("bolt connection refused")

        result = ExecutionResult(
            rows=[RowResult(index=0, sample_uid="A", status="written",
                            assay_assets_id=1)],
            counts=RegistrationCounts(submitted=1, written=1),
            recompute_sample_ids={100}, overall_status="succeeded")
        graph = self._run(superuser, result, graph_error=boom).json()["graph"]
        assert graph == {"status": "failed", "edges_recomputed": 0,
                         "error": "bolt connection refused"}


@pytest.mark.django_db
class TestTheRePostRepairPath:
    """The published recovery instruction, driven end to end through the view.

    `service._recompute`'s docstring, the spec's Recovery section and the
    endpoint description all tell an operator with a stale graph to re-POST the
    identical batch. Every pair then answers `already_present`, so nothing is
    written -- and while the recompute was fed the WRITTEN-only set, that made
    `recompute_sample_ids` empty, short-circuited `_recompute` to
    `{"status": "skipped"}`, and repaired nothing while reporting that there was
    nothing to repair. An operator following the instruction would read
    `skipped` and conclude the graph was fine.

    These two tests fail against the pre-fix code: the first because the
    recompute is never called, the second because the graph block says skipped.
    """

    def _repost(self, superuser):
        from nextseek_api.assay_registration.executor import ExecutionResult
        from nextseek_api.assay_registration.schemas import RegistrationCounts, RowResult

        result = ExecutionResult(
            rows=[
                RowResult(index=0, sample_uid="A", status="already_present",
                          sample_id=100, assay_id=351, assay_assets_id=900),
                RowResult(index=1, sample_uid="B", status="already_present",
                          sample_id=200, assay_id=351, assay_assets_id=901),
            ],
            counts=RegistrationCounts(submitted=2, already_present=2),
            # written UNION already_present: nothing was written, both rows are
            # already there, and both still need their derived labels rebuilt.
            recompute_sample_ids={100, 200}, overall_status="succeeded")

        client = APIClient()
        client.force_authenticate(user=superuser)
        with patch("nextseek_api.assay_registration.service.plan_batch") as plan, \
             patch("nextseek_api.assay_registration.service.execute",
                   return_value=result), \
             patch("nextseek_api.assay_registration.service.get_connection"), \
             patch("nextseek_api.assay_registration.service._neo4j",
                   return_value=(MagicMock(), "neo4j")), \
             patch("nextseek_api.assay_registration.service.recompute_for_samples",
                   return_value=128) as recompute:
            plan.return_value = MagicMock(
                total_rows=2, execution_mode=lambda threshold: "synchronous")
            response = client.post(URL, BODY, format="json")
        return response, recompute

    def test_the_recompute_runs_on_the_already_present_sample_ids(self, superuser):
        response, recompute = self._repost(superuser)
        assert response.status_code == 200
        recompute.assert_called_once()
        assert recompute.call_args[0][0] == {100, 200}, (
            "the rows the caller asked about, not the rows this request "
            "happened to insert -- which was none of them"
        )

    def test_the_graph_block_reports_the_repair_rather_than_a_skip(self, superuser):
        response, _ = self._repost(superuser)
        assert response.json()["graph"] == {
            "status": "succeeded", "edges_recomputed": 128, "error": None}

    def test_a_batch_with_nothing_ok_at_all_still_skips(self, superuser):
        """`skipped` is not deleted, it is narrowed: it now means no row ended
        written or already_present, so no membership exists for a label to be
        derived from. Widening the input must not turn that into a pointless
        Neo4j round trip."""
        from nextseek_api.assay_registration.executor import ExecutionResult
        from nextseek_api.assay_registration.schemas import (
            RegistrationCounts, RowError, RowResult)

        result = ExecutionResult(
            rows=[RowResult(index=0, sample_uid="DUP", status="skipped",
                            error=RowError(code="sample_uid_not_found", message="m"))],
            counts=RegistrationCounts(submitted=1, skipped=1),
            recompute_sample_ids=set(), overall_status="failed")

        client = APIClient()
        client.force_authenticate(user=superuser)
        with patch("nextseek_api.assay_registration.service.plan_batch") as plan, \
             patch("nextseek_api.assay_registration.service.execute",
                   return_value=result), \
             patch("nextseek_api.assay_registration.service.get_connection"), \
             patch("nextseek_api.assay_registration.service._neo4j") as neo:
            plan.return_value = MagicMock(
                total_rows=1, execution_mode=lambda threshold: "synchronous")
            response = client.post(URL, BODY, format="json")

        neo.assert_not_called()
        assert response.json()["graph"]["status"] == "skipped"


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
            recompute_sample_ids={100}, overall_status="succeeded")

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
            recompute_sample_ids={100}, overall_status="succeeded")
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
            recompute_sample_ids=set(), overall_status="succeeded")
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
            recompute_sample_ids=set(), overall_status="succeeded")
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
    zero processed rows, and nothing on the REQUEST path claims or runs it. A
    worker exists now (`runner.py`, drained by the loop the app container runs
    as `manage.py run_assay_registration_jobs`), but it is a separate process
    reached through the job table, so
    these remain the observable facts at the moment the 202 is written. What
    changed is why they matter: the handoff has to be complete and durable in
    the request, because the process that picks it up shares nothing with it.
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
        """The REQUEST does not run the batch, it only records it.

        Not "no worker exists": one does. The claim belongs to `runner.claim`
        in a separate process, and a request that pre-claimed or pre-advanced
        the job would hand the worker a lease it does not hold.
        """
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
            recompute_sample_ids={100}, overall_status="succeeded")

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
#:
#: Membership here is an EXEMPTION from documentation, so a code listed wrongly
#: is undocumented and unguarded at once -- which is exactly what happened to
#: `request_validation_error`: it sat here while `runner.py` put it inside a
#: `RowResult.error` in a stored receipt that a caller reads back from
#: `status_url`. `test_no_row_building_module_takes_the_envelope_exemption`
#: below is the structural guard that would have caught it, and the runner now
#: emits its own declared `job_request_not_executable` instead.
ENVELOPE_ONLY_CODES = frozenset({
    "request_validation_error", "job_not_found", "not_cancellable",
    "authentication_failed", "permission_denied",
})

#: Modules that construct per-row receipts. Nothing they emit may be exempt.
ROW_BUILDING_MODULES = ("executor.py", "resolver.py", "runner.py")


def _declared_codes_used_in(filename):
    """Every ERROR_CODES member appearing as a live string literal in a module.

    Literal-based rather than keyword-based on purpose: `runner.py` passes its
    code POSITIONALLY through `_fail` -> `_failure_receipt`, so a scan for
    `code=` keyword arguments would have found nothing there and reported the
    module clean. Docstrings are excluded (several discuss codes at length);
    `#` comments never reach the AST.
    """
    import ast
    import pathlib

    from nextseek_api.assay_registration import schemas
    from nextseek_api.assay_registration.schemas import ERROR_CODES

    path = pathlib.Path(schemas.__file__).with_name(filename)
    tree = ast.parse(path.read_text())
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            first = node.body[0] if node.body else None
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and node.value in ERROR_CODES and id(node) not in docstrings}


@pytest.mark.django_db
class TestTheAcceptedCountsPromiseNothing:
    """A 202 must not report rows as written that nothing has written.

    `preview(plan)` labels every row in `plan.to_write` "written", so
    `counts=preview(plan).counts` answered a 25,765-row POST with
    `{"written": 25700}` before a single row existed. A worker exists now, so
    those rows will eventually be written -- which makes the projection worse,
    not better: it is indistinguishable from a receipt until the moment it
    stops being true, and on any instance whose drain loop is not running it
    never becomes true at all. (That used to mean any instance without
    `COMPOSE_PROFILES=assay-registration` exported; the loop is a process of the
    app container since 2026-09-02, so it now means an instance whose app
    container is down, which is a louder failure.) That is the defect this
    endpoint was built to remove, reproduced on its own new path.
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

    def test_no_envelope_only_entry_is_stale(self):
        """A code retired from ERROR_CODES but left in the exemption list.

        Note what this does NOT do, because its docstring used to claim it: a
        code that is neither documented nor exempt is caught by the test ABOVE,
        which iterates `ERROR_CODES - ENVELOPE_ONLY_CODES`. This assertion runs
        the other way, over entries the exemption list holds that ERROR_CODES no
        longer declares -- harmless on its own, but a stale name here silently
        exempts nothing while looking like it exempts something.
        """
        from nextseek_api.assay_registration.schemas import ERROR_CODES

        assert ENVELOPE_ONLY_CODES <= ERROR_CODES

    def test_no_row_building_module_takes_the_envelope_exemption(self):
        """The structural guard, in the direction the defect actually ran.

        `ENVELOPE_ONLY_CODES` is hand-maintained, and a code listed there is
        excluded from the documentation check above. So a module that puts an
        "envelope-only" code inside a `RowResult.error` gets a live row-level
        code that is undocumented AND unchecked -- which is precisely what
        `runner.py` did with `request_validation_error`, in a receipt read back
        from `status_url`. Reading the exemption against what the row-building
        modules actually construct closes that loop without hand-listing
        anything.
        """
        offenders = {
            module: sorted(_declared_codes_used_in(module) & ENVELOPE_ONLY_CODES)
            for module in ROW_BUILDING_MODULES
        }
        assert {m: c for m, c in offenders.items() if c} == {}, (
            "these modules build per-row receipts, so every code they emit is "
            "row-level and must be documented rather than exempted"
        )

    def test_the_scan_that_guard_depends_on_actually_finds_codes(self):
        """Guards the guard: an AST scan that silently returns nothing would
        make the test above pass over any amount of drift."""
        found = {m: _declared_codes_used_in(m) for m in ROW_BUILDING_MODULES}
        assert all(found.values()), found
        assert "write_not_confirmed_by_readback" in found["executor.py"]
        assert "job_request_not_executable" in found["runner.py"], (
            "passed positionally through _fail(), which a `code=` keyword scan "
            "would have missed entirely"
        )
        assert "sample_uid_not_unique" in found["resolver.py"]


class TestThePublishedJobExample:
    """The example is schema, not decoration: drf-spectacular renders it into
    /schema/ and it is the only thing a client reads before writing a poller."""

    def test_it_does_not_imply_progress_the_worker_cannot_produce(self):
        """`runner.run_one` calls `record_progress` once, with the full total,
        in the line before `finish`. So `processed_rows` is 0 or `total_rows`,
        never anything between -- and the example published 4000 of 25765.
        """
        from nextseek_api.assay_registration.views import JOB_EXAMPLE

        assert JOB_EXAMPLE["processed_rows"] in (0, JOB_EXAMPLE["total_rows"])
        assert JOB_EXAMPLE["state"] == "running"
        assert JOB_EXAMPLE["processed_rows"] == 0, (
            "a running job has processed 0 rows: the write is one transaction, "
            "so there is no honest number in between"
        )

    def test_the_description_says_so_in_words(self):
        from nextseek_api.endpoint_descriptions import ASSAY_REGISTRATION_JOB_DESC

        assert "`processed_rows` stays 0 until the batch is terminal" in \
            ASSAY_REGISTRATION_JOB_DESC

    def test_the_runner_records_progress_exactly_once_with_the_total(self):
        """The source of the claim above, asserted rather than trusted: a
        second `record_progress` call, or one with a partial figure, would make
        the example and the description wrong again."""
        import ast
        import inspect

        from nextseek_api.assay_registration import runner as runner_module

        calls = [
            node for node in ast.walk(ast.parse(inspect.getsource(runner_module)))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "record_progress"
        ]
        assert len(calls) == 1, "more than one progress write means granular progress"
        [processed] = [ast.unparse(a) for a in calls[0].args[2:3]]
        assert processed == "result.counts.submitted", (
            f"progress is written as {processed}, not the whole batch"
        )


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

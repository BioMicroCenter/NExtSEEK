"""The worker that turns an accepted job into a receipt.

Without it the 202 is a promise nothing keeps, which is the defect this whole
endpoint exists to remove, one level up from the row.

Three of the brief's fixtures were corrected against the tree, each proved by a
run before it was changed:

* the `_recompute` doubles were `MagicMock`s, and `RegistrationResponse.graph`
  is a `GraphOutcome` field, so pydantic rejected them ("Input should be a valid
  dictionary or instance of GraphOutcome") -- and the rejection escaped
  `run_one` entirely, leaving the job `running` forever. They are real
  `GraphOutcome`s now.
* the failure assertions read `terminal_result["errors"]`, an `ErrorResponse`.
  `service.job_status` validates the stored receipt as a `RegistrationResponse`,
  so that shape makes every failed job's status_url a 500 rather than a report.
  See `TestTheReceiptIsReadableByTheStatusEndpoint`, which is the test that
  catches it; the assertions here read the receipt's own row.
* `test_a_lost_lease_does_not_overwrite_the_new_owner` never reaches `finish`:
  a worker that does not hold the lease loses at `jobs.claim`, so the patched
  `finish` is never called. Kept, because the guard it does exercise is real,
  and joined by `test_a_finish_the_lease_rejects_is_not_a_success`, which
  actually drives the branch its name describes.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.core.management import call_command, get_commands
from django.utils import timezone

from nextseek_api.assay_registration import jobs, runner, service
from nextseek_api.assay_registration.executor import ExecutionResult
from nextseek_api.assay_registration.models_db import AssayRegistrationJob
from nextseek_api.assay_registration.schemas import (
    GraphOutcome,
    JobStatusResponse,
    RegistrationCounts,
    RowResult,
)

BODY = {"registrations": [{"sample_uid": "D.NHP-1", "assay_id": 351}], "dry_run": False}


@pytest.fixture
def actor(db):
    return User.objects.create_user(username="admin", password="x", is_superuser=True)


def _result(written=1, overall_status="succeeded"):
    return ExecutionResult(
        rows=[RowResult(index=0, sample_uid="D.NHP-1", status="written",
                        assay_assets_id=414936)],
        counts=RegistrationCounts(submitted=1, written=written),
        written_sample_ids={100}, overall_status=overall_status,
    )


def _runs(result=None, graph_status="succeeded", edges=3):
    """Patch the whole write path out. Returns the patch context managers."""
    graph = GraphOutcome(status=graph_status, edges_recomputed=edges)
    return (
        patch("nextseek_api.assay_registration.runner.get_connection"),
        patch("nextseek_api.assay_registration.runner.plan_batch"),
        patch("nextseek_api.assay_registration.runner.execute",
              return_value=result if result is not None else _result()),
        patch("nextseek_api.assay_registration.runner._recompute",
              return_value=graph),
    )


@pytest.mark.django_db
class TestRunOne:
    def test_a_queued_job_becomes_a_receipt(self, actor):
        job = jobs.create_job(BODY, actor, total_rows=1)
        a, b, c, d = _runs()
        with a, b, c, d:
            assert runner.run_one(job, "worker-a") is True

        job.refresh_from_db()
        assert job.state == "succeeded"
        assert job.processed_rows == 1
        assert job.terminal_result["rows"][0]["assay_assets_id"] == 414936, (
            "the stored receipt must carry the database-assigned key, not a count"
        )

    def test_a_cancelled_job_is_never_executed(self, actor):
        """Cancellation means the job will not START. The write is one
        transaction, so there is no half-done state to stop in."""
        job = jobs.create_job(BODY, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        job.refresh_from_db()
        jobs.request_cancellation(job, actor)

        with patch("nextseek_api.assay_registration.runner.execute") as ex:
            assert runner.run_one(job, "worker-a") is False
        ex.assert_not_called()
        job.refresh_from_db()
        assert job.state == "cancelled"

    def test_a_lost_lease_does_not_overwrite_the_new_owner(self, actor):
        """finish is owner-scoped and returns False rather than raising. A
        zombie must not clobber a receipt it did not produce."""
        job = jobs.create_job(BODY, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        job.refresh_from_db()
        a, b, c, d = _runs(graph_status="skipped", edges=0)
        with a, b, c, d, \
             patch("nextseek_api.assay_registration.runner.jobs.finish",
                   return_value=False):
            assert runner.run_one(job, "worker-b") is False

    def test_an_execution_failure_is_recorded_as_failed_not_left_running(self, actor):
        """A crashed worker must not leave the job claimed forever. The caller
        polling status_url has to learn that it failed."""
        job = jobs.create_job(BODY, actor, total_rows=1)
        with patch("nextseek_api.assay_registration.runner.get_connection",
                   side_effect=RuntimeError("mysql gone")):
            assert runner.run_one(job, "worker-a") is False
        job.refresh_from_db()
        assert job.state == "failed"
        assert "mysql gone" in job.terminal_result["rows"][0]["error"]["message"]

    def test_the_stored_request_is_revalidated_not_trusted(self, actor):
        """submitted_request is JSON that has been round-tripped through the
        database. Feeding it to the planner unvalidated would let a schema
        change turn stored data into a crash inside the transaction."""
        job = jobs.create_job({"registrations": [{"sample_uid": ""}]}, actor, total_rows=1)
        assert runner.run_one(job, "worker-a") is False
        job.refresh_from_db()
        assert job.state == "failed"
        assert job.terminal_result["rows"][0]["error"]["code"] == "request_validation_error"

    def test_a_revalidation_failure_never_opens_a_connection(self, actor):
        """The point of revalidating is to fail BEFORE the transaction, not
        inside it. A crash inside would leave the job claimed."""
        job = jobs.create_job({"registrations": []}, actor, total_rows=1)
        with patch("nextseek_api.assay_registration.runner.get_connection") as conn:
            assert runner.run_one(job, "worker-a") is False
        conn.assert_not_called()

    def test_a_finish_the_lease_rejects_is_not_a_success(self, actor):
        """The branch `test_a_lost_lease_...` names but cannot reach, driven
        directly: the caller DOES hold the lease, so execution happens, and
        `finish` still refuses. run_one must report False rather than True."""
        job = jobs.create_job(BODY, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        job.refresh_from_db()
        a, b, c, d = _runs()
        with a, b, c, d, \
             patch("nextseek_api.assay_registration.runner.jobs.finish",
                   return_value=False) as finish:
            assert runner.run_one(job, "worker-a") is False
        finish.assert_called_once()

    def test_a_job_claimed_by_someone_else_is_left_alone(self, actor):
        job = jobs.create_job(BODY, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        job.refresh_from_db()
        with patch("nextseek_api.assay_registration.runner.execute") as ex, \
             patch("nextseek_api.assay_registration.runner.jobs.finish") as finish:
            assert runner.run_one(job, "worker-b") is False
        ex.assert_not_called()
        finish.assert_not_called()
        job.refresh_from_db()
        assert job.claim_owner == "worker-a"
        assert job.state == "running"

    def test_a_partial_outcome_is_stored_but_is_not_reported_as_success(self, actor):
        """`partial` is a terminal state jobs.finish accepts, and it must not
        be counted as a success by run_pending."""
        job = jobs.create_job(BODY, actor, total_rows=1)
        a, b, c, d = _runs(result=_result(overall_status="partial"))
        with a, b, c, d:
            assert runner.run_one(job, "worker-a") is False
        job.refresh_from_db()
        assert job.state == "partial"
        assert job.terminal_result["overall_status"] == "partial"
        assert job.processed_rows == 1, (
            "record_progress ran even though finish's success-only shortcut did not"
        )

    def test_the_graph_recompute_runs_after_the_connection_closes(self, actor):
        """The write must survive a graph failure, which is only true if the
        MySQL transaction has already committed when the recompute runs. Pinned
        by event order, the same way service.register pins it."""
        events = []
        seen_ids = []
        conn = MagicMock()
        conn.__enter__ = lambda self: self
        conn.__exit__ = lambda self, *a: events.append("connection_closed") or False
        job = jobs.create_job(BODY, actor, total_rows=1)
        with patch("nextseek_api.assay_registration.runner.get_connection",
                   return_value=conn), \
             patch("nextseek_api.assay_registration.runner.plan_batch"), \
             patch("nextseek_api.assay_registration.runner.execute",
                   side_effect=lambda *a: events.append("executed") or _result()), \
             patch("nextseek_api.assay_registration.runner._recompute",
                   side_effect=lambda ids: events.append("recomputed")
                   or seen_ids.append(ids)
                   or GraphOutcome(status="succeeded", edges_recomputed=3)):
            assert runner.run_one(job, "worker-a") is True
        assert events == ["executed", "connection_closed", "recomputed"]
        assert seen_ids == [{100}], (
            "the recompute must be handed the ids the executor actually wrote; "
            "an empty set reports `skipped` and silently leaves the graph stale"
        )

    def test_it_reads_the_job_from_the_database_not_the_handle_it_was_given(self, actor):
        """The caller ALREADY holds the lease, which is the only path on which
        run_one's own refresh is load-bearing -- `jobs.claim` refreshes the
        handle itself, so a test that lets run_one claim proves nothing here.
        (Mutation testing found exactly that: dropping the refresh survived the
        first version of this test.)

        `total_rows` is read from Python twice -- for the failure receipt, and by
        `jobs.finish`'s success shortcut, which is what `processed_rows` reports
        on the API surface -- so a stale handle silently misreports both.
        """
        job = jobs.create_job(BODY, actor, total_rows=5)
        jobs.claim(job, "worker-a")
        job.refresh_from_db()
        AssayRegistrationJob.objects.filter(pk=job.pk).update(total_rows=9)
        assert job.claim_owner == "worker-a", "run_one must skip the claim"
        assert job.total_rows == 5, "the handle is deliberately stale"

        with patch("nextseek_api.assay_registration.runner.get_connection",
                   side_effect=RuntimeError("mysql gone")):
            runner.run_one(job, "worker-a")
        job.refresh_from_db()
        assert job.terminal_result["counts"]["submitted"] == 9

    def test_a_failed_recompute_does_not_fail_the_job(self, actor):
        """assay_assets is the source of truth; the graph is derived. A stale
        derived view must not turn a correct write into a failed receipt."""
        job = jobs.create_job(BODY, actor, total_rows=1)
        a, b, c, d = _runs(graph_status="failed", edges=0)
        with a, b, c, d:
            assert runner.run_one(job, "worker-a") is True
        job.refresh_from_db()
        assert job.state == "succeeded"
        assert job.terminal_result["graph"]["status"] == "failed"


@pytest.mark.django_db
class TestTheReceiptIsReadableByTheStatusEndpoint:
    """Every terminal_result this module writes, driven through the real
    `service.job_status`.

    This is the test the brief did not have, and the one that catches the
    defect it shipped. `job_status` ends with
    ``RegistrationResponse.model_validate(job.terminal_result)`` and
    `views._JOB_LOOKUP_FAILURES` deliberately excludes pydantic's
    ValidationError, so a receipt in any other shape turns "your batch failed"
    into a 500 for the caller polling status_url. Asserting `job.state` alone
    passes while that is true.
    """

    def _status(self, job):
        return JobStatusResponse.model_validate(service.job_status(job.job_id))

    def test_a_success_receipt_reads_back(self, actor):
        job = jobs.create_job(BODY, actor, total_rows=1)
        a, b, c, d = _runs()
        with a, b, c, d:
            runner.run_one(job, "worker-a")
        status = self._status(job)
        assert status.state == "succeeded"
        assert status.processed_rows == 1 and status.total_rows == 1
        assert status.result.mode == "synchronous"
        assert status.result.counts.written == 1
        assert status.result.rows[0].assay_assets_id == 414936
        assert status.result.graph.edges_recomputed == 3

    def test_an_execution_failure_receipt_reads_back(self, actor):
        job = jobs.create_job(BODY, actor, total_rows=1)
        with patch("nextseek_api.assay_registration.runner.get_connection",
                   side_effect=RuntimeError("mysql gone")):
            runner.run_one(job, "worker-a")
        status = self._status(job)
        assert status.state == "failed"
        assert status.result.overall_status == "failed"
        assert status.result.counts.failed == 1
        assert status.result.graph.status == "skipped", (
            "nothing was written, so nothing was recomputed"
        )
        assert status.result.rows[0].error.code == "write_not_confirmed_by_readback"
        assert "mysql gone" in status.result.rows[0].error.message

    def test_a_revalidation_failure_receipt_reads_back(self, actor):
        job = jobs.create_job({"registrations": []}, actor, total_rows=1)
        runner.run_one(job, "worker-a")
        status = self._status(job)
        assert status.state == "failed"
        assert status.result.rows[0].error.code == "request_validation_error"

    def test_a_cancellation_stores_no_receipt_and_still_reads_back(self, actor):
        """`overall_status` has no "cancelled" member, so a receipt here could
        only misreport the outcome as one of the three it does have. `state`
        carries it instead, and processed_rows stays 0."""
        job = jobs.create_job(BODY, actor, total_rows=9)
        jobs.claim(job, "worker-a")
        job.refresh_from_db()
        jobs.request_cancellation(job, actor)
        runner.run_one(job, "worker-a")

        status = self._status(job)
        assert status.state == "cancelled"
        assert status.result is None
        assert status.processed_rows == 0 and status.total_rows == 9


@pytest.mark.django_db
class TestRunPending:
    def test_it_claims_only_what_it_can_hold(self, actor):
        a = jobs.create_job(BODY, actor, total_rows=1)
        b = jobs.create_job(BODY, actor, total_rows=1)
        jobs.claim(b, "someone-else")
        with patch("nextseek_api.assay_registration.runner.run_one",
                   return_value=True) as run:
            assert runner.run_pending(limit=10, owner="worker-a") == 1
        assert run.call_args[0][0].pk == a.pk

    def test_limit_is_respected(self, actor):
        for _ in range(3):
            jobs.create_job(BODY, actor, total_rows=1)
        with patch("nextseek_api.assay_registration.runner.run_one",
                   return_value=True):
            assert runner.run_pending(limit=2, owner="worker-a") == 2

    def test_a_cancelled_job_is_not_drained(self, actor):
        job = jobs.create_job(BODY, actor, total_rows=1)
        jobs.request_cancellation(job, actor)
        with patch("nextseek_api.assay_registration.runner.run_one") as run:
            assert runner.run_pending(limit=10, owner="worker-a") == 0
        run.assert_not_called()

    def test_a_terminal_job_is_not_drained(self, actor):
        job = jobs.create_job(BODY, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        job.refresh_from_db()
        jobs.finish(job, "worker-a", "succeeded", {})
        with patch("nextseek_api.assay_registration.runner.run_one") as run:
            assert runner.run_pending(limit=10, owner="worker-b") == 0
        run.assert_not_called()

    def test_a_cancellation_on_a_job_whose_worker_died_is_not_drained(self, actor):
        """The state filter alone does not cover this. `request_cancellation`
        moves an UNCLAIMED job straight to `cancelled`, so a job excluded by
        `state__in` proves nothing about the cancellation predicate. This one is
        still `running` and unclaimed -- a worker took it, the flag was set, the
        process died -- and it must not be picked up as fresh work."""
        job = jobs.create_job(BODY, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        job.refresh_from_db()
        assert jobs.request_cancellation(job, actor) is True
        AssayRegistrationJob.objects.filter(pk=job.pk).update(claim_owner=None)

        stranded = AssayRegistrationJob.objects.get(pk=job.pk)
        assert stranded.state == "running" and stranded.claim_owner is None

        with patch("nextseek_api.assay_registration.runner.run_one") as run:
            assert runner.run_pending(limit=10, owner="worker-b") == 0
        run.assert_not_called()

    def test_it_counts_successes_not_attempts(self, actor):
        """run_one returns True only for a successful terminal state. A caller
        must not read this number as "jobs processed"."""
        for _ in range(3):
            jobs.create_job(BODY, actor, total_rows=1)
        with patch("nextseek_api.assay_registration.runner.run_one",
                   side_effect=[True, False, True]):
            assert runner.run_pending(limit=10, owner="worker-a") == 2

    def test_a_non_positive_limit_drains_nothing_rather_than_raising(self, actor):
        """Django raises on a negative slice bound. An operator typo on --limit
        should be a no-op, not a traceback out of the worker."""
        jobs.create_job(BODY, actor, total_rows=1)
        with patch("nextseek_api.assay_registration.runner.run_one") as run:
            assert runner.run_pending(limit=0, owner="worker-a") == 0
            assert runner.run_pending(limit=-1, owner="worker-a") == 0
        run.assert_not_called()

    def test_jobs_sharing_a_created_at_are_drained_in_a_stable_order(self, actor):
        """created_at has finite resolution. Without the primary-key tie-break
        two jobs stamped alike drain in whatever order the server returns --
        the same non-determinism the planner's MIN(id) exists to remove."""
        made = [jobs.create_job(BODY, actor, total_rows=1) for _ in range(3)]
        stamp = timezone.now()
        AssayRegistrationJob.objects.filter(
            pk__in=[j.pk for j in made]).update(created_at=stamp)

        seen = []
        with patch("nextseek_api.assay_registration.runner.run_one",
                   side_effect=lambda job, owner: seen.append(job.pk) or True):
            runner.run_pending(limit=10, owner="worker-a")
        assert seen == sorted(j.pk for j in made)

    def test_the_drain_order_names_the_tie_break(self):
        """Asserted structurally, and the reason is worth writing down: under
        the SQLite test database a tied scan comes back in rowid order, which is
        exactly what the tie-break would have produced, so the behavioural test
        above passes with or without it. Only naming the ordering distinguishes
        a deliberate tie-break from an accident of the storage engine."""
        assert runner.DRAIN_ORDER == ("created_at", "pk")


@pytest.mark.django_db
class TestTheManagementCommand:
    def test_it_is_discoverable_by_manage_py(self):
        """`nextseek_api.assay_registration` is not an INSTALLED_APPS entry, so
        Django's per-app scan never walks its management/commands directory.
        Before the shim under `nextseek_api/management/commands/`, `manage.py
        run_assay_registration_jobs` answered "Unknown command" -- verified
        against this checkout. The two attribute-job commands carry the same
        shim for the same reason.
        """
        assert get_commands().get("run_assay_registration_jobs") == "nextseek_api"

    def test_the_shim_re_exports_the_real_implementation(self):
        from nextseek_api.assay_registration.management.commands import (
            run_assay_registration_jobs as real,
        )
        from nextseek_api.management.commands import (
            run_assay_registration_jobs as shim,
        )

        assert shim.Command is real.Command

    def test_it_drains_and_reports(self, actor, capsys):
        jobs.create_job(BODY, actor, total_rows=1)
        with patch("nextseek_api.assay_registration.runner.run_one",
                   return_value=True):
            call_command("run_assay_registration_jobs", "--limit", "3")
        assert "1 job(s) succeeded" in capsys.readouterr().out

    def test_it_passes_the_operators_limit_through(self, actor):
        """Asserting only the printed line lets `--limit` be silently ignored:
        with one job queued, a hardcoded limit of 1 prints the same sentence."""
        with patch("nextseek_api.assay_registration.management.commands"
                   ".run_assay_registration_jobs.run_pending",
                   return_value=0) as run_pending:
            call_command("run_assay_registration_jobs", "--limit", "7")
        assert run_pending.call_args.kwargs["limit"] == 7

    def test_the_owner_it_passes_is_the_worker_identity(self, actor):
        with patch("nextseek_api.assay_registration.management.commands"
                   ".run_assay_registration_jobs.run_pending",
                   return_value=0) as run_pending:
            call_command("run_assay_registration_jobs")
        assert run_pending.call_args.kwargs["limit"] == 1, (
            "the default must claim one job, not drain the whole backlog"
        )
        owner = run_pending.call_args.kwargs["owner"]
        assert owner.count(":") == 2 and owner.split(":")[1].isdigit()


class TestWorkerIdentity:
    def test_two_identities_from_one_process_differ(self):
        """Same host, same pid. Without the nonce every owner-scoped predicate
        in jobs.py would stop discriminating between two restarted workers."""
        assert runner.worker_identity() != runner.worker_identity()

    def test_it_carries_host_and_pid(self):
        import os
        import socket

        host, pid, nonce = runner.worker_identity().split(":")
        assert host == socket.gethostname()
        assert pid == str(os.getpid())
        assert len(nonce) == 8

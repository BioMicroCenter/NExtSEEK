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
* `test_a_lost_lease_does_not_overwrite_the_new_owner` was DELETED. It never
  reached `finish`: a worker that does not hold the lease loses at `jobs.claim`,
  so all five of its patches were unused. Its name and docstring described a
  branch it did not cover, which is the habit `jobs.py`'s own module docstring
  argues against, and it was a strict subset of
  `test_a_job_claimed_by_someone_else_is_left_alone`. The branch it named is
  driven by `test_a_finish_the_lease_rejects_is_not_a_success`.
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

#: The command module, not the runner: the command imports `run_pending` by
#: value, so patching `runner.run_pending` would not be seen by it.
_CMD = ("nextseek_api.assay_registration.management.commands"
        ".run_assay_registration_jobs")


@pytest.fixture
def actor(db):
    return User.objects.create_user(username="admin", password="x", is_superuser=True)


def _result(written=1, overall_status="succeeded"):
    return ExecutionResult(
        rows=[RowResult(index=0, sample_uid="D.NHP-1", status="written",
                        assay_assets_id=414936)],
        counts=RegistrationCounts(submitted=1, written=written),
        recompute_sample_ids={100}, overall_status=overall_status,
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
        assert job.terminal_result["rows"][0]["error"]["code"] == \
            "job_request_not_executable", (
            "NOT request_validation_error, which is the ViewSet's 422 envelope "
            "code for a live POST body. The caller of this receipt never sent a "
            "bad body -- the STORED request no longer revalidates -- and the "
            "actions differ: 422 says fix and resubmit, this says the job is "
            "dead and retrying it will fail identically"
        )

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

    def test_a_recompute_that_RAISES_still_leaves_the_batch_succeeded(self, actor):
        """The one the failed-recompute test below does not cover.

        That one patches `_recompute` to RETURN a failed outcome. This patches it
        to RAISE, which is the case that used to land in the `except` that writes
        "the whole batch failed, nothing was written" -- for a batch already
        committed at the block exit above it. Without this test the invariant is
        defended only by `service._recompute`'s own except clause, one module
        away, with nothing pinning it across the boundary.
        """
        job = jobs.create_job(BODY, actor, total_rows=1)
        with patch("nextseek_api.assay_registration.runner.get_connection"), \
             patch("nextseek_api.assay_registration.runner.plan_batch"), \
             patch("nextseek_api.assay_registration.runner.execute",
                   return_value=_result()), \
             patch("nextseek_api.assay_registration.runner._recompute",
                   side_effect=RuntimeError("bolt refused")):
            assert runner.run_one(job, "worker-a") is True

        job.refresh_from_db()
        assert job.state == "succeeded", "a committed batch is not failed by the graph"
        assert job.terminal_result["rows"][0]["status"] == "written"
        assert job.terminal_result["rows"][0]["assay_assets_id"] == 414936
        assert job.terminal_result["graph"]["status"] == "failed"
        assert "bolt refused" in job.terminal_result["graph"]["error"]

    def test_a_dry_run_job_is_refused_rather_than_executed(self, actor):
        """Unreachable through `service.register`, which answers a dry run inline
        before any job exists. Pinned so the runner is safe on its own terms
        rather than by a caller's construction: a stored request that says "do
        not write" must not be executed by whatever writes this table next."""
        job = jobs.create_job({"registrations": [{"sample_uid": "D.NHP-1",
                                                  "assay_id": 351}],
                               "dry_run": True}, actor, total_rows=1)
        with patch("nextseek_api.assay_registration.runner.get_connection") as conn:
            assert runner.run_one(job, "worker-a") is False
        conn.assert_not_called()
        job.refresh_from_db()
        assert job.state == "failed"
        assert "dry_run" in job.terminal_result["rows"][0]["error"]["message"]

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
        assert status.result.mode == "asynchronous", (
            "the caller was handed mode 'asynchronous' at 202 and sent to this "
            "very status_url; a receipt saying 'synchronous' contradicts the "
            "reply that sent them here"
        )
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
        assert status.result.rows[0].error.code == "job_execution_failed", (
            "not write_not_confirmed_by_readback: that code is published as 'an "
            "insert was attempted and the row was not there on read-back', which "
            "sends a client to inspect a row nothing touched. This one says retry"
        )
        assert "mysql gone" in status.result.rows[0].error.message

    def test_a_revalidation_failure_receipt_reads_back(self, actor):
        job = jobs.create_job({"registrations": []}, actor, total_rows=1)
        runner.run_one(job, "worker-a")
        status = self._status(job)
        assert status.state == "failed"
        assert status.result.rows[0].error.code == "job_request_not_executable"
        assert status.result.mode == "asynchronous"

    def test_every_receipt_this_module_writes_says_asynchronous(self, actor):
        """Both shapes, not just the happy one. `_failure_receipt` is the only
        failure receipt, and it stored "synchronous" too."""
        import ast
        import inspect

        from nextseek_api.assay_registration import runner as runner_module

        modes = {
            kw.value.value
            for node in ast.walk(ast.parse(inspect.getsource(runner_module)))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "RegistrationResponse"
            for kw in node.keywords
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant)
        }
        assert modes == {"asynchronous"}, (
            f"runner.py builds a RegistrationResponse with mode {sorted(modes)}"
        )

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
            call_command("run_assay_registration_jobs", "--once", "--limit", "3")
        assert "1 job(s) succeeded" in capsys.readouterr().out

    def test_a_hand_drain_reports_even_when_it_found_nothing(self, actor, capsys):
        """Silence from `--once` is indistinguishable from a hang."""
        with patch("nextseek_api.assay_registration.runner.run_one") as run:
            call_command("run_assay_registration_jobs", "--once")
        run.assert_not_called()
        assert "0 job(s) succeeded" in capsys.readouterr().out

    def test_it_loops_by_default(self, actor):
        """The founding argument of this task, one level up. A command that makes
        one pass and exits is only a worker if something re-runs it, and nothing
        did -- so `status_url` would still report `accepted`, 0 of N, forever in
        a real deployment. `dispatch_attribute_outbox` says the same in its own
        docstring and compose runs it `restart: unless-stopped`.

        The loop is broken by making `time.sleep` raise on the third call, which
        is also what proves the sleep is between passes rather than absent.
        """
        calls = []

        def stop_after_three(seconds):
            calls.append(seconds)
            if len(calls) == 3:
                raise KeyboardInterrupt

        with patch(_CMD + ".run_pending", return_value=0) as run_pending, \
             patch(_CMD + ".time.sleep", side_effect=stop_after_three):
            with pytest.raises(KeyboardInterrupt):
                call_command("run_assay_registration_jobs")

        assert run_pending.call_count == 3, "it must keep draining, not run once"
        assert calls == [5.0, 5.0, 5.0], "the default interval, between passes"

    def test_once_makes_exactly_one_pass_and_never_sleeps(self, actor):
        with patch(_CMD + ".run_pending", return_value=0) as run_pending, \
             patch(_CMD + ".time.sleep") as sleep:
            call_command("run_assay_registration_jobs", "--once")
        assert run_pending.call_count == 1
        sleep.assert_not_called()

    def test_the_interval_is_the_operators(self, actor):
        with patch(_CMD + ".run_pending", return_value=0), \
             patch(_CMD + ".time.sleep", side_effect=KeyboardInterrupt) as sleep:
            with pytest.raises(KeyboardInterrupt):
                call_command("run_assay_registration_jobs", "--interval", "0.25")
        sleep.assert_called_once_with(0.25)

    def test_one_bad_pass_does_not_kill_the_loop(self, actor, caplog):
        """Swallowed on purpose. One job's failure is already recorded on that
        job by run_one; a loop that dies on it stops draining every OTHER job."""
        with patch(_CMD + ".run_pending",
                   side_effect=[RuntimeError("db blipped"), 1, KeyboardInterrupt]) as rp, \
             patch(_CMD + ".time.sleep"):
            with pytest.raises(KeyboardInterrupt):
                call_command("run_assay_registration_jobs")
        assert rp.call_count == 3, "the pass after the failure still ran"
        assert "assay-registration drain pass failed" in caplog.text

    def test_the_loop_reports_each_pass_that_drained_something(self, actor, capsys):
        """The loop's report line had nothing pinning it: only the `--once`
        branch's was asserted, so a long-running container could drain job after
        job and log nothing but its opening line. Found by mutation testing."""
        with patch(_CMD + ".run_pending", side_effect=[2, KeyboardInterrupt]), \
             patch(_CMD + ".time.sleep"):
            with pytest.raises(KeyboardInterrupt):
                call_command("run_assay_registration_jobs")
        assert "2 job(s) succeeded" in capsys.readouterr().out

    def test_the_loop_stays_quiet_on_a_pass_that_found_nothing(self, actor, capsys):
        """The other half: a line every --interval seconds forever is noise, not
        a log. Zero-drain passes say nothing."""
        with patch(_CMD + ".run_pending", side_effect=[0, KeyboardInterrupt]), \
             patch(_CMD + ".time.sleep"):
            with pytest.raises(KeyboardInterrupt):
                call_command("run_assay_registration_jobs")
        assert "job(s) succeeded" not in capsys.readouterr().out

    def test_the_loop_announces_itself_before_the_first_sleep(self, actor, capsys):
        """A container whose logs are empty for its first interval looks stuck."""
        with patch(_CMD + ".run_pending", return_value=0), \
             patch(_CMD + ".time.sleep", side_effect=KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                call_command("run_assay_registration_jobs")
        assert "draining every 5.0s" in capsys.readouterr().out

    def test_it_passes_the_operators_limit_through(self, actor):
        """Asserting only the printed line lets `--limit` be silently ignored:
        with one job queued, a hardcoded limit of 1 prints the same sentence."""
        with patch(_CMD + ".run_pending", return_value=0) as run_pending:
            call_command("run_assay_registration_jobs", "--once", "--limit", "7")
        assert run_pending.call_args.kwargs["limit"] == 7

    def test_the_owner_it_passes_is_the_worker_identity(self, actor):
        with patch(_CMD + ".run_pending", return_value=0) as run_pending:
            call_command("run_assay_registration_jobs", "--once")
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

"""Durable job state for batches above the synchronous threshold."""

import uuid
from typing import get_args

import pytest
from django.contrib.auth.models import User
from django.db import models

from nextseek_api.assay_registration import jobs
from nextseek_api.assay_registration.models_db import AssayRegistrationJob
from nextseek_api.assay_registration.schemas import (
    GraphOutcome,
    JobStatusResponse,
    RegistrationCounts,
    RegistrationResponse,
    RowResult,
)


@pytest.fixture
def actor(db):
    return User.objects.create_user(username="admin", password="x", is_superuser=True)


@pytest.mark.django_db
class TestJobLifecycle:
    def test_create_records_the_actor_and_the_submitted_request(self, actor):
        job = jobs.create_job({"registrations": [], "dry_run": False}, actor, total_rows=7)
        assert job.state == "accepted"
        assert job.total_rows == 7
        assert job.processed_rows == 0
        assert job.actor_login == "admin"
        assert job.terminal_result is None

    def test_only_one_worker_can_claim_a_job(self, actor):
        job = jobs.create_job({}, actor, total_rows=1)
        assert jobs.claim(job, "worker-a") is True
        job.refresh_from_db()
        assert jobs.claim(job, "worker-b") is False, "a claimed job is not re-claimable"

    def test_progress_is_recorded_without_losing_the_claim(self, actor):
        job = jobs.create_job({}, actor, total_rows=10)
        jobs.claim(job, "worker-a")
        jobs.record_progress(job, "worker-a", 4)
        job.refresh_from_db()
        assert job.processed_rows == 4
        assert job.state == "running"

    def test_finish_stores_the_terminal_result(self, actor):
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        jobs.finish(job, "worker-a", "succeeded", {"mode": "synchronous", "counts": {}})
        job.refresh_from_db()
        assert job.state == "succeeded"
        assert job.terminal_result["mode"] == "synchronous"

    def test_a_terminal_job_cannot_be_cancelled(self, actor):
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        jobs.finish(job, "worker-a", "succeeded", {})
        job.refresh_from_db()
        assert jobs.request_cancellation(job, actor) is False

    def test_cancellation_is_visible_to_the_worker(self, actor):
        """NO refresh_from_db. That is the whole test.

        The worker holds a handle from before the cancellation. If is_cancelled
        reads the handle rather than the database, the obvious loop
        `while not jobs.is_cancelled(job)` never terminates. A version of this
        test that refreshes first passes against that broken implementation and
        proves only that a refresh works.
        """
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        worker_handle = jobs.get_job(job.job_id)   # what a worker would hold
        assert jobs.request_cancellation(job, actor) is True
        assert jobs.is_cancelled(worker_handle) is True

    def test_an_unclaimed_job_is_cancelled_outright(self, actor):
        """Nothing is running to notice the flag, so the flag alone would leave
        it reporting `accepted` forever with a cancellation nobody acts on."""
        job = jobs.create_job({}, actor, total_rows=1)
        assert jobs.request_cancellation(job, actor) is True
        job.refresh_from_db()
        assert job.state == "cancelled"

    def test_cancelling_a_job_claimed_since_our_handle_asks_the_worker_to_stop(self, actor):
        """The fall-through, exercised with the state it exists for.

        Handle A reads the job unclaimed; worker B claims it; A then cancels.
        The unclaimed branch must miss, and the fall-through must set the flag
        on the now-running job rather than cancelling it outright, because a
        worker IS running and cooperative cancellation is the right mechanism.
        """
        job = jobs.create_job({}, actor, total_rows=1)
        stale_handle = jobs.get_job(job.job_id)      # reads claim_owner as None
        assert jobs.claim(job, "worker-b") is True

        assert jobs.request_cancellation(stale_handle, actor) is True
        job.refresh_from_db()
        assert job.state == "running", "a running job is asked to stop, not terminated"
        assert jobs.is_cancelled(job) is True

    def test_finish_refuses_an_undeclared_state(self, actor):
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        with pytest.raises(ValueError, match="unknown terminal state"):
            jobs.finish(job, "worker-a", "done", {})

    def test_finish_is_owner_scoped(self, actor):
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        job.refresh_from_db()
        assert jobs.finish(job, "worker-b", "succeeded", {}) is False, (
            "a zombie worker must not overwrite the live owner's result"
        )
        assert jobs.finish(job, "worker-a", "succeeded", {}) is True

    def test_a_cancelled_job_does_not_report_every_row_processed(self, actor):
        """processed_rows is on the API surface. A job cancelled at row 4 of
        10,000 reporting 10,000 of 10,000 is a lie a human reads."""
        job = jobs.create_job({}, actor, total_rows=10000)
        jobs.claim(job, "worker-a")
        jobs.record_progress(job, "worker-a", 4)
        job.refresh_from_db()
        jobs.finish(job, "worker-a", "cancelled", {})
        job.refresh_from_db()
        assert job.processed_rows == 4

    def test_cancelling_twice_is_refused(self, actor):
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        assert jobs.request_cancellation(job, actor) is True
        job.refresh_from_db()
        assert jobs.request_cancellation(job, actor) is False

    def test_job_ids_are_unique_uuids(self, actor):
        a = jobs.create_job({}, actor, total_rows=1)
        b = jobs.create_job({}, actor, total_rows=1)
        assert isinstance(a.job_id, uuid.UUID)
        assert a.job_id != b.job_id

# --------------------------------------------------------------------------
# Hardening. Every test below exists because a measured mutation survived the
# suite above; each docstring names the mutation it kills.
# --------------------------------------------------------------------------

def _move_the_row_behind_the_handles_back(job):
    """Advance `state_version` without disturbing any other predicate.

    No public writer leaves a job active AND unclaimed with a moved version, so
    isolating the compare-and-set needs the row moved directly. This is exactly
    what a concurrent writer looks like from the row's point of view, and it is
    the only way to make a handle stale in one field and current in every other.
    """
    AssayRegistrationJob.objects.filter(pk=job.pk).update(
        state_version=models.F("state_version") + 1
    )


@pytest.mark.django_db
class TestTheCompareAndSetIsLoadBearing:
    """`claim` and `request_cancellation` are the two writers the module
    docstring calls compare-and-set. Measured on the round-1 suite, deleting the
    `state_version` predicate from either one left every other test green:
    `test_only_one_worker_can_claim_a_job` passes on `claim_owner__isnull`, and
    `test_cancelling_twice_is_refused` passes on `cancellation_requested_at__isnull`.
    Neither touches the CAS.

    Each test here shows the row is still reachable through a CURRENT handle
    before showing the STALE one is refused, so it cannot pass because some
    other predicate happened to reject the write.
    """

    def test_a_stale_handle_cannot_claim_a_job_whose_version_moved(self, actor):
        """Kills N1 (drop `state_version` from claim's filter)."""
        job = jobs.create_job({}, actor, total_rows=1)
        stale = AssayRegistrationJob.objects.get(pk=job.pk)
        _move_the_row_behind_the_handles_back(job)

        fresh = jobs.get_job(job.job_id)
        assert fresh.state == "accepted" and fresh.claim_owner is None, (
            "the row must still satisfy every non-CAS predicate, or this test "
            "would pass without the CAS"
        )

        assert jobs.claim(stale, "worker-a") is False
        assert jobs.claim(fresh, "worker-a") is True, (
            "a CURRENT handle claims the same row, so the refusal above was the "
            "version check and nothing else"
        )

    def test_a_stale_handle_cannot_cancel_a_job_whose_version_moved(self, actor):
        """Kills N33 (drop `state_version` from request_cancellation's filter).

        The handle must be a CLAIMED one. `request_cancellation` branches on
        `claim_owner is None`, and the unclaimed branch deliberately falls
        through to a non-CAS update so a user's cancel still lands on a job that
        got picked up mid-request.
        """
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        stale = AssayRegistrationJob.objects.get(pk=job.pk)
        _move_the_row_behind_the_handles_back(job)

        fresh = jobs.get_job(job.job_id)
        assert fresh.state == "running" and fresh.cancellation_requested_at is None

        assert jobs.request_cancellation(stale, actor) is False
        assert jobs.request_cancellation(fresh, actor) is True

    def test_claim_advances_the_version(self, actor):
        """Kills N4. Nothing in the brief's suite notices a frozen counter."""
        job = jobs.create_job({}, actor, total_rows=1)
        before = job.state_version
        assert jobs.claim(job, "worker-a") is True
        job.refresh_from_db()
        assert job.state_version == before + 1

    def test_cancellation_advances_the_version(self, actor):
        """Kills N39b (cancellation writes no version bump)."""
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        before = job.state_version
        assert jobs.request_cancellation(job, actor) is True
        job.refresh_from_db()
        assert job.state_version == before + 1

    def test_finish_advances_the_version_even_from_a_stale_handle(self, actor):
        """Kills N6: `finish` bumping with a Python-side `job.state_version + 1`.

        This is the ABA the round-1 code shipped. `finish` is owner-scoped, NOT
        compare-and-set, so unlike `claim` it CAN win with a stale handle -- and
        a Python-side bump then writes a number the row has already held. The
        token stops being monotonic and whoever holds that number passes a later
        compare-and-set they should fail. `models.F()` is what makes the
        increment relative to the row rather than to the handle.
        """
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        assert job.state_version == 1
        _move_the_row_behind_the_handles_back(job)   # row is at 2, handle says 1

        assert jobs.finish(job, "worker-a", "succeeded", {}) is True
        job.refresh_from_db()
        assert job.state_version == 3, (
            "a Python-side bump would write 1 + 1 = 2, a version the row already "
            "held; the counter must advance from the ROW's value, not the handle's"
        )


@pytest.mark.django_db
class TestATransitionTouchesExactlyOneJob:
    def test_claiming_one_job_does_not_claim_every_claimable_job(self, actor):
        """Kills N-claim-pk. Two jobs created back to back are identical to every
        predicate except the primary key."""
        first = jobs.create_job({}, actor, total_rows=1)
        second = jobs.create_job({}, actor, total_rows=1)

        assert jobs.claim(first, "worker-a") is True

        second.refresh_from_db()
        assert second.claim_owner is None
        assert second.state == "accepted"
        assert second.state_version == 0

    def test_finishing_one_job_does_not_terminate_every_job(self, actor):
        """Kills N24 (drop `pk` from finish's filter).

        Both jobs are claimed by the SAME owner and both are running, so `pk` is
        the only thing separating them. Dropping it would mark every job this
        worker holds succeeded and overwrite every terminal result -- the most
        destructive single edit available in this module.
        """
        first = jobs.create_job({}, actor, total_rows=1)
        second = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(first, "worker-a")
        jobs.claim(second, "worker-a")

        assert jobs.finish(first, "worker-a", "succeeded", {"mode": "synchronous"}) is True

        second.refresh_from_db()
        assert second.state == "running"
        assert second.terminal_result is None
        assert second.claim_owner == "worker-a"

    def test_progress_is_written_to_one_job_only(self, actor):
        """Kills N16 (drop `pk` from record_progress's filter)."""
        first = jobs.create_job({}, actor, total_rows=10)
        second = jobs.create_job({}, actor, total_rows=10)
        jobs.claim(first, "worker-a")
        jobs.claim(second, "worker-a")

        assert jobs.record_progress(first, "worker-a", 4) is True

        second.refresh_from_db()
        assert second.processed_rows == 0


@pytest.mark.django_db
class TestOnlyTheLeaseHolderMayAdvanceTheJob:
    """`record_progress` and `finish` are owner-plus-state scoped rather than
    compare-and-set. These pin that scoping, which is the whole of their safety.
    """

    def test_a_stranger_cannot_record_progress(self, actor):
        """Kills N14. A zombie worker must not corrupt the live owner's count."""
        job = jobs.create_job({}, actor, total_rows=10)
        jobs.claim(job, "worker-a")
        jobs.record_progress(job, "worker-a", 4)

        assert jobs.record_progress(job, "worker-b", 9) is False

        job.refresh_from_db()
        assert job.processed_rows == 4

    def test_progress_cannot_be_recorded_against_a_terminal_job(self, actor):
        """Kills N20. Without the state predicate the endpoint reports the
        contradiction "succeeded, 4 of 10".

        `finish` clears `claim_owner`, so writing this the obvious way tests
        nothing: the owner predicate rejects the write before the state
        predicate is ever consulted, and dropping `state="running"` from the
        filter stays green. The owner is put back so the state predicate is the
        only guard left standing. Measured -- this test did not kill N20 until
        that line was added.
        """
        job = jobs.create_job({}, actor, total_rows=10)
        jobs.claim(job, "worker-a")
        jobs.finish(job, "worker-a", "succeeded", {})
        AssayRegistrationJob.objects.filter(pk=job.pk).update(claim_owner="worker-a")
        job.refresh_from_db()
        assert job.processed_rows == 10 and job.claim_owner == "worker-a"

        assert jobs.record_progress(job, "worker-a", 4) is False

        job.refresh_from_db()
        assert job.processed_rows == 10
        assert job.state == "succeeded"

    def test_a_terminal_job_cannot_be_finished_again(self, actor):
        """Kills N23 (drop the active-state predicate from finish's filter).

        `finish` clears `claim_owner`, so a second finish is already refused by
        the owner predicate; this pins the state predicate as an independent
        guard rather than relying on that side effect.
        """
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        assert jobs.finish(job, "worker-a", "succeeded", {"first": True}) is True
        job.refresh_from_db()

        AssayRegistrationJob.objects.filter(pk=job.pk).update(claim_owner="worker-a")
        job.refresh_from_db()

        assert jobs.finish(job, "worker-a", "failed", {"second": True}) is False
        job.refresh_from_db()
        assert job.state == "succeeded"
        assert job.terminal_result == {"first": True}


@pytest.mark.django_db
class TestOnlyAnActiveJobIsClaimable:
    def test_a_terminal_job_is_not_claimable(self, actor):
        """Kills N-claim-state.

        `finish` clears `claim_owner`, so the finished row has a NULL owner and a
        version the refreshed handle matches: the state predicate is the only
        thing standing between a worker and a job that has already finished.
        """
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        jobs.finish(job, "worker-a", "failed", {})
        job.refresh_from_db()
        assert job.claim_owner is None and job.state_version == 2

        assert jobs.claim(job, "worker-b") is False


@pytest.mark.django_db
class TestTheLease:
    def test_the_claim_grants_a_lease_of_LEASE_SECONDS(self, actor):
        """Kills N57 (LEASE_SECONDS collapsed to 0) and N58 (lease granted as
        `now`). The constant is asserted separately from the arithmetic: a
        zero-length constant makes the subtraction agree with itself, so only the
        explicit bound on the constant catches it."""
        assert jobs.LEASE_SECONDS > 0, "a zero-length lease is never a lease"

        job = jobs.create_job({}, actor, total_rows=1)
        assert jobs.claim(job, "worker-a") is True
        job.refresh_from_db()

        assert job.last_heartbeat_at is not None
        assert job.lease_expires_at is not None
        granted = (job.lease_expires_at - job.last_heartbeat_at).total_seconds()
        assert granted == pytest.approx(jobs.LEASE_SECONDS, abs=1)

    def test_finish_releases_the_lease(self, actor):
        """Kills N27 and N28.

        The lease fields are cleared on termination so a future reaper's
        expired-lease scan does not have to special-case terminal jobs -- a
        finished job simply holds no lease.
        """
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        job.refresh_from_db()
        assert job.claim_owner == "worker-a" and job.lease_expires_at is not None

        assert jobs.finish(job, "worker-a", "succeeded", {}) is True

        job.refresh_from_db()
        assert job.claim_owner is None
        assert job.lease_expires_at is None
        assert job.last_heartbeat_at is None


@pytest.mark.django_db
class TestTheHeartbeat:
    """`heartbeat` is in this module's published interface and the brief's suite
    never calls it."""

    def test_the_owner_can_renew_its_own_lease(self, actor):
        """Kills N11 and N12: heartbeat returning True having written nothing."""
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        job.refresh_from_db()
        first_expiry, first_beat = job.lease_expires_at, job.last_heartbeat_at

        assert jobs.heartbeat(job, "worker-a") is True
        job.refresh_from_db()
        # Strictly later, not `>=`, or a heartbeat that writes nothing passes.
        assert job.lease_expires_at > first_expiry
        assert job.last_heartbeat_at > first_beat

    def test_a_stranger_cannot_renew_someone_elses_lease(self, actor):
        """Kills N8 and N10."""
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        assert jobs.heartbeat(job, "worker-b") is False

    def test_a_terminal_job_has_no_lease_to_renew(self, actor):
        """Kills N14.

        Same trap as the progress test above: `finish` clears `claim_owner`, so
        without putting it back the owner predicate does all the work and
        heartbeat's `state="running"` filter could be deleted unnoticed.
        Measured -- this did not kill N14 until the owner was restored.
        """
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        jobs.finish(job, "worker-a", "succeeded", {})
        AssayRegistrationJob.objects.filter(pk=job.pk).update(claim_owner="worker-a")
        job.refresh_from_db()
        assert job.claim_owner == "worker-a" and job.state == "succeeded"

        assert jobs.heartbeat(job, "worker-a") is False


@pytest.mark.django_db
class TestUpdatedAtActuallyMoves:
    def test_every_writer_advances_updated_at(self, actor):
        """Kills the five `updated_at` mutations (N13, N19, and the claim /
        finish / cancel equivalents).

        `auto_now` fires in `DateTimeField.pre_save`, which only runs on
        `Model.save()`. Every writer here goes through `QuerySet.update()`, which
        skips `pre_save` entirely -- so unless each update passes `updated_at`
        explicitly the column sits frozen at `created_at` for the life of the
        job, on a row an operator reads to see whether anything is still moving.
        """
        job = jobs.create_job({}, actor, total_rows=10)
        job.refresh_from_db()
        seen = [("create", job.updated_at)]

        for label, call in (
            ("claim", lambda: jobs.claim(job, "worker-a")),
            ("heartbeat", lambda: jobs.heartbeat(job, "worker-a")),
            ("record_progress", lambda: jobs.record_progress(job, "worker-a", 3)),
            ("request_cancellation", lambda: jobs.request_cancellation(job, actor)),
            ("finish", lambda: jobs.finish(job, "worker-a", "cancelled", {})),
        ):
            assert call() is True, f"{label} did not take effect"
            job.refresh_from_db()
            seen.append((label, job.updated_at))

        for (prev_label, prev), (label, current) in zip(seen, seen[1:]):
            assert current > prev, f"{label} left updated_at at its {prev_label} value"


@pytest.mark.django_db
class TestTheTerminalStateVocabulary:
    """`finish` takes a `str` into a field whose response model is a closed
    `Literal`. That is the same read-back hazard `ERROR_CODES` carries, in a
    second field: an undeclared state writes cleanly and then fails every
    subsequent status read.
    """

    def test_TERMINAL_STATES_partitions_the_response_literal_with_ACTIVE_STATES(self):
        """Kills N21 (a bogus member added to TERMINAL_STATES).

        Derived from the schema rather than restated, so the two cannot drift:
        every state JobStatusResponse admits is either active or terminal, with
        nothing invented and nothing orphaned.
        """
        declared = set(get_args(JobStatusResponse.model_fields["state"].annotation))

        assert jobs.TERMINAL_STATES < declared
        assert declared - jobs.TERMINAL_STATES == set(AssayRegistrationJob.ACTIVE_STATES)

    @pytest.mark.parametrize("state", sorted(jobs.TERMINAL_STATES))
    def test_every_declared_terminal_state_is_writable(self, actor, state):
        """Kills N20 (drop the guard) in the other direction: the guard must not
        reject a state the response model declares."""
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        assert jobs.finish(job, "worker-a", state, {}) is True
        job.refresh_from_db()
        assert job.state == state

    def test_a_stored_result_reads_back_through_JobStatusResponse(self, actor):
        """The round trip the guard exists to protect, exercised end to end:
        what `finish` persists is what the status endpoint will deserialize."""
        stored = RegistrationResponse(
            mode="synchronous", overall_status="succeeded",
            counts=RegistrationCounts(submitted=1, written=1),
            rows=[RowResult(index=0, sample_uid="S1", status="written",
                            assay_assets_id=99)],
            graph=GraphOutcome(status="succeeded", edges_recomputed=3),
        ).model_dump(mode="json")

        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        assert jobs.finish(job, "worker-a", "succeeded", stored) is True
        job.refresh_from_db()

        read_back = JobStatusResponse(
            job_id=job.job_id, state=job.state,
            processed_rows=job.processed_rows, total_rows=job.total_rows,
            result=RegistrationResponse.model_validate(job.terminal_result),
        )
        assert read_back.state == "succeeded"
        assert read_back.result.rows[0].assay_assets_id == 99


@pytest.mark.django_db
class TestCancellationReachesTheWorker:
    def test_is_cancelled_asks_the_database_not_the_handle(self, actor):
        """Kills N42, the defect this round fixed.

        `request_cancellation` writes through `QuerySet.update()`, which
        refreshes no other handle. A worker polling `while not
        jobs.is_cancelled(job)` holds one handle for the life of the run, so an
        implementation that reads `job.cancellation_requested_at` reports the
        moment that handle was loaded, forever, and the loop never terminates.

        The handle here is deliberately loaded BEFORE the cancellation and never
        refreshed -- that is the whole test.
        """
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        worker_handle = jobs.get_job(job.job_id)
        assert worker_handle.cancellation_requested_at is None

        assert jobs.request_cancellation(job, actor) is True

        assert jobs.is_cancelled(worker_handle) is True
        assert worker_handle.cancellation_requested_at is None, (
            "the handle is still stale; is_cancelled saw the cancellation only "
            "because it queried"
        )

    def test_a_claimed_but_uncancelled_job_is_not_cancelled(self, actor):
        """Kills N43 and N44.

        Every job reaching `is_cancelled` elsewhere was claimed first, so
        `lease_expires_at` and `last_heartbeat_at` are set on it too -- meaning
        the query could test either of those columns instead of the cancellation
        column and every one of those tests would still pass.
        """
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        job.refresh_from_db()
        assert job.lease_expires_at is not None

        assert jobs.is_cancelled(job) is False

    def test_cancelling_an_unclaimed_job_terminates_it_rather_than_flagging_it(self, actor):
        """Kills N36 (the unclaimed branch stops setting `state`).

        Cooperative cancellation needs a worker to notice the flag. An unclaimed
        job has none, so a flag alone leaves it reporting `accepted` forever with
        a cancellation nobody will ever act on. Terminal means terminal: no
        longer claimable, and the audit fields still recorded.
        """
        job = jobs.create_job({}, actor, total_rows=1)

        assert jobs.request_cancellation(job, actor) is True

        job.refresh_from_db()
        assert job.state == "cancelled"
        assert job.cancellation_requested_at is not None
        assert job.cancellation_actor_django_user_id == actor.id
        assert jobs.claim(job, "worker-a") is False, "a cancelled job is not work"

    def test_a_claimed_job_is_only_flagged_not_terminated(self, actor):
        """Kills N37/N38 (the unclaimed branch firing on a claimed job).

        The counterpart to the test above: something IS running, so the state
        must be left alone for the worker to unwind and call finish itself.
        Terminating it here would strand a live worker writing to a job the API
        already calls finished.
        """
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")

        assert jobs.request_cancellation(job, actor) is True

        job.refresh_from_db()
        assert job.state == "running"
        assert job.claim_owner == "worker-a"
        assert job.cancellation_requested_at is not None

    def test_the_fall_through_still_refuses_an_already_cancelled_job(self, actor):
        """Kills N70 (the fall-through dropping `cancellation_requested_at__isnull`).

        Reachable through the public API alone, and an ordinary thing for a user
        to do: cancel, then cancel again from a page whose handle predates the
        claim. The second request takes the fall-through, where the
        already-cancelled predicate is the only thing left to refuse it -- the
        first branch's own copy was passed over, and the fall-through drops the
        version pin by design. Without it the second cancel reports success and
        re-bumps the version for a cancellation that was already recorded.
        """
        job = jobs.create_job({}, actor, total_rows=1)
        stale_handle = jobs.get_job(job.job_id)      # reads claim_owner as None
        jobs.claim(job, "worker-b")
        assert jobs.request_cancellation(job, actor) is True
        job.refresh_from_db()
        version_after_the_real_cancellation = job.state_version

        assert jobs.request_cancellation(stale_handle, actor) is False

        job.refresh_from_db()
        assert job.state_version == version_after_the_real_cancellation, (
            "a refused cancellation must not advance the token"
        )

    def test_pure_version_drift_on_an_unclaimed_job_is_refused_not_half_applied(self, actor):
        """Kills N66 (the fall-through dropping `claim_owner__isnull=False`).

        The fall-through exists for one case: the job was claimed between our
        read and our write, so the cancellation should reach the running worker
        as a flag. Without a predicate pinning it to that case it ALSO catches
        pure version drift on a job that is still unclaimed -- and then it writes
        `cancellation_requested_at` without `state="cancelled"`, recreating the
        orphaned cancellation the unclaimed branch exists to prevent, from inside
        that branch's own fall-through. Nothing would ever act on the flag,
        because nothing is running.

        The drift is reached through the ORM because no public writer produces
        it: every writer that bumps the version either takes ownership or leaves
        the job terminal. That made this unreachable in practice and therefore
        latent, which is why the previous round reported it instead of pinning
        it -- a test then could only have pinned the wrong behaviour. Now that
        the behaviour is right, pinning it is what keeps it right.

        Refusing is the honest outcome: the caller retries with a fresh handle
        and gets a real cancellation, rather than a half-applied one that reads
        as success.
        """
        job = jobs.create_job({}, actor, total_rows=1)
        stale = jobs.get_job(job.job_id)
        _move_the_row_behind_the_handles_back(job)

        assert jobs.request_cancellation(stale, actor) is False

        job.refresh_from_db()
        assert job.state == "accepted"
        assert job.cancellation_requested_at is None, (
            "a refused cancellation must write nothing at all, not a flag with "
            "no state change behind it"
        )

        # The row is genuinely cancellable; only the stale handle was refused.
        assert jobs.request_cancellation(jobs.get_job(job.job_id), actor) is True
        job.refresh_from_db()
        assert job.state == "cancelled"


@pytest.mark.django_db
class TestWhatIsDurablyRecorded:
    def test_the_submitted_request_round_trips_through_the_database(self, actor):
        """Kills N47. The brief's create test passes a payload and never reads it
        back, so `create_job` could drop the field entirely and stay green."""
        payload = {"registrations": [{"sample_uid": "S1", "assay": "RNA-seq"}],
                   "dry_run": False}
        job = jobs.create_job(payload, actor, total_rows=1)
        job.refresh_from_db()
        assert job.submitted_request == payload

    def test_the_submitting_actor_id_is_recorded(self, actor):
        """Kills N48. The brief's create test checks `actor_login` but never the
        id it is paired with."""
        job = jobs.create_job({}, actor, total_rows=1)
        job.refresh_from_db()
        assert job.actor_django_user_id == actor.id

    def test_the_cancelling_actor_is_recorded(self, actor):
        """Kills N39. Who cancelled a production write is audit evidence."""
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        assert jobs.request_cancellation(job, actor) is True
        job.refresh_from_db()
        assert job.cancellation_actor_django_user_id == actor.id

    def test_a_succeeded_job_reports_every_row_as_processed(self, actor):
        """Kills N26. The success counterpart to the brief's cancelled-job test:
        `processed_rows` must be completed on success and left alone otherwise,
        and only asserting one half would let either rule be dropped."""
        job = jobs.create_job({}, actor, total_rows=10)
        jobs.claim(job, "worker-a")
        jobs.record_progress(job, "worker-a", 4)

        jobs.finish(job, "worker-a", "succeeded", {"mode": "synchronous"})
        job.refresh_from_db()
        assert job.processed_rows == 10

    def test_a_failed_job_keeps_the_count_it_actually_reached(self, actor):
        """Kills N25 (set `processed_rows` unconditionally).

        The brief pins this for `cancelled`; `failed` travels the same path and
        the same lie -- 10,000 of 10,000 for a job that died at row 4 -- reaches
        the same API surface.
        """
        job = jobs.create_job({}, actor, total_rows=10000)
        jobs.claim(job, "worker-a")
        jobs.record_progress(job, "worker-a", 4)

        jobs.finish(job, "worker-a", "failed", {"error": "boom"})
        job.refresh_from_db()
        assert job.processed_rows == 4


@pytest.mark.django_db
class TestGetJob:
    """`get_job` is the lookup the status and cancel endpoints are built on."""

    def test_it_looks_the_job_up_by_its_public_job_id(self, actor):
        """Kills N49 (lookup by primary key instead of `job_id`)."""
        jobs.create_job({}, actor, total_rows=1)
        wanted = jobs.create_job({}, actor, total_rows=2)

        found = jobs.get_job(wanted.job_id)
        assert found.pk == wanted.pk
        assert found.total_rows == 2

    def test_an_unknown_job_id_raises_rather_than_returning_None(self, actor):
        """Kills N50, and pins the contract the ViewSet depends on: it catches
        `ObjectDoesNotExist` and maps it to a 404, so a caller writing
        `if job is None` would get a 500 instead."""
        with pytest.raises(AssayRegistrationJob.DoesNotExist):
            jobs.get_job(uuid.uuid4())

"""Durable job state for batches above the synchronous threshold."""

import uuid

import pytest
from django.contrib.auth.models import User

from nextseek_api.assay_registration import jobs
from nextseek_api.assay_registration.models_db import AssayRegistrationJob


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
        jobs.record_progress(job, 4)
        job.refresh_from_db()
        assert job.processed_rows == 4
        assert job.state == "running"

    def test_finish_stores_the_terminal_result(self, actor):
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        jobs.finish(job, "succeeded", {"mode": "synchronous", "counts": {}})
        job.refresh_from_db()
        assert job.state == "succeeded"
        assert job.terminal_result["mode"] == "synchronous"

    def test_a_terminal_job_cannot_be_cancelled(self, actor):
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        jobs.finish(job, "succeeded", {})
        job.refresh_from_db()
        assert jobs.request_cancellation(job, actor) is False

    def test_cancellation_is_visible_to_the_worker(self, actor):
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        assert jobs.request_cancellation(job, actor) is True
        job.refresh_from_db()
        assert jobs.is_cancelled(job) is True

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


@pytest.mark.django_db
class TestTheCompareAndSetIsLoadBearing:
    """The brief's suite is green with the `state_version` predicate DELETED from
    both `claim` and `request_cancellation` (measured: mutations M1 and M11).

    `test_only_one_worker_can_claim_a_job` passes because of
    `claim_owner__isnull=True`, and `test_cancelling_twice_is_refused` passes
    because of `cancellation_requested_at__isnull=True`. Neither exercises the
    CAS. Each test below pins the CAS *specifically*, by first showing the row is
    still reachable through a CURRENT handle and only then showing that a STALE
    handle is refused -- so it cannot pass because some other predicate happened
    to reject the write.
    """

    def test_a_stale_handle_cannot_claim_a_job_whose_version_moved(self, actor):
        """Kills M1 (drop `state_version` from claim's filter).

        A cancellation lands while a worker is still deciding. The worker holds
        version 0; the row is at version 1, still `accepted` and still unclaimed,
        so every other predicate in the filter passes. Only the CAS refuses it.
        """
        job = jobs.create_job({}, actor, total_rows=1)
        stale = AssayRegistrationJob.objects.get(pk=job.pk)
        assert stale.state_version == 0

        jobs.request_cancellation(job, actor)
        job.refresh_from_db()
        assert job.state_version == 1
        assert job.state == "accepted" and job.claim_owner is None, (
            "the row must still satisfy every non-CAS predicate, or this test "
            "would pass without the CAS"
        )

        assert jobs.claim(stale, "worker-a") is False
        assert jobs.claim(job, "worker-a") is True, (
            "a CURRENT handle claims the same row, so the refusal above was the "
            "version check and nothing else"
        )

    def test_a_stale_handle_cannot_cancel_a_job_whose_version_moved(self, actor):
        """Kills M11 (drop `state_version` from request_cancellation's filter)."""
        job = jobs.create_job({}, actor, total_rows=1)
        stale = AssayRegistrationJob.objects.get(pk=job.pk)

        jobs.claim(job, "worker-a")
        job.refresh_from_db()
        assert job.state == "running" and job.cancellation_requested_at is None, (
            "the row must still satisfy every non-CAS predicate"
        )

        assert jobs.request_cancellation(stale, actor) is False
        assert jobs.request_cancellation(job, actor) is True, (
            "a CURRENT handle cancels the same row"
        )

    def test_claim_advances_the_version(self, actor):
        """Kills M4. Nothing in the brief's suite notices if the counter never moves."""
        job = jobs.create_job({}, actor, total_rows=1)
        before = job.state_version
        assert jobs.claim(job, "worker-a") is True
        job.refresh_from_db()
        assert job.state_version == before + 1

    def test_cancellation_advances_the_version(self, actor):
        """Kills M14."""
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        before = job.state_version
        assert jobs.request_cancellation(job, actor) is True
        job.refresh_from_db()
        assert job.state_version == before + 1

    def test_finish_advances_the_version(self, actor):
        """Kills M22."""
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        before = job.state_version
        jobs.finish(job, "succeeded", {})
        job.refresh_from_db()
        assert job.state_version == before + 1


@pytest.mark.django_db
class TestATransitionTouchesExactlyOneJob:
    def test_claiming_one_job_does_not_claim_every_claimable_job(self, actor):
        """Kills M8 (drop `pk` from claim's filter).

        Two jobs created back to back are identical to every predicate except the
        primary key: same version, same state, both unclaimed.
        """
        first = jobs.create_job({}, actor, total_rows=1)
        second = jobs.create_job({}, actor, total_rows=1)

        assert jobs.claim(first, "worker-a") is True

        second.refresh_from_db()
        assert second.claim_owner is None
        assert second.state == "accepted"
        assert second.state_version == 0

    def test_progress_is_written_to_one_job_only(self, actor):
        """Kills M19 (record_progress unscoped to the job)."""
        first = jobs.create_job({}, actor, total_rows=10)
        second = jobs.create_job({}, actor, total_rows=10)
        jobs.claim(first, "worker-a")

        jobs.record_progress(first, 4)

        second.refresh_from_db()
        assert second.processed_rows == 0


@pytest.mark.django_db
class TestOnlyAnActiveJobIsClaimable:
    def test_a_terminal_job_is_not_claimable(self, actor):
        """Kills M3 (drop the active-state predicate from claim's filter).

        The job never got claimed, so `claim_owner` is still NULL and the CAS
        matches after the refresh. The state predicate is the only thing standing
        between a worker and a job that has already finished.
        """
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.finish(job, "failed", {})
        job.refresh_from_db()
        assert job.claim_owner is None and job.state_version == 1

        assert jobs.claim(job, "worker-a") is False


@pytest.mark.django_db
class TestTheLease:
    def test_the_claim_grants_a_lease_of_LEASE_SECONDS(self, actor):
        """Kills M9 (LEASE_SECONDS collapsed to 0) and M10 (lease granted as `now`).

        The constant is asserted separately from the arithmetic: a zero-length
        constant makes the subtraction agree with itself, so only the explicit
        bound on the constant catches it.
        """
        assert jobs.LEASE_SECONDS > 0, "a zero-length lease is never a lease"

        job = jobs.create_job({}, actor, total_rows=1)
        assert jobs.claim(job, "worker-a") is True
        job.refresh_from_db()

        assert job.last_heartbeat_at is not None
        assert job.lease_expires_at is not None
        granted = (job.lease_expires_at - job.last_heartbeat_at).total_seconds()
        assert granted == pytest.approx(jobs.LEASE_SECONDS, abs=1)


@pytest.mark.django_db
class TestTheHeartbeat:
    """`heartbeat` is in this module's published interface and the brief's suite
    does not call it once: mutations M30, M31 and M32 all survived it.
    """

    def test_the_owner_can_renew_its_own_lease(self, actor):
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        job.refresh_from_db()
        first_expiry = job.lease_expires_at

        assert jobs.heartbeat(job, "worker-a") is True
        job.refresh_from_db()
        assert job.lease_expires_at >= first_expiry

    def test_a_stranger_cannot_renew_someone_elses_lease(self, actor):
        """Kills M30 (drop `claim_owner` from heartbeat's filter) and M32."""
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        assert jobs.heartbeat(job, "worker-b") is False

    def test_a_terminal_job_has_no_lease_to_renew(self, actor):
        """Kills M31 (drop `state="running"` from heartbeat's filter)."""
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        jobs.finish(job, "succeeded", {})
        job.refresh_from_db()

        assert jobs.heartbeat(job, "worker-a") is False


@pytest.mark.django_db
class TestWhatIsDurablyRecorded:
    def test_the_submitted_request_round_trips_through_the_database(self, actor):
        """Kills M28.

        The brief's create test passes a payload and then never reads it back, so
        `create_job` could drop the field entirely and stay green.
        """
        payload = {"registrations": [{"sample_uid": "S1", "assay": "RNA-seq"}],
                   "dry_run": False}
        job = jobs.create_job(payload, actor, total_rows=1)
        job.refresh_from_db()
        assert job.submitted_request == payload

    def test_the_cancelling_actor_is_recorded(self, actor):
        """Kills M16. Who cancelled a production write is audit evidence."""
        job = jobs.create_job({}, actor, total_rows=1)
        jobs.claim(job, "worker-a")
        assert jobs.request_cancellation(job, actor) is True
        job.refresh_from_db()
        assert job.cancellation_actor_django_user_id == actor.id

    def test_a_finished_job_reports_every_row_as_processed(self, actor):
        """Kills M23.

        Pinned for the succeeded path only. That `finish` sets
        `processed_rows = total_rows` for a FAILED or CANCELLED job too is a
        separate concern, raised in the task report rather than pinned here.
        """
        job = jobs.create_job({}, actor, total_rows=10)
        jobs.claim(job, "worker-a")
        jobs.record_progress(job, 4)

        jobs.finish(job, "succeeded", {"mode": "synchronous"})
        job.refresh_from_db()
        assert job.processed_rows == 10


@pytest.mark.django_db
class TestGetJob:
    """`get_job` is the lookup the status and cancel endpoints are built on and
    the brief's suite never calls it (M33 and M34 both survived).
    """

    def test_it_looks_the_job_up_by_its_public_job_id(self, actor):
        """Kills M33 (lookup by primary key instead of `job_id`)."""
        jobs.create_job({}, actor, total_rows=1)
        wanted = jobs.create_job({}, actor, total_rows=2)

        found = jobs.get_job(wanted.job_id)
        assert found.pk == wanted.pk
        assert found.total_rows == 2

    def test_an_unknown_job_id_raises_rather_than_returning_None(self, actor):
        """Kills M34, and pins the contract the ViewSet depends on.

        The annotation on `get_job` says `Optional[...]`, but the body cannot
        return None -- it raises. The ViewSet turns that raise into a 404 by
        catching `ObjectDoesNotExist`; a caller that trusted the annotation and
        tested `is None` would get an uncaught exception and a 500 instead.
        """
        with pytest.raises(AssayRegistrationJob.DoesNotExist):
            jobs.get_job(uuid.uuid4())

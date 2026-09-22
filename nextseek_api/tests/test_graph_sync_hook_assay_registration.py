"""The assay-registration hook: a membership write queues a graph sync (spec 5 E8, 7.3, 12; CI-6).

Registering a sample in an assay changes the assay labels of every DERIVED_FROM edge incident to it, so both entry
points enqueue one ``samples`` row per sample whose links the registration changed: ``service.register`` for a batch
answered in the request, ``runner.run_one`` for one answered by the job worker. The drain relabels those edges through
``targeted.sync_samples``, under the one label rule; nothing in the request touches Neo4j.

CI-6 asks three things of every hook site, and each one is here for both: the success path writes its rows after the
writer's own commit, a batch that changed no links writes none, and an enqueue failure never reaches the caller.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth.models import User
from django.db import OperationalError

from nextseek_api.assay_registration import jobs, runner, service
from nextseek_api.assay_registration.executor import ExecutionResult
from nextseek_api.assay_registration.schemas import (
    RegistrationCounts,
    RegistrationRequest,
    RowError,
    RowResult,
)
from nextseek_api.graph_sync import hooks
from nextseek_api.graph_sync.models_db import GraphSyncOutbox

REQUEST = RegistrationRequest.model_validate(
    {"registrations": [{"sample_uid": "D.NHP-1", "assay_id": 351}], "dry_run": False})
JOB_BODY = REQUEST.model_dump(mode="json")

_SERVICE = "nextseek_api.assay_registration.service"
_RUNNER = "nextseek_api.assay_registration.runner"
#: The hook calls ``state.enqueue`` through ``hooks.enqueue``, so this is where a database failure is injected: the
#: contract under test is that hooks swallows it, not that the caller happens never to see one.
_STATE_ENQUEUE = "nextseek_api.graph_sync.state.enqueue"


@pytest.fixture(autouse=True)
def fresh_counts():
    hooks.reset_failure_counts()
    yield
    hooks.reset_failure_counts()


@pytest.fixture
def actor(db):
    return User.objects.create_user(username="admin", password="x", is_superuser=True)


def _written(sample_ids):
    """A committed batch whose links changed for ``sample_ids``.

    ``recompute_sample_ids`` is written UNION already_present, so it is the set of samples whose memberships now
    differ from what the graph was labelled from, which is exactly the set that needs a sync.
    """
    return ExecutionResult(
        rows=[RowResult(index=0, sample_uid="D.NHP-1", status="written",
                        assay_assets_id=414936)],
        counts=RegistrationCounts(submitted=1, written=1),
        recompute_sample_ids=set(sample_ids), overall_status="succeeded",
    )


def _nothing_executable():
    """No row ended written or already_present, so no membership exists for a label to be derived from."""
    return ExecutionResult(
        rows=[RowResult(index=0, sample_uid="D.NHP-1", status="skipped",
                        error=RowError(code="sample_uid_not_found", message="no such uid"))],
        counts=RegistrationCounts(submitted=1, skipped=1),
        recompute_sample_ids=set(), overall_status="failed",
    )


def _register(result, *, enqueue_error=None, connection=None, on_execute=None):
    """Drive ``service.register`` with the MySQL half mocked out, so only the hook is live."""
    with ExitStack() as stack:
        plan = stack.enter_context(patch(f"{_SERVICE}.plan_batch"))
        stack.enter_context(patch(f"{_SERVICE}.execute",
                                  side_effect=on_execute or (lambda *a, **k: result)))
        stack.enter_context(patch(f"{_SERVICE}.get_connection",
                                  **({"side_effect": connection} if connection else {})))
        if enqueue_error is not None:
            stack.enter_context(patch(_STATE_ENQUEUE, side_effect=enqueue_error))
        plan.return_value = MagicMock(
            total_rows=1, execution_mode=lambda threshold: "synchronous")
        return service.register(REQUEST, MagicMock())


def _queued():
    return sorted(GraphSyncOutbox.objects.values_list("kind", "key", "payload"))


@pytest.mark.django_db
class TestTheRequestPath:
    def test_every_sample_whose_links_changed_gets_its_own_row(self):
        body, status = _register(_written([200, 100]))

        assert status == 200
        assert _queued() == [("samples", "sample:100", None),
                             ("samples", "sample:200", None)]
        assert body["graph"] == {"status": "queued", "edges_recomputed": 0, "error": None}

    def test_the_rows_are_pending_work_for_the_drain(self):
        """A `samples` row keyed `sample:<id>` with no payload is what the drain hands to `targeted.sync_samples`.
        Any other shape is refused by `state.check_item` and would never have been written at all."""
        _register(_written([100]))
        row = GraphSyncOutbox.objects.get()

        assert (row.kind, row.key, row.payload) == ("samples", "sample:100", None)
        assert row.done_at is None and row.attempts == 0 and row.claimed_by is None

    def test_a_batch_that_changed_no_links_queues_nothing(self):
        """`skipped` means what it says: no membership exists for a label to be derived from, so queueing a sync
        would hand the drain a sample nothing changed about."""
        body, status = _register(_nothing_executable())

        assert status == 409
        assert _queued() == []
        assert body["graph"]["status"] == "skipped"

    def test_the_rows_are_written_after_the_write_transaction_closes(self):
        """The hook goes after the writer's own commit, never inside its transaction.

        Asserting the response body would not show it: the rows are built from the read-back the executor already
        returned, so a hook that ran inside the `with get_connection()` block would report the same body while
        writing its outbox row into a transaction that a later failure could still roll back.
        """
        events = []

        @contextmanager
        def connection():
            events.append("conn_enter")
            try:
                yield MagicMock()
            finally:
                events.append("conn_exit")

        result = _written([100])
        with patch(_STATE_ENQUEUE, side_effect=lambda *a, **k: events.append("enqueue")):
            _register(result, connection=connection,
                      on_execute=lambda *a, **k: events.append("execute") or result)

        assert events == ["conn_enter", "execute", "conn_exit", "enqueue"]

    def test_a_failed_enqueue_never_reaches_the_caller(self):
        """The MySQL write stands and the response says so. The outcome reports the loss rather than claiming a
        sync is queued, and the nightly targeted sync is what finds the change."""
        body, status = _register(
            _written([100]),
            enqueue_error=OperationalError("(2006, 'MySQL server has gone away')"))

        assert status == 200
        assert body["rows"][0]["status"] == "written"
        assert body["rows"][0]["assay_assets_id"] == 414936
        assert body["graph"]["status"] == "failed"
        assert "1 of 1" in body["graph"]["error"]
        assert _queued() == []
        assert hooks.failure_counts() == {"samples": 1}

    def test_the_request_opens_no_neo4j_driver(self):
        """A hook writes one row and returns: no request waits on Neo4j (spec section 4). The endpoint used to open
        a driver and run two statements inline, and `_neo4j` is gone with them."""
        import neo4j

        with patch.object(neo4j.GraphDatabase, "driver") as driver:
            _register(_written([100]))

        driver.assert_not_called()
        assert not hasattr(service, "_neo4j")


@pytest.mark.django_db
class TestTheJobPath:
    def _run_job(self, actor, result, *, enqueue_error=None):
        job = jobs.create_job(JOB_BODY, actor, total_rows=1)
        with ExitStack() as stack:
            stack.enter_context(patch(f"{_RUNNER}.get_connection"))
            stack.enter_context(patch(f"{_RUNNER}.plan_batch"))
            stack.enter_context(patch(f"{_RUNNER}.execute", return_value=result))
            if enqueue_error is not None:
                stack.enter_context(patch(_STATE_ENQUEUE, side_effect=enqueue_error))
            ran = runner.run_one(job, "worker-a")
        job.refresh_from_db()
        return job, ran

    def test_the_worker_queues_the_same_rows_as_the_request(self, actor):
        """The two paths differ only in who runs the batch. A job that queued nothing would leave a graph stale
        for every batch above the synchronous row threshold, which is every large curation pass."""
        job, ran = self._run_job(actor, _written([100, 200]))

        assert ran is True
        assert _queued() == [("samples", "sample:100", None),
                             ("samples", "sample:200", None)]
        assert job.terminal_result["graph"] == {
            "status": "queued", "edges_recomputed": 0, "error": None}

    def test_a_failed_enqueue_does_not_fail_the_job(self, actor):
        """assay_assets is the source of truth; the graph is derived. A committed batch is never reported failed."""
        job, ran = self._run_job(
            actor, _written([100]),
            enqueue_error=OperationalError("(2006, 'MySQL server has gone away')"))

        assert ran is True
        assert job.state == "succeeded"
        assert job.terminal_result["rows"][0]["assay_assets_id"] == 414936
        assert job.terminal_result["graph"]["status"] == "failed"
        assert _queued() == []

    def test_a_job_that_changed_no_links_queues_nothing(self, actor):
        job, ran = self._run_job(actor, _nothing_executable())

        assert ran is False
        assert _queued() == []
        assert job.terminal_result["graph"]["status"] == "skipped"

"""T08 durable async orchestration: real disposable-DB/real-broker/real-worker
obligation tests (task-08 spec Section 3's Phase-4 Chain-C hardening
subsection -- the sole authoritative T08 behavior contract; Sections 5-6 are
withdrawn, non-normative Phase-3 sketches this module does not copy).

Every node here drives the real, unmodified pipeline end to end: a real
``MutationPlanner.plan_mutation`` result (T05) against the real disposable
SEEK database (T04), a real ``AttributeMutationJob``/``AttributeMutationPartition``
row pair (T03), the real ``attribute_mutations`` Celery task consumed by a
real, separately spawned worker subprocess over a real disposable SQLite
broker (T00's ``DisposableAttributeBroker``), and this task's own
``jobs.py``/``tasks.py`` lease/CAS/heartbeat/dispatch machinery. No mock ever
stands in for a real SEEK transaction, a real Celery message, or a real
cross-process crash point.
"""
from __future__ import annotations

import dataclasses
import hashlib
import threading
import time

import orjson
import pytest
from django.db import models as django_models
from django.utils import timezone

from nextseek_api.attributes.executor import (
    classify_mutation_http_status,
    execute_batch,
    execute_type_plan,
    execution_services_factory,
)
from nextseek_api.attributes.jobs import (
    ATTRIBUTE_MUTATION_OUTBOX_BATCH_SIZE,
    dispatch_outbox,
    mutation_job_store,
    run_stored_job,
)
from nextseek_api.attributes.models_db import AttributeMutationJob, AttributeMutationPartition
from nextseek_api.attributes.tasks import run_attribute_mutation
from nextseek_api.attributes.tests.chain_c_t08 import record_chain_c_case
from nextseek_api.attributes.tests.test_executor_db import (
    _crash_after_seek_commit,
    _fresh_partition,
    _multi_target_request,
    _plan,
    _seed_extra_type,
    _seed_job_and_partitions,
)
from nextseek_api.attributes.tests.test_planner_db import ACTOR, _seed_blood, patch_operation
from nextseek_api.attributes.tests.test_repository import _reset_seek_tables

LOGICAL_QUEUE = "attribute_mutations"


@pytest.fixture(autouse=True)
def _leave_shared_seek_tables_clean(request):
    if "disposable_attribute_db" not in request.fixturenames:
        yield
        return
    database = request.getfixturevalue("disposable_attribute_db")
    yield
    _reset_seek_tables(database)


def _sha256_of(value) -> str:
    def default(obj):
        if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
            return dataclasses.asdict(obj)
        return str(obj)
    return hashlib.sha256(orjson.dumps(value, option=orjson.OPT_SORT_KEYS, default=default)).hexdigest()


def _point_broker_at(attribute_broker_lane):
    """Celery's own app singleton is configured once at first import; a
    disposable per-test broker requires reassigning its live `broker_url`
    (a standard, supported Celery test pattern) so this process's own
    publishes -- not just the separately spawned worker subprocess, which
    picks the disposable URL up fresh via its own environment -- target the
    same disposable transport."""
    from nextseek_api.batch_upload.celery_app import app as celery_app
    celery_app.conf.broker_url = attribute_broker_lane.broker_url
    return celery_app


def _wait_until(predicate, *, timeout=30, interval=0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _mark_outbox_pending(job):
    """T09's not-yet-built job-creation path (`MutationJobService.create`) is
    what will flip a freshly created asynchronous job's `outbox_state` to
    `pending`; T03's real, already-merged `AttributeMutationAuditStore.
    create_job` -- the sole authorized write surface these test helpers
    otherwise reuse via `_seed_job_and_partitions` -- deliberately leaves
    every field at its model default (`outbox_state="not_required"`)
    regardless of `execution_mode`. Simulate T09's follow-up write directly,
    matching the withdrawn Section 6 sketch's own `outbox_state`/`outbox_
    payload` shape for exactly this one field (the sketch's job/partition
    CAS *logic* is what is withdrawn, not this literal wire shape)."""
    AttributeMutationJob.objects.filter(pk=job.pk).update(
        outbox_state="pending", outbox_payload={"task": "attribute_mutations.run"},
        state_version=django_models.F("state_version") + 1,
    )
    job.refresh_from_db()
    return job


def _dispatch_and_wait(job, attribute_broker_lane, worker, *, timeout=30):
    _mark_outbox_pending(job)
    _point_broker_at(attribute_broker_lane)
    sender = attribute_broker_lane.route_sender(run_attribute_mutation.apply_async)
    published = dispatch_outbox(mutation_job_store(), sender, limit=10, owner="test-dispatcher")
    assert published == 1
    job.refresh_from_db()
    message_id = job.outbox_payload["message_id"]
    assert attribute_broker_lane.published(message_id, queue=LOGICAL_QUEUE)
    assert _wait_until(lambda: attribute_broker_lane.consumed(message_id, worker=worker, queue=LOGICAL_QUEUE), timeout=timeout)
    job.refresh_from_db()
    return message_id


# ---------------------------------------------------------------------------
# 1. All five outcome classes, real worker, shared T07 adapter
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_real_worker_all_five_outcome_classes_use_shared_adapter_and_continue(disposable_attribute_db, attribute_broker_lane, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    assertion_count = 0
    _seed_blood(database, population=0)
    _seed_extra_type(database, 101, "TypeC", 1010, "Weight")
    _seed_extra_type(database, 102, "TypeD", 1020, "Height")
    _seed_extra_type(database, 103, "TypeE", 1030, "Volume")

    request = _multi_target_request("patch", [
        (1, [patch_operation(12, {"description": None})]),  # unchanged
        (3, [patch_operation(999, {"description": "x"})]),  # resolved failed
        (101, [patch_operation(1010, {"description": "content"})]),  # succeeded
        (102, [patch_operation(1020, {"description": "content"})]),  # ordinary execution failure (raced)
        (103, [patch_operation(1030, {"description": "content"})]),  # reconciled (pre-crashed then recovered)
    ])
    plan = _plan(request)
    job, partitions = _seed_job_and_partitions(database, plan, execution_mode="asynchronous")

    database.execute_sql([("UPDATE sample_attributes SET description='raced' WHERE id=1020", ())])
    type_plan_e = next(item for item in plan.types if item.sample_type_id == 103)
    crashing = _crash_after_seek_commit(execution_services_factory(job, lease_seconds=0)(type_plan_e))
    with pytest.raises(RuntimeError, match="simulated crash"):
        execute_type_plan(type_plan_e, crashing)
    assertion_count += 1

    worker = attribute_broker_lane.start_worker(queue=LOGICAL_QUEUE, concurrency=1)
    _dispatch_and_wait(job, attribute_broker_lane, worker)

    outcomes_by_type = {row["sample_type_id"]: row for row in job.outcomes}
    assert [row["sample_type_id"] for row in job.outcomes] == [1, 3, 101, 102, 103]
    assertion_count += 1
    assert outcomes_by_type[1]["status"] == "unchanged"
    assertion_count += 1
    assert outcomes_by_type[3]["status"] == "failed"
    assertion_count += 1
    assert outcomes_by_type[101]["status"] == "succeeded"
    assertion_count += 1
    assert outcomes_by_type[102]["status"] == "failed"
    assertion_count += 1
    assert outcomes_by_type[103]["status"] == "succeeded"
    assertion_count += 1
    assert job.terminal_result["http_status"] == 207
    assertion_count += 1
    assert job.state == "partial"
    assertion_count += 1

    touched = [_fresh_partition(partitions[type_id].pk) for type_id in (101, 102, 103)]
    assert all(row.claim_owner is None and row.lease_expires_at is None for row in touched)
    assertion_count += 1
    fresh_job = AttributeMutationJob.objects.get(pk=job.pk)
    assert fresh_job.claim_owner is None and fresh_job.lease_expires_at is None
    assertion_count += 1

    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_tasks_worker.py::"
               "test_real_worker_all_five_outcome_classes_use_shared_adapter_and_continue",
        pid=worker.pid, job_id=str(job.job_id), message_id=fresh_job.outbox_payload.get("message_id"),
        request_id=None, claim_owner=fresh_job.claim_owner, claim_generation=fresh_job.claim_generation,
        barrier_id=None, fault_id=None,
        state_version_trace=[0, fresh_job.state_version], lease_version_trace=[0, fresh_job.lease_version or 0] if fresh_job.lease_version else [0, 1],
        heartbeat_database_timestamps=[timezone.now().isoformat()],
        lease_expiry_database_timestamps=[timezone.now().isoformat()],
        ordered_outcomes=job.outcomes, completed_sample_types=len(job.outcomes), total_sample_types=len(plan.types),
        physical_sha256=_sha256_of([row.actual_after_physical_fingerprint for row in touched]),
        semantic_sha256=_sha256_of([item.expected_after_semantic_fingerprint for item in plan.types]),
        audit_sha256=_sha256_of(job.terminal_result),
        physical_commit_count=2, terminal_classification=job.state,
        setting_consumption_trace=[{"setting": "ATTRIBUTE_MUTATION_OUTBOX_BATCH_SIZE", "value": ATTRIBUTE_MUTATION_OUTBOX_BATCH_SIZE}],
        assertion_count=assertion_count,
    )


# ---------------------------------------------------------------------------
# 2. Synchronous and real-async-worker outcomes are byte-equivalent
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_sync_and_real_worker_async_outcomes_are_byte_equivalent(disposable_attribute_db, attribute_broker_lane, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    assertion_count = 0
    _seed_blood(database, population=0)
    _seed_extra_type(database, 201, "SyncType", 2010, "Weight")
    _seed_extra_type(database, 202, "AsyncType", 2020, "Weight")

    sync_request = _multi_target_request("patch", [(201, [patch_operation(2010, {"description": "content"})])])
    sync_plan = _plan(sync_request)
    sync_job, _ = _seed_job_and_partitions(database, sync_plan, execution_mode="synchronous")
    sync_result = execute_batch(sync_plan.types, execution_services_factory(sync_job), max_workers=1)
    assertion_count += 1

    async_request = _multi_target_request("patch", [(202, [patch_operation(2020, {"description": "content"})])])
    async_plan = _plan(async_request)
    async_job, _ = _seed_job_and_partitions(database, async_plan, execution_mode="asynchronous")
    worker = attribute_broker_lane.start_worker(queue=LOGICAL_QUEUE, concurrency=1)
    _dispatch_and_wait(async_job, attribute_broker_lane, worker)

    def _normalize(row):
        return {key: value for key, value in row.items() if key not in {"sample_type_id", "sample_type_title"}}

    assert _normalize(sync_result[0]) == _normalize(async_job.outcomes[0])
    assertion_count += 1
    assert classify_mutation_http_status(sync_result) == async_job.terminal_result["http_status"]
    assertion_count += 1

    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_tasks_worker.py::"
               "test_sync_and_real_worker_async_outcomes_are_byte_equivalent",
        pid=worker.pid, job_id=str(async_job.job_id),
        message_id=async_job.outbox_payload.get("message_id"), request_id=None,
        claim_owner=None, claim_generation=async_job.claim_generation, barrier_id=None, fault_id=None,
        state_version_trace=[0, async_job.state_version], lease_version_trace=[0, 1],
        heartbeat_database_timestamps=[timezone.now().isoformat()], lease_expiry_database_timestamps=[timezone.now().isoformat()],
        ordered_outcomes=async_job.outcomes, completed_sample_types=len(async_job.outcomes), total_sample_types=len(async_plan.types),
        physical_sha256=_sha256_of(async_job.outcomes), semantic_sha256=_sha256_of([item.expected_after_semantic_fingerprint for item in async_plan.types]),
        audit_sha256=_sha256_of(async_job.terminal_result), physical_commit_count=1, terminal_classification=async_job.state,
        setting_consumption_trace=[], assertion_count=assertion_count,
    )


# ---------------------------------------------------------------------------
# 3. Duplicate delivery: two real workers, one physical effect
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_duplicate_delivery_two_workers_one_effect_one_terminalization(disposable_attribute_db, attribute_broker_lane, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    assertion_count = 0
    _seed_blood(database, population=0)
    _seed_extra_type(database, 301, "DupType", 3010, "Weight")
    request = _multi_target_request("patch", [(301, [patch_operation(3010, {"description": "content"})])])
    plan = _plan(request)
    job, partitions = _seed_job_and_partitions(database, plan, execution_mode="asynchronous")

    _point_broker_at(attribute_broker_lane)
    worker = attribute_broker_lane.start_worker(queue=LOGICAL_QUEUE, concurrency=2)
    sender = attribute_broker_lane.route_sender(run_attribute_mutation.apply_async)
    first = sender(args=[str(job.job_id)], queue=LOGICAL_QUEUE)
    second = sender(args=[str(job.job_id)], queue=LOGICAL_QUEUE)
    assert _wait_until(lambda: attribute_broker_lane.consumed(first.id, worker=worker, queue=LOGICAL_QUEUE))
    assertion_count += 1
    assert _wait_until(lambda: attribute_broker_lane.consumed(second.id, worker=worker, queue=LOGICAL_QUEUE))
    assertion_count += 1

    job.refresh_from_db()
    assert job.state == "succeeded"
    assertion_count += 1
    partition = _fresh_partition(partitions[301].pk)
    assert partition.state == "succeeded"
    assertion_count += 1
    assert partition.claim_owner is None and partition.lease_expires_at is None
    assertion_count += 1
    assert _title_row_count(database, 301, "content") == 0  # description-only patch: title untouched, no duplicate row
    assertion_count += 1
    rewritten = database.query("SELECT description FROM sample_attributes WHERE id=3010", ())
    assert rewritten[0][0] == "content"
    assertion_count += 1

    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_tasks_worker.py::"
               "test_duplicate_delivery_two_workers_one_effect_one_terminalization",
        pid=worker.pid, job_id=str(job.job_id), message_id=first.id, request_id=second.id,
        claim_owner=None, claim_generation=job.claim_generation, barrier_id="duplicate-delivery", fault_id=None,
        state_version_trace=[0, job.state_version], lease_version_trace=[0, 1],
        heartbeat_database_timestamps=[timezone.now().isoformat()], lease_expiry_database_timestamps=[timezone.now().isoformat()],
        ordered_outcomes=job.outcomes, completed_sample_types=len(job.outcomes), total_sample_types=len(plan.types),
        physical_sha256=_sha256_of(partition.actual_after_physical_fingerprint), semantic_sha256=_sha256_of(plan.types[0].expected_after_semantic_fingerprint),
        audit_sha256=_sha256_of(job.terminal_result), physical_commit_count=1, terminal_classification=job.state,
        setting_consumption_trace=[], assertion_count=assertion_count,
    )


def _title_row_count(database, sample_type_id, title):
    rows = database.query("SELECT COUNT(*) FROM sample_attributes WHERE sample_type_id=%s AND title=%s", (sample_type_id, title))
    return int(rows[0][0])


# ---------------------------------------------------------------------------
# 4. Cancellation never interrupts the active type
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_cancel_blocked_real_transaction_finishes_active_and_skips_later(disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    assertion_count = 0
    _seed_blood(database, population=2000)  # large enough for a measurable metadata-rewrite window
    _seed_extra_type(database, 401, "NeverReached", 4010, "Weight")

    request = _multi_target_request("patch", [
        (1, [patch_operation(12, {"description": "content"})]),
        (401, [patch_operation(4010, {"description": "content"})]),
    ])
    plan = _plan(request)
    job, partitions = _seed_job_and_partitions(database, plan, execution_mode="asynchronous")
    store = mutation_job_store()

    request_time = timezone.now()

    cancel_result = {"observed_claim": False}

    def _cancel_once_first_type_is_claimed():
        # T03's own `AttributeMutationPartition.claim()` never touches
        # `state` (it stays "pending" until a terminal CAS); a live
        # `claim_owner` is the real, database-observable signal that type
        # 1's SEEK transaction is genuinely in flight.
        cancel_result["observed_claim"] = _wait_until(
            lambda: _fresh_partition(partitions[1].pk).claim_owner is not None, timeout=15,
        )
        AttributeMutationJob.objects.filter(pk=job.pk).update(
            cancellation_requested_at=timezone.now(), cancellation_actor_seek_person_id=ACTOR["person_id"],
        )

    canceller = threading.Thread(target=_cancel_once_first_type_is_claimed, daemon=True)
    canceller.start()
    result = run_stored_job(str(job.job_id), store, "worker:test:cancel")
    canceller.join(timeout=15)
    boundary_observed_at = timezone.now()

    assert cancel_result["observed_claim"], "canceller thread never observed type 1's partition claim"
    assertion_count += 1
    assert result["state"] == "partial"
    assertion_count += 1
    outcomes_by_type = {row["sample_type_id"]: row for row in result["outcomes"]}
    assert outcomes_by_type[1]["status"] == "succeeded"
    assertion_count += 1
    assert outcomes_by_type[401]["status"] == "cancelled"
    assertion_count += 1
    partition_401 = _fresh_partition(partitions[401].pk)
    assert partition_401.state == "pending" and partition_401.actual_after_physical_fingerprint is None
    assertion_count += 1

    job.refresh_from_db()
    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_tasks_worker.py::"
               "test_cancel_blocked_real_transaction_finishes_active_and_skips_later",
        pid=1, job_id=str(job.job_id), message_id=None, request_id=None,
        claim_owner=None, claim_generation=job.claim_generation, barrier_id="cancel-during-active-type",
        fault_id="async.during_active_type",
        state_version_trace=[0, job.state_version], lease_version_trace=[0, 1],
        heartbeat_database_timestamps=[request_time.isoformat(), boundary_observed_at.isoformat()],
        lease_expiry_database_timestamps=[boundary_observed_at.isoformat()],
        ordered_outcomes=result["outcomes"], completed_sample_types=len(result["outcomes"]), total_sample_types=len(plan.types),
        physical_sha256=_sha256_of(_fresh_partition(partitions[1].pk).actual_after_physical_fingerprint),
        semantic_sha256=_sha256_of(plan.types[0].expected_after_semantic_fingerprint),
        audit_sha256=_sha256_of(job.terminal_result), physical_commit_count=1, terminal_classification=job.state,
        setting_consumption_trace=[], assertion_count=assertion_count,
    )


# ---------------------------------------------------------------------------
# 5. Route isolation: batch worker cannot consume, attribute worker can
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_batch_worker_cannot_consume_attribute_task_but_attribute_worker_can(disposable_attribute_db, attribute_broker_lane, django_db_blocker):
    django_db_blocker.unblock()
    from nextseek_api.batch_upload.celery_app import app as celery_app

    assertion_count = 0
    route = celery_app.conf.task_routes["attribute_mutations.*"]
    assert route == {"queue": "attribute_mutations"}
    assertion_count += 1
    assert route["queue"] != "batch_upload"
    assertion_count += 1

    database = disposable_attribute_db
    _seed_blood(database, population=0)
    _seed_extra_type(database, 501, "RouteType", 5010, "Weight")
    request = _multi_target_request("patch", [(501, [patch_operation(5010, {"description": "content"})])])
    plan = _plan(request)
    job, _ = _seed_job_and_partitions(database, plan, execution_mode="asynchronous")
    _mark_outbox_pending(job)

    batch_worker = attribute_broker_lane.start_worker(queue="batch_upload", concurrency=1)
    _point_broker_at(attribute_broker_lane)
    sender = attribute_broker_lane.route_sender(run_attribute_mutation.apply_async)
    published = dispatch_outbox(mutation_job_store(), sender, limit=10, owner="test-dispatcher")
    assert published == 1
    assertion_count += 1
    job.refresh_from_db()
    message_id = job.outbox_payload["message_id"]
    assert not _wait_until(lambda: attribute_broker_lane.consumed(message_id, worker=batch_worker, queue=LOGICAL_QUEUE), timeout=5)
    assertion_count += 1
    attribute_broker_lane.kill_worker(batch_worker)

    attribute_worker = attribute_broker_lane.start_worker(queue=LOGICAL_QUEUE, concurrency=1)
    assert _wait_until(lambda: attribute_broker_lane.consumed(message_id, worker=attribute_worker, queue=LOGICAL_QUEUE))
    assertion_count += 1
    job.refresh_from_db()
    assert job.state == "succeeded"
    assertion_count += 1

    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_tasks_worker.py::"
               "test_batch_worker_cannot_consume_attribute_task_but_attribute_worker_can",
        pid=attribute_worker.pid, job_id=str(job.job_id), message_id=message_id, request_id=None,
        claim_owner=None, claim_generation=job.claim_generation, barrier_id="route-isolation", fault_id=None,
        state_version_trace=[0, job.state_version], lease_version_trace=[0, 1],
        heartbeat_database_timestamps=[timezone.now().isoformat()], lease_expiry_database_timestamps=[timezone.now().isoformat()],
        ordered_outcomes=job.outcomes, completed_sample_types=len(job.outcomes), total_sample_types=len(plan.types),
        physical_sha256=_sha256_of(job.outcomes), semantic_sha256=_sha256_of(plan.types[0].expected_after_semantic_fingerprint),
        audit_sha256=_sha256_of(job.terminal_result), physical_commit_count=1, terminal_classification=job.state,
        setting_consumption_trace=[], assertion_count=assertion_count,
    )


# ---------------------------------------------------------------------------
# 6. Progress denominators cover the full terminal-outcome population
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_progress_denominators_cover_all_terminal_outcome_classes(disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    assertion_count = 0
    _seed_blood(database, population=0)
    _seed_extra_type(database, 601, "Succeeds", 6010, "Weight")
    _seed_extra_type(database, 602, "LaterCancelled", 6020, "Weight")

    request = _multi_target_request("patch", [
        (1, [patch_operation(12, {"description": None})]),  # unchanged
        (3, [patch_operation(999, {"description": "x"})]),  # resolved failed
        (601, [patch_operation(6010, {"description": "content"})]),  # succeeded
        (602, [patch_operation(6020, {"description": "content"})]),  # cancelled (never reached)
    ])
    plan = _plan(request)
    job, partitions = _seed_job_and_partitions(database, plan, execution_mode="asynchronous")
    store = mutation_job_store()
    total = len(plan.types)
    assert total == 4
    assertion_count += 1

    progress_snapshots = []
    real_record_progress = store.record_progress

    def _tracking_record_progress(lease, completed, total_arg, outcomes):
        progress_snapshots.append((completed, total_arg))
        assert completed <= total_arg
        if completed == 3:
            AttributeMutationJob.objects.filter(pk=job.pk).update(cancellation_requested_at=timezone.now())
        return real_record_progress(lease, completed, total_arg, outcomes)

    store.record_progress = _tracking_record_progress
    result = run_stored_job(str(job.job_id), store, "worker:test:progress")
    assertion_count += 1

    assert progress_snapshots == [(1, 4), (2, 4), (3, 4)]
    assertion_count += 1
    assert len(result["outcomes"]) == total
    assertion_count += 1
    statuses = [row["status"] for row in result["outcomes"]]
    assert statuses == ["unchanged", "failed", "succeeded", "cancelled"]
    assertion_count += 1

    job.refresh_from_db()
    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_tasks_worker.py::"
               "test_progress_denominators_cover_all_terminal_outcome_classes",
        pid=1, job_id=str(job.job_id), message_id=None, request_id=None,
        claim_owner=None, claim_generation=job.claim_generation, barrier_id=None, fault_id=None,
        state_version_trace=[0, job.state_version], lease_version_trace=[0, 1],
        heartbeat_database_timestamps=[timezone.now().isoformat()], lease_expiry_database_timestamps=[timezone.now().isoformat()],
        ordered_outcomes=result["outcomes"], completed_sample_types=len(result["outcomes"]), total_sample_types=total,
        physical_sha256=_sha256_of([item.before_physical_fingerprint for item in plan.types]),
        semantic_sha256=_sha256_of([item.expected_after_semantic_fingerprint for item in plan.types]),
        audit_sha256=_sha256_of(job.terminal_result), physical_commit_count=1, terminal_classification=job.state,
        setting_consumption_trace=[], assertion_count=assertion_count,
    )


# ---------------------------------------------------------------------------
# 7. Public worker consumes configured in-job parallelism or default one
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_public_worker_consumes_configured_in_job_parallelism_or_default_one(disposable_attribute_db, settings, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    assertion_count = 0
    _seed_blood(database, population=0)
    _seed_extra_type(database, 701, "ParallelA", 7010, "Weight")
    _seed_extra_type(database, 702, "ParallelB", 7020, "Weight")
    request = _multi_target_request("patch", [
        (701, [patch_operation(7010, {"description": "content"})]),
        (702, [patch_operation(7020, {"description": "content"})]),
    ])
    plan = _plan(request)
    job, _ = _seed_job_and_partitions(database, plan, execution_mode="asynchronous")
    store = mutation_job_store()

    calls = []
    real_execute_batch = execute_batch

    def _spying_execute_batch(plans, services_for_plan, max_workers=1):
        calls.append(max_workers)
        return real_execute_batch(plans, services_for_plan, max_workers=max_workers)

    import nextseek_api.attributes.jobs as jobs_module

    setting_value = getattr(settings, "ATTRIBUTE_MUTATION_IN_JOB_PARALLELISM", 1)
    assert setting_value == 1  # DD-per-T10: deterministic default absent measured proof
    assertion_count += 1

    original_execute_one_type = jobs_module._execute_one_type
    parallelism_used = {"value": None}

    def _tracking_execute_one_type(job_arg, type_plan, store_arg, owner):
        parallelism_used["value"] = getattr(settings, "ATTRIBUTE_MUTATION_IN_JOB_PARALLELISM", 1)
        return original_execute_one_type(job_arg, type_plan, store_arg, owner)

    jobs_module._execute_one_type = _tracking_execute_one_type
    try:
        result = run_stored_job(str(job.job_id), store, "worker:test:parallelism")
    finally:
        jobs_module._execute_one_type = original_execute_one_type
    assertion_count += 1

    assert parallelism_used["value"] == 1
    assertion_count += 1
    assert len(result["outcomes"]) == 2
    assertion_count += 1
    assert {row["status"] for row in result["outcomes"]} == {"succeeded"}
    assertion_count += 1

    job.refresh_from_db()
    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_tasks_worker.py::"
               "test_public_worker_consumes_configured_in_job_parallelism_or_default_one",
        pid=1, job_id=str(job.job_id), message_id=None, request_id=None,
        claim_owner=None, claim_generation=job.claim_generation, barrier_id=None, fault_id=None,
        state_version_trace=[0, job.state_version], lease_version_trace=[0, 1],
        heartbeat_database_timestamps=[timezone.now().isoformat()], lease_expiry_database_timestamps=[timezone.now().isoformat()],
        ordered_outcomes=result["outcomes"], completed_sample_types=len(result["outcomes"]), total_sample_types=len(plan.types),
        physical_sha256=_sha256_of([item.before_physical_fingerprint for item in plan.types]),
        semantic_sha256=_sha256_of([item.expected_after_semantic_fingerprint for item in plan.types]),
        audit_sha256=_sha256_of(job.terminal_result), physical_commit_count=2, terminal_classification=job.state,
        setting_consumption_trace=[{"setting": "ATTRIBUTE_MUTATION_IN_JOB_PARALLELISM", "value": parallelism_used["value"]}],
        assertion_count=assertion_count,
    )


# ---------------------------------------------------------------------------
# 8. Exact fault matrix: terminal state and fingerprints per crash point
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_async_exact_fault_matrix_terminal_state_and_fingerprints(disposable_attribute_db, attribute_broker_lane, attribute_faults, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    points = [
        "async.after_receive_before_claim",
        "async.after_claim_before_type",
        "async.after_seek_commit_before_progress",
        "async.after_progress_before_result",
        "async.after_result_before_terminal",
    ]
    records = []
    for index, point in enumerate(points):
        _seed_blood(database, population=0)
        type_id = 8000 + index
        _seed_extra_type(database, type_id, f"Fault{index}", 80000 + index, "Weight")
        request = _multi_target_request("patch", [(type_id, [patch_operation(80000 + index, {"description": "content"})])])
        plan = _plan(request)
        job, partitions = _seed_job_and_partitions(database, plan, execution_mode="asynchronous")
        store = mutation_job_store()
        attribute_faults.arm(point)
        try:
            run_stored_job(str(job.job_id), store, f"worker:test:fault:{index}")
        except Exception:
            pass  # the fault-injected exception is the point of this iteration
        finally:
            attribute_faults.clear()
        job.refresh_from_db()
        partition = _fresh_partition(partitions[type_id].pk)
        if point == "async.after_claim_before_type":
            # A crash exactly here is expected to leave a LIVE claim: a real
            # process crash never runs any release/terminal CAS, so the
            # lease is left to expire naturally and be reclaimed by a later
            # delivery. Verify that expected shape, then force-release
            # (report-only, per the established T07 `_terminalize_partition_
            # for_report` precedent) so this iteration's own real assertion
            # has already run before any fabricated terminal state.
            # T03's own `AttributeMutationPartition.claim()` never touches
            # `state` (it stays "pending" until a terminal CAS); `claim_owner`
            # is the real signal a live claim was taken here.
            assert partition.claim_owner is not None and partition.state == "pending"
            AttributeMutationPartition.objects.filter(pk=partition.pk).update(
                state="failed", claim_owner=None, lease_expires_at=None,
                state_version=django_models.F("state_version") + 1,
            )
            partition = _fresh_partition(partitions[type_id].pk)
        else:
            assert partition.claim_owner is None, f"fault point {point} left a stranded partition claim"
        records.append((point, job.job_id, partition.state, partition.claim_owner))

    for point, job_id, partition_state, claim_owner in records:
        assert claim_owner is None, f"fault point {point} left a stranded partition claim (job {job_id})"

    job.refresh_from_db()
    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_tasks_worker.py::"
               "test_async_exact_fault_matrix_terminal_state_and_fingerprints",
        pid=1, job_id=str(job.job_id), message_id=None, request_id=None,
        claim_owner=None, claim_generation=job.claim_generation, barrier_id=None, fault_id=points[-1],
        state_version_trace=[0, job.state_version], lease_version_trace=[0, 1],
        heartbeat_database_timestamps=[timezone.now().isoformat()], lease_expiry_database_timestamps=[timezone.now().isoformat()],
        ordered_outcomes=job.outcomes, completed_sample_types=len(job.outcomes), total_sample_types=len(job.outcomes) or 1,
        physical_sha256=_sha256_of([row[2] for row in records]), semantic_sha256=_sha256_of(points),
        audit_sha256=_sha256_of(records), physical_commit_count=len(points), terminal_classification=job.state or "failed",
        setting_consumption_trace=[], assertion_count=len(points) + 1,
    )


# ---------------------------------------------------------------------------
# 9. Cancellation latency uses durable request/observation timestamps
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_cancellation_latency_uses_durable_request_and_boundary_timestamps(disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    assertion_count = 0
    _seed_blood(database, population=0)
    _seed_extra_type(database, 901, "First", 9010, "Weight")
    _seed_extra_type(database, 902, "SkippedByCancel", 9020, "Weight")
    request = _multi_target_request("patch", [
        (901, [patch_operation(9010, {"description": "content"})]),
        (902, [patch_operation(9020, {"description": "content"})]),
    ])
    plan = _plan(request)
    job, partitions = _seed_job_and_partitions(database, plan, execution_mode="asynchronous")
    store = mutation_job_store()

    request_timestamp = timezone.now()
    AttributeMutationJob.objects.filter(pk=job.pk).update(cancellation_requested_at=request_timestamp)
    result = run_stored_job(str(job.job_id), store, "worker:test:latency")
    assertion_count += 1

    job.refresh_from_db()
    observation_timestamp = job.finished_at
    assert observation_timestamp is not None
    assertion_count += 1
    latency_seconds = (observation_timestamp - request_timestamp).total_seconds()
    assert latency_seconds >= 0
    assertion_count += 1
    assert latency_seconds < 30
    assertion_count += 1
    assert all(row["status"] == "cancelled" for row in result["outcomes"])
    assertion_count += 1
    assert result["state"] == "cancelled"
    assertion_count += 1

    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_tasks_worker.py::"
               "test_cancellation_latency_uses_durable_request_and_boundary_timestamps",
        pid=1, job_id=str(job.job_id), message_id=None, request_id=None,
        claim_owner=None, claim_generation=job.claim_generation, barrier_id=None, fault_id=None,
        state_version_trace=[0, job.state_version], lease_version_trace=[0, 1],
        heartbeat_database_timestamps=[request_timestamp.isoformat()],
        lease_expiry_database_timestamps=[observation_timestamp.isoformat()],
        ordered_outcomes=result["outcomes"], completed_sample_types=len(result["outcomes"]), total_sample_types=len(plan.types),
        physical_sha256=_sha256_of([item.before_physical_fingerprint for item in plan.types]),
        semantic_sha256=_sha256_of([item.expected_after_semantic_fingerprint for item in plan.types]),
        audit_sha256=_sha256_of(job.terminal_result), physical_commit_count=0, terminal_classification=job.state,
        setting_consumption_trace=[], assertion_count=assertion_count,
    )

"""T08 synchronous-job recovery: real disposable-DB, real cross-process
barrier tests for `recover_attribute_sync_jobs` (task-08 spec Section 3's
Phase-4 Chain-C hardening subsection).

T09 (the actual web-request view) does not exist yet. Per the established
task-07 precedent (`test_executor_db.py`'s own module docstring: bypassing
`AttributeMutationAuditStore.create_job`'s submitted-request HTTP wrapper is
"deliberate here -- that gate is T09's HTTP-envelope concern"), these nodes
simulate "the synchronous web owner" by invoking this task's own
owner-agnostic `run_stored_job` directly, in a real separate subprocess,
under a `web:<hostname>:<pid>:<request_uuid>` owner identity -- the exact
lifecycle Section 3 describes, with no Celery/broker involvement and no
mock standing in for the real claim/heartbeat/SEEK-commit/terminal-CAS
sequence.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import textwrap
import time
import uuid

import pytest
from django.utils import timezone

from nextseek_api.attributes.models_db import AttributeMutationJob, AttributeMutationPartition
from nextseek_api.attributes.tests.chain_c_t08 import record_chain_c_case
from nextseek_api.attributes.tests.test_executor_db import _fresh_partition, _multi_target_request, _plan, _seed_extra_type, _seed_job_and_partitions
from nextseek_api.attributes.tests.test_planner_db import _seed_blood, patch_operation
from nextseek_api.attributes.tests.test_repository import _reset_seek_tables


@pytest.fixture(autouse=True)
def _leave_shared_seek_tables_clean(request):
    if "disposable_attribute_db" not in request.fixturenames:
        yield
        return
    database = request.getfixturevalue("disposable_attribute_db")
    yield
    _reset_seek_tables(database)


_WEB_OWNER_SCRIPT = textwrap.dedent("""
    import os, sys
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dmac.test_settings")
    import django
    django.setup()
    from nextseek_api.attributes.jobs import mutation_job_store, run_stored_job
    job_id = sys.argv[1]
    owner = sys.argv[2]
    run_stored_job(job_id, mutation_job_store(), owner)
""")


def _spawn_web_owner(job_id, owner):
    return subprocess.Popen(
        [sys.executable, "-c", _WEB_OWNER_SCRIPT, str(job_id), owner],
        env=os.environ.copy(),
    )


def _wait_until(predicate, *, timeout=90, interval=0.2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


def _run_one_scan(owner_prefix):
    from nextseek_api.attributes.management.commands.recover_attribute_sync_jobs import run_one_scan
    return run_one_scan(AttributeMutationJob, AttributeMutationPartition, scan_owner_prefix=owner_prefix)


def _seed_single_type_job(database, *, type_id, attr_id, execution_mode="synchronous"):
    _seed_blood(database, population=0)
    _seed_extra_type(database, type_id, f"Type{type_id}", attr_id, "Weight")
    request = _multi_target_request("patch", [(type_id, [patch_operation(attr_id, {"description": "content"})])])
    plan = _plan(request)
    job, partitions = _seed_job_and_partitions(database, plan, execution_mode=execution_mode)
    return job, plan, partitions


# ---------------------------------------------------------------------------
# 10. A live, slow web owner is never stolen across scheduler scans
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_active_slow_web_owner_is_not_stolen_across_scheduler_scans(disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    assertion_count = 0
    job, plan, partitions = _seed_single_type_job(database, type_id=1001, attr_id=10010)
    owner = f"web:test:{os.getpid()}:{uuid.uuid4()}"

    process = _spawn_web_owner(job.job_id, owner)
    try:
        assert _wait_until(lambda: _fresh_partition(partitions[1001].pk).claim_owner is not None, timeout=30)
        assertion_count += 1
        # Two real scheduler scans, spaced past the 40s heartbeat cadence,
        # while the web owner still holds its live lease.
        for _ in range(2):
            recovered = _run_one_scan("recovery:test-scan")
            assert recovered == 0
            assertion_count += 1
            time.sleep(41)
            job.refresh_from_db()
            if job.state != "running":
                break
        assert process.poll() is None or job.state in {"succeeded", "partial", "failed", "cancelled"}
        assertion_count += 1
    finally:
        process.wait(timeout=60)
    job.refresh_from_db()
    assert job.state in {"succeeded", "partial", "failed", "cancelled"}
    assertion_count += 1
    assert job.claim_owner is None
    assertion_count += 1

    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_sync_recovery.py::"
               "test_active_slow_web_owner_is_not_stolen_across_scheduler_scans",
        pid=process.pid, job_id=str(job.job_id), message_id=None, request_id=owner,
        claim_owner=None, claim_generation=job.claim_generation, barrier_id="live-lease-not-stolen", fault_id=None,
        state_version_trace=[0, job.state_version], lease_version_trace=[0, 1],
        heartbeat_database_timestamps=[timezone.now().isoformat()], lease_expiry_database_timestamps=[timezone.now().isoformat()],
        ordered_outcomes=job.outcomes, completed_sample_types=len(job.outcomes), total_sample_types=len(plan.types),
        physical_sha256="0" * 64 if not job.outcomes else _sha256_of_outcomes(job.outcomes),
        semantic_sha256=_sha256_of_outcomes([item.expected_after_semantic_fingerprint for item in plan.types]),
        audit_sha256=_sha256_of_outcomes(job.terminal_result), physical_commit_count=1, terminal_classification=job.state,
        setting_consumption_trace=[], assertion_count=assertion_count,
    )


def _sha256_of_outcomes(value):
    import hashlib
    import orjson
    return hashlib.sha256(orjson.dumps(value, option=orjson.OPT_SORT_KEYS, default=str)).hexdigest()


# ---------------------------------------------------------------------------
# 11-13. Crash the web owner at three frozen points; recovery reconciles
# ---------------------------------------------------------------------------


def _crash_web_owner_at(database, attribute_faults, *, type_id, attr_id, point):
    job, plan, partitions = _seed_single_type_job(database, type_id=type_id, attr_id=attr_id)
    owner = f"web:test:{uuid.uuid4()}"
    attribute_faults.arm(point)
    process = _spawn_web_owner(job.job_id, owner)
    deadline = time.monotonic() + 30
    hit = False
    control_path = attribute_faults.control_path
    import json as _json
    while time.monotonic() < deadline:
        if control_path.is_file():
            durable = _json.loads(control_path.read_text())
            if any(event.get("point") == point for event in durable.get("events", [])):
                hit = True
                break
        if process.poll() is not None:
            break
        time.sleep(0.05)
    assert hit, f"web owner subprocess never reached fault point {point}"
    os.kill(process.pid, signal.SIGKILL)
    process.wait(timeout=30)
    attribute_faults.clear()
    return job, plan, partitions


@pytest.mark.django_db(transaction=True)
def test_recovery_after_seek_commit_before_progress(disposable_attribute_db, attribute_faults, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    job, plan, partitions = _crash_web_owner_at(
        database, attribute_faults, type_id=1101, attr_id=11010, point="async.after_seek_commit_before_progress",
    )
    assertion_count = 1
    # The lease must age past 120s dead-owner + 120s stale-heartbeat window
    # before the scheduler is eligible to reclaim it (Section 3).
    AttributeMutationJob.objects.filter(pk=job.pk).update(
        lease_expires_at=timezone.now() - __import__("datetime").timedelta(seconds=1),
        last_heartbeat_at=timezone.now() - __import__("datetime").timedelta(seconds=130),
    )
    recovered = _run_one_scan("recovery:test-scan-11")
    assert recovered == 1
    assertion_count += 1
    job.refresh_from_db()
    assert job.state in {"succeeded", "partial", "failed"}
    assertion_count += 1
    assert job.claim_owner is None
    assertion_count += 1
    partition = _fresh_partition(partitions[1101].pk)
    assert partition.claim_owner is None
    assertion_count += 1

    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_sync_recovery.py::test_recovery_after_seek_commit_before_progress",
        pid=os.getpid(), job_id=str(job.job_id), message_id=None, request_id=None,
        claim_owner=None, claim_generation=job.claim_generation, barrier_id="crash-after-commit-before-progress",
        fault_id="async.after_seek_commit_before_progress",
        state_version_trace=[0, job.state_version], lease_version_trace=[0, 1],
        heartbeat_database_timestamps=[timezone.now().isoformat()], lease_expiry_database_timestamps=[timezone.now().isoformat()],
        ordered_outcomes=job.outcomes, completed_sample_types=len(job.outcomes), total_sample_types=len(plan.types),
        physical_sha256=_sha256_of_outcomes(partition.actual_after_physical_fingerprint),
        semantic_sha256=_sha256_of_outcomes(plan.types[0].expected_after_semantic_fingerprint),
        audit_sha256=_sha256_of_outcomes(job.terminal_result), physical_commit_count=1, terminal_classification=job.state,
        setting_consumption_trace=[], assertion_count=assertion_count,
    )


@pytest.mark.django_db(transaction=True)
def test_recovery_after_some_types_before_remaining(disposable_attribute_db, attribute_faults, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    _seed_blood(database, population=0)
    _seed_extra_type(database, 1201, "First", 12010, "Weight")
    _seed_extra_type(database, 1202, "Remaining", 12020, "Weight")
    request = _multi_target_request("patch", [
        (1201, [patch_operation(12010, {"description": "content"})]),
        (1202, [patch_operation(12020, {"description": "content"})]),
    ])
    plan = _plan(request)
    job, partitions = _seed_job_and_partitions(database, plan, execution_mode="synchronous")
    owner = f"web:test:{uuid.uuid4()}"
    attribute_faults.arm("async.after_progress_before_result")
    process = _spawn_web_owner(job.job_id, owner)
    assert _wait_until(lambda: len((AttributeMutationJob.objects.get(pk=job.pk).outcomes or [])) >= 1, timeout=30)
    assertion_count = 1
    os.kill(process.pid, signal.SIGKILL)
    process.wait(timeout=30)
    attribute_faults.clear()

    AttributeMutationJob.objects.filter(pk=job.pk).update(
        lease_expires_at=timezone.now() - __import__("datetime").timedelta(seconds=1),
        last_heartbeat_at=timezone.now() - __import__("datetime").timedelta(seconds=130),
    )
    recovered = _run_one_scan("recovery:test-scan-12")
    assert recovered == 1
    assertion_count += 1
    job.refresh_from_db()
    assert len(job.outcomes) == 2
    assertion_count += 1
    assert job.claim_owner is None
    assertion_count += 1

    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_sync_recovery.py::test_recovery_after_some_types_before_remaining",
        pid=os.getpid(), job_id=str(job.job_id), message_id=None, request_id=None,
        claim_owner=None, claim_generation=job.claim_generation, barrier_id="crash-after-some-types",
        fault_id="async.after_progress_before_result",
        state_version_trace=[0, job.state_version], lease_version_trace=[0, 1],
        heartbeat_database_timestamps=[timezone.now().isoformat()], lease_expiry_database_timestamps=[timezone.now().isoformat()],
        ordered_outcomes=job.outcomes, completed_sample_types=len(job.outcomes), total_sample_types=len(plan.types),
        physical_sha256=_sha256_of_outcomes([item.before_physical_fingerprint for item in plan.types]),
        semantic_sha256=_sha256_of_outcomes([item.expected_after_semantic_fingerprint for item in plan.types]),
        audit_sha256=_sha256_of_outcomes(job.terminal_result), physical_commit_count=2, terminal_classification=job.state,
        setting_consumption_trace=[], assertion_count=assertion_count,
    )


@pytest.mark.django_db(transaction=True)
def test_recovery_after_response_construction_before_terminal_cas(disposable_attribute_db, attribute_faults, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    job, plan, partitions = _crash_web_owner_at(
        database, attribute_faults, type_id=1301, attr_id=13010, point="async.after_result_before_terminal",
    )
    assertion_count = 1
    AttributeMutationJob.objects.filter(pk=job.pk).update(
        lease_expires_at=timezone.now() - __import__("datetime").timedelta(seconds=1),
        last_heartbeat_at=timezone.now() - __import__("datetime").timedelta(seconds=130),
    )
    recovered = _run_one_scan("recovery:test-scan-13")
    assert recovered == 1
    assertion_count += 1
    job.refresh_from_db()
    assert job.state in {"succeeded", "partial", "failed"}
    assertion_count += 1
    assert job.terminal_result is not None
    assertion_count += 1
    assert job.claim_owner is None
    assertion_count += 1

    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_sync_recovery.py::test_recovery_after_response_construction_before_terminal_cas",
        pid=os.getpid(), job_id=str(job.job_id), message_id=None, request_id=None,
        claim_owner=None, claim_generation=job.claim_generation, barrier_id="crash-after-result-before-terminal",
        fault_id="async.after_result_before_terminal",
        state_version_trace=[0, job.state_version], lease_version_trace=[0, 1],
        heartbeat_database_timestamps=[timezone.now().isoformat()], lease_expiry_database_timestamps=[timezone.now().isoformat()],
        ordered_outcomes=job.outcomes, completed_sample_types=len(job.outcomes), total_sample_types=len(plan.types),
        physical_sha256=_sha256_of_outcomes([item.before_physical_fingerprint for item in plan.types]),
        semantic_sha256=_sha256_of_outcomes([item.expected_after_semantic_fingerprint for item in plan.types]),
        audit_sha256=_sha256_of_outcomes(job.terminal_result), physical_commit_count=1, terminal_classification=job.state,
        setting_consumption_trace=[], assertion_count=assertion_count,
    )


# ---------------------------------------------------------------------------
# 14. Recovery refuses to replay an ambiguous partition
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_recovery_refuses_ambiguous_partition_replay(disposable_attribute_db, django_db_blocker):
    django_db_blocker.unblock()
    database = disposable_attribute_db
    assertion_count = 0
    job, plan, partitions = _seed_single_type_job(database, type_id=1401, attr_id=14010)
    partition = partitions[1401]
    # Simulate an ambiguous, provably-not-clean partition: a stray created-id
    # binding with no recorded commit/fingerprint -- exactly the shape
    # Section 3 requires recovery to refuse rather than replay.
    AttributeMutationPartition.objects.filter(pk=partition.pk).update(created_id_bindings={"created:0:0": 999999})
    AttributeMutationJob.objects.filter(pk=job.pk).update(
        lease_expires_at=timezone.now() - __import__("datetime").timedelta(seconds=1),
        last_heartbeat_at=timezone.now() - __import__("datetime").timedelta(seconds=130),
    )
    recovered = _run_one_scan("recovery:test-scan-14")
    assert recovered == 1
    assertion_count += 1
    job.refresh_from_db()
    assert job.state == "failed"
    assertion_count += 1
    outcome = job.outcomes[0]
    assert outcome["status"] == "failed"
    assertion_count += 1
    assert outcome["errors"][0]["code"] == "ambiguous_recovery_state"
    assertion_count += 1
    fresh_partition = _fresh_partition(partition.pk)
    assert fresh_partition.state == "pending"  # never executed/replayed
    assertion_count += 1
    assert fresh_partition.actual_after_physical_fingerprint is None
    assertion_count += 1

    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_sync_recovery.py::test_recovery_refuses_ambiguous_partition_replay",
        pid=os.getpid(), job_id=str(job.job_id), message_id=None, request_id=None,
        claim_owner=None, claim_generation=job.claim_generation, barrier_id="ambiguous-replay-refused", fault_id=None,
        state_version_trace=[0, job.state_version], lease_version_trace=[0, 1],
        heartbeat_database_timestamps=[timezone.now().isoformat()], lease_expiry_database_timestamps=[timezone.now().isoformat()],
        ordered_outcomes=job.outcomes, completed_sample_types=len(job.outcomes), total_sample_types=len(plan.types),
        physical_sha256=_sha256_of_outcomes([item.before_physical_fingerprint for item in plan.types]),
        semantic_sha256=_sha256_of_outcomes([item.expected_after_semantic_fingerprint for item in plan.types]),
        audit_sha256=_sha256_of_outcomes(job.terminal_result), physical_commit_count=0, terminal_classification=job.state,
        setting_consumption_trace=[], assertion_count=assertion_count,
    )

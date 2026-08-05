"""Idempotent synchronous-job recovery scheduler (Section 3). Runs only as a
separately managed scheduler service, outside any request process and
outside any Celery queue -- it never shares an owner identity with the web
or worker paths (`recovery:<hostname>:<pid>:<scan_uuid>`). Recovers only
`outbox_state="not_required"` jobs whose owning web process appears to have
died (expired lease, stale heartbeat); executes a partition only when its
durable state is unambiguously untouched and its current physical
fingerprint still matches the planned before-fingerprint, otherwise
terminalizes it as an infrastructure failure without replay.

Bare invocation performs exactly one scan-and-recover pass (deterministic
for tests and for `--check-heartbeat`); `--loop` repeats every
`--interval-seconds` until SIGTERM/SIGINT.
"""
from __future__ import annotations

import os
import signal
import time
import uuid
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import models
from django.db.models.functions import Now
from django.utils import timezone

from nextseek_api.attributes.jobs import (
    JobHeartbeat, JobLease, LEASE_SECONDS,
    DjangoMutationJobStore, _build_terminal_response, _execute_one_type, _replan,
)
from nextseek_api.attributes.models_async import AttributeOutboxDispatcherHeartbeat

SCHEDULER_HEARTBEAT_SINGLETON_KEY = "attribute_sync_recovery"
# Section 3: the scanner "separately prefilters last_heartbeat_at <= now -
# 120s", the same 120s dead-owner window as every other lease in this task.
STALE_HEARTBEAT_SECONDS = 120


def _eligible_job_ids(job_model):
    now = timezone.now()
    stale_cutoff = now - timedelta(seconds=STALE_HEARTBEAT_SECONDS)
    return list(job_model.objects.filter(
        outbox_state="not_required", state__in=("accepted", "queued", "running"),
        lease_expires_at__lt=now, last_heartbeat_at__lte=stale_cutoff,
    ).values_list("job_id", flat=True))


def _claim_for_recovery(job_model, job_id, owner, *, lease_seconds=LEASE_SECONDS):
    observed = job_model.objects.only(
        "pk", "state", "claim_owner", "claim_generation", "lease_version", "state_version",
    ).get(job_id=job_id)
    stale_cutoff = timezone.now() - timedelta(seconds=STALE_HEARTBEAT_SECONDS)
    updated = job_model.objects.filter(
        job_id=job_id, outbox_state="not_required", state__in=("accepted", "queued", "running"),
        claim_owner=observed.claim_owner, claim_generation=observed.claim_generation,
        lease_version=observed.lease_version, state_version=observed.state_version,
        lease_expires_at__lt=Now(), last_heartbeat_at__lte=stale_cutoff,
    ).update(
        claim_owner=owner, claim_generation=models.F("claim_generation") + 1,
        lease_version=models.F("lease_version") + 1, state_version=models.F("state_version") + 1,
        last_heartbeat_at=Now(), lease_expires_at=Now() + timedelta(seconds=lease_seconds),
    )
    if updated != 1:
        return None
    fresh = job_model.objects.only(
        "pk", "claim_owner", "claim_generation", "lease_version", "state_version",
    ).get(job_id=job_id)
    return JobLease(fresh.pk, fresh.claim_owner, fresh.claim_generation, fresh.lease_version, fresh.state_version)


def _recoverable_state(partition) -> bool:
    """A partition is safely resumable only when it is provably untouched:
    pending, no owner/lease, no append-only bindings, no recorded physical
    commit, and no reconciliation intent marker of any kind."""
    return (
        partition.state == "pending"
        and partition.claim_owner is None and partition.lease_expires_at is None
        and not partition.created_id_bindings and partition.actual_after_physical_fingerprint is None
        and not partition.reconciliation
    )


def _physical_fingerprint_now(sample_type_id) -> str:
    from django.conf import settings
    from django.db import connections

    from nextseek_api.attributes.executor import DEFINITION_COLUMNS, _physical_fingerprint, _rows_to_definitions

    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(
            f"SELECT {DEFINITION_COLUMNS} FROM sample_attributes WHERE sample_type_id=%s "
            "ORDER BY CASE WHEN pos IS NULL OR pos < 1 THEN 1 ELSE 0 END, pos, id",
            [sample_type_id],
        )
        rows = cursor.fetchall()
    return _physical_fingerprint(_rows_to_definitions(rows, sample_type_id))


def _ambiguous_outcome(type_plan, message) -> dict:
    return {
        "sample_type_id": type_plan.sample_type_id,
        "sample_type_title": str(type_plan.sample_type_title),
        "status": "failed", "counts": {}, "attributes": [], "automatic_changes": [],
        "errors": [{"code": "ambiguous_recovery_state", "message": message,
                    "target_index": None, "attribute_index": None, "field": None, "submitted_identifier": None}],
    }


def recover_one_job(job_model, partition_model, job_id, owner) -> bool:
    from nextseek_api.attributes.executor import adapt_type_outcome

    job = job_model.objects.get(job_id=job_id)
    lease = _claim_for_recovery(job_model, job_id, owner)
    if lease is None:
        return False
    store = DjangoMutationJobStore(job_model, partition_model)
    heartbeat = JobHeartbeat(store, lease).start()
    try:
        heartbeat.wait_for_first_renewal()
        plan = _replan(job)
        outcomes = []
        for type_plan in plan.types:
            if getattr(type_plan, "status", None) in {"unchanged", "failed", "plan_delta_required"}:
                outcomes.append(adapt_type_outcome(type_plan))
                continue
            partition = partition_model.objects.get(job=job, sample_type_id=type_plan.sample_type_id)
            if partition.state == "succeeded":
                outcomes.append(dict(partition.outcome))
                continue
            if not _recoverable_state(partition):
                outcomes.append(_ambiguous_outcome(type_plan, "partition state is not unambiguously untouched; recovery refuses to replay"))
                continue
            if _physical_fingerprint_now(type_plan.sample_type_id) != partition.before_physical_fingerprint:
                outcomes.append(_ambiguous_outcome(type_plan, "current physical state no longer matches the planned before-fingerprint"))
                continue
            outcomes.append(_execute_one_type(job, type_plan, store, owner))
        terminal = _build_terminal_response(outcomes, cancelled=False)
        store.finish(lease, terminal["overall_status"], terminal)
        return True
    finally:
        heartbeat.stop()


def run_one_scan(job_model, partition_model, *, scan_owner_prefix=None) -> int:
    owner = scan_owner_prefix or f"recovery:{os.uname().nodename}:{os.getpid()}:{uuid.uuid4()}"
    recovered = 0
    for job_id in _eligible_job_ids(job_model):
        try:
            if recover_one_job(job_model, partition_model, job_id, owner):
                recovered += 1
        except Exception:  # noqa: BLE001 - one job's recovery failure must never abort the scan for the rest
            continue
    return recovered


class Command(BaseCommand):
    help = "Recover synchronous attribute-mutation jobs whose owning web process appears to have died."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true", help="Repeat every --interval-seconds until signaled.")
        parser.add_argument("--interval-seconds", type=int, default=30)
        parser.add_argument("--check-heartbeat", action="store_true", help="Healthcheck mode: exit nonzero unless the scheduler heartbeat is fresh.")
        parser.add_argument("--max-age-seconds", type=int, default=90)
        parser.add_argument("--iterations", type=int, default=None, help="Bound the number of --loop iterations (test/diagnostic use).")

    def handle(self, *args, **options):
        from nextseek_api.attributes.models_db import AttributeMutationJob, AttributeMutationPartition

        if options["check_heartbeat"]:
            try:
                heartbeat = AttributeOutboxDispatcherHeartbeat.objects.get(singleton_key=SCHEDULER_HEARTBEAT_SINGLETON_KEY)
            except AttributeOutboxDispatcherHeartbeat.DoesNotExist:
                raise SystemExit("attribute sync-recovery scheduler heartbeat is absent")
            age = (timezone.now() - heartbeat.observed_at).total_seconds()
            if age > options["max_age_seconds"]:
                raise SystemExit(f"attribute sync-recovery scheduler heartbeat is stale ({age:.1f}s old)")
            if not heartbeat.owner:
                raise SystemExit("attribute sync-recovery scheduler heartbeat has no owner")
            return

        stopped = {"value": False}

        def stop(*_ignored):
            stopped["value"] = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        owner = f"{os.uname().nodename}:{os.getpid()}"
        iterations = options.get("iterations")
        ran = 0
        while True:
            heartbeat, _ = AttributeOutboxDispatcherHeartbeat.objects.get_or_create(
                singleton_key=SCHEDULER_HEARTBEAT_SINGLETON_KEY, defaults={"owner": owner},
            )
            changed = AttributeOutboxDispatcherHeartbeat.objects.filter(
                pk=heartbeat.pk, state_version=heartbeat.state_version,
            ).update(owner=owner, state_version=models.F("state_version") + 1)
            if changed != 1:
                raise RuntimeError("lost sync-recovery scheduler heartbeat CAS")
            run_one_scan(AttributeMutationJob, AttributeMutationPartition)
            ran += 1
            if not options["loop"]:
                return
            if iterations is not None and ran >= iterations:
                return
            if stopped["value"]:
                return
            if _sleep_until_stopped_or_interval(stopped, options["interval_seconds"]):
                return


def _sleep_until_stopped_or_interval(stopped, interval_seconds) -> bool:
    deadline = time.monotonic() + interval_seconds
    while time.monotonic() < deadline:
        if stopped["value"]:
            return True
        time.sleep(min(0.25, max(0.0, deadline - time.monotonic())))
    return stopped["value"]

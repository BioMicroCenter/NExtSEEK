"""T08 durable async orchestration (Phase-4 Chain-C hardening, task-08
Section 3 binding subsection; DD-12, DD-15, DD-23, DD-29, DD-31 through
DD-34).

This module owns four things: durable job creation (`MutationJobService`,
below); the transactional-outbox dispatcher that turns a durably-persisted,
pending `AttributeMutationJob` outbox row into one Celery message; the
dedicated `attribute_mutations` worker's stored-job execution
(`run_stored_job`, invoked by `tasks.run_attribute_mutation`); and the
shared six-field DD-13 lease/CAS primitives both the worker and (later,
T09's synchronous request path) heartbeat against the same
`AttributeMutationJob` row.

This module also owns `MutationJobService` (Amendment 2026-08-08 (1) to the
task-08 spec, Section 3): the durable job-creation path that persists one
`AttributeMutationJob` row plus its per-planned-type `AttributeMutationPartition`
rows in a single transaction, including the creation-time
`outbox_state` `not_required`->`pending` flip for an asynchronous job. T08
previously deferred that flip to T09 (see the withdrawn history below), but
task-09's frozen Section 6 imports the class from *this* T08-owned module
(`from .jobs import MutationJobService`, constructed with no arguments in
`AttributeServices.build`) and grants T09 no edit right over `jobs.py` --
unclosable without T08 supplying the symbol itself. The amendment authors
fresh normative Section 3 text for this; it does not reinstate any part of
the withdrawn Sections 5-6 sketch below.

Section 3 is the sole authoritative behavior contract; the plan's own
Sections 5-6 are explicitly withdrawn, non-normative Phase-3 sketches that
may not be copied or used to satisfy Modified Files or Verification (see
the task-08 spec's "Phase-4 Chain-C hardening (binding)" subsection). This
module is an independent implementation of Section 3's exact predicates,
not a port of that withdrawn sketch; two concrete points where the real
merged code forced a different design from the withdrawn sketch:

1. (Superseded 2026-08-08 -- see above.) The withdrawn Phase-3 sketch's
   `MutationJobService.create` is not copied; this module's own `create`
   below is an independent implementation against the real T03 model fields
   (`canonical_submitted_request_sha256`/`canonical_submitted_request`/
   `resolved_plan_sha256`/`resolved_plan_envelope`, not the sketch's
   differently-named `canonical_request_sha256`/`canonical_request`), reusing
   the same `build_resolved_plan_envelope`/`AttributeMutationPartition`
   construction the T07/T08 test suite's own `_seed_job_and_partitions`
   helper already exercises.
2. T05's planner deliberately has no plan<->bytes round-trip codec
   (`planner.py`'s own comment on `build_resolved_plan_envelope`: "why a full
   round-trip codec is not part of the real contract"). DD-23 names the real
   mechanism instead: "Asynchronous jobs perform the same recheck when
   execution begins." `_replan` re-derives the full in-memory `MutationPlan`
   from the job's durably-stored `canonical_submitted_request` by re-running
   `MutationPlanner.plan_mutation` against current resolved state -- the same
   pure, deterministic planner T05 already ships -- then matches each
   `sample_type_id`/`idempotency_key` against the durable
   `AttributeMutationPartition` row before claiming and executing it.
   `execute_type_plan`'s own locked-read fingerprint recheck (already merged,
   `executor.py`) is what actually enforces "no drift since planning" at the
   physical level; this module's own identity check only guards against the
   replanned type set disagreeing with what was durably promised.
"""
from __future__ import annotations

import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import timedelta

from django.db import models, transaction
from django.db.models import Q
from django.db.models.functions import Now
from django.utils import timezone

from nextseek_api.attributes.faults import attribute_fault

# Section 3: "Heartbeat cadence is 40 seconds" / lease grants are 120 seconds
# for both the aggregate job and a per-type partition claim.
HEARTBEAT_INTERVAL_SECONDS = 40
LEASE_SECONDS = 120
# Bounded wait for a partition claim that is live under the *same* aggregate
# job (Section 3: "a bounded wait/retry state, never an uncaught terminal
# task exception") -- e.g. a redelivered worker racing a not-yet-expired
# partition sub-lease left by a still-finishing sibling attempt.
PARTITION_CLAIM_WAIT_SECONDS = 5.0

# T08 owns the dispatcher's own tunables; `dmac/settings.py` is not in this
# task's Modified Files list (T09/T10 own their own settings additions), so
# these are local, environment-overridable constants rather than
# `settings.ATTRIBUTE_MUTATION_OUTBOX_*` references to an undeclared setting.
ATTRIBUTE_MUTATION_OUTBOX_BATCH_SIZE = int(os.environ.get("ATTRIBUTE_MUTATION_OUTBOX_BATCH_SIZE", "50"))
ATTRIBUTE_MUTATION_OUTBOX_IDLE_SECONDS = float(os.environ.get("ATTRIBUTE_MUTATION_OUTBOX_IDLE_SECONDS", "0.5"))


class JobLeaseLost(RuntimeError):
    """This owner's job-level lease was observed gone (stolen by a later
    recovery/redelivery owner) at a cancellation-boundary observation or a
    progress/terminal CAS. The caller must stop mutating partitions."""


# ---------------------------------------------------------------------------
# Shared six-field DD-13 job lease token
# ---------------------------------------------------------------------------


@dataclass
class JobLease:
    """Mutable, lock-protected six-field job token shared between the main
    execution loop and the background heartbeat thread. Both actors CAS the
    *same* `AttributeMutationJob` row, so both must observe/advance the same
    tracked `lease_version`/`state_version` under one lock -- otherwise the
    heartbeat thread's renewal and the main loop's progress/terminal CAS
    would silently race each other's stale in-memory copy."""

    job_pk: int
    owner: str
    claim_generation: int
    lease_version: int
    state_version: int
    lock: threading.Lock = field(default_factory=threading.Lock)


class JobHeartbeat:
    """Background 40s renewal thread for one `JobLease`. Starts and waits
    for its first acknowledged renewal before the caller may open any SEEK
    work (Section 3: "starts its heartbeat and waits for the first
    acknowledged renewal before opening the first ... transaction")."""

    def __init__(self, store, lease: JobLease, *, interval_seconds=HEARTBEAT_INTERVAL_SECONDS, lease_seconds=LEASE_SECONDS):
        self._store, self._lease = store, lease
        self._interval, self._lease_seconds = interval_seconds, lease_seconds
        self._stop = threading.Event()
        self._first_renewal = threading.Event()
        self._error: Exception | None = None
        self._thread = threading.Thread(target=self._run, name=f"attribute-job-heartbeat-{lease.job_pk}", daemon=True)

    def start(self) -> "JobHeartbeat":
        self._thread.start()
        return self

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                ok = self._store.heartbeat(self._lease, lease_seconds=self._lease_seconds)
            except Exception as exc:  # noqa: BLE001 - surfaced to the waiting/owning thread, never swallowed
                self._error = exc
                ok = False
            if not ok and self._error is None:
                self._error = JobLeaseLost("job heartbeat lost its CAS token")
            self._first_renewal.set()
            if not ok:
                self._stop.set()
                return
            if self._stop.wait(self._interval):
                return

    def wait_for_first_renewal(self, timeout=10) -> None:
        if not self._first_renewal.wait(timeout=timeout):
            self.stop()
            raise RuntimeError("job heartbeat did not perform its initial renewal in time")
        if self._error is not None:
            raise self._error

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=30)
        if self._thread.is_alive():
            raise RuntimeError("job heartbeat thread did not stop")


# ---------------------------------------------------------------------------
# Transactional outbox dispatcher
# ---------------------------------------------------------------------------


def mutation_job_store() -> "DjangoMutationJobStore":
    from nextseek_api.attributes.models_db import AttributeMutationJob, AttributeMutationPartition

    return DjangoMutationJobStore(AttributeMutationJob, AttributeMutationPartition)


def dispatch_outbox(store, sender, limit=100, owner="dispatcher") -> int:
    """Publish every currently-claimable outbox row (at most `limit`) through
    `sender(args=[job_id], queue="attribute_mutations")`, returning the
    number actually published. A publish failure releases the row back to
    `pending` for the next dispatcher pass rather than losing it."""
    published = 0
    for row in store.claim_publishable(limit, owner):
        try:
            attribute_fault("async.after_acceptance_before_outbox_publish")
            result = sender(args=[row["job_id"]], queue="attribute_mutations")
        except Exception as exc:  # noqa: BLE001 - broker/publish failure returns the row to pending
            store.release_publish(row["id"], owner, row["state_version"], str(exc))
        else:
            store.mark_published(row["id"], owner, row["state_version"], result.id)
            published += 1
    return published


# ---------------------------------------------------------------------------
# Async re-plan-and-recheck (DD-23) and the shared T07 outcome adapter
# ---------------------------------------------------------------------------


def _replan(job, *, threshold=None):
    """Re-derive the full in-memory `MutationPlan` for a durably-stored job
    from its immutable `canonical_submitted_request` (DD-23 "same recheck").
    No unresolved type may reach an accepted job (T09 rejects those before
    job creation), so an unresolved type here is an infrastructure anomaly,
    not a normal outcome."""
    from django.conf import settings

    from nextseek_api.attributes.planner import MutationPlanner
    from nextseek_api.attributes.repository import AttributeRepository, SeekAttributeGateway

    if threshold is None:
        threshold = getattr(settings, "ATTRIBUTE_MUTATION_AFFECTED_ROW_THRESHOLD", 0)
    submitted = job.canonical_submitted_request["request"]
    repository = AttributeRepository(SeekAttributeGateway())
    plan = MutationPlanner(threshold=threshold).plan_mutation(submitted, repository)
    if any(item.sample_type_id is None for item in plan.types):
        raise RuntimeError("async re-plan produced an unresolved type an accepted job cannot contain")
    return plan


def _cancelled_outcome(type_plan) -> dict:
    return {
        "sample_type_id": type_plan.sample_type_id,
        "sample_type_title": str(type_plan.sample_type_title),
        "status": "cancelled", "counts": {}, "attributes": [], "automatic_changes": [], "errors": [],
    }


def _execute_one_type(job, type_plan, store, owner) -> dict:
    """Route one replanned type through T07's shared outcome adapter
    (Section 3: "T08 shares T07's outcome adapter"). `unchanged` and
    already-resolved `failed`/`plan_delta_required` types never claim a
    partition or open SEEK; a `planned` type claims its durable partition
    (bounded wait/retry if it is live under this same aggregate job, a
    read-only reconciliation adapter if it is already terminal `succeeded`),
    then executes through the identical `execute_type_plan` kernel T07's
    synchronous caller uses."""
    from nextseek_api.attributes.executor import adapt_type_outcome, execute_type_plan

    if getattr(type_plan, "status", None) in {"unchanged", "failed", "plan_delta_required"}:
        return adapt_type_outcome(type_plan)
    try:
        services = store.claim_partition_services(job, type_plan, owner)
    except Exception as exc:  # noqa: BLE001 - a claim failure becomes a failed outcome for this type only
        return adapt_type_outcome(type_plan, error=exc)
    attribute_fault("async.after_claim_before_type")
    try:
        execution_result = execute_type_plan(type_plan, services)
    except Exception as exc:  # noqa: BLE001 - any execution failure terminalizes this type only
        if not services.seek_commit_observed(type_plan):
            services.record_failure(type_plan, exc)
        return adapt_type_outcome(type_plan, error=exc)
    return adapt_type_outcome(type_plan, execution_result=execution_result, reconciled=execution_result.get("reconciled"))


def _overall_status_and_http(outcomes, cancelled) -> tuple[str, int]:
    """Mirror `schemas.valid_completed_status_http`'s classification exactly
    (same `NO_COMMIT_ERROR_CLASS` table) so `MutationCompletedResponse`'s own
    validator accepts the terminal body this module builds."""
    from nextseek_api.attributes.schemas import NO_COMMIT_ERROR_CLASS

    statuses = {row["status"] for row in outcomes}
    executable = bool(statuses & {"succeeded", "unchanged"})
    blocked = bool(statuses & {"failed", "cancelled", "skipped"})
    if executable and not blocked:
        return "succeeded", 200
    if executable:
        return "partial", 207
    classes = {NO_COMMIT_ERROR_CLASS[error["code"]] for row in outcomes for error in row["errors"]}
    if "semantic" in classes:
        return "failed", 422
    if statuses <= {"cancelled", "skipped"} and "cancelled" in statuses:
        return "cancelled", 409
    return "failed", 409


def _build_terminal_response(outcomes, cancelled) -> dict:
    from nextseek_api.attributes.schemas import MutationCompletedResponse, MutationCounts, SampleTypeMutationOutcome

    normalized = [SampleTypeMutationOutcome.model_validate(row) for row in outcomes]
    overall_status, http_status = _overall_status_and_http(outcomes, cancelled)
    counts = MutationCounts(**{
        field_name: sum(getattr(row.counts, field_name) for row in normalized)
        for field_name in MutationCounts.model_fields
    })
    return MutationCompletedResponse(
        mode="asynchronous", overall_status=overall_status, http_status=http_status,
        counts=counts, outcomes=normalized,
    ).model_dump(mode="json")


def run_stored_job(job_id, store, owner, *, threshold=None) -> dict:
    """The dedicated `attribute_mutations` worker's stored-job execution
    (Section 3). Claims the job by the exact two-predicate CAS (never steals
    a live lease; a duplicate delivery observes `not_claimed`), starts its
    40s heartbeat, replans (DD-23), and executes every resolved type in
    order -- checking cancellation only between types, never inside one --
    persisting deterministic progress and, finally, the exact DD-33/DD-15
    terminal body through the shared T07 adapter."""
    attribute_fault("async.after_receive_before_claim")
    lease = store.start_job(job_id, owner)
    if lease is None:
        return {"state": "not_claimed"}
    job = store.get_job(job_id)
    heartbeat = JobHeartbeat(store, lease).start()
    try:
        heartbeat.wait_for_first_renewal()
        plan = _replan(job, threshold=threshold)
        total = len(plan.types)
        outcomes: list[dict] = []
        cancelled = False
        for type_plan in plan.types:
            owned, cancel_requested = store.cancellation_boundary(lease)
            if not owned:
                raise JobLeaseLost(f"job {job_id} lease no longer owned by {owner}")
            if cancel_requested:
                cancelled = True
                break
            outcome = _execute_one_type(job, type_plan, store, owner)
            attribute_fault("async.after_seek_commit_before_progress")
            outcomes.append(outcome)
            store.record_progress(lease, len(outcomes), total, outcomes)
            attribute_fault("async.after_progress_before_result")
        if cancelled:
            outcomes.extend(_cancelled_outcome(type_plan) for type_plan in plan.types[len(outcomes):])
        terminal = _build_terminal_response(outcomes, cancelled)
        attribute_fault("async.after_result_before_terminal")
        store.finish(lease, terminal["overall_status"], terminal)
        return {"state": terminal["overall_status"], "outcomes": outcomes}
    finally:
        heartbeat.stop()


# ---------------------------------------------------------------------------
# Django/default-database store: job outbox, job lease, partition claim
# ---------------------------------------------------------------------------


class DjangoMutationJobStore:
    def __init__(self, job_model, partition_model):
        self.job_model, self.partition_model = job_model, partition_model

    # -- outbox ---------------------------------------------------------

    def claim_publishable(self, limit, owner) -> list[dict]:
        cutoff = timezone.now() - timedelta(seconds=LEASE_SECONDS)
        claimed = []
        with transaction.atomic():
            rows = list(self.job_model.objects.select_for_update(skip_locked=True).filter(
                Q(outbox_state="pending")
                | Q(outbox_state="publishing", outbox_payload__claimed_at__isnull=True)
                | Q(outbox_state="publishing", outbox_payload__claimed_at__lt=cutoff.isoformat())
            ).order_by("created_at")[:limit])
            for row in rows:
                version = row.state_version
                changed = self.job_model.objects.filter(
                    pk=row.pk, state_version=version, outbox_state__in=["pending", "publishing"],
                ).update(
                    outbox_state="publishing",
                    outbox_attempts=models.F("outbox_attempts") + 1,
                    outbox_payload={**row.outbox_payload, "dispatcher_owner": owner, "claimed_at": timezone.now().isoformat()},
                    state_version=models.F("state_version") + 1,
                )
                if changed == 1:
                    claimed.append({"id": row.pk, "job_id": str(row.job_id), "state_version": version + 1})
        return claimed

    def mark_published(self, pk, owner, version, message_id) -> None:
        updated = self.job_model.objects.filter(
            pk=pk, state_version=version, outbox_state="publishing", outbox_payload__dispatcher_owner=owner,
        ).update(outbox_state="published", outbox_payload={"message_id": message_id}, state_version=models.F("state_version") + 1)
        if updated != 1:
            raise RuntimeError("lost outbox claim before publish CAS")

    def release_publish(self, pk, owner, version, error) -> None:
        updated = self.job_model.objects.filter(
            pk=pk, state_version=version, outbox_state="publishing", outbox_payload__dispatcher_owner=owner,
        ).update(outbox_state="pending", outbox_last_error=error, outbox_payload={}, state_version=models.F("state_version") + 1)
        if updated != 1:
            raise RuntimeError("lost outbox claim before release CAS")

    # -- job lease --------------------------------------------------------

    def get_job(self, job_id):
        return self.job_model.objects.get(job_id=job_id)

    def start_job(self, job_id, owner, *, lease_seconds=LEASE_SECONDS) -> JobLease | None:
        """Exactly two claim predicates (Section 3), both evaluated against
        database `Now()`. A queued job claims cleanly; a `running` job whose
        lease has expired (a dead owner's) is recovered by a fresh claim
        under an incremented generation. A live owner matches neither
        predicate and this returns `None` without touching any state."""
        observed = self.job_model.objects.only(
            "pk", "state", "claim_owner", "claim_generation", "lease_version", "state_version",
        ).get(job_id=job_id)
        claimed = self.job_model.objects.filter(
            job_id=job_id, state__in=("accepted", "queued"),
            claim_owner__isnull=True, lease_expires_at__isnull=True,
            claim_generation=observed.claim_generation, lease_version=observed.lease_version,
            state_version=observed.state_version,
        ).update(
            state="running", claim_owner=owner,
            claim_generation=models.F("claim_generation") + 1,
            lease_version=models.F("lease_version") + 1,
            state_version=models.F("state_version") + 1,
            last_heartbeat_at=Now(), lease_expires_at=Now() + timedelta(seconds=lease_seconds),
            started_at=timezone.now(),
        )
        if claimed != 1:
            claimed = self.job_model.objects.filter(
                job_id=job_id, state="running",
                claim_owner__isnull=False, claim_owner=observed.claim_owner,
                claim_generation=observed.claim_generation, lease_version=observed.lease_version,
                state_version=observed.state_version, lease_expires_at__lt=Now(),
            ).update(
                claim_owner=owner,
                claim_generation=models.F("claim_generation") + 1,
                lease_version=models.F("lease_version") + 1,
                state_version=models.F("state_version") + 1,
                last_heartbeat_at=Now(), lease_expires_at=Now() + timedelta(seconds=lease_seconds),
            )
            if claimed != 1:
                return None
        fresh = self.job_model.objects.only(
            "pk", "claim_owner", "claim_generation", "lease_version", "state_version",
        ).get(job_id=job_id)
        return JobLease(fresh.pk, fresh.claim_owner, fresh.claim_generation, fresh.lease_version, fresh.state_version)

    def heartbeat(self, lease: JobLease, *, lease_seconds=LEASE_SECONDS) -> bool:
        with lease.lock:
            updated = self.job_model.objects.filter(
                pk=lease.job_pk, claim_owner=lease.owner, claim_generation=lease.claim_generation,
                lease_version=lease.lease_version, state_version=lease.state_version, state="running",
                lease_expires_at__gte=Now(),
            ).update(
                last_heartbeat_at=Now(), lease_expires_at=Now() + timedelta(seconds=lease_seconds),
                lease_version=models.F("lease_version") + 1, state_version=models.F("state_version") + 1,
            )
            if updated == 1:
                lease.lease_version += 1
                lease.state_version += 1
            return updated == 1

    def cancellation_boundary(self, lease: JobLease) -> tuple[bool, bool]:
        """Return `(still_owned, cancellation_requested)`. The read is
        filtered by the exact live six-field token so a stolen lease is
        detected before another type is ever claimed."""
        with lease.lock:
            row = self.job_model.objects.filter(
                pk=lease.job_pk, claim_owner=lease.owner, claim_generation=lease.claim_generation,
                lease_version=lease.lease_version, state_version=lease.state_version, state="running",
            ).values("cancellation_requested_at").first()
        if row is None:
            return False, False
        return True, row["cancellation_requested_at"] is not None

    def record_progress(self, lease: JobLease, completed, total, outcomes) -> None:
        with lease.lock:
            updated = self.job_model.objects.filter(
                pk=lease.job_pk, claim_owner=lease.owner, claim_generation=lease.claim_generation,
                lease_version=lease.lease_version, state_version=lease.state_version, state="running",
            ).update(outcomes=outcomes, state_version=models.F("state_version") + 1)
            if updated != 1:
                raise JobLeaseLost("lost job lease before progress CAS")
            lease.state_version += 1

    def finish(self, lease: JobLease, state, terminal) -> None:
        with lease.lock:
            updated = self.job_model.objects.filter(
                pk=lease.job_pk, claim_owner=lease.owner, claim_generation=lease.claim_generation,
                lease_version=lease.lease_version, state_version=lease.state_version, state="running",
            ).update(
                state=state, terminal_result=terminal, outcomes=terminal["outcomes"],
                http_classification=terminal["http_status"], finished_at=timezone.now(),
                claim_owner=None, lease_expires_at=None, last_heartbeat_at=None,
                state_version=models.F("state_version") + 1,
            )
            if updated != 1:
                raise JobLeaseLost("lost job lease before terminal CAS")
            lease.state_version += 1

    # -- partition claim (per-type; reuses T07's outcome adapter) ---------

    def claim_partition_services(self, job, type_plan, owner, *, lease_seconds=LEASE_SECONDS, wait_seconds=PARTITION_CLAIM_WAIT_SECONDS):
        """Claim the durable partition matching `type_plan` and return a
        `DjangoExecutionServices` adapter with `synchronous=True` so T07's
        own kernel owns the full terminal CAS for this type (record_commit's
        self-terminalizing tail) -- the same adapter behavior T07's
        synchronous caller uses, just under a `worker:` owner. A live claim
        under this *same* aggregate job (Section 3) is a bounded wait/retry,
        never an immediate failure; a terminal `succeeded` partition returns
        the claimless read-only reconciliation adapter T07 already owns."""
        from nextseek_api.attributes.executor import DjangoExecutionServices, ExecutionConflict, PartitionClaim

        deadline = time.monotonic() + wait_seconds
        while True:
            row = self.partition_model.objects.get(job=job, sample_type_id=type_plan.sample_type_id)
            if row.idempotency_key != type_plan.idempotency_key:
                raise ExecutionConflict("stored partition/plan identity drifted since acceptance")
            if row.state == "succeeded":
                token = PartitionClaim(row.pk, row.claim_owner, row.claim_generation, row.lease_version, row.state_version)
                return DjangoExecutionServices(job, token, synchronous=True, read_only=True)
            live = row.claim_owner is not None and (row.lease_expires_at is None or row.lease_expires_at > timezone.now())
            if live:
                if time.monotonic() >= deadline:
                    raise ExecutionConflict("partition claim is live under the same aggregate job; bounded wait exhausted")
                time.sleep(0.25)
                continue
            claimed = row.claim(
                expected_state_version=row.state_version, expected_claim_generation=row.claim_generation,
                expected_lease_version=row.lease_version, owner=owner, lease_seconds=lease_seconds,
            )
            if not claimed:
                if time.monotonic() >= deadline:
                    raise ExecutionConflict("partition claim CAS repeatedly lost within the bounded wait")
                continue
            token = PartitionClaim(row.pk, owner, row.claim_generation, row.lease_version, row.state_version)
            return DjangoExecutionServices(job, token, synchronous=True)


# ---------------------------------------------------------------------------
# Durable job creation (Amendment 2026-08-08 (1), Review Blocker 5)
# ---------------------------------------------------------------------------


class MutationJobService:
    """Creates the durable `AttributeMutationJob`/`AttributeMutationPartition`
    row pair the rest of this module (dispatcher, worker, recovery) operates
    on. Task-09's frozen `service.py` composes `MutationJobService()` (no
    constructor arguments) into `AttributeServices.build` and calls
    `.create(plan, actor_identity, execution_mode)`; this is the exact,
    verbatim-satisfied signature (Amendment 2026-08-08 (1))."""

    def create(self, plan, actor_identity, execution_mode):
        """Persist one durable job and its per-planned-type partitions in
        exactly one `transaction.atomic()` block -- a partial create is
        never observable. `outbox_state` is set inside that same
        transaction: `"pending"` for an asynchronous job (the
        `not_required`->`pending` flip this module previously deferred to
        T09) or `"not_required"` otherwise; there is no separate
        post-commit flip anywhere in this class. Resolution failures never
        reach a write: any unresolved type (`sample_type_id is None`) raises
        before the transaction opens. `unchanged` types are not partitioned
        (`plan.executable_types` already excludes them, along with
        `failed`/`plan_delta_required` types).

        `attribute_fault("async.during_atomic_job_creation_after_job_before_partitions")`
        fires after the job INSERT and before the first partition INSERT,
        inside the same transaction. An armed fault therefore rolls back
        the job and every partition together. The dispatcher-owned
        `async.after_acceptance_before_outbox_publish` point is deliberately
        not fired here.
        """
        from nextseek_api.attributes.models_db import AttributeMutationJob, AttributeMutationPartition
        from nextseek_api.attributes.planner import build_resolved_plan_envelope

        if plan.unresolved_types:
            raise ValueError("resolution failures must be rejected before job creation")
        envelope_bytes = build_resolved_plan_envelope(plan, execution_mode=execution_mode)
        outbox_pending = execution_mode == "asynchronous"
        with transaction.atomic():
            job = AttributeMutationJob.objects.create(
                actor_seek_person_id=actor_identity["person_id"],
                actor_django_user_id=actor_identity["django_user_id"],
                actor_login=actor_identity["login"],
                actor_scheme=actor_identity["scheme"],
                actor_identity=dict(actor_identity),
                canonical_submitted_request_sha256=plan.canonical_submitted_request_sha256,
                canonical_submitted_request={"actor": dict(plan.actor), "request": plan.canonical_submitted_request},
                resolved_plan_sha256=hashlib.sha256(envelope_bytes).hexdigest(),
                resolved_plan_envelope=envelope_bytes,
                execution_mode=execution_mode,
                outbox_state="pending" if outbox_pending else "not_required",
                outbox_payload={"task": "attribute_mutations.run"} if outbox_pending else {},
            )
            attribute_fault("async.during_atomic_job_creation_after_job_before_partitions")
            for type_plan in plan.executable_types:
                AttributeMutationPartition.objects.create(
                    job=job, sample_type_id=type_plan.sample_type_id,
                    idempotency_key=type_plan.idempotency_key,
                    before_physical_fingerprint=type_plan.before_physical_fingerprint,
                    expected_after_semantic_fingerprint=type_plan.expected_after_semantic_fingerprint,
                    created_identity_tokens=[list(item) for item in type_plan.created_identity_tokens],
                )
        return job

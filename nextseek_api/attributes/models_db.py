"""Durable storage for native attribute-mutation jobs and partitions.

This module owns the sole default-database persistence boundary for T05/T07/T08
attribute mutation work: `AttributeMutationJob` (request/outbox/terminal state)
and `AttributeMutationPartition` (one row per `(job, sample_type_id)` owning
type-local CAS, claim, lease, idempotency, fingerprints, created-token
recovery, reconciliation, and outcome state).

Both models intentionally omit `_DATABASE` (see `seek.dbrouters`) so they live
on Django's `default` database, never on SEEK. No planner/executor/Celery
business logic lives here -- only the durable envelope, identity, and CAS
transition primitives that later tasks build on.

Product callers may not call `AttributeMutationJob.objects.create`,
`bulk_create`, or an unrestricted `save()` for initial audit creation; the
sole supported write surface is `AttributeMutationAuditStore.create_job`.
State changes after creation go through the named `cas_update` transition
methods, whose explicit mutable-field allowlists prevent rewriting immutable
audit identity (actor projection, submitted request/hash, resolved plan
bytes/hash, execution mode, job ID, partition sample-type ID, idempotency
key, planned fingerprints, and created tokens).
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from hashlib import sha256

import orjson
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils import timezone

PLAN_SCHEMA_VERSION = "attribute-mutation-plan/v1"
_ENVELOPE_KEYS = frozenset({"schema_version", "plan"})
_PLAN_KEYS = frozenset({
    "canonical_request_sha256",
    "plan_content_sha256",
    "execution_mode",
    "actor",
    "partition_sample_type_ids",
    "partition_plans",
})
_PARTITION_PLAN_KEYS = frozenset({
    "sample_type_id",
    "idempotency_key",
    "before_physical_fingerprint",
    "expected_after_semantic_fingerprint",
    "created_identity_tokens",
})
_ACTOR_KEYS = frozenset({"person_id", "django_user_id", "login", "scheme"})
_SUBMITTED_REQUEST_KEYS = frozenset({"actor", "request"})

MAX_SUBMITTED_REQUEST_BYTES = 4 * 1024 * 1024
MAX_PLAN_ENVELOPE_BYTES = 16 * 1024 * 1024

EXECUTION_MODES = ("synchronous", "asynchronous")


def _canonical_bytes(value) -> bytes:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS)


def _require_bytes(payload, field_name: str) -> bytes:
    if isinstance(payload, memoryview):
        payload = payload.tobytes()
    if not isinstance(payload, (bytes, bytearray)):
        raise ValidationError({field_name: ["must be a bytes-like canonical payload"]})
    return bytes(payload)


def require_canonical_json(payload, field_name: str, *, max_bytes: int):
    """Parse `payload` as JSON and require it round-trips to the exact same bytes
    under sorted-key canonical serialization. Rejects any non-canonical byte
    corruption (reordered keys, whitespace, truncation) before it ever reaches
    an INSERT."""
    raw = _require_bytes(payload, field_name)
    if not raw or len(raw) > max_bytes:
        raise ValidationError({field_name: [f"must be 1..{max_bytes} bytes"]})
    try:
        document = orjson.loads(raw)
    except orjson.JSONDecodeError as exc:
        raise ValidationError({field_name: ["must be valid JSON"]}) from exc
    if _canonical_bytes(document) != raw:
        raise ValidationError({field_name: ["must be exact canonical (sorted-key) JSON bytes"]})
    return document, raw


def require_hash(payload, expected_hex, field_name: str) -> None:
    """Recompute SHA-256 over `payload` bytes and require it equals `expected_hex`."""
    if not isinstance(expected_hex, str) or len(expected_hex) != 64 or any(
        character not in "0123456789abcdef" for character in expected_hex
    ):
        raise ValidationError({field_name: ["hash must be 64 lowercase hex characters"]})
    raw = payload.tobytes() if isinstance(payload, memoryview) else bytes(payload)
    if sha256(raw).hexdigest() != expected_hex:
        raise ValidationError({field_name: ["hash does not match recomputed digest"]})


def require_same_actor(*actors) -> None:
    """Require every supplied actor projection to be identical (defends against
    a TOCTOU drift between the live-authenticated, submitted, and resolved-plan
    actor identities across the auth -> validate -> plan -> audit pipeline)."""
    first = actors[0]
    if any(actor != first for actor in actors[1:]):
        raise ValidationError({"actor": ["authenticated, submitted, and resolved actor identities must match"]})


def _require_actor_shape(actor, field_name: str) -> dict:
    if not isinstance(actor, dict) or set(actor) != _ACTOR_KEYS:
        raise ValidationError({field_name: [f"actor must have exactly {sorted(_ACTOR_KEYS)}"]})
    if not isinstance(actor["person_id"], int) or isinstance(actor["person_id"], bool) or actor["person_id"] <= 0:
        raise ValidationError({field_name: ["actor.person_id must be a positive integer"]})
    return actor


class AttributeMutationJob(models.Model):
    """Durable audit record for one native attribute-mutation request.

    Owns request/outbox/terminal state. Exactly one normalized
    `AttributeMutationPartition` row exists per planned sample type; the job
    never duplicates per-type CAS/lease/fingerprint state.
    """

    job_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)

    # Immutable actor identity (DD-32/DD-34): the canonical T02 AuthenticatedSeekPerson
    # projection, stored both structured (for indexed lookup) and verbatim (for equality).
    actor_seek_person_id = models.BigIntegerField(db_index=True)
    actor_django_user_id = models.BigIntegerField()
    actor_login = models.CharField(max_length=255)
    actor_scheme = models.CharField(max_length=32)
    actor_identity = models.JSONField(default=dict)

    # Immutable submitted-request identity (pre-resolution).
    canonical_submitted_request_sha256 = models.CharField(max_length=64, db_index=True)
    canonical_submitted_request = models.JSONField(default=dict)

    # Immutable resolved-plan identity (post-resolution). Bytes are the exact
    # canonical `attribute-mutation-plan/v1` envelope; T03 stores and validates
    # the envelope shape but does not import T05's future semantic codec.
    resolved_plan_sha256 = models.CharField(max_length=64, db_index=True)
    resolved_plan_envelope = models.BinaryField()

    execution_mode = models.CharField(max_length=24, default="synchronous")

    # Mutable state (DD-12/DD-28/DD-29).
    state = models.CharField(max_length=32, default="accepted")

    # Six lease/CAS fields shared verbatim with AttributeMutationPartition (11.5).
    claim_owner = models.CharField(max_length=255, null=True, blank=True)
    claim_generation = models.PositiveBigIntegerField(default=0)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    lease_version = models.PositiveBigIntegerField(default=0)
    state_version = models.PositiveBigIntegerField(default=0)

    # Transactional outbox publication state (DD-28/DD-29): represented but not
    # published/consumed here -- no Celery dispatch in this task.
    outbox_state = models.CharField(max_length=24, default="not_required")
    outbox_payload = models.JSONField(default=dict, blank=True)
    outbox_attempts = models.PositiveIntegerField(default=0)
    outbox_last_error = models.TextField(null=True, blank=True)

    # Cancellation (DD-31): request/time/actor is durable; no transaction is
    # interrupted by this task.
    cancellation = models.JSONField(default=dict, blank=True)
    cancellation_requested_at = models.DateTimeField(null=True, blank=True)
    cancellation_actor_seek_person_id = models.BigIntegerField(null=True, blank=True)

    outcomes = models.JSONField(default=list, blank=True)
    terminal_result = models.JSONField(null=True, blank=True)
    http_classification = models.PositiveSmallIntegerField(null=True, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # Named CAS-mutable allowlist for this model (union with the shared base
    # vocabulary declared inline in cas_update). Immutable identity fields are
    # deliberately absent.
    JOB_MUTABLE_CAS_FIELDS = frozenset({
        "claim_owner",
        "claim_generation",
        "lease_expires_at",
        "last_heartbeat_at",
        "lease_version",
        "outbox_state",
        "outbox_payload",
        "outbox_attempts",
        "outbox_last_error",
        "cancellation",
        "cancellation_requested_at",
        "cancellation_actor_seek_person_id",
        "outcomes",
        "terminal_result",
        "http_classification",
        "started_at",
        "finished_at",
    })

    class Meta:
        db_table = "attributes_mutation_job"
        app_label = "nextseek_api"
        indexes = [
            models.Index(fields=["state", "lease_expires_at", "last_heartbeat_at"], name="attr_job_claim_scan"),
            models.Index(fields=["actor_seek_person_id", "created_at"], name="attr_job_actor_time"),
        ]

    def __str__(self) -> str:  # pragma: no cover - debugging aid only
        return f"AttributeMutationJob({self.job_id})"

    def cas_update(self, *, expected_state_version: int, transition: str, values: dict) -> bool:
        """Apply a named CAS transition: succeeds only if `state_version` still
        equals `expected_state_version`, atomically bumping it by one. `values`
        must be a subset of the mutable allowlist; identity fields can never be
        rewritten through this path."""
        MUTABLE_CAS_FIELDS = frozenset({"state", "state_version", "progress", "result"})
        allowed = MUTABLE_CAS_FIELDS | self.JOB_MUTABLE_CAS_FIELDS
        if not set(values) <= allowed or "state_version" in values:
            raise ValidationError({"cas_update": [f"transition {transition!r} touches a non-mutable field"]})
        updated = type(self).objects.filter(pk=self.pk, state_version=expected_state_version).update(
            state_version=models.F("state_version") + 1, **values
        )
        if updated:
            self.refresh_from_db()
        return bool(updated)

    def claim(self, *, expected_state_version: int, expected_claim_generation: int,
              expected_lease_version: int, owner: str, lease_seconds: int) -> bool:
        """Attempt to claim ownership using the reread six-field token. A
        successful claim affects exactly one row and increments
        `state_version`, `claim_generation`, and `lease_version` each by one;
        a stale/mismatched token affects zero rows and changes nothing."""
        now = timezone.now()
        updated = type(self).objects.filter(
            pk=self.pk,
            state_version=expected_state_version,
            claim_generation=expected_claim_generation,
            lease_version=expected_lease_version,
        ).update(
            state_version=models.F("state_version") + 1,
            claim_generation=models.F("claim_generation") + 1,
            lease_version=models.F("lease_version") + 1,
            claim_owner=owner,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            last_heartbeat_at=now,
        )
        if updated:
            self.refresh_from_db()
        return bool(updated)

    def release(self, *, expected_state_version: int) -> bool:
        """Release/terminal CAS: clears owner and timestamps but never
        decrements or clears `claim_generation` (DD-23)."""
        updated = type(self).objects.filter(pk=self.pk, state_version=expected_state_version).update(
            state_version=models.F("state_version") + 1,
            claim_owner=None,
            lease_expires_at=None,
            last_heartbeat_at=None,
        )
        if updated:
            self.refresh_from_db()
        return bool(updated)


class AttributeMutationPartition(models.Model):
    """Exactly one row per `(job, sample_type_id)`. Owns type-local CAS,
    claim, lease, idempotency, fingerprints, created-token recovery,
    reconciliation, and outcome state used by T07/T08.

    `before_physical_fingerprint`, `expected_after_semantic_fingerprint`, and
    `created_identity_tokens` are immutable once written by the constructor;
    later tasks may append `created_id_bindings` and
    `actual_after_physical_fingerprint` without rewriting either planned
    fingerprint.
    """

    job = models.ForeignKey(AttributeMutationJob, on_delete=models.CASCADE, related_name="partitions")
    sample_type_id = models.BigIntegerField()

    state = models.CharField(max_length=32, default="pending")

    claim_owner = models.CharField(max_length=255, null=True, blank=True)
    claim_generation = models.PositiveBigIntegerField(default=0)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    lease_version = models.PositiveBigIntegerField(default=0)
    state_version = models.PositiveBigIntegerField(default=0)

    # Immutable planned identity, written once by the constructor.
    idempotency_key = models.CharField(max_length=64)
    before_physical_fingerprint = models.CharField(max_length=64)
    expected_after_semantic_fingerprint = models.CharField(max_length=64)
    created_identity_tokens = models.JSONField(default=list)

    # Mutable execution-time state appended by T07/T08 without rewriting the
    # planned fingerprints above.
    created_id_bindings = models.JSONField(default=dict, blank=True)
    actual_after_physical_fingerprint = models.CharField(max_length=64, null=True, blank=True)
    progress = models.JSONField(default=dict, blank=True)
    reconciliation = models.JSONField(default=dict, blank=True)
    outcome = models.JSONField(null=True, blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    PARTITION_MUTABLE_CAS_FIELDS = frozenset({
        "claim_owner",
        "claim_generation",
        "lease_expires_at",
        "last_heartbeat_at",
        "lease_version",
        "created_id_bindings",
        "actual_after_physical_fingerprint",
        "progress",
        "reconciliation",
        "outcome",
        "started_at",
        "finished_at",
    })

    class Meta:
        db_table = "attributes_mutation_partition"
        app_label = "nextseek_api"
        constraints = [
            models.UniqueConstraint(fields=["job", "sample_type_id"], name="attr_part_job_type_uq"),
        ]
        indexes = [
            models.Index(fields=["job", "state", "lease_expires_at"], name="attr_part_job_claim"),
        ]

    def __str__(self) -> str:  # pragma: no cover - debugging aid only
        return f"AttributeMutationPartition(job={self.job_id}, sample_type_id={self.sample_type_id})"

    def cas_update(self, *, expected_state_version: int, transition: str, values: dict) -> bool:
        BASE_MUTABLE_CAS_FIELDS = frozenset({"state", "state_version"})
        allowed = BASE_MUTABLE_CAS_FIELDS | self.PARTITION_MUTABLE_CAS_FIELDS
        if not set(values) <= allowed or "state_version" in values:
            raise ValidationError({"cas_update": [f"transition {transition!r} touches a non-mutable field"]})
        updated = type(self).objects.filter(pk=self.pk, state_version=expected_state_version).update(
            state_version=models.F("state_version") + 1, **values
        )
        if updated:
            self.refresh_from_db()
        return bool(updated)

    def claim(self, *, expected_state_version: int, expected_claim_generation: int,
              expected_lease_version: int, owner: str, lease_seconds: int) -> bool:
        now = timezone.now()
        updated = type(self).objects.filter(
            pk=self.pk,
            state_version=expected_state_version,
            claim_generation=expected_claim_generation,
            lease_version=expected_lease_version,
        ).update(
            state_version=models.F("state_version") + 1,
            claim_generation=models.F("claim_generation") + 1,
            lease_version=models.F("lease_version") + 1,
            claim_owner=owner,
            lease_expires_at=now + __import__("datetime").timedelta(seconds=lease_seconds),
            last_heartbeat_at=now,
        )
        if updated:
            self.refresh_from_db()
        return bool(updated)


class AttributeMutationAuditStore:
    """The sole T03-owned initial-write service. Product code must not call
    `AttributeMutationJob.objects.create`, `bulk_create`, or an unrestricted
    `save()` to create audit rows; `create_job` is the only supported path."""

    @staticmethod
    def create_job(*, actor, canonical_submitted_request, resolved_plan_envelope, execution_mode):
        authenticated_actor = _require_actor_shape(dict(actor), "actor")
        if execution_mode not in EXECUTION_MODES:
            raise ValidationError({"execution_mode": [f"must be one of {EXECUTION_MODES}"]})

        submitted_document, submitted_raw = require_canonical_json(
            canonical_submitted_request, "canonical_submitted_request", max_bytes=MAX_SUBMITTED_REQUEST_BYTES
        )
        if not isinstance(submitted_document, dict) or set(submitted_document) != _SUBMITTED_REQUEST_KEYS:
            raise ValidationError({"canonical_submitted_request": [f"must have exactly {sorted(_SUBMITTED_REQUEST_KEYS)}"]})
        submitted_actor = _require_actor_shape(submitted_document["actor"], "canonical_submitted_request.actor")
        submitted_sha = sha256(canonical_submitted_request).hexdigest()

        resolved_document, resolved_raw = require_canonical_json(
            resolved_plan_envelope, "resolved_plan_envelope", max_bytes=MAX_PLAN_ENVELOPE_BYTES
        )
        if (
            not isinstance(resolved_document, dict)
            or set(resolved_document) != _ENVELOPE_KEYS
            or resolved_document.get("schema_version") != PLAN_SCHEMA_VERSION
        ):
            raise ValidationError({"resolved_plan_envelope": [f"must be a {PLAN_SCHEMA_VERSION} envelope"]})
        plan = resolved_document["plan"]
        if not isinstance(plan, dict) or set(plan) != _PLAN_KEYS:
            raise ValidationError({"resolved_plan_envelope": [f"plan must have exactly {sorted(_PLAN_KEYS)}"]})

        # Embedded provenance link: the resolved plan must carry the exact
        # submitted-request hash it was resolved from.
        require_hash(canonical_submitted_request, plan.get("canonical_request_sha256"), "canonical_request_sha256")

        # Self-declared plan-content integrity: independent of the submitted
        # link, guards against silent drift in the resolved business content.
        content_without_hash = {key: value for key, value in plan.items() if key != "plan_content_sha256"}
        require_hash(_canonical_bytes(content_without_hash), plan.get("plan_content_sha256"), "plan_content_sha256")

        # Envelope-bytes integrity gate immediately before the write.
        resolved_plan_sha256 = sha256(resolved_plan_envelope).hexdigest()
        require_hash(resolved_plan_envelope, resolved_plan_sha256, "resolved_plan")

        resolved_actor = _require_actor_shape(plan.get("actor"), "resolved_plan_envelope.plan.actor")
        require_same_actor(authenticated_actor, submitted_actor, resolved_actor)

        partition_plans = plan.get("partition_plans")
        if not isinstance(partition_plans, list) or not partition_plans:
            raise ValidationError({"resolved_plan_envelope": ["plan.partition_plans must be a non-empty list"]})
        parsed_partitions = []
        seen_type_ids = set()
        for entry in partition_plans:
            if not isinstance(entry, dict) or set(entry) != _PARTITION_PLAN_KEYS:
                raise ValidationError({"resolved_plan_envelope": [f"each partition plan must have exactly {sorted(_PARTITION_PLAN_KEYS)}"]})
            type_id = entry["sample_type_id"]
            if not isinstance(type_id, int) or isinstance(type_id, bool) or type_id <= 0:
                raise ValidationError({"resolved_plan_envelope": ["sample_type_id must be a positive integer"]})
            if type_id in seen_type_ids:
                raise ValidationError({"resolved_plan_envelope": ["sample_type_id must be unique across partition_plans"]})
            seen_type_ids.add(type_id)
            parsed_partitions.append(entry)

        declared_ids = plan.get("partition_sample_type_ids")
        detail_ids = sorted(seen_type_ids)
        if declared_ids != detail_ids:
            raise ValidationError({"partition_sample_type_ids": ["must equal the decoded executable partition set"]})

        with transaction.atomic(using="default"):
            job = AttributeMutationJob(
                actor_seek_person_id=authenticated_actor["person_id"],
                actor_django_user_id=authenticated_actor["django_user_id"],
                actor_login=authenticated_actor["login"],
                actor_scheme=authenticated_actor["scheme"],
                actor_identity=dict(authenticated_actor),
                canonical_submitted_request_sha256=submitted_sha,
                canonical_submitted_request=submitted_document,
                resolved_plan_sha256=resolved_plan_sha256,
                resolved_plan_envelope=resolved_raw,
                execution_mode=execution_mode,
            )
            job.full_clean()
            job.save()
            partitions = []
            for entry in parsed_partitions:
                partition = AttributeMutationPartition(
                    job=job,
                    sample_type_id=entry["sample_type_id"],
                    idempotency_key=entry["idempotency_key"],
                    before_physical_fingerprint=entry["before_physical_fingerprint"],
                    expected_after_semantic_fingerprint=entry["expected_after_semantic_fingerprint"],
                    created_identity_tokens=entry["created_identity_tokens"],
                )
                partition.full_clean()
                partition.save()
                partitions.append(partition)
        return job, partitions

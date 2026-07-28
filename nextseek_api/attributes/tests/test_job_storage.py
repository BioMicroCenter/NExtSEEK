"""Section 11.5 pinned real-boundary contract for the T03 audit-store write
surface: `AttributeMutationAuditStore.create_job`, its identity/actor/partition
guards, and the `AttributeMutationJob`/`AttributeMutationPartition` CAS/claim
primitives.

These tests run against Django's `default` database (the disposable per-test
database provisioned by pytest-django from `dmac.test_settings`), never
against SEEK. Every node name and parametrize ID below is pinned exactly by
task-03 Section 11.5/11.7 and must not be renamed or collapsed.
"""
from __future__ import annotations

from hashlib import sha256

import orjson
import pytest
from django.core.exceptions import ValidationError
from django.db import connections

from nextseek_api.attributes import models_db
from nextseek_api.attributes.models_db import (
    AttributeMutationAuditStore,
    AttributeMutationJob,
    AttributeMutationPartition,
)

pytestmark = pytest.mark.django_db


def _canonical_bytes(value) -> bytes:
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS)


def _actor(person_id: int) -> dict:
    return {
        "person_id": person_id,
        "django_user_id": person_id * 10,
        "login": f"user{person_id}",
        "scheme": "basic",
    }


def _partition_plan(sample_type_id: int) -> dict:
    return {
        "sample_type_id": sample_type_id,
        "idempotency_key": f"idem-{sample_type_id}",
        "before_physical_fingerprint": sha256(f"before-{sample_type_id}".encode()).hexdigest(),
        "expected_after_semantic_fingerprint": sha256(f"after-{sample_type_id}".encode()).hexdigest(),
        "created_identity_tokens": [["type-title", f"tok-{sample_type_id}"]],
    }


def _build_submission(actor: dict, request_body: dict) -> tuple[dict, bytes]:
    document = {"actor": actor, "request": request_body}
    return document, _canonical_bytes(document)


def _envelope_from_plan_content(plan_content: dict) -> tuple[dict, bytes]:
    content_sha256 = sha256(_canonical_bytes(plan_content)).hexdigest()
    plan = dict(plan_content)
    plan["plan_content_sha256"] = content_sha256
    envelope = {"schema_version": models_db.PLAN_SCHEMA_VERSION, "plan": plan}
    return envelope, _canonical_bytes(envelope)


def _build_envelope(actor: dict, submitted_bytes: bytes, sample_type_ids: list[int],
                     execution_mode: str = "synchronous") -> tuple[dict, bytes]:
    plan_content = {
        "canonical_request_sha256": sha256(submitted_bytes).hexdigest(),
        "execution_mode": execution_mode,
        "actor": actor,
        "partition_sample_type_ids": sorted(sample_type_ids),
        "partition_plans": [_partition_plan(type_id) for type_id in sample_type_ids],
    }
    return _envelope_from_plan_content(plan_content)


def _create_job(*, actor: dict | None = None, sample_type_ids=(7,), execution_mode: str = "synchronous"):
    actor = actor or _actor(1)
    submitted_document, submitted_bytes = _build_submission(
        actor, {"target": "sample_types", "sample_type_ids": list(sample_type_ids)}
    )
    envelope, envelope_bytes = _build_envelope(actor, submitted_bytes, list(sample_type_ids), execution_mode)
    return AttributeMutationAuditStore.create_job(
        actor=actor,
        canonical_submitted_request=submitted_bytes,
        resolved_plan_envelope=envelope_bytes,
        execution_mode=execution_mode,
    )


def test_supported_constructor_restart_round_trip():
    actor = _actor(7)
    job, partitions = _create_job(actor=actor, sample_type_ids=(3, 9))
    job_id = job.job_id
    submitted_sha = job.canonical_submitted_request_sha256
    submitted_document = dict(job.canonical_submitted_request)
    resolved_sha = job.resolved_plan_sha256
    envelope_bytes = bytes(job.resolved_plan_envelope)
    assert {partition.sample_type_id for partition in partitions} == {3, 9}

    # Simulate a fresh process read: drop the cached connection and re-fetch
    # by primary identity only, never reusing the in-memory Python objects.
    connections["default"].close()
    fresh_job = AttributeMutationJob.objects.get(job_id=job_id)
    assert fresh_job.canonical_submitted_request_sha256 == submitted_sha
    assert dict(fresh_job.canonical_submitted_request) == submitted_document
    assert fresh_job.resolved_plan_sha256 == resolved_sha
    assert bytes(fresh_job.resolved_plan_envelope) == envelope_bytes
    assert dict(fresh_job.actor_identity) == actor

    fresh_partitions = list(
        AttributeMutationPartition.objects.filter(job=fresh_job).order_by("sample_type_id")
    )
    assert [partition.sample_type_id for partition in fresh_partitions] == [3, 9]
    for stored, expected in zip(fresh_partitions, sorted(partitions, key=lambda item: item.sample_type_id)):
        assert stored.idempotency_key == expected.idempotency_key
        assert stored.before_physical_fingerprint == expected.before_physical_fingerprint
        assert stored.expected_after_semantic_fingerprint == expected.expected_after_semantic_fingerprint
        assert list(stored.created_identity_tokens) == list(expected.created_identity_tokens)


@pytest.mark.parametrize("corruption", ["submitted-bytes", "submitted-hash", "resolved-bytes", "resolved-hash"])
def test_submitted_and_resolved_identity_corruption_rejected(corruption):
    actor = _actor(11)
    submitted_document, submitted_bytes = _build_submission(actor, {"target": "sample_types", "sample_type_ids": [4]})
    envelope, envelope_bytes = _build_envelope(actor, submitted_bytes, [4])

    if corruption == "submitted-bytes":
        submitted_bytes = submitted_bytes + b" "
    elif corruption == "submitted-hash":
        plan_content = {
            "canonical_request_sha256": "0" * 64,  # false provenance link
            "execution_mode": "synchronous",
            "actor": actor,
            "partition_sample_type_ids": [4],
            "partition_plans": [_partition_plan(4)],
        }
        envelope, envelope_bytes = _envelope_from_plan_content(plan_content)
    elif corruption == "resolved-bytes":
        envelope_bytes = envelope_bytes + b" "
    elif corruption == "resolved-hash":
        plan = dict(envelope["plan"])
        plan["plan_content_sha256"] = "0" * 64  # false self-declared content hash
        envelope = {"schema_version": envelope["schema_version"], "plan": plan}
        envelope_bytes = _canonical_bytes(envelope)
    else:  # pragma: no cover - exhaustive parametrize guard
        raise AssertionError(corruption)

    with pytest.raises(ValidationError):
        AttributeMutationAuditStore.create_job(
            actor=actor,
            canonical_submitted_request=submitted_bytes,
            resolved_plan_envelope=envelope_bytes,
            execution_mode="synchronous",
        )
    assert AttributeMutationJob.objects.count() == 0
    assert AttributeMutationPartition.objects.count() == 0


@pytest.mark.parametrize("mismatch", ["actor", "partition"])
def test_supported_constructor_rejects_actor_and_partition_mismatch(mismatch):
    authenticated_actor = _actor(21)
    if mismatch == "actor":
        submitted_actor = _actor(99)  # diverges from the live-authenticated identity
        submitted_document, submitted_bytes = _build_submission(
            submitted_actor, {"target": "sample_types", "sample_type_ids": [5]}
        )
        envelope, envelope_bytes = _build_envelope(authenticated_actor, submitted_bytes, [5])
    elif mismatch == "partition":
        submitted_document, submitted_bytes = _build_submission(
            authenticated_actor, {"target": "sample_types", "sample_type_ids": [5]}
        )
        plan_content = {
            "canonical_request_sha256": sha256(submitted_bytes).hexdigest(),
            "execution_mode": "synchronous",
            "actor": authenticated_actor,
            "partition_sample_type_ids": [5, 6],  # declared set diverges from partition_plans below
            "partition_plans": [_partition_plan(5)],
        }
        envelope, envelope_bytes = _envelope_from_plan_content(plan_content)
    else:  # pragma: no cover - exhaustive parametrize guard
        raise AssertionError(mismatch)

    with pytest.raises(ValidationError):
        AttributeMutationAuditStore.create_job(
            actor=authenticated_actor,
            canonical_submitted_request=submitted_bytes,
            resolved_plan_envelope=envelope_bytes,
            execution_mode="synchronous",
        )
    assert AttributeMutationJob.objects.count() == 0


def test_immutable_identity_and_cas_allowlist_enforced():
    from django.db import models as django_models

    claim_owner_field = AttributeMutationJob._meta.get_field("claim_owner")
    assert isinstance(claim_owner_field, django_models.CharField)
    assert claim_owner_field.max_length == 255
    assert claim_owner_field.null is True and claim_owner_field.blank is True

    for name in ("lease_expires_at", "last_heartbeat_at"):
        field = AttributeMutationJob._meta.get_field(name)
        assert isinstance(field, django_models.DateTimeField)
        assert field.null is True

    for name in ("claim_generation", "lease_version", "state_version"):
        field = AttributeMutationJob._meta.get_field(name)
        assert isinstance(field, django_models.PositiveBigIntegerField)
        assert field.null is False
        assert field.default == 0

    job_index_names = {index.name for index in AttributeMutationJob._meta.indexes}
    assert "attr_job_claim_scan" in job_index_names
    job_scan_index = next(index for index in AttributeMutationJob._meta.indexes if index.name == "attr_job_claim_scan")
    assert list(job_scan_index.fields) == ["state", "lease_expires_at", "last_heartbeat_at"]

    partition_index_names = {index.name for index in AttributeMutationPartition._meta.indexes}
    assert "attr_part_job_claim" in partition_index_names
    partition_scan_index = next(
        index for index in AttributeMutationPartition._meta.indexes if index.name == "attr_part_job_claim"
    )
    assert list(partition_scan_index.fields) == ["job", "state", "lease_expires_at"]

    job, _partitions = _create_job(sample_type_ids=(41,))
    token = {
        "expected_state_version": job.state_version,
        "expected_claim_generation": job.claim_generation,
        "expected_lease_version": job.lease_version,
    }

    claimed = job.claim(**token, owner="worker:test-owner", lease_seconds=60)
    assert claimed is True
    assert job.state_version == token["expected_state_version"] + 1
    assert job.claim_generation == token["expected_claim_generation"] + 1
    assert job.lease_version == token["expected_lease_version"] + 1
    assert job.claim_owner == "worker:test-owner"

    # Reuse of the now-stale token affects zero rows and changes nothing.
    stale_claim = job.claim(**token, owner="worker:other-owner", lease_seconds=60)
    assert stale_claim is False
    unchanged = AttributeMutationJob.objects.get(pk=job.pk)
    assert unchanged.claim_owner == "worker:test-owner"
    assert unchanged.claim_generation == token["expected_claim_generation"] + 1

    # Unsupported writes cannot rewrite immutable identity or reach outside
    # the named CAS allowlist.
    with pytest.raises(ValidationError):
        job.cas_update(
            expected_state_version=job.state_version,
            transition="bad-transition",
            values={"canonical_submitted_request_sha256": "0" * 64},
        )
    with pytest.raises(ValidationError):
        job.cas_update(
            expected_state_version=job.state_version,
            transition="bad-transition",
            values={"resolved_plan_envelope": b"tampered"},
        )

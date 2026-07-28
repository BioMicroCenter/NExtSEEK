"""Supplemental T03 coverage tests for `nextseek_api.attributes.models_db`.

These are *not* part of the Section 11.5 pinned real-boundary contract (that
list lives exactly in `test_job_storage.py`); this module exists solely to
exercise remaining validation, CAS, claim, and release branches so the
frozen `coverage` lane's 95% owned-module gate is met. No pinned node name
or parametrize ID is defined here.
"""
from __future__ import annotations

from hashlib import sha256

import pytest
from django.core.exceptions import ValidationError

from nextseek_api.attributes.models_db import (
    AttributeMutationAuditStore,
    AttributeMutationPartition,
    _require_actor_shape,
    _require_bytes,
    require_canonical_json,
    require_hash,
)
from nextseek_api.attributes.tests.test_job_storage import (
    _actor,
    _build_envelope,
    _build_submission,
    _canonical_bytes,
    _create_job,
    _envelope_from_plan_content,
    _partition_plan,
)

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Free-function validation helpers.
# ---------------------------------------------------------------------------

def test_require_bytes_accepts_memoryview():
    assert _require_bytes(memoryview(b"abc"), "field") == b"abc"


def test_require_bytes_rejects_non_bytes_like():
    with pytest.raises(ValidationError, match="bytes-like"):
        _require_bytes("not-bytes", "field")


def test_require_canonical_json_rejects_empty_payload():
    with pytest.raises(ValidationError, match="1\\.\\."):
        require_canonical_json(b"", "field", max_bytes=1024)


def test_require_canonical_json_rejects_oversized_payload():
    with pytest.raises(ValidationError, match="1\\.\\."):
        require_canonical_json(b"{}" + b" " * 1024, "field", max_bytes=4)


def test_require_canonical_json_rejects_invalid_json():
    with pytest.raises(ValidationError, match="valid JSON"):
        require_canonical_json(b"{not json", "field", max_bytes=1024)


def test_require_canonical_json_rejects_non_canonical_bytes():
    with pytest.raises(ValidationError, match="canonical"):
        require_canonical_json(b'{"b": 1, "a": 2}', "field", max_bytes=1024)


def test_require_hash_rejects_malformed_hex():
    with pytest.raises(ValidationError, match="64 lowercase hex"):
        require_hash(b"payload", "not-hex", "field")
    with pytest.raises(ValidationError, match="64 lowercase hex"):
        require_hash(b"payload", "F" * 64, "field")  # uppercase rejected


def test_require_hash_rejects_mismatched_digest():
    with pytest.raises(ValidationError, match="does not match"):
        require_hash(b"payload", "0" * 64, "field")


def test_require_actor_shape_rejects_wrong_keys():
    with pytest.raises(ValidationError, match="exactly"):
        _require_actor_shape({"person_id": 1}, "actor")


@pytest.mark.parametrize("bad_person_id", [None, 0, -1, True, "1"])
def test_require_actor_shape_rejects_bad_person_id(bad_person_id):
    actor = {"person_id": bad_person_id, "django_user_id": 1, "login": "x", "scheme": "basic"}
    with pytest.raises(ValidationError, match="positive integer"):
        _require_actor_shape(actor, "actor")


# ---------------------------------------------------------------------------
# AttributeMutationJob CAS / claim / release.
# ---------------------------------------------------------------------------

def test_job_cas_update_rejects_state_version_in_values():
    job, _partitions = _create_job(sample_type_ids=(101,))
    with pytest.raises(ValidationError, match="non-mutable field"):
        job.cas_update(expected_state_version=0, transition="bad", values={"state_version": 5})


def test_job_cas_update_allows_mutable_field_and_bumps_version():
    job, _partitions = _create_job(sample_type_ids=(102,))
    updated = job.cas_update(
        expected_state_version=job.state_version, transition="outbox-sent",
        values={"outbox_state": "sent"},
    )
    assert updated is True
    assert job.outbox_state == "sent"
    assert job.state_version == 1


def test_job_release_clears_owner_and_lease_without_touching_generation():
    job, _partitions = _create_job(sample_type_ids=(103,))
    token = {
        "expected_state_version": job.state_version,
        "expected_claim_generation": job.claim_generation,
        "expected_lease_version": job.lease_version,
    }
    assert job.claim(**token, owner="worker:a", lease_seconds=60) is True
    generation_after_claim = job.claim_generation

    released = job.release(expected_state_version=job.state_version)
    assert released is True
    assert job.claim_owner is None
    assert job.lease_expires_at is None
    assert job.last_heartbeat_at is None
    assert job.claim_generation == generation_after_claim

    stale_release = job.release(expected_state_version=job.state_version - 5)
    assert stale_release is False


# ---------------------------------------------------------------------------
# AttributeMutationPartition CAS / claim.
# ---------------------------------------------------------------------------

def _first_partition(sample_type_id: int) -> AttributeMutationPartition:
    _job, partitions = _create_job(sample_type_ids=(sample_type_id,))
    return partitions[0]


def test_partition_cas_update_rejects_disallowed_field():
    partition = _first_partition(201)
    with pytest.raises(ValidationError, match="non-mutable field"):
        partition.cas_update(
            expected_state_version=0, transition="bad", values={"idempotency_key": "hacked"}
        )


def test_partition_cas_update_allows_mutable_field_and_bumps_version():
    partition = _first_partition(202)
    updated = partition.cas_update(
        expected_state_version=partition.state_version, transition="progress",
        values={"progress": {"step": 1}},
    )
    assert updated is True
    assert partition.progress == {"step": 1}
    assert partition.state_version == 1


def test_partition_claim_succeeds_once_and_rejects_stale_token():
    partition = _first_partition(203)
    token = {
        "expected_state_version": partition.state_version,
        "expected_claim_generation": partition.claim_generation,
        "expected_lease_version": partition.lease_version,
    }
    claimed = partition.claim(**token, owner="worker:p", lease_seconds=30)
    assert claimed is True
    assert partition.claim_owner == "worker:p"
    assert partition.state_version == 1
    assert partition.claim_generation == 1
    assert partition.lease_version == 1

    stale = partition.claim(**token, owner="worker:q", lease_seconds=30)
    assert stale is False
    unchanged = AttributeMutationPartition.objects.get(pk=partition.pk)
    assert unchanged.claim_owner == "worker:p"


# ---------------------------------------------------------------------------
# AttributeMutationAuditStore.create_job malformed-shape rejections.
# ---------------------------------------------------------------------------

def _valid_submission_and_envelope(actor, sample_type_ids=(9,)):
    _submitted_document, submitted_bytes = _build_submission(
        actor, {"target": "sample_types", "sample_type_ids": list(sample_type_ids)}
    )
    envelope, envelope_bytes = _build_envelope(actor, submitted_bytes, list(sample_type_ids))
    return submitted_bytes, envelope, envelope_bytes


def test_create_job_rejects_unsupported_execution_mode():
    actor = _actor(201)
    submitted_bytes, _envelope, envelope_bytes = _valid_submission_and_envelope(actor)
    with pytest.raises(ValidationError, match="execution_mode"):
        AttributeMutationAuditStore.create_job(
            actor=actor, canonical_submitted_request=submitted_bytes,
            resolved_plan_envelope=envelope_bytes, execution_mode="eventual",
        )


def test_create_job_rejects_submitted_document_with_wrong_keys():
    actor = _actor(202)
    document = {"actor": actor, "request": {}, "extra": True}
    submitted_bytes = _canonical_bytes(document)
    _submitted_bytes, _envelope, envelope_bytes = _valid_submission_and_envelope(actor)
    with pytest.raises(ValidationError, match="canonical_submitted_request"):
        AttributeMutationAuditStore.create_job(
            actor=actor, canonical_submitted_request=submitted_bytes,
            resolved_plan_envelope=envelope_bytes, execution_mode="synchronous",
        )


def test_create_job_rejects_resolved_envelope_with_wrong_schema_version():
    actor = _actor(203)
    submitted_bytes, envelope, _envelope_bytes = _valid_submission_and_envelope(actor)
    bad_envelope = {"schema_version": "attribute-mutation-plan/v9", "plan": envelope["plan"]}
    with pytest.raises(ValidationError, match="resolved_plan_envelope"):
        AttributeMutationAuditStore.create_job(
            actor=actor, canonical_submitted_request=submitted_bytes,
            resolved_plan_envelope=_canonical_bytes(bad_envelope), execution_mode="synchronous",
        )


def test_create_job_rejects_plan_with_wrong_keys():
    actor = _actor(204)
    submitted_bytes, envelope, _envelope_bytes = _valid_submission_and_envelope(actor)
    plan = dict(envelope["plan"])
    plan["extra_field"] = True
    bad_envelope = {"schema_version": envelope["schema_version"], "plan": plan}
    with pytest.raises(ValidationError, match="plan must have exactly"):
        AttributeMutationAuditStore.create_job(
            actor=actor, canonical_submitted_request=submitted_bytes,
            resolved_plan_envelope=_canonical_bytes(bad_envelope), execution_mode="synchronous",
        )


def test_create_job_rejects_non_list_partition_plans():
    actor = _actor(205)
    _submitted_document, submitted_bytes = _build_submission(
        actor, {"target": "sample_types", "sample_type_ids": [9]}
    )
    plan_content = {
        "canonical_request_sha256": sha256(submitted_bytes).hexdigest(),
        "execution_mode": "synchronous",
        "actor": actor,
        "partition_sample_type_ids": [9],
        "partition_plans": "not-a-list",
    }
    envelope, envelope_bytes = _envelope_from_plan_content(plan_content)
    with pytest.raises(ValidationError, match="non-empty list"):
        AttributeMutationAuditStore.create_job(
            actor=actor, canonical_submitted_request=submitted_bytes,
            resolved_plan_envelope=envelope_bytes, execution_mode="synchronous",
        )


def test_create_job_rejects_empty_partition_plans():
    actor = _actor(206)
    _submitted_document, submitted_bytes = _build_submission(
        actor, {"target": "sample_types", "sample_type_ids": []}
    )
    plan_content = {
        "canonical_request_sha256": sha256(submitted_bytes).hexdigest(),
        "execution_mode": "synchronous",
        "actor": actor,
        "partition_sample_type_ids": [],
        "partition_plans": [],
    }
    envelope, envelope_bytes = _envelope_from_plan_content(plan_content)
    with pytest.raises(ValidationError, match="non-empty list"):
        AttributeMutationAuditStore.create_job(
            actor=actor, canonical_submitted_request=submitted_bytes,
            resolved_plan_envelope=envelope_bytes, execution_mode="synchronous",
        )


def test_create_job_rejects_partition_entry_with_wrong_keys():
    actor = _actor(207)
    _submitted_document, submitted_bytes = _build_submission(
        actor, {"target": "sample_types", "sample_type_ids": [9]}
    )
    bad_entry = {**_partition_plan(9), "extra": True}
    plan_content = {
        "canonical_request_sha256": sha256(submitted_bytes).hexdigest(),
        "execution_mode": "synchronous",
        "actor": actor,
        "partition_sample_type_ids": [9],
        "partition_plans": [bad_entry],
    }
    envelope, envelope_bytes = _envelope_from_plan_content(plan_content)
    with pytest.raises(ValidationError, match="each partition plan must have exactly"):
        AttributeMutationAuditStore.create_job(
            actor=actor, canonical_submitted_request=submitted_bytes,
            resolved_plan_envelope=envelope_bytes, execution_mode="synchronous",
        )


@pytest.mark.parametrize("bad_type_id", [0, -1, True, "9"])
def test_create_job_rejects_non_positive_sample_type_id(bad_type_id):
    actor = _actor(208)
    _submitted_document, submitted_bytes = _build_submission(
        actor, {"target": "sample_types", "sample_type_ids": [9]}
    )
    entry = {**_partition_plan(9), "sample_type_id": bad_type_id}
    plan_content = {
        "canonical_request_sha256": sha256(submitted_bytes).hexdigest(),
        "execution_mode": "synchronous",
        "actor": actor,
        "partition_sample_type_ids": [9],
        "partition_plans": [entry],
    }
    envelope, envelope_bytes = _envelope_from_plan_content(plan_content)
    with pytest.raises(ValidationError, match="positive integer"):
        AttributeMutationAuditStore.create_job(
            actor=actor, canonical_submitted_request=submitted_bytes,
            resolved_plan_envelope=envelope_bytes, execution_mode="synchronous",
        )


def test_create_job_rejects_duplicate_sample_type_id_across_partition_plans():
    actor = _actor(209)
    _submitted_document, submitted_bytes = _build_submission(
        actor, {"target": "sample_types", "sample_type_ids": [9]}
    )
    plan_content = {
        "canonical_request_sha256": sha256(submitted_bytes).hexdigest(),
        "execution_mode": "synchronous",
        "actor": actor,
        "partition_sample_type_ids": [9],
        "partition_plans": [_partition_plan(9), _partition_plan(9)],
    }
    envelope, envelope_bytes = _envelope_from_plan_content(plan_content)
    with pytest.raises(ValidationError, match="unique across partition_plans"):
        AttributeMutationAuditStore.create_job(
            actor=actor, canonical_submitted_request=submitted_bytes,
            resolved_plan_envelope=envelope_bytes, execution_mode="synchronous",
        )

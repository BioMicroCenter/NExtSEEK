"""Fast behavioral gate for the V4-9 deploy record and compatibility harness."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy

import pytest
from pydantic import ValidationError

from nextseek_api.eval.deploy_record import (
    DataIdentity,
    DeployRecord,
    GenerationIdentity,
    GitIdentity,
    RuntimeIdentity,
    SchemaIdentity,
    deploy_record_schema,
)
from nextseek_api.eval.mixed_version_recovery import (
    ContractPhaseRefused,
    DestructiveRecoveryRefused,
    IdentityRefused,
    MixedVersionHarness,
    RecoveryAction,
    RecoveryOrderRefused,
)


def _digest(char: str) -> str:
    return "sha256:" + char * 64


def _runtime(identity_id: str, *, release: str, role: str) -> RuntimeIdentity:
    return RuntimeIdentity(
        identity_id=identity_id,
        release=release,
        role=role,
        source_sha=("1" if release == "old" else "2") * 40,
        image_digest=_digest("a" if release == "old" else "b"),
        owner="plan018-harness",
        min_schema_generation=1,
        max_schema_generation=3,
        queue_generation=1 if release == "old" else 2,
    )


def _record_payload() -> dict:
    runtimes = (
        _runtime("old-web", release="old", role="web"),
        _runtime("new-web", release="new", role="web"),
        _runtime("old-worker", release="old", role="worker"),
        _runtime("new-worker", release="new", role="worker"),
    )
    return {
        "schema_version": "plan018-deploy-record/v1",
        "deploy_id": "v4-9-disposable-001",
        "created_at": "2026-08-18T17:00:00Z",
        "owner": "plan018-harness",
        "phase": "expand",
        "git": GitIdentity(source_sha="2" * 40, diff_sha256="3" * 64),
        "images": {"prior": _digest("a"), "candidate": _digest("b")},
        "schema": SchemaIdentity(
            generation=2,
            migration_leaf="0019_merge_attribute_async_turn_ledger",
            migrations=("0017_paid_run_state", "0019_merge_attribute_async_turn_ledger"),
            fingerprint="4" * 64,
        ),
        "settings_sha256": "5" * 64,
        "schedule_state": {"paid_eval": False, "reconciliation": True},
        "flag_state": {"posterior_routing": True, "paid_eval": False},
        "generations": GenerationIdentity(active="6" * 64, prior="7" * 64),
        "data": {
            "database_sha256": "8" * 64,
            "artifact_sha256": "9" * 64,
            "tombstone_sha256": "a" * 64,
            "row_counts": {
                "judgments": 3,
                "exclusions": 2,
                "pending_attempts": 1,
                "failed_attempts": 1,
                "reservations": 1,
                "tombstones": 1,
            },
        },
        "network_identity": "isolated-plan018-v4-9",
        "runtime_identities": runtimes,
        "smoke_checks": {"schema": True, "selector": True, "worker": True},
    }


def _record() -> DeployRecord:
    return DeployRecord.model_validate(_record_payload())


def _replace_runtime(payload: dict, index: int, **changes) -> None:
    identities = list(payload["runtime_identities"])
    runtime = identities[index].model_dump()
    runtime.update(changes)
    identities[index] = runtime
    payload["runtime_identities"] = identities


def _schema_without_leaf(payload: dict) -> None:
    schema = payload["schema"].model_dump()
    schema["migration_leaf"] = "0018_missing_from_set"
    payload["schema"] = schema


def _missing_seed_category(payload: dict) -> None:
    del payload["data"]["row_counts"]["tombstones"]


def _duplicate_runtime_id(payload: dict) -> None:
    _replace_runtime(payload, 1, identity_id="old-web")


def test_deploy_record_schema_is_closed_and_round_trips() -> None:
    record = _record()
    schema = deploy_record_schema()

    assert record.model_dump(mode="json")["schema_version"] == "plan018-deploy-record/v1"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) >= {
        "git",
        "images",
        "schema",
        "generations",
        "data",
        "runtime_identities",
    }
    assert DeployRecord.model_validate_json(record.model_dump_json()) == record


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (lambda p: p.update(phase="contract"), "phase"),
        (lambda p: p["images"].update(candidate="nextseek:latest"), "pattern"),
        (lambda p: p.update(runtime_identities=p["runtime_identities"][:-1]), "old/new web and worker"),
        (lambda p: p["smoke_checks"].update(worker=False), "smoke"),
        (lambda p: p["data"]["row_counts"].update(tombstones=0), "greater than or equal"),
        (lambda p: p.update(generations={"active": "6" * 64, "prior": "6" * 64}), "distinct"),
        (_schema_without_leaf, "leaf"),
        (_missing_seed_category, "non-empty"),
        (lambda p: _replace_runtime(p, 0, min_schema_generation=3, max_schema_generation=2), "inverted"),
        (lambda p: p.update(created_at="2026-08-18T17:00:00"), "timezone"),
        (lambda p: p.update(images={"candidate": _digest("b")}), "exact prior"),
        (lambda p: p["images"].update(candidate=_digest("a")), "distinct"),
        (lambda p: p.update(schedule_state={}), "non-empty"),
        (_duplicate_runtime_id, "unique"),
        (lambda p: _replace_runtime(p, 0, image_digest=_digest("f")), "stale"),
        (lambda p: _replace_runtime(p, 0, min_schema_generation=3), "incompatible"),
        (lambda p: _replace_runtime(p, 0, owner="someone-else"), "owner"),
    ),
)
def test_deploy_record_refuses_stale_missing_or_contract_identity(mutation, message) -> None:
    payload = _record_payload()
    mutation(payload)

    with pytest.raises(ValidationError, match=message):
        DeployRecord.model_validate(payload)


def test_mixed_version_directions_and_exact_identity_checks() -> None:
    harness = MixedVersionHarness.seeded(_record())
    old_web = harness.identity("old-web")
    new_web = harness.identity("new-web")

    first = harness.read(old_web)
    harness.write(new_web, "new-writer", {"value": 1})
    harness.write(old_web, "old-writer", {"value": 2})
    second = harness.read(new_web)

    assert first["active_generation"] == "6" * 64
    assert second["writes"] == 2
    stale = old_web.model_copy(update={"image_digest": _digest("f")})
    with pytest.raises(IdentityRefused, match="stale"):
        harness.read(stale)
    with pytest.raises(IdentityRefused, match="missing"):
        harness.identity("missing-web")


def test_queued_old_task_redelivers_once_to_new_worker() -> None:
    harness = MixedVersionHarness.seeded(_record())
    old_worker = harness.identity("old-worker")
    new_worker = harness.identity("new-worker")

    harness.enqueue(old_worker, "task-old-1", {"attempt": "pending"})
    first = harness.redeliver(new_worker, "task-old-1")
    duplicate = harness.redeliver(new_worker, "task-old-1")

    assert first == duplicate
    assert harness.snapshot()["delivered_tasks"] == 1
    assert harness.snapshot()["queued_tasks"] == 0


def test_concurrent_old_new_readers_and_writers_preserve_every_write() -> None:
    harness = MixedVersionHarness.seeded(_record())
    writers = [harness.identity("old-web"), harness.identity("new-web")]
    readers = [harness.identity("new-web"), harness.identity("old-web")]

    def exercise(index: int) -> None:
        harness.write(writers[index % 2], f"write-{index}", {"index": index})
        harness.read(readers[index % 2])

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(exercise, range(24)))

    snapshot = harness.snapshot()
    assert snapshot["writes"] == 24
    assert snapshot["unique_write_keys"] == 24
    assert snapshot["read_count"] == 24


def test_contract_phase_is_absent_and_refused_before_and_after_drain() -> None:
    harness = MixedVersionHarness.seeded(_record())
    harness.enqueue(harness.identity("old-worker"), "queued-old", {"v": 1})

    with pytest.raises(ContractPhaseRefused, match="old runtime|queued"):
        harness.request_contract()

    harness.stop_schedules()
    harness.redeliver(harness.identity("new-worker"), "queued-old")
    harness.stop_workers()
    harness.retire_old_runtimes()
    harness.assert_drained()
    with pytest.raises(ContractPhaseRefused, match="not implemented"):
        harness.request_contract()


def test_drain_order_refuses_stopping_workers_with_queued_tasks() -> None:
    harness = MixedVersionHarness.seeded(_record())
    harness.enqueue(harness.identity("old-worker"), "queued-old", {"v": 1})

    with pytest.raises(RecoveryOrderRefused, match="queued"):
        harness.stop_workers()
    with pytest.raises(RecoveryOrderRefused, match="schedules"):
        harness.assert_drained()


def test_identity_and_queue_edge_refusals_are_explicit() -> None:
    harness = MixedVersionHarness.seeded(_record())
    old_web = harness.identity("old-web")
    old_worker = harness.identity("old-worker")
    new_worker = harness.identity("new-worker")

    with pytest.raises(IdentityRefused, match="missing"):
        harness.read(old_web.model_copy(update={"identity_id": "not-recorded"}))
    with pytest.raises(IdentityRefused, match="role mismatch"):
        harness.read(old_worker)
    with pytest.raises(ValueError, match="write key"):
        harness.write(old_web, "", {})
    harness.write(old_web, "same", {})
    with pytest.raises(ValueError, match="duplicate write"):
        harness.write(old_web, "same", {})

    harness.enqueue(new_worker, "new-queue", {})
    with pytest.raises(ValueError, match="duplicate task"):
        harness.enqueue(new_worker, "new-queue", {})
    with pytest.raises(IdentityRefused, match="newer queued-task"):
        harness.redeliver(old_worker, "new-queue")
    with pytest.raises(KeyError, match="unknown queued"):
        harness.redeliver(new_worker, "absent")


def test_each_drain_precondition_and_retired_identity_is_enforced() -> None:
    harness = MixedVersionHarness.seeded(_record())
    old_web = harness.identity("old-web")

    with pytest.raises(RecoveryOrderRefused, match="schedules"):
        harness.stop_workers()
    with pytest.raises(RecoveryOrderRefused, match="schedules, queue, and workers"):
        harness.retire_old_runtimes()
    harness.stop_schedules()
    with pytest.raises(RecoveryOrderRefused, match="workers"):
        harness.assert_drained()
    harness.stop_workers()
    with pytest.raises(RecoveryOrderRefused, match="old runtime"):
        harness.assert_drained()
    harness.retire_old_runtimes()
    with pytest.raises(IdentityRefused, match="retired"):
        harness.read(old_web)


def test_queue_and_worker_state_checks_remain_fail_closed() -> None:
    harness = MixedVersionHarness.seeded(_record())
    worker = harness.identity("old-worker")
    harness.stop_schedules()
    harness.enqueue(worker, "still-queued", {})
    with pytest.raises(RecoveryOrderRefused, match="queued"):
        harness.assert_drained()

    # Defensive state: a worker identity can never enqueue after the worker
    # subsystem is marked stopped, even before identity retirement completes.
    harness._workers_running = False
    with pytest.raises(RecoveryOrderRefused, match="workers are stopped"):
        harness.enqueue(worker, "late", {})


def test_runtime_schema_window_and_recovery_order_fail_closed() -> None:
    harness = MixedVersionHarness.seeded(_record())
    web = harness.identity("new-web")
    harness._schema_generation = 4
    with pytest.raises(IdentityRefused, match="incompatible"):
        harness.read(web)

    with pytest.raises(RecoveryOrderRefused, match="out of order"):
        MixedVersionHarness.seeded(_record()).recover(
            (RecoveryAction.stop_schedules, RecoveryAction.disable_flags)
        )


def test_non_destructive_recovery_preserves_pre_and_post_forward_state() -> None:
    harness = MixedVersionHarness.seeded(_record())
    harness.write(harness.identity("new-web"), "post-forward", {"preserve": True})
    before = deepcopy(harness.durable_state())

    harness.recover(
        (
            RecoveryAction.disable_flags,
            RecoveryAction.stop_schedules,
            RecoveryAction.stop_workers,
            RecoveryAction.activate_prior_generation,
            RecoveryAction.restore_prior_compatible_image,
            RecoveryAction.forward_corrective_migration,
        )
    )
    after = harness.durable_state()

    assert after["active_generation"] == "7" * 64
    assert after["retained"] == before["retained"]
    assert after["writes"] == before["writes"]
    assert after["tombstones"] == before["tombstones"]
    assert after["schema_generation"] == before["schema_generation"] + 1


@pytest.mark.parametrize(
    "action",
    (
        RecoveryAction.reverse_migration,
        RecoveryAction.delete_retained_rows,
        RecoveryAction.reset_persistent_database,
    ),
)
def test_destructive_recovery_actions_are_categorically_refused(action) -> None:
    harness = MixedVersionHarness.seeded(_record())
    before = harness.durable_state()

    with pytest.raises(DestructiveRecoveryRefused):
        harness.recover((action,))

    assert harness.durable_state() == before


def _identity_guard_oracle(harness: MixedVersionHarness) -> None:
    stale = harness.identity("old-web").model_copy(update={"image_digest": _digest("f")})
    try:
        harness.read(stale)
    except IdentityRefused:
        return
    raise AssertionError("stale identity was accepted")


def _contract_refusal_oracle(harness: MixedVersionHarness) -> None:
    try:
        harness.request_contract()
    except ContractPhaseRefused:
        return
    raise AssertionError("contract phase was accepted")


def _destructive_recovery_oracle(harness: MixedVersionHarness) -> None:
    try:
        harness.recover((RecoveryAction.delete_retained_rows,))
    except DestructiveRecoveryRefused:
        return
    raise AssertionError("destructive recovery was accepted")


def test_mutation_removed_runtime_identity_guard_is_killed(monkeypatch) -> None:
    canonical = MixedVersionHarness.seeded(_record())
    _identity_guard_oracle(canonical)

    monkeypatch.setattr(
        MixedVersionHarness,
        "_require_identity",
        lambda self, supplied, role: supplied,
    )
    with pytest.raises(AssertionError, match="stale identity"):
        _identity_guard_oracle(MixedVersionHarness.seeded(_record()))


def test_mutation_removed_contract_refusal_is_killed(monkeypatch) -> None:
    canonical = MixedVersionHarness.seeded(_record())
    _contract_refusal_oracle(canonical)

    monkeypatch.setattr(MixedVersionHarness, "request_contract", lambda self: None)
    with pytest.raises(AssertionError, match="contract phase"):
        _contract_refusal_oracle(MixedVersionHarness.seeded(_record()))


def test_mutation_removed_destructive_recovery_guard_is_killed(monkeypatch) -> None:
    canonical = MixedVersionHarness.seeded(_record())
    _destructive_recovery_oracle(canonical)

    monkeypatch.setattr(MixedVersionHarness, "recover", lambda self, actions: None)
    with pytest.raises(AssertionError, match="destructive recovery"):
        _destructive_recovery_oracle(MixedVersionHarness.seeded(_record()))

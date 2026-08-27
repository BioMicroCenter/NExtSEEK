"""Bounded mixed-version compatibility and non-destructive recovery harness.

The harness is deterministic and thread-safe so it can run on small machines.
Task 8 supplies real disposable image/database identities; this module enforces
the same identity, rollout, queue, and recovery contract without provider or
network access.
"""
from __future__ import annotations

import copy
import threading
from enum import StrEnum
from typing import Any, Iterable

from nextseek_api.eval.deploy_record import DeployRecord, RuntimeIdentity

__all__ = [
    "ContractPhaseRefused",
    "DestructiveRecoveryRefused",
    "IdentityRefused",
    "MixedVersionHarness",
    "RecoveryAction",
    "RecoveryOrderRefused",
]


class IdentityRefused(RuntimeError):
    pass


class ContractPhaseRefused(RuntimeError):
    pass


class DestructiveRecoveryRefused(RuntimeError):
    pass


class RecoveryOrderRefused(RuntimeError):
    pass


class RecoveryAction(StrEnum):
    disable_flags = "disable_flags"
    stop_schedules = "stop_schedules"
    stop_workers = "stop_workers"
    activate_prior_generation = "activate_prior_generation"
    restore_prior_compatible_image = "restore_prior_compatible_image"
    forward_corrective_migration = "forward_corrective_migration"
    reverse_migration = "reverse_migration"
    delete_retained_rows = "delete_retained_rows"
    reset_persistent_database = "reset_persistent_database"


_DESTRUCTIVE = frozenset(
    {
        RecoveryAction.reverse_migration,
        RecoveryAction.delete_retained_rows,
        RecoveryAction.reset_persistent_database,
    }
)

_SAFE_RECOVERY_ORDER = (
    RecoveryAction.disable_flags,
    RecoveryAction.stop_schedules,
    RecoveryAction.stop_workers,
    RecoveryAction.activate_prior_generation,
    RecoveryAction.restore_prior_compatible_image,
    RecoveryAction.forward_corrective_migration,
)


class MixedVersionHarness:
    """In-memory execution model bound to one validated deployment record."""

    def __init__(self, record: DeployRecord) -> None:
        self.record = record
        self._lock = threading.RLock()
        self._identities = {
            identity.identity_id: identity for identity in record.runtime_identities
        }
        self._active_identity_ids = set(self._identities)
        self._workers_running = True
        self._old_runtimes_retired = False
        self._schedule_state = dict(record.schedule_state)
        self._flag_state = dict(record.flag_state)
        self._active_generation = record.generations.active
        self._prior_generation = record.generations.prior
        self._current_image = record.images["candidate"]
        self._schema_generation = record.database_schema.generation
        self._writes: dict[str, Any] = {}
        self._queued: dict[str, dict[str, Any]] = {}
        self._delivered: dict[str, dict[str, Any]] = {}
        self._read_count = 0
        self._retained = {
            category: tuple(f"{category}-{index}" for index in range(count))
            for category, count in record.data.row_counts.items()
        }

    @classmethod
    def seeded(cls, record: DeployRecord) -> "MixedVersionHarness":
        """Build the required non-empty initial deployment state."""
        return cls(record)

    def identity(self, identity_id: str) -> RuntimeIdentity:
        try:
            return self._identities[identity_id]
        except KeyError as exc:
            raise IdentityRefused(f"missing runtime identity: {identity_id}") from exc

    def _require_identity(
        self, supplied: RuntimeIdentity, *, role: str
    ) -> RuntimeIdentity:
        expected = self._identities.get(supplied.identity_id)
        if expected is None:
            raise IdentityRefused(f"missing runtime identity: {supplied.identity_id}")
        if supplied != expected:
            raise IdentityRefused(f"stale runtime identity: {supplied.identity_id}")
        if supplied.identity_id not in self._active_identity_ids:
            raise IdentityRefused(f"retired runtime identity: {supplied.identity_id}")
        if supplied.role != role:
            raise IdentityRefused(
                f"runtime role mismatch: expected {role}, got {supplied.role}"
            )
        if not (
            supplied.min_schema_generation
            <= self._schema_generation
            <= supplied.max_schema_generation
        ):
            raise IdentityRefused(
                f"runtime {supplied.identity_id} is incompatible with schema generation"
            )
        return expected

    def read(self, identity: RuntimeIdentity) -> dict[str, Any]:
        with self._lock:
            self._require_identity(identity, role="web")
            self._read_count += 1
            return {
                "active_generation": self._active_generation,
                "writes": len(self._writes),
                "schema_generation": self._schema_generation,
            }

    def write(self, identity: RuntimeIdentity, key: str, value: Any) -> None:
        if not key:
            raise ValueError("write key is required")
        with self._lock:
            self._require_identity(identity, role="web")
            if key in self._writes:
                raise ValueError(f"duplicate write key: {key}")
            self._writes[key] = copy.deepcopy(value)

    def enqueue(
        self, identity: RuntimeIdentity, task_id: str, payload: dict[str, Any]
    ) -> None:
        with self._lock:
            worker = self._require_identity(identity, role="worker")
            if not self._workers_running:
                raise RecoveryOrderRefused("workers are stopped")
            if task_id in self._queued or task_id in self._delivered:
                raise ValueError(f"duplicate task ID: {task_id}")
            self._queued[task_id] = {
                "task_id": task_id,
                "payload": copy.deepcopy(payload),
                "queue_generation": worker.queue_generation,
                "created_by": worker.identity_id,
            }

    def redeliver(self, identity: RuntimeIdentity, task_id: str) -> dict[str, Any]:
        with self._lock:
            worker = self._require_identity(identity, role="worker")
            if task_id in self._delivered:
                return copy.deepcopy(self._delivered[task_id])
            try:
                queued = self._queued[task_id]
            except KeyError as exc:
                raise KeyError(f"unknown queued task: {task_id}") from exc
            if worker.queue_generation < queued["queue_generation"]:
                raise IdentityRefused("worker cannot read a newer queued-task generation")
            delivered = {
                **queued,
                "delivered_by": worker.identity_id,
                "delivery_count": 1,
            }
            self._delivered[task_id] = delivered
            del self._queued[task_id]
            return copy.deepcopy(delivered)

    def stop_schedules(self) -> None:
        with self._lock:
            self._schedule_state = {name: False for name in self._schedule_state}

    def stop_workers(self) -> None:
        with self._lock:
            if self._queued:
                raise RecoveryOrderRefused("queued tasks must drain before workers stop")
            if any(self._schedule_state.values()):
                raise RecoveryOrderRefused("schedules must stop before workers")
            self._workers_running = False
            self._active_identity_ids = {
                identity_id
                for identity_id in self._active_identity_ids
                if self._identities[identity_id].role != "worker"
            }

    def retire_old_runtimes(self) -> None:
        with self._lock:
            if self._workers_running or self._queued or any(self._schedule_state.values()):
                raise RecoveryOrderRefused(
                    "old runtimes retire only after schedules, queue, and workers drain"
                )
            self._active_identity_ids = {
                identity_id
                for identity_id in self._active_identity_ids
                if self._identities[identity_id].release != "old"
            }
            self._old_runtimes_retired = True

    def assert_drained(self) -> None:
        with self._lock:
            if any(self._schedule_state.values()):
                raise RecoveryOrderRefused("schedules are not stopped")
            if self._queued:
                raise RecoveryOrderRefused("queued tasks are not drained")
            if self._workers_running:
                raise RecoveryOrderRefused("workers are not stopped")
            if not self._old_runtimes_retired:
                raise RecoveryOrderRefused("old runtime identities are not retired")

    def request_contract(self) -> None:
        with self._lock:
            old_active = any(
                self._identities[identity_id].release == "old"
                for identity_id in self._active_identity_ids
            )
            if old_active or self._queued:
                raise ContractPhaseRefused(
                    "contract refused while an old runtime or queued task can run"
                )
            raise ContractPhaseRefused(
                "contract phase is not implemented in the V4-9 deploy record"
            )

    def recover(self, actions: Iterable[RecoveryAction]) -> None:
        requested = tuple(actions)
        if any(action in _DESTRUCTIVE for action in requested):
            raise DestructiveRecoveryRefused(
                "destructive persistent recovery is categorically forbidden"
            )
        if requested != _SAFE_RECOVERY_ORDER:
            raise RecoveryOrderRefused("safe recovery actions are out of order or incomplete")
        with self._lock:
            self._flag_state = {name: False for name in self._flag_state}
            self.stop_schedules()
            self.stop_workers()
            self._active_generation = self._prior_generation
            self._current_image = self.record.images["prior"]
            self._schema_generation += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "active_generation": self._active_generation,
                "schema_generation": self._schema_generation,
                "writes": len(self._writes),
                "unique_write_keys": len(set(self._writes)),
                "read_count": self._read_count,
                "queued_tasks": len(self._queued),
                "delivered_tasks": len(self._delivered),
            }

    def durable_state(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(
                {
                    "active_generation": self._active_generation,
                    "prior_generation": self._prior_generation,
                    "current_image": self._current_image,
                    "schema_generation": self._schema_generation,
                    "flags": self._flag_state,
                    "schedules": self._schedule_state,
                    "retained": self._retained,
                    "writes": self._writes,
                    "tombstones": self._retained["tombstones"],
                    "queued": self._queued,
                    "delivered": self._delivered,
                }
            )

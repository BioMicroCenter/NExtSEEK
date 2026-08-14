"""T07 synchronous mutation executor (Section 3 Phase-4 Chain-C hardening;
DD-05, DD-07, DD-13, DD-15, DD-21, DD-23, DD-32).

``execute_type_plan`` is the sole per-type execution kernel, shared
unmodified by the synchronous caller here and by T08's future asynchronous
worker: it locks the sample type and its complete ordered definition set,
rechecks ``before_physical_fingerprint`` under that lock, applies
definition/dependent/metadata changes inside exactly one
``transaction.atomic(using=settings.SEEK_DATABASE)`` block, resolves every
``created:<target_index>:<attribute_index>`` token through the physical
unique identity, verifies ``expected_after_semantic_fingerprint``, and
returns a plan-shaped outcome. The default-database T03 audit write
(``record_commit``/``record_reconciliation``/``record_failure``) always
happens strictly after that transaction has exited -- SEEK and the default
database are never atomic together (DD-32) -- using compare-and-set
transitions over the six-field DD-13 lease vocabulary already merged in
``AttributeMutationPartition`` (``claim_owner``, ``claim_generation``,
``lease_expires_at``, ``last_heartbeat_at``, ``lease_version``,
``state_version``).

``adapt_type_outcome`` is the one pure, ordered outcome-composition boundary
shared by this synchronous path and T08's asynchronous path (Section 3):
every terminal result -- an already-``unchanged`` plan, an already-resolved
``failed``/``plan_delta_required`` plan (never claims a partition or opens
SEEK), a successful ``execute_type_plan`` result, an ordinary execution
failure, or a reconciled committed state -- is rendered through it, so both
callers produce byte-equivalent shapes in plan order.

Chain B handoff (binding): this module imports T04's ``dd35_order_key``
(transitively, via ``logicalize_definitions``) for every locked/post-state
read -- the identical ordered identity T05 planned against -- and T05's
``classify_metadata_rewrite``/``MetadataRewriteDecision`` to decide whether
T06's kernel is invoked, rather than re-deriving either rule locally.
"""
from __future__ import annotations

import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import asdict, dataclass

from django.conf import settings
from django.db import connections, transaction
from django.utils import timezone

from .faults import attribute_fault
from .metadata import RewriteSpec, rewrite_type_metadata
from .planner import (
    canonical_sha256,
    classify_metadata_rewrite,
    resolve_created_identity_bindings,
    semantic_post_fingerprint,
)
from .repository import (
    AttributeRepository,
    Definition as RepositoryDefinition,
    RawAttribute,
    SeekAttributeGateway,
    bounded_identifier_chunks,
    logicalize_definitions,
    utc_datetime,
)

# Published T06 chunk defaults (hash-bound pointer at
# work/state/attribute-viewset/evidence/task-06/chunk-selection.pointer.json):
# chunk_rows=1000 / chunk_bytes=16MiB. No Django setting exists for these
# (T06's kernel takes them as explicit call parameters), so T07 pins them as
# local constants rather than inventing new global configuration.
METADATA_ROW_CHUNK_MAX = 1000
METADATA_JSON_BYTES_PER_CHUNK_MAX = 16 * 1024 * 1024

DEFINITION_COLUMNS = (
    "id,title,sample_attribute_type_id,required,pos,is_title,description,"
    "unit_id,sample_controlled_vocab_id,linked_sample_type_id,created_at,updated_at"
)

_SEMANTIC_FIELDS = (
    "title", "sample_attribute_type_id", "required", "pos", "is_title",
    "description", "unit_id", "sample_controlled_vocab_id", "linked_sample_type_id",
)


class ExecutionConflict(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Shared per-type execution kernel
# ---------------------------------------------------------------------------


def execute_type_plan(plan, services):
    """Execute one T05 ``TypeMutationPlan`` through the ``services`` adapter.

    ``services`` is the explicit per-partition adapter assembled from
    T03/T05/T06 state (``DjangoExecutionServices`` below is the concrete
    production adapter). This function never imports Django/database APIs
    directly -- every side effect goes through ``services`` -- so the exact
    same body drives both the synchronous caller in this module and T08's
    future asynchronous worker.
    """
    if getattr(plan, "status", None) == "unchanged":
        return {
            "status": "unchanged", "counts": dict(plan.counts),
            "attributes": list(plan.preview_records),
            "automatic_changes": [getattr(value, "__dict__", value)
                                  for value in plan.automatic_changes],
            "errors": [],
        }
    if getattr(plan, "status", None) != "planned":
        raise ExecutionConflict("non-executable plan reached executor")
    committed = services.already_committed(plan.idempotency_key)
    reconciliation_required = services.reconciliation_required(plan)
    if committed or reconciliation_required:
        # Never trust a default-DB marker or an interrupted execution intent alone:
        # re-read actual SEEK post-state before replay is even considered.
        with services.atomic("seek"):
            services.lock_type(plan.sample_type_id)
            observed = services.lock_schema(plan.sample_type_id)
            post = services.resolve_and_fingerprint_post_state(plan)
            reconciled = post["semantic_fingerprint"] == plan.expected_after_semantic_fingerprint
            if committed and not reconciled:
                raise ExecutionConflict("recorded commit disagrees with SEEK post-state")
            if reconciliation_required and not reconciled and observed != plan.before_physical_fingerprint:
                raise ExecutionConflict("interrupted execution has unknown SEEK post-state")
        if reconciled:
            outcome = (services.render_reconciled_outcome(plan, committed) if committed
                       else services.render_outcome(plan, post))
            services.record_reconciliation(plan, post["created_id_bindings"], post["physical_fingerprint"], outcome)
            return {**committed, "reconciled": True} if committed else outcome
        services.reset_seek_commit_observation()
    services.assert_idempotency(plan.idempotency_key)
    if not reconciliation_required:
        services.record_execution_intent(plan)
    with services.atomic("seek"):
        services.lock_type(plan.sample_type_id)
        observed = services.lock_schema(plan.sample_type_id)
        if observed != plan.before_physical_fingerprint:
            raise ExecutionConflict("type schema changed after planning")
        services.apply_definitions(plan)
        attribute_fault("async.during_active_type")
        services.apply_dependents(plan)
        services.rewrite_metadata(plan)
        post = services.resolve_and_fingerprint_post_state(plan)
        if post["semantic_fingerprint"] != plan.expected_after_semantic_fingerprint:
            raise ExecutionConflict("post-state fingerprint mismatch")
        # Rendering is pure but may validate; do it before SEEK can commit.
        outcome = services.render_outcome(plan, post)
    # The SEEK transaction has exited successfully before any default-DB audit write.
    services.record_commit(plan, post["created_id_bindings"], post["physical_fingerprint"], outcome)
    return outcome


# ---------------------------------------------------------------------------
# Shared outcome-composition boundary (T07/T08/T09)
# ---------------------------------------------------------------------------


def _error_dict(error) -> dict:
    """Project a T05 ``PlanError`` (or an already-dict error) into the public
    error shape.

    DD-33 (Section 3/round-3 review blocker): ``PlanError`` carries six
    fields -- ``code``, ``message``, ``target_index``, ``attribute_index``,
    ``field``, ``submitted_identifier`` -- and T01's ``MutationError`` types
    all four provenance fields as optional (``default=None``), so a
    ``code``/``message``-only projection here silently validates and loses
    exactly which attribute/field/submitted-value a resolved-failed target's
    error was about. Every field is preserved verbatim; a field genuinely
    absent on the source error stays ``None`` here rather than ever being
    fabricated.
    """
    if isinstance(error, dict):
        return error
    return {
        "code": error.code,
        "message": error.message,
        "target_index": error.target_index,
        "attribute_index": error.attribute_index,
        "field": error.field,
        "submitted_identifier": error.submitted_identifier,
    }


def adapt_type_outcome(plan, execution_result=None, error=None, reconciled=None) -> dict:
    """Pure, ordered outcome-composition boundary shared by T07's
    synchronous caller and T08's future asynchronous caller (Section 3).

    Exactly five input classes render through here: an already-``unchanged``
    T05 plan (terminal no-op, never touches a partition or SEEK); an
    already-resolved ``failed``/``plan_delta_required`` T05 plan (also never
    touches a partition or SEEK); a successful ``execute_type_plan`` result;
    an ordinary execution failure (``error`` set); and a reconciled
    committed state (``reconciled`` true, or already carried on
    ``execution_result``). Every branch normalizes to the same public shape
    T09 renders, in the caller's own plan order.
    """
    status = getattr(plan, "status", None)
    if status == "unchanged":
        result = {
            "status": "unchanged", "counts": dict(plan.counts),
            "attributes": list(plan.preview_records),
            "automatic_changes": [getattr(value, "__dict__", value)
                                  for value in plan.automatic_changes],
            "errors": [],
        }
    elif status in {"failed", "plan_delta_required"}:
        result = {
            "status": "failed", "counts": {}, "attributes": [], "automatic_changes": [],
            "errors": [_error_dict(item) for item in plan.errors],
        }
    elif error is not None:
        result = {
            "status": "failed", "counts": {}, "attributes": [], "automatic_changes": [],
            "errors": [{"code": type(error).__name__, "message": str(error)}],
        }
    else:
        result = dict(execution_result or {})
        if reconciled and "reconciled" not in result:
            result["reconciled"] = True
    result["sample_type_id"] = getattr(plan, "sample_type_id", None)
    result.setdefault("sample_type_title", str(getattr(plan, "sample_type_title", result["sample_type_id"])))
    result.setdefault("counts", {})
    result.setdefault("attributes", [])
    result.setdefault("automatic_changes", [])
    result.setdefault("errors", [])
    return result


def _execute_one(plan, services_for_plan, ordinal):
    status = getattr(plan, "status", None)
    if status in {"unchanged", "failed", "plan_delta_required"}:
        result = adapt_type_outcome(plan)
    else:
        try:
            services = services_for_plan(plan)
        except Exception as exc:  # noqa: BLE001 - claim/factory failure becomes a failed outcome
            result = adapt_type_outcome(plan, error=exc)
        else:
            try:
                execution_result = execute_type_plan(plan, services)
            except Exception as exc:  # noqa: BLE001 - any execution failure terminalizes this type only
                # A default-DB CAS/reconciliation failure after SEEK commit is unknown/recoverable,
                # never a physical mutation failure, and must never overwrite the partition outcome.
                if not services.seek_commit_observed(plan):
                    services.record_failure(plan, exc)
                result = adapt_type_outcome(plan, error=exc)
            else:
                result = adapt_type_outcome(
                    plan, execution_result=execution_result,
                    reconciled=execution_result.get("reconciled"),
                )
    result["_ordinal"] = ordinal
    return result


def execute_batch(plans, services_for_plan, max_workers=1):
    """Execute every plan in ``plans`` through the shared composition
    boundary, in caller order, continuing every independent type after an
    ordinary per-type failure (Section 3). ``max_workers`` greater than one
    is accepted only when every plan's ``sample_type_id`` is distinct
    (disjoint partitions); each call to ``services_for_plan`` produces one
    fresh adapter/token, never shared between types.
    """
    ordered = list(plans)
    ids = [plan.sample_type_id for plan in ordered if getattr(plan, "sample_type_id", None) is not None]
    if len(ids) != len(set(ids)):
        raise ExecutionConflict("parallel plans are not disjoint")
    if max_workers <= 1:
        values = [_execute_one(plan, services_for_plan, ordinal) for ordinal, plan in enumerate(ordered)]
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {
                pool.submit(_execute_one, plan, services_for_plan, ordinal): ordinal
                for ordinal, plan in enumerate(ordered)
            }
            values = [future.result() for future in as_completed(futures)]
        values.sort(key=lambda row: row["_ordinal"])
    return [{key: value for key, value in row.items() if key != "_ordinal"} for row in values]


def classify_mutation_http_status(outcomes):
    if not outcomes:
        return 422
    statuses = {row["status"] for row in outcomes}
    if statuses <= {"succeeded", "unchanged"}:
        return 200
    if statuses & {"succeeded", "unchanged"}:
        return 207
    return 409


# ---------------------------------------------------------------------------
# T04-ordered locked/post-state reads (DD-35 handoff)
# ---------------------------------------------------------------------------


def _rows_to_definitions(rows, sample_type_id) -> tuple[RepositoryDefinition, ...]:
    """Turn raw ``sample_attributes`` cursor rows (``DEFINITION_COLUMNS``
    order) into T04's own ordered ``Definition`` set.

    This is the exact T04/T07 order handoff (Section 3): rows are wrapped as
    ``RawAttribute`` (display-title fields blank -- irrelevant to ordering
    or to any semantic/physical fingerprint) and handed to T04's real
    ``logicalize_definitions``, which applies the single frozen
    ``dd35_order_key`` (valid positive positions first in ascending order,
    then legacy NULL/never-touched rows last by id) and assigns the
    contiguous logical ``pos`` 1..N -- the same first-touch normalization
    T05 planned against. No second ordering implementation exists here.
    """
    raw = [
        RawAttribute(
            id=row[0], title=row[1], sample_type_id=sample_type_id, sample_type_title="",
            sample_attribute_type_id=row[2], sample_attribute_type_title="",
            required=bool(row[3]), pos=row[4], is_title=bool(row[5]), description=row[6],
            unit_id=row[7], unit_title=None, unit_symbol=None,
            sample_controlled_vocab_id=row[8], sample_controlled_vocab_title=None,
            linked_sample_type_id=row[9], linked_sample_type_title=None,
            created_at=utc_datetime(row[10]), updated_at=utc_datetime(row[11]),
        )
        for row in rows
    ]
    return logicalize_definitions(raw)


def _physical_fingerprint(definitions) -> str:
    return canonical_sha256([
        (item.id, item.updated_at, item.title, item.sample_attribute_type_id, item.required,
         item.pos, item.is_title, item.description, item.unit_id,
         item.sample_controlled_vocab_id, item.linked_sample_type_id)
        for item in definitions
    ])


def resolve_created_identity_rows(connection, sample_type_id, created_identity_tokens):
    """Resolve opaque T05 create tokens using only ``sample_attributes.title``'s
    database collation -- never Python string comparison (Section 6).
    """
    tokens = tuple(created_identity_tokens)
    if len({token for token, _title in tokens}) != len(tokens):
        raise ExecutionConflict("created identity tokens are duplicated")
    rows = []
    for token_chunk in bounded_identifier_chunks(tokens, chunk_size=500):
        selects, params = [], []
        for token, submitted_title in token_chunk:
            if (not isinstance(token, str) or not token
                    or not isinstance(submitted_title, str) or not submitted_title):
                raise ExecutionConflict("created identity token/title is malformed")
            selects.append("SELECT %s AS identity_token,%s AS submitted_title")
            params.extend((token, submitted_title))
        if not selects:
            continue
        relation = " UNION ALL ".join(selects)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT requested.identity_token,a.id,a.title FROM (" + relation + ") requested "
                "JOIN sample_attributes a ON a.sample_type_id=%s "
                "AND a.title=requested.submitted_title "
                "ORDER BY requested.identity_token,a.id",
                [*params, sample_type_id],
            )
            rows.extend((str(token), int(identifier), actual_title)
                        for token, identifier, actual_title in cursor.fetchall())
    return rows


def rewrite_spec_from_type_plan(type_plan) -> RewriteSpec:
    """The sole T05 ``TypeMutationPlan`` -> T06 ``RewriteSpec`` join."""
    before_by_id = {row.id: row for row in type_plan.before if row.id is not None}
    after_by_id = {row.id: row for row in type_plan.after if row.id is not None}
    return RewriteSpec(
        resulting_titles=tuple(row.title for row in type_plan.after),
        renames=tuple(
            (before_by_id[pk].title, after_by_id[pk].title)
            for pk in sorted(before_by_id.keys() & after_by_id)
            if before_by_id[pk].title != after_by_id[pk].title
        ),
        additions=tuple(row.title for row in type_plan.after if row.id is None),
        deletions=tuple(before_by_id[pk].title for pk in sorted(before_by_id.keys() - after_by_id)),
    )


def _metadata_rewrite_required(type_plan) -> bool:
    """Re-derive, via T05's own shared pure classifier (never a local
    duplicate), whether any operation in ``type_plan`` actually requires a
    metadata rewrite -- the same classifier output T06 consumes."""
    before_by_id = {row.id: row for row in type_plan.before if row.id is not None}
    after_by_id = {row.id: row for row in type_plan.after if row.id is not None}
    common = before_by_id.keys() & after_by_id.keys()
    decisions = [
        classify_metadata_rewrite(before=None, after=None, operation_kind="create")
        for row in type_plan.after if row.id is None
    ]
    decisions += [
        classify_metadata_rewrite(before=before_by_id[pk], after=after_by_id[pk], operation_kind="patch")
        for pk in common
    ]
    decisions += [
        classify_metadata_rewrite(before=before_by_id[pk], after=None, operation_kind="delete")
        for pk in before_by_id.keys() - after_by_id.keys()
    ]
    return any(decision.requires_metadata_rewrite for decision in decisions)


# ---------------------------------------------------------------------------
# Six-field DD-13 partition claim token
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PartitionClaim:
    """T07's own minimal, immutable claim identity: exactly the fields the
    DD-13 six-field lease vocabulary needs to CAS one
    ``AttributeMutationPartition`` row. Never shared between two types."""

    partition_id: int
    owner: str
    claim_generation: int
    lease_version: int
    state_version: int


# ---------------------------------------------------------------------------
# Concrete production adapter
# ---------------------------------------------------------------------------


class DjangoExecutionServices:
    """The concrete ``services`` adapter ``execute_type_plan`` drives.

    Owns exactly the SEEK transaction (``atomic("seek")``) and the default-DB
    T03 partition CAS; never opens or commits a transaction inside T06's
    kernel or inside ``apply_definitions``/``apply_dependents``.
    """

    def __init__(self, job, claim: PartitionClaim, *, synchronous=False, read_only=False):
        from .models_db import AttributeMutationPartition

        self.job = job
        self.claim = claim
        self.partition_model = AttributeMutationPartition
        self.synchronous = synchronous
        # read_only: the claimless reconciliation adapter the synchronous
        # factory returns for a terminal `succeeded` partition (round-4
        # adjudication: Section 5 repeat-delivery reconciliation of an
        # already-terminal partition is a READ-ONLY flow -- it never
        # re-claims and never rewrites the DD-32 terminal audit).
        self.read_only = read_only
        self._seek_committed = False
        self._state_version = claim.state_version
        self._atomic_events: list[str] = []
        # True only while the durable "seek_execution_started" intent marker
        # provably covers no committed SEEK work: either this adapter wrote
        # the intent itself (and no commit has been observed), or the
        # recovery recheck proved "no progress" under the full-set lock.
        self._intent_disposable = False
        # Set the moment apply_definitions starts writing: distinguishes the
        # post-write verification read (strict created-token join) from the
        # recovery/recheck read (zero matches mean "the creates never
        # committed", not an error).
        self._wrote_definitions = False

    def atomic_event_ids(self) -> list[str]:
        return list(self._atomic_events)

    @contextmanager
    def atomic(self, alias):
        if alias != "seek":
            raise ValueError("executor owns only the SEEK transaction")
        self._atomic_events.append(f"atomic_started@{time.monotonic()!r}")
        with transaction.atomic(using=settings.SEEK_DATABASE, durable=False):
            yield
        self._atomic_events.append(f"atomic_finished@{time.monotonic()!r}")
        self._seek_committed = True

    def seek_commit_observed(self, plan):
        return self._seek_committed

    def reset_seek_commit_observation(self):
        self._seek_committed = False
        # The kernel calls this exactly on its "no progress -> resume"
        # branch: the recheck has just PROVEN, under the full-set lock, that
        # the prior attempt's SEEK work never committed (observed == planned
        # before-fingerprint), so the inherited "seek_execution_started"
        # marker is disposable if this resumed attempt also fails without a
        # commit (round-4 blocker 2 marker hygiene).
        self._intent_disposable = True

    # -- default-DB partition state -----------------------------------------

    def _partition(self):
        return self.partition_model.objects.get(pk=self.claim.partition_id)

    def already_committed(self, key):
        row = self._partition()
        if row.idempotency_key != key:
            raise ExecutionConflict("partition idempotency mismatch")
        return dict(row.outcome) if row.actual_after_physical_fingerprint and row.outcome else None

    def assert_idempotency(self, key):
        if self._partition().idempotency_key != key:
            raise ExecutionConflict("partition idempotency mismatch")

    def reconciliation_required(self, plan):
        row = self._partition()
        return (row.reconciliation or {}).get("state") == "seek_execution_started"

    def _cas(self, transition, **values):
        if self.read_only:
            # The claimless read-only reconciliation adapter holds no claim
            # and may never transition a terminal partition (DD-32).
            raise ExecutionConflict("terminal partition audit is immutable")
        row = self._partition()
        if row.claim_owner != self.claim.owner or row.claim_generation != self.claim.claim_generation:
            raise ExecutionConflict("partition claim/ownership changed")
        if not row.cas_update(expected_state_version=self._state_version, transition=transition, values=values):
            raise ExecutionConflict("lost partition CAS")
        self._state_version += 1

    def record_execution_intent(self, plan):
        self._cas("record_execution_intent", reconciliation={
            "state": "seek_execution_started",
            "idempotency_key": plan.idempotency_key,
            "before_physical_fingerprint": plan.before_physical_fingerprint,
            "expected_after_semantic_fingerprint": plan.expected_after_semantic_fingerprint,
            "recorded_at": timezone.now().isoformat(),
        })
        # This adapter wrote the intent itself; until a SEEK commit is
        # observed, the marker provably covers no committed work.
        self._intent_disposable = True

    def record_commit(self, plan, bindings, fingerprint, outcome):
        attribute_fault("executor.after_seek_commit_before_default_progress")
        current = dict(self._partition().created_id_bindings)
        if any(key in current and current[key] != value for key, value in bindings.items()):
            raise ExecutionConflict("created binding is not append-only")
        self._cas(
            "record_commit",
            created_id_bindings={**current, **bindings},
            actual_after_physical_fingerprint=fingerprint,
            outcome=outcome,
            reconciliation={"state": "verified", "verified_at": timezone.now().isoformat()},
        )
        attribute_fault("executor.after_default_progress_before_terminal")
        if self.synchronous:
            terminal_state = "succeeded" if outcome["status"] in {"succeeded", "unchanged"} else outcome["status"]
            self._cas(
                "terminalize", state=terminal_state,
                claim_owner=None, lease_expires_at=None, finished_at=timezone.now(),
            )

    def record_reconciliation(self, plan, bindings, fingerprint, outcome):
        if self.read_only:
            # Terminal partition: the audit already holds this exact verified
            # outcome -- re-verification of a duplicate delivery records
            # nothing (DD-32 terminal-audit immutability).
            return
        self.record_commit(plan, bindings, fingerprint, outcome)

    def record_failure(self, plan, exc):
        if self.read_only:
            # Round-4 blocker 1(b): a duplicate delivery of a terminal
            # `succeeded` partition that cannot be re-verified (a later job
            # drifted the type) reports its conflict in its OWN response
            # only; the terminal succeeded audit outcome is never rewritten.
            return
        row = self._partition()
        if row.actual_after_physical_fingerprint and row.outcome:
            # A recorded commit -- SEEK work that really committed and was
            # verified -- is never overwritten by a later delivery's failure
            # (DD-32). Release this claim so no active owner/lease remains,
            # leaving every audit field exactly as the commit recorded it.
            self._cas("release_preserving_recorded_commit", claim_owner=None, lease_expires_at=None)
            return
        values = {
            "state": "failed", "claim_owner": None, "lease_expires_at": None,
            "outcome": {"status": "failed", "errors": [{"code": type(exc).__name__, "message": str(exc)}]},
            "finished_at": timezone.now(),
        }
        if self._intent_disposable:
            # Marker hygiene (round-4 blocker 2): the "seek_execution_started"
            # intent provably covers no committed SEEK work (this adapter
            # wrote it itself with no commit observed, or the recheck proved
            # no progress under lock). Restamp it so a retry takes the clean
            # full-write path instead of the reconciliation recheck for an
            # execution that never happened -- pre-fix, that stale marker
            # made every CREATE retry die inside the recheck.
            values["reconciliation"] = {
                "state": "rolled_back",
                "idempotency_key": plan.idempotency_key,
                "rolled_back_at": timezone.now().isoformat(),
            }
        self._cas("record_failure", **values)

    # -- SEEK-side locks/reads/writes ----------------------------------------

    def lock_type(self, sample_type_id):
        with connections[settings.SEEK_DATABASE].cursor() as cursor:
            cursor.execute("SELECT id FROM sample_types WHERE id=%s FOR UPDATE", [sample_type_id])
            if cursor.fetchone() is None:
                raise ExecutionConflict("sample type disappeared")

    def _definitions(self, sample_type_id, *, lock):
        suffix = " FOR UPDATE" if lock else ""
        with connections[settings.SEEK_DATABASE].cursor() as cursor:
            cursor.execute(
                f"SELECT {DEFINITION_COLUMNS} FROM sample_attributes WHERE sample_type_id=%s "
                f"ORDER BY CASE WHEN pos IS NULL OR pos < 1 THEN 1 ELSE 0 END, pos, id{suffix}",
                [sample_type_id],
            )
            rows = cursor.fetchall()
        return _rows_to_definitions(rows, sample_type_id)

    def lock_schema(self, sample_type_id):
        return _physical_fingerprint(self._definitions(sample_type_id, lock=True))

    def apply_definitions(self, plan):
        self._wrote_definitions = True
        before_ids = {row.id for row in plan.before if row.id is not None}
        after_ids = {row.id for row in plan.after if row.id is not None}
        before_by_id = {row.id: row for row in plan.before if row.id is not None}
        with connections[settings.SEEK_DATABASE].cursor() as cursor:
            attribute_fault("executor.before_definition_write")
            deleted = sorted(before_ids - after_ids)
            if deleted:
                placeholders = ",".join(["%s"] * len(deleted))
                cursor.execute(
                    f"DELETE FROM sample_attributes WHERE sample_type_id=%s AND id IN ({placeholders})",
                    [plan.sample_type_id, *deleted],
                )
            created = [row for row in plan.after if row.id is None]
            if created:
                cursor.executemany(
                    "INSERT INTO sample_attributes (sample_type_id,title,sample_attribute_type_id,required,"
                    "pos,is_title,description,unit_id,sample_controlled_vocab_id,linked_sample_type_id,"
                    "created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(6),NOW(6))",
                    [(plan.sample_type_id, row.title, row.sample_attribute_type_id, row.required, row.pos,
                      row.is_title, row.description, row.unit_id, row.sample_controlled_vocab_id,
                      row.linked_sample_type_id) for row in created],
                )
            existing = [row for row in plan.after if row.id is not None]
            # A completely untouched sibling (same semantics AND same
            # position as its before-row) must never be written: an
            # unconditional per-row UPDATE across the whole after-set would
            # bump every row's `updated_at` on every mutation, silently
            # poisoning DD-23's optimistic-concurrency fingerprint for
            # unrelated future operations on that sibling. DD-24 first-touch
            # position normalization is written as its own pass, ahead of
            # every other semantic field, so a legacy NULL/never-touched
            # type's logical positions are durable before any
            # title/definition content changes commit; a pure reposition
            # never bumps `updated_at` (position is not tracked by SEEK's
            # own concurrency semantics).
            repositioned = [
                row for row in existing
                if before_by_id.get(row.id) is not None and before_by_id[row.id].physical_pos != row.pos
            ]
            if repositioned:
                cursor.executemany(
                    "UPDATE sample_attributes SET pos=%s WHERE sample_type_id=%s AND id=%s",
                    [(row.pos, plan.sample_type_id, row.id) for row in repositioned],
                )
            attribute_fault("executor.after_position_normalization")
            content_changed = [
                row for row in existing
                if before_by_id.get(row.id) is not None and any(
                    getattr(before_by_id[row.id], field_name) != getattr(row, field_name)
                    for field_name in _SEMANTIC_FIELDS if field_name != "pos"
                )
            ]
            if content_changed:
                cursor.executemany(
                    "UPDATE sample_attributes SET title=%s,sample_attribute_type_id=%s,required=%s,pos=%s,"
                    "is_title=%s,description=%s,unit_id=%s,sample_controlled_vocab_id=%s,"
                    "linked_sample_type_id=%s,updated_at=NOW(6) WHERE sample_type_id=%s AND id=%s",
                    [(row.title, row.sample_attribute_type_id, row.required, row.pos, row.is_title,
                      row.description, row.unit_id, row.sample_controlled_vocab_id,
                      row.linked_sample_type_id, plan.sample_type_id, row.id) for row in content_changed],
                )
            attribute_fault("executor.after_title_update")
        attribute_fault("executor.after_definition_write")

    def apply_dependents(self, plan):
        if plan.dependent_surface_verdict != "compatible":
            raise ExecutionConflict("dependent surface is not executable")

    def rewrite_metadata(self, plan):
        if not _metadata_rewrite_required(plan):
            return {"scanned": 0, "updated": 0, "statements": 0}

        def fault_hook(_phase, ordinal, total_chunks):
            if ordinal == 1:
                attribute_fault("executor.after_first_metadata_chunk")
            if total_chunks > 1 and ordinal == total_chunks - 1:
                attribute_fault("executor.before_last_metadata_chunk")

        result = rewrite_type_metadata(
            connections[settings.SEEK_DATABASE], plan.sample_type_id,
            rewrite_spec_from_type_plan(plan),
            METADATA_ROW_CHUNK_MAX, METADATA_JSON_BYTES_PER_CHUNK_MAX,
            fault_hook=fault_hook,
        )
        return {"scanned": result.scanned, "updated": result.updated, "statements": result.statements}

    def _bind_created_identities(self, plan, triples):
        try:
            return resolve_created_identity_bindings(plan, triples)
        except ValueError as exc:
            # Section 6: zero/multiple database-collation matches surface as
            # ExecutionConflict at this adapter boundary, never as T05's bare
            # ValueError.
            raise ExecutionConflict(str(exc)) from exc

    def resolve_and_fingerprint_post_state(self, plan):
        actual = self._definitions(plan.sample_type_id, lock=True)
        triples = resolve_created_identity_rows(
            connections[settings.SEEK_DATABASE], plan.sample_type_id, plan.created_identity_tokens,
        )
        expected_tokens = tuple(token for token, _title in plan.created_identity_tokens)
        if expected_tokens and not self._wrote_definitions:
            # Recovery/recheck context (round-4 blocker 2; Section 6:
            # "recovery performs this same join before deciding whether
            # execution may resume"): this adapter has written no definition
            # in this attempt, so a created token with zero database-collation
            # matches PROVES the creates never committed. The honest answer is
            # a post-state with empty bindings, whose semantic fingerprint
            # cannot equal `expected_after_semantic_fingerprint` -- letting
            # the kernel fall through to its observed==before "no progress ->
            # resume" decision instead of dying inside the recheck. A token
            # with MULTIPLE matches still conflicts: neither reconciliation
            # nor resume is provably safe against an ambiguous identity.
            matched = {row[0] for row in triples}
            if any(token not in matched for token in expected_tokens):
                bindings = {}
            else:
                bindings = self._bind_created_identities(plan, triples)
        else:
            bindings = self._bind_created_identities(plan, triples)
        semantic = semantic_post_fingerprint(plan, actual, bindings)
        physical = _physical_fingerprint(actual)
        display = AttributeRepository(SeekAttributeGateway()).display_fields_for([row.id for row in actual])
        enriched = tuple(
            RepositoryDefinition(
                id=row.id, title=row.title, sample_type_id=plan.sample_type_id,
                sample_type_title=display[row.id].sample_type_title,
                sample_attribute_type_id=row.sample_attribute_type_id,
                sample_attribute_type_title=display[row.id].sample_attribute_type_title,
                required=row.required, physical_pos=row.physical_pos, pos=row.pos,
                is_title=row.is_title, description=row.description,
                unit_id=row.unit_id, unit_title=display[row.id].unit_title,
                unit_symbol=display[row.id].unit_symbol,
                sample_controlled_vocab_id=row.sample_controlled_vocab_id,
                sample_controlled_vocab_title=display[row.id].sample_controlled_vocab_title,
                linked_sample_type_id=row.linked_sample_type_id,
                linked_sample_type_title=display[row.id].linked_sample_type_title,
                created_at=row.created_at, updated_at=row.updated_at,
            )
            for row in actual
        )
        records = AttributeRepository(None).materialize_attribute_records(enriched)
        return {
            "semantic_fingerprint": semantic, "created_id_bindings": bindings,
            "physical_fingerprint": physical, "actual": actual, "records": records,
        }

    def render_outcome(self, plan, post):
        return {
            "status": "unchanged" if plan.status == "unchanged" else "succeeded",
            "counts": dict(plan.counts),
            "attributes": [record.model_dump(mode="json") for record in post["records"]],
            "automatic_changes": [asdict(value) for value in plan.automatic_changes],
            "errors": [],
        }

    def render_reconciled_outcome(self, plan, committed):
        return {**committed, "reconciled": True}


def execution_services(job, partition_token):
    """Consume an already-claimed mutable token (T08's asynchronous
    caller). ``partition_token`` duck-types ``PartitionClaim``."""
    if partition_token is None:
        raise ValueError("caller must claim a T03 partition before execution")
    return DjangoExecutionServices(job, partition_token, synchronous=False)


def execution_services_factory(job, *, lease_seconds=120):
    """Return the synchronous per-plan claim factory: every call atomically
    claims exactly its matching T03 partition -- by the Section 6
    ``(job, idempotency_key, state_version, pending/unclaimed)`` predicate,
    bound through the DD-13 six-field CAS already merged on
    ``AttributeMutationPartition.claim`` -- and returns a fresh adapter with
    a distinct owner/claim. ``execute_batch`` invokes this exactly once per
    planned plan; a services instance or claim is never shared between two
    sample types.

    Round-4 adjudicated claim gate (full ruling: test_executor_db.py module
    docstring):

    - a LIVE claim (owner set, lease unexpired) is never stolen, whatever
      the row's freshly-read version fields would allow -- an expired lease
      is a dead owner's and is re-claimable under a fresh generation;
    - a terminal ``succeeded`` partition is never re-claimed and its DD-32
      audit never rewritten: repeat delivery reconciles through a claimless
      read-only adapter returning the recorded outcome;
    - a released terminal ``failed`` partition (which by DD-05 committed
      nothing) is re-claimable, keeping Section 6's "no progress -> resume"
      recovery decision reachable for legitimate retries.
    """
    from .models_db import AttributeMutationPartition

    def claim(plan):
        row = AttributeMutationPartition.objects.get(job=job, idempotency_key=plan.idempotency_key)
        if row.state == "succeeded":
            token = PartitionClaim(row.pk, row.claim_owner, row.claim_generation,
                                   row.lease_version, row.state_version)
            return DjangoExecutionServices(job, token, synchronous=True, read_only=True)
        if row.claim_owner is not None and (
                row.lease_expires_at is None or row.lease_expires_at > timezone.now()):
            raise ExecutionConflict("synchronous partition claim lost")
        owner = f"sync:{uuid.uuid4()}"
        claimed = row.claim(
            expected_state_version=row.state_version,
            expected_claim_generation=row.claim_generation,
            expected_lease_version=row.lease_version,
            owner=owner, lease_seconds=lease_seconds,
        )
        if not claimed:
            raise ExecutionConflict("synchronous partition claim lost")
        token = PartitionClaim(row.pk, owner, row.claim_generation, row.lease_version, row.state_version)
        return DjangoExecutionServices(job, token, synchronous=True)

    return claim

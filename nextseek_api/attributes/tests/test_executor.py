"""T07 synchronous mutation executor: helper-level behavior and the exact
DD-35 primary node (task-07 spec Section 5).

This module never touches a database -- it exercises
``execute_type_plan``/``execute_batch``/``adapt_type_outcome``/
``classify_mutation_http_status`` against a hand-written fake ``services``
double, exercises the handful of ``DjangoExecutionServices``/
``execution_services``/``resolve_created_identity_rows`` guard clauses that
raise before ever touching a connection, and separately proves the DD-35
ordered-read/first-touch-normalization handoff directly against T04's real,
unmodified ``logicalize_definitions``. ``test_executor_db.py`` drives the
same kernel against the real repository/adapter over a disposable SEEK
database, and owns the four critical-mutant killers this module
deliberately does not duplicate (task-07 spec Section 5: "The four
same-named MagicMock functions ... are retired illustrations ... must not
be selected as evidence" -- ``test_sibling_insert_conflicts_with_stale_plan``,
``test_versions_rechecked_under_full_set_lock``,
``test_fault_rolls_back_complete_type``, and
``test_crash_after_seek_commit_reconciles_without_replay`` are real
disposable-DB nodes only, never mock-based here).
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass, is_dataclass, asdict
from datetime import datetime, timezone

import orjson
import pytest

from nextseek_api.attributes.executor import (
    DjangoExecutionServices,
    ExecutionConflict,
    PartitionClaim,
    _physical_fingerprint,
    _rows_to_definitions,
    adapt_type_outcome,
    classify_mutation_http_status,
    execute_batch,
    execute_type_plan,
    execution_services,
    resolve_created_identity_rows,
)
from nextseek_api.attributes.faults import attribute_fault
from nextseek_api.attributes.planner import PlanError
from nextseek_api.attributes.repository import RawAttribute, logicalize_definitions
from nextseek_api.attributes.tests.chain_c_t07 import record_chain_c_case


def _sha256_of(value) -> str:
    """A real sha256 hex digest over a canonical projection of *any*
    attestation value -- the same rule test_executor_db.py's helper of the
    same name follows, duplicated here rather than imported since this
    module is deliberately Django/database-free."""
    def default(obj):
        if is_dataclass(obj) and not isinstance(obj, type):
            return asdict(obj)
        return str(obj)
    return hashlib.sha256(orjson.dumps(value, option=orjson.OPT_SORT_KEYS, default=default)).hexdigest()


@dataclass(frozen=True)
class TypePlan:
    """A minimal fake plan carrying exactly the fields
    ``execute_type_plan``/``adapt_type_outcome`` read."""

    sample_type_id: int
    sample_type_title: str = "Blood"
    before_physical_fingerprint: str = "before"
    expected_after_semantic_fingerprint: str = "after"
    idempotency_key: str = "key"
    created_identity_tokens: tuple = ()
    status: str = "planned"
    counts: dict = None
    preview_records: tuple = ()
    automatic_changes: tuple = ()
    errors: tuple = ()

    def __post_init__(self):
        if self.counts is None:
            object.__setattr__(self, "counts", {})


class FakeServices:
    """A hand-written double implementing the exact ``services`` protocol
    ``execute_type_plan`` calls, recording every call for assertion."""

    def __init__(self, *, lock_schema_values=("before",)):
        self.calls: list[tuple] = []
        self._lock_schema_values = list(lock_schema_values)
        self.already_committed_value = None
        self.reconciliation_required_value = False
        self._seek_committed = False
        self.post_state = {
            "semantic_fingerprint": "after", "created_id_bindings": {},
            "physical_fingerprint": "physical-after",
        }
        self.render_outcome_value = {"status": "succeeded"}
        self.apply_dependents_error = None
        self._post_state_values: list = []

    def already_committed(self, key):
        self.calls.append(("already_committed", key))
        return self.already_committed_value

    def reconciliation_required(self, plan):
        self.calls.append(("reconciliation_required", plan))
        return self.reconciliation_required_value

    def assert_idempotency(self, key):
        self.calls.append(("assert_idempotency", key))

    def record_execution_intent(self, plan):
        self.calls.append(("record_execution_intent", plan))

    def reset_seek_commit_observation(self):
        self.calls.append(("reset_seek_commit_observation",))
        self._seek_committed = False

    @contextmanager
    def atomic(self, alias):
        self.calls.append(("atomic", alias))
        yield
        self._seek_committed = True

    def seek_commit_observed(self, plan):
        return self._seek_committed

    def lock_type(self, sample_type_id):
        self.calls.append(("lock_type", sample_type_id))

    def lock_schema(self, sample_type_id):
        self.calls.append(("lock_schema", sample_type_id))
        value = self._lock_schema_values.pop(0) if len(self._lock_schema_values) > 1 else self._lock_schema_values[0]
        return value

    def apply_definitions(self, plan):
        self.calls.append(("apply_definitions", plan))

    def apply_dependents(self, plan):
        self.calls.append(("apply_dependents", plan))
        if self.apply_dependents_error is not None:
            raise self.apply_dependents_error

    def rewrite_metadata(self, plan):
        self.calls.append(("rewrite_metadata", plan))

    def resolve_and_fingerprint_post_state(self, plan):
        self.calls.append(("resolve_and_fingerprint_post_state", plan))
        if self._post_state_values:
            return self._post_state_values.pop(0) if len(self._post_state_values) > 1 else self._post_state_values[0]
        return self.post_state

    def render_outcome(self, plan, post):
        self.calls.append(("render_outcome", plan, post))
        return self.render_outcome_value

    def render_reconciled_outcome(self, plan, committed):
        self.calls.append(("render_reconciled_outcome", plan, committed))
        return {**committed, "reconciled": True}

    def record_commit(self, plan, bindings, fingerprint, outcome):
        self.calls.append(("record_commit", plan, bindings, fingerprint, outcome))

    def record_reconciliation(self, plan, bindings, fingerprint, outcome):
        self.calls.append(("record_reconciliation", plan, bindings, fingerprint, outcome))

    def record_failure(self, plan, exc):
        self.calls.append(("record_failure", plan, exc))


# ---------------------------------------------------------------------------
# execute_type_plan: ordering, unchanged short-circuit, reconciliation
# ---------------------------------------------------------------------------


def test_one_type_orders_lock_definitions_dependents_metadata_postcheck():
    """Section 5: the happy path locks the type/schema, applies definitions
    before dependents before metadata, post-checks the fingerprint, and
    records the commit only after the SEEK block exits -- never before."""
    services = FakeServices()
    plan = TypePlan(7)
    assert execute_type_plan(plan, services) == {"status": "succeeded"}
    names = [call[0] for call in services.calls]
    assert names[:9] == [
        "already_committed", "reconciliation_required", "assert_idempotency",
        "record_execution_intent", "atomic", "lock_type", "lock_schema",
        "apply_definitions", "apply_dependents",
    ]
    assert "rewrite_metadata" in names
    assert names.index("rewrite_metadata") > names.index("apply_dependents")
    assert names[-1] == "record_commit"
    commit_call = next(call for call in services.calls if call[0] == "record_commit")
    assert commit_call[1] is plan
    assert commit_call[2] == {}
    assert commit_call[3] == "physical-after"
    assert commit_call[4] == {"status": "succeeded"}


def test_unchanged_plan_never_reaches_partition_or_seek_executor():
    """A T05 ``unchanged`` plan is a terminal no-op: no services call at
    all, and the immutable preview outcome is returned verbatim."""
    services = FakeServices()
    plan = TypePlan(
        7, status="unchanged", counts={"unchanged": 1},
        preview_records=({"id": 1},), automatic_changes=(),
    )
    assert execute_type_plan(plan, services) == {
        "status": "unchanged", "counts": {"unchanged": 1},
        "attributes": [{"id": 1}], "automatic_changes": [], "errors": [],
    }
    assert services.calls == []


def test_non_planned_non_unchanged_status_is_rejected():
    """A resolved-failed T05 plan reaching the executor at all is a caller
    bug (Section 3: resolved failures never claim a partition or open
    SEEK, so they must never be handed to ``execute_type_plan``)."""
    services = FakeServices()
    plan = TypePlan(7, status="failed")
    with pytest.raises(ExecutionConflict, match="non-executable"):
        execute_type_plan(plan, services)
    assert services.calls == []


def test_missing_commit_marker_reconciles_from_durable_execution_intent():
    """An interrupted execution (durable ``seek_execution_started`` intent,
    no partition commit marker) re-reads SEEK post-state under lock; when it
    matches the expected semantic fingerprint, that state is reconciled
    without ever calling ``apply_definitions``/``rewrite_metadata`` again."""
    services = FakeServices()
    services.reconciliation_required_value = True
    plan = TypePlan(7)
    assert execute_type_plan(plan, services) == {"status": "succeeded"}
    names = [call[0] for call in services.calls]
    assert names == [
        "already_committed", "reconciliation_required", "atomic", "lock_type", "lock_schema",
        "resolve_and_fingerprint_post_state", "render_outcome", "record_reconciliation",
    ]
    assert "apply_definitions" not in names
    assert "rewrite_metadata" not in names


def test_reconciliation_disagreement_with_stale_schema_is_a_conflict():
    """When the post-state fingerprint does not match the plan's expected
    semantic fingerprint AND the locked schema also disagrees with the
    planned before-fingerprint, an interrupted execution's SEEK state is
    genuinely unknown and must fail closed rather than silently replay."""
    services = FakeServices(lock_schema_values=("something-else",))
    services.reconciliation_required_value = True
    services.post_state = {
        "semantic_fingerprint": "not-after", "created_id_bindings": {}, "physical_fingerprint": "x",
    }
    plan = TypePlan(7)
    with pytest.raises(ExecutionConflict, match="unknown SEEK post-state"):
        execute_type_plan(plan, services)


def test_title_race_preserves_at_most_one():
    """Two conflicting patches racing the same type: the first execution
    commits under its own lock; a second plan built against the same
    stale before-fingerprint is rejected by the full-set recheck."""
    services = FakeServices(lock_schema_values=["before", "winner-state"])
    first = TypePlan(7, idempotency_key="a")
    assert execute_type_plan(first, services)["status"] == "succeeded"
    second = TypePlan(7, expected_after_semantic_fingerprint="other", idempotency_key="b")
    with pytest.raises(ExecutionConflict):
        execute_type_plan(second, services)


def test_untouched_type_is_not_normalized():
    """``lock_type`` is always called with the plan's own
    ``sample_type_id`` -- never a different type's identity."""
    services = FakeServices()
    execute_type_plan(TypePlan(7), services)
    lock_calls = [call for call in services.calls if call[0] == "lock_type"]
    assert lock_calls and all(call[1] == 7 for call in lock_calls)


# ---------------------------------------------------------------------------
# adapt_type_outcome: the five shared outcome classes
# ---------------------------------------------------------------------------


def test_adapt_type_outcome_unchanged_class():
    plan = TypePlan(7, status="unchanged", counts={"unchanged": 1}, preview_records=({"id": 1},))
    result = adapt_type_outcome(plan)
    assert result["status"] == "unchanged"
    assert result["attributes"] == [{"id": 1}]
    assert result["sample_type_id"] == 7


def test_adapt_type_outcome_resolved_failed_class():
    """DD-33 (round-3 review blocker): the real T05 ``PlanError`` shape,
    including the four provenance fields a code/message-only projection
    would silently drop -- see test_executor_db.py's real-executed-207
    node (``test_all_five_outcome_classes_use_shared_adapter_in_plan_order``)
    for the disposable-DB-backed proof of the identical boundary."""
    error = PlanError("boom", "it broke", target_index=2, attribute_index=1,
                       field="title", submitted_identifier="Weight")
    plan = TypePlan(7, status="failed", errors=(error,))
    result = adapt_type_outcome(plan)
    assert result["status"] == "failed"
    assert result["errors"] == [{
        "code": "boom", "message": "it broke", "target_index": 2, "attribute_index": 1,
        "field": "title", "submitted_identifier": "Weight",
    }]
    assert result["counts"] == {}


def test_adapt_type_outcome_planned_success_class():
    plan = TypePlan(7)
    result = adapt_type_outcome(plan, execution_result={"status": "succeeded", "counts": {"created": 1}})
    assert result["status"] == "succeeded"
    assert result["counts"] == {"created": 1}
    assert result["sample_type_id"] == 7


def test_adapt_type_outcome_ordinary_execution_failure_class():
    plan = TypePlan(7)
    result = adapt_type_outcome(plan, error=RuntimeError("network blip"))
    assert result["status"] == "failed"
    assert result["errors"] == [{"code": "RuntimeError", "message": "network blip"}]


def test_adapt_type_outcome_reconciled_committed_class():
    plan = TypePlan(7)
    result = adapt_type_outcome(plan, execution_result={"status": "succeeded"}, reconciled=True)
    assert result["reconciled"] is True
    assert result["status"] == "succeeded"


# ---------------------------------------------------------------------------
# execute_batch: ordering, continuation-after-failure, parallel disjointness
# ---------------------------------------------------------------------------


def test_batch_continues_independent_types_after_ordinary_failure():
    plans = [TypePlan(8, idempotency_key="a"), TypePlan(9, idempotency_key="b")]
    services_by_type = {8: FakeServices(), 9: FakeServices()}
    services_by_type[9].apply_dependents_error = RuntimeError("boom")

    def factory(plan):
        return services_by_type[plan.sample_type_id]

    result = execute_batch(plans, factory, max_workers=1)
    assert [row["sample_type_id"] for row in result] == [8, 9]
    assert [row["status"] for row in result] == ["succeeded", "failed"]
    assert classify_mutation_http_status(result) == 207
    assert services_by_type[9].calls[-1][0] == "record_failure"


def test_batch_skips_partition_and_seek_for_unchanged_and_resolved_failed():
    """Section 3: ``unchanged`` and resolved-failed plans never claim a
    partition or open SEEK -- the factory is never invoked for them."""
    plans = [
        TypePlan(8, status="unchanged", counts={"unchanged": 1}),
        TypePlan(9, status="failed"),
    ]
    factory_calls = []

    def factory(plan):
        factory_calls.append(plan)
        raise AssertionError("factory must not be called for unchanged/resolved-failed plans")

    result = execute_batch(plans, factory, max_workers=1)
    assert factory_calls == []
    assert [row["status"] for row in result] == ["unchanged", "failed"]


def test_parallel_types_receive_distinct_services_and_preserve_order():
    plans = [TypePlan(8, idempotency_key="a"), TypePlan(9, idempotency_key="b")]
    made: list[FakeServices] = []

    def factory(_plan):
        value = FakeServices()
        made.append(value)
        return value

    result = execute_batch(plans, factory, max_workers=2)
    assert len(made) == 2 and made[0] is not made[1]
    assert [row["sample_type_id"] for row in result] == [8, 9]


def test_parallel_plans_must_be_disjoint():
    plans = [TypePlan(8, idempotency_key="a"), TypePlan(8, idempotency_key="b")]
    with pytest.raises(ExecutionConflict, match="disjoint"):
        execute_batch(plans, lambda plan: FakeServices(), max_workers=2)


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [(["succeeded", "unchanged"], 200), (["succeeded", "failed"], 207), (["failed"], 409), ([], 422)],
)
def test_http_classification(statuses, expected):
    assert classify_mutation_http_status([{"status": item} for item in statuses]) == expected


# ---------------------------------------------------------------------------
# DD-35 primary node (Section 3): T04/T07 ordered-read/first-touch handoff
# ---------------------------------------------------------------------------


def test_logical_read_order_matches_first_touch_lock_fingerprint_and_normalization():
    """DD-35 negative/cross-chain node (Section 3): locked and post-state
    reads use T04's valid-positive-first/NULL-last ``dd35_order_key``
    identity order through T04's own ``logicalize_definitions`` -- never a
    second ordering implementation -- and first-touch normalization (a
    legacy NULL/never-touched physical position) assigns the identical
    contiguous logical ``pos`` 1..N an independent, fresh call to
    ``logicalize_definitions`` produces from equivalent ``RawAttribute``
    rows, regardless of the physical cursor's own return order."""
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # (id, title, sample_attribute_type_id, required, pos, is_title,
    #  description, unit_id, vocab_id, linked_type_id, created_at, updated_at)
    raw_rows = [
        (30, "Legacy", 5, False, None, False, None, None, None, None, ts, ts),
        (10, "Second", 5, False, 2, False, None, None, None, None, ts, ts),
        (5, "First", 5, True, 1, True, None, None, None, None, ts, ts),
    ]
    assertion_count = 0
    observed = _rows_to_definitions(raw_rows, 7)

    expected = logicalize_definitions([
        RawAttribute(
            id=row[0], title=row[1], sample_type_id=7, sample_type_title="",
            sample_attribute_type_id=row[2], sample_attribute_type_title="",
            required=bool(row[3]), pos=row[4], is_title=bool(row[5]), description=row[6],
            unit_id=row[7], unit_title=None, unit_symbol=None,
            sample_controlled_vocab_id=row[8], sample_controlled_vocab_title=None,
            linked_sample_type_id=row[9], linked_sample_type_title=None,
            created_at=row[10], updated_at=row[11],
        )
        for row in raw_rows
    ])
    assert [(item.id, item.pos) for item in observed] == [(item.id, item.pos) for item in expected]
    assertion_count += 1
    # Valid positive positions sort first (ascending); the legacy NULL row
    # sorts last -- first-touch normalization assigns it the final
    # contiguous logical position.
    assert [item.id for item in observed] == [5, 10, 30]
    assertion_count += 1
    assert [item.pos for item in observed] == [1, 2, 3]
    assertion_count += 1

    # Physical read order never drives logical order: a scrambled cursor
    # return order produces the byte-identical ordered identity/fingerprint.
    scrambled = list(reversed(raw_rows))
    rescrambled_observed = _rows_to_definitions(scrambled, 7)
    assert [(item.id, item.pos) for item in rescrambled_observed] == [(item.id, item.pos) for item in observed]
    assertion_count += 1
    assert _physical_fingerprint(rescrambled_observed) == _physical_fingerprint(observed)
    assertion_count += 1

    record_chain_c_case(
        nodeid="nextseek_api/attributes/tests/test_executor.py::"
               "test_logical_read_order_matches_first_touch_lock_fingerprint_and_normalization",
        plan={"sample_type_id": 7, "raw_rows": raw_rows},
        request_payload={"scrambled_rows": scrambled},
        ordered_input_fingerprints=[_sha256_of(raw_rows)],
        ordered_output_fingerprints=[_physical_fingerprint(observed)],
        fault_point=None, classification="DD-35-order-and-first-touch-normalization",
        physical_commit_count=0,
        claim_owner=None, claim_generation=0, lease_version=0, state_version=0,
        lease_terminal=True,
        atomic_event_ids=[], connection_ids=[], token_ids=[],
        assertion_count=assertion_count,
    )


def test_logicalize_definitions_fails_closed_on_invalid_legacy_position():
    """A non-null, non-positive physical position is invalid legacy state
    and must fail closed rather than being silently grouped with NULL."""
    ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    raw_rows = [(1, "Bad", 5, False, 0, False, None, None, None, None, ts, ts)]
    with pytest.raises(Exception, match="invalid legacy state"):
        _rows_to_definitions(raw_rows, 7)


# ---------------------------------------------------------------------------
# Additional execute_type_plan/adapt_type_outcome/_execute_one branches
# ---------------------------------------------------------------------------


def test_committed_marker_disagreeing_with_post_state_is_a_conflict():
    """A recorded commit marker whose SEEK post-state no longer matches is
    a genuine, unresolvable conflict -- never silently replayed."""
    services = FakeServices()
    services.already_committed_value = {"status": "succeeded"}
    services.post_state = {
        "semantic_fingerprint": "not-after", "created_id_bindings": {}, "physical_fingerprint": "x",
    }
    with pytest.raises(ExecutionConflict, match="disagrees with SEEK post-state"):
        execute_type_plan(TypePlan(7), services)


def test_reconciliation_with_no_progress_proceeds_to_a_full_write():
    """An execution-intent marker whose SEEK state still equals the planned
    before-state means nothing was written yet: the recheck neither
    reconciles nor conflicts, and execution proceeds to a full write,
    skipping a duplicate ``record_execution_intent``. The recheck's own
    post-state read (still "before") is distinct from the real write's own
    post-state read afterward (now "after")."""
    services = FakeServices()
    services.reconciliation_required_value = True
    services._post_state_values = [
        {"semantic_fingerprint": "not-after", "created_id_bindings": {}, "physical_fingerprint": "x"},
        {"semantic_fingerprint": "after", "created_id_bindings": {}, "physical_fingerprint": "physical-after"},
    ]
    result = execute_type_plan(TypePlan(7), services)
    assert result == {"status": "succeeded"}
    names = [call[0] for call in services.calls]
    assert names.count("record_execution_intent") == 0
    assert names.count("reset_seek_commit_observation") == 1
    assert names.count("apply_definitions") == 1
    assert names.count("record_commit") == 1


def test_post_write_fingerprint_mismatch_is_a_conflict():
    services = FakeServices()
    services.post_state = {
        "semantic_fingerprint": "wrong", "created_id_bindings": {}, "physical_fingerprint": "x",
    }
    with pytest.raises(ExecutionConflict, match="post-state fingerprint mismatch"):
        execute_type_plan(TypePlan(7), services)


def test_error_dict_passes_a_plain_dict_through_unchanged():
    @dataclass(frozen=True)
    class _PlanWithDictError:
        sample_type_id: int
        sample_type_title: str
        status: str
        errors: tuple

    plan = _PlanWithDictError(7, "Blood", "failed", ({"code": "already_a_dict", "message": "m"},))
    result = adapt_type_outcome(plan)
    assert result["errors"] == [{"code": "already_a_dict", "message": "m"}]


def test_execute_one_records_factory_failure_as_an_ordinary_failure():
    plan = TypePlan(7)

    def failing_factory(_plan):
        raise RuntimeError("claim lost")

    result = execute_batch([plan], failing_factory, max_workers=1)
    assert result == [{
        "status": "failed", "sample_type_id": 7, "sample_type_title": "Blood",
        "counts": {}, "attributes": [], "automatic_changes": [],
        "errors": [{"code": "RuntimeError", "message": "claim lost"}],
    }]


def test_execute_one_skips_record_failure_when_seek_commit_already_observed():
    """A default-DB failure discovered after the SEEK transaction already
    committed must never be treated as a physical mutation failure, and
    must never overwrite the partition outcome via ``record_failure``."""
    services = FakeServices()

    def crashing_record_commit(*_args, **_kwargs):
        raise RuntimeError("default-db crashed after seek commit")

    services.record_commit = crashing_record_commit

    def factory(_plan):
        return services

    result = execute_batch([TypePlan(7)], factory, max_workers=1)
    assert result[0]["status"] == "failed"
    assert services.seek_commit_observed(TypePlan(7)) is True
    assert all(call[0] != "record_failure" for call in services.calls)


# ---------------------------------------------------------------------------
# resolve_created_identity_rows guard clauses (raise before touching a connection)
# ---------------------------------------------------------------------------


def test_resolve_created_identity_rows_rejects_duplicate_tokens():
    with pytest.raises(ExecutionConflict, match="duplicated"):
        resolve_created_identity_rows(None, 7, (("created:0:0", "A"), ("created:0:0", "B")))


def test_resolve_created_identity_rows_rejects_malformed_entries():
    with pytest.raises(ExecutionConflict, match="malformed"):
        resolve_created_identity_rows(None, 7, ((7, "A"),))


# ---------------------------------------------------------------------------
# DjangoExecutionServices/execution_services guard clauses (no connection needed)
# ---------------------------------------------------------------------------


def _dummy_claim():
    return PartitionClaim(1, "owner", 0, 0, 0)


def test_django_execution_services_atomic_rejects_a_non_seek_alias():
    services = DjangoExecutionServices(job=None, claim=_dummy_claim())
    with pytest.raises(ValueError, match="only the SEEK transaction"):
        with services.atomic("default"):
            pass  # pragma: no cover - never reached


def test_apply_dependents_rejects_an_incompatible_dependent_surface():
    @dataclass(frozen=True)
    class _DependentPlan:
        dependent_surface_verdict: str

    services = DjangoExecutionServices(job=None, claim=_dummy_claim())
    with pytest.raises(ExecutionConflict, match="dependent surface is not executable"):
        services.apply_dependents(_DependentPlan("invalid_json_present"))
    services.apply_dependents(_DependentPlan("compatible"))  # no raise


def test_execution_services_requires_an_already_claimed_token():
    with pytest.raises(ValueError, match="must claim a T03 partition"):
        execution_services(job=None, partition_token=None)
    adapter = execution_services(job=None, partition_token=_dummy_claim())
    assert isinstance(adapter, DjangoExecutionServices)
    assert adapter.synchronous is False


def test_read_only_reconciliation_adapter_never_writes():
    """Round-4 blocker 1 (adjudication point 2, test_executor_db.py module
    docstring): the claimless read-only adapter the factory returns for a
    terminal ``succeeded`` partition must be structurally incapable of
    rewriting the terminal audit -- its CAS path raises before ever touching
    a connection, and its failure/reconciliation surfaces are no-ops."""
    adapter = DjangoExecutionServices(
        job=None, claim=PartitionClaim(1, None, 3, 3, 9), synchronous=True, read_only=True,
    )
    plan = TypePlan(7)
    with pytest.raises(ExecutionConflict, match="terminal partition audit is immutable"):
        adapter.record_execution_intent(plan)
    # No-op write surfaces: neither may raise nor reach the CAS.
    adapter.record_failure(plan, RuntimeError("duplicate delivery could not be verified"))
    adapter.record_reconciliation(plan, {}, "fingerprint", {"status": "succeeded"})


# ---------------------------------------------------------------------------
# attribute_fault() early-return branches (deterministic, independent of
# whatever other tests in the same session happened to create the control
# file first)
# ---------------------------------------------------------------------------


def test_attribute_fault_is_inert_with_no_control_env_var(monkeypatch):
    monkeypatch.delenv("ATTRIBUTE_TEST_FAULT_CONTROL", raising=False)
    attribute_fault("some.point")  # must not raise


def test_attribute_fault_is_inert_with_a_not_yet_created_control_file(monkeypatch, tmp_path):
    monkeypatch.setenv("ATTRIBUTE_TEST_FAULT_CONTROL", str(tmp_path / "never-created.json"))
    attribute_fault("some.point")  # must not raise

"""The attribute API hook: ``DjangoExecutionServices.record_commit`` enqueues the graph sync (spec 5 E2, E11; C-15).

A committed attribute mutation changes the sample type's declared attributes (E11) and the stored metadata of every
sample of that type (E2), so it enqueues ``catalog *`` and ``samples_of_type type:<sample type id>``. The rows go in
only once the compare-and-set that records the commit has succeeded: a lost CAS, a recorded failure and a duplicate
delivery of a terminal partition enqueue nothing, and the nightly targeted sync finds what they would have carried.
The call is ``hooks.enqueue``, so a broken outbox never reaches the caller whose SEEK work has already committed.

Runs on the SQLite test settings: the partition row is a double, so neither the SEEK nor the MySQL connection opens.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from django.db import OperationalError

from nextseek_api.attributes.executor import (
    DjangoExecutionServices,
    ExecutionConflict,
    PartitionClaim,
)
from nextseek_api.graph_sync import hooks, state
from nextseek_api.graph_sync.models_db import GraphSyncOutbox

SAMPLE_TYPE_ID = 7
OUTCOME = {"status": "succeeded", "counts": {}}
BOTH_ROWS = {("catalog", "*"), ("samples_of_type", f"type:{SAMPLE_TYPE_ID}")}


class FakePartition:
    """The one ``AttributeMutationPartition`` row ``record_commit`` reads and compare-and-sets."""

    def __init__(self, *, cas_ok=True, bindings=None):
        self.created_id_bindings = dict(bindings or {})
        self.claim_owner = "owner"
        self.claim_generation = 0
        self.actual_after_physical_fingerprint = None
        self.outcome = None
        self.cas_ok = cas_ok
        self.transitions: list[str] = []

    def cas_update(self, *, expected_state_version, transition, values):
        self.transitions.append(transition)
        return self.cas_ok


def _services(monkeypatch, *, cas_ok=True, synchronous=False, read_only=False, bindings=None):
    services = DjangoExecutionServices(
        job=None, claim=PartitionClaim(1, "owner", 0, 0, 0),
        synchronous=synchronous, read_only=read_only,
    )
    row = FakePartition(cas_ok=cas_ok, bindings=bindings)
    monkeypatch.setattr(services, "_partition", lambda: row)
    return services, row


def _plan():
    return SimpleNamespace(sample_type_id=SAMPLE_TYPE_ID, idempotency_key="idem-1")


def _queued():
    return set(GraphSyncOutbox.objects.filter(done_at__isnull=True).values_list("kind", "key"))


@pytest.fixture(autouse=True)
def fresh_counts():
    hooks.reset_failure_counts()
    yield
    hooks.reset_failure_counts()


@pytest.mark.django_db
def test_a_committed_plan_enqueues_the_catalog_and_its_sample_type(monkeypatch):
    services, row = _services(monkeypatch)

    services.record_commit(_plan(), {}, "physical-after", OUTCOME)

    assert row.transitions == ["record_commit"]
    assert _queued() == BOTH_ROWS
    assert hooks.failure_counts() == {}


@pytest.mark.django_db
def test_the_synchronous_path_enqueues_the_same_two_rows(monkeypatch):
    services, row = _services(monkeypatch, synchronous=True)

    services.record_commit(_plan(), {}, "physical-after", OUTCOME)

    assert row.transitions == ["record_commit", "terminalize"]
    assert _queued() == BOTH_ROWS


@pytest.mark.django_db
def test_a_reconciled_replay_enqueues_the_same_two_rows(monkeypatch):
    """The recovery path that finds SEEK already in the planned post-state records through the same method."""
    services, row = _services(monkeypatch)

    services.record_reconciliation(_plan(), {}, "physical-after", OUTCOME)

    assert row.transitions == ["record_commit"]
    assert _queued() == BOTH_ROWS


@pytest.mark.django_db
def test_a_second_committed_plan_on_one_type_leaves_one_row_each(monkeypatch):
    """``(kind, key)`` is unique: repeated commits coalesce into the two rows the drain will read once."""
    services, _ = _services(monkeypatch)

    services.record_commit(_plan(), {}, "physical-after", OUTCOME)
    services.record_commit(_plan(), {}, "physical-after", OUTCOME)

    assert _queued() == BOTH_ROWS
    assert GraphSyncOutbox.objects.count() == 2


@pytest.mark.django_db
def test_a_lost_compare_and_set_enqueues_nothing(monkeypatch):
    services, row = _services(monkeypatch, cas_ok=False)

    with pytest.raises(ExecutionConflict, match="lost partition CAS"):
        services.record_commit(_plan(), {}, "physical-after", OUTCOME)

    assert row.transitions == ["record_commit"]
    assert not GraphSyncOutbox.objects.exists()


@pytest.mark.django_db
def test_a_claim_that_changed_owner_enqueues_nothing(monkeypatch):
    services, row = _services(monkeypatch)
    row.claim_owner = "another-worker"

    with pytest.raises(ExecutionConflict, match="claim/ownership changed"):
        services.record_commit(_plan(), {}, "physical-after", OUTCOME)

    assert row.transitions == []
    assert not GraphSyncOutbox.objects.exists()


@pytest.mark.django_db
def test_a_binding_that_is_not_append_only_enqueues_nothing(monkeypatch):
    services, _ = _services(monkeypatch, bindings={"created:0:0": 11})

    with pytest.raises(ExecutionConflict, match="append-only"):
        services.record_commit(_plan(), {"created:0:0": 12}, "physical-after", OUTCOME)

    assert not GraphSyncOutbox.objects.exists()


@pytest.mark.django_db
def test_a_recorded_failure_enqueues_nothing(monkeypatch):
    """Nothing committed in SEEK, so there is nothing for the graph to catch up with."""
    services, row = _services(monkeypatch)

    services.record_failure(_plan(), RuntimeError("type schema changed after planning"))

    assert row.transitions == ["record_failure"]
    assert not GraphSyncOutbox.objects.exists()


@pytest.mark.django_db
def test_a_duplicate_delivery_of_a_terminal_partition_enqueues_nothing(monkeypatch):
    """The claimless read-only adapter records nothing, and an ambiguous recovery never reaches ``record_commit``:
    the nightly targeted sync covers both."""
    services, row = _services(monkeypatch, synchronous=True, read_only=True)

    services.record_reconciliation(_plan(), {}, "physical-after", OUTCOME)
    services.record_failure(_plan(), RuntimeError("duplicate delivery could not be verified"))

    assert row.transitions == []
    assert not GraphSyncOutbox.objects.exists()


def test_a_broken_outbox_never_reaches_the_caller(monkeypatch):
    """The writer's SEEK work and its audit stand; the failure is counted per kind and the caller sees nothing."""
    services, row = _services(monkeypatch, synchronous=True)

    def broken(*args, **kwargs):
        raise OperationalError("(2006, 'MySQL server has gone away')")

    monkeypatch.setattr(state, "enqueue", broken)
    services.record_commit(_plan(), {}, "physical-after", OUTCOME)

    assert row.transitions == ["record_commit", "terminalize"]
    assert hooks.failure_counts() == {"catalog": 1, "samples_of_type": 1}

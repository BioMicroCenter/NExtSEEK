"""T05 dry-run mutation planner: fake-repository unit lane.

Every test's docstring names the exact DD/Section-11.7 obligation it
discharges. This module never touches Django or the database -- it
exercises ``MutationPlanner`` against ``FakePlanningRepository``, a
hand-written double implementing the full protocol documented at the top
of ``planner.py``. Every one of those methods now exists on the real
``AttributeRepository`` (T04c/T04d, integration ``d8806a9``), so the
double mirrors the real envelope/record *shapes* exactly -- including
T04d's bulk relationship-identifier resolution (plan Amendment Log
2026-08-04 (2)) -- and only stands in for the SQL *oracles* (collation
grouping, relationship tables, display joins, populations).
``test_planner_db.py`` drives the identical planner against the real
repository over a disposable SEEK database.

RED rationale: every test below was authored against the documented
contract before ``planner.py`` existed in working form; each fails for a
distinct reason against an empty/absent module -- ``ImportError`` for the
whole file, or (once the module exists but a specific behavior is
missing/wrong) an assertion on the wrong status/code/count/hash. The
per-test docstrings note the specific pre-implementation failure mode
where it is not obvious from the assertion itself.
"""
from __future__ import annotations

import copy
from dataclasses import replace
from datetime import datetime, timezone
from math import ceil

import orjson
import pytest

from nextseek_api.attributes.planner import (
    AutomaticChange,
    Definition,
    HYPOTHETICAL_PREVIEW_KEYS,
    MutationPlanner,
    apply_title_transition,
    build_resolved_plan_envelope,
    canonical_json,
    canonical_sha256,
    classify_metadata_rewrite,
    clear_previous_titles,
    cross_target_conflict,
    enforce_uid_protection,
    normalize_relationship_identifiers,
    require_unique_final_collation_classes,
    resolve_created_identity_bindings,
    semantic_post_fingerprint,
)
from nextseek_api.attributes.repository import (
    AttributeRepository,
    DefinitionSnapshot,
    RawAttribute,
    TitleCollationClass,
    TypeSnapshot,
)
from nextseek_api.attributes.schemas import AttributeErrorResponse, AttributeRecord, MutationCounts

ACTOR = {"person_id": 42, "django_user_id": 84, "login": "demo", "scheme": "basic"}

# Immutable stored creation timestamp the display-enrichment bridge must pull
# from `display_fields_for` -- deliberately different from every definition's
# `updated_at` so a bridge that silently reuses `updated_at` is detectable.
STORED_CREATED_AT = datetime(2025, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

# Submitted DD-26 relationship-field spellings, keyed by the resolved
# `*_id` name test overrides use. Requests are still SUBMITTED with these
# names (T01's grammar); since task-04d the resolved envelope replaces them
# with verified `*_id` fields.
_SUBMITTED_RELATIONSHIP_NAME = {
    "sample_attribute_type_id": "sample_attribute_type", "unit_id": "unit",
    "sample_controlled_vocab_id": "sample_controlled_vocab", "linked_sample_type_id": "linked_sample_type",
}


def snapshot_definition(identifier, title, physical_pos, *, required=False, is_title=False,
                         updated_at=None, sample_attribute_type_id=5, description=None, unit_id=None,
                         sample_controlled_vocab_id=None, linked_sample_type_id=None, sample_type_id=7):
    """Build a real ``DefinitionSnapshot`` -- the exact shape
    ``AttributeRepository.snapshots_for`` -> ``TypeSnapshot.definitions``
    returns -- rather than a hand-rolled fake shape, so tests exercise the
    module against T04's real public dataclass."""
    return DefinitionSnapshot(
        id=identifier, title=title, sample_type_id=sample_type_id,
        sample_attribute_type_id=sample_attribute_type_id, required=required,
        physical_pos=physical_pos, is_title=is_title, description=description, unit_id=unit_id,
        sample_controlled_vocab_id=sample_controlled_vocab_id, linked_sample_type_id=linked_sample_type_id,
        updated_at=updated_at or datetime(2026, 1, 1, 0, 0, identifier, tzinfo=timezone.utc),
    )


def type_snapshot(sample_type_id, sample_type_title, definitions, *, invalid_json_count=0):
    fingerprint = canonical_sha256([(d.id, d.updated_at.isoformat()) for d in definitions])
    return TypeSnapshot(
        sample_type_id=sample_type_id, sample_type_title=sample_type_title,
        fingerprint=fingerprint, definitions=tuple(definitions), invalid_json_count=invalid_json_count,
    )


def _is_create_definition(operation):
    """Mirror of ``repository._is_create_definition``: a create definition is
    an ``AttributeCreate``-shaped dict with no ``attribute`` key (DD-26); a
    patch operation is a dict *with* one, and a delete selector is a bare
    id/title scalar."""
    return isinstance(operation, dict) and "attribute" not in operation


# Fixture relationship tables mirroring the four physical lookup tables the
# real T04d relationship pass resolves against (``RELATIONSHIP_FIELD_TABLES``
# in repository.py). Rows are ``(id, title)``; every integer id any test in
# this module submits must exist here, because the faithful fake -- like the
# real adapter (DD-15) -- existence-verifies integer spellings rather than
# passing them through. Unit id 9 also carries symbol "mg" in
# ``_FAKE_UNIT_SYMBOLS``, which exists precisely so tests can prove a symbol
# spelling NEVER matches (DD-19), not because symbols participate in
# resolution.
_FAKE_RELATIONSHIP_ROWS = {
    "sample_attribute_type": ((2, "Text"), (3, "Float"), (5, "String"), (77, "Markdown")),
    "unit": ((9, "milligram"), (11, "second")),
    "sample_controlled_vocab": ((6, "Terms"), (8, "Species")),
    "linked_sample_type": ((2, "Tissue"), (4, "Plasma")),
}
_FAKE_UNIT_SYMBOLS = {9: "mg", 11: "s"}


class FakePlanningRepository:
    """Implements the full ``resolved_repository_view`` protocol documented
    in ``planner.py``, plus ``display_fields_for``.

    Every method's input/output *shape* is the real
    ``AttributeRepository``/``SeekAttributeGateway`` shape at integration
    ``d8806a9``: ``resolve_mutation`` reads the submitted
    ``target["attributes"]`` field for all three kinds (DD-26), nests a
    create definition under ``"definition"``, accepts a bare scalar delete
    selector, and -- since task-04d (plan Amendment Log 2026-08-04 (2)) --
    resolves the four submitted relationship-identifier fields to verified
    ``*_id`` planning fields from the fixture tables above, emitting the
    frozen ``{field}_{not_found,ambiguous}`` 5-key errors for unresolvable
    ones exactly like ``repository._resolve_relationship_fields``. Only the
    *oracles* are stand-ins -- real SQL collation grouping is replaced by
    casefolding, and display joins are synthesised. ``test_planner_db.py``
    drives the same planner against the real repository over a disposable
    SEEK database.
    """

    def __init__(self, snapshots, *, dependent="compatible", invalid_json=0,
                 populations=None, default_population=99, absent_display_ids=(),
                 relationship_rows=None):
        self.absent_display_ids = frozenset(absent_display_ids)
        self.snapshots = {snapshot.sample_type_id: snapshot for snapshot in snapshots}
        self.by_title = {snapshot.sample_type_title: snapshot.sample_type_id for snapshot in snapshots}
        self.dependent = dependent
        self.invalid_json = invalid_json
        self.populations = populations or {}
        self.default_population = default_population
        self.relationship_rows = dict(_FAKE_RELATIONSHIP_ROWS, **(relationship_rows or {}))
        self.writes = 0
        self.jobs = 0
        self.dispatches = 0
        self.locks = 0
        self.query_count = 0

    # -- resolve_mutation -------------------------------------------------

    def _resolve_type(self, value):
        if isinstance(value, int):
            return value if value in self.snapshots else None
        return self.by_title.get(value)

    def _relationship_matches(self, field, value):
        """Mirror of one bulk ``resolve_relationship`` outcome for a single
        identifier: integers and ascii-decimal strings resolve by ID
        (existence-verified, DD-15); any other string resolves by exact
        title under the same casefold stand-in this fake already uses for
        the collation oracle. Unit symbols have no match key (DD-19)."""
        rows = self.relationship_rows[field]
        if isinstance(value, bool):
            return []
        if isinstance(value, int):
            return [row for row in rows if row[0] == value]
        if isinstance(value, str) and value.isascii() and value.isdecimal():
            return [row for row in rows if row[0] == int(value)]
        canon = str(value).strip().casefold()
        return [row for row in rows if row[1].strip().casefold() == canon]

    def _resolve_relationship_fields(self, source, *, target_index, attribute_index):
        """Faithful mirror of ``repository._resolve_relationship_fields``
        (task-04d): explicit null -> ``*_id: None``; omitted key stays
        omitted; a single match replaces the submitted spelling with the
        verified ``*_id``; a miss/duplicate keeps the spelling in place and
        contributes one ``_error_to_dict``-shaped 5-key error (no
        ``message``) with full provenance. Never mutates ``source``."""
        resolved = dict(source)
        errors = []
        for field in ("sample_attribute_type", "unit", "sample_controlled_vocab", "linked_sample_type"):
            if field not in source:
                continue
            value = source[field]
            if value is None:
                resolved.pop(field)
                resolved[f"{field}_id"] = None
                continue
            matches = self._relationship_matches(field, value)
            if not matches:
                errors.append({"code": f"{field}_not_found", "target_index": target_index,
                               "attribute_index": attribute_index, "field": field,
                               "submitted_identifier": value})
                continue
            if len(matches) > 1:
                errors.append({"code": f"{field}_ambiguous", "target_index": target_index,
                               "attribute_index": attribute_index, "field": field,
                               "submitted_identifier": value})
                continue
            resolved.pop(field)
            resolved[f"{field}_id"] = int(matches[0][0])
        return resolved, errors

    def resolve_mutation(self, request):
        """Real T04d envelope shape: operations are read from the submitted
        ``attributes`` field for every kind, a create definition is nested
        under ``"definition"`` with its relationship fields resolved to
        ``*_id`` (as is a patch operation's ``changes`` sub-object), and a
        delete selector arrives as a bare scalar (``repository.py``
        ``resolve_mutation_envelope``)."""
        self.query_count += 1
        resolved_targets = []
        for target_index, target in enumerate(request["targets"]):
            if "resolution_errors" in target:
                resolved_targets.append({
                    "target_index": target_index, "sample_type_id": None, "sample_type_title": None,
                    "operations": [], "resolution_errors": target["resolution_errors"],
                })
                continue
            type_id = self._resolve_type(target["sample_type"])
            snapshot = self.snapshots[type_id]
            resolved_operations = []
            for attribute_index, operation in enumerate(target.get("attributes", [])):
                if _is_create_definition(operation):
                    definition, relationship_errors = self._resolve_relationship_fields(
                        operation, target_index=target_index, attribute_index=attribute_index,
                    )
                    resolved_operations.append({
                        "attribute_id": None, "attribute_index": attribute_index,
                        "resolution_errors": relationship_errors, "definition": definition,
                    })
                    continue
                if isinstance(operation, dict) and "resolution_errors" in operation:
                    resolved_operations.append({
                        "attribute_id": None, "attribute_index": attribute_index,
                        "resolution_errors": operation["resolution_errors"],
                    })
                    continue
                identifier = operation["attribute"] if isinstance(operation, dict) else operation
                match = next((d for d in snapshot.definitions
                              if d.id == identifier or d.title == identifier), None)
                entry = {"attribute_id": match.id if match else None, "attribute_index": attribute_index,
                         "resolution_errors": []}
                if isinstance(operation, dict) and "changes" in operation:
                    changes, relationship_errors = self._resolve_relationship_fields(
                        operation["changes"], target_index=target_index, attribute_index=attribute_index,
                    )
                    entry["changes"] = changes
                    entry["resolution_errors"] = relationship_errors
                resolved_operations.append(entry)
            resolved_targets.append({
                "target_index": target_index, "sample_type_id": type_id,
                "sample_type_title": snapshot.sample_type_title,
                "operations": resolved_operations, "resolution_errors": [],
            })
        return {**request, "targets": resolved_targets}

    def snapshots_for(self, resolved):
        self.query_count += 1
        ids = sorted({t["sample_type_id"] for t in resolved["targets"]})
        return {identifier: self.snapshots[identifier] for identifier in ids}

    def dependent_verdicts(self, type_ids, resolved):
        self.query_count += 1
        return {identifier: self.dependent for identifier in type_ids}

    def invalid_json_counts(self, type_ids):
        self.query_count += 1
        return {identifier: self.invalid_json for identifier in type_ids}

    def sample_type_populations(self, type_ids):
        self.query_count += 1
        return {identifier: self.populations.get(identifier, self.default_population) for identifier in type_ids}

    def title_collation_classes(self, requests):
        """Test-double-only case-fold stand-in for the real SQL collation
        oracle. This casefold logic lives entirely in the test double, never
        in ``planner.py`` -- the module under test only ever consumes the
        returned opaque ``TitleCollationClass`` values."""
        self.query_count += 1
        result = {}
        for item in requests:
            snapshot = self.snapshots[item.sample_type_id]
            canon = item.title.strip().casefold()
            match_ids = tuple(sorted(
                d.id for d in snapshot.definitions
                if d.title.strip().casefold() == canon and d.id != item.exclude_id
            ))
            result[(item.target_index, item.attribute_index, item.phase)] = TitleCollationClass(
                class_key=f"{item.sample_type_id}:{canon}", match_ids=match_ids,
            )
        return result

    def display_fields_for(self, ids):
        """Mirror of ``AttributeRepository.display_fields_for``: bulk id ->
        live physical ``RawAttribute``. An id with no live row is simply
        absent from the result, never a partial/null entry -- which is what
        ``absent_display_ids`` simulates (a row deleted concurrently between
        the locked planning snapshot and preview enrichment)."""
        self.query_count += 1
        wanted = {identifier for identifier in ids} - self.absent_display_ids
        found = {}
        for snapshot in self.snapshots.values():
            for item in snapshot.definitions:
                if item.id in wanted:
                    found[item.id] = RawAttribute(
                        id=item.id, title=item.title, sample_type_id=snapshot.sample_type_id,
                        sample_type_title=snapshot.sample_type_title,
                        sample_attribute_type_id=item.sample_attribute_type_id,
                        sample_attribute_type_title=f"type-{item.sample_attribute_type_id}",
                        required=item.required, pos=item.physical_pos, is_title=item.is_title,
                        description=item.description, unit_id=item.unit_id,
                        unit_title=None, unit_symbol=None,
                        sample_controlled_vocab_id=item.sample_controlled_vocab_id,
                        sample_controlled_vocab_title=None,
                        linked_sample_type_id=item.linked_sample_type_id, linked_sample_type_title=None,
                        created_at=STORED_CREATED_AT, updated_at=item.updated_at,
                    )
        return found

    def materialize_attribute_records(self, definitions):
        """Byte-for-byte the real ``AttributeRepository.materialize_attribute_records``:
        a pure field copy of an already display-enriched ``repository.Definition``
        into ``AttributeRecord``, performing no enrichment of its own. It
        therefore raises ``AttributeError`` on a lean planning row exactly
        like the real one does."""
        return AttributeRepository.materialize_attribute_records(self, definitions)

    def materialize_hypothetical_records(self, definitions):
        return tuple({
            "token": item["token"], "title": item["title"], "sample_type_id": item["sample_type_id"],
            "sample_type_title": self.snapshots[item["sample_type_id"]].sample_type_title,
            "sample_attribute_type_id": item["sample_attribute_type_id"],
            "sample_attribute_type_title": f"type-{item['sample_attribute_type_id']}",
            "required": item["required"], "pos": item["pos"], "is_title": item["is_title"],
            "description": item["description"], "unit_id": item["unit_id"], "unit_title": None, "unit_symbol": None,
            "sample_controlled_vocab_id": item["sample_controlled_vocab_id"], "sample_controlled_vocab_title": None,
            "linked_sample_type_id": item["linked_sample_type_id"], "linked_sample_type_title": None,
        } for item in definitions)


@pytest.fixture
def base_snapshot():
    return type_snapshot(7, "Blood", (
        snapshot_definition(1, "UID", 1, required=True, is_title=True),
        snapshot_definition(2, "RNA", 2),
        snapshot_definition(3, "Age", 3),
    ))


@pytest.fixture
def planner():
    return MutationPlanner(threshold=100)


def create_request(definitions, *, dry_run=True, sample_type="Blood"):
    return {"kind": "create", "dry_run": dry_run, "actor": dict(ACTOR),
            "targets": [{"sample_type": sample_type, "attributes": definitions}]}


def create_operation(title, **overrides):
    """A real DD-26 ``AttributeCreate``-shaped submitted definition: the four
    relationship fields carry their *submitted* names, exactly as T01
    validates them; T04d then resolves them to ``*_id`` inside the resolved
    envelope. Overrides may be spelled either way; the resolved ``*_id``
    spelling is translated so a caller can keep naming the planning field it
    is asserting on."""
    operation = {"title": title, "sample_attribute_type": 5, "required": False, "pos": None,
                 "is_title": False, "description": None, "unit": None,
                 "sample_controlled_vocab": None, "linked_sample_type": None}
    for key, value in overrides.items():
        operation[_SUBMITTED_RELATIONSHIP_NAME.get(key, key)] = value
    return operation


def patch_request(operations, *, dry_run=True, sample_type="Blood"):
    return {"kind": "patch", "dry_run": dry_run, "actor": dict(ACTOR),
            "targets": [{"sample_type": sample_type, "attributes": operations}]}


def patch_operation(attribute, changes):
    return {"attribute": attribute, "changes": changes}


def delete_request(ids, *, dry_run=True, sample_type="Blood"):
    return {"kind": "delete", "dry_run": dry_run, "actor": dict(ACTOR),
            "targets": [{"sample_type": sample_type, "attributes": list(ids)}]}


# ---------------------------------------------------------------------------
# Canonicalization / hashing (Section 11.9 shared primitives)
# ---------------------------------------------------------------------------


def test_canonical_json_sorted_and_hash_stable():
    """orjson sorted-key canonicalization is the sole serialization
    primitive; key order never affects bytes or hash (Section 4 step 2)."""
    assert canonical_json({"b": 2, "a": 1}) == b'{"a":1,"b":2}'
    assert canonical_sha256({"b": 2, "a": 1}) == canonical_sha256({"a": 1, "b": 2})


@pytest.mark.parametrize("left,right,same", [
    ({"sample_type_id": 7}, {"sample_type_id": 7}, True),
    ({"attribute_id": 2}, {"attribute_id": 2}, True),
    ({"title": "RNA"}, {"title": "rna"}, False),
    ({"operations": [1, 2]}, {"operations": [2, 1]}, False),
])
def test_metamorphic_hash_pairs(left, right, same):
    """Metamorphic property: canonical hashing is byte-sensitive and never
    case-folds or reorders semantically distinct content (Section 4 step 6)."""
    assert (canonical_sha256(left) == canonical_sha256(right)) is same


def test_plan_is_deterministic_across_repeated_calls(planner, base_snapshot):
    """Same request -> byte-identical submitted hash and idempotency key on
    repeated calls; before/after fingerprints differ once genuinely
    changed (DD-08 determinism, Section 4 step 2)."""
    repository = FakePlanningRepository([base_snapshot])
    request = patch_request([patch_operation(2, {"description": "x"})])
    first = planner.plan_mutation(copy.deepcopy(request), repository)
    second = planner.plan_mutation(copy.deepcopy(request), repository)
    assert first.canonical_submitted_request_sha256 == second.canonical_submitted_request_sha256
    assert first.types[0].idempotency_key == second.types[0].idempotency_key
    assert first.types[0].before_physical_fingerprint != first.types[0].expected_after_semantic_fingerprint


def test_runtime_timing_never_selects_mode(base_snapshot, monkeypatch):
    """DD-04: predicted mode is a pure function of counts/threshold, never
    wall-clock timing."""
    repository = FakePlanningRepository([base_snapshot])
    planner = MutationPlanner(threshold=100)
    def request():
        return patch_request([patch_operation(2, {"description": "x"})])
    monkeypatch.setattr("time.monotonic", lambda: 10**12)
    first = planner.plan_mutation(request(), repository)
    monkeypatch.setattr("time.monotonic", lambda: -10**12)
    second = planner.plan_mutation(request(), repository)
    assert first.predicted_mode == second.predicted_mode == "synchronous"
    assert first.canonical_submitted_request_sha256 == second.canonical_submitted_request_sha256


# ---------------------------------------------------------------------------
# Create semantics (DD-07, DD-16)
# ---------------------------------------------------------------------------


def test_identical_create_is_unchanged(planner, base_snapshot):
    """DD-07: an idempotent create (identical to a real-collation-matched
    existing definition) is reported unchanged, never executable, with zero
    reorder/created counts."""
    repository = FakePlanningRepository([base_snapshot])
    request = create_request([create_operation("RNA", pos=2)])
    plan = planner.plan_mutation(request, repository)
    item = plan.types[0]
    assert item.status == "unchanged"
    assert item.executable is False
    assert plan.executable_types == ()
    assert plan.unchanged_types == plan.types
    assert plan.rejected_types == ()
    assert item.counts["unchanged"] == 1
    assert item.counts["reordered"] == 0
    assert not [change for change in item.automatic_changes if change.field == "pos"]
    assert item.before == item.after


def test_create_uses_database_collation_oracle_not_python_title_equality(planner, base_snapshot):
    """DD-07: create idempotency is decided by T04's real-database
    collation oracle, never Python case-folding -- a differently-cased
    title that the oracle matches to an existing row is a conflict, not a
    silent duplicate accepted as new."""
    repository = FakePlanningRepository([base_snapshot])
    plan = planner.plan_mutation(create_request([create_operation("rna", description="different")]), repository)
    item = plan.types[0]
    assert item.status == "failed"
    assert item.errors[0].code == "create_definition_conflict"
    assert not any(row.id is None for row in item.after)


@pytest.mark.parametrize("field,value", [
    ("required", True), ("description", "drift"), ("sample_attribute_type_id", 3),
    ("unit_id", 9), ("sample_controlled_vocab_id", 8), ("linked_sample_type_id", 2),
])
def test_create_semantic_drift_is_conflict(planner, base_snapshot, field, value):
    """DD-07: any semantic field difference from the collation-matched
    existing definition is a conflict, never an upsert."""
    repository = FakePlanningRepository([base_snapshot])
    operation = create_operation("RNA", **{field: value})
    result = planner.plan_mutation(create_request([operation]), repository).types[0]
    assert result.status == "failed"
    assert result.errors[0].code == "create_definition_conflict"
    assert result.before == result.after


def test_insert_move_and_append_produce_contiguous_positions(planner, base_snapshot):
    """DD-16: an occupied positive position inserts and shifts subsequent
    attributes; an omitted position appends after the current maximum in
    submitted order -- final positions are contiguous by *list order*, not
    by re-sorting raw requested/physical pos values against each other."""
    repository = FakePlanningRepository([base_snapshot])
    request = create_request([create_operation("Inserted", pos=2), create_operation("Appended")])
    plan = planner.plan_mutation(request, repository).types[0]
    assert [(item.title, item.pos) for item in plan.after] == [
        ("UID", 1), ("Inserted", 2), ("RNA", 3), ("Age", 4), ("Appended", 5),
    ]


@pytest.mark.parametrize("position", [0, -1, -9])
def test_nonpositive_position_rejected(planner, base_snapshot, position):
    """DD-16: a non-positive requested create position is a validation
    failure, never silently clamped."""
    repository = FakePlanningRepository([base_snapshot])
    plan = planner.plan_mutation(create_request([create_operation("Bad", pos=position)]), repository).types[0]
    assert plan.status == "failed"
    assert plan.errors[0].code == "position_not_positive"


def test_legacy_order_normalization_is_previewed(planner):
    """DD-24/DD-35: a sample type with duplicated/NULL physical positions
    is normalized to DD-35 logical order on the first touched mutation,
    even when the submitted operation is itself an idempotent no-op --
    the type is still ``planned``/executable and reports one
    ``AutomaticChange`` per row whose *physical* pos differs from its
    assigned logical pos."""
    broken = type_snapshot(7, "Blood", (
        snapshot_definition(1, "UID", 2, required=True, is_title=True),
        snapshot_definition(2, "RNA", None),
        snapshot_definition(3, "Age", 2),
    ))
    repository = FakePlanningRepository([broken])
    plan = planner.plan_mutation(patch_request([patch_operation(3, {"description": None})]), repository)
    item = plan.types[0]
    assert item.status == "planned" and item.executable is True
    assert plan.executable_types == (item,) and plan.unchanged_types == ()
    assert item.counts["unchanged"] == 1
    assert [(row.title, row.pos) for row in item.after] == [("UID", 1), ("Age", 2), ("RNA", 3)]
    position_changes = [change for change in item.automatic_changes if change.field == "pos"]
    assert {change.attribute_id for change in position_changes} == {1, 2}
    assert item.counts["reordered"] == len(position_changes) == 2


# ---------------------------------------------------------------------------
# Title / UID invariants (DD-17, DD-18, Section 11.7 title kernel)
# ---------------------------------------------------------------------------


def test_new_title_atomically_clears_old_title(planner):
    """DD-17: promoting a new title without UID present atomically clears
    the previously-title definition and reports it as an automatic
    change."""
    without_uid = type_snapshot(7, "Blood", (
        snapshot_definition(2, "RNA", 1, is_title=True), snapshot_definition(3, "Age", 2),
    ))
    repository = FakePlanningRepository([without_uid])
    plan = planner.plan_mutation(patch_request([patch_operation(3, {"is_title": True})]), repository).types[0]
    assert [(item.title, item.is_title) for item in plan.after] == [("RNA", False), ("Age", True)]
    assert AutomaticChange("title_cleared", 2, "RNA", "is_title", True, False) in plan.automatic_changes


def test_uid_remains_required_and_sole_title(planner, base_snapshot):
    """DD-18: when UID is present, no other attribute may become the sole
    title -- create fails explicitly rather than silently demoting UID."""
    repository = FakePlanningRepository([base_snapshot])
    plan = planner.plan_mutation(create_request([create_operation("Other", is_title=True)]), repository).types[0]
    assert plan.status == "failed"
    assert plan.errors[0].code == "uid_is_sole_title"


@pytest.mark.parametrize("build_request,code", [
    (lambda: delete_request([1]), "uid_delete_forbidden"),
    (lambda: patch_request([patch_operation(1, {"title": "Identifier"})]), "uid_rename_forbidden"),
    (lambda: patch_request([patch_operation(1, {"required": False})]), "uid_required_forbidden"),
    (lambda: patch_request([patch_operation(1, {"is_title": False})]), "uid_title_forbidden"),
])
def test_uid_protected_mutations_fail_explicitly(planner, base_snapshot, build_request, code):
    """DD-18: UID cannot be deleted, renamed, un-required, or un-titled;
    each prohibited attempt fails with its own explicit code and zero
    planned change."""
    repository = FakePlanningRepository([base_snapshot])
    plan = planner.plan_mutation(build_request(), repository).types[0]
    assert plan.status == "failed"
    assert plan.errors[0].code == code
    assert plan.before == plan.after


def test_title_transition_kernel_is_a_pure_no_op_on_repromotion():
    """Section 11.7: re-promoting the row that is already the title is a
    no-op with no false automatic change (also exercised inside patch/create
    via the shared kernel)."""
    rows = (
        Definition(id=1, title="UID", sample_attribute_type_id=5, required=True, pos=1,
                   is_title=True, description=None, unit_id=None, sample_controlled_vocab_id=None,
                   linked_sample_type_id=None, updated_at=None),
    )
    updated, changes = apply_title_transition(rows, 1, operation_kind="patch")
    assert updated == rows
    assert changes == ()


def test_title_transition_kernel_rejects_non_uid_promotion_with_uid_present():
    """Section 11.7: the shared kernel itself (not just callers) enforces
    UID-sole-title -- promoting a non-UID row while UID exists raises
    without mutating any row."""
    rows = (
        Definition(id=1, title="UID", sample_attribute_type_id=5, required=True, pos=1,
                   is_title=True, description=None, unit_id=None, sample_controlled_vocab_id=None,
                   linked_sample_type_id=None, updated_at=None),
        Definition(id=2, title="RNA", sample_attribute_type_id=5, required=False, pos=2,
                   is_title=False, description=None, unit_id=None, sample_controlled_vocab_id=None,
                   linked_sample_type_id=None, updated_at=None),
    )
    from nextseek_api.attributes.planner import _TitleTransitionRejected
    with pytest.raises(_TitleTransitionRejected):
        apply_title_transition(rows, 2, operation_kind="patch")


def test_clear_previous_titles_never_clears_uid():
    """Section 11.7: UID is never cleared as an automatic side effect, even
    when it happens to hold ``is_title``."""
    rows = (
        Definition(id=1, title="UID", sample_attribute_type_id=5, required=True, pos=1,
                   is_title=True, description=None, unit_id=None, sample_controlled_vocab_id=None,
                   linked_sample_type_id=None, updated_at=None),
    )
    automatic = []
    result = clear_previous_titles(rows, 1, automatic)
    assert result == rows
    assert automatic == []


# ---------------------------------------------------------------------------
# Threshold / mode (DD-04, DD-28, Section 11.7 metadata-rewrite classifier)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("population,expected", [(99, "synchronous"), (100, "synchronous"), (101, "asynchronous")])
def test_threshold_boundaries_are_deterministic(planner, base_snapshot, population, expected):
    """DD-04/DD-28 as controlled by Section 11.9's obligation table: mode is
    asynchronous strictly *above* the threshold (at-threshold is still
    synchronous) -- a delete always requires a metadata rewrite, so its
    predicted affected-row count equals the type's population exactly."""
    repository = FakePlanningRepository([base_snapshot], populations={7: population})
    plan = planner.plan_mutation(delete_request([2]), repository)
    assert plan.affected_sample_rows == population
    assert plan.active_threshold == 100
    assert plan.predicted_mode == expected


@pytest.mark.parametrize("operation_kind,before_kwargs,after_kwargs,expected_class,expected_requires", [
    ("create", None, None, "create-new", True),
    ("delete", {}, None, "delete", True),
    ("patch", {}, {}, "true-noop", False),
    ("patch", {}, {"title": "New"}, "title-rename", True),
    ("patch", {}, {"description": "changed"}, "definition-only", False),
    ("patch", {}, {"required": True}, "definition-only", False),
    ("patch", {}, {"pos": 4}, "definition-only", False),
    ("patch", {}, {"is_title": True}, "definition-only", False),
    ("patch", {}, {"sample_attribute_type_id": 3}, "definition-only", False),
    ("patch", {}, {"unit_id": 9}, "definition-only", False),
    ("patch", {}, {"sample_controlled_vocab_id": 8}, "definition-only", False),
    ("patch", {}, {"linked_sample_type_id": 2}, "definition-only", False),
])
def test_metadata_rewrite_class(operation_kind, before_kwargs, after_kwargs, expected_class, expected_requires):
    """Section 11.7 shared classifier: create-new/title-rename/delete
    require a metadata rewrite; identical-create/true-noop/definition-only
    (description, required, position, is_title, value type, unit,
    vocabulary, linked-type) never do (Section 11.9
    test_metadata_rewrite_class[*])."""
    base = dict(id=2, title="RNA", sample_attribute_type_id=5, required=False, pos=2, is_title=False,
                description=None, unit_id=None, sample_controlled_vocab_id=None, linked_sample_type_id=None,
                updated_at=None)
    before = None if before_kwargs is None else Definition(**{**base, **before_kwargs})
    after = None if after_kwargs is None else Definition(**{**base, **after_kwargs})
    decision = classify_metadata_rewrite(before=before, after=after, operation_kind=operation_kind)
    assert decision.requires_metadata_rewrite is expected_requires
    assert decision.behavior_class == expected_class
    assert decision.reason


def test_metadata_rewrite_identical_create_requires_no_rewrite():
    """Section 11.9 test_metadata_rewrite_class[identical-create]: an
    idempotent create (matched to an existing identical row) requires no
    metadata rewrite."""
    existing = Definition(id=2, title="RNA", sample_attribute_type_id=5, required=False, pos=2, is_title=False,
                          description=None, unit_id=None, sample_controlled_vocab_id=None,
                          linked_sample_type_id=None, updated_at=None)
    decision = classify_metadata_rewrite(before=existing, after=None, operation_kind="create")
    assert decision.requires_metadata_rewrite is False
    assert decision.behavior_class == "identical-create"


def test_mixed_operations_contribute_population_once(planner, base_snapshot):
    """Section 11.7 test_metadata_rewrite_class[mixed-counts-once]: a mixed
    batch (one true-noop description patch + one genuine title-rename)
    contributes the type's population exactly once, driven by ``any(...)``
    over per-operation decisions rather than by operation count."""
    repository = FakePlanningRepository([base_snapshot], populations={7: 250})
    request = patch_request([
        patch_operation(2, {"description": None}),  # RNA's description is already None: true-noop
        patch_operation(3, {"title": "Renamed"}),  # genuine title-rename: requires rewrite
    ])
    plan = planner.plan_mutation(request, repository)
    assert plan.affected_sample_rows == 250
    item = plan.types[0]
    assert [d["behavior_class"] for d in item.rewrite_decisions] == ["true-noop", "title-rename"]


# ---------------------------------------------------------------------------
# Zero-write guarantee (DD-08)
# ---------------------------------------------------------------------------


def test_dry_run_has_zero_writes_jobs_or_dispatch(planner, base_snapshot):
    """DD-08: planning performs no definition/metadata write, job creation,
    outbox write, or lock acquisition -- this fake's own counters (which
    only a write/job/dispatch/lock call would increment) stay at zero. The
    real-boundary, separate-connection proof is
    ``test_planner_db.py::test_real_chain_no_write`` (deferred; requires a
    disposable database, not runnable tonight)."""
    repository = FakePlanningRepository([base_snapshot])
    planner.plan_mutation(patch_request([patch_operation(2, {"description": "x"})]), repository)
    assert repository.writes == 0
    assert repository.jobs == 0
    assert repository.dispatches == 0
    assert repository.locks == 0


# ---------------------------------------------------------------------------
# Plan-delta / policy gates (Section 3 "hardened plan-delta gate")
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("verdict", ["dependent_value_conversion_unknown", "cascade_unknown", "derived_title_unknown"])
def test_unresolved_dependent_policy_stops_type(planner, base_snapshot, verdict):
    """Unknown dependent-surface policy creates plan_delta_required, not
    execution, with zero writes and an unchanged before/after pair."""
    repository = FakePlanningRepository([base_snapshot], dependent=verdict)
    plan = planner.plan_mutation(delete_request([2]), repository).types[0]
    assert plan.status == "plan_delta_required"
    assert plan.errors[0].code == "dependent_policy_unresolved"
    assert plan.before == plan.after
    assert repository.writes == 0


def test_invalid_json_stops_type_without_writes(planner, base_snapshot):
    """Invalid JSON metadata in an affected type fails closed to
    plan_delta_required rather than coercing/dropping data."""
    repository = FakePlanningRepository([base_snapshot], invalid_json=1)
    plan = planner.plan_mutation(patch_request([patch_operation(2, {"title": "RNA2"})]), repository).types[0]
    assert plan.status == "plan_delta_required"
    assert plan.errors[0].code == "invalid_json_metadata"
    assert repository.writes == 0


@pytest.mark.parametrize(("code", "field"), [
    ("unit_not_found", "unit"), ("unit_ambiguous", "unit"),
    ("sample_controlled_vocab_not_found", "sample_controlled_vocab"),
    ("linked_sample_type_ambiguous", "linked_sample_type"),
])
def test_missing_or_ambiguous_relationship_fails_type(planner, base_snapshot, code, field):
    """DD-19: relationship identifiers are resolved (by T01) before the
    planner ever sees them; a missing/ambiguous relationship fails the
    whole type transaction, never coerced/guessed."""
    repository = FakePlanningRepository([base_snapshot])
    request = patch_request([{"attribute": 2, "changes": {}, "resolution_errors": [{
        "code": code, "message": code.replace("_", " "), "target_index": 0, "attribute_index": 0,
        "field": field, "submitted_identifier": "missing-or-ambiguous",
    }]}])
    plan = planner.plan_mutation(request, repository).types[0]
    assert plan.status == "failed"
    assert plan.errors[0].code == code
    assert (plan.errors[0].target_index, plan.errors[0].attribute_index, plan.errors[0].field) == (0, 0, field)


def test_plan_delta_type_is_never_an_execution_input(planner, base_snapshot):
    """A plan-delta type never appears in executable_types even though the
    overall request otherwise resolved."""
    repository = FakePlanningRepository([base_snapshot], dependent="dependent_values_require_policy")
    plan = planner.plan_mutation(patch_request([patch_operation(2, {"description": "x"})]), repository)
    assert plan.executable_types == ()
    assert plan.rejected_types[0].status == "plan_delta_required"
    assert plan.rejected_types[0].failed_operations == 1


# ---------------------------------------------------------------------------
# Concurrency identity (DD-23)
# ---------------------------------------------------------------------------


def test_full_set_versions_and_lock_oracle_are_captured(planner, base_snapshot):
    """DD-23: T05 captures the sample-type row plus every full ordered
    definition version and the sample_type-then-attribute-id lock order; it
    acquires zero locks itself (T07 alone locks/rechecks)."""
    repository = FakePlanningRepository([base_snapshot])
    plan = planner.plan_mutation(create_request([create_operation("Sibling")]), repository).types[0]
    assert plan.schema_identity["sample_type_id"] == 7
    assert plan.full_definition_versions == tuple(
        (identifier, datetime(2026, 1, 1, 0, 0, identifier, tzinfo=timezone.utc)) for identifier in (1, 2, 3)
    )
    assert plan.lock_order == (("sample_type", 7), ("sample_attribute", 1), ("sample_attribute", 2), ("sample_attribute", 3))
    assert plan.expected_conflict_oracle == "conflict_if_type_or_any_full_definition_version_differs"
    assert repository.locks == 0


@pytest.mark.parametrize("pair", ["create_create", "create_reorder", "reorder_reorder", "competing_title"])
def test_sibling_cases_emit_same_full_set_lock_identity(planner, base_snapshot, pair):
    """DD-23: every sibling-conflict shape (create/create, create/reorder,
    reorder/reorder, competing title) shares the identical full-set lock
    identity/order -- T05 does not narrow locking to only the touched
    row(s)."""
    repository = FakePlanningRepository([base_snapshot])
    plan = planner.plan_mutation(create_request([create_operation(f"Sibling-{pair}")]), repository).types[0]
    assert plan.concurrency_case == "full_set"
    assert plan.lock_order[0] == ("sample_type", 7)
    assert [lock[1] for lock in plan.lock_order[1:]] == [1, 2, 3]


# ---------------------------------------------------------------------------
# Serialization / T03 handoff
# ---------------------------------------------------------------------------


def test_created_semantic_fingerprint_is_independent_of_future_database_id(planner, base_snapshot):
    """A created row's expected semantic post-fingerprint is independent of
    whatever physical auto-increment ID it eventually receives, via the
    ``created:<target>:<attribute>`` token."""
    repository = FakePlanningRepository([base_snapshot])
    plan = planner.plan_mutation(create_request([create_operation("Future")]), repository).types[0]
    assert plan.created_identity_tokens == (("created:0:0", "Future"),)
    assert all(record["id"] is not None for record in plan.preview_records)
    assert plan.hypothetical_preview_records[0]["token"] == "created:0:0"
    assert "id" not in plan.hypothetical_preview_records[0]
    assert set(plan.hypothetical_preview_records[0]) == HYPOTHETICAL_PREVIEW_KEYS
    first_rows = tuple(replace(row, id=1001) if row.id is None else row for row in plan.after)
    second_rows = tuple(replace(row, id=9001) if row.id is None else row for row in plan.after)
    first = semantic_post_fingerprint(plan, first_rows, {"created:0:0": 1001})
    second = semantic_post_fingerprint(plan, second_rows, {"created:0:0": 9001})
    assert first == second == plan.expected_after_semantic_fingerprint


def test_recovery_binding_requires_unique_type_title_match(planner, base_snapshot):
    """T07's SQL collation join returns the token alongside the matched
    physical row; binding fails loudly if more than one row claims a
    token."""
    repository = FakePlanningRepository([base_snapshot])
    plan = planner.plan_mutation(create_request([create_operation("Future")]), repository).types[0]
    assert resolve_created_identity_bindings(plan, [("created:0:0", 77, "Future")]) == {"created:0:0": 77}
    with pytest.raises(ValueError, match="created identity is not unique"):
        resolve_created_identity_bindings(plan, [("created:0:0", 77, "Future"), ("created:0:0", 88, "future")])


def test_semantic_post_fingerprint_detects_actual_row_drift(planner, base_snapshot):
    """The post-execution semantic fingerprint changes if any planned row's
    actual physical state drifts from what was planned."""
    repository = FakePlanningRepository([base_snapshot])
    plan = planner.plan_mutation(patch_request([patch_operation(2, {"description": "x"})]), repository).types[0]
    assert semantic_post_fingerprint(plan, plan.after, {}) == plan.expected_after_semantic_fingerprint
    drifted = tuple(replace(row, required=not row.required) if row.id == 2 else row for row in plan.after)
    assert semantic_post_fingerprint(plan, drifted, {}) != plan.expected_after_semantic_fingerprint


def test_resolved_plan_envelope_matches_t03s_frozen_wire_shape(planner, base_snapshot):
    """T03's already-merged ``AttributeMutationAuditStore``/
    ``AttributeMutationJob`` (models_db.py) define the exact
    ``{schema_version, plan: {canonical_request_sha256, plan_content_sha256,
    execution_mode, actor, partition_sample_type_ids, partition_plans}}``
    wire shape; ``build_resolved_plan_envelope`` targets it byte-for-byte,
    proven here against T03's own pure structural validators (no DB)."""
    from nextseek_api.attributes.models_db import require_canonical_json, require_hash

    repository = FakePlanningRepository([base_snapshot])
    plan = planner.plan_mutation(patch_request([patch_operation(2, {"description": "x"})]), repository)
    payload = build_resolved_plan_envelope(plan, execution_mode="synchronous")
    document, _raw = require_canonical_json(payload, "resolved_plan_envelope", max_bytes=16 * 1024 * 1024)
    assert set(document) == {"schema_version", "plan"}
    assert document["schema_version"] == "attribute-mutation-plan/v1"
    plan_body = document["plan"]
    assert set(plan_body) == {
        "canonical_request_sha256", "plan_content_sha256", "execution_mode",
        "actor", "partition_sample_type_ids", "partition_plans",
    }
    require_hash(canonical_json(plan.canonical_submitted_request), plan_body["canonical_request_sha256"], "x")
    assert plan_body["partition_sample_type_ids"] == [7]
    assert plan_body["partition_plans"][0]["sample_type_id"] == 7
    assert plan_body["partition_plans"][0]["idempotency_key"] == plan.types[0].idempotency_key


def test_resolved_plan_envelope_requires_at_least_one_executable_type(planner, base_snapshot):
    """A wholly-rejected plan cannot be turned into a T03 job envelope."""
    repository = FakePlanningRepository([base_snapshot])
    plan = planner.plan_mutation(delete_request([1]), repository)  # UID delete -> failed
    assert plan.executable_types == ()
    with pytest.raises(ValueError, match="at least one executable type"):
        build_resolved_plan_envelope(plan, execution_mode="synchronous")


# ---------------------------------------------------------------------------
# Multi-target grouping / cross-target conflicts (DD-06)
# ---------------------------------------------------------------------------


def test_repeated_targets_collapse_to_one_atomic_type_plan(planner, base_snapshot):
    """Two targets naming the same resolved sample type collapse into one
    atomic ``TypeMutationPlan`` carrying both target indexes."""
    repository = FakePlanningRepository([base_snapshot])
    request = patch_request([patch_operation(2, {"description": "x"})])
    request["targets"].append({"sample_type": "Blood", "attributes": [patch_operation(3, {"description": "y"})]})
    plan = planner.plan_mutation(request, repository)
    assert len(plan.types) == 1
    assert plan.types[0].target_indexes == (0, 1)
    assert {row.description for row in plan.types[0].after if row.id in {2, 3}} == {"x", "y"}


def test_cross_target_conflict_fails_single_atomic_type_plan(planner, base_snapshot):
    """Two targets issuing conflicting operations against the same
    resolved attribute fail the single atomic type plan, preserving the
    second target's provenance index."""
    repository = FakePlanningRepository([base_snapshot])
    request = patch_request([patch_operation(2, {"description": "x"})])
    request["targets"].append({"sample_type": "Blood", "attributes": [patch_operation(2, {"description": "y"})]})
    plan = planner.plan_mutation(request, repository)
    assert len(plan.types) == 1
    item = plan.types[0]
    assert item.status == "failed"
    assert item.errors[0].code == "cross_target_conflict"
    assert (item.errors[0].target_index, item.errors[0].attribute_index) == (1, 0)


def test_identical_cross_target_creates_ignore_provenance_indexes(planner, base_snapshot):
    """Two targets submitting the identical create operation deduplicate
    into one unchanged/created outcome rather than a false conflict."""
    repository = FakePlanningRepository([base_snapshot])
    operation = create_operation("RNA", pos=2)
    request = create_request([operation])
    request["targets"].append({"sample_type": "Blood", "attributes": [dict(operation)]})
    plan = planner.plan_mutation(request, repository)
    assert len(plan.types) == 1
    assert plan.types[0].target_indexes == (0, 1)
    assert plan.types[0].status == "unchanged"
    assert not plan.types[0].errors
    assert plan.executable_types == ()


def test_unresolved_sample_type_makes_whole_request_preexecution_error(planner, base_snapshot):
    """A wholly-unresolved sample type short-circuits before any
    snapshot/dependent/invalid-json read and renders as a structural
    ``AttributeErrorResponse``."""
    repository = FakePlanningRepository([base_snapshot])
    request = patch_request([patch_operation(2, {"description": "x"})])
    request["targets"].insert(0, {
        "sample_type": "Missing",
        "resolution_errors": [{"code": "sample_type_not_found", "message": "missing"}],
    })
    plan = planner.plan_mutation(request, repository)
    assert [item.sample_type_id for item in plan.types] == [None]
    assert plan.types[0].errors[0].target_index == 0
    assert plan.wholly_nonexecutable is True
    assert plan.executable_types == ()
    assert plan.affected_sample_rows == 0
    assert repository.query_count == 1
    response = AttributeErrorResponse.model_validate({"errors": [error.__dict__ for error in plan.preexecution_errors]})
    assert response.errors[0].code == "sample_type_not_found"


def test_grouped_operation_error_keeps_original_target_and_item(planner, base_snapshot):
    """An error on a later-grouped target's operation preserves that
    target's own index, never the group's first target index."""
    repository = FakePlanningRepository([base_snapshot])
    request = patch_request([patch_operation(2, {"description": "x"})])
    request["targets"].append({"sample_type": "Blood", "attributes": [patch_operation(3, {"pos": 0})]})
    error = planner.plan_mutation(request, repository).types[0].errors[0]
    assert (error.target_index, error.attribute_index, error.field) == (1, 0, "pos")


def test_identical_create_repeated_across_targets_inserts_once(planner, base_snapshot):
    """Repeated identical create operations across targets insert exactly
    one physical row, not one per submission."""
    repository = FakePlanningRepository([base_snapshot])
    operation = create_operation("Future")
    request = create_request([operation])
    request["targets"].append({"sample_type": "Blood", "attributes": [dict(operation)]})
    result = planner.plan_mutation(request, repository).types[0]
    assert [item.title for item in result.after].count("Future") == 1
    assert result.counts["unchanged"] == 1


# ---------------------------------------------------------------------------
# Dedup / duplicate handling
# ---------------------------------------------------------------------------


def test_duplicate_identical_operation_deduplicates(planner, base_snapshot):
    """Identical duplicate patch operations against the same attribute
    deduplicate to one resolved outcome, while ``requested`` still counts
    both submissions."""
    repository = FakePlanningRepository([base_snapshot])
    operation = patch_operation(2, {"description": "x"})
    plan = planner.plan_mutation(patch_request([operation, dict(operation)]), repository).types[0]
    assert plan.status == "planned"
    assert plan.counts["requested"] == 2
    assert plan.counts["resolved"] == 1


def test_duplicate_conflicting_operation_fails(planner, base_snapshot):
    """Two duplicate operations against the same attribute with different
    payloads is an explicit conflict, never last-write-wins."""
    repository = FakePlanningRepository([base_snapshot])
    plan = planner.plan_mutation(patch_request([
        patch_operation(2, {"description": "x"}), patch_operation(2, {"description": "y"}),
    ]), repository).types[0]
    assert plan.status == "failed"
    assert plan.errors[0].code == "conflicting_duplicate_operation"


# ---------------------------------------------------------------------------
# Counts / public schema join (DD-33)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("build_request", "field"), [
    (lambda: create_request([create_operation("Future")]), "created"),
    (lambda: patch_request([patch_operation(2, {"description": "changed"})]), "patched"),
    (lambda: delete_request([2]), "deleted"),
])
def test_success_counts_join_public_mutation_counts(planner, base_snapshot, build_request, field):
    """Every executable outcome's ``counts`` dict validates directly
    against T01's public ``MutationCounts`` schema."""
    repository = FakePlanningRepository([base_snapshot])
    item = planner.plan_mutation(build_request(), repository).types[0]
    assert item.executable is True
    assert item.failed_operations == 0
    assert item.counts[field] == 1
    assert item.counts["resolved"] == 1
    public = MutationCounts.model_validate(item.counts)
    assert getattr(public, field) == 1


def test_identical_create_counts_unchanged_without_created(planner, base_snapshot):
    """An idempotent create's counts report unchanged=1, created=0, and
    zero affected samples."""
    repository = FakePlanningRepository([base_snapshot])
    item = planner.plan_mutation(create_request([create_operation("RNA", pos=2)]), repository).types[0]
    assert item.counts["unchanged"] == 1
    assert item.counts["created"] == 0
    assert item.counts["affected_samples"] == 0
    MutationCounts.model_validate(item.counts)


@pytest.mark.parametrize("build_request", [
    lambda: patch_request([patch_operation(2, {"description": "x"}), patch_operation(2, {"description": "y"})]),
    lambda: delete_request([1]),
])
def test_failed_type_is_excluded_from_execution_projection(planner, base_snapshot, build_request):
    """A failed type is fully excluded from executable_types/persistence
    and reports zero execution-result counters."""
    plan = planner.plan_mutation(build_request(), FakePlanningRepository([base_snapshot]))
    item = plan.types[0]
    assert item.status == "failed"
    assert item.executable is False
    assert plan.wholly_nonexecutable is False
    assert item.sample_type_id == 7
    assert item.failed_operations == item.counts["requested"]
    assert item in plan.rejected_types and item not in plan.executable_types
    assert all(item.counts[field] == 0 for field in ("created", "patched", "deleted", "unchanged", "updated_samples"))
    MutationCounts.model_validate(item.counts)


def test_preview_records_validate_against_attribute_record_schema(planner, base_snapshot):
    """DD-10/DD-33: preview_records are schema-valid ``AttributeRecord``
    dicts a T09 dry-run response can place directly into
    ``AttributeRecord[]``."""
    repository = FakePlanningRepository([base_snapshot])
    item = planner.plan_mutation(patch_request([patch_operation(2, {"description": "x"})]), repository).types[0]
    for record in item.preview_records:
        AttributeRecord.model_validate(record)


# ---------------------------------------------------------------------------
# Patch-title real-collation planning (Section 11.7)
# ---------------------------------------------------------------------------


def test_real_collation_two_way_title_swap_is_accepted(planner, base_snapshot):
    """A two-way rename (RNA<->Age) is accepted because the *final*
    assignment is unique under the real collation oracle, even though each
    side's raw match_ids would otherwise flag the other as a collision."""
    repository = FakePlanningRepository([base_snapshot])
    request = patch_request([patch_operation(2, {"title": "Age"}), patch_operation(3, {"title": "RNA"})])
    plan = planner.plan_mutation(request, repository).types[0]
    assert plan.status == "planned"
    titles = {item.id: item.title for item in plan.after}
    assert titles[2] == "Age" and titles[3] == "RNA"


def test_real_collation_many_to_one_final_assignment_is_rejected(planner, base_snapshot):
    """Two definitions renamed to the same final title within one batch is
    a many-to-one collision under the real collation oracle."""
    repository = FakePlanningRepository([base_snapshot])
    request = patch_request([patch_operation(2, {"title": "Shared"}), patch_operation(3, {"title": "Shared"})])
    plan = planner.plan_mutation(request, repository).types[0]
    assert plan.status == "failed"
    assert plan.errors[0].code == "stale_title_collation_oracle"


def test_real_collation_untouched_sibling_collision_is_rejected(planner, base_snapshot):
    """A rename onto an *untouched* sibling's current title is a
    collision (Section 11.7 patch-title real-collation planning)."""
    repository = FakePlanningRepository([base_snapshot])
    plan = planner.plan_mutation(patch_request([patch_operation(2, {"title": "Age"})]), repository).types[0]
    assert plan.status == "failed"
    assert plan.errors[0].code == "stale_title_collation_oracle"


def test_require_unique_final_collation_classes_direct():
    """Direct unit coverage of the many-to-one detector used by ``_patch``."""
    class _Row:
        def __init__(self, id_, target_index, attribute_index):
            self.id = id_
            self._target_index = target_index
            self._attribute_index = attribute_index

    classes = {
        (0, 0, "patch-final"): TitleCollationClass(class_key="7:shared", match_ids=()),
        (1, 0, "patch-final"): TitleCollationClass(class_key="7:shared", match_ids=()),
    }
    error = require_unique_final_collation_classes([_Row(2, 0, 0), _Row(3, 1, 0)], classes)
    assert error is not None and error.code == "stale_title_collation_oracle"


# ---------------------------------------------------------------------------
# UID protection helper (direct unit coverage)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("changes,code", [
    ({"title": "Renamed"}, "uid_rename_forbidden"),
    ({"required": False}, "uid_required_forbidden"),
    ({"is_title": False}, "uid_title_forbidden"),
])
def test_enforce_uid_protection_patch_direct(changes, code):
    current = Definition(id=1, title="UID", sample_attribute_type_id=5, required=True, pos=1, is_title=True,
                          description=None, unit_id=None, sample_controlled_vocab_id=None,
                          linked_sample_type_id=None, updated_at=None)
    error = enforce_uid_protection(current, {"changes": changes, "_target_index": 0, "_attribute_index": 0},
                                   operation_kind="patch")
    assert error is not None and error.code == code


def test_enforce_uid_protection_delete_direct():
    current = Definition(id=1, title="UID", sample_attribute_type_id=5, required=True, pos=1, is_title=True,
                          description=None, unit_id=None, sample_controlled_vocab_id=None,
                          linked_sample_type_id=None, updated_at=None)
    error = enforce_uid_protection(current, {"_target_index": 0, "_attribute_index": 0}, operation_kind="delete")
    assert error is not None and error.code == "uid_delete_forbidden"


def test_enforce_uid_protection_allows_untouched_uid():
    """Patching a non-UID field on UID (e.g. description) is not
    protected -- only rename/un-require/un-title/delete are."""
    current = Definition(id=1, title="UID", sample_attribute_type_id=5, required=True, pos=1, is_title=True,
                          description=None, unit_id=None, sample_controlled_vocab_id=None,
                          linked_sample_type_id=None, updated_at=None)
    error = enforce_uid_protection(current, {"changes": {"description": "note"}, "_target_index": 0, "_attribute_index": 0},
                                   operation_kind="patch")
    assert error is None


# ---------------------------------------------------------------------------
# Cross-target conflict helper (direct unit coverage)
# ---------------------------------------------------------------------------


def _grouped_create_operation(title, target_index, **overrides):
    """Build the exact shape ``_group_targets`` hands to
    ``cross_target_conflict``: a DD-26 submitted definition with its
    relationship identifiers normalized onto the resolved ``*_id`` planning
    fields, plus the ``_target_index``/``_attribute_index`` provenance
    markers. Feeding the raw submitted spelling here would test a shape the
    function never actually receives."""
    payload, errors = normalize_relationship_identifiers(
        create_operation(title, **overrides), target_index=target_index, attribute_index=0,
    )
    assert errors == []
    return {**payload, "_target_index": target_index, "_attribute_index": 0}


def test_cross_target_conflict_direct_identical_creates_do_not_conflict():
    operations = [
        _grouped_create_operation("RNA", 0, pos=2), _grouped_create_operation("RNA", 1, pos=2),
    ]
    assert cross_target_conflict("create", operations) is None


def test_cross_target_conflict_direct_differing_creates_conflict():
    operations = [
        _grouped_create_operation("RNA", 0, pos=2), _grouped_create_operation("RNA", 1, pos=3),
    ]
    error = cross_target_conflict("create", operations)
    assert error is not None and error.code == "cross_target_conflict"


# ---------------------------------------------------------------------------
# Bounded-work formula (manifest planner_query_formula; fake-adapter proxy)
# ---------------------------------------------------------------------------


def test_planner_query_formula_fake_adapter_proxy(planner, base_snapshot):
    """Proxy-only: counts fake-adapter calls, bounded by the manifest
    formula, for a single-type patch. This does NOT prove T04's real SQL
    statement count (see RCA-T04-FINDINGS-2026-08-03.md item under T05:
    the honest bound requires a real disposable-DB node,
    ``test_planner_db.py::test_real_chain_scale[...]``, deferred tonight)."""
    repository = FakePlanningRepository([base_snapshot])
    plan = planner.plan_mutation(patch_request([patch_operation(2, {"description": "x"})]), repository)
    assert len(plan.types) == 1
    unique_identifiers = 1
    assert repository.query_count <= 18 + 9 * ceil(unique_identifiers / 500)


def test_json_value_contract_round_trips_through_canonical_json():
    """orjson round-trips nested JSON-safe structures used throughout
    ``AutomaticChange.previous_value``/``new_value``."""
    value = {"a": [1, 2.5, None, True, "x"], "b": {"c": None}}
    assert orjson.loads(canonical_json(value)) == value


# ---------------------------------------------------------------------------
# Real T04 envelope consumption (create nesting + relationship resolution)
# ---------------------------------------------------------------------------


def test_create_definitions_are_read_from_the_resolved_envelope(planner, base_snapshot):
    """T04c nests a create definition under ``operations[*]["definition"]``
    and the submitted target names its list ``attributes`` (DD-26). Reading
    a submitted ``operations`` key instead silently plans zero create
    operations: ``requested`` collapses to 0 and the type reports
    ``unchanged`` rather than raising."""
    repository = FakePlanningRepository([base_snapshot])
    resolved = repository.resolve_mutation(create_request([create_operation("Fresh")]))
    operation = resolved["targets"][0]["operations"][0]
    assert set(operation) == {"attribute_id", "attribute_index", "resolution_errors", "definition"}
    assert operation["definition"]["title"] == "Fresh"
    assert "title" not in operation

    item = planner.plan_mutation(create_request([create_operation("Fresh")]), repository).types[0]
    assert item.counts["requested"] == 1
    assert item.counts["created"] == 1
    assert item.status == "planned" and item.executable is True
    assert item.created_identity_tokens == (("created:0:0", "Fresh"),)


def test_create_attribute_index_and_errors_come_from_the_resolved_wrapper(planner, base_snapshot):
    """``attribute_index`` and ``resolution_errors`` exist only on the
    resolved wrapper, never inside the nested definition; a wrapper error
    must still carry its own attribute index after flattening."""
    repository = FakePlanningRepository([base_snapshot])
    request = create_request([create_operation("A"), create_operation("B")])
    resolved = repository.resolve_mutation(request)
    resolved["targets"][0]["operations"][1]["resolution_errors"] = [
        {"code": "sample_attribute_type_not_found", "target_index": None,
         "attribute_index": None, "field": "sample_attribute_type", "submitted_identifier": "Nope"},
    ]
    repository.resolve_mutation = lambda _request: resolved
    item = planner.plan_mutation(request, repository).types[0]
    assert item.status == "failed"
    assert item.errors[0].code == "sample_attribute_type_not_found"
    assert (item.errors[0].target_index, item.errors[0].attribute_index) == (0, 1)


def test_resolution_error_without_message_key_is_accepted(planner, base_snapshot):
    """``repository._error_to_dict`` emits exactly ``{code, target_index,
    attribute_index, field, submitted_identifier}`` -- no ``message``. A bare
    ``PlanError(**error)`` raises ``TypeError`` on every real resolution
    failure; the code doubles as the message instead."""
    repository = FakePlanningRepository([base_snapshot])
    request = patch_request([{"attribute": 2, "changes": {}, "resolution_errors": [{
        "code": "attribute_not_found", "target_index": 0, "attribute_index": 0,
        "field": "attribute", "submitted_identifier": 99,
    }]}])
    item = planner.plan_mutation(request, repository).types[0]
    assert item.status == "failed"
    assert item.errors[0].code == "attribute_not_found"
    assert item.errors[0].message == "attribute not found"
    assert item.errors[0].submitted_identifier == 99


def test_unresolved_sample_type_error_without_message_is_accepted(planner, base_snapshot):
    """Same defect on the terminal (whole-target) resolution-failure path,
    which builds its ``PlanError`` values in ``_resolution_failure``."""
    repository = FakePlanningRepository([base_snapshot])
    request = patch_request([patch_operation(2, {"description": "x"})])
    request["targets"].insert(0, {"sample_type": "Missing", "resolution_errors": [{
        "code": "sample_type_not_found", "target_index": 0, "attribute_index": None,
        "field": "sample_type", "submitted_identifier": "Missing",
    }]})
    plan = planner.plan_mutation(request, repository)
    assert plan.types[0].errors[0].message == "sample type not found"
    assert plan.types[0].sample_type_title == ""


@pytest.mark.parametrize(("submitted", "expected"), [
    ({"unit": 9}, {"unit_id": 9}),
    ({"unit": None}, {"unit_id": None}),
    ({"sample_attribute_type": 3}, {"sample_attribute_type_id": 3}),
    ({"linked_sample_type": 4}, {"linked_sample_type_id": 4}),
    ({"sample_controlled_vocab": 6}, {"sample_controlled_vocab_id": 6}),
    ({"unit": 9, "unit_id": 11}, {"unit_id": 11}),
])
def test_normalize_relationship_identifiers_maps_ids(submitted, expected):
    """Defense-in-depth guard semantics (Amendment Log 2026-08-04 (2)): a
    real T04d envelope already spells these ``*_id``, so the guard only ever
    sees submitted names in a malformed envelope. There ``None`` and integer
    identifiers still map onto the resolved planning field (an integer is an
    ID under T04's identifier grammar); an already-resolved ``*_id`` wins."""
    normalized, errors = normalize_relationship_identifiers(
        dict(submitted), target_index=0, attribute_index=0,
    )
    assert errors == []
    assert normalized == expected


def test_normalize_relationship_identifiers_fails_closed_on_unflagged_title():
    """Defense-in-depth for malformed envelopes (Amendment Log 2026-08-04
    (2)): real T04d never emits an unflagged title spelling -- it either
    resolves the field to ``*_id`` or flags it in the operation's own
    ``resolution_errors``. A title spelling with no such flag therefore
    marks an envelope that bypassed the real adapter, and the guard fails it
    closed rather than guessing or silently dropping the identifier."""
    normalized, errors = normalize_relationship_identifiers(
        {"unit": "milligram"}, target_index=1, attribute_index=2,
    )
    assert normalized == {}
    assert errors[0]["code"] == "relationship_identifier_unresolved"
    assert (errors[0]["target_index"], errors[0]["attribute_index"]) == (1, 2)
    assert errors[0]["field"] == "unit"
    assert errors[0]["submitted_identifier"] == "milligram"


def test_normalize_relationship_identifiers_never_duplicates_t04_errors():
    """Amendment Log 2026-08-04 (2): when T04d could not resolve a field it
    leaves the submitted spelling in place AND reports the failure in the
    operation's wrapper ``resolution_errors``. The guard drops the leftover
    spelling without a second error -- T04's frozen-shape error is
    authoritative, and a duplicate would misreport one failure as two."""
    normalized, errors = normalize_relationship_identifiers(
        {"unit": "furlong", "sample_attribute_type": 5}, target_index=0, attribute_index=0,
        flagged_fields=frozenset({"unit"}),
    )
    assert errors == []
    assert normalized == {"sample_attribute_type_id": 5}


def test_title_spelled_create_relationship_resolves_and_plans(planner, base_snapshot):
    """USER-RULED semantics change (plan Amendment Log 2026-08-04 (2), Wave-4
    rel-id ruling): a title-spelled relationship identifier on a create
    definition now RESOLVES through the T04d bulk pass instead of failing
    closed, and the plan proceeds with the verified ``*_id``."""
    repository = FakePlanningRepository([base_snapshot])
    operation = create_operation("Fresh")
    operation["unit"] = "milligram"
    item = planner.plan_mutation(create_request([operation]), repository).types[0]
    assert item.status == "planned" and item.executable is True
    assert item.errors == ()
    assert [row.unit_id for row in item.after if row.id is None] == [9]
    assert item.created_identity_tokens == (("created:0:0", "Fresh"),)


def test_unresolvable_create_relationship_fails_with_t04d_code(planner, base_snapshot):
    """DD-15 end to end under the lifted semantics (Amendment Log 2026-08-04
    (2)): an unresolvable relationship identifier fails the whole type with
    T04d's own frozen ``unit_not_found`` code and provenance -- exactly one
    error, never a duplicate ``relationship_identifier_unresolved`` beside
    it, and never a planned row with a silently-null relationship id."""
    repository = FakePlanningRepository([base_snapshot])
    operation = create_operation("Fresh")
    operation["unit"] = "furlong"
    item = planner.plan_mutation(create_request([operation]), repository).types[0]
    assert item.status == "failed"
    assert [error.code for error in item.errors] == ["unit_not_found"]
    assert (item.errors[0].target_index, item.errors[0].attribute_index) == (0, 0)
    assert item.errors[0].field == "unit"
    assert item.errors[0].submitted_identifier == "furlong"
    assert item.before == item.after


def test_title_spelled_patch_relationship_resolves_and_plans(planner, base_snapshot):
    """The same user-ruled lift on the patch path (Amendment Log 2026-08-04
    (2)): a title-spelled value-type change in ``changes`` resolves through
    T04d and applies to the planned row."""
    repository = FakePlanningRepository([base_snapshot])
    item = planner.plan_mutation(
        patch_request([patch_operation(2, {"sample_attribute_type": "Float"})]), repository,
    ).types[0]
    assert item.status == "planned" and item.executable is True
    assert [row.sample_attribute_type_id for row in item.after if row.id == 2] == [3]


def test_unresolvable_patch_relationship_fails_with_t04d_code(planner, base_snapshot):
    """Unresolvable patch-side identifiers fail the type with the frozen
    T04d code for their field -- exactly one error per failed field, with the
    wrapper error's own provenance (Amendment Log 2026-08-04 (2)). Unit
    symbols exist in the fixture but are never a match key (DD-19)."""
    repository = FakePlanningRepository([base_snapshot])
    item = planner.plan_mutation(
        patch_request([patch_operation(2, {"sample_attribute_type": "Nonexistent"})]), repository,
    ).types[0]
    assert item.status == "failed"
    assert [error.code for error in item.errors] == ["sample_attribute_type_not_found"]
    assert item.errors[0].field == "sample_attribute_type"

    symbol = planner.plan_mutation(
        patch_request([patch_operation(2, {"unit": "mg"})]), FakePlanningRepository([base_snapshot]),
    ).types[0]
    assert symbol.status == "failed"
    assert [error.code for error in symbol.errors] == ["unit_not_found"]
    assert symbol.errors[0].submitted_identifier == "mg"


def test_ambiguous_relationship_title_fails_with_t04d_code(planner, base_snapshot):
    """A duplicate-title relationship match is ambiguous, never resolved to
    the first row (DD-03 duplicate-conflict precedent; the frozen
    ``{field}_ambiguous`` codes arrived with T04d)."""
    repository = FakePlanningRepository(
        [base_snapshot],
        relationship_rows={"unit": ((9, "milligram"), (11, "second"), (12, "Dup"), (13, "dup"))},
    )
    item = planner.plan_mutation(
        patch_request([patch_operation(2, {"unit": "Dup"})]), repository,
    ).types[0]
    assert item.status == "failed"
    assert [error.code for error in item.errors] == ["unit_ambiguous"]
    assert item.errors[0].submitted_identifier == "Dup"


def test_patch_relationship_id_change_applies_to_the_planned_row(planner, base_snapshot):
    """An integer-spelled relationship change normalizes onto the planning
    field, so ``replace()`` on the frozen ``Definition`` succeeds instead of
    raising ``TypeError: unexpected keyword argument 'unit'``."""
    repository = FakePlanningRepository([base_snapshot])
    item = planner.plan_mutation(patch_request([patch_operation(2, {"unit": 9})]), repository).types[0]
    assert item.status == "planned"
    assert [row.unit_id for row in item.after if row.id == 2] == [9]


# ---------------------------------------------------------------------------
# Display-enrichment bridge (TypeResolvedView.materialize_attribute_records)
# ---------------------------------------------------------------------------


def test_preview_created_at_comes_from_the_stored_row_not_updated_at(planner, base_snapshot):
    """``created_at`` is immutable and exists only on the live physical row,
    so it must come from ``display_fields_for``. A bridge that reuses the
    planning row's ``updated_at`` is detectable because the fixture's stored
    creation timestamp differs from every definition's ``updated_at``."""
    repository = FakePlanningRepository([base_snapshot])
    item = planner.plan_mutation(patch_request([patch_operation(2, {"description": "x"})]), repository).types[0]
    assert item.preview_records
    for record in item.preview_records:
        assert record["created_at"] == STORED_CREATED_AT.isoformat().replace("+00:00", "Z")
        assert record["created_at"] != record["updated_at"]


def test_preview_relationship_titles_follow_the_planned_ids(planner, base_snapshot):
    """``display_fields_for`` joins on each row's *currently stored*
    relationship ids, so its titles are stale for a row this plan re-points.
    The bridge must resolve display titles against the PLANNED ids -- a
    preview that shows the old value type beside the new id is wrong."""
    repository = FakePlanningRepository([base_snapshot])
    item = planner.plan_mutation(
        patch_request([patch_operation(2, {"sample_attribute_type": 77})]), repository,
    ).types[0]
    patched = [record for record in item.preview_records if record["id"] == 2][0]
    untouched = [record for record in item.preview_records if record["id"] == 3][0]
    assert patched["sample_attribute_type_id"] == 77
    assert patched["sample_attribute_type_title"] == "type-77"
    assert untouched["sample_attribute_type_title"] == "type-5"


def test_preview_sample_type_title_comes_from_the_locked_snapshot(planner, base_snapshot):
    """A definition cannot change its owning sample type, so its display
    title is free from the already-loaded ``TypeSnapshot`` and costs no
    extra query."""
    repository = FakePlanningRepository([base_snapshot])
    item = planner.plan_mutation(patch_request([patch_operation(2, {"description": "x"})]), repository).types[0]
    assert {record["sample_type_title"] for record in item.preview_records} == {"Blood"}
    assert {record["sample_type_id"] for record in item.preview_records} == {7}


def test_preview_records_are_real_attribute_record_models(planner, base_snapshot):
    """The real ``AttributeRepository.materialize_attribute_records`` returns
    pydantic ``AttributeRecord`` values (strict, ``extra="forbid"``), so the
    bridge's enriched rows must satisfy that model -- including non-null
    ``sample_type_title``/``sample_attribute_type_title`` and a non-null
    ``created_at``."""
    repository = FakePlanningRepository([base_snapshot])
    item = planner.plan_mutation(patch_request([patch_operation(2, {"description": "x"})]), repository).types[0]
    assert len(item.preview_records) == 3
    for record in item.preview_records:
        AttributeRecord.model_validate(record)


def test_vanished_planned_row_is_a_plan_delta_not_a_crash(planner, base_snapshot):
    """A row present in the locked planning snapshot but absent from
    ``display_fields_for`` was deleted concurrently. Fail the type closed
    rather than previewing a row that no longer exists."""
    repository = FakePlanningRepository([base_snapshot], absent_display_ids={3})
    item = planner.plan_mutation(patch_request([patch_operation(2, {"description": "x"})]), repository).types[0]
    assert item.status == "plan_delta_required"
    assert item.executable is False
    assert item.errors[0].code == "stale_planning_snapshot"
    assert item.after == item.before
    assert item.preview_records == ()
    assert item.counts["patched"] == 0


def test_bridge_short_circuits_on_an_empty_physical_set(planner, base_snapshot):
    """Deleting every physical row leaves nothing to enrich; the bridge must
    not issue a display lookup for an empty id list."""
    repository = FakePlanningRepository([type_snapshot(7, "Blood", (
        snapshot_definition(2, "RNA", 1), snapshot_definition(3, "Age", 2),
    ))])
    item = planner.plan_mutation(delete_request([2, 3]), repository).types[0]
    assert item.status == "planned"
    assert item.after == ()
    assert item.preview_records == ()


# ---------------------------------------------------------------------------
# Fail-closed guards (direct coverage of every rejection path)
# ---------------------------------------------------------------------------


def test_negative_threshold_is_rejected():
    """A negative threshold is a construction error, never clamped to zero."""
    with pytest.raises(ValueError, match="threshold must not be negative"):
        MutationPlanner(threshold=-1)


def test_actor_must_be_the_canonical_projection(planner, base_snapshot):
    """DD-02: the planner refuses any actor payload that is not exactly T02's
    ``AuthenticatedSeekPerson`` projection, rather than hashing a partial
    identity into the submitted request."""
    request = patch_request([patch_operation(2, {"description": "x"})])
    request["actor"] = {"person_id": 42, "login": "demo"}
    with pytest.raises(ValueError, match="canonical AuthenticatedSeekPerson"):
        planner.plan_mutation(request, FakePlanningRepository([base_snapshot]))


def test_unsupported_mutation_kind_is_rejected(planner, base_snapshot):
    """An envelope kind outside create/patch/delete is a hard error, never a
    silently empty plan."""
    request = patch_request([patch_operation(2, {"description": "x"})])
    request["kind"] = "upsert"
    with pytest.raises(ValueError, match="unsupported mutation kind"):
        planner.plan_mutation(request, FakePlanningRepository([base_snapshot]))


def test_find_by_identity_rejects_an_absent_promotion_target():
    """The title kernel refuses to promote a definition that is not in the
    planned set instead of silently promoting nothing."""
    rows = (Definition(id=1, title="RNA", sample_attribute_type_id=5, required=False, pos=1,
                       is_title=False, description=None, unit_id=None,
                       sample_controlled_vocab_id=None, linked_sample_type_id=None, updated_at=None),)
    with pytest.raises(LookupError):
        apply_title_transition(rows, 999, operation_kind="patch")


def test_semantic_post_fingerprint_marks_unexpected_physical_rows(planner, base_snapshot):
    """A physical row that is neither an expected existing id nor a bound
    created token is tagged, never silently folded into the expected
    fingerprint."""
    plan = planner.plan_mutation(patch_request([patch_operation(2, {"description": "x"})]), base_snapshot
                                 and FakePlanningRepository([base_snapshot])).types[0]
    intruder = replace(plan.after[0], id=4242)
    assert semantic_post_fingerprint(plan, (intruder,), {}) != plan.expected_after_semantic_fingerprint


def test_hypothetical_preview_shape_drift_is_rejected(planner, base_snapshot):
    """T04 preview-shape drift fails loudly: an extra or missing key in a
    hypothetical record is never passed through to a T09 response."""
    repository = FakePlanningRepository([base_snapshot])
    real = repository.materialize_hypothetical_records

    def drifted(definitions):
        return tuple({**record, "surprise": 1} for record in real(definitions))

    repository.materialize_hypothetical_records = drifted
    with pytest.raises(ValueError, match="hypothetical preview shape drift"):
        planner.plan_mutation(create_request([create_operation("Fresh")]), repository)


def test_target_level_resolution_error_on_a_resolved_type_fails_that_type(planner, base_snapshot):
    """A target that resolved its sample type but still carries a
    target-level resolution error fails the grouped type and keeps its own
    target index."""
    repository = FakePlanningRepository([base_snapshot])
    request = patch_request([patch_operation(2, {"description": "x"})])
    resolved = repository.resolve_mutation(request)
    resolved["targets"][0]["resolution_errors"] = [{
        "code": "attribute_owner_mismatch", "target_index": None, "attribute_index": None,
        "field": "attribute", "submitted_identifier": 2,
    }]
    repository.resolve_mutation = lambda _request: resolved
    item = planner.plan_mutation(request, repository).types[0]
    assert item.status == "failed"
    assert item.errors[0].code == "attribute_owner_mismatch"
    assert item.errors[0].target_index == 0


def _oracle_free_repository(base_snapshot):
    repository = FakePlanningRepository([base_snapshot])
    repository.title_collation_classes = lambda requests: {}
    return repository


def test_missing_create_collation_oracle_fails_the_operation(planner, base_snapshot):
    """A create whose title T04 did not resolve under database collation is
    rejected -- the planner never falls back to Python title equality."""
    item = planner.plan_mutation(
        create_request([create_operation("Fresh")]), _oracle_free_repository(base_snapshot),
    ).types[0]
    assert item.status == "failed"
    assert item.errors[0].code == "missing_title_collation_oracle"


def test_missing_patch_collation_oracle_fails_the_operation(planner, base_snapshot):
    """Same guard on the patch-title path."""
    item = planner.plan_mutation(
        patch_request([patch_operation(2, {"title": "Renamed"})]), _oracle_free_repository(base_snapshot),
    ).types[0]
    assert item.status == "failed"
    assert item.errors[0].code == "missing_title_collation_oracle"


def _collation_override(base_snapshot, entry, phase="create"):
    repository = FakePlanningRepository([base_snapshot])
    repository.title_collation_classes = lambda requests: {
        (item.target_index, item.attribute_index, phase): entry for item in requests
    }
    return repository


def test_create_title_matching_multiple_definitions_is_ambiguous(planner, base_snapshot):
    """One create title that the database collation maps onto more than one
    physical definition is ambiguous, never resolved to the first match."""
    repository = _collation_override(base_snapshot, TitleCollationClass(class_key="7:rna", match_ids=(2, 3)))
    item = planner.plan_mutation(create_request([create_operation("RNA")]), repository).types[0]
    assert item.status == "failed"
    assert item.errors[0].code == "attribute_ambiguous"


def test_create_title_match_absent_from_the_snapshot_is_stale(planner, base_snapshot):
    """A database title match that is not in the locked planning snapshot
    means the snapshot is stale; the type fails rather than planning against
    a row it cannot see."""
    repository = _collation_override(base_snapshot, TitleCollationClass(class_key="7:rna", match_ids=(9999,)))
    item = planner.plan_mutation(create_request([create_operation("RNA")]), repository).types[0]
    assert item.status == "failed"
    assert item.errors[0].code == "stale_title_collation_oracle"


def test_one_create_collation_class_resolving_to_two_definitions_is_stale(planner, base_snapshot):
    """Two creates whose titles share one database collation class but match
    different physical definitions is a stale-oracle failure, not a silent
    merge."""
    entries = {}

    def classes(requests):
        for index, item in enumerate(requests):
            entries[(item.target_index, item.attribute_index, "create")] = TitleCollationClass(
                class_key="7:shared", match_ids=(2,) if index else (),
            )
        return entries

    repository = FakePlanningRepository([base_snapshot])
    repository.title_collation_classes = classes
    item = planner.plan_mutation(
        create_request([create_operation("Shared"), create_operation("shared")]), repository,
    ).types[0]
    assert item.status == "failed"
    assert item.errors[0].code == "stale_title_collation_oracle"


def test_patch_against_an_unknown_attribute_fails(planner, base_snapshot):
    """A resolved attribute id that is absent from the locked snapshot fails
    that operation with ``attribute_not_found``."""
    repository = FakePlanningRepository([base_snapshot])
    request = patch_request([patch_operation(2, {"description": "x"})])
    resolved = repository.resolve_mutation(request)
    resolved["targets"][0]["operations"][0]["attribute_id"] = 4242
    repository.resolve_mutation = lambda _request: resolved
    item = planner.plan_mutation(request, repository).types[0]
    assert item.status == "failed"
    assert item.errors[0].code == "attribute_not_found"


def test_delete_of_an_unknown_attribute_fails(planner, base_snapshot):
    """Same guard on the delete path, before any row is removed."""
    repository = FakePlanningRepository([base_snapshot])
    request = delete_request([2])
    resolved = repository.resolve_mutation(request)
    resolved["targets"][0]["operations"][0]["attribute_id"] = 4242
    repository.resolve_mutation = lambda _request: resolved
    item = planner.plan_mutation(request, repository).types[0]
    assert item.status == "failed"
    assert item.errors[0].code == "attribute_not_found"
    assert item.before == item.after


def test_explicit_title_demotion_clears_only_that_definition(planner):
    """DD-17: ``is_title: false`` demotes exactly the targeted definition and
    never routes through the promotion kernel."""
    without_uid = type_snapshot(7, "Blood", (
        snapshot_definition(2, "RNA", 1, is_title=True), snapshot_definition(3, "Age", 2),
    ))
    repository = FakePlanningRepository([without_uid])
    item = planner.plan_mutation(patch_request([patch_operation(2, {"is_title": False})]), repository).types[0]
    assert item.status == "planned"
    assert [(row.id, row.is_title) for row in item.after] == [(2, False), (3, False)]
    assert not [change for change in item.automatic_changes if change.kind == "title_cleared"]

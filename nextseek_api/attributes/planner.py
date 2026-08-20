"""T05 deterministic dry-run mutation planner (DD-01..DD-35, Section 11.7).

``MutationPlanner.plan_mutation`` turns one T01-validated mutation envelope
into an immutable, deterministic, per-sample-type ``MutationPlan``. Planning
performs no definition/metadata/default-DB write, job creation, dispatch, or
lock acquisition (DD-08); the same plan is later consumed unchanged by
dry-run rendering (T09), synchronous execution (T07), and asynchronous
execution (T08).

Repository-view protocol
-------------------------

``plan_mutation(canonical_submitted_request, resolved_repository_view)``
consumes ``resolved_repository_view`` as the sole T04 identifier
interpreter. The exact methods this module calls on it are:

    resolve_mutation(request: dict) -> dict
    snapshots_for(resolved: dict) -> dict[int, TypeSnapshot]
    dependent_verdicts(type_ids, resolved) -> dict[int, str]
    invalid_json_counts(type_ids) -> dict[int, int]
    title_collation_classes(requests: Sequence[TitleCollationRequest])
        -> dict[tuple[int, int, str], TitleCollationClass]
    sample_type_populations(type_ids) -> dict[int, int]
    materialize_attribute_records(definitions) -> tuple[dict-like, ...]
    materialize_hypothetical_records(definitions: list[dict]) -> tuple[dict, ...]

All eight methods above exist on
``nextseek_api.attributes.repository.AttributeRepository`` as of the T04c
capability-gap closure (integration ``a6f4241``); this module is wired to,
and exercised against, the real repository over a disposable SEEK database
in ``nextseek_api/attributes/tests/test_planner_db.py``.

Envelope shapes this module consumes
------------------------------------

``resolve_mutation`` (``SeekAttributeGateway.resolve_mutation_envelope``)
reads each target's operations from the submitted ``target["attributes"]``
field -- the same field name for create, patch, and delete (DD-26;
``CreateTarget``/``PatchTarget``/``DeleteTarget`` in ``schemas.py``). It
returns one resolved operation per submitted operation, and this module
reads its operations *only* from the resolved envelope, never from the
submitted request:

* ``create``: ``{"attribute_id": None, "attribute_index": i,
  "resolution_errors": [...], "definition": <resolved definition dict>}``
  -- the definition payload is nested one level down under ``definition``
  and is flattened once, in ``_group_targets``.
* ``patch``: ``{"attribute_id": <int>, "attribute_index": i,
  "resolution_errors": [...], "changes": <resolved changes dict>}``.
* ``delete``: as patch, without ``changes``.

No resolved operation carries a ``target_index`` key and no resolved target
carries a ``sample_type`` key; both fall back to the resolved target's own
``target_index``/``sample_type_title``.

Relationship identifiers -- resolved by T04 (former KNOWN GAP, now CLOSED)
--------------------------------------------------------------------------

As of task-04d (user ruling, plan Amendment Log 2026-08-04 (2); merged at
integration ``d8806a9``), ``resolve_mutation_envelope`` bulk-resolves every
submitted relationship identifier (``sample_attribute_type``, ``unit``,
``sample_controlled_vocab``, ``linked_sample_type``) inside a create
definition and a patch ``changes`` sub-object. A resolvable identifier is
REPLACED by its verified ``*_id`` planning field (single source of truth;
an explicit null becomes ``*_id: None`` and an omitted key stays omitted,
preserving the T01 tri-state exactly), and an unresolvable one keeps its
submitted spelling in place while contributing one frozen 5-key error with
code ``{field}_{not_found,ambiguous}`` and full (target_index,
attribute_index) provenance to that operation's own ``resolution_errors``
(DD-19: unit symbols are never a match key). Those wrapper errors flow into
``PlanError`` through ``_plan_error_from_dict``/``_with_provenance`` and
fail the type as semantic resolution failures.

``normalize_relationship_identifiers`` therefore survives only as a
defense-in-depth conformance guard: on a well-formed resolved envelope every
relationship field already arrives spelled ``*_id`` and the function is a
pure pass-through. A leftover submitted-name spelling occurs either
alongside T04's own resolution error for that field -- in which case the
spelling is dropped without a duplicate planner error, because the wrapper
error is authoritative -- or in a malformed envelope that did not come
through the real adapter, where an unflagged null/integer still maps under
T04's identifier grammar and anything else fails the operation closed with
``relationship_identifier_unresolved`` rather than being guessed or
silently dropped (DD-15). Real T04d envelopes never reach that branch.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from typing import Any, Literal

import orjson

from nextseek_api.attributes.repository import (
    Definition as RepositoryDefinition,
    TitleCollationClass,
    TitleCollationRequest,
    TypeSnapshot,
    dd35_order_key,
)

PLAN_SCHEMA_VERSION = "attribute-mutation-plan/v1"

# Matches the real ``AttributeRepository.materialize_hypothetical_records``
# output shape exactly (key ``"token"``, not ``"identity_token"``).
HYPOTHETICAL_PREVIEW_KEYS = frozenset({
    "token", "title", "sample_type_id", "sample_type_title",
    "sample_attribute_type_id", "sample_attribute_type_title", "required", "pos",
    "is_title", "description", "unit_id", "unit_title", "unit_symbol",
    "sample_controlled_vocab_id", "sample_controlled_vocab_title",
    "linked_sample_type_id", "linked_sample_type_title",
})

_SEMANTIC_FIELDS = (
    "title", "sample_attribute_type_id", "required", "pos", "is_title",
    "description", "unit_id", "sample_controlled_vocab_id", "linked_sample_type_id",
)


def canonical_json(value: Any) -> bytes:
    """Sorted-key canonical bytes. The sole serialization primitive for
    every hash/fingerprint/identity in this module (Section 11.9)."""
    return orjson.dumps(value, option=orjson.OPT_SORT_KEYS | orjson.OPT_UTC_Z | orjson.OPT_NAIVE_UTC)


def canonical_sha256(value: Any) -> str:
    return sha256(canonical_json(value)).hexdigest()


# ---------------------------------------------------------------------------
# Immutable plan value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Definition:
    """T05's working planning row for one sample type. ``id`` is ``None``
    for a not-yet-created (planned) row. ``pos`` is the DD-35 logical
    position this module assigns/maintains; ``dd35_order_key`` supplies only
    the comparison key (T05 does not define a second ordering
    implementation). ``sample_type_id``/``identity_token`` are populated
    only at plan-assembly time, once a type's final planned set is known."""

    id: int | None
    title: str
    sample_attribute_type_id: int
    required: bool
    pos: int
    is_title: bool
    description: str | None
    unit_id: int | None
    sample_controlled_vocab_id: int | None
    linked_sample_type_id: int | None
    updated_at: datetime | None
    sample_type_id: int | None = None
    identity_token: str | None = None
    # Raw physical `sample_attributes.pos` at read time (``None`` for a
    # not-yet-created row). Kept only to detect/report DD-24 legacy-order
    # normalization; every semantic/business comparison uses ``pos`` (the
    # DD-35 logical position), never this field.
    physical_pos: int | None = None


@dataclass(frozen=True)
class AutomaticChange:
    kind: str
    attribute_id: int | None
    attribute_title: str
    field: str
    previous_value: Any
    new_value: Any


@dataclass(frozen=True)
class PlanError:
    code: str
    message: str
    target_index: int | None = None
    attribute_index: int | None = None
    field: str | None = None
    submitted_identifier: Any = None


# Submitted relationship-identifier name -> this module's resolved
# planning-field name. Since task-04d, `resolve_mutation_envelope` rewrites
# these to the `*_id` spelling itself; a submitted-name key only survives in
# the resolved envelope beside T04's own resolution error for that field
# (see the module docstring's closed-gap section).
RELATIONSHIP_IDENTIFIER_FIELDS = {
    "sample_attribute_type": "sample_attribute_type_id",
    "unit": "unit_id",
    "sample_controlled_vocab": "sample_controlled_vocab_id",
    "linked_sample_type": "linked_sample_type_id",
}


def _with_provenance(error: dict, target_index, attribute_index=None) -> dict:
    """Backfill a resolution error's submitted provenance.

    ``repository._error_to_dict`` always emits ``target_index``/
    ``attribute_index`` keys, frequently with a ``None`` value (its
    ``ResolutionError`` defaults), so ``error.get(key, fallback)`` returns
    ``None`` rather than the fallback and the operation's real provenance is
    silently lost. Only a genuinely non-null submitted value wins."""
    resolved = dict(error)
    if resolved.get("target_index") is None:
        resolved["target_index"] = target_index
    if resolved.get("attribute_index") is None:
        resolved["attribute_index"] = attribute_index
    return resolved


def _plan_error_from_dict(error: dict, *, target_index=None, attribute_index=None) -> PlanError:
    """Build a ``PlanError`` from a T04 resolution-error dict.

    ``repository._error_to_dict`` emits exactly ``{code, target_index,
    attribute_index, field, submitted_identifier}`` -- no ``message`` -- so a
    bare ``PlanError(**error)`` raises ``TypeError`` on every real resolution
    failure. The frozen error dict shape cannot change, so the code doubles as
    the human message when none was supplied."""
    payload = {key: value for key, value in error.items() if key != "message"}
    payload.setdefault("target_index", target_index)
    payload.setdefault("attribute_index", attribute_index)
    message = error.get("message")
    return PlanError(message=message if message else str(payload["code"]).replace("_", " "), **payload)


def normalize_relationship_identifiers(payload: dict, *, target_index, attribute_index,
                                       flagged_fields: frozenset = frozenset()):
    """Defense-in-depth conformance guard over a resolved envelope's
    relationship fields (Amendment Log 2026-08-04 (2): T04d now performs the
    real resolution -- see the module docstring's closed-gap section).

    On a well-formed T04d envelope every relationship field already arrives
    as ``*_id`` and this is a pass-through. A leftover submitted-name key is
    handled without ever interpreting a title: an already-resolved ``*_id``
    key wins; ``None`` and an integer map directly (T04's own identifier
    grammar makes an integer an ID); a field named in ``flagged_fields`` --
    the fields T04's own wrapper ``resolution_errors`` already reported for
    this operation -- is dropped without a duplicate error, because the
    frozen-shape T04 error is authoritative; anything else is a malformed
    envelope this module is not allowed to interpret, so the operation fails
    closed. Returns ``(normalized_payload, errors)``."""
    normalized = dict(payload)
    errors: list[dict] = []
    for submitted_name, planning_name in RELATIONSHIP_IDENTIFIER_FIELDS.items():
        if submitted_name not in normalized:
            continue
        value = normalized.pop(submitted_name)
        if planning_name in normalized:
            continue
        if value is None or (isinstance(value, int) and not isinstance(value, bool)):
            normalized[planning_name] = value
            continue
        if submitted_name in flagged_fields:
            continue
        errors.append({
            "code": "relationship_identifier_unresolved",
            "message": f"{submitted_name} identifier was not resolved to an id by T04",
            "target_index": target_index, "attribute_index": attribute_index,
            "field": submitted_name, "submitted_identifier": value,
        })
    return normalized, errors


@dataclass(frozen=True)
class MetadataRewriteDecision:
    requires_metadata_rewrite: bool
    behavior_class: Literal[
        "create-new", "title-rename", "delete",
        "identical-create", "true-noop", "definition-only",
    ]
    reason: str


class PlanDeltaRequired(Exception):
    """Raised internally to short-circuit a type to ``plan_delta_required``."""

    def __init__(self, error: PlanError) -> None:
        super().__init__(error.message)
        self.error = error


class _TitleTransitionRejected(Exception):
    def __init__(self, error: PlanError) -> None:
        super().__init__(error.message)
        self.error = error


def classify_metadata_rewrite(*, before: Definition | None, after: Definition | None,
                               operation_kind: str) -> MetadataRewriteDecision:
    """Pure semantic classifier imported unchanged by T05, T06, and T07
    (Section 11.7). ``before``/``after`` are per-operation semantic
    snapshots, not whole-type state:

    - ``create``: ``before`` is the matched pre-existing identical
      definition (idempotent create) or ``None`` (genuinely new); ``after``
      is unused.
    - ``delete``: ``before``/``after`` are unused; deleting always requires
      a rewrite.
    - ``patch``: ``before``/``after`` are the same attribute's semantic
      state before/after the change.
    """
    if operation_kind == "create":
        if before is None:
            behavior_class, reason = "create-new", "no prior definition with this identity existed"
        else:
            behavior_class, reason = "identical-create", "an identical definition already exists; create is idempotent"
    elif operation_kind == "delete":
        behavior_class, reason = "delete", "the definition is being removed"
    elif operation_kind == "patch":
        if before == after:
            behavior_class, reason = "true-noop", "no semantic field changed"
        elif before.title != after.title:
            behavior_class, reason = "title-rename", "title changed"
        else:
            behavior_class, reason = "definition-only", "only definition-only fields changed"
    else:
        raise ValueError(f"unsupported operation_kind: {operation_kind!r}")
    requires = behavior_class in {"create-new", "title-rename", "delete"}
    return MetadataRewriteDecision(requires, behavior_class, reason)


def clear_previous_titles(planned_set, promoted_target, recorded_changes):
    """Clear every actual non-promoted title in ``planned_set``, appending one
    ``AutomaticChange`` per real clear to ``recorded_changes``.

    The parameter names deliberately differ from the caller's local names:
    the frozen ``M-TITLE-CREATE-01`` mutation anchor (task-05 Section 11.10)
    is the literal call expression in ``apply_title_transition``, and it must
    occur exactly once in this file."""
    cleared = []
    for item in planned_set:
        if item.is_title and item is not promoted_target and item.id != _identity_of(promoted_target):
            recorded_changes.append(AutomaticChange("title_cleared", item.id, item.title, "is_title", True, False))
            cleared.append(replace(item, is_title=False))
        else:
            cleared.append(item)
    return tuple(cleared)


def _identity_of(promoted_id):
    return promoted_id.id if isinstance(promoted_id, Definition) else promoted_id


def _find_by_identity(definitions, promoted_id):
    for item in definitions:
        if item is promoted_id:
            return item
        if item.id is not None and item.id == promoted_id:
            return item
    raise LookupError("promoted definition not found in the planned set")


def apply_title_transition(definitions, promoted_id, *, operation_kind):
    """The one pure DD-17/DD-18 title-transition kernel shared by create and
    patch (Section 11.7). ``promoted_id`` is either a real attribute ID
    (patch) or the not-yet-created ``Definition`` instance itself (create),
    since a pending create has no ID to key by. Before promoting any
    definition to ``is_title=True``: if UID exists and the promoted target
    is not UID, reject with ``uid_is_sole_title`` and change nothing; if UID
    is absent, clear every actual previous title definition (never UID) and
    emit one ``AutomaticChange`` per real clear. Re-promoting the current
    title is a no-op. Returns ``(definitions, automatic_changes)``."""
    target = _find_by_identity(definitions, promoted_id)
    if target.is_title:
        return definitions, ()
    uid = next((item for item in definitions if item.title == "UID"), None)
    if uid is not None and promoted_id != uid.id:
        raise _TitleTransitionRejected(PlanError("uid_is_sole_title", "UID must remain sole title"))
    automatic_changes: list[AutomaticChange] = []
    definitions = clear_previous_titles(definitions, promoted_id, automatic_changes)
    definitions = tuple(
        replace(item, is_title=True) if (item is target or (item.id is not None and item.id == promoted_id)) else item
        for item in definitions
    )
    return definitions, tuple(automatic_changes)


def enforce_uid_protection(current: Definition, operation: dict, *, operation_kind: str) -> PlanError | None:
    """Reject a patch/delete that would rename, un-require, un-title, or
    remove the UID definition, before any planned or physical change
    (Section 11.7 / DD-18)."""
    target_index = operation.get("_target_index")
    attribute_index = operation.get("_attribute_index")
    if current.title == "UID" and operation_kind == "patch":
        changes = operation.get("changes", {})
        if "title" in changes and changes["title"] != "UID":
            return PlanError("uid_rename_forbidden", "UID cannot be renamed", target_index, attribute_index, "title")
        if changes.get("required") is False:
            return PlanError("uid_required_forbidden", "UID must remain required", target_index, attribute_index, "required")
        if changes.get("is_title") is False:
            return PlanError("uid_title_forbidden", "UID must remain the title", target_index, attribute_index, "is_title")
    if current.title == "UID" and operation_kind == "delete":
        return PlanError("uid_delete_forbidden", "UID cannot be deleted", target_index, attribute_index)
    return None


def require_unique_final_collation_classes(final_definitions, collation_classes):
    """Reject a final planned title assignment where two planned rows
    resolve to the same real-database collation class (many-to-one), or a
    row's own final title change resolves to a class already claimed by a
    different row. Two-way swaps pass only when each side's final class is
    individually unique (Section 11.7 patch-title real-collation
    planning)."""
    claims: dict[str, int | None] = {}
    for item in final_definitions:
        key = (getattr(item, "_target_index", None), getattr(item, "_attribute_index", None), "patch-final")
        entry = collation_classes.get(key)
        if entry is None:
            continue
        holder = claims.get(entry.class_key)
        if holder is not None and holder != item.id:
            return PlanError(
                "stale_title_collation_oracle",
                "final title assignment is many-to-one under the database collation",
                key[0], key[1], "title",
            )
        claims[entry.class_key] = item.id
    return None


def _semantics(item: Definition) -> dict:
    return {field_name: getattr(item, field_name) for field_name in _SEMANTIC_FIELDS}


def _renumber(definitions):
    """Reassign contiguous positive logical positions 1..N from the
    definitions' *current list order*. DD-16/DD-24 insert/move/append are
    list-index operations applied directly to the working list (see
    ``_create``/``_patch``); once applied, list order already *is* the
    correct final order and must not be re-sorted by raw ``pos`` values a
    second time -- doing so would silently undo an explicit insert-at-
    position (an inserted row's requested position can coincide with an
    existing row's position, at which point a value re-sort and a list-
    order renumbering disagree, and only the latter is DD-16-correct)."""
    return tuple(replace(item, pos=index) for index, item in enumerate(definitions, start=1))


def _dd35_normalize_snapshot(snapshot_definitions):
    """The one place T05 calls the imported ``dd35_order_key`` directly:
    defensively re-establish DD-35 order over a `TypeSnapshot`'s
    definitions (T04's own `type_snapshots` already returns this order, but
    the comparison key -- not a second ordering implementation -- is
    T04-owned per Section 11.7, so this reuses it rather than trusting list
    order silently)."""

    class _SnapshotPosView:
        __slots__ = ("pos", "id")

        def __init__(self, item):
            self.pos = item.physical_pos
            self.id = item.id

    return sorted(snapshot_definitions, key=lambda item: dd35_order_key(_SnapshotPosView(item)))


@dataclass(frozen=True)
class TypeMutationPlan:
    sample_type_id: int | None
    sample_type_title: str
    target_indexes: tuple[int, ...]
    status: str
    executable: bool
    failed_operations: int
    counts: dict
    before: tuple[Definition, ...]
    after: tuple[Definition, ...]
    automatic_changes: tuple[AutomaticChange, ...]
    errors: tuple[PlanError, ...]
    schema_identity: dict
    full_definition_versions: tuple
    lock_order: tuple
    expected_conflict_oracle: str
    concurrency_case: str
    dependent_surface_verdict: str
    idempotency_key: str
    before_physical_fingerprint: str
    expected_after_semantic_fingerprint: str
    created_identity_tokens: tuple
    preview_records: tuple[dict, ...] = ()
    hypothetical_preview_records: tuple[dict, ...] = ()
    rewrite_decisions: tuple[dict, ...] = ()


@dataclass(frozen=True)
class MutationPlan:
    """T05's immutable planning result. ``canonical_submitted_request`` is
    the pre-resolution T01 projection, hashed before any T04 call (Section
    11.7); it is never reconstructed from planned output. ``types`` and its
    derived projections are the sole T03/T07/T08/T09 execution/render
    surface (see the property docstrings)."""

    canonical_submitted_request: dict
    canonical_submitted_request_sha256: str
    actor: dict
    types: tuple[TypeMutationPlan, ...]
    affected_sample_rows: int
    active_threshold: int
    predicted_mode: str

    @property
    def unresolved_types(self):
        return tuple(item for item in self.types if item.sample_type_id is None)

    @property
    def wholly_nonexecutable(self):
        return bool(self.unresolved_types)

    @property
    def preexecution_errors(self):
        return tuple(error for item in self.unresolved_types for error in item.errors)

    @property
    def executable_types(self):
        """The only projection T03/T07/T08 may persist, claim, or execute."""
        if self.wholly_nonexecutable:
            return ()
        return tuple(item for item in self.types if item.status == "planned" and item.executable)

    @property
    def rejected_types(self):
        """Stable failed outcomes retained for dry-run/207/4xx rendering only."""
        return tuple(item for item in self.types if item.status in {"failed", "plan_delta_required"})

    @property
    def unchanged_types(self):
        """Terminal no-op outcomes rendered directly without persistence or execution."""
        return tuple(item for item in self.types if item.status == "unchanged")


# ---------------------------------------------------------------------------
# T03 resolved-plan envelope (durable audit/job storage shape)
# ---------------------------------------------------------------------------
#
# `AttributeMutationJob`/`AttributeMutationAuditStore` (already merged,
# nextseek_api/attributes/models_db.py) define the frozen wire shape T05
# must target: `{"schema_version": "attribute-mutation-plan/v1",
# "plan": {canonical_request_sha256, plan_content_sha256, execution_mode,
# actor, partition_sample_type_ids, partition_plans}}`, where
# `canonical_request_sha256` is *the submitted-request hash* (not a
# separate resolved-plan hash) and each `partition_plans` entry carries
# exactly `sample_type_id`, `idempotency_key`, `before_physical_fingerprint`,
# `expected_after_semantic_fingerprint`, `created_identity_tokens`. This is
# a much smaller envelope than the full `MutationPlan`/`TypeMutationPlan`
# object graph; T07/T09 consume the rich in-memory objects directly and the
# durable envelope exists only for T03 storage / DD-23 async re-plan-and-
# recheck (see the final report for why a full round-trip codec is not part
# of the real contract).


def build_resolved_plan_envelope(plan: MutationPlan, *, execution_mode: str) -> bytes:
    if not plan.executable_types:
        raise ValueError("resolved-plan envelope requires at least one executable type")
    partition_plans = [
        {
            "sample_type_id": item.sample_type_id,
            "idempotency_key": item.idempotency_key,
            "before_physical_fingerprint": item.before_physical_fingerprint,
            "expected_after_semantic_fingerprint": item.expected_after_semantic_fingerprint,
            "created_identity_tokens": list(item.created_identity_tokens),
        }
        for item in plan.executable_types
    ]
    plan_content = {
        "canonical_request_sha256": plan.canonical_submitted_request_sha256,
        "execution_mode": execution_mode,
        "actor": dict(plan.actor),
        "partition_sample_type_ids": sorted(item.sample_type_id for item in plan.executable_types),
        "partition_plans": partition_plans,
    }
    plan_content_sha256 = canonical_sha256(plan_content)
    document = {
        "schema_version": PLAN_SCHEMA_VERSION,
        "plan": {**plan_content, "plan_content_sha256": plan_content_sha256},
    }
    return canonical_json(document)


def resolve_created_identity_bindings(type_plan: TypeMutationPlan, rows):
    """T07's SQL collation join returns ``(token, physical_id, actual_title)``
    rows; bind each expected created-identity token to exactly one physical
    row."""
    bindings = {}
    expected_tokens = {token for token, _ in type_plan.created_identity_tokens}
    for token in expected_tokens:
        matches = [identifier for returned_token, identifier, _actual_title in rows if returned_token == token]
        if len(matches) != 1:
            raise ValueError("created identity is not unique")
        bindings[token] = matches[0]
    return bindings


def semantic_post_fingerprint(type_plan: TypeMutationPlan, actual_rows, created_id_bindings):
    token_by_id = {identifier: token for token, identifier in created_id_bindings.items()}
    expected_existing_ids = {item.id for item in type_plan.after if item.id is not None}
    semantic_rows = []
    for item in sorted(actual_rows, key=lambda row: (row.pos is None, row.pos, row.id)):
        if item.id in token_by_id:
            identity = token_by_id[item.id]
        elif item.id in expected_existing_ids:
            identity = item.id
        else:
            identity = ("unexpected_physical_id", item.id)
        semantic_rows.append((identity, *_semantics(item).values()))
    return canonical_sha256(semantic_rows)


def cross_target_conflict(kind, operations):
    seen: dict[tuple, tuple] = {}
    for operation in operations:
        if kind == "create":
            key = ("title", operation["title"])
            defaults = {
                "required": False, "pos": None, "is_title": False, "description": None,
                "unit_id": None, "sample_controlled_vocab_id": None, "linked_sample_type_id": None,
            }
            value = canonical_sha256({
                "title": operation["title"],
                "sample_attribute_type_id": operation["sample_attribute_type_id"],
                **{field_name: operation.get(field_name, default) for field_name, default in defaults.items()},
            })
        else:
            key = ("attribute_id", operation.get("attribute_id"))
            value = canonical_sha256(operation.get("changes", {"delete": True}))
        prior = seen.get(key)
        if prior and prior[0] != value and prior[1] != operation["_target_index"]:
            return PlanError(
                "cross_target_conflict",
                f"conflicting operations for {key} across targets {prior[1]} and {operation['_target_index']}",
                operation["_target_index"], operation["_attribute_index"],
            )
        seen[key] = (value, operation["_target_index"])
    return None


class MutationPlanner:
    def __init__(self, *, threshold: int):
        if threshold < 0:
            raise ValueError("threshold must not be negative")
        self.threshold = threshold

    # -- entry point ----------------------------------------------------

    def plan_mutation(self, canonical_submitted_request, resolved_repository_view):
        submitted_bytes = canonical_json(canonical_submitted_request)
        submitted_sha256 = sha256(submitted_bytes).hexdigest()
        actor = dict(canonical_submitted_request["actor"])
        if tuple(sorted(actor)) != ("django_user_id", "login", "person_id", "scheme"):
            raise ValueError("actor must be the canonical AuthenticatedSeekPerson projection")
        kind = canonical_submitted_request["kind"]
        # dry_run never affects *planning* (DD-08: planning is identical and
        # write-free regardless of the flag). It is already preserved
        # verbatim inside `canonical_submitted_request` for downstream
        # (T07/T09) consumption -- nothing to extract here.
        resolved = resolved_repository_view.resolve_mutation(canonical_submitted_request)
        resolved_targets = resolved["targets"]
        for fallback_index, target in enumerate(resolved_targets):
            target.setdefault("target_index", fallback_index)

        unresolved = [target for target in resolved_targets if target.get("sample_type_id") is None]
        if unresolved:
            failures = []
            for target in unresolved:
                target["resolution_errors"] = [
                    _with_provenance(error, target["target_index"])
                    for error in target.get("resolution_errors", [])
                ]
                failures.append(self._resolution_failure(target))
            failures = tuple(sorted(failures, key=lambda item: item.target_indexes[0]))
            return MutationPlan(canonical_submitted_request, submitted_sha256, actor, failures, 0, self.threshold, "synchronous")

        snapshots = resolved_repository_view.snapshots_for(resolved)
        type_ids = sorted(snapshots)
        dependent = resolved_repository_view.dependent_verdicts(type_ids, resolved)
        invalid_json = resolved_repository_view.invalid_json_counts(type_ids)
        populations = resolved_repository_view.sample_type_populations(type_ids)

        grouped, plan_order = self._group_targets(kind, resolved_targets)
        collation_classes = self._collation_classes_for(kind, grouped, resolved_repository_view)

        hydrated: dict[int, TypeMutationPlan] = {}
        for type_id, target in grouped.items():
            type_view = TypeResolvedView(
                snapshot=snapshots[type_id], collation_classes=collation_classes,
                population=populations.get(type_id, 0), delegate=resolved_repository_view,
            )
            type_plan = self._plan_type(
                kind, target["target_indexes"][0], target, type_view.snapshot,
                dependent[type_id], invalid_json[type_id], type_view,
            )
            hydrated[type_id] = self._materialize_previews(type_plan, type_id, type_view)

        type_plans = [hydrated[type_id] for type_id in plan_order]
        total = sum(item.counts["affected_samples"] for item in type_plans if item.executable)
        mode = "asynchronous" if total > self.threshold else "synchronous"
        return MutationPlan(canonical_submitted_request, submitted_sha256, actor, tuple(type_plans), total, self.threshold, mode)

    # -- request grouping -------------------------------------------------

    def _group_targets(self, kind, resolved_targets):
        grouped: dict[int, dict] = {}
        plan_order: list[int] = []
        for target in resolved_targets:
            type_id = target["sample_type_id"]
            if type_id not in grouped:
                plan_order.append(type_id)
            group = grouped.setdefault(type_id, {
                "sample_type_id": type_id, "sample_type_title": target["sample_type_title"],
                "target_indexes": [], "operations": [], "resolution_errors": [],
            })
            group["target_indexes"].append(target["target_index"])
            for error in target.get("resolution_errors", []):
                group["resolution_errors"].append(_with_provenance(error, target["target_index"]))

            # Every kind reads the *resolved* envelope. A create operation
            # nests its definition payload one level down under "definition"
            # (T04c passthrough); patch/delete operations are already flat.
            # `attribute_index`/`resolution_errors` always come from the
            # resolved wrapper, which is the only place they exist.
            for fallback_attribute_index, operation in enumerate(target["operations"]):
                target_index = operation.get("target_index", target["target_index"])
                attribute_index = operation.get("attribute_index", fallback_attribute_index)
                # Fields T04's own wrapper errors already reported for this
                # operation: their leftover submitted spellings are markers of
                # an authoritative T04 failure, never double-reported here.
                flagged = frozenset(
                    error["field"] for error in operation.get("resolution_errors", [])
                    if isinstance(error, dict) and error.get("field")
                )
                payload = operation["definition"] if kind == "create" else operation
                payload, relationship_errors = normalize_relationship_identifiers(
                    payload, target_index=target_index, attribute_index=attribute_index,
                    flagged_fields=flagged,
                )
                normalized = dict(payload, _target_index=target_index, _attribute_index=attribute_index)
                if "changes" in normalized:
                    changes, change_errors = normalize_relationship_identifiers(
                        normalized["changes"], target_index=target_index, attribute_index=attribute_index,
                        flagged_fields=flagged,
                    )
                    normalized["changes"] = changes
                    relationship_errors += change_errors
                normalized["resolution_errors"] = [
                    _with_provenance(error, target_index, attribute_index)
                    for error in operation.get("resolution_errors", [])
                ] + relationship_errors
                group["operations"].append(normalized)
        return grouped, plan_order

    def _collation_classes_for(self, kind, grouped, resolved_repository_view):
        requests: list[TitleCollationRequest] = []
        for target in grouped.values():
            type_id = target["sample_type_id"]
            for operation in target["operations"]:
                if kind == "create" and "title" in operation:
                    requests.append(TitleCollationRequest(
                        target_index=operation["_target_index"], attribute_index=operation["_attribute_index"],
                        phase="create", sample_type_id=type_id, title=operation["title"],
                    ))
                elif kind == "patch" and "title" in operation.get("changes", {}):
                    requests.append(TitleCollationRequest(
                        target_index=operation["_target_index"], attribute_index=operation["_attribute_index"],
                        phase="patch-final", sample_type_id=type_id, title=operation["changes"]["title"],
                        exclude_id=operation.get("attribute_id"),
                    ))
        if not requests:
            return {}
        return resolved_repository_view.title_collation_classes(requests)

    # -- preview materialization -------------------------------------------

    def _materialize_previews(self, type_plan, type_id, view):
        if type_plan.status not in {"planned", "unchanged"}:
            return type_plan
        token_by_title = {title: token for token, title in type_plan.created_identity_tokens}
        definitions = tuple(
            replace(item, sample_type_id=type_id, identity_token=token_by_title.get(item.title) if item.id is None else None)
            for item in type_plan.after
        )
        physical = tuple(item for item in definitions if item.id is not None)
        hypothetical = tuple(item for item in definitions if item.id is None)
        try:
            records = view.materialize_attribute_records(physical)
        except PlanDeltaRequired as delta:
            # A planned row vanished between the locked planning snapshot and
            # display enrichment (concurrent delete). Fail the type closed
            # rather than previewing a row that no longer exists.
            return replace(
                type_plan, status="plan_delta_required", executable=False,
                after=type_plan.before, automatic_changes=(), errors=(delta.error,),
                created_identity_tokens=(), rewrite_decisions=(),
                failed_operations=type_plan.counts["requested"],
                counts={**type_plan.counts, "created": 0, "patched": 0, "deleted": 0, "unchanged": 0,
                        "reordered": 0, "affected_samples": 0, "updated_samples": 0},
            )
        hypothetical_dicts = [
            {
                "token": item.identity_token, "title": item.title, "sample_type_id": item.sample_type_id,
                "sample_attribute_type_id": item.sample_attribute_type_id, "required": item.required,
                "pos": item.pos, "is_title": item.is_title, "description": item.description,
                "unit_id": item.unit_id, "sample_controlled_vocab_id": item.sample_controlled_vocab_id,
                "linked_sample_type_id": item.linked_sample_type_id,
            }
            for item in hypothetical
        ]
        hypothetical_records = view.materialize_hypothetical_records(hypothetical_dicts)
        if any(set(record) != HYPOTHETICAL_PREVIEW_KEYS for record in hypothetical_records):
            raise ValueError("T04 hypothetical preview shape drift")
        serialized = tuple(dict(record) if isinstance(record, dict) else record.model_dump(mode="json") for record in records)
        return replace(type_plan, preview_records=serialized, hypothetical_preview_records=tuple(hypothetical_records))

    # -- resolution-failure projection --------------------------------------

    def _resolution_failure(self, target):
        errors = tuple(_plan_error_from_dict(error) for error in target["resolution_errors"])
        identity = canonical_sha256({"target_index": target["target_index"], "errors": target["resolution_errors"]})
        return TypeMutationPlan(
            # A resolved target never echoes the submitted `sample_type`
            # identifier, and its `sample_type_title` is None precisely
            # because resolution failed; the submitted spelling survives on
            # each error's `submitted_identifier`.
            sample_type_id=None, sample_type_title=str(target.get("sample_type_title") or ""),
            target_indexes=(target["target_index"],), status="failed",
            executable=False, failed_operations=len(target.get("operations", [])),
            counts={"requested": len(target.get("operations", [])), "resolved": 0, "created": 0, "patched": 0,
                    "deleted": 0, "unchanged": 0, "reordered": 0, "affected_samples": 0, "updated_samples": 0},
            before=(), after=(), automatic_changes=(), errors=errors, schema_identity={},
            full_definition_versions=(), lock_order=(), expected_conflict_oracle="not_executable",
            concurrency_case="resolution_failed", dependent_surface_verdict="not_checked",
            idempotency_key=identity, before_physical_fingerprint=canonical_sha256([]),
            expected_after_semantic_fingerprint=canonical_sha256([]), created_identity_tokens=(),
        )

    # -- per-type planning ---------------------------------------------------

    def _plan_type(self, kind, target_index, target, snapshot: TypeSnapshot, dependent, invalid_json, view):
        physical_before = tuple(
            Definition(
                id=item.id, title=item.title, sample_attribute_type_id=item.sample_attribute_type_id,
                required=item.required, pos=index, is_title=item.is_title, description=item.description,
                unit_id=item.unit_id, sample_controlled_vocab_id=item.sample_controlled_vocab_id,
                linked_sample_type_id=item.linked_sample_type_id, updated_at=item.updated_at,
                physical_pos=item.physical_pos,
            )
            for index, item in enumerate(_dd35_normalize_snapshot(snapshot.definitions), start=1)
        )
        versions = tuple((item.id, item.updated_at) for item in physical_before)
        lock_order = (("sample_type", snapshot.sample_type_id),) + tuple(
            ("sample_attribute", item.id) for item in sorted(physical_before, key=lambda item: item.id)
        )
        base = dict(
            sample_type_id=snapshot.sample_type_id, sample_type_title=snapshot.sample_type_title,
            target_indexes=tuple(target["target_indexes"]),
            counts={"requested": len(target["operations"]), "resolved": 0, "created": 0, "patched": 0,
                    "deleted": 0, "unchanged": 0, "reordered": 0, "affected_samples": 0, "updated_samples": 0},
            before=physical_before, after=physical_before, automatic_changes=(), errors=(),
            schema_identity={"sample_type_id": snapshot.sample_type_id, "fingerprint": snapshot.fingerprint},
            full_definition_versions=versions, lock_order=lock_order,
            expected_conflict_oracle="conflict_if_type_or_any_full_definition_version_differs",
            concurrency_case="full_set", dependent_surface_verdict=dependent,
        )
        before_fp = canonical_sha256([
            (item.id, item.updated_at, *_semantics(item).values()) for item in physical_before
        ])
        if invalid_json:
            return self._finish(base, "plan_delta_required", physical_before, before_fp,
                                [PlanError("invalid_json_metadata", "invalid JSON metadata blocks this type", target_index)], ())
        if dependent != "compatible":
            return self._finish(base, "plan_delta_required", physical_before, before_fp,
                                [PlanError("dependent_policy_unresolved", dependent, target_index)], ())
        resolution_errors = list(target.get("resolution_errors", [])) + [
            error for operation in target["operations"] for error in operation.get("resolution_errors", [])
        ]
        if resolution_errors:
            errors = [_plan_error_from_dict(error, target_index=target_index) if isinstance(error, dict)
                      else PlanError(error, error, target_index) for error in resolution_errors]
            return self._finish(base, "failed", physical_before, before_fp, errors, ())
        conflict = cross_target_conflict(kind, target["operations"])
        if conflict:
            return self._finish(base, "failed", physical_before, before_fp, [conflict], ())

        try:
            if kind == "create":
                after, automatic, errors, counts, decisions = self._create(physical_before, target["operations"], target_index, view)
            elif kind == "patch":
                after, automatic, errors, counts, decisions = self._patch(physical_before, target["operations"], target_index, view)
            elif kind == "delete":
                after, automatic, errors, counts, decisions = self._delete(physical_before, target["operations"], target_index)
            else:
                raise ValueError("unsupported mutation kind")
        except PlanDeltaRequired as delta:
            return self._finish(base, "plan_delta_required", physical_before, before_fp, [delta.error], ())

        base["counts"].update(counts)
        # "planned" is only a provisional hint here: `_finish` makes the
        # definitive planned-vs-unchanged call once it can see whether a
        # DD-24 implicit reorder occurred (comparing physical vs. logical
        # pos), which a plain `after == physical_before` equality check
        # cannot detect on its own.
        status = "failed" if errors else "planned"
        if not errors:
            base["counts"]["affected_samples"] = snapshot_population_if_rewrite(decisions, view.population)
        tokens = self._created_identity_tokens(kind, target["operations"], after, errors)
        result_after = after if not errors else physical_before
        return self._finish(base, status, result_after, before_fp, errors, automatic, tokens, decisions)

    def _created_identity_tokens(self, kind, operations, after, errors):
        if kind != "create" or errors:
            return ()
        tokens = []
        seen_titles: set[str] = set()
        for operation in operations:
            title = operation["title"]
            if title in seen_titles:
                continue
            if any(item.id is None and item.title == title for item in after):
                tokens.append((f"created:{operation['_target_index']}:{operation['_attribute_index']}", title))
                seen_titles.add(title)
        return tuple(tokens)

    # -- create ---------------------------------------------------------

    def _create(self, before, operations, target_index, view):
        working = list(before)
        automatic: list[AutomaticChange] = []
        errors: list[PlanError] = []
        decisions: list[dict] = []
        resolved = created = unchanged = 0
        planned_by_collation_key: dict[str, Definition] = {}
        for item_index, operation in enumerate(operations):
            error_target = operation.get("_target_index", target_index)
            error_item = operation.get("_attribute_index", item_index)
            if operation.get("pos") is not None and operation["pos"] <= 0:
                errors.append(PlanError("position_not_positive", "position must be positive", error_target, error_item, "pos"))
                continue
            entry = view.collation_classes.get((error_target, error_item, "create"))
            if entry is None:
                errors.append(PlanError("missing_title_collation_oracle", "create title was not resolved under database collation",
                                        error_target, error_item, "title"))
                continue
            if len(entry.match_ids) > 1:
                errors.append(PlanError("attribute_ambiguous", "create title matched multiple physical definitions",
                                        error_target, error_item, "title"))
                continue
            existing = next((item for item in before if item.id == entry.match_ids[0]), None) if entry.match_ids else None
            if entry.match_ids and existing is None:
                errors.append(PlanError("stale_title_collation_oracle", "database title match is absent from the locked planning snapshot",
                                        error_target, error_item, "title"))
                continue
            planned = planned_by_collation_key.get(entry.class_key)
            if planned is not None:
                if existing is not None and existing.id != planned.id:
                    errors.append(PlanError("stale_title_collation_oracle", "one database title class resolved to different definitions",
                                            error_target, error_item, "title"))
                    continue
                existing = planned
            defaults = {"required": False, "pos": None, "is_title": False, "description": None,
                        "unit_id": None, "sample_controlled_vocab_id": None, "linked_sample_type_id": None}
            semantic = {"title": operation["title"], "sample_attribute_type_id": operation["sample_attribute_type_id"],
                        **{key: operation.get(key, value) for key, value in defaults.items()}}
            if existing:
                resolved += 1
                if all(getattr(existing, key) == semantic[key] for key in semantic if key != "pos"):
                    unchanged += 1
                    decisions.append(self._decision(target_index, "create", classify_metadata_rewrite(
                        before=existing, after=None, operation_kind="create")))
                    continue
                errors.append(PlanError("create_definition_conflict", "existing definition differs", error_target, error_item))
                continue
            created_item = Definition(
                id=None, title=semantic["title"], sample_attribute_type_id=semantic["sample_attribute_type_id"],
                required=semantic["required"], pos=semantic["pos"] or 0, is_title=False,
                description=semantic["description"], unit_id=semantic["unit_id"],
                sample_controlled_vocab_id=semantic["sample_controlled_vocab_id"],
                linked_sample_type_id=semantic["linked_sample_type_id"], updated_at=None,
            )
            if semantic["is_title"]:
                # Promote through the shared kernel with the not-yet-created
                # row appended so UID-sibling/clear-title invariants see the
                # complete planned set (Section 11.7). The row is re-found
                # by (title, id is None) since `replace()` inside
                # `apply_title_transition` returns a new object identity.
                try:
                    working_with_new = tuple(working) + (created_item,)
                    updated, title_changes = apply_title_transition(working_with_new, created_item, operation_kind="create")
                except _TitleTransitionRejected as rejected:
                    errors.append(replace(rejected.error, target_index=error_target, attribute_index=error_item))
                    continue
                created_item = next(item for item in updated if item.id is None and item.title == created_item.title)
                automatic.extend(title_changes)
                working = [item for item in updated if item is not created_item]
            position = created_item.pos or len(working) + 1
            working.insert(min(position - 1, len(working)), created_item)
            planned_by_collation_key[entry.class_key] = created_item
            resolved += 1
            created += 1
            decisions.append(self._decision(target_index, "create", classify_metadata_rewrite(before=None, after=None, operation_kind="create")))
        return (_renumber(working), tuple(automatic), tuple(errors),
                {"resolved": resolved, "created": created, "unchanged": unchanged}, tuple(decisions))

    # -- patch ------------------------------------------------------------

    def _patch(self, before, operations, target_index, resolved):
        working = list(before)
        automatic: list[AutomaticChange] = []
        errors: list[PlanError] = []
        decisions: list[dict] = []
        seen: dict[Any, str] = {}
        resolved_count = patched = unchanged = 0
        # Every id renamed in this same batch: T04's per-operation match_ids
        # reflect only current (pre-mutation) DB state, so an id that is
        # itself being renamed away in this batch can never be a real
        # collision -- that is exactly what makes a two-way swap legal.
        renaming_ids = {operation["attribute_id"] for operation in operations if "title" in operation.get("changes", {})}
        for item_index, operation in enumerate(operations):
            error_target = operation.get("_target_index", target_index)
            error_item = operation.get("_attribute_index", item_index)
            identifier = operation["attribute_id"]
            changes = operation["changes"]
            marker = canonical_sha256(changes)
            if identifier in seen:
                if seen[identifier] != marker:
                    errors.append(PlanError("conflicting_duplicate_operation", "duplicate operations conflict", error_target, error_item))
                continue
            seen[identifier] = marker
            index = next((i for i, item in enumerate(working) if item.id == identifier), None)
            if index is None:
                errors.append(PlanError("attribute_not_found", "attribute not found", error_target, error_item))
                continue
            current = working[index]
            uid_error = enforce_uid_protection(current, operation, operation_kind="patch")
            if uid_error:
                errors.append(uid_error)
                continue
            if changes.get("pos") is not None and changes["pos"] <= 0:
                errors.append(PlanError("position_not_positive", "position must be positive", error_target, error_item, "pos"))
                continue
            if "title" in changes:
                # Untouched-sibling collision: T04's oracle already excludes
                # this operation's own id (exclude_id); any id it still
                # reports that is *not* itself being renamed in this batch
                # holds -- and will keep holding -- the colliding title.
                entry = resolved.collation_classes.get((error_target, error_item, "patch-final"))
                if entry is None:
                    errors.append(PlanError("missing_title_collation_oracle", "patch title was not resolved under database collation",
                                            error_target, error_item, "title"))
                    continue
                residual = [match_id for match_id in entry.match_ids if match_id not in renaming_ids]
                if residual:
                    errors.append(PlanError("stale_title_collation_oracle", "final title collides with an untouched sibling",
                                            error_target, error_item, "title"))
                    continue
            new = replace(current, **{key: value for key, value in changes.items() if key != "is_title"})
            promoting = changes.get("is_title") is True
            if promoting:
                working[index] = new
                # `definitions`/`promoted_id`/`automatic_changes` are bound by
                # the frozen M-TITLE-01 mutation anchor (Section 11.10): this
                # is the sole patch entry into the shared title kernel.
                definitions, promoted_id = tuple(working), identifier
                try:
                    definitions, automatic_changes = apply_title_transition(definitions, promoted_id, operation_kind="patch")
                except _TitleTransitionRejected as rejected:
                    errors.append(replace(rejected.error, target_index=error_target, attribute_index=error_item))
                    working[index] = current
                    continue
                working = list(definitions)
                automatic.extend(automatic_changes)
                new = next(item for item in working if item.id == identifier)
            elif changes.get("is_title") is False:
                new = replace(new, is_title=False)
                working[index] = new
            else:
                working[index] = new
            before_semantics = _semantics(current)
            after_semantics = _semantics(new)
            if before_semantics == after_semantics and "pos" not in changes:
                resolved_count += 1
                unchanged += 1
                decisions.append(self._decision(target_index, "patch", classify_metadata_rewrite(
                    before=current, after=new, operation_kind="patch")))
                continue
            if "pos" in changes:
                index = next(i for i, item in enumerate(working) if item.id == identifier)
                working.pop(index)
                working.insert(min(changes["pos"] - 1, len(working)), new)
            resolved_count += 1
            patched += 1
            decisions.append(self._decision(target_index, "patch", classify_metadata_rewrite(before=current, after=new, operation_kind="patch")))
        # Attach provenance for the real-collation uniqueness check.
        # `final_definitions`/`resolved` are bound by the frozen
        # M-PATCH-COLLISION-01 mutation anchor (Section 11.10).
        final_definitions = []
        for item in working:
            match = next((op for op in operations if op.get("attribute_id") == item.id and "title" in op.get("changes", {})), None)
            if match is not None:
                final_definitions.append(_ProvenancedDefinition(item, match.get("_target_index"), match.get("_attribute_index")))
        collision = require_unique_final_collation_classes(final_definitions, resolved.collation_classes)
        if collision:
            errors.append(collision)
        return (_renumber(working), tuple(automatic), tuple(errors),
                {"resolved": resolved_count, "patched": patched, "unchanged": unchanged}, tuple(decisions))

    # -- delete -----------------------------------------------------------

    def _delete(self, before, operations, target_index):
        ids = {operation["attribute_id"] for operation in operations}
        errors: list[PlanError] = []
        decisions: list[dict] = []
        for operation in operations:
            identifier = operation["attribute_id"]
            current = next((item for item in before if item.id == identifier), None)
            if current is None:
                errors.append(PlanError("attribute_not_found", "attribute not found",
                                        operation.get("_target_index", target_index), operation.get("_attribute_index")))
                continue
            uid_error = enforce_uid_protection(current, operation, operation_kind="delete")
            if uid_error:
                errors.append(uid_error)
                continue
            decisions.append(self._decision(target_index, "delete", classify_metadata_rewrite(before=current, after=None, operation_kind="delete")))
        if errors:
            return before, (), tuple(errors), {"resolved": 0, "unchanged": 0}, tuple(decisions)
        remaining = tuple(item for item in before if item.id not in ids)
        resolved = len(before) - len(remaining)
        return _renumber(remaining), (), (), {"resolved": resolved, "deleted": resolved, "unchanged": 0}, tuple(decisions)

    # -- shared helpers -----------------------------------------------------

    def _decision(self, sample_type_id, operation_kind, decision: MetadataRewriteDecision) -> dict:
        return {"sample_type_id": sample_type_id, "operation_kind": operation_kind,
                "requires_metadata_rewrite": decision.requires_metadata_rewrite,
                "behavior_class": decision.behavior_class, "reason": decision.reason}

    def _finish(self, base, status, after, before_fp, errors, automatic=(), tokens=(), decisions=()):
        before_by_id = {item.id: item for item in base["before"] if item.id is not None}
        # `status` arrives from `_plan_type` as a provisional "planned"
        # guess for the success path (or the definitive "failed"/
        # "plan_delta_required" for a rejection). The success path is
        # finalized here: comparing raw *physical* pos (not the DD-35
        # logical pos already baked into `after`) against the assigned
        # logical pos is the only way to see a DD-24 legacy-order
        # normalization that produced no other semantic change (Section
        # 11.7: "Any nonzero reordered count makes the type planned,
        # executable... even if every caller-supplied operation was
        # idempotent").
        computing_success = status in {"planned", "unchanged"} and not errors
        position_effects = tuple(
            AutomaticChange("position_changed", item.id, item.title, "pos", before_by_id[item.id].physical_pos, item.pos)
            for item in after
            if item.id in before_by_id and before_by_id[item.id].physical_pos != item.pos
        ) if computing_success else ()
        if computing_success:
            status = "planned" if (tuple(after) != base["before"] or position_effects) else "unchanged"
        automatic = tuple(automatic) + position_effects
        token_by_title = {title: token for token, title in tokens}
        semantic_rows = []
        for item in after:
            identity = item.id if item.id is not None else token_by_title[item.title]
            semantic_rows.append((identity, *_semantics(item).values()))
        after_fp = canonical_sha256(semantic_rows)
        identity = canonical_sha256({"type": base["sample_type_id"], "before": before_fp, "after": after_fp,
                                     "versions": base["full_definition_versions"]})
        values = dict(base)
        executable = status == "planned" and not errors
        counts = dict(values["counts"])
        counts["reordered"] = len(position_effects) if status in {"planned", "unchanged"} and not errors else 0
        if status == "unchanged":
            counts["affected_samples"] = 0
        if status not in {"planned", "unchanged"} or errors:
            for field_name in ("created", "patched", "deleted", "unchanged", "updated_samples", "affected_samples"):
                counts[field_name] = 0
        failed_operations = 0 if status in {"planned", "unchanged"} and not errors else counts["requested"]
        if status not in {"planned", "unchanged"} or errors:
            tokens = ()
            decisions = ()
        values.update(status=status, executable=executable, failed_operations=failed_operations, counts=counts,
                      after=tuple(after), errors=tuple(errors), automatic_changes=tuple(automatic),
                      before_physical_fingerprint=before_fp, expected_after_semantic_fingerprint=after_fp,
                      created_identity_tokens=tuple(tokens), idempotency_key=identity,
                      rewrite_decisions=tuple(decisions))
        return TypeMutationPlan(**values)


def snapshot_population_if_rewrite(decisions: tuple[dict, ...], population: int) -> int:
    """DD-28/Section 11.7: contribute a type's current sample population
    exactly once iff any operation's decision requires a metadata rewrite,
    else zero. A mixed batch never contributes once per operation."""
    return population if any(item["requires_metadata_rewrite"] for item in decisions) else 0


@dataclass(frozen=True)
class TypeResolvedView:
    """One sample type's already-resolved planning inputs, bundled for the
    per-type planning pass: its ``TypeSnapshot``, the real-database title
    collation classes relevant to its operations, and its predicted sample
    population. ``materialize_attribute_records``/
    ``materialize_hypothetical_records`` delegate straight through to the
    top-level ``resolved_repository_view`` passed into ``plan_mutation``."""

    snapshot: TypeSnapshot
    collation_classes: dict[tuple[int, int, str], TitleCollationClass]
    population: int
    delegate: Any

    def materialize_attribute_records(self, definitions):
        """Display-enrich this module's lean planning rows before handing
        them to T04's ``materialize_attribute_records``.

        ``AttributeRepository.materialize_attribute_records`` is a pure
        field copy into ``AttributeRecord`` and reads seven attributes a
        planning ``Definition`` does not carry -- ``sample_type_title``,
        ``sample_attribute_type_title``, ``unit_title``, ``unit_symbol``,
        ``sample_controlled_vocab_title``, ``linked_sample_type_title``,
        and ``created_at`` -- because T05's only source of "before" rows,
        ``TypeSnapshot.definitions``, is the deliberately lean
        ``DefinitionSnapshot``. Two already-merged T04 accessors close that
        gap, and which one supplies which field matters:

        * ``display_fields_for`` joins on each row's *currently stored*
          relationship ids, so its titles go stale the moment this plan
          changes the matching ``*_id``. It is therefore used only for
          ``created_at``, which is immutable, plus the existence check.
        * ``materialize_hypothetical_records`` resolves *arbitrary*
          submitted relationship ids, so it is the only correct source for
          the six display titles of a row whose value type/unit/vocabulary/
          linked type this plan changed. It is used for all physical rows
          uniformly, so a patched row and an untouched row never take
          different code paths.

        ``sample_type_title`` comes free from the already-loaded snapshot -
        a definition cannot change its owning sample type."""
        definitions = tuple(definitions)
        if not definitions:
            return self.delegate.materialize_attribute_records(())
        stored = self.delegate.display_fields_for([item.id for item in definitions])
        missing = [item.id for item in definitions if item.id not in stored]
        if missing:
            raise PlanDeltaRequired(PlanError(
                "stale_planning_snapshot",
                f"planned definitions are absent from the live database: {sorted(missing)}",
            ))
        titles = self.delegate.materialize_hypothetical_records([
            {
                "token": f"display:{item.id}", "title": item.title, "sample_type_id": self.snapshot.sample_type_id,
                "sample_attribute_type_id": item.sample_attribute_type_id, "required": item.required,
                "pos": item.pos, "is_title": item.is_title, "description": item.description,
                "unit_id": item.unit_id, "sample_controlled_vocab_id": item.sample_controlled_vocab_id,
                "linked_sample_type_id": item.linked_sample_type_id,
            }
            for item in definitions
        ])
        enriched = tuple(
            RepositoryDefinition(
                id=item.id, title=item.title, sample_type_id=self.snapshot.sample_type_id,
                sample_type_title=self.snapshot.sample_type_title,
                sample_attribute_type_id=item.sample_attribute_type_id,
                sample_attribute_type_title=display["sample_attribute_type_title"],
                required=item.required, physical_pos=item.physical_pos, pos=item.pos,
                is_title=item.is_title, description=item.description,
                unit_id=item.unit_id, unit_title=display["unit_title"], unit_symbol=display["unit_symbol"],
                sample_controlled_vocab_id=item.sample_controlled_vocab_id,
                sample_controlled_vocab_title=display["sample_controlled_vocab_title"],
                linked_sample_type_id=item.linked_sample_type_id,
                linked_sample_type_title=display["linked_sample_type_title"],
                created_at=stored[item.id].created_at, updated_at=item.updated_at,
            )
            for item, display in zip(definitions, titles)
        )
        return self.delegate.materialize_attribute_records(enriched)

    def materialize_hypothetical_records(self, definitions):
        return self.delegate.materialize_hypothetical_records(definitions)


@dataclass(frozen=True)
class _ProvenancedDefinition:
    """Wraps a planned ``Definition`` with its originating (target_index,
    attribute_index) for the patch-title real-collation uniqueness check;
    never leaves ``_patch``."""

    _wrapped: Definition
    _target_index: int | None
    _attribute_index: int | None

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

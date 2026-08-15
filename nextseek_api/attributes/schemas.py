from __future__ import annotations

from datetime import datetime
import json
import math
from typing import Annotated, Any, Literal, TypeAlias
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, TypeAdapter, field_validator, model_validator
from pydantic.experimental.missing_sentinel import MISSING

Identifier: TypeAlias = Annotated[StrictInt | StrictStr, Field(description="Database ID as an integer or numeric string, otherwise an exact title.")]

# Named (not `Any`) so the generated JSON Schema resolves through a $ref literally
# named "json_value", matching the frozen machine schema contract's `type` string.
type json_value = str | int | float | bool | None | list[json_value] | dict[str, json_value]


def _identifier_key(value: Identifier) -> tuple[str, str]:
    if isinstance(value, int):
        return ("integer-id", str(value))
    if not value.strip():
        raise ValueError("identifier must not be blank")
    # Accepted nonblank strings are never trimmed, case-folded, coerced, or otherwise rewritten.
    # T01 deduplicates byte-identical submitted literals only. T04 resolves integer/numeric-string
    # equivalence and title identity under the actual database collation.
    # T04's resolver treats only ASCII base-10 digits as an ID spelling.  Python's
    # str.isdecimal() also accepts Unicode decimal characters, so both predicates
    # are required here to keep the database-free contract identical to T04.
    return (("numeric-string-id", value) if value.isascii() and value.isdecimal()
            else ("title-string", value))


def canonical_scalar_bytes(value: int | str) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")


def _hoist_nullable_constraints(node: Any) -> None:
    """Recursively republish a nullable field's nested-variant constraints
    onto its own top-level generated schema node.

    Pydantic represents ``X | None`` as ``anyOf: [X_schema, {"type": "null"}]``,
    leaving numeric/length constraints (``minimum``/``maximum``/``minItems``)
    on the inner ``X_schema`` branch. The frozen machine schema contract
    asserts these constraints directly on the property node, so this
    walk mirrors them upward without altering runtime validation.
    """
    if isinstance(node, dict):
        variants = node.get("anyOf") or node.get("oneOf") or []
        non_null = [variant for variant in variants if variant.get("type") != "null"]
        if len(non_null) == 1:
            variant = non_null[0]
            for key in ("minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "minItems", "maxItems"):
                if key in variant and key not in node:
                    node[key] = variant[key]
            if "minItems" in variant and "minLength" not in node:
                node["minLength"] = variant["minItems"]
        for value in node.values():
            _hoist_nullable_constraints(value)
    elif isinstance(node, list):
        for item in node:
            _hoist_nullable_constraints(item)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_default=True, strict=True)

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:
        schema = super().model_json_schema(*args, **kwargs)
        _hoist_nullable_constraints(schema)
        return schema


def _strict_json_value(value: Any) -> Any:
    """Reject non-JSON Python values and non-finite floats recursively."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON numbers must be finite")
        return value
    if isinstance(value, list):
        return [_strict_json_value(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _strict_json_value(item) for key, item in value.items()}
    raise ValueError("value must be strict JSON")


class SearchTarget(ContractModel):
    sample_type: Identifier = Field(description="Owning sample type selected by database ID, numeric-string ID, or exact title.")
    attributes: list[Identifier] | None = Field(default=None, min_length=1, description="Optional non-empty attribute identifiers; omission selects every attribute in this sample type.")

    @field_validator("sample_type")
    @classmethod
    def valid_sample_type(cls, value: Identifier) -> Identifier:
        _identifier_key(value)
        return value

    @field_validator("attributes")
    @classmethod
    def valid_attributes(cls, value: list[Identifier] | None) -> list[Identifier] | None:
        if value is not None:
            for item in value:
                _identifier_key(item)
        return value


class SearchRequest(ContractModel):
    targets: list[SearchTarget] = Field(min_length=1, description="Non-empty nested search targets preserving each sample type to attribute association.")


class AttributeCreate(ContractModel):
    title: str = Field(min_length=1, description="Exact title for the new attribute definition within its owning sample type.")
    sample_attribute_type: Identifier = Field(description="Value type selected by database ID, numeric-string ID, or exact title.")
    required: bool = Field(default=False, description="Whether samples of the owning type require a value for this attribute.")
    pos: int | None = Field(default=None, ge=1, description="Optional positive insertion position; omission appends in submitted order.")
    is_title: bool = Field(default=False, description="Whether this definition supplies the sample display title, subject to UID rules.")
    description: str | None = Field(default=None, description="Optional human-readable definition description; null stores no description.")
    unit: Identifier | None = Field(default=None, description="Optional unit selected by database ID, numeric-string ID, or exact non-null title.")
    sample_controlled_vocab: Identifier | None = Field(default=None, description="Optional controlled vocabulary selected by database ID, numeric-string ID, or exact title.")
    linked_sample_type: Identifier | None = Field(default=None, description="Optional linked sample type selected by database ID, numeric-string ID, or exact title.")

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value

    @field_validator("sample_attribute_type", "unit", "sample_controlled_vocab", "linked_sample_type")
    @classmethod
    def valid_reference(cls, value: Identifier | None) -> Identifier | None:
        if value is not None:
            _identifier_key(value)
        return value


class CreateTarget(ContractModel):
    sample_type: Identifier = Field(description="Owning sample type for every submitted definition in this nested target.")
    attributes: list[AttributeCreate] = Field(min_length=1, description="Non-empty definitions to create for exactly this owning sample type.")

    @model_validator(mode="after")
    def literal_duplicates(self) -> "CreateTarget":
        _identifier_key(self.sample_type)
        seen: dict[tuple[str, bytes], AttributeCreate] = {}
        result: list[AttributeCreate] = []
        for item in self.attributes:
            value = item.title
            key = (type(value).__name__, canonical_scalar_bytes(value))
            if key in seen and seen[key] != item:
                raise ValueError("conflicting duplicate create")
            if key not in seen:
                seen[key] = item
                result.append(item)
        self.attributes = result
        return self


class BatchCreateRequest(ContractModel):
    targets: list[CreateTarget] = Field(min_length=1, description="Non-empty nested sample-type targets for a single batch-create request.")
    dry_run: bool = Field(default=False, description="When true, validate and preview the mutation without writes, jobs, or dispatch.")


class AttributePatchChanges(ContractModel):
    title: str | MISSING = Field(default=MISSING, min_length=1, description="Replacement exact attribute title; omission preserves the current title.")
    sample_attribute_type: Identifier | MISSING = Field(default=MISSING, description="Replacement value type identifier; omission preserves the current value type.")
    required: bool | MISSING = Field(default=MISSING, description="Replacement requiredness flag; omission preserves current requiredness.")
    pos: Annotated[int, Field(ge=1)] | MISSING = Field(default=MISSING, description="Replacement positive position using insertion-and-shift semantics; omission preserves position.")
    is_title: bool | MISSING = Field(default=MISSING, description="Replacement sample-title flag; omission preserves the current title flag.")
    description: str | None | MISSING = Field(default=MISSING, description="Replacement description; explicit null clears it while omission preserves it.")
    unit: Identifier | None | MISSING = Field(default=MISSING, description="Replacement unit identifier; explicit null clears it while omission preserves it.")
    sample_controlled_vocab: Identifier | None | MISSING = Field(default=MISSING, description="Replacement controlled-vocabulary identifier; explicit null clears it while omission preserves it.")
    linked_sample_type: Identifier | None | MISSING = Field(default=MISSING, description="Replacement linked-sample-type identifier; explicit null clears it while omission preserves it.")

    @model_validator(mode="after")
    def at_least_one_change(self) -> "AttributePatchChanges":
        if not self.model_fields_set:
            raise ValueError("at least one patch change is required")
        if "title" in self.model_fields_set and not self.title.strip():
            raise ValueError("title must not be blank")
        for field_name in ("sample_attribute_type", "unit", "sample_controlled_vocab", "linked_sample_type"):
            if field_name in self.model_fields_set:
                value = getattr(self, field_name)
            else:
                continue
            if value is not None:
                _identifier_key(value)
        return self


class AttributePatch(ContractModel):
    attribute: Identifier = Field(description="Attribute selected by database ID, numeric-string ID, or exact title within its target.")
    changes: AttributePatchChanges = Field(description="Non-empty set of supplied replacement fields, preserving omission versus explicit null.")


class PatchTarget(ContractModel):
    sample_type: Identifier | None = Field(default=None, description="Owning sample type; may be omitted only when every attribute selector uses ID grammar.")
    attributes: list[AttributePatch] = Field(min_length=1, description="Non-empty attribute-and-change operations belonging to this nested target.")

    @model_validator(mode="after")
    def validate_target(self) -> "PatchTarget":
        if self.sample_type is not None:
            _identifier_key(self.sample_type)
        seen: dict[tuple[str, str], AttributePatch] = {}
        result: list[AttributePatch] = []
        for item in self.attributes:
            key = _identifier_key(item.attribute)
            if self.sample_type is None and key[0] not in {"integer-id", "numeric-string-id"}:
                raise ValueError("sample_type is required for title selectors")
            if key in seen and seen[key].changes != item.changes:
                raise ValueError("conflicting duplicate patch")
            if key not in seen:
                seen[key] = item
                result.append(item)
        self.attributes = result
        return self


class BatchPatchRequest(ContractModel):
    targets: list[PatchTarget] = Field(min_length=1, description="Non-empty nested sample-type targets for a single batch-patch request.")
    dry_run: bool = Field(default=False, description="When true, validate and preview the patch without writes, jobs, or dispatch.")


class DeleteTarget(ContractModel):
    sample_type: Identifier | None = Field(default=None, description="Owning sample type; may be omitted only when every attribute selector uses ID grammar.")
    attributes: list[Identifier] = Field(min_length=1, description="Non-empty attribute identifiers to delete from exactly this target sample type.")

    @model_validator(mode="after")
    def validate_target(self) -> "DeleteTarget":
        if self.sample_type is not None:
            _identifier_key(self.sample_type)
        result: list[Identifier] = []
        seen: set[tuple[str, str]] = set()
        for item in self.attributes:
            key = _identifier_key(item)
            if self.sample_type is None and key[0] not in {"integer-id", "numeric-string-id"}:
                raise ValueError("sample_type is required for title selectors")
            if key not in seen:
                seen.add(key)
                result.append(item)
        self.attributes = result
        return self


class BatchDeleteRequest(ContractModel):
    targets: list[DeleteTarget] = Field(min_length=1, description="Non-empty nested sample-type targets for a single batch-delete request.")
    dry_run: bool = Field(default=False, description="When true, validate and preview deletion without writes, jobs, or dispatch.")


class AttributeRecord(ContractModel):
    id: int = Field(description="Database primary key of the attribute definition row.")
    title: str = Field(description="Exact title stored on the attribute definition row.")
    sample_type_id: int = Field(description="Database primary key of the owning sample type.")
    sample_type_title: str = Field(description="Exact title of the owning sample type.")
    sample_attribute_type_id: int = Field(description="Database primary key of the attribute value type.")
    sample_attribute_type_title: str = Field(description="Exact title of the attribute value type.")
    required: bool = Field(description="Whether the owning sample type requires this attribute value.")
    pos: int = Field(ge=1, description="Deterministic contiguous logical API position within the owning sample type; valid positive physical positions sort first and legacy physical NULL rows sort last until first-touched mutation normalization, and reads never write.")
    is_title: bool = Field(description="Whether this definition supplies the sample display title.")
    description: str | None = Field(description="Optional human-readable description of the attribute definition.")
    unit_id: int | None = Field(description="Database primary key of the resolved unit, or null when absent.")
    unit_title: str | None = Field(description="Exact title of the resolved unit, or null when absent.")
    unit_symbol: str | None = Field(description="Display-only symbol of the resolved unit, or null when absent.")
    sample_controlled_vocab_id: int | None = Field(description="Database primary key of the resolved controlled vocabulary, or null when absent.")
    sample_controlled_vocab_title: str | None = Field(description="Exact title of the resolved controlled vocabulary, or null when absent.")
    linked_sample_type_id: int | None = Field(description="Database primary key of the resolved linked sample type, or null when absent.")
    linked_sample_type_title: str | None = Field(description="Exact title of the resolved linked sample type, or null when absent.")
    created_at: datetime = Field(description="Timestamp when the attribute definition was created by SEEK.")
    updated_at: datetime = Field(description="Timestamp of the latest persisted definition change used for concurrency checks.")

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def parse_iso_datetime_strings(cls, value: Any) -> Any:
        # Global strict=True otherwise rejects the ISO-8601 wire representation
        # ("...Z") produced by model_dump(mode="json")/orjson round trips and by
        # the OpenAPI examples; only well-formed ISO-8601 strings are accepted
        # here, so non-datetime, non-string inputs (e.g. epoch ints) still fail
        # strict core validation unchanged.
        if isinstance(value, str):
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value


class Pagination(ContractModel):
    page: int = Field(ge=1, description="Current one-based page number returned to the caller.")
    page_size: int = Field(ge=1, le=5000, description="Effective records per page, defaulting to 500 and capped at 5,000.")
    total_records: int = Field(ge=0, description="Total matching attribute records before page slicing.")
    total_pages: int = Field(ge=0, description="Total pages available at the effective page size.")


class MutationCounts(ContractModel):
    requested: int = Field(default=0, ge=0, description="Number of submitted attribute operations before resolution and deduplication.")
    resolved: int = Field(default=0, ge=0, description="Number of unique attribute operations resolved for execution.")
    created: int = Field(default=0, ge=0, description="Number of attribute definitions successfully created.")
    patched: int = Field(default=0, ge=0, description="Number of attribute definitions successfully patched.")
    deleted: int = Field(default=0, ge=0, description="Number of attribute definitions successfully deleted.")
    unchanged: int = Field(default=0, ge=0, description="Number of resolved operations requiring no persisted change.")
    reordered: int = Field(default=0, ge=0, description="Number of definitions automatically repositioned during normalization.")
    affected_samples: int = Field(default=0, ge=0, description="Number of existing sample metadata rows predicted to require rewriting.")
    updated_samples: int = Field(default=0, ge=0, description="Number of existing sample metadata rows actually rewritten.")

    @model_validator(mode="after")
    def enforce_count_invariants(self) -> "MutationCounts":
        if self.resolved > self.requested:
            raise ValueError("resolved must not exceed requested")
        if self.created + self.patched + self.deleted + self.unchanged > self.resolved:
            raise ValueError("operation outcomes must not exceed resolved")
        if self.updated_samples > self.affected_samples:
            raise ValueError("updated_samples must not exceed affected_samples")
        return self


def _enforce_aggregate_counts(counts: MutationCounts, outcomes: list["SampleTypeMutationOutcome"]) -> None:
    for field in MutationCounts.model_fields:
        if sum(getattr(outcome.counts, field) for outcome in outcomes) != getattr(counts, field):
            raise ValueError(f"per-type {field} does not sum to top-level {field}")


class MutationError(ContractModel):
    code: str = Field(min_length=1, description="Stable machine-readable error code for this rejected operation or target.")
    message: str = Field(min_length=1, description="Human-readable explanation of the rejected operation or target.")
    target_index: int | None = Field(default=None, ge=0, description="Zero-based submitted target index, or null for envelope-level errors.")
    attribute_index: int | None = Field(default=None, ge=0, description="Zero-based submitted attribute index, or null when not attribute-specific.")
    field: str | None = Field(default=None, description="Submitted field associated with the error, or null when not field-specific.")
    submitted_identifier: Identifier | None = Field(default=None, description="Original submitted mixed identifier associated with the error, or null when absent.")


NO_COMMIT_ERROR_CLASS = {
    "create_definition_conflict": "conflict",
    "cross_target_conflict": "conflict",
    "conflicting_duplicate_operation": "conflict",
    "ambiguous_recovery_state": "conflict",
    "sample_type_not_found": "semantic",
    "attribute_not_found": "semantic",
    "attribute_ambiguous": "semantic",
    "unit_not_found": "semantic",
    "unit_ambiguous": "semantic",
    "sample_attribute_type_not_found": "semantic",
    "sample_attribute_type_ambiguous": "semantic",
    "sample_controlled_vocab_not_found": "semantic",
    "sample_controlled_vocab_ambiguous": "semantic",
    "linked_sample_type_not_found": "semantic",
    "linked_sample_type_ambiguous": "semantic",
    "position_not_positive": "semantic",
    "uid_delete_forbidden": "semantic",
    "uid_rename_forbidden": "semantic",
    "uid_required_forbidden": "semantic",
    "uid_title_forbidden": "semantic",
    "uid_is_sole_title": "semantic",
    "dependent_policy_unresolved": "semantic",
    "dependent_values_require_policy": "semantic",
    "derived_title_unknown": "semantic",
    "invalid_json_metadata": "semantic",
    "missing_title_collation_oracle": "semantic",
    "stale_title_collation_oracle": "semantic",
    "plan_delta_required": "semantic",
}


def _completed_error_class(code: str) -> str:
    try:
        return NO_COMMIT_ERROR_CLASS[code]
    except KeyError as exc:
        raise ValueError(f"unclassified completed-response error code: {code}") from exc


class AutomaticChange(ContractModel):
    kind: str = Field(min_length=1, description="Stable machine-readable kind for an automatic normalization or title change.")
    attribute_id: int | None = Field(description="Resolved attribute database ID, or null for a not-yet-created definition.")
    attribute_title: str = Field(min_length=1, description="Exact attribute title associated with the automatic change.")
    field: str = Field(min_length=1, description="Definition field changed automatically by invariant enforcement.")
    previous_value: json_value | None = Field(description="JSON-compatible value before the automatic change was applied.")
    new_value: json_value | None = Field(description="JSON-compatible value after the automatic change was applied.")

    @field_validator("previous_value", "new_value")
    @classmethod
    def strict_json_values(cls, value: Any) -> Any:
        return _strict_json_value(value)


class SampleTypeMutationOutcome(ContractModel):
    sample_type_id: int = Field(description="Resolved database primary key of this outcome's owning sample type.")
    sample_type_title: str = Field(description="Resolved exact title of this outcome's owning sample type.")
    status: Literal["succeeded", "unchanged", "failed", "cancelled", "skipped"] = Field(description="Terminal classification for this atomic sample-type partition.")
    counts: MutationCounts = Field(description="Operation and sample-row counts scoped to this sample-type partition.")
    attributes: list[AttributeRecord] = Field(default_factory=list, description="Resolved changed or unchanged attribute records relevant to this outcome.")
    automatic_changes: list[AutomaticChange] = Field(default_factory=list, description="Automatic reorder or title effects applied or predicted for this partition.")
    errors: list[MutationError] = Field(default_factory=list, description="Structured errors that caused or explain this partition outcome.")

    @model_validator(mode="after")
    def terminal_state_is_coherent(self) -> "SampleTypeMutationOutcome":
        worked = self.counts.created + self.counts.patched + self.counts.deleted + self.counts.updated_samples
        if self.status in {"succeeded", "unchanged"} and self.errors:
            raise ValueError("successful/unchanged outcomes cannot contain errors")
        if self.status == "failed" and not self.errors:
            raise ValueError("failed outcomes require at least one error")
        if self.status in {"cancelled", "skipped"} and worked:
            raise ValueError("cancelled/skipped outcomes cannot claim committed work")
        return self


class AttributeListResponse(ContractModel):
    attributes: list[AttributeRecord] = Field(description="Stable ordered attribute records for the requested catalog or search page.")
    pagination: Pagination = Field(description="Page-number metadata for the complete matching record set.")


class MutationPreviewResponse(ContractModel):
    mode: Literal["dry_run"] = Field(description="Discriminator proving this response is a no-write mutation preview.")
    predicted_mode: Literal["synchronous", "asynchronous"] = Field(description="Deterministic execution mode predicted from affected sample rows and active threshold.")
    overall_status: Literal["succeeded", "partial", "failed"] = Field(description="Overall executable classification across all resolved sample-type partitions.")
    threshold: int = Field(ge=0, description="Active affected-sample-row threshold used to predict execution mode.")
    counts: MutationCounts = Field(description="Aggregate operation and sample-row counts across previewed partitions.")
    outcomes: list[SampleTypeMutationOutcome] = Field(description="Per-sample-type preview outcomes preserving atomic partition boundaries.")

    @model_validator(mode="after")
    def preview_counts_are_consistent(self) -> "MutationPreviewResponse":
        if not self.outcomes:
            raise ValueError("preview outcomes must be nonempty")
        if self.counts.updated_samples != 0 or any(item.counts.updated_samples != 0 for item in self.outcomes):
            raise ValueError("dry-run updated_samples must be zero")
        _enforce_aggregate_counts(self.counts, self.outcomes)
        statuses = {item.status for item in self.outcomes}
        executable = bool(statuses & {"succeeded", "unchanged"})
        blocked = bool(statuses & {"failed", "cancelled", "skipped"})
        expected = "partial" if executable and blocked else ("succeeded" if executable else "failed")
        if self.overall_status != expected:
            raise ValueError("preview overall_status contradicts outcomes")
        return self


def valid_completed_status_http(overall_status, http_status, outcomes) -> bool:
    if not outcomes:
        return False
    statuses = {item.status for item in outcomes}
    executable = bool(statuses & {"succeeded", "unchanged"})
    blocked = bool(statuses & {"failed", "cancelled", "skipped"})
    if executable and not blocked:
        expected = ("succeeded", 200)
    elif executable:
        expected = ("partial", 207)
    else:
        classes = {_completed_error_class(error.code)
                   for item in outcomes for error in item.errors}
        if "semantic" in classes:
            expected = ("failed", 422)
        elif not classes <= {"conflict"}:
            return False
        elif statuses <= {"cancelled", "skipped"} and "cancelled" in statuses:
            expected = ("cancelled", 409)
        elif statuses <= {"failed", "cancelled", "skipped"}:
            expected = ("failed", 409)
        else:
            return False
    return (overall_status, http_status) == expected


class MutationCompletedResponse(ContractModel):
    mode: Literal["synchronous", "asynchronous"] = Field(description="Discriminator identifying the completed request's actual execution mode.")
    overall_status: Literal["succeeded", "partial", "failed", "cancelled"] = Field(description="Overall terminal classification across every sample-type partition.")
    http_status: Literal[200, 207, 409, 422] = Field(description="HTTP classification shared by synchronous responses and terminal asynchronous results; structural 400 uses AttributeErrorResponse and is never completed.")
    counts: MutationCounts = Field(description="Aggregate operation and sample-row counts across completed partitions.")
    outcomes: list[SampleTypeMutationOutcome] = Field(description="Per-sample-type terminal outcomes preserving committed and failed partitions.")

    @model_validator(mode="after")
    def enforce_terminal_state(self) -> "MutationCompletedResponse":
        _enforce_aggregate_counts(self.counts, self.outcomes)
        if not valid_completed_status_http(self.overall_status, self.http_status, self.outcomes):
            raise ValueError("completed overall_status/http_status contradict outcomes")
        return self


class MutationAcceptedResponse(ContractModel):
    mode: Literal["asynchronous"] = Field(description="Discriminator proving the validated mutation was accepted for asynchronous execution.")
    job_id: UUID = Field(description="Durable NExtSEEK-owned mutation job identifier.")
    status_url: str = Field(min_length=1, description="Relative API URL for durable job status and terminal results.")
    counts: MutationCounts = Field(description="Resolved operation and affected-sample counts fixed at acceptance time.")

    @model_validator(mode="after")
    def accepted_has_no_updated_samples(self) -> "MutationAcceptedResponse":
        if self.counts.updated_samples != 0:
            raise ValueError("accepted asynchronous updated_samples must be zero")
        parsed = urlsplit(self.status_url)
        if parsed.scheme or parsed.netloc or not parsed.path.startswith("/nextseek_api/attributes/jobs/"):
            raise ValueError("status_url must be an approved relative API path")
        return self


class MutationJobStatusResponse(ContractModel):
    job_id: UUID = Field(description="Durable NExtSEEK-owned mutation job identifier.")
    state: Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"] = Field(description="Current durable job lifecycle state visible to SEEK administrators.")
    completed_sample_types: int = Field(ge=0, description="Number of sample-type partitions that reached a terminal state.")
    total_sample_types: int = Field(ge=0, description="Total resolved sample-type partitions owned by this job.")
    processed_samples: int = Field(ge=0, description="Number of existing sample rows processed by terminal partitions.")
    total_samples: int = Field(ge=0, description="Total existing sample rows expected across all job partitions.")
    result: MutationCompletedResponse | None = Field(description="Uniform completed result when terminal, otherwise null while work remains.")

    @model_validator(mode="after")
    def enforce_job_state(self) -> "MutationJobStatusResponse":
        if self.completed_sample_types > self.total_sample_types:
            raise ValueError("completed_sample_types exceeds total_sample_types")
        if self.processed_samples > self.total_samples:
            raise ValueError("processed_samples exceeds total_samples")
        NONTERMINAL_JOB_STATES = {"queued", "running"}
        if self.state in NONTERMINAL_JOB_STATES:
            if self.state in NONTERMINAL_JOB_STATES and self.result is not None:
                raise ValueError("nonterminal job cannot contain result")
            if self.state == "queued" and (self.completed_sample_types or self.processed_samples):
                raise ValueError("queued job progress must be zero")
        else:
            if self.result is None or self.result.overall_status != self.state:
                raise ValueError("terminal job result must agree with state")
            if (self.completed_sample_types, self.processed_samples) != (self.total_sample_types, self.total_samples):
                raise ValueError("terminal job progress must be exact")
        return self


class AttributeErrorResponse(ContractModel):
    errors: list[MutationError] = Field(min_length=1, description="Non-empty structured errors for a wholly nonexecutable or malformed request.")


SEARCH_REQUEST_ADAPTER = TypeAdapter(SearchRequest)
CREATE_REQUEST_ADAPTER = TypeAdapter(BatchCreateRequest)
PATCH_REQUEST_ADAPTER = TypeAdapter(BatchPatchRequest)
DELETE_REQUEST_ADAPTER = TypeAdapter(BatchDeleteRequest)
MUTATION_RESPONSE_UNION = MutationPreviewResponse | MutationCompletedResponse | MutationAcceptedResponse | MutationJobStatusResponse | AttributeErrorResponse
MutationResponseAdapter = TypeAdapter(MUTATION_RESPONSE_UNION)

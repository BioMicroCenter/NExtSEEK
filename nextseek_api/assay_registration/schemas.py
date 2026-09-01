"""Request and response contracts for batch assay-membership registration.

The request shape carries a safety property. Deletion is inexpressible for
three independent reasons: there is no delete verb; there is no Current-column
pair, which is the only input that reaches deleteOneRecord in the sheet path
(seek/dbtable_assay_assets.py:117-179); and there is no complete-list array
whose omissions could imply removal. ``extra="forbid"`` pins the first two.
The third is a property of the executor, asserted separately.

``direction`` is deliberately absent. The verified 25,765-row production write
used 0, ASSAY_ASSETS_DEFAULT uses 0, and nothing in NExtSEEK reads the column.
A membership says a sample belongs to an assay; it does not assert an input or
output role, and inventing one from a batch with no lineage context would
write a guess into production.
"""
from __future__ import annotations

from typing import List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Every code this API can emit, on a row or in an error envelope.
#: ``RowError`` ENFORCES membership, so a new code must be declared here before
#: it can be constructed. That enforcement is what actually keeps the resolver,
#: the planner, the views and the tests from drifting apart; the constant alone
#: would only look like it did.
#:
#: Note the two assay families. They are not near-duplicates: they split by
#: which form the caller used to name the assay.
ERROR_CODES = frozenset({
    # Sample resolution.
    "sample_uid_not_found",            # no `samples` row carries the uid
    "sample_uid_not_unique",           # 2+ rows carry it; the chunk-06 killer
    "sample_has_no_project",           # no projects_samples row
    # Assay named by numeric `assay_id`.
    "assay_not_found",                 # id reaches no project through studies
    "assay_project_mismatch",          # assay's projects disjoint from sample's
    # Assay named by internal title.
    "internal_assay_not_found",        # no internal assay by that title
    "assay_not_in_sample_project",     # candidates exist, none in the sample's project
    "assay_ambiguous_in_project",      # 2+ candidates in the sample's project
    # Write.
    "write_not_confirmed_by_readback",  # insert reported no error, row absent
    # Job execution. NOT the same as the code above, and the distinction is the
    # caller's action: `write_not_confirmed_by_readback` says an insert was
    # ATTEMPTED for that pair and the row was not there on read-back, so the
    # right response is to go and look at it. `job_execution_failed` says the
    # batch never got that far -- the connection died, planning raised -- so
    # nothing was attempted, the transaction rolled back, and the right response
    # is to retry. Emitting the readback code for a connection failure tells a
    # client switching on it to investigate a row that was never touched.
    "job_execution_failed",
    # Envelope-level, emitted by the ViewSet and service rather than per row.
    "request_validation_error",
    "job_not_found",
    "not_cancellable",
    "authentication_failed",
    "permission_denied",
})


class RegistrationRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_uid: str = Field(min_length=1, description="Sample UID, i.e. samples.uuid.")
    assay: Optional[str] = Field(
        default=None, min_length=1,
        description="Internal assay title, resolved inside the sample's own project.",
    )
    assay_id: Optional[int] = Field(
        default=None, gt=0,
        description="Numeric SEEK assay id, validated against the sample's project.",
    )

    @model_validator(mode="after")
    def exactly_one_assay_form(self) -> "RegistrationRow":
        if not self.sample_uid.strip():
            raise ValueError("sample_uid must not be blank")
        if (self.assay is None) == (self.assay_id is None):
            raise ValueError("provide exactly one of `assay` or `assay_id`")
        return self


class RegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    registrations: List[RegistrationRow] = Field(min_length=1)
    dry_run: bool = False


class RowError(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    submitted_identifier: Optional[str] = None

    @field_validator("code")
    @classmethod
    def code_is_declared(cls, value: str) -> str:
        """Make ERROR_CODES load-bearing rather than decorative.

        Without this, any module could invent a code and nothing — not the
        model, not the tests, not a type checker — would notice. Five modules
        construct RowError; this is the only place that can hold them to one
        vocabulary.
        """
        if value not in ERROR_CODES:
            raise ValueError(
                f"unknown error code {value!r}; declare it in ERROR_CODES first"
            )
        return value


class RowResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int
    sample_uid: str
    status: Literal["written", "already_present", "skipped", "failed"]
    sample_id: Optional[int] = None
    assay_id: Optional[int] = None
    assay_title: Optional[str] = None
    project_id: Optional[int] = None
    #: The primary key the DATABASE assigned, read back after the insert.
    #: Its presence is what licenses the "written" status.
    assay_assets_id: Optional[int] = None
    error: Optional[RowError] = None


class RegistrationCounts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submitted: int = 0
    written: int = 0
    already_present: int = 0
    skipped: int = 0
    failed: int = 0


class GraphOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["succeeded", "failed", "skipped"]
    edges_recomputed: int = 0
    error: Optional[str] = None


class RegistrationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["synchronous", "dry_run"]
    overall_status: Literal["succeeded", "partial", "failed"]
    counts: RegistrationCounts
    rows: List[RowResult]
    graph: GraphOutcome


class RegistrationAcceptedResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["asynchronous"]
    job_id: UUID
    status_url: str
    counts: RegistrationCounts


class JobStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: UUID
    state: Literal["accepted", "queued", "running", "succeeded", "partial",
                   "failed", "cancelled"]
    processed_rows: int
    total_rows: int
    result: Optional[RegistrationResponse] = None


class ErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    errors: List[RowError]

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

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Every code the endpoint can return on a row. Kept as a module constant so
#: the resolver, the planner and the tests cannot drift apart.
ERROR_CODES = frozenset({
    "sample_uid_not_found",
    "sample_uid_not_unique",
    "sample_has_no_project",
    "assay_not_found",
    "assay_project_mismatch",
    "internal_assay_not_found",
    "assay_not_in_sample_project",
    "assay_ambiguous_in_project",
    "write_not_confirmed_by_readback",
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

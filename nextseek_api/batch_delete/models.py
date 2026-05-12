from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def _validate_delete_eligibility_consistency(
    can_delete: bool,
    block_reason: Literal['forbidden', 'has_children'] | None,
    permission_basis: Literal['owner', 'admin'] | None,
) -> None:
    if can_delete and block_reason is not None:
        raise ValueError('block_reason must be None when can_delete=True')
    if block_reason == 'forbidden' and permission_basis is not None:
        raise ValueError('permission_basis must be None when block_reason=forbidden')


class ResolvedDeleteTarget(BaseModel):
    sample_identifier: str
    seek_id: int | None = None

    model_config = ConfigDict(extra='forbid', validate_default=True)


class LoadedSampleDeleteContext(BaseModel):
    sample_identifier: str
    seek_id: int = Field(..., gt=0)
    uuid: str
    policy_id: int = Field(..., gt=0)
    contributor_id: int = Field(..., gt=0)

    model_config = ConfigDict(extra='forbid', validate_default=True)


class DeleteEligibility(BaseModel):
    sample_identifier: str
    seek_id: int = Field(..., gt=0)
    uuid: str
    can_delete: bool
    permission_basis: Literal['owner', 'admin'] | None = None
    child_sample_uids: list[str] = Field(default_factory=list)
    block_reason: Literal['forbidden', 'has_children'] | None = None

    model_config = ConfigDict(extra='forbid', validate_default=True)

    def __init__(self, **data):
        super().__init__(**data)
        _validate_delete_eligibility_consistency(
            can_delete=self.can_delete,
            block_reason=self.block_reason,
            permission_basis=self.permission_basis,
        )


class DeleteOutcomeInternal(BaseModel):
    sample_identifier: str
    seek_id: int | None = None
    uuid: str | None = None
    status: Literal['deleted', 'skipped', 'failed']
    reason_code: Literal['not_found', 'forbidden', 'has_children', 'sql_failed', 'neo4j_failed', 'unknown'] | None = None
    message: str

    model_config = ConfigDict(extra='forbid', validate_default=True)


class BatchDeleteSummaryInternal(BaseModel):
    requested: int = Field(0, ge=0)
    resolved: int = Field(0, ge=0)
    eligible: int = Field(0, ge=0)
    deleted: int = Field(0, ge=0)
    skipped: int = Field(0, ge=0)
    failed: int = Field(0, ge=0)
    not_found: int = Field(0, ge=0)
    forbidden: int = Field(0, ge=0)
    has_children: int = Field(0, ge=0)
    sql_failed: int = Field(0, ge=0)
    neo4j_failed: int = Field(0, ge=0)
    elapsed_ms: int | None = Field(None, ge=0)

    model_config = ConfigDict(extra='forbid', validate_default=True)


class BatchDeleteSummaryReportRow(BaseModel):
    row_index: int
    sample_identifier: str
    seek_id: int | None = None
    uuid: str | None = None
    status: str
    reason_code: str | None = None
    message: str
    permission_basis: str | None = None
    child_sample_uids: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra='forbid', validate_default=True)


class BatchDeleteSummaryReport(BaseModel):
    rows: list[BatchDeleteSummaryReportRow] = Field(default_factory=list)
    summary: BatchDeleteSummaryInternal
    warnings: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra='forbid', validate_default=True)

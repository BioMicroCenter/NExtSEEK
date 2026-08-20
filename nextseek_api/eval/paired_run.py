"""Paired experimental batch schema (V4-7)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nextseek_api.eval.evidence_kinds import (
    EvidenceKind,
    ForgedEvidenceDiscriminator,
    MixedEvidenceBatch,
    OnlineEvidenceRejected,
    PAIRED_RUN_SCHEMA_VERSION,
)

__all__ = ["PairedArmRecord", "PairedExperimentalBatch", "build_paired_batch"]


class PairedArmRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    pair_id: str
    route: str
    query_id: str | None = None


class PairedExperimentalBatch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = PAIRED_RUN_SCHEMA_VERSION
    evidence_kind: EvidenceKind = EvidenceKind.paired_experimental
    paired_run_id: str = Field(min_length=1)
    pairs: list[dict[str, Any]]
    arm_records: dict[str, dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_paired_only(self) -> PairedExperimentalBatch:
        if self.evidence_kind is not EvidenceKind.paired_experimental:
            raise OnlineEvidenceRejected(
                f"expected paired_experimental, got {self.evidence_kind.value!r}"
            )
        if self.schema_version != PAIRED_RUN_SCHEMA_VERSION:
            raise ForgedEvidenceDiscriminator(
                f"expected schema {PAIRED_RUN_SCHEMA_VERSION!r}, got {self.schema_version!r}"
            )
        if not self.paired_run_id.strip():
            raise ForgedEvidenceDiscriminator("paired_run_id must be non-empty")
        return self


def build_paired_batch(
    *,
    paired_run_id: str,
    pairs: list[dict[str, Any]],
    arm_records: dict[str, dict[str, Any]],
    evidence_kind: EvidenceKind = EvidenceKind.paired_experimental,
    schema_version: str = PAIRED_RUN_SCHEMA_VERSION,
) -> PairedExperimentalBatch:
    if evidence_kind is not EvidenceKind.paired_experimental:
        raise MixedEvidenceBatch(f"refusing non-paired kind {evidence_kind.value!r}")
    return PairedExperimentalBatch(
        schema_version=schema_version,
        evidence_kind=evidence_kind,
        paired_run_id=paired_run_id,
        pairs=pairs,
        arm_records=arm_records,
    )

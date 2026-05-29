"""T3.1 — Pydantic v2 models for the artifact-validity in-image axis.

Locked DD-42: this module MUST NOT import `hibayes` at module level. All hibayes
imports stay inside run_hibayes.py.

Mirrors hibayes_runtime_reliability/models.py:122-142 for the posterior class.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ArtifactValidityRow(BaseModel):
    """One row of Stage A's hibayes_artifact_validity.csv (29 columns)."""

    model_config = ConfigDict(extra="allow")

    query_id: str
    task_family: str
    artifact_expected: bool
    artifact_success: bool
    artifact_validity_status: str


class PosteriorTaskFamilyReliability(BaseModel):
    """Per-stratum posterior summary per locked DD-41 lines 400-408.

    Identical shape to hibayes_runtime_reliability/models.py:122-142 so the
    nested wrapper schema is uniform across all three axes (DD-41).
    """

    model_config = ConfigDict(extra="forbid")

    task_family: str
    n_total: int = Field(ge=0)
    posterior_mean: float = Field(ge=0.0, le=1.0)
    posterior_median: float = Field(ge=0.0, le=1.0)
    hdi_low: float = Field(ge=0.0, le=1.0)
    hdi_high: float = Field(ge=0.0, le=1.0)
    p_success_lt_strong: float = Field(ge=0.0, le=1.0)
    p_success_lt_acceptable: float = Field(ge=0.0, le=1.0)
    band: str

    @model_validator(mode="after")
    def _check_hdi_ordering(self) -> "PosteriorTaskFamilyReliability":
        if self.hdi_low > self.hdi_high:
            raise ValueError(f"hdi_low ({self.hdi_low}) > hdi_high ({self.hdi_high})")
        return self

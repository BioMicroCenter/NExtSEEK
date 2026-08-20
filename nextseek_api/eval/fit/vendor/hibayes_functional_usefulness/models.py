"""T3.2 — Pydantic v2 models for the functional-usefulness in-image axis.

Locked DD-42: this module MUST NOT import `hibayes` at module level. All hibayes
imports stay inside run_hibayes.py.

Mirrors hibayes_runtime_reliability/models.py:122-142 for the posterior class
(per DD-41 — the `PosteriorTaskFamilyReliability` shape is uniform across axes).
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FunctionalUsefulnessRow(BaseModel):
    """One row of Stage C's hibayes_functional_usefulness.csv (12 columns).

    `functional_success` is the DD-08-derived column produced at Stage C time
    by `tools/e2e/functional_evaluator.py` (T2.1).
    """

    model_config = ConfigDict(extra="allow")

    query_id: str
    task_family: str
    functional_success: bool


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

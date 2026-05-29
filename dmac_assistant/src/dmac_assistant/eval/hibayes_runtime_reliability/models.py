"""Pydantic data models + reliability classification for the HiBayes runtime-reliability pipeline.

Public surface (imported by T04+):
    - RuntimeEvalRow                — one CSV row, validated.
    - TaskFamilyAggregate           — per-family aggregated counts (T04 emits).
    - PosteriorTaskFamilyReliability — per-family HiBayes posterior summary (T05 emits).
    - HiBayesRuntimeReport          — top-level run container (T06/T07 consume).
    - ReliabilityBand               — Reliable / Watch / Brittle / TooUncertain.
    - ReliabilityThresholds         — config-driven banding rules (DD-06).

Design references:
    - DD-01, DD-06, DD-11 (plan Section 2)
    - R-04 (single-row → TooUncertain), R-08 (subclass shape)
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tools.hibayes.exporter import FailureMode, HiBayesEvalRow


# ---------------------------------------------------------------------------
# Row layer (DD-01 / R-08)
# ---------------------------------------------------------------------------


class RuntimeEvalRow(HiBayesEvalRow):
    """Subclass of `HiBayesEvalRow` with three behavioral validators.

    Adds NO new fields; inherits `model_config = ConfigDict(extra="forbid")`
    from the parent. The three validators each touch fields already present
    on the parent — no field additions, no field removals.
    """

    @model_validator(mode="after")
    def _validate_failure_mode_whitelist(self) -> RuntimeEvalRow:
        # FailureMode is already constrained at the type level by Pydantic; this
        # validator is belt-and-braces against future enum extensions that DMAC
        # would not yet know how to interpret.
        if self.failure_mode not in (
            FailureMode.none,
            FailureMode.error,
            FailureMode.timeout,
            FailureMode.no_answer,
        ):
            raise ValueError(
                f"failure_mode {self.failure_mode!r} not in {{none,error,timeout,no_answer}}"
            )
        return self

    @model_validator(mode="after")
    def _validate_runtime_success_consistency(self) -> RuntimeEvalRow:
        expected = self.answer_provided and not self.is_error and not self.timed_out
        if self.runtime_success is not expected:
            raise ValueError(
                f"runtime_success {self.runtime_success!r} inconsistent with flags; "
                f"expected {expected!r}"
            )
        return self

    @model_validator(mode="after")
    def _validate_is_opus_binary(self) -> RuntimeEvalRow:
        if self.is_opus not in (0, 1):
            raise ValueError(f"is_opus {self.is_opus!r} not in {{0, 1}}")
        return self


# ---------------------------------------------------------------------------
# Aggregate + posterior layers
# ---------------------------------------------------------------------------


class TaskFamilyAggregate(BaseModel):
    """Per-family counts + averages, computed by T04 from list[RuntimeEvalRow].

    NOTE (DD-11): no `is_opus` field; the model-fitting layer (T05) is opus-blind in v1.
    """
    model_config = ConfigDict(extra="forbid")

    task_family: str
    n_total: int = Field(ge=0)
    n_success: int = Field(ge=0)
    n_failure: int = Field(ge=0)
    observed_success_rate: float = Field(ge=0.0, le=1.0)
    n_error: int = Field(ge=0)
    n_timeout: int = Field(ge=0)
    n_no_answer: int = Field(ge=0)
    n_artifact_rows: int = Field(ge=0)
    avg_latency_seconds: float = Field(ge=0.0)
    avg_cost_usd: float | None      # None when all rows in family had cost_usd=None (R-05)
    avg_tool_calls_total: float = Field(ge=0.0)

    @model_validator(mode="after")
    def _check_count_consistency(self) -> TaskFamilyAggregate:
        if self.n_total != self.n_success + self.n_failure:
            raise ValueError(
                f"n_total ({self.n_total}) != n_success+n_failure "
                f"({self.n_success}+{self.n_failure}={self.n_success + self.n_failure})"
            )
        if self.n_total > 0:
            expected_rate = self.n_success / self.n_total
            # Allow drift up to 1e-6. Source of drift is NOT the exporter (it does not
            # write TaskFamilyAggregate; only HiBayesEvalRow rows). It is T04's serializer:
            # if T04's aggregator rounds or truncates `observed_success_rate` to a fixed
            # number of decimal places (e.g. 4-6) when persisting aggregates to intermediate
            # JSON / YAML, the rounded value will not exactly equal `n_success/n_total`
            # on re-load. 1e-6 covers any realistic truncation while still catching real
            # off-by-one count errors (smallest realistic single-row error on a 1M-row
            # family is 1e-6; any plausible fixture has families of at most a few hundred
            # rows, so off-by-ones are >=3e-3, well above this tolerance).
            if abs(self.observed_success_rate - expected_rate) > 1e-6:
                raise ValueError(
                    f"observed_success_rate {self.observed_success_rate!r} "
                    f"!= n_success/n_total ({expected_rate!r})"
                )
        return self


class PosteriorTaskFamilyReliability(BaseModel):
    """Per-family posterior summary, emitted by T05.

    NOTE (DD-11): no `is_opus`; the posterior is over success rate alone.
    """
    model_config = ConfigDict(extra="forbid")

    task_family: str
    posterior_mean: float = Field(ge=0.0, le=1.0)
    posterior_median: float = Field(ge=0.0, le=1.0)
    hdi_low: float = Field(ge=0.0, le=1.0)
    hdi_high: float = Field(ge=0.0, le=1.0)
    p_success_lt_strong: float = Field(ge=0.0, le=1.0)      # P(success < strong_floor)
    p_success_lt_acceptable: float = Field(ge=0.0, le=1.0)  # P(success < acceptable_floor)
    n_total: int = Field(ge=0)
    band: ReliabilityBand  # forward reference resolved below

    @model_validator(mode="after")
    def _check_hdi_ordering(self) -> PosteriorTaskFamilyReliability:
        if self.hdi_low > self.hdi_high:
            raise ValueError(f"hdi_low ({self.hdi_low}) > hdi_high ({self.hdi_high})")
        return self


# ---------------------------------------------------------------------------
# Bands + thresholds (DD-06, R-04)
# ---------------------------------------------------------------------------


class ReliabilityBand(str, Enum):
    Reliable = "Reliable"
    Watch = "Watch"
    Brittle = "Brittle"
    TooUncertain = "TooUncertain"


class ReliabilityThresholds(BaseModel):
    """Banding rules — config-driven (DD-06). Defaults match the user's prompt.

    `min_n_for_classification` is the R-04 mitigation: families below this `n_total`
    are forced to TooUncertain regardless of posterior mean.
    """
    model_config = ConfigDict(extra="forbid")

    # Reliable
    reliable_mean_floor: float = Field(default=0.95, ge=0.0, le=1.0)
    reliable_p_lt_strong_max: float = Field(default=0.20, ge=0.0, le=1.0)
    # Watch
    watch_mean_floor: float = Field(default=0.80, ge=0.0, le=1.0)
    watch_p_lt_acceptable_max: float = Field(default=0.30, ge=0.0, le=1.0)
    # Brittle
    brittle_p_lt_acceptable_min: float = Field(default=0.50, ge=0.0, le=1.0)

    # P(success < x) thresholds
    strong_floor: float = Field(default=0.90, ge=0.0, le=1.0)
    acceptable_floor: float = Field(default=0.80, ge=0.0, le=1.0)

    # R-04 mitigation
    min_n_for_classification: int = Field(default=3, ge=1)

    def band_for(
        self,
        *,
        n_total: int,
        posterior_mean: float,
        p_lt_strong: float,
        p_lt_acceptable: float,
    ) -> ReliabilityBand:
        """Classify a posterior summary into a band.

        Order of checks:
          1. n_total < min_n_for_classification -> TooUncertain (R-04).
          2. Reliable: mean >= floor AND P(<strong) < cap.
          3. Brittle: P(<acceptable) >= floor.  (checked before Watch so a high mean
             with disastrous lower tail still classifies as Brittle.)
          4. Watch: mean >= watch floor AND P(<acceptable) < cap.
          5. Otherwise: TooUncertain.
        """
        if n_total < self.min_n_for_classification:
            return ReliabilityBand.TooUncertain
        if (
            posterior_mean >= self.reliable_mean_floor
            and p_lt_strong < self.reliable_p_lt_strong_max
        ):
            return ReliabilityBand.Reliable
        if p_lt_acceptable >= self.brittle_p_lt_acceptable_min:
            return ReliabilityBand.Brittle
        if (
            posterior_mean >= self.watch_mean_floor
            and p_lt_acceptable < self.watch_p_lt_acceptable_max
        ):
            return ReliabilityBand.Watch
        return ReliabilityBand.TooUncertain


# ---------------------------------------------------------------------------
# Top-level report container (T06 + T07 consumer)
# ---------------------------------------------------------------------------


class HiBayesRuntimeReport(BaseModel):
    """Single self-describing object that T06 renders and T07 emits as JSON.

    `diagnostics_summary` is `dict[str, Any]` rather than a typed model because
    HiBayes diagnostics surface a moving target of checker outputs (DD-10).
    Schema for that dict is the responsibility of T05 + T06.
    """
    model_config = ConfigDict(extra="forbid")

    aggregates: list[TaskFamilyAggregate]
    posteriors: list[PosteriorTaskFamilyReliability]
    thresholds: ReliabilityThresholds
    diagnostics_summary: dict[str, Any]
    generated_at: str  # ISO-8601 UTC timestamp; T07 sets it.


# Resolve the forward reference on PosteriorTaskFamilyReliability.band.
PosteriorTaskFamilyReliability.model_rebuild()

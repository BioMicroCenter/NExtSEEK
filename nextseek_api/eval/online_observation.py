"""Online observational row schema (V4-7)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nextseek_api.eval.evidence_kinds import (
    EvidenceKind,
    ForgedEvidenceDiscriminator,
    ONLINE_OBSERVATION_SCHEMA_VERSION,
    OnlineEvidenceRejected,
)
from nextseek_api.eval.router_models_proposal import RouteSource

__all__ = [
    "DEFAULT_SELECTION_CAVEAT",
    "PROPENSITY_UNAVAILABLE_REASON",
    "BANNED_COUNTERFACTUAL_PHRASES",
    "OnlineObservationalRow",
]

PROPENSITY_UNAVAILABLE_REASON = (
    "Assignment propensity is not recorded on TurnLedger; "
    "policy-selected routes lack logged randomized assignment scores."
)

DEFAULT_SELECTION_CAVEAT = (
    "Observational traffic only; route was policy-selected, not randomized. "
    "Cannot infer alternative-route superiority from this traffic alone."
)

BANNED_COUNTERFACTUAL_PHRASES = (
    "would be better",
    "counterfactual route",
    "other route is superior",
    "would have been better",
    "better than the alternative route",
)


class OnlineObservationalRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = ONLINE_OBSERVATION_SCHEMA_VERSION
    evidence_kind: EvidenceKind = EvidenceKind.online_observational
    observation_id: str = Field(min_length=1)
    session_id: str
    turn_number: int = Field(ge=0)
    route: str
    route_source: RouteSource
    task_family: str | None = None
    assignment_propensity: float | None = Field(default=None, ge=0.0, le=1.0)
    propensity_unavailable: bool = True
    propensity_unavailable_reason: str | None = Field(default=PROPENSITY_UNAVAILABLE_REASON, min_length=1)
    assignment_policy: str | None = None
    generation_id: int | None = None
    generation_hash: str | None = None
    selection_caveat: str = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_observational(self) -> OnlineObservationalRow:
        if self.evidence_kind is not EvidenceKind.online_observational:
            raise OnlineEvidenceRejected(
                f"expected online_observational, got {self.evidence_kind.value!r}"
            )
        if self.schema_version != ONLINE_OBSERVATION_SCHEMA_VERSION:
            raise ForgedEvidenceDiscriminator(
                f"expected schema {ONLINE_OBSERVATION_SCHEMA_VERSION!r}, got {self.schema_version!r}"
            )
        if self.route_source is RouteSource.forced:
            raise OnlineEvidenceRejected("forced route_source belongs on paired experimental rows")
        if self.assignment_propensity is not None and self.propensity_unavailable:
            raise OnlineEvidenceRejected(
                "assignment_propensity is set but propensity_unavailable=True"
            )
        if self.propensity_unavailable and not self.propensity_unavailable_reason:
            raise OnlineEvidenceRejected("propensity_unavailable requires propensity_unavailable_reason")
        lower = self.selection_caveat.lower()
        for phrase in BANNED_COUNTERFACTUAL_PHRASES:
            if phrase in lower:
                raise OnlineEvidenceRejected(
                    f"selection_caveat must not claim counterfactual superiority ({phrase!r})"
                )
        return self

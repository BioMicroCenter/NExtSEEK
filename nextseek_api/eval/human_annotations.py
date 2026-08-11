"""Human annotation schema and total vocabulary mapping (Plan 018 V4-3)."""
from __future__ import annotations

from enum import Enum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nextseek_api.eval.disposition import ExclusionReason, OutcomeBucket

__all__ = [
    "HUMAN_VOCABULARY",
    "HumanAnnotation",
    "HumanAnnotationError",
    "VOCABULARY_SEVERITY",
    "apply_human_annotation",
    "content_hash",
    "map_human_label",
]


class HumanAnnotationError(ValueError):
    pass


HUMAN_VOCABULARY = frozenset({"pass", "real", "masked", "policy", "drift", "notrun"})

VOCABULARY_SEVERITY = {
    "real": 0,
    "masked": 1,
    "policy": 2,
    "drift": 3,
    "notrun": 4,
    "pass": 5,
}


class HumanAnnotation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: str = Field(default="human_annotation/v1")
    run_id: str
    corpus_fingerprint: str
    case_id: str
    question_hash: str
    arm_id: str
    execution_id: str
    annotator_id: str
    annotator_authority: str
    vocabulary_version: str
    label: str
    annotated_at: str
    content_hash: str

    @model_validator(mode="after")
    def _label_in_vocab(self) -> HumanAnnotation:
        if self.label not in HUMAN_VOCABULARY:
            raise HumanAnnotationError(f"unknown label {self.label!r}")
        return self


def content_hash(payload: dict[str, Any]) -> str:
    canonical = repr(sorted(payload.items())).encode()
    return sha256(canonical).hexdigest()


def map_human_label(label: str) -> tuple[OutcomeBucket, ExclusionReason | None]:
    if label not in HUMAN_VOCABULARY:
        raise HumanAnnotationError(f"unknown label {label!r}")
    if label == "pass":
        return OutcomeBucket.desired, None
    if label == "real":
        return OutcomeBucket.not_desired, None
    if label in {"masked", "notrun"}:
        return OutcomeBucket.excluded, ExclusionReason.unevaluable
    if label == "policy":
        return OutcomeBucket.excluded, ExclusionReason.infrastructure
    if label == "drift":
        return OutcomeBucket.excluded, ExclusionReason.unknown
    raise HumanAnnotationError(f"unmapped label {label!r}")


def apply_human_annotation(
    judge_bucket: OutcomeBucket,
    annotation: HumanAnnotation | None,
    *,
    allow_override: bool = False,
) -> OutcomeBucket:
    """Sidecar labels never silently override judge output unless explicitly allowed."""
    if annotation is None:
        return judge_bucket
    mapped, _ = map_human_label(annotation.label)
    if mapped is judge_bucket:
        return judge_bucket
    if not allow_override:
        raise HumanAnnotationError("human annotation would override judge output")
    return mapped

"""Human annotation schema and total vocabulary mapping (Plan 018 V4-3)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nextseek_api.eval.disposition import ExclusionReason, OutcomeBucket

__all__ = [
    "HUMAN_VOCABULARY",
    "HumanAnnotation",
    "HumanAnnotationContext",
    "HumanAnnotationError",
    "HumanAnnotationRegistry",
    "HumanAnnotationRejectReason",
    "VOCABULARY_SEVERITY",
    "apply_human_annotation",
    "content_hash",
    "map_human_label",
    "verify_content_hash",
]


class HumanAnnotationError(ValueError):
    pass


class HumanAnnotationRejectReason(str, Enum):
    orphan = "orphan"
    duplicate = "duplicate"
    stale = "stale"
    unauthorized = "unauthorized"
    conflict = "conflict"


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


@dataclass(frozen=True)
class HumanAnnotationContext:
    """Known run scope for ingest validation."""

    run_id: str
    corpus_fingerprint: str
    vocabulary_version: str
    allowed_authorities: frozenset[str]
    known_cases: frozenset[str]
    known_arms: frozenset[str]
    known_executions: frozenset[str]
    stale_before: str | None = None


def content_hash(payload: dict[str, Any]) -> str:
    canonical = repr(sorted(payload.items())).encode()
    return sha256(canonical).hexdigest()


def verify_content_hash(annotation: HumanAnnotation) -> bool:
    payload = {
        "label": annotation.label,
        "case_id": annotation.case_id,
        "arm_id": annotation.arm_id,
        "execution_id": annotation.execution_id,
        "question_hash": annotation.question_hash,
    }
    return annotation.content_hash == content_hash(payload)


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


@dataclass
class HumanAnnotationRegistry:
    """Ingest-time validator rejecting orphan/dup/stale/unauthorized/conflicting annotations."""

    context: HumanAnnotationContext
    _by_ingest_key: dict[tuple[str, str, str, str, str], HumanAnnotation] = field(
        default_factory=dict
    )
    _by_arm_key: dict[tuple[str, str, str, str], HumanAnnotation] = field(default_factory=dict)

    def ingest(self, annotation: HumanAnnotation) -> None:
        self._validate(annotation)
        ingest_key = (
            annotation.run_id,
            annotation.case_id,
            annotation.arm_id,
            annotation.execution_id,
            annotation.annotator_id,
        )
        arm_key = (
            annotation.run_id,
            annotation.case_id,
            annotation.arm_id,
            annotation.execution_id,
        )
        if ingest_key in self._by_ingest_key:
            raise HumanAnnotationError(
                f"duplicate annotation for {ingest_key!r}",
            )
        existing = self._by_arm_key.get(arm_key)
        if existing is not None and existing.label != annotation.label:
            raise HumanAnnotationError(
                f"conflicting labels for {arm_key!r}: {existing.label!r} vs {annotation.label!r}",
            )
        self._by_ingest_key[ingest_key] = annotation
        self._by_arm_key[arm_key] = annotation

    def _validate(self, annotation: HumanAnnotation) -> None:
        ctx = self.context
        if annotation.annotator_authority not in ctx.allowed_authorities:
            raise HumanAnnotationError(
                f"unauthorized annotator authority {annotation.annotator_authority!r}",
            )
        if annotation.run_id != ctx.run_id:
            raise HumanAnnotationError(f"orphan run_id {annotation.run_id!r}")
        if annotation.corpus_fingerprint != ctx.corpus_fingerprint:
            raise HumanAnnotationError(
                f"orphan corpus_fingerprint {annotation.corpus_fingerprint!r}",
            )
        if annotation.case_id not in ctx.known_cases:
            raise HumanAnnotationError(f"orphan case_id {annotation.case_id!r}")
        if annotation.arm_id not in ctx.known_arms:
            raise HumanAnnotationError(f"orphan arm_id {annotation.arm_id!r}")
        if annotation.execution_id not in ctx.known_executions:
            raise HumanAnnotationError(f"orphan execution_id {annotation.execution_id!r}")
        if annotation.vocabulary_version != ctx.vocabulary_version:
            raise HumanAnnotationError(
                f"stale vocabulary_version {annotation.vocabulary_version!r}",
            )
        if ctx.stale_before is not None and annotation.annotated_at < ctx.stale_before:
            raise HumanAnnotationError(f"stale annotated_at {annotation.annotated_at!r}")
        if not verify_content_hash(annotation):
            raise HumanAnnotationError("stale or tampered content_hash")

    def get(self, run_id: str, case_id: str, arm_id: str, execution_id: str) -> HumanAnnotation | None:
        return self._by_arm_key.get((run_id, case_id, arm_id, execution_id))

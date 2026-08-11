"""Tests for human annotation schema."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from pydantic import ValidationError

from nextseek_api.eval.disposition import OutcomeBucket  # noqa: E402
from nextseek_api.eval.human_annotations import (  # noqa: E402
    HumanAnnotation,
    HumanAnnotationError,
    apply_human_annotation,
    content_hash,
    map_human_label,
)


def _ann(label: str) -> HumanAnnotation:
    payload = {"label": label, "case_id": "c1"}
    return HumanAnnotation(
        run_id="run-1",
        corpus_fingerprint="corp",
        case_id="c1",
        question_hash="qh",
        arm_id="arm-1",
        execution_id="ex-1",
        annotator_id="human-1",
        annotator_authority="maintainer",
        vocabulary_version="v1",
        label=label,
        annotated_at="2026-08-11T00:00:00Z",
        content_hash=content_hash(payload),
    )


def test_extra_forbid_unknown_label() -> None:
    with pytest.raises((HumanAnnotationError, ValidationError)):
        HumanAnnotation(
            run_id="r",
            corpus_fingerprint="c",
            case_id="c1",
            question_hash="qh",
            arm_id="a",
            execution_id="e",
            annotator_id="h",
            annotator_authority="m",
            vocabulary_version="v1",
            label="unknown_label",
            annotated_at="2026-08-11T00:00:00Z",
            content_hash="abc",
        )


def test_map_human_label_total_mapping() -> None:
    assert map_human_label("pass")[0] is OutcomeBucket.desired
    assert map_human_label("real")[0] is OutcomeBucket.not_desired
    assert map_human_label("masked")[0] is OutcomeBucket.excluded


def test_sidecar_cannot_override_judge_silently() -> None:
    with pytest.raises(HumanAnnotationError):
        apply_human_annotation(OutcomeBucket.desired, _ann("real"))

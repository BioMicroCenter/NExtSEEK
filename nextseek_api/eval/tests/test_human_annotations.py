"""Tests for human annotation schema and ingest validation."""
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
    HumanAnnotationContext,
    HumanAnnotationError,
    HumanAnnotationRegistry,
    apply_human_annotation,
    content_hash,
    map_human_label,
)


def _ctx() -> HumanAnnotationContext:
    return HumanAnnotationContext(
        run_id="run-1",
        corpus_fingerprint="corp",
        vocabulary_version="v1",
        allowed_authorities=frozenset({"maintainer", "reviewer"}),
        known_cases=frozenset({"c1"}),
        known_arms=frozenset({"arm-1"}),
        known_executions=frozenset({"ex-1"}),
        stale_before="2026-08-10T00:00:00Z",
    )


def _ann(label: str = "pass", *, authority: str = "maintainer", case_id: str = "c1") -> HumanAnnotation:
    payload = {
        "label": label,
        "case_id": case_id,
        "arm_id": "arm-1",
        "execution_id": "ex-1",
        "question_hash": "qh",
    }
    return HumanAnnotation(
        run_id="run-1",
        corpus_fingerprint="corp",
        case_id=case_id,
        question_hash="qh",
        arm_id="arm-1",
        execution_id="ex-1",
        annotator_id="human-1",
        annotator_authority=authority,
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


def test_reject_orphan_case() -> None:
    registry = HumanAnnotationRegistry(_ctx())
    with pytest.raises(HumanAnnotationError, match="orphan case_id"):
        registry.ingest(_ann(case_id="missing"))


def test_reject_duplicate() -> None:
    registry = HumanAnnotationRegistry(_ctx())
    ann = _ann()
    registry.ingest(ann)
    with pytest.raises(HumanAnnotationError, match="duplicate"):
        registry.ingest(ann)


def test_reject_stale_vocabulary() -> None:
    registry = HumanAnnotationRegistry(_ctx())
    ann = _ann()
    stale = ann.model_copy(update={"vocabulary_version": "v0"})
    with pytest.raises(HumanAnnotationError, match="stale vocabulary"):
        registry.ingest(stale)


def test_reject_stale_timestamp() -> None:
    registry = HumanAnnotationRegistry(_ctx())
    ann = _ann()
    stale = ann.model_copy(update={"annotated_at": "2026-08-01T00:00:00Z"})
    with pytest.raises(HumanAnnotationError, match="stale annotated_at"):
        registry.ingest(stale)


def test_reject_unauthorized_authority() -> None:
    registry = HumanAnnotationRegistry(_ctx())
    with pytest.raises(HumanAnnotationError, match="unauthorized"):
        registry.ingest(_ann(authority="guest"))


def test_reject_conflicting_labels() -> None:
    registry = HumanAnnotationRegistry(_ctx())
    registry.ingest(_ann("pass", authority="maintainer"))
    conflict = _ann("real", authority="reviewer")
    conflict = conflict.model_copy(update={"annotator_id": "human-2"})
    with pytest.raises(HumanAnnotationError, match="conflicting"):
        registry.ingest(conflict)


def test_reject_orphan_arm() -> None:
    registry = HumanAnnotationRegistry(_ctx())
    ann = _ann()
    orphan = ann.model_copy(update={"arm_id": "unknown-arm"})
    with pytest.raises(HumanAnnotationError, match="orphan arm_id"):
        registry.ingest(orphan)


def test_map_all_vocab_labels() -> None:
    assert map_human_label("policy")[1] is not None
    assert map_human_label("drift")[1] is not None
    assert map_human_label("notrun")[0] is OutcomeBucket.excluded


def test_allow_override_when_explicit() -> None:
    result = apply_human_annotation(
        OutcomeBucket.desired,
        _ann("real"),
        allow_override=True,
    )
    assert result is OutcomeBucket.not_desired


def test_registry_get_after_ingest() -> None:
    registry = HumanAnnotationRegistry(_ctx())
    ann = _ann()
    registry.ingest(ann)
    assert registry.get("run-1", "c1", "arm-1", "ex-1") is ann


def test_reject_orphan_run_and_execution() -> None:
    registry = HumanAnnotationRegistry(_ctx())
    with pytest.raises(HumanAnnotationError, match="orphan run_id"):
        registry.ingest(_ann().model_copy(update={"run_id": "other"}))
    with pytest.raises(HumanAnnotationError, match="orphan execution_id"):
        registry.ingest(_ann().model_copy(update={"execution_id": "missing-ex"}))

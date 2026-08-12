"""V4-8 RunManifest schema tests (Lane A)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from nextseek_api.eval.run_manifest import RunManifest, manifest_body_hash, validate_manifest_dict
from nextseek_api.eval.tests.v4_8_fixtures import sample_manifest_dict, sample_run_manifest


def test_manifest_requires_all_fields():
    with pytest.raises(ValidationError):
        RunManifest.model_validate({"corpus_id": "only-one"})


def test_manifest_rejects_extra_field():
    body = sample_manifest_dict(extra_field="nope")
    with pytest.raises(ValidationError):
        RunManifest.model_validate(body)


def test_manifest_rejects_retained_arm_count():
    body = sample_manifest_dict(retained_arm_count=3)
    with pytest.raises(ValueError, match="retained_arm_count"):
        validate_manifest_dict(body)


def test_judge_calls_per_eligible_arm_must_be_three():
    with pytest.raises(ValidationError, match="judge_calls_per_eligible_arm"):
        sample_run_manifest(judge_calls_per_eligible_arm=2)


def test_question_hash_length_must_match_ids():
    with pytest.raises(ValidationError, match="question_hashes"):
        sample_run_manifest(question_hashes=["only-one"])


def test_manifest_hash_is_stable_and_order_independent():
    a = sample_manifest_dict()
    b = dict(a)
    h1 = manifest_body_hash(a)
    h2 = manifest_body_hash(b)
    assert h1 == h2
    assert len(h1) == 64


def test_changed_rate_table_changes_hash():
    a = manifest_body_hash(sample_manifest_dict())
    b = manifest_body_hash(sample_manifest_dict(rate_table_hash="different"))
    assert a != b

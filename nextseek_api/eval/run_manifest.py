"""Immutable approved run manifest schema (V4-8)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["RunManifest", "manifest_body_hash", "validate_manifest_dict"]


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    corpus_id: str
    corpus_hash: str
    question_ids: list[str]
    question_hashes: list[str]
    taxonomy_version: str
    requested_pairs: int
    requested_turns: int
    requested_arms: int
    judge_calls_per_eligible_arm: int = 3
    max_retry_calls: int
    client_ids: list[str]
    model_ids: list[str]
    model_versions: list[str]
    retry_policy: str
    rate_source: str
    rate_timestamp: str
    rate_table_hash: str
    per_call_estimate_usd: Decimal
    worst_case_total_usd: Decimal
    hard_cap_usd: Decimal
    max_calls: int
    source_sha: str
    dirty_diff_sha256: str
    image_digest: str
    schema_hashes: dict[str, str]
    output_location: str
    approval_expires_at: datetime

    @field_validator("judge_calls_per_eligible_arm")
    @classmethod
    def _judge_calls_fixed(cls, value: int) -> int:
        if value != 3:
            raise ValueError("judge_calls_per_eligible_arm must be exactly 3")
        return value

    @field_validator("question_hashes")
    @classmethod
    def _question_hash_len(cls, value: list[str], info) -> list[str]:
        ids = info.data.get("question_ids") or []
        if len(value) != len(ids):
            raise ValueError("question_hashes length must match question_ids")
        return value


def manifest_body_hash(manifest: dict[str, Any] | RunManifest) -> str:
    if isinstance(manifest, RunManifest):
        payload = manifest.model_dump(mode="json")
    else:
        payload = validate_manifest_dict(manifest)
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def validate_manifest_dict(manifest: dict[str, Any]) -> dict[str, Any]:
    if "retained_arm_count" in manifest:
        raise ValueError("retained_arm_count is post-run reconciliation only")
    model = RunManifest.model_validate(manifest)
    return model.model_dump(mode="json")

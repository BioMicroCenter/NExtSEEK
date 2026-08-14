"""Shared V4-8 run manifest fixtures for Lane A/C tests."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from nextseek_api.eval.run_manifest import RunManifest


def sample_manifest_dict(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    base: dict[str, Any] = {
        "corpus_id": "corpus-v1",
        "corpus_hash": "abc123corpus",
        "question_ids": ["q1", "q2"],
        "question_hashes": ["hash-q1", "hash-q2"],
        "taxonomy_version": "tax-v1",
        "requested_pairs": 2,
        "requested_turns": 4,
        "requested_arms": 2,
        "judge_calls_per_eligible_arm": 3,
        "max_retry_calls": 1,
        "client_ids": ["client-a"],
        "model_ids": ["model-x"],
        "model_versions": ["v1"],
        "retry_policy": "none",
        "rate_source": "internal-table",
        "rate_timestamp": "2026-08-12T00:00:00Z",
        "rate_table_hash": "rate-table-deadbeef",
        "per_call_estimate_usd": "0.05",
        "worst_case_total_usd": "1.00",
        "hard_cap_usd": "1.00",
        "max_calls": 10,
        "source_sha": "deadbeef" * 8,
        "dirty_diff_sha256": "0" * 64,
        "image_digest": "sha256:" + "a" * 64,
        "schema_hashes": {"run_manifest": "sm-v1"},
        "output_location": "/data/scratch/v48",
        "approval_expires_at": (now + timedelta(hours=1)).isoformat(),
    }
    base.update(overrides)
    return base


def sample_run_manifest(**overrides: Any) -> RunManifest:
    return RunManifest.model_validate(sample_manifest_dict(**overrides))

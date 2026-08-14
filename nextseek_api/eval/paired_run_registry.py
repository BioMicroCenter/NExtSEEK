"""Approved paired-run registry helpers (V4-7)."""
from __future__ import annotations

import hashlib
import json
from typing import Any

__all__ = [
    "register_paired_run",
    "is_paired_run_approved",
    "content_hash_for_batch",
]


def content_hash_for_batch(pairs: list[dict[str, Any]], arm_records: dict[str, Any]) -> str:
    payload = {"pairs": pairs, "arm_records": arm_records}
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def is_paired_run_approved(paired_run_id: str) -> bool:
    try:
        from django.apps import apps

        if not apps.ready:
            return False
        from nextseek_api.assistant.models_db import PairedRunRegistry

        return PairedRunRegistry.objects.filter(paired_run_id=paired_run_id).exists()
    except Exception:
        return False


def register_paired_run(
    *,
    paired_run_id: str,
    schema_version: str,
    content_hash: str,
) -> None:
    from nextseek_api.assistant.models_db import PairedRunRegistry

    if PairedRunRegistry.objects.filter(paired_run_id=paired_run_id).exists():
        existing = PairedRunRegistry.objects.get(paired_run_id=paired_run_id)
        if existing.content_hash != content_hash:
            raise ValueError("immutable paired run registry — content_hash mismatch")
        return
    PairedRunRegistry.objects.create(
        paired_run_id=paired_run_id,
        schema_version=schema_version,
        content_hash=content_hash,
    )

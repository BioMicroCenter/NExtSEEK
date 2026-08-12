"""Post-run reconciliation artifact for V4-8."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from nextseek_api.eval.spend_conservation import ConservationSnapshot, compute_conservation

__all__ = ["RunReconciliation", "build_reconciliation", "write_reconciliation_artifact"]


@dataclass
class RunReconciliation:
    manifest_hash: str
    estimates: dict[str, str]
    conservation: dict[str, Any]
    attempts: list[dict[str, Any]]
    cache_hits: int
    exclusions: list[str]
    outputs: dict[str, Any]
    retained_arm_count: int | None = None


def build_reconciliation(
    record,
    *,
    attempts: list[dict[str, Any]],
    cache_hits: int = 0,
    exclusions: list[str] | None = None,
    outputs: dict[str, Any] | None = None,
    retained_arm_count: int | None = None,
) -> RunReconciliation:
    snap = compute_conservation(record)
    snap.assert_balanced()
    manifest = record.manifest
    return RunReconciliation(
        manifest_hash=record.manifest_hash,
        estimates={
            "per_call_estimate_usd": str(manifest.get("per_call_estimate_usd", "0")),
            "worst_case_total_usd": str(manifest.get("worst_case_total_usd", "0")),
            "hard_cap_usd": str(record.max_spend_usd),
        },
        conservation=_conservation_dict(snap),
        attempts=attempts,
        cache_hits=cache_hits,
        exclusions=list(exclusions or []),
        outputs=dict(outputs or {}),
        retained_arm_count=retained_arm_count,
    )


def write_reconciliation_artifact(path: Path, recon: RunReconciliation) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(recon), indent=2, sort_keys=True) + "\n")


def _conservation_dict(snap: ConservationSnapshot) -> dict[str, Any]:
    return {
        "approved_max_usd": str(snap.approved_max_usd),
        "available_usd": str(snap.available_usd),
        "reserved_usd": str(snap.reserved_usd),
        "reconciled_actual_usd": str(snap.reconciled_actual_usd),
        "released_expired_usd": str(snap.released_expired_usd),
        "pending_calls": snap.pending_calls,
        "succeeded_calls": snap.succeeded_calls,
        "failed_calls": snap.failed_calls,
        "reconciled_calls": snap.reconciled_calls,
        "released_calls": snap.released_calls,
    }

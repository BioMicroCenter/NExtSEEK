"""Hard experimental/observational boundary for paired fit and publish (V4-7)."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from nextseek_api.eval.evidence_kinds import (
    EvidenceKind,
    ForgedEvidenceDiscriminator,
    MixedEvidenceBatch,
    OnlineEvidenceRejected,
    UnapprovedPairedRun,
)
from nextseek_api.eval.online_observation import OnlineObservationalRow
from nextseek_api.eval.paired_run import PairedExperimentalBatch

__all__ = [
    "assert_paired_experimental_only",
    "require_approved_paired_run",
    "compute_paired_input_hash",
    "assert_zero_online_ids_in_hash",
    "refuse_raw_dict_fit_input",
    "extract_paired_run_id_from_provenance",
]


def assert_paired_experimental_only(obj: Any) -> None:
    if isinstance(obj, OnlineObservationalRow):
        raise OnlineEvidenceRejected("online observational row cannot enter paired fitter")
    if isinstance(obj, PairedExperimentalBatch):
        if obj.evidence_kind is not EvidenceKind.paired_experimental:
            raise MixedEvidenceBatch(f"unexpected evidence kind {obj.evidence_kind.value!r}")
        return
    if isinstance(obj, dict):
        kind = obj.get("evidence_kind")
        if kind == EvidenceKind.online_observational.value or kind == "online_observational":
            raise OnlineEvidenceRejected("raw dict carries online_observational kind")
        if kind is not None and kind != EvidenceKind.paired_experimental.value and kind != "paired_experimental":
            raise ForgedEvidenceDiscriminator(f"unknown evidence_kind {kind!r}")
        schema = obj.get("schema_version", "")
        if schema.startswith("online_observation"):
            raise OnlineEvidenceRejected("online observation schema_version on fit input")
        return
    if hasattr(obj, "evidence_kind"):
        kind = getattr(obj, "evidence_kind")
        if getattr(kind, "value", kind) == EvidenceKind.online_observational.value:
            raise OnlineEvidenceRejected("object declares online_observational kind")


def require_approved_paired_run(paired_run_id: str) -> None:
    from nextseek_api.eval.paired_run_registry import is_paired_run_approved

    if not is_paired_run_approved(paired_run_id):
        raise UnapprovedPairedRun(f"paired_run_id {paired_run_id!r} is not approved")


def compute_paired_input_hash(batch: PairedExperimentalBatch) -> str:
    assert_paired_experimental_only(batch)
    payload = {
        "paired_run_id": batch.paired_run_id,
        "schema_version": batch.schema_version,
        "evidence_kind": batch.evidence_kind.value,
        "pair_ids": sorted({p.get("pair_id", "") for p in batch.pairs}),
        "observation_ids": [],
    }
    canonical = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def assert_zero_online_ids_in_hash(
    batch: PairedExperimentalBatch,
    online_observation_ids: set[str],
) -> None:
    pair_ids = {str(p.get("pair_id", "")) for p in batch.pairs}
    overlap = pair_ids & online_observation_ids
    if overlap:
        raise OnlineEvidenceRejected(
            f"paired input hash contaminated by online observation ids: {sorted(overlap)}"
        )
    digest = compute_paired_input_hash(batch)
    for obs_id in online_observation_ids:
        if obs_id and obs_id in digest:
            raise OnlineEvidenceRejected("online observation id appears in paired input hash")


def refuse_raw_dict_fit_input(obj: Any, *, context: str) -> None:
    """Refuse a top-level dict masquerading as structured fit input."""
    if isinstance(obj, dict) and context in ("fit_admission", "v14", "publish"):
        if "evidence_kind" not in obj and "paired_run_id" not in obj:
            raise OnlineEvidenceRejected(f"raw dict fit input refused at {context}")


def extract_paired_run_id_from_provenance(source_provenance: dict[str, Any]) -> str | None:
    run_id = source_provenance.get("paired_run_id")
    if run_id:
        return str(run_id)
    return None


def validate_publish_provenance(source_provenance: dict[str, Any]) -> None:
    run_id = extract_paired_run_id_from_provenance(source_provenance)
    if not run_id:
        raise OnlineEvidenceRejected("publish requires paired_run_id in source_provenance")
    kind = source_provenance.get("evidence_kind")
    if kind not in (EvidenceKind.paired_experimental.value, "paired_experimental", None):
        raise OnlineEvidenceRejected(f"publish provenance evidence_kind {kind!r} refused")
    if source_provenance.get("route_source") not in (None, "forced"):
        raise OnlineEvidenceRejected("policy-selected online provenance cannot publish comparative generation")
    require_approved_paired_run(run_id)

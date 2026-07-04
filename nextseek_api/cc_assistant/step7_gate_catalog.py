"""Step 7 live-gate exercise catalog and instance binding (Gate 3C).

Loads committed JSON under acceptance_evidence/step7/ and builds per-op
invocation kwargs for the RUN_REALSTACK matrix. Replaces integration-invented
``_op_kwargs()`` and greenfield ``create_seeded_fixture()`` for live runs.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

UPSTREAM_SHA = "a429f1372a075e5db586a1b6efc8c3b1663e211a"

_CATALOG_DIR = Path(__file__).resolve().parent / "acceptance_evidence" / "step7"
CATALOG_PATH = _CATALOG_DIR / "STEP7-UPSTREAM-EXERCISE-CATALOG.json"
INSTANCE_BINDING_PATH = _CATALOG_DIR / "instance_binding.json"
PROVENANCE_PATH = _CATALOG_DIR / "catalog_provenance.json"
COVERAGE_PATH = _CATALOG_DIR / "catalog_coverage_report.json"
COST_SOURCE_MAP_PATH = _CATALOG_DIR / "cost_source_map.json"

BIN_OPS = (
    "nextseek-entity-extract",
    "nextseek-parse",
    "nextseek-graph",
    "nextseek-plan",
    "nextseek-query",
    "nextseek-api-read",
    "nextseek-api-write",
    "nextseek-report",
    "nextseek-generate-submission",
)


@dataclass
class InstanceBinding:
    binding_id: str
    project_id: int
    project_title: str
    sample_count: int
    sample_type_count: int
    reference_uids: list[str] = field(default_factory=list)
    cc_project_dirname: str = ""
    forbidden_actions: list[str] = field(default_factory=list)

    @classmethod
    def from_json(cls, obj: dict[str, Any]) -> "InstanceBinding":
        return cls(
            binding_id=str(obj["binding_id"]),
            project_id=int(obj["project_id"]),
            project_title=str(obj["project_title"]),
            sample_count=int(obj.get("sample_count", 0)),
            sample_type_count=int(obj.get("sample_type_count", 0)),
            reference_uids=list(obj.get("reference_uids") or []),
            cc_project_dirname=str(obj.get("cc_project_dirname") or ""),
            forbidden_actions=list(obj.get("forbidden_actions") or []),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "binding_id": self.binding_id,
            "project_id": self.project_id,
            "project_title": self.project_title,
            "sample_count": self.sample_count,
            "sample_type_count": self.sample_type_count,
            "reference_uids": self.reference_uids,
            "cc_project_dirname": self.cc_project_dirname,
            "forbidden_actions": self.forbidden_actions,
        }


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_exercise_catalog(path: Path | None = None) -> list[dict[str, Any]]:
    p = path or CATALOG_PATH
    data = load_json(p)
    exercises = data.get("exercises")
    if not isinstance(exercises, list):
        raise ValueError(f"{p}: missing exercises list")
    return exercises


def load_instance_binding(path: Path | None = None) -> InstanceBinding:
    p = path or INSTANCE_BINDING_PATH
    return InstanceBinding.from_json(load_json(p))


def catalog_covers_all_ops(exercises: list[dict[str, Any]]) -> bool:
    seen = {ex["bin_op"] for ex in exercises if isinstance(ex, dict) and ex.get("bin_op")}
    return seen == set(BIN_OPS)


def build_op_kwargs_from_catalog(
    exercises: list[dict[str, Any]],
    binding: InstanceBinding,
    *,
    allow_confirmed_write: bool = False,
) -> dict[str, dict[str, Any]]:
    """One exercise per bin_op; inputs may reference binding project/uids."""
    by_op: dict[str, dict[str, Any]] = {}
    for ex in exercises:
        op = ex.get("bin_op")
        if not isinstance(op, str):
            continue
        inputs = dict(ex.get("inputs") or {})
        if op == "nextseek-api-write":
            inputs.setdefault("confirmed_write", False)
            if allow_confirmed_write and ex.get("allow_confirmed_write"):
                inputs["confirmed_write"] = True
        if op == "nextseek-generate-submission" and binding.reference_uids:
            inputs.setdefault("uids", binding.reference_uids[0])
            if "type" not in inputs and "submission_type" not in inputs:
                inputs["type"] = "GEO"
        if op == "nextseek-report":
            inputs.setdefault("project", binding.project_title)
        by_op[op] = inputs
    missing = set(BIN_OPS) - set(by_op)
    if missing:
        raise ValueError(f"catalog missing bin_ops: {sorted(missing)}")
    return by_op


def build_cost_ledger_from_matrix(
    matrix: dict[str, dict[str, Any]],
    *,
    run_id: str,
    timestamp: str,
) -> dict[str, Any]:
    """Build ``cost_ledger.json`` from live matrix rows (Gate 3D writer helper)."""
    entries: list[dict[str, Any]] = []
    total = 0.0
    for op in BIN_OPS:
        row = matrix.get(op) or {}
        if op == "nextseek-api-write":
            entries.append({
                "op": op,
                "source_system": "none",
                "usd": 0.0,
                "call_id": f"{run_id}-{op}-blocked",
                "timestamp": timestamp,
                "reconciliation_note": "WRITE_BLOCKED unconfirmed leg",
            })
            continue
        usd = row.get("cost_usd")
        if usd is None:
            usd = 0.0
        try:
            usd_f = float(usd)
        except (TypeError, ValueError):
            usd_f = 0.0
        total += usd_f
        entries.append({
            "op": op,
            "source_system": row.get("cost_source") or "sidecar_server_llm",
            "usd": usd_f,
            "call_id": row.get("call_id") or f"{run_id}-{op}",
            "timestamp": row.get("cost_timestamp") or timestamp,
            "reconciliation_note": row.get("cost_reconciliation") or "matrix row correlation",
        })
    return {"run_id": run_id, "entries": entries, "total_usd": round(total, 6)}


def binding_fixture_record(binding: InstanceBinding) -> dict[str, Any]:
    """Artifact written instead of seeded_fixture.json for live gate."""
    return {
        "binding_id": binding.binding_id,
        "project_id": binding.project_id,
        "project_title": binding.project_title,
        "project": binding.cc_project_dirname or f"{binding.project_id}-published-data",
        "reference_uids": list(binding.reference_uids),
        "uids": list(binding.reference_uids),
        "requests": [],
        "forbidden_actions": binding.forbidden_actions,
        "source": "instance_binding.json",
    }

"""Authenticated set3 human-functional-grade input for the initial V14 fit.

Human grades populate only ``functional_success``. Runtime and artifact facts
remain separate inputs to the existing total disposition and conservation gates.
The default dry-run is deterministic, provider-free, and performs no DB writes.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import math
import re
import zipfile
from collections import Counter
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlsplit

from nextseek_api.cc_assistant.family_labels import corpus_snapshot
from nextseek_api.eval.conservation import (
    ConservationReport,
    FitAdmission,
    build_conservation_report,
    build_fit_admission,
)
from nextseek_api.eval.disposition import ArmBucket, classify_arm
from nextseek_api.eval.fit.v14.combined import CombinedFitResult, run_v14_generation
from nextseek_api.eval.fit.v14.fit_config import V14FitConfig
from nextseek_api.eval.fit.v14.pair_rows import PairFitRow, build_pair_rows
from nextseek_api.eval.paired_run import PairedExperimentalBatch, build_paired_batch
from nextseek_api.eval.paired_run_registry import content_hash_for_batch
from nextseek_api.eval.router_models_proposal import (
    ArtifactStatus,
    ErrorClass,
    EvalRow,
    FailureMode,
    FamilySource,
    RouteSource,
)

__all__ = [
    "DEFAULT_EVIDENCE_IDENTITY",
    "EvidenceIdentity",
    "EvidenceIntegrityError",
    "HumanFitArm",
    "HumanGradeFit",
    "ModelMode",
    "activate_human_grade_generation",
    "build_human_grade_fit",
    "manifest_for_combined",
    "publish_human_grade_fit",
]


class EvidenceIntegrityError(ValueError):
    pass


_STACK_COMPONENTS = (
    "nextseek_image",
    "container_agent_image",
    "sidecar_image",
    "seek_image",
)
_OCI_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_GIT_SHA_RE = re.compile(r"[0-9a-f]{7,40}\Z")
_LEGACY_STACK_DEBT = (
    "authenticated transferred evidence records only the source git SHA; "
    "the original four immutable runtime image digests cannot be reconstructed"
)


class ModelMode(str, Enum):
    dev_analytic = "dev_analytic"
    initial_human_grade = "initial_human_grade"
    authoritative_mcmc = "authoritative_mcmc"

    @property
    def authoritative(self) -> bool:
        return self is ModelMode.authoritative_mcmc


@dataclass(frozen=True)
class EvidenceIdentity:
    archive_sha256: str
    manifest_sha256: str
    artifact_validity_sha256: str
    member_sha256: dict[str, str]


DEFAULT_EVIDENCE_IDENTITY = EvidenceIdentity(
    archive_sha256="4e7c57a1c04015fbbe4696302d258038b72e71b1bedb17866810474ac74cb814",
    manifest_sha256="d14cb4b153448e295110f3bfdbc5004f1e0455e0673ebcac15ecfe9d635227c2",
    artifact_validity_sha256="7d8859bd206d1c932773cc1d2d0791341a3eb54bdbecc32ea250a58f1827f693",
    member_sha256={
        "corpus/corpus.json": "99efa7a10f2d418190a4a29eb550fea9927037a1b3844a6bc319017609155652",
        "set3_final/bayes_manifest.json": "b2afcb1cbfcf908662419db49aa16f82f22afbd9533b7a25053acf6a5641e6c0",
        "set3_final/hibayes/hibayes_eval_rows_ns.csv": "3ffa3bb85982430e68fe9933ab01d248ec9b6610486a4490ed050905647d4d72",
        "set3_final/hibayes/hibayes_eval_rows_cc.csv": "ac2bfac379392232b4ce98a81e00cfeb06e8938806a50094ec7cfc94228f4859",
        "set3_final/hibayes/hibayes_functional_usefulness_human_ns.csv": "c1ed72e72b2fa1811d15c1fb8a88b8af791072947038901c4b0d356b8e0e3cef",
        "set3_final/hibayes/hibayes_functional_usefulness_human_cc.csv": "b40d13e3a1bcc2ed0fcd607c3fc6c4fe1ac6a601129cf7978346da1d4edec76b",
    },
)


@dataclass(frozen=True)
class HumanFitArm:
    arm_id: str
    row: EvalRow
    bucket: ArmBucket
    combined_success: bool | None


@dataclass(frozen=True)
class HumanGradeFit:
    delivery_path: Path
    model_mode: ModelMode
    fit_config: V14FitConfig
    seed: int
    arms: tuple[HumanFitArm, ...]
    eval_rows: tuple[EvalRow, ...]
    conservation: ConservationReport
    admission: FitAdmission
    paired_batch: PairedExperimentalBatch
    paired_content_hash: str
    pair_rows: tuple[PairFitRow, ...]
    fit: CombinedFitResult
    publication_evidence: "PublicationEvidence"
    source_hashes: dict[str, Any]

    def report(self) -> dict[str, Any]:
        return {
            "schema_version": "human-grade-fit-report/v1",
            "source": {
                **self.source_hashes,
                "functional_success_source": "human_grades",
                "judge_calls_used": 0,
                "judge_output_gates_initial_fit": False,
            },
            "model": {
                "mode": self.model_mode.value,
                "authoritative": self.model_mode.authoritative,
                "initial_release_override_required": (
                    self.model_mode is ModelMode.initial_human_grade
                ),
                "diagnostics_ok": self.fit.diagnostics_ok,
                "config_fingerprint": self.fit.decision.config_fingerprint,
                "residual_debt": (
                    "non-MCMC empirical fit; does not satisfy original V14 diagnostics"
                    if not self.model_mode.authoritative
                    else None
                ),
            },
            "conservation": {
                "input_arms": self.conservation.input_count,
                "scored_desired": self.conservation.scored_desired,
                "scored_not_desired": self.conservation.scored_not_desired,
                "excluded_by_reason": self.conservation.excluded_by_reason,
                "pending": self.conservation.pending,
                "by_reason": dict(sorted(self.conservation.by_reason.items())),
                "balanced": self.conservation.balanced,
                "retained_pairs": len(self.admission.retained_pairs),
                "excluded_pairs": len(self.admission.excluded_pair_ids),
                "pending_pairs": len(self.admission.pending_pair_ids),
            },
            "paired_run": {
                "paired_run_id": self.paired_batch.paired_run_id,
                "content_hash": self.paired_content_hash,
                "pairs": len(self.paired_batch.pairs),
                "arms": len(self.paired_batch.arm_records),
            },
            "decision": {
                "generation_status": self.fit.decision.generation_status,
                "activated_families": list(self.fit.decision.activated_families),
                "posterior_expected_fdr": self.fit.decision.posterior_expected_fdr,
                "candidates": [
                    {
                        "family": item.family,
                        "status": item.status.value,
                        "local_error_prob": item.local_error_prob,
                        "activated": item.activated,
                    }
                    for item in self.fit.decision.candidates
                ],
            },
        }

    def report_json(self) -> str:
        return json.dumps(self.report(), sort_keys=True, separators=(",", ":"))


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_hash(path: Path, expected: str) -> None:
    actual = _sha256_file(path)
    if actual != expected:
        raise EvidenceIntegrityError(
            f"{path.name} sha256 mismatch: expected {expected}, got {actual}"
        )


def _verified_source_bytes(
    delivery: Path,
    identity: EvidenceIdentity,
) -> tuple[dict[str, bytes], bytes]:
    archive_path = delivery / "testquestions.zip"
    manifest_path = delivery / "MANIFEST.json"
    artifact_path = delivery / "artifact_validity_set3_final.csv"
    for path in (archive_path, manifest_path, artifact_path):
        if not path.is_file():
            raise EvidenceIntegrityError(f"required evidence file missing: {path}")

    # Authenticate containers before parsing either metadata or member content.
    _require_hash(archive_path, identity.archive_sha256)
    _require_hash(manifest_path, identity.manifest_sha256)
    _require_hash(artifact_path, identity.artifact_validity_sha256)

    manifest = json.loads(manifest_path.read_bytes())
    declared = {
        str(item.get("path_laptop", "")).split("/testquestions/", 1)[-1]: item
        for item in manifest.get("files", [])
        if "/testquestions/" in str(item.get("path_laptop", ""))
    }
    members: dict[str, bytes] = {}
    with zipfile.ZipFile(archive_path) as archive:
        for relative, expected in identity.member_sha256.items():
            item = declared.get(relative)
            if item is None or item.get("checksum") != expected:
                raise EvidenceIntegrityError(
                    f"authenticated MANIFEST.json does not bind {relative} to {expected}"
                )
            member_name = f"testquestions/{relative}"
            try:
                raw = archive.read(member_name)
            except KeyError as exc:
                raise EvidenceIntegrityError(f"archive member missing: {member_name}") from exc
            actual = hashlib.sha256(raw).hexdigest()
            if actual != expected:
                raise EvidenceIntegrityError(
                    f"archive member {relative} sha256 mismatch: expected {expected}, got {actual}"
                )
            members[relative] = raw
    return members, artifact_path.read_bytes()


def _csv_rows(raw: bytes, source: str, required: Iterable[str]) -> list[dict[str, str]]:
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8")))
    missing = set(required) - set(reader.fieldnames or ())
    if missing:
        raise EvidenceIntegrityError(f"{source} missing columns: {sorted(missing)}")
    rows = list(reader)
    if not rows:
        raise EvidenceIntegrityError(f"{source} contains no rows")
    return rows


def _strict_bool(value: str, *, source: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise EvidenceIntegrityError(f"{source} has non-boolean value {value!r}")


def _index(rows: Iterable[dict[str, str]], *, source: str) -> dict[str, dict[str, str]]:
    indexed: dict[str, dict[str, str]] = {}
    for row in rows:
        key = str(row["query_id"])
        if key in indexed:
            raise EvidenceIntegrityError(f"{source} duplicate query_id {key!r}")
        indexed[key] = row
    return indexed


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _stack_identity(run_meta: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    git_sha = run_meta.get("git_sha")
    if not isinstance(git_sha, str) or not git_sha.strip():
        raise EvidenceIntegrityError("bayes_manifest run_meta.git_sha is required")
    git_sha = git_sha.strip()
    digests = {
        name: run_meta[name]
        for name in _STACK_COMPONENTS
        if run_meta.get(name) not in (None, "")
    }
    if not digests:
        return f"legacy-git-sha-only:{git_sha}", {
            "stack_identity_status": "legacy_git_sha_only",
            "stack_identity_debt": _LEGACY_STACK_DEBT,
            "source_git_sha": git_sha,
            "stack_image_digests": {},
        }
    if set(digests) != set(_STACK_COMPONENTS):
        missing = sorted(set(_STACK_COMPONENTS) - set(digests))
        raise EvidenceIntegrityError(
            f"partial four-image stack identity; missing immutable digests: {missing}"
        )
    malformed = [
        name for name in _STACK_COMPONENTS
        if not isinstance(digests[name], str)
        or _OCI_DIGEST_RE.fullmatch(digests[name]) is None
    ]
    if malformed:
        raise EvidenceIntegrityError(
            f"stack image identities must be immutable lowercase OCI digests: {malformed}"
        )
    canonical = "stack-v1" + "".join(
        f"{len(name)}:{name}{len(digests[name])}:{digests[name]}"
        for name in _STACK_COMPONENTS
    )
    stack_hash = hashlib.sha256(canonical.encode()).hexdigest()
    ordered = {name: digests[name] for name in _STACK_COMPONENTS}
    return f"stack-v1:sha256:{stack_hash}", {
        "stack_identity_status": "exact_four_image_digests",
        "stack_identity_debt": None,
        "source_git_sha": git_sha,
        "stack_image_digests": ordered,
    }


def _validate_run_meta(
    run_meta: Any,
    *,
    corpus_sha256: str,
    pair_ids: list[str],
) -> dict[str, Any]:
    if not isinstance(run_meta, dict):
        raise EvidenceIntegrityError("bayes_manifest run_meta must be an object")
    _validate_run_meta_identity(
        run_meta,
        path="run_meta",
        corpus_sha256=corpus_sha256,
        pair_ids=pair_ids,
    )
    if not isinstance(run_meta.get("superseded_runs"), list):
        raise EvidenceIntegrityError("bayes_manifest run_meta.superseded_runs must be a list")
    for index, superseded in enumerate(run_meta["superseded_runs"]):
        path = f"run_meta.superseded_runs.{index}"
        if not isinstance(superseded, dict):
            raise EvidenceIntegrityError(f"bayes_manifest {path} must be an object")
        _validate_run_meta_identity(
            superseded,
            path=path,
            corpus_sha256=corpus_sha256,
            pair_ids=pair_ids,
        )
        if "superseded_runs" in superseded:
            raise EvidenceIntegrityError(
                f"bayes_manifest {path}.superseded_runs must not be nested"
            )
    return run_meta


def _validate_run_meta_identity(
    run_meta: dict[str, Any],
    *,
    path: str,
    corpus_sha256: str,
    pair_ids: list[str],
) -> None:
    prefix = f"bayes_manifest {path}"
    if run_meta.get("mode") != "bayesian":
        raise EvidenceIntegrityError(f"{prefix}.mode must be 'bayesian'")
    if run_meta.get("arms") != ["ns", "cc"]:
        raise EvidenceIntegrityError(f"{prefix}.arms must be exactly ['ns', 'cc']")
    if run_meta.get("corpus_fingerprint") != corpus_sha256:
        raise EvidenceIntegrityError(
            f"authenticated {prefix}.corpus_fingerprint does not match the current router corpus"
        )
    if run_meta.get("selected_ids") != pair_ids:
        raise EvidenceIntegrityError(
            f"authenticated {prefix}.selected_ids must exactly match the ordered pair IDs"
        )
    git_sha = run_meta.get("git_sha")
    if not isinstance(git_sha, str) or _GIT_SHA_RE.fullmatch(git_sha) is None:
        raise EvidenceIntegrityError(f"{prefix}.git_sha must be a lowercase 7-40 hex git SHA")
    base_url = run_meta.get("base_url")
    if not isinstance(base_url, str) or base_url != base_url.strip():
        raise EvidenceIntegrityError(f"{prefix}.base_url must be an absolute HTTP(S) origin")
    parsed = urlsplit(base_url)
    try:
        port = parsed.port
    except ValueError as exc:
        raise EvidenceIntegrityError(
            f"{prefix}.base_url must contain a valid port"
        ) from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or port is not None and not (1 <= port <= 65535)
    ):
        raise EvidenceIntegrityError(f"{prefix}.base_url must be an absolute HTTP(S) origin")
    max_usd = run_meta.get("max_usd")
    if (
        isinstance(max_usd, bool)
        or not isinstance(max_usd, (int, float))
        or not math.isfinite(max_usd)
        or max_usd < 0
    ):
        raise EvidenceIntegrityError(f"{prefix}.max_usd must be a finite nonnegative number")
    if not isinstance(run_meta.get("resumed"), bool):
        raise EvidenceIntegrityError(f"{prefix}.resumed must be boolean")


def _aggregate_rows(arms: Iterable[HumanFitArm]) -> list[dict[str, Any]]:
    return [
        {
            "arm_id": arm.arm_id,
            "bucket": arm.bucket.bucket.value,
            "exclusion_reason": (
                arm.bucket.exclusion_reason.value if arm.bucket.exclusion_reason else None
            ),
            "combined_success": arm.combined_success,
        }
        for arm in arms
    ]


def _publication_evidence_from_derived_facts(
    *,
    model_mode: ModelMode,
    fit: CombinedFitResult,
    source_hashes: dict[str, Any],
    paired_batch: PairedExperimentalBatch,
    paired_content_hash: str,
    arms: tuple[HumanFitArm, ...],
    conservation: ConservationReport,
    admission: FitAdmission,
    pair_rows: tuple[PairFitRow, ...],
):
    from nextseek_api.eval.publish import PublicationEvidence

    current = corpus_snapshot()
    if source_hashes.get("corpus_sha256") != current.corpus_sha256:
        raise EvidenceIntegrityError("prepared source corpus no longer matches the router corpus")
    grade_hashes = source_hashes.get("human_grade_sha256")
    if not isinstance(grade_hashes, dict) or set(grade_hashes) != {"ns", "cc"}:
        raise EvidenceIntegrityError("prepared human-grade source hashes are incomplete")
    stack_provenance = source_hashes.get("stack_identity")
    if not isinstance(stack_provenance, dict):
        raise EvidenceIntegrityError("prepared stack identity provenance is missing")

    family_retained = Counter(row.family for row in pair_rows)
    counts = {
        "input_arms": len(arms),
        "scored_desired": conservation.scored_desired,
        "scored_not_desired": conservation.scored_not_desired,
        "excluded_arms": conservation.excluded_by_reason,
        "pending_arms": conservation.pending,
        "retained_pairs": len(admission.retained_pairs),
        "excluded_pairs": len(admission.excluded_pair_ids),
        "pending_pairs": len(admission.pending_pair_ids),
        "functional_success_true": sum(arm.row.functional_success is True for arm in arms),
        "functional_success_false": sum(arm.row.functional_success is False for arm in arms),
        "runtime_failures": sum(not arm.row.runtime_success for arm in arms),
        "artifact_failures": sum(not arm.row.artifact_success for arm in arms),
    }
    provenance = {
        "origin": "set3_final_human_functional_grade_initial_fit",
        "paired_run_id": paired_batch.paired_run_id,
        "paired_run_content_hash": paired_content_hash,
        "evidence_kind": "paired_experimental",
        "route_source": "forced",
        "functional_success_source": "human_grades",
        "human_grades_are_judge_attempts": False,
        "judge_calls_used": 0,
        "judge_comparison_gates_initial_fit": False,
        "corpus_version": 2,
        "corpus_sha256": current.corpus_sha256,
        "model_mode": model_mode.value,
        "initial_release_override": model_mode is ModelMode.initial_human_grade,
        "source_hashes": source_hashes,
        **stack_provenance,
    }
    diagnostics = {
        "model_mode": model_mode.value,
        "authoritative": model_mode.authoritative,
        "initial_release_override": model_mode is ModelMode.initial_human_grade,
        "diagnostics_ok": fit.diagnostics_ok,
        "quality": {
            family: {
                "divergences": result.divergences,
                "rhat_max": result.rhat_max,
                "ess_bulk_min": result.ess_bulk_min,
                "ess_tail_min": result.ess_tail_min,
            }
            for family, result in sorted(fit.quality.items())
        },
        "latency": {
            family: {
                "divergences": result.divergences,
                "rhat_max": result.rhat_max,
                "ess_bulk_min": result.ess_bulk_min,
                "ess_tail_min": result.ess_tail_min,
            }
            for family, result in sorted(fit.latency.items())
        },
    }
    return PublicationEvidence(
        input_hash=_canonical_hash(
            {"sources": source_hashes, "paired_run": paired_content_hash}
        ),
        attempt_hash=_canonical_hash(
            {"kind": "human_functional_grades", "files": grade_hashes}
        ),
        aggregate_hash=_canonical_hash(_aggregate_rows(arms)),
        compatibility_keys={
            "taxonomy_version": current.taxonomy_version,
            "corpus_hash": current.corpus_sha256,
        },
        counts=counts,
        exclusions=dict(sorted(conservation.by_reason.items())),
        fit_diagnostics=diagnostics,
        source_provenance=provenance,
        family_retained_pairs=dict(sorted(family_retained.items())),
    )


def build_human_grade_fit(
    delivery: str | Path,
    *,
    identity: EvidenceIdentity = DEFAULT_EVIDENCE_IDENTITY,
    model_mode: ModelMode = ModelMode.dev_analytic,
    config: V14FitConfig | None = None,
    seed: int = 0,
) -> HumanGradeFit:
    delivery_path = Path(delivery)
    members, artifact_bytes = _verified_source_bytes(delivery_path, identity)

    corpus_payload = json.loads(members["corpus/corpus.json"])
    if corpus_payload.get("version") != 2:
        raise EvidenceIntegrityError("transferred corpus is not version 2")
    current = corpus_snapshot()
    if current.corpus_sha256 != identity.member_sha256["corpus/corpus.json"]:
        raise EvidenceIntegrityError("transferred corpus does not match the current router corpus")

    manifest_raw = json.loads(members["set3_final/bayes_manifest.json"])
    from nessie_tests.bayes_manifest import BayesManifest

    BayesManifest.model_validate(manifest_raw)
    pairs_raw = manifest_raw.get("pairs") or []
    if len(pairs_raw) != 149:
        raise EvidenceIntegrityError(f"expected 149 set3 pairs, got {len(pairs_raw)}")

    runtime_required = {
        "query_id", "task_family", "image", "answer_provided", "is_error",
        "timed_out", "runtime_success", "failure_mode", "latency_seconds", "cost_usd",
    }
    grade_required = {"query_id", "task_family", "functional_success", "grade_key", "arm"}
    artifact_required = {
        "query_id", "arm", "task_family", "artifact_expected", "plan_artifact_status",
        "artifact_success",
    }
    runtime: dict[str, dict[str, dict[str, str]]] = {}
    grades: dict[str, dict[str, dict[str, str]]] = {}
    for arm in ("ns", "cc"):
        runtime_name = f"set3_final/hibayes/hibayes_eval_rows_{arm}.csv"
        grade_name = f"set3_final/hibayes/hibayes_functional_usefulness_human_{arm}.csv"
        runtime[arm] = _index(
            _csv_rows(members[runtime_name], runtime_name, runtime_required),
            source=runtime_name,
        )
        grades[arm] = _index(
            _csv_rows(members[grade_name], grade_name, grade_required),
            source=grade_name,
        )
    artifact_rows = _csv_rows(artifact_bytes, "artifact_validity_set3_final.csv", artifact_required)
    artifacts: dict[tuple[str, str], dict[str, str]] = {}
    for row in artifact_rows:
        key = (row["query_id"], row["arm"])
        if key in artifacts:
            raise EvidenceIntegrityError(f"artifact validity duplicate arm {key!r}")
        artifacts[key] = row

    pair_ids = [str(pair["id"]) for pair in pairs_raw]
    if len(set(pair_ids)) != 149:
        raise EvidenceIntegrityError("paired manifest contains duplicate pair ids")
    expected_ids = set(pair_ids)
    run_meta = _validate_run_meta(
        manifest_raw.get("run_meta"),
        corpus_sha256=current.corpus_sha256,
        pair_ids=pair_ids,
    )
    stack_id, stack_provenance = _stack_identity(run_meta)
    for arm in ("ns", "cc"):
        if set(runtime[arm]) != expected_ids or set(grades[arm]) != expected_ids:
            raise EvidenceIntegrityError(f"{arm} runtime/grade query ids do not conserve pairs")
    if set(artifacts) != {(query_id, arm) for query_id in expected_ids for arm in ("ns", "cc")}:
        raise EvidenceIntegrityError("artifact-validity arms do not exactly match the 149 pairs")

    arms: list[HumanFitArm] = []
    pair_specs: list[dict[str, Any]] = []
    arm_records: dict[str, dict[str, Any]] = {}
    for pair in pairs_raw:
        query_id = str(pair["id"])
        family = str(pair["family"])
        pair_spec = {
            "pair_id": query_id,
            "query_id": query_id,
            "family": family,
            "ns_arm_id": f"{query_id}::ns",
            "cc_arm_id": f"{query_id}::cc",
        }
        pair_specs.append(pair_spec)
        for arm in ("ns", "cc"):
            if pair.get(arm) is None:
                raise EvidenceIntegrityError(f"paired manifest missing {query_id}::{arm}")
            runtime_row = runtime[arm][query_id]
            grade_row = grades[arm][query_id]
            artifact_row = artifacts[(query_id, arm)]
            if {runtime_row["task_family"], grade_row["task_family"], artifact_row["task_family"]} != {family}:
                raise EvidenceIntegrityError(f"task-family mismatch for {query_id}::{arm}")
            if grade_row["grade_key"] != f"{query_id}::{arm}" or grade_row["arm"] != arm:
                raise EvidenceIntegrityError(f"human grade provenance mismatch for {query_id}::{arm}")

            expected_route = "nextseek_query" if arm == "ns" else "container_cc"
            if runtime_row["image"] != expected_route:
                raise EvidenceIntegrityError(f"runtime route mismatch for {query_id}::{arm}")
            artifact_status = ArtifactStatus(artifact_row["plan_artifact_status"])
            runtime_success = _strict_bool(runtime_row["runtime_success"], source="runtime_success")
            artifact_success = _strict_bool(artifact_row["artifact_success"], source="artifact_success")
            functional_success = _strict_bool(grade_row["functional_success"], source="functional_success")
            outage = bool(pair[arm].get("outage"))
            row = EvalRow(
                query_id=query_id,
                route=expected_route,
                task_family=family,
                route_source=RouteSource.forced,
                family_source=FamilySource.corpus,
                stack_id=stack_id,
                answer_provided=_strict_bool(runtime_row["answer_provided"], source="answer_provided"),
                is_error=_strict_bool(runtime_row["is_error"], source="is_error"),
                timed_out=_strict_bool(runtime_row["timed_out"], source="timed_out"),
                runtime_success=runtime_success,
                failure_mode=FailureMode(runtime_row["failure_mode"]),
                error_class=ErrorClass.provider_outage if outage else ErrorClass.none,
                latency_seconds=float(runtime_row["latency_seconds"]),
                cost_usd=float(runtime_row["cost_usd"]) if runtime_row["cost_usd"] else None,
                artifact_expected=_strict_bool(artifact_row["artifact_expected"], source="artifact_expected"),
                artifact_status=artifact_status,
                artifact_success=artifact_success,
                # This is the only value read from the human grade as an outcome axis.
                functional_success=functional_success,
            )
            bucket = classify_arm(row)
            arm_id = f"{query_id}::{arm}"
            combined = row.outcome()
            arms.append(HumanFitArm(arm_id=arm_id, row=row, bucket=bucket, combined_success=combined))
            arm_records[arm_id] = {
                "pair_id": query_id,
                "route": "nextseek" if arm == "ns" else "container_cc",
                "query_id": query_id,
                "combined_success": combined,
                "latency_seconds": row.latency_seconds,
                "latency_censored": not row.runtime_success,
                "cost_usd": row.cost_usd,
            }

    buckets = [arm.bucket for arm in arms]
    conservation = build_conservation_report(buckets)
    if not conservation.balanced or conservation.input_count != 298:
        raise EvidenceIntegrityError("298-arm conservation failed")
    buckets_by_arm = {arm.arm_id: arm.bucket for arm in arms}
    paired_run_id = f"set3-final-{identity.member_sha256['set3_final/bayes_manifest.json'][:16]}"
    paired_batch = build_paired_batch(
        paired_run_id=paired_run_id,
        pairs=pair_specs,
        arm_records=arm_records,
    )
    paired_content_hash = content_hash_for_batch(pair_specs, arm_records)
    admission = build_fit_admission(pair_specs, buckets_by_arm)
    pair_rows = build_pair_rows(admission, arm_records)

    cfg = config or V14FitConfig()
    fit = run_v14_generation(
        pair_rows,
        cfg,
        seed=seed,
        use_mcmc=model_mode.authoritative,
    )
    grade_hashes = {
        arm: identity.member_sha256[
            f"set3_final/hibayes/hibayes_functional_usefulness_human_{arm}.csv"
        ]
        for arm in ("ns", "cc")
    }
    source_hashes = {
        "archive_sha256": identity.archive_sha256,
        "manifest_sha256": identity.manifest_sha256,
        "member_sha256": dict(sorted(identity.member_sha256.items())),
        "artifact_validity_sha256": identity.artifact_validity_sha256,
        "human_grade_sha256": grade_hashes,
        "corpus_version": 2,
        "corpus_sha256": current.corpus_sha256,
        "stack_identity": stack_provenance,
    }
    arms_tuple = tuple(arms)
    pair_rows_tuple = tuple(pair_rows)
    publication_evidence = _publication_evidence_from_derived_facts(
        model_mode=model_mode,
        fit=fit,
        source_hashes=source_hashes,
        paired_batch=paired_batch,
        paired_content_hash=paired_content_hash,
        arms=arms_tuple,
        conservation=conservation,
        admission=admission,
        pair_rows=pair_rows_tuple,
    )
    return HumanGradeFit(
        delivery_path=delivery_path,
        model_mode=model_mode,
        fit_config=cfg,
        seed=seed,
        arms=arms_tuple,
        eval_rows=tuple(arm.row for arm in arms),
        conservation=conservation,
        admission=admission,
        paired_batch=paired_batch,
        paired_content_hash=paired_content_hash,
        pair_rows=pair_rows_tuple,
        fit=fit,
        publication_evidence=publication_evidence,
        source_hashes=source_hashes,
    )


def manifest_for_combined(fit: CombinedFitResult, evidence: PublicationEvidence):
    """Create an inspectable manifest; publication separately enforces authority."""
    from nextseek_api.eval.publish import manifest_for_combined as _manifest

    return _manifest(fit, evidence, for_publication=False)


def publish_human_grade_fit(
    prepared: HumanGradeFit,
    *,
    actor: str = "local",
    allow_initial_release_override: bool = False,
):
    """Register the exact paired batch and publish; never activates implicitly."""
    from django.db import transaction

    from nextseek_api.eval.conservation import build_fit_admission as checked_admission
    from nextseek_api.eval.disposition import classify_arm as checked_classify_arm
    from nextseek_api.eval.fit.v14.pair_rows import build_pair_rows as checked_pair_rows
    from nextseek_api.eval.fit.fit_boundary import (
        assert_paired_experimental_only,
        validate_publish_provenance,
    )
    from nextseek_api.eval import generation_store
    from nextseek_api.eval.paired_run_registry import register_paired_run
    from nextseek_api.eval.publish import manifest_for_combined as _manifest

    # Pydantic's frozen model prevents field replacement, but its nested dicts
    # remain mutable. Re-authenticate the exact content boundary before any
    # registry row, refit, or generation can be written.
    actual_paired_hash = content_hash_for_batch(
        prepared.paired_batch.pairs,
        prepared.paired_batch.arm_records,
    )
    if actual_paired_hash != prepared.paired_content_hash:
        raise EvidenceIntegrityError(
            "prepared paired batch content hash changed after authentication"
        )
    assert_paired_experimental_only(prepared.paired_batch)

    # Publication always reopens and reauthenticates the maintained release
    # identity. A prepared object's mutable source hashes are never authority.
    authenticated = build_human_grade_fit(
        prepared.delivery_path,
        identity=DEFAULT_EVIDENCE_IDENTITY,
        model_mode=prepared.model_mode,
        config=prepared.fit_config,
        seed=prepared.seed,
    )
    if authenticated.source_hashes != prepared.source_hashes:
        raise EvidenceIntegrityError("authenticated source delivery changed after preparation")
    if (
        authenticated.paired_content_hash != actual_paired_hash
        or authenticated.paired_batch != prepared.paired_batch
    ):
        raise EvidenceIntegrityError("authenticated source delivery changed after preparation")

    arm_records = prepared.paired_batch.arm_records
    if len({arm.arm_id for arm in prepared.arms}) != len(prepared.arms):
        raise EvidenceIntegrityError("prepared fit contains duplicate arm ids")
    if {arm.arm_id for arm in prepared.arms} != set(arm_records):
        raise EvidenceIntegrityError("prepared fit arms do not match paired batch arms")
    checked_arms: list[HumanFitArm] = []
    for arm in prepared.arms:
        bucket = checked_classify_arm(arm.row)
        combined = arm.row.outcome()
        if bucket != arm.bucket or combined != arm.combined_success:
            raise EvidenceIntegrityError(
                f"prepared arm outcome changed after authentication: {arm.arm_id}"
            )
        suffix = arm.arm_id.rsplit("::", 1)[-1]
        expected_record = {
            "pair_id": arm.row.query_id,
            "route": "nextseek" if suffix == "ns" else "container_cc",
            "query_id": arm.row.query_id,
            "combined_success": combined,
            "latency_seconds": arm.row.latency_seconds,
            "latency_censored": not arm.row.runtime_success,
            "cost_usd": arm.row.cost_usd,
        }
        if arm_records.get(arm.arm_id) != expected_record:
            raise EvidenceIntegrityError(
                f"prepared arm facts do not match paired batch: {arm.arm_id}"
            )
        checked_arms.append(
            HumanFitArm(
                arm_id=arm.arm_id,
                row=arm.row,
                bucket=bucket,
                combined_success=combined,
            )
        )

    checked_arms_tuple = tuple(checked_arms)
    conservation = build_conservation_report([arm.bucket for arm in checked_arms_tuple])
    if not conservation.balanced or conservation.input_count != 298:
        raise EvidenceIntegrityError("publish-time 298-arm conservation failed")
    buckets_by_arm = {arm.arm_id: arm.bucket for arm in checked_arms_tuple}
    admission = checked_admission(
        prepared.paired_batch.pairs,
        buckets_by_arm,
    )
    checked_rows = checked_pair_rows(
        admission,
        arm_records,
    )
    checked_fit = run_v14_generation(
        checked_rows,
        prepared.fit_config,
        seed=prepared.seed,
        use_mcmc=prepared.model_mode.authoritative,
    )
    derived_evidence = _publication_evidence_from_derived_facts(
        model_mode=prepared.model_mode,
        fit=checked_fit,
        source_hashes=prepared.source_hashes,
        paired_batch=prepared.paired_batch,
        paired_content_hash=actual_paired_hash,
        arms=checked_arms_tuple,
        conservation=conservation,
        admission=admission,
        pair_rows=tuple(checked_rows),
    )
    if derived_evidence != prepared.publication_evidence:
        raise EvidenceIntegrityError(
            "prepared derived publication evidence changed after authentication"
        )
    if conservation != prepared.conservation or admission != prepared.admission:
        raise EvidenceIntegrityError("prepared conservation/admission changed after authentication")
    if tuple(checked_rows) != prepared.pair_rows:
        raise EvidenceIntegrityError("prepared pair rows changed after authentication")

    # Refuse authority/provenance defects before any durable state is mutated.
    manifest = _manifest(
        checked_fit,
        derived_evidence,
        for_publication=True,
        allow_initial_release_override=allow_initial_release_override,
    )
    with transaction.atomic():
        register_paired_run(
            paired_run_id=prepared.paired_batch.paired_run_id,
            schema_version=prepared.paired_batch.schema_version,
            content_hash=actual_paired_hash,
        )
        validate_publish_provenance(derived_evidence.source_provenance)
        return generation_store._publish_authenticated_generation(
            manifest,
            capability=generation_store._AUTHENTICATED_HUMAN_PUBLISH_CAPABILITY,
            actor=actor,
        )


def activate_human_grade_generation(
    generation_hash: str,
    *,
    expected_hash: str,
    activated_by: str = "local",
):
    """Explicit CAS activation; publishing never calls this function."""
    from nextseek_api.assistant.models_db import PosteriorGeneration
    from nextseek_api.eval.generation_store import activate_generation

    generation = PosteriorGeneration.objects.get(generation_hash=generation_hash)
    return activate_generation(
        generation,
        expected_hash=expected_hash,
        activated_by=activated_by,
    )


def main(argv: list[str] | None = None) -> int:
    import django
    from django.apps import apps

    if not apps.ready:
        django.setup()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("delivery", type=Path, nargs="?")
    parser.add_argument("--action", choices=("dry-run", "publish", "activate"), default="dry-run")
    parser.add_argument(
        "--model-mode",
        choices=tuple(mode.value for mode in ModelMode),
        default=ModelMode.dev_analytic.value,
    )
    parser.add_argument("--generation-hash")
    parser.add_argument("--expected-active-hash")
    parser.add_argument("--actor", default="local")
    parser.add_argument(
        "--allow-initial-release-override",
        action="store_true",
        help=(
            "explicitly permit the non-MCMC initial_human_grade release; "
            "never implies V14 statistical acceptance"
        ),
    )
    args = parser.parse_args(argv)

    if args.action == "activate":
        if not args.generation_hash or args.expected_active_hash is None:
            parser.error("activate requires --generation-hash and --expected-active-hash")
        pointer = activate_human_grade_generation(
            args.generation_hash,
            expected_hash=args.expected_active_hash,
            activated_by=args.actor,
        )
        print(json.dumps({"active_generation_hash": pointer.active.generation_hash}, sort_keys=True))
        return 0

    if args.delivery is None:
        parser.error("dry-run/publish requires the delivery directory")
    prepared = build_human_grade_fit(args.delivery, model_mode=ModelMode(args.model_mode))
    if args.action == "dry-run":
        print(prepared.report_json())
        return 0
    generation = publish_human_grade_fit(
        prepared,
        actor=args.actor,
        allow_initial_release_override=args.allow_initial_release_override,
    )
    print(json.dumps({"generation_hash": generation.generation_hash, "activated": False}, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through operator invocation
    raise SystemExit(main())

"""Capped, authenticated comparison of DD-44 judge results to human grades.

The command is a zero-provider dry run unless ``--execute-provider`` is explicit.
Provider execution additionally requires the exact SHA-256 of a previously
written sample manifest and never selects more than six arms.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import sys
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import orjson

from nextseek_api.eval.attempt_store import AttemptStore
from nextseek_api.eval.judge import aggregate_three_evaluations
from nextseek_api.eval.judge_models import FunctionalEvaluation, FunctionalEvaluationInput

ABSOLUTE_MAX_ARMS = 6
DEFAULT_MAX_ARMS = 4
CALLS_PER_ARM = 3
DEFAULT_ARCHIVE = Path(
    "/home/taishajo/work/NExtSEEK-dev/testquestions-2026-08-07/testquestions.zip"
)
DEFAULT_DELIVERY_MANIFEST = Path(
    "/home/taishajo/work/NExtSEEK-dev/testquestions-2026-08-07/MANIFEST.json"
)
PINNED_ARCHIVE_SHA256 = "4e7c57a1c04015fbbe4696302d258038b72e71b1bedb17866810474ac74cb814"
PINNED_MANIFEST_SHA256 = "d14cb4b153448e295110f3bfdbc5004f1e0455e0673ebcac15ecfe9d635227c2"
MODEL_ID = "gemini-3.1-pro-preview"
PROMPT_VERSION = "functional_evaluator.baml:DD-22"
EVALUATOR_VERSION = "plan018-v4-9-human-comparison-v1"

INPUT_MEMBER = "testquestions/set3_final/hibayes/hibayes_functional_eval_inputs.csv"
HUMAN_MEMBERS = {
    "ns": "testquestions/set3_final/hibayes/hibayes_functional_usefulness_human_ns.csv",
    "cc": "testquestions/set3_final/hibayes/hibayes_functional_usefulness_human_cc.csv",
}
REQUIRED_MEMBERS = (INPUT_MEMBER, *HUMAN_MEMBERS.values())


class ComparisonError(ValueError):
    """Fail-closed input, execution, or provenance violation."""


@dataclass(frozen=True)
class ComparisonArm:
    arm_id: str
    arm: str
    task_family: str
    human_functional_success: bool
    evaluation_input: FunctionalEvaluationInput
    input_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "arm_id": self.arm_id,
            "arm": self.arm,
            "task_family": self.task_family,
            "human_functional_success": self.human_functional_success,
            "input_sha256": self.input_sha256,
            "evaluation_input": self.evaluation_input.model_dump(mode="json"),
        }


@dataclass(frozen=True)
class AuthenticatedBundle:
    archive_path: Path
    delivery_manifest_path: Path
    archive_sha256: str
    manifest_sha256: str
    member_sha256: dict[str, str]
    arms: dict[str, ComparisonArm]
    ineligible_missing_final_answer: tuple[str, ...]


Evaluator = Callable[[str, int, FunctionalEvaluationInput], FunctionalEvaluation]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _strict_bool(value: str, *, field: str) -> bool:
    normalized = value.strip().lower()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise ComparisonError(f"{field} must be true or false, got {value!r}")


def _manifest_entry(manifest: dict[str, object], member: str) -> dict[str, object]:
    suffix = member.removeprefix("testquestions/")
    matches = [
        entry
        for entry in manifest.get("files", [])
        if isinstance(entry, dict)
        and isinstance(entry.get("path_laptop"), str)
        and entry["path_laptop"].endswith(f"/testquestions/{suffix}")
    ]
    if len(matches) != 1:
        raise ComparisonError(f"delivery manifest does not uniquely authenticate {member}")
    return matches[0]


def _read_csv(payload: bytes, member: str) -> list[dict[str, str]]:
    try:
        return list(csv.DictReader(io.StringIO(payload.decode("utf-8-sig"))))
    except (UnicodeDecodeError, csv.Error) as exc:
        raise ComparisonError(f"invalid CSV member {member}: {exc}") from exc


def _evaluation_input(row: dict[str, str]) -> FunctionalEvaluationInput:
    try:
        return FunctionalEvaluationInput.model_validate(
            {
                "task_family": row["task_family"],
                "query_text": row["query_text"],
                "final_answer": row["final_answer"] or None,
                "answer_provided": _strict_bool(row["answer_provided"], field="answer_provided"),
                "runtime_success": _strict_bool(row["runtime_success"], field="runtime_success"),
                "failure_mode": row["failure_mode"],
                "expected_behavior": row["expected_behavior"],
                "artifact_expected": _strict_bool(
                    row["artifact_expected"], field="artifact_expected"
                ),
                "artifact_status": row["artifact_status"] or None,
                "artifact_kind": row["artifact_kind"] or None,
                "declared_artifact_count": int(row["declared_artifact_count"]),
            }
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ComparisonError(f"invalid functional evaluation input: {exc}") from exc


def load_authenticated_bundle(
    archive_path: Path,
    delivery_manifest_path: Path,
    *,
    archive_sha256: str,
    manifest_sha256: str,
) -> AuthenticatedBundle:
    """Authenticate delivery bytes and join evaluator inputs to human grades."""
    archive_path = Path(archive_path)
    delivery_manifest_path = Path(delivery_manifest_path)
    manifest_bytes = delivery_manifest_path.read_bytes()
    actual_manifest_sha = _sha256(manifest_bytes)
    if actual_manifest_sha != manifest_sha256:
        raise ComparisonError(
            f"delivery manifest SHA-256 mismatch: expected {manifest_sha256}, got {actual_manifest_sha}"
        )
    try:
        manifest = json.loads(manifest_bytes)
    except json.JSONDecodeError as exc:
        raise ComparisonError(f"invalid delivery manifest JSON: {exc}") from exc
    archive_bytes = archive_path.read_bytes()
    actual_archive_sha = _sha256(archive_bytes)
    if actual_archive_sha != archive_sha256:
        raise ComparisonError(
            f"archive SHA-256 mismatch: expected {archive_sha256}, got {actual_archive_sha}"
        )
    source = manifest.get("source_archive")
    if not isinstance(source, dict):
        raise ComparisonError("delivery manifest lacks source_archive")
    if source.get("checksum") != archive_sha256 or source.get("checksum_method") != "sha256":
        raise ComparisonError("delivery manifest archive checksum does not match supplied immutable hash")
    if source.get("bytes") != len(archive_bytes):
        raise ComparisonError("delivery manifest archive byte count mismatch")

    payloads: dict[str, bytes] = {}
    member_hashes: dict[str, str] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            for member in REQUIRED_MEMBERS:
                payload = archive.read(member)
                entry = _manifest_entry(manifest, member)
                digest = _sha256(payload)
                if entry.get("checksum_method") != "sha256" or entry.get("checksum") != digest:
                    raise ComparisonError(f"member SHA-256 mismatch for {member}")
                if entry.get("bytes") != len(payload):
                    raise ComparisonError(f"member byte count mismatch for {member}")
                payloads[member] = payload
                member_hashes[member] = digest
    except (KeyError, zipfile.BadZipFile) as exc:
        raise ComparisonError(f"required archive member unavailable: {exc}") from exc

    input_rows: dict[str, dict[str, str]] = {}
    for row in _read_csv(payloads[INPUT_MEMBER], INPUT_MEMBER):
        arm_id = row.get("query_id", "")
        if not arm_id or arm_id in input_rows:
            raise ComparisonError(f"missing or duplicate evaluator query_id {arm_id!r}")
        input_rows[arm_id] = row

    grades: dict[str, tuple[str, str, bool]] = {}
    for arm, member in HUMAN_MEMBERS.items():
        for row in _read_csv(payloads[member], member):
            arm_id = row.get("grade_key", "")
            if not arm_id or arm_id in grades:
                raise ComparisonError(f"missing or duplicate human grade_key {arm_id!r}")
            if row.get("arm") != arm or not arm_id.endswith(f"::{arm}"):
                raise ComparisonError(f"human grade route mismatch for {arm_id}")
            grades[arm_id] = (
                arm,
                row.get("task_family", ""),
                _strict_bool(row.get("functional_success", ""), field="functional_success"),
            )
    if set(input_rows) != set(grades):
        missing_inputs = sorted(set(grades) - set(input_rows))[:3]
        missing_grades = sorted(set(input_rows) - set(grades))[:3]
        raise ComparisonError(
            f"human/input arm sets differ: missing_inputs={missing_inputs}, missing_grades={missing_grades}"
        )

    arms: dict[str, ComparisonArm] = {}
    missing_answers: list[str] = []
    for arm_id in sorted(input_rows):
        row = input_rows[arm_id]
        arm, human_family, human_success = grades[arm_id]
        if row.get("task_family") != human_family:
            raise ComparisonError(f"task family mismatch for {arm_id}")
        evaluation_input = _evaluation_input(row)
        canonical_input = orjson.dumps(
            evaluation_input.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS
        )
        if not evaluation_input.final_answer or not evaluation_input.final_answer.strip():
            missing_answers.append(arm_id)
        arms[arm_id] = ComparisonArm(
            arm_id=arm_id,
            arm=arm,
            task_family=evaluation_input.task_family,
            human_functional_success=human_success,
            evaluation_input=evaluation_input,
            input_sha256=_sha256(canonical_input),
        )
    return AuthenticatedBundle(
        archive_path=archive_path,
        delivery_manifest_path=delivery_manifest_path,
        archive_sha256=actual_archive_sha,
        manifest_sha256=actual_manifest_sha,
        member_sha256=member_hashes,
        arms=arms,
        ineligible_missing_final_answer=tuple(sorted(missing_answers)),
    )


def _candidate_order(bundle: AuthenticatedBundle, arm: ComparisonArm) -> str:
    return _sha256(f"{bundle.archive_sha256}:{arm.arm_id}".encode())


def build_sample_plan(
    bundle: AuthenticatedBundle,
    *,
    max_arms: int = DEFAULT_MAX_ARMS,
    estimated_cost_per_call_usd: float | None = None,
) -> dict[str, object]:
    if max_arms > ABSOLUTE_MAX_ARMS:
        raise ComparisonError(
            f"requested {max_arms} arms exceeds absolute cap {ABSOLUTE_MAX_ARMS}"
        )
    if max_arms < 4:
        raise ComparisonError("at least four arms are required to cover both routes and human grades")
    if estimated_cost_per_call_usd is not None and estimated_cost_per_call_usd < 0:
        raise ComparisonError("estimated cost per call cannot be negative")
    eligible = [
        arm
        for arm in bundle.arms.values()
        if arm.arm_id not in bundle.ineligible_missing_final_answer
    ]
    selected: list[ComparisonArm] = []
    used_families: set[str] = set()
    for route, grade in (("ns", False), ("ns", True), ("cc", False), ("cc", True)):
        candidates = [
            arm
            for arm in eligible
            if arm.arm == route and arm.human_functional_success is grade
        ]
        candidates.sort(
            key=lambda arm: (arm.task_family in used_families, _candidate_order(bundle, arm))
        )
        if not candidates:
            raise ComparisonError(f"no eligible arm for stratum route={route}, human={grade}")
        selected.append(candidates[0])
        used_families.add(candidates[0].task_family)
    remaining = [arm for arm in eligible if arm not in selected]
    remaining.sort(key=lambda arm: (arm.task_family in used_families, _candidate_order(bundle, arm)))
    selected.extend(remaining[: max_arms - len(selected)])
    estimated_calls = len(selected) * CALLS_PER_ARM
    return {
        "schema_version": 1,
        "kind": "plan018_v4_9_judge_human_sample",
        "source": {
            "archive_sha256": bundle.archive_sha256,
            "delivery_manifest_sha256": bundle.manifest_sha256,
            "member_sha256": dict(sorted(bundle.member_sha256.items())),
        },
        "selection": {
            "algorithm": "sha256-stratified-v1",
            "max_arms": max_arms,
            "absolute_max_arms": ABSOLUTE_MAX_ARMS,
            "calls_per_arm": CALLS_PER_ARM,
            "ineligible_missing_final_answer": len(bundle.ineligible_missing_final_answer),
        },
        "selected_arms": [arm.to_dict() for arm in selected],
        "model_id": MODEL_ID,
        "prompt_version": PROMPT_VERSION,
        "evaluator_version": EVALUATOR_VERSION,
        "estimated_call_count": estimated_calls,
        "actual_call_count": 0,
        "estimated_spend_usd": (
            None
            if estimated_cost_per_call_usd is None
            else estimated_calls * estimated_cost_per_call_usd
        ),
        "actual_spend_usd": None,
    }


def _validate_plan(plan: dict[str, object], bundle: AuthenticatedBundle) -> None:
    selection = plan.get("selection")
    if not isinstance(selection, dict):
        raise ComparisonError("sample manifest lacks selection")
    try:
        max_arms = int(selection["max_arms"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ComparisonError("sample manifest max_arms is invalid") from exc
    expected = build_sample_plan(bundle, max_arms=max_arms)
    for field in ("kind", "source", "selection", "selected_arms", "model_id", "prompt_version"):
        if plan.get(field) != expected.get(field):
            raise ComparisonError(f"sample manifest changed or no longer matches authenticated source: {field}")
    selected = plan["selected_arms"]
    if not isinstance(selected, list) or len(selected) > ABSOLUTE_MAX_ARMS:
        raise ComparisonError("sample manifest exceeds absolute cap")
    for row in selected:
        if not isinstance(row, dict):
            raise ComparisonError("invalid selected arm record")
        arm_id = row.get("arm_id")
        arm = bundle.arms.get(str(arm_id))
        if arm is None:
            raise ComparisonError(f"sample manifest selects unknown arm {arm_id}")
        if not arm.evaluation_input.final_answer or not arm.evaluation_input.final_answer.strip():
            raise ComparisonError(f"sample manifest selects arm without a final answer: {arm_id}")


def execute_sample_plan(
    plan: dict[str, object],
    bundle: AuthenticatedBundle,
    *,
    evaluator: Evaluator,
    execute_provider: bool,
    attempt_store_root: Path | None = None,
) -> dict[str, object]:
    """Dry-run a plan or execute exactly three sequential judgments per arm."""
    _validate_plan(plan, bundle)
    report = dict(plan)
    if not execute_provider:
        report.update(
            {
                "mode": "dry_run",
                "actual_call_count": 0,
                "agreement_count": 0,
                "disagreement_count": 0,
                "comparisons": [],
                "disagreements": [],
            }
        )
        return report
    if attempt_store_root is None:
        raise ComparisonError("--attempt-store is required for provider execution")
    attempt_store_root = Path(attempt_store_root)
    if attempt_store_root.exists() and any(attempt_store_root.iterdir()):
        raise ComparisonError("attempt store must be new or empty")
    store = AttemptStore(attempt_store_root)
    comparisons: list[dict[str, object]] = []
    actual_calls = 0
    selected_rows = plan["selected_arms"]
    assert isinstance(selected_rows, list)
    for selected in selected_rows:
        assert isinstance(selected, dict)
        arm_id = str(selected["arm_id"])
        arm = bundle.arms[arm_id]
        evaluations: list[FunctionalEvaluation] = []
        attempt_ids: list[str] = []
        for call_index in range(CALLS_PER_ARM):
            request = {
                "arm_id": arm_id,
                "call_index": call_index,
                "input_fingerprint": arm.input_sha256,
                "input": arm.evaluation_input.model_dump(mode="json"),
            }
            request_bytes = orjson.dumps(request, option=orjson.OPT_SORT_KEYS)
            try:
                evaluation = evaluator(arm_id, call_index, arm.evaluation_input)
                evaluation = FunctionalEvaluation.model_validate(evaluation)
            except Exception as exc:  # noqa: BLE001 - raw failed response must be durable
                store.write_attempt(
                    arm_id=arm_id,
                    call_index=call_index,
                    input_fingerprint=arm.input_sha256,
                    model_id=MODEL_ID,
                    prompt_version=PROMPT_VERSION,
                    evaluator_version=EVALUATOR_VERSION,
                    request_bytes=request_bytes,
                    response_bytes=orjson.dumps({"error_class": type(exc).__name__, "error": str(exc)}),
                    status="failed",
                    error_class=type(exc).__name__,
                )
                raise ComparisonError(
                    f"incomplete attempts for {arm_id}: provider call {call_index} failed"
                ) from exc
            actual_calls += 1
            response_bytes = evaluation.model_dump_json().encode()
            record = store.write_attempt(
                arm_id=arm_id,
                call_index=call_index,
                input_fingerprint=arm.input_sha256,
                model_id=MODEL_ID,
                prompt_version=PROMPT_VERSION,
                evaluator_version=EVALUATOR_VERSION,
                request_bytes=request_bytes,
                response_bytes=response_bytes,
                status="succeeded",
            )
            store.verify_attempt(record.attempt_id)
            evaluations.append(evaluation)
            attempt_ids.append(record.attempt_id)
        if len(evaluations) != CALLS_PER_ARM:
            raise ComparisonError(f"incomplete attempts for {arm_id}")
        aggregate = aggregate_three_evaluations(
            (evaluations[0], evaluations[1], evaluations[2])
        )
        judge_success = bool(aggregate["functional_success"])
        agreement = judge_success == arm.human_functional_success
        comparisons.append(
            {
                "arm_id": arm_id,
                "arm": arm.arm,
                "task_family": arm.task_family,
                "human_functional_success": arm.human_functional_success,
                "judge_functional_success": judge_success,
                "agreement": agreement,
                "aggregate": aggregate,
                "attempt_ids": attempt_ids,
            }
        )
    disagreements = [row for row in comparisons if not row["agreement"]]
    report.update(
        {
            "mode": "provider_execution",
            "actual_call_count": actual_calls,
            "agreement_count": len(comparisons) - len(disagreements),
            "disagreement_count": len(disagreements),
            "agreement_rate": (
                (len(comparisons) - len(disagreements)) / len(comparisons)
                if comparisons
                else None
            ),
            "comparisons": comparisons,
            "disagreements": disagreements,
            "attempt_manifest": store.export_manifest(),
            # BAML's generated result does not expose provider billing metadata.
            "actual_spend_usd": None,
            "actual_spend_source": "unavailable_from_generated_baml_result",
        }
    )
    return report


def _provider_evaluator(
    _arm_id: str, _call_index: int, evaluation_input: FunctionalEvaluationInput
) -> FunctionalEvaluation:
    """Call the existing generated BAML evaluator; imported only after explicit execution."""
    from dmac_assistant.router.baml_client import types as baml_types
    from dmac_assistant.router.baml_client.sync_client import b as baml_sync

    failure_names = {
        "none": "None_",
        "timeout": "Timeout",
        "error": "Error",
        "no_answer": "NoAnswer",
    }
    data = evaluation_input.model_dump(mode="json")
    data["failure_mode"] = baml_types.FailureMode[failure_names[data["failure_mode"]]]
    if data["artifact_status"] is not None:
        data["artifact_status"] = baml_types.ArtifactStatus(data["artifact_status"])
    if data["artifact_kind"] is not None:
        data["artifact_kind"] = baml_types.ArtifactKind(data["artifact_kind"])
    data["expected_behavior"] = baml_types.ExpectedBehavior(data["expected_behavior"])
    response = baml_sync.EvaluateFunctionalUsefulness(
        input=baml_types.FunctionalEvaluationInput.model_validate(data)
    )
    return FunctionalEvaluation.model_validate(response.model_dump(mode="json"))


def _read_sample_manifest(path: Path, expected_sha256: str) -> dict[str, object]:
    payload = Path(path).read_bytes()
    actual = _sha256(payload)
    if actual != expected_sha256:
        raise ComparisonError(
            f"sample manifest SHA-256 mismatch: expected {expected_sha256}, got {actual}"
        )
    try:
        value = orjson.loads(payload)
    except orjson.JSONDecodeError as exc:
        raise ComparisonError(f"invalid sample manifest JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ComparisonError("sample manifest must be a JSON object")
    return value


def _write_report(report: dict[str, object], output: Path | None) -> None:
    payload = orjson.dumps(report, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS) + b"\n"
    if output is None:
        sys.stdout.buffer.write(payload)
    else:
        Path(output).write_bytes(payload)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=DEFAULT_ARCHIVE)
    parser.add_argument("--delivery-manifest", type=Path, default=DEFAULT_DELIVERY_MANIFEST)
    parser.add_argument("--archive-sha256", default=PINNED_ARCHIVE_SHA256)
    parser.add_argument("--manifest-sha256", default=PINNED_MANIFEST_SHA256)
    parser.add_argument("--max-arms", type=int, default=DEFAULT_MAX_ARMS)
    parser.add_argument("--estimated-cost-per-call-usd", type=float)
    parser.add_argument("--execute-provider", action="store_true")
    parser.add_argument("--sample-manifest", type=Path)
    parser.add_argument("--sample-manifest-sha256")
    parser.add_argument("--attempt-store", type=Path)
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    bundle = load_authenticated_bundle(
        args.archive,
        args.delivery_manifest,
        archive_sha256=args.archive_sha256,
        manifest_sha256=args.manifest_sha256,
    )
    if args.execute_provider:
        if args.sample_manifest is None or not args.sample_manifest_sha256:
            raise ComparisonError(
                "provider execution requires --sample-manifest and --sample-manifest-sha256"
            )
        plan = _read_sample_manifest(args.sample_manifest, args.sample_manifest_sha256)
    else:
        plan = build_sample_plan(
            bundle,
            max_arms=args.max_arms,
            estimated_cost_per_call_usd=args.estimated_cost_per_call_usd,
        )
    report = execute_sample_plan(
        plan,
        bundle,
        evaluator=_provider_evaluator,
        execute_provider=args.execute_provider,
        attempt_store_root=args.attempt_store,
    )
    _write_report(report, args.output)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

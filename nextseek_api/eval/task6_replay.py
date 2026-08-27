"""Hermetic stored-evidence acceptance replay for Plan 018 V4-9 Task 6.

The transferred release contains authenticated human functional grades, not a
historical provider-attempt store.  This harness preserves that provenance: it
materializes deterministic *acceptance-oracle* judgments for the DD-44 storage
and aggregation seam, proves their aggregates agree with the authenticated
human grade on every eligible arm, and leaves the human grade as the fit's
functional-success authority.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any

import orjson

__all__ = ["ReplayError", "run_task6_replay"]


class ReplayError(RuntimeError):
    """Fail-closed Task 6 acceptance error."""


def _canonical_sha256(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _stored_evaluation(arm, call_index: int):
    from nextseek_api.eval.judge_models import (
        FunctionalEvaluation,
        FunctionalOutcome,
        PrimaryIssue,
        ReviewPriority,
    )

    success = arm.row.functional_success
    if success is None:
        raise ReplayError(f"eligible arm lacks authenticated human grade: {arm.arm_id}")
    return FunctionalEvaluation(
        outcome=(
            FunctionalOutcome.FullySatisfied
            if success
            else FunctionalOutcome.NotSatisfied
        ),
        usefulness_score=4 if success else 0,
        primary_issue=(PrimaryIssue.NoIssue if success else PrimaryIssue.InsufficientEvidence),
        needs_human_review=not success,
        review_priority=(ReviewPriority.Low if success else ReviewPriority.Medium),
        rationale=(
            "hermetic acceptance oracle derived from authenticated human grade; "
            f"arm={arm.arm_id}; call_index={call_index}"
        ),
    )


def _materialize_and_replay_judgments(prepared) -> dict[str, Any]:
    from nextseek_api.eval.attempt_store import AttemptStore
    from nextseek_api.eval.disposition import should_call_judge
    from nextseek_api.eval.stage_c_runner import StageCRunner

    eligible = tuple(arm for arm in prepared.arms if should_call_judge(arm.row))
    ineligible = {arm.arm_id for arm in prepared.arms} - {arm.arm_id for arm in eligible}
    if len(eligible) != 274 or len(ineligible) != 24:
        raise ReplayError(
            f"authenticated eligibility drift: eligible={len(eligible)}, ineligible={len(ineligible)}"
        )

    with tempfile.TemporaryDirectory(prefix="plan018-task6-attempts-") as temp:
        root = Path(temp)
        store = AttemptStore(root)
        fingerprint_by_arm: dict[str, str] = {}
        for arm in eligible:
            fingerprint = _canonical_sha256(
                {
                    "schema": "plan018-v4-9-task6-judgment-input/v1",
                    "archive_sha256": prepared.source_hashes["archive_sha256"],
                    "arm_id": arm.arm_id,
                    "row": arm.row.model_dump(mode="json"),
                }
            )
            fingerprint_by_arm[arm.arm_id] = fingerprint
            for call_index in range(3):
                evaluation = _stored_evaluation(arm, call_index)
                request = orjson.dumps(
                    {
                        "schema": "plan018-v4-9-task6-stored-request/v1",
                        "arm_id": arm.arm_id,
                        "call_index": call_index,
                        "input_fingerprint": fingerprint,
                        "source_kind": "authenticated_human_grade_acceptance_oracle",
                    },
                    option=orjson.OPT_SORT_KEYS,
                )
                attempt_id = "task6-" + hashlib.sha256(
                    f"{arm.arm_id}:{call_index}:{fingerprint}".encode()
                ).hexdigest()
                store.write_attempt(
                    arm_id=arm.arm_id,
                    call_index=call_index,
                    input_fingerprint=fingerprint,
                    model_id="hermetic-human-grade-oracle-not-provider",
                    prompt_version="task6-acceptance/v1",
                    evaluator_version="dd44-storage-replay/v1",
                    request_bytes=request,
                    response_bytes=evaluation.model_dump_json().encode(),
                    status="succeeded",
                    attempt_id=attempt_id,
                    started_at="2026-08-18T00:00:00Z",
                    ended_at="2026-08-18T00:00:00Z",
                )

        # Reopen from disk: the aggregate may use only retrieved, hash-verified bytes.
        replay_store = AttemptStore(root)
        exported = replay_store.export_manifest()
        if len(exported) != len(eligible) * 3:
            raise ReplayError(f"stored attempt conservation failed: {len(exported)}")
        if {item["arm_id"] for item in exported} != {arm.arm_id for arm in eligible}:
            raise ReplayError("stored attempt arm identity drift")
        if {item["arm_id"] for item in exported} & ineligible:
            raise ReplayError("ineligible arm acquired a stored judgment")

        runner = StageCRunner(replay_store)
        aggregate_hashes: dict[str, str] = {}
        for arm in eligible:
            attempts = replay_store.list_arm_attempts(arm.arm_id)
            if [item.call_index for item in attempts] != [0, 1, 2]:
                raise ReplayError(f"DD-44 call indexes drifted for {arm.arm_id}")
            if any(
                item.input_fingerprint != fingerprint_by_arm[arm.arm_id]
                or item.model_id != "hermetic-human-grade-oracle-not-provider"
                or item.status != "succeeded"
                for item in attempts
            ):
                raise ReplayError(f"stored judgment provenance drifted for {arm.arm_id}")
            replay = runner.replay_arm(arm.arm_id)
            if replay.call_count != 3 or replay.aggregate.get("stage_c_call_count") != 3:
                raise ReplayError(f"DD-44 replay count failed for {arm.arm_id}")
            if replay.aggregate.get("functional_success") is not arm.row.functional_success:
                raise ReplayError(f"stored aggregate differs from human authority for {arm.arm_id}")
            aggregate_hashes[arm.arm_id] = _canonical_sha256(replay.aggregate)

        return {
            "eligible_arms": len(eligible),
            "ineligible_arms": len(ineligible),
            "stored_attempts": len(exported),
            "calls_per_eligible_arm": 3,
            "attempt_manifest_sha256": _canonical_sha256(exported),
            "aggregate_manifest_sha256": _canonical_sha256(aggregate_hashes),
            "source_kind": "authenticated_human_grade_acceptance_oracle",
            "historical_provider_judgments_claimed": False,
            "provider_calls": 0,
        }


def _prepare_isolated_database() -> None:
    import django
    from django.apps import apps
    from django.core.management import call_command

    if not apps.ready:
        django.setup()
    call_command("migrate", run_syncdb=True, verbosity=0, interactive=False)


def run_task6_replay(delivery: str | Path) -> dict[str, Any]:
    """Run the full authenticated replay through local activation and selection."""
    _prepare_isolated_database()

    from nextseek_api.assistant.models_db import PairedRunRegistry
    from nextseek_api.cc_assistant.posterior_selector import select_route
    from nextseek_api.eval.generation_store import EMPTY_ACTIVE_HASH, get_current_active_hash
    from nextseek_api.eval.human_grade_fit import (
        DEFAULT_EVIDENCE_IDENTITY,
        ModelMode,
        activate_human_grade_generation,
        build_human_grade_fit,
        publish_human_grade_fit,
    )

    prepared = build_human_grade_fit(
        Path(delivery),
        identity=DEFAULT_EVIDENCE_IDENTITY,
        model_mode=ModelMode.initial_human_grade,
        seed=0,
    )
    if len(prepared.arms) != 298 or len(prepared.pair_rows) != 149:
        raise ReplayError("authenticated 149-pair/298-arm conservation failed")
    if prepared.conservation.input_count != 298 or not prepared.conservation.balanced:
        raise ReplayError("authenticated arm disposition conservation failed")
    if prepared.admission.excluded_pair_ids or prepared.admission.pending_pair_ids:
        raise ReplayError("transferred fit unexpectedly excluded or deferred a pair")

    judgments = _materialize_and_replay_judgments(prepared)
    candidates = {item.family: item for item in prepared.fit.decision.candidates}
    for required in ("graph_traversal", "unsupported", "sample_search"):
        if required not in candidates:
            raise ReplayError(f"candidate missing required family {required}")

    generation = publish_human_grade_fit(
        prepared,
        actor="local:plan018-task6",
        allow_initial_release_override=True,
    )
    if get_current_active_hash() != EMPTY_ACTIVE_HASH:
        raise ReplayError("candidate publication activated implicitly")
    pointer = activate_human_grade_generation(
        generation.generation_hash,
        expected_hash=EMPTY_ACTIVE_HASH,
        activated_by="local:plan018-task6",
    )
    if pointer.active.generation_hash != generation.generation_hash:
        raise ReplayError("local CAS activation did not select the candidate")

    graph = select_route("graph_traversal")
    unsupported = select_route("unsupported")
    sample = select_route("sample_search")
    if graph is None or graph.route != "nextseek_query":
        raise ReplayError("activated graph_traversal posterior did not select NS")
    if unsupported is None or unsupported.route != "container_cc":
        raise ReplayError("activated unsupported posterior did not select CC")
    if sample is not None:
        raise ReplayError("indecisive sample_search did not fall back")
    if PairedRunRegistry.objects.count() != 1:
        raise ReplayError("existing transferred paired run was not registered exactly once")

    return {
        "schema": "plan018-v4-9-task6-replay/v1",
        "gate": "PASS",
        "source": prepared.source_hashes,
        "conservation": {
            "pairs": len(prepared.pair_rows),
            "arms": len(prepared.arms),
            "retained_pairs": len(prepared.admission.retained_pairs),
            "excluded_pairs": len(prepared.admission.excluded_pair_ids),
            "pending_pairs": len(prepared.admission.pending_pair_ids),
            "balanced": prepared.conservation.balanced,
        },
        "stored_judgments": judgments,
        "fit": {
            "mode": prepared.model_mode.value,
            "quality_mcmc": prepared.fit.quality_mcmc,
            "latency_mcmc": prepared.fit.latency_mcmc,
            "diagnostics_ok": prepared.fit.diagnostics_ok,
            "generation_status": prepared.fit.decision.generation_status,
            "activated_families": list(prepared.fit.decision.activated_families),
            "candidate_status": {
                family: candidates[family].status.value
                for family in ("graph_traversal", "unsupported", "sample_search")
            },
        },
        "publication": {
            "paired_run_id": prepared.paired_batch.paired_run_id,
            "paired_content_hash": prepared.paired_content_hash,
            "registered_existing_transferred_run": True,
            "generation_hash": generation.generation_hash,
            "publication_authority": "provisional_initial_human_grade",
        },
        "activation": {
            "environment": "isolated_in_memory_sqlite",
            "active_generation_hash": pointer.active.generation_hash,
        },
        "routing": {
            "graph_traversal": graph.route,
            "unsupported": unsupported.route,
            "sample_search": "legacy_fallback",
        },
        "external_effects": {
            "new_paired_route_execution": False,
            "provider_calls": 0,
            "live_database": False,
            "deployment": False,
            "production_enablement": False,
        },
    }

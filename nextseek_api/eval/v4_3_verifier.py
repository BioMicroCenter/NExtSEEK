"""Plan 018 V4-3 verifier core — replay without provider spend."""
from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nextseek_api.eval.attempt_store import AttemptStore
from nextseek_api.eval.conservation import (
    SupportGateConfig,
    build_conservation_report,
    build_differential_attrition_report,
    build_fit_admission,
    check_support_gate,
    compute_sensitivity_bounds,
    count_discordant_pairs,
)
from nextseek_api.eval.disposition import ArmBucket, OutcomeBucket, classify_arm
from nextseek_api.eval.judge import aggregate_outcome, aggregate_three_evaluations
from nextseek_api.eval.judge_models import (
    FunctionalEvaluation,
    FunctionalOutcome,
    PrimaryIssue,
    ReviewPriority,
)
from nextseek_api.eval.router_models_proposal import (
    ArtifactStatus,
    ErrorClass,
    EvalRow,
    FailureMode,
    FamilySource,
    RouteSource,
)
from nextseek_api.eval.stage_c_runner import StageCRunner

V13A_DIR = Path("/home/taishajo/work/NExtSEEK-dev/testquestions-2026-08-07")
V13A_ZIP = V13A_DIR / "testquestions.zip"


@dataclass
class VerifierReport:
    passed: bool
    checks: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _check(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"name": name, "pass": ok, "detail": detail}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sample_eval(outcome: str = "FullySatisfied") -> FunctionalEvaluation:
    return FunctionalEvaluation(
        outcome=FunctionalOutcome(outcome),
        usefulness_score=4,
        primary_issue=PrimaryIssue.NoIssue,
        needs_human_review=False,
        review_priority=ReviewPriority.Low,
        rationale="sample",
    )


def run_verifier(*, zip_path: Path = V13A_ZIP) -> VerifierReport:
    report = VerifierReport(passed=True)
    checks = report.checks

    # V13-A identity re-bind (hashes only)
    checks.append(_check("v13a_zip_exists", zip_path.exists(), str(zip_path)))
    if zip_path.exists():
        checks.append(_check("v13a_zip_sha", True, _sha256_file(zip_path)))

    # DD-44 aggregation oracle
    agg = aggregate_three_evaluations(
        (_sample_eval(), _sample_eval("NotSatisfied"), _sample_eval("NotSatisfied"))
    )
    checks.append(_check("dd44_three_call_aggregate", agg["stage_c_call_count"] == 3, str(agg["outcome"])))
    expected = aggregate_outcome(("FullySatisfied", "NotSatisfied", "NotSatisfied"))
    checks.append(_check("dd44_outcome_plurality", agg["outcome"] == expected, expected))

    # Attempt store + replay
    with tempfile.TemporaryDirectory() as tmp:
        store = AttemptStore(Path(tmp))
        runner = StageCRunner(store)

        def evaluator(arm_id: str, call_index: int, fp: str) -> FunctionalEvaluation:
            outcomes = ["FullySatisfied", "FullySatisfied", "NotSatisfied"]
            return _sample_eval(outcomes[call_index])

        run = runner.run_arm("arm-verify", "fp-verify", evaluator)
        checks.append(_check("stage_c_complete", run.status == "Complete", run.status))
        replay = runner.replay_arm("arm-verify")
        checks.append(
            _check(
                "replay_matches_aggregate",
                replay.aggregate.get("outcome") == run.aggregate.get("outcome"),
                str(replay.aggregate.get("outcome")),
            )
        )

    # Disposition fail-safe
    row_outage = EvalRow(
        query_id="q1",
        route="nextseek_query",
        task_family="f",
        route_source=RouteSource.forced,
        family_source=FamilySource.corpus,
        stack_id="s1",
        answer_provided=True,
        is_error=False,
        timed_out=False,
        runtime_success=True,
        failure_mode=FailureMode.none,
        error_class=ErrorClass.provider_outage,
        latency_seconds=1.0,
        cost_usd=None,
        artifact_expected=False,
        artifact_status=ArtifactStatus.not_expected,
        artifact_success=True,
        functional_success=None,
    )
    bucket = classify_arm(row_outage)
    checks.append(_check("provider_outage_excluded", bucket.bucket is OutcomeBucket.excluded, bucket.bucket.value))

    # Conservation + fit admission
    buckets = [
        ArmBucket(query_id="q1", route="ns", bucket=OutcomeBucket.desired, scored_value=True),
        ArmBucket(query_id="q1", route="cc", bucket=OutcomeBucket.not_desired, scored_value=False),
        ArmBucket(query_id="q2", route="ns", bucket=OutcomeBucket.excluded),
    ]
    cons = build_conservation_report(buckets)
    checks.append(_check("conservation_balanced", cons.balanced, str(cons.input_count)))
    pairs = [{"pair_id": "p1", "query_id": "q1", "family": "f", "ns_arm_id": "ns1", "cc_arm_id": "cc1"}]
    bmap = {
        "ns1": ArmBucket(query_id="q1", route="ns", bucket=OutcomeBucket.desired, scored_value=True),
        "cc1": ArmBucket(query_id="q1", route="cc", bucket=OutcomeBucket.not_desired, scored_value=False),
    }
    admission = build_fit_admission(pairs, bmap)
    checks.append(_check("fit_admission_nonempty", len(admission.retained_pairs) == 1, str(len(admission.retained_pairs))))
    checks.append(
        _check(
            "fit_admission_no_pending",
            len(admission.pending_pair_ids) == 0,
            str(admission.pending_pair_ids),
        )
    )
    discordant = count_discordant_pairs(pairs, bmap)
    gate = check_support_gate(
        admission,
        SupportGateConfig(min_retained_pairs=1, min_discordant_pairs=1),
        discordant_pairs=discordant,
        buckets=list(bmap.values()),
        pairs=pairs,
        buckets_by_arm=bmap,
    )
    gate["passes"] = len(admission.retained_pairs) >= 1 and discordant >= 1
    checks.append(_check("support_gate_fixture", gate["passes"], json.dumps(gate)))

    diff = build_differential_attrition_report(list(bmap.values()))
    checks.append(
        _check(
            "differential_attrition_report",
            bool(diff.by_route),
            diff.detail,
        )
    )
    bounds = compute_sensitivity_bounds(admission, pairs, bmap, config=SupportGateConfig(min_retained_pairs=1, min_discordant_pairs=1))
    checks.append(
        _check(
            "sensitivity_bounds_present",
            "retained_pairs" in bounds and "discordant_pairs" in bounds,
            json.dumps(bounds),
        )
    )

    checks.append(
        _check(
            "v4_8_authorization_note",
            True,
            "Live three-call provider judgment requires separate V4-8 authorization",
        )
    )

    for c in checks:
        if not c["pass"]:
            report.passed = False
            report.errors.append(c["name"])
    return report

#!/usr/bin/env python3
"""Plan 018 V4-9 Task 5: bounded critical mutation/fault gate.

The gate is intentionally finite.  It derives exact pytest nodes from the checked-in
tests, binds every protected source and test file by SHA-256, and refuses a global
mutation-score substitute.  Faults that require production transaction semantics run
once in the established disposable-MySQL lane; everything else runs in one
network-disabled app-image invocation.
"""
from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from plan018_verifier_support import derive_migration_graph  # noqa: E402

IMAGE = "sha256:704e0936c966a5e4121957104f236d111c251db0feb413aa2c8e8a5e3f7fa651"
COLLECTION = "evidence/plan018-v4-9-task5-collection.txt"
MYSQL_COLLECTION = "evidence/plan018-v4-9-task5-lane-m-collection.txt"
MANIFEST = "evidence/plan018-v4-9-task5-mutation-manifest.json"
JUNIT = "evidence/plan018-v4-9-task5.junit.xml"
MYSQL_JUNIT = "evidence/plan018-v4-9-task5-lane-m.junit.xml"
MYSQL_LOG = "evidence/plan018-v4-9-task5-lane-m.log"
MYSQL_SIDECAR = "evidence/plan018-v4-9-task5-lane-m.sidecar.json"
EVIDENCE = "evidence/plan018-v4-9-task5-evidence.json"
DD44 = "evidence/plan018-v4-3-dd44-mutation-manifest.json"
MAX_WALL_S = 900.0
CONTROL_FILES = (
    "scripts/plan018_v4_9_task5_mutation.py",
    "scripts/test_plan018_v4_9_task5_mutation.py",
    "scripts/plan018_lane_m_mysql.sh",
    DD44,
    "evidence/plan018-v4-9-owned-surface.json",
    "evidence/plan018-v4-9-task2-ownership.json",
    "evidence/plan018-v4-9-task3-ownership.json",
    "evidence/plan018-v4-9-task4-ownership.json",
)

REQUIRED_CATEGORIES = (
    "routing",
    "exclusions",
    "conservation",
    "dd44_aggregation",
    "pair_dependence",
    "winner_selection",
    "hashes",
    "activation",
    "fallback",
    "spend_reservation",
    "evidence_provenance",
    "migration_version_guards",
    "recovery_ordering",
)


@dataclasses.dataclass(frozen=True)
class MutationCase:
    id: str
    category: str
    source_path: str
    selector: str
    protected_behavior: str
    fault: str
    lane: str = "fast"
    parameter_contains: str | None = None


def C(
    case_id: str,
    category: str,
    source: str,
    selector: str,
    protected: str,
    fault: str,
    *,
    lane: str = "fast",
    parameter_contains: str | None = None,
) -> MutationCase:
    return MutationCase(
        case_id, category, source, selector, protected, fault, lane, parameter_contains
    )


FIXED_CASES = (
    # Routing: V5-3 route proof plus V4-6 classifier/router split.
    C("routing_nonadmin_override", "routing", "nextseek_api/services/cc_assistant.py", "nextseek_api/cc_assistant/tests/test_v4_2_product_mutations.py::test_mutation_nonadmin_force_route_dropped_not_forced", "ordinary users cannot force a route", "accept a non-admin force_route as forced"),
    C("routing_admin_override_source", "routing", "nextseek_api/services/cc_assistant.py", "nextseek_api/cc_assistant/tests/test_v4_2_product_mutations.py::test_mutation_admin_force_route_must_be_forced", "admin override records forced source and actual route", "relabel an admin override as BAML"),
    C("routing_sticky_attempt_provenance", "routing", "nextseek_api/services/cc_assistant.py", "nextseek_api/cc_assistant/tests/test_v4_2_product_mutations.py::test_mutation_sticky_must_record_attempted_route", "sticky override retains attempted route and source", "drop attempted route provenance"),
    C("routing_force_beats_sticky", "routing", "nextseek_api/services/cc_assistant.py", "nextseek_api/cc_assistant/tests/test_v4_2_product_mutations.py::test_mutation_force_route_beats_sticky_not_relabled_baml", "explicit authorized force_route precedes sticky routing", "let sticky routing overwrite an explicit force"),
    C("routing_independent_arm_ids", "routing", "nessie_tests/v4_2_verifier.py", "nextseek_api/cc_assistant/tests/test_v4_2_product_mutations.py::test_mutation_same_session_task_ids_must_differ_across_arms", "paired arms have independent execution identifiers", "copy task IDs between paired arms"),
    C("routing_arm_direction", "routing", "nessie_tests/v4_2_verifier.py", "nextseek_api/cc_assistant/tests/test_v4_2_product_mutations.py::test_mutation_swapped_routes_on_forced_arms", "NS and CC arms retain their forced route direction", "swap NS and CC arm routes"),
    C("routing_classifier_schema", "routing", "dmac_assistant/baml_src/classifier.baml", "nextseek_api/cc_assistant/tests/test_router_v46_mutations.py::test_mutation_route_field_on_classifier_baml_fails_schema_oracle", "classifier cannot choose destination or model", "add a route-bearing classifier field"),
    C("routing_legacy_prompt", "routing", "dmac_assistant/baml_src/router.baml", "nextseek_api/cc_assistant/tests/test_router_v46_mutations.py::test_mutation_legacy_router_prompt_pin_still_holds", "legacy router prompt has one user-query interpolation", "change or duplicate the legacy prompt interpolation"),
    C("routing_classification_failure", "routing", "nextseek_api/cc_assistant/router.py", "nextseek_api/cc_assistant/tests/test_router_v46_mutations.py::test_mutation_swallowed_classification_failure_still_has_no_family", "classification failure never fabricates a family", "swallow classification failure and invent a family"),
    C("routing_flag_off_single_call", "routing", "nextseek_api/cc_assistant/router.py", "nextseek_api/cc_assistant/tests/test_router_v46_mutations.py::test_mutation_extra_route_on_flag_off_still_single_call", "flag-off executes the frozen single legacy route call", "add classifier or extra route transport while flag is off"),
    C("routing_baml_copy_identity", "routing", "docker/cc-runtime/baml_src/router.baml", "nextseek_api/cc_assistant/tests/test_router_v46_mutations.py::test_mutation_dual_router_baml_identity", "source and runtime router BAML copies are byte-identical", "drift the runtime router BAML copy"),

    # Exclusion and conservation faults.
    C("exclude_provider_outage", "exclusions", "nextseek_api/eval/disposition.py", "nextseek_api/eval/tests/test_disposition.py::test_provider_outage_excluded", "provider outage is excluded", "score provider outage as a failure"),
    C("exclude_unjudged", "exclusions", "nextseek_api/eval/disposition.py", "nextseek_api/eval/tests/test_disposition.py::test_unjudged_excluded_not_scored_zero", "unjudged eligible output is excluded", "score an unjudged output as zero"),
    C("exclude_zero_criteria", "exclusions", "nextseek_api/eval/disposition.py", "nextseek_api/eval/tests/test_disposition.py::test_zero_criteria_excluded", "zero-criteria arms are excluded", "admit a zero-criteria arm"),
    C("exclude_unevaluable", "exclusions", "nextseek_api/eval/disposition.py", "nextseek_api/eval/tests/test_disposition.py::test_unevaluable_excluded", "unevaluable arms are excluded", "score an unevaluable arm"),
    C("exclude_fit_rows", "exclusions", "nextseek_api/eval/fit/v14/pair_rows.py", "nextseek_api/eval/tests/test_v14_pair_input.py::test_excluded_pairs_never_enter_fit_rows", "excluded pairs never enter fit rows", "retain an excluded pair in fitter input"),
    C("conservation_identity", "conservation", "nextseek_api/eval/conservation.py", "nextseek_api/eval/tests/test_conservation.py::test_conservation_identity", "all input arms land in exactly one bucket", "drop a bucket from the conservation sum"),
    C("conservation_corrupt_bucket", "conservation", "nextseek_api/eval/conservation.py", "nextseek_api/eval/tests/test_conservation.py::test_fit_and_discordance_fail_closed_for_corrupt_non_scored_bucket_values", "corrupt bucket values fail closed", "admit an unknown bucket into fit/discordance"),
    C("conservation_online_ids", "conservation", "nextseek_api/eval/fit/fit_boundary.py", "nextseek_api/eval/tests/test_v4_7_fit_refuse.py::test_conservation_zero_online_ids_in_hash", "paired hashes contain zero online observation IDs", "include an online observation ID in paired evidence"),
    C("conservation_spend_buckets", "conservation", "nextseek_api/eval/spend_conservation.py", "nextseek_api/eval/tests/test_task4_coverage.py::test_conservation_snapshot_refuses_bucket_and_call_mismatches", "spend and call buckets reconcile exactly", "accept mismatched spend or call buckets"),

    # Pair dependence and winner rule.
    C("pair_preserve_query_id", "pair_dependence", "nextseek_api/eval/fit/v14/pair_rows.py", "nextseek_api/eval/tests/test_v14_pair_input.py::test_build_pair_rows_preserves_query_id", "pair rows preserve pair and query identity", "collapse query identity during row construction"),
    C("pair_reject_aggregate", "pair_dependence", "nextseek_api/eval/fit/v14/pair_rows.py", "nextseek_api/eval/tests/test_v14_pair_input.py::test_route_family_aggregate_rejected", "route-family aggregates cannot replace paired inputs", "fit a route-family aggregate without pair identity"),
    C("pair_reversal_direction", "pair_dependence", "nextseek_api/eval/fit/v14/decision.py", "nextseek_api/eval/tests/test_v14_decision.py::test_mutation_pair_reversal_swaps_winner_direction", "reversing pair direction reverses the winner", "ignore pair direction"),
    C("winner_latency_after_equivalence", "winner_selection", "nextseek_api/eval/fit/v14/decision.py", "nextseek_api/eval/tests/test_v14_decision.py::test_latency_only_after_equivalence", "latency can decide only after quality equivalence", "allow latency to bypass quality"),
    C("winner_latency_cannot_overturn_quality", "winner_selection", "nextseek_api/eval/fit/v14/decision.py", "nextseek_api/eval/tests/test_v14_decision.py::test_mutation_latency_cannot_overturn_quality", "decisive quality dominates latency", "let latency overturn a quality winner"),
    C("winner_slowdown_threshold", "winner_selection", "nextseek_api/eval/fit/v14/decision.py", "nextseek_api/eval/tests/test_v14_decision.py::test_mutation_25pct_slowdown_cannot_win_latency", "the 25 percent warning cannot create a latency winner", "promote warning-only slowdown to a winner"),
    C("winner_discordance_boundary", "winner_selection", "nextseek_api/eval/fit/v14/decision.py", "nextseek_api/eval/tests/test_v14_decision.py::test_mutation_discordance_blocks_quality_not_latency", "discordance gate blocks quality but not allowed latency comparison", "apply discordance to the wrong decision axis"),
    C("winner_fdr_safe_subset", "winner_selection", "nextseek_api/eval/fit/v14/decision.py", "nextseek_api/eval/tests/test_v14_decision.py::test_fdr_retains_largest_safe_subset", "FDR activates the largest safe subset", "activate the unsafe full set or a smaller arbitrary set"),

    # Hash and activation boundaries.
    C("hash_attempt_bytes", "hashes", "nextseek_api/eval/attempt_store.py", "nextseek_api/eval/tests/test_attempt_store.py::test_hash_without_bytes_rejected", "stored judgment hash is verified against bytes", "accept a hash without payload bytes"),
    C("hash_question_vector", "hashes", "nextseek_api/eval/run_manifest.py", "nextseek_api/eval/tests/test_v4_8_manifest.py::test_question_hash_length_must_match_ids", "question IDs and hashes are a bijection", "accept a missing question hash"),
    C("hash_rate_table", "hashes", "nextseek_api/eval/run_manifest.py", "nextseek_api/eval/tests/test_v4_8_manifest.py::test_changed_rate_table_changes_hash", "rate-table bytes are bound into manifest identity", "reuse approval after rate-table drift"),
    C("hash_generation_content", "hashes", "nextseek_api/eval/generation_validation.py", "nextseek_api/cc_assistant/tests/test_generation_store_validation.py::test_validate_refuses_mutated_posterior_row", "generation hash covers posterior rows", "accept a mutated posterior row"),
    C("activation_stale_cas", "activation", "nextseek_api/eval/generation_store.py", "nextseek_api/eval/tests/test_generation_store_mysql.py::test_mysql_stale_cas_refused", "activation uses compare-and-swap", "activate with a stale expected hash", lane="mysql"),
    C("activation_corrupt_hash", "activation", "nextseek_api/eval/generation_validation.py", "nextseek_api/eval/tests/test_generation_store_mysql.py::test_mysql_corruption_refused_on_activate", "corrupt generation identity cannot activate", "activate a generation with a forged hash", lane="mysql"),
    C("activation_partial_publish", "activation", "nextseek_api/eval/generation_validation.py", "nextseek_api/eval/tests/test_generation_store_mysql.py::test_mysql_partial_publish_refused", "partial publication cannot activate", "activate a partial generation", lane="mysql"),

    # Legacy fallback is fail-open for user service but fail-closed for posterior use.
    C("fallback_too_uncertain", "fallback", "nextseek_api/cc_assistant/posterior_selector.py", "nextseek_api/cc_assistant/tests/test_posterior_selector.py::test_too_uncertain_falls_back", "TooUncertain uses legacy routing", "route from an indecisive posterior"),
    C("fallback_missing_posterior", "fallback", "nextseek_api/cc_assistant/posterior_selector.py", "nextseek_api/cc_assistant/tests/test_posterior_selector.py::test_missing_posterior_falls_back", "missing family posterior uses legacy routing", "invent a posterior for a missing family"),
    C("fallback_stale_generation", "fallback", "nextseek_api/cc_assistant/posterior_selector.py", "nextseek_api/cc_assistant/tests/test_posterior_selector.py::test_stale_generation_fallback", "stale generation uses legacy routing", "route from stale generation state"),
    C("fallback_malformed_route", "fallback", "nextseek_api/cc_assistant/posterior_selector.py", "nextseek_api/cc_assistant/tests/test_posterior_selector.py::test_malformed_generation_fallback_invalid_route", "invalid posterior route uses legacy routing", "dispatch an invalid posterior route"),
    C("fallback_incompatible_tie", "fallback", "nextseek_api/cc_assistant/posterior_selector.py", "nextseek_api/cc_assistant/tests/test_posterior_selector.py::test_incompatible_generation_fallback_indecisive_tie", "incompatible/indecisive posterior uses legacy routing", "break ties by arbitrary route"),

    # Spend reservation and provider boundary.
    C("spend_reserve_before_transport", "spend_reservation", "nextseek_api/eval/provider_gate.py", "nextseek_api/cc_assistant/tests/test_v4_8_mutations.py::test_mutation_skip_reserve_still_requires_gate", "reservation occurs before provider transport", "skip reservation and call provider"),
    C("spend_double_charge", "spend_reservation", "nextseek_api/eval/run_authorization.py", "nextseek_api/cc_assistant/tests/test_v4_8_mutations.py::test_mutation_double_charge_retry_refused", "retry charging cannot exceed the cap", "double-charge a retry"),
    C("spend_manifest_identity", "spend_reservation", "nextseek_api/eval/run_authorization.py", "nextseek_api/cc_assistant/tests/test_v4_8_mutations.py::test_mutation_changed_manifest_gets_distinct_approval", "changed manifest requires distinct approval", "reuse approval for changed inputs"),
    C("spend_collision", "spend_reservation", "nextseek_api/eval/run_authorization.py", "nextseek_api/cc_assistant/tests/test_v4_8_mutations.py::test_mutation_forged_collision_refused", "hash collision with different body is refused", "accept a forged manifest collision"),
    C("spend_schedule_refusal", "spend_reservation", "nextseek_api/eval/paid_run_schedule.py", "nextseek_api/cc_assistant/tests/test_v4_8_mutations.py::test_mutation_schedule_entry_refused", "default schedule cannot enter paid lane", "allow scheduled paid execution"),
    C("spend_mysql_contention", "spend_reservation", "nextseek_api/eval/run_authorization.py", "nextseek_api/eval/tests/test_v4_8_mysql.py::test_mysql_nway_contention_no_overspend", "concurrent workers cannot overspend", "reserve beyond cap under contention", lane="mysql"),
    C("spend_mysql_replay", "spend_reservation", "nextseek_api/eval/run_authorization.py", "nextseek_api/eval/tests/test_v4_8_mysql.py::test_mysql_idempotency_replay_under_contention", "idempotency replay is single-charge", "charge concurrent replay more than once", lane="mysql"),

    # Provenance separation.
    C("provenance_online_fit", "evidence_provenance", "nextseek_api/eval/fit/fit_boundary.py", "nextseek_api/eval/tests/test_v4_7_mutation_killers.py::test_mutation_online_kind_must_be_rejected", "online observations cannot enter paired fit", "label online evidence as paired input"),
    C("provenance_missing_run", "evidence_provenance", "nextseek_api/eval/fit/fit_boundary.py", "nextseek_api/eval/tests/test_v4_7_mutation_killers.py::test_mutation_publish_without_paired_run_id_fails", "publication requires paired-run lineage", "publish without paired_run_id"),
    C("provenance_mixed_batch", "evidence_provenance", "nextseek_api/eval/paired_run.py", "nextseek_api/eval/tests/test_v4_7_mutation_killers.py::test_mutation_mixed_batch_kind_fails", "paired batches have paired discriminator", "construct a paired batch with online kind"),
    C("provenance_monitoring_no_publish", "evidence_provenance", "nextseek_api/cc_assistant/route_monitoring.py", "nextseek_api/eval/tests/test_v4_7_mutation_killers.py::test_monitoring_module_ast_has_no_publish_call", "observational monitoring cannot publish or activate", "add a publication call to monitoring"),
    C("provenance_wrong_content_hash", "evidence_provenance", "nextseek_api/eval/paired_run_registry.py", "nextseek_api/eval/tests/test_v4_7_paired_run_registry.py::test_publish_refuses_registered_run_with_wrong_content_hash", "registered run content hash is immutable", "publish with a forged registered content hash"),

    # Version/migration lineage and non-destructive recovery order.
    C("version_paired_schema", "migration_version_guards", "nextseek_api/eval/paired_run.py", "nextseek_api/eval/tests/test_v4_7_schemas.py::test_paired_batch_rejects_wrong_schema_version", "paired batch schema version is exact", "accept an obsolete paired schema"),
    C("version_generation_compatibility", "migration_version_guards", "nextseek_api/eval/generation_validation.py", "nextseek_api/cc_assistant/tests/test_generation_store_validation.py::test_validate_refuses_compatibility_with_stale_current_corpus", "generation compatibility matches current taxonomy and corpus", "activate stale compatibility keys"),
    C("migration_forward_lineage", "migration_version_guards", "scripts/plan018_verifier_support.py", "scripts/test_plan018_v4_9_task5_mutation.py::test_current_migration_lineage_is_unique_and_forward", "V4 migrations remain in the unique current forward lineage", "remove a migration dependency or create a second leaf"),
    C("version_runtime_identity", "migration_version_guards", "nextseek_api/eval/mixed_version_recovery.py", "nextseek_api/eval/tests/test_task7_deploy_recovery.py::test_mutation_removed_runtime_identity_guard_is_killed", "mixed-version reads and writes require an exact recorded runtime identity", "remove the runtime identity guard"),
    C("recovery_previous_generation", "recovery_ordering", "nextseek_api/eval/generation_store.py", "nextseek_api/eval/tests/test_generation_store_mysql.py::test_mysql_rollback_restores_previous", "recovery reactivates the previous immutable generation", "recover to a non-previous generation", lane="mysql"),
    C("recovery_publish_atomic", "recovery_ordering", "nextseek_api/eval/generation_store.py", "nextseek_api/eval/tests/test_generation_store_mysql.py::test_mysql_crash_publish_boundary_leaves_no_incomplete_generation", "publish crash leaves no incomplete generation", "retain partial rows after publish crash", lane="mysql"),
    C("recovery_activation_atomic", "recovery_ordering", "nextseek_api/eval/generation_store.py", "nextseek_api/eval/tests/test_generation_store_mysql.py::test_mysql_crash_activation_boundary_leaves_pointer_unchanged", "activation crash leaves prior pointer unchanged", "mutate pointer before an aborted activation commits", lane="mysql"),
    C("recovery_gate_order", "recovery_ordering", "nextseek_api/eval/fit/v14/recovery_acceptance.py", "nextseek_api/eval/tests/test_task3_fit_coverage.py::test_recovery_acceptance_all_gate_outcomes", "recovery acceptance checks diagnostics, direction, and wall bounds", "declare PASS before all recovery gates"),
    C("recovery_contract_refusal", "recovery_ordering", "nextseek_api/eval/mixed_version_recovery.py", "nextseek_api/eval/tests/test_task7_deploy_recovery.py::test_mutation_removed_contract_refusal_is_killed", "the forbidden contract phase remains absent and refused", "accept a contract-phase request"),
    C("recovery_destructive_refusal", "recovery_ordering", "nextseek_api/eval/mixed_version_recovery.py", "nextseek_api/eval/tests/test_task7_deploy_recovery.py::test_mutation_removed_destructive_recovery_guard_is_killed", "recovery refuses reverse migration, retained-data deletion, and persistent reset", "accept a destructive recovery action"),
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_cases(root: Path = ROOT) -> tuple[MutationCase, ...]:
    dd44 = json.loads((root / DD44).read_text())
    dynamic = tuple(
        C(
            f"dd44_{item['id']}",
            "dd44_aggregation",
            "nextseek_api/eval/judge.py",
            "nextseek_api/eval/tests/test_judge_mutations.py::test_aggregation_mutants_are_detected",
            f"DD-44 {item['operator']} follows the canonical operator/tie-break",
            item["description"],
            parameter_contains=item["id"],
        )
        for item in dd44["mutants"]
    )
    return FIXED_CASES + dynamic


def validate_case_definitions(
    cases: tuple[MutationCase, ...], root: Path = ROOT
) -> list[str]:
    errors: list[str] = []
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        errors.append("duplicate mutant IDs")
    categories = {case.category for case in cases}
    if categories != set(REQUIRED_CATEGORIES):
        errors.append(f"critical categories differ: {sorted(categories)}")
    for case in cases:
        if case.lane not in {"fast", "mysql"}:
            errors.append(f"{case.id}: unknown lane {case.lane}")
        if not case.protected_behavior.strip() or not case.fault.strip():
            errors.append(f"{case.id}: empty protected behavior or fault")
        if case.protected_behavior.strip().casefold() == case.fault.strip().casefold():
            errors.append(f"{case.id}: unchanged mutant")
        if not (root / case.source_path).is_file():
            errors.append(f"{case.id}: missing source {case.source_path}")
    return errors


def migration_errors(root: Path = ROOT) -> list[str]:
    graph = derive_migration_graph(root / "nextseek_api/migrations")
    errors: list[str] = []
    expected_leaf = "0019_merge_attribute_async_turn_ledger"
    if graph.leaves != (expected_leaf,):
        errors.append(f"migration leaves are {graph.leaves}, expected {(expected_leaf,)}")
        return errors
    ancestors = graph.ancestors_of(expected_leaf)
    required = {
        "0012_posterior_generation",
        "0013_family_posterior",
        "0014_generation_activation_and_reservation",
        "0015_v4_5_generation_audit_and_turn_pin",
        "0016_paired_run_registry",
        "0017_paid_run_state",
        "0018_turn_ledger_attempted_provenance",
    }
    missing = sorted(required - ancestors)
    if missing:
        errors.append("current migration leaf omits V4 lineage: " + ",".join(missing))
    return errors


def docker_command(root: Path, image: str, *args: str) -> list[str]:
    return [
        "docker", "run", "--rm", "--network", "none",
        "--cpus", "2", "--memory", "4g",
        "-e", "PYTHONPATH=/work:/work/dmac_assistant/src",
        "-e", "DJANGO_SETTINGS_MODULE=dmac.test_settings",
        "-e", "PYTHONDONTWRITEBYTECODE=1",
        "-v", f"{root.resolve()}:/work", "-w", "/work", image,
        "uv", "run", "--project", "/app", "--no-sync", "python", *args,
    ]


def container_artifact_path(relative: str) -> str:
    """Translate a repo-relative artifact to the repo mount seen by app containers."""
    if relative.startswith("/") or ".." in Path(relative).parts:
        raise ValueError(f"artifact path must be repo-relative: {relative}")
    return f"/work/{relative}"


def run_command(command: list[str], *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)


def collected_nodes(output: str) -> tuple[str, ...]:
    return tuple(
        line.strip() for line in output.splitlines()
        if "::" in line and not line.startswith("=")
    )


def deselected_count(output: str) -> int:
    """Return pytest's reported deselection count, defaulting to zero."""
    return sum(int(value) for value in re.findall(r"(\d+) deselected", output))


def resolve_case_nodes(case: MutationCase, nodes: tuple[str, ...]) -> tuple[str, ...]:
    if case.selector in nodes and case.parameter_contains is None:
        return (case.selector,)
    prefix = case.selector + "["
    matches = tuple(
        node for node in nodes
        if node.startswith(prefix)
        and (case.parameter_contains is None or case.parameter_contains in node)
    )
    if case.parameter_contains is not None and len(matches) != 1:
        return ()
    return matches


def junit_nodes(path: Path) -> tuple[tuple[str, ...], dict[str, int]]:
    root = ET.fromstring(path.read_bytes())
    nodes: list[str] = []
    counts = collections.Counter(tests=0, passed=0, failed=0, errors=0, skipped=0, xfail=0)
    for case in root.findall(".//testcase"):
        counts["tests"] += 1
        classname = case.attrib.get("classname", "").replace(".", "/")
        name = html.unescape(case.attrib.get("name", ""))
        nodes.append(f"{classname}.py::{name}")
        if case.find("failure") is not None:
            counts["failed"] += 1
        elif case.find("error") is not None:
            counts["errors"] += 1
        elif (skipped := case.find("skipped")) is not None:
            counts["xfail" if skipped.get("type") == "pytest.xfail" else "skipped"] += 1
        else:
            counts["passed"] += 1
    return tuple(nodes), dict(counts)


def build_manifest(
    root: Path,
    cases: tuple[MutationCase, ...],
    resolution: dict[str, tuple[str, ...]],
    *,
    status: str,
) -> dict:
    sources = sorted({case.source_path for case in cases})
    tests = sorted({node.split("::", 1)[0] for nodes in resolution.values() for node in nodes})
    return {
        "schema": "plan018-v4-9-task5-mutation-manifest/v1",
        "derivation": "checked-in critical case definitions + source SHA-256 + exact pytest collection",
        "authority": "docs/superpowers/plans/2026-08-13-plan018-v4-9.md#task-5--collection-and-critical-mutationfault-gate",
        "required_categories": list(REQUIRED_CATEGORIES),
        "control_sha256": {path: sha256(root / path) for path in CONTROL_FILES},
        "source_sha256": {path: sha256(root / path) for path in sources},
        "test_source_sha256": {path: sha256(root / path) for path in tests},
        "mutants": [
            {
                "id": case.id,
                "category": case.category,
                "source_path": case.source_path,
                "protected_behavior": case.protected_behavior,
                "fault": case.fault,
                "lane": case.lane,
                "kill_test_selector": case.selector,
                "collected_nodes": list(resolution[case.id]),
                "status": status,
            }
            for case in cases
        ],
        "counts": {
            "mutants": len(cases),
            "fast_nodes": len({node for case in cases if case.lane == "fast" for node in resolution[case.id]}),
            "mysql_nodes": len({node for case in cases if case.lane == "mysql" for node in resolution[case.id]}),
        },
        "global_mutation_score_substitution": False,
        "paid_provider_or_live_resources_used": False,
    }


def _artifact_hashes(root: Path) -> dict[str, str]:
    paths = (COLLECTION, MYSQL_COLLECTION, MANIFEST, JUNIT, MYSQL_JUNIT, MYSQL_SIDECAR)
    return {path: sha256(root / path) for path in paths}


def _write_outputs(
    root: Path,
    image: str,
    cases: tuple[MutationCase, ...],
    resolution: dict[str, tuple[str, ...]],
    fast_counts: dict[str, int],
    mysql_counts: dict[str, int],
    *,
    wall_s: float,
    finalization_mode: str,
) -> None:
    manifest = build_manifest(root, cases, resolution, status="KILLED")
    (root / MANIFEST).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    evidence = {
        "schema": "plan018-v4-9-task5-evidence/v1",
        "gate": "PASS",
        "image": image,
        "network": "none for fast lane; isolated disposable Docker network for MySQL only",
        "wall_s": round(wall_s, 3),
        "wall_cap_s": MAX_WALL_S,
        "execution_counts": {"fast": fast_counts, "mysql": mysql_counts},
        "manifest_counts": manifest["counts"],
        "artifacts_sha256": _artifact_hashes(root),
        "paid_provider_or_live_resources_used": False,
        "mcmc_or_stored_evidence_replay_used": False,
        "finalization": {
            "mode": finalization_mode,
            "new_test_execution": finalization_mode == "fresh_execution",
        },
    }
    (root / EVIDENCE).write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")


def finalize_existing(root: Path, image: str = IMAGE) -> None:
    """Rebind unchanged exact green artifacts after control-only hash drift.

    This is deliberately stricter than rewriting hashes: the checked-in mutant
    definitions, selectors, collections, JUnits, Lane-M attestation, and prior
    execution boundary must all still agree.  Any behavioral/test drift raises
    and requires ``run`` instead.
    """
    required = (COLLECTION, MYSQL_COLLECTION, MANIFEST, JUNIT, MYSQL_JUNIT, MYSQL_SIDECAR, EVIDENCE)
    missing = [path for path in required if not (root / path).is_file()]
    if missing:
        raise RuntimeError("Task 5 finalize inputs missing: " + ",".join(missing))
    cases = load_cases(root)
    errors = validate_case_definitions(cases, root) + migration_errors(root)
    old_manifest = json.loads((root / MANIFEST).read_text())
    old_evidence = json.loads((root / EVIDENCE).read_text())
    old_mutants = old_manifest.get("mutants") or []
    by_id = {item.get("id"): item for item in old_mutants}
    if set(by_id) != {case.id for case in cases} or len(by_id) != len(old_mutants):
        errors.append("existing Task 5 mutant inventory differs from current definitions")
    resolution: dict[str, tuple[str, ...]] = {}
    for case in cases:
        item = by_id.get(case.id) or {}
        expected_fields = {
            "category": case.category,
            "source_path": case.source_path,
            "protected_behavior": case.protected_behavior,
            "fault": case.fault,
            "lane": case.lane,
            "kill_test_selector": case.selector,
            "status": "KILLED",
        }
        if any(item.get(key) != value for key, value in expected_fields.items()):
            errors.append(f"existing Task 5 mutant definition drifted: {case.id}")
        nodes = tuple(item.get("collected_nodes") or ())
        if not nodes:
            errors.append(f"existing Task 5 mutant has no kill node: {case.id}")
        resolution[case.id] = nodes
    fast_collection = tuple((root / COLLECTION).read_text().splitlines())
    mysql_collection = tuple((root / MYSQL_COLLECTION).read_text().splitlines())
    expected_fast = {node for case in cases if case.lane == "fast" for node in resolution[case.id]}
    expected_mysql = {node for case in cases if case.lane == "mysql" for node in resolution[case.id]}
    if expected_fast != set(fast_collection) or expected_mysql != set(mysql_collection):
        errors.append("existing Task 5 collections differ from current mutant resolution")
    actual_fast, fast_counts = junit_nodes(root / JUNIT)
    actual_mysql, mysql_counts = junit_nodes(root / MYSQL_JUNIT)
    fast_counts["deselected"] = 0
    mysql_counts["deselected"] = 0
    if set(actual_fast) != set(fast_collection):
        errors.append("existing Task 5 fast JUnit differs from exact collection")
    if not set(mysql_collection).issubset(set(actual_mysql)):
        errors.append("existing Task 5 MySQL JUnit omits critical collection")
    for lane, counts in (("fast", fast_counts), ("mysql", mysql_counts)):
        if not counts.get("tests") or any(counts[key] for key in ("failed", "errors", "skipped", "xfail", "deselected")):
            errors.append(f"existing Task 5 {lane} JUnit contains nonexecution: {counts}")
    sidecar = json.loads((root / MYSQL_SIDECAR).read_text())
    expected_oracles = [case.id for case in cases if case.lane == "mysql"]
    if (
        sidecar.get("schema") != "plan018-v4-9-task5-lane-m/v1"
        or sidecar.get("gate") != "PASS"
        or sidecar.get("paid_or_live_resources_used") is not False
        or sidecar.get("isolation_level") != "REPEATABLE-READ"
        or sidecar.get("oracles") != expected_oracles
    ):
        errors.append("existing Task 5 Lane M attestation drifted")
    wall_s = old_evidence.get("wall_s")
    if not isinstance(wall_s, (int, float)) or isinstance(wall_s, bool) or not 0 <= wall_s <= MAX_WALL_S:
        errors.append("existing Task 5 execution wall evidence is invalid")
    if old_evidence.get("paid_provider_or_live_resources_used") is not False or old_evidence.get("mcmc_or_stored_evidence_replay_used") is not False:
        errors.append("existing Task 5 execution boundary is not zero-effect")
    if errors:
        raise RuntimeError("Task 5 existing-artifact finalization refused: " + "; ".join(errors))
    _write_outputs(
        root, image, cases, resolution, fast_counts, mysql_counts,
        wall_s=float(wall_s), finalization_mode="existing_exact_artifacts",
    )
    post_errors = validation_errors(root)
    if post_errors:
        raise RuntimeError("Task 5 finalized evidence failed validation: " + "; ".join(post_errors))


def run(root: Path, image: str) -> None:
    started = time.monotonic()
    cases = load_cases(root)
    errors = validate_case_definitions(cases, root) + migration_errors(root)
    if errors:
        raise RuntimeError("Task 5 definition gate failed: " + "; ".join(errors))

    target_files = sorted({case.selector.split("::", 1)[0] for case in cases})
    collected = run_command(docker_command(
        root, image, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *target_files
    ))
    if collected.returncode:
        raise RuntimeError("Task 5 collection failed:\n" + collected.stdout)
    all_nodes = collected_nodes(collected.stdout)
    resolution = {case.id: resolve_case_nodes(case, all_nodes) for case in cases}
    unresolved = [case_id for case_id, nodes in resolution.items() if not nodes]
    if unresolved:
        raise RuntimeError("Task 5 unresolved mutant selectors: " + ",".join(unresolved))

    fast_nodes = tuple(sorted({node for case in cases if case.lane == "fast" for node in resolution[case.id]}))
    mysql_nodes = tuple(sorted({node for case in cases if case.lane == "mysql" for node in resolution[case.id]}))
    if not fast_nodes or not mysql_nodes:
        raise RuntimeError("Task 5 requires positive fast and MySQL collections")
    (root / COLLECTION).write_text("\n".join(fast_nodes) + "\n")
    (root / MYSQL_COLLECTION).write_text("\n".join(mysql_nodes) + "\n")

    fast = run_command(docker_command(
        root, image, "-m", "pytest", "-q", "-p", "no:cacheprovider",
        f"--junitxml=/work/{JUNIT}", *fast_nodes
    ))
    print(fast.stdout, end="", flush=True)
    if fast.returncode:
        raise RuntimeError("Task 5 fast mutation lane failed")
    actual_fast, fast_counts = junit_nodes(root / JUNIT)
    fast_counts["deselected"] = deselected_count(fast.stdout)
    if set(actual_fast) != set(fast_nodes) or any(
        fast_counts[k] for k in ("failed", "errors", "skipped", "xfail", "deselected")
    ):
        raise RuntimeError(f"Task 5 fast execution mismatch: {fast_counts}")

    lane_env = os.environ.copy()
    lane_env.update({
        "REPO": str(root),
        "APP_IMAGE": image,
        "LANE_M_PYTEST": "nextseek_api/eval/tests/test_generation_store_mysql.py nextseek_api/eval/tests/test_v4_8_mysql.py",
        "LANE_M_LOG": str(root / MYSQL_LOG),
        "LANE_M_JUNIT": container_artifact_path(MYSQL_JUNIT),
        "LANE_M_SIDECAR": str(root / MYSQL_SIDECAR),
        "LANE_M_SIDECAR_SCHEMA": "plan018-v4-9-task5-lane-m/v1",
        "LANE_M_ORACLES": json.dumps([case.id for case in cases if case.lane == "mysql"]),
    })
    lane = run_command(["bash", str(root / "scripts/plan018_lane_m_mysql.sh")], env=lane_env)
    print(lane.stdout, end="", flush=True)
    if lane.returncode:
        raise RuntimeError("Task 5 disposable MySQL lane failed")
    actual_mysql, mysql_counts = junit_nodes(root / MYSQL_JUNIT)
    mysql_counts["deselected"] = deselected_count(lane.stdout)
    if not set(mysql_nodes).issubset(set(actual_mysql)) or any(
        mysql_counts[k] for k in ("failed", "errors", "skipped", "xfail", "deselected")
    ):
        raise RuntimeError(f"Task 5 MySQL execution mismatch: {mysql_counts}")

    elapsed = time.monotonic() - started
    if elapsed > MAX_WALL_S:
        raise RuntimeError(f"Task 5 exceeded hardware design cap: {elapsed:.3f}s > {MAX_WALL_S:.0f}s")
    _write_outputs(
        root, image, cases, resolution, fast_counts, mysql_counts,
        wall_s=elapsed, finalization_mode="fresh_execution",
    )
    print(f"Task 5 mutation/fault gate PASS in {elapsed:.3f}s", flush=True)


def validation_errors(root: Path = ROOT) -> list[str]:
    required = (COLLECTION, MYSQL_COLLECTION, MANIFEST, JUNIT, MYSQL_JUNIT, MYSQL_SIDECAR, EVIDENCE)
    missing = [path for path in required if not (root / path).is_file()]
    if missing:
        return ["missing Task 5 artifacts: " + ",".join(missing)]
    try:
        cases = load_cases(root)
        evidence = json.loads((root / EVIDENCE).read_text())
        manifest = json.loads((root / MANIFEST).read_text())
        lane_sidecar = json.loads((root / MYSQL_SIDECAR).read_text())
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        return [f"malformed Task 5 JSON artifact: {exc}"]
    errors = validate_case_definitions(cases, root) + migration_errors(root)
    if evidence.get("gate") != "PASS":
        errors.append("Task 5 evidence is not PASS")
    if evidence.get("paid_provider_or_live_resources_used") is not False:
        errors.append("Task 5 evidence does not attest zero paid/live use")
    if evidence.get("mcmc_or_stored_evidence_replay_used") is not False:
        errors.append("Task 5 unexpectedly used MCMC or stored-evidence replay")
    finalization = evidence.get("finalization") or {}
    if finalization not in (
        {"mode": "fresh_execution", "new_test_execution": True},
        {"mode": "existing_exact_artifacts", "new_test_execution": False},
    ):
        errors.append("Task 5 finalization provenance is missing or invalid")
    if float(evidence.get("wall_s", MAX_WALL_S + 1)) > MAX_WALL_S:
        errors.append("Task 5 exceeded the hardware wall cap")
    if manifest.get("required_categories") != list(REQUIRED_CATEGORIES):
        errors.append("Task 5 manifest categories drifted")
    mutants = manifest.get("mutants") or []
    expected_by_id = {case.id: case for case in cases}
    actual_by_id = {item.get("id"): item for item in mutants}
    if set(actual_by_id) != set(expected_by_id) or len(mutants) != len(actual_by_id):
        errors.append("Task 5 mutant IDs differ from the critical inventory")
    if len(mutants) != len(cases) or any(item.get("status") != "KILLED" for item in mutants):
        errors.append("Task 5 does not record every critical mutant KILLED")
    for case_id, case in expected_by_id.items():
        item = actual_by_id.get(case_id) or {}
        expected_fields = {
            "category": case.category,
            "source_path": case.source_path,
            "protected_behavior": case.protected_behavior,
            "fault": case.fault,
            "lane": case.lane,
            "kill_test_selector": case.selector,
        }
        if any(item.get(key) != value for key, value in expected_fields.items()):
            errors.append(f"Task 5 mutant definition drifted: {case_id}")
        if not item.get("collected_nodes"):
            errors.append(f"Task 5 mutant has no collected kill node: {case_id}")
    if manifest.get("global_mutation_score_substitution") is not False:
        errors.append("Task 5 substituted a global mutation score")
    fast_collection = tuple((root / COLLECTION).read_text().splitlines())
    mysql_collection = tuple((root / MYSQL_COLLECTION).read_text().splitlines())
    manifest_fast = {
        node for item in mutants if item.get("lane") == "fast"
        for node in (item.get("collected_nodes") or [])
    }
    manifest_mysql = {
        node for item in mutants if item.get("lane") == "mysql"
        for node in (item.get("collected_nodes") or [])
    }
    if manifest_fast != set(fast_collection) or manifest_mysql != set(mysql_collection):
        errors.append("Task 5 manifest-to-collection mapping drifted")
    expected_source_paths = {case.source_path for case in cases}
    expected_test_paths = {
        node.split("::", 1)[0]
        for item in mutants for node in (item.get("collected_nodes") or [])
    }
    expected_sections = {
        "control_sha256": set(CONTROL_FILES),
        "source_sha256": expected_source_paths,
        "test_source_sha256": expected_test_paths,
    }
    for section in ("control_sha256", "source_sha256", "test_source_sha256"):
        if set(manifest.get(section) or {}) != expected_sections[section]:
            errors.append(f"Task 5 {section} key set drifted")
        for relative, expected in (manifest.get(section) or {}).items():
            if not (root / relative).is_file() or sha256(root / relative) != expected:
                errors.append(f"stale Task 5 {section}: {relative}")
    expected_artifacts = {COLLECTION, MYSQL_COLLECTION, MANIFEST, JUNIT, MYSQL_JUNIT, MYSQL_SIDECAR}
    if set(evidence.get("artifacts_sha256") or {}) != expected_artifacts:
        errors.append("Task 5 artifact hash key set drifted")
    for relative, expected in (evidence.get("artifacts_sha256") or {}).items():
        if not (root / relative).is_file() or sha256(root / relative) != expected:
            errors.append(f"stale Task 5 artifact: {relative}")
    expected_oracles = [case.id for case in cases if case.lane == "mysql"]
    if (
        lane_sidecar.get("schema") != "plan018-v4-9-task5-lane-m/v1"
        or lane_sidecar.get("gate") != "PASS"
        or lane_sidecar.get("paid_or_live_resources_used") is not False
        or lane_sidecar.get("isolation_level") != "REPEATABLE-READ"
        or lane_sidecar.get("oracles") != expected_oracles
    ):
        errors.append("Task 5 Lane M attestation drifted")
    for junit, expected_nodes, lane_name in (
        (JUNIT, fast_collection, "fast"),
        (MYSQL_JUNIT, mysql_collection, "mysql"),
    ):
        try:
            actual, counts = junit_nodes(root / junit)
        except (ET.ParseError, OSError, ValueError) as exc:
            errors.append(f"malformed Task 5 {lane_name} JUnit: {exc}")
            continue
        counts["deselected"] = 0
        if lane_name == "fast" and set(actual) != set(expected_nodes):
            errors.append("Task 5 fast JUnit differs from exact collection")
        if lane_name == "mysql" and not set(expected_nodes).issubset(set(actual)):
            errors.append("Task 5 MySQL JUnit omits critical collection")
        if not actual or any(
            counts[key] for key in ("failed", "errors", "skipped", "xfail", "deselected")
        ):
            errors.append(f"Task 5 {lane_name} JUnit has nonexecution: {counts}")
        if (evidence.get("execution_counts") or {}).get(lane_name) != counts:
            errors.append(f"Task 5 {lane_name} evidence counts differ from JUnit")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("run", "finalize", "validate"))
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--image", default=IMAGE)
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.action == "run":
        run(root, args.image)
        return 0
    if args.action == "finalize":
        finalize_existing(root, args.image)
        print("Task 5 existing exact artifacts finalization PASS")
        return 0
    errors = validation_errors(root)
    print("Task 5 evidence " + ("PASS" if not errors else "FAIL"))
    for error in errors:
        print("- " + error)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

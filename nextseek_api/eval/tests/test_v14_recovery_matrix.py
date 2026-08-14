"""Recovery matrix support vs the quality-equivalence routing policy."""
from __future__ import annotations

from nextseek_api.eval.fit.v14.decision import (
    CandidateDecision,
    DecisionStatus,
    GenerationDecision,
    quality_discordance_ok,
    retained_support_ok,
)
from nextseek_api.eval.fit.v14.fit_config import V14FitConfig
from nextseek_api.eval.fit.v14.recovery_acceptance import slot_winner
from nextseek_api.eval.fit.v14.recovery_matrix import (
    RecoveryScenario,
    build_scenario_rows,
    ground_truth,
    matrix_fingerprint,
)


def test_matrix_fingerprint_includes_current_contract():
    fp = matrix_fingerprint()
    assert len(fp) == 64
    assert fp != "deadbeef"


def test_quality_eq_support_under_ruling_b():
    cfg = V14FitConfig()
    for scenario in (RecoveryScenario.quality_eq_ns_faster, RecoveryScenario.quality_eq_cc_faster):
        rows = build_scenario_rows(scenario)
        assert retained_support_ok(rows, "fam_a", cfg)
        assert not quality_discordance_ok(rows, "fam_a", cfg)
        assert ground_truth(scenario).startswith("strong_")


def test_equivalence_policy_preserves_posterior_uncertainty():
    assert ground_truth(RecoveryScenario.right_censor_30pct) == "strong_ns"
    assert ground_truth(RecoveryScenario.null_equal) == "indecisive"
    assert ground_truth(RecoveryScenario.adversarial_outliers) == "indecisive"


def test_below_min_support_indecisive():
    rows = build_scenario_rows(RecoveryScenario.below_min_support)
    cfg = V14FitConfig()
    assert not retained_support_ok(rows, "small_fam", cfg)
    assert ground_truth(RecoveryScenario.below_min_support) == "indecisive"


def test_recovery_acceptance_recognizes_equivalence_ns_winner():
    decision = GenerationDecision(
        candidates=(
            CandidateDecision(
                family="fam_a",
                status=DecisionStatus.quality_equivalent_ns,
                local_error_prob=0.02,
                activated=True,
            ),
        ),
        posterior_expected_fdr=0.02,
        activated_families=("fam_a",),
        generation_status="activated_all",
        config_fingerprint="test",
    )

    assert slot_winner(decision) == "ns"

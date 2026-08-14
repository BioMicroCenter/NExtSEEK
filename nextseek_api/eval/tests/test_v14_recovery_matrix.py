"""Recovery matrix support vs ground truth under ruling B."""
from __future__ import annotations

from nextseek_api.eval.fit.v14.decision import quality_discordance_ok, retained_support_ok
from nextseek_api.eval.fit.v14.fit_config import V14FitConfig
from nextseek_api.eval.fit.v14.recovery_matrix import (
    RECOVERY_SCENARIOS,
    RecoveryScenario,
    build_scenario_rows,
    ground_truth,
    matrix_fingerprint,
)


def test_matrix_fingerprint_includes_contract_b():
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


def test_adversarial_indecisive_gt():
    assert ground_truth(RecoveryScenario.adversarial_outliers) == "indecisive"


def test_below_min_support_indecisive():
    rows = build_scenario_rows(RecoveryScenario.below_min_support)
    cfg = V14FitConfig()
    assert not retained_support_ok(rows, "small_fam", cfg)
    assert ground_truth(RecoveryScenario.below_min_support) == "indecisive"

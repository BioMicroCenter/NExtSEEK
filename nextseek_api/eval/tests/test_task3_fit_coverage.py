"""Fast branch tests for the V4-9 Task-3 fitter/recovery surface.

The statistical backends are replaced with deterministic in-process doubles.
These tests exercise our orchestration and refusal logic without running MCMC;
the real NumPyro fit remains covered by the authenticated V4-4 acceptance run.
"""
from __future__ import annotations

import json
import sys
import types
from dataclasses import replace

import numpy as np
import pytest

from nextseek_api.eval.evidence_kinds import (
    EvidenceKind,
    ForgedEvidenceDiscriminator,
    OnlineEvidenceRejected,
    UnapprovedPairedRun,
)
from nextseek_api.eval.fit import fit_boundary
from nextseek_api.eval.fit.v14 import combined, latency_model, quality_model
from nextseek_api.eval.fit.v14.decision import (
    CandidateDecision,
    DecisionStatus,
    GenerationDecision,
    apply_complete_set_fdr,
    decision_status_to_band,
    decide_family,
    evaluate_generation,
)
from nextseek_api.eval.fit.v14.fit_config import V14FitConfig
from nextseek_api.eval.fit.v14.pair_rows import (
    JointQualityState,
    LatencyObservationKind,
    PairFitRow,
    _arm_for_pair,
    _latency_fields,
    joint_state_from_success,
)
from nextseek_api.eval.fit.v14.recovery_acceptance import (
    evaluate_recovery_results,
    scenario_from_value,
    slot_winner,
    winner_matches_gt,
)
from nextseek_api.eval.fit.v14.recovery_matrix import (
    RECOVERY_SCENARIOS,
    RecoveryScenario,
    build_scenario_rows,
    ground_truth,
)
from nextseek_api.eval.fit.v14 import recovery_runner
from nextseek_api.eval.online_observation import (
    DEFAULT_SELECTION_CAVEAT,
    OnlineObservationalRow,
)
from nextseek_api.eval.paired_run import PairedExperimentalBatch
from nextseek_api.eval.router_models_proposal import RouteSource


def _row(
    pair_id: str = "p1",
    *,
    family: str = "fam",
    state: JointQualityState = JointQualityState.both_succeed,
    kind: LatencyObservationKind = LatencyObservationKind.observed,
) -> PairFitRow:
    kwargs = {}
    if kind is LatencyObservationKind.observed:
        kwargs = {"log_latency_ns": 0.0, "log_latency_cc": 0.2}
    elif kind is LatencyObservationKind.ns_right_censored:
        kwargs = {"log_d_lower": 0.3}
    elif kind is LatencyObservationKind.cc_right_censored:
        kwargs = {"log_d_upper": -0.3}
    return PairFitRow(
        pair_id=pair_id,
        query_id=f"q-{pair_id}",
        family=family,
        joint_state=state,
        latency_kind=kind,
        **kwargs,
    )


def _decision(status: DecisionStatus, *, activated: bool = True) -> GenerationDecision:
    return GenerationDecision(
        candidates=(CandidateDecision("fam", status, 0.01, activated),),
        posterior_expected_fdr=0.01,
        activated_families=("fam",) if activated else (),
        generation_status="activated_all" if activated else "empty_candidate_set",
        config_fingerprint="fp",
    )


def test_fit_boundary_rejects_observational_and_forged_inputs(monkeypatch):
    observational = OnlineObservationalRow(
        observation_id="obs-1",
        session_id="s",
        turn_number=1,
        route="container_cc",
        route_source=RouteSource.baml,
        selection_caveat=DEFAULT_SELECTION_CAVEAT,
    )
    with pytest.raises(OnlineEvidenceRejected, match="observational row"):
        fit_boundary.assert_paired_experimental_only(observational)
    with pytest.raises(OnlineEvidenceRejected, match="raw dict"):
        fit_boundary.assert_paired_experimental_only(
            {"evidence_kind": "online_observational"}
        )
    with pytest.raises(ForgedEvidenceDiscriminator, match="unknown"):
        fit_boundary.assert_paired_experimental_only({"evidence_kind": "forged"})
    with pytest.raises(OnlineEvidenceRejected, match="schema_version"):
        fit_boundary.assert_paired_experimental_only(
            {"schema_version": "online_observation/v1"}
        )

    class DeclaredOnline:
        evidence_kind = EvidenceKind.online_observational

    with pytest.raises(OnlineEvidenceRejected, match="declares"):
        fit_boundary.assert_paired_experimental_only(DeclaredOnline())
    fit_boundary.assert_paired_experimental_only({"evidence_kind": "paired_experimental"})
    fit_boundary.assert_paired_experimental_only(object())
    forged_batch = PairedExperimentalBatch.model_construct(
        schema_version="paired_run/v1",
        evidence_kind=EvidenceKind.online_observational,
        paired_run_id="run",
        pairs=[],
        arm_records={},
    )
    with pytest.raises(Exception, match="unexpected evidence kind"):
        fit_boundary.assert_paired_experimental_only(forged_batch)


def test_fit_boundary_hash_and_provenance_guards(monkeypatch):
    batch = PairedExperimentalBatch(
        paired_run_id="run-1",
        pairs=[{"pair_id": "pair-a"}, {"pair_id": "pair-a"}],
    )
    assert fit_boundary.compute_paired_input_hash(batch) == fit_boundary.compute_paired_input_hash(batch)
    fit_boundary.assert_zero_online_ids_in_hash(batch, {"obs-not-a-pair", ""})
    with pytest.raises(OnlineEvidenceRejected, match="contaminated"):
        fit_boundary.assert_zero_online_ids_in_hash(batch, {"pair-a"})
    monkeypatch.setattr(fit_boundary, "compute_paired_input_hash", lambda _: "contains-obs-1")
    with pytest.raises(OnlineEvidenceRejected, match="appears"):
        fit_boundary.assert_zero_online_ids_in_hash(batch, {"obs-1"})
    with pytest.raises(OnlineEvidenceRejected, match="raw dict"):
        fit_boundary.refuse_raw_dict_fit_input({}, context="publish")
    fit_boundary.refuse_raw_dict_fit_input({}, context="report")
    assert fit_boundary.extract_paired_run_id_from_provenance({}) is None
    assert fit_boundary.extract_paired_run_id_from_provenance({"paired_run_id": 7}) == "7"

    monkeypatch.setattr(fit_boundary, "require_approved_paired_run", lambda *a, **k: None)
    good = {
        "paired_run_id": "run-1",
        "paired_run_content_hash": "hash-1",
        "evidence_kind": "paired_experimental",
        "route_source": "forced",
    }
    fit_boundary.validate_publish_provenance(good)
    for update, message, error in (
        ({"paired_run_id": ""}, "requires paired_run_id", OnlineEvidenceRejected),
        ({"evidence_kind": "online_observational"}, "evidence_kind", OnlineEvidenceRejected),
        ({"route_source": "classifier"}, "policy-selected", OnlineEvidenceRejected),
        ({"paired_run_content_hash": ""}, "content_hash", UnapprovedPairedRun),
    ):
        with pytest.raises(error, match=message):
            fit_boundary.validate_publish_provenance({**good, **update})


def test_fit_boundary_approved_run_hash_mismatch(monkeypatch):
    registry = types.SimpleNamespace(
        objects=types.SimpleNamespace(
            get=lambda **_: types.SimpleNamespace(content_hash="registered")
        )
    )
    monkeypatch.setattr(
        "nextseek_api.eval.paired_run_registry.is_paired_run_approved",
        lambda run_id: run_id == "approved",
    )
    with pytest.raises(UnapprovedPairedRun, match="not approved"):
        fit_boundary.require_approved_paired_run("no")
    monkeypatch.setattr("nextseek_api.assistant.models_db.PairedRunRegistry", registry)
    fit_boundary.require_approved_paired_run("approved")
    with pytest.raises(UnapprovedPairedRun, match="content_hash mismatch"):
        fit_boundary.require_approved_paired_run("approved", expected_content_hash="other")


@pytest.mark.parametrize(
    ("status", "band"),
    [
        ("legacy_fallback", "TooUncertain"),
        ("quality_ns", "Reliable"),
        ("latency_cc", "Reliable"),
        ("unrelated_canned", "Brittle"),
        ("future_status", "Watch"),
    ],
)
def test_decision_band_complete_mapping(status, band):
    assert decision_status_to_band(status) == band


def test_complete_set_fdr_all_selected_and_tie_breaking():
    cfg = V14FitConfig(fdr_threshold=0.05)
    candidates = (
        CandidateDecision("z", DecisionStatus.quality_ns, 0.02),
        CandidateDecision("a", DecisionStatus.quality_cc, 0.02),
    )
    final, fdr, status = apply_complete_set_fdr(candidates, cfg)
    assert status == "activated_all"
    assert fdr == pytest.approx(0.02)
    assert all(item.activated for item in final)


def test_evaluate_generation_sorts_families(monkeypatch):
    rows = [_row("2", family="z"), _row("1", family="a")]
    seen = []

    def fake_decide(rows, family, quality, latency, cfg):
        seen.append(family)
        return CandidateDecision(family, DecisionStatus.indecisive, 1.0)

    monkeypatch.setattr("nextseek_api.eval.fit.v14.decision.decide_family", fake_decide)
    result = evaluate_generation(
        rows,
        {"a": object(), "z": object()},
        {"a": object(), "z": object()},
        V14FitConfig(),
        config_fingerprint="fp",
    )
    assert seen == ["a", "z"]
    assert result.generation_status == "empty_candidate_set"


def test_decision_unrelated_and_cc_latency_paths():
    cfg = V14FitConfig(min_retained_pairs=1, min_discordant_pairs=1)
    rows = [_row("1", state=JointQualityState.nextseek_only_succeeds)]
    quality = quality_model.QualityFitResult(
        "fam", np.ones(4) / 4, 0.0, np.zeros(8), 0, 1.0, 500, 500
    )
    latency = latency_model.LatencyFitResult(
        "fam", np.full(8, 1.0), 0.0, 0, 1.0, 500, 500
    )
    assert decide_family(rows, "unrelated", quality, latency, cfg).status is DecisionStatus.unrelated_canned
    assert decide_family(rows, "fam", quality, latency, cfg).status is DecisionStatus.latency_cc


def test_pair_row_remaining_state_latency_and_missing_arm_paths():
    assert joint_state_from_success(False, True) is JointQualityState.container_cc_only_succeeds
    assert joint_state_from_success(False, False) is JointQualityState.both_fail
    assert _latency_fields(True, True, None, None, False, False)[0] is LatencyObservationKind.latency_uninformative
    with pytest.raises(KeyError, match="missing nextseek"):
        _arm_for_pair({}, "p", "nextseek")


def test_combined_orchestration_modes_and_diagnostic_failures(monkeypatch):
    rows = [_row(str(i), family="fam") for i in range(5)]
    cfg = V14FitConfig(min_discordant_pairs=0)
    descriptive_q = quality_model.DescriptiveQualityResult("fam", np.ones(4) / 4, 0.0)
    descriptive_l = latency_model.DescriptiveLatencyResult("fam", 5)
    monkeypatch.setattr(combined, "fit_quality_models", lambda *a, **k: {"fam": descriptive_q})
    monkeypatch.setattr(combined, "fit_latency_model", lambda *a, **k: descriptive_l)
    profile = combined.run_v14_generation(rows, cfg, use_mcmc=False)
    assert profile.decision.generation_status == "profile_only"
    assert not profile.diagnostics_ok

    monkeypatch.setattr(combined, "fit_quality_models", lambda *a, **k: {"fam": descriptive_q})
    with pytest.raises(TypeError, match="descriptive"):
        combined.run_v14_generation(rows, cfg, quality_use_mcmc=True, latency_use_mcmc=False)

    good_q = quality_model.QualityFitResult(
        "fam", np.ones(4) / 4, 0.0, np.zeros(8), 0, 1.0, 500, 500
    )
    bad_l = latency_model.LatencyFitResult("fam", np.zeros(8), 0.5, 1, 1.2, 1, 1)
    monkeypatch.setattr(combined, "fit_quality_models", lambda *a, **k: {"fam": good_q})
    monkeypatch.setattr(combined, "fit_latency_model", lambda *a, **k: bad_l)
    monkeypatch.setattr(combined, "evaluate_generation", lambda *a, **k: _decision(DecisionStatus.quality_ns))
    posterior = combined.run_v14_generation(rows, cfg, use_mcmc=True)
    assert not posterior.diagnostics_ok


def _install_fake_probabilistic_stack(monkeypatch, *, quality: bool) -> None:
    jnp = types.ModuleType("jax.numpy")
    for name in ("array", "asarray", "zeros", "eye", "broadcast_to"):
        setattr(jnp, name, getattr(np, name))
    jax = types.ModuleType("jax")
    jax.numpy = jnp
    jax.random = types.SimpleNamespace(PRNGKey=lambda seed: ("key", seed))

    class Dist:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    dist = types.ModuleType("numpyro.distributions")
    for name in ("Normal", "HalfNormal", "StudentT", "TruncatedNormal", "MultivariateNormal", "Categorical"):
        setattr(dist, name, Dist)

    class Plate:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    numpyro = types.ModuleType("numpyro")
    numpyro.distributions = dist
    numpyro.set_host_device_count = lambda count: None
    numpyro.plate = lambda *args: Plate()
    numpyro.sample = lambda name, distribution, obs=None: (
        np.zeros(3)
        if name == "z_global"
        else np.zeros((1, 3))
        if name == "delta_family"
        else 1.0
        if name == "sigma_latency"
        else 0.0
        if name in {"mu_latency", "delta_latency"}
        else obs
    )

    class NUTS:
        def __init__(self, model):
            self.model = model

    class MCMC:
        def __init__(self, kernel, **kwargs):
            self.kernel = kernel

        def run(self, *args, **kwargs):
            self.kernel.model()

        def get_samples(self, group_by_chain=False):
            if group_by_chain:
                return {"posterior": np.ones((1, 4))}
            if quality:
                return {
                    "z_global": np.zeros((4, 3)),
                    "delta_family": np.zeros((4, 1, 3)),
                }
            return {"mu_latency": np.array([-0.2, -0.1]), "delta_latency": np.zeros(2)}

        def get_extra_fields(self, group_by_chain=False):
            return {"diverging": np.array([[False]])}

    infer = types.ModuleType("numpyro.infer")
    infer.MCMC = MCMC
    infer.NUTS = NUTS
    numpyro.infer = infer
    monkeypatch.setitem(sys.modules, "jax", jax)
    monkeypatch.setitem(sys.modules, "jax.numpy", jnp)
    monkeypatch.setitem(sys.modules, "numpyro", numpyro)
    monkeypatch.setitem(sys.modules, "numpyro.distributions", dist)
    monkeypatch.setitem(sys.modules, "numpyro.infer", infer)


def test_fast_quality_mcmc_orchestration_and_wrapper(monkeypatch):
    _install_fake_probabilistic_stack(monkeypatch, quality=True)

    class Metric:
        def __init__(self, value):
            self.value = value

        def to_array(self):
            return self

        def max(self):
            return self.value

        def min(self):
            return self.value

    az = types.ModuleType("arviz")
    az.rhat = lambda _: Metric(1.0)
    az.ess = lambda _, method: Metric(500.0)
    monkeypatch.setitem(sys.modules, "arviz", az)
    rows = [
        _row("1", state=JointQualityState.nextseek_only_succeeds),
        _row("2", state=JointQualityState.both_succeed),
    ]
    results = quality_model.fit_quality_models(rows, V14FitConfig(num_chains=1), seed=4)
    assert results["fam"].diagnostics_scope == "shared_generation"
    assert quality_model.fit_quality_model(rows, "fam", V14FitConfig(), use_mcmc=False).family == "fam"
    assert quality_model.fit_quality_model(rows, "missing", V14FitConfig()).quality_advantage_ns == 0
    assert quality_model.fit_quality_models([], V14FitConfig()) == {}
    assert set(
        quality_model.fit_quality_models(
            [rows[0], _row("other", family="other")],
            V14FitConfig(),
            use_mcmc=False,
        )
    ) == {"fam", "other"}
    assert quality_model.quality_advantage_from_counts(np.zeros(4)) == 0.0
    assert quality_model.quality_advantage_from_counts(np.array([0, 3, 1, 0])) == 0.5
    assert quality_model.jax_key(8) == ("key", 8)


def test_quality_diagnostics_fail_closed(monkeypatch):
    az = types.ModuleType("arviz")
    az.rhat = lambda _: (_ for _ in ()).throw(ValueError("bad diagnostics"))
    monkeypatch.setitem(sys.modules, "arviz", az)
    with pytest.raises(RuntimeError, match="could not be computed"):
        quality_model._shared_diagnostics(
            types.SimpleNamespace(get_samples=lambda **_: {})
        )


def test_fast_latency_mcmc_all_observation_branches(monkeypatch):
    _install_fake_probabilistic_stack(monkeypatch, quality=False)
    # Force the conservative diagnostics fallback without depending on ArviZ.
    monkeypatch.setitem(sys.modules, "arviz", types.ModuleType("arviz"))
    rows = [
        _row("o"),
        _row("l", kind=LatencyObservationKind.ns_right_censored),
        _row("u", kind=LatencyObservationKind.cc_right_censored),
        _row("uninformative", kind=LatencyObservationKind.latency_uninformative),
        _row("other", family="other"),
    ]
    result = latency_model.fit_latency_model(rows, "fam", V14FitConfig(num_chains=1), seed=3)
    assert isinstance(result, latency_model.LatencyFitResult)
    assert result.posterior_ns_faster_prob == 1.0
    assert result.rhat_max == 1.0
    assert latency_model._jax_key(9) == ("key", 9)
    assert latency_model.fit_latency_model([], "fam", V14FitConfig()).observation_count == 0
    assert latency_model.fit_latency_model(rows, "fam", V14FitConfig(), use_mcmc=False).observation_count == 3
    assert latency_model._extract_d_obs(rows, "absent") == ([], [])


def test_latency_model_refuses_to_assume_unknown_internal_kind(monkeypatch):
    _install_fake_probabilistic_stack(monkeypatch, quality=False)
    monkeypatch.setitem(sys.modules, "arviz", types.ModuleType("arviz"))
    monkeypatch.setattr(
        latency_model,
        "_extract_d_obs",
        lambda rows, family: ([0.1, 0.2], ["upper", "future_kind"]),
    )
    result = latency_model.fit_latency_model([], "fam", V14FitConfig(num_chains=1))
    assert isinstance(result, latency_model.LatencyFitResult)


def test_latency_successful_diagnostics(monkeypatch):
    _install_fake_probabilistic_stack(monkeypatch, quality=False)

    class Metric:
        def __init__(self, value):
            self.value = value

        def to_array(self):
            return self

        def max(self):
            return self.value

        def min(self):
            return self.value

    az = types.ModuleType("arviz")
    az.from_numpyro = lambda _: types.SimpleNamespace(
        sample_stats={"diverging": np.array([False])}
    )
    az.rhat = lambda _: Metric(1.0)
    az.ess = lambda _, method: Metric(500.0)
    monkeypatch.setitem(sys.modules, "arviz", az)
    result = latency_model.fit_latency_model([_row()], "fam", V14FitConfig(num_chains=1))
    assert result.ess_tail_min == 500.0


@pytest.mark.parametrize("scenario", RECOVERY_SCENARIOS)
def test_every_recovery_scenario_builds_expected_rows(scenario):
    rows = build_scenario_rows(scenario)
    assert rows
    assert ground_truth(scenario) in {"strong_ns", "strong_cc", "indecisive"}
    assert scenario_from_value(scenario.value) is scenario


def test_recovery_matrix_unknown_value_uses_conservative_default():
    rows = build_scenario_rows("future_scenario")
    assert len(rows) == 8
    assert {row.joint_state for row in rows} == {
        JointQualityState.nextseek_only_succeeds,
        JointQualityState.container_cc_only_succeeds,
    }


def test_recovery_acceptance_all_gate_outcomes():
    assert winner_matches_gt("ignored", "strong_ns", "ns")
    assert winner_matches_gt("ignored", "strong_cc", "cc")
    assert winner_matches_gt("ignored", "indecisive", "none")
    assert slot_winner(_decision(DecisionStatus.quality_cc)) == "cc"
    assert slot_winner(_decision(DecisionStatus.indecisive, activated=False)) == "none"
    assert slot_winner(_decision(DecisionStatus.indecisive, activated=True)) == "none"
    profile = evaluate_recovery_results([], use_mcmc=False, wall_s=0, serial_s=0)
    assert profile.gate == "PROFILE_ONLY"

    bad = []
    for seed in range(5):
        bad.extend(
            [
                {"scenario": "strong", "ground_truth": "strong_ns", "winner": "cc", "seed": seed, "diagnostics_ok": False},
                {"scenario": "null", "ground_truth": "indecisive", "winner": "ns", "seed": seed, "diagnostics_ok": True},
            ]
        )
    failed = evaluate_recovery_results(bad, use_mcmc=True, wall_s=1, serial_s=1)
    assert failed.gate == "FAIL"
    assert failed.wrong_direction == 10
    timed = evaluate_recovery_results(bad, use_mcmc=True, wall_s=2, serial_s=1, wall_limit_s=1)
    assert timed.gate == "INCONCLUSIVE"
    passed = evaluate_recovery_results(
        [
            {"scenario": "strong", "ground_truth": "strong_ns", "winner": "ns", "seed": 1, "diagnostics_ok": True},
            {"scenario": "null", "ground_truth": "indecisive", "winner": "none", "seed": 1, "diagnostics_ok": True},
        ],
        use_mcmc=True,
        wall_s=0,
        serial_s=0,
    )
    assert passed.gate == "PASS"

    almost = [
        {"scenario": "strong", "ground_truth": "strong_ns", "winner": "ns", "seed": seed, "diagnostics_ok": True}
        for seed in range(5)
    ]
    assert evaluate_recovery_results(almost, use_mcmc=True, wall_s=0, serial_s=0).gate == "PASS"


def test_recovery_runner_fast_paths(monkeypatch, tmp_path, capsys):
    slots = recovery_runner.all_slots()
    assert len(slots) == len(RECOVERY_SCENARIOS) * 5

    fake_fit = types.SimpleNamespace(
        decision=_decision(DecisionStatus.quality_ns), diagnostics_ok=True
    )
    monkeypatch.setattr(recovery_runner, "run_v14_generation", lambda *a, **k: fake_fit)
    result = recovery_runner.run_recovery(slot_indices=[0, -1, 999], use_mcmc=True)
    assert result["completed"] == 1
    assert result["results"][0]["winner"] == "ns"
    assert recovery_runner.run_recovery(slot_indices=None, use_mcmc=True)["completed"] == 40

    times = iter((0.0, 2.0, 2.0, 2.0))
    monkeypatch.setattr(recovery_runner.time, "monotonic", lambda: next(times))
    capped = recovery_runner.run_recovery(slot_indices=[0], wall_limit_s=1)
    assert capped["reason"] == "wall_clock_cap"

    out = tmp_path / "recovery.json"
    monkeypatch.setattr(
        recovery_runner,
        "run_recovery",
        lambda **kwargs: {"gate": "PASS", "completed": 5},
    )
    monkeypatch.setattr(sys, "argv", ["recovery", "--feasibility", "--no-mcmc", "--out", str(out)])
    assert recovery_runner.main() == 0
    assert json.loads(out.read_text())["gate"] == "PASS"
    assert '"completed": 5' in capsys.readouterr().out
    monkeypatch.setattr(
        recovery_runner,
        "run_recovery",
        lambda **kwargs: {"gate": "FAIL", "completed": 0},
    )
    monkeypatch.setattr(sys, "argv", ["recovery", "--slot", "0", "--out", str(out)])
    assert recovery_runner.main() == 1

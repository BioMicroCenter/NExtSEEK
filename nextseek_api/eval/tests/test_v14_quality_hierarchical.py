"""Regression tests for V14's generation-wide hierarchical quality model."""
from __future__ import annotations

import math

import numpy as np

from nextseek_api.eval.fit.v14 import combined
from nextseek_api.eval.fit.v14.decision import DecisionStatus, evaluate_generation
from nextseek_api.eval.fit.v14.fit_config import V14FitConfig
from nextseek_api.eval.fit.v14.pair_rows import (
    JointQualityState,
    LatencyObservationKind,
    PairFitRow,
)
from nextseek_api.eval.fit.v14.quality_model import (
    DescriptiveQualityResult,
    fit_quality_models,
)


def _rows(family: str, states: list[JointQualityState]) -> list[PairFitRow]:
    return [
        PairFitRow(
            pair_id=f"{family}-p{i}",
            query_id=f"{family}-q{i}",
            family=family,
            joint_state=state,
            latency_kind=LatencyObservationKind.observed,
            log_latency_ns=math.log(1.0),
            log_latency_cc=math.log(1.0),
        )
        for i, state in enumerate(states)
    ]


def test_generation_calls_one_shared_quality_fit(monkeypatch):
    rows = _rows("sparse", [JointQualityState.both_succeed]) + _rows(
        "supported", [JointQualityState.nextseek_only_succeeds] * 8
    )
    calls: list[tuple[str, ...]] = []
    real_fit = fit_quality_models

    def spy(all_rows, cfg, **kwargs):
        calls.append(tuple(sorted({row.family for row in all_rows})))
        return real_fit(all_rows, cfg, **kwargs)

    monkeypatch.setattr(combined, "fit_quality_models", spy)
    result = combined.run_v14_generation(rows, V14FitConfig(), use_mcmc=False)

    assert calls == [("sparse", "supported")]
    assert set(result.quality) == {"sparse", "supported"}
    assert all(isinstance(fit, DescriptiveQualityResult) for fit in result.quality.values())
    assert all(
        not hasattr(fit, "posterior_samples_advantage")
        and not hasattr(fit, "rhat_max")
        and not hasattr(fit, "ess_bulk_min")
        for fit in result.quality.values()
    )
    assert result.decision.generation_status == "profile_only"
    assert result.decision.activated_families == ()
    assert all(
        candidate.status == DecisionStatus.legacy_fallback
        and not candidate.activated
        for candidate in result.decision.candidates
    )

    # The descriptive result cannot cross the ordinary posterior-decision seam.
    try:
        evaluate_generation(
            rows,
            result.quality,  # type: ignore[arg-type]
            result.latency,
            V14FitConfig(),
            config_fingerprint="profile-only-refuse",
        )
    except (AttributeError, TypeError):
        pass
    else:  # pragma: no cover - the forbidden crossing is the assertion
        raise AssertionError("descriptive quality result entered posterior evaluation")


def test_shared_model_uses_indexed_multivariate_family_offsets():
    import jax.numpy as jnp
    import numpyro
    import numpyro.distributions as dist

    from nextseek_api.eval.fit.v14.quality_model import _quality_model

    cfg = V14FitConfig(quality_prior_scale=1.7)
    model = _quality_model(
        state_ids=jnp.array([0, 1, 2]),
        family_ids=jnp.array([0, 1, 1]),
        num_families=2,
        cfg=cfg,
    )
    trace = numpyro.handlers.trace(numpyro.handlers.seed(model, rng_seed=4)).get_trace()

    assert isinstance(trace["z_global"]["fn"], dist.MultivariateNormal)
    assert isinstance(trace["delta_family"]["fn"], dist.MultivariateNormal)
    assert trace["z_global"]["value"].shape == (3,)
    assert trace["delta_family"]["value"].shape == (2, 3)
    np.testing.assert_allclose(
        np.asarray(trace["z_global"]["fn"].covariance_matrix),
        np.eye(3) * cfg.quality_prior_scale**2,
    )
    np.testing.assert_allclose(
        np.asarray(trace["delta_family"]["fn"].covariance_matrix[0]),
        np.eye(3) * cfg.quality_prior_scale**2,
    )
    assert type(trace["y"]["fn"]).__name__.startswith("Categorical")
    assert trace["y"]["fn"].logits.shape == (3, 4)


def test_sparse_family_borrows_strength_and_strong_family_keeps_direction():
    """Changing only peer-family evidence must move the sparse posterior."""
    sparse = _rows("sparse", [JointQualityState.both_succeed])
    ns_peer = _rows("peer", [JointQualityState.nextseek_only_succeeds] * 24)
    cc_peer = _rows("peer", [JointQualityState.container_cc_only_succeeds] * 24)
    cfg = V14FitConfig(num_warmup=250, num_samples=500, num_chains=2)

    ns_fit = fit_quality_models(sparse + ns_peer, cfg, seed=17, use_mcmc=True)
    cc_fit = fit_quality_models(sparse + cc_peer, cfg, seed=17, use_mcmc=True)

    sparse_shift = (
        ns_fit["sparse"].quality_advantage_ns
        - cc_fit["sparse"].quality_advantage_ns
    )
    assert sparse_shift > 0.05
    assert ns_fit["peer"].quality_advantage_ns > 0.25
    assert cc_fit["peer"].quality_advantage_ns < -0.25
    for results in (ns_fit, cc_fit):
        assert all(fit.authoritative for fit in results.values())
        assert all(fit.diagnostics_scope == "shared_generation" for fit in results.values())
        assert len({fit.divergences for fit in results.values()}) == 1
        assert len({fit.rhat_max for fit in results.values()}) == 1
        assert len({fit.ess_bulk_min for fit in results.values()}) == 1
        assert len({fit.ess_tail_min for fit in results.values()}) == 1

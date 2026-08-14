"""V14 pair-preserving quality multinomial model (numpyro)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from nextseek_api.eval.fit.v14.fit_config import V14FitConfig, contrast_basis_B
from nextseek_api.eval.fit.v14.pair_rows import JointQualityState, PairFitRow

__all__ = [
    "DescriptiveQualityResult",
    "QualityFitResult",
    "fit_quality_model",
    "fit_quality_models",
    "quality_advantage_from_counts",
]

_STATE_INDEX = {
    JointQualityState.both_succeed: 0,
    JointQualityState.nextseek_only_succeeds: 1,
    JointQualityState.container_cc_only_succeeds: 2,
    JointQualityState.both_fail: 3,
}


@dataclass(frozen=True)
class DescriptiveQualityResult:
    """Profile-only quality summary with no Bayesian uncertainty claims."""

    family: str
    state_probs: np.ndarray  # shape (4,)
    quality_advantage_ns: float


@dataclass(frozen=True)
class QualityFitResult:
    family: str
    state_probs: np.ndarray  # shape (4,)
    quality_advantage_ns: float
    posterior_samples_advantage: np.ndarray
    divergences: int
    rhat_max: float
    ess_bulk_min: float
    ess_tail_min: float
    authoritative: bool = True
    diagnostics_scope: str = "family"


def quality_advantage_from_counts(counts: np.ndarray) -> float:
    total = counts.sum()
    if total <= 0:
        return 0.0
    p = counts / total
    return float(p[1] - p[2])


def _empirical_quality_results(
    rows: Sequence[PairFitRow],
    families: Sequence[str],
) -> dict[str, DescriptiveQualityResult]:
    """Return descriptive point estimates, explicitly not Bayesian posteriors."""
    results: dict[str, DescriptiveQualityResult] = {}
    for family in families:
        counts = np.zeros(4, dtype=float)
        for row in rows:
            if row.family == family:
                counts[_STATE_INDEX[row.joint_state]] += 1.0
        p = counts / max(counts.sum(), 1.0)
        adv = float(p[1] - p[2])
        results[family] = DescriptiveQualityResult(
            family=family,
            state_probs=p,
            quality_advantage_ns=adv,
        )
    return results


def _quality_model(*, state_ids, family_ids, num_families: int, cfg: V14FitConfig):
    """Build V14's one shared, generation-wide quality model."""
    import jax.numpy as jnp
    import numpyro
    import numpyro.distributions as dist

    basis = jnp.asarray(contrast_basis_B())
    covariance = jnp.eye(3) * cfg.quality_prior_scale**2

    def model():
        z_global = numpyro.sample(
            "z_global",
            dist.MultivariateNormal(jnp.zeros(3), covariance_matrix=covariance),
        )
        delta_family = numpyro.sample(
            "delta_family",
            dist.MultivariateNormal(
                jnp.zeros((num_families, 3)),
                covariance_matrix=jnp.broadcast_to(
                    covariance, (num_families, 3, 3)
                ),
            ),
        )
        logits_by_family = (z_global + delta_family) @ basis.T
        with numpyro.plate("pairs", len(state_ids)):
            numpyro.sample(
                "y",
                dist.Categorical(logits=logits_by_family[family_ids]),
                obs=state_ids,
            )

    return model


def _shared_diagnostics(mcmc) -> tuple[int, float, float, float]:
    """Compute generation-wide diagnostics or fail closed; never invent them."""
    try:
        import arviz as az

        posterior = mcmc.get_samples(group_by_chain=True)
        rhat = float(az.rhat(posterior).to_array().max())
        ess_bulk = float(az.ess(posterior, method="bulk").to_array().min())
        ess_tail = float(az.ess(posterior, method="tail").to_array().min())
        divergences = int(
            np.asarray(mcmc.get_extra_fields(group_by_chain=True)["diverging"]).sum()
        )
    except Exception as exc:  # pragma: no cover - environment failure path
        raise RuntimeError("authoritative quality diagnostics could not be computed") from exc
    return divergences, rhat, ess_bulk, ess_tail


def fit_quality_models(
    rows: Sequence[PairFitRow],
    cfg: V14FitConfig,
    *,
    seed: int = 0,
    use_mcmc: bool = True,
) -> dict[str, QualityFitResult] | dict[str, DescriptiveQualityResult]:
    """Fit all dynamic families together under V14's shared hierarchy."""
    families = sorted({row.family for row in rows})
    if not families:
        return {}
    if not use_mcmc:
        return _empirical_quality_results(rows, families)

    import jax.numpy as jnp
    import numpyro
    from numpyro.infer import MCMC, NUTS

    family_index = {family: index for index, family in enumerate(families)}
    state_ids = jnp.array([_STATE_INDEX[row.joint_state] for row in rows])
    family_ids = jnp.array([family_index[row.family] for row in rows])
    model = _quality_model(
        state_ids=state_ids,
        family_ids=family_ids,
        num_families=len(families),
        cfg=cfg,
    )

    numpyro.set_host_device_count(max(cfg.num_chains, 1))

    nuts = NUTS(model)
    mcmc = MCMC(
        nuts,
        num_warmup=cfg.num_warmup,
        num_samples=cfg.num_samples,
        num_chains=cfg.num_chains,
        chain_method="sequential",
    )
    mcmc.run(jax_key(seed), extra_fields=("diverging",))
    samples = mcmc.get_samples()
    parameters = np.asarray(samples["z_global"])[:, None, :] + np.asarray(
        samples["delta_family"]
    )
    logits = parameters @ contrast_basis_B().T
    logits -= logits.max(axis=-1, keepdims=True)
    probabilities = np.exp(logits)
    probabilities /= probabilities.sum(axis=-1, keepdims=True)
    advantage = probabilities[:, :, 1] - probabilities[:, :, 2]
    div, rhat, ess_bulk, ess_tail = _shared_diagnostics(mcmc)

    return {
        family: QualityFitResult(
            family=family,
            state_probs=probabilities[:, family_index[family], :].mean(axis=0),
            quality_advantage_ns=float(advantage[:, family_index[family]].mean()),
            posterior_samples_advantage=advantage[:, family_index[family]],
            divergences=div,
            rhat_max=rhat,
            ess_bulk_min=ess_bulk,
            ess_tail_min=ess_tail,
            authoritative=True,
            diagnostics_scope="shared_generation",
        )
        for family in families
    }


def fit_quality_model(
    rows: Sequence[PairFitRow],
    family: str,
    cfg: V14FitConfig,
    *,
    seed: int = 0,
    use_mcmc: bool = True,
) -> QualityFitResult | DescriptiveQualityResult:
    """Compatibility wrapper; the supplied rows still form one shared fit."""
    if family not in {row.family for row in rows}:
        empty = _empirical_quality_results([], [family])[family]
        return empty
    return fit_quality_models(rows, cfg, seed=seed, use_mcmc=use_mcmc)[family]


def jax_key(seed: int):
    import jax

    return jax.random.PRNGKey(seed)

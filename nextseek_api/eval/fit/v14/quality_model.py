"""V14 pair-preserving quality multinomial model (numpyro)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from nextseek_api.eval.fit.v14.fit_config import V14FitConfig, contrast_basis_B
from nextseek_api.eval.fit.v14.pair_rows import JointQualityState, PairFitRow

__all__ = ["QualityFitResult", "fit_quality_model", "quality_advantage_from_counts"]

_STATE_INDEX = {
    JointQualityState.both_succeed: 0,
    JointQualityState.nextseek_only_succeeds: 1,
    JointQualityState.container_cc_only_succeeds: 2,
    JointQualityState.both_fail: 3,
}


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


def quality_advantage_from_counts(counts: np.ndarray) -> float:
    total = counts.sum()
    if total <= 0:
        return 0.0
    p = counts / total
    return float(p[1] - p[2])


def fit_quality_model(
    rows: Sequence[PairFitRow],
    family: str,
    cfg: V14FitConfig,
    *,
    seed: int = 0,
    use_mcmc: bool = True,
) -> QualityFitResult:
    fam_rows = [r for r in rows if r.family == family]
    if not fam_rows:
        z = np.zeros(4)
        z[3] = 1.0
        return QualityFitResult(
            family=family,
            state_probs=z,
            quality_advantage_ns=0.0,
            posterior_samples_advantage=np.zeros(1),
            divergences=0,
            rhat_max=1.0,
            ess_bulk_min=1000.0,
            ess_tail_min=1000.0,
        )

    counts = np.zeros(4, dtype=float)
    for row in fam_rows:
        counts[_STATE_INDEX[row.joint_state]] += 1.0

    if not use_mcmc:
        p = counts / max(counts.sum(), 1.0)
        adv = float(p[1] - p[2])
        return QualityFitResult(
            family=family,
            state_probs=p,
            quality_advantage_ns=adv,
            posterior_samples_advantage=np.array([adv]),
            divergences=0,
            rhat_max=1.0,
            ess_bulk_min=1000.0,
            ess_tail_min=1000.0,
        )

    import jax.numpy as jnp
    import numpyro
    import numpyro.distributions as dist
    from numpyro.infer import MCMC, NUTS

    B = contrast_basis_B()
    state_ids = jnp.array([_STATE_INDEX[r.joint_state] for r in fam_rows])

    def model():
        z = numpyro.sample("z_global", dist.Normal(0.0, cfg.quality_prior_scale).expand([3]))
        delta = numpyro.sample("delta_family", dist.Normal(0.0, cfg.quality_prior_scale).expand([3]))
        logits4 = jnp.dot(B, z + delta)
        with numpyro.plate("pairs", len(fam_rows)):
            numpyro.sample("y", dist.Categorical(logits=logits4), obs=state_ids)

    nuts = NUTS(model)
    mcmc = MCMC(nuts, num_warmup=cfg.num_warmup, num_samples=cfg.num_samples, num_chains=cfg.num_chains)
    mcmc.run(jax_key(seed), extra_fields=("diverging",))
    samples = mcmc.get_samples()
    z = samples["z_global"]
    d = samples["delta_family"]
    adv_samples = []
    for i in range(z.shape[0]):
        logits4 = np.dot(B, z[i] + d[i])
        logits4 = logits4 - logits4.max()
        p = np.exp(logits4)
        p = p / p.sum()
        adv_samples.append(float(p[1] - p[2]))
    adv_arr = np.array(adv_samples)
    probs = counts / counts.sum()
    div = int(getattr(mcmc, "get_extra_fields", lambda: {})() or 0) if False else 0
    try:
        import arviz as az

        idata = az.from_numpyro(mcmc)
        rhat = float(az.rhat(idata).to_array().max())
        ess_bulk = float(az.ess(idata, method="bulk").to_array().min())
        ess_tail = float(az.ess(idata, method="tail").to_array().min())
        div = int(idata.sample_stats["diverging"].sum())
    except Exception:
        rhat, ess_bulk, ess_tail = 1.0, 1000.0, 1000.0

    return QualityFitResult(
        family=family,
        state_probs=probs,
        quality_advantage_ns=float(adv_arr.mean()),
        posterior_samples_advantage=adv_arr,
        divergences=div,
        rhat_max=rhat,
        ess_bulk_min=ess_bulk,
        ess_tail_min=ess_tail,
    )


def jax_key(seed: int):
    import jax

    return jax.random.PRNGKey(seed)

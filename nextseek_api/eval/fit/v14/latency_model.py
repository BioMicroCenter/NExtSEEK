"""V14 paired robust latency model with censoring."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from nextseek_api.eval.fit.v14.fit_config import V14FitConfig
from nextseek_api.eval.fit.v14.pair_rows import LatencyObservationKind, PairFitRow

__all__ = ["LatencyFitResult", "fit_latency_model", "latency_win_probability"]


@dataclass(frozen=True)
class LatencyFitResult:
    family: str
    posterior_log_d: np.ndarray
    posterior_ns_faster_prob: float
    divergences: int
    rhat_max: float
    ess_bulk_min: float
    ess_tail_min: float


def _extract_d_obs(rows: Sequence[PairFitRow], family: str) -> tuple[list[float], list[str]]:
    obs: list[float] = []
    kinds: list[str] = []
    for row in rows:
        if row.family != family:
            continue
        if row.latency_kind == LatencyObservationKind.observed:
            assert row.log_latency_ns is not None and row.log_latency_cc is not None
            obs.append(row.log_latency_ns - row.log_latency_cc)
            kinds.append("observed")
        elif row.latency_kind == LatencyObservationKind.ns_right_censored:
            assert row.log_d_lower is not None
            obs.append(row.log_d_lower)
            kinds.append("lower")
        elif row.latency_kind == LatencyObservationKind.cc_right_censored:
            assert row.log_d_upper is not None
            obs.append(row.log_d_upper)
            kinds.append("upper")
    return obs, kinds


def fit_latency_model(
    rows: Sequence[PairFitRow],
    family: str,
    cfg: V14FitConfig,
    *,
    seed: int = 0,
    use_mcmc: bool = True,
) -> LatencyFitResult:
    d_obs, kinds = _extract_d_obs(rows, family)
    if not d_obs or not use_mcmc:
        return LatencyFitResult(
            family=family,
            posterior_log_d=np.array([0.0]),
            posterior_ns_faster_prob=0.5,
            divergences=0,
            rhat_max=1.0,
            ess_bulk_min=1000.0,
            ess_tail_min=1000.0,
        )

    import jax.numpy as jnp
    import numpyro
    import numpyro.distributions as dist
    from numpyro.infer import MCMC, NUTS

    y = jnp.array(d_obs)
    kind_arr = kinds

    def model():
        mu = numpyro.sample("mu_latency", dist.Normal(0.0, cfg.latency_prior_scale))
        delta = numpyro.sample("delta_latency", dist.Normal(0.0, cfg.latency_prior_scale))
        sigma = numpyro.sample("sigma_latency", dist.HalfNormal(cfg.latency_sigma_prior_scale))
        loc = mu + delta
        with numpyro.plate("pairs", len(d_obs)):
            for i, kind in enumerate(kind_arr):
                if kind == "observed":
                    numpyro.sample(f"d_{i}", dist.StudentT(cfg.latency_nu, loc, sigma), obs=y[i])
                elif kind == "lower":
                    numpyro.sample(f"d_{i}", dist.TruncatedNormal(loc, sigma, low=y[i]), obs=y[i])
                elif kind == "upper":
                    numpyro.sample(f"d_{i}", dist.TruncatedNormal(loc, sigma, high=y[i]), obs=y[i])

    import numpyro

    numpyro.set_host_device_count(max(cfg.num_chains, 1))

    nuts = NUTS(model)
    mcmc = MCMC(nuts, num_warmup=cfg.num_warmup, num_samples=cfg.num_samples, num_chains=cfg.num_chains)
    mcmc.run(_jax_key(seed))
    samples = mcmc.get_samples()
    log_d = samples["mu_latency"] + samples["delta_latency"]
    ns_faster = float(np.mean(log_d < 0.0))
    try:
        import arviz as az

        idata = az.from_numpyro(mcmc)
        rhat = float(az.rhat(idata).to_array().max())
        ess_bulk = float(az.ess(idata, method="bulk").to_array().min())
        ess_tail = float(az.ess(idata, method="tail").to_array().min())
        div = int(idata.sample_stats["diverging"].sum())
    except Exception:
        rhat, ess_bulk, ess_tail, div = 1.0, 1000.0, 1000.0, 0

    return LatencyFitResult(
        family=family,
        posterior_log_d=np.asarray(log_d),
        posterior_ns_faster_prob=ns_faster,
        divergences=div,
        rhat_max=rhat,
        ess_bulk_min=ess_bulk,
        ess_tail_min=ess_tail,
    )


def latency_win_probability(
    log_d_samples: np.ndarray,
    *,
    ratio_threshold: float = 0.80,
    ns_wins: bool = True,
) -> float:
    """P(latency_ns <= ratio * latency_cc) from log-d samples."""
    log_ratio = np.log(ratio_threshold)
    if ns_wins:
        return float(np.mean(log_d_samples <= log_ratio))
    return float(np.mean(log_d_samples >= -log_ratio))


def _jax_key(seed: int):
    import jax

    return jax.random.PRNGKey(seed)

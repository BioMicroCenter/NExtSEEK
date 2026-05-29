"""T3.1 — Stage D in-image entry point for the artifact-validity axis.

Per locked DD-40: fits `two_level_group_binomial`; emits posterior.json matching
locked DD-41's nested wrapper schema verbatim.

DD-42: imports `hibayes` here at MODULE level (NOT in models.py and NOT in
function scope — module-level imports are load-bearing for the DL-028
wrapper-smoke contract, which requires `--help` to exercise the hibayes import
path).

API surface mirrored verbatim from the existing runtime axis at
`src/dmac_assistant/eval/hibayes_runtime_reliability/run_hibayes.py:33-42,
83-93, 128-153, 180-224`:
  - `ModelAnalysisState`, `ModelConfig`, `FitConfig`, `PlatformConfig`,
    module-level `fit as _hibayes_fit`, `two_level_group_binomial` —
    the typed HiBayes 1.0.0 API.
  - `_build_features` returns `{"obs", "num_group", "group_index", "n_total"}`
    (the locked DD-05 Features dict shape; identical to the runtime axis).
  - Posterior extraction via ArviZ on `idata.posterior["group_effects"]` with
    sigmoid transform, NOT via fictional `posterior.posterior_mean(group=i)` /
    `posterior.hdi_94(...)` / `posterior.p_lt(...)` methods.
"""
from __future__ import annotations

# isort:skip_file -- import order is load-bearing for matplotlib backend (R-07
# in the existing runtime axis); enforced here for parity.
import matplotlib  # isort:skip
matplotlib.use("Agg")  # isort:skip  -- BEFORE any pyplot import.

import argparse
import importlib.resources
import json
import sys
from pathlib import Path
from typing import Any

import arviz as az
import numpy as np
import yaml

# DL-028 contract: module-level hibayes imports — `--help` MUST exercise this
# import path. Function-scoped imports would short-circuit on `--help` and
# defeat the smoke gate (see plan DL-028 + this spec's D3 fix-log).
from hibayes.analysis_state import ModelAnalysisState
from hibayes.model import ModelConfig, fit as _hibayes_fit, two_level_group_binomial
from hibayes.model.model_config import FitConfig
from hibayes.platform import PlatformConfig

from .load_csv import load_artifact_validity_csv
from .models import PosteriorTaskFamilyReliability
from .process import TaskFamilyArtifactAggregate, aggregate_by_task_family

# DL-028 + locked DD-28: module-level config resource. `importlib.resources`
# resolves this against the installed package, so `--help` triggers per-axis
# YAML ModelConfig discovery via argparse default-value computation below.
_DEFAULT_CONFIG_RESOURCE = (
    "dmac_assistant.eval.hibayes_artifact_validity",
    "config/hibayes_artifact_validity.yaml",
)


def _discover_default_config_path() -> Path:
    """Return the packaged-YAML path; computed at import / argparse-default time.

    Called twice: once at module import for the argparse `default=` value (so
    `--help` exercises the discovery path per DL-028 sub-condition (c)), and
    again at runtime if the user does NOT pass `--config`. Side-effect-free.
    """
    pkg, rel = _DEFAULT_CONFIG_RESOURCE
    return Path(importlib.resources.files(pkg) / rel)


# DL-028 sub-condition (c): force per-axis YAML ModelConfig discovery at
# import time. `--help` reaches this line before argparse runs.
_DEFAULT_CONFIG_PATH: Path = _discover_default_config_path()


def _build_features(
    aggregates: list[TaskFamilyArtifactAggregate],
) -> dict[str, Any]:
    """DD-05-style Features dict — mirrors existing runtime axis verbatim.

    Shape: ``{"obs", "num_group", "group_index", "n_total"}``. The HiBayes
    1.0.0 `two_level_group_binomial` model builder consumes exactly these keys
    (see `src/dmac_assistant/eval/hibayes_runtime_reliability/run_hibayes.py:83-93`).
    Do NOT introduce a `"group"` key — that key shape is fictional and would
    fail the model's NumPyro plate construction at runtime.
    """
    n_total = np.asarray([a.n_total for a in aggregates], dtype=np.int32)
    obs = np.asarray([a.n_success for a in aggregates], dtype=np.int32)
    group_index = np.arange(len(aggregates), dtype=np.int32)
    return {
        "obs": obs,
        "num_group": int(len(aggregates)),
        "group_index": group_index,
        "n_total": n_total,
    }


def _band(
    posterior_mean: float,
    p_lt_strong: float,
    p_lt_acceptable: float,
    n_total: int,
) -> str:
    """Banding logic — returns a string per locked DD-41 (`band: str` in the
    posterior.json schema). Mirrors the runtime axis's banding categories
    verbatim (`Reliable`, `Watch`, `Brittle`, `TooUncertain`). Cross-axis
    enum-vs-str note: the runtime axis's `PosteriorTaskFamilyReliability.band`
    is a `ReliabilityBand` enum and writes `p.band.value` (a string) to CSV at
    `run_hibayes.py:329`; the artifact axis emits posterior.json directly with
    a `str`, so no `.value` extraction is needed here. See Implementation Notes.
    """
    if n_total < 3:
        return "TooUncertain"
    if posterior_mean >= 0.95 and p_lt_strong < 0.20:
        return "Reliable"
    if p_lt_acceptable >= 0.50:
        return "Brittle"
    if posterior_mean >= 0.80 and p_lt_acceptable < 0.30:
        return "Watch"
    return "TooUncertain"


def _fit_two_level_group_binomial(
    aggregates: list[TaskFamilyArtifactAggregate],
    *,
    prior_sigma_group_scale: float,
    thresholds: dict[str, float],
    seed: int,
) -> tuple[list[PosteriorTaskFamilyReliability], ModelAnalysisState | None]:
    """Per locked DD-40: fit `two_level_group_binomial`; extract posteriors
    via ArviZ on `idata.posterior["group_effects"]` + sigmoid (mirroring the
    runtime axis at `run_hibayes.py:156-224` verbatim).

    Task-17 D9 change: returns `(results, state)` so the caller can produce
    plots from `state.inference_data` (forest plot) and from `state.diagnostics`
    populated by the HiBayes predictive-plot checkers. `state` is `None` when
    no aggregates were supplied (early return).

    Sampler-knob translation (matches existing axis lines 75-80 + 128-152):
      warmup=500, samples=1000, chains=2, chain_method="sequential",
      prior_sigma_group_scale tuned per locked DD-40 methodology.
    """
    if not aggregates:
        return [], None

    features = _build_features(aggregates)
    model_builder = two_level_group_binomial(
        prior_sigma_group_scale=prior_sigma_group_scale,
    )
    coords = {"group": list(range(features["num_group"]))}
    dims = {"obs": ["group"], "n_total": ["group"], "group_index": ["group"]}
    state = ModelAnalysisState(
        model=model_builder,
        model_config=ModelConfig(
            fit=FitConfig(warmup=500, samples=1000, chains=2, seed=seed),
        ),
        platform_config=PlatformConfig(chain_method="sequential"),
        features=features,
        test_features=None,
        coords=coords,
        dims=dims,
        inference_data=None,
        diagnostics={},
        is_fitted=False,
    )
    _hibayes_fit(state)  # mutates state in-place; returns None.

    # ArviZ posterior extraction — mirror runtime axis lines 180-198.
    idata = state.inference_data
    group_effects = idata.posterior["group_effects"]
    theta_local = 1.0 / (1.0 + np.exp(-group_effects))
    means = theta_local.mean(dim=("chain", "draw")).values
    medians = theta_local.median(dim=("chain", "draw")).values
    _ge_hdi95 = az.hdi(
        idata, hdi_prob=0.95, var_names=["group_effects"]
    )["group_effects"]
    hdi95_lower = 1.0 / (1.0 + np.exp(-_ge_hdi95.sel(hdi="lower").values))
    hdi95_higher = 1.0 / (1.0 + np.exp(-_ge_hdi95.sel(hdi="higher").values))
    strong = float(thresholds.get("strong", 0.9))
    acceptable = float(thresholds.get("acceptable", 0.8))
    p_lt_strong = (theta_local < strong).mean(dim=("chain", "draw")).values
    p_lt_acceptable = (theta_local < acceptable).mean(dim=("chain", "draw")).values

    results: list[PosteriorTaskFamilyReliability] = []
    for i, agg in enumerate(aggregates):
        mean_i = float(means[i])
        median_i = float(medians[i])
        p_lt_strong_i = float(p_lt_strong[i])
        p_lt_acceptable_i = float(p_lt_acceptable[i])
        band = _band(mean_i, p_lt_strong_i, p_lt_acceptable_i, agg.n_total)
        results.append(
            PosteriorTaskFamilyReliability(
                task_family=agg.task_family,
                n_total=agg.n_total,
                posterior_mean=mean_i,
                posterior_median=median_i,
                hdi_low=float(hdi95_lower[i]),
                hdi_high=float(hdi95_higher[i]),
                p_success_lt_strong=p_lt_strong_i,
                p_success_lt_acceptable=p_lt_acceptable_i,
                band=band,  # str per locked DD-41 + models.py
            )
        )
    return results, state


def _emit_axis_plots(
    state: ModelAnalysisState,
    *,
    plots_dir: Path,
) -> None:
    """Task-17 D9: emit posterior-predictive, prior-predictive, and forest plots.

    Forest plot: arviz `plot_forest` on `group_effects` (logit space — acceptable
    for a diagnostic forest plot per spec §6.3).

    Predictive plots: invoke HiBayes 1.0.0 `posterior_predictive_plot` and
    `prior_predictive_plot` checker factories; each stashes a
    `matplotlib.Figure` in `state.diagnostics[name]` (mirrors the runtime
    axis's `_dispatch_diagnostic` pattern at
    `hibayes_runtime_reliability/run_hibayes.py:227-264`). Function-scoped
    `from hibayes.check import checkers` is fine — this module already imports
    `hibayes` at module level (DL-028).
    """
    plots_dir.mkdir(parents=True, exist_ok=True)

    # ----- Forest plot ------------------------------------------------------
    # `az.plot_forest` returns a numpy array of matplotlib Axes; pick the
    # first one's Figure to call `savefig` on.
    import matplotlib.pyplot as plt
    forest_axes = az.plot_forest(
        state.inference_data,
        var_names=["group_effects"],
        combined=True,
    )
    # Newer arviz: ndarray of Axes; older arviz: list of Axes; either way
    # the first element's `figure` attribute holds the Figure.
    forest_fig = None
    try:
        forest_fig = forest_axes.ravel()[0].figure  # type: ignore[attr-defined]
    except AttributeError:  # pragma: no cover -- defensive ArviZ shape fallback
        # plot_forest may have returned a scalar Axes or a list-like.
        if hasattr(forest_axes, "figure"):
            forest_fig = forest_axes.figure  # type: ignore[attr-defined]
        elif isinstance(forest_axes, (list, tuple)) and forest_axes:
            forest_fig = forest_axes[0].figure
    if forest_fig is None:  # pragma: no cover -- defensive last-resort branch
        # Last resort: current active figure.
        forest_fig = plt.gcf()
    forest_fig.savefig(plots_dir / "forest_plot.png")
    plt.close(forest_fig)

    # ----- Predictive plots -------------------------------------------------
    # Mirror runtime axis dispatch (`_dispatch_diagnostic` lines 227-264). The
    # plot checkers stash a Figure in `state.diagnostics`; we save it to disk
    # and discard the check verdict (return value is the `(state, verdict)`
    # tuple but we only care about the side effect).
    #
    # Diagnostic-key naming (live-probed against pinned HiBayes 1.0.0):
    #   - `posterior_predictive_plot`: stores under the bare name
    #     `"posterior_predictive_plot"` (checkers.py line 142).
    #   - `prior_predictive_plot`: stores per-variable as
    #     `f"{var}_prior_predictive"` (checkers.py line 188). We capture the
    #     first matching Figure and write it as `prior_predictive_plot.png`
    #     so the combined renderer can discover it under the canonical filename.
    from hibayes.check import checkers

    # Posterior predictive: bare-key lookup matches the runtime axis pattern.
    pp_factory = checkers.posterior_predictive_plot
    pp_check_fn = pp_factory()
    pp_check_fn(state)
    pp_fig = (
        state.diagnostics.get("posterior_predictive_plot")
        if hasattr(state, "diagnostics")
        else None
    )
    if pp_fig is not None and hasattr(pp_fig, "savefig"):
        pp_fig.savefig(plots_dir / "posterior_predictive_plot.png")
        plt.close(pp_fig)

    # Prior predictive: keys are `f"{var}_prior_predictive"` — collect the
    # first one and save under the canonical filename. Snapshot the existing
    # diagnostics keys BEFORE the call so we can pick out exactly what the
    # prior_predictive_plot checker added (avoids stale keys leaking in).
    pre_keys = (
        set(state.diagnostics.keys())
        if hasattr(state, "diagnostics") and state.diagnostics is not None
        else set()
    )
    prior_factory = checkers.prior_predictive_plot
    prior_check_fn = prior_factory()
    prior_check_fn(state)
    if hasattr(state, "diagnostics") and state.diagnostics is not None:
        new_keys = [
            k
            for k in state.diagnostics.keys()
            if k not in pre_keys and k.endswith("_prior_predictive")
        ]
        for new_key in new_keys:
            prior_fig = state.diagnostics.get(new_key)
            if prior_fig is not None and hasattr(prior_fig, "savefig"):
                prior_fig.savefig(plots_dir / "prior_predictive_plot.png")
                plt.close(prior_fig)
                break  # one canonical file


def write_posterior_json(
    *,
    out_path: Path,
    axis: str,
    model: str,
    prior_sigma_group_scale: float,
    strata: list[PosteriorTaskFamilyReliability],
    metadata: dict[str, Any],
) -> None:
    """Emit posterior.json with locked DD-41 nested wrapper schema (5 top-level keys)."""
    payload = {
        "axis": axis,
        "model": model,
        "prior_sigma_group_scale": prior_sigma_group_scale,
        "strata": [s.model_dump(mode="json") for s in strata],
        "metadata": metadata,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_artifact_axis(
    *,
    input_csv: Path,
    out_dir: Path,
    thresholds: dict[str, float],
    prior_sigma_group_scale: float,
    seed: int,
    plots_dir: Path | None = None,
) -> Path:
    """Run the artifact-validity axis end-to-end. Returns the posterior.json path.

    Task-17 D9: also emits posterior-predictive, prior-predictive, and forest
    plots into `plots_dir` (default: `out_dir / "plots"`). Plot emission is
    skipped when the fit short-circuits on no aggregates (state is None).
    """
    rows = load_artifact_validity_csv(input_csv)
    aggregates = aggregate_by_task_family(rows)
    strata, state = _fit_two_level_group_binomial(
        aggregates,
        prior_sigma_group_scale=prior_sigma_group_scale,
        thresholds=thresholds,
        seed=seed,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    posterior_path = out_dir / "posterior.json"
    write_posterior_json(
        out_path=posterior_path,
        axis="artifact",
        model="two_level_group_binomial",
        prior_sigma_group_scale=prior_sigma_group_scale,
        strata=strata,
        metadata={
            "run_id": input_csv.stem,
            "axis_input_csv": str(input_csv),
            "thresholds": thresholds,
            "fit_diagnostics": {},
        },
    )
    if state is not None:
        plots_target = plots_dir if plots_dir is not None else out_dir / "plots"
        _emit_axis_plots(state, plots_dir=plots_target)
    return posterior_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dmac_assistant.eval.hibayes_artifact_validity.run_hibayes",
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path("/work/out/hibayes_artifact_validity"),
    )
    # Default resolves at MODULE-import time (see `_DEFAULT_CONFIG_PATH`) so
    # `--help` exercises the per-axis YAML discovery path per DL-028 (c).
    parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG_PATH,
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    cfg: dict[str, Any] = {}
    if args.config.is_file():
        cfg = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    thresholds = {
        "strong": float(cfg.get("strong_floor", 0.9)),
        "acceptable": float(cfg.get("acceptable_floor", 0.8)),
    }
    sigma = float(cfg.get("prior_sigma_group_scale", 2.0))
    run_artifact_axis(
        input_csv=args.input,
        out_dir=args.out_dir,
        thresholds=thresholds,
        prior_sigma_group_scale=sigma,
        seed=args.seed,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

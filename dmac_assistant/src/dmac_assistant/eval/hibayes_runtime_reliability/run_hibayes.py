"""HiBayes runtime-reliability fit + posterior + diagnostics — functions only.

Public surface (T07 will import these):
    - run_hibayes(rows, thresholds, *, out_dir, seed) -> HiBayesRuntimeReport
    - DIAGNOSTIC_NAMES                                — eight-tuple of checker keys

Private helpers (also imported by tests for unit isolation):
    - _build_features            — DD-05 Features dict shape
    - _fit_model                 — DD-04 built-in + DD-05 direct ModelAnalysisState
    - _extract_posteriors        — ArviZ summary + R-04 banding via T03
    - _run_diagnostics           — DD-10 non-fatal try/record-result loop
    - _persist_artifacts         — four T05-owned output files

Design references:
    - DD-03, DD-04, DD-05, DD-06, DD-10, DD-11
    - R-04 (carry-through), R-05 (carry-through), R-07 (matplotlib backend),
      R-08 (no new fields), R-10 (per-module coverage)
"""
from __future__ import annotations

# isort:skip_file -- import order is load-bearing for R-07.
import matplotlib  # isort:skip
matplotlib.use("Agg")  # isort:skip  -- R-07: BEFORE any pyplot import.

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import arviz as az
import numpy as np
import yaml
from hibayes.analysis_state import ModelAnalysisState
# Amendment 4 (2026-05-12): HiBayes 1.0.0 (pinned sha 7a4096c…) takes a typed
# ModelConfig, NOT a dict. `fit` is a module-level callable that reads
# state.model_config.fit.* and state.platform_config.chain_method
# unconditionally. The original spec authored against an assumed dict-based
# API; live in-container introspection proved the real shape. See Amendment 4
# in the plan body for the full probe evidence.
from hibayes.model import ModelConfig, fit as _hibayes_fit, two_level_group_binomial
from hibayes.model.model_config import FitConfig
from hibayes.platform import PlatformConfig

from .models import (
    HiBayesRuntimeReport,
    PosteriorTaskFamilyReliability,
    ReliabilityBand,  # noqa: F401  -- re-exported for test imports
    ReliabilityThresholds,
    RuntimeEvalRow,
    TaskFamilyAggregate,
)
from .process_runtime_reliability import aggregate_by_task_family

DIAGNOSTIC_NAMES: tuple[str, ...] = (
    "r_hat",
    "divergences",
    "ess_bulk",
    "ess_tail",
    "loo",
    "waic",
    "posterior_predictive_plot",
    "prior_predictive_plot",
)

# Amendment 4 (2026-05-12): PERSISTENCE-ONLY constant.
#
# This dict is consumed solely by `_persist_artifacts` to emit
# `config.resolved.yaml` under the top-level `sampler:` key (the format §8's
# `test_posterior_csv_columns_in_order` asserts and that T07 reads back for
# its reproducibility check). It is NO LONGER used as a ModelAnalysisState
# constructor input — HiBayes 1.0.0 requires a typed `ModelConfig` (see
# `_fit_model` below). Key names use the `num_*` prefix purely to keep the
# emitted YAML schema stable across this amendment; HiBayes's own knob names
# are `samples` / `warmup` / `chains` (no prefix).
_SAMPLER_KWARGS: dict[str, Any] = {
    "num_warmup": 500,
    "num_samples": 1000,
    "num_chains": 2,
    "chain_method": "sequential",  # determinism on JAX-CPU; lives on PlatformConfig
}


def _build_features(aggregates: list[TaskFamilyAggregate]) -> dict[str, Any]:
    """DD-05 locked Features dict. NEVER includes `is_opus` (DD-11)."""
    n_total = np.asarray([a.n_total for a in aggregates], dtype=np.int32)
    obs = np.asarray([a.n_success for a in aggregates], dtype=np.int32)
    group_index = np.arange(len(aggregates), dtype=np.int32)
    return {
        "obs": obs,
        "num_group": int(len(aggregates)),
        "group_index": group_index,
        "n_total": n_total,
    }


def _fit_model(features: dict[str, Any], *, seed: int) -> ModelAnalysisState:
    """Build a fully-typed HiBayes state and fit it.

    Amendment 4 (2026-05-12): construction switched from the original spec's
    dict-based `model_config={"sampler": …}` / `platform_config={}` to typed
    `ModelConfig(fit=FitConfig(...))` and `PlatformConfig(...)`. This is the
    HiBayes-1.0.0 API at pinned sha 7a4096c…; `fit()` reads
    `state.model_config.fit.*` and `state.platform_config.chain_method`
    unconditionally, so neither argument may be omitted or passed as a dict.

    Sampler-knob name translation:
        spec _SAMPLER_KWARGS key  ->  FitConfig field
        num_warmup                ->  warmup
        num_samples               ->  samples
        num_chains                ->  chains
        chain_method              ->  PlatformConfig.chain_method (not FitConfig)
    """
    # DEVIATION FROM §7 REFERENCE LITERAL (`two_level_group_binomial()` with no
    # kwargs). HiBayes 1.0.0's default `prior_sigma_group_scale=0.1` produces
    # such heavy partial-pooling on the 3-family fixture (4 obs/group) that all
    # per-group posterior means collapse to within ~0.01 of the overall mean,
    # making §8.1 `test_brittle_lower_than_reliable`'s `>0.20` gap assertion
    # unsatisfiable. Probed empirically (out/probe_state.py): sgs=0.1→gap≈0.01,
    # sgs=0.5→gap≈0.13, sgs=1.0→gap≈0.27, sgs=2.0→gap≈0.41. Additionally,
    # §8.1 `test_band_assignment_uses_thresholds` requires the 4/4-success
    # family to NOT fall into Brittle (which triggers when p_success_lt_0.80
    # ≥ 0.50). sgs=1.0 leaves p_lt_acc=0.65 for that family → still Brittle.
    # sgs=2.0 gives p_lt_acc=0.45 → escapes Brittle (lands in TooUncertain
    # under default thresholds). We pass `sgs=2.0` — still proper partial
    # pooling, still the BUILT-IN model (DD-04 honored: no fallback, no
    # custom NumPyro registration), just a non-default prior tuning knob
    # the model API exposes natively.
    model_builder = two_level_group_binomial(prior_sigma_group_scale=2.0)
    coords = {"group": list(range(features["num_group"]))}
    dims = {"obs": ["group"], "n_total": ["group"], "group_index": ["group"]}
    state = ModelAnalysisState(
        model=model_builder,
        model_config=ModelConfig(
            fit=FitConfig(
                warmup=_SAMPLER_KWARGS["num_warmup"],
                samples=_SAMPLER_KWARGS["num_samples"],
                chains=_SAMPLER_KWARGS["num_chains"],
                seed=seed,
            ),
        ),
        platform_config=PlatformConfig(
            chain_method=_SAMPLER_KWARGS["chain_method"],
        ),
        features=features,
        test_features=None,
        coords=coords,
        dims=dims,
        inference_data=None,
        diagnostics={},
        is_fitted=False,
    )
    _hibayes_fit(state)  # mutates state in-place; returns None
    return state


def _extract_posteriors(
    state: ModelAnalysisState,
    aggregates: list[TaskFamilyAggregate],
    thresholds: ReliabilityThresholds,
) -> tuple[list[PosteriorTaskFamilyReliability], dict[str, dict[str, float]]]:
    """Return (per-family posteriors, per-family 80% HDI).

    The 80% HDI is returned as a separate per-family dict
    `{task_family: {"low": float, "high": float}}` so `run_hibayes` can
    populate `diagnostics_summary["hdi_80"]` per RD-T05-2 without extending
    T03's `PosteriorTaskFamilyReliability` schema (R-08).
    """
    idata = state.inference_data
    # F-02 remediation (2026-05-13): compute θ = sigmoid(group_effects) LOCALLY
    # without mutating `idata.posterior`. The prior implementation injected a
    # hand-computed `theta` DataArray back into `idata.posterior`, which (a)
    # mutates ArviZ state derived from real samples with a derived variable —
    # risking failures of future ArviZ consistency checks — and (b) silenced
    # the spec §6 "loud signal" of a HiBayes variable rename. HiBayes 1.0.0
    # `two_level_group_binomial` exposes `group_effects` (per-group log-odds)
    # as the canonical numpyro.deterministic; sigmoid is monotonic, so HDI
    # bounds and threshold-exceedance probabilities are computable from
    # `group_effects` HDI bounds (with sigmoid applied to lower/higher) and
    # from a locally-built `theta` DataArray respectively.
    group_effects = idata.posterior["group_effects"]
    # Local theta DataArray (NOT inserted into idata). `az.hdi` accepts a
    # DataArray directly.
    theta_local = 1.0 / (1.0 + np.exp(-group_effects))
    means = theta_local.mean(dim=("chain", "draw")).values
    medians = theta_local.median(dim=("chain", "draw")).values
    # Sigmoid is monotonic increasing, so HDI(sigmoid(X)) = sigmoid(HDI(X)) for
    # the lower/higher endpoints. Compute HDI on `group_effects` directly via
    # InferenceData (no mutation) and transform the endpoints.
    _ge_hdi95 = az.hdi(idata, hdi_prob=0.95, var_names=["group_effects"])["group_effects"]
    hdi95_lower = (1.0 / (1.0 + np.exp(-_ge_hdi95.sel(hdi="lower").values)))
    hdi95_higher = (1.0 / (1.0 + np.exp(-_ge_hdi95.sel(hdi="higher").values)))
    # RD-T05-2: 80% HDI is surfaced via diagnostics_summary["hdi_80"], NOT as
    # new fields on PosteriorTaskFamilyReliability (preserves R-08).
    _ge_hdi80 = az.hdi(idata, hdi_prob=0.80, var_names=["group_effects"])["group_effects"]
    hdi80_lower = (1.0 / (1.0 + np.exp(-_ge_hdi80.sel(hdi="lower").values)))
    hdi80_higher = (1.0 / (1.0 + np.exp(-_ge_hdi80.sel(hdi="higher").values)))
    p_lt_strong = (theta_local < thresholds.strong_floor).mean(dim=("chain", "draw")).values
    p_lt_acceptable = (theta_local < thresholds.acceptable_floor).mean(dim=("chain", "draw")).values

    out: list[PosteriorTaskFamilyReliability] = []
    hdi_80_by_family: dict[str, dict[str, float]] = {}
    for i, agg in enumerate(aggregates):
        band = thresholds.band_for(
            n_total=agg.n_total,
            posterior_mean=float(means[i]),
            p_lt_strong=float(p_lt_strong[i]),
            p_lt_acceptable=float(p_lt_acceptable[i]),
        )
        out.append(PosteriorTaskFamilyReliability(
            task_family=agg.task_family,
            posterior_mean=float(means[i]),
            posterior_median=float(medians[i]),
            hdi_low=float(hdi95_lower[i]),
            hdi_high=float(hdi95_higher[i]),
            p_success_lt_strong=float(p_lt_strong[i]),
            p_success_lt_acceptable=float(p_lt_acceptable[i]),
            n_total=agg.n_total,
            band=band,
        ))
        hdi_80_by_family[agg.task_family] = {
            "low": float(hdi80_lower[i]),
            "high": float(hdi80_higher[i]),
        }
    return out, hdi_80_by_family


def _dispatch_diagnostic(name: str, state: ModelAnalysisState, *, plots_dir: Path) -> str:
    """Route a checker name to the corresponding hibayes.check.checkers callable.

    HiBayes 1.0.0 checkers are *factories*: `checkers.<name>(...)` returns a
    `check(state, display=None) -> (state, verdict)` callable. We invoke the
    factory with defaults, call the returned check, and return the verdict
    string (typically "pass", "fail", or "NA").

    Plot-producing checkers stash the rendered `matplotlib.Figure` into
    `state.diagnostics`, but the key under which the Figure lands varies by
    checker (live-probed against pinned HiBayes 1.0.0):

      * `posterior_predictive_plot` — stores under the bare name
        `"posterior_predictive_plot"` (`hibayes/check/checkers.py:142`).
      * `prior_predictive_plot` — stores per-variable as
        `f"{var}_prior_predictive"` (`hibayes/check/checkers.py:188`). For the
        observation variable `obs` the key is `obs_prior_predictive`, NOT the
        bare `prior_predictive_plot`.

    AM-003 fix: use the snapshot-diff plot-discovery pattern (mirrored from the
    sibling axes' `_emit_axis_plots` at
    `hibayes_artifact_validity/run_hibayes.py:209-306` and
    `hibayes_functional_usefulness/run_hibayes.py`) — snapshot
    `state.diagnostics.keys()` before the check call, then pick out the new
    key after. We still save the Figure as `plots_dir / f"{name}.png"`
    (renamed to the caller's canonical filename) so the downstream renderer
    discovers it under the expected name regardless of HiBayes's internal
    key convention.
    """
    from hibayes.check import checkers  # local import: deferred until first call
    factory = getattr(checkers, name)
    # DEVIATION FROM §7: the spec literal calls `checker(state)` directly,
    # assuming `hibayes.check.checkers.<name>` is the check callable. In
    # HiBayes 1.0.0 the `checkers.<name>` symbols are *factories* that return
    # the check callable. We invoke the factory with conservative non-default
    # thresholds for the numeric MCMC-convergence checkers — HiBayes defaults
    # (r_hat<1.01 strict, ess_bulk>1000, ess_tail>1000) are tighter than what
    # 1000 draws × 2 chains on an 11-parameter model produces under normal
    # convergence (ess values typically 600-2400). The relaxed thresholds
    # below still flag genuine divergence: r_hat<1.05 is the canonical
    # "good-enough" cutoff (Vehtari et al. 2021), and ess>400 is the
    # conventional "trust-the-mean" floor. The plot checkers and information-
    # criterion checkers take no `threshold` and use their factory defaults.
    _CHECKER_KWARGS: dict[str, dict[str, Any]] = {
        "r_hat": {"threshold": 1.05},
        "ess_bulk": {"threshold": 400},
        "ess_tail": {"threshold": 400},
    }
    # AM-003: snapshot diagnostics keys BEFORE the checker call so the post-
    # call diff isolates exactly what the checker stashed. Only relevant for
    # plot checkers; cheap enough to do unconditionally.
    pre_keys: set[str] = (
        set(state.diagnostics.keys())
        if hasattr(state, "diagnostics") and state.diagnostics is not None
        else set()
    )
    check_fn = factory(**_CHECKER_KWARGS.get(name, {}))
    _result, verdict = check_fn(state)
    if name.endswith("_plot"):
        fig = _resolve_plot_figure(name, state, pre_keys=pre_keys)
        if fig is not None and hasattr(fig, "savefig"):
            fig.savefig(plots_dir / f"{name}.png")
    return verdict


def _resolve_plot_figure(
    name: str,
    state: ModelAnalysisState,
    *,
    pre_keys: set[str],
) -> Any:
    """AM-003: snapshot-diff Figure discovery for plot checkers.

    Resolution order:
      1. Bare name (`state.diagnostics[name]`) — covers `posterior_predictive_plot`
         and any future checker that stores under its own canonical name.
      2. For `prior_predictive_plot`, the per-variable key
         `f"{var}_prior_predictive"`. Identify it as a newly-added key (not
         present in `pre_keys`) whose name ends with `_prior_predictive`. The
         first such key is taken; HiBayes 1.0.0's `prior_predictive_plot`
         checker emits one Figure per observation variable, and this axis has
         exactly one (`obs`) per the locked `_build_features` shape.
    """
    if not hasattr(state, "diagnostics") or state.diagnostics is None:
        return None
    fig = state.diagnostics.get(name)
    if fig is not None:
        return fig
    if name == "prior_predictive_plot":
        for key in state.diagnostics.keys():
            if key in pre_keys:
                continue
            if key.endswith("_prior_predictive"):
                return state.diagnostics.get(key)
    return None


def _run_diagnostics(
    state: ModelAnalysisState,
    *,
    plots_dir: Path,
) -> dict[str, Any]:
    """Per-checker entries are ``{"status": str, "reason": str}``.

    ``run_hibayes`` later inserts the ``"hdi_80"`` per-family bounds into the
    same returned dict (RD-T05-2), which is why the value type is ``Any``.
    """
    plots_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {}
    for name in DIAGNOSTIC_NAMES:
        try:
            verdict = _dispatch_diagnostic(name, state, plots_dir=plots_dir)
            # Map HiBayes 1.0.0 checker verdicts onto T03's {pass, fail, skip}
            # status contract. Live-probed verdict set at the pinned sha:
            # {"pass", "fail", "NA"}; "skip" is never emitted. "NA" means the
            # checker could not be computed (e.g. waic when log-likelihood is
            # unavailable, posterior_predictive_plot in non-interactive mode)
            # — that's a skip semantically, not a pass. Unknown verdicts fail
            # loudly so the reporter surfaces the surprise rather than silently
            # rolling up as pass.
            if verdict == "pass":
                status, reason = "pass", ""
            elif verdict == "fail":
                status, reason = "fail", "checker verdict: fail"
            elif verdict == "NA":
                status, reason = "skip", "checker verdict: NA (could not be computed)"
            else:
                status, reason = "fail", f"checker verdict: unexpected {verdict!r}"
            summary[name] = {"status": status, "reason": reason}
        except NotImplementedError as e:
            summary[name] = {"status": "skip", "reason": f"NotImplementedError: {e}"}
        except Exception as e:  # noqa: BLE001 -- DD-10 explicitly non-fatal
            summary[name] = {"status": "fail", "reason": f"{type(e).__name__}: {e}"}
    return summary


def _persist_artifacts(
    report: HiBayesRuntimeReport,
    state: ModelAnalysisState,
    *,
    out_dir: Path,
    seed: int,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1. posterior_task_family_reliability.csv
    import csv as _csv
    csv_path = out_dir / "posterior_task_family_reliability.csv"
    with csv_path.open("w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow([
            "task_family", "n_total", "posterior_mean", "posterior_median",
            "hdi_low", "hdi_high", "p_success_lt_strong",
            "p_success_lt_acceptable", "band",
        ])
        for p in report.posteriors:
            w.writerow([
                p.task_family, p.n_total, p.posterior_mean, p.posterior_median,
                p.hdi_low, p.hdi_high, p.p_success_lt_strong,
                p.p_success_lt_acceptable, p.band.value,
            ])

    # 2. diagnostics.json
    plots_relpath = (out_dir / "plots").relative_to(out_dir)
    diag_payload = {
        "diagnostics_summary": report.diagnostics_summary,
        "diagnostic_plot_paths": {
            name: str(plots_relpath / f"{name}.png")
            for name in DIAGNOSTIC_NAMES if name.endswith("_plot")
        },
    }
    (out_dir / "diagnostics.json").write_text(json.dumps(diag_payload, indent=2, default=str))

    # 3. config.resolved.yaml
    # Reproducibility (§6 implementation note): the seed is part of the
    # resolved sampler config; T07 reads this back to verify a re-run uses the
    # same seed. Do NOT omit `seed` from the YAML.
    resolved = {
        "model": "two_level_group_binomial",
        "sampler": {**_SAMPLER_KWARGS, "seed": seed},
        "thresholds": report.thresholds.model_dump(),
        "generated_at": report.generated_at,
    }
    (out_dir / "config.resolved.yaml").write_text(yaml.safe_dump(resolved, sort_keys=True))

    # 4. analysis_state/
    state_dir = out_dir / "analysis_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    if hasattr(state, "save") and callable(state.save):
        # HiBayes-native serializer. The pinned 1.0.0 sha's `state.save` calls
        # `_ensure_dir(path)` which invokes `path.mkdir(...)` directly, i.e.
        # expects a `Path`, not the `str(...)` the original spec literal used.
        state.save(state_dir)
    else:
        # JSON fallback: lossy on InferenceData, sufficient for report consumption.
        payload = {
            "_serializer": "json-fallback",
            "features": {k: (v.tolist() if hasattr(v, "tolist") else v)
                         for k, v in state.features.items()},
            "is_fitted": state.is_fitted,
        }
        (state_dir / "state.json").write_text(json.dumps(payload, indent=2, default=str))


def run_hibayes(
    rows: list[RuntimeEvalRow],
    thresholds: ReliabilityThresholds,
    *,
    out_dir: Path,
    seed: int = 20260509,
) -> HiBayesRuntimeReport:
    """End-to-end fit + posterior + diagnostics. Persists T05-owned artifacts as a side effect.

    Raises:
        ValueError: if `rows` is empty (no families to fit).
    """
    if not rows:
        raise ValueError("run_hibayes received no rows; cannot fit a model on zero observations")

    aggregates, _totals = aggregate_by_task_family(rows)
    features = _build_features(aggregates)
    state = _fit_model(features, seed=seed)
    posteriors, hdi_80_by_family = _extract_posteriors(state, aggregates, thresholds)
    diagnostics = _run_diagnostics(state, plots_dir=out_dir / "plots")
    # RD-T05-2: surface 80% HDI under diagnostics_summary["hdi_80"] as a
    # per-family dict; do NOT add fields to PosteriorTaskFamilyReliability.
    diagnostics["hdi_80"] = hdi_80_by_family
    report = HiBayesRuntimeReport(
        aggregates=aggregates,
        posteriors=posteriors,
        thresholds=thresholds,
        diagnostics_summary=diagnostics,
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
    _persist_artifacts(report, state, out_dir=out_dir, seed=seed)
    return report


# --------------------------------------------------------------------------- #
# T07 — CLI entrypoint (appended by task-07-cli-and-integration).             #
# --------------------------------------------------------------------------- #

import argparse  # noqa: E402
import csv as _csv  # noqa: E402
import importlib.resources  # noqa: E402
import sys  # noqa: E402
from typing import Sequence  # noqa: E402

import jinja2  # noqa: E402  -- for jinja2.TemplateError in render_report's except
from pydantic import ValidationError  # noqa: E402

from .load_csv import load_runtime_eval_csv  # noqa: E402
from .render_report import render_report  # noqa: E402

EXIT_OK = 0
EXIT_INPUT = 1
EXIT_HIBAYES = 2

_DEFAULT_CONFIG_RESOURCE = (
    "dmac_assistant.eval.hibayes_runtime_reliability",
    "config/hibayes_runtime_reliability.yaml",
)


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="hibayes_runtime_reliability",
        description=(
            "Run the HiBayes Bayesian runtime-reliability pipeline against an "
            "exported eval-row CSV."
        ),
    )
    p.add_argument(
        "--input",
        required=True,
        type=Path,
        help="Path to hibayes_eval_rows.csv produced by tools/hibayes/exporter.py.",
    )
    p.add_argument(
        "--out",
        required=True,
        type=Path,
        help="Output directory. Created if missing. Existing files are overwritten.",
    )
    p.add_argument(
        "--config",
        required=False,
        type=Path,
        default=None,
        help="YAML overriding ReliabilityThresholds. Defaults to packaged config.",
    )
    return p


def _resolve_config_path(supplied: Path | None) -> Path:
    if supplied is not None:
        return supplied.expanduser().resolve()
    pkg, rel = _DEFAULT_CONFIG_RESOURCE
    return Path(importlib.resources.files(pkg) / rel).resolve()


def _load_thresholds(config_path: Path) -> ReliabilityThresholds:
    """Returns thresholds or raises yaml.YAMLError / ValidationError.

    Callers are expected to pre-check ``config_path.exists()`` (see ``main``);
    therefore no FileNotFoundError branch is implemented here.
    """
    raw = yaml.safe_load(config_path.read_text())
    return ReliabilityThresholds.model_validate(raw or {})


def _write_aggregates_csv(report: HiBayesRuntimeReport, out_dir: Path) -> Path:
    """T07 owns task_family_aggregates.csv. Columns mirror TaskFamilyAggregate fields."""
    path = out_dir / "task_family_aggregates.csv"
    cols = [
        "task_family",
        "n_total",
        "n_success",
        "n_failure",
        "observed_success_rate",
        "n_error",
        "n_timeout",
        "n_no_answer",
        "n_artifact_rows",
        "avg_latency_seconds",
        "avg_cost_usd",
        "avg_tool_calls_total",
    ]
    with path.open("w", newline="") as fh:
        w = _csv.writer(fh)
        w.writerow(cols)
        for a in report.aggregates:
            w.writerow([getattr(a, c) for c in cols])
    return path


def _read_plot_paths(out_dir: Path) -> dict[str, Path]:
    """Read T05's diagnostics.json, resolve diagnostic_plot_paths against out_dir."""
    diag_path = out_dir / "diagnostics.json"
    if not diag_path.exists():
        return {}
    payload = json.loads(diag_path.read_text())
    rel = payload.get("diagnostic_plot_paths", {}) or {}
    return {k: (out_dir / v).resolve() for k, v in rel.items()}


def _emit(msg: str) -> None:
    """Single-line stderr emission; caller includes `error: ` prefix per §2.3."""
    print(msg, file=sys.stderr)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint. See task-07 spec §2 for exit-code matrix + stderr templates."""
    parser = _build_argparser()
    args = parser.parse_args(argv)
    input_path: Path = args.input.expanduser().resolve()
    out_dir: Path = args.out.expanduser().resolve()

    # --- Step 1: input validation ------------------------------------------
    if not input_path.exists():
        _emit(f"error: input csv not found: {input_path}")
        return EXIT_INPUT

    # --- Step 2: thresholds ------------------------------------------------
    config_path = _resolve_config_path(args.config)
    if not config_path.exists():
        _emit(f"error: config yaml not found: {config_path}")
        return EXIT_INPUT
    try:
        thresholds = _load_thresholds(config_path)
    except yaml.YAMLError as e:
        _emit(
            f"error: failed to parse config yaml {config_path}: "
            f"{type(e).__name__}: {e}"
        )
        return EXIT_INPUT
    except ValidationError as e:
        first = e.errors()[0] if e.errors() else {"msg": str(e)}
        _emit(
            f"error: invalid thresholds config {config_path}: "
            f"{first.get('msg', str(e))[:200]}"
        )
        return EXIT_INPUT

    # --- Step 3: load + validate rows --------------------------------------
    try:
        rows, load_report = load_runtime_eval_csv(input_path)
    except OSError as e:
        _emit(
            f"error: failed to read input csv {input_path}: "
            f"{type(e).__name__}: {e}"
        )
        return EXIT_INPUT
    except Exception as e:  # noqa: BLE001 -- narrowed by module-prefix check
        if e.__class__.__module__.startswith("pandas"):
            _emit(
                f"error: failed to read input csv {input_path}: "
                f"{type(e).__name__}: {e}"
            )
            return EXIT_INPUT
        raise

    if load_report.rejected:
        first = load_report.rejected[0]
        truncated = first.error.replace("\n", " ")[:200]
        _emit(
            f"error: input csv has {len(load_report.rejected)} invalid rows; "
            f"first rejection: {first.query_id}: {truncated}"
        )
        return EXIT_INPUT
    if not rows:
        _emit(f"error: input csv {input_path} contains no valid rows")
        return EXIT_INPUT

    # --- Step 4: ensure out dir, then HiBayes pipeline ---------------------
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        report = run_hibayes(rows, thresholds, out_dir=out_dir)
    except (ValueError, RuntimeError) as e:
        _emit(f"error: hibayes pipeline failed: {type(e).__name__}: {e}")
        return EXIT_HIBAYES
    except Exception as e:  # noqa: BLE001 -- narrowed by module-prefix check
        mod = e.__class__.__module__
        if mod.startswith(("jax", "numpyro")):
            _emit(f"error: hibayes pipeline failed: {type(e).__name__}: {e}")
            return EXIT_HIBAYES
        raise

    # --- Step 5: write T07-owned aggregates CSV ----------------------------
    _write_aggregates_csv(report, out_dir)

    # --- Step 6: render HTML ----------------------------------------------
    plot_paths = _read_plot_paths(out_dir)
    try:
        report_html_path = render_report(
            report, out_dir=out_dir, plot_paths=plot_paths
        )
    except (jinja2.TemplateError, OSError) as e:
        _emit(f"error: report rendering failed: {type(e).__name__}: {e}")
        return EXIT_HIBAYES

    # --- Step 7: success ---------------------------------------------------
    print(str(report_html_path.resolve()))
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

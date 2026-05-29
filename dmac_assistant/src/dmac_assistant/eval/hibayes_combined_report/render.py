"""Task-17 — Combined HiBayes HTML report renderer (tabbed, posterior.json-driven).

Reads three posterior.json files (runtime + artifact + functional) and emits a
single tabbed HTML file (`out/hibayes_combined_report.html`) whose three tab
panels mirror the visual quality of the existing runtime report. Hibayes-import-
clean (locked DD-41 + plan DL-013).

Per locked DD-41 partial-failure behavior:
- Render placeholder section for each missing axis.
- Exit non-zero by default if any expected axis is missing; --allow-partial overrides.

Task-17 D1: ONE shared Jinja2 template (combined.html.j2); no per-axis
`section.html.j2` partials. Each tab panel uses the runtime report's CSS /
content vocabulary.

Task-17 D8: per-axis chart set is constrained to `posterior.json` data — metric
cards, a posterior-mean-by-task-family bar chart, a risk bar chart of
p_success_lt_acceptable per family, the per-task-family table of all 9 strata
fields, and embedded plot PNGs. The runtime report's observedVsPosteriorChart
and failureModeChart are NOT reproduced — they require aggregate data that
posterior.json does not carry.

Task-17 D5: forest plot appears on the artifact + functional tabs only (the
runtime tab keeps its existing two plots). Enforced by the data layer —
`_build_axis_context` skips `forest_plot.png` when it is absent from the
axis's `plots_dir`, so `plot_inlines` carries no `forest_plot` entry on the
runtime axis. The `has_forest_plot` context flag is retained for callers
that may want to inspect it but is no longer load-bearing in the template.
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup


REQUIRED_WRAPPER_KEYS: frozenset[str] = frozenset(
    {"axis", "model", "prior_sigma_group_scale", "strata", "metadata"}
)

# Filenames the renderer looks for in each axis's plots/ dir (D4 / D5).
# Order is render-order: posterior-predictive first, then prior-predictive,
# then forest (last; new-axis-only per D5).
_PLOT_FILENAMES: tuple[str, ...] = (
    "posterior_predictive_plot.png",
    "prior_predictive_plot.png",
    "forest_plot.png",
)

_FOREST_FILENAME: str = "forest_plot.png"


def validate_posterior_wrapper_schema(
    payload: Any,
    *,
    source_label: str,
) -> None:
    """Raise KeyError if `payload` is missing any of the 5 top-level wrapper keys.

    Also raises TypeError if `payload` is not a dict (e.g., someone fed a flat
    strata list instead of the wrapped object).
    """
    if not isinstance(payload, dict):
        raise TypeError(
            f"posterior.json from {source_label}: expected dict with wrapper keys "
            f"{sorted(REQUIRED_WRAPPER_KEYS)}; got {type(payload).__name__}"
        )
    missing = REQUIRED_WRAPPER_KEYS - set(payload.keys())
    if missing:
        raise KeyError(
            f"posterior.json from {source_label}: missing top-level wrapper keys "
            f"{sorted(missing)}; required: {sorted(REQUIRED_WRAPPER_KEYS)}"
        )


def _load_posterior(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    validate_posterior_wrapper_schema(payload, source_label=str(path))
    return payload


def _check_axis(
    payload: dict[str, Any] | None, expected_axis: str, *, source_label: str
) -> None:
    """Raise ValueError if the loaded posterior's axis label disagrees with
    the slot it was passed to. Guards against operators swapping the
    --runtime / --artifact / --functional CLI arguments and producing a
    silently miscategorized combined report.
    """
    if payload is None:
        return
    actual = payload.get("axis")
    if actual != expected_axis:
        raise ValueError(
            f"posterior.json from {source_label}: axis mismatch — "
            f"expected axis={expected_axis!r}, got axis={actual!r}. "
            f"This usually means the --runtime / --artifact / --functional "
            f"CLI arguments were passed in the wrong order."
        )


# --------------------------------------------------------------------------- #
# Helpers (copied from `hibayes_runtime_reliability.render_report` per §6.1).  #
# render.py stays hibayes-import-clean — these are duplicated rather than     #
# imported to avoid coupling `hibayes_combined_report` to                     #
# `hibayes_runtime_reliability`.                                              #
# --------------------------------------------------------------------------- #


def encode_plot_png(path: Path) -> str | None:
    """Base64-encode a PNG file; return None if missing/unreadable.

    Verbatim copy from `hibayes_runtime_reliability.render_report.encode_plot_png`
    (lines 88-98) per spec §6.1. `OSError` covers `FileNotFoundError`,
    `PermissionError`, and `IsADirectoryError` (all subclasses) — the single
    except is sufficient for every "file is not readable" condition.
    """
    try:
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None


def _dash_for_none(x: Any) -> str:
    """Jinja filter: render '—' (em-dash) for None/NaN, else `str(x)`.

    Verbatim copy from `hibayes_runtime_reliability.render_report._dash_for_none`
    (lines 70-77) per spec §6.1.
    """
    if x is None:
        return "—"
    # numpy NaN check without importing numpy:
    if isinstance(x, float) and x != x:
        return "—"
    return str(x)


# --------------------------------------------------------------------------- #
# Per-axis context builder                                                    #
# --------------------------------------------------------------------------- #


_BAND_TO_PILL_CLASS: dict[str, str] = {
    "Reliable": "pill ok",
    "Watch": "pill warn",
    "Brittle": "pill fail",
    "TooUncertain": "pill muted",
}


def _build_axis_context(
    payload: dict[str, Any] | None,
    *,
    axis_key: str,
    axis_heading: str,
    plots_dir: Path | None,
) -> dict[str, Any] | None:
    """Produce the per-axis context dict consumed by `combined.html.j2`.

    Returns None when `payload` is None (missing axis); the template renders a
    placeholder block for None panels.
    """
    if payload is None:
        return None

    strata: list[dict[str, Any]] = list(payload.get("strata", []) or [])
    # Decorate strata rows with a pill class for the band cell (template uses it).
    table_rows: list[dict[str, Any]] = []
    for s in strata:
        row = dict(s)
        row["pill_class"] = _BAND_TO_PILL_CLASS.get(
            str(s.get("band", "")), "pill muted"
        )
        table_rows.append(row)

    # Metric-card values.
    family_count = len(strata)
    total_n = sum(int(s.get("n_total", 0) or 0) for s in strata)
    band_counts = {
        "Reliable": sum(1 for s in strata if s.get("band") == "Reliable"),
        "Watch": sum(1 for s in strata if s.get("band") == "Watch"),
        "Brittle": sum(1 for s in strata if s.get("band") == "Brittle"),
        "TooUncertain": sum(
            1 for s in strata if s.get("band") == "TooUncertain"
        ),
    }

    # Chart data — only what posterior.json carries (D8).
    families = [str(s.get("task_family", "")) for s in strata]
    posterior_means = [s.get("posterior_mean") for s in strata]
    risk_values = [s.get("p_success_lt_acceptable") for s in strata]
    chart_data: dict[str, Any] = {
        "posteriorMean": {
            "families": families,
            "values": posterior_means,
        },
        "risk": {
            "families": families,
            "values": risk_values,
        },
    }

    # Plot embedding — read every PNG that lives in `plots_dir` for this axis.
    plot_inlines: list[dict[str, str]] = []
    has_forest_plot = False
    if plots_dir is not None and plots_dir.is_dir():
        for name in _PLOT_FILENAMES:
            p = plots_dir / name
            if not p.is_file():
                continue
            encoded = encode_plot_png(p)
            if encoded is None:
                continue
            label = p.stem  # "posterior_predictive_plot", etc.
            plot_inlines.append({"label": label, "b64": encoded})
            if name == _FOREST_FILENAME:
                has_forest_plot = True

    return {
        "axis_key": axis_key,
        "axis_heading": axis_heading,
        "model": payload.get("model", ""),
        "prior_sigma_group_scale": payload.get("prior_sigma_group_scale", ""),
        "metadata": payload.get("metadata", {}) or {},
        "strata": table_rows,
        "metrics": {
            "family_count": family_count,
            "total_n": total_n,
            "band_counts": band_counts,
        },
        "chart_data": chart_data,
        "plot_inlines": plot_inlines,
        "has_forest_plot": has_forest_plot,
    }


# --------------------------------------------------------------------------- #
# Jinja2 environment                                                          #
# --------------------------------------------------------------------------- #


def _build_jinja_environment() -> Environment:
    """Configure the Jinja2 environment for the combined-report template.

    Single FileSystemLoader pointed at the combined-report's own
    `report_template/` directory. No DictLoader / ChoiceLoader: task-17 D1
    removes the per-axis `section.html.j2` partial mechanism (one shared
    template per D1).

    Filter registrations copied from
    `hibayes_runtime_reliability.render_report._build_environment` (lines 70-77
    for the `_dash_for_none` function body and 249-252 for the filter
    registrations) per spec §6.1.
    """
    combined_template_dir = Path(__file__).parent / "report_template"
    env = Environment(
        loader=FileSystemLoader(str(combined_template_dir)),
        autoescape=select_autoescape(["html", "htm", "j2", "xml"]),
        trim_blocks=False,
        lstrip_blocks=False,
    )
    env.filters["dash_for_none"] = _dash_for_none
    env.filters["pct"] = lambda x: "—" if x is None else f"{100 * x:.1f}%"
    env.filters["money"] = lambda x: "—" if x is None else f"${x:.4f}"
    env.filters["fixed"] = lambda x, d=2: "—" if x is None else f"{x:.{d}f}"
    return env


# --------------------------------------------------------------------------- #
# Public entrypoint                                                           #
# --------------------------------------------------------------------------- #


def render_combined_report(
    *,
    runtime_path: Path,
    artifact_path: Path,
    functional_path: Path,
    out_html: Path,
    allow_partial: bool,
    runtime_plots_dir: Path | None = None,
    artifact_plots_dir: Path | None = None,
    functional_plots_dir: Path | None = None,
) -> int:
    """Render the combined tabbed HTML report; return 0 / 1 exit code.

    Plot-directory derivation rule (§6.1): for each axis, if the corresponding
    `*_plots_dir` argument is None, the renderer derives the directory as
    `<that axis's posterior.json>.parent / "plots"`. An explicit `*_plots_dir`
    argument overrides the derivation.
    """
    runtime = _load_posterior(runtime_path)
    artifact = _load_posterior(artifact_path)
    functional = _load_posterior(functional_path)

    _check_axis(runtime, "runtime", source_label=str(runtime_path))
    _check_axis(artifact, "artifact", source_label=str(artifact_path))
    _check_axis(functional, "functional", source_label=str(functional_path))

    # Plot-directory derivation (§6.1): default to `<posterior.json>.parent / "plots"`.
    rt_plots = (
        runtime_plots_dir
        if runtime_plots_dir is not None
        else runtime_path.parent / "plots"
    )
    av_plots = (
        artifact_plots_dir
        if artifact_plots_dir is not None
        else artifact_path.parent / "plots"
    )
    fu_plots = (
        functional_plots_dir
        if functional_plots_dir is not None
        else functional_path.parent / "plots"
    )

    runtime_ctx = _build_axis_context(
        runtime,
        axis_key="runtime",
        axis_heading="Runtime Reliability",
        plots_dir=rt_plots,
    )
    artifact_ctx = _build_axis_context(
        artifact,
        axis_key="artifact",
        axis_heading="Artifact Validity",
        plots_dir=av_plots,
    )
    functional_ctx = _build_axis_context(
        functional,
        axis_key="functional",
        axis_heading="Functional Usefulness",
        plots_dir=fu_plots,
    )

    env = _build_jinja_environment()
    template = env.get_template("combined.html.j2")

    # Per-axis chart-data JSON — passed through Markup so the autoescape does
    # not turn `<` into `&lt;`. Pattern mirrors
    # `hibayes_runtime_reliability.render_report.render_report` lines 285-291.
    def _dump_chart_data(ctx: dict[str, Any] | None) -> Markup:
        if ctx is None:
            return Markup("null")
        return Markup(
            json.dumps(ctx["chart_data"], default=str).replace("</", "<\\/")
        )

    html = template.render(
        runtime=runtime_ctx,
        artifact=artifact_ctx,
        functional=functional_ctx,
        runtime_chart_data_json=_dump_chart_data(runtime_ctx),
        artifact_chart_data_json=_dump_chart_data(artifact_ctx),
        functional_chart_data_json=_dump_chart_data(functional_ctx),
        chartjs_cdn="https://cdn.jsdelivr.net/npm/chart.js@4",
    )
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(html, encoding="utf-8")

    any_missing = any(p is None for p in (runtime, artifact, functional))
    if any_missing and not allow_partial:
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dmac_assistant.eval.hibayes_combined_report.render",
    )
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--functional", type=Path, required=True)
    parser.add_argument("--out-html", type=Path, required=True)
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args(argv)
    return render_combined_report(
        runtime_path=args.runtime,
        artifact_path=args.artifact,
        functional_path=args.functional,
        out_html=args.out_html,
        allow_partial=args.allow_partial,
    )


if __name__ == "__main__":
    sys.exit(main())

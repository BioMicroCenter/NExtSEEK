"""HTML report renderer for the HiBayes runtime-reliability pipeline.

Public entrypoint: `render_report(report, *, out_dir, plot_paths=None) -> Path`.

Design constraints (all locked in the T06 spec, §3):
  - DD-07: visual mirror of evidence/headless/20260507T224850Z/report.html
  - DD-10: warning banner ONLY when at least one checker failed
  - R-12:  Jinja2 select_autoescape on (html, htm, j2, xml)
  - R-03:  numeric chart values must remain numeric; missing values render as `—`
  - R-07:  this module MUST NOT import matplotlib (enforced by test T-19)
  - R-08:  this module MUST NOT mutate any T03 schema (read-only consumer)
"""
from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup  # re-exported by jinja2; explicit import for clarity

from dmac_assistant.eval.hibayes_runtime_reliability.models import (
    HiBayesRuntimeReport,
    ReliabilityBand,
)


# --------------------------------------------------------------------------- #
# Locked constants                                                            #
# --------------------------------------------------------------------------- #

T05_PLOT_KEYS: tuple[str, ...] = (
    "posterior_predictive_plot",
    "prior_predictive_plot",
)

CHART_CANVAS_IDS: tuple[str, ...] = (
    "posteriorMeanChart",
    "observedVsPosteriorChart",
    "failureModeChart",
    "riskChart",
)

MANIFEST_SCRIPT_ID: str = "hibayes-manifest"
MANIFEST_SCHEMA_VERSION: str = "1"

_BAND_TO_PILL_CLASS: dict[ReliabilityBand, str] = {
    ReliabilityBand.Reliable: "pill ok",
    ReliabilityBand.Watch: "pill warn",
    ReliabilityBand.Brittle: "pill fail",
    ReliabilityBand.TooUncertain: "pill muted",
}

_TEMPLATES_DIR: Path = Path(__file__).parent / "templates"
_TEMPLATE_NAME: str = "hibayes_runtime_report.html.j2"


# --------------------------------------------------------------------------- #
# Pure helpers (extracted in REFACTOR — Step 8 of §4)                         #
# --------------------------------------------------------------------------- #


def _to_chart_value(x: float | int | None) -> float | int | None:
    """Identity passthrough for numeric/None; never coerces to str (R-03)."""
    return x


def _dash_for_none(x: Any) -> str:
    """Jinja filter: render '—' (em-dash) for None/NaN, else `str(x)`."""
    if x is None:
        return "—"
    # numpy NaN check without importing numpy:
    if isinstance(x, float) and x != x:
        return "—"
    return str(x)


def has_failed_checkers(diagnostics_summary: Mapping[str, Any]) -> bool:
    """True iff at least one entry in `diagnostics_summary` has `status == 'fail'`."""
    for v in diagnostics_summary.values():
        if isinstance(v, dict) and v.get("status") == "fail":
            return True
    return False


def encode_plot_png(path: Path) -> str | None:
    """Base64-encode a PNG file; return None if missing/unreadable.

    `OSError` covers `FileNotFoundError`, `PermissionError`, and `IsADirectoryError`
    (all subclasses) — the single except is sufficient for every "file is not
    readable" condition the renderer might encounter on macOS / Linux.
    """
    try:
        return base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None


def build_chart_data(report: HiBayesRuntimeReport) -> dict[str, Any]:
    """Shape the four chart datasets. Pure: no I/O, no Jinja, no template state.

    Locked output keys (DL-T06-3): `posteriorMean`, `observedVsPosterior`,
    `failureMode`, `risk` (camelCase to mirror canvas IDs).
    """
    families = [p.task_family for p in report.posteriors]
    posteriors_by_family = {p.task_family: p for p in report.posteriors}
    aggregates_by_family = {a.task_family: a for a in report.aggregates}

    posterior_means = [posteriors_by_family[f].posterior_mean for f in families]
    hdi_bands = [
        (posteriors_by_family[f].hdi_low, posteriors_by_family[f].hdi_high)
        for f in families
    ]
    observed_rates = [
        aggregates_by_family[f].observed_success_rate for f in families
    ]

    failure_mode = {
        "n_error": [aggregates_by_family[f].n_error for f in families],
        "n_timeout": [aggregates_by_family[f].n_timeout for f in families],
        "n_no_answer": [aggregates_by_family[f].n_no_answer for f in families],
    }

    risk_pairs = sorted(
        [(p.task_family, p.p_success_lt_acceptable) for p in report.posteriors],
        key=lambda kv: kv[1],
        reverse=True,
    )

    return {
        "posteriorMean": {
            "families": families,
            "values": [_to_chart_value(v) for v in posterior_means],
            "hdi_bands": [(_to_chart_value(lo), _to_chart_value(hi)) for lo, hi in hdi_bands],
        },
        "observedVsPosterior": {
            "families": families,
            "observed": [_to_chart_value(v) for v in observed_rates],
            "posterior": [_to_chart_value(v) for v in posterior_means],
        },
        "failureMode": {
            "families": families,
            "n_error": failure_mode["n_error"],
            "n_timeout": failure_mode["n_timeout"],
            "n_no_answer": failure_mode["n_no_answer"],
        },
        "risk": {
            "families": [k for k, _ in risk_pairs],
            "values": [_to_chart_value(v) for _, v in risk_pairs],
        },
    }


def _build_table_rows(report: HiBayesRuntimeReport) -> list[dict[str, Any]]:
    """One row per posterior, with band → pill_class joined and hdi_80 lookup."""
    aggregates_by_family = {a.task_family: a for a in report.aggregates}
    hdi80 = report.diagnostics_summary.get("hdi_80", {}) or {}
    rows: list[dict[str, Any]] = []
    for p in report.posteriors:
        a = aggregates_by_family[p.task_family]
        h = hdi80.get(p.task_family) if isinstance(hdi80, dict) else None
        rows.append(
            {
                "task_family": p.task_family,
                "band": p.band.value,
                "pill_class": _BAND_TO_PILL_CLASS[p.band],
                "n_total": p.n_total,
                "n_success": a.n_success,
                "n_failure": a.n_failure,
                "observed_success_rate": a.observed_success_rate,
                "posterior_mean": p.posterior_mean,
                "posterior_median": p.posterior_median,
                "hdi_low": p.hdi_low,
                "hdi_high": p.hdi_high,
                "p_success_lt_strong": p.p_success_lt_strong,
                "p_success_lt_acceptable": p.p_success_lt_acceptable,
                "avg_cost_usd": a.avg_cost_usd,
                "avg_latency_seconds": a.avg_latency_seconds,
                "avg_tool_calls_total": a.avg_tool_calls_total,
                "n_error": a.n_error,
                "n_timeout": a.n_timeout,
                "n_no_answer": a.n_no_answer,
                "hdi_80_low": (h.get("low") if isinstance(h, dict) else None),
                "hdi_80_high": (h.get("high") if isinstance(h, dict) else None),
            }
        )
    return rows


def _build_manifest(
    report: HiBayesRuntimeReport,
    table_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": report.generated_at,
        "n_families": len(report.posteriors),
        "thresholds": report.thresholds.model_dump(),
        "task_family_results": [
            {
                k: row[k]
                for k in (
                    "task_family", "band", "n_total", "n_success", "n_failure",
                    "observed_success_rate",
                    "posterior_mean", "posterior_median",
                    "hdi_low", "hdi_high",
                    "p_success_lt_strong", "p_success_lt_acceptable",
                    "avg_cost_usd", "avg_latency_seconds", "avg_tool_calls_total",
                    "n_error", "n_timeout", "n_no_answer",
                    "hdi_80_low", "hdi_80_high",
                )
            }
            for row in table_rows
        ],
        "diagnostics_summary": dict(report.diagnostics_summary),
    }


def _resolve_plot_inlines(
    plot_paths: Mapping[str, Path] | None,
) -> dict[str, str]:
    """Return {key: base64} for every supplied + readable T05_PLOT_KEYS entry."""
    if not plot_paths:
        return {}
    out: dict[str, str] = {}
    for key in T05_PLOT_KEYS:
        if key not in plot_paths:
            continue
        encoded = encode_plot_png(plot_paths[key])
        if encoded is not None:
            out[key] = encoded
    return out


# --------------------------------------------------------------------------- #
# Jinja2 environment (R-12 mitigation locked at construction)                 #
# --------------------------------------------------------------------------- #


def _build_environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(enabled_extensions=("html", "htm", "j2", "xml")),
        trim_blocks=False,
        lstrip_blocks=False,
    )
    env.filters["dash_for_none"] = _dash_for_none
    env.filters["pct"] = lambda x: "—" if x is None else f"{100 * x:.1f}%"
    env.filters["money"] = lambda x: "—" if x is None else f"${x:.4f}"
    env.filters["fixed"] = lambda x, d=2: "—" if x is None else f"{x:.{d}f}"
    # Register ReliabilityBand as a Jinja global so the template's
    # `selectattr('band','equalto',ReliabilityBand.Reliable)` filters can resolve
    # the enum at render time. Without this, Jinja raises UndefinedError on
    # every metric-card count and band-table render.
    env.globals["ReliabilityBand"] = ReliabilityBand
    return env


# --------------------------------------------------------------------------- #
# Public entrypoint                                                           #
# --------------------------------------------------------------------------- #


def render_report(
    report: HiBayesRuntimeReport,
    *,
    out_dir: Path,
    plot_paths: Mapping[str, Path] | None = None,
) -> Path:
    """Render `report` to `<out_dir>/report.html` and return the report path."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    table_rows = _build_table_rows(report)
    chart_data = build_chart_data(report)
    manifest = _build_manifest(report, table_rows)
    plot_inlines = _resolve_plot_inlines(plot_paths)
    has_failures = has_failed_checkers(report.diagnostics_summary)

    env = _build_environment()
    template = env.get_template(_TEMPLATE_NAME)

    chart_data_dump = json.dumps(chart_data, default=str).replace("</", "<\\/")
    manifest_dump = json.dumps(manifest, default=str).replace("</", "<\\/")

    html = template.render(
        report=report,
        table_rows=table_rows,
        chart_data_json=Markup(chart_data_dump),
        manifest_json=Markup(manifest_dump),
        manifest_script_id=MANIFEST_SCRIPT_ID,
        plot_inlines=plot_inlines,
        has_failures=has_failures,
        chart_ids=CHART_CANVAS_IDS,
        chartjs_cdn="https://cdn.jsdelivr.net/npm/chart.js@4",
        rendered_at_utc=datetime.now(timezone.utc).isoformat(),
    )

    out_path = out_dir / "report.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path

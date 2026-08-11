"""T3.3 — Runtime axis CSV → posterior.json adapter.

Reads the existing axis's posterior_task_family_reliability.csv (emitted by
run_hibayes.py:316-330) and writes posterior.json matching locked DD-41's
nested wrapper schema verbatim.

Hibayes-import-clean (stdlib csv/json only) — host tests run without
pytest.importorskip("hibayes").

ADDITIVE: this file does NOT modify any existing file in this module.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


# Pinned to run_hibayes.py:320-323 verbatim.
EXPECTED_CSV_HEADER_9: tuple[str, ...] = (
    "task_family",
    "n_total",
    "posterior_mean",
    "posterior_median",
    "hdi_low",
    "hdi_high",
    "p_success_lt_strong",
    "p_success_lt_acceptable",
    "band",
)


_FLOAT_KEYS = (
    "posterior_mean",
    "posterior_median",
    "hdi_low",
    "hdi_high",
    "p_success_lt_strong",
    "p_success_lt_acceptable",
)
_INT_KEYS = ("n_total",)


def adapt_runtime_csv_to_posterior_json(
    *,
    csv_path: Path,
    out_path: Path,
    prior_sigma_group_scale: float,
    run_id: str,
    thresholds: dict[str, float],
    fit_diagnostics: dict[str, Any] | None = None,
) -> Path:
    """Convert the runtime axis CSV to a DD-41-compliant posterior.json.

    Raises:
        KeyError: if the CSV header diverges from EXPECTED_CSV_HEADER_9.
    """
    strata: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames is None:
            raise KeyError(f"empty CSV: {csv_path}")
        actual = tuple(reader.fieldnames)
        if actual != EXPECTED_CSV_HEADER_9:
            raise KeyError(
                f"runtime CSV header diverges from expected 9-column list. "
                f"Expected: {EXPECTED_CSV_HEADER_9}; got: {actual}"
            )
        for row in reader:
            stratum: dict[str, Any] = {}
            for k in EXPECTED_CSV_HEADER_9:
                v = row[k]
                if k in _INT_KEYS:
                    stratum[k] = int(v)
                elif k in _FLOAT_KEYS:
                    stratum[k] = float(v)
                else:
                    stratum[k] = v
            strata.append(stratum)

    payload = {
        "axis": "runtime",
        "model": "two_level_group_binomial",
        "prior_sigma_group_scale": prior_sigma_group_scale,
        "strata": strata,
        "metadata": {
            "run_id": run_id,
            "axis_input_csv": str(csv_path),
            "thresholds": thresholds,
            "fit_diagnostics": fit_diagnostics or {},
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out_path

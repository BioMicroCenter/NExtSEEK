"""nf-core/cutandrun's own "@" tokens: a peak row's FRiP and peak count, only on the rows of
the run's primary peak caller.

cutandrun 3.x can call peaks with SEACR, MACS2 or both (`--peakcaller seacr,macs2`); the
first caller named is the primary one, and only its peaks get the FRiP score and peak count
(PEAK_QC takes `ch_peaks_primary` in workflows/cutandrun.nf). Both reach MultiQC under one
section per sample, so the sample's `$derived.frip_pct` and `$derived.peak_count` describe
the primary caller's peaks, and the other caller's row must not carry them.
"""
from __future__ import annotations

import re

from NessieAI.ns.reingest.pipelines.common import _all_outputs

#: The release's default `--peakcaller` (nextflow_schema.json at 3.2.2).
DEFAULT_PEAKCALLER = "seacr"
#: The peak files each caller publishes, as path fragments (conf/modules.config at 3.2.2).
_CALLER_FOLDERS = {"seacr": "/04_called_peaks/seacr/", "macs2": "/04_called_peaks/macs2/"}


def primary_caller(run_manifest, context) -> str | None:
    """The run's primary peak caller, lower-case: the first of `params.peakcaller`, the
    release default when the params were read but name none, else the one caller whose
    peak files the run holds. None when the run says nothing that settles it."""
    params = run_manifest.params or {}
    named = str(params.get("peakcaller") or "").strip()
    if named:
        return re.split(r"\s*,\s*", named)[0].lower() or None
    if params:
        return DEFAULT_PEAKCALLER
    found = {caller for caller, folder in _CALLER_FOLDERS.items()
             for output in _all_outputs(run_manifest, context)
             if folder in "/" + str(getattr(output, "path", "") or "")}
    return found.pop() if len(found) == 1 else None


def _only_for(caller: str, derived_key: str):
    def compute(run_manifest, sample, context):
        if sample is None or primary_caller(run_manifest, context) != caller:
            return None
        return (getattr(sample, "derived", None) or {}).get(derived_key)
    return compute


COMPUTED = {
    "seacr_frip_pct": _only_for("seacr", "frip_pct"),
    "seacr_peak_count": _only_for("seacr", "peak_count"),
    "macs2_frip_pct": _only_for("macs2", "frip_pct"),
    "macs2_peak_count": _only_for("macs2", "peak_count"),
}

"""Report template metadata helpers: alias normalization, template-basename
mapping, nf-core pipeline extraction, and JSON template loading.

Moved out of ``helpers.py`` during Phase 2 of the src/ restructure.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def load_report_template(config, report_type: str | None) -> dict:
    """
    Load a report template JSON from reports/ based on report_type name.
    Returns an empty dict on missing files or parse errors to let report writer proceed with defaults.
    """
    template_basename = get_report_template_basename(report_type)
    if not template_basename:
        return {}
    try:
        path = Path(config.BASE_DIR) / "reports" / f"{template_basename}.json"
        if path.exists():
            return json.loads(path.read_text())
    except Exception as e:
        print("[DEBUG][REPORT_WRITER] Failed to load report template:", repr(e))
    return {}


def normalize_report_type(report_type: str | None) -> str | None:
    """
    Normalize user/model-facing report type aliases into canonical internal labels.
    Returns None for empty values so callers can preserve "not specified" semantics.
    """
    if not isinstance(report_type, str):
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", report_type).strip("_").upper()
    if not cleaned:
        return None

    alias_map = {
        "GEO": "GEO",
        "PRIDE": "PRIDE",
        "SRA": "SRA",
        "NFCORE": "NFCORE",
        "NF_CORE": "NFCORE",
        "NFCORE_RNASEQ": "NFCORE_RNASEQ",
        "NFCORE_RNASEQ_SAMPLESHEET": "NFCORE_RNASEQ",
        "NF_CORE_RNASEQ": "NFCORE_RNASEQ",
        "NFCORE_SCRNASEQ": "NFCORE_SCRNASEQ",
        "NFCORE_SCRNASEQ_SAMPLESHEET": "NFCORE_SCRNASEQ",
        "NF_CORE_SCRNASEQ": "NFCORE_SCRNASEQ",
        "NFCORE_ATACSEQ": "NFCORE_ATACSEQ",
        "NF_CORE_ATACSEQ": "NFCORE_ATACSEQ",
        "NFCORE_CHIPSEQ": "NFCORE_CHIPSEQ",
        "NF_CORE_CHIPSEQ": "NFCORE_CHIPSEQ",
        "NFCORE_SAREK": "NFCORE_SAREK",
        "NF_CORE_SAREK": "NFCORE_SAREK",
        "NFCORE_METHYLSEQ": "NFCORE_METHYLSEQ",
        "NF_CORE_METHYLSEQ": "NFCORE_METHYLSEQ",
        "NFCORE_AMPLISEQ": "NFCORE_AMPLISEQ",
        "NF_CORE_AMPLISEQ": "NFCORE_AMPLISEQ",
        "NFCORE_FETCHNGS": "NFCORE_FETCHNGS",
        "NF_CORE_FETCHNGS": "NFCORE_FETCHNGS",
    }
    return alias_map.get(cleaned, cleaned)


def nfcore_pipeline_from_report_type(report_type: str | None) -> str | None:
    """If report_type is NFCORE_<PIPELINE>, return the lowercase pipeline key.
    Generic NFCORE returns None (caller should run pipeline selector).
    """
    canonical = normalize_report_type(report_type)
    if not canonical or not canonical.startswith("NFCORE"):
        return None
    if canonical == "NFCORE":
        return None
    return canonical[len("NFCORE_"):].lower()


def get_report_template_basename(report_type: str | None) -> str | None:
    """
    Map canonical report types to the JSON template basename stored in reports/.
    """
    canonical = normalize_report_type(report_type)
    if not canonical:
        return None
    static = {
        "GEO": "GEO-updated",
        "PRIDE": "pride",
        "SRA": "SRA",
    }
    if canonical in static:
        return static[canonical]
    if canonical.startswith("NFCORE_"):
        return f"nfcore/{canonical[len('NFCORE_'):].lower()}"
    if canonical == "NFCORE":
        return "nfcore/rnaseq"  # generic default; pipeline selector overrides upstream
    return canonical.lower()

"""Curated per-pipeline params, the species->bundle reference registry, and the
deterministic helpers configure_run uses to assemble params.yml values.

Data lives in reports/templates/nfcore/<key>.json (+ reference_bundles.json),
loaded package-relative and cached. No config dependency -> import-time testable.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_NFCORE_DIR = Path(__file__).resolve().parent.parent / "reports" / "templates" / "nfcore"


@lru_cache(maxsize=None)
def load_pipeline_context(pipeline_key: str) -> dict[str, Any]:
    """Curated context for a pipeline: {'params': {...}, 'reference_resources': [...]}.
    Returns {'params': {}, 'reference_resources': []} when the file or keys are absent."""
    path = _NFCORE_DIR / f"{(pipeline_key or '').strip().lower()}.json"
    if not path.exists():
        return {"params": {}, "reference_resources": []}
    doc = json.loads(path.read_text())
    return {"params": doc.get("params") or {}, "reference_resources": list(doc.get("reference_resources") or [])}


@lru_cache(maxsize=1)
def load_reference_bundles() -> dict[str, Any]:
    """Load the species->bundle reference registry; empty registry if the file is absent."""
    path = _NFCORE_DIR / "reference_bundles.json"
    if not path.exists():
        return {"store_root": None, "species_to_bundle": {}, "bundles": {}}
    return json.loads(path.read_text())


def resolve_bundle_for_species(species: str | None) -> str | None:
    """Map a free-text species value to a bundle key (case-insensitive). None if unknown."""
    if not species:
        return None
    table = {k.lower(): v for k, v in (load_reference_bundles().get("species_to_bundle") or {}).items()}
    return table.get(str(species).strip().lower())


def build_reference_params(pipeline_key: str, bundle_key: str | None) -> tuple[dict[str, Any], str]:
    """Return (reference_params, reference_status) for a pipeline + bundle.

    status: 'configured'             -> store_root set, explicit resource paths emitted
            'igenomes_fallback'      -> store_root unset, bundle has igenomes_key -> {'genome': key}
            'unconfigured_no_fallback' -> store_root unset and no igenomes_key (e.g. PDX combo)
            'no_bundle'              -> bundle_key is None/unknown
    """
    if not bundle_key:
        return {}, "no_bundle"
    reg = load_reference_bundles()
    bundle = (reg.get("bundles") or {}).get(bundle_key)
    if not bundle:
        return {}, "no_bundle"
    store_root = reg.get("store_root")
    ctx = load_pipeline_context(pipeline_key)
    wanted = set(ctx.get("reference_resources") or [])
    if store_root:
        params: dict[str, Any] = {}
        for name, templated in (bundle.get("resources") or {}).items():
            if name in wanted and isinstance(templated, str):
                params[name] = templated.replace("{store_root}", str(store_root).rstrip("/"))
        # Stamp the bundle's gencode flag when set (a GENCODE GTF needs --gencode), but
        # only for pipelines that actually accept a gencode param.
        if bundle.get("gencode") and "gencode" in (ctx.get("params") or {}):
            params["gencode"] = bundle["gencode"]
        return params, "configured"
    # store_root unset: fall back to the iGenomes genome key, or flag honestly.
    if bundle.get("igenomes_key"):
        return {"genome": bundle["igenomes_key"]}, "igenomes_fallback"
    return {}, "unconfigured_no_fallback"

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

from ..luria.run_script import has_local_luria_ref

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


@lru_cache(maxsize=None)
def _load_pipeline_doc(pipeline_key: str) -> dict[str, Any]:
    """Full curated JSON for a pipeline ({} if absent)."""
    path = _NFCORE_DIR / f"{(pipeline_key or '').strip().lower()}.json"
    return json.loads(path.read_text()) if path.exists() else {}


def process_args_for(pipeline_key: str, protocol: str | None) -> dict[str, str]:
    """{process_name: ext_args} the curated JSON declares a protocol needs — e.g. scrnaseq
    'dropseq' -> {'SIMPLEAF_QUANT': '--knee'}. Read from <key>.json 'protocol_process_args'.
    Data-driven (the JSON owns which protocol needs which process args); {} when none."""
    if not protocol:
        return {}
    table = _load_pipeline_doc(pipeline_key).get("protocol_process_args") or {}
    entry = table.get(str(protocol).strip()) or {}
    return {k: v for k, v in entry.items() if not str(k).startswith("_")}


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


def gencode_for_genome_key(genome_key: str | None) -> bool:
    """True if the bundle whose igenomes_key == genome_key is GENCODE-formatted.

    Used by the Luria backend: its local reference genomes (luria.config) are
    GENCODE GTFs for human/mouse but Ensembl for the macaques, so `--gencode`
    must follow the genome. This is deliberately NOT applied on the Tower path,
    where the same key resolves to (non-GENCODE) AWS iGenomes references.
    """
    if not genome_key:
        return False
    for bundle in (load_reference_bundles().get("bundles") or {}).values():
        if bundle.get("igenomes_key") == genome_key:
            return bool(bundle.get("gencode"))
    return False


def build_reference_params(pipeline_key: str, bundle_key: str | None) -> tuple[dict[str, Any], str]:
    """Return (reference_params, reference_status) for a pipeline + bundle.

    status: 'local_luria'            -> genome key has a local Luria ref (LURIA_GENOMES) -> {'genome': key}
            'configured'             -> store_root set, explicit resource paths emitted
            'igenomes_fallback'      -> store_root unset, bundle has igenomes_key, NO local ref -> {'genome': key}
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
    igenomes_key = bundle.get("igenomes_key")
    # Local Luria reference wins: the submit path injects it as --fasta/--gtf
    # (path > iGenomes), so report it honestly rather than as an iGenomes fallback.
    if igenomes_key and has_local_luria_ref(igenomes_key):
        return {"genome": igenomes_key}, "local_luria"
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


_STRIP_KEYS = ("input", "outdir")


def build_run_params(
    pipeline_key: str,
    agent_params: dict[str, Any] | None,
    bundle_key: str | None,
) -> tuple[dict[str, Any], list[str], str]:
    """Assemble final params.yml values + validation errors + reference_status.

    Merge order: curated defaults <- bundle/reference params <- agent overrides.
    Validation: an agent key is allowed if it is in the curated params menu OR the
    pipeline's reference_resources OR is 'genome'. Only enum *values* are validated
    (against 'allowed'); bool/string values pass through to the emitter / Nextflow
    schema. 'input'/'outdir' are stripped (the emitter owns them). On any error the
    return is ({}, errors, "invalid") so the caller surfaces errors and never an
    incomplete params set.
    """
    ctx = load_pipeline_context(pipeline_key)
    menu = ctx.get("params") or {}
    ref_names = set(ctx.get("reference_resources") or [])
    allowed_keys = set(menu) | ref_names | {"genome"}

    agent_params = dict(agent_params or {})
    for k in _STRIP_KEYS:
        agent_params.pop(k, None)

    errors: list[str] = []
    for key, value in agent_params.items():
        if key not in allowed_keys:
            errors.append(f"unknown param {key!r} — not in the curated menu for {pipeline_key}.")
            continue
        spec = menu.get(key)
        if spec and spec.get("type") == "enum" and value not in (spec.get("allowed") or []):
            errors.append(f"param {key!r} value {value!r} not allowed (choose from {spec.get('allowed')}).")
    if errors:
        return {}, errors, "invalid"

    # curated defaults
    merged: dict[str, Any] = {k: spec.get("default") for k, spec in menu.items()
                              if spec.get("default") is not None}
    # bundle/reference params
    ref_params, status = build_reference_params(pipeline_key, bundle_key)
    merged.update(ref_params)
    # agent overrides win
    merged.update(agent_params)
    return merged, [], status

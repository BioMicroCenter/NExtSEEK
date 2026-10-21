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


# Pulling a run's SRA accessions before the pipeline starts (the fetchngs pre-stage):
# once per run, then per accession. Small public runs; a large one takes longer.
_SRA_FETCH_MINUTES = (5, 10)
_SRA_FETCH_PER_SAMPLE_MINUTES = (2, 10)
# Above this much FASTQ in any one fetched run, the per-accession range above is not claimed
# and the download is left out of the range, named by its size instead. A policy line, not a
# measurement: the range was only ever seen to hold on runs of about 1 M reads, and six runs of
# 16.5-21.1 GB fetched none in 4 h (hlatyping launch, SLURM 11426306, 2026-10-20), when the
# range had said 17-70 minutes for the whole download. Nessie has no measured ENA-to-Luria
# transfer rate to turn bytes into minutes.
_SMALL_RUN_FASTQ_BYTES = 10 ** 9


def _half_hours(minutes: float) -> str:
    value = round(minutes / 30) / 2
    return f"{value:g}"


def _five_minutes(minutes: float) -> int:
    return int(5 * round(minutes / 5))


def _gb(n_bytes: int) -> str:
    return f"{n_bytes / 10 ** 9:.1f} GB"


def _sra_download(n_fetch: int, fetch_sizes: dict[str, dict] | None) -> dict[str, Any]:
    """What the estimate says about the SRA pre-stage, from ENA's sizes where it gave them.

    ``timed`` is False when any fetched run holds more than _SMALL_RUN_FASTQ_BYTES of FASTQ:
    the small-run minutes are then left out of the range and the size is named instead."""
    sizes = {acc: s for acc, s in (fetch_sizes or {}).items() if s and s.get("fastq_bytes")}
    total = sum(s["fastq_bytes"] for s in sizes.values())
    large = {acc: s for acc, s in sizes.items() if s["fastq_bytes"] > _SMALL_RUN_FASTQ_BYTES}
    plural = "s" if n_fetch != 1 else ""
    unknown = max(n_fetch - len(sizes), 0)
    out: dict[str, Any] = {"runs": n_fetch, "sized": len(sizes), "fastq_bytes": total,
                           "timed": not large, "warning": None}
    if large:
        acc, biggest = max(large.items(), key=lambda kv: kv[1]["fastq_bytes"])
        reads = biggest.get("read_count")
        unit = "read pairs" if biggest.get("paired") else "reads"
        depth = f", {reads / 10 ** 6:.0f} M {unit}" if reads else ""
        known = (f"{_gb(total)} of FASTQ" if not unknown
                 else f"{_gb(total)} of FASTQ for {len(sizes)} of them, the rest of unknown size")
        out["include"] = f"downloading {n_fetch} sample{plural} from SRA first ({known}; not timed)"
        out["warning"] = (
            f"This range leaves out the SRA download: {known}. {len(large)} of the {n_fetch} "
            f"runs hold{'s' if len(large) == 1 else ''} more than {_gb(_SMALL_RUN_FASTQ_BYTES)} "
            f"of FASTQ (largest {acc}: "
            f"{_gb(biggest['fastq_bytes'])}{depth}). How long that takes depends on ENA's "
            "transfer speed to Luria, which Nessie has not measured. The per-sample times "
            "were set for runs of typical depth, so deeper runs take longer there too.")
    elif unknown:
        out["include"] = (f"downloading {n_fetch} sample{plural} from SRA first "
                          "(timed as small runs; ENA gave no size for "
                          f"{unknown} of them)")
        out["warning"] = (
            f"ENA gave no size for {unknown} of the {n_fetch} SRA runs, so their download is "
            "timed as small runs (about 1 M reads). A deep run takes much longer: a run of "
            "about 20 GB of FASTQ did not finish downloading in 4 hours.")
    else:
        out["include"] = (f"downloading {n_fetch} sample{plural} from SRA first "
                          f"({_gb(total)} of FASTQ)")
    return out


def runtime_estimate(pipeline_key: str, n_samples: int, n_fetch: int = 0,
                     fetch_sizes: dict[str, dict] | None = None) -> dict[str, Any] | None:
    """A rough range for how long a run takes once its job starts on Luria, or None when
    the pipeline's curated JSON has no ``runtime`` block.

    The block holds ``setup_minutes`` (fixed work such as building the genome index, which
    every run with local references repeats), ``base_minutes`` (per-run steps: MultiQC and
    the like) and ``per_sample_minutes``, each a [low, high] pair, and ``basis`` saying
    where the numbers come from. Samples fetched from SRA add download time, timed as
    small runs. ``fetch_sizes`` ({accession: {"fastq_bytes", "read_count", "paired"}}, from
    ENA) names the download's size; when a run is larger than _SMALL_RUN_FASTQ_BYTES the
    download is left out of the range and ``warning`` says so and why. Queue time before the
    job starts is not included, and the text says so.
    """
    block = _load_pipeline_doc(pipeline_key).get("runtime")
    if not block:
        return None
    n_samples = max(int(n_samples or 0), 0)
    n_fetch = max(int(n_fetch or 0), 0)
    setup = block.get("setup_minutes") or [0, 0]
    base = block.get("base_minutes") or [0, 0]
    per_sample = block.get("per_sample_minutes") or [0, 0]
    low = setup[0] + base[0] + n_samples * per_sample[0]
    high = setup[1] + base[1] + n_samples * per_sample[1]
    includes = []
    if setup[1] and block.get("setup_label"):
        includes.append(f"{block['setup_label']} (about {setup[0]}–{setup[1]} min)")
    includes.append(f"{n_samples} sample{'s' if n_samples != 1 else ''} "
                    f"at about {per_sample[0]}–{per_sample[1]} min each")
    download = _sra_download(n_fetch, fetch_sizes) if n_fetch else None
    if download:
        if download["timed"]:
            low += _SRA_FETCH_MINUTES[0] + n_fetch * _SRA_FETCH_PER_SAMPLE_MINUTES[0]
            high += _SRA_FETCH_MINUTES[1] + n_fetch * _SRA_FETCH_PER_SAMPLE_MINUTES[1]
        includes.append(download.pop("include"))
    if high < 90:
        text = f"about {_five_minutes(low)}–{_five_minutes(high)} minutes"
    else:
        text = f"about {_half_hours(low)}–{_half_hours(high)} hours"
    excludes = "time waiting in the Slurm queue before the job starts"
    if download and not download["timed"]:
        at_least = "at least " if download["sized"] < download["runs"] else ""
        text += (f", plus downloading {at_least}{_gb(download['fastq_bytes'])} from SRA, "
                 "which it does not time")
        excludes = f"the SRA download, and {excludes}"
    out = {"low_minutes": low, "high_minutes": high, "text": text, "includes": includes,
           "excludes": excludes, "basis": block.get("basis", "")}
    if download:
        warning = download.pop("warning")
        out["sra_download"] = download
        if warning:
            out["warning"] = warning
    return out


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

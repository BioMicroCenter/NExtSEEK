"""Curated per-pipeline params, the species->bundle reference registry, and the
deterministic helpers configure_run uses to assemble params.yml values.

Data lives in reports/templates/nfcore/<key>.json (+ reference_bundles.json),
loaded package-relative and cached. No config dependency -> import-time testable.
"""
from __future__ import annotations

import json
import re
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


# Pulling a run's SRA accessions before the pipeline starts (the fetchngs pre-stage).
# Fixed start-up, once per run; and, for a run ENA gave no size for, a per-accession range
# that only ever held for small runs (about 1 M reads).
_SRA_FETCH_MINUTES = (5, 10)
_SRA_FETCH_PER_SAMPLE_MINUTES = (2, 10)
# Each sample downloads at about 1 MB/s, side by side with the others. Measured from the
# fetchngs execution traces on Luria (SRA_FASTQ_FTP, bytes written over task time), ENA's FTP
# to a Luria job, July to October 2026: 0.91-1.03 MB/s per sample with four at once (3.0-4.7 GB
# each, 2026-07-23), 1.16-1.17 (two Kamm runs, 0.1-0.2 GB, 2026-10-19) and 1.21-1.38 (two
# MDA-MB-231 runs, 1.6-1.7 GB, 2026-10-20). Four at once each kept that rate. A sample's
# download is one task, both mates in turn, so its time is its FASTQ size over this rate.
_SRA_BYTES_PER_SECOND = 10 ** 6
# How many samples download at once: fetchngs' SRA_FASTQ_FTP is a process_low task (2 CPUs)
# in the 16-CPU job run.sh requests (run_script.DEFAULT_RESOURCES), so 8. More than four at
# once has not been measured; six ran side by side on 2026-10-20.
_SRA_PARALLEL_DOWNLOADS = 8
# A sample whose own download takes longer than this is named in a warning: a transfer that
# breaks restarts from zero (three 17-21 GB downloads broke after 2.5-3.2 h, 2026-10-20).
_LONG_DOWNLOAD_MINUTES = 120


def _half_hours(minutes: float) -> str:
    value = round(minutes / 30) / 2
    return f"{value:g}"


def _five_minutes(minutes: float) -> int:
    return int(5 * round(minutes / 5))


def _gb(n_bytes: int) -> str:
    return f"{n_bytes / 10 ** 9:.1f} GB"


def _duration(minutes: float) -> str:
    if minutes < 90:
        return f"about {max(_five_minutes(minutes), 5)} minutes"
    return f"about {_half_hours(minutes)} hours"


def _download_minutes(sizes: list[int]) -> float:
    """How long downloads of these FASTQ sizes take, _SRA_PARALLEL_DOWNLOADS at a time at
    _SRA_BYTES_PER_SECOND each: the largest first, each to the slot that frees up soonest."""
    slots = [0.0] * _SRA_PARALLEL_DOWNLOADS
    for size in sorted(sizes, reverse=True):
        slots[slots.index(min(slots))] += size / _SRA_BYTES_PER_SECOND / 60
    return max(slots)


def _sra_download(n_fetch: int, fetch_sizes: dict[str, dict] | None) -> dict[str, Any]:
    """What the estimate adds for the SRA pre-stage: low and high minutes, the ``include``
    line, and a ``warning`` or None.

    A run ENA sized is timed at _SRA_BYTES_PER_SECOND, _SRA_PARALLEL_DOWNLOADS at a time. A
    run it gave no size for keeps the small-run range, and the warning says so."""
    sizes = {acc: s for acc, s in (fetch_sizes or {}).items() if s and s.get("fastq_bytes")}
    total = sum(s["fastq_bytes"] for s in sizes.values())
    unknown = max(n_fetch - len(sizes), 0)
    sized_minutes = _download_minutes([s["fastq_bytes"] for s in sizes.values()]) if sizes else 0.0
    low = _SRA_FETCH_MINUTES[0] + sized_minutes + unknown * _SRA_FETCH_PER_SAMPLE_MINUTES[0]
    high = _SRA_FETCH_MINUTES[1] + sized_minutes + unknown * _SRA_FETCH_PER_SAMPLE_MINUTES[1]
    plural = "s" if n_fetch != 1 else ""
    warnings: list[str] = []
    if sizes:
        at_once = min(len(sizes), _SRA_PARALLEL_DOWNLOADS)
        detail = (f"{_gb(total)} of FASTQ, {_duration(sized_minutes)} at about 1 MB/s per "
                  f"sample, {at_once} at a time")
        if unknown:
            detail += f"; {unknown} of unknown size, timed as small runs"
        acc, biggest = max(sizes.items(), key=lambda kv: kv[1]["fastq_bytes"])
        own = biggest["fastq_bytes"] / _SRA_BYTES_PER_SECOND / 60
        if own > _LONG_DOWNLOAD_MINUTES:
            reads = biggest.get("read_count")
            unit = "read pairs" if biggest.get("paired") else "reads"
            depth = f", {reads / 10 ** 6:.0f} M {unit}" if reads else ""
            warnings.append(
                f"The largest run, {acc} ({_gb(biggest['fastq_bytes'])}{depth}), takes "
                f"{_duration(own)} to download on its own at about 1 MB/s. A transfer that "
                "breaks starts again from zero, so it can take longer.")
    else:
        detail = "timed as small runs; ENA gave no size for them"
    if unknown:
        warnings.append(
            f"ENA gave no size for {unknown} of the {n_fetch} SRA runs, so their download is "
            "timed as small runs (about 1 M reads). A deep run takes much longer: about an "
            "hour per 3.6 GB of FASTQ.")
    return {"low": low, "high": high,
            "include": f"downloading {n_fetch} sample{plural} from SRA first ({detail})",
            "summary": {"runs": n_fetch, "sized": len(sizes), "fastq_bytes": total,
                        "download_minutes": round(sized_minutes)},
            "warning": " ".join(warnings) or None}


def runtime_estimate(pipeline_key: str, n_samples: int, n_fetch: int = 0,
                     fetch_sizes: dict[str, dict] | None = None) -> dict[str, Any] | None:
    """A rough range for how long a run takes once its job starts on Luria, or None when
    the pipeline's curated JSON has no ``runtime`` block.

    The block holds ``setup_minutes`` (fixed work such as building the genome index, which
    every run with local references repeats), ``base_minutes`` (per-run steps: MultiQC and
    the like) and ``per_sample_minutes``, each a [low, high] pair, and ``basis`` saying
    where the numbers come from. Samples fetched from SRA add download time: from
    ``fetch_sizes`` ({accession: {"fastq_bytes", "read_count", "paired"}}, from ENA) at
    about 1 MB/s per sample (_SRA_BYTES_PER_SECOND), or as small runs where ENA gave no size,
    and ``warning`` names a very long download or a missing size. Queue time before the job
    starts is not included, and the text says so.
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
        low += download["low"]
        high += download["high"]
        includes.append(download["include"])
    if high < 90:
        text = f"about {_five_minutes(low)}–{_five_minutes(high)} minutes"
    else:
        text = f"about {_half_hours(low)}–{_half_hours(high)} hours"
    out = {"low_minutes": low, "high_minutes": high, "text": text, "includes": includes,
           "excludes": "time waiting in the Slurm queue before the job starts",
           "basis": block.get("basis", "")}
    if download:
        out["sra_download"] = download["summary"]
        if download["warning"]:
            out["warning"] = download["warning"]
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

# nf-core modules splice some string params (multiqc_title, *_args) into bash script blocks, so a value the
# chat can steer must not carry shell syntax.
# ponytail: a character denylist, not per-param patterns; a regex-style barcode pattern with ( ) { } is refused too.
_SHELL_META = re.compile(r"""[$`;|&<>(){}\\"'\n\r]""")
# The *_args params are appended unquoted to a tool's command line: each token must be a plain flag, so no
# paths, globs or ~ (an output-file flag could overwrite lab storage).
_ARG_TOKEN = re.compile(r"^(-{0,2}[A-Za-z][A-Za-z0-9_.-]*(=[A-Za-z0-9_.,:+-]*)?|[A-Za-z0-9_][A-Za-z0-9_.,:+-]*)$")


def refuse_shell_syntax(params: dict[str, Any]) -> None:
    """Raise ValueError naming every string param that carries shell syntax.

    The last check before a params file is written, whichever path built the params; build_run_params gives the
    user the readable refusal first.
    """
    bad = sorted(k for k, v in params.items() if isinstance(v, str) and _SHELL_META.search(v))
    if bad:
        raise ValueError(f"params with shell characters refused: {', '.join(bad)}")


def agent_param_names(pipeline_key: str) -> set[str]:
    """The params the agent may send for a pipeline: its curated menu, its reference
    resources and 'genome'."""
    ctx = load_pipeline_context(pipeline_key)
    return set(ctx.get("params") or {}) | set(ctx.get("reference_resources") or []) | {"genome"}


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
    menu = load_pipeline_context(pipeline_key).get("params") or {}
    allowed_keys = agent_param_names(pipeline_key)

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
        elif value is not None and not isinstance(value, (str, bool, int, float)):
            errors.append(f"param {key!r} must be a plain value, not {type(value).__name__}.")
        elif isinstance(value, str) and _SHELL_META.search(value):
            errors.append(f"param {key!r} contains shell characters ($ ` ; | & < > ( ) {{ }} quotes, backslash or a "
                          "line break); use plain text.")
        elif isinstance(value, str) and key.endswith("_args") and not all(_ARG_TOKEN.match(t) for t in value.split()):
            errors.append(f"param {key!r} takes plain flags only (like --flag or name=value, no paths or globs).")
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

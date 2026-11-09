"""The "@" tokens a map may use: generic ones for every pipeline, plus a
pipeline's own when it has a module here.

Names are declared here and resolved lazily, so `maps` can validate a map
without importing any resolver. A map may use COMMON_TOKENS and its own
pipeline's tokens, nothing else. Most pipelines need no module.
"""
from __future__ import annotations

import importlib
from typing import Callable

COMMON_TOKENS: tuple[str, ...] = (
    "fastq_stem", "run_date", "gex_name", "row_name", "nextseek_user", "lab",
    "genome_path", "gtf_path", "file_stem")

PIPELINE_TOKENS: dict[str, tuple[str, ...]] = {
    "differentialabundance": (
        "contrast", "contrast_variable", "contrast_reference", "contrast_target",
        "paramset", "method_settings", "report_link", "model_object", "study_type_note"),
    "methylseq": ("conversion_method", "methylation_protocol"),
}


def _slug(pipeline: str) -> str:
    return str(pipeline or "").split("/")[-1].strip().lower()


def token_names_for(pipeline: str) -> tuple[str, ...]:
    return COMMON_TOKENS + PIPELINE_TOKENS.get(_slug(pipeline), ())


def tokens_for(pipeline: str) -> dict[str, Callable]:
    from NessieAI.ns.reingest.pipelines import common
    resolvers = dict(common.COMPUTED)
    slug = _slug(pipeline)
    if slug in PIPELINE_TOKENS:
        module = importlib.import_module(f"NessieAI.ns.reingest.pipelines.{slug}")
        resolvers.update(module.COMPUTED)
    return resolvers

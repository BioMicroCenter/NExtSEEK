"""nf-core/differentialabundance's own "@" tokens: contrast, paramset, method
settings, report link, model object, study-type note."""
from __future__ import annotations

import os
import re

from NessieAI.ns.reingest.pipelines.common import (
    _all_outputs, _computed_file_stem)


def _computed_contrast(run_manifest, sample, context) -> str | None:
    """The contrast id of a per_file differential-abundance row.

    nf-core/differentialabundance 1.x names a results table after the contrast
    id alone (``LPS_vs_Ctrl.deseq2.results.tsv``), so ``@file_stem`` is the id.
    2.0.0 appends the study name (``interaction_genotype_treatment_SRP254919``),
    which is not part of the contrast, so it comes off -- but only on a 2.x run,
    and only when the stem really ends with ``_<params.study_name>``: on 1.x a
    contrast that happens to end with the study name is the whole id.
    """
    stem = _computed_file_stem(run_manifest, sample, context)
    if not stem:
        return None
    version = re.match(r"v?(\d+)", str(run_manifest.pipeline.version or ""))
    study = str((run_manifest.params or {}).get("study_name") or "")
    if version and int(version.group(1)) >= 2 and study and stem.endswith("_" + study):
        return stem[:-len(study) - 1] or None
    return stem


def _major_version(run_manifest) -> int | None:
    found = re.match(r"v?(\d+)", str(run_manifest.pipeline.version or ""))
    return int(found.group(1)) if found else None


def _contrast_field(field: str):
    """``@contrast_<field>``: one direction field of the row's contrast, from the
    run's contrasts file (``RunManifest.contrasts``). None when the file was not
    in the run folder, or does not list this contrast, or (a 2.0.0 formula
    contrast) states no such field -- never a value read off the contrast id."""
    def compute(run_manifest, sample, context) -> str | None:
        contrast = _computed_contrast(run_manifest, sample, context)
        entry = (run_manifest.contrasts or {}).get(contrast or "") or {}
        return entry.get(field) or None
    return compute


# The one differential method a results file came from, by its suffix.
_RESULT_METHODS = {".deseq2.results.tsv": "deseq2", ".limma.results.tsv": "limma",
                   ".dream.results.tsv": "dream"}


# Each method's fitted model object, beside its results under other/<method>/.
# dream writes a model description (.dream.model.txt) but no object.
_MODEL_OBJECT_SUFFIXES = {"deseq2": ".dds.rld.rds", "limma": ".MArrayLM.limma.rds"}


# Settings that never change a result: compute, colours, report layout.
_SETTINGS_IGNORED = {"deseq2_cores"}


# The significance and filtering thresholds a results table was cut by.
_SHARED_SETTINGS = ("differential_max_qval", "differential_max_pval",
                    "differential_min_fold_change", "differential_subset_to_contrast_samples")


def _result_method(context) -> str | None:
    name = os.path.basename(str((context or {}).get("file_path") or ""))
    return next((m for suffix, m in _RESULT_METHODS.items() if name.endswith(suffix)), None)


def _computed_paramset(run_manifest, sample, context) -> str | None:
    """The 2.0.0 paramset a per_file row's file was published under: the folder
    between ``tables/<kind>/`` and the file. 1.x has no paramsets: None."""
    path = str((context or {}).get("file_path") or "")
    parts = path.split("/")
    if (_major_version(run_manifest) or 0) < 2 or len(parts) != 4 or parts[0] != "tables":
        return None
    return parts[2] or None


def _computed_method_settings(run_manifest, sample, context) -> str | None:
    """The settings that shaped this result, as ``key=value; ...`` sorted by key:
    its own method's parameters (``deseq2_*`` for a DESeq2 table, never limma's),
    the ``filtering_*`` parameters, and the significance thresholds. Unset values
    are skipped. None on 2.x: there each paramset carries its own settings,
    which the run's params do not hold, and the top-level defaults would be the
    wrong set to record."""
    method = _result_method(context)
    if not method or (_major_version(run_manifest) or 0) >= 2:
        return None
    params = run_manifest.params or {}
    keys = sorted(k for k in params
                  if (k.startswith(method + "_") or k.startswith("filtering_")
                      or k in _SHARED_SETTINGS) and k not in _SETTINGS_IGNORED)
    pairs = []
    for key in keys:
        value = params[key]
        if value is None or value == "":
            continue
        text = str(value).lower() if isinstance(value, bool) else str(value)
        pairs.append(f"{key}={text}")
    return "; ".join(pairs) or None


def _computed_report_link(run_manifest, sample, context) -> str | None:
    """The study report describing this row: on 2.0.0 its own paramset's
    ``report/<paramset>/*_differentialabundance_report.html``, else the run's
    report (``$outputs.differentialabundance_report_html``)."""
    paramset = _computed_paramset(run_manifest, sample, context)
    if paramset:
        prefix = f"report/{paramset}/"
        for output in sorted(_all_outputs(run_manifest, context), key=lambda o: o.path):
            if (output.path.startswith(prefix) and "/" not in output.path[len(prefix):]
                    and output.path.endswith("_differentialabundance_report.html")):
                return output.path
        return None
    return (run_manifest.named_outputs or {}).get("differentialabundance_report_html")


def _computed_model_object(run_manifest, sample, context) -> str | None:
    """The fitted model object behind this result table, from the same method's
    ``other/<method>/[<paramset>/]<stem><suffix>`` (DESeq2's ``.dds.rld.rds``,
    limma's ``.MArrayLM.limma.rds``). A bare file name, as File_PrimaryData is,
    unless another paramset wrote an object of the same name; then its folder
    stays. None for dream, which writes no object, and when none was published."""
    method = _result_method(context)
    suffix = _MODEL_OBJECT_SUFFIXES.get(method or "")
    stem = _computed_file_stem(run_manifest, sample, context)
    if not suffix or not stem:
        return None
    paramset = _computed_paramset(run_manifest, sample, context)
    wanted = f"other/{method}/" + (f"{paramset}/" if paramset else "") + stem + suffix
    outputs = _all_outputs(run_manifest, context)
    if not any(o.path == wanted for o in outputs):
        return None
    name = os.path.basename(wanted)
    if sum(os.path.basename(o.path) == name for o in outputs) > 1:
        return "/".join(wanted.split("/")[-2:])
    return name


_STUDY_TYPE_NOTES = {
    "affy_array": "Affymetrix microarray (CEL files); values are normalised array intensities",
    "geo_soft_file": "GEO series matrix; values are as GEO published them",
    "maxquant": "MaxQuant proteomics; values are protein abundance, not gene expression",
}


def _computed_study_type_note(run_manifest, sample, context) -> str | None:
    """For a differentialabundance run that is not RNA-seq, what the values are
    (``study_type: maxquant (...)``). None for RNA-seq, the case the rules are
    written for, and for a run that records no study type."""
    study_type = str((run_manifest.params or {}).get("study_type") or "")
    if not study_type or study_type == "rnaseq":
        return None
    detail = _STUDY_TYPE_NOTES.get(study_type, "not RNA-seq")
    return f"study_type: {study_type} ({detail})"


COMPUTED = {
    "contrast": _computed_contrast,
    "contrast_variable": _contrast_field("variable"),
    "contrast_reference": _contrast_field("reference"),
    "contrast_target": _contrast_field("target"),
    "paramset": _computed_paramset,
    "method_settings": _computed_method_settings,
    "report_link": _computed_report_link,
    "model_object": _computed_model_object,
    "study_type_note": _computed_study_type_note,
}

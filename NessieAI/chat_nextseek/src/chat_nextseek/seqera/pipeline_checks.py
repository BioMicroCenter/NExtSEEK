"""Checks a pipeline's own code makes only by exiting mid-run, made before anything is launched.

Two kinds, keyed by catalog key:

- `row_errors(pipeline, rows, evidence)`: the agent's samplesheet rows (generic column names,
  before PIPELINE_COLUMN_ALIASES), called by write_samplesheet after the param atlas's own row
  check. `evidence` is the session's data_driven_evidence, so a blank data-driven cell can say
  what the sample's metadata decided.
- `param_errors(pipeline, params)`: the merged run params, called by build_run_params.

Each check cites the release code it mirrors. Standard library only.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Mapping, Sequence

_NO_SPACE = re.compile(r"^\S+$")


def _cell(row: Mapping[str, Any], column: str) -> str:
    value = row.get(column)
    return "" if value is None else str(value).strip()


def _uid(row: Mapping[str, Any]) -> str:
    return _cell(row, "sample") or _cell(row, "Sample") or "?"


def _on(value: Any) -> bool:
    return value is True or str(value).strip().lower() in ("true", "1", "yes")


def _verdict_hint(evidence: Mapping[str, Any] | None, uid: str, column: str) -> str:
    verdict = ((evidence or {}).get(uid) or {}).get(column) or {}
    if verdict.get("value") not in (None, ""):
        return (f" The sample's metadata verdict is {verdict['value']!r} ({verdict.get('verdict')}): "
                "write that.")
    return " Nothing in its metadata decides it: ask the user."


# --- nf-core/eager 2.5.3 (main.nf extract_data) ---------------------------------------------------

#: Data-driven TSV cells the agent writes (SeqType, Library_ID and the NA cells are the emitter's),
#: with the values extract_data accepts (None: any text without whitespace).
_EAGER_CELLS: dict[str, tuple[str, ...] | None] = {
    "Lane": None,
    "Colour_Chemistry": ("2", "4"),
    "Strandedness": ("single", "double"),
    "UDG_Treatment": ("none", "half", "full"),
}


def _eager_rows(rows: Sequence[Mapping[str, Any]], evidence: Mapping[str, Any] | None) -> list[str]:
    errors: list[str] = []
    for row in rows:
        uid = _uid(row)
        for column, allowed in _EAGER_CELLS.items():
            value = _cell(row, column)
            if not value:
                errors.append(f"row {uid}: {column} is blank, and eager refuses an empty cell."
                              + _verdict_hint(evidence, uid, column))
            elif allowed is not None and value not in allowed:
                errors.append(f"row {uid}: {column} is {value!r}; eager accepts only "
                              f"{', '.join(allowed)}.")
            elif allowed is None and not _NO_SPACE.match(value):
                errors.append(f"row {uid}: {column} {value!r} has a space; eager names its "
                              "files after it.")
    return errors


def _eager_params(params: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    # main.nf "BAM filtering validation": a quality threshold without filtering exits.
    if not _on(params.get("run_bam_filtering")) and str(params.get("bam_mapping_quality_threshold") or 0) != "0":
        errors.append("bam_mapping_quality_threshold needs run_bam_filtering: true (eager exits otherwise).")
    if _on(params.get("run_genotyping")):
        tool = str(params.get("genotyping_tool") or "")
        # No genotyping process runs without a tool: the run would finish with no genotypes.
        if not tool:
            errors.append("run_genotyping needs genotyping_tool (ug, hc or freebayes); without one "
                          "no genotyping step runs.")
        source = str(params.get("genotyping_source") or "raw")
        # main.nf: genotyping from 'trimmed' or 'pmd' BAMs exits unless that step ran.
        if source == "trimmed" and not _on(params.get("run_trim_bam")):
            errors.append("genotyping_source trimmed needs run_trim_bam: true (eager exits otherwise).")
        if source == "pmd" and not _on(params.get("run_pmdtools")):
            errors.append("genotyping_source pmd needs run_pmdtools: true (eager exits otherwise).")
    return errors


# --- nf-core/raredisease 3.1.2 (assets/schema_input.json, docs/usage.md) --------------------------

_SEX = ("0", "1", "2", "other")
_PHENOTYPE = ("0", "1", "2")
_NO_PARENT = ("", "0")
#: Columns every row of one sample must agree on: raredisease merges a sample's lanes.
_PEDIGREE = ("sex", "phenotype", "case_id", "paternal_id", "maternal_id")


def _raredisease_rows(rows: Sequence[Mapping[str, Any]],
                      evidence: Mapping[str, Any] | None) -> list[str]:
    errors: list[str] = []
    by_sample: dict[str, dict[str, str]] = {}
    for row in rows:
        uid = _uid(row)
        cells = {c: _cell(row, c) for c in _PEDIGREE}
        seen = by_sample.setdefault(uid, cells)
        if seen != cells:
            errors.append(f"row {uid}: its lanes disagree on its pedigree "
                          f"({', '.join(c for c in _PEDIGREE if seen[c] != cells[c])}); one sample "
                          "has one sex, phenotype, case and parents.")
    for uid, cells in by_sample.items():
        case = cells["case_id"]
        if not case:
            errors.append(f"row {uid}: case_id is blank. raredisease calls each family (case) "
                          "together: ask the user which family this sample belongs to, unless its "
                          "metadata names one.")
        elif not _NO_SPACE.match(case):
            errors.append(f"row {uid}: case_id {case!r} has a space, which raredisease refuses.")
        if cells["sex"] and cells["sex"] not in _SEX:
            errors.append(f"row {uid}: sex is {cells['sex']!r}; raredisease takes 1 (male), "
                          "2 (female), 0 or other (unknown).")
        if cells["phenotype"] and cells["phenotype"] not in _PHENOTYPE:
            errors.append(f"row {uid}: phenotype is {cells['phenotype']!r}; raredisease takes "
                          "2 (affected), 1 (unaffected) or 0 (missing).")
        for column, sex, word in (("paternal_id", "1", "father"), ("maternal_id", "2", "mother")):
            parent = cells[column]
            if parent in _NO_PARENT:
                continue
            if parent == uid:
                errors.append(f"row {uid}: {column} names the sample as its own {word}.")
                continue
            other = by_sample.get(parent)
            if other is None:
                errors.append(f"row {uid}: {column} {parent!r} is not a sample in this sheet; name "
                              f"the {word}'s own sample, or leave it blank when the {word} is not "
                              "sequenced.")
                continue
            if other["case_id"] != case:
                errors.append(f"row {uid}: its {word} {parent} is in case {other['case_id']!r}, "
                              f"not {case!r}; a family shares one case_id.")
            if other["sex"] != sex:
                errors.append(f"row {uid}: its {word} {parent} has sex {other['sex']!r}, not "
                              f"{sex!r}; check the pedigree with the user.")
    return errors


_SKIPPED_SUBWORKFLOWS = {
    "repeat_calling": "variant_catalog",
    "repeat_annotation": "variant_catalog",
    "snv_annotation": "vep_cache, gnomad_af, vcfanno_resources and the rank models",
    "sv_annotation": "svdb_query_dbs and vep_cache",
    "mt_annotation": "vep_cache and vcfanno_resources",
    "me_calling": "mobile_element_references",
    "me_annotation": "mobile_element_svdb_annotations and vep_cache",
    "generate_clinical_set": "vep_filters (and the annotation it filters)",
}
_SKIPPED_TOOLS = {
    "gens": "the Gens panels of normals and gnomAD positions",
    "germlinecnvcaller": "a GATK ploidy model, gCNV models and readcount_intervals",
}


def _raredisease_params(params: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    for param, needed in (("skip_subworkflows", _SKIPPED_SUBWORKFLOWS), ("skip_tools", _SKIPPED_TOOLS)):
        given = {s.strip() for s in str(params.get(param) or "").split(",") if s.strip()}
        for name, refs in needed.items():
            if name not in given:
                errors.append(f"{param} must keep {name}: it needs {refs}, which are not staged on "
                              "Luria, so the run would abort.")
    if str(params.get("analysis_type") or "wgs") == "wes" and not str(params.get("target_bed") or "").strip():
        errors.append("analysis_type wes needs target_bed: the capture kit's target BED as a path on "
                      "the cluster.")
    return errors


RowCheck = Callable[[Sequence[Mapping[str, Any]], "Mapping[str, Any] | None"], list]
_ROW_CHECKS: dict[str, RowCheck] = {"eager": _eager_rows, "raredisease": _raredisease_rows}
_PARAM_CHECKS: dict[str, Callable[[Mapping[str, Any]], list]] = {
    "eager": _eager_params, "raredisease": _raredisease_params}


def row_errors(pipeline: str, rows: Sequence[Mapping[str, Any]],
               evidence: Mapping[str, Any] | None = None) -> list[str]:
    """Problems in the agent's rows that the pipeline would refuse mid-run; [] when none, or
    when the pipeline has no check."""
    check = _ROW_CHECKS.get(pipeline)
    return check(rows, evidence) if check else []


def param_errors(pipeline: str, params: Mapping[str, Any]) -> list[str]:
    """Problems in the merged run params that the pipeline would refuse mid-run."""
    check = _PARAM_CHECKS.get(pipeline)
    return check(params) if check else []

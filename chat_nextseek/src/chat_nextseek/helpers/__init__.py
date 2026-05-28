"""Helpers package — utilities + I/O tools.

PUBLIC API: This `__init__.py` re-exports symbols that external consumers
(notably dmac_assistant's nextseek plugin) import as `chat_nextseek.helpers.<name>`.
DO NOT remove re-exports from this file without coordinating with downstream
consumers. See CLAUDE.md "Portability Contract" section.
"""
from __future__ import annotations

from .prompts import load_prompt, log_usage, log_prompt, log_llm_call  # noqa: F401
from .json_io import _extract_required_paths, estimate_tokens_from_text, safe_parse_json  # noqa: F401
from .text import strip_html, strip_html_recursive, load_file_for_memory, load_json_for_memory  # noqa: F401
from .tools.memory_code import (  # noqa: F401
    _json_type_name,
    _compact_json_value,
    _merge_skeletons,
    _build_skeleton_node,
    _find_record_arrays,
    build_memory_data_profile,
    _build_field_index,
    MemoryCodeSafetyError,
    MemoryCodeTimeoutError,
    _validate_memory_code,
    execute_memory_code,
)
from .results import slim_api_result_for_llm, collect_bundle_files, normalize_api_result_for_memory  # noqa: F401
from .tools.neo4j import tool_neo4j_query  # noqa: F401
from .tools.nextseek_api import (  # noqa: F401
    tool_nextseek_api_request,
    _sanitize_api_row_strings,
    log_api_call,
    fix_sample_endpoint,
    build_recent_results_summary,
    _extract_total_and_rows,
    _should_retry_advanced_search,
    _split_retry_keyword,
    _has_expandable_keyword,
    _advanced_search_retry_attempts,
    _retry_advanced_search_if_empty,
)
from .tools.catalog_match import (  # noqa: F401
    _norm_text,
    _tokenize,
    _doc_from_sampletype,
    _doc_from_assay,
    _score_pair,
    shortlist_catalog,
)
from .tools.catalog_semantic import (  # noqa: F401
    SemanticIndex,
    fuse_rrf,
    dynamic_cutoff,
    doc_sampletype,
    doc_assay,
    doc_endpoint,
)
from .dates import (  # noqa: F401
    _normalize_project_id,
    _normalize_years,
    _parse_month,
    _month_range_to_yymmdd_bounds,
    _parse_day,
    _day_range_to_yymmdd_bounds,
)
from .lineage import enumerate_lineage_leaves  # noqa: F401

# Re-export from sibling reports/ package — preserves the legacy import path
# `from chat_nextseek.helpers import run_reporter_summary` used by the plugin.
#
# These are exposed LAZILY via PEP 562 `__getattr__`. An eager `from ..reports.*`
# block here forms a circular import: several `reports/*` modules import from
# `..helpers.*` at module scope, which forces this `__init__` to run, which in
# turn imports back into `reports.*` — and whichever side is imported first ends
# up only partially initialized. Resolving the name on first *access* (after both
# packages have finished importing) breaks that cycle while keeping the public
# `chat_nextseek.helpers.<name>` import paths intact.
_REPORTS_REEXPORTS: dict[str, str] = {
    # ..reports.runners
    "run_project_sample_report": "runners",
    "run_project_protocols_report": "runners",
    "run_project_published_report": "runners",
    "run_reporter_summary": "runners",
    # ..reports.metadata
    "annotate_metadata_with_sampletypes": "metadata",
    "fetch_reporter_metadata": "metadata",
    "_scalar_for_summary": "metadata",
    "_entries_from_metadata": "metadata",
    "build_metadata_summary": "metadata",
    "_is_sequencing_type": "metadata",
    "filter_summary_to_sequencing_lineage": "metadata",
    "build_accession_metadata_lookup": "metadata",
    "filter_summary_for_deg": "metadata",
    # ..reports.protocols
    "extract_protocol_refs_from_metadata": "protocols",
    "_request_protocol_record": "protocols",
    "fetch_protocols": "protocols",
    "_extract_docx_text": "protocols",
    "_extract_pdf_text": "protocols",
    "sanitize_protocols_for_llm": "protocols",
    "download_and_extract_protocol_blobs": "protocols",
    # ..reports.nfcore
    "top_items": "nfcore",
    "_extract_nfcore_samplesheet_rows": "nfcore",
    "_accession_matches_criterion": "nfcore",
    "_handle_nfcore_artifacts": "nfcore",
    "_build_cohort_summary_md": "nfcore",
    # ..reports.outputs
    "persist_report_file": "outputs",
    "generate_report_outputs": "outputs",
    # ..reports.templates_meta
    "load_report_template": "templates_meta",
    "normalize_report_type": "templates_meta",
    "nfcore_pipeline_from_report_type": "templates_meta",
    "get_report_template_basename": "templates_meta",
    # ..reports.exporters.geo_xlsx
    "_copy_row_format": "exporters.geo_xlsx",
    "_write_cell": "exporters.geo_xlsx",
    "_write_list_down": "exporters.geo_xlsx",
    "_build_header_map": "exporters.geo_xlsx",
    "_normalize_geo_key": "exporters.geo_xlsx",
    "_normalize_sheet_label": "exporters.geo_xlsx",
    "_geo_get": "exporters.geo_xlsx",
    "_find_first_row_with_label": "exporters.geo_xlsx",
    "_find_sample_header_row": "exporters.geo_xlsx",
    "_find_paired_end_header_row": "exporters.geo_xlsx",
    "_select_study_summary": "exporters.geo_xlsx",
    "_populate_geo_seq_workbook": "exporters.geo_xlsx",
    "export_geo_report_to_seq_xlsx": "exporters.geo_xlsx",
    # ..reports.exporters.sra_xlsx
    "_extract_sra_section_reports": "exporters.sra_xlsx",
    "_worksheet_headers": "exporters.sra_xlsx",
    "_write_template_rows": "exporters.sra_xlsx",
    "_export_sra_section_to_xlsx": "exporters.sra_xlsx",
    "export_sra_report_to_xlsx": "exporters.sra_xlsx",
    "export_sra_biosample_report_to_xlsx": "exporters.sra_xlsx",
    # ..reports.exporters.csv
    "_coerce_scalar_csv_value": "exporters.csv",
    "_normalize_rows_for_csv": "exporters.csv",
    "_extract_report_section_rows": "exporters.csv",
    "_ordered_csv_columns": "exporters.csv",
    "_write_csv_rows": "exporters.csv",
}


def __getattr__(name: str):
    """PEP 562 lazy re-export of reports.* symbols (see comment above)."""
    submodule = _REPORTS_REEXPORTS.get(name)
    if submodule is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    mod = importlib.import_module(f"..reports.{submodule}", __name__)
    value = getattr(mod, name)
    globals()[name] = value  # cache so subsequent access skips __getattr__
    return value


def __dir__() -> list[str]:
    return sorted(list(globals().keys()) + list(_REPORTS_REEXPORTS.keys()))

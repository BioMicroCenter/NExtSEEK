"""Helpers package — utilities + I/O tools.

PUBLIC API: This `__init__.py` re-exports symbols that external consumers
(notably dmac_assistant's nextseek plugin) import as `chat_nextseek.helpers.<name>`.
DO NOT remove re-exports from this file without coordinating with downstream
consumers. See CLAUDE.md "Portability Contract" section.
"""
from __future__ import annotations

from .prompts import load_prompt, log_usage, log_prompt  # noqa: F401
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
from ..reports.runners import (  # noqa: F401
    run_project_sample_report,
    run_project_protocols_report,
    run_project_published_report,
    run_reporter_summary,
)
from ..reports.metadata import (  # noqa: F401
    annotate_metadata_with_sampletypes,
    fetch_reporter_metadata,
    _scalar_for_summary,
    _entries_from_metadata,
    build_metadata_summary,
    _is_sequencing_type,
    filter_summary_to_sequencing_lineage,
    build_accession_metadata_lookup,
    filter_summary_for_deg,
)
from ..reports.protocols import (  # noqa: F401
    extract_protocol_refs_from_metadata,
    _request_protocol_record,
    fetch_protocols,
    _extract_docx_text,
    _extract_pdf_text,
    sanitize_protocols_for_llm,
    download_and_extract_protocol_blobs,
)
from ..reports.nfcore import (  # noqa: F401
    top_items,
    _extract_nfcore_samplesheet_rows,
    _accession_matches_criterion,
    _handle_nfcore_artifacts,
    _build_cohort_summary_md,
)
from ..reports.outputs import persist_report_file, generate_report_outputs  # noqa: F401
from ..reports.templates_meta import (  # noqa: F401
    load_report_template,
    normalize_report_type,
    nfcore_pipeline_from_report_type,
    get_report_template_basename,
)
from ..reports.exporters.geo_xlsx import (  # noqa: F401
    _copy_row_format,
    _write_cell,
    _write_list_down,
    _build_header_map,
    _normalize_geo_key,
    _normalize_sheet_label,
    _geo_get,
    _find_first_row_with_label,
    _find_sample_header_row,
    _find_paired_end_header_row,
    _select_study_summary,
    _populate_geo_seq_workbook,
    export_geo_report_to_seq_xlsx,
)
from ..reports.exporters.sra_xlsx import (  # noqa: F401
    _extract_sra_section_reports,
    _worksheet_headers,
    _write_template_rows,
    _export_sra_section_to_xlsx,
    export_sra_report_to_xlsx,
    export_sra_biosample_report_to_xlsx,
)
from ..reports.exporters.csv import (  # noqa: F401
    _coerce_scalar_csv_value,
    _normalize_rows_for_csv,
    _extract_report_section_rows,
    _ordered_csv_columns,
    _write_csv_rows,
)

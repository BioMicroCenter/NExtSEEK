"""Tools + dispatch for the full-agentic nf-core pipeline agent.

Four anthropic-style tools driven by BedrockClient.chat_with_tools:
  - resolve_samples:   UIDs/last-search -> compact leaf table (+ caches refs)
  - write_samplesheet: agent-built cohorts -> validated samplesheet + launch.yml
  - submit_to_tower:   submit the built launch artifacts
  - conclude:          terminate the conversation (control tool, intercepted by the loop)
"""
from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..config import ChatConfig

PIPELINE_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "resolve_samples",
        "description": (
            "Resolve a sample reference into a per-leaf metadata table. Call this FIRST. "
            "ref.kind is 'last_search' (the user's most recent search results), "
            "'explicit_uids' (uids you were given), or 'accessions' (raw GEO/ENA accessions "
            "for fetchngs). Returns each sequencing leaf with its uid, sample_type, assay, "
            "any accessions, and the grouping-candidate fields with their distinct values. "
            "Use the returned fields+values to map a group-by phrase to a real field. "
            "Pass the pipeline_key you intend to run so the right leaf sample types are eligible."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["last_search", "explicit_uids", "accessions"]},
                "uids": {"type": "array", "items": {"type": "string"},
                         "description": "Required when kind='explicit_uids'."},
                "accessions": {"type": "array", "items": {"type": "string"},
                               "description": "Required when kind='accessions'."},
                "pipeline_key": {"type": "string",
                                 "description": "the pipeline you intend to run; determines which leaf sample types are eligible."},
            },
            "required": ["kind"],
        },
    },
    {
        "name": "write_samplesheet",
        "description": (
            "Build the nf-core samplesheet(s) and launch artifacts from cohorts YOU assemble. "
            "Each cohort is one pipeline run. Put each sample in exactly one cohort. For a "
            "group-by, make one cohort per distinct field value; for a filter, make one cohort "
            "of the matching samples. Every row's 'sample' and (if present) 'accession' MUST "
            "come from a resolve_samples result — invented refs are rejected and returned to you "
            "to fix. Leave fastq_1/fastq_2 empty; the ENA layer fills them from accessions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pipeline_key": {"type": "string",
                                 "description": "Catalog key, e.g. 'rnaseq', 'scrnaseq', 'fetchngs'."},
                "cohorts": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "description": "kebab-case cohort label, unique."},
                            "rows": {
                                "type": "array",
                                "items": {"type": "object", "description": "samplesheet row; keys are column names."},
                            },
                        },
                        "required": ["label", "rows"],
                    },
                },
            },
            "required": ["pipeline_key", "cohorts"],
        },
    },
    {
        "name": "submit_to_tower",
        "description": (
            "Submit the most recently built launch artifacts to Seqera Tower. Only call this "
            "AFTER the user has confirmed they want to submit. If Tower is not configured this "
            "returns the samplesheet path instead of submitting."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "conclude",
        "description": (
            "End the conversation. Call this when the task is fully done: after a successful "
            "submit (outcome='submitted'), after answering a standalone question (outcome='answered'), "
            "when the request can't be done (outcome='rejected'), or on user cancel (outcome='cancelled'). "
            "Do NOT call conclude when you are pausing to ask the user something — just write your "
            "question as plain text and stop."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "outcome": {"type": "string", "enum": ["submitted", "rejected", "cancelled", "answered"]},
                "message": {"type": "string", "description": "final user-facing message."},
            },
            "required": ["outcome", "message"],
        },
    },
]

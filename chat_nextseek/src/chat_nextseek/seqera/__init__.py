"""Seqera/nf-core integration: pipeline catalog, ENA accession resolution,
samplesheet/launch.yml emission, and optional seqerakit submission."""

from .catalog import NFCORE_PIPELINE_CATALOG, get_pipeline_entry, list_pipeline_keys
from .ena import (
    ENAResolution,
    extract_accessions_from_metadata,
    resolve_accessions,
    resolve_accession_to_fastqs,
)
from .emitter import EmissionResult, emit_launch_artifacts, emit_nfcore_artifacts, write_combined_launch_yml
from .submitter import submit_launch
from .tower_client import TowerAPIError, TowerClient
from .tower_datasets import upload_samplesheet_as_dataset

__all__ = [
    "ENAResolution",
    "EmissionResult",
    "NFCORE_PIPELINE_CATALOG",
    "emit_launch_artifacts",
    "emit_nfcore_artifacts",
    "extract_accessions_from_metadata",
    "get_pipeline_entry",
    "list_pipeline_keys",
    "resolve_accessions",
    "resolve_accession_to_fastqs",
    "submit_launch",
    "TowerAPIError",
    "TowerClient",
    "upload_samplesheet_as_dataset",
    "write_combined_launch_yml",
]

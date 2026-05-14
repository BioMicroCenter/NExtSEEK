from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ReporterPlan(BaseModel):
    project: str | None = None
    years: list[int | str] = Field(default_factory=list)
    month_range: list[str] | None = None
    day_range: list[str] | None = None
    reporter_mode: str | None = None          # "summary" | "report_generation"
    summary_mode: str | None = None           # "samples" | "protocols" | "published" | "RPPR"
    report_type: str | None = None
    uids: list[str] = Field(default_factory=list)
    reporter_context: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""

    model_config = ConfigDict(extra="ignore")


class ReporterSummary(BaseModel):
    project: str | None = None
    project_id: int | str | None = None
    rows_returned: int | None = None
    filters: dict[str, Any] = Field(default_factory=dict)
    top_sampletypes: list[dict[str, Any]] = Field(default_factory=list)
    top_labs: list[dict[str, Any]] = Field(default_factory=list)
    years: list[dict[str, Any]] = Field(default_factory=list)
    top_months: list[dict[str, Any]] = Field(default_factory=list)
    uuid_report_file: str | None = None

    model_config = ConfigDict(extra="ignore")


class ReportWriterPlan(BaseModel):
    report_type: str | None = None
    reporter_context: dict[str, Any] = Field(default_factory=dict)
    notes: str = ""

    model_config = ConfigDict(extra="ignore")


class ReportWriterOutput(BaseModel):
    report_type: str | None = None
    report: dict[str, Any] = Field(default_factory=dict)
    narrative: str | None = None
    notes: str = ""

    model_config = ConfigDict(extra="ignore")


class GEOReportBody(BaseModel):
    description: str | None = None
    study: dict[str, Any] = Field(default_factory=dict)
    samples: list[dict[str, Any]] = Field(default_factory=list)
    protocols: dict[str, Any] = Field(default_factory=dict)
    paired_end_experiments: list[dict[str, Any]] = Field(default_factory=list)
    notes: str | None = None
    checksums: dict[str, Any] = Field(default_factory=dict)

    model_config = ConfigDict(extra="ignore")


class ReportWriterOutputGEO(BaseModel):
    report_type: str | None = "GEO"
    report: GEOReportBody = Field(default_factory=GEOReportBody)
    narrative: str | None = None
    notes: str = ""

    model_config = ConfigDict(extra="ignore")


class PipelineCohort(BaseModel):
    """One nf-core run cohort: a pipeline + the metadata criterion that scopes
    samples into it. A study with mixed library types (e.g. RNA-seq + amplicon)
    yields multiple cohorts, each launched as its own Tower run.
    """

    label: str = Field(description="Short slug used as subdir name, e.g. 'rnaseq' or 'ampliseq'")
    pipeline: str = Field(description="Catalog key: rnaseq | scrnaseq | atacseq | chipseq | sarek | methylseq | ampliseq | fetchngs")
    rationale: str = ""
    enrichment_metadata_fields: list[str] = Field(
        default_factory=list,
        description=(
            "Per-cohort NExtSEEK metadata field names to carry into the samplesheet "
            "as extra columns. Pick fields that vary across samples within THIS cohort."
        ),
    )
    cohort_criterion: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Metadata field → value pairs that scope samples into this cohort. "
            "A sample is included if every key=value pair matches its flat lineage "
            "metadata (case-insensitive value compare). Empty dict = all samples."
        ),
    )
    expected_sample_count: int = 0

    model_config = ConfigDict(extra="ignore")


class SeqeraLaunchPlan(BaseModel):
    """Tower launch plan: params + run-name/path suffixes for the chosen pipeline."""

    run_name: str
    params: dict[str, Any] = Field(default_factory=dict)
    outdir_suffix: str = ""
    work_dir_suffix: str = ""
    pipeline_revision: str | None = None
    profile: str | None = None
    notes: str = ""

    model_config = ConfigDict(extra="ignore")

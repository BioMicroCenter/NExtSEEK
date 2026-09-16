"""The RunManifest: the single structured artifact a harvest produces.

Nothing downstream reads the run directory again. `sources` maps each manifest
field to the file it came from so a surprising value is traceable without a
re-run, and `uid_resolution` records HOW each UID was matched so a weaker match
is never mistaken for a strong one.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

MANIFEST_VERSION = 1

# uid_resolution values, strongest first.
RESOLUTION_LAUNCH_RECORD = "launch_record"
RESOLUTION_FASTQ_EXACT = "fastq_path_exact"
RESOLUTION_FASTQ_BASENAME = "fastq_basename"
RESOLUTION_AMBIGUOUS = "ambiguous"
RESOLUTION_MULTIRUN = "multirun"
RESOLUTION_UNRESOLVED = "unresolved"

RUN_COMPLETE = "complete"
RUN_INCOMPLETE = "incomplete"


class PipelineInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    version: str = ""
    nextflow_version: str = ""
    run_name: str = ""


class SampleRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nfcore_sample: str
    fastq_1: str = ""
    fastq_2: str | None = None
    d_seq_uid: str | None = None
    uid_resolution: str = RESOLUTION_UNRESOLVED
    strandedness_declared: str | None = None
    strandedness_inferred: str | None = None
    metrics: dict[str, float | str] = Field(default_factory=dict)
    derived: dict[str, float] = Field(default_factory=dict)


class OutputRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    bytes: int = 0
    sample: str | None = None


class ExecutionInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processes: int = 0
    failed: int = 0
    non_terminal: int = 0


class CapsInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")
    files_read: int = 0
    bytes_read: int = 0
    truncated: list[str] = Field(default_factory=list)


class RunManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manifest_version: int = MANIFEST_VERSION
    run_dir: str
    run_status: str = RUN_INCOMPLETE
    pipeline: PipelineInfo = Field(default_factory=PipelineInfo)
    params: dict = Field(default_factory=dict)
    software_versions: dict[str, str] = Field(default_factory=dict)
    # Full fidelity for software_versions: {process: {tool: version}}, so a
    # tool reported at two different versions under two different processes
    # (see parsers.parse_software_versions_by_process) is never collapsed to
    # whichever `software_versions` happened to keep last. This is the
    # source of truth; `software_versions` above is the flat convenience view.
    software_versions_by_process: dict[str, dict[str, str]] = Field(default_factory=dict)
    samples: list[SampleRecord] = Field(default_factory=list)
    # `outputs` is the INVENTORY: one entry per candidate output file found by
    # matching INVENTORY_GLOBS against a remote listing (see harvest.py) --
    # never the file's content, just its path and real size. The next reingest
    # plan matches its own per-pipeline globs (e.g.
    # "{star_salmon,star_rsem}/*.markdup.sorted.bam") against this list.
    # `named_outputs` is the second, different consumer: well-known SINGLE
    # files resolved from that same inventory by name (at minimum
    # `multiqc_report_html`, `kraken2_report`, `deseq2_dds_rdata`). A map
    # reference like "$outputs.multiqc_report_html" resolves against
    # `named_outputs`, never against `outputs` -- `outputs` is a list, so a
    # `bag.get(key)`-style lookup against it silently returns None for every
    # key (see the 2026-09-16 whole-branch review that found this).
    outputs: list[OutputRecord] = Field(default_factory=list)
    named_outputs: dict[str, str] = Field(default_factory=dict)
    execution: ExecutionInfo = Field(default_factory=ExecutionInfo)
    sources: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    caps: CapsInfo = Field(default_factory=CapsInfo)

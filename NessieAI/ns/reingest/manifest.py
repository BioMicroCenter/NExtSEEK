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
    # The resolved parent's UID. The NAME is now historical: a parent found
    # through the launch record or by path can legitimately be an
    # already-analysed A.* sample (e.g. A.ALN, A.VCF), not only a raw D.SEQ
    # -- see maps.PipelineMap.accepts_parent_types -- but nothing downstream
    # depends on the name, so it is not renamed here. `parent_sample_type`
    # below carries the actual SampleType title this UID points at.
    d_seq_uid: str | None = None
    # The resolved parent's real SampleType title (e.g. "D.SEQ", "A.ALN"),
    # looked up from the database by `harvest.py` via
    # `nextseek_api.services.reingest_lookups.sample_types_for_uids` --
    # never parsed off the UID's prefix, since ~1.5% of real samples do not
    # follow that convention (free-text titles on CEL samples, measured
    # against the live database). Empty string means "not known": either no
    # lookup was reachable at harvest time, or the lookup could not resolve
    # this UID. `mapper.py` is the layer that decides what an unknown parent
    # type means for the QC backfill row -- it must never guess.
    parent_sample_type: str = ""
    # Populated only when `uid_resolution == RESOLUTION_MULTIRUN`: the D.SEQ
    # UIDs recovered for THIS sample's own contributing samplesheet rows
    # (see uid_resolve._resolve_multirun_parents), first-occurrence
    # de-duplicated, in samplesheet order. `d_seq_uid` above stays None for
    # a multi-run sample -- there is no single parent to name -- so this is
    # the only place a multi-run sample's lineage lives. Empty when none of
    # its rows resolved (a wholly-unresolved multi-run sample); a non-empty
    # but short list is a real, honest partial resolution, not an error.
    d_seq_uid_multirun: list[str] = Field(default_factory=list)
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
    # {OutputRecord.path: hex md5 digest}. Filled in two ways, both measured
    # by us (there is no separate provenance to track -- see the removed
    # `.md5`-sibling design in this branch's history for why that distinction
    # was deliberately dropped): (1) `harvest.harvest_local`, folding in
    # run-harvest's own automatic hash of any inventoried output cheap
    # enough to fit under granular.py's `_HARVEST_CHECKSUM_MAX_FILE_BYTES` /
    # `_HARVEST_CHECKSUM_MAX_TOTAL_BYTES` ceilings, computed during the same
    # SSH staging call that already produced the inventory -- never a second
    # round trip; and (2) `granular._run_checksum` with `--manifest-id`, an
    # explicit caller-requested hash of a caller-named set of files (e.g. a
    # multi-GB BAM too large for the automatic ceiling). Keyed by the SAME
    # run-relative path string as `OutputRecord.path` / `outputs[].path`, not
    # by a canonical name like `named_outputs` -- an arbitrary subset of the
    # inventory may be hashed, so there is no fixed set of keys to name in
    # advance. `mapper.apply` looks a row's own primary-output path up here
    # to fill `Checksum_PrimaryData`; a path with no entry (too large for
    # both the automatic ceiling and an explicit run-checksum call, or not
    # yet checksummed at all) simply contributes nothing, the same as any
    # other unresolved optional attribute.
    checksums: dict[str, str] = Field(default_factory=dict)
    execution: ExecutionInfo = Field(default_factory=ExecutionInfo)
    sources: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    caps: CapsInfo = Field(default_factory=CapsInfo)

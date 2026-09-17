"""Per-pipeline output maps: the only place a raw key becomes a sample attribute.

A map is data, not code. `$`-prefixed values are LOOKUPS into a named manifest
section and nothing else — there is no evaluation, no attribute traversal beyond
one dotted key, and no way to reach a Python object. That is what makes a map
file reviewable in a diff rather than a security surface.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

MAPS_DIR = Path(__file__).resolve().parent.parent / "reingest_maps"

# The only sections a $-ref may name. "outputs" reads RunManifest.named_outputs
# (well-known single files by key), never RunManifest.outputs (the file
# inventory, which is a list and has no .get(key)). "checksums" reads
# RunManifest.checksums directly (path -> hex digest) -- a run-scoped bag
# like the others, not a per-sample one, since a checksum is keyed by the
# file's path, not by which sample it belongs to. It resolves to None until
# run-checksum has actually hashed that path (see manifest.py's docstring on
# the field); a map rule referencing it before that is an ordinary miss, not
# an error.
#
# No committed map (rnaseq.outputs.json included) actually spells a
# "$checksums.<path>" ref today, and none is expected to: an output rule's
# `glob` matches a harvest-time path that varies per run (per sample, even),
# so no map file authored ahead of time can name it literally. That is
# exactly why `Checksum_PrimaryData` is wired the other way --
# `mapper._attach_checksum` re-derives the SAME path from the rule's own
# `glob`/`primary_data` and looks it up in `RunManifest.checksums` directly,
# bypassing `resolve_ref` entirely -- and is the checksum wiring's real,
# reachable, tested consumer (test_mapper.py, and the rendered-workbook
# round-trip in test_build_upload_manifest.py). "checksums" stays in
# `_RUN_SECTIONS` for the one thing `resolve_ref` DOES support today and
# `test_maps.py` exercises directly (`test_resolve_ref_reads_a_checksum_by_path`):
# a literal, hand-typed "$checksums.<path>" naming a path that genuinely is
# fixed ahead of time -- there is no such path anywhere in a committed map
# today, but the mechanism is real, not aspirational.
#
# It does NOT, despite an earlier version of this comment's claim, cover
# `named_outputs` keys like `deseq2_dds_rdata` as "a narrower future use one
# map edit away": those are resolved by `harvest._NAMED_OUTPUT_MATCHERS`
# matching a SUFFIX against whatever the run's inventory actually contains
# (e.g. anything ending `.dds.rdata` or `.dds.rds`), so
# `deseq2_dds_rdata`'s real path is exactly as run-time-variable as an
# output rule's own `glob` match -- no committed map can spell it literally
# any more than it can spell a per-sample BAM path. A future
# "DESeqFile_Checksum" entry would need a deref form this module does not
# have, e.g. `$checksums.$outputs.deseq2_dds_rdata` (look up
# `named_outputs["deseq2_dds_rdata"]` first, then use THAT as the checksums
# key) -- `resolve_ref` below only ever does one dotted lookup, never a
# lookup-of-a-lookup, so this is a real gap to design around, not one edit
# away.
_RUN_SECTIONS = ("params", "pipeline", "software_versions", "outputs", "checksums")
_SAMPLE_SECTIONS = ("metrics", "derived")
_RUN_SECTION_ATTR = {"outputs": "named_outputs"}


class UnknownPipelineMap(LookupError):
    """No committed map file for this pipeline."""


class OutputRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    glob: str
    sample_type: str
    # A typo here must fail map load loudly, not fall through to one branch
    # silently -- this repo has already been bitten by exactly that class of
    # bug (an unrecognised status value silently read as "complete").
    cardinality: Literal["per_sample", "per_run"] = "per_sample"
    primary_data: bool = False
    secondary_data_glob: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    # Whether this rule's rows also receive the map's `provenance_attributes`
    # (mapper.apply merges that one flat dict into every row of every output
    # rule that opts in). Default False is deliberate: provenance_attributes
    # is shared across the whole map, but sample types differ in which
    # attributes they even have -- e.g. in the rnaseq map, A.GEX carries the
    # pipeline/reference/DESeq provenance set and A.ALN does not have most of
    # those attributes at all. A new output rule must opt IN to inherit that
    # set rather than silently receiving attributes that may not exist on
    # its sample type, which would break the upload at runtime.
    include_provenance: bool = False


class AttributeRule(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_key: str = Field(alias="from")
    target: str
    datatype: str = "string"
    alternates: list[str] = Field(default_factory=list)
    note: str = ""
    provenance: str = ""


class UnmappedRuling(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    why: str


class PipelineMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    map_version: int = 1
    pipeline: str
    applies_to_versions: str = ""
    harvest_globs: list[str] = Field(default_factory=list)
    derived_metrics: list[str] = Field(default_factory=list)
    outputs: list[OutputRule] = Field(default_factory=list)
    # Which sample types this pipeline's samplesheet can legitimately point
    # back to as a PARENT. `nextseek_api.services.reingest_lookups.
    # uids_by_primary_data`, the fastq-path fallback used when a run has no
    # Nessie launch record, used to search only D.SEQ -- so a run whose input
    # was already-analysed data (an A.* sample fed into a downstream
    # pipeline, e.g. hlatyping's `bam` column, or rnavar's `vcf` column)
    # could never be found by path and fell through to RESOLUTION_UNRESOLVED
    # even though its real parent exists and is findable. This field lets a
    # map opt into searching wider, but ONLY among types its own pinned
    # samplesheet schema actually declares as acceptable input -- never an
    # unscoped string search across the whole ~101-type catalog, where a
    # wrong-but-plausible match would be indistinguishable from a right one
    # and would write a fabricated lineage into a database of record. The
    # default, ["D.SEQ"], is deliberately the narrow, pre-existing behavior:
    # an undeclared map (or one nobody has verified against a wider set)
    # must keep searching exactly what it always searched, not silently
    # widen. Must be non-empty -- see test_map_contract.py's
    # test_accepts_parent_types_is_never_empty for why an empty list is
    # illegal rather than a silent no-op.
    accepts_parent_types: list[str] = Field(
        default_factory=lambda: ["D.SEQ"], min_length=1)
    provenance_attributes: dict[str, str] = Field(default_factory=dict)
    qc_attributes: dict[str, AttributeRule] = Field(default_factory=dict)
    deliberately_unmapped: list[UnmappedRuling] = Field(default_factory=list)

    def ruled_out(self) -> set[str]:
        """Keys someone already decided not to map. Never re-proposed."""
        return {entry.key for entry in self.deliberately_unmapped}


def _slug(pipeline: str) -> str:
    return pipeline.split("/")[-1].strip().lower()


def available() -> list[str]:
    return sorted(p.name[: -len(".outputs.json")]
                  for p in MAPS_DIR.glob("*.outputs.json"))


def load(pipeline: str) -> PipelineMap:
    path = MAPS_DIR / f"{_slug(pipeline)}.outputs.json"
    if not path.is_file():
        raise UnknownPipelineMap(
            f"no map for {pipeline!r}; have {', '.join(available()) or 'none'}")
    return PipelineMap.model_validate_json(path.read_text(encoding="utf-8"))


def resolve_ref(ref: str, run_manifest, sample=None):
    """Resolve one map value. A non-`$` value is a literal and passes through.

    Only `$<section>.<key>` and bare `$<section>` resolve, and only for the
    section names above. Anything else returns None rather than reaching into
    an object.
    """
    if not isinstance(ref, str) or not ref.startswith("$"):
        return ref
    body = ref[1:]
    section, _, key = body.partition(".")

    if section in _SAMPLE_SECTIONS:
        if sample is None:
            return None
        bag = getattr(sample, section, None) or {}
        return bag.get(key) if key else bag
    if section in _RUN_SECTIONS:
        attr = _RUN_SECTION_ATTR.get(section, section)
        bag = getattr(run_manifest, attr, None)
        if bag is None:
            return None
        if not key:
            return bag
        if isinstance(bag, dict):
            return bag.get(key)
        if isinstance(bag, BaseModel) and key in type(bag).model_fields:
            return getattr(bag, key, None)
        return None
    return None

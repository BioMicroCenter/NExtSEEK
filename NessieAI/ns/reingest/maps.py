"""Per-pipeline output maps: the only place a raw key becomes a sample attribute.

A map is data, not code. `$`-prefixed values are LOOKUPS into a named manifest
section and nothing else — there is no evaluation, no attribute traversal beyond
one dotted key, and no way to reach a Python object. That is what makes a map
file reviewable in a diff rather than a security surface.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from pydantic import BaseModel, ConfigDict, Field, model_validator

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
# Map values beginning "@" are COMPUTED by `mapper.py`, not looked up: a `$ref`
# is a literal or a single key lookup and cannot transform, but several real
# attributes need exactly that -- a fastq basename with its extension stripped,
# a date pulled out of a longer timestamp, a prefix joined to that date, the
# name of whoever is running the reingest. Those live in code (`mapper._COMPUTED`),
# where they are testable, rather than as string munging inside a map file.
#
# The NAMES live here and the IMPLEMENTATIONS in mapper.py, so a map can be
# validated at load time without importing mapper (which imports this module).
# `test_maps.py` asserts the two sets are identical, so adding one in only one
# place fails loudly instead of resolving to None at runtime.
COMPUTED_VALUES = ("fastq_stem", "run_date", "gex_name", "nextseek_user", "lab",
                   "genome_path", "gtf_path", "file_stem", "contrast",
                   "contrast_variable", "contrast_reference", "contrast_target",
                   "paramset", "method_settings", "report_link", "model_object",
                   "study_type_note", "upstream_parent")

_RUN_SECTIONS = ("params", "pipeline", "software_versions", "outputs", "checksums")
_SAMPLE_SECTIONS = ("metrics", "derived")
_RUN_SECTION_ATTR = {"outputs": "named_outputs"}


class UnknownPipelineMap(LookupError):
    """No committed map file for this pipeline."""


def expand_braces(pattern: str) -> list[str]:
    """Expand every non-nested ``{a,b,c}`` group into every literal variant.

    ``fnmatch`` and ``pathlib`` have no brace syntax. Groups expand left to
    right and cross-multiply; a pattern with no ``{`` passes through.
    """
    if "{" not in pattern:
        return [pattern]
    before, _, rest = pattern.partition("{")
    body, _, after = rest.partition("}")
    return [expanded
            for alt in body.split(",")
            for expanded in expand_braces(before + alt + after)]


_RULE_ID = re.compile(r"[a-z][a-z0-9_]*")


class Companion(BaseModel):
    """A file derived from a rule's primary output: a subset, an index, other
    units, a model object. Never its own row. Placed by mapper in this order:
    ``slot`` if set and the row's type has it, then ``File_SecondaryData``,
    then appended to ``Notes`` (spec G-3)."""
    model_config = ConfigDict(extra="forbid")
    glob: str
    slot: str | None = None


class OutputRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Short name for the rule, unique in its map. Version overrides and fixture
    # snapshots refer to rules by it. v1 maps get rule0, rule1, ... at load.
    id: str = ""
    # Ordered by preference, most-processed first. The first pattern with a
    # match supplies the row's primary file; files matched by a later pattern
    # become companions (spec G-4). v1's single `glob` reads as a one-item list.
    candidates: list[str] = Field(min_length=1)
    sample_type: str
    # A typo here must fail map load loudly, not fall through to one branch
    # silently -- this repo has already been bitten by exactly that class of
    # bug (an unrecognised status value silently read as "complete").
    # per_sample: one row per resolved sample, from that sample's own file.
    # per_run: one row for the run, from the single matching file (the first,
    #   if several match), parented by every resolved sample.
    # per_file: one row per matching file, each parented like a per_run row.
    #   For outputs a run writes once per contrast, comparison or condition,
    #   where per_run would keep the first and silently drop the rest.
    cardinality: Literal["per_sample", "per_run", "per_file"] = "per_sample"
    primary_data: bool = False
    companions: list[Companion] = Field(default_factory=list)
    # A flag-dependent output (--cram, --tools salmon). Matching nothing is
    # expected, so it is not reported as a miss (spec G-10).
    optional: bool = False
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
    # The HOUSE assay this rule's samples belong to, by internal assay title
    # (`internal_assays.internal_assay_title`) -- e.g. "Genome Alignment" for
    # A.ALN, "Gene Expression Analysis" for A.GEX. A NAME, never an id: the
    # integer the ASSAY sheet needs is a study-scoped `assays.id`, and one
    # internal assay maps to many of them (Gene Expression Analysis to eight,
    # Genome Alignment to three). The id is resolved per run from the parent
    # D.SEQ samples' study, so a committed id would attach samples to whatever
    # project happened to be first. None means emit no ASSAY rows for this
    # rule -- the pre-existing behaviour, not an error.
    assay: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _v1_aliases(cls, data):
        """v1 spelled one pattern as `glob` and one companion as
        `secondary_data_glob`. Both still load, folded into the v2 fields."""
        if not isinstance(data, dict):
            return data
        data = dict(data)
        if "glob" in data:
            if "candidates" in data:
                raise ValueError("an output rule takes glob or candidates, not both")
            data["candidates"] = [data.pop("glob")]
        if "secondary_data_glob" in data:
            secondary = data.pop("secondary_data_glob")
            if secondary:
                data["companions"] = [{"glob": secondary}, *data.get("companions", [])]
        return data

    @model_validator(mode="after")
    def per_file_rules(self):
        """A per_file rule counts its rows by the files it matches, so it must
        declare primary data. It takes exactly one candidate pattern: per-stem
        candidate fallback is not supported. Companions pair with each row's
        file by its stem (see mapper._pairs_with_row_file)."""
        if self.cardinality == "per_file":
            if not self.primary_data:
                raise ValueError(f"per_file rule {self.candidates[0]!r} must set primary_data")
            if len(self.candidates) != 1:
                raise ValueError(
                    f"per_file rule {self.candidates[0]!r} takes exactly one candidate pattern")
        return self


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


class VersionOverride(BaseModel):
    """Rules for one release range. An output with an existing id replaces that
    base rule, a new id adds a rule, and `drop` removes base rules by id."""
    model_config = ConfigDict(extra="forbid")
    range: str
    outputs: list[OutputRule] = Field(default_factory=list)
    drop: list[str] = Field(default_factory=list)


class PipelineMap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    map_version: int = 1
    pipeline: str
    applies_to_versions: str = ""
    harvest_globs: list[str] = Field(default_factory=list)
    derived_metrics: list[str] = Field(default_factory=list)
    outputs: list[OutputRule] = Field(default_factory=list)
    # This map's own inventory patterns (listed by name and size, never copied).
    # The stager lists the shared core plus every map's list (spec §4.1).
    inventory_globs: list[str] = Field(default_factory=list)
    # Merged into every output rule's attributes before the rule's own; an
    # attribute the row's type lacks is dropped at map time (spec G-9).
    row_defaults: dict[str, str] = Field(default_factory=dict)
    versions: list[VersionOverride] = Field(default_factory=list)
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

    @model_validator(mode="after")
    def computed_names_must_exist(self):
        """An unknown "@name" fails map load rather than resolving to None.

        Same reasoning as `OutputRule.cardinality` being a Literal: a typo in a
        committed map must be loud at load, not a silently blank column a
        curator would have to notice was missing.
        """
        bad = []
        for rule in self.outputs:
            for attr, value in rule.attributes.items():
                if (isinstance(value, str) and value.startswith("@")
                        and value[1:] not in COMPUTED_VALUES):
                    bad.append(f"{rule.sample_type}::{attr} = {value!r}")
        for attr, value in self.provenance_attributes.items():
            if (isinstance(value, str) and value.startswith("@")
                    and value[1:] not in COMPUTED_VALUES):
                bad.append(f"provenance_attributes::{attr} = {value!r}")
        if bad:
            raise ValueError(
                "unknown computed value(s); known: "
                f"{', '.join('@' + n for n in COMPUTED_VALUES)} -- got "
                + "; ".join(bad))
        return self

    @model_validator(mode="after")
    def rule_ids_and_ranges(self):
        if self.map_version >= 2:
            missing = [r.candidates[0] for r in self.outputs if not r.id]
            if missing:
                raise ValueError(f"every v2 output rule needs an id; missing on {missing}")
        else:
            for index, rule in enumerate(self.outputs):
                if not rule.id:
                    rule.id = f"rule{index}"
        ids = [r.id for r in self.outputs]
        bad = [i for i in ids if not _RULE_ID.fullmatch(i)]
        if bad:
            raise ValueError(f"rule id must be lowercase word characters: {bad}")
        dupes = sorted({i for i in ids if ids.count(i) > 1})
        if dupes:
            raise ValueError(f"duplicate rule id(s): {dupes}")
        for spec in [self.applies_to_versions] if self.applies_to_versions else []:
            _specifier(spec)
        known = set(ids)
        for override in self.versions:
            _specifier(override.range)
            unknown = [d for d in override.drop if d not in known]
            if unknown:
                raise ValueError(f"override {override.range!r} drops unknown rule(s) {unknown}")
            replaced = {r.id for r in override.outputs}
            if not all(replaced):
                raise ValueError(f"override {override.range!r}: every output needs an id")
            both = sorted(replaced & set(override.drop))
            if both:
                raise ValueError(
                    f"override {override.range!r} both drops and replaces {both}")
            # Validate override output ids match the rule id pattern
            bad_ids = [r.id for r in override.outputs if not _RULE_ID.fullmatch(r.id)]
            if bad_ids:
                raise ValueError(f"rule id must be lowercase word characters: {bad_ids}")
            # Check for duplicate ids within the override
            override_ids = [r.id for r in override.outputs]
            override_dupes = sorted({i for i in override_ids if override_ids.count(i) > 1})
            if override_dupes:
                raise ValueError(f"duplicate rule id(s) in override {override.range!r}: {override_dupes}")
        return self


def _slug(pipeline: str) -> str:
    return pipeline.split("/")[-1].strip().lower()


def _specifier(text: str) -> SpecifierSet:
    try:
        return SpecifierSet(text)
    except InvalidSpecifier as exc:
        raise ValueError(f"not a version range: {text!r}") from exc


def available() -> list[str]:
    return sorted(p.name[: -len(".outputs.json")]
                  for p in MAPS_DIR.glob("*.outputs.json"))


def load(pipeline: str) -> PipelineMap:
    path = MAPS_DIR / f"{_slug(pipeline)}.outputs.json"
    if not path.is_file():
        raise UnknownPipelineMap(
            f"no map for {pipeline!r}; have {', '.join(available()) or 'none'}")
    return PipelineMap.model_validate_json(path.read_text(encoding="utf-8"))


def effective(pipeline_map: PipelineMap, version: str) -> tuple[PipelineMap, list[str]]:
    """The rules to apply to a run of ``version``, and a warning if it is unchecked.

    Base rules, then the first ``versions`` override whose range contains the
    version: an override output replaces the base rule with its id, a new id is
    added, ``drop`` removes. A version outside ``applies_to_versions`` that no
    override covers, or no usable version at all, still applies the base rules,
    with one warning (spec G-2). Never raises on the version itself.
    """
    from packaging.version import InvalidVersion, Version

    text = str(version or "").strip().lstrip("vV")
    try:
        parsed = Version(text) if text else None
    except InvalidVersion:
        parsed = None
    name = pipeline_map.pipeline
    if parsed is None:
        return pipeline_map, [
            f"{name}: no usable pipeline version ({version!r}); rules applied unchecked"]

    def contains(spec: str) -> bool:
        return _specifier(spec).contains(parsed, prereleases=True)

    override = next((o for o in pipeline_map.versions if contains(o.range)), None)
    checked = pipeline_map.applies_to_versions
    warnings: list[str] = []
    if override is None and not (checked and contains(checked)):
        warnings.append(f"{name} {text} is outside the checked range "
                        f"{checked or '(none)'}; rules applied unchecked")
    if override is None:
        return pipeline_map, warnings
    replacing = {rule.id: rule for rule in override.outputs}
    outputs = []
    for rule in pipeline_map.outputs:
        if rule.id in override.drop:
            continue
        outputs.append(replacing.pop(rule.id, rule))
    outputs.extend(replacing.values())
    return pipeline_map.model_copy(update={"outputs": outputs}), warnings


def all_harvest_globs() -> tuple[str, ...]:
    """Every committed map's ``harvest_globs``, deduplicated, in a stable order.

    The UNION across pipelines, not one map's list, because staging happens
    before the pipeline is known: `run-harvest` copies files off the cluster and
    only then reads `software_versions.yml` to find out what produced them.
    Selecting a single map's list would need either a second SSH round trip or a
    caller-supplied pipeline name that can be wrong.

    The union is naturally scoped anyway -- these patterns name pipeline-specific
    directories (`{star_salmon,star_rsem,hisat2}/log/*.Log.final.out`), so a run
    from another pipeline simply matches none of them. And the remote stager
    deduplicates matches by relative path, so a file two patterns both match is
    staged once and counted against the caps once.

    A map that fails to load is skipped rather than fatal: one broken map must
    not stop every other pipeline's files being staged.
    """
    seen: dict[str, None] = {}
    for pipeline in available():
        try:
            for pattern in load(pipeline).harvest_globs:
                seen.setdefault(pattern, None)
        except Exception:
            continue
    return tuple(seen)


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
        # `$outputs.<key>` is the one run section with a per-SAMPLE half. When a
        # sample is in scope and it has its own copy of this named output, that
        # copy wins: a per_sample row must never be handed another sample's file,
        # which is what a purely run-scoped `named_outputs` did for per-sample
        # outputs like <sample>.kraken2.report.txt. Falls through to the
        # run-level dict when the sample has no copy (an ordinary run-level
        # output such as multiqc_report.html), so nothing else changes.
        if section == "outputs" and sample is not None and key:
            own = (getattr(run_manifest, "named_outputs_by_sample", None) or {}).get(
                getattr(sample, "nfcore_sample", "") or "", {})
            if key in own:
                return own[key]
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

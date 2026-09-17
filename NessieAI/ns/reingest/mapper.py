"""Apply a pipeline map to a manifest, and name everything it could not map.

Resolution order is committed map rule, then approved database rule, then
unmapped. A committed, code-reviewed map rule is the stronger authority: if it
already produced a value for an attribute, an approved rule must not overwrite
it — it only fills gaps the committed rule left. A PENDING proposal is never
applied here at all: it is evidence shown to the agent, which must re-affirm
it, so an unreviewed rule cannot quietly become permanent by repetition.
"""
from __future__ import annotations

import fnmatch
import os

from pydantic import BaseModel, ConfigDict, Field

from NessieAI.ns.reingest import manifest, maps

ORIGIN_MAP = "map"
ORIGIN_APPROVED = "approved"
ORIGIN_PROPOSED = "proposed"
ORIGIN_PARKED = "parked"

# A sample whose UID resolved this way cannot carry a QC backfill row.
_NO_BACKFILL = {
    manifest.RESOLUTION_MULTIRUN,
    manifest.RESOLUTION_AMBIGUOUS,
    manifest.RESOLUTION_UNRESOLVED,
}

# Per the UID-resolution table in
# docs/superpowers/specs/2026-09-15-nfcore-reingest-design.md (Section 11,
# "UID resolution"), an ambiguous or multi-run sample gets no per_sample /
# per_run child row at all: ambiguous because guessing between candidate
# parents would write a wrong lineage, multi-run because uid_resolve.resolve()
# discards the sample's contributing UIDs and SampleRecord cannot carry a
# list, so no honest `Parent` exists at this layer. An unresolved sample is
# NOT in this set -- the spec's table is explicit that its child still ships,
# just with no `Parent` to name.
_NO_CHILD_ROW = {
    manifest.RESOLUTION_MULTIRUN,
    manifest.RESOLUTION_AMBIGUOUS,
}

# Resolutions for which the spec's table says a real D.SEQ parent exists, so
# a child row may carry `Parent`. Checked by name (not by `d_seq_uid`
# truthiness) so a future bug in uid_resolve.py that set `d_seq_uid` on an
# ambiguous or unresolved sample could not leak into a fabricated `Parent`
# here.
_HAS_PARENT = {
    manifest.RESOLUTION_LAUNCH_RECORD,
    manifest.RESOLUTION_FASTQ_EXACT,
    manifest.RESOLUTION_FASTQ_BASENAME,
}


class MappedAttribute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attribute: str
    value: object
    origin: str
    raw_key: str = ""
    source_file: str = ""
    # Populated only for a File_PrimaryData attribute whose winning candidate
    # was NOT singled out by a checksum -- i.e. more than one inventoried
    # output matched the rule's own glob and either none or more than one of
    # them is checksummed, so `sorted()` (see `_primary_output`) made the
    # call rather than measured evidence. Holds every candidate's harvested,
    # run-relative path (the chosen one included) so a reader can see the
    # directory each shared a basename with -- `value` itself is a bare
    # basename and cannot show that. Empty whenever there was only one
    # candidate, or a checksum uniquely picked the winner: an ordinary,
    # non-ambiguous pick is not something to flag.
    candidates: list[str] = Field(default_factory=list)


class MappedRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_type: str
    uid: str | None = None
    nfcore_sample: str = ""
    attributes: dict[str, MappedAttribute] = Field(default_factory=dict)


class MapResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[MappedRow] = Field(default_factory=list)
    unmapped: list[dict] = Field(default_factory=list)


def apply(run_manifest: manifest.RunManifest, pipeline_map: maps.PipelineMap,
          approved_rules: dict[str, maps.AttributeRule] | None = None) -> MapResult:
    approved_rules = approved_rules or {}
    result = MapResult()
    ruled_out = pipeline_map.ruled_out()
    metrics_source = run_manifest.sources.get("metrics", "")

    # Every raw key either kind of rule claims -- whether or not it ends up
    # applied to a given sample -- is known evidence, not an unmapped key.
    all_rules = list(pipeline_map.qc_attributes.values()) + list(approved_rules.values())
    claimed = {rule.from_key for rule in all_rules}
    claimed |= {alt for rule in all_rules for alt in rule.alternates}

    for sample in run_manifest.samples:
        if sample.uid_resolution not in _NO_BACKFILL and sample.d_seq_uid:
            row = MappedRow(sample_type="D.SEQ", uid=sample.d_seq_uid,
                            nfcore_sample=sample.nfcore_sample)

            # Committed map rules go first and claim their attributes outright.
            for attribute, rule in pipeline_map.qc_attributes.items():
                found = _lookup(rule, run_manifest, sample)
                if found is None:
                    continue
                value, raw_key = found
                row.attributes[attribute] = MappedAttribute(
                    attribute=attribute, value=value, origin=ORIGIN_MAP,
                    raw_key=raw_key, source_file=metrics_source)

            # Approved rules only fill attributes the committed map left empty.
            for attribute, rule in approved_rules.items():
                if attribute in row.attributes:
                    continue
                found = _lookup(rule, run_manifest, sample)
                if found is None:
                    continue
                value, raw_key = found
                row.attributes[attribute] = MappedAttribute(
                    attribute=attribute, value=value, origin=ORIGIN_APPROVED,
                    raw_key=raw_key, source_file=metrics_source)

            if row.attributes:
                result.rows.append(row)

        for raw_key, value in sample.metrics.items():
            if raw_key in claimed or raw_key in ruled_out:
                continue
            if any(u["raw_key"] == raw_key for u in result.unmapped):
                continue
            result.unmapped.append({
                "raw_key": raw_key, "example_value": value,
                "source_file": metrics_source,
                "example_sample": sample.nfcore_sample,
            })

    for rule in pipeline_map.outputs:
        # The output rule's own attributes are the more specific authority:
        # they must win over a provenance attribute of the same name (e.g.
        # A.ALN's "Software" is the STAR version string; provenance's
        # "Software" is the whole software_versions dict).
        merged_attrs = {**pipeline_map.provenance_attributes, **rule.attributes}

        if rule.cardinality == "per_sample":
            for row in _per_sample_rows(rule, merged_attrs, run_manifest):
                result.rows.append(row)
        else:
            row = _per_run_row(rule, merged_attrs, run_manifest)
            if row is not None:
                result.rows.append(row)

    return result


def _expand_braces(pattern: str) -> list[str]:
    """Expand every non-nested ``{a,b,c}`` alternation in an output rule's
    ``glob`` into every literal variant -- ``fnmatch`` itself has no brace
    syntax. Every committed output rule's ``glob`` today (see
    ``NessieAI/ns/reingest_maps/rnaseq.outputs.json``) uses at most one,
    unnested group, e.g. ``"{star_salmon,star_rsem,hisat2}/*.bam"``, but the
    SAME map file's ``harvest_globs`` already uses two in one pattern
    (``"{star_salmon,star_rsem,hisat2}/samtools_stats/*.{flagstat,idxstats,stats}"``),
    so two groups are an established habit in this map format, not a
    hypothetical. Handling only the first group silently mismatches a
    second: the residual ``{p,q}`` would reach ``fnmatch`` literally, match
    nothing, and drop a checksum with no error or warning. This recurses
    left-to-right instead, expanding one group per call and recursing on
    the (still possibly braced) remainder, so any number of non-nested
    groups fully cross-multiplies; a pattern with no ``{`` passes through
    unchanged. A pattern with an unbalanced/nested ``{`` (no committed map
    has one) falls through to ``partition``'s empty-string behaviour on a
    missing ``}``, which is caught by whatever loads the map, not here.
    """
    if "{" not in pattern:
        return [pattern]
    before, _, rest = pattern.partition("{")
    body, _, after = rest.partition("}")
    return [expanded
            for alt in body.split(",")
            for expanded in _expand_braces(before + alt + after)]


def _primary_candidates(rule: maps.OutputRule, run_manifest: manifest.RunManifest,
                        sample_name: str | None) -> list[manifest.OutputRecord]:
    """Every inventoried output this rule's own ``glob`` matches, sorted by
    path -- the shared candidate-gathering step behind both ``_primary_output``
    (which picks one) and ``_attach_checksum`` (which also needs to know
    whether more than one candidate was in play, to decide whether the pick
    was a real tie-break worth flagging). Factored out rather than
    duplicated so the two can never drift on what counts as a candidate.

    ``sample_name`` filters to one sample's own output (a per_sample rule) by
    ``OutputRecord.sample`` -- attributed once, at harvest time, by
    ``harvest.py``'s ``_sample_for_output_path``, never re-derived here;
    ``None`` skips that filter for a per_run rule's single, run-wide file.
    Empty whenever the rule declares no primary data, or nothing in the
    inventory matches yet -- never an error.
    """
    if not rule.primary_data:
        return []
    patterns = _expand_braces(rule.glob)
    return sorted(
        (o for o in run_manifest.outputs
         if (sample_name is None or o.sample == sample_name)
         and any(fnmatch.fnmatch(o.path, pat) for pat in patterns)),
        key=lambda o: o.path)


def _primary_output(rule: maps.OutputRule, run_manifest: manifest.RunManifest,
                    sample_name: str | None) -> manifest.OutputRecord | None:
    """The one candidate from ``_primary_candidates`` to treat as this rule's
    primary file, so ``Checksum_PrimaryData`` can be looked up by the SAME
    path ``run-checksum`` hashed -- reusing ``rule.glob``/``rule.primary_data``
    rather than a static ``$``-ref, since the harvested path is a run-time
    value no committed map can name in advance (see mapper.py's module
    docstring on why a map rule cannot express this directly).

    Ties (more than one candidate, e.g. two aligners both present) prefer
    whichever candidate ``run_manifest.checksums`` already has a digest for
    -- run-checksum only ever hashes what the agent actually pointed at (see
    its own docstring), so when the agent hashed one of several equally
    valid candidates (e.g. nf-core/rnaseq with both ``--aligner star_salmon``
    and ``--pseudo_aligner salmon`` publishing the same-named gene-counts
    matrix under two directories), the one it paid to SSH-hash is the one
    this must return -- picking sorted-first regardless would silently
    discard a real, already-computed checksum whenever the alphabetically
    first candidate happens not to be it. Among candidates that are equally
    checksummed (including "none of them"), sorted-first is still the
    deterministic tie-break ``harvest.py``'s own ``_resolve_named_outputs``
    uses -- not a claim that it is the "right" one. Returns ``None`` on an
    ordinary miss (see ``_primary_candidates``) -- never an error.
    """
    candidates = _primary_candidates(rule, run_manifest, sample_name)
    if not candidates:
        return None
    checksummed = [o for o in candidates if o.path in run_manifest.checksums]
    return checksummed[0] if checksummed else candidates[0]


def _attach_checksum(row: MappedRow, rule: maps.OutputRule,
                     run_manifest: manifest.RunManifest, sample_name: str | None) -> None:
    """Set ``File_PrimaryData`` (always, once a primary output is found) and
    ``Checksum_PrimaryData`` (only once that file has actually been hashed)
    on ``row``, both keyed by this rule's own matched output path -- sharing
    ``_primary_candidates``' tie-break logic with ``_primary_output`` (rather
    than calling it) so this can also see the FULL candidate set, needed to
    tell an ordinary pick from an ambiguous one; both ways of picking always
    name the SAME file, never two different candidates.

    Without ``File_PrimaryData`` (or ``Link_PrimaryData``, which reingest
    never produces), A.ALN/A.GEX fail the catalog's own
    ``ALTERNATIVE_REQUIRED_GROUPS`` gate (``NessieAI/ns/reingest_qa.py``) on
    every populated catalog, and ``granular.py`` skips
    ``render_upload_workbook`` on a HARD_REJECT -- so no workbook, checksum
    included, was ever reachable until this filled it. Checksumming stays
    advisory: a manifest with no checksums at all still gets
    ``File_PrimaryData`` (from the inventory ``run-harvest`` already
    produced) but no ``Checksum_PrimaryData`` cell, same as before this
    method existed.

    ``File_PrimaryData`` is set from ``os.path.basename(primary.path)``, not
    the harvested run-relative path (with its pipeline-internal directory,
    e.g. ``star_salmon/``) and not a cluster-absolute path built from
    ``RunManifest.run_dir``. Checked against every real
    ``File_PrimaryData`` value in the committed seed
    (``startup/seed/seek_production.sql.gz``, table ``samples``,
    ``json_metadata``): of ~23k values, zero are absolute filesystem paths
    and the overwhelming majority (~90%) are bare filenames with no path
    separator at all -- the rest are external identifiers (``s3://...``,
    ``http://...``), never a local path. This is also why
    ``uid_resolve.py``'s fastq matching tries an exact full-path match
    first and only THEN falls back to a basename match: the exact tier
    exists for the rare case a full path was stored, and the basename tier
    is what actually resolves the common case where only a filename was
    written down. Writing a directory-qualified path here would be the one
    genuinely novel convention in the table.

    Precedence: a no-op (row unchanged, for each attribute independently)
    when the rule has no primary file, nothing in the inventory matches
    yet, or the rule's own ``attributes`` (a committed map ``$``-ref)
    already named that attribute explicitly -- the committed map rule wins
    outright, same precedence as everywhere else in this module. No
    committed map currently sets ``File_PrimaryData`` this way, but the
    guard costs nothing and keeps the rule uniform with
    ``Checksum_PrimaryData``'s own.

    ``File_PrimaryData``'s ``raw_key`` is deliberately left blank rather than
    a ``$outputs.<path>``-shaped string: ``$outputs`` only ever resolves
    against ``RunManifest.named_outputs`` BY KEY (``maps.resolve_ref``), never
    by path, so a string built from ``primary.path`` looks like a resolvable
    reference (its sibling ``Checksum_PrimaryData``'s ``$checksums.<path>``
    raw_key genuinely does resolve, by path, which is exactly what makes the
    difference easy to miss) but silently resolves to ``None`` if anyone ever
    pasted it into a map file. ``source_file`` already carries the same path
    for the Provenance sheet, so nothing is lost by leaving ``raw_key`` empty.

    When more than one inventoried output matched the rule's glob and no
    single checksum picked a clear winner (see ``_primary_output``'s
    docstring), the pick is a real, silent judgement call -- so every
    candidate's path (winner included) is recorded on the attribute's own
    ``candidates`` list. That reaches the run's QA reply
    (``NessieAI/ns/granular.py`` folds it into ``ambiguous_primary``, and
    ``report.py`` renders it) so a same-basename ambiguity a curator could
    never see on the Samples sheet becomes reviewable instead of silent. A
    single candidate, or one a checksum genuinely singled out, is an
    ordinary pick and leaves ``candidates`` empty.
    """
    candidates = _primary_candidates(rule, run_manifest, sample_name)
    if not candidates:
        return
    checksummed = [o for o in candidates if o.path in run_manifest.checksums]
    primary = checksummed[0] if checksummed else candidates[0]
    if "File_PrimaryData" not in row.attributes:
        ambiguous = len(candidates) > 1 and len(checksummed) != 1
        row.attributes["File_PrimaryData"] = MappedAttribute(
            attribute="File_PrimaryData", value=os.path.basename(primary.path),
            origin=ORIGIN_MAP, raw_key="", source_file=primary.path,
            candidates=[c.path for c in candidates] if ambiguous else [])
    if "Checksum_PrimaryData" in row.attributes:
        return
    checksum = run_manifest.checksums.get(primary.path)
    if not checksum:
        return
    row.attributes["Checksum_PrimaryData"] = MappedAttribute(
        attribute="Checksum_PrimaryData", value=checksum, origin=ORIGIN_MAP,
        raw_key=f"$checksums.{primary.path}", source_file=primary.path)


def _per_sample_rows(rule: maps.OutputRule, merged_attrs: dict[str, str],
                     run_manifest: manifest.RunManifest) -> list[MappedRow]:
    """One row per sample this rule's child ships for; the analysis record
    does not exist yet, so `uid` stays None and this row is what creates it.

    Policy follows the UID-resolution table in
    docs/superpowers/specs/2026-09-15-nfcore-reingest-design.md (Section 11),
    per resolution:

    - `_HAS_PARENT` (launch record / fastq exact / fastq basename): row
      ships, `Parent` set to the resolved `d_seq_uid`, and this row joins the
      per_run `Parent` (see `_per_run_row`).
    - `unresolved`: row still SHIPS -- the spec is explicit that the hard
      reject on this resolution applies "on the backfill only" -- but with
      no `Parent` key at all. There is no parent to name, and fabricating
      one (or emitting `Parent: ""`) would assert a lineage that does not
      exist; the child instead preserves the output file and makes the
      missing lineage visible for a curator to attach later.
    - `ambiguous`: no row. The spec calls this a hard reject -- never guess
      between candidate parents.
    - `multirun`: no row, matching today's behaviour. The spec wants this
      child to carry a `;`-joined `Parent` across every contributing D.SEQ,
      but `uid_resolve.resolve()` discards those source UIDs and
      `SampleRecord` cannot carry a list, so it is not implementable at this
      layer. Tracked separately.
    """
    rows: list[MappedRow] = []
    for sample in run_manifest.samples:
        if sample.uid_resolution in _NO_CHILD_ROW:
            continue
        row = MappedRow(sample_type=rule.sample_type, nfcore_sample=sample.nfcore_sample)
        for attribute, ref in merged_attrs.items():
            value = maps.resolve_ref(ref, run_manifest, sample)
            if value is None or value == "":
                continue
            row.attributes[attribute] = MappedAttribute(
                attribute=attribute, value=value, origin=ORIGIN_MAP,
                raw_key=ref if isinstance(ref, str) and ref.startswith("$") else "",
                source_file=run_manifest.sources.get("params", ""))
        _attach_checksum(row, rule, run_manifest, sample.nfcore_sample)
        # Parent is a structural lineage field, not a mapped attribute -- set
        # it last so no rule attribute can accidentally clobber it. Only a
        # resolution in `_HAS_PARENT` may set it; an unresolved sample ships
        # its row with no `Parent` key.
        if sample.uid_resolution in _HAS_PARENT and sample.d_seq_uid:
            row.attributes["Parent"] = MappedAttribute(
                attribute="Parent", value=sample.d_seq_uid, origin=ORIGIN_MAP)
        rows.append(row)
    return rows


def _per_run_row(rule: maps.OutputRule, merged_attrs: dict[str, str],
                 run_manifest: manifest.RunManifest) -> MappedRow | None:
    """One row for the whole run, with `Parent` `;`-joined across the run's
    resolved `d_seq_uid`s.

    Join order follows the manifest's own sample order (the samplesheet
    order the harvester recorded), which is deterministic and requires no
    extra sort key. Duplicates are dropped by first occurrence. Only a
    sample whose resolution is in `_HAS_PARENT` contributes its `d_seq_uid`
    to the join -- checked by resolution, not by `d_seq_uid` truthiness, so
    an unresolved, ambiguous, or multi-run sample (see `_per_sample_rows`)
    can never leak into this join even if a future change set a UID on one
    of them. If no sample resolved at all, `Parent` is omitted entirely
    rather than set to an empty string.
    """
    row = MappedRow(sample_type=rule.sample_type)
    for attribute, ref in merged_attrs.items():
        value = maps.resolve_ref(ref, run_manifest)
        if value is None or value == "":
            continue
        row.attributes[attribute] = MappedAttribute(
            attribute=attribute, value=value, origin=ORIGIN_MAP,
            raw_key=ref if isinstance(ref, str) and ref.startswith("$") else "",
            source_file=run_manifest.sources.get("params", ""))
    _attach_checksum(row, rule, run_manifest, None)

    seen: set[str] = set()
    parents: list[str] = []
    for sample in run_manifest.samples:
        # `and sample.d_seq_uid` is not redundant with the resolution check:
        # the pairing of a _HAS_PARENT resolution with a non-None uid is an
        # invariant of uid_resolve.resolve(), enforced in another module. Were
        # it ever broken, a None here would reach ";".join() and raise. Cheap
        # to keep the guard local.
        if (sample.uid_resolution in _HAS_PARENT and sample.d_seq_uid
                and sample.d_seq_uid not in seen):
            seen.add(sample.d_seq_uid)
            parents.append(sample.d_seq_uid)
    if parents:
        row.attributes["Parent"] = MappedAttribute(
            attribute="Parent", value=";".join(parents), origin=ORIGIN_MAP)

    return row if row.attributes else None


def _lookup(rule: maps.AttributeRule, run_manifest: manifest.RunManifest,
            sample: manifest.SampleRecord) -> tuple[object, str] | None:
    """(value, raw_key) from the rule's primary key, then each alternate."""
    for key in [rule.from_key, *rule.alternates]:
        if key.startswith("$"):
            value = maps.resolve_ref(key, run_manifest, sample)
        else:
            value = sample.metrics.get(key)
        if value is not None and value != "":
            return value, key
    return None

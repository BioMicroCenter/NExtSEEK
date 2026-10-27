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
import posixpath

from pydantic import BaseModel, ConfigDict, Field

from NessieAI.ns.reingest import manifest, maps, pipelines
from NessieAI.ns.reingest.pipelines import common

ORIGIN_MAP = "map"
ORIGIN_APPROVED = "approved"
ORIGIN_PROPOSED = "proposed"
ORIGIN_PARKED = "parked"
# A value a curator supplied in chat (a fill), a file they picked among
# ambiguous candidates (a choose), or an uncovered key they placed for this
# run only (a place). See NessieAI/ns/reingest/answers.py.
ORIGIN_CURATOR = "curator"

# A sample whose UID resolved this way cannot carry a QC backfill row.
_NO_BACKFILL = {
    manifest.RESOLUTION_MULTIRUN,
    manifest.RESOLUTION_AMBIGUOUS,
    manifest.RESOLUTION_UNRESOLVED,
}

# Per the UID-resolution table in
# docs/superpowers/specs/2026-09-15-nfcore-reingest-design.md (Section 11,
# "UID resolution"), an ambiguous sample gets no per_sample / per_run child
# row at all: guessing between candidate parents would write a wrong
# lineage. Neither unresolved NOR multi-run is in this set -- the spec's
# table is explicit that both still ship a child, unresolved with no
# `Parent` to name, multi-run with `Parent` `;`-joined across whichever of
# its own contributing rows resolved (see `_per_sample_rows` and
# `SampleRecord.d_seq_uid_multirun`).
_NO_CHILD_ROW = {
    manifest.RESOLUTION_AMBIGUOUS,
}

# Resolutions for which the spec's table says a real, SINGLE D.SEQ parent
# exists in `sample.d_seq_uid`, so a child row may carry `Parent` from it
# directly. Checked by name (not by `d_seq_uid` truthiness) so a future bug
# in uid_resolve.py that set `d_seq_uid` on an ambiguous or unresolved
# sample could not leak into a fabricated `Parent` here.
#
# Multi-run is deliberately NOT in this set: it never has a single parent --
# `sample.d_seq_uid` stays None for it by construction (see manifest.py) --
# its (possibly several, possibly partial) parents live in
# `sample.d_seq_uid_multirun` instead, and are joined separately wherever
# this set is checked (`_per_sample_rows`, `_per_run_row`).
_HAS_PARENT = {
    manifest.RESOLUTION_LAUNCH_RECORD,
    manifest.RESOLUTION_FASTQ_EXACT,
    manifest.RESOLUTION_FASTQ_BASENAME,
}


def _join_uids(uids: list[str]) -> str | None:
    """`;`-join `uids`, first-occurrence de-duplicated, in the order given;
    `None` when there is nothing to join so a caller omits the `Parent` key
    entirely rather than writing an empty string. The one join rule shared by
    a multi-run sample's own per_sample `Parent` (`_per_sample_rows`) and the
    run-wide per_run `Parent` (`_per_run_row`), so the two can never drift.
    """
    seen: set[str] = set()
    ordered: list[str] = []
    for uid in uids:
        if uid and uid not in seen:
            seen.add(uid)
            ordered.append(uid)
    return ";".join(ordered) if ordered else None


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
    # Who answered, and when, for an ORIGIN_CURATOR cell; empty otherwise.
    # Rendered as the Provenance sheet's "Answered by" column.
    answered_by: str = ""


class MappedRow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_type: str
    uid: str | None = None
    nfcore_sample: str = ""
    # The output rule that made this row; "" for a QC backfill row.
    rule_id: str = ""
    attributes: dict[str, MappedAttribute] = Field(default_factory=dict)


class MapResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rows: list[MappedRow] = Field(default_factory=list)
    unmapped: list[dict] = Field(default_factory=list)
    # Real, visible outcomes that are not a row and not an unmapped key:
    # today, only the QC-backfill cases in `apply`'s main loop below --
    # a parent whose real type could not be determined (no backfill row
    # written for it), and a parent whose real type IS known but some of the
    # map's qc_attributes rules target a different type (those measured
    # attributes have no home on this row). Never silently dropped; see the
    # loop below for both cases.
    warnings: list[str] = Field(default_factory=list)
    # {rule_id, sample_type} for each non-optional primary-data rule that
    # matched no file. Also in `warnings`; this copy lets the report say why
    # the primary file is blank instead of asking for it (a data file is
    # never set from chat).
    missed_rules: list[dict] = Field(default_factory=list)


_METRICS_REF = "$metrics."


def claimed_raw_keys(pipeline_map: maps.PipelineMap | None,
                     approved_rules: dict[str, maps.AttributeRule] | None = None) -> set[str]:
    """Raw general-stats keys some rule of ``pipeline_map`` reads: each QC
    rule's ``from`` and alternates, and every ``$metrics.<key>`` an output
    rule, version override, row default or provenance value names (rnavar's
    VCF row reads snpEff's Ts/Tv this way). Harvest folds per-read columns
    only for these keys, and apply never queues them."""
    if pipeline_map is None:
        return set()
    rules = list(pipeline_map.qc_attributes.values()) + list((approved_rules or {}).values())
    keys = {rule.from_key for rule in rules} | {alt for rule in rules for alt in rule.alternates}
    outputs = list(pipeline_map.outputs) + [r for o in pipeline_map.versions for r in o.outputs]
    values = [v for rule in outputs for v in rule.attributes.values()]
    values += list(pipeline_map.row_defaults.values()) + list(pipeline_map.provenance_attributes.values())
    keys |= {v[len(_METRICS_REF):] for v in values
             if isinstance(v, str) and v.startswith(_METRICS_REF)}
    return keys


def apply(run_manifest: manifest.RunManifest, pipeline_map: maps.PipelineMap,
          approved_rules: dict[str, maps.AttributeRule] | None = None,
          context: dict | None = None) -> MapResult:
    """``context`` carries what the manifest cannot: values about the SESSION
    rather than the run (``user_full_name`` for ``@nextseek_user``, the
    injected catalog lookups). Absent means the computed values that need it
    resolve to None, leaving those cells blank -- never a placeholder.

    ``context["has_attribute"]``, an optional ``(sample_type, attribute) ->
    bool``, says whether a type has an attribute: a row default or companion
    slot the type lacks is dropped or falls through (spec G-3, G-9). Absent
    means every attribute is assumed to exist.

    The run's pipeline version picks the map's rules (``maps.effective``), and
    "@" tokens resolve by the MAP's pipeline, so a manifest that could not
    name its pipeline still gets that pipeline's own tokens."""
    approved_rules = approved_rules or {}
    context = _with_build_local_ordinals(context or {})
    context = {**context, "pipeline": pipeline_map.pipeline}
    result = MapResult()
    pipeline_map, version_warnings = maps.effective(pipeline_map, run_manifest.pipeline.version)
    result.warnings.extend(version_warnings)
    run_manifest = _without_ruled_out_files(run_manifest, pipeline_map)
    ruled_out = pipeline_map.ruled_out()
    metrics_source = run_manifest.sources.get("metrics", "")

    # Every raw key either kind of rule claims -- whether or not it ends up
    # applied to a given sample -- is known evidence, not an unmapped key.
    claimed = claimed_raw_keys(pipeline_map, approved_rules)

    for sample in run_manifest.samples:
        if sample.uid_resolution not in _NO_BACKFILL and sample.d_seq_uid:
            parent_type = sample.parent_sample_type
            if not parent_type:
                # The lookup that ran at harvest time (see harvest.py's
                # `sample_type_lookup`) either was not reachable at all, or
                # could not resolve this particular UID. Before parents
                # could legitimately be an already-analysed A.* sample, this
                # branch never existed -- a resolved d_seq_uid was always
                # D.SEQ. Now that assumption is not safe: writing a
                # D.SEQ-shaped row on a guess is exactly the failure mode
                # this change exists to stop (a real A.ALN row has no
                # MappedPercent, and asserting one would invent a sample
                # attribute, or worse, silently corrupt the wrong sample's
                # metadata). So: no backfill row, and say so -- silently
                # doing nothing here would look identical to "this sample's
                # metrics genuinely had nothing to map", which is not what
                # happened.
                result.warnings.append(
                    f"{sample.nfcore_sample}: parent {sample.d_seq_uid}'s "
                    "sample type could not be determined; no QC backfill "
                    "row was written for it rather than guess one")
            else:
                row = MappedRow(sample_type=parent_type, uid=sample.d_seq_uid,
                                nfcore_sample=sample.nfcore_sample)
                unmatched = 0

                # Committed map rules go first and claim their attributes
                # outright -- but only the ones whose `target` is this
                # sample's ACTUAL parent type. A rule targeting some other
                # type is still looked up (not skipped outright): if it
                # finds a real measured value, that value has no home on
                # this row, and is counted rather than silently discarded --
                # see the warning below. A map whose rules all target D.SEQ
                # therefore contributes nothing to an A.ALN parent, which is
                # correct: A.ALN has no MappedPercent, and writing one would
                # break reingest's invariant that it never invents a sample
                # attribute.
                for attribute, rule in pipeline_map.qc_attributes.items():
                    found = _lookup(rule, run_manifest, sample)
                    if found is None:
                        continue
                    if rule.target != parent_type:
                        unmatched += 1
                        continue
                    value, raw_key = found
                    row.attributes[attribute] = MappedAttribute(
                        attribute=attribute, value=value, origin=ORIGIN_MAP,
                        raw_key=raw_key, source_file=metrics_source)

                # Approved rules only fill attributes the committed map left
                # empty, same target-matching rule as above.
                for attribute, rule in approved_rules.items():
                    if attribute in row.attributes:
                        continue
                    found = _lookup(rule, run_manifest, sample)
                    if found is None:
                        continue
                    if rule.target != parent_type:
                        unmatched += 1
                        continue
                    value, raw_key = found
                    row.attributes[attribute] = MappedAttribute(
                        attribute=attribute, value=value, origin=ORIGIN_APPROVED,
                        raw_key=raw_key, source_file=metrics_source)

                if unmatched:
                    # A real, visible outcome, not a no-op: this many
                    # genuinely measured attributes had a rule that fired
                    # but targets a different sample type than this
                    # sample's actual parent, so none of them got a home on
                    # this row. Silently losing them is the failure mode
                    # this whole change exists to stop.
                    result.warnings.append(
                        f"{sample.nfcore_sample}: parent {sample.d_seq_uid} "
                        f"is {parent_type}, but {unmatched} measured "
                        "qc_attributes value(s) targeted a different sample "
                        "type and had no matching rule for it")

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
        # Precedence, lowest first: row_defaults, provenance (opted-in rules
        # only, see OutputRule.include_provenance), the rule's own attributes.
        # The rule's own are the most specific authority (e.g. A.ALN's
        # "Software" is the STAR version string; provenance's is the whole
        # software_versions dict).
        defaults = {name: ref for name, ref in pipeline_map.row_defaults.items()
                    if _exists(context, rule.sample_type, name)}
        provenance = pipeline_map.provenance_attributes if rule.include_provenance else {}
        merged_attrs = {**defaults, **provenance, **rule.attributes}

        if rule.primary_data and not any(_candidate_tiers(rule, run_manifest, None)):
            if rule.optional:
                # Its flag was off, so the output does not exist and there is
                # nothing to register. A row would carry no data file and
                # block the whole upload on a file no one can supply.
                continue
            result.warnings.append(
                f'rule "{rule.id}" ({rule.sample_type}) matched no file')
            result.missed_rules.append({"rule_id": rule.id, "sample_type": rule.sample_type})

        if rule.cardinality == "per_sample":
            result.rows.extend(
                _per_sample_rows(rule, merged_attrs, run_manifest, context, result.warnings))
        elif rule.cardinality == "per_file":
            result.rows.extend(
                _per_file_rows(rule, merged_attrs, run_manifest, context, result.warnings))
        else:
            row = _per_run_row(rule, merged_attrs, run_manifest, context, result.warnings)
            if row is not None:
                result.rows.append(row)

    return result


def _without_ruled_out_files(run_manifest: manifest.RunManifest,
                             pipeline_map: maps.PipelineMap) -> manifest.RunManifest:
    """``run_manifest`` without the outputs the map's ``unmapped_files`` rule out.

    A ruled-out file is never a rule's candidate or companion. Without this, a
    ruling would hold only in the fixture claims test: nf-core/sarek writes a
    tumor/normal pair's VCF as ``<tumor>_vs_<normal>/<tumor>_vs_<normal>.*``, the
    harvest attributes it to the tumor by its basename's prefix, and a germline
    rule would register it as the tumor's own call set. Matching is the claims
    test's, ``maps.UnmappedFiles.rules_out``. A map without ``unmapped_files`` gets
    its manifest back unchanged."""
    rulings = pipeline_map.unmapped_files
    if not rulings:
        return run_manifest
    kept = [o for o in run_manifest.outputs
            if not any(ruling.rules_out(o.path) for ruling in rulings)]
    if len(kept) == len(run_manifest.outputs):
        return run_manifest
    return run_manifest.model_copy(update={"outputs": kept})


def _with_build_local_ordinals(context: dict) -> dict:
    """``context`` with a ``next_name_ordinal`` that also counts this build's rows.

    The injected lookup reads the SAVED catalog, which cannot see the rows this
    call is still building: a run whose map makes several A.GEX rows (a counts
    matrix and one per contrast) would get the same "next" number for every one
    and name them all ``gex_<date>_1``. So the catalog is asked once per
    (sample type, prefix) and later rows take the following numbers. A context
    without the lookup is returned unchanged, keeping those Names blank.
    """
    lookup = context.get("next_name_ordinal")
    if not callable(lookup):
        return context
    issued: dict[tuple[str, str], int] = {}

    def next_ordinal(sample_type: str, prefix: str) -> int:
        key = (sample_type, prefix)
        issued[key] = issued[key] + 1 if key in issued else lookup(sample_type, prefix)
        return issued[key]

    return {**context, "next_name_ordinal": next_ordinal}


# Moved to maps.expand_braces; harvest.staged_globs and older tests use this name.
_expand_braces = maps.expand_braces


def _resolve_value(ref, run_manifest, sample=None, context=None):
    """One map value: "@name" computes, "$section.key" looks up, else literal."""
    if isinstance(ref, str) and ref.startswith("@"):
        pipeline = (context or {}).get("pipeline") or run_manifest.pipeline.name
        fn = pipelines.tokens_for(pipeline).get(ref[1:])
        return fn(run_manifest, sample, context) if fn else None
    return maps.resolve_ref(ref, run_manifest, sample)


def _candidate_tiers(rule: maps.OutputRule, run_manifest: manifest.RunManifest,
                     sample_name: str | None) -> list[list[manifest.OutputRecord]]:
    """Per candidate pattern, in preference order, the outputs it matches,
    sorted by path. ``sample_name`` filters to one sample's own outputs (the
    harvest-time ``OutputRecord.sample``); ``None`` takes the whole run."""
    if not rule.primary_data:
        return []
    tiers = []
    for pattern in rule.candidates:
        expanded = _expand_braces(pattern)
        tiers.append(sorted(
            (o for o in run_manifest.outputs
             if (sample_name is None or o.sample == sample_name)
             and any(fnmatch.fnmatch(o.path, p) for p in expanded)),
            key=lambda o: o.path))
    return tiers


def _primary_candidates(rule: maps.OutputRule, run_manifest: manifest.RunManifest,
                        sample_name: str | None) -> list[manifest.OutputRecord]:
    """The matches of the first candidate pattern that matches anything
    (spec G-4). The shared candidate step behind ``_primary_output`` and
    ``_attach_checksum``, so the two never disagree on what is a candidate."""
    return next((tier for tier in _candidate_tiers(rule, run_manifest, sample_name) if tier), [])


def _losing_candidates(rule: maps.OutputRule, run_manifest: manifest.RunManifest,
                       sample_name: str | None) -> list[manifest.OutputRecord]:
    """Outputs matched only by candidate patterns after the winning one. They
    are companions of the row, never rows of their own."""
    tiers = _candidate_tiers(rule, run_manifest, sample_name)
    winner = next((i for i, tier in enumerate(tiers) if tier), None)
    if winner is None:
        return []
    taken = {o.path for o in tiers[winner]}
    losers: list[manifest.OutputRecord] = []
    for tier in tiers[winner + 1:]:
        for output in tier:
            if output.path not in taken:
                taken.add(output.path)
                losers.append(output)
    return losers


def _pick_primary(candidates: list[manifest.OutputRecord],
                  checksums: dict[str, str]) -> manifest.OutputRecord | None:
    """The one tie-break rule behind both ``_primary_output`` and
    ``_attach_checksum``, kept in exactly one place so the two can never
    drift on which candidate wins: prefer whichever candidate ``checksums``
    already has a digest for -- run-checksum only ever hashes what the agent
    actually pointed at (see its own docstring), so when the agent hashed
    one of several equally valid candidates (e.g. nf-core/rnaseq with both
    ``--aligner star_salmon`` and ``--pseudo_aligner salmon`` publishing the
    same-named gene-counts matrix under two directories), the one it paid to
    SSH-hash is the one this must return -- picking sorted-first regardless
    would silently discard a real, already-computed checksum whenever the
    alphabetically first candidate happens not to be it. Among candidates
    that are equally checksummed (including "none of them"), sorted-first is
    still the deterministic tie-break ``harvest.py``'s own
    ``_resolve_named_outputs`` uses -- not a claim that it is the "right"
    one. ``candidates`` is assumed already sorted by path (as
    ``_primary_candidates`` returns it). ``None`` on an empty candidate list,
    never an error.
    """
    if not candidates:
        return None
    checksummed = [o for o in candidates if o.path in checksums]
    return checksummed[0] if checksummed else candidates[0]


def _primary_output(rule: maps.OutputRule, run_manifest: manifest.RunManifest,
                    sample_name: str | None) -> manifest.OutputRecord | None:
    """The one candidate from ``_primary_candidates`` to treat as this rule's
    primary file, so ``Checksum_PrimaryData`` can be looked up by the SAME
    path ``run-checksum`` hashed -- reusing ``rule.candidates``/``rule.primary_data``
    rather than a static ``$``-ref, since the harvested path is a run-time
    value no committed map can name in advance (see mapper.py's module
    docstring on why a map rule cannot express this directly).

    Delegates the actual tie-break to ``_pick_primary``, the same function
    ``_attach_checksum`` calls on its own candidate list -- one rule, one
    place, so a future change to the tie-break cannot update this selector
    without also updating the live rendering path. Returns ``None`` on an
    ordinary miss (see ``_primary_candidates``) -- never an error.
    """
    candidates = _primary_candidates(rule, run_manifest, sample_name)
    return _pick_primary(candidates, run_manifest.checksums)


def _attach_checksum(row: MappedRow, rule: maps.OutputRule,
                     run_manifest: manifest.RunManifest, sample_name: str | None) -> None:
    """Set ``File_PrimaryData`` (always, once a primary output is found) and
    ``Checksum_PrimaryData`` (only once that file has actually been hashed)
    on ``row``, both keyed by this rule's own matched output path -- sharing
    ``_primary_candidates``' tie-break logic with ``_primary_output`` (rather
    than calling it) so this can also see the FULL candidate set, needed to
    tell an ordinary pick from an ambiguous one; both ways of picking always
    name the SAME file, never two different candidates.

    Without ``File_PrimaryData`` (or ``Link_PrimaryData``, which ``_attach_links``
    fills from this same file), A.ALN/A.GEX fail the catalog's own
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
    genuinely novel convention in the table. The single exception, a
    basename two samples share (Salmon's ``quant.sf``), keeps its immediate
    folder -- see ``_file_primary_data_value``.

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
    # Same tie-break _primary_output uses, shared via _pick_primary rather
    # than re-derived here, so the two can never pick different winners.
    primary = _pick_primary(candidates, run_manifest.checksums)
    if "File_PrimaryData" not in row.attributes:
        ambiguous = len(candidates) > 1 and len(checksummed) != 1
        row.attributes["File_PrimaryData"] = MappedAttribute(
            attribute="File_PrimaryData",
            value=_file_primary_data_value(rule, run_manifest, primary, sample_name),
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


def _file_primary_data_value(rule: maps.OutputRule, run_manifest: manifest.RunManifest,
                             primary: manifest.OutputRecord,
                             sample_name: str | None) -> str:
    """The basename, by house convention (see ``_attach_checksum``) -- unless
    another SAMPLE's file under the same rule has the same basename, in
    which case the immediate folder is kept: "CONTROL_REP1/quant.sf".

    Salmon names every sample's output ``quant.sf``; a bare basename would
    put the same string on every row of the Samples sheet. Only a collision
    between samples counts: a per_run rule (``sample_name`` None) names one
    file for the whole run, and two same-named candidates for ONE sample are
    the tie-break ``_pick_primary`` already resolves and QA already reports,
    not a reason to change what the cell looks like.
    """
    name = os.path.basename(primary.path)
    if sample_name is None:
        return name
    collides = any(
        o.sample != sample_name and os.path.basename(o.path) == name
        for o in _primary_candidates(rule, run_manifest, None))
    if not collides:
        return name
    folder = os.path.basename(os.path.dirname(primary.path))
    return f"{folder}/{name}" if folder else name


#: The link cell for each file cell, filled by _attach_links.
LINK_FOR = {"File_PrimaryData": "Link_PrimaryData", "File_SecondaryData": "Link_SecondaryData"}


def run_file_path(run_manifest: manifest.RunManifest, rel_path: str) -> str:
    """Where a run-relative output path sits on the cluster: under ``run_dir``."""
    return posixpath.normpath(posixpath.join(run_manifest.run_dir, rel_path))


def _attach_links(row: MappedRow, run_manifest: manifest.RunManifest, context) -> None:
    """Put each placed data file's location in its link cell: Link_PrimaryData for
    File_PrimaryData, Link_SecondaryData for File_SecondaryData.

    Interim (ruled 2026-10-21): the file cell holds a bare name by house convention
    (see _attach_checksum), so an uploaded row said what its file was called but not
    where it was. Until the data has a permanent home, the link is the file's path in
    the cluster run folder. Only for a type that has the link attribute, and never over
    a link the map already set."""
    for file_attr, link_attr in LINK_FOR.items():
        placed = row.attributes.get(file_attr)
        if (placed is None or not placed.source_file or link_attr in row.attributes
                or not _exists(context, row.sample_type, link_attr)):
            continue
        row.attributes[link_attr] = MappedAttribute(
            attribute=link_attr, value=run_file_path(run_manifest, placed.source_file),
            origin=ORIGIN_MAP, source_file=placed.source_file)


NOTES_PREFIX = "Also in run: "


def _exists(context, sample_type: str, attribute: str) -> bool:
    has = (context or {}).get("has_attribute")
    return bool(has(sample_type, attribute)) if callable(has) else True


def _wild_dirs(context) -> list[str]:
    """The folder names a per_file row's glob matched with a bare "*" segment
    (differentialabundance 2.0.0's paramset). A companion must sit under the
    same ones, so p1's filtered table never pairs with p2's results.

    fnmatch's "*" crosses "/", so a flat expansion ("tables/differential/
    *.tsv") also matches a nested path; only an expansion with as many
    segments as the path says which folders were wildcards."""
    path = str((context or {}).get("file_path") or "")
    glob = str((context or {}).get("file_glob") or "")
    have = path.split("/")
    for pattern in _expand_braces(glob):
        want = pattern.split("/")
        if len(want) == len(have) and fnmatch.fnmatch(path, pattern):
            return [seg for seg, pat in zip(have[:-1], want[:-1]) if pat == "*"]
    return []


def _pairs_with_row_file(path: str, companion_glob: str, context) -> bool:
    """A per_file row's companion: the same stem and the same wildcard folders
    as the row's own file.

    When the companion glob's file-name part is "*" plus a literal suffix, its
    own stem is read the way ``@file_stem`` reads the row's, and the two must
    be equal, so contrast "A_vs_B" never takes "A_vs_B_batch"'s table. Any
    other companion glob falls back to a prefix test: the row's stem followed
    by "." or "_"."""
    stem = common.file_stem(context)
    if not stem:
        return False
    own = common.file_stem({"file_path": path, "file_glob": companion_glob})
    if own is not None:
        if own != stem:
            return False
    else:
        name = os.path.basename(path)
        if not (name.startswith(stem + ".") or name.startswith(stem + "_")):
            return False
    segments = path.split("/")
    return all(folder in segments for folder in _wild_dirs(context))


def _companion_outputs(glob: str, run_manifest, sample_name, context) -> list:
    expanded = _expand_braces(glob)
    per_file = bool((context or {}).get("file_path"))
    found = []
    for output in sorted(common.all_outputs(run_manifest, context), key=lambda o: o.path):
        if not any(fnmatch.fnmatch(output.path, p) for p in expanded):
            continue
        if sample_name is not None and output.sample != sample_name:
            continue
        if per_file and not _pairs_with_row_file(output.path, glob, context):
            continue
        found.append(output)
    return found


def _place_companions(row: MappedRow, rule: maps.OutputRule, run_manifest,
                      sample_name, context, losers, warnings: list[str]) -> None:
    """Place every companion of this row (spec G-3): the named slot if the
    type has it and it is empty, else File_SecondaryData if the type has it
    and it is empty, else the run-relative path appended to Notes. A type
    with no Notes sends the leftovers to the warnings instead. A value the
    map already set is never overwritten."""
    if not rule.primary_data:
        return
    primary = row.attributes.get("File_PrimaryData")
    seen = {primary.source_file} if primary is not None else set()
    queue: list[tuple[str | None, manifest.OutputRecord]] = []
    for companion in rule.companions:
        for output in _companion_outputs(companion.glob, run_manifest, sample_name, context):
            if output.path not in seen:
                seen.add(output.path)
                queue.append((companion.slot, output))
    for output in losers:
        if output.path not in seen:
            seen.add(output.path)
            queue.append((None, output))
    overflow: list[str] = []
    for slot, output in queue:
        if slot and slot not in row.attributes and _exists(context, row.sample_type, slot):
            row.attributes[slot] = MappedAttribute(
                attribute=slot, value=os.path.basename(output.path), origin=ORIGIN_MAP,
                source_file=output.path)
            continue
        if ("File_SecondaryData" not in row.attributes
                and _exists(context, row.sample_type, "File_SecondaryData")):
            row.attributes["File_SecondaryData"] = MappedAttribute(
                attribute="File_SecondaryData", value=os.path.basename(output.path),
                origin=ORIGIN_MAP, source_file=output.path)
            checksum = run_manifest.checksums.get(output.path)
            if checksum and "Checksum_SecondaryData" not in row.attributes:
                row.attributes["Checksum_SecondaryData"] = MappedAttribute(
                    attribute="Checksum_SecondaryData", value=checksum, origin=ORIGIN_MAP,
                    raw_key=f"$checksums.{output.path}", source_file=output.path)
            continue
        overflow.append(output.path)
    if not overflow:
        return
    if not _exists(context, row.sample_type, "Notes"):
        label = row.nfcore_sample or (context or {}).get("file_path") or rule.id
        warnings.append(f"{label}: no Notes on {row.sample_type}; also in run: "
                        + ", ".join(overflow))
        return
    note = NOTES_PREFIX + ", ".join(overflow)
    existing = row.attributes.get("Notes")
    value = f"{existing.value}; {note}" if existing is not None and existing.value else note
    row.attributes["Notes"] = MappedAttribute(
        attribute="Notes", value=value, origin=ORIGIN_MAP,
        raw_key=existing.raw_key if existing is not None else "",
        source_file=overflow[0])


def _per_sample_rows(rule: maps.OutputRule, merged_attrs: dict[str, str],
                     run_manifest: manifest.RunManifest,
                     context: dict | None, warnings: list[str]) -> list[MappedRow]:
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
    - `multirun`: row SHIPS -- this is what registers the output files a
      multi-run sample's analysis produced, which is the whole point: nf-core
      concatenates its reads before alignment, so the resulting BAM (etc.) is
      real primary data, same as any other sample's. `Parent` is the
      `;`-joined, first-occurrence de-duplicated set of D.SEQ UIDs recovered
      for the sample's OWN contributing rows (`sample.d_seq_uid_multirun`,
      built by `uid_resolve._resolve_multirun_parents`) -- omitted entirely,
      not written empty, when none of them resolved. A partial list (some but
      not all contributing rows resolved) is real and is not upgraded or
      downgraded to look like anything else; `harvest.py` records a warning
      when it happens; this layer just joins whatever it was handed.

    Replicate groups (`RunManifest.replicate_groups`, a map with
    `replicate_samples`): each sample is one replicate. For each group, a file
    the GROUP owns (atacseq's merged replicates, `<sample>.mRp.*`) wins when its
    candidate tier comes no later than any replicate's: the group then gets ONE
    row for this rule, parented by every replicate, and its replicates' files
    for the rule are that row's losing candidates (companions, G-4), never rows
    of their own. Otherwise each replicate gets its own row from its own files,
    exactly as any sample does. So one biological unit never registers the same
    kind of output twice; a map that wants both writes two rules.
    """
    groups = run_manifest.replicate_groups or {}
    group_of = {member: group for group, members in groups.items() for member in members}
    decided: dict[str, MappedRow | None] = {}
    rows: list[MappedRow] = []
    for sample in run_manifest.samples:
        group = group_of.get(sample.nfcore_sample)
        if group is not None:
            if group not in decided:
                members = [s for s in run_manifest.samples
                           if group_of.get(s.nfcore_sample) == group]
                decided[group] = _group_row(rule, merged_attrs, run_manifest, context,
                                            warnings, group, members)
                if decided[group] is not None:
                    rows.append(decided[group])
            if decided[group] is not None:
                continue
        row = _sample_row(rule, merged_attrs, run_manifest, context, warnings, sample)
        if row is not None:
            rows.append(row)
    return rows


def _first_tier(rule: maps.OutputRule, run_manifest: manifest.RunManifest,
                sample_name: str) -> int | None:
    """The index of the first candidate pattern that matches one of `sample_name`'s files."""
    tiers = _candidate_tiers(rule, run_manifest, sample_name)
    return next((i for i, tier in enumerate(tiers) if tier), None)


def _group_row(rule: maps.OutputRule, merged_attrs: dict[str, str],
               run_manifest: manifest.RunManifest, context: dict | None,
               warnings: list[str], group: str,
               members: list[manifest.SampleRecord]) -> MappedRow | None:
    """The replicate group's own row for this rule, or None when the group owns
    no file that wins over its replicates' (see `_per_sample_rows`). Its parents
    are every replicate's; its replicates' files for the rule (candidates and
    companions) become its losing candidates, so none is dropped unseen."""
    own = _first_tier(rule, run_manifest, group)
    if own is None:
        return None
    theirs = [t for t in (_first_tier(rule, run_manifest, m.nfcore_sample) for m in members)
              if t is not None]
    if theirs and min(theirs) < own:
        return None
    parents: list[str] = []
    unresolved: list[str] = []
    for member in members:
        if member.uid_resolution in _HAS_PARENT and member.d_seq_uid:
            parents.append(member.d_seq_uid)
        elif member.uid_resolution == manifest.RESOLUTION_MULTIRUN and member.d_seq_uid_multirun:
            parents.extend(member.d_seq_uid_multirun)
        else:
            unresolved.append(member.nfcore_sample)
    if unresolved:
        warnings.append(f"{group}: replicate(s) {', '.join(unresolved)} resolved to no "
                        f"D.SEQ; the group's {rule.id} row names only the others as Parent")
    first = members[0]
    stand_in = manifest.SampleRecord(
        nfcore_sample=group, fastq_1=first.fastq_1, fastq_2=first.fastq_2,
        uid_resolution=manifest.RESOLUTION_MULTIRUN, d_seq_uid_multirun=parents,
        parent_sample_type=first.parent_sample_type)
    lost: list[manifest.OutputRecord] = []
    for member in members:
        for tier in _candidate_tiers(rule, run_manifest, member.nfcore_sample):
            lost.extend(tier)
        for companion in rule.companions:
            lost.extend(_companion_outputs(companion.glob, run_manifest,
                                           member.nfcore_sample, context))
    return _sample_row(rule, merged_attrs, run_manifest, context, warnings, stand_in,
                       extra_losers=sorted(lost, key=lambda o: o.path))


def _sample_row(rule: maps.OutputRule, merged_attrs: dict[str, str],
                run_manifest: manifest.RunManifest, context: dict | None,
                warnings: list[str], sample: manifest.SampleRecord,
                extra_losers: list[manifest.OutputRecord] = ()) -> MappedRow | None:
    """One sample's row for a per_sample rule (see `_per_sample_rows`), or None."""
    if sample.uid_resolution in _NO_CHILD_ROW:
        return None
    if (rule.optional and rule.primary_data and rule.skip_samples_without_file
            and not any(_candidate_tiers(rule, run_manifest, sample.nfcore_sample))):
        # The map says some samples never have this output (a tumour in a
        # tumour/normal run gets no germline call set). A row without its
        # data file is a hard reject that blocks the whole upload, so none
        # is written, and the skip is logged. Every other rule still writes
        # the fileless row: there its missing file is a real gap to show.
        warnings.append(f"{sample.nfcore_sample}: no file for optional rule "
                        f"{rule.id}; no row written")
        return None
    row = MappedRow(sample_type=rule.sample_type, nfcore_sample=sample.nfcore_sample,
                    rule_id=rule.id)
    row_context = {**(context or {}), "row_sample_type": rule.sample_type}
    for attribute, ref in merged_attrs.items():
        value = _resolve_value(ref, run_manifest, sample, row_context)
        if value is None or value == "":
            continue
        row.attributes[attribute] = MappedAttribute(
            attribute=attribute, value=value, origin=ORIGIN_MAP,
            raw_key=ref if isinstance(ref, str) and ref.startswith("$") else "",
            source_file=run_manifest.sources.get("params", ""))
    _attach_checksum(row, rule, run_manifest, sample.nfcore_sample)
    _place_companions(row, rule, run_manifest, sample.nfcore_sample, row_context,
                      _losing_candidates(rule, run_manifest, sample.nfcore_sample)
                      + list(extra_losers),
                      warnings)
    _attach_links(row, run_manifest, row_context)
    # Parent is a structural lineage field, not a mapped attribute -- set
    # it last so no rule attribute can accidentally clobber it. A
    # multi-run sample joins its OWN multi-parent list; a resolution in
    # `_HAS_PARENT` sets its single `d_seq_uid` instead; an unresolved
    # sample ships its row with no `Parent` key at all.
    if sample.uid_resolution == manifest.RESOLUTION_MULTIRUN:
        joined = _join_uids(sample.d_seq_uid_multirun)
        if joined:
            row.attributes["Parent"] = MappedAttribute(
                attribute="Parent", value=joined, origin=ORIGIN_MAP)
    elif sample.uid_resolution in _HAS_PARENT and sample.d_seq_uid:
        row.attributes["Parent"] = MappedAttribute(
            attribute="Parent", value=sample.d_seq_uid, origin=ORIGIN_MAP)
    return row


def _per_run_row(rule: maps.OutputRule, merged_attrs: dict[str, str],
                 run_manifest: manifest.RunManifest,
                 context: dict | None, warnings: list[str]) -> MappedRow | None:
    """One row for the whole run, with `Parent` `;`-joined across the run's
    resolved `d_seq_uid`s -- and, since they too are honest identity
    (`sample.d_seq_uid_multirun` is real, resolved D.SEQ UIDs, only the
    QC backfill is a measurement limit for them -- see the module and
    uid_resolve.py docstrings), across every multi-run sample's own resolved
    parents as well.

    Join order follows the manifest's own sample order (the samplesheet
    order the harvester recorded), which is deterministic and requires no
    extra sort key; within a multi-run sample's own contribution, the order
    is whatever `uid_resolve._resolve_multirun_parents` returned (that
    sample's rows, in their own order). Duplicates are dropped by first
    occurrence, run-wide -- so a D.SEQ that is somehow both a resolved
    single-run sample's parent AND a multi-run sample's contributing parent
    is still only named once. A sample whose resolution is in `_HAS_PARENT`
    contributes its `d_seq_uid`, checked by resolution, not by `d_seq_uid`
    truthiness, so an unresolved or ambiguous sample (see `_per_sample_rows`)
    can never leak into this join even if a future change set a UID on one
    of them. If nothing resolved at all, `Parent` is omitted entirely rather
    than set to an empty string.
    """
    row = MappedRow(sample_type=rule.sample_type, rule_id=rule.id)
    row_context = {**(context or {}), "row_sample_type": rule.sample_type}
    for attribute, ref in merged_attrs.items():
        value = _resolve_value(ref, run_manifest, None, row_context)
        if value is None or value == "":
            continue
        row.attributes[attribute] = MappedAttribute(
            attribute=attribute, value=value, origin=ORIGIN_MAP,
            raw_key=ref if isinstance(ref, str) and ref.startswith("$") else "",
            source_file=run_manifest.sources.get("params", ""))
    _attach_checksum(row, rule, run_manifest, None)
    _place_companions(row, rule, run_manifest, None, row_context,
                      _losing_candidates(rule, run_manifest, None), warnings)
    _attach_links(row, run_manifest, row_context)

    uids: list[str] = []
    for sample in run_manifest.samples:
        # `and sample.d_seq_uid` is not redundant with the resolution check:
        # the pairing of a _HAS_PARENT resolution with a non-None uid is an
        # invariant of uid_resolve.resolve(), enforced in another module.
        # Cheap to keep the guard local.
        if sample.uid_resolution in _HAS_PARENT and sample.d_seq_uid:
            uids.append(sample.d_seq_uid)
        elif sample.uid_resolution == manifest.RESOLUTION_MULTIRUN:
            uids.extend(sample.d_seq_uid_multirun)
    joined = _join_uids(uids)
    if joined and "Parent" not in row.attributes:
        row.attributes["Parent"] = MappedAttribute(
            attribute="Parent", value=joined, origin=ORIGIN_MAP)

    return row if row.attributes else None


def _per_file_rows(rule: maps.OutputRule, merged_attrs: dict[str, str],
                   run_manifest: manifest.RunManifest,
                   context: dict | None, warnings: list[str]) -> list[MappedRow]:
    """One per_run-shaped row for EACH file the rule's glob matches, in path order.

    A per_run rule keeps the first match and drops the rest, which is right for
    a run's single counts matrix and wrong for an output written once per
    contrast. Each row here is built by ``_per_run_row`` against a copy of the
    manifest whose inventory holds only that row's file, so the file, its
    checksum and the run-wide ``Parent`` join are decided by exactly the same
    code as a per_run row, and no row can be handed another row's file or
    checksum. The file is also put in ``context``, for ``@file_stem``, with the
    whole inventory, from which a companion pairs with the row's file by stem
    and wildcard folders (``_pairs_with_row_file``).

    Two matches can share a file name in different folders (nf-core/
    differentialabundance 2.0.0 runs one contrast under several paramsets).
    ``File_PrimaryData`` is a bare file name by house convention, so those rows
    keep their immediate folder instead, as Salmon's per-sample ``quant.sf``
    does (see ``_file_primary_data_value``): otherwise two rows would name the
    same file.
    """
    candidates = _primary_candidates(rule, run_manifest, None)
    name_counts: dict[str, int] = {}
    for output in candidates:
        base = os.path.basename(output.path)
        name_counts[base] = name_counts.get(base, 0) + 1
    rows: list[MappedRow] = []
    for output in candidates:
        only_this_file = run_manifest.model_copy(update={"outputs": [output]})
        row_context = {**(context or {}), "file_path": output.path, "file_glob": rule.candidates[0],
                       "all_outputs": list(run_manifest.outputs)}
        row = _per_run_row(rule, merged_attrs, only_this_file, row_context, warnings)
        if row is None:
            continue
        primary = row.attributes.get("File_PrimaryData")
        base = os.path.basename(output.path)
        if (primary is not None and primary.origin == ORIGIN_MAP and primary.value == base
                and name_counts[base] > 1):
            primary.value = "/".join(output.path.split("/")[-2:])
        rows.append(row)
    return rows


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

"""Apply a pipeline map to a manifest, and name everything it could not map.

Resolution order is committed map rule, then approved database rule, then
unmapped. A committed, code-reviewed map rule is the stronger authority: if it
already produced a value for an attribute, an approved rule must not overwrite
it — it only fills gaps the committed rule left. A PENDING proposal is never
applied here at all: it is evidence shown to the agent, which must re-affirm
it, so an unreviewed rule cannot quietly become permanent by repetition.
"""
from __future__ import annotations

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
    # Real, visible outcomes that are not a row and not an unmapped key:
    # today, only the QC-backfill cases in `apply`'s main loop below --
    # a parent whose real type could not be determined (no backfill row
    # written for it), and a parent whose real type IS known but some of the
    # map's qc_attributes rules target a different type (those measured
    # attributes have no home on this row). Never silently dropped; see the
    # loop below for both cases.
    warnings: list[str] = Field(default_factory=list)


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
        # provenance_attributes is only merged in for a rule that opts in
        # (rule.include_provenance) -- see OutputRule.include_provenance in
        # maps.py for why the default is False. A rule that does not opt in
        # gets only its own attributes.
        #
        # When it IS merged, the output rule's own attributes are still the
        # more specific authority: they must win over a provenance attribute
        # of the same name (e.g. A.ALN's "Software" is the STAR version
        # string; provenance's "Software" is the whole software_versions
        # dict).
        if rule.include_provenance:
            merged_attrs = {**pipeline_map.provenance_attributes, **rule.attributes}
        else:
            merged_attrs = dict(rule.attributes)

        if rule.cardinality == "per_sample":
            for row in _per_sample_rows(rule, merged_attrs, run_manifest):
                result.rows.append(row)
        else:
            row = _per_run_row(rule, merged_attrs, run_manifest)
            if row is not None:
                result.rows.append(row)

    return result


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
        rows.append(row)
    return rows


def _per_run_row(rule: maps.OutputRule, merged_attrs: dict[str, str],
                 run_manifest: manifest.RunManifest) -> MappedRow | None:
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
    row = MappedRow(sample_type=rule.sample_type)
    for attribute, ref in merged_attrs.items():
        value = maps.resolve_ref(ref, run_manifest)
        if value is None or value == "":
            continue
        row.attributes[attribute] = MappedAttribute(
            attribute=attribute, value=value, origin=ORIGIN_MAP,
            raw_key=ref if isinstance(ref, str) and ref.startswith("$") else "",
            source_file=run_manifest.sources.get("params", ""))

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
    if joined:
        row.attributes["Parent"] = MappedAttribute(
            attribute="Parent", value=joined, origin=ORIGIN_MAP)

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

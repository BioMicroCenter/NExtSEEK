# NessieAI/ns/reingest/answers.py
"""Curator answers to one reingest run's flags, applied before rendering.

The fix loop: build-upload-xlsx maps and QAs the run, Nessie relays the flags,
the curator answers in chat, and the op is called again with those answers.
Three kinds, each licensed only by what THIS run flagged:

  fill    set a flagged, empty, non-run-sourced cell to a value the curator said
  choose  pick one of the listed candidates for an ambiguous data file
  place   put an uncovered raw metric into an existing attribute, this run only

The load-bearing rule is that no measured number passes through the model. A
fill is therefore checked against an allowlist built from the findings, and a
run-sourced attribute (derived from the map, never hand-listed) is refused even
when a finding names it. A place or choose moves values that come from the
manifest; Nessie supplies only the decision.

Pure: no Django, no I/O. granular.py wires it in.
"""
from __future__ import annotations

import hashlib
import json
import os

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from NessieAI.ns.reingest.mapper import ORIGIN_CURATOR, MappedAttribute
from NessieAI.ns.reingest_qa import _value_missing, group_members_for_label

_PRIMARY = ("File_PrimaryData", "Checksum_PrimaryData")
_SECONDARY = ("File_SecondaryData", "Checksum_SecondaryData")

# Keys the row structure owns. Never set from chat, whatever QA says: UID and
# Parent are identity and lineage, Notes is composed under its own clobber
# guard (reingest_qa NOTES_WOULD_CLOBBER), and a checksum is a measurement of
# a file, which only the run (or a choose, from the manifest) supplies.
NEVER_FILLABLE = frozenset({"UID", "Parent", "Notes",
                            "Checksum_PrimaryData", "Checksum_SecondaryData"})

# A place moves a raw metric into an attribute; a data file and its checksum
# are never a metric, so neither is a place target.
NEVER_PLACEABLE = NEVER_FILLABLE | frozenset(_PRIMARY) | frozenset(_SECONDARY)


class AnswerRejected(ValueError):
    """One or more answers fall outside this run's writable set."""

    def __init__(self, reasons: list[str]):
        self.reasons = list(reasons)
        super().__init__("; ".join(self.reasons))


class FillAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_type: str
    attribute: str
    value: str = Field(min_length=1)
    rows: list[int] | None = None

    @field_validator("value")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("a fill value must not be blank")
        return value


class ChooseAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sample_type: str
    attribute: str
    path: str


class PlaceAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_key: str
    sample_type: str
    attribute: str


class Answers(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fill: list[FillAnswer] = Field(default_factory=list)
    choose: list[ChooseAnswer] = Field(default_factory=list)
    place: list[PlaceAnswer] = Field(default_factory=list)

    def is_empty(self) -> bool:
        return not (self.fill or self.choose or self.place)

    def digest(self) -> str:
        """Stable sha256 of the answers, recorded on each build."""
        body = json.dumps(self.model_dump(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode("utf-8")).hexdigest()


def parse_answers(raw) -> Answers:
    """``raw`` is None, "", a JSON string or a dict. Anything malformed raises."""
    if raw is None or raw == "" or raw == {}:
        return Answers()
    data = raw
    if isinstance(raw, str):
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise AnswerRejected([f"answers is not valid JSON: {exc}"]) from exc
    try:
        return Answers.model_validate(data)
    except ValidationError as exc:
        raise AnswerRejected([f"answers is malformed: {exc}"]) from exc


def run_sourced_attributes(pipeline_map, approved_rules, sample_type: str) -> frozenset[str]:
    """Attributes of ``sample_type`` whose value the run itself supplies.

    Derived from the map, so an attribute added to a map later is unwritable
    from chat by default. A ``@computed`` value (the session user, the run
    date, a name ordinal) is not a measurement and is not included.
    """
    out: set[str] = set()
    for name, rule in pipeline_map.qc_attributes.items():
        if rule.target == sample_type:
            out.add(name)
    for name, rule in (approved_rules or {}).items():
        if rule.target == sample_type:
            out.add(name)
    for rule in pipeline_map.outputs:
        if rule.sample_type != sample_type:
            continue
        merged = ({**pipeline_map.provenance_attributes, **rule.attributes}
                  if rule.include_provenance else dict(rule.attributes))
        out |= {name for name, value in merged.items()
                if isinstance(value, str) and value.startswith("$")}
        if rule.primary_data:
            out |= set(_PRIMARY)
        if rule.secondary_data_glob:
            out |= set(_SECONDARY)
    return frozenset(out)


def _names(finding_attribute: str, attribute: str) -> bool:
    if finding_attribute == attribute:
        return True
    return attribute in (group_members_for_label(finding_attribute) or ())


def _flagged_rows(answer: FillAnswer, findings) -> list[int]:
    return sorted({f.row_index for f in findings
                   if f.row_index >= 0
                   and f.sample_type == answer.sample_type
                   and _names(f.attribute, answer.attribute)})


def fill_targets(answer: FillAnswer, findings) -> list[int]:
    """The rows a fill applies to: its own ``rows``, else exactly the flagged ones."""
    return list(answer.rows) if answer.rows is not None else _flagged_rows(answer, findings)


def _is_empty(mapped) -> bool:
    return mapped is None or _value_missing(mapped.value)


def check_fill(answer: FillAnswer, *, findings, rows, run_sourced) -> list[str]:
    """Reasons ``answer`` is refused; empty means allowed.

    ``findings`` are the first QA pass's findings for this sample type and
    ``rows`` its mapped rows, in the order QA indexed them.
    """
    label = f"fill {answer.sample_type}.{answer.attribute}"
    if answer.attribute in NEVER_FILLABLE:
        return [f"{label}: {answer.attribute} is never set from chat"]
    if answer.attribute in run_sourced:
        return [f"{label}: this value comes from the run data, so it cannot be set from chat"]
    if answer.rows is not None and not answer.rows:
        return [f"{label}: rows is empty"]
    flagged = _flagged_rows(answer, findings)
    if not flagged:
        return [f"{label}: QA did not flag this attribute in this run"]
    targets = fill_targets(answer, findings)
    unflagged = [r for r in targets if r not in flagged]
    if unflagged:
        return [f"{label}: rows {unflagged} were not flagged for it"]
    held = [r for r in targets if not _is_empty(rows[r].attributes.get(answer.attribute))]
    if held:
        return [f"{label}: rows {held} already hold a value; a fill never overwrites"]
    return []


_CHECKSUM_FOR = {"File_PrimaryData": "Checksum_PrimaryData",
                 "File_SecondaryData": "Checksum_SecondaryData"}


def check_choose(answer: ChooseAnswer, *, groups: list[dict]) -> list[str]:
    label = f"choose {answer.sample_type}.{answer.attribute}"
    if answer.attribute not in _CHECKSUM_FOR:
        return [f"{label}: only a data file can be chosen"]
    matches = [g for g in groups
               if g["sample_type"] == answer.sample_type and g["attribute"] == answer.attribute]
    if not matches:
        return [f"{label}: this run has no ambiguous pick for it"]
    if not any(answer.path in g["candidates"] for g in matches):
        listed = sorted({c for g in matches for c in g["candidates"]})
        return [f"{label}: {answer.path!r} is not one of the candidates {listed}"]
    return []


def check_place(answer: PlaceAnswer, *, unmapped: list[dict], rows, attribute_exists,
                run_sourced, existing_values) -> list[str]:
    """Reasons ``answer`` is refused; empty means allowed.

    ``run_sourced`` is the sample type's run-sourced attribute set, and
    ``existing_values(uids, attribute)`` returns ``{uid: current value}`` for
    the targeted samples that already hold one in NExtSEEK. An update row
    carries only the backfill, and the upload deep-merges it over the sample,
    so the mapped row alone cannot show what a place would overwrite. Any
    failure of that lookup refuses the place; it never allows it.
    """
    label = f"place {answer.raw_key} -> {answer.sample_type}.{answer.attribute}"
    if answer.raw_key not in {u.get("raw_key") for u in unmapped}:
        return [f"{label}: not an uncovered key in this run"]
    if answer.attribute in NEVER_PLACEABLE:
        return [f"{label}: {answer.attribute} is never set from chat"]
    if answer.attribute in run_sourced:
        return [f"{label}: this attribute is measured by the run; it cannot be placed from chat"]
    existing = [r for r in rows if r.uid]
    if not existing:
        return [f"{label}: no existing {answer.sample_type} rows in this run carry per-sample metrics"]
    if any(not _is_empty(r.attributes.get(answer.attribute)) for r in existing):
        return [f"{label}: {answer.attribute} is already set from the run by the map"]
    if not attribute_exists(answer.sample_type, answer.attribute):
        return [f"{label}: {answer.attribute} is not defined on {answer.sample_type}; "
                "the upload would reject the row"]
    try:
        held = existing_values(sorted({r.uid for r in existing}), answer.attribute)
    except Exception:  # noqa: BLE001 -- an unreadable sample must never read as empty
        return [f"{label}: could not read existing values to check for overwrites"]
    if held:
        return [f"{label}: {len(held)} sample(s) already hold a value in NExtSEEK; "
                "place never overwrites"]
    return []


def split_for_call(bundle: Answers, *, this_call: set[str], other_call: set[str]):
    """Answers for this call's sample types, plus the deferred rest.

    The agent sends every answer on every call; a new-mode call renders the
    analysis children and an update-mode call the backfill, so an answer about
    the other call's types is deferred, not refused. A type in neither is a typo.
    """
    keep: dict[str, list] = {"fill": [], "choose": [], "place": []}
    deferred: list[dict] = []
    unknown: list[str] = []
    for kind in ("fill", "choose", "place"):
        for answer in getattr(bundle, kind):
            if answer.sample_type in this_call:
                keep[kind].append(answer)
            elif answer.sample_type in other_call:
                deferred.append({"kind": kind, "sample_type": answer.sample_type,
                                 "attribute": answer.attribute})
            else:
                unknown.append(f"{kind} {answer.sample_type}.{answer.attribute}: "
                               f"{answer.sample_type} is not produced by this run")
    if unknown:
        raise AnswerRejected(unknown)
    return Answers(**keep), deferred


def _conflicts(bundle: Answers, findings_by_type) -> list[str]:
    """Reasons two answers aim at the same cell; list order must never decide."""
    reasons: list[str] = []
    fill_rows: dict[tuple[str, str], set[int]] = {}
    for answer in bundle.fill:
        key = (answer.sample_type, answer.attribute)
        rows = set(fill_targets(answer, findings_by_type.get(answer.sample_type, [])))
        overlap = sorted(rows & fill_rows.get(key, set()))
        if overlap:
            reasons.append(f"fill {key[0]}.{key[1]}: answered twice for rows {overlap}")
        fill_rows.setdefault(key, set()).update(rows)
    seen_choose: set[tuple[str, str]] = set()
    for answer in bundle.choose:
        key = (answer.sample_type, answer.attribute)
        if key in seen_choose:
            reasons.append(f"choose {key[0]}.{key[1]}: answered twice")
        seen_choose.add(key)
    seen_place: set[tuple[str, str]] = set()
    seen_keys: set[str] = set()
    for answer in bundle.place:
        key = (answer.sample_type, answer.attribute)
        if key in seen_place:
            reasons.append(f"place {key[0]}.{key[1]}: answered twice")
        if answer.raw_key in seen_keys:
            reasons.append(f"place {answer.raw_key}: placed twice")
        if key in fill_rows:
            reasons.append(f"place {key[0]}.{key[1]}: also set by a fill")
        seen_place.add(key)
        seen_keys.add(answer.raw_key)
    return reasons


def validate(bundle: Answers, *, findings_by_type, mapped_by_type, unmapped, groups,
             run_sourced_for, attribute_exists, existing_values) -> None:
    reasons: list[str] = _conflicts(bundle, findings_by_type)
    for answer in bundle.fill:
        reasons += check_fill(answer, findings=findings_by_type.get(answer.sample_type, []),
                              rows=mapped_by_type.get(answer.sample_type, []),
                              run_sourced=run_sourced_for(answer.sample_type))
    for answer in bundle.choose:
        reasons += check_choose(answer, groups=groups)
    for answer in bundle.place:
        reasons += check_place(answer, unmapped=unmapped,
                               rows=mapped_by_type.get(answer.sample_type, []),
                               attribute_exists=attribute_exists,
                               run_sourced=run_sourced_for(answer.sample_type),
                               existing_values=existing_values)
    if reasons:
        raise AnswerRejected(reasons)


def apply_answers(bundle: Answers, *, mapped_by_type, findings_by_type, run_manifest,
                  answered_by: str) -> None:
    """Apply an already-validated set. Call ``validate`` first."""
    for answer in bundle.fill:
        rows = mapped_by_type.get(answer.sample_type, [])
        for index in fill_targets(answer, findings_by_type.get(answer.sample_type, [])):
            rows[index].attributes[answer.attribute] = MappedAttribute(
                attribute=answer.attribute, value=answer.value, origin=ORIGIN_CURATOR,
                answered_by=answered_by)
    for answer in bundle.choose:
        checksum_attr = _CHECKSUM_FOR[answer.attribute]
        for row in mapped_by_type.get(answer.sample_type, []):
            current = row.attributes.get(answer.attribute)
            if current is None or answer.path not in current.candidates:
                continue
            # Reingest never produces Link_PrimaryData/Link_SecondaryData (see
            # mapper._attach_checksum), so the file name and its checksum are
            # the whole of the chosen file's harvested values.
            row.attributes[answer.attribute] = MappedAttribute(
                attribute=answer.attribute, value=os.path.basename(answer.path),
                origin=ORIGIN_CURATOR, source_file=answer.path, answered_by=answered_by)
            checksum = run_manifest.checksums.get(answer.path)
            if checksum:
                row.attributes[checksum_attr] = MappedAttribute(
                    attribute=checksum_attr, value=checksum, origin=ORIGIN_CURATOR,
                    raw_key=f"$checksums.{answer.path}", source_file=answer.path,
                    answered_by=answered_by)
            else:
                # The old checksum hashed the file that was NOT chosen.
                row.attributes.pop(checksum_attr, None)
    samples = {s.nfcore_sample: s for s in run_manifest.samples}
    metrics_source = run_manifest.sources.get("metrics", "")
    for answer in bundle.place:
        for row in mapped_by_type.get(answer.sample_type, []):
            sample = samples.get(row.nfcore_sample)
            # A sample whose metrics lack the key is skipped, exactly as a map
            # rule skips a sample where its metric is absent.
            if not row.uid or sample is None or answer.raw_key not in sample.metrics:
                continue
            row.attributes[answer.attribute] = MappedAttribute(
                attribute=answer.attribute, value=sample.metrics[answer.raw_key],
                origin=ORIGIN_CURATOR, raw_key=answer.raw_key,
                source_file=metrics_source, answered_by=answered_by)

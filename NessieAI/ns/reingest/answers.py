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

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from NessieAI.ns.reingest_qa import _value_missing, group_members_for_label

# Keys the row structure owns. Never set from chat, whatever QA says: UID and
# Parent are identity and lineage, and Notes is composed under its own clobber
# guard (reingest_qa NOTES_WOULD_CLOBBER).
NEVER_FILLABLE = frozenset({"UID", "Parent", "Notes"})

_PRIMARY = ("File_PrimaryData", "Checksum_PrimaryData")
_SECONDARY = ("File_SecondaryData", "Checksum_SecondaryData")


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

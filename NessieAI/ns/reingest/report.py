"""Render the QA outcome as something a scientist can act on.

Three rules, each learned from the version that leaked developer output into a
chat window:

* Count, do not enumerate. 24 rows sharing a finding is one sentence; a list
  only when the items genuinely differ.
* Name the measurement, not the key. The raw MultiQC column belongs in the
  Provenance sheet and the approval queue, not here.
* Every flag ends in a choice the reader can make.

The server renders this and the agent relays it VERBATIM. Letting the model
re-summarise makes the wording different every run and loses the calibration.
"""
from __future__ import annotations

from NessieAI.ns import reingest_qa as qa

# Human names for the attributes a reader will actually see flagged.
_FRIENDLY = {
    "ContamPercent": "contamination percentage",
    "MappedPercent": "mapping rate",
    "rRNAPercent": "rRNA content",
    "GenesDetected": "genes detected",
    "Strandedness": "strandedness",
    "DuplicationPercent": "duplication rate",
}

# UNRESOLVED_UID lists affected samples by name; cap it so a genuinely large
# batch doesn't turn into the exact log-in-a-chat-window leak rule 1 forbids.
_MAX_SAMPLES_LISTED = 10


def _name(attribute: str) -> str:
    return _FRIENDLY.get(attribute, attribute)


def _cap(s: str) -> str:
    """Capitalize only the first character. str.capitalize() also lowercases
    the tail, which mangles a curated name like "rRNA content" or a bare
    attribute like "MedianCV" that is already correctly cased."""
    return s[:1].upper() + s[1:] if s else s


def _plural(count: int, word: str = "row") -> str:
    return word if count == 1 else f"{word}s"


def _verb(count: int, plural_form: str, singular_form: str | None = None) -> str:
    """Subject-verb agreement for "{count} row(s) <verb> ...": a plural count
    takes `plural_form` ("name", "claim", "are"), a count of exactly one
    takes `singular_form` (defaults to `plural_form` + "s")."""
    if count == 1:
        return singular_form if singular_form is not None else f"{plural_form}s"
    return plural_form


def _workbook_ref(sample_type: str) -> str:
    return f"the {sample_type} workbook" if sample_type else "this workbook"


def _is_backfill(key: str) -> bool:
    """A backfill/update workbook, by the one naming convention the caller
    guarantees: its artifact key ends in "_update". Used both to sort TO
    UPLOAD (children before the backfill) and to choose its instruction --
    from one place, so the two can never disagree."""
    return key.endswith("_update")


def _resolve_artifact(artifacts: dict, sample_type: str):
    """Match a sample type to its workbook, anchored on the exact key first
    and the "_update" backfill variant second -- never a bare substring test,
    which would let sample type "A.GEX" match artifact key
    "reingest_A.GEXPLUS" before "reingest_A.GEX" depending on dict order."""
    norm = sample_type.replace(".", "_")
    exact = f"reingest_{norm}"
    update = f"{exact}_update"
    for key, path in artifacts.items():
        if key.replace(".", "_") == exact:
            return key, path
    for key, path in artifacts.items():
        if key.replace(".", "_") == update:
            return key, path
    return None, None


def _artifact_name(artifacts: dict, sample_type: str) -> str:
    _, path = _resolve_artifact(artifacts, sample_type)
    return path.rsplit("/", 1)[-1] if path else sample_type


def _children_first(item):
    return (0 if item[0].startswith("A.") else 1, item[0])


def render_qa_for_user(reports, artifacts, run_name) -> str:
    blocked = any(built.disposition == qa.HARD_REJECT for built in reports.values())
    lines: list[str] = []

    if blocked:
        lines.append(f"Reingest blocked — run {run_name}")
    else:
        lines.append(f"Reingest ready — run {run_name}")
    lines.append("")

    # One ordering for both the status list and the findings below it, so a
    # sample type can't appear in a different position in each.
    ordered = sorted(reports.items(), key=_children_first)

    for sample_type, built in ordered:
        mark = {"CLEAN": "OK", "SOFT_FLAG": "!", "HARD_REJECT": "X"}[built.disposition]
        name = _artifact_name(artifacts, sample_type)
        lines.append(f"  {mark}  {name}")
    lines.append("")

    hard_checks: list[tuple] = []
    soft_checks: list[tuple] = []
    for sample_type, built in ordered:
        for (code, attribute), bucket in qa.group(built.findings).items():
            # The Finding itself often leaves sample_type blank for row-level
            # checks (a blank Parent, a duplicate name, ...); the QaReport
            # we're iterating is already scoped to one sample type, which is
            # the reliable source, so it wins over whatever group() carried.
            bucket["sample_type"] = sample_type
            target = hard_checks if bucket["severity"] == qa.HARD else soft_checks
            target.append((code, attribute, bucket))

    if hard_checks:
        lines.append("WHAT IS BLOCKING")
        lines.append("")
        for index, (code, attribute, bucket) in enumerate(hard_checks, start=1):
            lines.extend(_render_one(index, code, attribute, bucket))
            lines.append("")

    if soft_checks:
        header = ("ONE THING TO CHECK" if len(soft_checks) == 1
                   else f"{len(soft_checks)} THINGS TO CHECK")
        lines.append(header)
        lines.append("")
        for index, (code, attribute, bucket) in enumerate(soft_checks, start=1):
            lines.extend(_render_one(index, code, attribute, bucket))
            lines.append("")

    lines.append("TO UPLOAD")
    lines.append("")

    # A workbook whose sample type was hard-rejected is not something to
    # upload -- the message above just said it is blocked.
    uploadable = []
    for sample_type, built in ordered:
        if built.disposition == qa.HARD_REJECT:
            continue
        key, path = _resolve_artifact(artifacts, sample_type)
        if path is None:
            continue
        uploadable.append((key, path))
    uploadable.sort(key=lambda kp: (_is_backfill(kp[0]), kp[0]))

    if uploadable:
        for order, (key, path) in enumerate(uploadable, 1):
            suffix = ('   — tick "update existing samples"' if _is_backfill(key)
                       else "   — normal upload")
            lines.append(f"  {order}.  {path.rsplit('/', 1)[-1]}{suffix}")
    else:
        lines.append("  Nothing to upload yet — fix the blockers above first.")

    return "\n".join(lines)


def _render_one(index, code, attribute, bucket):
    count = bucket["count"]
    detail = bucket["detail"]
    sample_type = bucket.get("sample_type", "")
    tag = f"[{sample_type}] " if sample_type else ""
    rows = _plural(count)

    if code == qa.UNAPPROVED_ATTRIBUTE:
        return [
            f"  {index}.  {tag}{_cap(_name(attribute))} — worth a sanity-check.",
            "",
            f"      I filled this in on {count} sample{'' if count == 1 else 's'}"
            f" for the first time. {detail.get('example', '')}".rstrip(),
            "",
            "      That judgement is mine and nobody has confirmed it. If you know",
            "      what to expect for these samples, that is the number to look at.",
            "",
            "      Upload as-is — the cells are marked in the workbook's Provenance",
            "      sheet, so it stays traceable.",
            "",
            "      Or ask an administrator to confirm it, and it will not be flagged",
            "      again on this run or any future one.",
        ]
    if code == qa.ATTRIBUTE_NOT_DEFINED:
        return [
            f"  {index}.  {tag}{_cap(_name(attribute))} has nowhere to live.",
            "",
            "      There is no matching sample attribute, so I have written it into",
            f"      the Notes of {count} sample{'' if count == 1 else 's'}, tagged with this run.",
            "",
            "      I have told the administrators, who can add it as a real attribute.",
            "      Re-run this reingest once they have, and I will file it properly.",
            "      Existing Notes content is preserved — nothing was overwritten.",
        ]
    if code == qa.UNRESOLVED_UID:
        samples = [s for s in bucket.get("samples", []) if s]
        if not samples:
            single = detail.get("nfcore_sample")
            if single:
                samples = [single]
        shown = samples[:_MAX_SAMPLES_LISTED]
        extra = len(samples) - len(shown)
        body = [
            f"  {index}.  {tag}{count} sample{'' if count == 1 else 's'} could not be matched",
            "      to any sequencing sample in NExtSEEK:",
        ]
        if shown:
            body.append("")
            body.extend(f"          {s}" for s in shown)
            if extra > 0:
                body.append(f"          ...and {extra} more")
        body.extend([
            "",
            "      Most likely they were added to the run by hand after launch.",
            "      Register them first, or tell me to skip them and build the",
            "      backfill for the ones that matched.",
        ])
        return body
    if code == qa.MULTIRUN_NOT_ATTRIBUTABLE:
        return [
            f"  {index}.  {tag}{count} sample{'' if count == 1 else 's'} were sequenced across",
            "      several runs and merged before QC.",
            "",
            "      The pipeline reports one figure for the merged result, and there",
            "      is no honest way to split it across the original samples, so they",
            "      are left out of the backfill. Their analysis outputs are unaffected.",
        ]
    if code == qa.MISSING_REQUIRED:
        return [
            f"  {index}.  {tag}{_cap(_name(attribute))} is required and missing on {count} {rows}.",
            "",
            "      The server will reject these rows. I could not derive the value;",
            "      fill it in, or tell me where to get it.",
        ]
    if code == qa.UNKNOWN_SAMPLETYPE:
        label = sample_type or "This sample type"
        return [
            f"  {index}.  '{label}' is not in NExtSEEK's catalog of sample types.",
            "",
            "      Reingest never creates a sample type on its own. Check the code",
            "      for a typo, or ask an administrator to add it, then re-run.",
        ]
    if code == qa.BLANK_PARENT:
        return [
            f"  {index}.  {count} {rows} in {_workbook_ref(sample_type)}"
            f" {_verb(count, 'name')} no input sample.",
            "",
            "      Every reingested sample is derived from something. Tell me which",
            "      sequencing sample each of these came from, and I will fill it in.",
        ]
    if code == qa.PARENT_UID_NOT_FOUND:
        return [
            f"  {index}.  {count} {rows} in {_workbook_ref(sample_type)}"
            f" {_verb(count, 'name')} an input sample that does not resolve to anything in NExtSEEK.",
            "",
            "      Register that sample first, or correct the identifier, then re-run.",
        ]
    if code == qa.DUPLICATE_NAME:
        return [
            f"  {index}.  {count} {rows} in {_workbook_ref(sample_type)}"
            f" {_verb(count, 'claim')} a name already used elsewhere in this batch.",
            "",
            "      The server uses that name to detect re-uploads, so a duplicate",
            "      inside one batch is not safe. Rename one, or drop the duplicate.",
        ]
    if code == qa.SURPRISE_SENTINEL:
        what = _cap(_name(attribute)) if attribute else "A value"
        return [
            f"  {index}.  {tag}{what} on {count} {rows} still contains a placeholder",
            "      marker (TODO, XXX, TBD, or similar) that was not deliberately",
            "      flagged as a placeholder.",
            "",
            "      Replace it with the real value, or confirm it is intentional and",
            "      it will not be flagged again.",
        ]
    if code == qa.UID_MISSING_IN_UPDATE:
        return [
            f"  {index}.  {count} {rows} in {_workbook_ref(sample_type)}"
            f" {_verb(count, 'name')} no existing sample to update.",
            "",
            "      I could not match these to anything already in NExtSEEK.",
            "      Identify the sample, or leave the row out of this backfill.",
        ]
    if code == qa.UID_PRESENT_IN_NEW:
        return [
            f"  {index}.  {count} {rows} in {_workbook_ref(sample_type)}"
            f" {_verb(count, 'are', 'is')} meant to create new samples but"
            f" already {_verb(count, 'carry')} an existing",
            "      sample's identifier.",
            "",
            "      New samples and updates cannot be mixed in one workbook. Say",
            "      which was intended for these rows.",
        ]
    if code == qa.NOTES_WOULD_CLOBBER:
        reason = detail.get("reason")
        if reason == "existing Notes not fetched":
            why = [
                "      I could not read the current Notes on these samples, so I",
                "      refused to write over text I never saw.",
            ]
        else:
            # "existing text absent", or any other/unknown reason -- treat it
            # the same way: the safest assumption is that writing would lose
            # something, since that is exactly what this guard exists to catch.
            why = [
                "      The Notes I was about to write does not contain what is",
                "      already there, and writing it would destroy that text.",
            ]
        return [
            f"  {index}.  Writing Notes on {count} {rows} in {_workbook_ref(sample_type)}"
            " would destroy text someone already wrote.",
            "",
            *why,
            "",
            "      Nothing was written. Retry once that is fixed, or ask for the",
            "      note to be left alone.",
        ]
    return [
        f"  {index}.  {tag}Something on {count} {rows} needs a look before upload.",
        "",
        f"      This does not have friendly wording yet (internal code: {code}).",
        "      Check the affected rows in the workbook's Provenance sheet before",
        "      deciding whether to upload as-is or ask an administrator.",
    ]

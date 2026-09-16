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


def _name(attribute: str) -> str:
    return _FRIENDLY.get(attribute, attribute)


def render_qa_for_user(reports, artifacts, run_name) -> str:
    blocked = any(r.disposition == qa.HARD_REJECT for r in reports.values())
    lines: list[str] = []

    if blocked:
        lines.append(f"Reingest blocked — run {run_name}")
    else:
        lines.append(f"Reingest ready — run {run_name}")
    lines.append("")

    for sample_type, built in sorted(reports.items(), key=_children_first):
        mark = {"CLEAN": "OK", "SOFT_FLAG": "!", "HARD_REJECT": "X"}[built.disposition]
        name = _artifact_name(artifacts, sample_type)
        lines.append(f"  {mark}  {name}")
    lines.append("")

    checks = [(code, attr, bucket)
              for built in reports.values()
              for (code, attr), bucket in qa.group(built.findings).items()]
    if checks:
        header = "WHAT IS BLOCKING" if blocked else \
                 ("ONE THING TO CHECK" if len(checks) == 1 else
                  f"{len(checks)} THINGS TO CHECK")
        lines.append(header)
        lines.append("")
        for index, (code, attribute, bucket) in enumerate(checks, start=1):
            lines.extend(_render_one(index, code, attribute, bucket))
            lines.append("")

    lines.append("TO UPLOAD")
    lines.append("")
    for order, (key, path) in enumerate(sorted(artifacts.items(), key=_backfill_last), 1):
        suffix = ('   — tick "update existing samples"' if "update" in key
                  else "   — normal upload")
        lines.append(f"  {order}.  {path.rsplit('/', 1)[-1]}{suffix}")
    return "\n".join(lines)


def _render_one(index, code, attribute, bucket):
    count = bucket["count"]
    detail = bucket["detail"]
    if code == qa.UNAPPROVED_ATTRIBUTE:
        return [
            f"  {index}.  {_name(attribute).capitalize()} — worth a sanity-check.",
            "",
            f"      I filled this in on {count} sample{'s' if count != 1 else ''}"
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
            f"  {index}.  {_name(attribute).capitalize()} has nowhere to live.",
            "",
            f"      There is no matching sample attribute, so I have written it into",
            f"      the Notes of {count} sample{'s' if count != 1 else ''}, tagged with this run.",
            "",
            "      I have told the administrators, who can add it as a real attribute.",
            "      Re-run this reingest once they have, and I will file it properly.",
            "      Existing Notes content is preserved — nothing was overwritten.",
        ]
    if code == qa.UNRESOLVED_UID:
        samples = [d for d in bucket.get("samples", []) if d] or [detail.get("nfcore_sample")]
        listed = "\n".join(f"          {s}" for s in samples if s)
        return [
            f"  {index}.  {count} sample{'s' if count != 1 else ''} could not be matched",
            "      to any sequencing sample in NExtSEEK:",
            "",
            listed,
            "",
            "      Most likely they were added to the run by hand after launch.",
            "      Register them first, or tell me to skip them and build the",
            "      backfill for the ones that matched.",
        ]
    if code == qa.MULTIRUN_NOT_ATTRIBUTABLE:
        return [
            f"  {index}.  {count} sample{'s' if count != 1 else ''} were sequenced across",
            "      several runs and merged before QC.",
            "",
            "      The pipeline reports one figure for the merged result, and there",
            "      is no honest way to split it across the original samples, so they",
            "      are left out of the backfill. Their analysis outputs are unaffected.",
        ]
    if code == qa.MISSING_REQUIRED:
        return [
            f"  {index}.  {attribute} is required and missing on {count} row"
            f"{'s' if count != 1 else ''}.",
            "",
            "      The server will reject these rows. I could not derive the value;",
            "      fill it in, or tell me where to get it.",
        ]
    return [f"  {index}.  {code}: {attribute} ({count} row{'s' if count != 1 else ''})"]


def _children_first(item):
    return (0 if item[0].startswith("A.") else 1, item[0])


def _backfill_last(item):
    return (1 if "update" in item[0] else 0, item[0])


def _artifact_name(artifacts, sample_type):
    for key, path in artifacts.items():
        if sample_type.replace(".", "_") in key.replace(".", "_"):
            return path.rsplit("/", 1)[-1]
    return sample_type

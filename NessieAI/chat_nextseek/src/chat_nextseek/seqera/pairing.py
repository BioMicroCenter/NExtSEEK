"""Tumour/normal pairing for nf-core/sarek (spec 2026-10-22-nfcore-dna-pipelines §3).

Tools that need a tumour and its matched normal run only when every patient in the run has
both. A status is confirmed when the sample's own metadata decided it (a decisive param-atlas
verdict) or when the user confirmed the pairing table configure_run showed them.
"""
from __future__ import annotations

#: sarek 3.10.0 tools that need a tumour/normal pair. Their tumour-only modes are not offered.
TUMOUR_TOOLS = frozenset({"ascat", "controlfreec", "msisensorpro", "muse", "mutect2",
                          "sentieon_tnscope"})
_DECIDED = frozenset({"corroborated", "derived_uncorroborated"})
_LABEL = {"0": "normal", "1": "tumour"}


def requested_tumour_tools(tools) -> list[str]:
    asked = {t.strip().lower() for t in str(tools or "").split(",") if t.strip()}
    return sorted(asked & TUMOUR_TOOLS)


def _status(row: dict) -> str:
    value = row.get("status")
    return str(value).strip() if value not in (None, "") else "0"


def somatic_pairing_check(tools, rows: list[dict], evidence: dict, *,
                          confirmed: bool = False) -> str:
    """'' when the run may go ahead, else the plain-text question for the user."""
    wanted = requested_tumour_tools(tools)
    samples: dict[str, tuple[str, str]] = {}
    for row in rows:
        samples.setdefault(str(row.get("sample") or "?"),
                           (str(row.get("patient") or "?"), _status(row)))
    if not wanted and all(status == "0" for _, status in samples.values()):
        return ""
    if wanted:
        by_patient: dict[str, set[str]] = {}
        for patient, status in samples.values():
            by_patient.setdefault(patient, set()).add(status)
        unpaired = sorted(p for p, statuses in by_patient.items() if statuses != {"0", "1"})
        if unpaired:
            return (f"{', '.join(wanted)} need a tumour and a matched normal for every patient, "
                    f"and these patients do not have both: {', '.join(unpaired)}. "
                    "Remove those tools, or add the missing samples.")
    undecided = {s for s in samples
                 if (((evidence or {}).get(s) or {}).get("status") or {}).get("verdict")
                 not in _DECIDED}
    if undecided and not confirmed:
        lines = ["Please confirm which samples are tumour and which are normal; the metadata "
                 "did not decide every one:", "",
                 "| patient | sample | status |", "|---|---|---|"]
        for sample, (patient, status) in sorted(samples.items()):
            note = " (not from metadata)" if sample in undecided else ""
            lines.append(f"| {patient} | {sample} | {_LABEL.get(status, status)}{note} |")
        lines += ["", "A wrong status makes sarek call germline variants as somatic, or the reverse."]
        return "\n".join(lines)
    return ""

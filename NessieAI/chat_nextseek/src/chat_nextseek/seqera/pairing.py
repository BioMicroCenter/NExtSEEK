"""Tumour/normal pairing for nf-core/sarek, and the same confirmation for nf-core/oncoanalyser
(``group_pairing_question``, below).

Tools that need a tumour and its matched normal run only when every patient in the run has
both. A status is confirmed when the sample's own metadata decided it (a decisive param-atlas
verdict) or when the user confirmed the pairing table configure_run showed them.

A status the metadata reads both ways (``conflict``) or not at all (``absent``) is unclear:
the param atlas does not ask about it at write_samplesheet (``confirm_in: pairing_table``),
so this table asks, in every run, even one with no tumour tools. A ``defaulted`` normal is
asked about only when the run has a tumour tool or a tumour row.
"""
from __future__ import annotations

#: sarek 3.10.0 tools that need a tumour/normal pair. Their tumour-only modes are not offered.
TUMOUR_TOOLS = frozenset({"ascat", "controlfreec", "msisensorpro", "muse", "mutect2",
                          "sentieon_tnscope"})
_DECIDED = frozenset({"corroborated", "derived_uncorroborated"})
_UNCLEAR = frozenset({"conflict", "absent"})
_LABEL = {"0": "normal", "1": "tumour"}


def tool_names(tools) -> set[str]:
    """sarek's `tools` (a comma string or a list) as lower-case names, blanks dropped."""
    names = tools if isinstance(tools, (list, tuple, set, frozenset)) else str(tools or "").split(",")
    return {str(t).strip().lower() for t in names if str(t).strip()}


def requested_tumour_tools(tools) -> list[str]:
    return sorted(tool_names(tools) & TUMOUR_TOOLS)


def _note(sample: str, verdict: dict, undecided: set[str]) -> str:
    """What the table says after a sample's status when the metadata did not decide it."""
    if verdict.get("verdict") == "conflict":
        evidence = str(verdict.get("evidence") or "").strip()
        return f" (metadata unclear: {evidence})" if evidence else " (metadata unclear)"
    return " (not from metadata)" if sample in undecided else ""


def _status(row: dict) -> str:
    value = row.get("status")
    return str(value).strip() if value not in (None, "") else "0"


#: What pairing_question returned. Only the confirmation table is something the user can answer
#: with "yes"; the other two are refusals that ask them to change the run.
OK, REFUSAL, TABLE = "ok", "refusal", "table"


def somatic_pairing_check(tools, rows: list[dict], evidence: dict, *,
                          confirmed: bool = False) -> str:
    """'' when the run may go ahead, else the plain-text question for the user."""
    return pairing_question(tools, rows, evidence, confirmed=confirmed)[1]


def pairing_question(tools, rows: list[dict], evidence: dict, *,
                     confirmed: bool = False) -> tuple[str, str]:
    """(kind, text): (OK, "") to go ahead, else (REFUSAL or TABLE, the plain-text question)."""
    wanted = requested_tumour_tools(tools)
    samples: dict[str, tuple[str, str]] = {}
    for row in rows:
        samples.setdefault(str(row.get("sample") or "?"),
                           (str(row.get("patient") or "?"), _status(row)))
    if wanted and not samples:
        return REFUSAL, (f"{', '.join(wanted)} need a tumour and a matched normal for every "
                         "patient, but the samplesheet has no samples. Build the samplesheet first.")
    verdicts = {s: (((evidence or {}).get(s) or {}).get("status") or {}) for s in samples}
    unclear = {s for s, v in verdicts.items() if v.get("verdict") in _UNCLEAR}
    if not wanted and not unclear and all(status == "0" for _, status in samples.values()):
        return OK, ""
    if wanted:
        by_patient: dict[str, set[str]] = {}
        for patient, status in samples.values():
            by_patient.setdefault(patient, set()).add(status)
        unpaired = sorted(p for p, statuses in by_patient.items() if statuses != {"0", "1"})
        if unpaired:
            return REFUSAL, (f"{', '.join(wanted)} need a tumour and a matched normal for every "
                             f"patient, and these patients do not have both: {', '.join(unpaired)}. "
                             "Remove those tools, or add the missing samples.")
    undecided = {s for s, v in verdicts.items() if v.get("verdict") not in _DECIDED}
    if undecided and not confirmed:
        lines = ["Please confirm which samples are tumour and which are normal; the metadata "
                 "did not decide every one:", "",
                 "| patient | sample | status |", "|---|---|---|"]
        for sample, (patient, status) in sorted(samples.items()):
            lines.append(f"| {patient} | {sample} | {_LABEL.get(status, status)}"
                         f"{_note(sample, verdicts[sample], undecided)} |")
        lines += ["", "A wrong status makes sarek call germline variants as somatic, or the reverse."]
        return TABLE, "\n".join(lines)
    return OK, ""


# --- nf-core/oncoanalyser: groups of a tumour, an optional normal and optional tumour RNA -------

#: The (sample_type, sequence_type) slots oncoanalyser 3.0.0 reads per group
#: (lib/Utils.groovy validateInput); donor and tumor_normal rows are not built here.
_GROUP_SLOTS = {("tumor", "dna"), ("tumor", "rna"), ("normal", "dna")}


def _group_refusals(rows: list[dict]) -> list[str]:
    """What oncoanalyser 3.0.0's own input checks would abort on, one line each."""
    problems: list[str] = []
    groups: dict[str, dict] = {}
    for row in rows:
        group = str(row.get("group_id") or "").strip() or "?"
        sample = str(row.get("sample_id") or "").strip() or "?"
        stype = str(row.get("sample_type") or "").strip()
        seq = str(row.get("sequence_type") or "").strip()
        g = groups.setdefault(group, {"subjects": set(), "slots": {}})
        g["subjects"].add(str(row.get("subject_id") or "").strip())
        if stype not in ("tumor", "normal"):
            problems.append(f"{sample}: sample_type is {stype!r}; it must be tumor or normal.")
            continue
        if seq not in ("dna", "rna"):
            problems.append(f"{sample}: sequence_type is {seq!r}; it must be dna or rna.")
            continue
        if (stype, seq) not in _GROUP_SLOTS:
            problems.append(f"{sample}: a normal RNA library is not an oncoanalyser input; "
                            "only tumour RNA is read.")
            continue
        g["slots"].setdefault((stype, seq), set()).add(sample)
    for group, g in sorted(groups.items()):
        if len(g["subjects"]) > 1:
            problems.append(f"group {group}: its rows name more than one subject_id "
                            f"({', '.join(sorted(g['subjects']))}).")
        slots = g["slots"]
        for (stype, seq), samples in sorted(slots.items()):
            if len(samples) > 1:
                problems.append(f"group {group}: more than one {stype} {seq.upper()} sample "
                                f"({', '.join(sorted(samples))}); run each in its own group.")
        if slots and not any(stype == "tumor" for stype, _ in slots):
            problems.append(f"group {group}: no tumour sample; oncoanalyser does not run on a "
                            "normal alone.")
        elif ("normal", "dna") in slots and ("tumor", "dna") not in slots:
            problems.append(f"group {group}: a normal DNA sample needs the tumour DNA it is "
                            "matched to in the same group.")
    return problems


def group_pairing_question(rows: list[dict], evidence: dict, *,
                           confirmed: bool = False) -> tuple[str, str]:
    """oncoanalyser's tumour/normal check on its written (long-format) sheet: (OK, "") to go
    ahead, (REFUSAL, text) for a group the pipeline would abort on, or (TABLE, text) when the
    metadata did not decide some sample's sample_type and the user has not confirmed it."""
    problems = _group_refusals(rows)
    if problems:
        return REFUSAL, "\n".join(
            ["oncoanalyser cannot run these groups as they stand:", ""]
            + [f"- {p}" for p in problems]
            + ["", "Fix sample_type, sequence_type or group_id and rebuild the samplesheet."])
    samples: dict[str, tuple[str, str, str]] = {}
    for row in rows:
        samples.setdefault(str(row.get("sample_id") or "?"),
                           (str(row.get("group_id") or "?"), str(row.get("sequence_type") or ""),
                            str(row.get("sample_type") or "")))
    verdicts = {s: (((evidence or {}).get(s) or {}).get("sample_type") or {}) for s in samples}
    undecided = {s for s, v in verdicts.items() if v.get("verdict") not in _DECIDED}
    if undecided and not confirmed:
        lines = ["Please confirm which samples are tumour and which are normal; the metadata "
                 "did not decide every one:", "",
                 "| group | sample | sequence | type |", "|---|---|---|---|"]
        for sample, (group, seq, stype) in sorted(samples.items(), key=lambda kv: (kv[1][0], kv[0])):
            lines.append(f"| {group} | {sample} | {seq} | {stype}"
                         f"{_note(sample, verdicts[sample], undecided)} |")
        lines += ["", "A wrong type makes oncoanalyser read the normal's germline variants as "
                      "somatic, or subtract the tumour's own mutations."]
        return TABLE, "\n".join(lines)
    return OK, ""

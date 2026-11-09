"""Checks and clean-up for what goes into a public repository package (GEO / SRA / PRIDE).

Deterministic code around the report coder's output, so a prompt slip cannot publish:
- an identifier labelled as an accession that is not one (``SRA run: <lab label>``);
- the lab's internal free-text attributes (``Notes``) copied into public cells;
- date-only values printed with a midnight time.
"""
from __future__ import annotations

import copy
import re
from typing import Any

# The catalog does not mark attributes as internal, so the set lives here, in one place.
INTERNAL_FREE_TEXT_ATTRS = ("Notes",)

_DO_NOT_PUBLISH_RE = re.compile(r"do\s*not\s*publish|don'?t\s*publish|not\s+for\s+publication", re.I)

# label regex -> accession pattern a value under that label must match
_ACCESSION_LABELS = [
    (r"SRA\s+run|run\s+accession|SRA\s+accession", r"[SED]RR\d+"),
    (r"SRA\s+experiment|experiment\s+accession", r"[SED]RX\d+"),
    (r"BioProject", r"PRJ[EDN][A-Z]\d+"),
    (r"BioSample", r"SAM[NED][A-Z]?\d+"),
]
_CLAIMS = [
    (re.compile(rf"(?:;\s*)?\b(?:{label})\s*[:=]\s*([^;\n]*)", re.I), re.compile(rf"(?:{pat})", re.I))
    for label, pat in _ACCESSION_LABELS
]
_MIDNIGHT_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2})[ T]00:00:00(?:\.0+)?(?!\d)")
_EDGE_RE = re.compile(r"^[;,\s]+|[;,\s]+$")
_SHORT_NOTE = 8  # a note shorter than this is removed only when a whole cell equals it
# ponytail: substring match for notes; a note that is a common phrase under 8 chars is only matched whole-cell.


def normalize_dates(obj: Any) -> Any:
    """Copy of ``obj`` with ``YYYY-MM-DD 00:00:00`` turned into ``YYYY-MM-DD`` in every string; real times stay."""
    if isinstance(obj, str):
        return _MIDNIGHT_RE.sub(r"\1", obj)
    if isinstance(obj, dict):
        return {k: normalize_dates(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [normalize_dates(v) for v in obj]
    return obj


def strip_internal_attributes(metadata: Any, *, drop_do_not_publish: bool = False):
    """Return ``(clean_metadata, withheld, notes)``: a copy without the internal free-text attributes, the records whose
    note says not to publish (``{"uid", "name", "note"}``), and every stored note text.
    ``drop_do_not_publish=True`` also removes those records from the copy (the operator's open ruling)."""
    withheld: list[dict] = []
    notes: list[str] = []

    def is_record(x):
        return isinstance(x, dict) and isinstance(x.get("metadata"), dict)

    def note_of(rec) -> str:
        m = rec["metadata"]
        return next((str(m[k]).strip() for k in m if k.lower() in {a.lower() for a in INTERNAL_FREE_TEXT_ATTRS}
                     and m[k] not in (None, "")), "")

    def walk(x):
        if isinstance(x, list):
            out = []
            for item in x:
                if is_record(item):
                    note = note_of(item)
                    if note:
                        notes.append(note)
                        if _DO_NOT_PUBLISH_RE.search(note):
                            m = item["metadata"]
                            withheld.append({"uid": m.get("UID") or item.get("uuid") or item.get("uid"),
                                             "name": m.get("Name"), "note": note})
                            if drop_do_not_publish:
                                continue
                out.append(walk(item))
            return out
        if isinstance(x, dict):
            res = {}
            for k, v in x.items():
                if k == "metadata" and isinstance(v, dict):
                    res[k] = {a: b for a, b in v.items() if a.lower() not in {i.lower() for i in INTERNAL_FREE_TEXT_ATTRS}}
                else:
                    res[k] = walk(v)
            return res
        return copy.deepcopy(x)

    return walk(metadata), withheld, list(dict.fromkeys(notes))


# PLACEHOLDER user-facing wording, pending the operator's yes (see proposals/b3-geo.md).
DNP_WARNING = "{n} libraries are marked do not publish in their lab notes: {names}."


def do_not_publish_warning(withheld: list[dict]) -> str:
    if not withheld:
        return ""
    names = [str(w.get("name") or w.get("uid")) for w in withheld]
    shown = ", ".join(names[:5]) + (f" and {len(names) - 5} more" if len(names) > 5 else "")
    return DNP_WARNING.format(n=len(names), names=shown)


def scrub_public_report(report: Any, notes: list[str]):
    """Post-check on the coder's rows: returns ``(copy, stats)``. In every string cell an accession-labelled value that
    is not an accession is dropped, a stored note is removed, and midnight times are cut to the date."""
    stats = {"claims_dropped": 0, "notes_removed": 0}
    notes = sorted({n.strip() for n in notes or [] if n and n.strip()}, key=len, reverse=True)

    def cell(s: str) -> str:
        orig = s
        for claim, accession in _CLAIMS:
            def repl(m):
                toks = [t for t in re.split(r"[,\s]+", m.group(1).strip()) if t]
                if toks and all(accession.fullmatch(t) for t in toks):
                    return m.group(0)
                stats["claims_dropped"] += 1
                return ""
            s = claim.sub(repl, s)
        for n in notes:
            if s.strip() == n:
                s = ""
                stats["notes_removed"] += 1
            elif len(n) >= _SHORT_NOTE and n in s:
                s = re.sub(r"(?:;\s*)?" + re.escape(n), "", s)
                stats["notes_removed"] += 1
        s = _MIDNIGHT_RE.sub(r"\1", s)
        if s != orig:
            s = _EDGE_RE.sub("", s)
        return s

    def walk(x):
        if isinstance(x, str):
            return cell(x)
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x

    return walk(report), stats

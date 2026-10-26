"""The contrasts file a comparison pipeline needs, written by Nessie from the user's answer.

nf-core/rnasplice will not run without ``--contrasts``: a CSV whose header is
``contrast,treatment,control`` and whose rows name which ``condition`` value is the
treatment and which the control. Which way round a comparison goes is the user's
call, never a default (a swapped contrast flips the sign of every result), so the
value stays a required user param (``required_user_params`` in the pipeline's
template). What changed is the form of the answer: the user states the comparison
("EC vs FB") and Nessie writes the CSV into the run folder, rather than the user
making a file on Luria and handing over its path.

An answer that is an absolute path is still taken as the user's own file on Luria
and passed through untouched.

Accepted answers (a string, or a list of strings, one comparison each):

- ``"EC vs FB"`` -- treatment first, then control; ``vs.`` and ``versus`` also work.
  Several comparisons go on separate lines or are separated by ``;``.
- the CSV itself, pasted: a ``contrast,treatment,control`` header and its rows.
"""
from __future__ import annotations

import csv
import io
import re
from pathlib import Path
from typing import Any

#: The name the written file has, locally beside the samplesheet and in the remote
#: run folder; run.sh starts Nextflow in that folder, so the param can name it bare.
CONTRASTS_FILENAME = "contrasts.csv"

#: The user param this module serves. A pipeline opts in by declaring a
#: ``required_user_params`` entry with this name.
CONTRASTS_PARAM = "contrasts"

_HEADER = ("contrast", "treatment", "control")
_VS_RE = re.compile(r"^\s*(\S+)\s+(?:vs\.?|versus)\s+(\S+)\s*$", re.IGNORECASE)
# rnasplice's samplesheet schema requires a condition with no whitespace (^\S+$);
# the contrast id names output files, so it is held to a filename-safe set.
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def is_user_path(value: Any) -> bool:
    """True when the answer is the user's own contrasts file on Luria."""
    return isinstance(value, str) and value.strip().startswith("/")


def parse_contrasts(value: Any) -> tuple[list[dict[str, str]], list[str]]:
    """The user's answer -> (rows of {contrast, treatment, control}, problems).

    Rows come back only when there are no problems, so a caller never writes half
    a file."""
    if isinstance(value, (list, tuple)):
        lines = [str(v) for v in value]
    else:
        text = str(value or "").strip()
        if text.lower().replace(" ", "").startswith(",".join(_HEADER)):
            return _parse_csv(text)
        lines = re.split(r"[;\n]", text)
    rows: list[dict[str, str]] = []
    errors: list[str] = []
    for line in (ln.strip() for ln in lines):
        if not line:
            continue
        m = _VS_RE.match(line)
        if not m:
            errors.append(f"contrasts: {line!r} is not 'TREATMENT vs CONTROL'")
            continue
        treatment, control = m.group(1), m.group(2)
        rows.append({"contrast": f"{treatment}_vs_{control}",
                     "treatment": treatment, "control": control})
    if not rows and not errors:
        errors.append("contrasts: no comparison given")
    errors += _row_problems(rows)
    return ([] if errors else rows), errors


def _parse_csv(text: str) -> tuple[list[dict[str, str]], list[str]]:
    reader = csv.DictReader(io.StringIO(text))
    names = [(n or "").strip().lower() for n in (reader.fieldnames or [])]
    if tuple(names[:3]) != _HEADER:
        return [], [f"contrasts: the CSV header must start {','.join(_HEADER)}"]
    rows = [{k: (row.get(orig) or "").strip()
             for k, orig in zip(_HEADER, reader.fieldnames[:3])}
            for row in reader if any((row.get(orig) or "").strip() for orig in reader.fieldnames[:3])]
    errors = _row_problems(rows)
    if not rows and not errors:
        errors.append("contrasts: the CSV has a header but no comparison rows")
    return ([] if errors else rows), errors


def _row_problems(rows: list[dict[str, str]]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for field in _HEADER:
            if not _ID_RE.match(row[field]):
                errors.append(f"contrasts: {field} {row[field]!r} must be letters, digits, '_', '.' or '-'")
        if row["treatment"] == row["control"]:
            errors.append(f"contrasts: {row['contrast']!r} compares {row['treatment']!r} with itself")
        if row["contrast"] in seen:
            errors.append(f"contrasts: {row['contrast']!r} is listed twice")
        seen.add(row["contrast"])
    return errors


def samplesheet_conditions(samplesheet: str | Path | None) -> list[str]:
    """The distinct ``condition`` values in the built samplesheet, in first-seen order;
    [] when it is unreadable or has no such column."""
    try:
        with open(samplesheet or "", newline="") as handle:
            values = [(r.get("condition") or "").strip() for r in csv.DictReader(handle)]
    except OSError:
        return []
    return list(dict.fromkeys(v for v in values if v))


def condition_problems(rows: list[dict[str, str]], conditions: list[str]) -> list[str]:
    """A treatment or control that is not a condition in the samplesheet matches no
    samples, and rnasplice would fail on the cluster rather than here."""
    known = set(conditions)
    shown = ", ".join(conditions) or "none"
    return [f"contrasts: {row[side]!r} ({side} of {row['contrast']}) is not a condition "
            f"in the samplesheet (its conditions: {shown})"
            for row in rows for side in ("treatment", "control") if row[side] not in known]


def write_contrasts_csv(rows: list[dict[str, str]], out_dir: str | Path) -> Path:
    """Write the contrasts CSV into out_dir and return its path."""
    path = Path(out_dir) / CONTRASTS_FILENAME
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(_HEADER), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text(buf.getvalue(), encoding="utf-8")
    return path

"""The triage form: the reviewer's analysis that the review page (report.html) renders.

A reviewer used to write `triage.json` free-form, and the page read whatever it found.
Four of the eight triage files written for the runs of 2026-09-22 and 09-23 gave `gaps` as
`{title, body}` and `next` as objects; the page reads a gap as `{id, text}` and a next step
as a string, so those sections rendered "undefined" and "[object Object]". Nothing checked a
verdict entry's keys either, so a mistyped case id silently dropped its verdict.

`validate_triage` fixes the shape with a schema (unknown keys refused, fixed vocabularies),
and `normalize` still reads the older shapes, converting them and saying so. The canonical
file `write_triage` produces is exactly what `build_report.py` and the page consume.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal, Optional, Union

from pydantic import Field

from NessieAI.tests.nessie_tests.output_skill.common import (
    FormError, Strict, atomic_write, dump, read_json, validate,
)

# The six words every review uses (output-skill/SKILL.md "Assign one verdict per case").
Verdict = Literal["pass", "real", "drift", "policy", "masked", "notrun"]
VERDICTS = ("pass", "real", "drift", "policy", "masked", "notrun")
# The tones the page styles (templates/report.html.tpl: .n.<tone>, .find.<severity>).
Tone = Literal["pass", "real", "drift", "policy", "mute"]
Severity = Literal["real", "drift", "policy", "mute"]
Mark = Literal["ok", "fail", "info"]
Cell = Union[str, int, float, bool, None]


class VerdictEntry(Strict):
    verdict: Verdict
    head: str = Field(min_length=1, description="one line: what went wrong, or why it is fine")
    observed: Optional[list[tuple[Cell, Cell, Cell, Mark]]] = Field(
        default=None, description="[field, expected, what came back, ok|fail|info] rows")
    note: Optional[str] = Field(default=None, description="the reasoning and the evidence")
    task: Optional[int] = None


class Finding(Strict):
    severity: Severity = "real"
    title: str = Field(min_length=1)
    body: list[str] = Field(min_length=1, description="paragraphs; inline HTML allowed")
    evidence: Optional[str] = None


class Gap(Strict):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1, description="inline HTML allowed")


class Stat(Strict):
    n: Union[int, float, str]
    label: str
    tone: Optional[Tone] = None


class TriageForm(Strict):
    # The defaults are build_report.py's own, so a minimal triage renders as it always did.
    title: str = Field(default="nessie_tests run review", min_length=1)
    headline: str = ""
    eyebrow: str = ""
    subhead: str = ""
    runline: list[str] = Field(default_factory=list)
    runroot: str = "/app/outputs/<run>"
    reframe: str = ""
    coverage_lede: Optional[str] = None
    graph_limit: Optional[int] = Field(default=None, description="only to review an older run capped at 250")
    notes_id: Optional[str] = Field(default=None, description="browser key for the notes; defaults to the run dir name")
    notes_file: Optional[str] = None
    stats: Optional[list[Stat]] = None
    coverage: Optional[list[tuple[str, int, int]]] = None
    findings: list[Finding] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    next: list[str] = Field(default_factory=list)
    verdicts: dict[str, VerdictEntry] = Field(default_factory=dict)


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _as_text(v) -> str:
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    return str(v if v is not None else "")


def normalize(raw: dict) -> tuple[dict, list[str]]:
    """Read the older shapes the page mis-rendered, converting each and saying so."""
    if not isinstance(raw, dict):
        raise FormError([f"a triage is a JSON object, not a {type(raw).__name__}"])
    data = dict(raw)
    warns: list[str] = []
    gaps = []
    for i, g in enumerate(data.get("gaps") or []):
        if isinstance(g, dict) and "title" in g and "id" not in g:
            gaps.append({"id": _strip_tags(_as_text(g.get("title"))), "text": _as_text(g.get("body"))})
            warns.append(f"gaps[{i}]: {{title, body}} converted to {{id, text}} (the page reads id and text)")
        else:
            gaps.append(g)
    if "gaps" in data:
        data["gaps"] = gaps
    nxt = []
    for i, n in enumerate(data.get("next") or []):
        if isinstance(n, dict):
            head = n.get("id") or n.get("title") or ""
            body = n.get("text") if "text" in n else n.get("body")
            nxt.append(f"<b>{_as_text(head)}</b> {_as_text(body)}".strip())
            warns.append(f"next[{i}]: an object converted to a string (the page reads strings)")
        else:
            nxt.append(n)
    if "next" in data:
        data["next"] = nxt
    finds = []
    for i, f in enumerate(data.get("findings") or []):
        if isinstance(f, dict) and isinstance(f.get("body"), str):
            f = {**f, "body": [f["body"]]}
            warns.append(f"findings[{i}].body: a string wrapped into a list of paragraphs")
        finds.append(f)
    if "findings" in data:
        data["findings"] = finds
    return data, warns


def validate_triage(raw: dict, *, entry_ids=None) -> tuple[dict, list[str]]:
    """The canonical triage dict and the conversion warnings. Raises FormError.

    `entry_ids`: the run's manifest entry ids. A verdict for an id the run does not hold is
    refused: it would never render, and the case it was meant for would fall back to its
    default verdict with nobody noticing.
    """
    data, warns = normalize(raw)
    form = validate(TriageForm, data, "triage")
    if entry_ids is not None:
        stray = sorted(set(form.verdicts) - set(entry_ids))
        if stray:
            raise FormError([f"verdicts name {len(stray)} case(s) the run does not hold: {stray[:10]}. "
                             "Check the ids against manifest.json."])
    return form.model_dump(mode="json", exclude_none=True), warns


def load_triage(path, *, entry_ids=None) -> tuple[dict, list[str]]:
    return validate_triage(read_json(path, "triage"), entry_ids=entry_ids)


def write_triage(form_path, out_path, *, manifest_path=None, force=False) -> list[str]:
    ids = None
    if manifest_path:
        ids = [e["id"] for e in read_json(manifest_path, "manifest").get("entries", [])]
    data, warns = load_triage(form_path, entry_ids=ids)
    atomic_write(out_path, dump(data), force=force)
    return warns


def add_cli(sub) -> None:
    p = sub.add_parser("triage", help="validate a triage form and write the canonical triage.json")
    p.add_argument("--form", required=True, help="the triage you wrote")
    p.add_argument("--manifest", help="the run's manifest.json: every verdict must name one of its cases")
    p.add_argument("--out", help="default: rewrite --form in place")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=_cli)


def _cli(a) -> int:
    out = a.out or a.form
    warns = write_triage(a.form, out, manifest_path=a.manifest, force=a.force or out == a.form)
    for w in warns:
        print(f"CONVERTED: {w}")
    print(f"OK: wrote {Path(out)}")
    return 0

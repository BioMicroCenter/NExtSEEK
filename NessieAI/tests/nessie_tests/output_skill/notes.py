"""The reviewer's notes, folded back into a triage as decisions.

The review page downloads `nessie-notes.json`: `{report, saved_at, overall, cases: {id:
{verdict, family, status, note}}}` (templates/report.html.tpl `saveNotes`). The fold used to be
free-form ("adjust verdicts, notes, findings and rebuild"), so which notes were answered, and how,
was not recorded anywhere the operator could see. Now it is two deterministic steps:

    notes fold  --notes N --triage T --out fold.json    one item per note, decision null
    notes apply --fold fold.json --triage T --out T2    every item decided; writes T2 and FOLD.md

A decision is `keep` (the verdict stands; `reply` says why), `change` (to `new_verdict`), or
`ask` (a question back to the operator, listed first in FOLD.md). Every item needs a `reply`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional, Union

from pydantic import Field, model_validator

from NessieAI.tests.nessie_tests.output_skill.common import (
    FormError, Strict, atomic_write, dump, md, read_json, validate,
)
from NessieAI.tests.nessie_tests.output_skill.triage import Verdict, load_triage, validate_triage


class NoteEntry(Strict):
    verdict: Optional[Verdict] = None
    family: Optional[str] = None
    status: Optional[str] = None
    note: str


class NotesFile(Strict):
    """What the page's Save notes button writes (and its Import button reads)."""

    report: str
    saved_at: str
    overall: str = ""
    cases: dict[str, Union[NoteEntry, str]] = Field(default_factory=dict)


class FoldItem(Strict):
    case_id: str
    note: str
    verdict_at_note: Optional[Verdict] = None
    verdict_now: Optional[Verdict] = None
    has_entry: bool
    decision: Optional[Literal["keep", "change", "ask"]] = None
    new_verdict: Optional[Verdict] = None
    new_head: Optional[str] = None
    reply: Optional[str] = None


class FoldForm(Strict):
    schema_: str = Field(alias="schema")
    notes_file: str
    triage_file: str
    report: str
    saved_at: str
    overall: str = ""
    overall_reply: Optional[str] = None
    items: list[FoldItem]

    @model_validator(mode="after")
    def _decided(self):
        problems = []
        for it in self.items:
            if it.decision is None or not (it.reply or "").strip():
                problems.append(f"{it.case_id}: decide (keep, change or ask) and write a reply")
                continue
            if it.decision == "change":
                if it.new_verdict is None or it.new_verdict == it.verdict_now:
                    problems.append(f"{it.case_id}: 'change' needs a new_verdict other than {it.verdict_now}")
                if not it.has_entry and not it.new_head:
                    problems.append(f"{it.case_id}: the triage has no entry for this case; 'change' needs new_head")
            elif it.new_verdict is not None:
                problems.append(f"{it.case_id}: new_verdict is only for 'change'")
        if self.overall.strip() and not (self.overall_reply or "").strip():
            problems.append("the reviewer wrote an overall note: write overall_reply")
        if problems:
            raise ValueError("; ".join(problems))
        return self


def read_notes(path) -> NotesFile:
    return validate(NotesFile, read_json(path, "notes"), "notes file")


def fold(notes_path, triage_path, out_path, *, force=False) -> dict:
    notes = read_notes(notes_path)
    triage, _ = load_triage(triage_path)
    verdicts = triage.get("verdicts", {})
    items = []
    for cid, entry in notes.cases.items():
        e = entry if isinstance(entry, NoteEntry) else NoteEntry(note=entry)
        if not e.note.strip():
            continue
        items.append({"case_id": cid, "note": e.note, "verdict_at_note": e.verdict,
                      "verdict_now": (verdicts.get(cid) or {}).get("verdict", e.verdict),
                      "has_entry": cid in verdicts, "decision": None, "new_verdict": None,
                      "new_head": None, "reply": None})
    form = {"schema": "nessie-notes-fold/v1", "notes_file": str(notes_path), "triage_file": str(triage_path),
            "report": notes.report, "saved_at": notes.saved_at, "overall": notes.overall,
            "overall_reply": None, "items": items}
    atomic_write(out_path, dump(form), force=force)
    return form


def apply(fold_path, triage_path, out_path, *, summary_path=None, force=False) -> dict:
    f = validate(FoldForm, read_json(fold_path, "fold form"), "fold form")
    triage, _ = load_triage(triage_path)
    verdicts = triage.setdefault("verdicts", {})
    for it in f.items:
        entry = verdicts.get(it.case_id)
        if entry is None:
            if it.decision != "change":
                continue
            entry = verdicts[it.case_id] = {"verdict": it.new_verdict, "head": it.new_head}
        elif it.decision == "change":
            entry["verdict"] = it.new_verdict
            if it.new_head:
                entry["head"] = it.new_head
        stamp = f.saved_at[:10]
        added = f"Reviewer ({stamp}): {it.note.strip()} / Reply ({it.decision}): {it.reply.strip()}"
        entry["note"] = f"{entry['note']}\n\n{added}" if entry.get("note") else added
    data, _ = validate_triage(triage)
    atomic_write(out_path, dump(data), force=force)
    if summary_path:
        atomic_write(summary_path, render_fold_md(f), force=force)
    return data


def render_fold_md(f: FoldForm) -> str:
    L = [f"# Reviewer notes folded: {f.report}", "",
         f"Notes saved {f.saved_at}; {len(f.items)} case note(s)"
         + ("; an overall note" if f.overall.strip() else "") + ".", ""]
    asks = [i for i in f.items if i.decision == "ask"]
    if asks:
        L += ["## Questions back to the reviewer", ""]
        L += [f"- `{i.case_id}`: {i.reply}" for i in asks]
        L.append("")
    if f.overall.strip():
        L += ["## Overall", "", f"Reviewer: {f.overall}", "", f"Reply: {f.overall_reply}", ""]
    L += ["## Case notes", "", "| Case | Reviewer's note | Verdict then | Verdict now | Decision | Reply |",
          "|---|---|---|---|---|---|"]
    for i in f.items:
        now = i.new_verdict if i.decision == "change" else i.verdict_now
        L.append(f"| `{md(i.case_id)}` | {md(i.note)} | {i.verdict_at_note or ''} | {now or ''} | {i.decision} | {md(i.reply)} |")
    return "\n".join(L) + "\n"


def add_cli(sub) -> None:
    p = sub.add_parser("notes", help="fold the reviewer's downloaded notes into a triage")
    s = p.add_subparsers(dest="notes_cmd", required=True)
    a = s.add_parser("check", help="validate a downloaded notes file")
    a.add_argument("--notes", required=True)
    a.set_defaults(func=_check)
    a = s.add_parser("fold", help="write the fold form: one item per note, each decision null")
    a.add_argument("--notes", required=True)
    a.add_argument("--triage", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--force", action="store_true")
    a.set_defaults(func=_fold)
    a = s.add_parser("apply", help="apply a decided fold form: a new triage and FOLD.md")
    a.add_argument("--fold", required=True)
    a.add_argument("--triage", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--summary", help="write the fold as markdown here")
    a.add_argument("--force", action="store_true")
    a.set_defaults(func=_apply)


def _check(a) -> int:
    n = read_notes(a.notes)
    print(f"OK: {len(n.cases)} case note(s), overall {'yes' if n.overall.strip() else 'no'}, saved {n.saved_at}")
    return 0


def _fold(a) -> int:
    form = fold(a.notes, a.triage, a.out, force=a.force)
    print(f"OK: wrote {a.out} with {len(form['items'])} item(s); decide each, then `notes apply`")
    return 0


def _apply(a) -> int:
    apply(a.fold, a.triage, a.out, summary_path=a.summary, force=a.force)
    print(f"OK: wrote {a.out}" + (f" and {a.summary}" if a.summary else "") + "; rebuild the page with it")
    return 0


__all__ = ["NotesFile", "FoldForm", "fold", "apply", "read_notes", "render_fold_md", "FormError"]

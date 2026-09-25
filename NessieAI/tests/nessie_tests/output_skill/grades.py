"""The grades form: a grader's verdict on every case and every turn of a run.

Graders used to write `grades.json` and `GRADES.md` by hand. Two graded runs of 2026-09-25
produced two unrelated shapes under the same file name (one per file, case and turn with
defects, features and totals; the other a launch summary with no turns), so the operator read
a different layout each time and nothing could compare them. This form fixes the layout:

* the grader fills the judgement (verdicts, evidence, defects, features, the lead paragraph);
* `build_grades` checks it (the verdict words, the harness status against the verdict, every
  real or masked case explained by a defect) and computes every count and sum itself;
* `render_grades_md` renders GRADES.md the same way every time, and `to_triage` turns the
  same verdicts into the triage the review page reads, so the page and the report agree.
"""
from __future__ import annotations

import statistics
from collections import Counter
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field, field_validator, model_validator

from NessieAI.tests.nessie_tests.output_skill.common import (
    FormError, Strict, atomic_write, dump, md, read_json, validate,
)
from NessieAI.tests.nessie_tests.output_skill.triage import VERDICTS, Verdict, validate_triage

HarnessStatus = Literal["passed", "failed", "error", "xpass", "no_assertions", "skipped",
                        "known_fail", "not_run"]
DefectClass = Literal["product", "probe", "environment", "none"]
ORDER = ("pass", "real", "masked", "drift", "policy", "notrun")


class Turn(Strict):
    label: str
    task_id: Optional[int] = None
    route: Optional[str] = Field(default=None, description="nextseek_query, container_cc, unrelated ...")
    route_source: Optional[str] = None
    resumed: bool = False
    server_s: Optional[float] = Field(default=None, ge=0)
    cost_usd: Optional[float] = Field(default=None, ge=0, description="null = unpriced (an NS turn)")
    verdict: Verdict
    evidence: str = Field(min_length=3, description="the reply's key line and how it was checked")
    class_: DefectClass = Field(default="none", alias="class")


class Case(Strict):
    id: str
    family: Optional[str] = None
    harness_status: HarnessStatus
    harness_failed: list[str] = Field(default_factory=list, description="the failed criteria, as the manifest names them")
    verdict: Verdict
    class_: DefectClass = Field(alias="class")
    head: str = Field(min_length=3)
    extra: bool = Field(default=False, description="ran without being asked (the auto-run consistency group)")
    note: Optional[str] = None
    turns: list[Turn] = Field(min_length=1)

    @model_validator(mode="after")
    def _words(self):
        tv = {t.verdict for t in self.turns}
        if self.harness_status == "passed" and self.verdict == "real":
            raise ValueError(f"{self.id}: the harness passed it, so a wrong reply is 'masked', not 'real'")
        if self.verdict == "masked" and self.harness_status not in ("passed", "xpass"):
            raise ValueError(f"{self.id}: 'masked' means the harness passed a wrong reply; the harness "
                             f"said {self.harness_status}")
        if self.verdict == "notrun" and self.harness_status not in ("error", "skipped", "not_run"):
            raise ValueError(f"{self.id}: 'notrun' needs a harness status of error, skipped or not_run")
        if self.verdict == "pass" and tv != {"pass"}:
            raise ValueError(f"{self.id}: a 'pass' case cannot hold a turn graded {sorted(tv - {'pass'})}")
        if "real" in tv and self.verdict not in ("real", "masked"):
            raise ValueError(f"{self.id}: a turn is 'real', so the case is 'real' or 'masked'")
        return self


class FileGrades(Strict):
    file: str
    run_dir: Optional[str] = None
    name: str
    harness_line: Optional[str] = None
    cases: list[Case] = Field(min_length=1)


class Defect(Strict):
    id: str = Field(pattern=r"^D\d+$")
    severity: Literal["high", "medium", "low"]
    class_: DefectClass = Field(alias="class")
    area: str
    cases: list[str] = Field(min_length=1)
    tasks: list[int] = Field(default_factory=list)
    summary: str = Field(min_length=5)
    evidence: str = Field(min_length=5)
    expected: str = Field(min_length=1)


class Feature(Strict):
    name: str
    shown: Literal["yes", "partly", "no"]
    evidence: str = Field(min_length=5)


class Meta(Strict):
    title: str = Field(min_length=5)
    instance: Literal["dev", "prod", "local"]
    sha: str = Field(pattern=r"^[0-9a-f]{7,40}$")
    build: str = Field(min_length=3, description="which images were built from which sha")
    graded_at: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}$")
    grader: str
    window_utc: Optional[tuple[str, str]] = None
    basis: str = Field(min_length=10, description="how it was graded: what was read, never the pass rate")


class Cost(Strict):
    budget_usd: Optional[float] = None
    harness_printed: Optional[str] = None
    bedrock_503: Optional[int] = None
    note: Optional[str] = None


class GradesForm(Strict):
    meta: Meta
    verdict_text: str = Field(min_length=40, description="the lead paragraph the operator reads first")
    files: list[FileGrades] = Field(min_length=1)
    defects: list[Defect] = Field(default_factory=list)
    features: list[Feature] = Field(default_factory=list)
    harness_issues: list[str] = Field(default_factory=list)
    cost: Cost = Field(default_factory=Cost)

    @field_validator("defects")
    @classmethod
    def _ids(cls, v):
        ids = [d.id for d in v]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"duplicate defect ids {dup}")
        return v

    @model_validator(mode="after")
    def _cross(self):
        problems = []
        ids = [c.id for f in self.files for c in f.cases]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            problems.append(f"a case appears twice: {dup}")
        tasks = [t.task_id for f in self.files for c in f.cases for t in c.turns if t.task_id is not None]
        tdup = sorted({t for t in tasks if tasks.count(t) > 1})
        if tdup:
            problems.append(f"a task id appears on two turns: {tdup}")
        for d in self.defects:
            unknown = [c for c in d.cases if c not in ids]
            if unknown:
                problems.append(f"{d.id} names cases the form does not hold: {unknown}")
        explained = {c for d in self.defects for c in d.cases}
        for f in self.files:
            for c in f.cases:
                if c.verdict in ("real", "masked") and c.id not in explained:
                    problems.append(f"{c.id} is '{c.verdict}' but no defect names it: add a defect row")
        if problems:
            raise ValueError("; ".join(problems))
        return self


def _is_cc(t: Turn) -> bool:
    return (t.route or "").startswith("container_cc")


def _counts(items) -> dict:
    c = Counter(items)
    return {k: c[k] for k in ORDER if c[k]}


def totals(form: GradesForm) -> dict:
    per_file = {}
    all_asked, all_turns = [], []
    for f in form.files:
        asked = [c for c in f.cases if not c.extra]
        turns = [t for c in f.cases for t in c.turns]
        priced = [t.cost_usd for t in turns if t.cost_usd is not None]
        per_file[f.file] = {
            "cases_asked": len(asked), "cases_extra": len(f.cases) - len(asked),
            "turns": len(turns), "cc_turns": sum(_is_cc(t) for t in turns),
            "ns_turns": sum(not _is_cc(t) for t in turns),
            "verdicts": _counts(c.verdict for c in asked),
            "turn_verdicts": _counts(t.verdict for t in turns),
            "priced_cost_usd": round(sum(priced), 4), "priced_turns": len(priced),
            "unpriced_turns": len(turns) - len(priced),
        }
        all_asked += asked
        all_turns += turns

    def med(xs):
        return round(statistics.median(xs), 1) if xs else None

    priced = [t.cost_usd for t in all_turns if t.cost_usd is not None]
    return {
        "files": per_file,
        "cases_asked": len(all_asked),
        "verdicts": _counts(c.verdict for c in all_asked),
        "turns": len(all_turns),
        "cc_turns": sum(_is_cc(t) for t in all_turns),
        "ns_turns": sum(not _is_cc(t) for t in all_turns),
        "priced_cost_usd": round(sum(priced), 4),
        "priced_turns": len(priced),
        "unpriced_turns": len(all_turns) - len(priced),
        "server_s_median": {"cc": med([t.server_s for t in all_turns if _is_cc(t) and t.server_s is not None]),
                            "ns": med([t.server_s for t in all_turns if not _is_cc(t) and t.server_s is not None])},
        "server_s_max": {"cc": max([t.server_s for t in all_turns if _is_cc(t) and t.server_s is not None], default=None),
                         "ns": max([t.server_s for t in all_turns if not _is_cc(t) and t.server_s is not None], default=None)},
        "defects": _counts_sev(form.defects),
    }


def _counts_sev(defects) -> dict:
    c = Counter(d.severity for d in defects)
    return {k: c[k] for k in ("high", "medium", "low") if c[k]}


def _vline(counts: dict) -> str:
    return ", ".join(f"{counts[k]} {k}" for k in ORDER if k in counts) or "none"


def build_grades(raw: dict) -> dict:
    form = validate(GradesForm, raw, "grades form")
    out = form.model_dump(mode="json", by_alias=True)
    out = {"schema": "nessie-grades/2", **out, "totals": totals(form)}
    return out


def _route(t: dict) -> str:
    r = {"nextseek_query": "NS", "container_cc": "CC"}.get(t.get("route") or "", t.get("route") or "?")
    extra = []
    if t.get("resumed"):
        extra.append("resumed")
    src = f" ({t['route_source']})" if t.get("route_source") else ""
    return f"{r}{', ' + ', '.join(extra) if extra else ''}{src}"


def _cost(v) -> str:
    return "unpriced" if v is None else f"${v:.2f}"


def render_grades_md(g: dict) -> str:
    m, tot = g["meta"], g["totals"]
    L = [f"# {m['title']}", "", "## Verdict", "", g["verdict_text"], "",
         f"Of the {tot['cases_asked']} cases asked: **{_vline(tot['verdicts'])}**. "
         f"{tot['turns']} turns ({tot['cc_turns']} Container-CC, {tot['ns_turns']} NS); "
         f"**${tot['priced_cost_usd']:.2f}** on {tot['priced_turns']} priced turns, "
         f"{tot['unpriced_turns']} unpriced. Median server time: CC {tot['server_s_median']['cc']} s, "
         f"NS {tot['server_s_median']['ns']} s.", "",
         f"How this was graded: {m['basis']}", "",
         f"Instance {m['instance']} at `{m['sha']}` ({m['build']}); graded {m['graded_at']} by {m['grader']}"
         + (f"; Nessie window {m['window_utc'][0]} to {m['window_utc'][1]}." if m.get("window_utc") else "."), "",
         "Verdicts: pass, real (a product defect), masked (the harness passed a wrong reply), drift (the "
         "criterion is wrong, the reply is right), policy (the operator decides), notrun. Class: whose "
         "problem it is (product, probe, environment).", "", "## Tables per file", ""]
    for f in g["files"]:
        ft = tot["files"][f["file"]]
        L += [f"### {f['name']}", "",
              f"`{f['file']}`, {ft['cases_asked']} cases asked"
              + (f" (+{ft['cases_extra']} extra)" if ft["cases_extra"] else "")
              + f" / {ft['turns']} turns: {ft['cc_turns']} CC, {ft['ns_turns']} NS. "
              + (f"Harness: {f['harness_line']}. " if f.get("harness_line") else "")
              + f"Verdicts: {_vline(ft['verdicts'])}. Priced cost ${ft['priced_cost_usd']:.2f}.", "",
              "| Case / turn | Task | Route (source) | Server s | Cost | Verdict | Evidence | Class |",
              "|---|---|---|---|---|---|---|---|"]
        for c in f["cases"]:
            srv = round(sum(t["server_s"] or 0 for t in c["turns"]), 1)
            cost = [t["cost_usd"] for t in c["turns"] if t["cost_usd"] is not None]
            L.append(f"| **{md(c['id'])}**{' (extra)' if c['extra'] else ''} | | | {srv} | "
                     f"{_cost(round(sum(cost), 4)) if cost else 'unpriced'} | **{c['verdict']}** "
                     f"(harness: {c['harness_status']}) | {md(c['head'])} | {c['class']} |")
            for t in c["turns"]:
                L.append(f"| &nbsp;&nbsp;{md(t['label'])} | {t.get('task_id') or ''} | {_route(t)} | "
                         f"{t.get('server_s') if t.get('server_s') is not None else ''} | {_cost(t.get('cost_usd'))} | "
                         f"{t['verdict']} | {md(t['evidence'])} | {t['class']} |")
        L.append("")
    L += ["## Defects", ""]
    if g["defects"]:
        L += ["| Id | Severity | Class | Area | Cases | Tasks | Summary | Expected | Evidence |",
              "|---|---|---|---|---|---|---|---|---|"]
        for d in g["defects"]:
            L.append(f"| {d['id']} | {d['severity']} | {d['class']} | {md(d['area'])} | {', '.join(d['cases'])} "
                     f"| {', '.join(map(str, d['tasks']))} | {md(d['summary'])} | {md(d['expected'])} | {md(d['evidence'])} |")
    else:
        L.append("None.")
    L += ["", "## Features the run was meant to show", ""]
    if g["features"]:
        L += ["| Feature | Shown | Evidence |", "|---|---|---|"]
        L += [f"| {md(x['name'])} | {x['shown']} | {md(x['evidence'])} |" for x in g["features"]]
    else:
        L.append("None listed.")
    L += ["", "## Harness and criteria issues", ""]
    L += [f"- {x}" for x in g["harness_issues"]] or ["- none"]
    c = g["cost"]
    L += ["", "## Totals", "", "| | |", "|---|---|",
          f"| Cases asked | {tot['cases_asked']} ({_vline(tot['verdicts'])}) |",
          f"| Turns | {tot['turns']}: {tot['cc_turns']} CC, {tot['ns_turns']} NS |",
          f"| Priced cost | ${tot['priced_cost_usd']:.2f} on {tot['priced_turns']} turns; {tot['unpriced_turns']} unpriced |",
          f"| Budget | {'$' + format(c['budget_usd'], '.2f') if c.get('budget_usd') is not None else 'not given'} |",
          f"| Harness printed | {md(c.get('harness_printed') or 'not given')} |",
          f"| Bedrock 503s | {c['bedrock_503'] if c.get('bedrock_503') is not None else 'not counted'} |",
          f"| Server s, median (max) | CC {tot['server_s_median']['cc']} ({tot['server_s_max']['cc']}), "
          f"NS {tot['server_s_median']['ns']} ({tot['server_s_max']['ns']}) |",
          f"| Defects | {', '.join(f'{v} {k}' for k, v in tot['defects'].items()) or 'none'} |"]
    if c.get("note"):
        L += ["", c["note"]]
    return "\n".join(L) + "\n"


_SEVERITY = {"product": "real", "probe": "drift", "environment": "policy", "none": "mute"}
_MARK = {"pass": "ok", "real": "fail", "masked": "fail"}


def to_triage(g: dict, *, file: Optional[str] = None) -> dict:
    """The review page's triage for the same verdicts (one file of the run, or all)."""
    files = [f for f in g["files"] if file is None or f["file"] == file]
    if not files:
        raise FormError([f"no file {file!r} in the grades form"])
    ids = {c["id"] for f in files for c in f["cases"]}
    verdicts = {}
    for f in files:
        for c in f["cases"]:
            notes = [c["note"]] if c.get("note") else []
            verdicts[c["id"]] = {
                "verdict": c["verdict"], "head": c["head"],
                "observed": [[f"{t['label']}" + (f" (task {t['task_id']})" if t.get("task_id") else ""),
                              _route(t), t["evidence"], _MARK.get(t["verdict"], "info")] for t in c["turns"]],
                **({"note": " ".join(notes)} if notes else {}),
            }
    m, tot = g["meta"], g["totals"]
    counts = Counter(v["verdict"] for f in files for c in f["cases"] if not c["extra"]
                     for v in [verdicts[c["id"]]])
    triage = {
        "title": m["title"],
        "eyebrow": f"{m['instance']} at {m['sha'][:8]} // graded {m['graded_at']}",
        "headline": f"{sum(counts.values())} cases: {_vline(dict(counts))}",
        "subhead": m["basis"],
        "reframe": g["verdict_text"],
        "runline": [f["file"] for f in files] + [f"${tot['priced_cost_usd']:.2f} priced"],
        "findings": [{"severity": _SEVERITY[d["class"]], "title": f"{d['id']} ({d['severity']}): {d['area']}",
                      "body": [d["summary"], f"Expected: {d['expected']}", f"Evidence: {d['evidence']}"],
                      "evidence": ", ".join(d["cases"]) + (f"; tasks {', '.join(map(str, d['tasks']))}" if d["tasks"] else "")}
                     for d in g["defects"] if set(d["cases"]) & ids],
        "gaps": [{"id": f"issue {i}", "text": x} for i, x in enumerate(g["harness_issues"], 1)],
        "next": [],
        "verdicts": verdicts,
    }
    data, _ = validate_triage(triage)
    return data


def write_grades(form_path, out_dir, *, triage_file=None, force=False) -> dict:
    g = build_grades(read_json(form_path, "grades form"))
    out = Path(out_dir)
    atomic_write(out / "grades.json", dump(g), force=force)
    atomic_write(out / "GRADES.md", render_grades_md(g), force=force)
    if triage_file is not None:
        name = "triage.json" if triage_file == "*" else f"triage-{Path(triage_file).stem}.json"
        atomic_write(out / name, dump(to_triage(g, file=None if triage_file == "*" else triage_file)), force=force)
    return g


def add_cli(sub) -> None:
    p = sub.add_parser("grades", help="validate a grades form; write grades.json, GRADES.md and a triage")
    p.add_argument("--form", required=True)
    p.add_argument("--out-dir", required=True)
    p.add_argument("--triage", nargs="?", const="*", default=None,
                   help="also write the review page's triage: all files, or the one named")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=_cli)


def _cli(a) -> int:
    g = write_grades(a.form, a.out_dir, triage_file=a.triage, force=a.force)
    t = g["totals"]
    print(f"OK: wrote {Path(a.out_dir) / 'grades.json'} and GRADES.md"
          + (" and a triage" if a.triage else ""))
    print(f"CASES: {t['cases_asked']} asked: {_vline(t['verdicts'])}; ${t['priced_cost_usd']:.2f} priced")
    return 0


__all__ = ["GradesForm", "build_grades", "render_grades_md", "to_triage", "write_grades", "VERDICTS"]

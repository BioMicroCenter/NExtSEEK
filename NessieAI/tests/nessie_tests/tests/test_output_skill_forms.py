"""The review forms of the nessie-run-review skill: triage, grades, notes.

Each form is a schema, a validator and a deterministic writer, like the handoff skill's report.
What these tests hold: the shapes the review page reads, the verdict words, the rules that tie a
verdict to the harness status and to a defect, the fold of the reviewer's notes, and that
`build_report.py` refuses a triage it cannot render correctly.
"""
from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path

import pytest

from NessieAI.tests.nessie_tests.output_skill import grades as G
from NessieAI.tests.nessie_tests.output_skill import notes as N
from NessieAI.tests.nessie_tests.output_skill import triage as T
from NessieAI.tests.nessie_tests.output_skill.__main__ import main as forms_main
from NessieAI.tests.nessie_tests.output_skill.common import FormError

HERE = Path(__file__).resolve().parents[1]
SKILL = HERE / "output-skill"
EXAMPLE_TRIAGE = SKILL / "examples" / "triage.json"
EXAMPLE_GRADES = SKILL / "examples" / "grades-form.json"
EXAMPLE_RUN = SKILL / "examples" / "run-2026-07-24"


# --------------------------------------------------------------------------- triage
def test_the_committed_example_triage_is_valid_and_unchanged():
    raw = json.loads(EXAMPLE_TRIAGE.read_text())
    data, warns = T.validate_triage(raw)
    assert warns == []
    assert data["verdicts"] == {k: {kk: vv for kk, vv in v.items() if vv is not None}
                                for k, v in raw["verdicts"].items()}


def test_legacy_gaps_and_next_are_converted_to_what_the_page_reads():
    raw = {"title": "t", "headline": "h",
           "gaps": [{"title": "<b>Needs a re-measure</b>", "body": ["a", "b"]}, {"id": "ok", "text": "fine"}],
           "next": [{"title": "From this run", "body": ["do x"]}, {"id": "later", "text": "y"}, "plain"],
           "findings": [{"severity": "real", "title": "f", "body": "one paragraph"}]}
    data, warns = T.validate_triage(raw)
    assert data["gaps"][0] == {"id": "Needs a re-measure", "text": "a b"}
    assert data["next"] == ["<b>From this run</b> do x", "<b>later</b> y", "plain"]
    assert data["findings"][0]["body"] == ["one paragraph"]
    assert len(warns) == 4


@pytest.mark.parametrize("mutate,needle", [
    (lambda t: t["verdicts"]["x"].update(verdict="bug"), "verdict"),
    (lambda t: t["verdicts"]["x"].update(observed=[["f", "e", "o", "bad"]]), "observed"),
    (lambda t: t["verdicts"]["x"].update(observed=[["f", "e", "o"]]), "observed"),
    (lambda t: t.update(surprise=1), "surprise"),
    (lambda t: t["verdicts"]["x"].update(head=""), "head"),
    (lambda t: t.update(findings=[{"severity": "urgent", "title": "t", "body": ["b"]}]), "severity"),
    (lambda t: t.update(stats=[{"n": 1, "label": "x", "tone": "loud"}]), "tone"),
])
def test_triage_rules(mutate, needle):
    t = {"title": "t", "headline": "h", "verdicts": {"x": {"verdict": "real", "head": "h"}}}
    mutate(t)
    with pytest.raises(FormError) as e:
        T.validate_triage(t)
    assert needle in str(e.value)


def test_a_verdict_for_a_case_the_run_does_not_hold_is_refused():
    t = {"title": "t", "headline": "h", "verdicts": {"graph.tpyo": {"verdict": "real", "head": "h"}}}
    with pytest.raises(FormError) as e:
        T.validate_triage(t, entry_ids=["graph.typo"])
    assert "graph.tpyo" in str(e.value)


def _build_report(tmp_path, triage_path):
    return subprocess.run(
        [sys.executable, str(SKILL / "scripts" / "build_report.py"), "--run", str(EXAMPLE_RUN),
         "--repo", str(HERE.parents[2]), "--triage", str(triage_path), "--out", str(tmp_path / "r.html")],
        capture_output=True, text=True)


def test_build_report_refuses_a_bad_triage_with_exit_2(tmp_path):
    bad = json.loads(EXAMPLE_TRIAGE.read_text())
    first = next(iter(bad["verdicts"]))
    bad["verdicts"][first]["verdict"] = "wrong"
    bad["verdicts"]["no.such.case"] = {"verdict": "real", "head": "x"}
    p = tmp_path / "t.json"
    p.write_text(json.dumps(bad))
    r = _build_report(tmp_path, p)
    assert r.returncode == 2 and "verdict" in r.stderr
    assert not (tmp_path / "r.html").exists()


def test_build_report_still_renders_the_example(tmp_path):
    r = _build_report(tmp_path, EXAMPLE_TRIAGE)
    assert r.returncode == 0, r.stderr
    assert "cases 44" in r.stdout


# --------------------------------------------------------------------------- grades
def example():
    return json.loads(EXAMPLE_GRADES.read_text())


def test_the_example_grades_build_and_count_themselves():
    g = G.build_grades(example())
    t = g["totals"]
    assert g["schema"] == "nessie-grades/2"
    assert t["cases_asked"] == 3                      # the extra consistency group is not "asked"
    assert t["verdicts"] == {"pass": 1, "real": 1, "masked": 1}
    assert t["turns"] == 8 and t["cc_turns"] == 1 and t["ns_turns"] == 7
    assert t["priced_cost_usd"] == 0.25 and t["unpriced_turns"] == 7
    assert t["defects"] == {"high": 1, "medium": 1}


def test_grades_md_is_the_same_every_time():
    g = G.build_grades(example())
    a, b = G.render_grades_md(g), G.render_grades_md(G.build_grades(example()))
    assert a == b
    for section in ("## Verdict", "## Tables per file", "## Defects", "## Features the run was meant to show",
                    "## Harness and criteria issues", "## Totals"):
        assert section in a
    assert "**1 pass, 1 real, 1 masked**" in a


def _case(g, cid):
    return next(c for f in g["files"] for c in f["cases"] if c["id"] == cid)


@pytest.mark.parametrize("mutate,needle", [
    (lambda g: _case(g, "fu.plot_goes_to_cc").update(verdict="real"), "masked"),
    (lambda g: _case(g, "fu.fresh_count_after_follow_up").update(verdict="masked"), "harness passed a wrong reply"),
    (lambda g: _case(g, "fu.fresh_count_after_follow_up").update(verdict="notrun"), "notrun"),
    (lambda g: _case(g, "fu.breakdown_stays_on_ns")["turns"][1].update(verdict="drift"), "cannot hold a turn"),
    (lambda g: _case(g, "fu.fresh_count_after_follow_up").update(verdict="drift"), "the case is 'real' or 'masked'"),
    (lambda g: g.update(defects=[d for d in g["defects"] if d["id"] != "D1"]), "no defect names it"),
    (lambda g: g["defects"][0].update(cases=["no.such"]), "does not hold"),
    (lambda g: _case(g, "fu.plot_goes_to_cc")["turns"][0].update(task_id=101), "task id appears on two turns"),
    (lambda g: g["meta"].update(sha="HEAD"), "sha"),
    (lambda g: _case(g, "fu.plot_goes_to_cc").update(**{"class": "someone"}), "class"),
])
def test_grades_rules(mutate, needle):
    g = example()
    mutate(g)
    with pytest.raises(FormError) as e:
        G.build_grades(g)
    assert needle in str(e.value)


def test_grades_become_the_triage_the_page_reads():
    g = G.build_grades(example())
    t = G.to_triage(g)
    assert set(t["verdicts"]) == {"fu.breakdown_stays_on_ns", "fu.plot_goes_to_cc",
                                  "fu.fresh_count_after_follow_up", "cons.sequencing_engine"}
    assert t["verdicts"]["fu.plot_goes_to_cc"]["observed"][1][3] == "fail"
    assert [f["severity"] for f in t["findings"]] == ["real", "real"]
    T.validate_triage(t)  # the page's own schema


def test_the_grades_cli_writes_three_files(tmp_path, capsys):
    assert forms_main(["grades", "--form", str(EXAMPLE_GRADES), "--out-dir", str(tmp_path), "--triage"]) == 0
    assert {p.name for p in tmp_path.iterdir()} == {"grades.json", "GRADES.md", "triage.json"}
    assert forms_main(["grades", "--form", str(EXAMPLE_GRADES), "--out-dir", str(tmp_path)]) == 3


# --------------------------------------------------------------------------- notes
PAGE_NOTES = {"report": "Nessie grades: example", "saved_at": "2026-01-15T16:11:21.097Z", "overall": "",
              "cases": {"fu.fresh_count_after_follow_up": {"verdict": "real", "family": "search_refinement",
                                                           "status": "failed", "note": "this looks correct??"},
                        "fu.breakdown_stays_on_ns": {"verdict": "pass", "family": "followup_over_results",
                                                     "status": "passed", "note": "any files served?"}}}


@pytest.fixture
def folded(tmp_path):
    (tmp_path / "grades").mkdir()
    G.write_grades(EXAMPLE_GRADES, tmp_path / "grades", triage_file="*")
    notes = tmp_path / "nessie-notes.json"
    notes.write_text(json.dumps(PAGE_NOTES))
    fold_p = tmp_path / "fold.json"
    N.fold(notes, tmp_path / "grades" / "triage.json", fold_p)
    return tmp_path, fold_p


def test_the_page_download_validates_and_other_shapes_do_not(tmp_path):
    p = tmp_path / "n.json"
    p.write_text(json.dumps(PAGE_NOTES))
    assert len(N.read_notes(p).cases) == 2
    p.write_text(json.dumps({"source": "an artifact", "notes": {"x": "y"}}))
    with pytest.raises(FormError):
        N.read_notes(p)


def test_fold_lists_every_note_undecided(folded):
    _, fold_p = folded
    f = json.loads(fold_p.read_text())
    assert [i["case_id"] for i in f["items"]] == list(PAGE_NOTES["cases"])
    assert all(i["decision"] is None for i in f["items"])
    assert f["items"][0]["verdict_now"] == "real" and f["items"][0]["has_entry"]


def test_apply_refuses_until_every_note_is_answered(folded):
    tmp, fold_p = folded
    with pytest.raises(FormError) as e:
        N.apply(fold_p, tmp / "grades" / "triage.json", tmp / "t2.json")
    assert "decide (keep, change or ask)" in str(e.value)


def test_apply_changes_a_verdict_and_records_the_exchange(folded):
    tmp, fold_p = folded
    f = json.loads(fold_p.read_text())
    f["items"][0].update(decision="change", new_verdict="pass",
                         reply="Re-measured: 8 is the broad text match the operator accepts. Now pass.")
    f["items"][1].update(decision="ask", reply="No file was served on this turn; did you expect one?")
    fold_p.write_text(json.dumps(f))
    out = N.apply(fold_p, tmp / "grades" / "triage.json", tmp / "t2.json", summary_path=tmp / "FOLD.md")
    v = out["verdicts"]["fu.fresh_count_after_follow_up"]
    assert v["verdict"] == "pass" and "Reviewer (2026-01-15): this looks correct??" in v["note"]
    md = (tmp / "FOLD.md").read_text()
    assert md.index("## Questions back to the reviewer") < md.index("## Case notes")


@pytest.mark.parametrize("item,needle", [
    ({"decision": "change", "reply": "r"}, "needs a new_verdict"),
    ({"decision": "change", "new_verdict": "real", "reply": "r"}, "other than real"),
    ({"decision": "keep", "new_verdict": "pass", "reply": "r"}, "only for 'change'"),
])
def test_fold_rules(folded, item, needle):
    tmp, fold_p = folded
    f = json.loads(fold_p.read_text())
    f["items"][0].update(item)
    f["items"][1].update(decision="keep", reply="fine")
    fold_p.write_text(json.dumps(f))
    with pytest.raises(FormError) as e:
        N.apply(fold_p, tmp / "grades" / "triage.json", tmp / "t2.json")
    assert needle in str(e.value)

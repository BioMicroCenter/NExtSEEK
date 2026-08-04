"""The split report. Grading is BLIND then revealed.

Showing the LLM's verdict beside the transcript while the human grades inflates
agreement by anchoring, and the disagreement set is the whole reason both graders
exist. So the verdict ships in the page but is not rendered for a row until that
row has a human grade.
"""
import json
import pathlib
import re
import subprocess
import sys

from nessie_tests import bayes_manifest, export

SCRIPTS = pathlib.Path(__file__).resolve().parents[1] / "output-skill-bayesian" / "scripts"


def _build(tmp_path, manifest, llm=None):
    (tmp_path / bayes_manifest.MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
    if llm is not None:
        (tmp_path / "stage_c.json").write_text(json.dumps(llm), encoding="utf-8")
    out = tmp_path / "report_bayes.html"
    subprocess.run([sys.executable, str(SCRIPTS / "build_bayes_report.py"),
                    "--run", str(tmp_path), "--out", str(out)], check=True)
    return out.read_text(encoding="utf-8")


def _literal(html, name):
    m = re.search(rf"const {name}\s*=\s*(\[.*?\]|\{{.*?\}});", html, re.S)
    return json.loads(m.group(1)) if m else None


MANIFEST = {"run_meta": {"mode": "bayesian"}, "pairs": [
    {"id": "a.one", "family": "f", "hibayes_subtype": "Search-Basic",
     "ns": {"id": "a.one", "family": "f", "tier": "full", "status": "passed"},
     "cc": {"id": "a.one", "family": "f", "tier": "full", "status": "failed"}}]}


def test_every_pair_reaches_the_page_with_both_arms(tmp_path):
    pairs = _literal(_build(tmp_path, MANIFEST), "PAIRS")
    assert len(pairs) == 1
    assert pairs[0]["ns"]["status"] == "passed"
    assert pairs[0]["cc"]["status"] == "failed"


def test_the_llm_verdict_is_embedded_but_flagged_blind(tmp_path):
    html = _build(tmp_path, MANIFEST,
                  llm={"a.one::ns": {"outcome": "FullySatisfied", "usefulness_score": 4}})
    assert _literal(html, "LLM")["a.one::ns"]["outcome"] == "FullySatisfied"
    assert "BLIND_UNTIL_GRADED" in html


def test_the_page_never_renders_a_verdict_before_a_grade_server_side(tmp_path):
    """The verdict must reach the reader only through the blind-gated JS path, not
    as static markup a browser paints immediately."""
    html = _build(tmp_path, MANIFEST,
                  llm={"a.one::ns": {"outcome": "FullySatisfied", "usefulness_score": 4}})
    body = html.split("<script", 1)[0]
    assert "FullySatisfied" not in body


def test_grades_autosave_key_is_run_scoped(tmp_path):
    """Two runs graded in the same browser must not share a localStorage bucket."""
    html = _build(tmp_path, MANIFEST)
    assert "nessie-bayes-grades:" in html


def test_the_report_builds_without_stage_c_output(tmp_path):
    """Human grading comes first in the workflow, so the report must be usable
    before the LLM has run at all."""
    html = _build(tmp_path, MANIFEST)
    assert _literal(html, "LLM") == {}


# --------------------------------------------------------------------------- #
# The grades.json contract. Task 5's `merge_grades` reads this file; if the page
# emits a different key form or a different grade vocabulary the two halves of
# the plan do not join, and nothing else in either half would notice.
# --------------------------------------------------------------------------- #

def test_the_grade_key_is_the_stage_b_key_form(tmp_path):
    """`<variant_id>::<arm>`, the SAME key Stage C's `stage_c.json` uses -- and
    pinned against `export.stage_b_query_id` rather than against the literal
    `"::"`, so the separator can only move in one place."""
    c = _literal(_build(tmp_path, MANIFEST), "GRADE_CONTRACT")

    assert c["sep"] == export.STAGE_B_ID_SEP
    assert c["arms"] == list(export.ARMS)
    for arm in c["arms"]:
        assert (MANIFEST["pairs"][0]["id"] + c["sep"] + arm
                == export.stage_b_query_id(MANIFEST["pairs"][0]["id"], arm))


def test_the_grade_vocabulary_is_exactly_pass_and_fail(tmp_path):
    """`merge_grades` treats anything outside `("pass", "fail")` as UNGRADED and
    raises. A third value written by the page would abort the whole merge."""
    c = _literal(_build(tmp_path, MANIFEST), "GRADE_CONTRACT")

    assert c["values"] == ["pass", "fail"]


def test_a_grade_is_timestamped(tmp_path):
    """300 grades is a real human pass and late-pass fatigue is real. The
    timestamp is what makes it measurable after the fact."""
    c = _literal(_build(tmp_path, MANIFEST), "GRADE_CONTRACT")

    assert "ts" in c["fields"]


def test_grading_is_keyboard_driven(tmp_path):
    """By mouse, 300 grades is its own failure mode. The bindings are read from
    this literal by the handler, so an unbound key breaks the page rather than
    quietly disagreeing with the documentation."""
    keys = _literal(_build(tmp_path, MANIFEST), "KEYS")

    assert [keys[k] for k in ("next", "prev", "pass", "fail", "note")] == \
        ["j", "k", "1", "2", "n"]


# --------------------------------------------------------------------------- #
# Excluded arms. Task 3 keeps outages, never-executed and deadline-aborted arms
# out of the runtime CSVs, so Stage C never grades them and `merge_grades` never
# asks for a human grade for them. Grading one is wasted human effort; HIDING one
# makes a pass with 40 unobserved arms look complete.
# --------------------------------------------------------------------------- #

def _excluded_manifest(**arm_over):
    ns = {"id": "a.one", "family": "f", "tier": "full", "status": "error"}
    ns.update(arm_over)
    return {"run_meta": {"mode": "bayesian"}, "pairs": [
        {"id": "a.one", "family": "f", "hibayes_subtype": "Search-Basic", "ns": ns,
         "cc": {"id": "a.one", "family": "f", "tier": "full", "status": "passed"}}]}


def test_an_outage_arm_is_carried_to_the_page_marked_ungradable(tmp_path):
    """Dropping it would make the report show 253 of 254 arms and call the pass
    complete at 253."""
    pairs = _literal(_build(tmp_path, _excluded_manifest(outage=True)), "PAIRS")

    assert pairs[0]["ns"]["excluded"]["cause"] == export.CAUSE_OUTAGE
    assert pairs[0]["ns"]["excluded"]["reason"]
    assert pairs[0]["cc"]["excluded"] is None


def test_a_deadline_aborted_arm_is_excluded_from_the_collected_row(tmp_path):
    """The one exclusion that is invisible in the manifest: the arm's status is
    `failed`, and only the still-non-terminal collected task row shows it never
    answered. Reads the same evidence `export` does."""
    art = tmp_path / "artifacts" / "a.one" / "cc"
    art.mkdir(parents=True)
    (art / "task.json").write_text(json.dumps({"status": "pending", "progress": []}),
                                   encoding="utf-8")
    manifest = json.loads(json.dumps(MANIFEST))

    pairs = _literal(_build(tmp_path, manifest), "PAIRS")

    assert pairs[0]["cc"]["excluded"]["cause"] == export.CAUSE_DEADLINE
    assert pairs[0]["ns"]["excluded"] is None


def test_the_progress_denominator_counts_only_gradable_arms(tmp_path):
    """"127 of 254" against a denominator that includes arms nobody can grade
    reads as an unfinished pass forever."""
    meta = _literal(_build(tmp_path, _excluded_manifest(outage=True)), "META")

    assert meta["arms"] == 2
    assert meta["gradable"] == 1
    assert meta["excluded_by_cause"] == {export.CAUSE_OUTAGE: 1}


# --------------------------------------------------------------------------- #
# The manifest filename collision. A normal run's `manifest.json` validates as an
# EMPTY `BayesManifest` rather than raising; two data-loss defects on this branch
# came from exactly that. A report of nothing must not be one of them.
# --------------------------------------------------------------------------- #

def test_a_run_directory_with_no_paired_manifest_is_refused(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"started_at": "t0", "ended_at": "t1", "tier": "full", "scope": "all",
         "entries": [{"id": "a.one", "family": "f", "tier": "full", "status": "passed"}]}),
        encoding="utf-8")

    proc = subprocess.run([sys.executable, str(SCRIPTS / "build_bayes_report.py"),
                           "--run", str(tmp_path), "--out", str(tmp_path / "r.html")],
                          capture_output=True, text=True)

    assert proc.returncode != 0
    assert bayes_manifest.MANIFEST_NAME in proc.stderr
    assert not (tmp_path / "r.html").exists()


def test_the_builder_names_the_manifest_through_the_constant():
    """A literal `"bayes_manifest.json"` here would survive a rename of the
    constant and read a normal run's manifest as an empty paired one."""
    src = (SCRIPTS / "build_bayes_report.py").read_text(encoding="utf-8")

    assert "bayes_manifest.json" not in src
    assert "MANIFEST_NAME" in src


# --------------------------------------------------------------------------- #
# What a grader actually reads
# --------------------------------------------------------------------------- #

def test_the_answer_under_grade_reaches_the_page(tmp_path):
    """The last turn's reply, from the collected task row. Without it the page is
    a list of ids and the grading pass cannot happen at all."""
    art = tmp_path / "artifacts" / "a.one" / "ns"
    art.mkdir(parents=True)
    (art / "task.json").write_text(json.dumps(
        {"status": "completed", "progress": [], "result": {"reply": "705 mice."}}),
        encoding="utf-8")

    pairs = _literal(_build(tmp_path, MANIFEST), "PAIRS")

    assert pairs[0]["ns"]["reply"] == "705 mice."


def test_the_question_text_reaches_the_page(tmp_path):
    """From the corpus, by variant id. A grader judging an answer needs the
    question, and the manifest does not carry it."""
    corpus = tmp_path / "corpus.json"
    corpus.write_text(json.dumps({"families": {"f": {"variants": [
        {"id": "a.one", "family": "f",
         "turns": [{"label": "main", "query": "How many mice?"}]}]}}}), encoding="utf-8")
    (tmp_path / bayes_manifest.MANIFEST_NAME).write_text(json.dumps(MANIFEST),
                                                         encoding="utf-8")
    out = tmp_path / "r.html"
    subprocess.run([sys.executable, str(SCRIPTS / "build_bayes_report.py"),
                    "--run", str(tmp_path), "--corpus", str(corpus), "--out", str(out)],
                   check=True)

    pairs = _literal(out.read_text(encoding="utf-8"), "PAIRS")

    assert pairs[0]["turns"][0]["query"] == "How many mice?"

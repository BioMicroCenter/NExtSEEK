"""The split report. Grading is BLIND then revealed.

Showing the LLM's verdict beside the transcript while the human grades inflates
agreement by anchoring, and the disagreement set is the whole reason both graders
exist. So the verdict ships in the page but is not rendered for a row until that
row has a human grade.
"""
import csv
import json
import pathlib
import re
import subprocess
import sys

from nessie_tests import bayes_manifest, collect, export

ROOT = pathlib.Path(__file__).resolve().parents[2]
SKILL_DIR = pathlib.Path(__file__).resolve().parents[1] / "output-skill-bayesian"
SCRIPTS = SKILL_DIR / "scripts"


def _build(tmp_path, manifest, llm=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
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


_SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I)


def _outside_scripts(html: str) -> str:
    """The whole document with every `<script>` block removed.

    The blinding guard used to split on the FIRST `<script` and check only what
    came before it, which left everything after `</script>` unexamined -- and a
    browser paints that just as readily. The one guard the blind design has must
    cover the whole document.
    """
    return _SCRIPT_BLOCK.sub("", html)


def _grades_key_expr(html: str) -> str | None:
    """The right-hand side of `const GRADES_KEY = ...;`, verbatim."""
    m = re.search(r"const GRADES_KEY\s*=\s*(.+?);\s*$", html, re.M)
    return m.group(1).strip() if m else None


MANIFEST = {"run_meta": {"mode": "bayesian"}, "pairs": [
    {"id": "a.one", "family": "f", "hibayes_subtype": "Search-Basic",
     "ns": {"id": "a.one", "family": "f", "tier": "full", "status": "passed"},
     "cc": {"id": "a.one", "family": "f", "tier": "full", "status": "failed"}}]}


def _with_run_id(run_id):
    m = json.loads(json.dumps(MANIFEST))
    m["run_meta"]["run_id"] = run_id
    return m


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
    as static markup a browser paints immediately.

    Checked over the WHOLE document outside `<script>` blocks. Checking only what
    precedes the first `<script` -- which this test used to do -- leaves a verdict
    emitted after `</script>` invisible to the guard, and the browser paints that
    one exactly as readily.
    """
    llm = {"a.one::ns": {"outcome": "FullySatisfied", "usefulness_score": 4,
                         "primary_issue": "invented the sample count"}}
    html = _build(tmp_path, MANIFEST, llm=llm)
    static = _outside_scripts(html)

    # The strip removed the scripts and not the page: a guard over an empty string
    # would pass forever.
    assert "<script" in html
    assert "Download grades.json" in static
    for verdict_text in ("FullySatisfied", "invented the sample count"):
        assert verdict_text in html, "the verdict must SHIP in the file"
        assert verdict_text not in static, "...and only ever inside a script block"


def test_grades_autosave_key_is_run_scoped(tmp_path):
    """Two runs graded in the same browser must not share a localStorage bucket.

    Asserting that the PREFIX appears somewhere in the page -- which this test
    used to do -- passes for a hardcoded `"nessie-bayes-grades:shared"`, the exact
    regression the name promises to prevent: two runs would then autosave into one
    bucket and silently overwrite each other's grades. So this pins the key's
    DERIVATION from the run instead of its prefix.
    """
    a = _build(tmp_path / "a", _with_run_id("run-A"))
    b = _build(tmp_path / "b", _with_run_id("run-B"))
    expr = _grades_key_expr(a)

    assert expr is not None, "no `const GRADES_KEY = ...;` in the page at all"
    # Built from the run this page reports on, not a constant.
    assert "META.run_id" in expr
    assert re.fullmatch(r"""["'].*["']""", expr) is None, f"constant key: {expr}"
    # ONE derivation over two runs, and the runs it reads really do differ.
    assert _grades_key_expr(b) == expr
    assert _literal(a, "META")["run_id"] == "run-A"
    assert _literal(b, "META")["run_id"] == "run-B"


def test_the_blind_gate_is_on_and_load_bearing(tmp_path):
    """`BLIND_UNTIL_GRADED` is the one flag that switches the feature off.

    Setting it false fails loudly today -- `verdictHTML` dereferences an undefined
    grade, `render` throws, and the page paints nothing -- but the feature this
    whole report exists for must not rest on a downstream accident, and a later
    null-guard in `verdictHTML` would turn that loud failure into a silent leak.

    THE SENSE OF THE GUARD IS PINNED, NOT ONLY ITS PRESENCE. This test used to
    assert the flag was `true` and that the identifier appeared somewhere in
    `verdictHTML`; both survive dropping the `!`, which turns the gate inside out
    so that the verdict is shown to exactly the rows that have NOT been graded --
    every row, on first load, which is the leak the whole page exists to prevent.
    So the condition is matched literally and the early return is required to
    come BEFORE any use of `LLM`.
    """
    html = _build(tmp_path, MANIFEST)

    assert re.search(r"const BLIND_UNTIL_GRADED\s*=\s*true\s*;", html)
    gate = re.search(r"function verdictHTML\(.*?\n\}", html, re.S)
    assert gate, "verdictHTML is gone"
    body = gate.group(0)

    guard = re.search(
        r"if\s*\(\s*BLIND_UNTIL_GRADED\s*&&\s*!\s*\(\s*g\s*&&\s*"
        r"GRADE_CONTRACT\.values\.includes\(\s*g\.grade\s*\)\s*\)\s*\)\s*"
        r"return\s", body)
    assert guard, ("the ungraded-row guard is not `BLIND_UNTIL_GRADED && "
                   "!(<row is graded>)` followed by a return; its SENSE has "
                   "changed or it is gone:\n" + body)
    # ...and it returns before the verdict is built, not after.
    assert guard.start() < body.index("LLM["), \
        "the guard no longer precedes the only read of the LLM verdicts"


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


def _fn(html, name):
    """One JS function's source, from `function <name>(` to its closing brace."""
    m = re.search(rf"function {name}\(.*?\n\}}", html, re.S)
    return m.group(0) if m else None


def test_a_grade_is_timestamped(tmp_path):
    """300 grades is a real human pass and late-pass fatigue is real. The
    timestamp is what makes it measurable after the fact.

    The declarative `GRADE_CONTRACT.fields` entry is not enough on its own:
    deleting `ts:` from the writer leaves it there, and grades.json then carries
    no timestamp at all while the contract still advertises one. So the WRITER
    is pinned too -- both of them, because a note saved before a grade is a
    record of the pass as much as a grade is.
    """
    html = _build(tmp_path, MANIFEST)
    c = _literal(html, "GRADE_CONTRACT")

    assert "ts" in c["fields"]
    for writer in ("setGrade", "setNote"):
        src = _fn(html, writer)
        assert src, f"{writer} is gone"
        assert re.search(r"\bts:\s*.*new Date\(\)\.toISOString\(\)", src), \
            f"{writer} writes a grade record with no timestamp:\n{src}"


def test_grading_is_keyboard_driven(tmp_path):
    """By mouse, 300 grades is its own failure mode. The bindings are read from
    this literal by the handler, so an unbound key breaks the page rather than
    quietly disagreeing with the documentation.

    The literal alone pins nothing: deleting the whole `keydown` listener leaves
    `KEYS` sitting in the page, the documented shortcuts dead, and this test
    green. So the HANDLER is required, and required to act on every binding.
    """
    html = _build(tmp_path, MANIFEST)
    keys = _literal(html, "KEYS")

    assert [keys[k] for k in ("next", "prev", "pass", "fail", "note")] == \
        ["j", "k", "1", "2", "n"]

    listener = re.search(r'addEventListener\(\s*"keydown".*?\n\}\);', html, re.S)
    assert listener, "the page binds no keydown handler at all"
    for binding in ("next", "prev", "pass", "fail", "note"):
        assert f"KEYS.{binding}" in listener.group(0), \
            f"nothing in the keydown handler reads KEYS.{binding}"


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
    reads as an unfinished pass forever.

    META is not the denominator. The bar reads `SLOTS.length`, which the page
    builds from PAIRS in the browser, so a test that checks only `META.gradable`
    -- as this one used to -- passes while the bar counts all 254. Both are
    pinned: the numbers in META, and the two lines of page code that decide what
    a slot is and what the bar divides by.
    """
    html = _build(tmp_path, _excluded_manifest(outage=True))
    meta = _literal(html, "META")

    assert meta["arms"] == 2
    assert meta["gradable"] == 1
    assert meta["excluded_by_cause"] == {export.CAUSE_OUTAGE: 1}

    # The excluded arm really is in PAIRS, so the SLOTS filter has something to
    # drop -- without this the two assertions below are vacuous.
    pairs = _literal(html, "PAIRS")
    assert pairs[0]["ns"]["excluded"] and not pairs[0]["cc"]["excluded"]

    slots = re.search(r"const SLOTS = \[\];.*?\n\}", html, re.S)
    assert slots and re.search(r"if\(\s*a\s*&&\s*!\s*a\.excluded\s*\)\s*SLOTS\.push",
                               slots.group(0)), \
        f"an excluded arm is no longer kept out of SLOTS:\n{slots and slots.group(0)}"
    assert re.search(r"const n = gradedCount\(\), total = SLOTS\.length;", html), \
        "the progress bar's denominator is no longer the gradable-slot count"


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


# --------------------------------------------------------------------------- #
# The artifacts-tree invariant. `export` and the report builder must read the
# SAME collected tree. The page bands an arm ungradable and strips its grade
# controls using `export._exclusion`, and a deadline abort is visible ONLY in the
# collected task row -- so an export run over a different (or no) tree scores a
# row the page cannot grade, and `merge_grades` then raises `IncompleteGrading`
# instructing the operator to grade it. One derivation, `collect.artifacts_dir`,
# is what makes that impossible rather than merely documented.
# --------------------------------------------------------------------------- #

def _corpus_for(tmp_path, vid="a.one"):
    """`name` and `turns` included: both are required by `e2e.catalog.Variant`,
    which is what `export_stage_b` reads `query_text` through."""
    p = tmp_path / "corpus_for_export.json"
    p.write_text(json.dumps({"version": 2, "families": {"f": {"variants": [
        {"id": vid, "family": "f", "name": vid, "status": "active",
         "is_bayesian": True, "turns": [{"label": "main", "query": "How many mice?"}],
         "hibayes_subtype": "S", "expected_behavior": "AnswerDirectly",
         "artifact_expected": False, "artifact_kind": None}]}},
        "family_defaults": {"f": {}}}), encoding="utf-8")
    return p


def _csv_keys(run, arm):
    with open(run / f"hibayes_eval_rows_{arm}.csv", newline="", encoding="utf-8") as fh:
        return {export.stage_b_query_id(r["query_id"], arm) for r in csv.DictReader(fh)}


def test_the_export_and_the_report_agree_on_which_arms_are_gradable(tmp_path):
    """The demonstrated divergence, as a run: the CC arm blew the deadline, which
    only its still-non-terminal collected row shows. Export the CSVs and build the
    page from the same run directory, and the arms one scores must be exactly the
    arms the other lets a human grade."""
    art = collect.artifacts_dir(tmp_path) / "a.one" / "cc"
    art.mkdir(parents=True)
    (art / "task.json").write_text(json.dumps({"status": "pending", "progress": []}),
                                   encoding="utf-8")
    html = _build(tmp_path, MANIFEST)
    assert export.main(["--run", str(tmp_path),
                        "--corpus", str(_corpus_for(tmp_path))]) == 0

    on_page = {export.stage_b_query_id(p["id"], arm) for p in _literal(html, "PAIRS")
               for arm in export.ARMS if p[arm] and not p[arm]["excluded"]}
    in_csv = _csv_keys(tmp_path, "ns") | _csv_keys(tmp_path, "cc")

    assert in_csv == on_page
    # And the divergence itself: the deadline abort is out on BOTH sides.
    assert "a.one::cc" not in in_csv
    assert "a.one::ns" in in_csv


def test_the_builder_derives_the_collected_tree_rather_than_spelling_it():
    """A second `/ "artifacts"` here is how the two sides come to disagree: the
    predicate is already shared (`export._exclusion`), so the only hole left is
    the INPUT it reads."""
    src = (SCRIPTS / "build_bayes_report.py").read_text(encoding="utf-8")

    assert "collect.artifacts_dir(" in src
    assert '/ "artifacts"' not in src


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


def test_a_failed_follow_up_does_not_show_turn_1s_answer_as_the_final_one(tmp_path):
    """THE bias this fix removes.

    A `refine_and_recall` arm whose follow-up errored is gradable, not excluded:
    `error` is terminal, so no deadline abort, and the manifest entry reads
    `failed`. The page took the last NON-EMPTY reply, so it rendered turn 1's
    answer -- to a DIFFERENT question -- under the heading "Final answer", and a
    grader reading a coherent answer marks it a pass. That inflates
    `human_success` systematically, in the 25-variant family the paired design
    keeps precisely because it is where the two engines differ most.

    The earlier turn is not hidden: it stays in the per-turn trace, beside the
    question it actually answered.
    """
    art = collect.artifacts_dir(tmp_path) / "a.one" / "cc"
    art.mkdir(parents=True)
    (art / "turns.json").write_text(json.dumps([
        {"task_id": "t1", "row": {"status": "completed", "progress": [],
                                  "result": {"reply": "705 mice."}}},
        {"task_id": "t2", "row": {"status": "error", "progress": [], "result": None}},
    ]), encoding="utf-8")

    cc = _literal(_build(tmp_path, MANIFEST), "PAIRS")[0]["cc"]

    assert cc["excluded"] is None, "an errored follow-up is gradable, not excluded"
    assert cc["reply"] == "", "turn 1's answer must not stand in for the final one"
    assert [t["reply"] for t in cc["trace"]] == ["705 mice.", ""]


def test_both_graders_are_shown_the_same_answer(tmp_path):
    """The page's "Final answer" and Stage C's `final_answer` column are the same
    string, because the study IS the comparison of the two verdicts on it. One
    extraction (`export.reply_of`) is what makes that true rather than hoped."""
    art = collect.artifacts_dir(tmp_path) / "a.one" / "ns"
    art.mkdir(parents=True)
    (art / "task.json").write_text(json.dumps(
        {"status": "completed", "progress": [], "result": {"reply": "705 mice."}}),
        encoding="utf-8")
    html = _build(tmp_path, MANIFEST)

    assert export.main(["--run", str(tmp_path),
                        "--corpus", str(_corpus_for(tmp_path))]) == 0

    on_page = _literal(html, "PAIRS")[0]["ns"]["reply"]
    with open(tmp_path / "hibayes_functional_eval_inputs.csv", newline="",
              encoding="utf-8") as fh:
        in_csv = {r["query_id"]: r["final_answer"] for r in csv.DictReader(fh)}
    assert on_page == in_csv["a.one::ns"] == "705 mice."


def test_a_report_built_over_no_collected_tree_says_the_run_is_degraded(tmp_path):
    """The one hole in the refusal chain. `collect` exits 2 and `export` warns in
    detail; the BUILDER -- step 3, immediately before a 254-arm human grading
    pass -- printed "8 arms 8 gradable" and said nothing at all, over a page on
    which every answer is missing and no deadline abort can be seen."""
    (tmp_path / bayes_manifest.MANIFEST_NAME).write_text(json.dumps(MANIFEST),
                                                         encoding="utf-8")
    assert not collect.artifacts_dir(tmp_path).exists()

    proc = subprocess.run([sys.executable, str(SCRIPTS / "build_bayes_report.py"),
                           "--run", str(tmp_path), "--out", str(tmp_path / "r.html")],
                          capture_output=True, text=True, check=True)

    assert str(collect.artifacts_dir(tmp_path)) in proc.stderr
    assert export.CAUSE_DEADLINE in proc.stderr
    assert "DEGRADED" in proc.stderr


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


# --------------------------------------------------------------------------- #
# The runbook itself. A documented step that silently does nothing is worse than
# a missing one, because the operator believes it ran: BOTH `collect.py` and
# `export.py` were printed in SKILL.md as `python -m` commands while having no
# entry point at all, so steps 1 and 2 exited 0, printed nothing and wrote
# nothing -- and the report was then built and graded over an empty tree.
# --------------------------------------------------------------------------- #

def _skill_text():
    return (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")


def test_every_module_command_in_the_runbook_has_an_entry_point():
    """`python -m nessie_tests.X` needs BOTH a `main` and a `__main__` block. A
    module carrying `main` alone still exits 0 and does nothing from a shell,
    which is the exact shape of the defect."""
    named = set(re.findall(r"python -m (nessie_tests\.[A-Za-z_.]+)", _skill_text()))

    assert named, "the runbook names no module commands at all"
    for dotted in sorted(named):
        path = ROOT / (dotted.replace(".", "/") + ".py")
        assert path.is_file(), f"{dotted} names no module"
        src = path.read_text(encoding="utf-8")
        assert re.search(r"^def main\(", src, re.M), f"{dotted} has no main()"
        assert '__name__ == "__main__"' in src, f"{dotted} never calls its main()"


def test_every_script_command_in_the_runbook_exists_and_is_executable():
    named = set(re.findall(r"python (nessie_tests/[\w./-]+\.py)", _skill_text()))

    assert named, "the runbook names no scripts at all"
    for rel in sorted(named):
        path = ROOT / rel
        assert path.is_file(), rel
        assert '__name__ == "__main__"' in path.read_text(encoding="utf-8"), rel


def test_the_runbook_does_not_present_collection_as_a_runnable_command():
    """It is not runnable: `collect.collect` needs a concrete `Sources` and no
    task in this plan builds one. The runbook must say so and name what is
    missing, rather than printing a command that exits 0."""
    text = _skill_text()

    assert "python -m nessie_tests.collect --run" not in text
    assert "not runnable" in text.lower()
    for name in ("Sources", "task_rows", "cc_transcript", "copy_tree"):
        assert name in text


def test_the_runbook_points_at_the_importable_merge_grades():
    """The script is a thin entry point; every decision lives in the package, and
    a Files table naming the script as the logic sends the next reader to edit
    the wrong file."""
    text = _skill_text()

    assert "nessie_tests/output_skill_bayesian/merge_grades.py" in text

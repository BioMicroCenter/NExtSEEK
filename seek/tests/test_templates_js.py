"""The Download Templates picker's inline JavaScript, run under node.

The picker's whole behaviour -- chips, suggestions, the requirement prompt
strip, auto-adding a required parent and cleaning it up again -- lives in one
inline <script> in seek/templates/templatesList.html, and Python tests can only
prove that the string reached the page. Clicking through it by hand missed
three defects that these cases reproduce in milliseconds.

seek/tests/js/harness.js lifts that script block out of the template verbatim
and runs it against a hand-rolled DOM stub (seek/tests/js/dom.js -- jsdom is
not, and should not become, a dependency of this repo). Nothing about the
picker is reimplemented here: edit the template and these tests run the edit.

node is not in the stack image, so this module skips there. Run it directly on
a host that has node:

    node seek/tests/js/cases.js
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CASES = ROOT / "seek" / "tests" / "js" / "cases.js"

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

pytestmark = requires_node


@pytest.fixture(scope="module")
def results():
    """Every case, run in one node process."""
    proc = subprocess.run(
        [NODE, str(CASES)], cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"node failed:\n{proc.stderr}"
    return json.loads(proc.stdout)


def case(results, name):
    """One case's observations, with a crash inside the picker reported as one."""
    out = results[name]
    assert "error" not in out, f"{name} raised inside the picker: {out.get('error')}"
    return out


# --- the harness is really driving the template's own script ---------------

def test_the_harness_runs_the_templates_own_script():
    """If the template stops having exactly one script block, say so loudly."""
    template = (ROOT / "seek" / "templates" / "templatesList.html").read_text()
    assert template.count("<script>") == 1
    proc = subprocess.run(
        [NODE, "-e",
         "process.stdout.write(String(require('./seek/tests/js/harness')"
         ".extractScript(require('fs').readFileSync("
         "'seek/templates/templatesList.html','utf8')).indexOf('renderRequirements')))"],
        cwd=str(ROOT), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert int(proc.stdout) > -1


def test_ticking_a_type_makes_a_chip(results):
    out = case(results, "chips_track_the_selection")
    assert out["selected"] == ["TIS"]
    assert out["chips"] == ["TIS"]
    assert out["count"] == "1"
    assert out["submitDisabled"] is False


def test_the_suggestion_strip_follows_the_children_map(results):
    out = case(results, "suggestions_mirror_the_children_map")
    assert out["hidden"] is False
    assert out["codes"] == ["A.ALN", "CEX", "D.SEQ", "PAV"]


def test_the_search_filters_the_catalog(results):
    assert case(results, "search_filters_the_catalog")["visible"] == ["D.FLOW"]


def test_clear_empties_the_selection_and_the_prompt_strip(results):
    out = case(results, "clear_resets_everything")
    assert out["selected"] == []
    assert out["chips"] == []
    assert out["prompts"]["hidden"] is True
    assert out["submitDisabled"] is True


# --- C1: the outer stack frame must not repaint from a stale `needs` -------

def test_an_auto_added_parents_own_prompt_is_not_wiped(results):
    """Ticking D.SEQ adds DNA, and DNA needs one of BAC/TIS/RNA.

    That prompt is computed by the render() the auto-add re-enters. The frame
    that started the auto-add must not then repaint the strip from the `needs`
    it had built before the recursion -- on the live table this silently wiped
    11 of the 19 single-parent prompts.
    """
    out = case(results, "c1_the_auto_added_parents_own_prompt_survives")
    assert out["selected"] == ["DNA", "D.SEQ"]
    assert out["prompts"]["hidden"] is False
    assert [n["child"] for n in out["prompts"]["needs"]] == ["DNA"]
    assert out["prompts"]["needs"][0]["options"] == ["BAC", "TIS", "RNA"]


def test_the_auto_added_chip_is_badged_with_the_type_that_needs_it(results):
    out = case(results, "c1_the_auto_added_parents_own_prompt_survives")
    dna = next(c for c in out["chips"] if c["code"] == "DNA")
    assert dna["title"] == "required by D.SEQ"
    assert "is-required" in dna["classes"]
    seq = next(c for c in out["chips"] if c["code"] == "D.SEQ")
    assert seq["title"] == "" and "is-required" not in seq["classes"]


def test_a_requirement_satisfied_mid_pass_is_not_advertised_as_unmet(results):
    """"add all" with PAV listed before CEX: PAV's need is computed while NHP
    is unchecked, then CEX's auto-add checks NHP. The strip must not end up
    asking for something the same pass already added."""
    out = case(results, "c1_no_prompt_for_a_requirement_the_same_pass_satisfied")
    assert "NHP" in out["selected"]
    assert out["prompts"]["needs"] == []
    assert out["prompts"]["hidden"] is True


# --- C2: an auto-added chip is removable -----------------------------------

def test_the_chip_x_can_remove_an_auto_added_parent(results):
    """Someone may legitimately upload D.SEQ against DNA already in NExtSEEK.

    The chip goes away and stays away; the strip keeps showing the requirement
    as unmet so the omission is visible rather than silent.
    """
    out = case(results, "c2_the_chip_x_removes_an_auto_added_parent")
    assert out["selected"] == ["D.SEQ"]
    assert out["prompts"]["hidden"] is False
    assert [n["child"] for n in out["prompts"]["needs"]] == ["D.SEQ"]
    assert out["prompts"]["needs"][0]["options"] == ["DNA"]


def test_unticking_an_auto_added_parent_also_sticks(results):
    """The box and the chip are two code paths; only one fires 'change'."""
    out = case(results, "c2_unticking_removes_an_auto_added_parent")
    assert out["selected"] == ["D.SEQ"]
    assert [n["child"] for n in out["prompts"]["needs"]] == ["D.SEQ"]


def test_the_decline_lasts_only_while_the_child_is_selected(results):
    out = case(results, "c2_the_decline_dies_with_the_child")
    assert out["afterDecline"] == ["D.SEQ"]
    assert out["afterRepick"] == ["DNA", "D.SEQ"]


def test_the_prompt_can_put_a_declined_parent_back(results):
    out = case(results, "c2_the_prompt_can_put_a_declined_parent_back")
    assert out["selected"] == ["DNA", "D.SEQ"]
    dna = next(c for c in out["chips"] if c["code"] == "DNA")
    assert dna["title"] == "required by D.SEQ"


# --- I1: termination is structural, not contingent on the data -------------

def test_a_requirement_naming_a_code_with_no_checkbox_does_not_hang(results):
    """load_requirements() drops out-of-catalog parents, but that guard is
    Python and the consumer is JavaScript. check() must terminate on its own:
    an unmatched code used to recurse until the stack blew, killing the whole
    picker IIFE -- no chips, no search, no submit."""
    out = case(results, "i1_a_requirement_naming_an_unknown_code_is_survivable")
    assert out["selected"] == ["D.FLOW"]
    assert out["chips"] == ["D.FLOW"]
    # Unsatisfiable, so it is shown rather than silently dropped.
    assert [n["child"] for n in out["prompts"]["needs"]] == ["D.FLOW"]
    assert out["prompts"]["needs"][0]["options"] == ["NOPE"]
    # ... and the rest of the picker still works.
    assert out["visibleAfterSearch"] == ["TIS"]
    assert out["selectedAfterSearch"] == ["TIS", "D.FLOW"]


# --- I2: cleanup follows the chain and respects other children -------------

def test_dropping_the_head_of_a_chain_takes_the_whole_chain(results):
    """A.ALN -> D.SEQ -> DNA. Unchecking D.SEQ from script fires no 'change',
    so nothing used to clean up the DNA that D.SEQ had itself pulled in --
    leaving a type the user never picked, credited to one that is gone."""
    out = case(results, "i2_unticking_the_head_of_a_chain_takes_the_whole_chain")
    assert out["afterTick"] == ["DNA", "D.SEQ", "A.ALN"]
    assert out["afterUntick"] == []
    assert out["chips"] == []


def test_the_chip_x_follows_the_chain_too(results):
    assert case(results, "i2_the_chip_x_also_follows_the_chain")["selected"] == []


def test_a_shared_parent_stays_while_another_selected_child_needs_it(results):
    """CEX and PAV both want NHP. Dropping CEX must not take PAV's only
    satisfier away with it."""
    out = case(results, "i2_a_shared_parent_survives_while_another_child_needs_it")
    assert out["selected"] == ["NHP", "PAV"]
    assert out["prompts"]["needs"] == []
    nhp = next(c for c in out["chips"] if c["code"] == "NHP")
    assert nhp["title"] == "required by PAV"


def test_a_parent_nothing_still_needs_is_dropped(results):
    assert case(results, "i2_a_parent_no_one_needs_is_dropped")["selected"] == []


def test_a_hand_picked_parent_is_never_cleaned_up(results):
    """Only types the picker added itself are ever removed behind the user."""
    assert case(results, "i2_a_hand_picked_parent_is_never_removed")["selected"] == ["NHP"]

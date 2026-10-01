"""What the Container-CC agent is told about paths, counts and charts (2026-09-25 dev run).

- D7: 6 of 12 CC replies named ``/data/scratch/...`` and one printed the documented
  template ``/dmac/users/<project>/<user>/scratch/<run id>/...`` literally. The agent is
  now told to take the real prefix from ``DMAC_PATH_MAPPINGS`` and never to write a
  placeholder, and the skill's example says each ``<...>`` stands for a real value.
- D5: a reply stated "44 are Macaca fascicularis" (47 is right, and its parts summed to
  55 of 58) after a turn whose only code printed the table. Every stated number must now
  come from code run in the turn, or from a file read, and the parts must add up.
- D6: with no plotting library, chart turns hand-encoded PNGs and one tried
  ``pip install`` twice. The image now has matplotlib, and the agent is told so.

The operator-approved sentences are pinned verbatim, so a later edit is a conscious one.
"""
from __future__ import annotations

import re
import tomllib

from NessieAI import paths
from NessieAI.cc import cc_engine

CLAUDE_MD = paths.CC_RUNTIME_DIR / "container" / "CLAUDE.md"
SKILL_MD = paths.CC_PLUGIN_DIR / "skills" / "nextseek" / "SKILL.md"

PATH_RULE = (
    "The one kind of path you may give is where a file you handed over lives, and only in "
    "its user-facing form: take this turn's real prefix from the `DMAC_PATH_MAPPINGS` "
    "variable (the `nextseek` skill's SKILL.md says how) and put it in place of "
    "`/data/scratch`. Never write a placeholder such as `<project>`, `<user>` or `<run id>` "
    "into a reply: if you have not read the mapping, give the file's name and say it is in "
    "the downloads under your reply. Files you write to `/data/scratch/` are always offered "
    "as downloads under your reply."
)
COUNT_RULE = (
    "- Every number you state must come from code you ran in this turn that printed it (a "
    "count, a sum, a group-by), or be copied from a file you read (such as the count in "
    "`search_details.json`). Never count rows by eye from a printed table, and check that "
    "the parts you state add up to the total you state."
)
CHART_RULE = (
    "- To draw a chart, use matplotlib (it is installed) and save a PNG to `/data/scratch/`. "
    "Nothing else can be installed in this container, so never try `pip install`."
)
GROUP_RULE = (
    "- Report the group counts as the table gives them, and state the total the groups "
    "were taken from."
)
SKILL_EXAMPLE = (
    "`/data/scratch/chart.svg` becomes `/dmac/users/<project>/<user>/scratch/<run id>/chart.svg`, "
    "where each <...> is the real value the variable holds; never copy a <...> into a reply."
)


def _section(text: str, heading: str) -> str:
    match = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match, f"container/CLAUDE.md has no '## {heading}' section"
    return match.group(1)


def _flat(text: str) -> str:
    return " ".join(text.split())


def test_what_the_user_sees_carries_the_path_rule_verbatim():
    section = _section(CLAUDE_MD.read_text(encoding="utf-8"), "What the user sees")
    assert PATH_RULE in section


def test_what_the_user_sees_no_longer_shows_the_template_as_the_answer():
    section = _section(CLAUDE_MD.read_text(encoding="utf-8"), "What the user sees")
    assert "is `/dmac/users/<project>/<user>/scratch/<run id>/chart.svg` to the user" not in section
    assert "are also offered as downloads" not in section


def test_the_path_rule_names_a_variable_the_agent_is_given():
    env = cc_engine.build_agent_environment(source={}, api_user="u", api_pass="p",
                                            path_mappings={})
    assert "DMAC_PATH_MAPPINGS" in env


def test_counts_and_breakdowns_keeps_its_last_rule_and_adds_two_after_it():
    section = _section(CLAUDE_MD.read_text(encoding="utf-8"), "Counts and breakdowns")
    bullets = [line for line in section.splitlines() if line.startswith("- ")]
    assert bullets[-3:] == [GROUP_RULE, COUNT_RULE, CHART_RULE]


def test_the_chart_rule_names_a_library_the_image_installs():
    data = tomllib.loads((paths.CC_RUNTIME_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    extra = data["project"]["optional-dependencies"]["container"]
    names = [re.split(r"[<>=~!\[; ]", spec, maxsplit=1)[0].lower() for spec in extra]
    assert "matplotlib" in names


def test_the_skill_example_says_each_placeholder_is_a_real_value():
    skill = _flat(SKILL_MD.read_text(encoding="utf-8"))
    assert SKILL_EXAMPLE in skill


MODEL_RULE = (
    "- **The model is chosen for you.** Every turn starts on the same Opus model through the "
    "Bedrock proxy; the router does not choose it. If that model is unavailable, the turn can "
    "switch to a second model partway through. Nothing for you to do."
)


def test_how_your_turn_runs_says_the_model_can_switch():
    """--fallback-model can move a turn to a second model partway through, so "The model
    is fixed" stopped being true."""
    section = _section(CLAUDE_MD.read_text(encoding="utf-8"), "How your turn runs")
    assert MODEL_RULE in section.splitlines()
    assert "The model is fixed" not in section


REDACTED_RULE = (
    "In the resumed conversation an earlier tool result can show `<REDACTED>`: NExtSEEK masked it "
    "after that turn ended, and you saw the real value when you ran the command. It is never a "
    "reason to take back what you told the user. A path you gave for a file you handed over stays valid."
)


def test_how_your_turn_runs_says_a_redacted_earlier_result_is_no_reason_to_retract():
    section = _section(CLAUDE_MD.read_text(encoding="utf-8"), "How your turn runs")
    bullet = next(line for line in section.splitlines() if line.startswith("- **Each turn is a new container.**"))
    assert bullet.endswith(
        "The user's answer to a question you ask arrives as the next turn, in a new container that "
        "resumes this conversation. " + REDACTED_RULE
    )


def test_the_skill_says_to_answer_first_and_keep_it_short():
    section = _section(SKILL_MD.read_text(encoding="utf-8"), "Composing the reply")
    flat = _flat(section)
    surface = flat.index("- Surface what the user asked for")
    short = flat.index(
        "- **Answer first, and keep it short.** The first sentence is the answer, or that you cannot do it "
        "and why, in plain words. Say each thing once. No headings, and no table for fewer than four rows. "
        "Aim for ten lines or fewer. Do not describe which calls you made, which failed or which you "
        "retried, unless the answer is incomplete because of it; then say in one sentence what is missing.")
    assert surface < short < flat.index("- Do not fabricate counts")


ANSWER_FIRST_RULE = (
    "**Answer first, and keep it short.** The first sentence is the answer, or that you cannot do it "
    "and why, in plain words. Aim for ten lines or fewer: no headings, no list of the steps you took "
    "or the things you checked, and no table for fewer than four rows. The `nextseek` skill's "
    "\"Composing the reply\" has the rest."
)
MASK_RULE = (
    "NExtSEEK masks values that match a credential; never try to recover or repeat a masked value."
)


def test_what_the_user_sees_ends_with_the_answer_first_rule():
    section = _section(CLAUDE_MD.read_text(encoding="utf-8"), "What the user sees").strip()
    paragraphs = section.split("\n\n")
    assert paragraphs[-1] == ANSWER_FIRST_RULE
    assert paragraphs[-2].startswith("Your reply is read by a researcher")


def test_credentials_ends_with_the_masked_value_rule():
    section = _section(CLAUDE_MD.read_text(encoding="utf-8"), "Credentials").strip()
    assert section.split("\n\n")[-1] == MASK_RULE

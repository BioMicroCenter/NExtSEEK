"""/nextseek slash command content contract (per-op design).

Ported from dmac-assistant tests/unit/test_nextseek_command.py and adapted:
the command routes per-op via the skill, including query/recall roles (spec-001
T11). The load-bearing invariants (allowed-tools == {Bash, Read}, no
AskUserQuestion, $ARGUMENTS, skill reference) are preserved. Markdown-only; no
chat_nextseek import.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMAND_PATH = (
    REPO_ROOT
    / "docker" / "cc-runtime" / "build_context" / "plugins" / "nextseek"
    / "commands" / "nextseek.md"
)


def _read_command() -> str:
    return COMMAND_PATH.read_text(encoding="utf-8")


def _body() -> str:
    text = _read_command()
    return re.sub(r"^---\n.*?\n---\n", "", text, count=1, flags=re.DOTALL)


def test_command_md_exists():
    assert COMMAND_PATH.exists(), f"/nextseek command file missing at {COMMAND_PATH}"


def test_yaml_frontmatter_shape():
    text = _read_command()
    m = re.match(r"^---\n(.*?)\n---\n", text, flags=re.DOTALL)
    assert m, "command file must start with a YAML frontmatter block"
    fm = m.group(1)
    desc_match = re.search(r"^description:\s*(\S.*)$", fm, flags=re.MULTILINE)
    assert desc_match, "frontmatter must declare a non-empty `description:`"
    at_match = re.search(r"^allowed-tools:\s*(.*?)$", fm, flags=re.MULTILINE)
    assert at_match, "frontmatter must declare `allowed-tools:`"
    tools = {t.strip() for t in at_match.group(1).split(",")}
    assert tools == {"Bash", "Read"}, (
        f"allowed-tools must be exactly {{'Bash', 'Read'}}; got {tools}. "
        f"Adding Write/Edit/Task here would expand the command's privilege."
    )


def test_body_references_nextseek_skill_by_name():
    body = _body()
    assert "nextseek" in body
    assert "skill" in body.lower(), (
        "command body must reference the skill it delegates to"
    )


def test_body_routes_per_op_including_query_and_recall():
    body = _body()
    assert "nextseek-parse" in body, (
        "command body must route per-op (e.g. name the parse -> api-read search recipe)"
    )
    assert "nextseek-query" in body
    assert "nextseek-recall" in body
    assert "--turn" in body


def test_body_contains_arguments_placeholder():
    body = _body()
    assert "$ARGUMENTS" in body, (
        "command body must contain the literal $ARGUMENTS placeholder"
    )


def test_body_does_not_invoke_askuserquestion():
    text = _read_command()
    assert "AskUserQuestion" not in text, (
        "/nextseek command must not invoke AskUserQuestion; L3 lives in SKILL.md."
    )

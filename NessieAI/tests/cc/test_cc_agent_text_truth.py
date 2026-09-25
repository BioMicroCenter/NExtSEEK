"""What the Container-CC agent is told about api-write, recall and the schema op matches what they do.

- ``nextseek-api-write`` was described as "Execute a write (POST/PUT/DELETE)", but the REST tool
  behind it refuses every mutating (method, path) pair and returns ok: false, and the 2026-09-24
  ruling is that CC has no write path. The op stays installed; every text now says the server
  refuses writes and to send the user to NExtSEEK itself (routing findings R4).
- ``nextseek-recall`` is steered to the staged rows first, and says it covers graph turns.
- ``nextseek-graph-schema`` was said to return every sample type "with its attributes and their
  stored values". It returns the structure and a type index; attributes, value types and numeric
  and date bounds only for ``--types``; never stored values (schema review flag F12).
"""
from __future__ import annotations

from NessieAI import paths
from NessieAI.cc.op_registry.ops import OPS

CLAUDE_MD = paths.CC_RUNTIME_DIR / "container" / "CLAUDE.md"
SKILL_MD = paths.CC_PLUGIN_DIR / "skills" / "nextseek" / "SKILL.md"
MANIFEST_MD = paths.CC_PLUGIN_DIR / "context" / "MANIFEST.md"


def _flat(path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def _purpose(op_id: str) -> str:
    (op,) = [o for o in OPS if o.op_id == op_id]
    return op.skill_row.purpose


def test_api_write_is_described_as_refused_not_as_a_write():
    purpose = _purpose("api-write")
    assert "Execute a write" not in purpose
    assert "refuses every create, update and delete" in purpose
    assert "NExtSEEK itself" in purpose


def test_the_skill_no_longer_sends_writes_to_api_write():
    skill = _flat(SKILL_MD)
    assert "Execute a write" not in skill
    assert "Every write goes through `nextseek-api-write`" not in skill
    assert "nextseek-api-write --parser-plan '<plan>' --confirmed-write" not in skill
    assert "**Create / update / delete: not from this chat.**" in skill
    assert "`nextseek-parse` → `nextseek-api-write`" not in skill


def test_the_manifest_and_claude_md_say_no_write_reaches_nextseek():
    manifest = _flat(MANIFEST_MD)
    assert "`nextseek-api-read` / `nextseek-api-write`" not in manifest
    assert "not from this chat" in manifest
    claude = _flat(CLAUDE_MD)
    assert "Confirm every write with the user conversationally before executing it" not in claude
    assert "the server refuses every create, update and delete" in claude


def test_recall_points_at_the_staged_rows_and_covers_graph_turns():
    purpose = _purpose("recall")
    assert "graph or REST" in purpose
    assert "/data/previous_turns/turn-NN/rows.csv" in purpose
    assert "—" not in purpose


def test_the_schema_op_is_described_as_it_is():
    manifest = _flat(MANIFEST_MD)
    claude = _flat(CLAUDE_MD)
    for text in (manifest, claude):
        assert "their stored values" not in text and "and stored values" not in text
        assert "never returns stored values" in text
        assert "numeric and date bounds" in text


def test_the_manifest_row_does_not_promise_values_in_its_when_column():
    """Review N6."""
    row = next(line for line in MANIFEST_MD.read_text(encoding="utf-8").splitlines()
               if "not a file: run `nextseek-graph-schema`" in line)
    assert "or the values it holds" not in row

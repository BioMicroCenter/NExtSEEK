"""Drift guards binding the issue-conventions surfaces to scripts/validate_issue.py.

Surfaces: docs/ISSUE-CONVENTIONS.md (this task), .github Issue Form (Task 3),
scripts/seed_issue_labels.sh (Task 4), .claude/skills/nextseek-issues (Task 5).
Hermetic: file reads only; runs in the repo-mounted container lane.
"""
import importlib.util
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "validate_issue", REPO_ROOT / "scripts" / "validate_issue.py"
)
vi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vi)

CONVENTIONS = REPO_ROOT / "docs" / "ISSUE-CONVENTIONS.md"


class TestConventionsDoc:
    def _text(self):
        assert CONVENTIONS.is_file(), "docs/ISSUE-CONVENTIONS.md missing"
        return CONVENTIONS.read_text(encoding="utf-8")

    def test_type_table_matches_enum(self):
        text = self._text()
        documented = set(re.findall(r"^\| `type: ([a-z-]+)`", text, re.MULTILINE))
        assert documented == set(vi.ISSUE_TYPES)

    def test_seeded_areas_match(self):
        text = self._text()
        documented = set(re.findall(r"^\| `area: ([a-z0-9_-]+)`", text, re.MULTILINE))
        assert documented == set(vi.SEEDED_AREAS)

    def test_sentinel_verbatim(self):
        assert vi.ROOT_CAUSE_SENTINEL in self._text()

    def test_epistemic_tags_documented(self):
        text = self._text()
        for tag in vi.EPISTEMIC_TAGS:
            assert f"`{tag}`" in text, tag

    def test_worked_example_present_and_valid(self):
        text = self._text()
        m = re.search(r"```markdown\n(---\n.*?)\n```", text, re.DOTALL)
        assert m, "worked example fenced block missing"
        vi.parse_draft(m.group(1))  # must be validator-clean

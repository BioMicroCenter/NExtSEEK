"""Drift guards binding the issue-conventions surfaces to scripts/validate_issue.py.

Surfaces: docs/ISSUE-CONVENTIONS.md (this task), .github Issue Form (Task 3),
scripts/seed_issue_labels.sh (Task 4), .claude/skills/nextseek-issues (Task 5).
Hermetic: file reads only; runs in the repo-mounted container lane.
"""
import importlib.util
import re
from pathlib import Path

import yaml  # add to module imports

REPO_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "validate_issue", REPO_ROOT / "scripts" / "validate_issue.py"
)
vi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vi)

CONVENTIONS = REPO_ROOT / "docs" / "ISSUE-CONVENTIONS.md"
FORM = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "structured-issue.yml"
FORM_CONFIG = REPO_ROOT / ".github" / "ISSUE_TEMPLATE" / "config.yml"


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


class TestIssueForm:
    def _form(self):
        assert FORM.is_file(), ".github/ISSUE_TEMPLATE/structured-issue.yml missing"
        return yaml.safe_load(FORM.read_text(encoding="utf-8"))

    def _field(self, form, fid):
        for item in form["body"]:
            if item.get("id") == fid:
                return item
        raise AssertionError(f"form field id={fid} missing")

    def test_type_dropdown_matches_enum(self):
        assert tuple(self._field(self._form(), "type")["attributes"]["options"]) == vi.ISSUE_TYPES

    def test_priority_dropdown_matches(self):
        assert tuple(self._field(self._form(), "priority")["attributes"]["options"]) == vi.PRIORITIES

    def test_required_fields(self):
        form = self._form()
        for fid in ("type", "areas", "summary", "evidence", "impact",
                    "root_cause", "verification_recipe", "provenance"):
            assert self._field(form, fid).get("validations", {}).get("required"), fid

    def test_root_cause_prefilled_with_sentinel(self):
        assert self._field(self._form(), "root_cause")["attributes"]["value"] == vi.ROOT_CAUSE_SENTINEL

    def test_areas_placeholder_shows_seeded_examples(self):
        ph = self._field(self._form(), "areas")["attributes"]["placeholder"]
        assert "cc_assistant" in ph and "sample-search" in ph

    def test_blank_issues_disabled(self):
        cfg = yaml.safe_load(FORM_CONFIG.read_text(encoding="utf-8"))
        assert cfg["blank_issues_enabled"] is False

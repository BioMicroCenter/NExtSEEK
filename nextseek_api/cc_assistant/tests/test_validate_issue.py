"""Unit tests for scripts/validate_issue.py (issue-conventions validator).

Hermetic: file reads + in-memory strings only. Runs in the repo-mounted
container lane (pydantic + PyYAML come from the app env).
"""
import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "validate_issue", REPO_ROOT / "scripts" / "validate_issue.py"
)
vi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(vi)

WORKED_EXAMPLE = '''---
title: "ChatSession order_by('-updated_at') sites fetch multi-MB results_history and can hit MySQL 1038 'Out of sort memory'"
type: bug
areas: [cc_assistant, nextseek_api]
priority: medium
---

## Summary
Three call sites order the user's full ChatSession set with every column selected,
including the multi-MB `results_history` JSONField.

## Evidence
- reproduced-live (sibling site): OperationalError 1038 killed a live cc-assistant turn
  (2026-07-22); traceback landed on `services/cc_assistant.py:140`.
- code-reading: unprotected sites `services/cc_assistant.py:201`, `services/assistant.py:694`,
  `services/assistant.py:821`.

## Impact
Any chat user with a large session history can have requests fail mid-turn (500)
nondeterministically.

## Root cause
Full-row ORDER BY over multi-MB JSON columns exceeds sort_buffer_size under rowid
sort. Established via the fixed sibling (services/assistant.py:439-446) and the live
1038 traceback.

## Suggested fix direction
Non-binding: apply the existing two-step PK-lookup pattern or `.defer("results_history")`.

## Verification recipe
Confirm-present: `grep -n 'order_by' nextseek_api/services/cc_assistant.py` shows the
cited sites selecting all columns. Confirm-fixed: same grep shows defer/two-step.

## Provenance
outstanding-items id `chatsession-orderby-1038-sibling-sites` (2026-07-23); related fix
commit 2f942f2 (_session_metas defer).
'''


def _mutate(src: str, old: str, new: str) -> str:
    assert old in src, f"test fixture drift: {old!r} not in draft"
    return src.replace(old, new)


class TestConstants:
    def test_nine_types(self):
        assert len(vi.ISSUE_TYPES) == 9
        assert vi.ISSUE_TYPES[0] == "bug" and "design-question" in vi.ISSUE_TYPES

    def test_fifteen_seeded_areas(self):
        assert len(vi.SEEDED_AREAS) == 15
        for a in vi.SEEDED_AREAS:
            assert vi.AREA_RE.fullmatch(a), a

    def test_sentinel_exact(self):
        assert vi.ROOT_CAUSE_SENTINEL == "Not established — do not guess."


class TestAccepts:
    def test_worked_example_valid(self):
        d = vi.parse_draft(WORKED_EXAMPLE)
        assert d.type == "bug" and d.areas == ["cc_assistant", "nextseek_api"]

    def test_sentinel_root_cause_accepted(self):
        src = WORKED_EXAMPLE.replace(
            "Full-row ORDER BY over multi-MB JSON columns exceeds sort_buffer_size under rowid\nsort. Established via the fixed sibling (services/assistant.py:439-446) and the live\n1038 traceback.",
            vi.ROOT_CAUSE_SENTINEL,
        )
        assert vi.parse_draft(src).root_cause.strip() == vi.ROOT_CAUSE_SENTINEL

    def test_triple_hash_headings_accepted(self):
        src = WORKED_EXAMPLE.replace("\n## ", "\n### ")
        assert vi.parse_draft(src).summary

    def test_optional_section_omittable(self):
        src = WORKED_EXAMPLE.replace(
            "## Suggested fix direction\nNon-binding: apply the existing two-step PK-lookup pattern or `.defer(\"results_history\")`.\n\n",
            "",
        )
        assert vi.parse_draft(src).fix_direction is None

    def test_labels_for(self):
        d = vi.parse_draft(WORKED_EXAMPLE)
        assert vi.labels_for(d) == ["type: bug", "area: cc_assistant", "area: nextseek_api", "priority: medium"]


class TestRejects:
    def test_missing_required_section(self):
        src = WORKED_EXAMPLE.replace("## Impact", "## Impact-Renamed")
        with pytest.raises(Exception, match="[Ii]mpact"):
            vi.parse_draft(src)

    def test_empty_root_cause(self):
        src = WORKED_EXAMPLE.replace(
            "Full-row ORDER BY over multi-MB JSON columns exceeds sort_buffer_size under rowid\nsort. Established via the fixed sibling (services/assistant.py:439-446) and the live\n1038 traceback.",
            "",
        )
        with pytest.raises(Exception, match="Root cause|root_cause"):
            vi.parse_draft(src)

    def test_evidence_without_epistemic_tag(self):
        src = WORKED_EXAMPLE.replace("reproduced-live", "observed").replace("code-reading", "seen")
        with pytest.raises(Exception, match="epistemic"):
            vi.parse_draft(src)

    def test_action_first_title(self):
        src = _mutate(src=WORKED_EXAMPLE, old='title: "ChatSession order_by', new='title: "Fix ChatSession order_by')
        with pytest.raises(Exception, match="behavior"):
            vi.parse_draft(src)

    def test_bad_area_charset(self):
        src = _mutate(WORKED_EXAMPLE, "areas: [cc_assistant, nextseek_api]", "areas: [Sample Search]")
        with pytest.raises(Exception):
            vi.parse_draft(src)

    def test_zero_areas(self):
        src = _mutate(WORKED_EXAMPLE, "areas: [cc_assistant, nextseek_api]", "areas: []")
        with pytest.raises(Exception):
            vi.parse_draft(src)

    def test_unknown_type(self):
        src = _mutate(WORKED_EXAMPLE, "type: bug", "type: feature")
        with pytest.raises(Exception):
            vi.parse_draft(src)

    def test_missing_frontmatter(self):
        with pytest.raises(vi.DraftError):
            vi.parse_draft("## Summary\nno frontmatter here")


class TestSecretScan:
    def test_planted_github_token(self):
        hits = vi.scan_secrets(WORKED_EXAMPLE + "\nleak: ghp_" + "a1B2" * 6)
        assert hits and "GitHub" in hits[0]

    def test_env_var_name_alone_is_fine(self):
        assert vi.scan_secrets("the key name AWS_BEARER_TOKEN_BEDROCK must be set (value not shown)") == []

    def test_clean_example(self):
        assert vi.scan_secrets(WORKED_EXAMPLE) == []


class TestCli:
    def test_cli_valid_and_invalid(self, tmp_path):
        good = tmp_path / "good.md"
        good.write_text(WORKED_EXAMPLE, encoding="utf-8")
        assert vi.main([str(good)]) == 0
        bad = tmp_path / "bad.md"
        bad.write_text(_mutate(WORKED_EXAMPLE, "type: bug", "type: feature"), encoding="utf-8")
        assert vi.main([str(bad)]) == 1
        assert vi.main([str(tmp_path / "missing.md")]) == 2

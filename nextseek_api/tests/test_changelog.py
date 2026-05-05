"""CHANGELOG.md existence + content fixture tests."""
from pathlib import Path

import pytest

# nextseek_api/tests/test_changelog.py → parents[1] → nextseek_api/ root
# AMD-03: CHANGELOG relocated inside nextseek_api/ to stay in permission scope.
# Note: spec Section 5.2 wrote `parents[2]`, but parents[0]=tests/, parents[1]=nextseek_api/,
# parents[2]=repo root. parents[1] is the correct level for the AMD-03 location.
APP_ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = APP_ROOT / "CHANGELOG.md"


def test_changelog_exists_at_app_root():
    assert CHANGELOG.exists(), f"CHANGELOG.md missing at {CHANGELOG}"


def test_changelog_has_unreleased_section():
    content = CHANGELOG.read_text()
    assert "## [Unreleased]" in content or "## Unreleased" in content


def test_changelog_mentions_v2_opt_in_accept_header():
    content = CHANGELOG.read_text()
    assert "vnd.nextseek.v2+json" in content
    assert "Accept" in content


def test_changelog_mentions_envelope_results_key():
    content = CHANGELOG.read_text()
    assert "results" in content
    assert "count" in content  # v2 envelope key


def test_changelog_mentions_errors_array_shape():
    content = CHANGELOG.read_text()
    assert "errors" in content
    assert "JSON:API" in content or "jsonapi" in content.lower()


def test_changelog_mentions_schema_endpoints():
    content = CHANGELOG.read_text()
    assert "/schema/v2/" in content

"""Mutant-killer tests for bounded capabilities.md projection (Task 12 coverage)."""
from __future__ import annotations

from pathlib import Path

import pytest

from nextseek_api.cc_assistant.op_registry.ns_capabilities import (
    NsCapabilitiesError,
    load_ns_projection,
    project_ns_capabilities,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
CANONICAL = (
    REPO_ROOT / "chat_nextseek" / "src" / "chat_nextseek" / "context" / "capabilities.md"
)


def _md(
    *,
    overview="NExtSEEK helps researchers query samples.",
    h3=("Search samples", "Inspect graphs"),
    negatives=("- **Live deploys** are out of scope.\n", "- **Credential dumps** are refused.\n"),
) -> str:
    h3_block = "\n".join(f"### {title}\n\nBody.\n" for title in h3)
    return (
        f"## Overview\n\n{overview}\n\n"
        f"## What You Can Ask\n\n{h3_block}\n"
        "## What the System Cannot Do\n\n" + "".join(negatives)
    )


def test_canonical_markdown_projects_without_stale_pipeline_phrase():
    projection = load_ns_projection(CANONICAL)
    assert projection.description
    assert projection.tools
    assert "cannot run pipelines" not in projection.not_for
    assert load_ns_projection(CANONICAL).tools == projection.tools


def test_missing_file_is_red(tmp_path: Path):
    with pytest.raises(NsCapabilitiesError, match="missing"):
        load_ns_projection(tmp_path / "nope.md")


def test_crlf_and_unclosed_fence_are_red():
    with pytest.raises(NsCapabilitiesError, match="LF"):
        project_ns_capabilities(_md().replace("\n", "\r\n"))
    with pytest.raises(NsCapabilitiesError, match="unclosed"):
        project_ns_capabilities(_md() + "\n```python\nprint(1)\n")


def test_required_h2_order_and_duplicates_are_red():
    swapped = (
        "## What You Can Ask\n\n### A\n\n"
        "## Overview\n\nHi.\n\n"
        "## What the System Cannot Do\n\n- **X** no.\n"
    )
    with pytest.raises(NsCapabilitiesError, match="out of order"):
        project_ns_capabilities(swapped)
    duplicated = _md() + "\n## Overview\n\nAgain.\n"
    with pytest.raises(NsCapabilitiesError, match="exactly once"):
        project_ns_capabilities(duplicated)


def test_duplicate_and_empty_labels_are_red():
    with pytest.raises(NsCapabilitiesError, match="duplicate"):
        project_ns_capabilities(_md(h3=("Search", "search")))
    with pytest.raises(NsCapabilitiesError, match="malformed H3"):
        project_ns_capabilities(
            "## Overview\n\nHi.\n\n## What You Can Ask\n\n###\n\n"
            "## What the System Cannot Do\n\n- **X** no.\n"
        )
    with pytest.raises(NsCapabilitiesError, match="malformed bold"):
        project_ns_capabilities(_md(negatives=("- no bold lead\n",)))
    with pytest.raises(NsCapabilitiesError, match="unclosed bold"):
        project_ns_capabilities(_md(negatives=("- **open lead is bad\n",)))


def test_nested_heading_and_stale_phrase_are_red():
    nested = _md() + "\n> ## Nested\n"
    with pytest.raises(NsCapabilitiesError, match="nested"):
        project_ns_capabilities(nested)
    with pytest.raises(NsCapabilitiesError, match="stale phrase"):
        project_ns_capabilities(_md(h3=("cannot run pipelines", "Inspect graphs")))


def test_over_budget_label_is_red():
    with pytest.raises(NsCapabilitiesError, match="exceeds"):
        project_ns_capabilities(_md(h3=("A" * 121, "B")))

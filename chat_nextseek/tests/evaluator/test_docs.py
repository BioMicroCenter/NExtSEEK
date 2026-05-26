from __future__ import annotations

import re
from pathlib import Path

import pytest

from chat_nextseek.evaluator.runner import build_parser


DOC_ROOT = Path(__file__).resolve().parents[2] / "src" / "chat_nextseek" / "evaluator"
DOCS_DIR = DOC_ROOT / "docs"
REQUIRED_DOCS = {
    DOC_ROOT / "README.md": ["Quickstart", "docs/operations.md"],
    DOCS_DIR / "architecture.md": ["Data flow", "BAML"],
    DOCS_DIR / "operations.md": ["baml-regeneration", "Failure buckets"],
    DOCS_DIR / "cli.md": ["--eval-batch", "--eval-batch-async"],
    DOCS_DIR / "runbook.md": ["103", "Expected cost"],
}


def test_runner_parser_includes_batch_and_demo_flags():
    parser = build_parser()
    help_text = parser.format_help()
    assert "--eval-batch" in help_text
    assert "--eval-batch-async" in help_text
    assert "--eval-batch-limit" in help_text
    assert "--eval-demo" in help_text


def test_runner_parser_includes_source_actions():
    parser = build_parser()
    help_text = parser.format_help()
    assert "--eval-source" in help_text
    assert "--eval-context" in help_text
    assert "--eval-retry" in help_text


@pytest.mark.parametrize("path,needles", list(REQUIRED_DOCS.items()))
def test_doc_exists_and_has_required_sections(path, needles):
    assert path.is_file(), f"Missing doc: {path}"
    text = path.read_text(encoding="utf-8")
    for needle in needles:
        assert needle in text, f"{path.name} missing required text: {needle!r}"


def test_cli_doc_references_match_parser():
    parser = build_parser()
    long_flags = set()
    for action in parser._actions:
        for opt in action.option_strings:
            if opt.startswith("--"):
                long_flags.add(opt)

    cli_md = (DOCS_DIR / "cli.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"--[a-z][a-z0-9\\-]+", cli_md))

    stray = documented - long_flags
    assert not stray, f"cli.md documents flags that don't exist in build_parser: {stray}"

    undocumented = long_flags - documented - {"--help"}
    assert not undocumented, f"Parser flags missing from cli.md: {undocumented}"

"""Unit tests for build_tools.ingest_nextseek_docs.__main__."""
from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from build_tools.ingest_nextseek_docs import __main__ as orchestrator
from build_tools.ingest_nextseek_docs.constants import BEGIN_MARKER, END_MARKER


def _markdown(sections: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    for title, para in sections:
        parts.append(f"# {title}\n\n{para}\n")
    return "\n".join(parts)


def _stub_loader(return_text: str):
    def _load(url: str) -> str:
        return return_text

    return _load


def _seed_claude_md(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"# Header\n{BEGIN_MARKER}\n{END_MARKER}\n# Footer\n")


def test_ingest_signature_has_loader_kwarg() -> None:
    sig = inspect.signature(orchestrator.ingest)
    params = sig.parameters
    assert "loader" in params
    assert all(p.kind == inspect.Parameter.KEYWORD_ONLY for p in params.values())


def test_ingest_exits_0_when_hash_matches(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    docs_dir.mkdir(parents=True)
    md = _markdown([("A", "a-body.")])
    sections = orchestrator.split_by_h1(md)
    (docs_dir / ".content-hash").write_text(
        orchestrator._compute_snapshot_hash(sections) + "\n"
    )
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)

    rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="fake",
        force=False,
        loader=_stub_loader(md),
    )
    assert rc == 0
    captured = capsys.readouterr()
    assert "no changes" in captured.out
    assert list(docs_dir.glob("*-*.md")) == []


def test_ingest_exits_2_on_fresh_run_writes_expected_files(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)
    md = _markdown([("Welcome", "Intro."), ("Getting Started", "More.")])

    rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="fake",
        force=False,
        loader=_stub_loader(md),
    )
    assert rc == 2
    assert (docs_dir / "01-welcome.md").exists()
    assert (docs_dir / "02-getting-started.md").exists()
    assert (docs_dir / "README.md").exists()
    assert (docs_dir / ".content-hash").exists()
    content = claude_md.read_text()
    assert "## NExtSEEK Documentation" in content
    assert "Welcome" in content


def test_ingest_force_true_overrides_hash_match(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    docs_dir.mkdir(parents=True)
    md = _markdown([("A", "a-body.")])
    sections = orchestrator.split_by_h1(md)
    (docs_dir / ".content-hash").write_text(
        orchestrator._compute_snapshot_hash(sections) + "\n"
    )
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)

    rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="fake",
        force=True,
        loader=_stub_loader(md),
    )
    assert rc == 2
    assert (docs_dir / "01-a.md").exists()


def test_ingest_loader_exception_exits_1_no_writes(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)

    def boom(url: str) -> str:
        raise RuntimeError("network down")

    rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="fake",
        force=False,
        loader=boom,
    )
    assert rc == 1
    assert not (docs_dir / ".content-hash").exists()
    assert list(docs_dir.glob("*-*.md")) == []


def test_ingest_loader_returns_no_h1_exits_1(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)

    rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="fake",
        force=False,
        loader=_stub_loader("just plain text, no headings\n"),
    )
    assert rc == 1
    assert not (docs_dir / ".content-hash").exists()


def test_ingest_cleans_stale_section_files(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    docs_dir.mkdir(parents=True)
    (docs_dir / "99-stale.md").write_text("# Stale\n\nold.\n")
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)

    md = _markdown([("Welcome", "Intro.")])
    rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="fake",
        force=True,
        loader=_stub_loader(md),
    )
    assert rc == 2
    assert not (docs_dir / "99-stale.md").exists()
    assert (docs_dir / "01-welcome.md").exists()


def test_ingest_writes_hash_last(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)
    md = _markdown([("Welcome", "Intro.")])

    def boom(path: Path, digest: str) -> None:
        raise OSError("disk full on hash write")

    monkeypatch.setattr(orchestrator, "write_stored_hash", boom)

    rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="fake",
        force=True,
        loader=_stub_loader(md),
    )
    assert rc == 1
    assert (docs_dir / "01-welcome.md").exists()
    assert (docs_dir / "README.md").exists()
    assert not (docs_dir / ".content-hash").exists()


def test_ingest_emits_info_logs_and_status_line(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    caplog.set_level(logging.INFO, logger="build_tools.ingest_nextseek_docs")
    docs_dir = tmp_path / "docs" / "nextseek"
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)
    md = _markdown([("Welcome", "Intro.")])

    rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="fake",
        force=True,
        loader=_stub_loader(md),
    )
    assert rc == 2

    captured = capsys.readouterr()
    assert "changes written" in captured.out.lower()

    info_records = [
        record
        for record in caplog.records
        if record.levelno == logging.INFO
        and record.name.startswith("build_tools.ingest_nextseek_docs")
    ]
    assert len(info_records) >= 1


def test_ingest_preserves_existing_readme_md_during_cleanup(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    docs_dir.mkdir(parents=True)
    (docs_dir / "README.md").write_text("# OLD README\n\nstale.\n")
    (docs_dir / "01-old-section.md").write_text("# Old\n\nstale.\n")
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)
    md = _markdown([("Welcome", "Intro.")])

    rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="fake",
        force=True,
        loader=_stub_loader(md),
    )
    assert rc == 2
    assert not (docs_dir / "01-old-section.md").exists()
    readme = docs_dir / "README.md"
    assert readme.exists()
    assert "OLD README" not in readme.read_text()
    assert "Welcome" in readme.read_text()


def test_cli_module_help_mentions_force_flag() -> None:
    # Plan A · Amendment 7 v2: build_tools is a sibling project; tests run
    # from build_tools/ as cwd. Subprocess `python -m build_tools.X` needs
    # cwd at repo root so the `build_tools` package is on Python's default
    # sys.path (containing '').
    repo_root = Path(__file__).resolve().parents[3]
    result = subprocess.run(
        [sys.executable, "-m", "build_tools.ingest_nextseek_docs", "--help"],
        capture_output=True,
        text=True,
        cwd=repo_root,
    )
    assert result.returncode == 0, result.stderr
    assert "--force" in result.stdout
    assert "--help" in result.stdout


def test_main_passes_parsed_args_to_ingest(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    claude_md = tmp_path / "container" / "CLAUDE.md"

    with patch.object(orchestrator, "ingest", return_value=2) as mocked_ingest:
        rc = orchestrator.main(
            [
                "--force",
                "--doc-url",
                "https://example.test/export",
                "--docs-dir",
                str(docs_dir),
                "--claude-md-path",
                str(claude_md),
            ]
        )

    assert rc == 2
    mocked_ingest.assert_called_once_with(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="https://example.test/export",
        force=True,
    )


def test_test_main_uses_monkeypatch_at_most_once_per_test_class() -> None:
    src = Path(__file__).read_text()
    references = src.count("monkeypatch.setattr(" "orchestrator")
    assert references <= 1


def test_extract_overview_falls_back_when_first_section_is_empty_body(
    tmp_path: Path,
) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)
    md = "# Overview\n# Real Section\n\nBody here.\n"

    rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="fake",
        force=True,
        loader=_stub_loader(md),
    )
    assert rc == 2
    content = claude_md.read_text()
    assert "covering 2 top-level sections" in content


def test_snapshot_hash_ignores_section_order() -> None:
    first = orchestrator.split_by_h1(_markdown([("A", "one."), ("B", "two.")]))
    second = orchestrator.split_by_h1(_markdown([("B", "two."), ("A", "one.")]))

    assert orchestrator._compute_snapshot_hash(first) == (
        orchestrator._compute_snapshot_hash(second)
    )


def test_ingest_retries_until_snapshot_stabilizes(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)
    markdowns = iter(
        [
            _markdown([("Welcome", "first body.")]),
            _markdown([("Welcome", "final body.")]),
            _markdown([("Welcome", "final body.")]),
        ]
    )

    def loader(_: str) -> str:
        return next(markdowns)

    rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="fake",
        force=False,
        loader=loader,
    )

    assert rc == 2
    assert "final body." in (docs_dir / "01-welcome.md").read_text()


def test_ingest_fails_closed_when_snapshot_never_stabilizes(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)
    markdowns = iter(
        [
            _markdown([("Welcome", "first body.")]),
            _markdown([("Welcome", "second body.")]),
            _markdown([("Welcome", "third body.")]),
        ]
    )

    def loader(_: str) -> str:
        return next(markdowns)

    rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="fake",
        force=False,
        loader=loader,
    )

    assert rc == 1
    assert not docs_dir.exists()

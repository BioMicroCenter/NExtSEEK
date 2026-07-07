"""End-to-end integration tests for the NExtSEEK docs ingestion pipeline."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from build_tools.ingest_nextseek_docs import __main__ as orchestrator
from build_tools.ingest_nextseek_docs.constants import BEGIN_MARKER, END_MARKER

REPO_ROOT = Path(__file__).resolve().parents[3]

SECTIONS_A = [
    ("Welcome", "Introductory paragraph for the welcome page."),
    ("Getting Started", "Getting started guide intro paragraph."),
    ("Sample Registration", "How to register samples in NExtSEEK."),
]

SECTIONS_B = [
    ("Welcome", "Introductory paragraph for the welcome page."),
    ("Data Upload", "How to upload data."),
    ("Sample Registration", "How to register samples in NExtSEEK."),
]


def _seed_claude_md(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Header\n"
        f"{BEGIN_MARKER}\n"
        f"{END_MARKER}\n"
        "# Footer\n"
    )


def _markdown(sections: list[tuple[str, str]]) -> str:
    parts: list[str] = []
    for title, para in sections:
        parts.append(f"# {title}\n\n{para}\n")
    return "\n".join(parts)


def _make_loader(markdown: str):
    def _load(url: str) -> str:
        return markdown

    return _load


def _git_status_for_repo_paths() -> str:
    try:
        result = subprocess.run(
            [
                "git",
                "status",
                "--porcelain",
                "docker/cc-runtime/docs/nextseek/",
                "docker/cc-runtime/container/CLAUDE.md",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pytest.skip("git not available or timed out")
    if result.returncode != 0:
        pytest.skip(f"git status failed: {result.stderr.strip()}")
    return result.stdout


def test_end_to_end_fresh_ingest_writes_expected_files(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)

    rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="https://fake.example/",
        force=True,
        loader=_make_loader(_markdown(SECTIONS_A)),
    )

    assert rc == 2
    welcome = (docs_dir / "01-welcome.md").read_text()
    getting_started = (docs_dir / "02-getting-started.md").read_text()
    sample_registration = (docs_dir / "03-sample-registration.md").read_text()
    assert "# Welcome" in welcome
    assert "Introductory paragraph for the welcome page." in welcome
    assert "# Getting Started" in getting_started
    assert "Getting started guide intro paragraph." in getting_started
    assert "# Sample Registration" in sample_registration
    assert "How to register samples in NExtSEEK." in sample_registration

    readme = (docs_dir / "README.md").read_text()
    assert "Welcome" in readme
    assert "Getting Started" in readme
    assert "Sample Registration" in readme
    assert "https://fake.example/" in readme

    hash_text = (docs_dir / ".content-hash").read_text().strip()
    assert len(hash_text) == 64


def test_end_to_end_idempotent_rerun(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)
    loader = _make_loader(_markdown(SECTIONS_A))

    first_rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="u",
        force=True,
        loader=loader,
    )
    assert first_rc == 2
    first_readme = (docs_dir / "README.md").read_bytes()

    second_rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="u",
        force=False,
        loader=loader,
    )

    assert second_rc == 0
    assert (docs_dir / "README.md").read_bytes() == first_readme
    assert "no changes" in capsys.readouterr().out


def test_end_to_end_mutation_deletes_stale_and_writes_new(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)

    first_rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="u",
        force=True,
        loader=_make_loader(_markdown(SECTIONS_A)),
    )
    assert first_rc == 2
    assert (docs_dir / "02-getting-started.md").exists()

    second_rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="u",
        force=False,
        loader=_make_loader(_markdown(SECTIONS_B)),
    )

    assert second_rc == 2
    assert not (docs_dir / "02-getting-started.md").exists()
    new_section = (docs_dir / "02-data-upload.md").read_text()
    assert "# Data Upload" in new_section
    assert "How to upload data." in new_section
    assert (docs_dir / "01-welcome.md").exists()
    assert (docs_dir / "03-sample-registration.md").exists()


def test_end_to_end_container_claude_md_block_populated(tmp_path: Path) -> None:
    docs_dir = tmp_path / "docs" / "nextseek"
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)

    rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="https://fake.example/",
        force=True,
        loader=_make_loader(_markdown(SECTIONS_A)),
    )

    assert rc == 2
    content = claude_md.read_text()
    begin_idx = content.index(BEGIN_MARKER) + len(BEGIN_MARKER)
    end_idx = content.index(END_MARKER)
    block = content[begin_idx:end_idx]

    assert "Welcome" in block
    assert "Getting Started" in block
    assert "Sample Registration" in block
    assert "/app/docs/nextseek/README.md" in block
    assert "# Header" in content
    assert "# Footer" in content


def test_end_to_end_does_not_pollute_repo(tmp_path: Path) -> None:
    before = _git_status_for_repo_paths()

    docs_dir = tmp_path / "docs" / "nextseek"
    claude_md = tmp_path / "container" / "CLAUDE.md"
    _seed_claude_md(claude_md)
    rc = orchestrator.ingest(
        docs_dir=docs_dir,
        claude_md_path=claude_md,
        doc_url="u",
        force=True,
        loader=_make_loader(_markdown(SECTIONS_A)),
    )

    assert rc == 2
    after = _git_status_for_repo_paths()
    assert after == before

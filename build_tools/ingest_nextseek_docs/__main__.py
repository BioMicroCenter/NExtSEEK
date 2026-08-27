"""CLI and orchestration for NExtSEEK docs ingestion."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Callable

from build_tools.ingest_nextseek_docs.constants import (
    DEFAULT_CLAUDE_MD_PATH,
    DEFAULT_DOC_URL,
    DEFAULT_DOCS_DIR,
)
from build_tools.ingest_nextseek_docs.fetch import (
    load_gitbook_markdown_corpus,
)
from build_tools.ingest_nextseek_docs.hashing import (
    compute_content_hash,
    read_stored_hash,
    write_stored_hash,
)
from build_tools.ingest_nextseek_docs.split import Section, split_by_h1
from build_tools.ingest_nextseek_docs.toc import (
    render_claude_md_block,
    render_readme,
    update_claude_md,
)

logger = logging.getLogger(__name__)

EXIT_NO_CHANGE = 0
EXIT_ERROR = 1
EXIT_CHANGES_WRITTEN = 2
MAX_STABILITY_ATTEMPTS = 3


def ingest(
    *,
    docs_dir: Path,
    claude_md_path: Path,
    doc_url: str,
    force: bool,
    loader: Callable[[str], str] = load_gitbook_markdown_corpus,
) -> int:
    """Run the full ingestion pipeline and return an exit code."""
    try:
        snapshot = _load_stable_snapshot(
            doc_url=doc_url,
            loader=loader,
        )
        if snapshot is None:
            return EXIT_ERROR

        content_hash = snapshot.content_hash
        sections = snapshot.sections
        hash_path = docs_dir / ".content-hash"
        stored_hash = read_stored_hash(hash_path)

        if stored_hash == content_hash and not force:
            sys.stdout.write("no changes\n")
            return EXIT_NO_CHANGE

        docs_dir.mkdir(parents=True, exist_ok=True)
        for stale in docs_dir.glob("*.md"):
            if stale.name == "README.md":
                continue
            stale.unlink()

        for section in sections:
            section_path = docs_dir / f"{section.ordinal:02d}-{section.slug}.md"
            section_path.write_text(section.body)

        readme = render_readme(sections, doc_url, content_hash)
        (docs_dir / "README.md").write_text(readme)

        overview = _extract_overview_paragraph(sections)
        block = render_claude_md_block(sections, overview)
        update_claude_md(claude_md_path, block)

        write_stored_hash(hash_path, content_hash)

        sys.stdout.write(
            f"changes written: {len(sections)} section files, "
            "README, container/CLAUDE.md\n"
        )
        return EXIT_CHANGES_WRITTEN
    except Exception:
        logger.exception("ingest failed")
        return EXIT_ERROR


class _Snapshot:
    """One stabilized ingest snapshot."""

    def __init__(self, *, sections: list[Section], content_hash: str) -> None:
        self.sections = sections
        self.content_hash = content_hash


def _load_stable_snapshot(
    *,
    doc_url: str,
    loader: Callable[[str], str],
) -> _Snapshot | None:
    """Load the source corpus until the section snapshot repeats, or give up."""
    snapshots_by_hash: dict[str, _Snapshot] = {}

    for attempt in range(1, MAX_STABILITY_ATTEMPTS + 1):
        logger.info(
            "Loading source from %s (attempt %d/%d)",
            doc_url,
            attempt,
            MAX_STABILITY_ATTEMPTS,
        )
        raw_markdown = loader(doc_url)
        logger.info("Loaded %d characters of markdown", len(raw_markdown))
        sections = split_by_h1(raw_markdown)
        if not sections:
            logger.warning(
                "parser returned markdown with zero H1 sections on attempt %d/%d",
                attempt,
                MAX_STABILITY_ATTEMPTS,
            )
            continue

        snapshot = _Snapshot(
            sections=sections,
            content_hash=_compute_snapshot_hash(sections),
        )
        if snapshot.content_hash in snapshots_by_hash:
            if attempt > 1:
                logger.info(
                    "Source snapshot stabilized on attempt %d/%d",
                    attempt,
                    MAX_STABILITY_ATTEMPTS,
                )
            return snapshot
        snapshots_by_hash[snapshot.content_hash] = snapshot

    logger.error(
        "source content did not stabilize across %d attempts; aborting without writes",
        MAX_STABILITY_ATTEMPTS,
    )
    return None


def _compute_snapshot_hash(sections: list[Section]) -> str:
    """Hash a canonical, order-insensitive representation of the split sections."""
    parts: list[str] = []
    for section in sorted(sections, key=lambda item: (item.slug, item.title)):
        parts.extend(
            [
                f"slug:{section.slug}",
                f"title:{section.title}",
                _normalize_for_hash(section.body),
                "<<<END SECTION>>>",
            ]
        )
    return compute_content_hash("\n".join(parts))


def _normalize_for_hash(text: str) -> str:
    """Normalize line endings and trailing whitespace before hashing."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in normalized.split("\n")).strip()


def _extract_overview_paragraph(sections: list[Section]) -> str:
    """Return the first section description or a generic fallback."""
    description = sections[0].description
    if description and description != "(section overview)":
        return description
    return f"NExtSEEK documentation covering {len(sections)} top-level sections."


def main(argv: list[str] | None = None) -> int:
    """argparse CLI entry point."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(
        prog="ingest_nextseek_docs",
        description="Fetch NExtSEEK GitBook, split, and regenerate docs.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="regenerate even if the content hash matches",
    )
    parser.add_argument(
        "--doc-url",
        default=DEFAULT_DOC_URL,
        help=f"source URL (default: {DEFAULT_DOC_URL})",
    )
    parser.add_argument(
        "--docs-dir",
        type=Path,
        default=DEFAULT_DOCS_DIR,
        help=f"output docs dir (default: {DEFAULT_DOCS_DIR})",
    )
    parser.add_argument(
        "--claude-md-path",
        type=Path,
        default=DEFAULT_CLAUDE_MD_PATH,
        help=f"container CLAUDE.md path (default: {DEFAULT_CLAUDE_MD_PATH})",
    )
    args = parser.parse_args(argv)
    return ingest(
        docs_dir=args.docs_dir,
        claude_md_path=args.claude_md_path,
        doc_url=args.doc_url,
        force=args.force,
    )


if __name__ == "__main__":
    sys.exit(main())

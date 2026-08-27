"""Unit tests for build_tools.ingest_nextseek_docs.hashing."""
from __future__ import annotations

from pathlib import Path

from build_tools.ingest_nextseek_docs.hashing import (
    compute_content_hash,
    read_stored_hash,
    write_stored_hash,
)

SHA256_OF_HELLO = (
    "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
)


def test_compute_content_hash_matches_known_digest_for_hello() -> None:
    assert compute_content_hash("hello") == SHA256_OF_HELLO


def test_compute_content_hash_matches_known_digest_for_empty_string() -> None:
    assert compute_content_hash("") == (
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_compute_content_hash_distinguishes_different_inputs() -> None:
    assert compute_content_hash("a") != compute_content_hash("b")


def test_read_stored_hash_returns_none_when_file_missing(tmp_path: Path) -> None:
    assert read_stored_hash(tmp_path / "nope.txt") is None


def test_read_stored_hash_strips_trailing_whitespace(tmp_path: Path) -> None:
    path = tmp_path / "hash.txt"
    path.write_text("  abc123\n")
    assert read_stored_hash(path) == "abc123"


def test_read_stored_hash_strips_leading_whitespace(tmp_path: Path) -> None:
    path = tmp_path / "hash.txt"
    path.write_text("   abc123")
    assert read_stored_hash(path) == "abc123"


def test_write_stored_hash_writes_digest_with_trailing_newline(tmp_path: Path) -> None:
    path = tmp_path / "hash.txt"
    write_stored_hash(path, "deadbeef")
    assert path.read_text() == "deadbeef\n"


def test_write_stored_hash_creates_missing_parent_dirs(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "deeper" / "hash.txt"
    assert not path.parent.exists()
    write_stored_hash(path, "xyz")
    assert path.exists()
    assert path.parent.exists()
    assert path.read_text() == "xyz\n"


def test_write_stored_hash_overwrites_existing_file(tmp_path: Path) -> None:
    path = tmp_path / "hash.txt"
    path.write_text("old\n")
    write_stored_hash(path, "new")
    assert path.read_text() == "new\n"

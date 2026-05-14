"""Tests for bootstrap.lib.env."""
from __future__ import annotations

from pathlib import Path

import pytest

from bootstrap.lib.env import read_env, write_env, update_env


def test_read_simple_keyvalue(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    p.write_text('FOO="bar"\nBAZ="qux"\n')
    assert read_env(p) == {"FOO": "bar", "BAZ": "qux"}


def test_read_strips_quotes_and_handles_unquoted(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    p.write_text("FOO=bar\nBAZ='qux quux'\nZIM=\"zam\"\n")
    assert read_env(p) == {"FOO": "bar", "BAZ": "qux quux", "ZIM": "zam"}


def test_read_ignores_comments_and_blanks(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    p.write_text("# comment\n\nFOO=bar\n# another\nBAZ=qux\n")
    assert read_env(p) == {"FOO": "bar", "BAZ": "qux"}


def test_write_creates_quoted_values(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    write_env(p, {"FOO": "bar", "BAZ": "has spaces"})
    text = p.read_text()
    assert 'FOO="bar"' in text
    assert 'BAZ="has spaces"' in text


def test_update_preserves_comments_and_blank_lines(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    original = "# header comment\n\nFOO=oldvalue\n# trailing\nBAZ=keepme\n"
    p.write_text(original)
    update_env(p, {"FOO": "newvalue"})
    text = p.read_text()
    assert "# header comment" in text
    assert "FOO=\"newvalue\"" in text
    assert "FOO=oldvalue" not in text
    assert "# trailing" in text
    assert "BAZ=keepme" in text


def test_update_appends_new_keys_with_blank_line(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    p.write_text("FOO=existing\n")
    update_env(p, {"NEW_KEY": "newvalue"})
    text = p.read_text()
    assert "FOO=existing" in text
    assert 'NEW_KEY="newvalue"' in text
    assert text.index("FOO=existing") < text.index("NEW_KEY")


def test_update_is_idempotent(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    p.write_text("# header\nFOO=bar\n")
    update_env(p, {"FOO": "bar"})
    update_env(p, {"FOO": "bar"})
    assert p.read_text().count("FOO=") == 1


def test_round_trip_preserves_quotes_and_backslashes(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    original = {
        "WITH_QUOTES": 'has "embedded" quotes',
        "WITH_BACKSLASH": r"C:\Users\foo",
        "BOTH": r'mix "of" \ everything',
    }
    write_env(p, original)
    assert read_env(p) == original


def test_quote_refuses_newlines(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    with pytest.raises(ValueError, match="newlines"):
        write_env(p, {"BAD": "line1\nline2"})
    with pytest.raises(ValueError, match="newlines"):
        write_env(p, {"BAD": "line1\rline2"})

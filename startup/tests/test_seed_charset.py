"""R7 guard: the dmac seed dump's assistant_chat_session must be utf8mb4."""
from __future__ import annotations

import gzip
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DUMP = REPO_ROOT / "seed" / "dmac.sql.gz"


def _table_default_charset(table: str) -> str | None:
    create_re = re.compile(rf"CREATE TABLE `{re.escape(table)}`")
    end_re = re.compile(r"\)\s*ENGINE=.*DEFAULT CHARSET=(\w+)", re.IGNORECASE)
    in_block = False
    with gzip.open(DUMP, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not in_block and create_re.search(line):
                in_block = True
            if in_block:
                m = end_re.search(line)
                if m:
                    return m.group(1).lower()
    return None


def _count_default_charset(charset: str) -> int:
    pat = re.compile(rf"DEFAULT CHARSET={re.escape(charset)}\b", re.IGNORECASE)
    n = 0
    with gzip.open(DUMP, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if pat.search(line):
                n += 1
    return n


def test_assistant_chat_session_is_utf8mb4():
    charset = _table_default_charset("assistant_chat_session")
    assert charset is not None, "assistant_chat_session CREATE TABLE not found in dump"
    assert charset == "utf8mb4", (
        f"seed dump still declares assistant_chat_session DEFAULT CHARSET={charset} "
        "— regenerate startup/seed/dmac.sql.gz as utf8mb4 (R7)"
    )


def test_no_table_declares_latin1_default():
    assert _count_default_charset("latin1") == 0, (
        "seed dump still has latin1 DEFAULT CHARSET tables — regenerate as utf8mb4 (R7)"
    )
    assert _count_default_charset("utf8mb4") >= 1

#!/usr/bin/env python3
"""Turn `context/` into database writes.

The five JSON files Nessie reads are exports, not source. Once per UTC day
`_fetch_context_files_from_db`
(`NessieAI/chat_nextseek/src/chat_nextseek/config.py:717-725`) runs
`SELECT * FROM dmac.sample_types_context`, `dmac.assay_context` and
`dmac.projects_context` and rewrites them in place, so editing an export changes
nothing that survives a day. `context/` is the hand-owned source; this program is
how it reaches a database.

    python scripts/context_gen.py --emit update --table all --out /tmp/context.sql
    python scripts/context_gen.py --emit seed --table assays --out startup/seed/sql/assay_context.sql

Nothing here connects to a database. It reads committed JSON and writes SQL text.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_source(path: Path) -> list[dict]:
    """The curated rows at `path`, as a list of dicts.

    A relative path is tried against the working directory first and then
    against the repository root, so callers can name `context/projects.json`
    from anywhere.
    """
    candidate = Path(path)
    if not candidate.is_absolute() and not candidate.exists():
        candidate = REPO_ROOT / candidate
    rows = json.loads(candidate.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"{path}: expected a list of rows")
    return rows

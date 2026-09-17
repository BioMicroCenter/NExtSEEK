"""scripts/context_gen.py, the generator that turns context/ into database writes.

The five JSON files Nessie reads are exports of three MySQL tables, rewritten in
place once per UTC day by `_fetch_context_files_from_db`
(`NessieAI/chat_nextseek/src/chat_nextseek/config.py:717-725`). Editing an export
changes nothing that survives a day, so the curated content in `context/` reaches
a database only through this generator. These tests are what make it safe to
point at a database.

No database and no Django: everything here reads committed JSON and renders text.
"""
from pathlib import Path

import scripts.context_gen as cg


def test_load_source_reads_every_curated_row():
    rows = cg.load_source(Path("context/sample_types.json"))
    assert len(rows) == 109
    assert all("sample_type" in r for r in rows)

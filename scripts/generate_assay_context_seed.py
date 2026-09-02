#!/usr/bin/env python3
"""Regenerate startup/seed/sql/assay_context.sql from the committed JSON export.

Source: chat_nextseek/src/chat_nextseek/context/assays_db.json, itself a
`SELECT * FROM dmac.assay_context` against production, mapped by
chat_nextseek/src/chat_nextseek/config.py::map_assay.

Run from the repo root:  python scripts/generate_assay_context_seed.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPORT = ROOT / "chat_nextseek/src/chat_nextseek/context/assays_db.json"
DEST = ROOT / "startup/seed/sql/assay_context.sql"

# Exported JSON key -> database column. The column spellings are map_assay's
# first choice for each field, which is what production answered with.
COLUMNS = [
    ("Name", "assay_name"),
    ("Description", "Description"),
    ("Tags", "Tags"),
    ("Alternative Assay Names", "Alternative_Assay_Names"),
    ("Required Parent Sample Types", "Required_Parent_Sample_Types"),
    ("Optional Parent Sample Types", "Optional_Parent_Sample_Types"),
    ("Children Sample Types", "Children_Sample_Types"),
    ("Parent Clade Type", "Parent_Clade_Type"),
    ("Child Clade Type", "Child_Clade_Type"),
    ("AssaySheet Link", "AssaySheet_Link"),
    ("AssociatedRepository", "AssociatedRepository"),
    ("Critical Attributes", "Critical_Attributes"),
    ("Protocols_Phrases", "Protocols_Phrases"),
    ("Protocols_UIDs", "Protocols_UIDs"),
    ("Internal Assay ID", "internal_assay_id"),
]

DDL = """\
-- Curated context for internal assays: what each one consumes, produces and is
-- called. Generated from a committed export by
-- scripts/generate_assay_context_seed.py; regenerate rather than hand-editing.
--
-- Created in SQL like sample_types_context and for the same reason: no Django
-- migration references it, and it is absent from dmac.sql.gz because
-- regenerating that seed needs maintainer credentials for a remote host.
-- Production has its own copy of this table; this file is what gives the local
-- and dev stacks one.
--
-- The rows are two unreconciled sources merged: 80 carry sample types and no
-- internal_assay_id, 91 the reverse, 46 both, and 22 assay_name values appear
-- twice. The catalog page renders that as it is; it does not merge rows.
CREATE TABLE IF NOT EXISTS assay_context (
  id                           INT AUTO_INCREMENT PRIMARY KEY,
  assay_name                   VARCHAR(255) NULL,
  Description                  TEXT         NULL,
  Tags                         TEXT         NULL,
  Alternative_Assay_Names      TEXT         NULL,
  Required_Parent_Sample_Types TEXT         NULL,
  Optional_Parent_Sample_Types TEXT         NULL,
  Children_Sample_Types        TEXT         NULL,
  Parent_Clade_Type            VARCHAR(64)  NULL,
  Child_Clade_Type             VARCHAR(64)  NULL,
  AssaySheet_Link              VARCHAR(255) NULL,
  AssociatedRepository         VARCHAR(255) NULL,
  Critical_Attributes          TEXT         NULL,
  Protocols_Phrases            TEXT         NULL,
  Protocols_UIDs               TEXT         NULL,
  internal_assay_id            INT          NULL,
  KEY idx_assay_name (assay_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

"""


def literal(value):
    """A MySQL literal. NULL for missing, an escaped single-quoted string else.

    Newlines are escaped rather than emitted raw so every INSERT is exactly one
    line: several Description values run to a dozen sentences with embedded
    newlines, and a statement that spans lines makes the file painful to diff
    and to count.
    """
    if value is None or value == "":
        return "NULL"
    if isinstance(value, int):
        return str(value)
    text = str(value)
    # Backslash first, or it would double-escape everything added after it.
    text = text.replace("\\", "\\\\").replace("'", "\\'")
    text = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return "'" + text + "'"


def main():
    rows = json.loads(EXPORT.read_text())
    cols = ", ".join("`%s`" % db for _, db in COLUMNS)
    lines = [DDL]
    for row in rows:
        values = ", ".join(literal(row.get(src)) for src, _ in COLUMNS)
        lines.append(f"INSERT INTO `assay_context` ({cols}) VALUES ({values});")
    DEST.write_text("\n".join(lines) + "\n")
    print(f"wrote {DEST} with {len(rows)} rows")


if __name__ == "__main__":
    main()

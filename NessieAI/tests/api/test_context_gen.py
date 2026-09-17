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


# --- 6.5 column mapping ------------------------------------------------------
#
# The point is that a typo in a curated file fails here rather than silently
# dropping a column at write time. So the expected column sets are derived from
# files the generator does not own, never restated by hand:
#
#   assays        scripts/generate_assay_context_seed.py::COLUMNS, whose database
#                 spellings are what production answered with
#   sample_types  the Django model seek/models/nextseek.py::Sample_types_context,
#                 whose fields are the live columns (`tags` carries the one
#                 db_column override, capital-T `Tags`)
#   projects      the CREATE TABLE in startup/seed/sql/projects_context.sql
#
# Two columns are newer than all three fixtures and are declared, with their
# reason, in cg.ADDED_COLUMNS.

import re

NEXTSEEK_MODELS = Path("seek/models/nextseek.py")
PROJECTS_DDL = Path("startup/seed/sql/projects_context.sql")


def _repo(path: Path) -> str:
    candidate = Path(path)
    if not candidate.exists():
        candidate = Path(cg.REPO_ROOT) / candidate
    return candidate.read_text(encoding="utf-8")


def _model_columns() -> set[str]:
    """The live columns of `sample_types_context`, read off the Django model.

    A field's column is its name unless it carries a `db_column`, which exactly
    one of them does.
    """
    body = _repo(NEXTSEEK_MODELS).split("class Sample_types_context(", 1)[1].split("\nclass ", 1)[0]
    found = set()
    for name, args in re.findall(r"^    (\w+) = models\.\w+\((.*)\)$", body, re.M):
        override = re.search(r'db_column="([^"]+)"', args)
        found.add(override.group(1) if override else name)
    return found


def _assay_seed_columns() -> set[str]:
    body = _repo(Path("scripts/generate_assay_context_seed.py")).split("COLUMNS = [", 1)[1].split("]", 1)[0]
    return {db for _src, db in re.findall(r'\("([^"]*)",\s*"([^"]*)"\)', body)}


def _projects_ddl_columns() -> set[str]:
    body = _repo(PROJECTS_DDL).split("CREATE TABLE IF NOT EXISTS projects_context (", 1)[1].split(")\nENGINE", 1)[0]
    found = set()
    for line in body.splitlines():
        match = re.match(r"\s{2}(\w+)\s+\w", line)
        if match and match.group(1) not in {"KEY", "PRIMARY"}:
            found.add(match.group(1))
    return found - {"id"}


def test_columns_match_the_fixtures_that_define_them():
    assert set(cg.COLUMNS["assays"]) == _assay_seed_columns()
    assert set(cg.COLUMNS["sample_types"]) == _model_columns() | {"repository_attributes"}
    assert set(cg.COLUMNS["projects"]) == _projects_ddl_columns() | {"pi_names"}
    # Everything the fixtures do not name is declared as new, with a reason.
    assert set(cg.ADDED_COLUMNS) == {"sample_types", "projects"}
    assert set(cg.ADDED_COLUMNS["sample_types"]) == {"repository_attributes"}
    assert set(cg.ADDED_COLUMNS["projects"]) == {"pi_names"}


def test_every_curated_key_maps_to_a_known_column():
    for table in ("sample_types", "assays", "projects"):
        rows = cg.load_source(cg.TABLES[table].source)
        assert rows, table
        cg.check_columns(table, rows)          # raises if any key is unknown
        keys = {k for row in rows for k in row}
        assert keys <= set(cg.COLUMNS[table]), table
        assert "id" not in keys, table          # the autoincrement is the database's


def test_an_unknown_key_raises_naming_the_key_and_the_table():
    import pytest

    rows = [{"sample_type": "TIS", "nmae": "Tissue"}]
    with pytest.raises(cg.UnknownColumn) as excinfo:
        cg.check_columns("sample_types", rows)
    message = str(excinfo.value)
    assert "nmae" in message and "sample_types" in message


def test_a_missing_natural_key_raises():
    import pytest

    with pytest.raises(cg.MissingKey):
        cg.check_columns("projects", [{"pi": "Kamm, Roger D. (MIT, contact PI)"}])


# --- 6.7 parse_pi ------------------------------------------------------------
#
# Per plan D7 the generator parses the free-text `pi` field into structured names
# so the person-name rule (13c) matches deterministically instead of asking an
# LLM to read `Last, First M. (Affiliation, role); ...`. The free-text field
# survives for display.
#
# The format, measured across all 12 curated rows: 9 carry PI names and 3 carry
# nothing; PIs are semicolon-separated; a PI is `Last, First M.` with an optional
# `(Affiliation, role)` suffix. Two hazards the format description hides:
#
#   1. a semicolon can appear INSIDE the parentheses (Griffith's row), so
#      splitting on every semicolon invents a PI called
#      "Scientific Director, Center for Gynepathology Research"
#   2. several PIs in one row carry no parenthetical at all (RMS-NGC's row)

PI_CSBC = "White, Forest M. (MIT, contact PI); Michor, Franziska (Dana-Farber, co-PI)"
PI_GRIFFITH = ("Griffith, Linda G. (MIT, PI; Scientific Director, Center for Gynepathology "
               "Research); Goods, Brittany A. (University of Melbourne, partner lab)")
PI_RMS = ("Koehler, Angela N. (MIT Koch Institute and Broad Institute, contact PI); "
          "Burgin, Alex B.; Gould, Alexandra E.; Linardic, Corinne M.; "
          "Nomura, Daniel (multi-PIs)")


def test_parse_pi_yields_every_surname_and_every_full_name():
    assert cg.parse_pi(PI_CSBC) == ["White", "Forest M. White", "Michor", "Franziska Michor"]


def test_parse_pi_ignores_a_semicolon_inside_the_parenthetical():
    assert cg.parse_pi(PI_GRIFFITH) == [
        "Griffith", "Linda G. Griffith", "Goods", "Brittany A. Goods",
    ]


def test_parse_pi_reads_a_pi_with_no_parenthetical():
    assert cg.parse_pi(PI_RMS) == [
        "Koehler", "Angela N. Koehler", "Burgin", "Alex B. Burgin",
        "Gould", "Alexandra E. Gould", "Linardic", "Corinne M. Linardic",
        "Nomura", "Daniel Nomura",
    ]


def test_parse_pi_of_nothing_is_empty():
    for empty in ("None", "none", "", "   ", None):
        assert cg.parse_pi(empty) == [], repr(empty)


def test_parse_pi_of_a_single_name_does_not_repeat_it():
    assert cg.parse_pi("Levine (MIT)") == ["Levine"]


def test_every_curated_project_row_parses():
    rows = cg.load_source(cg.TABLES["projects"].source)
    with_names = [r for r in rows if cg.parse_pi(r.get("pi"))]
    assert len(rows) == 12
    assert len(with_names) == 9
    # A surname is never dropped: every parsed name is non-empty and stripped.
    for row in rows:
        for name in cg.parse_pi(row.get("pi")):
            assert name == name.strip() and name


def test_pi_names_is_emitted_alongside_the_free_text_pi():
    rows = cg.with_pi_names(cg.load_source(cg.TABLES["projects"].source))
    csbc = next(r for r in rows if r["name"] == "CSBC")
    assert csbc["pi"] == PI_CSBC                      # free text kept for display
    assert csbc["pi_names"] == ["White", "Forest M. White", "Michor", "Franziska Michor"]
    bprc = next(r for r in rows if r["name"] == "BPRC")
    assert bprc["pi_names"] == []
    # Every row gains the column, so the write never leaves it undefined.
    assert all("pi_names" in r for r in rows)

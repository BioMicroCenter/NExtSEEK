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
#   assays        the committed startup/seed/sql/assay_context.sql CREATE TABLE,
#                 whose spellings are what production answered with
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


def _ddl_columns(ddl: str, table_name: str) -> list[str]:
    """The column names a CREATE TABLE declares, in order, without `id`."""
    body = ddl.split(f"CREATE TABLE IF NOT EXISTS {table_name} (", 1)[1].rsplit(") ENGINE", 1)[0]
    found = []
    for line in body.splitlines():
        match = re.match(r"\s{2}`?(\w+)`?\s+\w", line)
        if match and match.group(1) not in {"KEY", "PRIMARY", "UNIQUE"}:
            found.append(match.group(1))
    return [c for c in found if c != "id"]


def _assay_seed_columns() -> set[str]:
    """The committed assay_context.sql CREATE TABLE.

    Its column spellings were map_assay's first choice for each field, which is
    what production answered with on the 2026-09-11 pull; the retired
    scripts/generate_assay_context_seed.py wrote them there.
    """
    return set(_ddl_columns(_repo(Path("startup/seed/sql/assay_context.sql")), "assay_context"))


def _projects_ddl_columns() -> set[str]:
    return set(_ddl_columns(_repo(PROJECTS_DDL), "projects_context"))


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


# --- 6.9 the update SQL ------------------------------------------------------
#
# What "idempotent" has to mean here, measured against the 2026-09-11 production
# pull rather than assumed:
#
#   * one INSERT ... ON DUPLICATE KEY UPDATE per curated row, so re-running the
#     script is a no-op;
#   * the upsert only fires against a UNIQUE key, and no context table has one
#     today, so the script adds it;
#   * production's assay_context holds 217 rows against the curated 138: 68 names
#     the curated source no longer carries and 22 duplicated names, 20 of them
#     names the curated source does carry. projects_context holds a GBM row the
#     curated source drops. Upserting alone would leave every one of those behind
#     while reporting success, so the script deletes what the source no longer
#     names and collapses duplicate keys before it adds the index;
#   * every value escaped, and no literal autoincrement `id`.

INSERT_RE = re.compile(r"^INSERT INTO ", re.M)


def _rows_for(table: str) -> list[dict]:
    rows = cg.load_source(cg.TABLES[table].source)
    return cg.with_pi_names(rows) if table == "projects" else rows


def test_update_writes_one_upsert_per_row():
    for table, expected in (("sample_types", 109), ("assays", 138), ("projects", 12)):
        sql = cg.render_update(table, _rows_for(table))
        assert len(INSERT_RE.findall(sql)) == expected, table
        assert sql.count("ON DUPLICATE KEY UPDATE") == expected, table


def test_update_never_writes_the_autoincrement_id():
    for table in ("sample_types", "assays", "projects"):
        sql = cg.render_update(table, _rows_for(table))
        for statement in sql.split("INSERT INTO ")[1:]:
            columns = statement.split("(", 1)[1].split(")", 1)[0]
            assert "`id`" not in columns, table


def test_update_sets_every_column_including_the_key_on_a_duplicate():
    """The key is reassigned too, and that is not redundant.

    MySQL matches the unique key case-insensitively and ignores trailing spaces,
    so a row whose key differs only in case matches and is updated in place. The
    curated data holds exactly one such correction, `Chemical challenge` ->
    `Chemical Challenge` in assay_context. Leaving the key out of the SET list
    keeps production's old spelling while reporting a successful write.
    """
    spec = cg.TABLES["projects"]
    sql = cg.render_update("projects", _rows_for("projects"))
    tail = sql.split("ON DUPLICATE KEY UPDATE", 1)[1].split(";", 1)[0]
    for column in spec.columns:
        assert f"`{column}`=VALUES(`{column}`)" in tail, column


def test_update_removes_rows_the_source_no_longer_names():
    sql = cg.render_update("projects", _rows_for("projects"))
    assert "DELETE FROM `projects_context` WHERE `name` NOT IN (" in sql
    assert "'CSBC'" in sql.split("NOT IN (", 1)[1].split(")", 1)[0]
    # And collapses duplicate keys, which production's assay_context has 22 of.
    assert "DELETE `a` FROM `projects_context` `a`" in sql


def test_update_adds_the_unique_key_the_upsert_needs_and_any_new_column():
    sql = cg.render_update("projects", _rows_for("projects"))
    assert "uq_projects_context_name" in sql
    assert "information_schema" in sql          # the idempotent add, not a bare ALTER
    assert f"`pi_names` {cg.ADDED_COLUMNS['projects']['pi_names']}" in sql
    sample = cg.render_update("sample_types", _rows_for("sample_types"))
    added = cg.ADDED_COLUMNS["sample_types"]["repository_attributes"]
    assert f"`repository_attributes` {added}" in sample


def test_update_escapes_a_quote_by_doubling_it():
    rows = [{"name": "Griffith", "pi": "O'Neill, Pat (MIT)"}]
    sql = cg.render_update("projects", cg.with_pi_names(rows))
    assert "'O''Neill, Pat (MIT)'" in sql
    assert "\\'" not in sql                     # no backslash escapes: see literal()


def test_update_refuses_a_backslash_rather_than_corrupting_it():
    import pytest

    rows = [{"name": "Backslash", "description": r"a\b"}]
    with pytest.raises(cg.UnsupportedValue):
        cg.render_update("projects", cg.with_pi_names(rows))


def test_update_writes_json_columns_as_json_text():
    sql = cg.render_update("projects", _rows_for("projects"))
    assert '\'["BTC", "Breakthrough Cancer"' in sql
    assert '\'["White", "Forest M. White", "Michor", "Franziska Michor"]\'' in sql


def test_update_is_deterministic():
    rows = _rows_for("assays")
    assert cg.render_update("assays", rows) == cg.render_update("assays", rows)


def test_update_refuses_a_duplicate_key_in_the_source():
    import pytest

    rows = [{"name": "CSBC"}, {"name": "csbc "}]
    with pytest.raises(cg.DuplicateKey):
        cg.render_update("projects", cg.with_pi_names(rows))


def test_no_curated_value_needs_a_backslash():
    """The precondition `literal` refuses on, checked against the real files.

    `literal` will not guess at a backslash because MySQL interprets one inside a
    string literal and SQLite does not. That is only safe while no curated value
    contains one, so this is where that is checked rather than assumed.
    """
    for table in ("sample_types", "assays", "projects"):
        for row in cg.load_source(cg.TABLES[table].source):
            for column, value in row.items():
                rendered = cg.db_value(table, column, value)
                assert "\\" not in str(rendered or ""), f"{table}.{column}"


# --- 6.10 the seed SQL -------------------------------------------------------
#
# A fresh install gets these tables from startup/seed/sql/, not from
# startup/seed/dmac.sql.gz: none of the three has a Django migration and
# regenerating that dump needs maintainer credentials for a remote host
# (startup/seed/README.md). assay_context.sql is the template, header comment
# included, so a regenerated file is a diff of rows rather than of shape.
#
# The seed files keep their committed one-line-per-INSERT style, which escapes
# newlines as `\n` the way MySQL reads them. The update SQL deliberately does not
# (see cg.literal), because that spelling means something else in SQLite.

SEED_PATHS = {
    "sample_types": Path("startup/seed/sql/sample_types_context.sql"),
    "assays": Path("startup/seed/sql/assay_context.sql"),
    "projects": Path("startup/seed/sql/projects_context.sql"),
}


def test_seed_ddl_declares_exactly_the_columns_the_module_writes():
    for table, spec in cg.TABLES.items():
        assert _ddl_columns(cg.DDL[table], spec.name) == list(spec.columns), table


def test_seed_ddl_declares_the_unique_key_the_upsert_needs():
    for table, spec in cg.TABLES.items():
        assert f"UNIQUE KEY `uq_{spec.name}_{spec.key}` (`{spec.key}`)" in cg.DDL[table], table


def test_seed_writes_the_ddl_then_one_insert_per_line():
    for table, expected in (("sample_types", 109), ("assays", 138), ("projects", 12)):
        sql = cg.render_seed(table, _rows_for(table))
        assert sql.startswith("-- ")                      # the header comment
        assert cg.DDL[table] in sql
        inserts = [line for line in sql.splitlines() if line.startswith("INSERT INTO ")]
        assert len(inserts) == expected, table
        assert all(line.endswith(");") for line in inserts), table
        assert "ON DUPLICATE KEY UPDATE" not in sql, table   # a seed loads once
        assert sql.endswith("\n")


def test_seed_escapes_a_newline_so_every_insert_is_one_line():
    # 79 curated sample type values and 30 assay values contain a newline.
    sql = cg.render_seed("sample_types", _rows_for("sample_types"))
    body = sql.split(");", 1)[1]                            # past the CREATE TABLE
    assert "\\n" in sql
    for line in body.splitlines():
        assert line.startswith(("INSERT INTO ", "--", "")) or not line.strip(), line


def test_seed_matches_the_committed_files_shape():
    """The regenerated files are what is committed, so 6.13 is a diff of rows."""
    for table, path in SEED_PATHS.items():
        committed = _repo(path)
        rendered = cg.render_seed(table, _rows_for(table))
        assert committed == rendered, f"{path} is stale; regenerate it"


def test_the_retired_assay_seed_generator_is_gone():
    """Decided at 6.10: retired, not repointed.

    scripts/generate_assay_context_seed.py wrote the same file from a committed
    JSON export of production. Two programs writing startup/seed/sql/
    assay_context.sql from different sources is how the file goes stale without
    anyone noticing, and context/ is now the source of truth.
    """
    assert not (Path(cg.REPO_ROOT) / "scripts/generate_assay_context_seed.py").exists()
    assert "context_gen.py" in _repo(Path("scripts/README.md"))


def test_render_seed_refuses_the_mapping_operations():
    import pytest

    with pytest.raises(ValueError):
        cg.render_seed("mappings", [])

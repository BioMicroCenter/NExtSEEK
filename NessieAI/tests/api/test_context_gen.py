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


# --- the mapping operations --------------------------------------------------
#
# context/assay_mappings.json is not a table. It is 95 operations on rows that
# already exist in dmac.internal_assays and dmac.assays_internal_assays, applied
# grouped in the order context/README.md gives: renames, creates, maps and
# remaps, merges. Every `from_*` key is a production value so the generator can
# refuse if production has moved; each statement's WHERE clause is that refusal.
#
# Measured against the 2026-09-11 production pull, which is why each guard is
# there rather than defensive:
#
#   * all 25 `map` ops name exactly the 25 assays_internal_assays rows whose
#     internal_assay_id is NULL;
#   * all 39 `remap` ops match a row carrying the stated from_internal_assay_id,
#     and no SEEK assay has more than one row;
#   * all 13 `merge_internal` ids exist, and all 4 renames match their from_title;
#   * 2 of the 14 `create_internal` titles ALREADY exist in production, and they
#     are exactly the 2 the renames free up, which is what makes the group order
#     load-bearing rather than stylistic.


def test_mappings_render_every_operation_in_the_readme_order():
    rows = cg.load_source(cg.TABLES_EXTRA["mappings"])
    sql = cg.render_update("mappings", rows)
    order = [sql.index(f"-- {group}") for group in
             ("rename_internal", "create_internal", "map and remap", "merge_internal")]
    assert order == sorted(order)
    assert sql.count("UPDATE `internal_assays`") == 4                  # renames
    assert sql.count("INSERT INTO `internal_assays`") == 14            # creates
    assert sql.count("UPDATE `assays_internal_assays`") == 25 + 39     # maps and remaps
    assert sql.count("DELETE FROM `internal_assays`") == 13            # merges


def test_a_map_only_fills_a_null_and_a_remap_only_moves_the_stated_id():
    rows = cg.load_source(cg.TABLES_EXTRA["mappings"])
    sql = cg.render_update("mappings", rows)
    # map: seek assay 466 -> Library Creation, and only while it is still NULL
    assert ("UPDATE `assays_internal_assays` SET `internal_assay_id` = "
            "(SELECT `id` FROM `internal_assays` WHERE `internal_assay_title` = "
            "'Library Creation' ORDER BY `id` LIMIT 1)\n"
            "  WHERE `assay_id` = 466 AND `internal_assay_id` IS NULL;") in sql
    # remap: seek assay 37 moves off 130, and re-running is a no-op
    assert "WHERE `assay_id` = 37 AND `internal_assay_id` IN (130, (SELECT `id`" in sql


def test_a_create_is_a_no_op_on_a_second_run():
    rows = cg.load_source(cg.TABLES_EXTRA["mappings"])
    sql = cg.render_update("mappings", rows)
    assert sql.count("WHERE NOT EXISTS (SELECT 1 FROM `internal_assays`") == 14


def test_the_renames_have_to_run_before_the_creates():
    """The group order is load-bearing, and this is the measurement that says so.

    Two `create_internal` titles exist in production today, `Mass Spectrometry` and
    `Mass Spectrometry Analysis`, and they are exactly the two the renames free up:
    internal assay 130 becomes `Mass Spectrometry Proteomics` and 47 becomes `Mass
    Spectrometry Proteomics Analysis`. Run the creates first and their NOT EXISTS
    guard skips both, so the two new internal assays never exist and every remap
    aimed at them resolves to the old id instead.
    """
    rows = cg.load_source(cg.TABLES_EXTRA["mappings"])
    freed = {r["from_title"] for r in rows if r["action"] == "rename_internal"}
    created = {r["internal_assay_title"] for r in rows if r["action"] == "create_internal"}
    assert freed & created == {"Mass Spectrometry", "Mass Spectrometry Analysis"}
    sql = cg.render_update("mappings", rows)
    for title in sorted(freed & created):
        # The rename that frees this exact title, not merely the first rename.
        frees_it = sql.index(f"AND `internal_assay_title` IN ('{title}',")
        create_at = sql.index(f"SELECT '{title}' FROM DUAL")
        assert frees_it < create_at, title


def test_a_merge_refuses_while_any_seek_assay_still_points_at_it():
    rows = cg.load_source(cg.TABLES_EXTRA["mappings"])
    sql = cg.render_update("mappings", rows)
    assert ("DELETE FROM `internal_assays` WHERE `id` = 174 AND "
            "`internal_assay_title` = 'Library Preparation'\n"
            "  AND NOT EXISTS (SELECT 1 FROM `assays_internal_assays` "
            "WHERE `internal_assay_id` = 174);") in sql


def test_mappings_refuse_an_unknown_action():
    import pytest

    with pytest.raises(cg.UnknownAction):
        cg.render_update("mappings", [{"action": "delete_everything"}])


def test_mappings_refuse_a_missing_key_for_a_known_action():
    import pytest

    with pytest.raises(cg.UnknownColumn):
        cg.render_update("mappings", [{"action": "create_internal", "oops": "x"}])


def test_every_new_internal_assay_is_named_by_both_files():
    """context/README.md: a row in assays.json with internal_assay_id null IS a
    create_internal entry, named identically. 14 each way, so a rename in one
    file that misses the other fails here rather than at write time."""
    assays = cg.load_source(cg.TABLES["assays"].source)
    mappings = cg.load_source(cg.TABLES_EXTRA["mappings"])
    cg.check_mapping_consistency(assays, mappings)
    nameless = {r["assay_name"] for r in assays if r.get("internal_assay_id") is None}
    created = {m["internal_assay_title"] for m in mappings if m["action"] == "create_internal"}
    assert nameless == created and len(created) == 14


def test_a_created_internal_assay_with_no_assays_row_is_refused():
    import pytest

    with pytest.raises(cg.MappingMismatch):
        cg.check_mapping_consistency(
            [{"assay_name": "Kept", "internal_assay_id": 1}],
            [{"action": "create_internal", "internal_assay_title": "Orphan"}],
        )


# --- 6.11 the round trip -----------------------------------------------------
#
# Generate the update SQL, apply it to a real engine, SELECT * back and compare
# field for field with the curated rows. This is the test that makes the generator
# safe to point at production: everything above checks the shape of the text, and
# only this one checks that a value survives becoming a SQL literal.
#
# The engine is SQLite because the lane has no database. Two things are translated
# and nothing else, both named here so it is clear what is NOT being tested:
#
#   1. the schema, built from cg.TABLES[...].columns rather than from cg.DDL,
#      because MySQL column types would have to be translated too and
#      test_seed_ddl_declares_exactly_the_columns_the_module_writes already pins
#      the two against each other;
#   2. the upsert tail, `ON DUPLICATE KEY UPDATE c=VALUES(c)` ->
#      `ON CONFLICT(key) DO UPDATE SET c=excluded.c`.
#
# The VALUES list, which is the part under test, passes through untouched. That is
# why cg.literal doubles quotes and keeps newlines literal instead of using MySQL's
# backslash escapes: the same text means the same thing to both engines, so this
# test is not checking an escaper against its own inverse.

import sqlite3


def _sqlite_schema(table: str) -> str:
    spec = cg.TABLES[table]
    columns = ["`id` INTEGER PRIMARY KEY AUTOINCREMENT"]
    for column in spec.columns:
        columns.append(f"`{column}` " + ("INTEGER" if column in spec.int_columns else "TEXT"))
    columns.append(f"UNIQUE(`{spec.key}`)")
    return f"CREATE TABLE `{spec.name}` (\n  " + ",\n  ".join(columns) + "\n);"


def _translate(sql: str, table: str) -> str:
    """The two named substitutions, and a count so nothing else slipped through."""
    spec = cg.TABLES[table]
    assignments = ", ".join(f"`{c}`=excluded.`{c}`" for c in spec.columns)
    out = sql.replace(
        "ON DUPLICATE KEY UPDATE " + ", ".join(f"`{c}`=VALUES(`{c}`)" for c in spec.columns),
        f"ON CONFLICT(`{spec.key}`) DO UPDATE SET {assignments}",
    )
    assert "VALUES(`" not in out                      # every tail was translated
    return out


def _apply(conn, table: str, rows: list[dict], *, preload=()) -> None:
    sql = cg.render_update(table, rows)
    head, marker, body = sql.partition(cg.ROWS_MARKER)
    assert marker, "render_update stopped emitting the rows marker"
    conn.executescript(_sqlite_schema(table))
    for statement in preload:
        conn.execute(statement)
    # The only statement from the preamble that is not MySQL-only: it is what
    # removes the rows the curated source no longer names.
    delete = next(line for line in head.splitlines() if line.startswith("DELETE FROM "))
    conn.executescript(_translate(delete + "\n" + body, table))


def _read_back(conn, table: str) -> list[dict]:
    spec = cg.TABLES[table]
    conn.row_factory = sqlite3.Row
    cursor = conn.execute(f"SELECT * FROM `{spec.name}` ORDER BY `id`")
    return [dict(row) for row in cursor.fetchall()]


def test_update_sql_round_trips_every_curated_field():
    for table in ("sample_types", "assays", "projects"):
        spec = cg.TABLES[table]
        rows = _rows_for(table)
        with sqlite3.connect(":memory:") as conn:
            _apply(conn, table, rows)
            stored = _read_back(conn, table)
        assert len(stored) == len(rows), table
        for curated, back in zip(rows, stored):
            assert set(back) == set(spec.columns) | {"id"}, table
            for column in spec.columns:
                assert back[column] == cg.db_value(table, column, curated.get(column)), \
                    f"{table}.{column} of {curated[spec.key]!r}"


def test_a_value_with_a_newline_and_an_apostrophe_survives_byte_for_byte():
    """Named values rather than a normalizer, so this cannot agree with itself."""
    rows = [{
        "name": "Quote and newline",
        "description": "It's two lines.\nSecond line, with 'quotes' and a % sign.",
        "pi": "O'Neill, Pat (MIT)",
        "alternative_names": ["a'b", "plain"],
    }]
    with sqlite3.connect(":memory:") as conn:
        _apply(conn, "projects", cg.with_pi_names(rows))
        back = _read_back(conn, "projects")[0]
    assert back["description"] == "It's two lines.\nSecond line, with 'quotes' and a % sign."
    assert back["pi"] == "O'Neill, Pat (MIT)"
    assert back["alternative_names"] == '["a\'b", "plain"]'
    assert back["pi_names"] == '["O\'Neill", "Pat O\'Neill"]'


def test_a_newline_inside_a_json_column_is_refused_and_says_why():
    """The generator's one declared limit, pinned rather than discovered later.

    A newline inside a JSON column survives json.dumps as the two characters `\n`,
    and a backslash is the one thing cg.literal will not guess at. No curated value
    has one (test_no_curated_value_needs_a_backslash), and a plain text column takes
    a newline literally, so this is the narrow case: a list or dict value whose text
    contains one.
    """
    import pytest

    rows = cg.with_pi_names([{"name": "Wrapped", "alternative_names": ["two\nlines"]}])
    with pytest.raises(cg.UnsupportedValue) as excinfo:
        cg.render_update("projects", rows)
    assert "backslash" in str(excinfo.value)


def test_update_sql_is_a_no_op_on_a_second_run():
    table = "assays"
    rows = _rows_for(table)
    with sqlite3.connect(":memory:") as conn:
        _apply(conn, table, rows)
        once = _read_back(conn, table)
        head, _, body = cg.render_update(table, rows).partition(cg.ROWS_MARKER)
        delete = next(line for line in head.splitlines() if line.startswith("DELETE FROM "))
        conn.executescript(_translate(delete + "\n" + body, table))
        twice = _read_back(conn, table)
    assert once == twice
    assert len(twice) == 138


def test_update_sql_drops_a_stale_row_and_updates_an_existing_one_in_place():
    """The production case, not a hypothetical one.

    projects_context holds a `GBM` row the curated source drops, and 11 of its 12
    curated names are already there with older content. So: a row whose key the
    source no longer names goes, and a row whose key it does name is updated
    without changing its id.
    """
    rows = _rows_for("projects")
    with sqlite3.connect(":memory:") as conn:
        _apply(conn, "projects", rows, preload=(
            "INSERT INTO `projects_context` (`name`, `description`) VALUES ('GBM', 'stale');",
            "INSERT INTO `projects_context` (`name`, `description`) VALUES ('CSBC', 'old');",
        ))
        stored = {row["name"]: row for row in _read_back(conn, "projects")}
    assert "GBM" not in stored
    assert stored["CSBC"]["id"] == 2                      # updated in place, not reinserted
    assert stored["CSBC"]["description"] != "old"
    assert len(stored) == 12


# --- 6.15 the generated investigation block ----------------------------------
#
# capabilities.md's "Known Projects and Investigations" section lists eight names
# and tells the agent to "use these names exactly". Five of the eight return
# nothing: SEEK carries two parallel investigation systems, and the list names the
# paper-tracking copies in TestProject_250820 (38 bibliographic studies, zero
# samples) rather than the real investigations that hold the samples. Measured on
# the live 1.2 graph and confirmed against the 2026-09-11 production pull; a sync
# does not repair it.
#
# Operator decision, 2026-09-17: do not hand-edit that list, generate it. So the
# section becomes a marked generated block filled from projects_context, and the
# generator REFUSES an investigation that resolves to zero samples. That refusal is
# the whole point: the defect cannot be committed in the first place.
# catalog.assistant_investigations in nextseek_api/graph_sync/drift.py stays as the
# runtime backstop for the case where the data moves under a correct file.
#
# This module owns the renderer only. The investigation rows are 6.15c and the
# consumer audit is 6.15d, both gated on the operator's xlsx review, and nothing
# here edits capabilities.md.

# The five that resolve to nothing today, and what they should say, from the plan's
# measured table. Sample counts are used to DECIDE, never emitted.
LIVE_COUNTS = {
    "Impactb Investigation": 84394, "MIT_SRP": 56004, "GBM_BTC": 4564,
    "Endometriosis": 3247, "Collagen Study": 568, "CSBC": 3643, "MetNet": 10379,
    "TCGA": 918519,
}
DEAD_NAMES = ("Impact", "SRP", "GBM", "Griffith", "Shoulders")


def _investigation(name, **extra):
    row = {"name": name, "entity_type": "investigation", "parent_project": "MIT-Koch",
           "project_id": 5, "research_focus": f"What {name} studies.",
           "alternative_names": [], "pi": None}
    row.update(extra)
    return row


def test_capabilities_block_is_one_marked_generated_block():
    block = cg.render_capabilities_block([_investigation("TCGA")], {"TCGA": 918519})
    assert block.startswith(cg.CAPABILITIES_BEGIN)
    assert block.rstrip("\n").endswith(cg.CAPABILITIES_END)
    assert "BEGIN" in cg.CAPABILITIES_BEGIN and "END" in cg.CAPABILITIES_END
    assert cg.CAPABILITIES_BEGIN.startswith("<!--") and cg.CAPABILITIES_END.endswith("-->")


def test_capabilities_block_lists_investigations_and_skips_projects():
    """Only investigations. The section's names are checked against Investigation
    nodes, so a project row that is not also an investigation title would make the
    drift check fail for a row that is perfectly correct."""
    rows = [_investigation("TCGA"),
            {"name": "MIT-Koch", "entity_type": "project", "research_focus": "A program."}]
    block = cg.render_capabilities_block(rows, {"TCGA": 918519})
    assert "**TCGA**" in block
    assert "MIT-Koch" not in block


def test_capabilities_block_carries_no_counts():
    """A baked count rots the day the next sync runs, and the repo's doc rules
    forbid a dated count in a README or CLAUDE file. Live counts reach the graph
    agent through the catalog reader instead."""
    rows = [_investigation(name) for name in sorted(LIVE_COUNTS)]
    block = cg.render_capabilities_block(rows, LIVE_COUNTS)
    assert not re.search(r"\d", block), "the block must carry no digits at all"
    for count in LIVE_COUNTS.values():
        assert str(count) not in block and f"{count:,}" not in block


def test_capabilities_block_refuses_an_investigation_with_no_samples():
    """The refusal that is the whole point of generating this section."""
    import pytest

    rows = [_investigation("TCGA")] + [_investigation(name) for name in DEAD_NAMES]
    with pytest.raises(cg.ZeroSampleInvestigation) as excinfo:
        cg.render_capabilities_block(rows, LIVE_COUNTS)
    message = str(excinfo.value)
    for name in DEAD_NAMES:
        assert name in message, name
    assert "TCGA" not in message


def test_capabilities_block_refuses_a_name_the_counts_do_not_mention():
    """Absent is not zero, but it is not evidence either."""
    import pytest

    with pytest.raises(cg.ZeroSampleInvestigation):
        cg.render_capabilities_block([_investigation("Nowhere")], {"TCGA": 918519})


def test_capabilities_block_refuses_with_no_counts_at_all():
    import pytest

    with pytest.raises(cg.ZeroSampleInvestigation):
        cg.render_capabilities_block([_investigation("TCGA")])


def test_capabilities_block_refuses_when_no_row_is_an_investigation():
    """Today's live state: all 12 projects_context rows are projects.

    Emitting an empty list would silently delete the agent's only list of
    investigations, so this says to add the rows (6.15c) first.
    """
    import pytest

    with pytest.raises(cg.NoInvestigations):
        cg.render_capabilities_block(cg.with_pi_names(_rows_for("projects")), LIVE_COUNTS)


def test_capabilities_block_refuses_an_investigation_with_nothing_to_say():
    import pytest

    row = _investigation("TCGA", research_focus=None, description=None)
    with pytest.raises(cg.IncompleteInvestigation):
        cg.render_capabilities_block([row], {"TCGA": 918519})


def test_capabilities_block_bridges_what_users_type_to_the_exact_title():
    """`Impact` has to reach `Impactb Investigation` instead of failing silently."""
    row = _investigation("Impactb Investigation",
                         research_focus="Tuberculosis in non-human primates.",
                         alternative_names=["Impact", "IMPAcTb"])
    block = cg.render_capabilities_block([row], LIVE_COUNTS)
    assert "**Impactb Investigation**" in block
    assert "Impact" in block and "IMPAcTb" in block


def test_capabilities_block_sorts_by_name_and_one_bullet_per_row():
    rows = [_investigation(name) for name in ("TCGA", "CSBC", "MetNet")]
    block = cg.render_capabilities_block(rows, LIVE_COUNTS)
    bullets = [line for line in block.splitlines() if line.startswith("- **")]
    assert len(bullets) == 3
    assert [b.split("**")[1] for b in bullets] == ["CSBC", "MetNet", "TCGA"]


def test_capabilities_block_falls_back_to_the_first_sentence_of_the_description():
    row = _investigation("TCGA", research_focus=None,
                         description="Public pan-cancer atlas. Many more sentences follow.")
    block = cg.render_capabilities_block([row], LIVE_COUNTS)
    assert "Public pan-cancer atlas." in block
    assert "Many more sentences" not in block


def test_the_drift_check_reads_exactly_the_names_the_block_emits():
    """The generator and the runtime backstop have to agree, so this uses the real
    parser rather than a copy of its regex. Same function drift.py calls after every
    ./startup.sh rebuild."""
    from nextseek_api.graph_sync import drift

    names = ["CSBC", "Collagen Study", "Endometriosis", "GBM_BTC",
             "Impactb Investigation", "MIT_SRP", "MetNet", "TCGA"]
    rows = [_investigation(name) for name in names]
    block = cg.render_capabilities_block(rows, LIVE_COUNTS)
    document = ("## Known Projects and Investigations\n\n" + block +
                "\n---\n\n## What the System Cannot Do\n\n- **Generate charts** nope\n")
    assert drift.assistant_investigation_names(document) == sorted(names)


def test_the_block_replaces_the_section_body_between_its_markers():
    """The substitution the chain needs, as text: context_gen writes the block into
    capabilities.md, then gen_op_surfaces reads capabilities.md to regenerate
    route_capabilities.json. Running those out of order ships a
    route_capabilities.json built from the old list."""
    block = cg.render_capabilities_block([_investigation("TCGA")], {"TCGA": 918519})
    before = ("## Known Projects and Investigations\n\n"
              f"{cg.CAPABILITIES_BEGIN}\nold text\n{cg.CAPABILITIES_END}\n\n---\n")
    after = cg.replace_capabilities_block(before, block)
    assert "old text" not in after
    assert "**TCGA**" in after
    assert after.count(cg.CAPABILITIES_BEGIN) == 1
    assert after.endswith("\n---\n")
    assert cg.replace_capabilities_block(after, block) == after      # idempotent


def test_replacing_the_block_refuses_a_document_with_no_markers():
    import pytest

    with pytest.raises(ValueError):
        cg.replace_capabilities_block("## Known Projects and Investigations\n\n- **X** y\n", "b")


def test_a_dead_name_may_survive_as_an_alternative_but_never_as_a_checked_name():
    """The trap in the bridging design, pinned.

    `SRP` is one of the five names that resolve to nothing, and it is also what
    people type for `MIT_SRP`. It has to reach the agent as an alias without
    becoming a name the drift check then looks up and fails on. Only the bold term
    is checked, so an alternative in brackets is safe -- as long as it stays out of
    the bold run.
    """
    from nextseek_api.graph_sync import drift

    row = _investigation("MIT_SRP", research_focus="Environmental exposure and DNA damage.",
                         alternative_names=["SRP"])
    block = cg.render_capabilities_block([row], LIVE_COUNTS)
    assert "[also: SRP]" in block
    document = "## Known Projects and Investigations\n\n" + block + "\n---\n"
    assert drift.assistant_investigation_names(document) == ["MIT_SRP"]
    for name in DEAD_NAMES:
        assert name not in drift.assistant_investigation_names(document)


def test_an_alternative_name_never_carries_markdown_that_would_split_the_bold_run():
    """The regex captures `[^*]+`, so a `*` in a name would truncate it."""
    import pytest

    row = _investigation("Bad*Name", research_focus="Anything.")
    with pytest.raises(cg.UnsupportedValue):
        cg.render_capabilities_block([row], {"Bad*Name": 1})

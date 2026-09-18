"""scripts/context_gen.py, the generator that turns context/ into database writes.

The five JSON files Nessie reads are exports of three MySQL tables, rewritten in
place once per UTC day by `_fetch_context_files_from_db`
(`NessieAI/chat_nextseek/src/chat_nextseek/config.py:717-725`). Editing an export
changes nothing that survives a day, so the curated content in `context/` reaches
a database only through this generator.

No database: everything here reads committed JSON and renders text, and the one
engine it uses is an in-memory SQLite for the plain row statements. Five tests do
need Django, because they import nextseek_api.graph_sync.drift to check the
capabilities block against drift's own parser; the rest run under `--noconftest`
with no Django at all. What only MySQL can prove -- target widths and charsets, the
guards, the transaction, the checks -- is test_context_gen_mysql.py's.
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
    """The DDL check, against fixtures this module does not write.

    The installer's startup/seed/sql/assay_context.sql and projects_context.sql
    are the pre-generator files (the generator's own output is held in the
    `.curated.sql` files beside them), and the model is Django's, so all three
    are independent of `cg.DDL`. `test_every_column_is_one_the_runtime_actually_reads`,
    below, checks the same columns against the consumer.
    """
    assert set(cg.COLUMNS["assays"]) == _assay_seed_columns()
    assert set(cg.COLUMNS["sample_types"]) == _model_columns() | {"repository_attributes"}
    assert set(cg.COLUMNS["projects"]) == _projects_ddl_columns()
    # Everything the fixtures do not name is declared as new, with a reason.
    assert set(cg.ADDED_COLUMNS) == {"sample_types"}
    assert set(cg.ADDED_COLUMNS["sample_types"]) == {"repository_attributes"}


CONFIG = Path("NessieAI/chat_nextseek/src/chat_nextseek/config.py")


def _reader_columns(mapper: str) -> set[str]:
    """The raw column spellings one of config.py's mappers reads.

    `map_sampletype`, `map_assay` and `map_project` each lowercase the row and then
    name every column they want, which is why the names come back lowercase and the
    comparison below folds both sides: the one column whose live spelling is
    capitalised, `Tags`, is read as `tags`. That list is written in a file this
    module does not own, by the code that actually consumes these tables, so it is
    the fixture `test_columns_match_the_fixtures_that_define_them` can no longer be.
    """
    body = _repo(CONFIG).split(f"def {mapper}(row: dict) -> dict:", 1)[1]
    body = body.split("\n            def ", 1)[0]
    return set(re.findall(r'lower\.get\("(\w+)"\)', body))


def test_every_column_is_one_the_runtime_actually_reads():
    """A column dropped from the module would otherwise pass every test.

    `check_columns` raises on an UNKNOWN key, never on a missing one, so deleting a
    column from `cg.TABLES` regenerates a DDL without it, keeps the whole suite
    green, and makes the production upsert quietly stop setting it -- the apply
    reports success while production keeps a stale value there forever. This is the
    check that sees it, because its fixture is the consumer.

    The one exception is named rather than subtracted, because it is a real open
    gap and not a convention: `repository_attributes` is written to the database
    and read by nothing, since `map_sampletype` builds its export from a fixed key
    list, and it has no named consumer at all. Projects have none: the generated
    `pi_names` column, which nothing read, is retired (spec 2026-09-18, section 8),
    and `present_on` is a generator-only key, never a column.
    """
    unread = {"projects": set(), "sample_types": {"repository_attributes"},
              "assays": set()}
    for table, mapper in (("sample_types", "map_sampletype"), ("assays", "map_assay"),
                          ("projects", "map_project")):
        reads = _reader_columns(mapper)
        assert reads, mapper
        declared = {c.lower() for c in cg.COLUMNS[table]}
        assert declared - reads == unread[table], table    # nothing written unread
        assert reads - declared == set(), table            # nothing read unwritten


def test_every_curated_key_maps_to_a_known_column():
    for table in ("sample_types", "assays", "projects"):
        rows = cg.load_source(cg.TABLES[table].source)
        assert rows, table
        cg.check_columns(table, rows)          # raises if any key is unknown
        keys = {k for row in rows for k in row}
        # present_on is the one curated key that is not a column: generator-only.
        assert keys <= set(cg.COLUMNS[table]) | set(cg.TABLES[table].generator_only), table
        assert "id" not in keys, table          # the autoincrement is the database's
    assert cg.TABLES["projects"].generator_only == ("present_on",)
    assert all(not spec.generator_only for name, spec in cg.TABLES.items() if name != "projects")


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


# --- pi is display prose ------------------------------------------------------
#
# `pi` stays as curated display prose: the project page shows it and the entity LLM
# reads it as context. Nothing parses it any more (spec 2026-09-18, section 8). Lab
# codes and lab heads' surnames come from SEEK's institution titles instead, so the
# parser, `with_pi_names` and the generated `pi_names` column are retired: a second
# source parsed from free text would disagree with the first. No database ever had
# the column (the gated write never ran), so retiring it costs no migration.


def test_pi_is_display_prose_and_nothing_parses_it():
    assert not hasattr(cg, "parse_pi")
    assert not hasattr(cg, "with_pi_names")
    assert "pi_names" not in cg.COLUMNS["projects"]
    assert "pi_names" not in cg.DDL["projects"]
    assert "pi_names" not in cg.ADDED_COLUMNS.get("projects", {})
    assert "pi" in cg.COLUMNS["projects"]


def test_a_curated_pi_names_is_refused_as_an_unknown_column():
    """No special case: once the column is gone, the ordinary column check refuses it."""
    import pytest

    with pytest.raises(cg.UnknownColumn) as excinfo:
        cg.check_columns("projects", [{"name": "X", "entity_type": "project", "pi_names": ["Doe"]}])
    assert "pi_names" in str(excinfo.value)


def test_a_pi_spelled_as_nothing_is_stored_as_null():
    assert cg.db_value("projects", "pi", "None") is None
    assert cg.db_value("projects", "pi", " n/a ") is None
    assert cg.db_value("projects", "pi", "Doe, Jane") == "Doe, Jane"


def test_the_curated_pi_text_is_written_unchanged():
    """The free text reaches the database as curated, whatever shape it has."""
    rows = [{"name": "Zephyr", "entity_type": "project",
             "pi": "Doe, Jane (Example Institute, PI; Director, Example Center)"}]
    sql = cg.render_update("projects", rows)
    assert "'Doe, Jane (Example Institute, PI; Director, Example Center)'" in sql


# --- 6.9 the update SQL ------------------------------------------------------
#
# What "idempotent" has to mean here:
#
#   * one UPDATE and one guarded INSERT per curated row, so re-running the script
#     changes no row. Not INSERT ... ON DUPLICATE KEY UPDATE: that needs the unique
#     key first, adding the key needs the duplicates gone first, and a dedupe before
#     the rows ran outside any transaction;
#   * production's assay_context holds names the curated source no longer carries
#     and duplicated names, and projects_context a row the source drops. Upserting
#     alone would leave every one of those behind while reporting success, so the
#     script deletes what the source no longer names and collapses duplicate keys;
#   * every value escaped, and no literal autoincrement `id`.
#
# test_context_gen_mysql.py applies all of it to a real MySQL; the checks here are
# on the text.

INSERT_RE = re.compile(r"^INSERT INTO ", re.M)


def _rows_for(table: str) -> list[dict]:
    rows = cg.load_source(cg.TABLES[table].source)
    return cg.rows_for(table) if table == "projects" else rows


def test_update_writes_one_update_and_one_guarded_insert_per_row():
    for table, expected in (("sample_types", 109), ("assays", 138), ("projects", 21)):
        spec = cg.TABLES[table]
        sql = cg.render_update(table, _rows_for(table))
        assert len(INSERT_RE.findall(sql)) == expected, table
        assert len(re.findall(rf"^UPDATE `{spec.name}` SET `{spec.columns[0]}` = ", sql, re.M)) \
            == expected, table
        assert sql.count(f"WHERE NOT EXISTS (SELECT 1 FROM `{spec.name}` WHERE") == expected, table
        assert "ON DUPLICATE KEY UPDATE" not in sql and "VALUES(`" not in sql, table


def test_update_never_writes_the_autoincrement_id():
    for table in ("sample_types", "assays", "projects"):
        spec = cg.TABLES[table]
        sql = cg.render_update(table, _rows_for(table))
        for statement in sql.split(f"INSERT INTO `{spec.name}` ")[1:]:
            columns = statement.split("(", 1)[1].split(")", 1)[0]
            assert "`id`" not in columns, table
        assert "SET `id`" not in sql and ", `id` =" not in sql, table


def test_update_sets_every_column_including_the_key():
    """The key is reassigned too, and that is not redundant.

    The WHERE matches the key case-insensitively, so a row whose key differs only
    in case is updated in place. The curated data holds exactly one such
    correction, `Chemical challenge` -> `Chemical Challenge` in assay_context.
    Leaving the key out of the SET list keeps the old spelling while reporting a
    successful write.
    """
    spec = cg.TABLES["projects"]
    sql = cg.render_update("projects", _rows_for("projects"))
    statement = sql.split(f"UPDATE `{spec.name}` SET ", 1)[1].split(";\n", 1)[0]
    for column in spec.columns:
        assert f"`{column}` = " in statement, column
    # assay_context's link is the database's, resolved by title, so the row
    # statements never write the curated number.
    assays = cg.render_update("assays", _rows_for("assays"))
    rows_part = assays.split(cg.ROWS_MARKER, 1)[1].split(cg.MYSQL_ONLY_MARKER, 1)[0]
    assert "`internal_assay_id`" not in rows_part


def test_update_removes_rows_the_source_no_longer_names():
    sql = cg.render_update("assays", _rows_for("assays"))
    assert "DELETE FROM `assay_context` WHERE `assay_name` NOT IN (" in sql
    # projects_context keys on the pair, so its delete names each (name, entity_type).
    sql = cg.render_update("projects", _rows_for("projects"))
    delete = sql.split("DELETE FROM `projects_context` WHERE NOT (", 1)[1].split(";", 1)[0]
    assert "(`name` = 'CSBC' AND `entity_type` = 'project')" in delete
    # And collapses duplicate keys, which production's assay_context has 22 of.
    assert "DELETE `a` FROM `projects_context` `a`" in sql


def test_a_row_with_no_key_at_all_is_deleted_too():
    """`NULL NOT IN (...)` is NULL, not TRUE, so the plain delete never saw one.

    A unique key permits any number of NULLs and no upsert matches one either, so
    such a row survived every run untouched while the script's own header promised
    the table would hold exactly the curated rows. Proven on MySQL: a preloaded
    `(assay_name=NULL)` row came back after run 1 and run 2 unchanged.
    """
    for table, spec in cg.TABLES.items():
        sql = cg.render_update(table, _rows_for(table))
        nulls = " OR ".join(f"`{column}` IS NULL" for column in spec.key_columns)
        assert f"OR {nulls};" in sql, table


def test_the_dedupe_runs_only_where_there_is_an_id_to_order_by():
    """The statement that aborted the apply on the table it was written for.

    `dmac.projects_context` has NO `id` column -- measured on the running stack,
    where its PRIMARY KEY is `name`, and confirmed by the 2026-09-11 production
    pull, whose projects_context rows carry no `id` key while sample_types_context
    and assay_context both do. Emitted unconditionally, `DELETE a ... AND a.id >
    b.id` answered `ERROR 1054 Unknown column 'a.id' in 'on clause'` AFTER step 2
    had deleted GBM and before a single curated row was written, and every re-run
    repeated it. So it has to be conditional on the column existing, not merely
    present.
    """
    for table, spec in cg.TABLES.items():
        sql = cg.render_update(table, _rows_for(table))
        dedupe = f"DELETE `a` FROM `{spec.name}` `a`"
        assert dedupe in sql, table
        # The guard is what precedes it: information_schema.COLUMNS for `id`, and
        # the statement runs when it is FOUND rather than when it is missing.
        head = sql.split(dedupe, 1)[0].rsplit("SET @nextseek_found", 1)[1]
        assert "COLUMN_NAME = 'id'" in head, table
        assert "IF(@nextseek_found," in head, table


def test_every_row_change_is_one_transaction_that_commits_only_when_checked():
    """Nothing that deletes or writes a row runs outside the transaction.

    The dedupe used to run before it, autocommitted, and each section committed on
    its own: a failure in one left the others written. DDL commits implicitly in
    MySQL, so the ALTERs stay outside -- before it the ones that add or widen a
    column, after it the unique key -- and none of them removes a row.
    """
    for table, spec in cg.TABLES.items():
        sql = cg.render_update(table, _rows_for(table))
        begin, checks = sql.index("START TRANSACTION;"), sql.index(cg.CHECKS_MARKER)
        delete = sql.index(f"DELETE FROM `{spec.name}` WHERE")
        last_insert = sql.rindex(f"INSERT INTO `{spec.name}`")
        dedupe = sql.index(f"DELETE `a` FROM `{spec.name}` `a`")
        assert begin < delete < last_insert < dedupe < checks, table
        tail = sql[checks:]
        assert "IF(@nextseek_problems = '', 'COMMIT', 'ROLLBACK')" in tail, table
        assert sql.count("START TRANSACTION;") == 1 and "COMMIT;" not in sql, table
        assert "ALTER TABLE" not in sql[begin:sql.index(cg.KEYS_MARKER)], table
    everything = _update_all()
    assert everything.count("START TRANSACTION;") == 1
    assert "IF(@nextseek_problems = '', 'COMMIT', 'ROLLBACK')" in everything


def test_update_adds_the_unique_key_and_any_new_column():
    sql = cg.render_update("projects", _rows_for("projects"))
    assert "ADD UNIQUE KEY `uq_projects_context_name_type`" in sql
    assert "information_schema" in sql          # the idempotent add, not a bare ALTER
    assert "ADD COLUMN" not in sql              # projects_context gains no column
    sample = cg.render_update("sample_types", _rows_for("sample_types"))
    added = cg.ADDED_COLUMNS["sample_types"]["repository_attributes"]
    assert f"`repository_attributes` {added}" in sample


def test_update_escapes_a_quote_by_doubling_it():
    rows = [{"name": "Zephyr", "entity_type": "project", "pi": "O'Neill, Pat (Example)"}]
    sql = cg.render_update("projects", rows)
    assert "'O''Neill, Pat (Example)'" in sql
    assert "\\'" not in sql                     # no backslash escapes: see literal()


def test_update_refuses_a_backslash_rather_than_corrupting_it():
    import pytest

    rows = [{"name": "Backslash", "entity_type": "project", "description": r"a\b"}]
    with pytest.raises(cg.UnsupportedValue):
        cg.render_update("projects", rows)


def test_update_writes_json_columns_as_json_text():
    sql = cg.render_update("projects", _rows_for("projects"))
    assert '\'["BTC", "Breakthrough Cancer"' in sql


def _update_all() -> str:
    """What `--emit update --table all` writes."""
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert cg.main(["--emit", "update", "--table", "all"]) == 0
    return buffer.getvalue()


def test_update_all_is_one_script_in_the_documented_order():
    sql = _update_all()
    order = [sql.index(f"-- ---- {name} ----") for name in (
        "sample_types_context", "the internal assay mapping operations",
        "assay_context", "projects_context")]
    assert order == sorted(order)
    for marker in (cg.SCHEMA_MARKER, cg.ROWS_MARKER, cg.CHECKS_MARKER, cg.KEYS_MARKER):
        assert sql.count(marker) == 1, marker


def test_update_is_deterministic():
    rows = _rows_for("assays")
    assert cg.render_update("assays", rows) == cg.render_update("assays", rows)


def test_an_empty_source_is_refused_rather_than_emptying_the_table():
    """The update deletes every row the source does not name, and an empty source
    rendered `NOT IN ()`, which is a syntax error on MySQL today and a delete-all in
    any dialect that accepts it."""
    import pytest

    for table in ("sample_types", "assays", "projects"):
        with pytest.raises(cg.EmptySource):
            cg.render_update(table, [])


def test_update_refuses_a_duplicate_key_in_the_source():
    import pytest

    rows = [{"name": "CSBC", "entity_type": "project"}, {"name": "csbc", "entity_type": "project"}]
    with pytest.raises(cg.DuplicateKey):
        cg.render_update("projects", rows)


def test_a_collision_only_utf8mb4_unicode_ci_would_see_is_refused_too():
    """The collision class that ends in a silently lost row rather than an error.

    utf8mb4_unicode_ci does more than lowercase and strip: on MySQL 8.0.46
    `SELECT 'u' = _utf8mb4'ü' COLLATE utf8mb4_unicode_ci` is 1, and so is the
    `ss`/`ß` pair, while Python's `.lower()` keeps them apart. Two such rows passed
    the old check, and then ON DUPLICATE KEY UPDATE did not raise 1062 -- it
    absorbed the collision. Proven against a utf8mb4_unicode_ci table carrying the
    unique key render_update adds: the two upserts exited 0 and left ONE row, so a
    curated row disappeared with no failed statement. `ü` already appears in
    context/assays.json, in a Description rather than a key.
    """
    import pytest

    for first, second in (("Müller", "Muller"), ("Strauß", "Strauss")):
        with pytest.raises(cg.DuplicateKey):
            cg.render_update("projects", [{"name": first, "entity_type": "project"},
                                          {"name": second, "entity_type": "project"}])
    assert cg.fold_key("Müller") == cg.fold_key("Muller")


def test_two_supplementary_characters_collide_as_the_key_collation_compares_them():
    """utf8mb4_unicode_ci gives every character above U+FFFF the same weight, so two
    different emoji compare equal: `SELECT _utf8mb4 X'F09FA7AA' = _utf8mb4 X'F09FA7AC'
    COLLATE utf8mb4_unicode_ci` is 1 on mysql:8.0.46. Two keys differing only there
    passed the check, and the second write then landed on the first row: one row
    gone, exit 0. The MySQL lane checks the collation itself."""
    import pytest

    assert cg.fold_key("Lab \U0001F9EA") == cg.fold_key("Lab \U0001F9EC")
    assert cg.fold_key("Lab \U0001F9EA") != cg.fold_key("Lab \ufffd")
    with pytest.raises(cg.DuplicateKey):
        cg.render_update("projects", [{"name": "Lab \U0001F9EA", "entity_type": "project"},
                                      {"name": "Lab \U0001F9EC", "entity_type": "project"}])


def test_a_key_with_surrounding_whitespace_is_refused():
    """MySQL ignores trailing spaces when it compares a key and not when it stores
    one, so such a key is two things at once; refused rather than normalised."""
    import pytest

    for key in (" CSBC", "CSBC ", "CSBC\t"):
        with pytest.raises(cg.UnsupportedValue):
            cg.render_update("projects", [{"name": key, "entity_type": "project"}])


def test_a_control_character_is_refused_in_every_literal_and_comment():
    """A NUL got through every renderer and the mysql client then refused the
    statement, partway through the artifact. A carriage return was silently
    rewritten to a newline, so the stored value never equalled the curated one.
    Newline and tab are the only control characters a value may carry."""
    import pytest

    for bad in ("a\x00b", "a\rb", "a\x1bb", "a\x1fb"):
        with pytest.raises(cg.UnsupportedValue):
            cg.literal(bad)
        with pytest.raises(cg.UnsupportedValue):
            cg.seed_literal(bad)
        with pytest.raises(cg.UnsupportedValue):
            cg.render_mappings([{"action": "map", "seek_assay_id": 7, "seek_title": bad,
                                 "internal_assay_title": "RNA-Seq"}], assays=[])
    for fine in ("a\nb", "a\tb", "caf\u00e9 \u03b3 \U0001F9EA"):
        cg.literal(fine)
        cg.seed_literal(fine)


def test_the_real_curated_keys_do_not_collide_under_that_wider_fold():
    """The wider net must not refuse data that is already there."""
    for table in ("sample_types", "assays", "projects"):
        rows = cg.load_source(cg.TABLES[table].source)
        cg._checked_keys(table, rows)           # raises on a collision


# --- the natural key is (name, entity_type) ------------------------------------
#
# The real CSBC and MetNet investigations share their exact SEEK titles with the CSBC
# and MetNet project rows, so `projects_context` keyed on `name` alone cannot hold both
# (spec 2026-09-18, section 9.1). Everything that keys a project row keys the pair: the
# refusals, the delete of rows the source no longer names, the upsert, the dedupe, the
# digest and the unique key. The live table's PRIMARY KEY (name) is widened in the
# schema part, before the rows transaction; the MySQL lane proves it on both shapes.

PAIR = [
    {"name": "Zephyr", "entity_type": "project", "project_id": 4, "research_focus": "A project."},
    {"name": "Zephyr", "entity_type": "investigation", "project_id": 4,
     "parent_project": "Zephyr", "research_focus": "Its investigation."},
]


def test_projects_are_keyed_on_name_and_entity_type():
    assert cg.TABLES["projects"].key_columns == ("name", "entity_type")
    assert cg.TABLES["assays"].key_columns == ("assay_name",)
    assert cg.TABLES["sample_types"].key_columns == ("sample_type",)
    assert cg.key_of("projects", PAIR[1]) == ("Zephyr", "investigation")
    ddl = cg.DDL["projects"]
    assert "UNIQUE KEY `uq_projects_context_name_type` (`name`, `entity_type`)" in ddl
    assert "`uq_projects_context_name` (" not in ddl
    assert re.search(r"^  entity_type\s+VARCHAR\(64\)\s+NOT NULL,$", ddl, re.M)


def test_a_project_and_an_investigation_may_share_a_name():
    for render in (cg.render_update, cg.render_seed):
        sql = render("projects", PAIR)
        assert "'investigation'" in sql and "'project'" in sql


def test_one_name_twice_within_one_entity_type_is_still_a_duplicate():
    import pytest

    for rows in ([PAIR[1], dict(PAIR[1], name="zephyr")], [PAIR[0], dict(PAIR[0])]):
        with pytest.raises(cg.DuplicateKey):
            cg.render_update("projects", rows)


def test_entity_type_is_project_or_investigation_exactly():
    import pytest

    for bad in ("Project", "study", " project", "investigation ", "INVESTIGATION"):
        with pytest.raises(cg.UnsupportedValue) as excinfo:
            cg.check_columns("projects", [{"name": "Zephyr", "entity_type": bad}])
        assert "entity_type" in str(excinfo.value)
    for row in ({"name": "Zephyr"}, {"name": "Zephyr", "entity_type": None},
                {"name": "Zephyr", "entity_type": ""}):
        with pytest.raises(cg.MissingKey):
            cg.check_columns("projects", [row])


def test_every_row_statement_keys_on_both_columns():
    sql = cg.render_update("projects", PAIR)
    rows_part = sql.split(cg.ROWS_MARKER, 1)[1].split(cg.MYSQL_ONLY_MARKER, 1)[0]
    assert "  WHERE `name` = 'Zephyr' AND `entity_type` = 'investigation';" in rows_part
    assert ("WHERE NOT EXISTS (SELECT 1 FROM `projects_context` WHERE `name` = 'Zephyr' "
            "AND `entity_type` = 'project');") in rows_part
    delete = rows_part.split("DELETE FROM `projects_context` WHERE ", 1)[1].split(";", 1)[0]
    assert "(`name` = 'Zephyr' AND `entity_type` = 'project')" in delete
    assert "(`name` = 'Zephyr' AND `entity_type` = 'investigation')" in delete
    assert delete.endswith("OR `name` IS NULL OR `entity_type` IS NULL")
    assert ("ON `a`.`name` = `b`.`name` AND `a`.`entity_type` = `b`.`entity_type` "
            "AND `a`.`id` > `b`.`id`") in sql


def test_the_schema_part_moves_either_old_key_to_the_pair_before_any_row_changes():
    """Each step conditional on the shape found, and none of them removes a row.

    The live table's PRIMARY KEY is `(name)`; the old held seed's table carries the unique
    key `uq_projects_context_name`. Either one refuses the second row of a shared name, so
    both go before the rows transaction. Only relaxing uniqueness, neither can fail on the
    rows already there.
    """
    sql = cg.render_update("projects", PAIR)
    schema = sql.split(cg.SCHEMA_MARKER, 1)[1].split(cg.ROWS_MARKER, 1)[0]
    widen = "ALTER TABLE `projects_context` DROP PRIMARY KEY, ADD PRIMARY KEY (`name`, `entity_type`)"
    assert widen in schema
    guard = schema.split(widen, 1)[0].rsplit("SET @nextseek_found", 1)[1]
    assert "INDEX_NAME = 'PRIMARY'" in guard and "= 'name'" in guard
    drop = "ALTER TABLE `projects_context` DROP INDEX `uq_projects_context_name`"
    assert drop in schema
    assert "INDEX_NAME = 'uq_projects_context_name'" in schema.split(drop, 1)[0].rsplit("SET @nextseek_found", 1)[1]
    keys = sql.split(cg.KEYS_MARKER, 1)[1]
    add = "ADD UNIQUE KEY `uq_projects_context_name_type` (`name`, `entity_type`)"
    assert add in keys
    guard = keys.split(add, 1)[0].rsplit("SET @nextseek_found", 1)[1]
    assert "INDEX_NAME = 'PRIMARY'" in guard and "'id'" in guard and "'name,entity_type'" in guard
    # The single-column tables keep their own shape.
    assays = cg.render_update("assays", _rows_for("assays"))
    assert "DROP PRIMARY KEY" not in assays and "uq_assay_context_assay_name" in assays


def test_the_digest_orders_by_both_key_columns():
    """Two rows share a name, so ordering by name alone left their order to the engine."""
    assert cg.content_digest("projects", PAIR) == cg.content_digest("projects", PAIR[::-1])
    sql = cg.render_update("projects", PAIR)
    assert ("ORDER BY CONVERT(`name` USING utf8mb4) COLLATE utf8mb4_bin, "
            "CONVERT(`entity_type` USING utf8mb4) COLLATE utf8mb4_bin") in sql


def test_a_same_named_project_and_investigation_round_trip_and_rerun_to_nothing():
    import sqlite3

    with sqlite3.connect(":memory:") as conn:
        _apply(conn, "projects", PAIR)
        once = _read_back(conn, "projects")
        _apply(conn, "projects", PAIR, fresh=False)
        twice = _read_back(conn, "projects")
    assert once == twice
    assert [(r["name"], r["entity_type"]) for r in once] == [("Zephyr", "project"),
                                                             ("Zephyr", "investigation")]


# --- the rules a projects row keeps --------------------------------------------
#
# Investigations become rows of `projects_context` (spec 2026-09-18, section 9), with
# conventions of their own, and `present_on` is the one curated key that is not a
# column: it says which instances hold an investigation and is never written to a
# database. `check_project_rows` refuses what the conventions do not allow; the
# generator runs it on the curated file before any SQL or block is rendered.

def _project(name, **extra):
    row = {"name": name, "entity_type": "project", "project_id": 4, "parent_project": None,
           "alternative_names": [], "research_focus": f"What {name} studies."}
    row.update(extra)
    return row


def _inquiry(name, **extra):
    """An investigation row of the invented project Zephyr."""
    row = {"name": name, "entity_type": "investigation", "project_id": 4,
           "parent_project": "Zephyr", "alternative_names": [], "pi": None,
           "research_focus": f"What {name} holds."}
    row.update(extra)
    return row


def test_present_on_is_a_curated_key_and_never_a_column():
    import pytest

    rows = [_project("Zephyr"), _inquiry("Atlas", project_id=None, parent_project="Atlas",
                                         present_on=["local", "dev"])]
    cg.check_columns("projects", rows)                     # accepted as a key
    assert "present_on" not in cg.COLUMNS["projects"]
    assert "present_on" not in cg.DDL["projects"]
    for render in (cg.render_update, cg.render_seed):
        assert "present_on" not in render("projects", rows)
    with pytest.raises(cg.UnknownColumn):                  # a key of projects only
        cg.check_columns("assays", [{"assay_name": "X", "present_on": ["dev"]}])


def test_rows_for_strips_what_only_the_generator_reads(monkeypatch):
    rows = [_project("Zephyr"), _inquiry("Atlas", project_id=None, parent_project="Atlas",
                                         present_on=["local", "dev"])]
    monkeypatch.setattr(cg, "load_source", lambda path: [dict(r) for r in rows])
    stripped = cg.rows_for("projects")
    assert all("present_on" not in row for row in stripped)
    assert cg.curated_rows("projects")[1]["present_on"] == ["local", "dev"]


def test_the_real_curated_projects_keep_every_rule():
    cg.check_project_rows(cg.load_source(cg.TABLES["projects"].source))


def test_present_on_names_some_instances_and_only_on_an_investigation():
    import pytest

    base = [_project("Zephyr")]
    for bad in ([], ["staging"], ["local", "local"], ["local", "dev", "prod"], "local",
                [None], ["Local"]):
        with pytest.raises(cg.UnsupportedValue) as excinfo:
            cg.check_project_rows(base + [_inquiry("Atlas", present_on=bad)])
        assert "present_on" in str(excinfo.value), bad
    with pytest.raises(cg.UnsupportedValue):
        cg.check_project_rows([_project("Zephyr", present_on=["dev"])])
    for good in (None, ["local"], ["local", "dev"], ["dev", "prod"]):
        cg.check_project_rows(base + [_inquiry("Atlas", present_on=good)])


def test_an_investigation_needs_a_short_one_line_research_focus():
    import pytest

    base = [_project("Zephyr")]
    for focus in (None, "", "   "):
        with pytest.raises(cg.IncompleteInvestigation):
            cg.check_project_rows(base + [_inquiry("Atlas", research_focus=focus)])
    for focus in ("x" * 201, "two\nlines"):
        with pytest.raises(cg.UnsupportedValue) as excinfo:
            cg.check_project_rows(base + [_inquiry("Atlas", research_focus=focus)])
        assert "research_focus" in str(excinfo.value)
    cg.check_project_rows(base + [_inquiry("Atlas", research_focus="x" * 200)])


def test_an_investigation_belongs_to_a_project():
    """`parent_project` names the owning project row; `project_id` is that row's id, and
    null only where the id differs by instance, which `present_on` has to say."""
    import pytest

    base = [_project("Zephyr")]
    with pytest.raises(cg.UnsupportedValue):
        cg.check_project_rows(base + [_inquiry("Atlas", parent_project=None)])
    with pytest.raises(cg.UnsupportedValue):
        cg.check_project_rows(base + [_inquiry("Atlas", project_id=5)])
    with pytest.raises(cg.UnsupportedValue):
        cg.check_project_rows(base + [_inquiry("Atlas", project_id=None)])
    with pytest.raises(cg.UnsupportedValue):
        cg.check_project_rows(base + [_inquiry("Atlas", project_id=None, present_on=["dev"])])
    # No project row of that name: the owner is named by its SEEK title, and the id is
    # the instance's own.
    cg.check_project_rows(base + [_inquiry("Atlas", project_id=None, parent_project="Atlas",
                                           present_on=["local", "dev"])])


def test_an_investigation_leaves_the_pi_and_the_data_types_to_its_project():
    import pytest

    base = [_project("Zephyr")]
    for extra in ({"pi": "Doe, Jane"}, {"key_data_types": ["RNA sequencing"]},
                  {"nih_reporter_link": "https://example.org/x"},
                  {"fairdomhub_published_link": "https://example.org/y"}):
        with pytest.raises(cg.UnsupportedValue):
            cg.check_project_rows(base + [_inquiry("Atlas", **extra)])
    cg.check_project_rows(base + [_inquiry("Atlas", key_data_types=[], pi=None)])


def test_an_alias_names_one_row_only():
    """Once folded, an alias may not equal another row's name or alias (spec 9.4).

    The exception is what bridges what users type to an investigation: an investigation row
    may repeat its own parent project's name or aliases. The mirror image is refused: a
    project row carrying an investigation's exact title as an alias, which is why the five
    investigation titles leave the project rows.
    """
    import pytest

    parent = _project("Zephyr", alternative_names=["ZPH", "Zephyr Center"])
    other = _project("Yarrow", project_id=7, alternative_names=["Yarrow Lab"])
    # The investigation repeats its parent's name and alias: accepted.
    cg.check_project_rows([parent, other, _inquiry("Atlas", alternative_names=["Zephyr", "zph"])])
    for rows in (
        # a project alias that is an investigation's title
        [dict(parent, alternative_names=["ATLAS"]), other, _inquiry("Atlas")],
        # an investigation alias that is another project's name or alias
        [parent, other, _inquiry("Atlas", alternative_names=["Yarrow"])],
        [parent, other, _inquiry("Atlas", alternative_names=["yarrow lab"])],
        # two projects sharing an alias, folded
        [parent, dict(other, alternative_names=["Zéphyr Center"])],
        # two investigations of one parent sharing an alias
        [parent, other, _inquiry("Atlas", alternative_names=["Wind"]),
         _inquiry("Breeze", alternative_names=["WIND"])],
    ):
        with pytest.raises(cg.DuplicateAlias):
            cg.check_project_rows(rows)


def test_rows_for_projects_refuses_what_the_rules_refuse(monkeypatch):
    import pytest

    rows = [_project("Zephyr", alternative_names=["Atlas"]), _inquiry("Atlas")]
    monkeypatch.setattr(cg, "load_source", lambda path: [dict(r) for r in rows])
    with pytest.raises(cg.DuplicateAlias):
        cg.rows_for("projects")


# --- column widths -----------------------------------------------------------
#
# The defect that shipped, and the check that could have seen it. `projects.json`'s
# `Impact` row carries a 276 character `pi`; `pi` was declared VARCHAR(255). Against
# mysql:8.0.46 at the image's default sql_mode (docker-compose.yml sets none) the
# committed seed answered `ERROR 1406 (22001) at line 35: Data too long for column
# 'pi' at row 1`, exit 1, 4 of 12 rows loaded -- and that is `./startup.sh install`
# dying, because schema_fixups pipes the file into `mysql` on stdin and compose_exec
# raises on a non-zero exit. The 58-test suite was green throughout, because the
# round trip's engine is SQLite and SQLite ignores a VARCHAR width outright.

WIDTH_ERROR_EXAMPLE = "a" * 300


def test_no_curated_value_exceeds_its_declared_column_width():
    """Every value measured against the table's own DDL, not against a restatement."""
    for table in ("sample_types", "assays", "projects"):
        cg.check_widths(table, _rows_for(table))    # raises on the first overflow


def test_an_over_long_value_is_refused_by_both_emitters():
    """Both artifacts, because both aborted on it and for the same reason."""
    import pytest

    rows = [{"name": WIDTH_ERROR_EXAMPLE, "entity_type": "project", "description": "x"}]
    for render in (cg.render_update, cg.render_seed):
        with pytest.raises(cg.ValueTooLong) as excinfo:
            render("projects", rows)
        message = str(excinfo.value)
        assert "name" in message and "255" in message and "1406" in message


def test_the_column_that_aborted_the_load_is_wide_enough_now():
    """`pi` is TEXT, which is what the live column is, not VARCHAR(255)."""
    limits = cg.declared_limits("projects")
    assert limits["pi"] == ("bytes", cg.TEXT_BYTES)
    longest = max(len(str(r.get("pi") or "")) for r in _rows_for("projects"))
    assert longest == 276                      # the Impact row, unchanged
    assert "`pi` VARCHAR" not in cg.DDL["projects"]


def test_the_declared_widths_are_read_off_the_ddl_and_not_restated():
    """A column widened in the DDL widens the check, with no second edit."""
    limits = cg.declared_limits("assays")
    assert limits["assay_name"] == ("characters", 255)
    # Production's widths, which the DDL was narrower than: measured on the live
    # assay_context, varchar(128) / varchar(128) / varchar(512).
    assert limits["Parent_Clade_Type"] == ("characters", 128)
    assert limits["Child_Clade_Type"] == ("characters", 128)
    assert limits["AssaySheet_Link"] == ("characters", 512)
    assert "internal_assay_id" not in limits    # an INT has no length limit


def test_a_text_column_is_measured_in_bytes_because_mysql_measures_it_in_bytes():
    """TEXT holds 65,535 BYTES and these columns are utf8mb4."""
    import pytest

    four_byte = "\U0001F9EA" * 20000          # 20,000 characters, 80,000 bytes
    rows = [{"name": "Emoji", "entity_type": "project", "description": four_byte}]
    with pytest.raises(cg.ValueTooLong) as excinfo:
        cg.render_update("projects", rows)
    assert "bytes" in str(excinfo.value)


# --- the connection charset --------------------------------------------------


def test_every_artifact_pins_the_connection_charset():
    """Without it the installer's own apply path double-encodes every non-ASCII value.

    `schema_fixups._create_table` pipes the file into `mysql` with no
    `--default-character-set`, and the db container has no UTF-8 locale, so the
    client default resolves to latin1 -- measured on the real compose db container,
    `SELECT @@character_set_client` answers `latin1`. Applying assay_context.sql
    that way stored the gamma of "Antibody-Dependent NK Cell Activation Assay" as
    HEX C38EC2B3 where the file holds CEB3: latin1 -> utf8mb4 double encoding. With
    the line below, the same apply stored CEB3.
    """
    for table in cg.TABLES:
        assert cg.CHARSET_PREAMBLE in cg.render_seed(table, _rows_for(table)), table
        assert cg.CHARSET_PREAMBLE in cg.render_update(table, _rows_for(table)), table
    assert cg.CHARSET_PREAMBLE in cg.render_update(
        "mappings", cg.load_source(cg.TABLES_EXTRA["mappings"]))
    # And in the files an install actually reads.
    for path in SEED_PATHS.values():
        assert cg.CHARSET_PREAMBLE in _repo(path), path


def test_a_column_added_to_a_live_table_carries_its_own_charset():
    """`ADD COLUMN c TEXT NULL` inherits the table default, and the live
    dmac.projects_context is DEFAULT CHARSET=latin1 (measured 2026-09-17: 10 of its
    12 columns are latin1). The bare form would create a column narrower than the
    DDL declares, on the instances that matter and nowhere the SQLite lane can see:
    on MySQL 8.0.46 a four-byte character into such a column answers `ERROR 1366
    Incorrect string value`. json_text passes one through raw (ensure_ascii=False)
    and literal accepts it, so nothing else would stop one."""
    for definitions in cg.ADDED_COLUMNS.values():
        for definition in definitions.values():
            assert "CHARACTER SET utf8mb4" in definition
            assert "COLLATE utf8mb4_unicode_ci" in definition
    sql = cg.render_update("sample_types", _rows_for("sample_types"))
    assert "ADD COLUMN `repository_attributes` TEXT CHARACTER SET utf8mb4" in sql


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

SEED_PATHS = {table: cg.SEED_DIR / name for table, name in cg.SEED_FILES.items()}


def test_seed_ddl_declares_exactly_the_columns_the_module_writes():
    for table, spec in cg.TABLES.items():
        assert _ddl_columns(cg.DDL[table], spec.name) == list(spec.columns), table


def test_seed_ddl_declares_the_unique_key_the_upsert_needs():
    for table, spec in cg.TABLES.items():
        columns = ", ".join(f"`{c}`" for c in spec.key_columns)
        assert f"UNIQUE KEY `{cg.unique_key_name(table)}` ({columns})" in cg.DDL[table], table
    assert cg.unique_key_name("projects") == "uq_projects_context_name_type"
    assert cg.unique_key_name("assays") == "uq_assay_context_assay_name"


def test_seed_writes_the_ddl_then_one_insert_per_line():
    for table, expected in (("sample_types", 109), ("assays", 138), ("projects", 21)):
        sql = cg.render_seed(table, _rows_for(table))
        assert sql.startswith("-- ")                      # the header comment
        assert cg.DDL[table] in sql
        inserts = [line for line in sql.splitlines() if line.startswith("INSERT INTO ")]
        assert len(inserts) == expected, table
        assert all(line.endswith(");") for line in inserts), table
        assert "ON DUPLICATE KEY UPDATE" not in sql, table   # a seed loads once
        assert sql.endswith("\n")


def test_a_seed_literal_means_the_same_with_and_without_backslash_escapes():
    """A backslash-quote ends the string under NO_BACKSLASH_ESCAPES, so the rest of
    the line ran as SQL: the committed seeds stopped after 1 of 12 and 13 of 138
    rows there, and a crafted value dropped a table. A seed now carries no backslash
    at all: quotes are doubled and a newline is `CHAR(10 USING utf8mb4)` inside
    CONCAT, which both modes read the same way. The MySQL lane loads the seeds
    under that mode."""
    import pytest

    assert cg.seed_literal("O'Neill") == "'O''Neill'"
    assert cg.seed_literal("one\ntwo's") == "CONCAT('one', CHAR(10 USING utf8mb4), 'two''s')"
    with pytest.raises(cg.UnsupportedValue):
        cg.seed_literal("back\\slash")
    for table in cg.TABLES:
        assert "\\" not in cg.render_seed(table, _rows_for(table)), table


def test_seed_escapes_a_newline_so_every_insert_is_one_line():
    # 79 curated sample type values and 30 assay values contain a newline.
    sql = cg.render_seed("sample_types", _rows_for("sample_types"))
    body = sql.split(");", 1)[1]                            # past the CREATE TABLE
    assert "CHAR(10 USING utf8mb4)" in sql
    for line in body.splitlines():
        # `startswith(("INSERT INTO ", "--", ""))` was the old form, and `""` makes
        # every string match, so it asserted nothing at all.
        assert not line.strip() or line.startswith(("INSERT INTO ", "--")), line
    assert any(line.startswith("INSERT INTO ") for line in body.splitlines())


def test_seed_matches_the_committed_files_shape():
    """The regenerated files are what is committed, so 6.13 is a diff of rows."""
    for table, path in SEED_PATHS.items():
        committed = _repo(path)
        rendered = cg.render_seed(table, _rows_for(table))
        assert committed == rendered, f"{path} is stale; regenerate it"


def test_no_seed_file_has_two_writers():
    """Two programs writing one file from different sources is how it goes stale.

    Until the curated seeds are signed off, scripts/generate_assay_context_seed.py
    keeps writing the installer's startup/seed/sql/assay_context.sql and this
    module writes only the held `.curated.sql` files, so the two never share one.
    """
    old = _repo(Path("scripts/generate_assay_context_seed.py"))
    assert 'DEST = ROOT / "startup/seed/sql/assay_context.sql"' in old
    assert all(name.endswith(".curated.sql") for name in cg.SEED_FILES.values())
    assert "assay_context.sql" not in cg.SEED_FILES.values()


def test_the_curated_seeds_are_held_until_sign_off():
    """No install step reads the generated seeds until the content is signed off.

    A fresh `install` or any `reset` runs the schema fixups, so registering a
    `.curated.sql` file there would load the curated content on every new stack
    before anyone had reviewed it. Switching them on is one reviewed commit
    (scripts/README.md group C), and that commit inverts this test.
    """
    fixups = _repo(Path("startup/steps/schema_fixups.py"))
    for name in cg.SEED_FILES.values():
        assert name not in fixups, name
    assert 'table="sample_types_context"' not in fixups
    for path in SEED_PATHS.values():
        assert "HELD: no install step reads this file" in _repo(path), path


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
    exact = ("`internal_assay_title` = CONVERT('Library Creation' USING utf8mb4) "
             "COLLATE utf8mb4_bin")
    # map: seek assay 466 -> Library Creation, and only while it is still NULL
    assert ("UPDATE `assays_internal_assays` SET `internal_assay_id` = "
            f"(SELECT MIN(`id`) FROM `internal_assays` WHERE {exact})\n"
            "  WHERE `assay_id` = 466 AND `internal_assay_id` IS NULL\n"
            f"  AND EXISTS (SELECT 1 FROM `internal_assays` WHERE {exact});") in sql
    # remap: seek assay 37 moves off 130 only while 130 carries the title it had
    # when the remap was written, and re-running is a no-op
    remap = sql.split("WHERE `assay_id` = 37 AND ", 1)[1].split(";", 1)[0]
    assert remap.startswith("(`internal_assay_id` <=> (SELECT MIN(`id`)")
    assert ("OR (`internal_assay_id` = 130 AND EXISTS (SELECT 1 FROM `internal_assays` "
            "WHERE `id` = 130\n      AND `internal_assay_title` = CONVERT(") in remap


def test_a_moved_target_writes_nothing_rather_than_a_null():
    """The guard that turns upstream drift into a no-op instead of damage.

    `_by_title` returns NULL when nothing carries the title, and `SET col = NULL`
    is a write. Worse, the remap's own `WHERE col IN (<from_id>, <subquery>)` is
    still TRUE on the from_id arm, so the row MATCHES and is blanked. Measured on
    mysql:8.0.46: retitling one remap target upstream and applying the mappings
    once moved assay_ids 367 and 490 from internal assay 196 to NULL, and a second
    run did not heal them, because NULL is not matched by the IN. 14 of the remap
    target titles are produced by no create_internal and no rename_internal, so
    they must already exist in production -- which is exactly the drift this is
    against. After the guard, the same run left both rows at 196.
    """
    rows = cg.load_source(cg.TABLES_EXTRA["mappings"])
    sql = cg.render_update("mappings", rows)
    targets = {r["internal_assay_title"] for r in rows if r["action"] in ("map", "remap")}
    assert len(targets) > 1
    for statement in sql.split("UPDATE `assays_internal_assays`")[1:]:
        statement = statement.split(";", 1)[0]
        title = statement.split("`internal_assay_title` = CONVERT('", 1)[1].split("'", 1)[0]
        assert ("AND EXISTS (SELECT 1 FROM `internal_assays` WHERE "
                f"`internal_assay_title` = CONVERT('{title}' USING utf8mb4) "
                "COLLATE utf8mb4_bin)") in statement, title


def test_every_assay_row_is_linked_by_title_after_the_creates():
    """Both sections that can change a link re-link every assay_context row by title.

    Previously the 14 rows whose curated id is null were filled only by a NULL-only
    backfill in the mappings section, while the assays section reset them to NULL
    on every run: `--table assays` alone left them NULL, silently. And the other
    rows carried production's internal assay numbers as literals, which on a stack
    numbered differently point at the wrong assay. Now the link is always the id of
    the internal assay whose title is the row's name, exactly.
    """
    mappings = cg.load_source(cg.TABLES_EXTRA["mappings"])
    relink = "UPDATE `assay_context` SET `internal_assay_id` = (SELECT MIN(`ia`.`id`)"
    ops = cg.render_update("mappings", mappings)
    assert ops.count(relink) == 1
    assert ops.rindex("INSERT INTO `internal_assays`") < ops.index(relink)
    assert ops.rindex("UPDATE `internal_assays`") < ops.index(relink)
    assays = cg.render_update("assays", _rows_for("assays"))
    assert assays.count(relink) == 1
    assert assays.rindex("INSERT INTO `assay_context`") < assays.index(relink)
    assert ("CONVERT(`assay_context`.`assay_name` USING utf8mb4) COLLATE utf8mb4_bin"
            in assays)


def test_every_remap_source_is_pinned_by_the_title_it_carries():
    """A remap names its source by production's number only; the title comes from
    the curated files, so another stack's number cannot move the wrong SEEK assay."""
    import pytest

    assays = cg.load_source(cg.TABLES["assays"].source)
    mappings = cg.load_source(cg.TABLES_EXTRA["mappings"])
    sources = cg.remap_source_titles(mappings, assays)
    remaps = [m for m in mappings if m["action"] == "remap"]
    assert set(sources) == {m["from_internal_assay_id"] for m in remaps}
    merged = {m["internal_assay_id"]: m["from_title"] for m in mappings
              if m["action"] == "merge_internal"}
    for ident, title in merged.items():
        if ident in sources:
            assert sources[ident] == title
    orphan = {"action": "remap", "seek_assay_id": 7, "seek_title": "x",
              "from_internal_assay_id": 999999, "internal_assay_title": "RNA-Seq"}
    with pytest.raises(cg.MappingMismatch):
        cg.remap_source_titles(mappings + [orphan], assays)


def test_the_checks_name_every_operation_they_verify():
    """Drift used to be skipped silently: a moved target wrote nothing and the apply
    exited 0. The checks at the end of the transaction name every post-condition."""
    sql = cg.render_update("mappings", cg.load_source(cg.TABLES_EXTRA["mappings"]))
    checks = sql.split(cg.CHECKS_MARKER, 1)[1]
    for phrase in ("curated internal assay titles are not held by exactly one internal assay",
                   "renamed internal assays not carrying their new title",
                   "SEEK assays not on their curated internal assay",
                   "merged internal assays still present",
                   "assay_context rows link to a missing internal assay"):
        assert phrase in checks, phrase
    assert "context_gen REFUSED and rolled back; nothing was committed" in checks


def test_a_rename_waits_for_its_new_title_to_be_free_and_a_merge_for_its_survivor():
    """On a stack where another internal assay already held a rename's new title,
    the rename made two of them; and a merge never checked that its survivor
    exists."""
    sql = cg.render_update("mappings", cg.load_source(cg.TABLES_EXTRA["mappings"]))
    assert sql.count("SET @nextseek_taken := ") == sql.count("UPDATE `internal_assays` SET") == 4
    assert sql.count("AND @nextseek_taken = 0;") == 4
    assert sql.count("SET @nextseek_survivor := ") == sql.count("DELETE FROM `internal_assays`") == 13


def test_a_mapping_comment_refuses_a_newline_rather_than_emitting_a_statement():
    """`seek_title` and `into_internal_assay_title` go into `--` comments raw.

    A `--` comment ends at the newline, so a newline in either value makes the rest
    of it an executable statement in a script the operator runs against production.
    Proven before the fix: a seek_title of "Harmless title\nDROP TABLE
    `internal_assays`; -- " rendered that DROP as live SQL. These two fields exist
    only for the comment, so they are the only values in a hand-edited
    assay_mappings.json that no consumer would otherwise reject: every other value
    in the same statements goes through literal() or int().
    """
    import pytest

    injected = "Harmless title\nDROP TABLE `internal_assays`; -- "
    cases = (
        {"action": "map", "seek_assay_id": 7, "seek_title": injected,
         "internal_assay_title": "RNA-Seq"},
        {"action": "remap", "seek_assay_id": 7, "seek_title": injected,
         "from_internal_assay_id": 1, "internal_assay_title": "RNA-Seq"},
        {"action": "merge_internal", "internal_assay_id": 1, "from_title": "Old",
         "into_internal_assay_title": injected},
    )
    for row in cases:
        with pytest.raises(cg.UnsupportedValue) as excinfo:
            cg.render_mappings([row])
        assert "newline" in str(excinfo.value), row["action"]


def test_a_create_is_a_no_op_on_a_second_run():
    rows = cg.load_source(cg.TABLES_EXTRA["mappings"])
    sql = cg.render_update("mappings", rows)
    assert sql.count("FROM DUAL\n  WHERE NOT EXISTS (SELECT 1 FROM `internal_assays`") == 14


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
            "WHERE `internal_assay_id` = 174) AND @nextseek_survivor > 0;") in sql


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
# The row statements -- the DELETE, one UPDATE and one guarded INSERT per curated
# row -- are plain SQL, and this runs them on SQLite to check that every value
# survives becoming a SQL literal, with no Docker. That is ALL it can check. What
# SQLite does not model is where every defect a verification lens found lived: a
# column width on the target, a latin1 column, a collation that folds two keys, the
# information_schema guards, the transaction, the checks, the mysql client itself.
# All of that is test_context_gen_mysql.py's, against a real mysql:8.0.
#
# The VALUES pass through untouched, which is why cg.literal doubles quotes and
# keeps newlines literal instead of using MySQL's backslash escapes: the same text
# means the same thing to both engines, so this is not checking an escaper against
# its own inverse.

import sqlite3

_SQLITE_TYPE = {"INT": "INTEGER", "VARCHAR": "TEXT", "TEXT": "TEXT"}


def _sqlite_schema(table: str) -> str:
    """The committed CREATE TABLE, with only its types translated."""
    spec = cg.TABLES[table]
    body = cg.DDL[table].split(f"CREATE TABLE IF NOT EXISTS {spec.name} (", 1)[1]
    body = body.rsplit(") ENGINE", 1)[0]
    columns = []
    for line in body.splitlines():
        match = re.match(r"\s{2}`?(\w+)`?\s+(\w+)", line)
        if not match or match.group(1) in {"KEY", "PRIMARY", "UNIQUE"}:
            continue
        name, declared = match.group(1), match.group(2).upper()
        if name == "id":
            columns.append("`id` INTEGER PRIMARY KEY AUTOINCREMENT")
            continue
        columns.append(f"`{name}` {_SQLITE_TYPE[declared]}")
    assert len(columns) == len(spec.columns) + 1, table   # every column, plus id
    return f"CREATE TABLE `{spec.name}` (\n  " + ",\n  ".join(columns) + "\n);"


def _row_statements(table: str, rows: list[dict]) -> str:
    """The plain-SQL part of the update: from the rows marker to the MySQL-only one."""
    sql = cg.render_update(table, rows)
    body = sql.split(cg.ROWS_MARKER, 1)[1].split(cg.MYSQL_ONLY_MARKER, 1)[0]
    assert "PREPARE" not in body and "information_schema" not in body
    return (body.replace("START TRANSACTION;", "BEGIN;").replace(" FROM DUAL\n", "\n")
            + "\nCOMMIT;\n")


def _apply(conn, table: str, rows: list[dict], *, preload=(), fresh=True) -> None:
    if fresh:
        conn.executescript(_sqlite_schema(table))
        for statement in preload:
            conn.execute(statement)
    conn.executescript(_row_statements(table, rows))


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
                if column == "internal_assay_id":
                    continue       # linked by title in MySQL, after these statements
                assert back[column] == cg.db_value(table, column, curated.get(column)), \
                    f"{table}.{column} of {curated[spec.key]!r}"


def test_a_value_with_a_newline_and_an_apostrophe_survives_byte_for_byte():
    """Named values rather than a normalizer, so this cannot agree with itself."""
    rows = [{
        "name": "Quote and newline",
        "entity_type": "project",
        "description": "It's two lines.\nSecond line, with 'quotes' and a % sign.",
        "pi": "O'Neill, Pat (MIT)",
        "alternative_names": ["a'b", "plain"],
    }]
    with sqlite3.connect(":memory:") as conn:
        _apply(conn, "projects", rows)
        back = _read_back(conn, "projects")[0]
    assert back["description"] == "It's two lines.\nSecond line, with 'quotes' and a % sign."
    assert back["pi"] == "O'Neill, Pat (MIT)"
    assert back["alternative_names"] == '["a\'b", "plain"]'


def test_a_newline_inside_a_json_column_is_refused_and_says_why():
    """The generator's one declared limit, pinned rather than discovered later.

    A newline inside a JSON column survives json.dumps as the two characters `\n`,
    and a backslash is the one thing cg.literal will not guess at. No curated value
    has one (test_no_curated_value_needs_a_backslash), and a plain text column takes
    a newline literally, so this is the narrow case: a list or dict value whose text
    contains one.
    """
    import pytest

    rows = [{"name": "Wrapped", "entity_type": "project", "alternative_names": ["two\nlines"]}]
    with pytest.raises(cg.UnsupportedValue) as excinfo:
        cg.render_update("projects", rows)
    assert "backslash" in str(excinfo.value)


def test_update_sql_is_a_no_op_on_a_second_run():
    table = "assays"
    rows = _rows_for(table)
    with sqlite3.connect(":memory:") as conn:
        _apply(conn, table, rows)
        once = _read_back(conn, table)
        _apply(conn, table, rows, fresh=False)
        twice = _read_back(conn, table)
    assert once == twice
    assert len(twice) == len(rows)


def test_a_row_with_no_key_does_not_survive_the_round_trip():
    """`NULL NOT IN (...)` is NULL, so the plain delete left it there forever."""
    rows = _rows_for("assays")
    with sqlite3.connect(":memory:") as conn:
        _apply(conn, "assays", rows, preload=(
            "INSERT INTO `assay_context` (`assay_name`, `Description`) "
            "VALUES (NULL, 'nameless row');",
        ))
        stored = _read_back(conn, "assays")
    assert not [row for row in stored if row["assay_name"] is None]
    assert len(stored) == len(rows)


def test_update_sql_drops_a_stale_row_and_updates_an_existing_one_in_place():
    """A row whose key the source no longer names goes, and a row whose key it does
    name is updated without changing its id."""
    rows = _rows_for("projects")
    with sqlite3.connect(":memory:") as conn:
        _apply(conn, "projects", rows, preload=(
            "INSERT INTO `projects_context` (`name`, `entity_type`, `description`) "
            "VALUES ('Retired', 'project', 'stale');",
            "INSERT INTO `projects_context` (`name`, `entity_type`, `description`) "
            "VALUES ('CSBC', 'project', 'old');",
            # A row with no entity_type has no key, so it goes like any keyless row.
            "INSERT INTO `projects_context` (`name`, `description`) VALUES ('MetNet', 'untyped');",
        ))
        stored = {(row["name"], row["entity_type"]): row for row in _read_back(conn, "projects")}
    assert ("Retired", "project") not in stored
    assert stored[("CSBC", "project")]["id"] == 2         # updated in place, not reinserted
    assert stored[("CSBC", "project")]["description"] != "old"
    assert ("MetNet", None) not in stored
    assert len(stored) == len(rows)


# --- the curated investigation rows (spec 2026-09-18, section 9.3) -------------
#
# Nine investigations become rows of projects_context: the plan's eight plus
# BioMicroCenter. Each is named by the exact SEEK title that holds the samples, owned by
# a project row (TCGA's project exists only on the local and dev instances, with a
# different id on each, so its id is null and present_on says where it is). The five
# investigation titles the project rows carried as aliases leave them, so an exact title
# resolves to one row.

INVESTIGATION_ROWS = {   # name: (project_id, parent_project, present_on)
    "BioMicroCenter": (5, "MIT-Koch", None),
    "CSBC": (10, "CSBC", None),
    "Collagen Study": (11, "Shoulders", None),
    "Endometriosis": (7, "Griffith", None),
    "GBM_BTC": (9, "Break Through Cancer", None),
    "Impactb Investigation": (2, "Impact", None),
    "MIT_SRP": (3, "SRP", None),
    "MetNet": (4, "MetNet", None),
    "TCGA": (None, "TCGA", ["local", "dev"]),
}


def _curated_investigations() -> dict:
    return {r["name"]: r for r in cg.curated_rows("projects") if r["entity_type"] == "investigation"}


def test_the_nine_investigation_rows_are_curated():
    investigations = _curated_investigations()
    assert {name: (r["project_id"], r["parent_project"], r.get("present_on"))
            for name, r in investigations.items()} == INVESTIGATION_ROWS
    assert len(cg.curated_rows("projects")) == 12 + 9


def test_an_investigation_is_owned_by_a_project_row_where_one_exists():
    rows = cg.curated_rows("projects")
    projects = {r["name"]: r for r in rows if r["entity_type"] == "project"}
    for name, row in _curated_investigations().items():
        owner = projects.get(row["parent_project"])
        if owner is None:
            assert row.get("present_on"), name          # only TCGA's owner has no row
        else:
            assert owner["project_id"] == row["project_id"], name


def test_a_project_and_its_same_named_investigation_are_both_curated():
    keys = {cg.key_of("projects", r) for r in cg.curated_rows("projects")}
    for name in ("CSBC", "MetNet"):
        assert {(name, "project"), (name, "investigation")} <= keys, name


def test_the_investigation_titles_left_the_project_rows_aliases():
    """An exact investigation title now resolves to its own row, not a whole project."""
    for row in cg.curated_rows("projects"):
        if row["entity_type"] == "project":
            folded = {cg.fold_key(a) for a in row.get("alternative_names") or []}
            assert not folded & {cg.fold_key(name) for name in INVESTIGATION_ROWS}, row["name"]


def test_what_people_type_reaches_the_investigation():
    investigations = _curated_investigations()
    for alias, name in (("Impact", "Impactb Investigation"), ("IMPAcTb", "Impactb Investigation"),
                        ("SRP", "MIT_SRP"), ("Superfund", "MIT_SRP"), ("BTC-GBM", "GBM_BTC"),
                        ("The Cancer Genome Atlas", "TCGA"), ("BioMicro Center", "BioMicroCenter")):
        assert alias in investigations[name]["alternative_names"], alias
    assert investigations["CSBC"]["alternative_names"] == []
    assert investigations["MetNet"]["alternative_names"] == []


def test_the_curated_projects_file_is_in_its_documented_order():
    """By name, folded; a project row before an investigation row of the same name."""
    rows = cg.load_source(cg.TABLES["projects"].source)
    assert rows == sorted(rows, key=lambda r: (r["name"].casefold(), r["entity_type"] != "project"))


def test_the_curated_rows_render_the_block_drift_will_read():
    block = cg.render_capabilities_text(cg.curated_rows("projects"))
    document = f"{cg.DRIFT_SECTION_HEADING}\n\n{block}"
    assert cg.listed_investigations(document) == [
        (name, INVESTIGATION_ROWS[name][2] is None) for name in sorted(INVESTIGATION_ROWS)]
    assert "(not on every instance: loaded on local and dev only)" in block


# --- 6.15 the generated investigation block ----------------------------------
#
# capabilities.md's "Known Projects and Investigations" section listed eight names and
# told the agent to "use these names exactly". Five of the eight return nothing: SEEK
# carries two parallel investigation systems, and the list named the paper-tracking
# copies rather than the real investigations that hold the samples.
#
# Operator decision, 2026-09-17: do not hand-edit that list, generate it from the
# investigation rows of projects_context. The generation is split in two (spec
# 2026-09-18, section 10.2): `render_capabilities_text(rows)` is pure and graph-free and
# makes every check a row allows; `check_investigation_counts(rows, docs)` holds the
# refusals that need a measurement, one counts file per instance. Counts only refuse:
# they never change the text. drift.py stays the runtime backstop.

# Synthetic counts: used to DECIDE, never emitted, and only their sign matters here.
LIVE_COUNTS = {
    "Impactb Investigation": 7001, "MIT_SRP": 7002, "GBM_BTC": 7003,
    "Endometriosis": 7004, "Collagen Study": 7005, "CSBC": 7006, "MetNet": 7007,
    "TCGA": 7008,
}
DEAD_NAMES = ("Impact", "SRP", "GBM", "Griffith", "Shoulders")


def _doc(counts: dict, measured_on: str = "local", nodes: int = 1) -> dict:
    """A counts file as `graph_sync --investigation-counts --instance <profile> --json`
    writes it."""
    return {"measured_on": measured_on, "measured_at": "2026-09-19T06:10:00Z",
            "investigations": {title: {"nodes": nodes, "samples": samples}
                               for title, samples in counts.items()}}


def _counts_for(*names, measured_on="local"):
    """The measured counts for exactly these names, as one counts file."""
    return _doc({name: LIVE_COUNTS[name] for name in names}, measured_on)


def _investigation(name, **extra):
    row = {"name": name, "entity_type": "investigation", "parent_project": "MIT-Koch",
           "project_id": 5, "research_focus": f"What {name} studies.",
           "alternative_names": [], "pi": None}
    row.update(extra)
    return row


def _tcga(**extra):
    """An investigation that is not on every instance, like the real TCGA."""
    return _investigation("TCGA", project_id=None, parent_project="TCGA",
                          present_on=["local", "dev"], **extra)


def test_capabilities_block_is_one_marked_generated_block():
    block = cg.render_capabilities_text([_investigation("TCGA")])
    assert block.startswith(cg.CAPABILITIES_BEGIN)
    assert block.rstrip("\n").endswith(cg.CAPABILITIES_END)
    assert "BEGIN" in cg.CAPABILITIES_BEGIN and "END" in cg.CAPABILITIES_END
    assert cg.CAPABILITIES_BEGIN.startswith("<!--") and cg.CAPABILITIES_END.endswith("-->")


def test_the_block_is_exactly_the_documented_shape():
    """Spec 10.2, verbatim, with invented research foci. The separator is a colon."""
    rows = [_investigation("Impactb Investigation", research_focus="Focus one",
                           alternative_names=["Impact", "IMPACT", "IMPAcTb"]),
            _tcga(research_focus="Focus two", alternative_names=["The Cancer Genome Atlas"])]
    assert cg.render_capabilities_text(rows) == (
        "<!-- BEGIN CONTEXT-GEN:investigations -->\n"
        "\n"
        "The graph database organizes samples into studies grouped under named investigations. "
        "The investigations that hold samples are:\n"
        "\n"
        "- **Impactb Investigation**: Focus one [also: Impact, IMPACT, IMPAcTb]\n"
        "- **TCGA**: Focus two [also: The Cancer Genome Atlas] "
        "(not on every instance: loaded on local and dev only)\n"
        "\n"
        "Use these names exactly when asking graph questions scoped to one investigation. "
        "The names in brackets are what people call them; the bold name is what the graph "
        "answers to. A name marked \"not on every instance\" is loaded only on the instances it "
        "lists. Where a query scoped to it finds no samples, it is not loaded on this instance: "
        "say so rather than reporting zero.\n"
        "\n"
        "<!-- END CONTEXT-GEN:investigations -->\n"
    )


def test_the_availability_sentence_appears_only_when_a_name_is_marked():
    block = cg.render_capabilities_text([_investigation("CSBC"), _investigation("MetNet")])
    assert "not on every instance" not in block
    assert block.count(" — ") == 0 and "**CSBC**: What CSBC studies." in block


def test_the_availability_note_lists_the_instances_in_a_fixed_order():
    assert cg.availability_note(None) is None
    assert cg.availability_note(["dev", "local"]) == "(not on every instance: loaded on local and dev only)"
    assert cg.availability_note(["prod"]) == "(not on every instance: loaded on prod only)"
    assert cg.availability_note(["prod", "local"]) == "(not on every instance: loaded on local and prod only)"
    assert cg.availability_note(["dev"]).startswith(cg.NOT_EVERYWHERE_MARK)


def test_capabilities_block_lists_investigations_and_skips_projects():
    """Only investigations. The section's names are checked against Investigation
    nodes, so a project row that is not also an investigation title would make the
    drift check fail for a row that is perfectly correct."""
    rows = [_investigation("TCGA"),
            {"name": "MIT-Koch", "entity_type": "project", "project_id": 5,
             "research_focus": "A program."}]
    block = cg.render_capabilities_text(rows)
    assert "**TCGA**" in block
    assert "MIT-Koch" not in block


def test_capabilities_block_carries_no_counts_and_counts_never_change_it():
    """A baked count rots the day the next sync runs, and the repo's doc rules
    forbid a dated count in a README or CLAUDE file. The counts only refuse."""
    rows = [_investigation(name, research_focus=f"{name} studies PAX3-FOXO1 and COL2A1.")
            for name in sorted(LIVE_COUNTS)]
    block = cg.render_capabilities_text(rows)
    assert cg.check_investigation_counts(rows, [_doc(LIVE_COUNTS)]) is None
    assert "PAX3-FOXO1" in block                  # a gene is not a count
    assert not cg._COUNT_LIKE.search(block)
    for count in LIVE_COUNTS.values():
        assert str(count) not in block and f"{count:,}" not in block
    assert cg.render_capabilities_text(rows) == block


def test_a_count_in_a_curated_description_is_refused():
    """The one field an author types free text into, and the only way a count could
    still reach the block."""
    import pytest

    for focus in ("Pan-cancer atlas of 1,234,567 samples across 33 cohorts.",
                  "Holds 76543 samples today."):
        with pytest.raises(cg.BakedCount):
            cg.render_capabilities_text([_investigation("TCGA", research_focus=focus)])


def test_the_block_refuses_to_drop_an_investigation_that_answers():
    """Silently under-reporting is the mirror of the zero-sample refusal: a name the
    measurement proves holds samples, with no curated row, would never reach the agent."""
    import pytest

    rows = [_investigation("TCGA")]
    with pytest.raises(cg.UnlistedInvestigation) as excinfo:
        cg.check_investigation_counts(rows, [_doc({"TCGA": 7008, "MetNet": 7007})])
    assert "MetNet" in str(excinfo.value)
    assert "TCGA" not in str(excinfo.value)
    # A name that answers nothing is not surplus; it is the other refusal's case.
    cg.check_investigation_counts(rows, [_doc({"TCGA": 7008, "GBM": 0})])


def test_an_ignored_title_is_not_unlisted():
    rows = [_investigation("TCGA")]
    counts = [_doc({"TCGA": 7008, "Paper Copy": 3})]
    cg.check_investigation_counts(rows, counts, ignore=["Paper Copy"])


def test_capabilities_block_refuses_an_investigation_with_no_samples():
    """The refusal that is the whole point of generating this section."""
    import pytest

    rows = [_investigation("TCGA")] + [_investigation(name) for name in DEAD_NAMES]
    with pytest.raises(cg.ZeroSampleInvestigation) as excinfo:
        cg.check_investigation_counts(rows, [_doc(dict(LIVE_COUNTS, GBM=0))])
    message = str(excinfo.value)
    for name in DEAD_NAMES:
        assert name in message, name
    assert "TCGA" not in message


def test_capabilities_block_refuses_a_name_the_counts_do_not_mention():
    """Absent is not zero, but for a name on every instance it is not evidence either."""
    import pytest

    with pytest.raises(cg.ZeroSampleInvestigation):
        cg.check_investigation_counts([_investigation("Nowhere")], [_doc({"TCGA": 7008})])


def test_capabilities_block_refuses_with_no_counts_at_all():
    import pytest

    with pytest.raises(cg.ZeroSampleInvestigation):
        cg.check_investigation_counts([_investigation("TCGA")], [])


def test_absent_and_empty_are_told_apart_by_where_the_count_was_measured():
    """Spec 10.3's table. On an instance the row's present_on names, the name must hold
    samples; on one it does not name, it must be absent: an empty node there is the
    confident zero, and samples there mean present_on is wrong."""
    import pytest

    rows = [_tcga()]
    cg.check_investigation_counts(rows, [_doc({"TCGA": 5}, "local")])
    cg.check_investigation_counts(rows, [_doc({}, "prod")])
    for doc in (_doc({}, "local"), _doc({"TCGA": 0}, "local"), _doc({"TCGA": 0}, "prod")):
        with pytest.raises(cg.ZeroSampleInvestigation) as excinfo:
            cg.check_investigation_counts(rows, [doc])
        assert doc["measured_on"] in str(excinfo.value)
    with pytest.raises(cg.AvailabilityMismatch) as excinfo:
        cg.check_investigation_counts(rows, [_doc({"TCGA": 5}, "prod")])
    assert "present_on" in str(excinfo.value)
    # A name on every instance is held to "holds samples" wherever it was measured.
    everywhere = [_investigation("CSBC")]
    for where in cg.PROFILES:
        cg.check_investigation_counts(everywhere, [_doc({"CSBC": 5}, where)])
        with pytest.raises(cg.ZeroSampleInvestigation):
            cg.check_investigation_counts(everywhere, [_doc({}, where)])


def test_every_counts_file_must_pass():
    import pytest

    rows = [_investigation("CSBC")]
    with pytest.raises(cg.ZeroSampleInvestigation) as excinfo:
        cg.check_investigation_counts(rows, [_doc({"CSBC": 5}, "local"), _doc({}, "dev")])
    assert "dev" in str(excinfo.value)


def test_a_counts_file_says_where_and_when_it_was_measured():
    import pytest

    good = _doc({"CSBC": 5})
    bad = [
        dict(good, measured_on="staging"), dict(good, measured_on=None),
        {k: v for k, v in good.items() if k != "measured_on"},
        dict(good, measured_at=""), {k: v for k, v in good.items() if k != "measured_at"},
        dict(good, investigations=[]), dict(good, investigations={"CSBC": 5}),
        dict(good, investigations={"CSBC": {"samples": 5}}),
        dict(good, investigations={"CSBC": {"nodes": 1, "samples": "5"}}),
        dict(good, investigations={"CSBC": {"nodes": 1, "samples": True}}),
        dict(good, investigations={"CSBC": {"nodes": 1, "samples": -1}}),
        dict(good, extra=1),
    ]
    for doc in bad:
        with pytest.raises(cg.UnsupportedValue):
            cg.check_investigation_counts([_investigation("CSBC")], [doc])
    with pytest.raises(cg.UnsupportedValue) as excinfo:     # two files from one instance
        cg.check_investigation_counts([_investigation("CSBC")], [good, dict(good)])
    assert "local" in str(excinfo.value)


def test_the_flat_shape_and_drifts_stat_are_no_longer_counts(tmp_path):
    """Neither says where it was measured, and neither can tell an absent investigation
    from an empty one, so both are refused with the command that writes the new shape."""
    import json as _json

    import pytest

    for payload in ({"TCGA": 7008}, {"samples": {"TCGA": 7008}},
                    {"stats": {"assistant_investigations": {"samples": {}}}}):
        path = tmp_path / "counts.json"
        path.write_text(_json.dumps(payload))
        with pytest.raises(cg.UnsupportedValue) as excinfo:
            cg.load_counts(path)
        assert "--investigation-counts" in str(excinfo.value)
    path.write_text(_json.dumps(_doc({"TCGA": 1})))
    assert cg.load_counts(path) == _doc({"TCGA": 1})


def test_capabilities_block_refuses_when_no_row_is_an_investigation():
    """Emitting an empty list would silently delete the agent's only list of
    investigations."""
    import pytest

    rows = [{"name": "Zephyr", "entity_type": "project", "project_id": 4}]
    with pytest.raises(cg.NoInvestigations):
        cg.render_capabilities_text(rows)
    with pytest.raises(cg.NoInvestigations):
        cg.check_investigation_counts(rows, [_doc({})])


def test_capabilities_block_refuses_an_investigation_with_nothing_to_say():
    import pytest

    with pytest.raises(cg.IncompleteInvestigation):
        cg.render_capabilities_text([_investigation("TCGA", research_focus=None)])


def test_a_description_is_no_substitute_for_a_research_focus():
    """The block used to fall back to the description's first sentence. research_focus
    is now required on an investigation row, so the fallback could only hide a gap."""
    import pytest

    row = _investigation("TCGA", research_focus=None,
                         description="Public pan-cancer atlas. Many more sentences follow.")
    with pytest.raises(cg.IncompleteInvestigation):
        cg.render_capabilities_text([row])


def test_capabilities_block_bridges_what_users_type_to_the_exact_title():
    """`Impact` has to reach `Impactb Investigation` instead of failing silently."""
    row = _investigation("Impactb Investigation",
                         research_focus="Tuberculosis in non-human primates.",
                         alternative_names=["Impact", "IMPAcTb"])
    block = cg.render_capabilities_text([row])
    assert "**Impactb Investigation**" in block
    assert "[also: Impact, IMPAcTb]" in block


def test_capabilities_block_sorts_by_name_and_one_bullet_per_row():
    rows = [_investigation(name) for name in ("TCGA", "CSBC", "MetNet")]
    bullets = [line for line in cg.render_capabilities_text(rows).splitlines()
               if line.startswith("- **")]
    assert [b.split("**")[1] for b in bullets] == ["CSBC", "MetNet", "TCGA"]


def test_the_drift_check_reads_exactly_the_names_the_block_emits():
    """The generator and the runtime backstop have to agree, so this uses the real
    parser rather than a copy of its regex."""
    from nextseek_api.graph_sync import drift

    names = ["CSBC", "Collagen Study", "Endometriosis", "GBM_BTC",
             "Impactb Investigation", "MIT_SRP", "MetNet", "TCGA"]
    rows = [_investigation(name) for name in names if name != "TCGA"] + [_tcga()]
    document = ("## Known Projects and Investigations\n\n" + cg.render_capabilities_text(rows) +
                "\n---\n\n## What the System Cannot Do\n\n- **Generate charts** nope\n")
    assert drift.assistant_investigation_names(document) == sorted(names)
    assert [name for name, _ in cg.listed_investigations(document)] == sorted(names)


def test_the_generators_mirror_of_drifts_parser_reads_what_drift_reads():
    """The gate is standard library only, so it cannot import drift; it reads the block
    with `listed_investigations` instead, which has to agree with drift's parser."""
    from nextseek_api.graph_sync import drift

    documents = [
        "## Known Projects and Investigations\n\n- **A** x\n- **B**: y\n\n---\n\n- **C** z\n",
        "# Title\n\n## Known Projects and Investigations\n- **A** x\n## Next\n- **B** y\n",
        "## Something else\n\n- **A** x\n",
        _repo(Path("NessieAI/chat_nextseek/src/chat_nextseek/context/capabilities.md")),
    ]
    for document in documents:
        assert [n for n, _ in cg.listed_investigations(document)] == \
            drift.assistant_investigation_names(document)


def test_the_block_replaces_the_section_body_between_its_markers():
    """The substitution as text: context_gen writes the block into capabilities.md,
    which the image then COPYs and both images rebuild."""
    block = cg.render_capabilities_text([_investigation("TCGA")])
    before = (f"{cg.DRIFT_SECTION_HEADING}\n\n"
              f"{cg.CAPABILITIES_BEGIN}\nold text\n{cg.CAPABILITIES_END}\n\n---\n")
    after = cg.replace_capabilities_block(before, block)
    assert "old text" not in after
    assert "**TCGA**" in after
    assert after.count(cg.CAPABILITIES_BEGIN) == 1
    assert after.endswith("\n---\n")
    assert cg.replace_capabilities_block(after, block) == after      # idempotent


def test_the_block_refuses_to_sit_anywhere_drift_would_not_read_it():
    """Generation and the runtime backstop share a blind spot without this: drift keys on
    the exact heading, and with no such line its check PASSES."""
    import pytest
    from nextseek_api.graph_sync import drift

    block = cg.render_capabilities_text([_investigation("TCGA")])
    renamed = (f"## Known Investigations\n\n"
               f"{cg.CAPABILITIES_BEGIN}\nold\n{cg.CAPABILITIES_END}\n\n---\n")
    assert drift.assistant_investigation_names(
        renamed.replace(f"{cg.CAPABILITIES_BEGIN}\nold\n{cg.CAPABILITIES_END}", block)
    ) == []
    with pytest.raises(ValueError) as excinfo:
        cg.replace_capabilities_block(renamed, block)
    assert cg.DRIFT_SECTION_HEADING in str(excinfo.value)
    assert drift._CAPABILITIES_SECTION.pattern.strip("^$\\s+").startswith("##")


def test_regenerating_the_block_leaves_the_ns_projection_identical():
    """The NS projection reads only REQUIRED_H2, never this section, so regenerating the
    block cannot move route_capabilities.json. Run against the REAL committed
    capabilities.md, whose markers are placed, and the block of the real curated rows."""
    import importlib.util
    import sys

    location = Path(cg.REPO_ROOT) / "NessieAI/cc/op_registry/ns_capabilities.py"
    spec = importlib.util.spec_from_file_location("ns_capabilities_for_test", location)
    ns_capabilities = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = ns_capabilities      # its @dataclass looks itself up
    spec.loader.exec_module(ns_capabilities)
    assert ns_capabilities.REQUIRED_H2 == (
        "Overview", "What You Can Ask", "What the System Cannot Do")

    text = _repo(Path("NessieAI/chat_nextseek/src/chat_nextseek/context/capabilities.md"))
    block = cg.render_capabilities_text(cg.curated_rows("projects"))
    before = ns_capabilities.project_ns_capabilities(text)
    after = ns_capabilities.project_ns_capabilities(cg.replace_capabilities_block(text, block))
    assert before == after
    assert before.route_level_object() == after.route_level_object()


def _counts_file(tmp_path, name, doc):
    import json as _json

    path = tmp_path / name
    path.write_text(_json.dumps(doc))
    return str(path)


def test_the_capabilities_mode_writes_the_block_between_the_committed_markers(tmp_path):
    """The markers are placed, so with counts that clear every curated investigation row
    the mode writes the rows' block between them and leaves every other byte alone. It
    writes into a copy here; the committed file is the operator's to regenerate. And
    --counts is still required."""
    import pytest

    parser_text = _repo(Path("scripts/context_gen.py"))
    assert '"update", "seed", "capabilities"' in parser_text
    rows = cg.curated_rows("projects")
    local = {r["name"]: 5 for r in rows if r["entity_type"] == "investigation"}
    counts = _counts_file(tmp_path, "local.json", _doc(local))
    target = tmp_path / "capabilities.md"
    committed = _repo(Path("NessieAI/chat_nextseek/src/chat_nextseek/context/capabilities.md"))
    target.write_text(committed)
    assert cg.emit_capabilities([counts], out=target) == 0
    written = target.read_text()
    assert written == cg.replace_capabilities_block(committed, cg.render_capabilities_text(rows))
    assert written.split(cg.CAPABILITIES_BEGIN)[0] == committed.split(cg.CAPABILITIES_BEGIN)[0]
    assert written.split(cg.CAPABILITIES_END)[1] == committed.split(cg.CAPABILITIES_END)[1]
    with pytest.raises(SystemExit):
        cg.main(["--emit", "capabilities"])
    with pytest.raises(SystemExit):
        cg.main(["--emit", "seed", "--ignore-investigation", "X"])


def test_the_capabilities_mode_takes_a_counts_file_per_instance(tmp_path, monkeypatch):
    """`--counts` once per instance, each must pass, `--ignore-investigation` by title;
    what lands between the markers is `render_capabilities_text` of the rows."""
    import pytest

    rows = [_investigation("CSBC"), _tcga()]
    monkeypatch.setattr(cg, "curated_rows", lambda table: [dict(r) for r in rows])
    target = tmp_path / "capabilities.md"
    original = (f"{cg.DRIFT_SECTION_HEADING}\n\n{cg.CAPABILITIES_BEGIN}\nold\n"
                f"{cg.CAPABILITIES_END}\n\n---\n\n## Tips\n")
    target.write_text(original)
    local = _counts_file(tmp_path, "local.json",
                         _doc({"CSBC": 3, "TCGA": 4, "Paper Copy": 2}, "local"))
    prod = _counts_file(tmp_path, "prod.json", _doc({"CSBC": 9}, "prod"))
    with pytest.raises(cg.UnlistedInvestigation):
        cg.main(["--emit", "capabilities", "--counts", local, "--counts", prod, "--out", str(target)])
    assert target.read_text() == original                  # a refusal writes nothing
    assert cg.main(["--emit", "capabilities", "--counts", local, "--counts", prod,
                    "--ignore-investigation", "Paper Copy", "--out", str(target)]) == 0
    written = target.read_text()
    assert written == cg.replace_capabilities_block(original, cg.render_capabilities_text(rows))
    dead = _counts_file(tmp_path, "dev.json", _doc({"CSBC": 9, "TCGA": 0}, "dev"))
    with pytest.raises(cg.ZeroSampleInvestigation):
        cg.main(["--emit", "capabilities", "--counts", dead, "--out", str(target)])
    assert target.read_text() == written


def test_the_committed_capabilities_file_carries_one_marker_pair_around_drifts_names():
    """6.15c's markers, placed: one well-formed pair, inside the section drift reads, and
    every name drift reads sits between them, so the generated block is the whole list."""
    from nextseek_api.graph_sync import drift

    text = _repo(Path("NessieAI/chat_nextseek/src/chat_nextseek/context/capabilities.md"))
    assert text.count(cg.CAPABILITIES_BEGIN) == 1 and text.count(cg.CAPABILITIES_END) == 1
    assert cg.check_capabilities_markers(text) == []
    inside = text.split(cg.CAPABILITIES_BEGIN, 1)[1].split(cg.CAPABILITIES_END, 1)[0]
    names = drift.assistant_investigation_names(text)
    assert names
    assert drift.assistant_investigation_names(f"{cg.DRIFT_SECTION_HEADING}\n{inside}") == names
    head = text.split(cg.CAPABILITIES_BEGIN, 1)[0]
    assert head.rstrip("\n").endswith(cg.DRIFT_SECTION_HEADING)


_SECTION = f"{cg.DRIFT_SECTION_HEADING}\n\n"
_AFTER = "\n---\n\n## What the System Cannot Do\n\n- **Charts** no\n\n## Tips\n\n- tip\n"
_PAIR = f"{cg.CAPABILITIES_BEGIN}\nold\n{cg.CAPABILITIES_END}\n"

MALFORMED_MARKERS = {
    # END above BEGIN: the text between them was duplicated on every run.
    "reversed": _SECTION + f"{cg.CAPABILITIES_END}\nold\n{cg.CAPABILITIES_BEGIN}\n" + _AFTER,
    # A second pair: the stale second block survived and drift read its names.
    "duplicated": _SECTION + _PAIR + "\n" + _PAIR + _AFTER,
    # END at the end of the file: every section after BEGIN was deleted, exit 0.
    "END past later sections": (_SECTION + f"{cg.CAPABILITIES_BEGIN}\nold\n" + _AFTER
                                + f"{cg.CAPABILITIES_END}\n"),
    # A rule between the heading and BEGIN ends drift's section before the block,
    # so drift read no names and its check passed.
    "rule before BEGIN": _SECTION + "---\n\n" + _PAIR + _AFTER,
    # Under a later heading: drift kept reading the hand-kept list above.
    "under a later heading": _SECTION + "- **Old** x\n" + _AFTER + "\n" + _PAIR,
    "BEGIN only": _SECTION + f"{cg.CAPABILITIES_BEGIN}\nold\n" + _AFTER,
}


def test_malformed_markers_are_refused_and_named():
    """Only the presence of each marker was checked, and the first of each was used."""
    import pytest

    block = cg.render_capabilities_text([_investigation("TCGA")])
    for case, text in MALFORMED_MARKERS.items():
        assert cg.check_capabilities_markers(text), case
        with pytest.raises(ValueError):
            cg.replace_capabilities_block(text, block)
    good = _SECTION + _PAIR + _AFTER
    assert cg.check_capabilities_markers(good) == []
    after = cg.replace_capabilities_block(good, block)
    assert cg.check_capabilities_markers(after) == []
    assert after.split(cg.CAPABILITIES_END, 1)[1] == good.split(cg.CAPABILITIES_END, 1)[1]
    assert cg.replace_capabilities_block(after, block) == after


def test_a_document_with_no_markers_is_well_formed_until_someone_places_them():
    assert cg.check_capabilities_markers(_SECTION + "- **Old** x\n" + _AFTER) == []


def test_replacing_the_block_refuses_a_document_with_no_markers():
    import pytest

    with pytest.raises(ValueError):
        cg.replace_capabilities_block("## Known Projects and Investigations\n\n- **X** y\n", "b")


def test_a_dead_name_may_survive_as_an_alternative_but_never_as_a_checked_name():
    """`SRP` resolves to nothing and is also what people type for `MIT_SRP`: it reaches
    the agent as an alias, outside the bold run drift checks."""
    from nextseek_api.graph_sync import drift

    row = _investigation("MIT_SRP", research_focus="Environmental exposure and DNA damage.",
                         alternative_names=["SRP"])
    block = cg.render_capabilities_text([row])
    assert "[also: SRP]" in block
    document = "## Known Projects and Investigations\n\n" + block + "\n---\n"
    assert drift.assistant_investigation_names(document) == ["MIT_SRP"]
    for name in DEAD_NAMES:
        assert name not in drift.assistant_investigation_names(document)


def test_the_block_checks_its_rows_like_every_other_table():
    """A misspelt `alternative_name` dropped its aliases silently; a string alias list
    rendered letter by letter; two rows named alike gave two bullets."""
    import pytest

    with pytest.raises(cg.UnknownColumn):
        cg.render_capabilities_text([_investigation("TCGA", alternative_name=["x"])])
    with pytest.raises(cg.UnsupportedValue):
        cg.render_capabilities_text([_investigation("TCGA", alternative_names="Impact")])
    with pytest.raises(cg.DuplicateKey):
        cg.render_capabilities_text([_investigation("TCGA"), _investigation("tcga")])
    with pytest.raises(cg.UnsupportedValue):
        cg.render_capabilities_text([_investigation(" TCGA ")])


def test_a_count_in_an_alias_or_with_a_count_noun_is_refused_but_a_year_is_not():
    import pytest

    for row in (_investigation("TCGA", research_focus="Holds 321 samples."),
                _investigation("TCGA", research_focus="About 84k samples."),
                _investigation("TCGA", research_focus="Over 900 donors."),
                _investigation("TCGA", alternative_names=["TCGA 123456 samples"])):
        with pytest.raises(cg.BakedCount):
            cg.render_capabilities_text([row])
    cg.render_capabilities_text(
        [_investigation("TCGA", research_focus="Samples collected 2019-2023, PAX3-FOXO1.")])


def test_a_title_never_carries_markdown_that_would_split_the_bold_run():
    """The regex captures `[^*]+`, so a `*` in a name would truncate it."""
    import pytest

    with pytest.raises(cg.UnsupportedValue):
        cg.render_capabilities_text([_investigation("Bad*Name", research_focus="Anything.")])


def test_an_alternative_name_is_sanitised_exactly_like_a_title():
    """A newline in an alias opened a bullet of its own that drift read as a curated name."""
    import pytest
    from nextseek_api.graph_sync import drift

    row = _investigation("MIT_SRP", research_focus="Anything.",
                         alternative_names=["SRP", "x]\n- **GBM**"])
    with pytest.raises(cg.UnsupportedValue) as excinfo:
        cg.render_capabilities_text([row])
    assert "alternative name" in str(excinfo.value)
    starred = _investigation("MIT_SRP", research_focus="Anything.", alternative_names=["S*RP"])
    with pytest.raises(cg.UnsupportedValue):
        cg.render_capabilities_text([starred])
    clean = _investigation("MIT_SRP", research_focus="Anything.", alternative_names=["SRP"])
    document = ("## Known Projects and Investigations\n\n" + cg.render_capabilities_text([clean])
                + "\n---\n")
    assert drift.assistant_investigation_names(document) == ["MIT_SRP"]


def test_curated_text_may_not_carry_a_marker_or_the_availability_phrase():
    """A marker in curated text would end the block early; the availability phrase in a
    research_focus would mark a name as not on every instance that is."""
    import pytest

    for row in (_investigation("TCGA", research_focus="Held (not on every instance: dev)."),
                _investigation("TCGA", research_focus=f"x {cg.CAPABILITIES_END}"),
                _investigation("TCGA", alternative_names=["<!-- note -->"])):
        with pytest.raises(cg.UnsupportedValue):
            cg.render_capabilities_text([row])


def test_the_availability_phrase_is_the_one_drift_keys_on():
    """The generator writes the phrase and drift reads it; neither owns it, so this ties them,
    as DRIFT_SECTION_HEADING is tied, and holds the two parsers to one reading of a block."""
    from nextseek_api.graph_sync import drift

    assert cg.NOT_EVERYWHERE_MARK == drift.NOT_EVERYWHERE_MARK
    rows = [_investigation("CSBC"), _tcga(), _investigation("MetNet", present_on=["prod"])]
    document = ("## Known Projects and Investigations\n\n" + cg.render_capabilities_text(rows)
                + "\n---\n")
    expected = [("CSBC", True), ("MetNet", False), ("TCGA", False)]
    assert drift.assistant_investigation_entries(document) == expected
    assert cg.listed_investigations(document) == expected


# --- the investigation names outside the block (spec 2026-09-18, section 10.7) ---
#
# Only the list is generated. The example queries around it are prose, and they named
# the dead investigations too: "the SRP investigation", "the GBM project", a tip listing
# seven names. They are edited by hand, and these pin them to the curated rows: an
# investigation is named by its exact title, a project by a project row's name or alias.

import re as _re

_CAPABILITIES_FILE = Path("NessieAI/chat_nextseek/src/chat_nextseek/context/capabilities.md")


def _outside_the_block() -> str:
    text = _repo(_CAPABILITIES_FILE)
    return text.split(cg.CAPABILITIES_BEGIN, 1)[0] + text.split(cg.CAPABILITIES_END, 1)[1]


def test_the_prose_names_an_investigation_by_its_exact_title():
    titles = set(INVESTIGATION_ROWS)
    prose = _outside_the_block()
    named = _re.findall(r"\bthe ((?:[\w-]+ ){0,2}[\w-]+) investigation\b", prose)
    assert named, "no example names an investigation any more"
    assert [n for n in named if n not in titles] == []
    entry = next(line for line in prose.splitlines() if line.startswith("- **Investigation**"))
    examples = _re.findall(r'"([^"]+)"', entry)
    assert examples and set(examples) <= titles, examples


def test_the_prose_names_a_project_by_a_project_row():
    rows = cg.curated_rows("projects")
    known = {cg.fold_key(r["name"]) for r in rows if r["entity_type"] == "project"}
    known |= {cg.fold_key(a) for r in rows if r["entity_type"] == "project"
              for a in r.get("alternative_names") or []}
    named = _re.findall(r"\bthe ((?:[\w-]+ ){0,2}[\w-]+) project\b", _outside_the_block())
    assert named and [n for n in named if cg.fold_key(n) not in known] == []


def test_the_tip_points_at_the_generated_list_instead_of_keeping_one():
    prose = _outside_the_block()
    tip = next(p for p in prose.split("\n\n") if "Use investigation names" in p)
    assert cg.DRIFT_SECTION_HEADING.lstrip("# ") in tip
    assert "GBM_BTC investigation" in tip
    assert not _re.search(r"\((?:[\w-]+, ){2,}", tip), tip


def test_the_cc_manifest_points_at_the_generated_list_instead_of_keeping_one():
    """The CC plugin's MANIFEST.md listed five dead names as the investigations; its
    capabilities.md row now points at the generated list, and its projects_db.json row says
    what the rows carry (spec 2026-09-18, section 10.7)."""
    md = _repo(Path("NessieAI/docker/cc-runtime/build_context/plugins/nextseek/context/MANIFEST.md"))
    lines = md.splitlines()
    capabilities = next(line for line in lines if line.startswith("| `capabilities.md`"))
    for name in (*DEAD_NAMES, *INVESTIGATION_ROWS):
        assert not _re.search(rf"\b{_re.escape(name)}\b", capabilities), name
    assert "Known Projects and Investigations" in capabilities
    projects = next(line for line in lines if line.startswith("| `projects_db.json`"))
    assert "entity_type" in projects and "labs" in projects

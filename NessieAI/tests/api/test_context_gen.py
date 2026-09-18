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
    """The DDL check, against fixtures this module does not write.

    The installer's startup/seed/sql/assay_context.sql and projects_context.sql
    are the pre-generator files (the generator's own output is held in the
    `.curated.sql` files beside them), and the model is Django's, so all three
    are independent of `cg.DDL`. `test_every_column_is_one_the_runtime_actually_reads`,
    below, checks the same columns against the consumer.
    """
    assert set(cg.COLUMNS["assays"]) == _assay_seed_columns()
    assert set(cg.COLUMNS["sample_types"]) == _model_columns() | {"repository_attributes"}
    assert set(cg.COLUMNS["projects"]) == _projects_ddl_columns() | {"pi_names"}
    # Everything the fixtures do not name is declared as new, with a reason.
    assert set(cg.ADDED_COLUMNS) == {"sample_types", "projects"}
    assert set(cg.ADDED_COLUMNS["sample_types"]) == {"repository_attributes"}
    assert set(cg.ADDED_COLUMNS["projects"]) == {"pi_names"}


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

    The two exceptions are named rather than subtracted, because each is a real
    open gap and not a convention: `pi_names` and `repository_attributes` are
    written to the database and read by nothing. `map_project` builds
    projects_db.json from a fixed key list ending at `tags`, so pi_names never
    reaches the entity agent, and `map_sampletype` drops repository_attributes the
    same way. Wiring pi_names up is plan task 13c's; repository_attributes has no
    named consumer at all.
    """
    unread = {"projects": {"pi_names"}, "sample_types": {"repository_attributes"},
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
    assert cg.parse_pi(PI_CSBC) == [
        "White", "Forest M. White", "Forest White", "Michor", "Franziska Michor",
    ]


def test_parse_pi_yields_the_spelling_a_question_actually_uses():
    """The initial-free full name, which is the one a person types.

    The curated file writes a middle initial for most PIs and nobody asking a
    question does, and neither exact nor substring matching bridges the two:
    "Roger Kamm" is not a substring of "Roger D. Kamm" or the reverse. Before this
    the column carried only the surname and the middle-initial spelling, so 18 of
    the 21 curated PI entries had no form a question could match. That the plain
    form is the live one is measurable in the repo: the CSBC row's own `tags` carry
    "Forest White" and MetNet's description says "led by Roger Kamm (MIT)".
    """
    rows = {r["name"]: r["pi_names"] for r in cg.rows_for("projects")}
    assert "Roger Kamm" in rows["MetNet"]
    assert "Forest White" in rows["CSBC"]
    assert "Linda Griffith" in rows["Griffith"]
    for name in ("Sarah Fortune", "JoAnne Flynn", "Alex Shalek", "Douglas Lauffenburger"):
        assert name in rows["Impact"], name
    # The middle-initial spelling is kept, not replaced.
    assert "Roger D. Kamm" in rows["MetNet"]


def test_a_multi_word_surname_keeps_every_word_when_the_initials_go():
    assert cg.parse_pi("van der Meer, Jos W. M. (Radboud)") == [
        "van der Meer", "Jos W. M. van der Meer", "Jos van der Meer",
    ]


def test_parse_pi_ignores_a_semicolon_inside_the_parenthetical():
    assert cg.parse_pi(PI_GRIFFITH) == [
        "Griffith", "Linda G. Griffith", "Linda Griffith",
        "Goods", "Brittany A. Goods", "Brittany Goods",
    ]


def test_parse_pi_reads_a_pi_with_no_parenthetical():
    assert cg.parse_pi(PI_RMS) == [
        "Koehler", "Angela N. Koehler", "Angela Koehler",
        "Burgin", "Alex B. Burgin", "Alex Burgin",
        "Gould", "Alexandra E. Gould", "Alexandra Gould",
        "Linardic", "Corinne M. Linardic", "Corinne Linardic",
        "Nomura", "Daniel Nomura",
    ]


def test_parse_pi_of_nothing_is_empty():
    for empty in ("None", "none", "", "   ", None):
        assert cg.parse_pi(empty) == [], repr(empty)


def test_an_unclosed_parenthesis_is_refused_rather_than_swallowing_the_rest():
    """One missing `)` silently deleted every PI after it.

    `_split_outside_parens` never returns depth to 0 once a `(` is unclosed, so
    every later semicolon is swallowed. Measured:
    `parse_pi("Kamm, Roger D. (MIT, contact PI; Shenoy, Vivek B. (UPenn, co-PI)")`
    returned `['Kamm', 'Roger D. Kamm']` -- Shenoy gone, no exception, and the
    rendered INSERT reporting success. This module refuses rather than guesses
    everywhere else a value is ambiguous, and the output is SQL bound for
    production.
    """
    import pytest

    with pytest.raises(cg.UnsupportedValue) as excinfo:
        cg.parse_pi("Kamm, Roger D. (MIT, contact PI; Shenoy, Vivek B. (UPenn, co-PI)")
    assert "unclosed" in str(excinfo.value)
    # Balanced nesting, which the Griffith row has, still parses.
    assert cg.parse_pi("Kamm, Roger D. (MIT (Mech E), PI)") == [
        "Kamm", "Roger D. Kamm", "Roger Kamm"]


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
    assert csbc["pi_names"] == [
        "White", "Forest M. White", "Forest White", "Michor", "Franziska Michor",
    ]
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


def test_a_row_with_no_key_at_all_is_deleted_too():
    """`NULL NOT IN (...)` is NULL, not TRUE, so the plain delete never saw one.

    A unique key permits any number of NULLs and no upsert matches one either, so
    such a row survived every run untouched while the script's own header promised
    the table would hold exactly the curated rows. Proven on MySQL: a preloaded
    `(assay_name=NULL)` row came back after run 1 and run 2 unchanged.
    """
    for table, spec in cg.TABLES.items():
        sql = cg.render_update(table, _rows_for(table))
        assert f"OR `{spec.key}` IS NULL;" in sql, table


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


def test_the_destructive_half_is_one_transaction():
    """A value the server refuses must not leave the DELETE committed.

    That is not hypothetical: an over-long `pi` aborted the upserts with error 1406
    having already committed the delete, so the table was left with GBM gone, 0 of
    12 rows written and no way back. DDL commits implicitly in MySQL, so the ALTERs
    cannot join the transaction -- they are idempotent and non-destructive instead,
    and the delete plus the upserts are what is wrapped. Reproduced on MySQL after
    the fix: the same 1406 rolled back and GBM was still there.
    """
    for table, spec in cg.TABLES.items():
        sql = cg.render_update(table, _rows_for(table))
        begin, commit = sql.index("START TRANSACTION;"), sql.rindex("COMMIT;")
        delete = sql.index(f"DELETE FROM `{spec.name}` WHERE")
        last_upsert = sql.rindex("ON DUPLICATE KEY UPDATE")
        assert begin < delete < last_upsert < commit, table
        # And the ALTERs stay outside it, where an implicit commit cannot break it.
        assert sql.index("ALTER TABLE") < begin, table


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
    assert ('\'["White", "Forest M. White", "Forest White", "Michor", '
            '"Franziska Michor"]\'') in sql


def test_update_is_deterministic():
    rows = _rows_for("assays")
    assert cg.render_update("assays", rows) == cg.render_update("assays", rows)


def test_update_refuses_a_duplicate_key_in_the_source():
    import pytest

    rows = [{"name": "CSBC"}, {"name": "csbc "}]
    with pytest.raises(cg.DuplicateKey):
        cg.render_update("projects", cg.with_pi_names(rows))


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
            cg.render_update("projects", cg.with_pi_names(
                [{"name": first}, {"name": second}]))
    assert cg.fold_key("Müller") == cg.fold_key("Muller")


def test_the_real_curated_keys_do_not_collide_under_that_wider_fold():
    """The wider net must not refuse data that is already there."""
    for table in ("sample_types", "assays", "projects"):
        rows = cg.load_source(cg.TABLES[table].source)
        cg._checked_keys(table, rows)           # raises on a collision


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

    rows = cg.with_pi_names([{"name": WIDTH_ERROR_EXAMPLE, "description": "x"}])
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
    rows = cg.with_pi_names([{"name": "Emoji", "description": four_byte}])
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
    sql = cg.render_update("projects", _rows_for("projects"))
    assert "ADD COLUMN `pi_names` TEXT CHARACTER SET utf8mb4" in sql


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
    # map: seek assay 466 -> Library Creation, and only while it is still NULL
    assert ("UPDATE `assays_internal_assays` SET `internal_assay_id` = "
            "(SELECT `id` FROM `internal_assays` WHERE `internal_assay_title` = "
            "'Library Creation' ORDER BY `id` LIMIT 1)\n"
            "  WHERE `assay_id` = 466 AND `internal_assay_id` IS NULL\n"
            "  AND EXISTS (SELECT 1 FROM `internal_assays` WHERE "
            "`internal_assay_title` = 'Library Creation');") in sql
    # remap: seek assay 37 moves off 130, and re-running is a no-op
    assert "WHERE `assay_id` = 37 AND `internal_assay_id` IN (130, (SELECT `id`" in sql


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
        title = statement.split("`internal_assay_title` = '", 1)[1].split("'", 1)[0]
        assert ("AND EXISTS (SELECT 1 FROM `internal_assays` WHERE "
                f"`internal_assay_title` = '{title}')") in statement, title


def test_the_created_internal_assays_are_backfilled_into_assay_context():
    """The 14 assay_context rows whose internal_assay_id nothing else ever writes.

    They are exactly the 14 create_internal titles (check_mapping_consistency
    enforces that), their ids are assigned by AUTO_INCREMENT, and before this the
    emitted script held zero `UPDATE assay_context` statements -- so the column
    stayed NULL forever while context/README.md said the generator assigns it and
    chat_nextseek publishes it to the agent as "Internal Assay ID". It has to live
    with the mappings, not with --table assays, because the ids do not exist until
    the creates above have run and the assay rows are emitted first.
    """
    mappings = cg.load_source(cg.TABLES_EXTRA["mappings"])
    sql = cg.render_update("mappings", mappings)
    created = [m["internal_assay_title"] for m in mappings
               if m["action"] == "create_internal"]
    assert len(created) == 14
    assert sql.count("UPDATE `assay_context` SET `internal_assay_id`") == 14
    for title in created:
        assert (f"  WHERE `assay_name` = '{title}' AND `internal_assay_id` IS NULL;"
                ) in sql, title
    # After the creates, or it would resolve to nothing.
    assert sql.index("INSERT INTO `internal_assays`") < sql.index("UPDATE `assay_context`")


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
# The engine is SQLite because the lane has no database, and what that engine
# CANNOT see is now written down here rather than left implied, because it is what
# let a seed file that aborts the installer at line 35 through a green suite:
#
#   * a VARCHAR width. SQLite ignores one, so the 276 character `pi` that MySQL
#     refuses with error 1406 round-trips byte for byte here. `check_widths` and
#     `test_no_curated_value_exceeds_its_declared_column_width` are what cover
#     that, statically, against the DDL.
#   * a charset. There is none, so the latin1 double encoding cannot occur here
#     either; `test_every_artifact_pins_the_connection_charset` covers it.
#   * a collation. SQLite's default is BINARY and case-sensitive, so `Müller` and
#     `Muller` are simply distinct keys;
#     `test_a_collision_only_utf8mb4_unicode_ci_would_see_is_refused_too` covers it.
#   * the instance's real column list. Every fixture here has an `id`;
#     `dmac.projects_context` does not, which is
#     `test_the_dedupe_runs_only_where_there_is_an_id_to_order_by`.
#
# What it DOES cover, and what changed: the schema is now built from cg.DDL, the
# committed CREATE TABLE, rather than from a synthetic all-TEXT one; the fixture no
# longer declares the unique key, so the preamble's ADD UNIQUE KEY has to create it
# or the upsert cannot work; and the whole preamble runs, translated statement by
# statement, instead of one hand-picked DELETE. Previously three of the four
# preamble steps -- the two ALTERs, the dedupe and the key -- were executed by
# nothing in any dialect.
#
# The VALUES list, which is the part under test, passes through untouched. That is
# why cg.literal doubles quotes and keeps newlines literal instead of using MySQL's
# backslash escapes: the same text means the same thing to both engines, so this
# test is not checking an escaper against its own inverse.

import sqlite3

# The MySQL column types cg.DDL uses, and what SQLite is given for each. SQLite has
# no width and no charset, which is exactly the note above.
_SQLITE_TYPE = {"INT": "INTEGER", "VARCHAR": "TEXT", "TEXT": "TEXT"}


def _sqlite_schema(table: str) -> str:
    """The committed CREATE TABLE, with only its types translated.

    Built from cg.DDL rather than from cg.TABLES[...].columns, so this fixture is
    the shape the module actually ships. The UNIQUE KEY is deliberately dropped:
    preamble step 3 is responsible for adding it, and a fixture that supplies it
    proves the upsert against a schema the untested step was assumed to produce.
    """
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


def _translate(sql: str, table: str) -> str:
    """The upsert tail, and a count so nothing else slipped through."""
    spec = cg.TABLES[table]
    assignments = ", ".join(f"`{c}`=excluded.`{c}`" for c in spec.columns)
    out = sql.replace(
        "ON DUPLICATE KEY UPDATE " + ", ".join(f"`{c}`=VALUES(`{c}`)" for c in spec.columns),
        f"ON CONFLICT(`{spec.key}`) DO UPDATE SET {assignments}",
    )
    assert "VALUES(`" not in out                      # every tail was translated
    return out


def _translate_preamble(head: str, table: str) -> list[str]:
    """Every preamble statement, as the SQLite equivalent, with none dropped.

    The preamble is three conditional blocks: add a column if it is missing,
    collapse duplicates if there is an `id`, add the unique key if it is missing.
    Each is `information_schema` + PREPARE + EXECUTE, which SQLite has no form of,
    so the CONDITION is evaluated here in Python against the fixture and the
    STATEMENT is translated and run. That is the honest split: the guard mechanism
    is MySQL's and is verified against MySQL, and what this covers is that the
    statements themselves do what the module says they do.

    Nothing is skipped silently: an unrecognised block raises.
    """
    spec = cg.TABLES[table]
    statements = []
    for block in head.split("SET @nextseek_found")[1:]:
        inner = block.split("IF(", 1)[1].split("', '", 1)[0].split(", '", 1)[1]
        if inner.startswith(f"ALTER TABLE `{spec.name}` ADD COLUMN "):
            # SQLite has no charset, which is the note at the top of this section.
            statements.append(
                re.sub(r" CHARACTER SET \w+ COLLATE \w+", "", inner) + ";")
        elif inner.startswith(f"ALTER TABLE `{spec.name}` ADD UNIQUE KEY "):
            index = f"uq_{spec.name}_{spec.key}"
            statements.append(
                f"CREATE UNIQUE INDEX `{index}` ON `{spec.name}` (`{spec.key}`);")
        elif inner.startswith(f"DELETE `a` FROM `{spec.name}` `a`"):
            statements.append(
                f"DELETE FROM `{spec.name}` WHERE `rowid` NOT IN "
                f"(SELECT MIN(`rowid`) FROM `{spec.name}` GROUP BY `{spec.key}`);")
        else:
            raise AssertionError(f"unrecognised preamble statement: {inner[:80]!r}")
    assert len(statements) == len(cg.ADDED_COLUMNS.get(table, {})) + 2, table
    return statements


def _apply(conn, table: str, rows: list[dict], *, preload=(), fresh=True) -> None:
    """Apply the whole script: the translated preamble, then the rows.

    `fresh` builds the fixture WITHOUT the columns render_update adds, so the
    preamble's ADD COLUMN has something to do, exactly as a table that predates
    them would.
    """
    sql = cg.render_update(table, rows)
    head, marker, body = sql.partition(cg.ROWS_MARKER)
    assert marker, "render_update stopped emitting the rows marker"
    if fresh:
        schema = _sqlite_schema(table)
        for column in cg.ADDED_COLUMNS.get(table, {}):
            schema = re.sub(rf"\n  `{column}` \w+,", "", schema)
        conn.executescript(schema)
        for statement in preload:
            conn.execute(statement)
        for statement in _translate_preamble(head, table):
            conn.executescript(statement)
    body = body.replace("START TRANSACTION;", "BEGIN;")   # the one dialect word
    conn.executescript(_translate(body, table))


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
        _apply(conn, table, rows, fresh=False)
        twice = _read_back(conn, table)
    assert once == twice
    assert len(twice) == 138


def test_the_preamble_adds_the_columns_and_the_key_it_is_responsible_for():
    """The steps that used to be executed by nothing, in any dialect.

    The fixture starts without the added columns and without the unique key, so if
    the preamble does not create them the upserts cannot run at all. Previously
    only step 2's DELETE was executed and the fixture declared the key itself, so
    the ALTERs, the dedupe and the key add were string-checked only -- and they are
    the first statements to touch production and the only ones that delete rows
    there.
    """
    for table, added in (("projects", "pi_names"), ("sample_types", "repository_attributes")):
        rows = _rows_for(table)
        with sqlite3.connect(":memory:") as conn:
            _apply(conn, table, rows)
            names = {row[1] for row in conn.execute(
                f"PRAGMA table_info(`{cg.TABLES[table].name}`)")}
            indexes = {row[1] for row in conn.execute(
                f"PRAGMA index_list(`{cg.TABLES[table].name}`)")}
        assert added in names, table
        assert f"uq_{cg.TABLES[table].name}_{cg.TABLES[table].key}" in indexes, table


def test_the_dedupe_keeps_the_lowest_id_when_it_runs():
    """Production's assay_context has 22 duplicated assay_name values."""
    rows = _rows_for("assays")
    name = rows[0]["assay_name"]
    with sqlite3.connect(":memory:") as conn:
        _apply(conn, "assays", rows, preload=(
            f"INSERT INTO `assay_context` (`assay_name`, `Description`) "
            f"VALUES ('{name}', 'first');",
            f"INSERT INTO `assay_context` (`assay_name`, `Description`) "
            f"VALUES ('{name}', 'second');",
        ))
        stored = _read_back(conn, "assays")
    kept = [row for row in stored if row["assay_name"] == name]
    assert len(kept) == 1
    assert kept[0]["id"] == 1                      # the lowest, not the later one
    assert len(stored) == 138


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
    assert len(stored) == 138


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


def _counts_for(*names):
    """The measured counts for exactly these names.

    Passing the whole LIVE_COUNTS map beside one row is now a refusal rather than a
    convenience: a name the counts prove answers, with no curated row, would be
    dropped from the agent's only list silently. See
    test_the_block_refuses_to_drop_an_investigation_that_answers.
    """
    return {name: LIVE_COUNTS[name] for name in names}


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
    agent through the catalog reader instead.

    The "no digit at all" rule this used to assert was vacuous AND unusable: every
    synthetic row's description was `What {name} studies.`, so nothing could ever
    fail it, and real curated descriptions already carry digits that must stay
    ("PAX3-FOXO1" in RMS-NGC, "COL2A1" in Shoulders). So the rule is the narrow one
    a count actually satisfies: four or more consecutive digits, or a comma-grouped
    number. Checked here against descriptions that DO carry digits.
    """
    rows = [_investigation(name, research_focus=f"{name} studies PAX3-FOXO1 and COL2A1.")
            for name in sorted(LIVE_COUNTS)]
    block = cg.render_capabilities_block(rows, LIVE_COUNTS)
    assert "PAX3-FOXO1" in block                  # a gene is not a count
    assert not cg._COUNT_LIKE.search(block)
    for count in LIVE_COUNTS.values():
        assert str(count) not in block and f"{count:,}" not in block


def test_a_count_in_a_curated_description_is_refused():
    """The one field an author types free text into, and the only way a count
    could still reach the block. The counts path itself is clean -- they decide
    what is emitted and are then discarded -- but nothing stopped
    `research_focus` from carrying one, and a baked count rots on the next sync."""
    import pytest

    for focus in ("Pan-cancer atlas of 1,084,754 samples across 33 cohorts.",
                  "Holds 84394 samples today."):
        row = _investigation("TCGA", research_focus=focus)
        with pytest.raises(cg.BakedCount):
            cg.render_capabilities_block([row], _counts_for("TCGA"))


def test_the_block_refuses_to_drop_an_investigation_that_answers():
    """Silently under-reporting is the mirror of the zero-sample refusal.

    Only the curated rows were iterated, and `counts` was read solely through
    `counts.get(...)`, so a name the measurement proves holds samples but that no
    row carries was simply left out -- with no refusal, no warning, and nothing on
    drift's side either, because drift only checks names already present in the
    file. The agent would never learn the investigation exists.
    """
    import pytest

    rows = [_investigation("TCGA")]
    with pytest.raises(cg.UnlistedInvestigation) as excinfo:
        cg.render_capabilities_block(rows, {"TCGA": 918519, "MetNet": 10379})
    assert "MetNet" in str(excinfo.value)
    assert "TCGA" not in str(excinfo.value)
    # A name that answers nothing is not surplus; it is the other refusal's case.
    cg.render_capabilities_block(rows, {"TCGA": 918519, "GBM": 0})


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
    block = cg.render_capabilities_block([row], _counts_for("Impactb Investigation"))
    assert "**Impactb Investigation**" in block
    assert "Impact" in block and "IMPAcTb" in block


def test_capabilities_block_sorts_by_name_and_one_bullet_per_row():
    rows = [_investigation(name) for name in ("TCGA", "CSBC", "MetNet")]
    block = cg.render_capabilities_block(rows, _counts_for("TCGA", "CSBC", "MetNet"))
    bullets = [line for line in block.splitlines() if line.startswith("- **")]
    assert len(bullets) == 3
    assert [b.split("**")[1] for b in bullets] == ["CSBC", "MetNet", "TCGA"]


def test_capabilities_block_falls_back_to_the_first_sentence_of_the_description():
    row = _investigation("TCGA", research_focus=None,
                         description="Public pan-cancer atlas. Many more sentences follow.")
    block = cg.render_capabilities_block([row], _counts_for("TCGA"))
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
    """The substitution as text: context_gen writes the block into capabilities.md,
    which the image then COPYs and both images rebuild.

    Not, as this used to say, that running it before `gen_op_surfaces` ships a
    stale route_capabilities.json: the NS projection reads only the three required
    H2 sections ("Overview", "What You Can Ask", "What the System Cannot Do"), so
    regenerating this block leaves the projection and the route-level object byte
    for byte identical. That claim is checked below rather than repeated.
    """
    block = cg.render_capabilities_block([_investigation("TCGA")], {"TCGA": 918519})
    before = (f"{cg.DRIFT_SECTION_HEADING}\n\n"
              f"{cg.CAPABILITIES_BEGIN}\nold text\n{cg.CAPABILITIES_END}\n\n---\n")
    after = cg.replace_capabilities_block(before, block)
    assert "old text" not in after
    assert "**TCGA**" in after
    assert after.count(cg.CAPABILITIES_BEGIN) == 1
    assert after.endswith("\n---\n")
    assert cg.replace_capabilities_block(after, block) == after      # idempotent


def test_the_block_refuses_to_sit_anywhere_drift_would_not_read_it():
    """Generation and the runtime backstop share a blind spot without this.

    drift keys on the exact line `## Known Projects and Investigations`
    (`_CAPABILITIES_SECTION`), and when it finds no such line
    `assistant_investigation_names` returns [] and
    `_check_assistant_investigations` then PASSES, with the detail "capabilities.md
    has no Known Projects and Investigations section". So renaming or moving the
    heading turns the backstop off silently while the generator keeps writing. The
    heading is owned by neither side, so this is where they are tied together.
    """
    import pytest
    from nextseek_api.graph_sync import drift

    block = cg.render_capabilities_block([_investigation("TCGA")], {"TCGA": 918519})
    renamed = (f"## Known Investigations\n\n"
               f"{cg.CAPABILITIES_BEGIN}\nold\n{cg.CAPABILITIES_END}\n\n---\n")
    # This is the failure it prevents, shown with drift's real parser.
    assert drift.assistant_investigation_names(
        renamed.replace(f"{cg.CAPABILITIES_BEGIN}\nold\n{cg.CAPABILITIES_END}", block)
    ) == []
    with pytest.raises(ValueError) as excinfo:
        cg.replace_capabilities_block(renamed, block)
    assert cg.DRIFT_SECTION_HEADING in str(excinfo.value)
    assert drift._CAPABILITIES_SECTION.pattern.strip("^$\\s+").startswith("##")


def test_regenerating_the_block_leaves_the_ns_projection_identical():
    """The documented ordering hazard is false, and this is the measurement.

    `replace_capabilities_block`'s note used to say that regenerating the block
    before `gen_op_surfaces --write` ships a route_capabilities.json built from the
    old list. `project_ns_capabilities` reads only REQUIRED_H2 -- "Overview", "What
    You Can Ask", "What the System Cannot Do" -- and never this section, so the
    projection cannot move. Run against the REAL committed capabilities.md, with
    the markers inserted the way 6.15c will insert them, so this is the file the
    claim is about rather than a fixture chosen to agree with it. The step that
    carries a new list to the agent is the image COPY and rebuild.
    """
    import importlib.util
    import sys

    # Loaded by path: NessieAI is not on the test lane's sys.path, and the module
    # is standard-library only, so there is nothing else to resolve.
    location = Path(cg.REPO_ROOT) / "NessieAI/cc/op_registry/ns_capabilities.py"
    spec = importlib.util.spec_from_file_location("ns_capabilities_for_test", location)
    ns_capabilities = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = ns_capabilities      # its @dataclass looks itself up
    spec.loader.exec_module(ns_capabilities)
    assert ns_capabilities.REQUIRED_H2 == (
        "Overview", "What You Can Ask", "What the System Cannot Do")

    text = _repo(Path("NessieAI/chat_nextseek/src/chat_nextseek/context/capabilities.md"))
    head, _, rest = text.partition(cg.DRIFT_SECTION_HEADING + "\n")
    assert rest, "capabilities.md no longer carries the heading drift keys on"
    body, _, tail = rest.partition("\n---\n")
    marked = (f"{head}{cg.DRIFT_SECTION_HEADING}\n{cg.CAPABILITIES_BEGIN}\n"
              f"{body}\n{cg.CAPABILITIES_END}\n---\n{tail}")

    before = ns_capabilities.project_ns_capabilities(marked)
    block = cg.render_capabilities_block([_investigation("TCGA")], {"TCGA": 918519})
    after = ns_capabilities.project_ns_capabilities(
        cg.replace_capabilities_block(marked, block))
    assert before == after
    assert before.route_level_object() == after.route_level_object()


def test_the_capabilities_mode_exists_and_refuses_today():
    """The refusal is only real if something can reach it.

    As shipped the renderer had no --emit mode and no caller anywhere in the tree,
    no CI or test guard on the committed file, and capabilities.md carries no
    CONTEXT-GEN markers -- so the five dead investigation names are still committed
    and nothing but a live rebuild could see them. The mode is what makes the
    refusal reachable; it raises today, and that is the point rather than a gap.
    """
    import json as _json
    import tempfile

    import pytest

    # The mode is declared, so `--emit capabilities` is a real entry point.
    parser_text = _repo(Path("scripts/context_gen.py"))
    assert '"update", "seed", "capabilities"' in parser_text
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        _json.dump({"TCGA": 918519}, handle)
        counts = handle.name
    # Every projects_context row is still a project, so the first refusal fires.
    with pytest.raises(cg.NoInvestigations):
        cg.emit_capabilities(counts)
    # And --counts is required: no evidence, nothing told to the agent.
    with pytest.raises(SystemExit):
        cg.main(["--emit", "capabilities"])


def test_the_committed_capabilities_file_still_names_the_dead_investigations():
    """What is true today, pinned so 6.15c's change is visible rather than assumed.

    The generator cannot repair this yet: the markers are not in the file, the
    investigation rows are not in context/projects.json, and the prose around the
    section names the dead investigations outside any block drift reads. This is
    the record that the refusal has not yet been applied, not a claim that it has.
    """
    from nextseek_api.graph_sync import drift

    text = _repo(Path("NessieAI/chat_nextseek/src/chat_nextseek/context/capabilities.md"))
    assert cg.CAPABILITIES_BEGIN not in text        # 6.15c adds the markers
    names = drift.assistant_investigation_names(text)
    assert set(DEAD_NAMES) <= set(names), names


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
    block = cg.render_capabilities_block([row], _counts_for("MIT_SRP"))
    assert "[also: SRP]" in block
    document = "## Known Projects and Investigations\n\n" + block + "\n---\n"
    assert drift.assistant_investigation_names(document) == ["MIT_SRP"]
    for name in DEAD_NAMES:
        assert name not in drift.assistant_investigation_names(document)


def test_a_title_never_carries_markdown_that_would_split_the_bold_run():
    """The regex captures `[^*]+`, so a `*` in a name would truncate it."""
    import pytest

    row = _investigation("Bad*Name", research_focus="Anything.")
    with pytest.raises(cg.UnsupportedValue):
        cg.render_capabilities_block([row], {"Bad*Name": 1})


def test_an_alternative_name_is_sanitised_exactly_like_a_title():
    """The test above passes a TITLE, so the aliases were covered in name only.

    An alias was `.strip()`ed and nothing more, and a newline in one opens a bullet
    of its own. Proven with drift's real parser: the row
    `{name: "MIT_SRP", alternative_names: ["SRP", "x]\\n- **GBM**"]}` rendered two
    bullets and `drift.assistant_investigation_names` then answered
    `['MIT_SRP', 'GBM']` -- a retired name back in the checked list, from a row
    nobody would read as declaring it.
    """
    import pytest
    from nextseek_api.graph_sync import drift

    row = _investigation("MIT_SRP", research_focus="Anything.",
                         alternative_names=["SRP", "x]\n- **GBM**"])
    with pytest.raises(cg.UnsupportedValue) as excinfo:
        cg.render_capabilities_block([row], _counts_for("MIT_SRP"))
    assert "alternative name" in str(excinfo.value)
    # And an asterisk in an alias, for the same reason as in a title.
    starred = _investigation("MIT_SRP", research_focus="Anything.",
                             alternative_names=["S*RP"])
    with pytest.raises(cg.UnsupportedValue):
        cg.render_capabilities_block([starred], _counts_for("MIT_SRP"))
    # The clean row still renders and drift still reads exactly one name.
    clean = _investigation("MIT_SRP", research_focus="Anything.",
                           alternative_names=["SRP"])
    block = cg.render_capabilities_block([clean], _counts_for("MIT_SRP"))
    document = "## Known Projects and Investigations\n\n" + block + "\n---\n"
    assert drift.assistant_investigation_names(document) == ["MIT_SRP"]

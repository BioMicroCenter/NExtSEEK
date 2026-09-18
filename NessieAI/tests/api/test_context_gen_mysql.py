"""scripts/context_gen.py against a real MySQL: the lane the SQLite round trip cannot be.

Every defect that reached a verification lens lived in what SQLite does not model:
a column width on the TARGET table, a latin1 column, a collation that folds two keys
into one, the information_schema guards, the transaction boundaries, the mysql
client's own parsing. So this module applies the generated SQL the way an operator
does -- `mysql <database> < file`, stdin, no `--default-character-set` -- to a
throwaway `mysql:8.0` container over a production-shaped pre-state
(`context_gen_prestate.sql` beside this file, plus rows derived from context/*.json).

Opt-in, because it starts a container:

    CONTEXT_GEN_MYSQL=1 python -m pytest NessieAI/tests/api/test_context_gen_mysql.py

Without that variable, or without a working `docker`, every test here skips, so the
default no-Docker lanes collect it and move on. The container runs with
`--network none --memory=768m` and is removed when the session ends, pass or fail.
Nothing here touches any other container.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import pytest

import scripts.context_gen as cg

HERE = Path(__file__).resolve().parent
PRESTATE = HERE / "context_gen_prestate.sql"
IMAGE = "mysql:8.0"
PASSWORD = "context-gen-lane"
TABLES = ("sample_types_context", "assay_context", "projects_context",
          "internal_assays", "assays_internal_assays")


def _docker_usable() -> str | None:
    """Why this lane cannot run here, or None when it can."""
    if os.environ.get("CONTEXT_GEN_MYSQL") != "1":
        return "set CONTEXT_GEN_MYSQL=1 to run the real-MySQL lane (it starts a container)"
    if shutil.which("docker") is None:
        return "no docker CLI on PATH"
    probe = subprocess.run(["docker", "info"], capture_output=True, text=True)
    if probe.returncode != 0:
        return "docker is installed but the daemon is not reachable"
    return None


_UNUSABLE = _docker_usable()
pytestmark = pytest.mark.skipif(_UNUSABLE is not None, reason=_UNUSABLE or "")


class MySQL:
    """One throwaway server, driven through `docker exec` the way an operator would."""

    def __init__(self, name: str):
        self.name = name

    def _exec(self, argv, stdin: str | None = None, timeout: int = 300):
        return subprocess.run(
            ["docker", "exec", "-i", "-e", f"MYSQL_PWD={PASSWORD}", self.name, *argv],
            input=(stdin or "").encode("utf-8"), capture_output=True, timeout=timeout,
        )

    def apply(self, sql: str, db: str, *, force: bool = False, verbose: bool = False):
        """`mysql -uroot <db> < file`, and nothing else: no charset flag, no --force.

        Returns (exit code, stdout, stderr) with the output decoded.
        """
        argv = ["mysql", "-uroot"]
        if force:
            argv.append("--force")
        if verbose:
            argv.append("-vvv")
        done = self._exec([*argv, db], stdin=sql)
        return (done.returncode, done.stdout.decode("utf-8", "replace"),
                done.stderr.decode("utf-8", "replace"))

    def must(self, sql: str, db: str = "") -> str:
        code, out, err = self.apply(sql, db)
        assert code == 0, err
        return out

    def fresh(self, db: str) -> str:
        self.must(f"DROP DATABASE IF EXISTS `{db}`; CREATE DATABASE `{db}` "
                  "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;")
        return db

    def value(self, db: str, query: str):
        """One JSON value, read over a utf8mb4 connection with no client escaping."""
        done = self._exec(["mysql", "-uroot", "--default-character-set=utf8mb4",
                           "-N", "-B", "-r", "-e", query, db])
        assert done.returncode == 0, done.stderr.decode()
        text = done.stdout.decode("utf-8").strip()
        return None if text in ("", "NULL") else json.loads(text)

    def rows(self, db: str, table: str, columns) -> list[dict]:
        pairs = ", ".join(f"'{c}', `{c}`" for c in columns)
        return self.value(db, f"SELECT JSON_ARRAYAGG(JSON_OBJECT({pairs})) FROM `{table}`") or []

    def scalar(self, db: str, query: str):
        return self.value(db, f"SELECT JSON_ARRAY(({query}))")[0]

    def data(self, db: str, like: dict | None = None) -> dict:
        """Every table's rows, over the columns each table has now or had in `like`.

        What "no row changed" compares. A refused apply may still have run its
        schema step, which only adds, widens or re-charsets columns; reading the
        rows over the columns they had before is what shows that no value moved.
        """
        state = {}
        for table in TABLES:
            if like is not None:
                columns = like[table]["columns"]
            else:
                columns = self.value(db, "SELECT JSON_ARRAYAGG(COLUMN_NAME) FROM information_schema.COLUMNS "
                                          f"WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table}'")
            rows = self.rows(db, table, columns) if columns else []
            state[table] = {"columns": columns,
                            "rows": sorted(json.dumps(r, sort_keys=True) for r in rows)}
        return state

    def snapshot(self, db: str, *, auto_increment: bool = True) -> str:
        """Every table's DDL and rows, in primary-key order.

        With `auto_increment=False` the tables' next AUTO_INCREMENT value is left out.
        """
        present = [t for t in TABLES if self.scalar(
            db, "SELECT COUNT(*) FROM information_schema.TABLES WHERE TABLE_SCHEMA = "
                f"DATABASE() AND TABLE_NAME = '{t}'")]
        done = self._exec(["mysqldump", "-uroot", "--compact", "--skip-extended-insert",
                           "--order-by-primary", "--default-character-set=utf8mb4",
                           db, *present])
        assert done.returncode == 0, done.stderr.decode()
        text = done.stdout.decode("utf-8")
        return text if auto_increment else re.sub(r" AUTO_INCREMENT=\d+", "", text)


@pytest.fixture(scope="session")
def mysql():
    name = f"context-gen-mysql-{os.getpid()}-{uuid.uuid4().hex[:6]}"
    started = subprocess.run(
        ["docker", "run", "--rm", "-d", "--name", name, "--network", "none",
         "--memory=768m", "-e", f"MYSQL_ROOT_PASSWORD={PASSWORD}", IMAGE],
        capture_output=True, text=True,
    )
    assert started.returncode == 0, started.stderr
    try:
        # The image's entrypoint runs a temporary server (port 0) to initialise,
        # then the real one; only the real one logs port 3306.
        deadline = time.time() + 180
        while time.time() < deadline:
            logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
            if re.search(r"ready for connections.*port: 3306", logs.stdout + logs.stderr):
                break
            time.sleep(1)
        else:
            pytest.fail(f"{IMAGE} did not become ready in 180 s")
        yield MySQL(name)
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


# --- the pre-state -------------------------------------------------------------


def _q(value) -> str:
    return "NULL" if value is None else "'" + str(value).replace("'", "''") + "'"


def _curated():
    assays = cg.load_source(cg.TABLES["assays"].source)
    mappings = cg.load_source(cg.TABLES_EXTRA["mappings"])
    return assays, mappings


def production_like_rows(*, id_offset: int = 0) -> str:
    """The rows the curated operations were written against, derived from context/.

    `internal_assays` holds every id the curated files name, titled as it was before
    the operations run: an assays.json row's id carries its assay_name, a renamed id
    carries its from_title, a merged id its from_title. `assays_internal_assays`
    holds the SEEK assays the maps (NULL today) and remaps (their from id) move.
    `assay_context` holds the linked rows an instance already has, each with older
    text, and for three names a lower-id duplicate with no link, which is the pair a
    dedupe must not resolve by deleting the linked row.

    `id_offset` renumbers every internal assay, which is what a stack whose
    internal_assays did not come from production looks like.
    """
    assays, mappings = _curated()
    renamed = {m["internal_assay_id"]: m for m in mappings if m["action"] == "rename_internal"}
    titles: dict[int, str] = {}
    for row in assays:
        if row.get("internal_assay_id") is not None:
            ident = row["internal_assay_id"]
            titles[ident] = renamed[ident]["from_title"] if ident in renamed else row["assay_name"]
    for m in mappings:
        if m["action"] == "merge_internal":
            titles[m["internal_assay_id"]] = m["from_title"]
    out = ["INSERT INTO `internal_assays` (`id`, `internal_assay_title`) VALUES " + ", ".join(
        f"({ident + id_offset}, {_q(title)})" for ident, title in sorted(titles.items())) + ";"]
    links = [(m["seek_assay_id"], None) for m in mappings if m["action"] == "map"]
    links += [(m["seek_assay_id"], m["from_internal_assay_id"] + id_offset)
              for m in mappings if m["action"] == "remap"]
    out.append("INSERT INTO `assays_internal_assays` (`assay_id`, `internal_assay_id`) VALUES "
               + ", ".join(f"({a}, {_q(i)})" for a, i in links) + ";")
    linked = [r for r in assays if r.get("internal_assay_id") is not None]
    duplicated = linked[:3]
    out.append("INSERT INTO `assay_context` (`assay_name`, `Description`, `internal_assay_id`) VALUES "
               + ", ".join(f"({_q(r['assay_name'])}, 'Older unlinked duplicate.', NULL)"
                           for r in duplicated) + ";")
    out.append("INSERT INTO `assay_context` (`assay_name`, `Description`, `internal_assay_id`) VALUES "
               + ", ".join(
                   f"({_q(titles[r['internal_assay_id']])}, 'Older text.', {r['internal_assay_id'] + id_offset})"
                   for r in linked) + ";")
    sample_types = cg.load_source(cg.TABLES["sample_types"].source)
    out.append("INSERT INTO `sample_types_context` (`sample_type`, `name`, `description`) VALUES "
               f"({_q(sample_types[0]['sample_type'])}, 'Older name', 'Older text.');")
    project = next(r for r in cg.rows_for("projects") if r["entity_type"] == "project")
    out.append("INSERT INTO `projects_context` (`name`, `entity_type`, `description`) VALUES "
               f"({_q(project['name'])}, 'project', 'Older text.');")
    return "\n".join(out) + "\n"


def load_prestate(mysql: MySQL, db: str, *, extra: str = "", id_offset: int = 0) -> str:
    mysql.fresh(db)
    mysql.must(PRESTATE.read_text(encoding="utf-8") + production_like_rows(id_offset=id_offset)
               + extra, db)
    return db


def update_sql(table: str = "all") -> str:
    """Exactly what `--emit update --table <table>` writes."""
    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        assert cg.main(["--emit", "update", "--table", table]) == 0
    return buffer.getvalue()


# --- the update ----------------------------------------------------------------


def test_the_update_applies_twice_and_the_second_run_changes_nothing(mysql):
    db = load_prestate(mysql, "twice")
    script = update_sql()
    code, _, err = mysql.apply(script, db)
    assert code == 0, err
    once = mysql.snapshot(db, auto_increment=False)
    code, _, err = mysql.apply(script, db)
    assert code == 0, err
    assert mysql.snapshot(db, auto_increment=False) == once


def test_every_curated_value_lands_byte_for_byte(mysql):
    db = load_prestate(mysql, "values")
    code, _, err = mysql.apply(update_sql(), db)
    assert code == 0, err
    for table in ("sample_types", "assays", "projects"):
        spec = cg.TABLES[table]
        curated = cg.rows_for(table)
        stored = {cg.key_of(table, row): row for row in mysql.rows(db, spec.name, spec.columns)}
        assert len(stored) == len(curated), table
        for row in curated:
            back = stored[cg.key_of(table, row)]
            for column in spec.columns:
                if column == "internal_assay_id":
                    continue            # the database's id, checked by the next test
                assert back[column] == cg.db_value(table, column, row.get(column)), \
                    f"{table}.{column} of {row[spec.key]!r}"


def test_every_assay_links_to_the_internal_assay_carrying_its_name(mysql):
    db = load_prestate(mysql, "links")
    code, _, err = mysql.apply(update_sql(), db)
    assert code == 0, err
    unlinked = mysql.scalar(db, "SELECT COUNT(*) FROM assay_context WHERE internal_assay_id IS NULL")
    mislinked = mysql.scalar(db, (
        "SELECT COUNT(*) FROM assay_context ac JOIN internal_assays ia "
        "ON ia.id = ac.internal_assay_id "
        "WHERE CAST(ia.internal_assay_title AS BINARY) <> CAST(ac.assay_name AS BINARY)"))
    assert (unlinked, mislinked) == (0, 0)
    _, mappings = _curated()
    for m in mappings:
        if m["action"] in ("map", "remap"):
            title = mysql.scalar(db, (
                "SELECT ia.internal_assay_title FROM assays_internal_assays x "
                f"JOIN internal_assays ia ON ia.id = x.internal_assay_id WHERE x.assay_id = {m['seek_assay_id']}"))
            assert title == m["internal_assay_title"], m
        elif m["action"] == "merge_internal":
            assert mysql.scalar(db, f"SELECT COUNT(*) FROM internal_assays WHERE id = {m['internal_assay_id']}") == 0
    # What no operation names came through untouched.
    assert mysql.scalar(db, "SELECT internal_assay_id FROM assays_internal_assays WHERE assay_id = 990001") == 9001


# --- the seed files ------------------------------------------------------------


@pytest.mark.parametrize("table", sorted(cg.SEED_FILES))
def test_each_curated_seed_loads_the_installers_way(mysql, table):
    """Piped into `mysql` with no charset flag, exactly as schema_fixups does it."""
    db = mysql.fresh(f"seed_{table}")
    sql = (Path(cg.REPO_ROOT) / cg.SEED_DIR / cg.SEED_FILES[table]).read_text(encoding="utf-8")
    code, _, err = mysql.apply(sql, db)
    assert code == 0, err
    spec = cg.TABLES[table]
    curated = cg.rows_for(table)
    stored = {cg.key_of(table, row): row for row in mysql.rows(db, spec.name, spec.columns)}
    assert len(stored) == len(curated)
    for row in curated:
        for column in spec.columns:
            assert stored[cg.key_of(table, row)][column] == cg.db_value(table, column, row.get(column)), \
                f"{table}.{column} of {row[spec.key]!r}"


# --- the target's own columns ----------------------------------------------------


def _installer_ddl(name: str) -> str:
    """The CREATE TABLE a pre-generator install ran, without any of its rows."""
    text = (Path(cg.REPO_ROOT) / "startup/seed/sql" / name).read_text(encoding="utf-8")
    return text.split("\nINSERT INTO ", 1)[0].rstrip() + "\n"


def test_a_table_the_old_installer_created_is_widened_rather_than_refused(mysql):
    """The blocker. The pre-generator seed declares `pi VARCHAR(255)`, a curated pi is
    longer, and every install since that seed was added created exactly that table.
    The update used to widen nothing and measure only against its own DDL, so it
    aborted at ERROR 1406 on every run. The old assay widths get the same treatment."""
    db = mysql.fresh("oldinstall")
    prestate = PRESTATE.read_text(encoding="utf-8")
    for table, name in (("assay_context", "assay_context.sql"),
                        ("projects_context", "projects_context.sql")):
        start = prestate.index(f"CREATE TABLE `{table}`")
        prestate = prestate[:start] + _installer_ddl(name) + prestate[prestate.index(";", start) + 1:]
    mysql.must(prestate + production_like_rows(), db)
    assert mysql.scalar(db, "SELECT DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = "
                            "DATABASE() AND TABLE_NAME = 'projects_context' AND COLUMN_NAME = 'pi'") == "varchar"
    code, _, err = mysql.apply(update_sql(), db)
    assert code == 0, err
    assert mysql.scalar(db, "SELECT COUNT(*) FROM projects_context") == len(cg.rows_for("projects"))
    widths = mysql.value(db, (
        "SELECT JSON_OBJECTAGG(CONCAT(TABLE_NAME, '.', COLUMN_NAME), COLUMN_TYPE) "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND COLUMN_NAME IN "
        "('pi', 'Parent_Clade_Type', 'AssaySheet_Link')"))
    assert widths == {"projects_context.pi": "text",
                      "assay_context.Parent_Clade_Type": "varchar(128)",
                      "assay_context.AssaySheet_Link": "varchar(512)"}


@pytest.mark.parametrize("sql_mode", ["STRICT_TRANS_TABLES", "NO_ENGINE_SUBSTITUTION"])
def test_a_character_latin1_cannot_hold_reaches_a_latin1_table_intact(mysql, sql_mode):
    """The live projects_context is latin1. Strict mode refused a gamma with 1366;
    a non-strict server stored it as `?` and exited 0. Either way the curated text
    did not arrive, so the update moves every written text column to utf8mb4."""
    db = load_prestate(mysql, "latin1")
    rows = [{"name": "Synthetic Gamma Study", "entity_type": "project",
             "description": "IFN-γ response, 4-byte \U0001F9EA too."}]
    script = f"SET SESSION sql_mode = '{sql_mode}';\n" + cg.render_update("projects", rows)
    code, _, err = mysql.apply(script, db)
    assert code == 0, err
    stored = mysql.rows(db, "projects_context", ["name", "description"])
    assert stored == [{"name": "Synthetic Gamma Study",
                       "description": "IFN-γ response, 4-byte \U0001F9EA too."}]


def test_a_value_the_target_cannot_take_is_refused_before_any_row_changes(mysql):
    """What the widening cannot fix: a curated NULL for a column the target declares
    NOT NULL. The generator's own DDL allows it, so only the target can say no, and it
    has to say so before the delete runs, not halfway through. The live table's two
    NOT NULL columns are now the key, which the generator refuses a NULL for itself,
    so the target here declares a third one."""
    extra = ("UPDATE `projects_context` SET `research_focus` = 'x';\n"
             "ALTER TABLE `projects_context` MODIFY `research_focus` text NOT NULL;\n")
    db = load_prestate(mysql, "notnull", extra=extra)
    before = mysql.data(db)
    rows = [dict(row) for row in cg.rows_for("projects")]
    rows[0]["research_focus"] = None
    code, out, err = mysql.apply(cg.render_update("projects", rows), db)
    assert code != 0
    assert "context_gen REFUSED" in err and "research_focus" in out + err
    assert mysql.data(db, like=before) == before


# --- one transaction, and a second run that changes nothing ----------------------

_CHANGED = re.compile(r"Query OK, [1-9]\d* rows? affected|Changed: [1-9]")


def _rows_changed(verbose_output: str) -> list[str]:
    """The `mysql -vvv` report lines of every statement that changed a row."""
    return [line for line in verbose_output.splitlines() if _CHANGED.search(line)]


def test_a_failure_anywhere_in_the_rows_leaves_every_table_as_it_was(mysql):
    """Each section used to commit on its own, and the dedupe ran before any of them:
    a failure in the projects section left the sample types and assays written, and
    the duplicate assay rows carrying the internal assay link deleted. Now one
    transaction holds every row change, so a failure anywhere changes no row."""
    db = load_prestate(mysql, "midfail")
    before = mysql.data(db)
    script = update_sql()
    for point in ("-- ---- projects_context ----", cg.CHECKS_MARKER):
        broken = script.replace(point, "SELECT * FROM `nextseek_no_such_table`;\n" + point, 1)
        code, _, err = mysql.apply(broken, db)
        assert code != 0 and "nextseek_no_such_table" in err, point
        assert mysql.data(db, like=before) == before, point


def test_the_second_run_changes_no_row_and_consumes_no_id(mysql):
    """Not only the same end state: no statement writes anything. The old script
    reset fourteen assay links to NULL on every run and restored them three
    sections later, and each upsert took an AUTO_INCREMENT value it never used."""
    db = load_prestate(mysql, "noop")
    script = update_sql()
    code, _, err = mysql.apply(script, db)
    assert code == 0, err
    once = mysql.snapshot(db)
    code, out, err = mysql.apply(script, db, verbose=True)
    assert code == 0, err
    assert _rows_changed(out) == []
    assert mysql.snapshot(db) == once


def test_the_assays_section_alone_after_a_full_apply_changes_nothing(mysql):
    """`--table assays` on its own used to NULL every created assay's link, silently,
    because only the mappings section filled them in. Each section now links by
    title itself."""
    db = load_prestate(mysql, "assaysalone")
    code, _, err = mysql.apply(update_sql(), db)
    assert code == 0, err
    code, out, err = mysql.apply(update_sql("assays"), db, verbose=True)
    assert code == 0, err
    assert _rows_changed(out) == []
    assert mysql.scalar(db, "SELECT COUNT(*) FROM assay_context WHERE internal_assay_id IS NULL") == 0


def test_the_dedupe_keeps_the_lowest_id_and_loses_no_value(mysql):
    """Duplicated names collapse to one row that holds the curated values and the link."""
    db = load_prestate(mysql, "dedupe")
    duplicated = [r["assay_name"] for r in cg.rows_for("assays") if r.get("internal_assay_id")][:3]
    lowest = {name: mysql.scalar(db, f"SELECT MIN(id) FROM assay_context WHERE assay_name = {_q(name)}")
              for name in duplicated}
    code, _, err = mysql.apply(update_sql(), db)
    assert code == 0, err
    for name in duplicated:
        kept = mysql.value(db, "SELECT JSON_ARRAYAGG(JSON_OBJECT('id', id, 'link', internal_assay_id)) "
                               f"FROM assay_context WHERE assay_name = {_q(name)}")
        assert len(kept) == 1 and kept[0]["id"] == lowest[name] and kept[0]["link"] is not None, name


def test_the_unique_keys_are_added_once_and_never_beside_a_primary_key(mysql):
    db = load_prestate(mysql, "keys")
    code, _, err = mysql.apply(update_sql(), db)
    assert code == 0, err
    assert _unique_keys(mysql, db) == {
        "sample_types_context.PRIMARY": "id",
        "sample_types_context.uq_sample_types_context_sample_type": "sample_type",
        "assay_context.PRIMARY": "id",
        "assay_context.uq_assay_context_assay_name": "assay_name",
        "projects_context.PRIMARY": "name,entity_type",
    }


def _unique_keys(mysql, db, tables=("sample_types_context", "assay_context", "projects_context")):
    """Every unique index of `tables`, as `table.index` -> its columns in order."""
    names = ", ".join(f"'{t}'" for t in tables)
    return mysql.value(db, (
        "SELECT JSON_OBJECTAGG(`k`, `cols`) FROM (SELECT CONCAT(TABLE_NAME, '.', INDEX_NAME) AS `k`, "
        "GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX SEPARATOR ',') AS `cols` "
        "FROM information_schema.STATISTICS WHERE TABLE_SCHEMA = DATABASE() AND NON_UNIQUE = 0 "
        f"AND TABLE_NAME IN ({names}) GROUP BY TABLE_NAME, INDEX_NAME) `u`"))


# --- the (name, entity_type) key, on every shape the table has had --------------------
#
# The real CSBC and MetNet investigations share their titles with the project rows, so
# the key becomes the pair (spec 2026-09-18, section 9.1). Three shapes exist: the live
# table (no id, PRIMARY KEY (name)), the old held seed's (an id and a unique key on name)
# and the new DDL's (an id and a unique key on the pair). Each must end keyed on the pair,
# hold a project and an investigation of one name, and change nothing on a second run.

def _with_a_shared_name() -> list[dict]:
    """The curated project rows plus an invented investigation named like the first."""
    rows = [dict(row) for row in cg.rows_for("projects")]
    first = next(row for row in rows if row["entity_type"] == "project")
    rows.append({"name": first["name"], "entity_type": "investigation",
                 "project_id": first["project_id"], "parent_project": first["name"],
                 "alternative_names": [], "research_focus": "A synthetic investigation."})
    return rows


def _pairs(mysql, db) -> list:
    return sorted((r["name"], r["entity_type"])
                  for r in mysql.rows(db, "projects_context", ["name", "entity_type"]))


def _twice(mysql, db, script) -> None:
    code, _, err = mysql.apply(script, db)
    assert code == 0, err
    once = mysql.snapshot(db)
    code, out, err = mysql.apply(script, db, verbose=True)
    assert code == 0, err
    assert _rows_changed(out) == []
    assert mysql.snapshot(db) == once


def test_the_live_shape_is_rekeyed_on_the_pair_and_takes_a_shared_name(mysql):
    db = load_prestate(mysql, "livepair")
    assert _unique_keys(mysql, db, ("projects_context",)) == {"projects_context.PRIMARY": "name"}
    rows = _with_a_shared_name()
    _twice(mysql, db, cg.render_update("projects", rows))
    assert _unique_keys(mysql, db, ("projects_context",)) == {
        "projects_context.PRIMARY": "name,entity_type"}
    assert _pairs(mysql, db) == sorted(cg.key_of("projects", r) for r in rows)


def test_the_old_seed_shape_loses_its_name_only_key_and_takes_a_shared_name(mysql):
    db = mysql.fresh("oldseedpair")
    mysql.must(cg.DDL["projects"] + (
        "ALTER TABLE `projects_context` DROP INDEX `uq_projects_context_name_type`, "
        "ADD UNIQUE KEY `uq_projects_context_name` (`name`), "
        "MODIFY `entity_type` VARCHAR(64) NULL;\n"), db)
    rows = _with_a_shared_name()
    _twice(mysql, db, cg.render_update("projects", rows))
    assert _unique_keys(mysql, db, ("projects_context",)) == {
        "projects_context.PRIMARY": "id",
        "projects_context.uq_projects_context_name_type": "name,entity_type"}
    assert _pairs(mysql, db) == sorted(cg.key_of("projects", r) for r in rows)


def test_the_new_seed_shape_takes_the_update_without_a_second_key(mysql):
    db = mysql.fresh("newseedpair")
    rows = _with_a_shared_name()
    mysql.must(cg.render_seed("projects", rows), db)
    _twice(mysql, db, cg.render_update("projects", rows))
    assert _unique_keys(mysql, db, ("projects_context",)) == {
        "projects_context.PRIMARY": "id",
        "projects_context.uq_projects_context_name_type": "name,entity_type"}
    assert _pairs(mysql, db) == sorted(cg.key_of("projects", r) for r in rows)


# --- drift is refused, loudly, and nothing is committed ----------------------------


def _refused(mysql, db: str, script: str, before: dict, *, force: bool = False) -> str:
    code, out, err = mysql.apply(script, db, force=force)
    if not force:
        assert code != 0, out
    assert "context_gen REFUSED" in err, err
    assert mysql.data(db, like=before) == before
    return out + err


def _a_remap_target_no_operation_creates() -> str:
    _, mappings = _curated()
    created = {m["internal_assay_title"] for m in mappings if m["action"] in ("create_internal", "rename_internal")}
    return next(m["internal_assay_title"] for m in mappings
                if m["action"] == "remap" and m["internal_assay_title"] not in created)


def test_a_target_retitled_upstream_is_refused_and_rolled_back(mysql):
    """The guards made a moved target a no-op, and the apply then exited 0 with the
    SEEK assay left where it was: a partial apply nobody would see."""
    title = _a_remap_target_no_operation_creates()
    db = load_prestate(mysql, "drift", extra=(
        "UPDATE internal_assays SET internal_assay_title = CONCAT(internal_assay_title, ' (retitled)') "
        f"WHERE internal_assay_title = {_q(title)};\n"))
    before = mysql.data(db)
    text = _refused(mysql, db, update_sql(), before)
    assert "SEEK assays not on their curated internal assay" in text


def test_a_merge_a_new_seek_assay_still_points_at_is_refused(mysql):
    """A SEEK assay linked to a merged internal assay after the curation blocks the
    merge, correctly -- and used to do so with exit 0."""
    _, mappings = _curated()
    merged = next(m["internal_assay_id"] for m in mappings if m["action"] == "merge_internal")
    db = load_prestate(mysql, "blocked", extra=(
        f"INSERT INTO assays_internal_assays (assay_id, internal_assay_id) VALUES (990002, {merged});\n"))
    before = mysql.data(db)
    text = _refused(mysql, db, update_sql(), before)
    assert "merged internal assays still present" in text


def test_a_stack_numbered_differently_is_refused_rather_than_mislinked(mysql):
    """Production's internal assay ids, written as literals, pointed rows of a stack
    whose internal_assays came from elsewhere at the wrong assays, with exit 0."""
    db = load_prestate(mysql, "renumbered", id_offset=1000)
    before = mysql.data(db)
    _refused(mysql, db, update_sql(), before)


def test_force_cannot_commit_a_partial_apply(mysql):
    """`mysql --force` runs on past a failed statement and used to reach COMMIT. The
    commit is now conditional on the checks, so a skipped row rolls everything back."""
    db = load_prestate(mysql, "force")
    before = mysql.data(db)
    first = next(r for r in cg.rows_for("projects") if r["entity_type"] == "project")["name"]
    script = update_sql()
    where = f"`name` = {cg.literal(first)} AND `entity_type` = 'project'"
    statement = f"UPDATE `projects_context` SET `name` = {cg.literal(first)},"
    start = script.index(statement)
    end = script.index("\n", script.index(f"WHERE {where};", start))
    broken = script[:start] + "UPDATE `projects_context` SET `no_such_column` = 1;" + script[end:]
    broken = broken.replace(
        f"SELECT 1 FROM `projects_context` WHERE {where});",
        "SELECT 1 FROM `projects_context`);", 1)
    _refused(mysql, db, broken, before, force=True)


# --- what the key collation and the seed parser do ---------------------------------


def test_the_key_collation_equates_two_emoji_as_fold_key_says(mysql):
    db = mysql.fresh("collation")
    equal = mysql.scalar(db, "SELECT _utf8mb4 X'F09FA7AA' = _utf8mb4 X'F09FA7AC' "
                             "COLLATE utf8mb4_unicode_ci")
    assert equal == 1
    assert cg.fold_key("\U0001F9EA") == cg.fold_key("\U0001F9EC")


@pytest.mark.parametrize("table", sorted(cg.SEED_FILES))
def test_each_curated_seed_loads_identically_under_no_backslash_escapes(mysql, table):
    """A server running NO_BACKSLASH_ESCAPES read the old seeds' backslash-quote as the
    end of a string. The mode is switched on for the session first, which is what
    such a server does for every session, and the mysql client follows it."""
    db = mysql.fresh(f"nbe_{table}")
    sql = (Path(cg.REPO_ROOT) / cg.SEED_DIR / cg.SEED_FILES[table]).read_text(encoding="utf-8")
    code, _, err = mysql.apply(
        "SET SESSION sql_mode = CONCAT(@@SESSION.sql_mode, ',NO_BACKSLASH_ESCAPES');\n" + sql, db)
    assert code == 0, err
    spec = cg.TABLES[table]
    stored = {cg.key_of(table, row): row for row in mysql.rows(db, spec.name, spec.columns)}
    curated = cg.rows_for(table)
    assert len(stored) == len(curated)
    for row in curated:
        for column in spec.columns:
            assert stored[cg.key_of(table, row)][column] == cg.db_value(table, column, row.get(column))

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

    def snapshot(self, db: str, *, auto_increment: bool = True) -> str:
        """Every table's DDL and rows, in primary-key order.

        With `auto_increment=False` the tables' next AUTO_INCREMENT value is left out,
        which is the one thing a re-run may consume without changing any row.
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
    projects = cg.load_source(cg.TABLES["projects"].source)
    out.append("INSERT INTO `projects_context` (`name`, `entity_type`, `description`) VALUES "
               f"({_q(projects[0]['name'])}, 'project', 'Older text.');")
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
        stored = {row[spec.key]: row for row in mysql.rows(db, spec.name, spec.columns)}
        assert len(stored) == len(curated), table
        for row in curated:
            back = stored[row[spec.key]]
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
    stored = {row[spec.key]: row for row in mysql.rows(db, spec.name, spec.columns)}
    assert len(stored) == len(curated)
    for row in curated:
        for column in spec.columns:
            assert stored[row[spec.key]][column] == cg.db_value(table, column, row.get(column)), \
                f"{table}.{column} of {row[spec.key]!r}"

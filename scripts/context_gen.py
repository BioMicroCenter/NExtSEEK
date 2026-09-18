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
    python scripts/context_gen.py --emit seed --table all
    python scripts/context_gen.py --emit capabilities --counts /tmp/investigations.json

`--emit update` writes one re-runnable script (stdout when `--out` is left off),
`--emit seed` rewrites the held `startup/seed/sql/*.curated.sql` seeds in place,
`--out` naming the directory rather than a file, and `--emit capabilities` rewrites
the generated investigation block in `capabilities.md` from the live sample counts.
No install step reads the `.curated.sql` files until the curated content is signed
off; `scripts/README.md` group C says what switching them on takes.

`--emit capabilities` refuses today, and that is the point rather than a gap: every
`projects_context` row is still a project, and `capabilities.md` carries no
CONTEXT-GEN markers. Task 6.15c adds the investigation rows and the markers.

Nothing here connects to a database. It reads committed JSON and writes SQL text;
the operator applies it. `scripts/README.md` group C is the reference.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# Every artifact this module writes opens with this. The installer's apply path
# pipes a seed file into `mysql` with no `--default-character-set`
# (`startup/steps/schema_fixups.py::_create_table`), and the db container has no
# UTF-8 locale, so the client default `auto` resolves to latin1: measured on the
# real compose db container, `SELECT @@character_set_client` answers `latin1` with
# `LANG` unset. Applying assay_context.sql that way stored the gamma of
# "Antibody-Dependent NK Cell Activation Assay" as C3 8E C2 B3 where the file holds
# CE B3 -- latin1 -> utf8mb4 double encoding, i.e. the text reads as "Î³". Pinning
# the connection in the file fixes every apply path at once, including the hand
# apply `scripts/README.md` describes, which names no charset either.
CHARSET_PREAMBLE = "SET NAMES utf8mb4;"


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


# --- the three context tables ------------------------------------------------
#
# Column spellings, and where each one is pinned:
#
#   sample_types  the Django model seek/models/nextseek.py::Sample_types_context,
#                 whose fields are the live columns (`tags` carries the one
#                 db_column override, capital-T `Tags`)
#   assays        the CREATE TABLE in startup/seed/sql/assay_context.sql, whose
#                 spellings are map_assay's first choice for each field, which is
#                 what production answered with on the 2026-09-11 pull
#   projects      the CREATE TABLE in startup/seed/sql/projects_context.sql
#
# NessieAI/tests/api/test_context_gen.py re-derives all three from those files
# and fails if this module drifts from them.

# Columns newer than every one of those fixtures, so a table created before this
# change does not have them. render_update emits an idempotent ALTER for each.
# Both are TEXT, not JSON: `projects_context` already stores `alternative_names`
# and `key_data_types` as JSON text in TEXT columns because `map_project` reads
# them with json.loads and a '|' split fallback, and these two are read the same
# way. A JSON column would be stricter than every sibling for no gain.
#
# Each carries its charset explicitly rather than inheriting the table's. `ADD
# COLUMN <c> TEXT NULL` takes the table default, and the live `dmac.projects_context`
# is `DEFAULT CHARSET=latin1` (measured 2026-09-17: 10 of its 12 columns are
# latin1), while production's seek_production is an aged database of 558 utf8mb3
# columns and 13 latin1
# (`nextseek_api/attributes/tests/test_repository_collation_charset.py`). So the
# bare form creates a column narrower than the DDL declares, on the instances that
# matter and nowhere the test lane can see, and a four-byte character then answers
# `ERROR 1366 Incorrect string value` there and nowhere else. `json_text` passes
# one through raw (`ensure_ascii=False`) and `literal` accepts it.
ADDED_COLUMNS = {
    "sample_types": {
        # Curated in context/sample_types.json; see context/README.md
        # "repository_attributes". Absent from the 2026-09-11 production pull.
        "repository_attributes": "TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL",
    },
    "projects": {
        # Generated by parse_pi, not curated: the free-text `pi` field parsed
        # into names the person-name rule can match without an LLM (plan D7).
        "pi_names": "TEXT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci NULL",
    },
}


class UnknownColumn(KeyError):
    """A curated row carries a key that is not a column of its table."""


class MissingKey(ValueError):
    """A curated row has no value for the column rows are keyed on."""


class ValueTooLong(ValueError):
    """A curated value is longer than the column its table declares for it."""


class Table:
    """One context table: where its rows come from and how they are written."""

    def __init__(self, name, source, key, columns, json_columns=(), int_columns=()):
        self.name = name
        self.source = Path(source)
        self.key = key
        self.columns = tuple(columns)
        self.json_columns = frozenset(json_columns)
        self.int_columns = frozenset(int_columns)


TABLES = {
    "sample_types": Table(
        name="sample_types_context",
        source="context/sample_types.json",
        key="sample_type",
        columns=(
            "sample_type", "sampletype_id", "name", "clade", "description", "Tags",
            "required_metadata", "standard_metadata", "possible_metadata_fields",
            "parent_sampletypes", "child_sampletypes",
            "associated_assay_parents", "associated_assay_children",
            "repository_attributes", "sampletype_file_link",
        ),
        json_columns=("repository_attributes",),
        int_columns=("sampletype_id",),
    ),
    "assays": Table(
        name="assay_context",
        source="context/assays.json",
        key="assay_name",
        columns=(
            "assay_name", "Description", "Tags", "Alternative_Assay_Names",
            "Required_Parent_Sample_Types", "Optional_Parent_Sample_Types",
            "Children_Sample_Types", "Parent_Clade_Type", "Child_Clade_Type",
            "AssaySheet_Link", "AssociatedRepository", "Critical_Attributes",
            "Protocols_Phrases", "Protocols_UIDs", "internal_assay_id",
        ),
        int_columns=("internal_assay_id",),
    ),
    "projects": Table(
        name="projects_context",
        source="context/projects.json",
        key="name",
        columns=(
            "name", "alternative_names", "entity_type", "project_id",
            "parent_project", "pi", "pi_names", "research_focus", "key_data_types",
            "description", "nih_reporter_link", "fairdomhub_published_link", "tags",
        ),
        json_columns=("alternative_names", "key_data_types", "pi_names"),
        int_columns=("project_id",),
    ),
}

COLUMNS = {name: table.columns for name, table in TABLES.items()}


def check_columns(table: str, rows: list[dict]) -> None:
    """Refuse rows whose keys are not columns, or that lack the natural key.

    A typo in a curated file has to fail here. The alternative is a column
    silently dropped at write time, which looks like a successful write.
    """
    spec = TABLES[table]
    known = set(spec.columns)
    for index, row in enumerate(rows):
        unknown = sorted(set(row) - known)
        if unknown:
            raise UnknownColumn(
                f"{spec.source} row {index}: {', '.join(unknown)} "
                f"{'is not a column' if len(unknown) == 1 else 'are not columns'} "
                f"of {table} ({spec.name})"
            )
        if not row.get(spec.key):
            raise MissingKey(f"{spec.source} row {index}: no {spec.key}, which rows are keyed on")


# --- column widths ----------------------------------------------------------
#
# MySQL 8 under its default strict mode refuses a value longer than its column and
# the client stops at the first error, so one over-long value aborts the artifact
# partway. A curated `pi` is longer than VARCHAR(255): the seed file that declared
# that width died at ERROR 1406 after four rows, and SQLite, the old round trip's
# engine, ignores a VARCHAR width outright, so nothing in the test lane saw it.
#
# Two checks, because there are two tables to be wrong about. `check_widths` below
# measures every value against the generator's own DDL before anything is written,
# which is what the seed files load into. But `--emit update` writes into a table
# that already exists, whose widths are whatever created it: every install since
# the pre-generator projects seed has `pi VARCHAR(255)`. So the update also widens
# any narrower target column and then measures the curated values against the
# target itself, at apply time (`_pin_columns`, `_target_problems`).

# `(?!\w)` rather than `\b`: a VARCHAR width ends in `)`, and `\b` after a
# non-word character never matches, so `\b` here silently found no VARCHAR at all.
_DDL_TYPE = re.compile(r"^\s{2}`?(\w+)`?\s+(VARCHAR\((\d+)\)|TEXT|INT)(?!\w)", re.M | re.I)

# MySQL's TEXT holds 65,535 BYTES, and these columns are utf8mb4 where one
# character is up to four of them. VARCHAR(n) holds n characters.
TEXT_BYTES = 65535


def declared_limits(table: str) -> dict[str, tuple[str, int]]:
    """Each column's length limit, read off its own CREATE TABLE.

    Read from `DDL[table]` rather than restated here, so widening a column cannot
    leave this measuring the old width. Integer columns have no length limit and
    are left out.
    """
    limits: dict[str, tuple[str, int]] = {}
    for column, declared, width in _DDL_TYPE.findall(DDL[table]):
        if column == "id" or declared.upper() == "INT":
            continue
        limits[column] = ("characters", int(width)) if width else ("bytes", TEXT_BYTES)
    return limits


def declared_types(table: str) -> dict[str, str]:
    """Each text column's type as `DDL[table]` declares it: `VARCHAR(n)` or `TEXT`."""
    types: dict[str, str] = {}
    for column, declared, width in _DDL_TYPE.findall(DDL[table]):
        if column == "id" or declared.upper() == "INT":
            continue
        types[column] = f"VARCHAR({width})" if width else "TEXT"
    return types


def check_widths(table: str, rows: list[dict]) -> None:
    """Refuse a value the table's own DDL is too narrow to hold.

    This is the check whose absence let a 276 character `pi` into a VARCHAR(255)
    column and through a green 58-test lane; see the note above. It measures the
    value `db_value` produces, which is what actually reaches the column, not the
    curated one.
    """
    spec = TABLES[table]
    limits = declared_limits(table)
    for index, row in enumerate(rows):
        for column in spec.columns:
            limit = limits.get(column)
            if limit is None:
                continue
            value = db_value(table, column, row.get(column))
            if value is None or isinstance(value, int):
                continue
            text = str(value)
            unit, allowed = limit
            size = len(text) if unit == "characters" else len(text.encode("utf-8"))
            if size > allowed:
                raise ValueTooLong(
                    f"{spec.source} row {index} ({row.get(spec.key)!r}): {column} is "
                    f"{size} {unit} and {spec.name}.{column} holds {allowed}. MySQL "
                    f"refuses it with error 1406 and the client stops there, so the "
                    f"artifact would load partway and stop. Widen the column in "
                    f"DDL[{table!r}] or shorten the value."
                )


# --- the target table, measured at apply time ---------------------------------
#
# Everything below reads information_schema on the instance the script is applied
# to, because the generator cannot know that table: its widths, its charsets and
# which of its columns refuse a NULL all depend on what created it.

_CHAR_TYPES = "('char', 'varchar')"
_TEXT_TYPES = "('tinytext', 'text', 'mediumtext', 'longtext')"


def _too_narrow(declared: str) -> str:
    """The information_schema.COLUMNS test for a column narrower than `declared`."""
    if declared == "TEXT":
        return (f"(DATA_TYPE IN {_CHAR_TYPES} OR DATA_TYPE = 'tinytext') "
                f"AND CHARACTER_OCTET_LENGTH < {TEXT_BYTES}")
    width = int(declared[len("VARCHAR("):-1])
    return f"DATA_TYPE IN {_CHAR_TYPES} AND CHARACTER_MAXIMUM_LENGTH < {width}"


_NOT_UTF8MB4 = "CHARACTER_SET_NAME IS NOT NULL AND CHARACTER_SET_NAME <> 'utf8mb4'"


def _pin_columns(table: str) -> str:
    """One ALTER that widens and re-charsets the target's written text columns.

    A column narrower than the DDL declares is widened to the declared type, and a
    text column in any charset but utf8mb4 is converted, keeping its type. Both
    were measured failures, not hypotheses. `pi VARCHAR(255)` is what the
    pre-generator projects seed created, and a curated `pi` does not fit: ERROR
    1406 on every run. The live `projects_context` is latin1: a gamma answered
    ERROR 1366 under strict mode and was stored as `?` with exit 0 without it.

    NULL-ability is kept as the target has it, and so is a utf8mb4 column's own
    collation (the live JSON columns are utf8mb4_bin). The statement is assembled
    from information_schema by the server, so it only names columns that exist and
    only when one needs it; otherwise it is `DO 0`.
    """
    spec = TABLES[table]
    types = declared_types(table)
    cases = " ".join(
        f"WHEN COLUMN_NAME = '{column}' AND {_too_narrow(declared)} THEN '{declared}'"
        for column, declared in types.items()
    )
    wanted = " OR ".join(
        f"(COLUMN_NAME = '{column}' AND (({_too_narrow(declared)}) OR {_NOT_UTF8MB4}))"
        for column, declared in types.items()
    )
    return (
        "SET @nextseek_stmt := (SELECT CONCAT("
        f"'ALTER TABLE `{spec.name}` ', GROUP_CONCAT(CONCAT("
        "'MODIFY COLUMN `', COLUMN_NAME, '` ', "
        f"CASE {cases} ELSE COLUMN_TYPE END, "
        "' CHARACTER SET utf8mb4 COLLATE ', "
        "IF(CHARACTER_SET_NAME = 'utf8mb4', COLLATION_NAME, 'utf8mb4_unicode_ci'), "
        "IF(IS_NULLABLE = 'NO', ' NOT NULL', ' NULL')) "
        "ORDER BY ORDINAL_POSITION SEPARATOR ', '))\n"
        f"  FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{spec.name}'\n"
        f"  AND ({wanted}));\n"
        "SET @nextseek_stmt := COALESCE(@nextseek_stmt, 'DO 0');\n"
        "PREPARE nextseek_stmt FROM @nextseek_stmt;\n"
        "EXECUTE nextseek_stmt;\n"
        "DEALLOCATE PREPARE nextseek_stmt;\n"
    )


def _target_problems(table: str, rows: list[dict]) -> str:
    """A SQL expression naming each target column that cannot take what is written.

    Run after `_pin_columns`, so on a healthy instance it names nothing. What it is
    for: a width the widening could not reach, a charset it could not convert, and
    a column the target declares NOT NULL where a curated row carries NULL -- which
    the generator's DDL allows and the live `projects_context.entity_type` refuses
    with ERROR 1048 halfway through the rows. NULL when there is no problem.
    """
    spec = TABLES[table]
    types = declared_types(table)
    clauses = []
    for column in spec.columns:
        values = [db_value(table, column, row.get(column)) for row in rows]
        texts = [str(v) for v in values if v is not None and not isinstance(v, int)]
        tests = []
        if column in types:
            tests.append(_NOT_UTF8MB4)
            if texts:
                chars = max(len(t) for t in texts)
                octets = max(len(t.encode("utf-8")) for t in texts)
                tests.append(f"(DATA_TYPE IN {_CHAR_TYPES} AND CHARACTER_MAXIMUM_LENGTH < {chars})")
                tests.append(f"(DATA_TYPE IN {_TEXT_TYPES} AND CHARACTER_OCTET_LENGTH < {octets})")
        if any(v is None for v in values):
            tests.append("(IS_NULLABLE = 'NO' AND COLUMN_DEFAULT IS NULL)")
        if tests:
            clauses.append(f"(COLUMN_NAME = '{column}' AND ({' OR '.join(tests)}))")
    return (
        "(SELECT GROUP_CONCAT(CONCAT("
        f"'{spec.name}.', COLUMN_NAME, ' is ', COLUMN_TYPE, "
        "IF(IS_NULLABLE = 'NO', ' NOT NULL', ''), IFNULL(CONCAT(' ', CHARACTER_SET_NAME), ''), "
        "' and cannot take what the curated rows write') SEPARATOR ' | ')\n"
        f"  FROM information_schema.COLUMNS WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{spec.name}'\n"
        f"  AND ({' OR '.join(clauses) or 'FALSE'}))"
    )


def _refuse(when: str) -> str:
    """Stop the script, loudly, when `@nextseek_problems` names anything.

    SIGNAL is the natural statement and cannot be used here: it is legal only in a
    stored program, and `PREPARE` refuses it (ERROR 1295, measured on mysql:8.0.46).
    Setting `sql_mode` to a value that is not a mode is an ordinary statement that
    fails with the value in its message -- `ERROR 1231 ... can't be set to the value
    of 'context_gen REFUSED ...'` -- and when there is no problem it sets the mode
    to itself. A comma would end the quoted value early, because sql_mode is a list,
    so commas are replaced. The server cuts that message at about 200 characters,
    so the full list is printed first.
    """
    return (
        "SELECT @nextseek_problems AS context_gen_problems FROM DUAL WHERE @nextseek_problems <> '';\n"
        "SET SESSION sql_mode = IF(@nextseek_problems = '', CONVERT(@@SESSION.sql_mode USING utf8mb4), "
        f"CONCAT('context_gen REFUSED {when}: ', REPLACE(@nextseek_problems, ',', ';')));\n"
    )


# --- the PI field ------------------------------------------------------------

# Anything that means "no PI recorded". The database and the curated files
# disagree about how to spell an absent value.
_NO_PI = {"", "none", "null", "n/a", "na", "-", "tbd", "unknown"}


def _split_outside_parens(value: str, separator: str = ";") -> list[str]:
    """Split on `separator`, ignoring separators inside parentheses.

    Required, not defensive: one curated row is
    `Griffith, Linda G. (MIT, PI; Scientific Director, Center for Gynepathology
    Research); Goods, Brittany A. (...)`. Splitting on every semicolon invents a
    PI called "Scientific Director, Center for Gynepathology Research".
    """
    parts, current, depth = [], [], 0
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == separator and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    if depth:
        # An unclosed "(" swallows every later separator, so the PIs after it
        # simply vanish: `Kamm, Roger D. (MIT, contact PI; Shenoy, Vivek B. (UPenn,
        # co-PI)` parsed to Kamm alone, with no exception and a successful-looking
        # INSERT. This module refuses rather than guesses everywhere else a value
        # is ambiguous (`literal` on a backslash), and the output is SQL bound for
        # production, so it refuses here too.
        raise UnsupportedValue(
            f"{value[:80]!r} has {depth} unclosed parenthes{'is' if depth == 1 else 'es'}; "
            "every PI after it would be silently dropped rather than parsed"
        )
    parts.append("".join(current))
    return [part.strip() for part in parts if part.strip()]


# A middle initial, as the curated file spells one: a single letter and a period.
# `Roger D.`, `Alex K.`, and `Jos W. M.` (two of them).
_INITIAL = re.compile(r"\b[A-Za-z]\.")


def _without_initials(given: str) -> str:
    """`given` with its middle initials dropped: `Roger D.` -> `Roger`.

    Only whole single-letter-plus-period tokens go, so a spelled-out middle name
    and a multi-word surname particle both survive.
    """
    return " ".join(_INITIAL.sub("", given).split())


def parse_pi(value) -> list[str]:
    """The names in a free-text `pi` field: each PI's surname, then their full names.

    `Last, First M. (Affiliation, role)`, semicolon-separated, the parenthetical
    optional. Returns names in file order, deduplicated, so the person-name rule
    can match a question's person name without an LLM (plan D7). An absent PI
    gives an empty list.

        >>> parse_pi("White, Forest M. (MIT, contact PI); Michor, Franziska (Dana-Farber, co-PI)")
        ['White', 'Forest M. White', 'Forest White', 'Michor', 'Franziska Michor']

    Both full spellings are emitted, and the initial-free one is the load-bearing
    addition. The curated files write a middle initial for most PIs; nobody asking
    a question does. `Roger D. Kamm` is not a substring of `Roger Kamm` or the
    reverse, so neither exact nor substring matching recovers it, and 18 of the 21
    curated PI entries carried no matchable plain form before this. That the plain
    form is the live spelling is not a guess: the curated CSBC row's own `tags`
    carry "Forest White", MetNet's description says "led by Roger Kamm (MIT)", and
    every Scientist attribute value in the repo's live evidence is initial-free
    ("Bryan Bryson", "JoAnne Flynn", "Alex Shalek").

    `Last, First` is deliberately NOT emitted: that is the spelling the free-text
    `pi` column already holds for display, and it is not how a question names a
    person.
    """
    if value is None:
        return []
    text = str(value).strip()
    if text.lower() in _NO_PI:
        return []

    names: list[str] = []
    for entry in _split_outside_parens(text):
        # Drop the affiliation and role, which are not names.
        bare = entry.split("(", 1)[0].strip().rstrip(",").strip()
        if not bare or bare.lower() in _NO_PI:
            continue
        surname, _, given = (part.strip() for part in bare.partition(","))
        spellings = [surname]
        if given:
            spellings.append(f"{given} {surname}")
            plain = _without_initials(given)
            if plain and plain != given:
                spellings.append(f"{plain} {surname}")
        for name in spellings:
            name = name.strip()
            if name and name not in names:
                names.append(name)
    return names


def with_pi_names(rows: list[dict]) -> list[dict]:
    """Copies of `rows` carrying `pi_names` beside the free-text `pi`.

    Every row gains the column, including the ones with no PI, so the write never
    leaves it undefined. This happens before the database write, not after.
    """
    return [{**row, "pi_names": parse_pi(row.get("pi"))} for row in rows]


# --- rendering values --------------------------------------------------------


class UnsupportedValue(ValueError):
    """A value this renderer will not put in a SQL literal."""


class DuplicateKey(ValueError):
    """Two curated rows share a natural key, as MySQL would compare it."""


def json_text(value) -> str:
    """A JSON column's stored text.

    `json.dumps` defaults, which is how production already stores
    `alternative_names` (`'["Forest", "White", "U54"]'`), and `ensure_ascii=False`
    to match `context/README.md`'s convention and the utf8mb4 columns.
    """
    return json.dumps(value, ensure_ascii=False)


def db_value(table: str, column: str, value):
    """The Python value a column holds, before it becomes a SQL literal.

    One place so that the renderers and the round-trip check cannot disagree
    about it. Empty strings become NULL, which is how production stores them.
    """
    spec = TABLES[table]
    if column in spec.json_columns:
        return None if value is None else json_text(value)
    if value is None or value == "":
        return None
    if column in spec.int_columns:
        return int(value)
    return value


def literal(value) -> str:
    """A portable SQL literal: NULL, a bare integer, or a single-quoted string.

    Quotes are doubled rather than backslash-escaped and newlines stay literal, so
    the text means the same thing to MySQL and to the SQLite the round-trip check
    runs against. That is the whole reason this differs from
    `seed_literal` below, which keeps the committed seed files' one-line shape.

    A backslash is refused rather than guessed at: MySQL interprets `\\` inside a
    string literal and SQLite does not, so no single spelling survives both. No
    curated value contains one today (`test_no_curated_value_needs_a_backslash`),
    and if one ever does, this raises instead of writing something that means one
    thing to the generator's tests and another to production.
    """
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        raise UnsupportedValue(f"boolean {value!r}: no context column is boolean")
    if isinstance(value, int):
        return str(value)
    text = str(value)
    if "\\" in text:
        raise UnsupportedValue(
            f"{text[:60]!r} contains a backslash; MySQL and SQLite disagree about "
            "how to spell it, so add explicit handling rather than guessing"
        )
    return "'" + text.replace("'", "''") + "'"


def fold_key(key: str) -> str:
    """A natural key folded the way utf8mb4_unicode_ci compares it.

    `.strip().lower()` is not enough, and the gap is silent rather than loud.
    utf8mb4_unicode_ci equates a base letter with its accented form and `ss` with
    `ß`: on MySQL 8.0.46, `SELECT 'u' = _utf8mb4'ü' COLLATE utf8mb4_unicode_ci` is
    1 and so is the `ss`/`ß` pair, while Python's `'Müller'.lower() == 'Muller'`
    is False. Two such rows pass an unfolded check, and then
    `ON DUPLICATE KEY UPDATE` does not raise 1062 -- it absorbs the collision, so
    one curated row disappears with exit 0 and no failed statement. Proven against
    a utf8mb4_unicode_ci table carrying the unique key `render_update` adds: the
    two upserts left one row.

    NFKD with the combining marks dropped, then `casefold`, which is what folds
    `ß` to `ss`. That is not byte-for-byte the DUCET collation -- it is a
    deliberately wider net, because the cost of refusing two keys MySQL would have
    kept apart is an error message, and the cost of missing a pair it merges is a
    row lost in production.
    """
    decomposed = unicodedata.normalize("NFKD", key.strip())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


def _checked_keys(table: str, rows: list[dict]) -> list[str]:
    """The rows' natural keys, refusing any collision MySQL would see.

    `sample_types_context`, `assay_context` and `projects_context` are all
    utf8mb4_unicode_ci, which compares case-insensitively, ignores trailing spaces
    and equates an accented letter with its base one. Two curated rows that differ
    only that way would collide in the unique key below, so they are refused here
    instead. `fold_key` is where that comparison lives.
    """
    spec = TABLES[table]
    keys, seen = [], {}
    for index, row in enumerate(rows):
        key = str(row[spec.key])
        folded = fold_key(key)
        if folded in seen:
            raise DuplicateKey(
                f"{spec.source}: rows {seen[folded]} and {index} both key on "
                f"{key!r} as MySQL compares it (case-insensitive, trailing space "
                f"ignored, accents folded)"
            )
        seen[folded] = index
        keys.append(key)
    return keys


# --- the update script -------------------------------------------------------

# The script splits here. Everything ABOVE is schema work: MySQL-only, autocommitted
# by definition (DDL commits implicitly), and every statement conditional on the
# shape the instance actually has. Everything BELOW is the data, and it runs in one
# transaction, so a value the server refuses rolls the whole change back instead of
# leaving the table half migrated. The round-trip check applies the part below.
ROWS_MARKER = "-- == rows =="

_CONDITIONAL = """\
SET @nextseek_found := (SELECT COUNT(*) > 0 FROM information_schema.{catalog}
  WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = '{table}' AND {name_column} = '{name}');
SET @nextseek_stmt := IF({test}, '{statement}', 'DO 0');
PREPARE nextseek_stmt FROM @nextseek_stmt;
EXECUTE nextseek_stmt;
DEALLOCATE PREPARE nextseek_stmt;
"""


def _conditional(catalog: str, table_name: str, name_column: str, name: str,
                 statement: str, *, when_present: bool) -> str:
    """`statement`, run only when `name` is (or is not) in an information_schema catalog.

    `statement` is carried inside a single-quoted MySQL string, so it may not
    contain a quote of its own; every caller builds one out of identifiers only.
    """
    if "'" in statement:
        raise UnsupportedValue(
            f"a conditional statement may not contain a quote: {statement[:80]!r}"
        )
    return _CONDITIONAL.format(
        catalog=catalog, table=table_name, name_column=name_column, name=name,
        test="@nextseek_found" if when_present else "NOT @nextseek_found",
        statement=statement,
    )


def _add_column(table_name: str, column: str, definition: str) -> str:
    return _conditional(
        "COLUMNS", table_name, "COLUMN_NAME", column,
        f"ALTER TABLE `{table_name}` ADD COLUMN `{column}` {definition}",
        when_present=False,
    )


def _add_unique_key(table_name: str, key: str) -> str:
    index = f"uq_{table_name}_{key}"
    return _conditional(
        "STATISTICS", table_name, "INDEX_NAME", index,
        f"ALTER TABLE `{table_name}` ADD UNIQUE KEY `{index}` (`{key}`)",
        when_present=False,
    )


def _collapse_duplicates(table_name: str, key: str) -> str:
    """Delete every row but the lowest-id one for each `key`, where there is an id.

    The id is the guard, not an assumption. `dmac.projects_context` has NO `id`
    column: measured on the running local stack and confirmed by the 2026-09-11
    production pull, whose `projects_context` rows carry no `id` key while
    `sample_types_context` and `assay_context` both do. Its PRIMARY KEY is `name`,
    which is also why it cannot hold a duplicate for this statement to collapse.

    Emitted unconditionally, this aborted the apply with
    `ERROR 1054 (42S22) at line 19: Unknown column 'a.id' in 'on clause'` -- after
    step 2 had already deleted GBM and before a single curated row was written,
    every re-run repeating it (reproduced on mysql:8.0.46 against that exact
    shape). So it runs only where there is an id to order by, and where there is
    not, there is nothing for it to do.
    """
    return _conditional(
        "COLUMNS", table_name, "COLUMN_NAME", "id",
        f"DELETE `a` FROM `{table_name}` `a` JOIN `{table_name}` `b` "
        f"ON `a`.`{key}` = `b`.`{key}` AND `a`.`id` > `b`.`id`",
        when_present=True,
    )


def render_update(table: str, rows: list[dict]) -> str:
    """The SQL that makes `table` hold exactly these rows, re-runnable.

    The schema work happens first, and each step is needed against the 2026-09-11
    production pull rather than hypothetical:

      1. **Add the columns the table may predate.** `repository_attributes` and
         `pi_names` are newer than every instance's table.
      2. **Collapse duplicate keys, keeping the lowest id.** Production's
         `assay_context` has 22 duplicated `assay_name` values, 20 of them names
         the curated source carries, and the unique key in step 3 cannot be added
         while they exist. Conditional on the table having an `id`, because
         `projects_context` does not; see `_collapse_duplicates`.
      3. **Add the unique key.** `ON DUPLICATE KEY UPDATE` only fires against one,
         and no context table has one today.

    Then, in ONE transaction:

      4. **Delete the rows the curated source no longer names.** Production's
         `assay_context` holds 68 such names and `projects_context` holds one
         (`GBM`). An upsert alone leaves every one of them in place while
         reporting success.
      5. **One upsert per row**, setting every column including the key: MySQL
         matches the key case-insensitively, so the one case-only correction in
         the curated data (`Chemical challenge` -> `Chemical Challenge`) needs the
         key reassigned or production keeps its old spelling.

    Steps 4 and 5 share a transaction because they are the destructive pair, and
    the failure that motivated it was real: an over-long value aborted the upserts
    with error 1406 having already committed the DELETE, so the table was left
    with GBM gone, 0 of 12 rows written, and no way back. DDL commits implicitly
    in MySQL, so steps 1 to 3 cannot join them -- they are idempotent and
    non-destructive instead.

    A NULL key is covered explicitly. `NULL NOT IN (...)` is NULL rather than
    TRUE, MySQL's unique keys permit any number of NULLs, and no upsert matches
    one, so without `OR IS NULL` such a row survives every run untouched and the
    header's promise above it is false.
    """
    if table == "mappings":
        return render_mappings(rows)
    spec = TABLES[table]
    check_columns(table, rows)
    check_widths(table, rows)
    keys = _checked_keys(table, rows)
    columns = ", ".join(f"`{c}`" for c in spec.columns)
    assignments = ", ".join(f"`{c}`=VALUES(`{c}`)" for c in spec.columns)

    out = [
        f"-- {spec.name}: {len(rows)} rows generated from {spec.source} by",
        "-- scripts/context_gen.py --emit update. Regenerate rather than hand-editing.",
        "--",
        "-- Re-runnable: every statement below is idempotent, so applying this twice",
        f"-- leaves {spec.name} holding exactly the {len(rows)} curated rows.",
        "",
        CHARSET_PREAMBLE,
        "",
        "SET SESSION group_concat_max_len = 1048576;",
        "",
    ]
    added = ADDED_COLUMNS.get(table, {})
    if added:
        out.append("-- 1. columns this instance's table may predate")
        for column, definition in added.items():
            out.append(_add_column(spec.name, column, definition))
    out += [
        "-- 1b. text columns narrower than the curated values need, or not utf8mb4",
        _pin_columns(table),
        f"-- 2. duplicate `{spec.key}` values, keeping the lowest id",
        _collapse_duplicates(spec.name, spec.key),
        "-- 3. the unique key the upsert fires against",
        _add_unique_key(spec.name, spec.key),
        "-- 3b. refuse, before any row changes, what this table still cannot take",
        f"SET @nextseek_problems := CONCAT_WS(' | ', {_target_problems(table, rows)});",
        _refuse("before writing anything"),
        ROWS_MARKER,
        "START TRANSACTION;",
        "",
        f"-- 4. rows {spec.source} no longer names, and any row with no key at all",
        f"DELETE FROM `{spec.name}` WHERE `{spec.key}` NOT IN "
        f"({', '.join(literal(k) for k in keys)}) OR `{spec.key}` IS NULL;",
        "",
        "-- 5. the curated rows",
    ]
    for row in rows:
        values = ", ".join(literal(db_value(table, c, row.get(c))) for c in spec.columns)
        out.append(f"INSERT INTO `{spec.name}` ({columns}) VALUES ({values})\n"
                   f"  ON DUPLICATE KEY UPDATE {assignments};")
    out += ["", "COMMIT;"]
    return "\n".join(out) + "\n"


# --- the seed files ----------------------------------------------------------
#
# The seed a fresh install would load for each table, written to
# startup/seed/sql/<table>.curated.sql. HELD: no install step reads those files
# until the curated content is signed off. startup/steps/schema_fixups.py still
# registers the pre-generator assay_context.sql and the empty projects_context.sql,
# so `install` and `reset` load what they loaded before this module existed;
# scripts/README.md group C says what switching them on takes. The files are
# committed so the review sees exactly what an install would get, and the test
# lane pins them byte for byte to what this module renders.
#
# Column types come from seek/models/nextseek.py::Sample_types_context for the
# sample types and from the live tables for the other two. The unique key on
# each natural key is declared here; `--emit update` adds it to an existing table.

DDL = {
    "sample_types": """\
-- Curated context for sample types: what each code means, which attributes it
-- collects, what it is made from and what it feeds. Generated from
-- context/sample_types.json by scripts/context_gen.py --emit seed; regenerate
-- rather than hand-editing.
--
-- HELD: no install step reads this file until the curated content is signed off
-- (scripts/README.md group C).
--
-- Created in SQL because no Django migration references the table. The seeded
-- dump startup/seed/dmac.sql.gz DOES create and populate it, in an older shape
-- with no repository_attributes column and no unique key, so this file would
-- matter only where the dump does not run.
--
-- It is not an update. Against a table that already exists CREATE TABLE IF NOT
-- EXISTS skips, and the INSERTs then either fail (a column the table lacks, or the
-- unique key this file declares) or, on a table with every column and no unique
-- key, land on top of the rows already there. `--emit update` is what brings an
-- existing table to the curated content.
--
-- Column types follow seek/models/nextseek.py::Sample_types_context, which is
-- how the application reads and writes these rows. `Tags` is capitalised: it is
-- that model's one db_column override. `repository_attributes` is JSON text and
-- nothing reads it yet.
SET NAMES utf8mb4;
CREATE TABLE IF NOT EXISTS sample_types_context (
  id                        INT AUTO_INCREMENT PRIMARY KEY,
  sample_type               VARCHAR(32)  NULL,
  sampletype_id             INT          NULL,
  name                      VARCHAR(255) NULL,
  clade                     VARCHAR(64)  NULL,
  description               TEXT         NULL,
  Tags                      TEXT         NULL,
  required_metadata         TEXT         NULL,
  standard_metadata         TEXT         NULL,
  possible_metadata_fields  TEXT         NULL,
  parent_sampletypes        TEXT         NULL,
  child_sampletypes         TEXT         NULL,
  associated_assay_parents  TEXT         NULL,
  associated_assay_children TEXT         NULL,
  repository_attributes     TEXT         NULL,
  sampletype_file_link      VARCHAR(255) NULL,
  UNIQUE KEY `uq_sample_types_context_sample_type` (`sample_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
""",
    "assays": """\
-- Curated context for internal assays: what each one consumes, produces and is
-- called. Generated from context/assays.json by
-- scripts/context_gen.py --emit seed; regenerate rather than hand-editing.
--
-- HELD: no install step reads this file until the curated content is signed off
-- (scripts/README.md group C).
--
-- Created in SQL because no Django migration references the table and the seeded
-- dump startup/seed/dmac.sql.gz does not carry it. One row per internal assay,
-- `assay_name` unique.
--
-- internal_assay_id is the id production gives each internal assay. A stack whose
-- internal_assays was not copied from production numbers them differently, so on
-- such a stack these ids point at nothing or at the wrong assay; `--emit update`
-- links each row by its title instead.
--
-- It is not an update. Against a table that already exists CREATE TABLE IF NOT
-- EXISTS skips, and the INSERTs then either fail (a column the table lacks, or the
-- unique key this file declares) or, on a table with every column and no unique
-- key, land on top of the rows already there. `--emit update` is what brings an
-- existing table to the curated content.
--
-- The three widths below are the live table's: Parent_Clade_Type and
-- Child_Clade_Type varchar(128), AssaySheet_Link varchar(512).
SET NAMES utf8mb4;
CREATE TABLE IF NOT EXISTS assay_context (
  id                           INT AUTO_INCREMENT PRIMARY KEY,
  assay_name                   VARCHAR(255) NULL,
  Description                  TEXT         NULL,
  Tags                         TEXT         NULL,
  Alternative_Assay_Names      TEXT         NULL,
  Required_Parent_Sample_Types TEXT         NULL,
  Optional_Parent_Sample_Types TEXT         NULL,
  Children_Sample_Types        TEXT         NULL,
  Parent_Clade_Type            VARCHAR(128) NULL,
  Child_Clade_Type             VARCHAR(128) NULL,
  AssaySheet_Link              VARCHAR(512) NULL,
  AssociatedRepository         VARCHAR(255) NULL,
  Critical_Attributes          TEXT         NULL,
  Protocols_Phrases            TEXT         NULL,
  Protocols_UIDs               TEXT         NULL,
  internal_assay_id            INT          NULL,
  UNIQUE KEY `uq_assay_context_assay_name` (`assay_name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
""",
    "projects": """\
-- Curated context for SEEK projects and investigations: who runs one, what it
-- studies, where it is published. Generated from context/projects.json by
-- scripts/context_gen.py --emit seed; regenerate rather than hand-editing.
--
-- HELD: no install step reads this file until the curated content is signed off
-- (scripts/README.md group C).
--
-- Read by the project page header; every field is optional and the header falls
-- back to the SEEK title and description when the row is absent. The lookup is
-- `SELECT * FROM projects_context WHERE project_id = %s`
-- (nextseek_api/services/context_catalog.py), keyed on the SEEK project id and
-- with no fallback by name. The project_id values below are PRODUCTION's SEEK
-- ids. The committed seek seed carries exactly one project, `Published Data` at
-- id 1, so on a fresh install every row here matches no project and the header
-- falls back -- and on any stack whose SEEK projects were created in a different
-- order, a row renders against whichever project happens to hold that id. Check
-- the ids against the target stack's `projects` table before relying on them.
--
-- alternative_names, key_data_types and pi_names are JSON arrays stored as text,
-- matching how chat_nextseek's map_project already reads them: it json.loads the
-- value and falls back to splitting on '|'. pi_names is generated from the
-- free-text pi field by scripts/context_gen.py::parse_pi, never hand-written.
--
-- pi, nih_reporter_link and fairdomhub_published_link are TEXT, which is what the
-- live column is in all three cases; a curated pi is longer than VARCHAR(255).
SET NAMES utf8mb4;
CREATE TABLE IF NOT EXISTS projects_context (
  id                        INT AUTO_INCREMENT PRIMARY KEY,
  name                      VARCHAR(255) NULL,
  alternative_names         TEXT         NULL,
  entity_type               VARCHAR(64)  NULL,
  project_id                INT          NULL,
  parent_project            VARCHAR(255) NULL,
  pi                        TEXT         NULL,
  pi_names                  TEXT         NULL,
  research_focus            TEXT         NULL,
  key_data_types            TEXT         NULL,
  description               TEXT         NULL,
  nih_reporter_link         TEXT         NULL,
  fairdomhub_published_link TEXT         NULL,
  tags                      TEXT         NULL,
  KEY idx_project_id (project_id),
  UNIQUE KEY `uq_projects_context_name` (`name`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
""",
}


def seed_literal(value) -> str:
    """A MySQL literal in the committed seed files' style.

    Newlines are escaped rather than emitted raw so every INSERT is exactly one
    line: 79 curated sample type values and 30 assay values run to a dozen
    sentences with embedded newlines, and a statement that spans lines makes the
    file painful to diff and to count. Carried over verbatim from the retired
    scripts/generate_assay_context_seed.py, which is why these files' shape did
    not change when this module took them over.

    This is MySQL-only, on purpose: `\\n` in a literal means a newline to MySQL and
    two characters to SQLite. `literal` above is the portable one.
    """
    if value is None:
        return "NULL"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    text = str(value)
    # Backslash first, or it would double-escape everything added after it.
    text = text.replace("\\", "\\\\").replace("'", "\\'")
    text = text.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")
    return "'" + text + "'"


def render_seed(table: str, rows: list[dict]) -> str:
    """The seed file for `table`: its DDL, then one INSERT per row per line."""
    if table not in TABLES:
        raise ValueError(
            f"{table!r} has no seed file. context/assay_mappings.json is a list of "
            "operations on rows that already exist in internal_assays and "
            "assays_internal_assays, not a table to seed; use --emit update."
        )
    spec = TABLES[table]
    check_columns(table, rows)
    check_widths(table, rows)
    _checked_keys(table, rows)
    columns = ", ".join(f"`{c}`" for c in spec.columns)
    out = [DDL[table]]
    for row in rows:
        values = ", ".join(seed_literal(db_value(table, c, row.get(c))) for c in spec.columns)
        out.append(f"INSERT INTO `{spec.name}` ({columns}) VALUES ({values});")
    return "\n".join(out) + "\n"


# --- the mapping operations --------------------------------------------------
#
# context/assay_mappings.json is not a table. It is a list of operations on rows
# that already exist in `dmac.internal_assays` and `dmac.assays_internal_assays`,
# applied grouped in the order context/README.md gives: renames, creates, maps and
# remaps, merges. Targets are named by title, after renames, and every `from_*`
# key is a production value so the generator can refuse if production has moved.
# Each statement's WHERE clause is that refusal and also what makes it a no-op on
# a second run.
#
# Each guard below is there for a measured reason, not a hypothetical one. Against
# the 2026-09-11 production pull: the 25 `map` ops name exactly the 25
# `assays_internal_assays` rows whose `internal_assay_id` is NULL; every `remap`
# matches a row carrying its stated `from_internal_assay_id`; and every
# `merge_internal` id and `rename_internal` from_title matches.
#
# The rename-before-create order is load-bearing, not stylistic. Exactly 2 of the
# 14 `create_internal` titles already exist in production, `Mass Spectrometry` and
# `Mass Spectrometry Analysis`, and they are exactly the 2 titles the renames free
# up (ids 130 and 47 become `Mass Spectrometry Proteomics` and `Mass Spectrometry
# Proteomics Analysis`). Run the creates first and their NOT EXISTS guard skips
# both, so the two new internal assays never exist and every remap aimed at them
# resolves to the old id instead. `test_the_renames_have_to_run_before_the_creates`
# pins that.

TABLES_EXTRA = {"mappings": Path("context/assay_mappings.json")}

MAPPING_KEYS = {
    "rename_internal": {"internal_assay_id", "from_title", "internal_assay_title"},
    "create_internal": {"internal_assay_title"},
    "map": {"seek_assay_id", "seek_title", "internal_assay_title"},
    "remap": {"seek_assay_id", "seek_title", "from_internal_assay_id", "internal_assay_title"},
    "merge_internal": {"internal_assay_id", "from_title", "into_internal_assay_title"},
}
MAPPING_ORDER = ("rename_internal", "create_internal", "map", "remap", "merge_internal")


class UnknownAction(ValueError):
    """A mapping row names an operation this generator does not implement."""


class MappingMismatch(ValueError):
    """assays.json and assay_mappings.json disagree about a new internal assay."""


def _by_title(title: str) -> str:
    """The subquery that resolves an internal assay title to its id.

    By title and never by id, because `create_internal` ids are assigned by the
    database, and after the rename group every title is the curated spelling.
    """
    return (f"(SELECT `id` FROM `internal_assays` WHERE `internal_assay_title` = "
            f"{literal(title)} ORDER BY `id` LIMIT 1)")


def _title_exists(title: str) -> str:
    """The guard that makes a moved target a no-op instead of a NULL write.

    `_by_title` returns NULL when nothing carries the title, and `SET col = NULL`
    is a write, not a skip. Worse, the remap's own `WHERE col IN (<from_id>,
    <subquery>)` is still TRUE on the `from_id` arm, so the row matches and is
    blanked. Measured on mysql:8.0.46: retitling one remap target upstream and
    applying the mappings once moved two `assays_internal_assays` rows from
    internal assay 196 to NULL, and a second run did not heal them, because NULL
    is not matched by the IN. 14 of the remap targets are titles no `create_internal`
    or `rename_internal` produces, so they have to already exist in production for
    the operation to mean anything -- which is exactly the drift this guards.
    """
    return (f"EXISTS (SELECT 1 FROM `internal_assays` WHERE `internal_assay_title` = "
            f"{literal(title)})")


def _comment(text: str) -> str:
    """A value safe to interpolate into a `--` comment.

    A `--` comment runs to the end of the line, so a newline in the value ends it
    and everything after becomes a statement in a script the operator runs against
    production. `seek_title` and `into_internal_assay_title` exist only for these
    comments, so they are the two values in `context/assay_mappings.json` that no
    consumer would otherwise reject, and that file is hand-edited. Refused rather
    than stripped, for the same reason `literal` refuses a backslash: the curated
    file is wrong and should say so.
    """
    value = str(text)
    if "\n" in value or "\r" in value:
        raise UnsupportedValue(
            f"{value[:60]!r} contains a newline, which would end the SQL comment it "
            "is written into and make the rest of it an executable statement"
        )
    return value


def check_mapping_consistency(assays: list[dict], mappings: list[dict]) -> None:
    """Refuse a new internal assay that only one of the two files knows about.

    context/README.md: a row in `assays.json` with `internal_assay_id: null` IS a
    `create_internal` entry, and its `assay_name` must equal that entry's title.
    A rename in one file that misses the other fails here rather than leaving an
    assay_context row pointing at nothing.
    """
    nameless = {r.get("assay_name") for r in assays if r.get("internal_assay_id") is None}
    created = {m.get("internal_assay_title") for m in mappings
               if m.get("action") == "create_internal"}
    if nameless != created:
        raise MappingMismatch(
            f"created but not in assays.json: {sorted(created - nameless)}; "
            f"null internal_assay_id but never created: {sorted(nameless - created)}"
        )


def _check_mapping_rows(rows: list[dict]) -> None:
    for index, row in enumerate(rows):
        action = row.get("action")
        if action not in MAPPING_KEYS:
            raise UnknownAction(f"assay_mappings.json row {index}: unknown action {action!r}")
        expected = MAPPING_KEYS[action]
        keys = set(row) - {"action"}
        if keys != expected:
            raise UnknownColumn(
                f"assay_mappings.json row {index} ({action}): expected "
                f"{sorted(expected)}, got {sorted(keys)}"
            )


def render_mappings(rows: list[dict]) -> str:
    """The SQL for `context/assay_mappings.json`, grouped and re-runnable.

    One statement here writes to a context table rather than to the two mapping
    tables: the `assay_context` backfill after the creates. It has to be here and
    not in `render_update("assays")`, because the ids it copies do not exist until
    the `create_internal` INSERTs above it have run, and `--emit update --table all`
    emits the assay rows first. Without it the 14 curated rows whose
    `internal_assay_id` is null stay null forever -- nothing else in the emitted
    script ever writes that column -- while `context/README.md` says the generator
    assigns those ids and `chat_nextseek`'s `map_sampletype` publishes the field to
    the agent as "Internal Assay ID".
    """
    _check_mapping_rows(rows)
    grouped = {action: [r for r in rows if r["action"] == action] for action in MAPPING_ORDER}

    out = [
        f"-- internal_assays and assays_internal_assays: {len(rows)} operations generated",
        "-- from context/assay_mappings.json by scripts/context_gen.py --emit update.",
        "--",
        "-- Grouped in context/README.md's order. Every WHERE clause pins the production",
        "-- value the operation was written against, so an operation whose target has",
        "-- moved affects no rows instead of writing the wrong one, and a second run is",
        "-- a no-op.",
        "",
        CHARSET_PREAMBLE,
        "",
        f"-- rename_internal: {len(grouped['rename_internal'])}",
    ]
    for row in grouped["rename_internal"]:
        out.append(
            f"UPDATE `internal_assays` SET `internal_assay_title` = "
            f"{literal(row['internal_assay_title'])}\n"
            f"  WHERE `id` = {int(row['internal_assay_id'])} AND `internal_assay_title` IN "
            f"({literal(row['from_title'])}, {literal(row['internal_assay_title'])});"
        )

    out += ["", f"-- create_internal: {len(grouped['create_internal'])}. These run AFTER the "
                "renames on purpose: two of the",
            "-- titles are ones a rename above frees up, and the guard would skip them.",
            "-- The guard itself is what makes a second run a no-op."]
    for row in grouped["create_internal"]:
        title = literal(row["internal_assay_title"])
        out.append(
            f"INSERT INTO `internal_assays` (`internal_assay_title`) SELECT {title} FROM DUAL\n"
            f"  WHERE NOT EXISTS (SELECT 1 FROM `internal_assays` "
            f"WHERE `internal_assay_title` = {title});"
        )

    out += ["", f"-- the assay_context rows those {len(grouped['create_internal'])} creates "
                "gave an id. The id is the database's, so",
            "-- it cannot be written by --table assays, which is emitted before this file.",
            "-- Only a NULL is filled, so a second run is a no-op."]
    for row in grouped["create_internal"]:
        title = row["internal_assay_title"]
        out.append(
            f"UPDATE `assay_context` SET `internal_assay_id` = {_by_title(title)}\n"
            f"  WHERE `assay_name` = {literal(title)} AND `internal_assay_id` IS NULL;"
        )

    out += ["", f"-- map and remap: {len(grouped['map'])} + {len(grouped['remap'])}. A map only "
                "fills a NULL; a remap only",
            "-- moves the internal assay it was written against. Both refuse outright when "
            "the target",
            "-- title is not there, rather than writing the NULL the subquery would return."]
    for row in grouped["map"]:
        out.append(
            f"-- {_comment(row['seek_title'])} (SEEK assay {int(row['seek_assay_id'])})\n"
            f"UPDATE `assays_internal_assays` SET `internal_assay_id` = "
            f"{_by_title(row['internal_assay_title'])}\n"
            f"  WHERE `assay_id` = {int(row['seek_assay_id'])} AND `internal_assay_id` IS NULL\n"
            f"  AND {_title_exists(row['internal_assay_title'])};"
        )
    for row in grouped["remap"]:
        target = _by_title(row["internal_assay_title"])
        out.append(
            f"-- {_comment(row['seek_title'])} (SEEK assay {int(row['seek_assay_id'])})\n"
            f"UPDATE `assays_internal_assays` SET `internal_assay_id` = {target}\n"
            f"  WHERE `assay_id` = {int(row['seek_assay_id'])} AND `internal_assay_id` IN "
            f"({int(row['from_internal_assay_id'])}, {target})\n"
            f"  AND {_title_exists(row['internal_assay_title'])};"
        )

    out += ["", f"-- merge_internal: {len(grouped['merge_internal'])}. Each one refuses while any "
                "SEEK assay still points at it,",
            "-- which is what makes the remaps above a precondition rather than an intention."]
    for row in grouped["merge_internal"]:
        assay_id = int(row["internal_assay_id"])
        out.append(
            f"-- into {_comment(row['into_internal_assay_title'])}\n"
            f"DELETE FROM `internal_assays` WHERE `id` = {assay_id} AND "
            f"`internal_assay_title` = {literal(row['from_title'])}\n"
            f"  AND NOT EXISTS (SELECT 1 FROM `assays_internal_assays` "
            f"WHERE `internal_assay_id` = {assay_id});"
        )
    return "\n".join(out) + "\n"


# --- the generated investigation block ---------------------------------------
#
# capabilities.md's "Known Projects and Investigations" section lists eight names
# and tells the agent to "use these names exactly". Five of the eight return
# nothing: SEEK carries two parallel investigation systems, and the list names the
# paper-tracking copies in TestProject_250820 (38 bibliographic studies, zero
# samples) rather than the real investigations that hold the samples. Measured on
# the live 1.2 graph and confirmed against the 2026-09-11 production pull. It is
# not repaired by a sync and it is not a local artifact.
#
# Operator decision, 2026-09-17: do not hand-edit that list, generate it. The
# section becomes a marked generated block filled from projects_context rows whose
# entity_type is "investigation", following the repo's existing
# `<!-- BEGIN DOCS-MAP:... -->` precedent. The marker is CONTEXT-GEN rather than
# DOCS-MAP because ci/docs_map.py owns that namespace and does not own this block.
#
# Two rules the block keeps, and one refusal:
#
#   * **No counts.** The repo's doc rules forbid a dated count in a README or CLAUDE
#     file, and a baked count rots the day the next sync runs. Names and a short
#     description. Live counts reach the graph agent through the catalog reader.
#   * **Investigations only.** `catalog.assistant_investigations` checks every name
#     in the section against Investigation nodes, so a project row that is not also
#     an investigation title would fail a check for a perfectly correct row.
#   * **Refuse at generation, not at rebuild.** An investigation that resolves to
#     zero samples is not emitted, so the defect cannot be committed in the first
#     place. `drift.py` stays the runtime backstop for the case where the data moves
#     under a correct file.

CAPABILITIES_BEGIN = "<!-- BEGIN CONTEXT-GEN:investigations -->"
CAPABILITIES_END = "<!-- END CONTEXT-GEN:investigations -->"

_CAPABILITIES_INTRO = (
    "The graph database organizes samples into studies grouped under named "
    "investigations. The investigations that hold samples are:"
)
_CAPABILITIES_OUTRO = (
    "Use these names exactly when asking graph questions scoped to one "
    "investigation. The names in brackets are what people call them; the bold name "
    "is what the graph answers to."
)


class ZeroSampleInvestigation(ValueError):
    """An investigation in the catalog resolves to no samples in the graph."""


class NoInvestigations(ValueError):
    """No row is an investigation, so there is no list to generate."""


class IncompleteInvestigation(ValueError):
    """An investigation row carries no description to tell the agent what it is."""


class UnlistedInvestigation(ValueError):
    """The counts prove an investigation answers, and no curated row names it."""


class BakedCount(ValueError):
    """A curated description carries a number that reads as a sample count."""


# A count, as opposed to a gene or a protein. `PAX3-FOXO1` and `COL2A1` are real
# curated content and must pass; `1,084,754` and `84394` must not. Four or more
# consecutive digits, or any comma-grouped number.
_COUNT_LIKE = re.compile(r"\d{4,}|\d{1,3}(?:,\d{3})+")

# Markdown a name or an alias may not carry. `drift.assistant_investigation_names`
# captures the bold term as `[^*]+`, so an asterisk truncates the name it then looks
# up; a newline ends the bullet outright, and an alias carrying one can open a bullet
# of its own that drift reads as a curated investigation name.
_BULLET_BREAKING = "*\n\r"


def _short_description(row: dict) -> str:
    """One line saying what an investigation studies.

    `research_focus` when it is there, else the first sentence of `description`.
    Both are single lines by the time they reach the block: a bullet that wraps
    would end the list as far as a Markdown reader is concerned.
    """
    focus = (row.get("research_focus") or "").strip()
    if not focus:
        text = " ".join((row.get("description") or "").split())
        focus = text.split(". ", 1)[0].strip()
        if focus and not focus.endswith("."):
            focus += "."
    return " ".join(focus.split())


def _bullet_safe(value: str, what: str) -> str:
    """`value`, or a refusal if it carries Markdown that would break the bullet.

    Applied to aliases as well as titles, which is not symmetry for its own sake.
    An alias is only `.strip()`ed otherwise, and a newline in one opens a bullet of
    its own: the alias `x]\\n- **GBM**` renders a second bullet, and drift's real
    parser then answers `['MIT_SRP', 'GBM']` -- a retired name back in the checked
    list, from a row nobody would read as declaring it.
    """
    if any(char in value for char in _BULLET_BREAKING):
        raise UnsupportedValue(
            f"{what} {value!r} contains Markdown that would split the bold run the "
            "drift check reads; rename it or the check fails on a name nobody wrote"
        )
    return value


def render_capabilities_block(rows: list[dict], sample_counts=None) -> str:
    """The generated "Known Projects and Investigations" block.

    `rows` are `projects_context` rows; only the investigations are listed.
    `sample_counts` maps an investigation title to its live sample count and is
    what the refusal is decided on. `nextseek_api/graph_sync/drift.py` already
    produces exactly that mapping, in the `assistant_investigations` stat, from the
    same Cypher its runtime check uses, so generation and the backstop are decided
    on one measurement.

    No counts are written. They decide what is emitted and are then discarded.

    Passing no counts refuses everything, which is the honest reading: without
    evidence that a name answers, nothing may be told to the agent.
    """
    counts = dict(sample_counts or {})
    investigations = sorted(
        (r for r in rows if (r.get("entity_type") or "").strip().lower() == "investigation"),
        key=lambda r: str(r.get("name") or ""),
    )
    if not investigations:
        raise NoInvestigations(
            "no row has entity_type 'investigation', so this block would empty the "
            "section and take the agent's only list of investigations with it. Add "
            "the rows to context/projects.json first (plan task 6.15c)."
        )

    dead = [str(r.get("name")) for r in investigations if counts.get(str(r.get("name")), 0) <= 0]
    if dead:
        raise ZeroSampleInvestigation(
            f"{len(dead)} investigation(s) resolve to no samples: {', '.join(sorted(dead))}. "
            "An empty investigation is worse than a missing one: the agent scopes to it "
            "and gets a confident zero rather than an error. Point the row at the "
            "investigation that holds the samples, or drop it."
        )

    listed = {str(r.get("name")) for r in investigations}
    surplus = sorted(name for name, count in counts.items()
                     if count > 0 and str(name) not in listed)
    if surplus:
        raise UnlistedInvestigation(
            f"{len(surplus)} investigation(s) hold samples and no curated row names "
            f"them: {', '.join(surplus)}. Emitting the block would drop them from the "
            "only list the agent has, silently, so the agent would never learn they "
            "exist. Add a row to context/projects.json, or stop measuring them."
        )

    missing = [str(r.get("name")) for r in investigations if not _short_description(r)]
    if missing:
        raise IncompleteInvestigation(
            f"no research_focus or description for: {', '.join(sorted(missing))}. "
            "A name on its own tells the agent nothing about when to use it."
        )

    lines = [CAPABILITIES_BEGIN, "", _CAPABILITIES_INTRO, ""]
    for row in investigations:
        name = _bullet_safe(str(row["name"]), "investigation title")
        alternatives = [
            _bullet_safe(str(a).strip(), f"alternative name of {name!r}")
            for a in (row.get("alternative_names") or [])
            if str(a).strip() and str(a).strip() != name
        ]
        description = _short_description(row)
        found = _COUNT_LIKE.search(description)
        if found:
            raise BakedCount(
                f"{name}: its description carries {found.group(0)!r}, which reads as a "
                "count. A count baked into this block rots the day the next sync runs, "
                "and live counts already reach the agent through the catalog reader. "
                "Rewrite the research_focus without it."
            )
        bullet = f"- **{name}** — {description}"
        if alternatives:
            bullet += f" [also: {', '.join(alternatives)}]"
        lines.append(bullet)
    lines += ["", _CAPABILITIES_OUTRO, "", CAPABILITIES_END, ""]
    return "\n".join(lines)


# The heading drift keys on. `drift.assistant_investigation_names` finds this exact
# line and reads the bullets under it, and when it finds no such line it returns []
# and `_check_assistant_investigations` then PASSES with "capabilities.md has no
# Known Projects and Investigations section". So a renamed or moved heading disables
# the runtime backstop silently while the generator keeps writing happily. Neither
# side owns the heading, so this is where the two are tied together.
DRIFT_SECTION_HEADING = "## Known Projects and Investigations"


def replace_capabilities_block(text: str, block: str) -> str:
    """`text` with everything between the markers replaced by `block`.

    The generated block is placed under the H2 heading `drift.py` keys on, and this
    refuses if it is not: see `DRIFT_SECTION_HEADING`.

    What this does NOT need to enforce, contrary to an earlier note here: writing
    the block does not make `route_capabilities.json` stale. The NS projection reads
    only the three required H2 sections ("Overview", "What You Can Ask", "What the
    System Cannot Do"), so regenerating this block leaves the projection and the
    route-level object byte for byte identical -- measured by regenerating the block
    and re-projecting. The step that actually carries a new list to the agent is the
    image COPY and rebuild, not `gen_op_surfaces`.
    """
    if CAPABILITIES_BEGIN not in text or CAPABILITIES_END not in text:
        raise ValueError(
            f"no {CAPABILITIES_BEGIN} ... {CAPABILITIES_END} pair to replace. The "
            "markers are added to capabilities.md once, by hand, around the section "
            "body; after that this function owns what is between them."
        )
    head = text.split(CAPABILITIES_BEGIN, 1)[0]
    if not re.search(rf"^{re.escape(DRIFT_SECTION_HEADING)}\s*$", head, re.M):
        raise ValueError(
            f"the block would not sit under `{DRIFT_SECTION_HEADING}`, which is the "
            "heading nextseek_api/graph_sync/drift.py reads the names from. With no "
            "such heading above it drift finds no names and its check PASSES, so the "
            "runtime backstop would be off and nothing would say so."
        )
    tail = text.split(CAPABILITIES_END, 1)[1]
    return head + block.rstrip("\n") + tail


# --- the command line --------------------------------------------------------

# `.curated.sql`, not the names startup/steps/schema_fixups.py registers: these are
# held out of install until the curated content is signed off. See the note above
# DDL and scripts/README.md group C.
SEED_FILES = {"sample_types": "sample_types_context.curated.sql",
              "assays": "assay_context.curated.sql",
              "projects": "projects_context.curated.sql"}
SEED_DIR = Path("startup/seed/sql")

# The file `--emit capabilities` rewrites in place.
CAPABILITIES_PATH = Path("NessieAI/chat_nextseek/src/chat_nextseek/context/capabilities.md")


def rows_for(table: str) -> list[dict]:
    """The curated rows for `table`, with everything the generator adds."""
    if table in TABLES_EXTRA:
        return load_source(TABLES_EXTRA[table])
    rows = load_source(TABLES[table].source)
    return with_pi_names(rows) if table == "projects" else rows


def _emit(text: str, out) -> None:
    if out is None:
        print(text, end="")
        return
    path = Path(out)
    if not path.is_absolute():
        path = Path.cwd() / path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    print(f"wrote {out} ({len(text.splitlines())} lines)")


def emit_capabilities(counts_path, out=None) -> int:
    """Rewrite the generated investigation block in `capabilities.md`.

    `counts_path` is a JSON object mapping an investigation title to its live
    sample count -- `drift`'s `assistant_investigations` stat, whose `samples` key
    is exactly that shape. The counts are read from a file rather than measured
    here because this module connects to nothing: no database, no graph.

    Until task 6.15c adds the investigation rows and the markers, this raises. That
    is the mode earning its place rather than failing to: the refusals in
    `render_capabilities_block` are stricter and earlier than the runtime backstop,
    and with no entry point at all nothing could reach them, so today's committed
    `capabilities.md` still names five investigations that answer nothing.
    """
    counts = json.loads(Path(counts_path).read_text(encoding="utf-8"))
    if isinstance(counts, dict) and "samples" in counts:      # drift's stat, whole
        counts = counts["samples"]
    block = render_capabilities_block(rows_for("projects"), counts)
    target = Path(out) if out else (REPO_ROOT / CAPABILITIES_PATH)
    text = target.read_text(encoding="utf-8")
    target.write_text(replace_capabilities_block(text, block), encoding="utf-8")
    print(f"wrote {target}")
    return 0


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--emit", required=True, choices=("update", "seed", "capabilities"),
                        help="update: SQL for a live database. seed: a fresh install's "
                             "file. capabilities: the investigation block in capabilities.md.")
    parser.add_argument("--table", default="all",
                        choices=("all",) + tuple(TABLES) + tuple(TABLES_EXTRA),
                        help="which table, or all of them")
    parser.add_argument("--counts", default=None,
                        help="for --emit capabilities: a JSON file mapping an "
                             "investigation title to its live sample count, as drift's "
                             "assistant_investigations stat reports it")
    parser.add_argument("--out", default=None,
                        help="a file for --emit update (default stdout), or the seed "
                             f"directory for --emit seed (default {SEED_DIR}), or the "
                             "markdown file for --emit capabilities")
    args = parser.parse_args(argv)

    if args.emit == "capabilities":
        if not args.counts:
            parser.error("--emit capabilities needs --counts: without evidence that a "
                         "name answers, nothing may be told to the agent")
        return emit_capabilities(args.counts, args.out)

    if args.table == "all":
        tables = list(TABLES) + (list(TABLES_EXTRA) if args.emit == "update" else [])
    else:
        tables = [args.table]
    if {"assays", "mappings"} <= set(tables):
        # Both files describe the same new internal assays; refuse if they disagree.
        check_mapping_consistency(rows_for("assays"), rows_for("mappings"))
    if args.emit == "seed":
        directory = Path(args.out) if args.out else (REPO_ROOT / SEED_DIR)
        for table in tables:
            _emit(render_seed(table, rows_for(table)), directory / SEED_FILES[table])
        return 0
    _emit("\n".join(render_update(table, rows_for(table)) for table in tables), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

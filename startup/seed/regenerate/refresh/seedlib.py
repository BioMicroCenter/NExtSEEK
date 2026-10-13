"""Shared settings and helpers for the seed refresh kit (see ../README.md).

Everything runs against THROWAWAY containers named by SEED_PREFIX (default `seedrefresh`): `<prefix>-mysql`,
`<prefix>-neo4j`, `<prefix>-verify-mysql`, `<prefix>-verify-neo4j`, never a compose stack. Their passwords are
fixed throwaway values. The work schema `<prefix>_work` (kill sets, needles) lives only in `<prefix>-mysql` and is
never dumped.
"""
import os
import re
import subprocess
from pathlib import Path

KIT = Path(__file__).resolve().parent
REPO = KIT.parents[3]  # refresh -> regenerate -> seed -> startup -> repo root
PREFIX = os.environ.get("SEED_PREFIX", "seedrefresh")
C = os.environ.get("C", f"{PREFIX}-mysql")  # the MySQL container a step talks to
PW = os.environ.get("SEED_MYSQL_PW", f"{PREFIX}root")
NEO4J_PW = os.environ.get("SEED_NEO4J_PW", f"{PREFIX}pass")
WORK = f"{PREFIX}_work"
SEEK, DMAC = "seek_production", "dmac"


def run_sql(sql, db=None, check=True):
    """Run SQL text through the container's mysql client; return stdout (tab-separated, -N -B)."""
    cmd = ["docker", "exec", "-i", "-e", f"MYSQL_PWD={PW}", C, "mysql", "-uroot", "-N", "-B",
           "--default-character-set=utf8mb4"]
    if db:
        cmd.append(db)
    p = subprocess.run(cmd, input=sql.encode(), capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"mysql failed: {p.stderr.decode()[:2000]}\nSQL: {sql[:500]}")
    return p.stdout.decode("utf8", "replace")


def rows(sql, db=None):
    return [line.split("\t") for line in run_sql(sql, db).split("\n") if line != ""]


def scalar(sql, db=None):
    r = rows(sql, db)
    return r[0][0] if r else None


def columns(schema):
    """{table: [(column, data_type, column_type)]} in ordinal order, base tables only."""
    out = {}
    for t, c, dt, ct in rows(
        "SELECT c.table_name, c.column_name, c.data_type, c.column_type FROM information_schema.columns c "
        "JOIN information_schema.tables t ON t.table_schema=c.table_schema AND t.table_name=c.table_name "
        f"WHERE c.table_schema='{schema}' AND t.table_type='BASE TABLE' ORDER BY c.table_name, c.ordinal_position"
    ):
        out.setdefault(t, []).append((c, dt, ct))
    return out


def foreign_keys(schema):
    return rows(
        "SELECT table_name, column_name, referenced_table_schema, referenced_table_name, referenced_column_name "
        f"FROM information_schema.key_column_usage WHERE table_schema='{schema}' AND referenced_table_name IS NOT NULL"
    )


IRREGULAR = {"Person": "people", "Programme": "programmes"}
RAILS_TABLE = {"Git::Repository": "git_repositories", "Git::Version": "git_versions",
               "Git::Annotation": "git_annotations"}


def _underscore(name):
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return re.sub(r"([a-z\d])([A-Z])", r"\1_\2", s).lower()


def _plural(word):
    if word.endswith("y") and not word.endswith(("ay", "ey", "oy", "uy")):
        return word[:-1] + "ies"
    if word.endswith(("s", "x", "ch", "sh")):
        return word + "es"
    return word + "s"


def rails_table(cls):
    """Rails class name -> SEEK table (tableize), e.g. DataFile::Version -> data_file_versions."""
    if cls in RAILS_TABLE:
        return RAILS_TABLE[cls]
    if cls in IRREGULAR:
        return IRREGULAR[cls]
    if cls.endswith("::Version"):
        return _underscore(cls[: -len("::Version")]) + "_versions"
    return _plural(_underscore(cls.replace("::", "")))


def is_word_needle(n):
    """Short all-caps needles (e.g. a consortium acronym) match as a word: DNA barcodes like GTCGAG hold TCGA."""
    return n.isalnum() and n.upper() == n and len(n) <= 6


if __name__ == "__main__":
    for cls, want in [("DataFile", "data_files"), ("DataFile::Version", "data_file_versions"), ("Person", "people"),
                      ("Study", "studies"), ("WorkGroup", "work_groups"), ("FavouriteGroup", "favourite_groups"),
                      ("SampleType", "sample_types"), ("Sop::Version", "sop_versions"),
                      ("HumanDisease", "human_diseases"), ("TextValue", "text_values")]:
        assert rails_table(cls) == want, (cls, rails_table(cls))
    assert is_word_needle("TCGA") and not is_word_needle("Tool Check") and not is_word_needle("someone")
    print("seedlib ok")

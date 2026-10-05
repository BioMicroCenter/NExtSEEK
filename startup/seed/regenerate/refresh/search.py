"""Leftover search, not joins: scan every text-like column of every table for each needle (case-insensitive).

Needles live in <work schema>.needles(needle, kind), captured by `search.py capture` BEFORE the deletes (the people
they come from are deleted). Kinds: hard = must be 0 after the run (TCGA, Tool Check, the operator, every deleted
account's login and email); info = reported only (deleted people's full names, which can legitimately appear as
sample metadata such as a Scientist attribute).
Usage: search.py capture ; search.py scan OUT.json
"""
import json
import os
import sys

import seedlib as s

W = s.WORK
TITLE_NEEDLES = [t for t in os.environ.get("TARGETS", "").split(",") + os.environ.get("PROJECT_NEEDLES", "").split(",") if t]
NEEDLES = TITLE_NEEDLES + [t for t in os.environ.get("EXTRA_NEEDLES", "").split(",") if t]
TEXT = ("char", "varchar", "text", "tinytext", "mediumtext", "longtext", "json", "enum", "set")
KEEP = tuple(os.environ.get("KEEP_LOGINS", "demo,user").split(","))
# multi-MB text values (chat transcripts, sample JSON) outrun MySQL's default regex step and stack limits
#   (global-only variables; these are throwaway servers)
LIMITS = "SET GLOBAL regexp_time_limit = 2000000000; SET GLOBAL regexp_stack_limit = 2000000000;"


def match_sql(expr, n):
    """Short all-caps needles as a word (seedlib.is_word_needle); every other needle a substring, case-insensitive."""
    if s.is_word_needle(n):
        return f"({expr} REGEXP '(^|[^A-Za-z]){n}([^A-Za-z]|$)')"
    esc = n.replace("\\", "\\\\").replace("'", "''").replace("%", "\\%").replace("_", "\\_")
    return f"({expr} LIKE '%{esc}%')"


def capture():
    s.run_sql(f"CREATE TABLE IF NOT EXISTS `{W}`.needles (needle VARCHAR(255) PRIMARY KEY, kind VARCHAR(8))")
    keep = ",".join(f"'{k}'" for k in KEEP)
    q = [
        # what was removed by title, plus EXTRA_NEEDLES (e.g. a removed account's name fragments; keep them
        # out of git: pass them in the environment)
        *[f"SELECT '{n.replace(chr(39), chr(39) * 2)}', 'hard'" for n in NEEDLES],
        f"SELECT login, 'hard' FROM seek_production.users WHERE login NOT IN ({keep}) AND CHAR_LENGTH(login) >= 4",
        f"SELECT email, 'hard' FROM seek_production.people WHERE email LIKE '%@%' AND id NOT IN (SELECT person_id FROM seek_production.users WHERE login IN ({keep}) AND person_id IS NOT NULL)",
        f"SELECT username, 'hard' FROM dmac.auth_user WHERE username NOT IN ({keep}) AND CHAR_LENGTH(username) >= 4",
        f"SELECT email, 'hard' FROM dmac.auth_user WHERE email LIKE '%@%' AND username NOT IN ({keep})",
        f"SELECT CONCAT(first_name, ' ', last_name), 'info' FROM seek_production.people WHERE CHAR_LENGTH(first_name) > 1 AND CHAR_LENGTH(last_name) > 1 AND id NOT IN (SELECT person_id FROM seek_production.users WHERE login IN ({keep}) AND person_id IS NOT NULL)",
    ]
    for sql in q:
        s.run_sql(f"INSERT IGNORE INTO `{W}`.needles {sql}")
    # an email kept by demo/user (e.g. a shared mailbox) is not a leftover
    s.run_sql(f"DELETE FROM `{W}`.needles WHERE needle IN (SELECT email FROM seek_production.people WHERE id IN (SELECT person_id FROM seek_production.users WHERE login IN ({keep})))")
    print(s.rows(f"SELECT kind, COUNT(*) FROM `{W}`.needles GROUP BY kind"))


def scan(out):
    needles = s.rows(f"SELECT needle, kind FROM `{W}`.needles ORDER BY kind, needle")
    hits = {}
    for schema in ("seek_production", "dmac"):
        for t, cols in s.columns(schema).items():
            tc = [c for c, dt, _ in cols if dt in TEXT]
            if not tc:
                continue
            blob = "CONCAT_WS(' ', " + ", ".join(f"CAST(`{c}` AS CHAR)" for c in tc) + ")"
            sums = ", ".join(f"SUM({match_sql('x.b', n)})" for n, _ in needles)
            r = s.rows(f"{LIMITS} SELECT {sums} FROM (SELECT {blob} AS b FROM `{schema}`.`{t}`) x")
            for (n, kind), v in zip(needles, r[0] if r else []):
                if v not in ("0", "NULL"):
                    per_col = {}
                    for c in tc:
                        cnt = s.scalar(f"{LIMITS} SELECT COUNT(*) FROM `{schema}`.`{t}` WHERE {match_sql(f'CAST(`{c}` AS CHAR)', n)}")
                        if cnt != "0":
                            per_col[c] = int(cnt)
                    hits.setdefault(kind, {}).setdefault(n, {})[f"{schema}.{t}"] = per_col
    json.dump(hits, open(out, "w"), indent=1, sort_keys=True)
    for kind in ("hard", "info"):
        print(f"== {kind}: {sum(len(v) for v in hits.get(kind, {}).values())} table hits over {len(hits.get(kind, {}))} needles")
        for n, tabs in sorted(hits.get(kind, {}).items()):
            label = n if n in TITLE_NEEDLES else f"<{kind} needle #{[x for x, _ in needles].index(n)}>"
            print(" ", label, {t: c for t, c in tabs.items()})


if __name__ == "__main__":
    capture() if sys.argv[1] == "capture" else scan(sys.argv[2])

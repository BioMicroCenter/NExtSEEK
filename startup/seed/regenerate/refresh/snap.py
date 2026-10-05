"""Snapshot: exact row counts per table + schema-only dump of seek_production and dmac.
Usage: snap.py PREFIX   -> PREFIX.counts.json, PREFIX.schema.sql     (container from env C)
       snap.py --diff A B   -> per-table before/after/deleted + schema byte comparison
"""
import hashlib
import json
import subprocess
import sys

import seedlib as s

SCHEMAS = ("seek_production", "dmac")


def snap(prefix):
    counts = {}
    for schema in SCHEMAS:
        tables = [r[0] for r in s.rows(
            f"SELECT table_name FROM information_schema.tables WHERE table_schema='{schema}' AND table_type='BASE TABLE' ORDER BY table_name")]
        sql = "\n".join(f"SELECT '{t}', COUNT(*) FROM `{schema}`.`{t}`;" for t in tables)
        for t, n in s.rows(sql):
            counts[f"{schema}.{t}"] = int(n)
    json.dump(counts, open(prefix + ".counts.json", "w"), indent=1, sort_keys=True)
    out = subprocess.run(
        ["docker", "exec", "-e", f"MYSQL_PWD={s.PW}", s.C, "mysqldump", "-uroot", "--no-data", "--skip-dump-date",
         "--routines", "--triggers", "--databases", *SCHEMAS], capture_output=True, check=True).stdout
    open(prefix + ".schema.sql", "wb").write(out)
    info = {}
    for schema in SCHEMAS:
        info[schema] = {
            "tables": s.rows(f"SELECT table_name, engine, table_collation FROM information_schema.tables WHERE table_schema='{schema}' ORDER BY 1"),
            "columns": s.rows(f"SELECT table_name, column_name, ordinal_position, column_type, is_nullable, COALESCE(column_default,'<null>'), COALESCE(character_set_name,''), COALESCE(collation_name,''), extra FROM information_schema.columns WHERE table_schema='{schema}' ORDER BY 1,3"),
            "indexes": s.rows(f"SELECT table_name, index_name, seq_in_index, column_name, non_unique, index_type FROM information_schema.statistics WHERE table_schema='{schema}' ORDER BY 1,2,3"),
            "routines": s.rows(f"SELECT routine_name, routine_type FROM information_schema.routines WHERE routine_schema='{schema}' ORDER BY 1"),
            "triggers": s.rows(f"SELECT trigger_name FROM information_schema.triggers WHERE trigger_schema='{schema}' ORDER BY 1"),
        }
    json.dump(info, open(prefix + ".schema_info.json", "w"), indent=0, sort_keys=True)
    print(f"{prefix}: {len(counts)} tables, {sum(counts.values())} rows, schema sha256 {hashlib.sha256(out).hexdigest()[:16]}")


def diff(a, b):
    ca, cb = json.load(open(a + ".counts.json")), json.load(open(b + ".counts.json"))
    sa, sb = open(a + ".schema.sql", "rb").read(), open(b + ".schema.sql", "rb").read()
    print("schema dump byte-identical:", sa == sb, hashlib.sha256(sa).hexdigest()[:16], hashlib.sha256(sb).hexdigest()[:16])
    ia, ib = json.load(open(a + ".schema_info.json")), json.load(open(b + ".schema_info.json"))
    for schema in SCHEMAS:
        for part in ia[schema]:
            same = ia[schema][part] == ib[schema][part]
            if not same:
                x, y = {tuple(r) for r in ia[schema][part]}, {tuple(r) for r in ib[schema][part]}
                print(f"  {schema}.{part}: DIFFERS  only-before={sorted(x - y)[:5]} only-after={sorted(y - x)[:5]}")
    print("schema semantically identical (information_schema):", ia == ib)
    print(f"{'table':55s} {'before':>10s} {'after':>10s} {'deleted':>10s}")
    for k in sorted(set(ca) | set(cb)):
        x, y = ca.get(k), cb.get(k)
        if x != y:
            print(f"{k:55s} {x!s:>10s} {y!s:>10s} {(x or 0) - (y or 0):>10d}")
    print("totals", sum(ca.values()), sum(cb.values()), sum(ca.values()) - sum(cb.values()))


if __name__ == "__main__":
    diff(sys.argv[2], sys.argv[3]) if sys.argv[1] == "--diff" else snap(sys.argv[1])

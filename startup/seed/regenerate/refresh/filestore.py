"""Rebuild the SEEK filestore snapshot to match a cleaned seek_production (container from env C).

Keeps: assets/<uuid>.dat and converted-assets/<uuid>.* of surviving content_blobs; avatars/<id>.* of surviving
avatars; model_images/<id>.* of surviving model_images; rdf/{public,private}/<Type>-production-<id>.rdf of surviving
items; the bare directory entries. Drops everything else, notably tmp/ (caches, incl. a dump of every person) and,
unless KEEP_KEYS=1, secret_key_base/ and attr_encrypted/ (SEEK writes fresh keys on first boot).
Usage: fs_clean.py IN.tar.gz OUT.tar.gz REPORT.json
"""
import collections
import json
import os
import re
import sys
import tarfile

import seedlib as s

KEEP_KEYS = os.environ.get("KEEP_KEYS") == "1"


def ids(table):
    return {r[0] for r in s.rows(f"SELECT id FROM seek_production.`{table}`")}


def main(src, dst, report):
    blobs = {r[0] for r in s.rows("SELECT uuid FROM seek_production.content_blobs WHERE uuid IS NOT NULL")}
    avatars = ids("avatars")
    model_images = ids("model_images")
    table_ids = {}
    stats = collections.Counter()
    unknown = collections.Counter()
    kept_assets = set()

    def keep(name):
        p = name[2:] if name.startswith("./") else name
        parts = p.split("/")
        top = parts[0]
        if len(parts) == 1 and not p.endswith("/") and p != "":
            unknown[p] += 1  # a loose file at the filestore root (a key, a dump): never shipped unseen
            return False, "unknown:" + top
        if len(parts) == 1 or p.endswith("/") or p == "":
            return top not in ("tmp",) and (KEEP_KEYS or top not in ("secret_key_base", "attr_encrypted")), "dir"
        base = parts[-1]
        if top == "assets":
            return base.split(".")[0] in blobs, top
        if top == "converted-assets":
            return base.split(".")[0] in blobs, top
        if top == "avatars" and len(parts) == 2:
            return base.split(".")[0] in avatars, top
        if top == "model_images" and len(parts) == 2:
            return base.split(".")[0] in model_images, top
        if top == "rdf":
            m = re.match(r"^([A-Za-z:]+)-[a-z]+-(\d+)\.rdf$", base)
            if not m:
                unknown[p] += 1
                return False, "rdf-unparsed"
            tab = s.rails_table(m.group(1).replace("::", ""))
            if tab not in table_ids:
                try:
                    table_ids[tab] = ids(tab)
                except RuntimeError:
                    table_ids[tab] = set()
            return m.group(2) in table_ids[tab], "rdf"
        if top in ("secret_key_base", "attr_encrypted"):
            return KEEP_KEYS, top
        if top == "tmp":
            return False, top
        unknown[p] += 1
        return False, "unknown:" + top

    with tarfile.open(src, "r|gz") as tin, tarfile.open(dst, "w:gz", format=tarfile.GNU_FORMAT) as tout:
        for m in tin:
            ok, kind = keep(m.name + ("/" if m.isdir() and not m.name.endswith("/") else ""))
            stats[f"{'kept' if ok else 'dropped'} {kind}"] += 1
            if ok:
                tout.addfile(m, tin.extractfile(m) if m.isfile() else None)
                if kind == "assets":
                    kept_assets.add(m.name.rsplit("/", 1)[-1].split(".")[0])
    without_file = sorted(blobs - kept_assets)
    print(f"surviving content_blobs without an assets/ file: {len(without_file)} (remote-URL blobs have none)")
    json.dump({"stats": dict(stats), "unknown_examples": list(unknown)[:50], "content_blobs_rows": len(blobs),
               "blobs_without_file": len(without_file),
               "keep_keys": KEEP_KEYS}, open(report, "w"), indent=1, sort_keys=True)
    for k, v in sorted(stats.items()):
        print(f"{v:>8}  {k}")


if __name__ == "__main__":
    main(*sys.argv[1:4])

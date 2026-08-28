#!/usr/bin/env python3
"""Generate reviewable MySQL for transferring SEEK publications dev -> prod.

Emits one transaction per publication. Ids are never carried across instances
(they are different id spaces on the two hosts); every foreign key is either
captured from LAST_INSERT_ID() or supplied as a parameter at the top of the file.

Tables written, in dependency order, per publication:
  policies -> permissions -> publications -> publication_versions
  -> projects_publication_versions -> publication_authors -> projects_publications
"""
from __future__ import annotations
import argparse, csv, pathlib, sys

ACCESS_ACCESSIBLE = 2   # Policy::ACCESSIBLE  -- API calls this "download"
ACCESS_MANAGING   = 4   # Policy::MANAGING    -- API calls this "manage"

PUB_COLS = ["pubmed_id","title","abstract","published_date","journal","first_letter",
            "contributor_id","created_at","updated_at","doi","uuid","policy_id","citation",
            "deleted_contributor","registered_mode","booktitle","publisher","editor",
            "publication_type_id","url","version","license","other_creators"]
# publication_versions mirrors publications minus license/other_creators
# (Publication declares sync_ignore_columns: ['license','other_creators']).
VER_COLS = ["publication_id","version","revision_comments","pubmed_id","title","abstract",
            "published_date","journal","first_letter","contributor_id","created_at","updated_at",
            "doi","uuid","policy_id","citation","deleted_contributor","registered_mode",
            "booktitle","publisher","editor","publication_type_id","url","visibility"]

def q(v):
    """MySQL literal. Empty string becomes NULL: the CSV export cannot represent
    NULL, and Rails wrote NULL (confirmed against the dev API, which does
    distinguish them)."""
    if v is None: return "NULL"
    s = str(v)
    if s == "": return "NULL"
    s = (s.replace("\\", "\\\\").replace("'", "\\'")
          .replace("\n", "\\n").replace("\r", "\\r").replace("\x1a", "\\Z"))
    return "'" + s + "'"

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pubs", required=True)
    ap.add_argument("--authors", required=True)
    ap.add_argument("--ids", help="comma-separated dev publication ids (default: all)")
    ap.add_argument("--exclude", help="comma-separated dev publication ids to skip")
    ap.add_argument("--project", help="PROD project id, applied to every publication")
    ap.add_argument("--project-map", help="CSV: dev_publication_id,prod_project_id[,doi,source]")
    ap.add_argument("--contributor", required=True, help="PROD person id")
    ap.add_argument("-o", "--out", required=True)
    a = ap.parse_args()

    if not (a.project or a.project_map):
        sys.exit("give --project or --project-map")
    pmap: dict[str, int] = {}
    psrc: dict[str, str] = {}
    if a.project_map:
        for r in csv.DictReader(open(a.project_map)):
            pmap[r["dev_publication_id"]] = int(r["prod_project_id"])
            psrc[r["dev_publication_id"]] = r.get("source", "")

    pubs = list(csv.DictReader(open(a.pubs)))
    authors = list(csv.DictReader(open(a.authors)))
    by_pub: dict[str, list[dict]] = {}
    for r in authors:
        by_pub.setdefault(r["publication_id"], []).append(r)
    for v in by_pub.values():
        v.sort(key=lambda r: int(r["author_index"] or 0))

    if a.ids:
        want = [i.strip() for i in a.ids.split(",")]
        sel = [p for p in pubs if p["id"] in want]
        missing = set(want) - {p["id"] for p in sel}
        if missing:
            sys.exit(f"dev publication ids not found in export: {sorted(missing)}")
    else:
        sel = pubs
    if a.exclude:
        drop = {i.strip() for i in a.exclude.split(",")}
        sel = [p for p in sel if p["id"] not in drop]

    L: list[str] = []
    add = L.append
    add("-- SEEK publication transfer: fairdata-dev -> fairdata (prod)")
    add(f"-- {len(sel)} publication(s). Generated from a dev MySQL export.")
    add("--")
    add("-- Dev and prod ids are DIFFERENT id spaces. Nothing below carries a dev id:")
    add("--   dev person 1 is 'Demo Demo'; prod person 1 is 'Jingzhi Zhu'.")
    add("--   dev project 1 is 'Published Data', which does not exist on prod.")
    add("-- Both are supplied explicitly here instead.")
    add("--")
    add("-- MUST be run with --default-character-set=utf8mb4. Without it the")
    add("-- client reads these UTF-8 bytes as latin1 and re-encodes them, turning")
    add("-- 'IFN-\u03b3' into 'IFN-\u00ce\u00b3'. 11 of the 52 publications contain Greek")
    add("-- letters, en-dashes, curly quotes or accents; 3 author rows do too.")
    add("--")
    add("-- Run against the seek_production schema on the prod host.")
    add("-- Afterwards, in the seek container:")
    add("--   bundle exec rake seek:repopulate_auth_lookup_tables RAILS_ENV=production")
    add("--   bundle exec rake seek:reindex_all RAILS_ENV=production")
    add("-- Until those run the records exist but are not listed or searchable.")
    add("")
    add(f"SET @contrib := {int(a.contributor)};   -- prod person id (contributor + manage permission)")
    if a.project and not pmap:
        add(f"SET @proj    := {int(a.project)};   -- prod project id (same for every publication)")
    add("")
    add("-- CHECK 1 -- who these publications will be attributed to on prod.")
    add("-- Read the name before running anything below. Dev's contributor was the")
    add("-- 'Demo Demo' account; prod person 1 is a real named researcher.")
    add("SELECT id, first_name, last_name FROM people WHERE id = @contrib;")
    add("")
    add("-- CHECK 2 -- which project(s) they will land in.")
    projids = sorted({pmap.get(p["id"], int(a.project or 0)) for p in sel})
    add(f"SELECT id, title FROM projects WHERE id IN ({', '.join(str(i) for i in projids)});")
    add("")
    add("-- CHECK 3 -- refuse to run twice. Should return 0 rows.")
    dois = ", ".join(q(p["doi"]) for p in sel)
    add(f"SELECT id, doi FROM publications WHERE doi IN ({dois});")
    add("")

    for p in sel:
        add(f"-- ---- dev id {p['id']}: {p['doi']} ({p['title'][:60]}) ----")
        if pmap:
            src = psrc.get(p["id"], "")
            note = f"   -- project mapping source: {src}" if src else ""
            if src.startswith("INFERRED"):
                note += "  <-- UNVERIFIED, confirm before running"
            add(f"SET @proj := {pmap[p['id']]};{note}")
        add("START TRANSACTION;")
        add("INSERT INTO policies (name, sharing_scope, access_type, use_allowlist, use_denylist, created_at, updated_at)")
        add(f"  VALUES (NULL, NULL, {ACCESS_ACCESSIBLE}, 0, 0, NOW(), NOW());")
        add("SET @pol := LAST_INSERT_ID();")
        add("INSERT INTO permissions (contributor_type, contributor_id, policy_id, access_type, created_at, updated_at)")
        add(f"  VALUES ('Person', @contrib, @pol, {ACCESS_MANAGING}, NOW(), NOW());")

        vals = []
        for c in PUB_COLS:
            if c == "policy_id":      vals.append("@pol")
            elif c == "contributor_id": vals.append("@contrib")
            else:                     vals.append(q(p.get(c)))
        add(f"INSERT INTO publications ({', '.join(PUB_COLS)})")
        add(f"  VALUES ({', '.join(vals)});")
        add("SET @pub := LAST_INSERT_ID();")

        vvals = []
        for c in VER_COLS:
            if c == "publication_id":   vvals.append("@pub")
            elif c == "policy_id":      vvals.append("@pol")
            elif c == "contributor_id": vvals.append("@contrib")
            elif c == "revision_comments": vvals.append("NULL")
            # 2 == :public, from Seek::ExplicitVersioning::VISIBILITY.
            # Rails sets this via a before_validation callback that a raw INSERT
            # bypasses. Left NULL, `visible?` returns nil and every page and API
            # read answers 403 "This version is not available".
            elif c == "visibility":     vvals.append("2")
            else:                       vvals.append(q(p.get(c)))
        add(f"INSERT INTO publication_versions ({', '.join(VER_COLS)})")
        add(f"  VALUES ({', '.join(vvals)});")
        add("SET @ver := LAST_INSERT_ID();")
        add("INSERT INTO projects_publication_versions (project_id, version_id) VALUES (@proj, @ver);")

        auths = by_pub.get(p["id"], [])
        if auths:
            rows = ",\n  ".join(
                f"({q(x['first_name'])}, {q(x['last_name'])}, @pub, {q(x['created_at'])}, "
                f"{q(x['updated_at'])}, {int(x['author_index'] or 0)}, NULL)"
                for x in auths)
            add("INSERT INTO publication_authors (first_name, last_name, publication_id, created_at, updated_at, author_index, person_id) VALUES")
            add("  " + rows + ";")
        else:
            add("-- no authors in the dev export for this publication")

        add("INSERT INTO projects_publications (project_id, publication_id) VALUES (@proj, @pub);")
        add("COMMIT;")
        add(f"SELECT @pub AS new_publication_id, {q(p['doi'])} AS doi;")
        add("")

    add("-- Verification (expect one row per publication above):")
    add("SELECT p.id, p.doi, p.pubmed_id, p.policy_id, p.contributor_id,")
    add("       (SELECT COUNT(*) FROM publication_authors a WHERE a.publication_id = p.id) AS authors,")
    add("       (SELECT COUNT(*) FROM projects_publications j WHERE j.publication_id = p.id) AS projects,")
    add("       (SELECT COUNT(*) FROM publication_versions v WHERE v.publication_id = p.id) AS versions")
    add(f"  FROM publications p WHERE p.doi IN ({dois});")
    add("")

    pathlib.Path(a.out).write_text("\n".join(L) + "\n")
    print(f"wrote {a.out}: {len(sel)} publication(s), "
          f"{sum(len(by_pub.get(p['id'], [])) for p in sel)} author rows")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

"""Orphan scan: for every reference column (FK, *_id by name, *_type/*_id polymorphic pair) count rows whose target
row is missing. Usage: orphans.py OUT.json   (container from env C). Compare two runs with orphans.py --diff A B.
"""
import json
import sys

import seedlib as s

SEEK, DMAC = "seek_production", "dmac"

# column -> target table (same schema unless "schema.table"). None = not a row reference (external id, enum).
SEEK_MAP = {
    "contributor_id": "people", "creator_id": "people", "person_id": "people", "sender_id": "people",
    "user_id": "users", "resource_owner_id": "users", "version_creator_id": "users",
    "policy_id": "policies", "default_policy_id": "policies",
    "linked_sample_type_id": "sample_types", "originating_data_file_id": "data_files",
    "recommended_environment_id": "recommended_model_environments", "parent_attribute_id": "template_attributes",
    "linked_extended_metadata_type_id": "extended_metadata_types", "attribute_id": "annotation_attributes",
    "ancestor_id": "strains", "descendant_id": "strains", "provider_id": None,
    "role_type_id": None, "status_id": None, "ontology_id": None, "pubmed_id": None, "doid_id": None,
    "zenodo_deposition_id": None, "space_perm_id": None, "session_id": None, "sabiork_id": None, "chebi_id": None,
    "kegg_id": None, "bio_tools_id": None, "application_id": "oauth_applications",
}
SELF_PARENT = {"project_folders", "suggested_assay_types", "suggested_technology_types", "templates", "strains"}
DMAC_MAP = {
    "user_id": "auth_user", "actor_django_user_id": "auth_user", "cancellation_actor_django_user_id": "auth_user",
    "actor_seek_person_id": "seek_production.people", "cancellation_actor_seek_person_id": "seek_production.people",
    "assay_id": "seek_production.assays", "internal_assay_id": "internal_assays", "clade_id": "clades",
    "sample_type_id": "seek_production.sample_types", "sampletype_id": "seek_production.sample_types",
    "project_id": "seek_production.projects", "sample_id": "seek_production.samples",
}


def plural_guess(col, tables):
    base = col[:-3]
    for cand in (base + "s", base + "es", base[:-1] + "ies" if base.endswith("y") else None, base):
        if cand and cand in tables:
            return cand
    return None


def checks(schema, cols, fks):
    """Yield (key, table, column, target_schema, target_table, poly_type_or_None)."""
    tables = set(cols)
    fk_cols = {(t, c): (rs, rt, rc) for t, c, rs, rt, rc in fks}
    unmapped = []
    out = []
    for t, clist in cols.items():
        names = [c for c, _, _ in clist]
        for c in names:
            if (t, c) in fk_cols:
                rs, rt, rc = fk_cols[(t, c)]
                out.append((t, c, rs, rt, ("@pk", rc)))
                continue
            if c.endswith("_type") and c[:-5] + "_id" in names:
                idc = c[:-5] + "_id"
                for (typ,) in s.rows(f"SELECT DISTINCT `{c}` FROM `{t}` WHERE `{c}` IS NOT NULL AND `{idc}` IS NOT NULL", schema):
                    tgt = s.rails_table(typ)
                    if tgt in tables:
                        out.append((t, idc, schema, tgt, (c, typ)))
                    else:
                        unmapped.append(f"{t}.{idc} type={typ}")
                continue
            if not c.endswith("_id") or c == "id":
                continue
            if c[:-3] + "_type" in names:
                continue  # handled as poly
            m = (DMAC_MAP if schema == DMAC else SEEK_MAP)
            if schema == DMAC and c not in m and c in SEEK_MAP:
                m = SEEK_MAP  # dmac's SEEK mirror tables use SEEK's names, inside dmac
            if c in m:
                tgt = m[c]
                if tgt is None:
                    continue
                rs, rt = (tgt.split(".") if "." in tgt else (schema, tgt))
                out.append((t, c, rs, rt, None))
                continue
            if c == "parent_id" and t in SELF_PARENT:
                out.append((t, c, schema, t, None)); continue
            if c == "asset_id" and t.endswith("_auth_lookup"):
                out.append((t, c, schema, plural_guess(t[: -len("_auth_lookup")] + "_id", tables), None)); continue
            if c == "version_id" and t.endswith("_versions_projects"):
                out.append((t, c, schema, t[: -len("_projects")], None)); continue
            if c == "version_id" and t.startswith("projects_") and t.endswith("_versions"):
                out.append((t, c, schema, t[len("projects_"):], None)); continue
            if c in ("task_id", "object_id", "remote_id", "field_id", "assay_stream_id", "forum_id", "variable_id", "repo_schema_id") or (c == "session_id" and t in ("session_state", "assistant_chat_session")):
                continue  # own ids, generic object ids, or references to tables SEEK does not have
            tgt = plural_guess(c, tables)
            if tgt:
                out.append((t, c, schema, tgt, None))
            else:
                unmapped.append(f"{t}.{c}")
    return out, unmapped


def scan():
    result, unmapped_all = {}, []
    for schema in (SEEK, DMAC):
        cols = s.columns(schema)
        fks = s.foreign_keys(schema)
        lst, unmapped = checks(schema, cols, fks)
        unmapped_all += [f"{schema}.{u}" for u in unmapped]
        sqls = []
        for t, c, rs, rt, poly in lst:
            if rt is None:
                unmapped_all.append(f"{schema}.{t}.{c} (no target)"); continue
            pk = "id"
            if poly and poly[0] == "@pk":
                pk, poly = poly[1], None
            where = f"x.`{c}` IS NOT NULL AND x.`{c}` <> 0" if c not in ("session_id",) else f"x.`{c}` IS NOT NULL"
            if poly:
                where += f" AND x.`{poly[0]}` = '{poly[1]}'"
            key = f"{schema}.{t}.{c}" + (f"[{poly[1]}]" if poly else "") + f"->{rs}.{rt}"
            sqls.append((key, f"SELECT COUNT(*) FROM `{schema}`.`{t}` x LEFT JOIN `{rs}`.`{rt}` g ON g.`{pk}` = x.`{c}` WHERE {where} AND g.`{pk}` IS NULL"))
        for key, q in sqls:
            try:
                result[key] = int(s.scalar(q))
            except Exception as e:  # report, never hide
                result[key] = f"ERROR {str(e)[:200]}"
    return result, sorted(set(unmapped_all))


if __name__ == "__main__":
    if sys.argv[1] == "--diff":
        a, b = json.load(open(sys.argv[2])), json.load(open(sys.argv[3]))
        for k in sorted(set(a["orphans"]) | set(b["orphans"])):
            va, vb = a["orphans"].get(k, "-"), b["orphans"].get(k, "-")
            if va != vb:
                print(f"{k}: before={va} after={vb}")
        sys.exit(0)
    res, unmapped = scan()
    json.dump({"orphans": res, "unmapped": unmapped}, open(sys.argv[1], "w"), indent=1, sort_keys=True)
    bad = {k: v for k, v in res.items() if v}
    print(f"{len(res)} reference checks, {len(bad)} with orphans, {len(unmapped)} unmapped columns")
    for k, v in sorted(bad.items()):
        print(" ", k, v)
    print("unmapped:", ", ".join(unmapped))

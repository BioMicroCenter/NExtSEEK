"""Build the kill sets in the work schema (seedlib.WORK), then (execute) DELETE every killed row.

Roots: target investigations by title (TARGETS), target projects (PROJECT_REGEX), sample types by title
(SAMPLE_TYPES), every SEEK user/person and Django user not in KEEP_LOGINS, all login state, chat history, queued
jobs and graph-sync history (ALL_ROWS), encrypted SEEK settings, rows that exist only for an account already gone,
activity-log rows whose text names a search needle. Everything else is a cascade to a fixpoint: a row whose reference
column points at a killed row is killed, except the soft links in NO_CASCADE (contributor and catalog references).
Assets (SOPs, data files, ...) linked only to killed ISA items are killed by the asset rule; policies go when only
killed rows used them. Running build again on a cleaned copy finds only what is new (a second pass).

Usage: kill.py build | kill.py execute | kill.py report   (settings from the environment, see ../README.md)
Writes only to the work schema (kill tables) and, with execute, DELETEs from seek_production / dmac.
"""
import json
import os
import sys

import orphans
import seedlib as s

W = s.WORK
SEEK, DMAC = "seek_production", "dmac"
KEEP_LOGINS = tuple(os.environ.get("KEEP_LOGINS", "demo,user").split(","))
TARGETS = [t for t in os.environ.get("TARGETS", "").split(",") if t]  # investigation titles to remove
PROJECT_REGEX = os.environ.get("PROJECT_REGEX", "")  # projects to remove, by title; empty = none
SAMPLE_TYPES = [t for t in os.environ.get("SAMPLE_TYPES", "").split(",") if t]  # sample type titles to remove
BATCH = 20000


def lit(v):
    """A MySQL string literal's body: backslashes and quotes doubled, so a regex like ^TCGA\\. keeps its meaning."""
    return v.replace("\\", "\\\\").replace("'", "''")

# Soft links: the referencing row survives when its target dies (it keeps a dangling contributor; UPDATE is not part of the method), or the
# reference is "owned by" the other side (policies, avatars, catalog).
NO_CASCADE = {
    "contributor_id", "version_creator_id", "policy_id", "default_policy_id", "avatar_id", "template_id",
    "file_template_id", "originating_data_file_id", "sample_type_id", "linked_sample_type_id",
    "sample_controlled_vocab_id", "sampletype_id", "provider_id", "parent_id", "model_image_id",
    "linked_extended_metadata_type_id", "parent_attribute_id", "template_attribute_id",
}
CASCADE_ANYWAY = {  # owned by the other side after all
    ("permissions", "policy_id"),  # a permission belongs to its policy
    # a removed sample type takes its definitions and links with it
    ("sample_attributes", "sample_type_id"), ("projects_sample_types", "sample_type_id"),
    ("sample_types_studies", "sample_type_id"), ("sample_types_clades", "sample_type_id"),
    ("sample_types_context", "sampletype_id"), ("attributes_mutation_partition", "sample_type_id"),
}
ALL_ROWS = {  # source-box runtime state: every row goes, whoever it belongs to
    SEEK: ["sessions", "api_tokens", "identities", "oauth_sessions", "oauth_access_grants", "oauth_access_tokens",
           "delayed_jobs"],  # the box's job queue (mails to removed users, jobs on removed rows)
    DMAC: ["assistant_chat_session", "assistant_query_task", "assistant_cc_transcript", "assistant_turn_ledger",
           "assistant_cc_turn", "eval_turn_judgment", "session_state", "django_session", "authtoken_token",
           "graph_sync_run", "graph_sync_outbox"],  # the box's sync history: a new install has not synced yet
}
ASSET_TYPES = ["DataFile", "Sop", "Model", "Document", "Presentation", "Publication", "Collection", "Workflow",
               "Placeholder", "FileTemplate", "Strain", "Event"]


def kt(schema, table):
    return f"`{W}`.`k__{'s' if schema == SEEK else 'd'}__{table}`"


def pk_info():
    """{(schema, table): (pk_col, column_type)} for single-column primary keys."""
    out = {}
    r = s.rows(
        "SELECT k.table_schema, k.table_name, k.column_name, c.column_type FROM information_schema.key_column_usage k "
        "JOIN information_schema.columns c ON c.table_schema=k.table_schema AND c.table_name=k.table_name AND c.column_name=k.column_name "
        f"WHERE k.constraint_name='PRIMARY' AND k.table_schema IN ('{SEEK}','{DMAC}')")
    cnt = {}
    for sc, t, c, ct in r:
        cnt[(sc, t)] = cnt.get((sc, t), 0) + 1
        out[(sc, t)] = (c, ct)
    return {k: v for k, v in out.items() if cnt[k] == 1}


def refs():
    """All reference checks of both schemas: (schema, table, col, rschema, rtable, rcol, poly)."""
    out = []
    for schema in (SEEK, DMAC):
        lst, _ = orphans.checks(schema, s.columns(schema), s.foreign_keys(schema))
        for t, c, rs, rt, poly in lst:
            if rt is None:
                continue
            rc = "id"
            if poly and poly[0] == "@pk":
                rc, poly = poly[1], None
            out.append((schema, t, c, rs, rt, rc, poly))
    return out


def ensure_kill_table(pks, schema, table):
    pkc, ctype = pks[(schema, table)]
    s.run_sql(f"CREATE TABLE IF NOT EXISTS {kt(schema, table)} (id {ctype.replace(' unsigned', '')} {'UNSIGNED' if 'unsigned' in ctype else ''}{' COLLATE utf8mb4_bin' if 'char' in ctype else ''} PRIMARY KEY, why VARCHAR(64))")


def add(pks, schema, table, select_sql, why):
    """Insert the pk values returned by select_sql (one column) into the table's kill set; return rows added."""
    ensure_kill_table(pks, schema, table)
    out = s.run_sql(f"INSERT IGNORE INTO {kt(schema, table)} (id, why) SELECT q.v, '{why[:60]}' FROM ({select_sql}) q(v); SELECT ROW_COUNT();")
    return int(out.strip().split("\n")[-1])


def build():
    s.run_sql(f"CREATE DATABASE IF NOT EXISTS `{W}`")  # the work schema only, never dumped
    for name in kill_tables():  # a rebuild starts from empty kill sets; needles stay
        s.run_sql(f"DROP TABLE `{W}`.`{name}`")
    pks = pk_info()
    log = {}

    def root(schema, table, sql, why):
        n = add(pks, schema, table, sql, why)
        log[f"root {schema}.{table} [{why}]"] = n

    # --- roots ---
    if not (TARGETS or PROJECT_REGEX or SAMPLE_TYPES):
        raise SystemExit("set TARGETS (investigation titles), PROJECT_REGEX and/or SAMPLE_TYPES")
    root(SEEK, "projects", f"SELECT id FROM {SEEK}.projects WHERE {'FALSE' if not PROJECT_REGEX else f'title REGEXP \'{lit(PROJECT_REGEX)}\''}", "target project")
    titles = ",".join(f"'{lit(t)}'" for t in TARGETS) or "NULL"
    root(SEEK, "investigations", f"SELECT id FROM {SEEK}.investigations WHERE BINARY title IN ({titles})", "target investigation")
    root(SEEK, "investigations", f"SELECT ip.investigation_id FROM {SEEK}.investigations_projects ip GROUP BY ip.investigation_id "
         f"HAVING SUM(ip.project_id NOT IN (SELECT id FROM {kt(SEEK, 'projects')})) = 0", "only in killed projects")
    root(SEEK, "studies", f"SELECT id FROM {SEEK}.studies WHERE investigation_id IN (SELECT id FROM {kt(SEEK, 'investigations')})", "in killed investigation")
    root(SEEK, "assays", f"SELECT id FROM {SEEK}.assays WHERE study_id IN (SELECT id FROM {kt(SEEK, 'studies')})", "in killed study")
    root(SEEK, "samples",
         f"SELECT aa.asset_id FROM {SEEK}.assay_assets aa WHERE aa.asset_type='Sample' GROUP BY aa.asset_id "
         f"HAVING SUM(aa.assay_id IN (SELECT id FROM {kt(SEEK, 'assays')})) > 0 "
         f"AND SUM(aa.assay_id NOT IN (SELECT id FROM {kt(SEEK, 'assays')})) = 0", "only in killed assays")
    root(SEEK, "samples", f"SELECT ps.sample_id FROM {SEEK}.projects_samples ps GROUP BY ps.sample_id "
         f"HAVING SUM(ps.project_id NOT IN (SELECT id FROM {kt(SEEK, 'projects')})) = 0", "only in killed projects")
    mixed = s.scalar(
        f"SELECT COUNT(DISTINCT aa.asset_id) FROM {SEEK}.assay_assets aa WHERE aa.asset_type='Sample' "
        f"AND aa.asset_id IN (SELECT asset_id FROM {SEEK}.assay_assets WHERE asset_type='Sample' AND assay_id IN (SELECT id FROM {kt(SEEK, 'assays')})) "
        f"AND aa.assay_id NOT IN (SELECT id FROM {kt(SEEK, 'assays')})")
    log["STOP-CHECK samples in a killed AND a kept assay"] = int(mixed)
    keep = ",".join(f"'{lit(x)}'" for x in KEEP_LOGINS)
    root(SEEK, "users", f"SELECT id FROM {SEEK}.users WHERE login NOT IN ({keep}) OR login IS NULL", "not in KEEP_LOGINS")
    root(SEEK, "people", f"SELECT id FROM {SEEK}.people WHERE id NOT IN (SELECT person_id FROM {SEEK}.users WHERE login IN ({keep}) AND person_id IS NOT NULL)", "person not in KEEP_LOGINS")
    st_titles = ",".join(f"'{lit(t)}'" for t in SAMPLE_TYPES) or "NULL"
    root(SEEK, "sample_types", f"SELECT id FROM {SEEK}.sample_types WHERE BINARY title IN ({st_titles})", "target sample type")
    if "attributes_mutation_partition" in set(s.columns(DMAC)):  # a job that touched a removed type goes whole
        root(DMAC, "attributes_mutation_job", f"SELECT DISTINCT job_id FROM {DMAC}.attributes_mutation_partition "
             f"WHERE sample_type_id IN (SELECT id FROM {kt(SEEK, 'sample_types')})", "job on a removed sample type")
    for t in ALL_ROWS[SEEK]:
        root(SEEK, t, f"SELECT id FROM {SEEK}.`{t}`", "runtime state, all rows")
    if s.scalar(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{W}' AND table_name='needles'") == "1":
        # a log row whose text names a removed thing or account exists only to record it
        root(SEEK, "activity_logs", f"SELECT a.id FROM {SEEK}.activity_logs a JOIN `{W}`.needles n "
             f"ON a.data LIKE CONCAT('%', REPLACE(REPLACE(n.needle, '%', '\\\\%'), '_', '\\\\_'), '%') "
             f"AND NOT (n.needle REGEXP '^[A-Z0-9]{{1,6}}$')", "log text names a needle")
    root(SEEK, "settings", f"SELECT id FROM {SEEK}.settings WHERE encrypted_value IS NOT NULL", "encrypted secret")
    root(DMAC, "auth_user", f"SELECT id FROM {DMAC}.auth_user WHERE username NOT IN ({keep})", "not in KEEP_LOGINS")
    for t in ALL_ROWS[DMAC]:
        if (DMAC, t) in pks:
            c = pks[(DMAC, t)][0]
            root(DMAC, t, f"SELECT `{c}` FROM {DMAC}.`{t}`", "runtime state, all rows")
    dmac_tables = set(s.columns(DMAC))
    if "graph_sync_outbox" in dmac_tables:  # outbox rows whose every payload sample id dies
        root(DMAC, "graph_sync_outbox",
             f"SELECT o.id FROM {DMAC}.graph_sync_outbox o, JSON_TABLE(o.payload, '$[*]' COLUMNS (v BIGINT PATH '$')) j "
             f"WHERE o.kind = 'samples' AND JSON_TYPE(o.payload) = 'ARRAY' GROUP BY o.id "
             f"HAVING SUM(j.v IN (SELECT id FROM {kt(SEEK, 'samples')})) = COUNT(*)", "outbox: only killed samples")
    if "projects_context" in dmac_tables and (DMAC, "projects_context") in pks:
        c = pks[(DMAC, "projects_context")][0]
        root(DMAC, "projects_context", f"SELECT `{c}` FROM {DMAC}.projects_context WHERE {'FALSE' if not PROJECT_REGEX else f'name REGEXP \'{lit(PROJECT_REGEX)}\''} "
             f"OR project_id IN (SELECT id FROM {kt(SEEK, 'projects')})", "context row of a removed project")

    # rows that exist only for an account that is ALREADY gone (pre-existing orphans to people/users)
    for schema, t, c, rs, rt, rc, poly in refs():
        if rt not in ("people", "users", "auth_user") or c in NO_CASCADE or (schema, t) not in pks:
            continue
        pkc = pks[(schema, t)][0]
        where = f" AND x.`{poly[0]}`='{poly[1]}'" if poly else ""
        n = add(pks, schema, t, f"SELECT x.`{pkc}` FROM `{schema}`.`{t}` x LEFT JOIN `{rs}`.`{rt}` g ON g.`{rc}` = x.`{c}` "
                f"WHERE x.`{c}` IS NOT NULL AND x.`{c}` <> 0 AND g.`{rc}` IS NULL{where}", f"for gone {rt}.{c}"[:60])
        if n:
            log[f"root {schema}.{t} [{c}{'[' + poly[1] + ']' if poly else ''} -> already-missing {rt}]"] = n

    # --- asset rule (before the cascade so cascades from killed assets follow) ---
    isa = {"Assay": kt(SEEK, "assays"), "Study": kt(SEEK, "studies"), "Investigation": kt(SEEK, "investigations")}
    seek_tables = set(s.columns(SEEK))
    for typ in ASSET_TYPES:
        tab = s.rails_table(typ)
        if tab not in seek_tables:
            continue
        ensure_kill_table(pks, SEEK, tab)
        links = [f"SELECT asset_id AS a, (assay_id IN (SELECT id FROM {isa['Assay']})) AS k FROM {SEEK}.assay_assets WHERE asset_type='{typ}'"]
        for it, k in isa.items():
            links.append(f"SELECT subject_id, (other_object_id IN (SELECT id FROM {k})) FROM {SEEK}.relationships WHERE subject_type='{typ}' AND other_object_type='{it}'")
            links.append(f"SELECT other_object_id, (subject_id IN (SELECT id FROM {k})) FROM {SEEK}.relationships WHERE other_object_type='{typ}' AND subject_type='{it}'")
        if typ == "Sop":
            links.append(f"SELECT sop_id, (study_id IN (SELECT id FROM {isa['Study']})) FROM {SEEK}.sops_studies")
        n = add(pks, SEEK, tab, f"SELECT l.a FROM ({' UNION ALL '.join(links)}) l GROUP BY l.a HAVING SUM(l.k) > 0 AND SUM(1 - l.k) = 0", "asset linked only to killed ISA")
        log[f"asset {tab} [ISA links]"] = n
        jt = f"{tab}_projects"
        idc = tab[:-1] + "_id" if not tab.endswith("ies") else tab[:-3] + "y_id"
        if jt in seek_tables and idc in [c for c, _, _ in s.columns(SEEK)[jt]]:
            n = add(pks, SEEK, tab, f"SELECT `{idc}` FROM {SEEK}.`{jt}` GROUP BY `{idc}` HAVING SUM(project_id NOT IN (SELECT id FROM {kt(SEEK, 'projects')})) = 0", "asset only in killed projects")
            log[f"asset {tab} [project links]"] = n

    # --- cascade to a fixpoint ---
    rl = refs()
    for rnd in range(1, 30):
        added = 0
        existing = {r[0] for r in s.rows(f"SELECT table_name FROM information_schema.tables WHERE table_schema='{W}'")}
        for schema, t, c, rs, rt, rc, poly in rl:
            if c in NO_CASCADE and (t, c) not in CASCADE_ANYWAY:
                continue
            if f"k__{'s' if rs == SEEK else 'd'}__{rt}" not in existing:
                continue  # nothing of the target is killed
            if (rs, rt) in pks and pks[(rs, rt)][0] != rc:
                continue  # reference to a non-pk column (rare FKs)
            if (schema, t) not in pks:
                continue  # leaf without single pk: predicate delete at execute time
            pkc = pks[(schema, t)][0]
            where = f" AND x.`{poly[0]}`='{poly[1]}'" if poly else ""
            n = add(pks, schema, t, f"SELECT x.`{pkc}` FROM `{schema}`.`{t}` x JOIN {kt(rs, rt)} k ON k.id = x.`{c}` WHERE 1{where}", f"-> {rt}.{c}"[:60])
            if n:
                log[f"cascade r{rnd} {schema}.{t} via {c}{'[' + poly[1] + ']' if poly else ''} -> {rt}"] = n
                added += n
        if not added:
            break

    # --- policies: referenced by a killed row and by no surviving row ---
    pol_refs = [(sc, t, c) for sc, t, c, rs, rt, rc, poly in rl if rs == SEEK and rt == "policies" and sc == SEEK and t != "permissions"]
    killed_refs = " UNION ".join(f"SELECT x.`{c}` FROM {SEEK}.`{t}` x JOIN {kt(SEEK, t)} k ON k.id = x.id"
                                 for sc, t, c in pol_refs if (SEEK, t) in pks and f"k__s__{t}" in {r[0] for r in s.rows(f"SELECT table_name FROM information_schema.tables WHERE table_schema='{W}'")})
    kept_refs = " UNION ".join(f"SELECT x.`{c}` FROM {SEEK}.`{t}` x LEFT JOIN {kt(SEEK, t)} k ON k.id = x.id WHERE k.id IS NULL AND x.`{c}` IS NOT NULL"
                               if (SEEK, t) in pks else f"SELECT `{c}` FROM {SEEK}.`{t}` WHERE `{c}` IS NOT NULL" for sc, t, c in pol_refs)
    for sc, t, c in pol_refs:
        ensure_kill_table(pks, SEEK, t) if (SEEK, t) in pks else None
    if killed_refs:
        log["root seek_production.policies [freed by killed rows]"] = add(
            pks, SEEK, "policies", f"SELECT p.v FROM ({killed_refs}) p(v) WHERE p.v IS NOT NULL AND p.v NOT IN (SELECT v FROM ({kept_refs}) q(v) WHERE v IS NOT NULL)", "freed policy")
        log["cascade seek_production.permissions via policy_id"] = add(
            pks, SEEK, "permissions", f"SELECT x.id FROM {SEEK}.permissions x JOIN {kt(SEEK, 'policies')} k ON k.id = x.policy_id", "-> policies.policy_id")
    json.dump(log, open(os.environ.get("LOG", "kill_build.json"), "w"), indent=1)
    for k, v in log.items():
        if v:
            print(f"{v:>9}  {k}")
    print("STOP-CHECK mixed samples:", log["STOP-CHECK samples in a killed AND a kept assay"])


def kill_tables():
    return [r[0] for r in s.rows(f"SELECT table_name FROM information_schema.tables WHERE table_schema='{W}' AND table_name LIKE 'k\\_\\_%'")]


def report():
    for name in sorted(kill_tables()):
        print(name, s.scalar(f"SELECT COUNT(*) FROM `{W}`.`{name}`"))


def execute():
    """DELETE every killed row. Leaf tables without a single pk are deleted by predicate on each cascading ref.
    FOREIGN_KEY_CHECKS=0 per session (a session variable, no schema change); the orphan scan proves the result."""
    pks = pk_info()
    rl = refs()
    done = {}
    names = set(kill_tables())

    def run_batched(schema, table, join_col, killset, extra=""):
        ids = [r[0] for r in s.rows(f"SELECT id FROM {killset} ORDER BY id")]
        total = 0
        for i in range(0, len(ids), BATCH):
            lo, hi = ids[i], ids[min(i + BATCH, len(ids)) - 1]
            q = (f"SET FOREIGN_KEY_CHECKS=0; DELETE x FROM `{schema}`.`{table}` x JOIN {killset} k ON k.id = x.`{join_col}` "
                 f"WHERE k.id BETWEEN '{lo}' AND '{hi}'{extra}; SELECT ROW_COUNT();")
            total += int(s.run_sql(q, db=schema).strip().split("\n")[-1])
        return total

    # 1) leaf tables without a single pk: predicate deletes for every cascading reference into a kill set
    for schema, t, c, rs, rt, rc, poly in rl:
        if (schema, t) in pks or (c in NO_CASCADE and (t, c) not in CASCADE_ANYWAY):
            continue
        if (rs, rt) in pks and pks[(rs, rt)][0] != rc:
            continue  # reference to a non-pk column (as build skips it)
        name = f"k__{'s' if rs == SEEK else 'd'}__{rt}"
        if name not in names:
            continue
        extra = f" AND x.`{poly[0]}`='{poly[1]}'" if poly else ""
        n = run_batched(schema, t, c, f"`{W}`.`{name}`", extra)
        done[f"{schema}.{t}"] = done.get(f"{schema}.{t}", 0) + n
    for t in ALL_ROWS[DMAC]:  # runtime tables without a single pk (session_state)
        if (DMAC, t) not in pks and s.scalar(f"SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='{DMAC}' AND table_name='{t}'") == "1":
            done[f"{DMAC}.{t}"] = int(s.run_sql(f"DELETE FROM {DMAC}.`{t}`; SELECT ROW_COUNT();").strip().split("\n")[-1])
    # 2) every table with a kill set, by its pk
    for name in sorted(names):
        sc = SEEK if name.startswith("k__s__") else DMAC
        t = name[6:]
        n = run_batched(sc, t, pks[(sc, t)][0], f"`{W}`.`{name}`")
        done[f"{sc}.{t}"] = done.get(f"{sc}.{t}", 0) + n
    json.dump(done, open(os.environ.get("LOG", "kill_exec.json"), "w"), indent=1, sort_keys=True)
    for k, v in sorted(done.items()):
        if v:
            print(f"{v:>9}  {k}")


if __name__ == "__main__":
    {"build": build, "execute": execute, "report": report}[sys.argv[1]]()

-- verify_merge.sql: gate M for the merged MySQL (plan task M1; spec section 4). Every SELECT
-- returns one or more rows of (check_name, expected, actual, pass); merge_tcga.sh turns them into
-- $GS_WORK/runs/M1/gate_m.json. Read-only apart from one session temporary table.
--
-- Inputs: seek_production and dmac (the merged data), dev_seek and dev_dmac (the dev dump the TCGA
-- rows came from), dmac.gs_premerge_checksums (snapshot taken right after the local backup loads)
-- and dmac.gs_postmerge_checksums (written by merge_tcga.sh just before this file runs).
-- Right after `merge_tcga.sh load` the TCGA checks fail and the local checks pass; after the merge
-- every check passes.
-- Expected constants come from the spec (merged counts, TCGA content) and from the local backup of
-- 2026-09-14 (orphan baselines that predate the merge); a different backup needs new constants.

USE dmac;
SET SESSION group_concat_max_len = 1000000;

-- The TCGA samples, as the dev data defines them: Sample assets of the TCGA investigation's assays.
DROP TEMPORARY TABLE IF EXISTS gs_v_tcga;
CREATE TEMPORARY TABLE gs_v_tcga (id INT PRIMARY KEY) ENGINE=InnoDB
  SELECT DISTINCT aa.asset_id AS id
  FROM dev_seek.assay_assets aa
  JOIN dev_seek.assays a ON a.id = aa.assay_id
  JOIN dev_seek.studies s ON s.id = a.study_id
  JOIN dev_seek.investigations i ON i.id = s.investigation_id
  WHERE BINARY i.title = 'TCGA' AND aa.asset_type = 'Sample';

SET @tcga_project := (SELECT id FROM seek_production.projects WHERE BINARY title = 'TCGA');
SET @tcga_inv := (SELECT MIN(investigation_id) FROM seek_production.investigations_projects
                  WHERE project_id = @tcga_project);
SET @demo_person := (SELECT person_id FROM seek_production.users WHERE BINARY login = 'demo');

-- The pre-merge maximum of each appended table's key: rows above it are the ones the merge wrote.
SET @mx_projects := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'seek_production' AND table_name = 'projects');
SET @mx_wg := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'seek_production' AND table_name = 'work_groups');
SET @mx_gm := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'seek_production' AND table_name = 'group_memberships');
SET @mx_users := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'seek_production' AND table_name = 'users');
SET @mx_perm := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'seek_production' AND table_name = 'permissions');
SET @mx_inv := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'seek_production' AND table_name = 'investigations');
SET @mx_ip := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'seek_production' AND table_name = 'investigations_projects');
SET @mx_studies := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'seek_production' AND table_name = 'studies');
SET @mx_assays := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'seek_production' AND table_name = 'assays');
SET @mx_st := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'seek_production' AND table_name = 'sample_types');
SET @mx_pst := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'seek_production' AND table_name = 'projects_sample_types');
SET @mx_sa := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'seek_production' AND table_name = 'sample_attributes');
SET @mx_samples := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'seek_production' AND table_name = 'samples');
SET @mx_ps := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'seek_production' AND table_name = 'projects_samples');
SET @mx_aa := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'seek_production' AND table_name = 'assay_assets');
SET @mx_aia := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'dmac' AND table_name = 'assays_internal_assays');
SET @mx_stc := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'dmac' AND table_name = 'sample_types_clades');
SET @mx_sup := (SELECT max_ref FROM dmac.gs_premerge_checksums WHERE schema_name = 'dmac' AND table_name = 'seek_user_profile');

-- =============================================================================================
-- 1. Merged counts (spec section 4 and the merge analysis). Policies and permissions include the
--    new project's default policy and its one grant.
-- =============================================================================================
SELECT 'count:samples' AS check_name, '1084754' AS expected, COUNT(*) AS actual, COUNT(*) = 1084754 AS pass FROM seek_production.samples;
SELECT 'count:projects_samples', '1127894', COUNT(*), COUNT(*) = 1127894 FROM seek_production.projects_samples;
SELECT 'count:assay_assets', '1546204', COUNT(*), COUNT(*) = 1546204 FROM seek_production.assay_assets;
SELECT 'count:sample_types', '118', COUNT(*), COUNT(*) = 118 FROM seek_production.sample_types;
SELECT 'count:sample_attributes', '3530', COUNT(*), COUNT(*) = 3530 FROM seek_production.sample_attributes;
SELECT 'count:projects', '14', COUNT(*), COUNT(*) = 14 FROM seek_production.projects;
SELECT 'count:investigations', '17', COUNT(*), COUNT(*) = 17 FROM seek_production.investigations;
SELECT 'count:studies', '81', COUNT(*), COUNT(*) = 81 FROM seek_production.studies;
SELECT 'count:assays', '995', COUNT(*), COUNT(*) = 995 FROM seek_production.assays;
SELECT 'count:policies', '1143295', COUNT(*), COUNT(*) = 1143295 FROM seek_production.policies;
SELECT 'count:permissions', '1193688', COUNT(*), COUNT(*) = 1193688 FROM seek_production.permissions;
SELECT 'count:people', '126', COUNT(*), COUNT(*) = 126 FROM seek_production.people;
SELECT 'count:users', '126', COUNT(*), COUNT(*) = 126 FROM seek_production.users;
SELECT 'count:dmac.internal_assays', '143', COUNT(*), COUNT(*) = 143 FROM dmac.internal_assays;
SELECT 'count:dmac.assays_internal_assays', '970', COUNT(*), COUNT(*) = 970 FROM dmac.assays_internal_assays;
SELECT 'count:dmac.sample_types_clades', '118', COUNT(*), COUNT(*) = 118 FROM dmac.sample_types_clades;
SELECT 'count:dmac.auth_user', '35', COUNT(*), COUNT(*) = 35 FROM dmac.auth_user;

-- =============================================================================================
-- 2. Every pre-existing local row is unchanged. An appended table is compared on a copy of the
--    rows its snapshot filter selects; every other table must be byte-identical (CHECKSUM TABLE).
-- =============================================================================================
SELECT CONCAT('local_rows_unchanged:', pre.schema_name, '.', pre.table_name) AS check_name,
       CONCAT(pre.row_count, ' rows, checksum ', pre.checksum) AS expected,
       CONCAT(IFNULL(post.row_count, 'NULL'), ' rows, checksum ', IFNULL(post.checksum, 'NULL')) AS actual,
       (pre.checksum IS NOT NULL AND post.row_count <=> pre.row_count AND post.checksum <=> pre.checksum) AS pass
FROM dmac.gs_premerge_checksums pre
LEFT JOIN dmac.gs_postmerge_checksums post ON post.schema_name = pre.schema_name AND post.table_name = pre.table_name
WHERE pre.scope = 'rows'
ORDER BY pre.schema_name, pre.table_name;
SELECT 'local_tables_unchanged' AS check_name,
       CONCAT(COUNT(*), ' tables') AS expected,
       CONCAT(SUM(pre.checksum IS NOT NULL AND post.row_count <=> pre.row_count AND post.checksum <=> pre.checksum),
              ' tables unchanged; changed: ',
              IFNULL(GROUP_CONCAT(CASE WHEN NOT (pre.checksum IS NOT NULL AND post.row_count <=> pre.row_count
                                                  AND post.checksum <=> pre.checksum)
                                       THEN CONCAT(pre.schema_name, '.', pre.table_name) END
                                  ORDER BY pre.schema_name, pre.table_name), 'none')) AS actual,
       COUNT(*) > 0 AND COUNT(*) = SUM(pre.checksum IS NOT NULL AND post.row_count <=> pre.row_count
                                       AND post.checksum <=> pre.checksum) AS pass
FROM dmac.gs_premerge_checksums pre
LEFT JOIN dmac.gs_postmerge_checksums post ON post.schema_name = pre.schema_name AND post.table_name = pre.table_name
WHERE pre.scope = 'table';

-- =============================================================================================
-- 3. No orphans on any remapped foreign key. "new" counts orphans among the rows the merge wrote
--    (key above the snapshot maximum) and must be 0; "local" counts the orphans the local backup
--    already had, which must not move.
-- =============================================================================================
SELECT 'orphans:samples.sample_type_id' AS check_name, 'new 0, local 0' AS expected,
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_samples THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_samples THEN 1 END)) AS actual,
       COUNT(CASE WHEN x.id > @mx_samples THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_samples THEN 1 END) = 0 AS pass
FROM seek_production.samples x LEFT JOIN seek_production.sample_types r ON r.id = x.sample_type_id WHERE r.id IS NULL;
SELECT 'orphans:samples.policy_id', 'new 0, local 88',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_samples THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_samples THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_samples THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_samples THEN 1 END) = 88
FROM seek_production.samples x LEFT JOIN seek_production.policies r ON r.id = x.policy_id WHERE r.id IS NULL;
SELECT 'orphans:samples.contributor_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_samples THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_samples THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_samples THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_samples THEN 1 END) = 0
FROM seek_production.samples x LEFT JOIN seek_production.people r ON r.id = x.contributor_id WHERE r.id IS NULL;
SELECT 'orphans:assay_assets.assay_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_aa THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_aa THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_aa THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_aa THEN 1 END) = 0
FROM seek_production.assay_assets x LEFT JOIN seek_production.assays r ON r.id = x.assay_id WHERE r.id IS NULL;
SELECT 'orphans:assay_assets.asset_id(Sample)', 'new 0, local 368',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_aa THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_aa THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_aa THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_aa THEN 1 END) = 368
FROM seek_production.assay_assets x LEFT JOIN seek_production.samples r ON r.id = x.asset_id
WHERE x.asset_type = 'Sample' AND r.id IS NULL;
SELECT 'orphans:permissions.policy_id', 'new 0, local 6',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_perm THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_perm THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_perm THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_perm THEN 1 END) = 6
FROM seek_production.permissions x LEFT JOIN seek_production.policies r ON r.id = x.policy_id WHERE r.id IS NULL;
SELECT 'orphans:permissions.contributor_id(Project)', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_perm THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_perm THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_perm THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_perm THEN 1 END) = 0
FROM seek_production.permissions x LEFT JOIN seek_production.projects r ON r.id = x.contributor_id
WHERE x.contributor_type = 'Project' AND r.id IS NULL;
SELECT 'orphans:projects_samples.project_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.sample_id > @mx_ps THEN 1 END), ', local ', COUNT(CASE WHEN x.sample_id <= @mx_ps OR x.sample_id IS NULL THEN 1 END)),
       COUNT(CASE WHEN x.sample_id > @mx_ps THEN 1 END) = 0 AND COUNT(CASE WHEN x.sample_id <= @mx_ps OR x.sample_id IS NULL THEN 1 END) = 0
FROM seek_production.projects_samples x LEFT JOIN seek_production.projects r ON r.id = x.project_id WHERE r.id IS NULL;
SELECT 'orphans:projects_samples.sample_id', 'new 0, local 21',
       CONCAT('new ', COUNT(CASE WHEN x.sample_id > @mx_ps THEN 1 END), ', local ', COUNT(CASE WHEN x.sample_id <= @mx_ps OR x.sample_id IS NULL THEN 1 END)),
       COUNT(CASE WHEN x.sample_id > @mx_ps THEN 1 END) = 0 AND COUNT(CASE WHEN x.sample_id <= @mx_ps OR x.sample_id IS NULL THEN 1 END) = 21
FROM seek_production.projects_samples x LEFT JOIN seek_production.samples r ON r.id = x.sample_id WHERE r.id IS NULL;
SELECT 'orphans:studies.investigation_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_studies THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_studies THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_studies THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_studies THEN 1 END) = 0
FROM seek_production.studies x LEFT JOIN seek_production.investigations r ON r.id = x.investigation_id WHERE r.id IS NULL;
SELECT 'orphans:assays.study_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_assays THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_assays THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_assays THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_assays THEN 1 END) = 0
FROM seek_production.assays x LEFT JOIN seek_production.studies r ON r.id = x.study_id WHERE r.id IS NULL;
SELECT 'orphans:assays.assay_class_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_assays THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_assays THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_assays THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_assays THEN 1 END) = 0
FROM seek_production.assays x LEFT JOIN seek_production.assay_classes r ON r.id = x.assay_class_id
WHERE x.assay_class_id IS NOT NULL AND r.id IS NULL;
SELECT 'orphans:isa.policy_id', 'new 0, local 0',
       CONCAT('new ', SUM(n_new), ', local ', SUM(n_local)), SUM(n_new) = 0 AND SUM(n_local) = 0
FROM (SELECT COUNT(CASE WHEN x.id > @mx_inv THEN 1 END) AS n_new, COUNT(CASE WHEN x.id <= @mx_inv THEN 1 END) AS n_local
      FROM seek_production.investigations x LEFT JOIN seek_production.policies r ON r.id = x.policy_id WHERE r.id IS NULL
      UNION ALL
      SELECT COUNT(CASE WHEN x.id > @mx_studies THEN 1 END), COUNT(CASE WHEN x.id <= @mx_studies THEN 1 END)
      FROM seek_production.studies x LEFT JOIN seek_production.policies r ON r.id = x.policy_id WHERE r.id IS NULL
      UNION ALL
      SELECT COUNT(CASE WHEN x.id > @mx_assays THEN 1 END), COUNT(CASE WHEN x.id <= @mx_assays THEN 1 END)
      FROM seek_production.assays x LEFT JOIN seek_production.policies r ON r.id = x.policy_id WHERE r.id IS NULL
      UNION ALL
      SELECT COUNT(CASE WHEN x.id > @mx_projects THEN 1 END), COUNT(CASE WHEN x.id <= @mx_projects THEN 1 END)
      FROM seek_production.projects x LEFT JOIN seek_production.policies r ON r.id = x.default_policy_id
      WHERE x.default_policy_id IS NOT NULL AND r.id IS NULL) u;
SELECT 'orphans:isa_and_types.contributor_id', 'new 0, local 0',
       CONCAT('new ', SUM(n_new), ', local ', SUM(n_local)), SUM(n_new) = 0 AND SUM(n_local) = 0
FROM (SELECT COUNT(CASE WHEN x.id > @mx_inv THEN 1 END) AS n_new, COUNT(CASE WHEN x.id <= @mx_inv THEN 1 END) AS n_local
      FROM seek_production.investigations x LEFT JOIN seek_production.people r ON r.id = x.contributor_id
      WHERE x.contributor_id IS NOT NULL AND r.id IS NULL
      UNION ALL
      SELECT COUNT(CASE WHEN x.id > @mx_studies THEN 1 END), COUNT(CASE WHEN x.id <= @mx_studies THEN 1 END)
      FROM seek_production.studies x LEFT JOIN seek_production.people r ON r.id = x.contributor_id
      WHERE x.contributor_id IS NOT NULL AND r.id IS NULL
      UNION ALL
      SELECT COUNT(CASE WHEN x.id > @mx_assays THEN 1 END), COUNT(CASE WHEN x.id <= @mx_assays THEN 1 END)
      FROM seek_production.assays x LEFT JOIN seek_production.people r ON r.id = x.contributor_id
      WHERE x.contributor_id IS NOT NULL AND r.id IS NULL
      UNION ALL
      SELECT COUNT(CASE WHEN x.id > @mx_st THEN 1 END), COUNT(CASE WHEN x.id <= @mx_st THEN 1 END)
      FROM seek_production.sample_types x LEFT JOIN seek_production.people r ON r.id = x.contributor_id
      WHERE x.contributor_id IS NOT NULL AND r.id IS NULL) u;
SELECT 'orphans:investigations_projects.investigation_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.investigation_id > @mx_ip THEN 1 END), ', local ', COUNT(CASE WHEN x.investigation_id <= @mx_ip OR x.investigation_id IS NULL THEN 1 END)),
       COUNT(CASE WHEN x.investigation_id > @mx_ip THEN 1 END) = 0 AND COUNT(CASE WHEN x.investigation_id <= @mx_ip OR x.investigation_id IS NULL THEN 1 END) = 0
FROM seek_production.investigations_projects x LEFT JOIN seek_production.investigations r ON r.id = x.investigation_id WHERE r.id IS NULL;
SELECT 'orphans:investigations_projects.project_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.investigation_id > @mx_ip THEN 1 END), ', local ', COUNT(CASE WHEN x.investigation_id <= @mx_ip OR x.investigation_id IS NULL THEN 1 END)),
       COUNT(CASE WHEN x.investigation_id > @mx_ip THEN 1 END) = 0 AND COUNT(CASE WHEN x.investigation_id <= @mx_ip OR x.investigation_id IS NULL THEN 1 END) = 0
FROM seek_production.investigations_projects x LEFT JOIN seek_production.projects r ON r.id = x.project_id WHERE r.id IS NULL;
SELECT 'orphans:sample_attributes.sample_type_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_sa THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_sa THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_sa THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_sa THEN 1 END) = 0
FROM seek_production.sample_attributes x LEFT JOIN seek_production.sample_types r ON r.id = x.sample_type_id WHERE r.id IS NULL;
SELECT 'orphans:sample_attributes.sample_attribute_type_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_sa THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_sa THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_sa THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_sa THEN 1 END) = 0
FROM seek_production.sample_attributes x LEFT JOIN seek_production.sample_attribute_types r ON r.id = x.sample_attribute_type_id WHERE r.id IS NULL;
-- projects_sample_types has no id, and 4 local rows name projects 17 and 29, which do not exist:
-- its new rows are the ones naming the new project (max_ref holds that id, see merge_tcga.sh).
SELECT 'orphans:projects_sample_types.project_id', 'new 0, local 4',
       CONCAT('new ', COUNT(CASE WHEN x.project_id = @mx_pst THEN 1 END), ', local ', COUNT(CASE WHEN NOT (x.project_id <=> @mx_pst) THEN 1 END)),
       COUNT(CASE WHEN x.project_id = @mx_pst THEN 1 END) = 0 AND COUNT(CASE WHEN NOT (x.project_id <=> @mx_pst) THEN 1 END) = 4
FROM seek_production.projects_sample_types x LEFT JOIN seek_production.projects r ON r.id = x.project_id WHERE r.id IS NULL;
SELECT 'orphans:projects_sample_types.sample_type_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.project_id = @mx_pst THEN 1 END), ', local ', COUNT(CASE WHEN NOT (x.project_id <=> @mx_pst) THEN 1 END)),
       COUNT(CASE WHEN x.project_id = @mx_pst THEN 1 END) = 0 AND COUNT(CASE WHEN NOT (x.project_id <=> @mx_pst) THEN 1 END) = 0
FROM seek_production.projects_sample_types x LEFT JOIN seek_production.sample_types r ON r.id = x.sample_type_id WHERE r.id IS NULL;
SELECT 'orphans:work_groups.project_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_wg THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_wg THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_wg THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_wg THEN 1 END) = 0
FROM seek_production.work_groups x LEFT JOIN seek_production.projects r ON r.id = x.project_id WHERE r.id IS NULL;
SELECT 'orphans:group_memberships.work_group_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_gm THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_gm THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_gm THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_gm THEN 1 END) = 0
FROM seek_production.group_memberships x LEFT JOIN seek_production.work_groups r ON r.id = x.work_group_id WHERE r.id IS NULL;
SELECT 'orphans:group_memberships.person_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_gm THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_gm THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_gm THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_gm THEN 1 END) = 0
FROM seek_production.group_memberships x LEFT JOIN seek_production.people r ON r.id = x.person_id WHERE r.id IS NULL;
SELECT 'orphans:users.person_id', 'new 0, local 2',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_users THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_users THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_users THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_users THEN 1 END) = 2
FROM seek_production.users x LEFT JOIN seek_production.people r ON r.id = x.person_id WHERE r.id IS NULL;
SELECT 'orphans:dmac.assays_internal_assays.assay_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_aia THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_aia THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_aia THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_aia THEN 1 END) = 0
FROM dmac.assays_internal_assays x LEFT JOIN seek_production.assays r ON r.id = x.assay_id WHERE r.id IS NULL;
SELECT 'orphans:dmac.assays_internal_assays.internal_assay_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_aia THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_aia THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_aia THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_aia THEN 1 END) = 0
FROM dmac.assays_internal_assays x LEFT JOIN dmac.internal_assays r ON r.id = x.internal_assay_id WHERE r.id IS NULL;
SELECT 'orphans:dmac.sample_types_clades.sample_type_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_stc THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_stc THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_stc THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_stc THEN 1 END) = 0
FROM dmac.sample_types_clades x LEFT JOIN seek_production.sample_types r ON r.id = x.sample_type_id WHERE r.id IS NULL;
SELECT 'orphans:dmac.sample_types_clades.clade_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_stc THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_stc THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_stc THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_stc THEN 1 END) = 0
FROM dmac.sample_types_clades x LEFT JOIN dmac.clades r ON r.id = x.clade_id WHERE r.id IS NULL;
SELECT 'orphans:dmac.seek_user_profile.user_id', 'new 0, local 0',
       CONCAT('new ', COUNT(CASE WHEN x.id > @mx_sup THEN 1 END), ', local ', COUNT(CASE WHEN x.id <= @mx_sup THEN 1 END)),
       COUNT(CASE WHEN x.id > @mx_sup THEN 1 END) = 0 AND COUNT(CASE WHEN x.id <= @mx_sup THEN 1 END) = 0
FROM dmac.seek_user_profile x LEFT JOIN dmac.auth_user r ON r.id = x.user_id WHERE r.id IS NULL;

-- =============================================================================================
-- 4. TCGA content intact: every TCGA sample present, JSON bytes, per-sample CRC32 against the
--    dev dump, and the kept columns.
-- =============================================================================================
SELECT 'tcga:source_samples_in_dev_dump' AS check_name, '918519' AS expected, COUNT(*) AS actual, COUNT(*) = 918519 AS pass
FROM gs_v_tcga;
SELECT 'tcga:json_bytes', '896626185', COALESCE(SUM(LENGTH(m.json_metadata)), 0),
       COALESCE(SUM(LENGTH(m.json_metadata)), 0) = 896626185
FROM gs_v_tcga t JOIN seek_production.samples m ON m.id = t.id;
SELECT 'tcga:json_crc32_mismatches_or_missing', '0', COUNT(*), COUNT(*) = 0
FROM gs_v_tcga t
JOIN dev_seek.samples d ON d.id = t.id
LEFT JOIN seek_production.samples m ON m.id = d.id
WHERE m.id IS NULL
   OR NOT (CRC32(m.json_metadata) <=> CRC32(d.json_metadata))
   OR NOT (LENGTH(m.json_metadata) <=> LENGTH(d.json_metadata));
SELECT 'tcga:kept_columns_type_or_contributor_mismatches', '0', COUNT(*), COUNT(*) = 0
FROM gs_v_tcga t
JOIN dev_seek.samples d ON d.id = t.id
JOIN dev_seek.sample_types dt ON dt.id = d.sample_type_id
LEFT JOIN seek_production.samples m ON m.id = d.id
LEFT JOIN seek_production.sample_types mt ON mt.id = m.sample_type_id
WHERE m.id IS NULL
   OR NOT (BINARY m.uuid <=> BINARY d.uuid)
   OR NOT (BINARY m.title <=> BINARY d.title)
   OR NOT (m.policy_id <=> d.policy_id)
   OR NOT (m.created_at <=> d.created_at)
   OR NOT (m.updated_at <=> d.updated_at)
   OR NOT (BINARY mt.title <=> BINARY dt.title)
   OR NOT (m.contributor_id <=> @demo_person);
SELECT 'tcga:projects_samples', '918519 in TCGA, 0 elsewhere',
       CONCAT(COUNT(CASE WHEN ps.project_id = @tcga_project THEN 1 END), ' in TCGA, ',
              COUNT(CASE WHEN NOT (ps.project_id <=> @tcga_project) THEN 1 END), ' elsewhere'),
       COUNT(CASE WHEN ps.project_id = @tcga_project THEN 1 END) = 918519
       AND COUNT(CASE WHEN NOT (ps.project_id <=> @tcga_project) THEN 1 END) = 0
FROM gs_v_tcga t JOIN seek_production.projects_samples ps ON ps.sample_id = t.id;
SELECT 'tcga:project_holds_only_tcga_samples', '0', COUNT(*), @tcga_project IS NOT NULL AND COUNT(*) = 0
FROM seek_production.projects_samples ps
LEFT JOIN gs_v_tcga t ON t.id = ps.sample_id
WHERE ps.project_id = @tcga_project AND t.id IS NULL;
SELECT 'tcga:policies_kept', '919082',
       COUNT(*), COUNT(*) = 919082
FROM (SELECT d.policy_id AS id FROM gs_v_tcga t JOIN dev_seek.samples d ON d.id = t.id
      UNION SELECT i.policy_id FROM dev_seek.investigations i WHERE BINARY i.title = 'TCGA'
      UNION SELECT s.policy_id FROM dev_seek.studies s JOIN dev_seek.investigations i ON i.id = s.investigation_id
            WHERE BINARY i.title = 'TCGA'
      UNION SELECT a.policy_id FROM dev_seek.assays a JOIN dev_seek.studies s ON s.id = a.study_id
            JOIN dev_seek.investigations i ON i.id = s.investigation_id WHERE BINARY i.title = 'TCGA') tp
JOIN dev_seek.policies d ON d.id = tp.id
JOIN seek_production.policies m ON m.id = d.id
 AND BINARY m.name <=> BINARY d.name AND m.sharing_scope <=> d.sharing_scope AND m.access_type <=> d.access_type
 AND m.use_allowlist <=> d.use_allowlist AND m.use_denylist <=> d.use_denylist
 AND m.created_at <=> d.created_at AND m.updated_at <=> d.updated_at;
SELECT 'tcga:permissions', '959088 grants to TCGA, 0 other',
       CONCAT(COUNT(CASE WHEN p.contributor_type = 'Project' AND p.contributor_id = @tcga_project THEN 1 END),
              ' grants to TCGA, ',
              COUNT(CASE WHEN NOT (p.contributor_type = 'Project' AND p.contributor_id <=> @tcga_project) THEN 1 END),
              ' other'),
       COUNT(CASE WHEN p.contributor_type = 'Project' AND p.contributor_id = @tcga_project THEN 1 END) = 959088
       AND COUNT(CASE WHEN NOT (p.contributor_type = 'Project' AND p.contributor_id <=> @tcga_project) THEN 1 END) = 0
-- The TCGA policies are a derived table joined on permissions.policy_id: an IN (... UNION ...)
-- subquery is not materialized and runs once per permission row.
FROM (SELECT d.policy_id AS id FROM gs_v_tcga t JOIN seek_production.samples d ON d.id = t.id
      UNION SELECT policy_id FROM seek_production.investigations WHERE id = @tcga_inv
      UNION SELECT policy_id FROM seek_production.studies WHERE investigation_id = @tcga_inv
      UNION SELECT a.policy_id FROM seek_production.assays a
            JOIN seek_production.studies s ON s.id = a.study_id
            WHERE s.investigation_id = @tcga_inv) tp
JOIN seek_production.permissions p ON p.policy_id = tp.id
WHERE p.id > @mx_perm;
SELECT 'tcga:isa', '1 investigation, 33 studies, 529 assays',
       CONCAT((SELECT COUNT(*) FROM seek_production.investigations_projects WHERE project_id = @tcga_project),
              ' investigation, ',
              (SELECT COUNT(*) FROM seek_production.studies WHERE investigation_id = @tcga_inv), ' studies, ',
              (SELECT COUNT(*) FROM seek_production.assays a JOIN seek_production.studies s ON s.id = a.study_id
               WHERE s.investigation_id = @tcga_inv), ' assays'),
       (SELECT COUNT(*) FROM seek_production.investigations_projects WHERE project_id = @tcga_project) = 1
       AND (SELECT COUNT(*) FROM seek_production.studies WHERE investigation_id = @tcga_inv) = 33
       AND (SELECT COUNT(*) FROM seek_production.assays a JOIN seek_production.studies s ON s.id = a.study_id
            WHERE s.investigation_id = @tcga_inv) = 529;
SELECT 'tcga:isa_rows_kept_by_uuid', '1 investigation, 33 studies, 529 assays',
       CONCAT((SELECT COUNT(*) FROM dev_seek.investigations d JOIN seek_production.investigations m
                 ON BINARY m.uuid = BINARY d.uuid AND m.id = @tcga_inv AND BINARY m.title = BINARY d.title
                AND m.policy_id <=> d.policy_id
               WHERE BINARY d.title = 'TCGA'), ' investigation, ',
              (SELECT COUNT(*) FROM dev_seek.studies d JOIN dev_seek.investigations di ON di.id = d.investigation_id
               JOIN seek_production.studies m ON BINARY m.uuid = BINARY d.uuid AND m.investigation_id = @tcga_inv
                AND BINARY m.title = BINARY d.title AND m.policy_id <=> d.policy_id
               WHERE BINARY di.title = 'TCGA'), ' studies, ',
              (SELECT COUNT(*) FROM dev_seek.assays d JOIN dev_seek.studies ds ON ds.id = d.study_id
               JOIN dev_seek.investigations di ON di.id = ds.investigation_id
               JOIN seek_production.assays m ON BINARY m.uuid = BINARY d.uuid AND BINARY m.title = BINARY d.title
                AND m.policy_id <=> d.policy_id
               JOIN seek_production.studies ms ON ms.id = m.study_id AND BINARY ms.uuid = BINARY ds.uuid
               WHERE BINARY di.title = 'TCGA'), ' assays'),
       CONCAT((SELECT COUNT(*) FROM dev_seek.investigations d JOIN seek_production.investigations m
                 ON BINARY m.uuid = BINARY d.uuid AND m.id = @tcga_inv AND BINARY m.title = BINARY d.title
                AND m.policy_id <=> d.policy_id
               WHERE BINARY d.title = 'TCGA'), ' investigation, ',
              (SELECT COUNT(*) FROM dev_seek.studies d JOIN dev_seek.investigations di ON di.id = d.investigation_id
               JOIN seek_production.studies m ON BINARY m.uuid = BINARY d.uuid AND m.investigation_id = @tcga_inv
                AND BINARY m.title = BINARY d.title AND m.policy_id <=> d.policy_id
               WHERE BINARY di.title = 'TCGA'), ' studies, ',
              (SELECT COUNT(*) FROM dev_seek.assays d JOIN dev_seek.studies ds ON ds.id = d.study_id
               JOIN dev_seek.investigations di ON di.id = ds.investigation_id
               JOIN seek_production.assays m ON BINARY m.uuid = BINARY d.uuid AND BINARY m.title = BINARY d.title
                AND m.policy_id <=> d.policy_id
               JOIN seek_production.studies ms ON ms.id = m.study_id AND BINARY ms.uuid = BINARY ds.uuid
               WHERE BINARY di.title = 'TCGA'), ' assays') = '1 investigation, 33 studies, 529 assays';
SELECT 'tcga:assay_assets_kept', '1299815', COUNT(*), COUNT(*) = 1299815
FROM dev_seek.assay_assets d
JOIN dev_seek.assays da ON da.id = d.assay_id
JOIN dev_seek.studies ds ON ds.id = da.study_id
JOIN dev_seek.investigations di ON di.id = ds.investigation_id
JOIN seek_production.assay_assets m ON m.id = d.id AND m.asset_id <=> d.asset_id AND BINARY m.asset_type <=> BINARY d.asset_type
 AND m.version <=> d.version AND m.direction <=> d.direction AND m.created_at <=> d.created_at
JOIN seek_production.assays ma ON ma.id = m.assay_id AND BINARY ma.uuid = BINARY da.uuid
WHERE BINARY di.title = 'TCGA';
SELECT 'tcga:internal_assay_links', '529', COUNT(*), COUNT(*) = 529
FROM dev_dmac.assays_internal_assays d
JOIN dev_dmac.internal_assays di ON di.id = d.internal_assay_id
JOIN dev_seek.assays da ON da.id = d.assay_id
JOIN dev_seek.studies ds ON ds.id = da.study_id
JOIN dev_seek.investigations dinv ON dinv.id = ds.investigation_id
JOIN seek_production.assays ma ON BINARY ma.uuid = BINARY da.uuid
JOIN dmac.assays_internal_assays m ON m.assay_id = ma.id
JOIN dmac.internal_assays mi ON mi.id = m.internal_assay_id AND BINARY mi.internal_assay_title = BINARY di.internal_assay_title
WHERE BINARY dinv.title = 'TCGA';
SELECT 'tcga:sample_types_in_project_and_clade', '14 in the project, 14 with a clade',
       CONCAT(COUNT(*), ' in the project, ',
              COUNT(CASE WHEN EXISTS (SELECT 1 FROM dmac.sample_types_clades c WHERE c.sample_type_id = pst.sample_type_id)
                         THEN 1 END), ' with a clade'),
       COUNT(*) = 14 AND COUNT(CASE WHEN EXISTS (SELECT 1 FROM dmac.sample_types_clades c
                                                 WHERE c.sample_type_id = pst.sample_type_id) THEN 1 END) = 14
FROM seek_production.projects_sample_types pst WHERE pst.project_id = @tcga_project;

-- =============================================================================================
-- 5. Every TCGA json_metadata key is a declared attribute title of its mapped type. The mapped
--    type is the merged sample's type; before the merge, the local type with the same title.
-- =============================================================================================
SELECT 'tcga:json_keys_undeclared_on_mapped_type' AS check_name, '0' AS expected, COUNT(*) AS actual, COUNT(*) = 0 AS pass
FROM (SELECT DISTINCT COALESCE(m.sample_type_id, tm.local_id) AS type_id, k.key_name
      FROM gs_v_tcga t
      JOIN dev_seek.samples d ON d.id = t.id
      LEFT JOIN seek_production.samples m ON m.id = d.id
      LEFT JOIN (SELECT dt.id AS dev_id, lt.id AS local_id
                 FROM dev_seek.sample_types dt
                 JOIN seek_production.sample_types lt ON BINARY lt.title = BINARY dt.title) tm
        ON tm.dev_id = d.sample_type_id
      CROSS JOIN JSON_TABLE(JSON_KEYS(d.json_metadata), '$[*]' COLUMNS (key_name VARCHAR(255) PATH '$')) AS k) x
WHERE NOT EXISTS (SELECT 1 FROM seek_production.sample_attributes a
                  WHERE a.sample_type_id = x.type_id AND BINARY a.title = BINARY x.key_name);
SELECT 'catalog:duplicate_type_attribute_titles', '0', COUNT(*), COUNT(*) = 0
FROM (SELECT sample_type_id, BINARY title FROM seek_production.sample_attributes
      GROUP BY sample_type_id, BINARY title HAVING COUNT(*) > 1) x;
SELECT 'catalog:types_without_exactly_one_title_attribute', '0', COUNT(*), COUNT(*) = 0
FROM seek_production.sample_types t
WHERE (SELECT COALESCE(SUM(a.is_title), 0) FROM seek_production.sample_attributes a WHERE a.sample_type_id = t.id) <> 1;

-- =============================================================================================
-- 6. No four-byte character in any TCGA text written into a utf8mb3 column (the source rows).
-- =============================================================================================
SELECT 'tcga:fourbyte_sample_text', '0', COUNT(*), COUNT(*) = 0
FROM gs_v_tcga t JOIN dev_seek.samples d ON d.id = t.id
WHERE LENGTH(d.json_metadata) <> LENGTH(CONVERT(d.json_metadata USING utf8mb3))
   OR LENGTH(d.title) <> LENGTH(CONVERT(d.title USING utf8mb3))
   OR LENGTH(d.uuid) <> LENGTH(CONVERT(d.uuid USING utf8mb3))
   OR LENGTH(d.other_creators) <> LENGTH(CONVERT(d.other_creators USING utf8mb3))
   OR LENGTH(d.deleted_contributor) <> LENGTH(CONVERT(d.deleted_contributor USING utf8mb3));
SELECT 'tcga:fourbyte_isa_type_attribute_text', '0', SUM(n), SUM(n) = 0
FROM (SELECT COUNT(*) AS n FROM dev_seek.investigations WHERE BINARY title = 'TCGA'
        AND (LENGTH(title) <> LENGTH(CONVERT(title USING utf8mb3))
             OR LENGTH(description) <> LENGTH(CONVERT(description USING utf8mb3)))
      UNION ALL
      SELECT COUNT(*) FROM dev_seek.studies s JOIN dev_seek.investigations i ON i.id = s.investigation_id
      WHERE BINARY i.title = 'TCGA'
        AND (LENGTH(s.title) <> LENGTH(CONVERT(s.title USING utf8mb3))
             OR LENGTH(s.description) <> LENGTH(CONVERT(s.description USING utf8mb3))
             OR LENGTH(s.experimentalists) <> LENGTH(CONVERT(s.experimentalists USING utf8mb3)))
      UNION ALL
      SELECT COUNT(*) FROM dev_seek.assays a JOIN dev_seek.studies s ON s.id = a.study_id
      JOIN dev_seek.investigations i ON i.id = s.investigation_id
      WHERE BINARY i.title = 'TCGA'
        AND (LENGTH(a.title) <> LENGTH(CONVERT(a.title USING utf8mb3))
             OR LENGTH(a.description) <> LENGTH(CONVERT(a.description USING utf8mb3)))
      UNION ALL
      SELECT COUNT(*) FROM seek_production.projects_sample_types pst
      JOIN seek_production.sample_types mt ON mt.id = pst.sample_type_id
      JOIN dev_seek.sample_types d ON BINARY d.title = BINARY mt.title
      LEFT JOIN dev_seek.sample_attributes da ON da.sample_type_id = d.id
      WHERE pst.project_id = @tcga_project
        AND (LENGTH(d.title) <> LENGTH(CONVERT(d.title USING utf8mb3))
             OR LENGTH(d.description) <> LENGTH(CONVERT(d.description USING utf8mb3))
             OR LENGTH(da.title) <> LENGTH(CONVERT(da.title USING utf8mb3))
             OR LENGTH(da.description) <> LENGTH(CONVERT(da.description USING utf8mb3)))) u;

-- =============================================================================================
-- 7. samples.uuid duplicates: only the 14 byte-exact duplicate values the local backup already had.
-- =============================================================================================
SELECT 'samples:uuid_duplicate_values', '14', COUNT(*), COUNT(*) = 14
FROM (SELECT BINARY uuid AS u FROM seek_production.samples GROUP BY BINARY uuid HAVING COUNT(*) > 1) x;

-- =============================================================================================
-- 8. Accounts: tcgamember is a TCGA-only member with the seed password; the seed `user` is not a
--    TCGA member; demo is the Django superuser.
-- =============================================================================================
SELECT 'account:tcgamember_projects', IFNULL(CAST(@tcga_project AS CHAR), 'the TCGA project id'),
       IFNULL(GROUP_CONCAT(DISTINCT wg.project_id ORDER BY wg.project_id), 'none'),
       @tcga_project IS NOT NULL AND GROUP_CONCAT(DISTINCT wg.project_id ORDER BY wg.project_id) = CAST(@tcga_project AS CHAR)
FROM seek_production.users u
JOIN seek_production.group_memberships gm ON gm.person_id = u.person_id AND gm.has_left = 0
JOIN seek_production.work_groups wg ON wg.id = gm.work_group_id
WHERE BINARY u.login = 'tcgamember';
SELECT 'account:tcgamember_seek_password_is_seed_user', '1', COUNT(*), COUNT(*) = 1
FROM seek_production.users t JOIN seek_production.users s ON BINARY s.login = 'user'
WHERE BINARY t.login = 'tcgamember' AND t.crypted_password = s.crypted_password AND t.salt = s.salt
  AND t.person_id IS NOT NULL AND t.activated_at IS NOT NULL;
SELECT 'account:tcgamember_django_user', '1', COUNT(*), COUNT(*) = 1
FROM dmac.auth_user t JOIN dmac.auth_user s ON s.username = 'user'
WHERE t.username = 'tcgamember' AND t.password = s.password AND t.is_superuser = 0 AND t.is_active = 1;
SELECT 'account:user_not_in_tcga', '0', COUNT(*), COUNT(*) = 0
FROM seek_production.users u
JOIN seek_production.group_memberships gm ON gm.person_id = u.person_id
JOIN seek_production.work_groups wg ON wg.id = gm.work_group_id
WHERE BINARY u.login = 'user' AND wg.project_id = @tcga_project;
SELECT 'account:superusers', 'demo 1, user 0, tcgamember 0',
       CONCAT('demo ', MAX(CASE WHEN username = 'demo' THEN is_superuser END),
              ', user ', MAX(CASE WHEN username = 'user' THEN is_superuser END),
              ', tcgamember ', IFNULL(MAX(CASE WHEN username = 'tcgamember' THEN is_superuser END), 'missing')),
       MAX(CASE WHEN username = 'demo' THEN is_superuser END) = 1
       AND MAX(CASE WHEN username = 'user' THEN is_superuser END) = 0
       AND MAX(CASE WHEN username = 'tcgamember' THEN is_superuser END) = 0
FROM dmac.auth_user WHERE username IN ('demo', 'user', 'tcgamember');

DROP TEMPORARY TABLE gs_v_tcga;

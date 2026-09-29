-- merge_tcga.sql: add TCGA from the dev-box dump (schemas dev_seek, dev_dmac) to the local production
-- snapshot (schemas seek_production, dmac) as a new project. Plan task M1; spec section 4.
-- Run by merge_tcga.sh on a fresh `load`; verify_merge.sql checks the result (gate M).
--
-- Kept as they are (no local id sits in their range): sample ids, their uuids, policies and
-- json_metadata byte for byte; the ISA policies; assay_assets ids. Remapped: the project, the
-- investigation, studies, assays, sample types (by title), sample attributes, permissions (new ids),
-- dmac internal assays (by title), assays_internal_assays and sample_types_clades. Every mapping is
-- recorded in dmac.gs_remap(kind, old_id, new_id), which stays for later tasks.
-- New ids are GREATEST(MAX(id) + 1, AUTO_INCREMENT): information_schema caches AUTO_INCREMENT, and
-- the cache can still describe the tables that merge_tcga.sh renamed away, so the cache is disabled.
-- Each block is one transaction; a failed assertion (dmac.gs_assert) aborts the client, which rolls
-- the open block back. A rerun starts from `merge_tcga.sh load`.

SET SESSION information_schema_stats_expiry = 0;
SET SESSION group_concat_max_len = 1000000;

DROP PROCEDURE IF EXISTS dmac.gs_assert;
DELIMITER //
CREATE PROCEDURE dmac.gs_assert(IN ok BOOLEAN, IN what VARCHAR(128))
BEGIN
  IF ok IS NULL OR NOT ok THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = what;
  END IF;
END //
DELIMITER ;

-- ---------------------------------------------------------------------------------------------
-- Block 1: scaffolding and preflight. The TCGA investigation is found by title in the dev data;
-- its samples are the Sample assets of its assays.
-- ---------------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dmac.gs_remap (
  kind VARCHAR(32) NOT NULL,
  old_id INT NOT NULL,
  new_id INT NOT NULL,
  PRIMARY KEY (kind, old_id),
  KEY gs_remap_new (kind, new_id)
) ENGINE=InnoDB DEFAULT CHARSET=ascii;
CALL dmac.gs_assert((SELECT COUNT(*) FROM dmac.gs_remap) = 0,
  'dmac.gs_remap is not empty: run merge_tcga.sh load before merging again');
CALL dmac.gs_assert((SELECT COUNT(*) FROM dev_seek.investigations WHERE BINARY title = 'TCGA') = 1,
  'expected exactly one dev investigation titled TCGA');
SET @dev_inv := (SELECT id FROM dev_seek.investigations WHERE BINARY title = 'TCGA');

DROP TABLE IF EXISTS dmac.gs_tmp_samples, dmac.gs_tmp_types, dmac.gs_tmp_policies, dmac.gs_tmp_local_uuids;
CREATE TABLE dmac.gs_tmp_samples (id INT PRIMARY KEY) ENGINE=InnoDB;
INSERT INTO dmac.gs_tmp_samples (id)
  SELECT DISTINCT aa.asset_id
  FROM dev_seek.assay_assets aa
  JOIN dev_seek.assays a ON a.id = aa.assay_id
  JOIN dev_seek.studies s ON s.id = a.study_id
  WHERE s.investigation_id = @dev_inv AND aa.asset_type = 'Sample';
CALL dmac.gs_assert((SELECT COUNT(*) FROM dmac.gs_tmp_samples) > 0, 'the TCGA investigation has no samples');
CALL dmac.gs_assert((SELECT COUNT(*) FROM dmac.gs_tmp_samples t
                     LEFT JOIN dev_seek.samples d ON d.id = t.id WHERE d.id IS NULL) = 0,
  'a TCGA assay asset names a sample that is not in dev_seek.samples');
CALL dmac.gs_assert((SELECT COUNT(*) FROM dmac.gs_tmp_samples t JOIN dev_seek.samples d ON d.id = t.id
                     WHERE d.policy_id IS NULL OR d.sample_type_id IS NULL
                        OR d.originating_data_file_id IS NOT NULL) = 0,
  'a TCGA sample has no policy, no type, or a data file link this merge does not remap');

CREATE TABLE dmac.gs_tmp_types (id INT PRIMARY KEY) ENGINE=InnoDB;
INSERT INTO dmac.gs_tmp_types (id)
  SELECT DISTINCT d.sample_type_id FROM dmac.gs_tmp_samples t JOIN dev_seek.samples d ON d.id = t.id;

-- Every TCGA ISA row must carry a policy and no link this merge does not remap.
CALL dmac.gs_assert((SELECT COUNT(*) FROM dev_seek.investigations WHERE id = @dev_inv AND policy_id IS NULL)
                  + (SELECT COUNT(*) FROM dev_seek.studies WHERE investigation_id = @dev_inv AND policy_id IS NULL)
                  + (SELECT COUNT(*) FROM dev_seek.assays a JOIN dev_seek.studies s ON s.id = a.study_id
                     WHERE s.investigation_id = @dev_inv
                       AND (a.policy_id IS NULL OR a.sample_type_id IS NOT NULL OR a.assay_stream_id IS NOT NULL
                            OR a.suggested_assay_type_id IS NOT NULL
                            OR a.suggested_technology_type_id IS NOT NULL)) = 0,
  'a TCGA ISA row has no policy or carries a link this merge does not remap');
CALL dmac.gs_assert((SELECT COUNT(*) FROM dev_seek.assay_assets aa
                     JOIN dev_seek.assays a ON a.id = aa.assay_id
                     JOIN dev_seek.studies s ON s.id = a.study_id
                     WHERE s.investigation_id = @dev_inv
                       AND (aa.asset_type <> 'Sample' OR aa.relationship_type_id IS NOT NULL)) = 0,
  'a TCGA assay asset is not a plain Sample link');
CALL dmac.gs_assert((SELECT COUNT(*) FROM dev_seek.sample_types d JOIN dmac.gs_tmp_types t ON t.id = d.id
                     WHERE d.template_id IS NOT NULL
                       AND NOT EXISTS (SELECT 1 FROM seek_production.sample_types l
                                       WHERE BINARY l.title = BINARY d.title)) = 0,
  'a new TCGA sample type has a template this merge does not carry');

CREATE TABLE dmac.gs_tmp_policies (id INT PRIMARY KEY) ENGINE=InnoDB;
INSERT INTO dmac.gs_tmp_policies (id)
  SELECT d.policy_id FROM dmac.gs_tmp_samples t JOIN dev_seek.samples d ON d.id = t.id
  UNION SELECT policy_id FROM dev_seek.investigations WHERE id = @dev_inv
  UNION SELECT policy_id FROM dev_seek.studies WHERE investigation_id = @dev_inv
  UNION SELECT a.policy_id FROM dev_seek.assays a JOIN dev_seek.studies s ON s.id = a.study_id
        WHERE s.investigation_id = @dev_inv;

-- Kept ids must be free locally.
CALL dmac.gs_assert((SELECT COUNT(*) FROM dmac.gs_tmp_samples t JOIN seek_production.samples l ON l.id = t.id) = 0,
  'a TCGA sample id already exists locally');
CALL dmac.gs_assert((SELECT COUNT(*) FROM dmac.gs_tmp_policies t JOIN seek_production.policies l ON l.id = t.id) = 0,
  'a TCGA policy id already exists locally');
CALL dmac.gs_assert((SELECT COUNT(*) FROM dev_seek.assay_assets aa
                     JOIN dev_seek.assays a ON a.id = aa.assay_id
                     JOIN dev_seek.studies s ON s.id = a.study_id
                     JOIN seek_production.assay_assets l ON l.id = aa.id
                     WHERE s.investigation_id = @dev_inv) = 0,
  'a TCGA assay_assets id already exists locally');
-- Byte-exact uuid comparison through an indexed copy: a BINARY() join on both sides is not an
-- equi-join to the optimizer and becomes a cartesian product.
CREATE TABLE dmac.gs_tmp_local_uuids (u VARBINARY(255) NOT NULL, KEY gs_tmp_local_uuids_u (u)) ENGINE=InnoDB;
INSERT INTO dmac.gs_tmp_local_uuids (u)
  SELECT CAST(uuid AS BINARY) FROM seek_production.samples WHERE uuid IS NOT NULL;
CALL dmac.gs_assert((SELECT COUNT(*) FROM dmac.gs_tmp_samples t JOIN dev_seek.samples d ON d.id = t.id
                     JOIN dmac.gs_tmp_local_uuids x ON x.u = CAST(d.uuid AS BINARY)) = 0,
  'a TCGA sample uuid already exists locally');

-- Permissions on TCGA policies all grant to the investigation's project(s).
CALL dmac.gs_assert((SELECT COUNT(*) FROM dev_seek.permissions p JOIN dmac.gs_tmp_policies t ON t.id = p.policy_id
                     WHERE p.contributor_type <> 'Project'
                        OR p.contributor_id NOT IN (SELECT project_id FROM dev_seek.investigations_projects
                                                    WHERE investigation_id = @dev_inv
                                                      AND project_id IS NOT NULL)) = 0,
  'a TCGA permission is not a grant to the TCGA project');
CALL dmac.gs_assert((SELECT COUNT(*) FROM dev_seek.investigations_projects WHERE investigation_id = @dev_inv) = 1,
  'expected the TCGA investigation in exactly one dev project');

-- The local text columns are utf8mb3: no TCGA ISA, type or attribute text may hold a four-byte
-- character (the sample text is checked in block 6, before the samples are inserted).
CALL dmac.gs_assert(
    (SELECT COUNT(*) FROM dev_seek.investigations WHERE id = @dev_inv
       AND (LENGTH(title) <> LENGTH(CONVERT(title USING utf8mb3))
            OR LENGTH(description) <> LENGTH(CONVERT(description USING utf8mb3))))
  + (SELECT COUNT(*) FROM dev_seek.studies WHERE investigation_id = @dev_inv
       AND (LENGTH(title) <> LENGTH(CONVERT(title USING utf8mb3))
            OR LENGTH(description) <> LENGTH(CONVERT(description USING utf8mb3))
            OR LENGTH(experimentalists) <> LENGTH(CONVERT(experimentalists USING utf8mb3))))
  + (SELECT COUNT(*) FROM dev_seek.assays a JOIN dev_seek.studies s ON s.id = a.study_id
     WHERE s.investigation_id = @dev_inv
       AND (LENGTH(a.title) <> LENGTH(CONVERT(a.title USING utf8mb3))
            OR LENGTH(a.description) <> LENGTH(CONVERT(a.description USING utf8mb3))))
  + (SELECT COUNT(*) FROM dev_seek.sample_types d JOIN dmac.gs_tmp_types t ON t.id = d.id
     WHERE LENGTH(d.title) <> LENGTH(CONVERT(d.title USING utf8mb3))
        OR LENGTH(d.description) <> LENGTH(CONVERT(d.description USING utf8mb3)))
  + (SELECT COUNT(*) FROM dev_seek.sample_attributes d JOIN dmac.gs_tmp_types t ON t.id = d.sample_type_id
     WHERE LENGTH(d.title) <> LENGTH(CONVERT(d.title USING utf8mb3))
        OR LENGTH(d.description) <> LENGTH(CONVERT(d.description USING utf8mb3))
        OR LENGTH(d.original_accessor_name) <> LENGTH(CONVERT(d.original_accessor_name USING utf8mb3))) = 0,
  'a TCGA ISA, type or attribute text column holds a four-byte character');

-- The accounts this merge needs, and the names it adds, must be as expected.
SET @demo_person := (SELECT person_id FROM seek_production.users WHERE BINARY login = 'demo');
SET @user_person := (SELECT person_id FROM seek_production.users WHERE BINARY login = 'user');
CALL dmac.gs_assert(@demo_person IS NOT NULL AND @user_person IS NOT NULL,
  'the seed SEEK logins demo and user must exist locally');
CALL dmac.gs_assert((SELECT COUNT(*) FROM dmac.auth_user WHERE username = 'user') = 1,
  'the seed Django user must exist in dmac.auth_user');
CALL dmac.gs_assert((SELECT COUNT(*) FROM seek_production.users WHERE login = 'tcgamember')
                  + (SELECT COUNT(*) FROM dmac.auth_user WHERE username = 'tcgamember')
                  + (SELECT COUNT(*) FROM seek_production.projects WHERE title = 'TCGA') = 0,
  'tcgamember or a project titled TCGA already exists locally');

-- Every dev contributor on a TCGA row becomes the seed test person demo, never a person by email.
INSERT INTO dmac.gs_remap (kind, old_id, new_id)
  SELECT 'person', c.contributor_id, @demo_person
  FROM (SELECT d.contributor_id FROM dmac.gs_tmp_samples t JOIN dev_seek.samples d ON d.id = t.id
        UNION SELECT contributor_id FROM dev_seek.investigations WHERE id = @dev_inv
        UNION SELECT contributor_id FROM dev_seek.studies WHERE investigation_id = @dev_inv
        UNION SELECT a.contributor_id FROM dev_seek.assays a JOIN dev_seek.studies s ON s.id = a.study_id
              WHERE s.investigation_id = @dev_inv
        UNION SELECT d.contributor_id FROM dev_seek.sample_types d JOIN dmac.gs_tmp_types t ON t.id = d.id) c
  WHERE c.contributor_id IS NOT NULL;

-- ---------------------------------------------------------------------------------------------
-- Block 2: the project, its work group (the most used local institution) and its default policy
-- (a copy of the newest local project's default policy and its project grant).
-- ---------------------------------------------------------------------------------------------
START TRANSACTION;
SET @new_project := GREATEST(
  (SELECT COALESCE(MAX(id), 0) + 1 FROM seek_production.projects),
  (SELECT COALESCE(AUTO_INCREMENT, 0) FROM information_schema.tables
   WHERE table_schema = 'seek_production' AND table_name = 'projects'));
SET @tmpl_project := (SELECT MAX(id) FROM seek_production.projects WHERE default_policy_id IS NOT NULL);
SET @tmpl_policy := (SELECT default_policy_id FROM seek_production.projects WHERE id = @tmpl_project);
SET @programme := (SELECT programme_id FROM seek_production.projects WHERE programme_id IS NOT NULL
                   GROUP BY programme_id ORDER BY COUNT(*) DESC, programme_id LIMIT 1);
SET @institution := (SELECT institution_id FROM seek_production.work_groups WHERE institution_id IS NOT NULL
                     GROUP BY institution_id ORDER BY COUNT(*) DESC, institution_id LIMIT 1);
CALL dmac.gs_assert(@tmpl_policy IS NOT NULL AND @institution IS NOT NULL,
  'no local default policy or institution to copy');

INSERT INTO seek_production.policies (name, sharing_scope, access_type, use_allowlist, use_denylist,
                                      created_at, updated_at)
  SELECT name, sharing_scope, access_type, use_allowlist, use_denylist, NOW(), NOW()
  FROM seek_production.policies WHERE id = @tmpl_policy;
SET @new_policy := LAST_INSERT_ID();
INSERT INTO seek_production.permissions (contributor_type, contributor_id, policy_id, access_type,
                                         created_at, updated_at)
  SELECT contributor_type, @new_project, @new_policy, access_type, NOW(), NOW()
  FROM seek_production.permissions
  WHERE policy_id = @tmpl_policy AND contributor_type = 'Project' AND contributor_id = @tmpl_project;
INSERT INTO seek_production.projects (id, title, description, created_at, updated_at, default_policy_id,
                                      first_letter, uuid, programme_id, default_license, use_default_policy)
  VALUES (@new_project, 'TCGA',
          'TCGA samples from the dev-box dump, merged into the local production snapshot for the graph_search proof of concept.',
          NOW(), NOW(), @new_policy, 'T', UUID(), @programme, 'CC-BY-4.0', 0);
INSERT INTO seek_production.work_groups (name, institution_id, project_id, created_at, updated_at)
  VALUES (NULL, @institution, @new_project, NOW(), NOW());
SET @new_wg := LAST_INSERT_ID();
INSERT INTO dmac.gs_remap (kind, old_id, new_id)
  SELECT 'project', project_id, @new_project
  FROM dev_seek.investigations_projects WHERE investigation_id = @dev_inv;
INSERT INTO dmac.gs_remap (kind, old_id, new_id)
  SELECT 'work_group', wg.id, @new_wg
  FROM dev_seek.work_groups wg
  JOIN dev_seek.investigations_projects ip ON ip.project_id = wg.project_id
  WHERE ip.investigation_id = @dev_inv;
COMMIT;

-- ---------------------------------------------------------------------------------------------
-- Block 3: the investigation, investigations_projects, studies and assays, with new ids; their
-- policies keep their ids (inserted in block 7) and their contributor becomes demo.
-- ---------------------------------------------------------------------------------------------
START TRANSACTION;
SET @new_inv := GREATEST(
  (SELECT COALESCE(MAX(id), 0) + 1 FROM seek_production.investigations),
  (SELECT COALESCE(AUTO_INCREMENT, 0) FROM information_schema.tables
   WHERE table_schema = 'seek_production' AND table_name = 'investigations'));
INSERT INTO dmac.gs_remap (kind, old_id, new_id) VALUES ('investigation', @dev_inv, @new_inv);
INSERT INTO seek_production.investigations (id, title, description, created_at, updated_at, first_letter,
                                            uuid, policy_id, contributor_id, other_creators,
                                            deleted_contributor, position, is_isa_json_compliant)
  SELECT ri.new_id, d.title, d.description, d.created_at, d.updated_at, d.first_letter,
         d.uuid, d.policy_id, pc.new_id, d.other_creators,
         d.deleted_contributor, d.position, d.is_isa_json_compliant
  FROM dev_seek.investigations d
  JOIN dmac.gs_remap ri ON ri.kind = 'investigation' AND ri.old_id = d.id
  LEFT JOIN dmac.gs_remap pc ON pc.kind = 'person' AND pc.old_id = d.contributor_id;
INSERT INTO seek_production.investigations_projects (project_id, investigation_id)
  SELECT rp.new_id, ri.new_id
  FROM dev_seek.investigations_projects ip
  JOIN dmac.gs_remap ri ON ri.kind = 'investigation' AND ri.old_id = ip.investigation_id
  JOIN dmac.gs_remap rp ON rp.kind = 'project' AND rp.old_id = ip.project_id;

SET @study_base := GREATEST(
  (SELECT COALESCE(MAX(id), 0) + 1 FROM seek_production.studies),
  (SELECT COALESCE(AUTO_INCREMENT, 0) FROM information_schema.tables
   WHERE table_schema = 'seek_production' AND table_name = 'studies'));
INSERT INTO dmac.gs_remap (kind, old_id, new_id)
  SELECT 'study', id, @study_base + ROW_NUMBER() OVER (ORDER BY id) - 1
  FROM dev_seek.studies WHERE investigation_id = @dev_inv;
INSERT INTO seek_production.studies (id, title, description, investigation_id, experimentalists, begin_date,
                                     created_at, updated_at, first_letter, uuid, policy_id, contributor_id,
                                     other_creators, deleted_contributor, position)
  SELECT rs.new_id, d.title, d.description, ri.new_id, d.experimentalists, d.begin_date,
         d.created_at, d.updated_at, d.first_letter, d.uuid, d.policy_id, pc.new_id,
         d.other_creators, d.deleted_contributor, d.position
  FROM dev_seek.studies d
  JOIN dmac.gs_remap rs ON rs.kind = 'study' AND rs.old_id = d.id
  JOIN dmac.gs_remap ri ON ri.kind = 'investigation' AND ri.old_id = d.investigation_id
  LEFT JOIN dmac.gs_remap pc ON pc.kind = 'person' AND pc.old_id = d.contributor_id;

INSERT INTO dmac.gs_remap (kind, old_id, new_id)
  SELECT 'assay_class', d.id, l.id
  FROM dev_seek.assay_classes d JOIN seek_production.assay_classes l ON BINARY l.title = BINARY d.title;
SET @assay_base := GREATEST(
  (SELECT COALESCE(MAX(id), 0) + 1 FROM seek_production.assays),
  (SELECT COALESCE(AUTO_INCREMENT, 0) FROM information_schema.tables
   WHERE table_schema = 'seek_production' AND table_name = 'assays'));
INSERT INTO dmac.gs_remap (kind, old_id, new_id)
  SELECT 'assay', a.id, @assay_base + ROW_NUMBER() OVER (ORDER BY a.id) - 1
  FROM dev_seek.assays a
  JOIN dev_seek.studies s ON s.id = a.study_id
  WHERE s.investigation_id = @dev_inv;
CALL dmac.gs_assert((SELECT COUNT(*) FROM dev_seek.assays d
                     JOIN dmac.gs_remap r ON r.kind = 'assay' AND r.old_id = d.id
                     LEFT JOIN dmac.gs_remap rc ON rc.kind = 'assay_class' AND rc.old_id = d.assay_class_id
                     WHERE d.assay_class_id IS NOT NULL AND rc.new_id IS NULL) = 0,
  'a TCGA assay class has no local class of the same title');
INSERT INTO seek_production.assays (id, title, description, created_at, updated_at, study_id, contributor_id,
                                    first_letter, assay_class_id, uuid, policy_id, assay_type_uri,
                                    technology_type_uri, suggested_assay_type_id, suggested_technology_type_id,
                                    other_creators, deleted_contributor, sample_type_id, position, assay_stream_id)
  SELECT r.new_id, d.title, d.description, d.created_at, d.updated_at, rs.new_id, pc.new_id,
         d.first_letter, rc.new_id, d.uuid, d.policy_id, d.assay_type_uri,
         d.technology_type_uri, NULL, NULL,
         d.other_creators, d.deleted_contributor, NULL, d.position, NULL
  FROM dev_seek.assays d
  JOIN dmac.gs_remap r ON r.kind = 'assay' AND r.old_id = d.id
  JOIN dmac.gs_remap rs ON rs.kind = 'study' AND rs.old_id = d.study_id
  LEFT JOIN dmac.gs_remap pc ON pc.kind = 'person' AND pc.old_id = d.contributor_id
  LEFT JOIN dmac.gs_remap rc ON rc.kind = 'assay_class' AND rc.old_id = d.assay_class_id;
COMMIT;

-- ---------------------------------------------------------------------------------------------
-- Block 4: sample types. A TCGA type whose title exists locally maps to that local type; the
-- others (A.MET, A.RPPA and D.ARR on this data) are inserted with new ids. Every TCGA type is
-- listed for the new project in projects_sample_types.
-- ---------------------------------------------------------------------------------------------
START TRANSACTION;
INSERT INTO dmac.gs_remap (kind, old_id, new_id)
  SELECT 'sample_type', d.id, l.id
  FROM dmac.gs_tmp_types t
  JOIN dev_seek.sample_types d ON d.id = t.id
  JOIN seek_production.sample_types l ON BINARY l.title = BINARY d.title;
SET @st_base := GREATEST(
  (SELECT COALESCE(MAX(id), 0) + 1 FROM seek_production.sample_types),
  (SELECT COALESCE(AUTO_INCREMENT, 0) FROM information_schema.tables
   WHERE table_schema = 'seek_production' AND table_name = 'sample_types'));
INSERT INTO dmac.gs_remap (kind, old_id, new_id)
  SELECT 'sample_type', d.id, @st_base + ROW_NUMBER() OVER (ORDER BY d.id) - 1
  FROM dmac.gs_tmp_types t
  JOIN dev_seek.sample_types d ON d.id = t.id
  WHERE NOT EXISTS (SELECT 1 FROM seek_production.sample_types l WHERE BINARY l.title = BINARY d.title);
INSERT INTO seek_production.sample_types (id, title, uuid, created_at, updated_at, first_letter, description,
                                          uploaded_template, contributor_id, deleted_contributor, template_id,
                                          other_creators)
  SELECT r.new_id, d.title, d.uuid, d.created_at, d.updated_at, d.first_letter, d.description,
         d.uploaded_template, pc.new_id, d.deleted_contributor, NULL,
         d.other_creators
  FROM dev_seek.sample_types d
  JOIN dmac.gs_remap r ON r.kind = 'sample_type' AND r.old_id = d.id AND r.new_id >= @st_base
  LEFT JOIN dmac.gs_remap pc ON pc.kind = 'person' AND pc.old_id = d.contributor_id;
INSERT INTO seek_production.projects_sample_types (project_id, sample_type_id)
  SELECT @new_project, new_id FROM dmac.gs_remap WHERE kind = 'sample_type' ORDER BY new_id;
COMMIT;

-- ---------------------------------------------------------------------------------------------
-- Block 5: sample attributes. Every attribute the dev data declares on a TCGA type and the local
-- type lacks (same title, byte for byte) is inserted: sample_attribute_type_id is mapped by the
-- attribute type's title (the ids differ between the instances); on an existing type it goes after
-- the type's last position and is not the title attribute; on a new type it keeps its dev
-- position and title flag.
-- ---------------------------------------------------------------------------------------------
START TRANSACTION;
INSERT INTO dmac.gs_remap (kind, old_id, new_id)
  SELECT 'sample_attribute_type', d.id, l.id
  FROM dev_seek.sample_attribute_types d
  JOIN seek_production.sample_attribute_types l ON BINARY l.title = BINARY d.title;
INSERT INTO dmac.gs_remap (kind, old_id, new_id)
  SELECT 'sample_attribute', da.id, la.id
  FROM dev_seek.sample_attributes da
  JOIN dmac.gs_remap st ON st.kind = 'sample_type' AND st.old_id = da.sample_type_id
  JOIN seek_production.sample_attributes la ON la.sample_type_id = st.new_id AND BINARY la.title = BINARY da.title;
SET @sa_base := GREATEST(
  (SELECT COALESCE(MAX(id), 0) + 1 FROM seek_production.sample_attributes),
  (SELECT COALESCE(AUTO_INCREMENT, 0) FROM information_schema.tables
   WHERE table_schema = 'seek_production' AND table_name = 'sample_attributes'));
INSERT INTO dmac.gs_remap (kind, old_id, new_id)
  SELECT 'sample_attribute', da.id, @sa_base + ROW_NUMBER() OVER (ORDER BY st.new_id, da.pos, da.id) - 1
  FROM dev_seek.sample_attributes da
  JOIN dmac.gs_remap st ON st.kind = 'sample_type' AND st.old_id = da.sample_type_id
  WHERE NOT EXISTS (SELECT 1 FROM dmac.gs_remap x WHERE x.kind = 'sample_attribute' AND x.old_id = da.id);
CALL dmac.gs_assert((SELECT COUNT(*) FROM dev_seek.sample_attributes da
                     JOIN dmac.gs_remap r ON r.kind = 'sample_attribute' AND r.old_id = da.id AND r.new_id >= @sa_base
                     LEFT JOIN dmac.gs_remap sat ON sat.kind = 'sample_attribute_type'
                                                AND sat.old_id = da.sample_attribute_type_id
                     WHERE sat.new_id IS NULL OR da.unit_id IS NOT NULL OR da.sample_controlled_vocab_id IS NOT NULL
                        OR da.isa_tag_id IS NOT NULL OR da.template_attribute_id IS NOT NULL
                        OR da.linked_sample_type_id IS NOT NULL) = 0,
  'a new attribute has an unmapped attribute type, unit, vocabulary, tag, template or link');
INSERT INTO seek_production.sample_attributes (id, title, sample_attribute_type_id, required, created_at, updated_at,
                                               pos, sample_type_id, unit_id, is_title, template_column_index,
                                               original_accessor_name, sample_controlled_vocab_id,
                                               linked_sample_type_id, pid, description, isa_tag_id,
                                               allow_cv_free_text, template_attribute_id)
  SELECT r.new_id, da.title, sat.new_id, da.required, da.created_at, da.updated_at,
         IF(st.new_id >= @st_base, da.pos, COALESCE(lm.max_pos, 0) + ROW_NUMBER() OVER w),
         st.new_id, NULL, IF(st.new_id >= @st_base, da.is_title, 0),
         IF(st.new_id >= @st_base, da.template_column_index, COALESCE(lm.max_tci, 0) + ROW_NUMBER() OVER w),
         da.original_accessor_name, NULL,
         NULL, da.pid, da.description, NULL,
         da.allow_cv_free_text, NULL
  FROM dev_seek.sample_attributes da
  JOIN dmac.gs_remap r ON r.kind = 'sample_attribute' AND r.old_id = da.id AND r.new_id >= @sa_base
  JOIN dmac.gs_remap st ON st.kind = 'sample_type' AND st.old_id = da.sample_type_id
  JOIN dmac.gs_remap sat ON sat.kind = 'sample_attribute_type' AND sat.old_id = da.sample_attribute_type_id
  LEFT JOIN (SELECT sample_type_id, MAX(pos) AS max_pos, MAX(template_column_index) AS max_tci
             FROM seek_production.sample_attributes GROUP BY sample_type_id) lm ON lm.sample_type_id = st.new_id
  WINDOW w AS (PARTITION BY st.new_id ORDER BY da.pos, da.id);
COMMIT;

-- ---------------------------------------------------------------------------------------------
-- Block 6: samples. id, uuid, policy_id and json_metadata are kept byte for byte; sample_type_id
-- and contributor_id go through gs_remap.
-- ---------------------------------------------------------------------------------------------
CALL dmac.gs_assert((SELECT COUNT(*) FROM dmac.gs_tmp_samples t JOIN dev_seek.samples d ON d.id = t.id
                     WHERE LENGTH(d.json_metadata) <> LENGTH(CONVERT(d.json_metadata USING utf8mb3))
                        OR LENGTH(d.title) <> LENGTH(CONVERT(d.title USING utf8mb3))
                        OR LENGTH(d.uuid) <> LENGTH(CONVERT(d.uuid USING utf8mb3))
                        OR LENGTH(d.other_creators) <> LENGTH(CONVERT(d.other_creators USING utf8mb3))
                        OR LENGTH(d.deleted_contributor) <> LENGTH(CONVERT(d.deleted_contributor USING utf8mb3))) = 0,
  'a TCGA sample text column holds a four-byte character');
START TRANSACTION;
INSERT INTO seek_production.samples (id, title, sample_type_id, json_metadata, uuid, contributor_id, policy_id,
                                     created_at, updated_at, first_letter, other_creators,
                                     originating_data_file_id, deleted_contributor)
  SELECT d.id, d.title, st.new_id, d.json_metadata, d.uuid, pc.new_id, d.policy_id,
         d.created_at, d.updated_at, d.first_letter, d.other_creators,
         NULL, d.deleted_contributor
  FROM dmac.gs_tmp_samples t
  JOIN dev_seek.samples d ON d.id = t.id
  JOIN dmac.gs_remap st ON st.kind = 'sample_type' AND st.old_id = d.sample_type_id
  LEFT JOIN dmac.gs_remap pc ON pc.kind = 'person' AND pc.old_id = d.contributor_id;
CALL dmac.gs_assert((SELECT COUNT(*) FROM dmac.gs_tmp_samples t JOIN seek_production.samples l ON l.id = t.id)
                  = (SELECT COUNT(*) FROM dmac.gs_tmp_samples),
  'not every TCGA sample was inserted');
COMMIT;

-- ---------------------------------------------------------------------------------------------
-- Block 7: projects_samples (the new project), policies (kept ids), permissions (new ids, granted
-- to the new project), assay_assets (kept ids, assay_id through gs_remap).
-- ---------------------------------------------------------------------------------------------
START TRANSACTION;
INSERT INTO seek_production.projects_samples (project_id, sample_id)
  SELECT rp.new_id, ps.sample_id
  FROM dev_seek.projects_samples ps
  JOIN dmac.gs_tmp_samples t ON t.id = ps.sample_id
  JOIN dmac.gs_remap rp ON rp.kind = 'project' AND rp.old_id = ps.project_id
  ORDER BY ps.sample_id;
INSERT INTO seek_production.policies (id, name, sharing_scope, access_type, use_allowlist, use_denylist,
                                      created_at, updated_at)
  SELECT p.id, p.name, p.sharing_scope, p.access_type, p.use_allowlist, p.use_denylist,
         p.created_at, p.updated_at
  FROM dmac.gs_tmp_policies t JOIN dev_seek.policies p ON p.id = t.id
  ORDER BY p.id;
CALL dmac.gs_assert((SELECT COUNT(*) FROM dmac.gs_tmp_policies t JOIN seek_production.policies l ON l.id = t.id)
                  = (SELECT COUNT(*) FROM dmac.gs_tmp_policies),
  'a TCGA policy is missing from dev_seek.policies');
INSERT INTO seek_production.permissions (contributor_type, contributor_id, policy_id, access_type,
                                         created_at, updated_at)
  SELECT p.contributor_type, rp.new_id, p.policy_id, p.access_type, p.created_at, p.updated_at
  FROM dev_seek.permissions p
  JOIN dmac.gs_tmp_policies t ON t.id = p.policy_id
  JOIN dmac.gs_remap rp ON rp.kind = 'project' AND rp.old_id = p.contributor_id
  WHERE p.contributor_type = 'Project'
  ORDER BY p.id;
INSERT INTO seek_production.assay_assets (id, assay_id, asset_id, version, created_at, updated_at,
                                          relationship_type_id, asset_type, direction)
  SELECT aa.id, ra.new_id, aa.asset_id, aa.version, aa.created_at, aa.updated_at,
         aa.relationship_type_id, aa.asset_type, aa.direction
  FROM dev_seek.assay_assets aa
  JOIN dmac.gs_remap ra ON ra.kind = 'assay' AND ra.old_id = aa.assay_id
  ORDER BY aa.id;
COMMIT;

-- ---------------------------------------------------------------------------------------------
-- Block 8: dmac. Internal assays map by title (new titles get new ids); assays_internal_assays
-- rows follow the assay remap; a new sample type gets its dev clade by the clade's title.
-- ---------------------------------------------------------------------------------------------
START TRANSACTION;
DROP TEMPORARY TABLE IF EXISTS dmac.gs_tmp_internal_assays;
CREATE TEMPORARY TABLE dmac.gs_tmp_internal_assays (id INT PRIMARY KEY)
  SELECT DISTINCT aia.internal_assay_id AS id
  FROM dev_dmac.assays_internal_assays aia
  JOIN dmac.gs_remap ra ON ra.kind = 'assay' AND ra.old_id = aia.assay_id
  WHERE aia.internal_assay_id IS NOT NULL;
INSERT INTO dmac.gs_remap (kind, old_id, new_id)
  SELECT 'internal_assay', d.id, l.id
  FROM dmac.gs_tmp_internal_assays t
  JOIN dev_dmac.internal_assays d ON d.id = t.id
  JOIN dmac.internal_assays l ON BINARY l.internal_assay_title = BINARY d.internal_assay_title;
SET @ia_base := GREATEST(
  (SELECT COALESCE(MAX(id), 0) + 1 FROM dmac.internal_assays),
  (SELECT COALESCE(AUTO_INCREMENT, 0) FROM information_schema.tables
   WHERE table_schema = 'dmac' AND table_name = 'internal_assays'));
INSERT INTO dmac.gs_remap (kind, old_id, new_id)
  SELECT 'internal_assay', d.id, @ia_base + ROW_NUMBER() OVER (ORDER BY d.id) - 1
  FROM dev_dmac.internal_assays d
  WHERE d.id IN (SELECT id FROM dmac.gs_tmp_internal_assays)
    AND NOT EXISTS (SELECT 1 FROM dmac.internal_assays l
                    WHERE BINARY l.internal_assay_title = BINARY d.internal_assay_title);
INSERT INTO dmac.internal_assays (id, internal_assay_title)
  SELECT r.new_id, d.internal_assay_title
  FROM dev_dmac.internal_assays d
  JOIN dmac.gs_remap r ON r.kind = 'internal_assay' AND r.old_id = d.id AND r.new_id >= @ia_base;
INSERT INTO dmac.assays_internal_assays (internal_assay_id, assay_id)
  SELECT ri.new_id, ra.new_id
  FROM dev_dmac.assays_internal_assays aia
  JOIN dmac.gs_remap ra ON ra.kind = 'assay' AND ra.old_id = aia.assay_id
  JOIN dmac.gs_remap ri ON ri.kind = 'internal_assay' AND ri.old_id = aia.internal_assay_id
  ORDER BY aia.id;
INSERT INTO dmac.gs_remap (kind, old_id, new_id)
  SELECT 'clade', d.id, l.id FROM dev_dmac.clades d JOIN dmac.clades l ON BINARY l.title = BINARY d.title;
INSERT INTO dmac.sample_types_clades (clade_id, sample_type_id)
  SELECT rc.new_id, st.new_id
  FROM dev_dmac.sample_types_clades d
  JOIN dmac.gs_remap st ON st.kind = 'sample_type' AND st.old_id = d.sample_type_id AND st.new_id >= @st_base
  JOIN dmac.gs_remap rc ON rc.kind = 'clade' AND rc.old_id = d.clade_id
  ORDER BY d.id;
DROP TEMPORARY TABLE dmac.gs_tmp_internal_assays;
COMMIT;

-- ---------------------------------------------------------------------------------------------
-- Block 9: accounts. tcgamember is a new person and SEEK user (password hash and salt copied from
-- the seed login `user`), a member of the new project's work group only, with a Django user whose
-- password hash is the seed `user`'s, so both accounts use the seed password. Not a superuser.
-- ---------------------------------------------------------------------------------------------
START TRANSACTION;
INSERT INTO seek_production.people (created_at, updated_at, first_name, last_name, email, status_id,
                                    first_letter, uuid, roles_mask)
  SELECT NOW(), NOW(), 'TCGA', 'Member', CONCAT('tcgamember', SUBSTRING(email, LOCATE('@', email))), status_id,
         'M', UUID(), 0
  FROM seek_production.people WHERE id = @user_person;
SET @tcga_person := LAST_INSERT_ID();
INSERT INTO seek_production.users (login, crypted_password, salt, created_at, updated_at, activated_at,
                                   person_id, uuid)
  SELECT 'tcgamember', crypted_password, salt, NOW(), NOW(), NOW(), @tcga_person, UUID()
  FROM seek_production.users WHERE BINARY login = 'user';
INSERT INTO seek_production.group_memberships (person_id, work_group_id, created_at, updated_at, has_left)
  VALUES (@tcga_person, @new_wg, NOW(), NOW(), 0);
INSERT INTO dmac.auth_user (password, last_login, is_superuser, username, first_name, last_name, email,
                            is_staff, is_active, date_joined)
  SELECT password, NULL, 0, 'tcgamember', 'TCGA', 'Member', CONCAT('tcgamember', SUBSTRING(email, LOCATE('@', email))),
         is_staff, 1, NOW(6)
  FROM dmac.auth_user WHERE username = 'user';
SET @tcga_auth := LAST_INSERT_ID();
INSERT INTO dmac.seek_user_profile (project, laboratory, user_id)
  SELECT 'TCGA', laboratory, @tcga_auth
  FROM dmac.seek_user_profile WHERE user_id = (SELECT id FROM dmac.auth_user WHERE username = 'user');
COMMIT;

DROP TABLE dmac.gs_tmp_samples, dmac.gs_tmp_types, dmac.gs_tmp_policies, dmac.gs_tmp_local_uuids;
DROP PROCEDURE dmac.gs_assert;

SELECT kind, COUNT(*) AS mapped, MIN(new_id) AS min_new_id, MAX(new_id) AS max_new_id
FROM dmac.gs_remap GROUP BY kind ORDER BY kind;

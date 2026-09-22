-- The production-shaped pre-state test_context_gen_mysql.py applies the generator's
-- SQL to. Synthetic rows only: nothing here is copied from any database.
--
-- The SHAPES are the point. Each CREATE TABLE is the shape a live instance has,
-- which is what the generator's own DDL is not:
--
--   sample_types_context  the seeded dump's: an id, no unique key, and no
--                         repository_attributes column
--   assay_context         the live table's: an id, no unique key, the widened
--                         clade and link columns
--   projects_context      the live table's: NO id, PRIMARY KEY (name), latin1,
--                         entity_type NOT NULL, and the JSON columns as
--                         longtext utf8mb4_bin with a json_valid CHECK
--   internal_assays,      the seeded dump's
--   assays_internal_assays
--
-- The rows the curated files depend on (the internal assays the mapping operations
-- name, the SEEK assays they move) are built by the test from context/*.json, so
-- they cannot drift from those files. The rows below are the ones no curated file
-- names, which is what every step that deletes or skips has to see.
SET NAMES utf8mb4;

CREATE TABLE `sample_types_context` (
  `id` int NOT NULL AUTO_INCREMENT,
  `sampletype_id` int DEFAULT NULL,
  `sample_type` varchar(32) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `description` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `required_metadata` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `standard_metadata` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `possible_metadata_fields` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `clade` varchar(64) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `sampletype_file_link` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `associated_assay_parents` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `associated_assay_children` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `parent_sampletypes` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `child_sampletypes` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `Tags` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `assay_context` (
  `id` int NOT NULL AUTO_INCREMENT,
  `assay_name` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `Description` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `Alternative_Assay_Names` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `internal_assay_id` int DEFAULT NULL,
  `Required_Parent_Sample_Types` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `Optional_Parent_Sample_Types` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `Children_Sample_Types` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `Parent_Clade_Type` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `Child_Clade_Type` varchar(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `AssaySheet_Link` varchar(512) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `AssociatedRepository` varchar(255) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci DEFAULT NULL,
  `Critical_Attributes` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `Protocols_Phrases` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `Protocols_UIDs` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  `Tags` text CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `projects_context` (
  `name` varchar(255) NOT NULL,
  `alternative_names` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
  `entity_type` varchar(64) NOT NULL,
  `project_id` int DEFAULT NULL,
  `parent_project` varchar(255) DEFAULT NULL,
  `pi` text,
  `research_focus` text,
  `key_data_types` longtext CHARACTER SET utf8mb4 COLLATE utf8mb4_bin,
  `description` text,
  `nih_reporter_link` text,
  `fairdomhub_published_link` text,
  `tags` text,
  PRIMARY KEY (`name`),
  KEY `idx_projects_context_project_id` (`project_id`),
  CONSTRAINT `projects_context_chk_1` CHECK (json_valid(`alternative_names`)),
  CONSTRAINT `projects_context_chk_2` CHECK (json_valid(`key_data_types`))
) ENGINE=InnoDB DEFAULT CHARSET=latin1;

CREATE TABLE `internal_assays` (
  `id` int NOT NULL AUTO_INCREMENT,
  `internal_assay_title` longtext COLLATE utf8mb4_unicode_ci NOT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `assays_internal_assays` (
  `id` int NOT NULL AUTO_INCREMENT,
  `internal_assay_id` int DEFAULT NULL,
  `assay_id` int DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Rows the curated source no longer names, and rows with no key at all. The update
-- has to delete every one of them.
INSERT INTO `sample_types_context` (`sample_type`, `name`, `description`) VALUES
  ('ZZ.STALE', 'Synthetic retired sample type', 'No curated row names this code.'),
  (NULL, 'Synthetic row with no key', NULL);
INSERT INTO `assay_context` (`assay_name`, `Description`, `internal_assay_id`) VALUES
  ('Synthetic Retired Assay', 'No curated row names this assay.', NULL),
  (NULL, 'Synthetic row with no key', NULL);
INSERT INTO `projects_context` (`name`, `entity_type`, `description`) VALUES
  ('Synthetic Retired Project', 'project', 'No curated row names this project.');

-- An internal assay and a SEEK assay link no mapping operation names. Both must
-- come through every apply untouched.
INSERT INTO `internal_assays` (`id`, `internal_assay_title`) VALUES
  (9001, 'Synthetic Unrelated Internal Assay');
INSERT INTO `assays_internal_assays` (`assay_id`, `internal_assay_id`) VALUES
  (990001, 9001);

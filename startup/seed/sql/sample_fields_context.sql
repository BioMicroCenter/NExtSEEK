-- Per-field definitions shown on the download workbook's README sheet.
--
-- Created out-of-band, like sample_types_context: no Django migration
-- references either table. `./startup.sh install` applies this file to `dmac`
-- whenever the table is absent — see the MissingTable entry in
-- startup/steps/schema_fixups.py — so a fresh or an existing install heals
-- itself. Apply it by hand only to an instance you are not reinstalling
-- (production), and fold the table into the seed on the next `dump-db` cycle.
--
-- sample_type is the scope. '' is the definition used on every tab; a sample
-- type code overrides it for that tab only. It is NOT NULL DEFAULT '' rather
-- than nullable on purpose: MySQL treats NULLs as distinct inside a unique
-- index, so a nullable column would accept two conflicting global rows for the
-- same field and uk_field_scope would never fire.
CREATE TABLE IF NOT EXISTS `sample_fields_context` (
  `id`          int NOT NULL AUTO_INCREMENT,
  `field_name`  varchar(255) NOT NULL,
  `sample_type` varchar(32)  NOT NULL DEFAULT '',
  `meaning`     text,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_field_scope` (`field_name`, `sample_type`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

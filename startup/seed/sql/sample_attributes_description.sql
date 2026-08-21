-- Copy the reviewed field definitions into seek_production.sample_attributes.
--
-- dmac.sample_attributes_unique is the source of truth. This file only projects it
-- onto SEEK's own `description` column so the sample attributes page can show
-- the text. Edit a meaning in dmac and re-run this file; never edit description
-- directly, or the two will disagree with no way to tell which is current.
--
-- Re-runnable: every matching row is refreshed on each run.
--
-- Needs SELECT on `dmac` and UPDATE on `seek_production`, so run it as root:
--   mysql -uroot -p < startup/seed/sql/sample_attributes_description.sql
--
-- Undo with ROLLBACK_sample_attributes_description.sql.

-- Pre-flight. `definitions_available` being 0 means dmac.sample_attributes_unique
-- is missing or unpopulated on this instance -- apply sample_attributes_unique.sql
-- and its data first, or the UPDATE below silently touches nothing.
SELECT COUNT(*) AS definitions_available
  FROM dmac.sample_attributes_unique
 WHERE sample_type = '';

SELECT COUNT(*) AS attribute_rows
  FROM seek_production.sample_attributes;

-- Matching is case-SENSITIVE. field_name is already utf8mb4_bin but title is
-- utf8mb4_unicode_ci, and the looser of the two would otherwise win: it is the
-- COLLATE clause on title that forces a binary comparison. Drop it and SEEK's
-- case-only attribute pairs -- Figure/figure, Bead_Coating/Bead_coating, five
-- such pairs in production -- collapse onto one definition.
--
-- Only global definitions (sample_type = '') are copied. Per-tab overrides stay
-- in dmac and are applied by the download README: honouring them here would
-- mean joining a sample type code onto sample_attributes.sample_type_id, an id
-- that does not agree across instances, which is precisely what joining on
-- field_name exists to avoid. The attributes page shows the global meaning.
UPDATE seek_production.sample_attributes sa
  JOIN dmac.sample_attributes_unique f
    ON sa.title COLLATE utf8mb4_bin = f.field_name
   AND f.sample_type = ''
   SET sa.description = f.meaning;

-- Verification. Rows left blank are attributes with no data anywhere to ground
-- a definition, which is the agreed behaviour, not a failure.
SELECT
    SUM(description IS NOT NULL AND description <> '') AS rows_populated,
    SUM(description IS NULL OR description = '')       AS rows_left_blank
  FROM seek_production.sample_attributes;

-- Definitions with nowhere to land: field names in live use that SEEK never
-- registered as attributes. They stay visible in the download README, which
-- reads dmac directly. A non-zero count here is expected.
SELECT COUNT(*) AS definitions_with_no_attribute_row
  FROM dmac.sample_attributes_unique f
 WHERE f.sample_type = ''
   AND NOT EXISTS (
         SELECT 1 FROM seek_production.sample_attributes sa
          WHERE sa.title COLLATE utf8mb4_bin = f.field_name);

-- Not handled deliberately: deleting a definition from dmac leaves its old text
-- behind here. Clearing unmatched rows would also erase any description written
-- through SEEK's own UI, which this file has no way to distinguish from its own.

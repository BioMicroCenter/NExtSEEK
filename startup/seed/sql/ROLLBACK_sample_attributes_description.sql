-- Undo sample_attributes_description.sql.
-- Safe because the column was empty on every row beforehand: this returns it
-- to that state rather than restoring prior content. Confirmed on production
-- 2026-08-20: 0 of 2,954 rows had a description.
UPDATE seek_production.sample_attributes SET description = NULL;

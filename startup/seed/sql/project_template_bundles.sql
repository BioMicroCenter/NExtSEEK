-- Named one-click template downloads, curated per project. A bundle is a label
-- plus an ordered list of sample type codes; clicking it POSTs those codes to
-- the existing /seek/templates/download/.
--
-- Ships EMPTY, and empty is a working state, not a broken one: a project with
-- no curated bundles falls back to bundles derived from its own connection rows,
-- one per internal assay. Curation overrides the fallback wholesale for that
-- project; it does not merge with it.
--
-- `codes` is a JSON array as text rather than a join table. A bundle is edited
-- as a unit, is never queried by member code, and is at most a handful of
-- entries, so a second table would buy nothing.
CREATE TABLE IF NOT EXISTS project_template_bundles (
  id         INT AUTO_INCREMENT PRIMARY KEY,
  project_id INT          NOT NULL,
  position   INT          NOT NULL DEFAULT 0,
  label      VARCHAR(128) NOT NULL,
  codes      TEXT         NOT NULL,
  KEY idx_project_position (project_id, position)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

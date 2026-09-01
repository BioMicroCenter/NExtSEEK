-- Which sample types an upload cannot omit, derived from Neo4j DERIVED_FROM
-- edges by `manage.py derive_sample_type_requirements`. Empty until that
-- command is run; the Download Templates page treats an empty table as "no
-- requirements known" and behaves exactly as it did before.
--
-- parent_codes is a JSON array ordered by descending share. One entry is a hard
-- requirement; two or three are alternatives, of which the upload needs one.
CREATE TABLE IF NOT EXISTS sample_type_requirements (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  child_code   VARCHAR(32)  NOT NULL,
  parent_codes TEXT         NOT NULL,
  coverage     DECIMAL(4,3) NOT NULL,
  support      INT          NOT NULL,
  assay_titles TEXT         NULL,
  source       VARCHAR(16)  NOT NULL DEFAULT 'graph',
  computed_at  DATETIME     NOT NULL,
  UNIQUE KEY uniq_child (child_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

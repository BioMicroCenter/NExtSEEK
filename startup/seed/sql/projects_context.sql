-- Curated context for SEEK projects: who runs one, what it studies, where it is
-- published. Read by the project page header; every field is optional and the
-- header falls back to the SEEK title and description when the row is absent.
--
-- Ships EMPTY. Production has rows, this stack does not, and there is no
-- committed export to generate them from. A project with no row renders exactly
-- the header it rendered before this feature existed.
--
-- alternative_names and key_data_types are JSON arrays stored as text, matching
-- how chat_nextseek's map_project already reads them: it json.loads the value
-- and falls back to splitting on '|'.
CREATE TABLE IF NOT EXISTS projects_context (
  id                        INT AUTO_INCREMENT PRIMARY KEY,
  name                      VARCHAR(255) NULL,
  alternative_names         TEXT         NULL,
  entity_type               VARCHAR(64)  NULL,
  project_id                INT          NULL,
  parent_project            VARCHAR(255) NULL,
  pi                        VARCHAR(255) NULL,
  research_focus            TEXT         NULL,
  key_data_types            TEXT         NULL,
  description               TEXT         NULL,
  nih_reporter_link         VARCHAR(512) NULL,
  fairdomhub_published_link VARCHAR(512) NULL,
  tags                      TEXT         NULL,
  KEY idx_project_id (project_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

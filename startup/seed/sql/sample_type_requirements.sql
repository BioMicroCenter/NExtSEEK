-- What the Download Templates picker adds for you, derived from Neo4j
-- DERIVED_FROM edges by `manage.py derive_sample_type_requirements`. Empty
-- until that command is run; the picker treats an empty table as "nothing
-- known" and behaves exactly as it did before the feature existed.
--
-- One row per (kind, trigger_code). The columns are named for the direction
-- the user experiences, not the direction of the graph edge, because the two
-- kinds run opposite ways:
--
--   kind='requires'   trigger_code is a CHILD type, add_codes are the PARENTS
--                     it cannot be uploaded without. One entry is a hard
--                     requirement; two or three are alternatives, of which the
--                     upload needs one. D.SEQ -> [DNA]; PAV -> [NHP, PAT].
--
--   kind='companion'  trigger_code is a PARENT type, add_codes holds the single
--                     CHILD that dominates what it produces -- not required,
--                     but almost always recorded alongside. NHP -> [PAV] at 82%.
--
-- Naming these child_code/parent_codes would have meant the child column held
-- a parent for every companion row.
--
-- `coverage` is the share add_codes accounts for: of the trigger's parents for
-- a requirement, of the trigger's children for a companion. It is stored for
-- auditing the rule, and is deliberately not shipped to the browser.
CREATE TABLE IF NOT EXISTS sample_type_requirements (
  id           INT AUTO_INCREMENT PRIMARY KEY,
  kind         VARCHAR(16)  NOT NULL DEFAULT 'requires',
  trigger_code VARCHAR(32)  NOT NULL,
  add_codes    TEXT         NOT NULL,
  coverage     DECIMAL(4,3) NOT NULL,
  support      INT          NOT NULL,
  assay_titles TEXT         NULL,
  source       VARCHAR(16)  NOT NULL DEFAULT 'graph',
  computed_at  DATETIME     NOT NULL,
  UNIQUE KEY uniq_kind_trigger (kind, trigger_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

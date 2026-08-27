-- Repair: publication_versions.visibility was left NULL.
--
-- Seek::ExplicitVersioning::VISIBILITY = {0=>:private, 1=>:registered_users, 2=>:public}
-- and default_visibility is :public. Rails writes it in a before_validation
-- callback (explicit_versioning.rb:145) that a raw INSERT bypasses.
--
-- With NULL, `visible?` falls through its case statement and returns nil, so
-- find_display_asset (assets_common.rb:20) reports
-- "This version is not available" with status :forbidden -- an HTTP 403 and a
-- redirect to the home page, for every user including the owner.
--
-- Idempotent: only touches rows that are still NULL.

SELECT 'before' AS stage, id, publication_id, version, visibility
  FROM publication_versions ORDER BY publication_id;

UPDATE publication_versions SET visibility = 2 WHERE visibility IS NULL;

SELECT 'after' AS stage, id, publication_id, version, visibility
  FROM publication_versions ORDER BY publication_id;
-- Expect visibility = 2 on every row.

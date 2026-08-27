-- SEEK publication transfer: fairdata-dev -> fairdata (prod)
-- 3 publication(s). Generated from a dev MySQL export.
--
-- Dev and prod ids are DIFFERENT id spaces. Nothing below carries a dev id:
--   dev person 1 is 'Demo Demo'; prod person 1 is 'Jingzhi Zhu'.
--   dev project 1 is 'Published Data', which does not exist on prod.
-- Both are supplied explicitly here instead.
--
-- Run against the seek_production schema on the prod host.
-- Afterwards, in the seek container:
--   bundle exec rake seek:repopulate_auth_lookup_tables RAILS_ENV=production
--   bundle exec rake seek:reindex_all RAILS_ENV=production
-- Until those run the records exist but are not listed or searchable.

SET @contrib := 139;   -- prod person id (contributor + manage permission)

-- CHECK 1 -- who these publications will be attributed to on prod.
-- Read the name before running anything below. Dev's contributor was the
-- 'Demo Demo' account; prod person 1 is a real named researcher.
SELECT id, first_name, last_name FROM people WHERE id = @contrib;

-- CHECK 2 -- which project(s) they will land in.
SELECT id, title FROM projects WHERE id IN (2);

-- CHECK 3 -- refuse to run twice. Should return 0 rows.
SELECT id, doi FROM publications WHERE doi IN ('10.1084/jem.20241760', '10.1126/sciadv.adq8229', '10.1101/2024.05.24.595747');

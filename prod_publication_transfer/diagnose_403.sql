-- Why do the 3 transferred publications return 403?
-- Read-only. Paste into DBeaver against prod `seek_production`.

-- 1. What we inserted: policy + permissions on the new publications.
SELECT p.id, p.doi, p.policy_id, p.contributor_id,
       po.access_type, po.sharing_scope,
       (SELECT GROUP_CONCAT(CONCAT(pe.contributor_type, ':', pe.contributor_id, '=', pe.access_type))
          FROM permissions pe WHERE pe.policy_id = po.id) AS perms
  FROM publications p
  JOIN policies po ON po.id = p.policy_id
 ORDER BY p.id;

-- 2. KNOWN-GOOD COMPARISON: what prod's existing assets use.
--    If access_type / sharing_scope differ from query 1, that is the answer.
SELECT 'study' AS kind, s.id, LEFT(s.title, 40) AS title,
       po.access_type, po.sharing_scope,
       (SELECT GROUP_CONCAT(CONCAT(pe.contributor_type, ':', pe.contributor_id, '=', pe.access_type))
          FROM permissions pe WHERE pe.policy_id = po.id) AS perms
  FROM studies s JOIN policies po ON po.id = s.policy_id
 ORDER BY s.id;

-- 3. The authorization cache for the new publications.
--    can_view = 0 means the rebuild ran and decided "no access".
--    No rows at all means it never ran for these.
SELECT asset_id, user_id, can_view, can_download, can_edit, can_manage
  FROM publication_auth_lookup
 ORDER BY asset_id, user_id
 LIMIT 30;

SELECT COUNT(*) AS lookup_rows FROM publication_auth_lookup;

-- 4. Which account the API token authenticates as.
--    (Token values are NOT selected.)
SELECT t.id AS token_id, t.title, u.id AS user_id, u.login,
       pe.id AS person_id, CONCAT(pe.first_name, ' ', pe.last_name) AS person
  FROM api_tokens t
  JOIN users u ON u.id = t.user_id
  LEFT JOIN people pe ON pe.id = u.person_id
 ORDER BY t.id;

-- 5. Is the contributor (139) actually a member of project 2?
--    A publication in a project its owner does not belong to can behave oddly.
SELECT gm.person_id, wg.project_id, pr.title AS project
  FROM group_memberships gm
  JOIN work_groups wg ON wg.id = gm.work_group_id
  JOIN projects pr ON pr.id = wg.project_id
 WHERE gm.person_id = 139;

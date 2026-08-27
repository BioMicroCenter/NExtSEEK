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

-- ---- dev id 1: 10.1084/jem.20241760 (Noncanonical T cell responses are associated with protection) ----
SET @proj := 2;   -- project mapping source: bib
START TRANSACTION;
INSERT INTO policies (name, sharing_scope, access_type, use_allowlist, use_denylist, created_at, updated_at)
  VALUES (NULL, NULL, 2, 0, 0, NOW(), NOW());
SET @pol := LAST_INSERT_ID();
INSERT INTO permissions (contributor_type, contributor_id, policy_id, access_type, created_at, updated_at)
  VALUES ('Person', @contrib, @pol, 4, NOW(), NOW());
INSERT INTO publications (pubmed_id, title, abstract, published_date, journal, first_letter, contributor_id, created_at, updated_at, doi, uuid, policy_id, citation, deleted_contributor, registered_mode, booktitle, publisher, editor, publication_type_id, url, version, license, other_creators)
  VALUES ('40192640', 'Noncanonical T cell responses are associated with protection from tuberculosis in mice and humans.', 'While control of Mycobacterium tuberculosis (Mtb) infection is generally understood to require Th1 cells and IFNgamma, infection produces a spectrum of immunological and pathological phenotypes in diverse human populations. By characterizing Mtb infection in mouse strains that model the genetic heterogeneity of an outbred population, we identified strains that control Mtb comparably to a standard IFNgamma-dependent mouse model but with substantially lower lung IFNgamma levels. We report that these mice have a significantly altered CD4 T cell profile that specifically lacks the terminal effector Th1 subset and that this phenotype is detectable before infection. These mice still require T cells to control bacterial burden but are less dependent on IFNgamma signaling. Instead, noncanonical immune features such as Th17-like CD4 and gammadeltaT cells correlate with low bacterial burden. We find the same Th17 transcriptional programs are associated with resistance to Mtb infection in humans, implicating specific non-Th1 T cell responses as a common feature of Mtb control across species.', '2025-07-07', 'J Exp Med', 'N', @contrib, '2026-08-25 15:33:50', '2026-08-25 15:33:50', '10.1084/jem.20241760', '4bbe6970-82c8-013f-1b32-76d2f039ec7a', @pol, 'J Exp Med. 2025 Jul 7;222(7):e20241760. doi: 10.1084/jem.20241760. Epub 2025 Apr  7.', NULL, '1', NULL, NULL, NULL, '1', NULL, '1', NULL, NULL);
SET @pub := LAST_INSERT_ID();
INSERT INTO publication_versions (publication_id, version, revision_comments, pubmed_id, title, abstract, published_date, journal, first_letter, contributor_id, created_at, updated_at, doi, uuid, policy_id, citation, deleted_contributor, registered_mode, booktitle, publisher, editor, publication_type_id, url, visibility)
  VALUES (@pub, '1', NULL, '40192640', 'Noncanonical T cell responses are associated with protection from tuberculosis in mice and humans.', 'While control of Mycobacterium tuberculosis (Mtb) infection is generally understood to require Th1 cells and IFNgamma, infection produces a spectrum of immunological and pathological phenotypes in diverse human populations. By characterizing Mtb infection in mouse strains that model the genetic heterogeneity of an outbred population, we identified strains that control Mtb comparably to a standard IFNgamma-dependent mouse model but with substantially lower lung IFNgamma levels. We report that these mice have a significantly altered CD4 T cell profile that specifically lacks the terminal effector Th1 subset and that this phenotype is detectable before infection. These mice still require T cells to control bacterial burden but are less dependent on IFNgamma signaling. Instead, noncanonical immune features such as Th17-like CD4 and gammadeltaT cells correlate with low bacterial burden. We find the same Th17 transcriptional programs are associated with resistance to Mtb infection in humans, implicating specific non-Th1 T cell responses as a common feature of Mtb control across species.', '2025-07-07', 'J Exp Med', 'N', @contrib, '2026-08-25 15:33:50', '2026-08-25 15:33:50', '10.1084/jem.20241760', '4bbe6970-82c8-013f-1b32-76d2f039ec7a', @pol, 'J Exp Med. 2025 Jul 7;222(7):e20241760. doi: 10.1084/jem.20241760. Epub 2025 Apr  7.', NULL, '1', NULL, NULL, NULL, '1', NULL, NULL);
SET @ver := LAST_INSERT_ID();
INSERT INTO projects_publication_versions (project_id, version_id) VALUES (@proj, @ver);
INSERT INTO publication_authors (first_name, last_name, publication_id, created_at, updated_at, author_index, person_id) VALUES
  ('M. K.', 'Proulx', @pub, '2026-08-25 15:33:50', '2026-08-25 15:33:50', 0, NULL),
  ('C. D.', 'Wiggins', @pub, '2026-08-25 15:33:50', '2026-08-25 15:33:50', 1, NULL),
  ('C. J.', 'Reames', @pub, '2026-08-25 15:33:50', '2026-08-25 15:33:50', 2, NULL),
  ('C.', 'Wu', @pub, '2026-08-25 15:33:50', '2026-08-25 15:33:50', 3, NULL),
  ('M. C.', 'Kiritsy', @pub, '2026-08-25 15:33:50', '2026-08-25 15:33:50', 4, NULL),
  ('P.', 'Xu', @pub, '2026-08-25 15:33:50', '2026-08-25 15:33:50', 5, NULL),
  ('J. C.', 'Gallant', @pub, '2026-08-25 15:33:50', '2026-08-25 15:33:50', 6, NULL),
  ('P. S.', 'Grace', @pub, '2026-08-25 15:33:50', '2026-08-25 15:33:50', 7, NULL),
  ('B. A.', 'Fenderson', @pub, '2026-08-25 15:33:50', '2026-08-25 15:33:50', 8, NULL),
  ('C. M.', 'Smith', @pub, '2026-08-25 15:33:50', '2026-08-25 15:33:50', 9, NULL),
  ('C. S.', 'Lindestam Arlehamn', @pub, '2026-08-25 15:33:50', '2026-08-25 15:33:50', 10, NULL),
  ('G.', 'Alter', @pub, '2026-08-25 15:33:50', '2026-08-25 15:33:50', 11, NULL),
  ('D. A.', 'Lauffenburger', @pub, '2026-08-25 15:33:50', '2026-08-25 15:33:50', 12, NULL),
  ('C. M.', 'Sassetti', @pub, '2026-08-25 15:33:50', '2026-08-25 15:33:50', 13, NULL);
INSERT INTO projects_publications (project_id, publication_id) VALUES (@proj, @pub);
COMMIT;
SELECT @pub AS new_publication_id, '10.1084/jem.20241760' AS doi;

-- ---- dev id 14: 10.1126/sciadv.adq8229 (High-dose intravenous BCG vaccination induces enhanced immun) ----
SET @proj := 2;   -- project mapping source: bib
START TRANSACTION;
INSERT INTO policies (name, sharing_scope, access_type, use_allowlist, use_denylist, created_at, updated_at)
  VALUES (NULL, NULL, 2, 0, 0, NOW(), NOW());
SET @pol := LAST_INSERT_ID();
INSERT INTO permissions (contributor_type, contributor_id, policy_id, access_type, created_at, updated_at)
  VALUES ('Person', @contrib, @pol, 4, NOW(), NOW());
INSERT INTO publications (pubmed_id, title, abstract, published_date, journal, first_letter, contributor_id, created_at, updated_at, doi, uuid, policy_id, citation, deleted_contributor, registered_mode, booktitle, publisher, editor, publication_type_id, url, version, license, other_creators)
  VALUES ('39742484', 'High-dose intravenous BCG vaccination induces enhanced immune signaling in the airways', NULL, '2025-01-01', 'Science Advances', 'H', @contrib, '2026-08-25 16:29:02', '2026-08-25 18:39:00', '10.1126/sciadv.adq8229', '01bc8cf0-82d0-013f-1b34-76d2f039ec7a', @pol, 'Science Advances 11(1)', NULL, '4', NULL, 'American Association for the Advancement of Science (AAAS)', NULL, '1', 'http://dx.doi.org/10.1126/sciadv.adq8229', '1', NULL, NULL);
SET @pub := LAST_INSERT_ID();
INSERT INTO publication_versions (publication_id, version, revision_comments, pubmed_id, title, abstract, published_date, journal, first_letter, contributor_id, created_at, updated_at, doi, uuid, policy_id, citation, deleted_contributor, registered_mode, booktitle, publisher, editor, publication_type_id, url, visibility)
  VALUES (@pub, '1', NULL, '39742484', 'High-dose intravenous BCG vaccination induces enhanced immune signaling in the airways', NULL, '2025-01-01', 'Science Advances', 'H', @contrib, '2026-08-25 16:29:02', '2026-08-25 18:39:00', '10.1126/sciadv.adq8229', '01bc8cf0-82d0-013f-1b34-76d2f039ec7a', @pol, 'Science Advances 11(1)', NULL, '4', NULL, 'American Association for the Advancement of Science (AAAS)', NULL, '1', 'http://dx.doi.org/10.1126/sciadv.adq8229', NULL);
SET @ver := LAST_INSERT_ID();
INSERT INTO projects_publication_versions (project_id, version_id) VALUES (@proj, @ver);
INSERT INTO publication_authors (first_name, last_name, publication_id, created_at, updated_at, author_index, person_id) VALUES
  ('Joshua M.', 'Peters', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 0, NULL),
  ('Edward B.', 'Irvine', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 1, NULL),
  ('Mohau S.', 'Makatsa', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 2, NULL),
  ('Jacob M.', 'Rosenberg', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 3, NULL),
  ('Marc H.', 'Wadsworth', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 4, NULL),
  ('Travis K.', 'Hughes', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 5, NULL),
  ('Matthew S.', 'Sutton', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 6, NULL),
  ('Sarah K.', 'Nyquist', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 7, NULL),
  ('Joshua D.', 'Bromley', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 8, NULL),
  ('Rajib', 'Mondal', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 9, NULL),
  ('Mario', 'Roederer', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 10, NULL),
  ('Robert A.', 'Seder', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 11, NULL),
  ('Patricia A.', 'Darrah', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 12, NULL),
  ('Galit', 'Alter', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 13, NULL),
  ('Chetan', 'Seshadri', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 14, NULL),
  ('JoAnne L.', 'Flynn', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 15, NULL),
  ('Alex K.', 'Shalek', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 16, NULL),
  ('Sarah M.', 'Fortune', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 17, NULL),
  ('Bryan D.', 'Bryson', @pub, '2026-08-25 16:29:02', '2026-08-25 16:29:02', 18, NULL);
INSERT INTO projects_publications (project_id, publication_id) VALUES (@proj, @pub);
COMMIT;
SELECT @pub AS new_publication_id, '10.1126/sciadv.adq8229' AS doi;

-- ---- dev id 17: 10.1101/2024.05.24.595747 (Systematic deconstruction of myeloid cell signaling in tuber) ----
SET @proj := 2;   -- project mapping source: bib
START TRANSACTION;
INSERT INTO policies (name, sharing_scope, access_type, use_allowlist, use_denylist, created_at, updated_at)
  VALUES (NULL, NULL, 2, 0, 0, NOW(), NOW());
SET @pol := LAST_INSERT_ID();
INSERT INTO permissions (contributor_type, contributor_id, policy_id, access_type, created_at, updated_at)
  VALUES ('Person', @contrib, @pol, 4, NOW(), NOW());
INSERT INTO publications (pubmed_id, title, abstract, published_date, journal, first_letter, contributor_id, created_at, updated_at, doi, uuid, policy_id, citation, deleted_contributor, registered_mode, booktitle, publisher, editor, publication_type_id, url, version, license, other_creators)
  VALUES (NULL, 'Systematic deconstruction of myeloid cell signaling in tuberculosis granulomas reveals IFN-γ, TGF-β, and time are associated with conserved myeloid diversity', NULL, '2024-05-01', NULL, 'S', @contrib, '2026-08-25 16:29:15', '2026-08-25 16:29:15', '10.1101/2024.05.24.595747', '092a6e50-82d0-013f-1b34-76d2f039ec7a', @pol, NULL, NULL, '4', NULL, 'openRxiv', NULL, '1', 'http://dx.doi.org/10.1101/2024.05.24.595747', '1', NULL, NULL);
SET @pub := LAST_INSERT_ID();
INSERT INTO publication_versions (publication_id, version, revision_comments, pubmed_id, title, abstract, published_date, journal, first_letter, contributor_id, created_at, updated_at, doi, uuid, policy_id, citation, deleted_contributor, registered_mode, booktitle, publisher, editor, publication_type_id, url, visibility)
  VALUES (@pub, '1', NULL, NULL, 'Systematic deconstruction of myeloid cell signaling in tuberculosis granulomas reveals IFN-γ, TGF-β, and time are associated with conserved myeloid diversity', NULL, '2024-05-01', NULL, 'S', @contrib, '2026-08-25 16:29:15', '2026-08-25 16:29:15', '10.1101/2024.05.24.595747', '092a6e50-82d0-013f-1b34-76d2f039ec7a', @pol, NULL, NULL, '4', NULL, 'openRxiv', NULL, '1', 'http://dx.doi.org/10.1101/2024.05.24.595747', NULL);
SET @ver := LAST_INSERT_ID();
INSERT INTO projects_publication_versions (project_id, version_id) VALUES (@proj, @ver);
INSERT INTO publication_authors (first_name, last_name, publication_id, created_at, updated_at, author_index, person_id) VALUES
  ('Joshua M.', 'Peters', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 0, NULL),
  ('Hannah P.', 'Gideon', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 1, NULL),
  ('Travis K.', 'Hughes', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 2, NULL),
  ('Cal', 'Gunnarson', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 3, NULL),
  ('Pauline', 'Maiello', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 4, NULL),
  ('Douaa', 'Mugahid', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 5, NULL),
  ('Sarah K.', 'Nyquist', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 6, NULL),
  ('Joshua D.', 'Bromley', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 7, NULL),
  ('Paul C.', 'Blainey', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 8, NULL),
  ('Beth F.', 'Junecko', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 9, NULL),
  ('Molly L.', 'Nelson', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 10, NULL),
  ('Douglas A.', 'Lauffenburger', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 11, NULL),
  ('Philana Ling', 'Lin', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 12, NULL),
  ('JoAnne L.', 'Flynn', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 13, NULL),
  ('Alex K.', 'Shalek', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 14, NULL),
  ('Sarah M.', 'Fortune', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 15, NULL),
  ('Joshua T.', 'Mattila', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 16, NULL),
  ('Bryan D.', 'Bryson', @pub, '2026-08-25 16:29:15', '2026-08-25 16:29:15', 17, NULL);
INSERT INTO projects_publications (project_id, publication_id) VALUES (@proj, @pub);
COMMIT;
SELECT @pub AS new_publication_id, '10.1101/2024.05.24.595747' AS doi;

-- Verification (expect one row per publication above):
SELECT p.id, p.doi, p.pubmed_id, p.policy_id, p.contributor_id,
       (SELECT COUNT(*) FROM publication_authors a WHERE a.publication_id = p.id) AS authors,
       (SELECT COUNT(*) FROM projects_publications j WHERE j.publication_id = p.id) AS projects,
       (SELECT COUNT(*) FROM publication_versions v WHERE v.publication_id = p.id) AS versions
  FROM publications p WHERE p.doi IN ('10.1084/jem.20241760', '10.1126/sciadv.adq8229', '10.1101/2024.05.24.595747');


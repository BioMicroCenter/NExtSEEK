-- Add DOI and PMID as attributes of every sample type.
--
-- 104 sample types, so 208 rows. Type 7 (Text) matches the overwhelming
-- convention (2,320 of 2,650 existing attributes) and matches DOI, so a blank
-- value on an unpublished sample cannot fail validation.
--
-- Appended at the end of each type's column order, optional, never the title.
--
-- IMPORTANT CONSEQUENCE: __getRecordToJson writes EVERY attribute of a sample
-- type into json_metadata, not just the ones an uploader filled in (see
-- seek/dbtable_sample.py:848). So from now on every uploaded sample carries a
-- DOI and PMID key, usually blank. Any reader MUST treat '', ' ' and absent as
-- equally "no paper" -- the same empty-string-not-null trap the Neo4j side has.
--
-- Idempotent: the NOT EXISTS guards mean a re-run inserts nothing.
-- Run with --default-character-set=utf8mb4.

SELECT COUNT(*) AS attributes_before FROM sample_attributes;

INSERT INTO sample_attributes
  (title, original_accessor_name, sample_attribute_type_id, required,
   created_at, updated_at, pos, template_column_index, sample_type_id, is_title, description)
SELECT 'DOI', 'DOI', 7, 0, NOW(), NOW(),
       COALESCE(m.maxpos, 0) + 1, COALESCE(m.maxpos, 0) + 1, st.id, 0,
       'DOI of the publication this sample appears in. Blank if the sample is not published.'
  FROM sample_types st
  LEFT JOIN (SELECT sample_type_id, MAX(pos) AS maxpos
               FROM sample_attributes GROUP BY sample_type_id) m
    ON m.sample_type_id = st.id
 WHERE NOT EXISTS (SELECT 1 FROM sample_attributes a
                    WHERE a.sample_type_id = st.id AND a.title = 'DOI');

-- Recomputes max(pos) after the DOI insert, so PMID lands immediately after it.
INSERT INTO sample_attributes
  (title, original_accessor_name, sample_attribute_type_id, required,
   created_at, updated_at, pos, template_column_index, sample_type_id, is_title, description)
SELECT 'PMID', 'PMID', 7, 0, NOW(), NOW(),
       COALESCE(m.maxpos, 0) + 1, COALESCE(m.maxpos, 0) + 1, st.id, 0,
       'PubMed ID of the publication this sample appears in. Blank if the sample is not published.'
  FROM sample_types st
  LEFT JOIN (SELECT sample_type_id, MAX(pos) AS maxpos
               FROM sample_attributes GROUP BY sample_type_id) m
    ON m.sample_type_id = st.id
 WHERE NOT EXISTS (SELECT 1 FROM sample_attributes a
                    WHERE a.sample_type_id = st.id AND a.title = 'PMID');

SELECT COUNT(*) AS attributes_after FROM sample_attributes;

-- Expect 104 and 104.
SELECT title, COUNT(*) AS sample_types_covered
  FROM sample_attributes WHERE title IN ('DOI','PMID') GROUP BY title;

-- Expect 0: every type should have both, and DOI immediately before PMID.
SELECT COUNT(*) AS types_missing_either FROM sample_types st
 WHERE (SELECT COUNT(*) FROM sample_attributes a
         WHERE a.sample_type_id = st.id AND a.title IN ('DOI','PMID')) <> 2;

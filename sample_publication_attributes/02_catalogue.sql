-- Register DOI and PMID in the NExtSEEK attribute catalogue.
--
-- sample_attributes_unique carries the curator- and assistant-facing meaning of
-- each attribute name. An empty sample_type means the meaning applies to every
-- type, which is what we want here.
--
-- Idempotent.

INSERT INTO dmac.sample_attributes_unique (field_name, sample_type, meaning)
SELECT 'DOI', '', 'DOI of the publication this sample appears in, e.g. 10.1084/jem.20241760. Blank when the sample is not published. A sample may be published in more than one paper.'
 WHERE NOT EXISTS (SELECT 1 FROM dmac.sample_attributes_unique
                    WHERE field_name = 'DOI' AND sample_type = '');

INSERT INTO dmac.sample_attributes_unique (field_name, sample_type, meaning)
SELECT 'PMID', '', 'PubMed ID of the publication this sample appears in, e.g. 40192640. Blank when the sample is not published or the paper is a preprint with no PubMed record.'
 WHERE NOT EXISTS (SELECT 1 FROM dmac.sample_attributes_unique
                    WHERE field_name = 'PMID' AND sample_type = '');

SELECT field_name, sample_type, LEFT(meaning, 60) AS meaning
  FROM dmac.sample_attributes_unique WHERE field_name IN ('DOI','PMID');

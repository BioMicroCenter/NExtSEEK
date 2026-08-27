-- Repair mojibake on prod publication 3 (dev id 17).
--
-- Cause: the first pilot run piped a UTF-8 file into `mysql` without
-- --default-character-set=utf8mb4, so the client read the UTF-8 bytes as
-- latin1 and re-encoded them. 'IFN-γ' became 'IFN-Î³'.
--
-- MUST be run WITH the charset flag, or it will corrupt the value again:
--   mysql --default-character-set=utf8mb4 ...
--
-- Idempotent: matches on DOI, sets the title from the dev source of truth.

SELECT 'before' AS stage, id, title FROM publications WHERE doi = '10.1101/2024.05.24.595747';

UPDATE publications        SET title = 'Systematic deconstruction of myeloid cell signaling in tuberculosis granulomas reveals IFN-γ, TGF-β, and time are associated with conserved myeloid diversity' WHERE doi = '10.1101/2024.05.24.595747';
UPDATE publication_versions SET title = 'Systematic deconstruction of myeloid cell signaling in tuberculosis granulomas reveals IFN-γ, TGF-β, and time are associated with conserved myeloid diversity' WHERE doi = '10.1101/2024.05.24.595747';

SELECT 'after' AS stage, id, title FROM publications WHERE doi = '10.1101/2024.05.24.595747';
-- Expect: IFN-γ, TGF-β  (Greek gamma and beta, not 'Î³' / 'Î²')

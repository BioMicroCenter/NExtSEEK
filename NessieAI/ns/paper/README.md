# NessieAI/ns/paper/

Stages 0 and 1 of paper reingest: turn what a curator typed (a PMID, PMCID, DOI, article link or an
attached PDF) into two typed files, `paper.json` and `evidence.json`. Pure Python over pydantic and
`requests`: no Django, no `nextseek_api` import (the boundary in `NessieAI/CLAUDE.md`). The run row, its
Celery task and the op's host seams are in `nextseek_api/paper_reingest/`.

Nothing here writes to SEEK or the graph, and nothing here calls a model. Reading the evidence and
proposing rows is a later piece.

## Surface

| Module | What it does |
|---|---|
| `models.py` | the artifacts: `PaperArtifact` (`paper.json`) and `EvidenceArtifact` (`evidence.json`), with `PaperRef`, `PaperText`, `DepositRecord`, `EvidenceItem`, `SkeletonRow` and `Gap`. Every model forbids unknown keys, so an artifact one version wrote and another reads fails loudly; `EvidenceArtifact` also refuses a duplicate item ID or a skeleton row citing an ID with no item |
| `identify.py` | `identify`: the typed text to one `PaperInput` (`pmid`, `pmcid`, `doi` or `file`), reading current and legacy PubMed, PMC and Europe PMC links; raises `IdentifyError` for anything else. `doi_readings`: each DOI printed in raw PDF text with its readings, a DOI broken over two lines at a hyphen read both with and without that hyphen (`doi_candidates` flattens them) |
| `fetch.py` | `Fetcher`, the only way the package reaches the internet, and `ALLOWED_HOSTS`. Retries only network errors (`requests.RequestException`) and 408, 429 and 5xx answers, waiting the backoff or a longer `Retry-After`; a wait past the deadline or `max_wait_s` is `SourceUnavailable` at once |
| `europepmc.py` | `resolve` (metadata), `full_text_xml` (open-access JATS) and `supplementary_zip`. A consortium author (`collectiveName`) is kept with `collective` set; the publication date keeps the precision Europe PMC gives (`firstPublicationDate`, else `pubYear`). A search answer of the wrong shape raises `UnreadableAnswer` |
| `jats.py` | `parse_jats`: sections, captions and the supplement list (each file once, its href URL-decoded to a file name), with evidence IDs. Broken XML, a root other than `<article>` or nesting too deep raises `JatsUnreadable`; an article with no body is empty text |
| `pdftext.py` | `pdf_to_text` and `split_sections`: an attached PDF to the same sections and IDs, pages joined by a form feed. A line broken at a hyphen inside an accession, DOI or URL is rejoined; a long paragraph is cut at a sentence end, else between words. `pypdf` is imported lazily, only here |
| `accessions.py` | `find_accessions`: deposit accessions and repository links in paper text. An ID of a hyphen-free repository written `<ID>-<digits>` (a range, or an ID broken over a line) is read both ways |
| `launch.py` | `launch_check` and `LaunchSeams`: stage 0, seconds, inside the op. Methods must be readable, and either a Data Availability section or at least one accession; a DOI already on a sample puts the paper in `update` mode. A PDF's DOI is used only when Europe PMC's title for it is on the PDF's first page (`_own_ref`, at most `MAX_PDF_DOIS` printed DOIs, each with its readings: a DOI broken at a line-end hyphen read both ways (`identify.doi_readings`) and one glued to the next word also tried without it). A reading Europe PMC answers badly is skipped; if none matched and one was skipped, the start is refused (`PdfDoiLookupFailed`); with no match, the paper has no DOI and starts in `new` mode |
| `start.py` | `start`: the body of `paper-reingest-start`. Curators only (`is_superuser`), only when the feature flag is on, and it turns each launch failure into a `StartRefused` with an `error_type`. An unreadable source answer is `source_bad_answer`; on the PDF path (its DOI lookup) the message does not ask for the PDF |
| `deposits/` | one adapter per repository, `ADAPTERS[repo](accession, fetcher) -> list[DepositRecord]`: `geo.py`, `ena.py`, `pride.py`, and `repos.py` for Zenodo, figshare and the Stanford Digital Repository (`sdr`). A GEO SuperSeries also returns each SubSeries' own project record (its samples come with the SuperSeries'). A checksum is typed only as md5 or sha1; any other digest stays verbatim in the file record's `checksum` field. An SDR file with `publish="no"` gets no record, only a name in the project's `unpublished_files` |
| `si.py` | `supplement_items`: the supplementary zip to evidence items, and gaps for what could not be read. A listed supplement matches a member by file name, URL-decoded and case-folded; an image the paper does not list is one of its figures, skipped. Workbook cells read as a curator sees them (`cell_text`). Decompression is bounded per member, per workbook (`MAX_WORKBOOK_UNPACKED_BYTES`) and per archive (`MAX_ARCHIVE_UNPACKED_BYTES`) |
| `evidence.py` | `paper_items`, `deposit_items`, `field_id`, `file_id`: records to quotable items |
| `skeleton.py` | deposit records to skeleton rows: one GEO row per GSM (its SRA runs folded in through the SRX link), one ENA row per experiment for runs with no GSM, a PRIDE `raw` row per RAW file and an `analysis` row per SEARCH or RESULT file, one `file` row per Zenodo, figshare or SDR file |
| `acquire.py` | `acquire`: `paper.json` to `evidence.json`. Reads the supplement (Europe PMC's zip for any open-access paper with a PMCID, a PDF-read one included), finds every accession, fetches each deposit once (projects before their parts; a GSM cited without its series fetches its own SRX) and builds the skeleton. A missing deposit or supplement, or a deposit that answers with something unreadable, is a `Gap` (logged); a source that is down raises `SourceUnavailable` and a spent deadline `OutOfTime` |
| `store.py` | `save_artifact` and `load_artifact`: content-addressed files, one folder per run under `NEXTSEEK_PAPER_REINGEST_DIR` |
| `dates.py` | lenient date parsing (ISO, and GEO's `Jan 05 2024`). `parse_partial_date` keeps a year or a month as `YYYY` or `YYYY-MM`, never padded to a day; `PaperRef.published` holds that form |

## Evidence IDs

Every quotable thing in `evidence.json` has an ID that never changes for the same input, so a later
stage can cite it and a reviewer can find it.

| Form | Is |
|---|---|
| `E:<slug>:p<n>` | paragraph `n` (counted per section, in document order) of the paper's section `<slug>`, such as `E:methods:p3` |
| `E:cap:<n>` | the paper's figure or table caption `n` |
| `E:si:<file>!<cell>` or `E:si:<file>!<sheet>!<cell>` | a cell of a supplementary csv, tsv or workbook |
| `E:si:<file>!p<n>` | paragraph `n` of a supplementary PDF or text file |
| `E:<repo>:<record_id>:<field>` | one field of a deposit record, such as `E:geo:GSM8007257:title` |
| `E:file:<repo>:<record_id>:<name>` | one file of a deposit record, with its size and checksum. A second file of the same name in the same record gets `#2`, a third `#3`, and so on |

## The allowlist rule

The package reaches only the hosts in `fetch.py` `ALLOWED_HOSTS`, and only over https. Redirects are
followed by hand, so a redirect to a host outside the list is refused (`HostNotAllowed`). A body over
the byte cap is refused. A 5xx, a 408, a 429, a timeout or a dropped connection is retried with backoff, or
the server's `Retry-After` when longer (a wait past `max_wait_s` or the deadline is not slept), and then
raised as `SourceUnavailable`, which the run turns into `paused`; any other 4xx is `SourceNotFound`. To
read a new repository, add its adapter under `deposits/` and its host to the list in the same change.
Supplementary files come from Europe PMC's `supplementaryFiles` zip, never from publisher pages.

## Running and testing

The tests need no Django and no network (`NessieAI/tests/ns/paper/fakes.py` stands in for the internet):

```
PYTHONPATH="$PWD" uv run --no-project --with pytest --with 'pydantic>=2.13' --with requests \
  --with openpyxl --with pypdf python -m pytest NessieAI/tests/ns/paper -q --noconftest -p no:cacheprovider
```

When an adapter may have fallen behind a repository's real API, run `scripts/paper_reingest_live_check.py`
by hand (it uses the network; `scripts/README.md`). It prints counts only.

## Depends on / depended on by

- Depends on `pydantic`, `requests`, `openpyxl` and `pypdf`; nothing in the repo.
- Called by `NessieAI/ns/granular.py` (`_paper_reingest_start`, importing `start`) and by
  `nextseek_api/paper_reingest/` (the run service, the Celery task, the host seams and the operator
  command).

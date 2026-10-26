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
| `identify.py` | `identify`: the typed text to one `PaperInput` (`pmid`, `pmcid`, `doi` or `file`); raises `IdentifyError` for anything else |
| `fetch.py` | `Fetcher`, the only way the package reaches the internet, and `ALLOWED_HOSTS` |
| `europepmc.py` | `resolve` (metadata), `full_text_xml` (open-access JATS) and `supplementary_zip` |
| `jats.py` | `parse_jats`: sections, captions and the supplement list, with evidence IDs |
| `pdftext.py` | `pdf_to_text` and `split_sections`: an attached PDF to the same sections and IDs. `pypdf` is imported lazily, only here |
| `accessions.py` | `find_accessions`: deposit accessions and repository links in paper text |
| `launch.py` | `launch_check` and `LaunchSeams`: stage 0, seconds, inside the op. Methods must be readable, and either a Data Availability section or at least one accession; a DOI already on a sample puts the paper in `update` mode |
| `start.py` | `start`: the body of `paper-reingest-start`. Curators only (`is_superuser`), only when the feature flag is on, and it turns each launch failure into a `StartRefused` with an `error_type` |
| `deposits/` | one adapter per repository, `ADAPTERS[repo](accession, fetcher) -> list[DepositRecord]`: `geo.py`, `ena.py`, `pride.py`, and `repos.py` for Zenodo, figshare and the Stanford Digital Repository (`sdr`) |
| `si.py` | `supplement_items`: the supplementary zip to evidence items, and gaps for what could not be read |
| `evidence.py` | `paper_items`, `deposit_items`, `field_id`, `file_id`: records to quotable items |
| `skeleton.py` | deposit records to skeleton rows: one GEO row per GSM (its SRA runs folded in through the SRX link), one ENA row per experiment for runs with no GSM, a PRIDE `raw` row per RAW file and an `analysis` row per SEARCH or RESULT file, one `file` row per Zenodo, figshare or SDR file |
| `acquire.py` | `acquire`: `paper.json` to `evidence.json`. Reads the supplement, finds every accession, fetches each deposit once (projects before their parts) and builds the skeleton. A missing deposit or supplement, or a deposit that answers with something unreadable, is a `Gap` (logged); a source that is down raises `SourceUnavailable` and a spent deadline `OutOfTime` |
| `store.py` | `save_artifact` and `load_artifact`: content-addressed files, one folder per run under `NEXTSEEK_PAPER_REINGEST_DIR` |
| `dates.py` | lenient date parsing (ISO, and GEO's `Jan 05 2024`) |

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
the byte cap is refused. A 5xx, a 429, a timeout or a dropped connection is retried with backoff and then
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

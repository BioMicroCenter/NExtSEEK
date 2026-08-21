# Publication (DOI/PMID) Links for Samples — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show which published paper a sample appears in — in sample search, on the sample detail page, and through Nessie — and support the reverse lookup "what samples were used in \<study\>?".

**Architecture:** The DOI and PMID are attributes of the study, held in both `seek_production.studies` (which the search UI reads) and on Neo4j `Study` nodes (which Nessie reads). A sample's paper is inherited from study membership — one hop along `IN_STUDY`, or one join through `assay_assets` in SQL. One command extracts DOIs from study-description prose, has a curator approve them, and writes both stores.

**Tech Stack:** Django 4 + Mezzanine, MySQL 8 (two schemas on one server), Neo4j, pytest, `uv`.

**Design document:** `docs/2026-08-21-publication-links-design.md` — read it first, including its Revision history. Every finding cited below was verified against the running stack on 2026-08-21.

## Revision note

This plan replaces an earlier one built around SEEK's `publications` table,
`relationships` links, `:Publication` nodes and `REPORTED_IN` edges. All of that
is gone, along with the per-sample override table and the write API that curated
it. Task 1 below is unchanged from that plan; everything after it is new.

## Global Constraints

- **`uv`, never `pip`.** Run everything as `uv run …`. Do not hand-edit dependency pins.
- **No new dependencies.** `requests` (pyproject.toml:92) and stdlib `difflib` cover the fill command.
- **Never interpolate user text into SQL.** The existing search builder concatenates strings; every value this plan adds to it must be an `int()` first, or go through a parameterized query.
- **DOIs are stored lowercased** for comparison; DOIs are case-insensitive by specification.
- **No Django migration may create the MySQL columns.** The entrypoint runs a plain `migrate`, which touches only the default database (design finding 11). Column creation is done explicitly by the fill command, guarded by an `information_schema` lookup — MySQL 8 has no `ADD COLUMN IF NOT EXISTS`.
- **Every graph write must be idempotent** and must work whether or not `doi`/`pmid` already exist on `Study`. They do on dev and prod; they do not on the local docker stack (design finding 10).
- **Tests must not require a database.** Existing suite style (`seek/tests/test_search_pubmed_nested.py`) is pure-unit. Use stubs for anything touching MySQL or Neo4j. Run with `uv run pytest`.
- **`seek/publications.py` must not import `seek/dbtable_sample.py`.** That module pulls in MySQLdb, pandas and the neo4j driver at import time; keeping the dependency one-way is what keeps these tests fast.
- **Commit after every task**, using conventional commits with a module scope.

---

### Task 1: DOI extraction from study description prose

Pure text parsing, no database and no network. This is separated out because the input is unstructured prose written by many people over several years, and it is the part most likely to be wrong.

**Files:**
- Create: `seek/doi_extract.py`
- Test: `seek/tests/test_doi_extract.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `Candidate` — frozen dataclass with fields `kind: str` (`"doi"` | `"pmc"` | `"unresolvable"`), `value: str`, `raw: str`, `note: str`
  - `normalize_doi(raw: str) -> str | None`
  - `extract_publication_candidates(description: str | None) -> list[Candidate]`
  - `REJECTED_HOSTS: tuple[str, ...]`

- [ ] **Step 1: Write the failing test**

Create `seek/tests/test_doi_extract.py`. Every string below is a real substring of a study description in the production database — do not simplify them.

```python
"""Extraction of publication references from real SEEK study descriptions.

Every fixture string here was taken verbatim from seek_production.studies on
2026-08-21. They are ugly on purpose: that is what the extractor must survive.
"""

import pytest

from seek.doi_extract import (
    Candidate,
    extract_publication_candidates,
    normalize_doi,
)


class TestNormalizeDoi:
    def test_plain_doi(self):
        assert normalize_doi("10.1038/s41590-021-01066-1") == "10.1038/s41590-021-01066-1"

    def test_strips_trailing_period(self):
        assert normalize_doi("10.1371/journal.pone.0249477.") == "10.1371/journal.pone.0249477"

    def test_strips_biorxiv_version_suffix(self):
        assert normalize_doi("10.1101/2022.01.29.22269829v1") == "10.1101/2022.01.29.22269829"

    def test_strips_version_and_full_suffix(self):
        assert normalize_doi("10.1101/2024.05.24.595747v1.full") == "10.1101/2024.05.24.595747"

    def test_strips_url_fragment(self):
        assert normalize_doi("10.1038/s41596-024-01076-x#Sec43") == "10.1038/s41596-024-01076-x"

    def test_lowercases(self):
        assert normalize_doi("10.1039/D2DT03848J") == "10.1039/d2dt03848j"

    def test_truncated_doi_is_rejected(self):
        # Study 31's description contains exactly this and nothing more.
        assert normalize_doi("10.3390/") is None

    def test_non_doi_is_rejected(self):
        assert normalize_doi("not-a-doi") is None


class TestExtractFromRealDescriptions:
    def test_doi_org_url(self):
        cands = extract_publication_candidates(
            "https://doi.org/10.1101/2021.09.30.462577"
        )
        assert cands == [
            Candidate("doi", "10.1101/2021.09.30.462577", "10.1101/2021.09.30.462577", "")
        ]

    def test_medrxiv_content_url(self):
        cands = extract_publication_candidates(
            "https://www.medrxiv.org/content/10.1101/2022.01.29.22269829v1 "
        )
        assert [c.value for c in cands] == ["10.1101/2022.01.29.22269829"]

    def test_science_org_doi_path(self):
        cands = extract_publication_candidates(
            "https://www.science.org/doi/10.1126/sciadv.adq8229"
        )
        assert [c.value for c in cands] == ["10.1126/sciadv.adq8229"]

    def test_acs_doi_full_path(self):
        cands = extract_publication_candidates(
            "https://pubs.acs.org/doi/full/10.1021/acsomega.4c03959"
        )
        assert [c.value for c in cands] == ["10.1021/acsomega.4c03959"]

    def test_truncated_doi_is_flagged_not_dropped(self):
        cands = extract_publication_candidates("https://doi.org/10.3390/")
        assert len(cands) == 1
        assert cands[0].kind == "unresolvable"
        assert cands[0].raw == "10.3390/"

    def test_nature_article_url_maps_to_doi(self):
        cands = extract_publication_candidates(
            "DOI: https://www.nature.com/articles/s41596-024-01076-x#Sec43"
        )
        assert [(c.kind, c.value) for c in cands] == [
            ("doi", "10.1038/s41596-024-01076-x")
        ]

    def test_pmc_url_yields_pmc_id(self):
        cands = extract_publication_candidates(
            "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8439179/"
        )
        assert [(c.kind, c.value) for c in cands] == [("pmc", "PMC8439179")]

    def test_imgur_figure_is_rejected(self):
        assert extract_publication_candidates("![](https://i.imgur.com/dJLbsO4.png)") == []

    def test_geo_accession_is_rejected(self):
        assert extract_publication_candidates(
            "https://www.ncbi.nlm.nih.gov/geo/query/acc.cgi?acc=GSE267774"
        ) == []

    def test_omero_link_is_rejected(self):
        assert extract_publication_candidates(
            "https://omero.mit.edu/webclient/?show=project-559"
        ) == []

    def test_publisher_url_without_identifier_is_flagged(self):
        cands = extract_publication_candidates(
            "https://www.sciencedirect.com/science/article/abs/pii/S0142961224002655"
        )
        assert [c.kind for c in cands] == ["unresolvable"]

    def test_figure_alongside_real_doi_does_not_interfere(self):
        cands = extract_publication_candidates(
            "![](https://i.imgur.com/8TPKDA6.jpg)\n\nhttps://doi.org/10.1101/2021.09.30.462577"
        )
        assert [c.value for c in cands] == ["10.1101/2021.09.30.462577"]

    def test_none_description(self):
        # 3 of the 51 studies have description IS NULL.
        assert extract_publication_candidates(None) == []

    def test_empty_description(self):
        assert extract_publication_candidates("") == []

    def test_duplicate_doi_mentioned_twice_yields_one_candidate(self):
        cands = extract_publication_candidates(
            "see 10.1126/sciadv.adq6652 and also https://doi.org/10.1126/sciadv.adq6652"
        )
        assert [c.value for c in cands] == ["10.1126/sciadv.adq6652"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest seek/tests/test_doi_extract.py -v
```

Expected: collection error — `ModuleNotFoundError: No module named 'seek.doi_extract'`.

- [ ] **Step 3: Write the implementation**

Create `seek/doi_extract.py`:

```python
"""Extract publication references from free-text SEEK study descriptions.

Pure functions: no database, no network, no Django settings. The input is prose
written by many people over several years, so every rule here is derived from a
string that actually appears in seek_production.studies rather than from what a
well-formed citation ought to look like.

See docs/2026-08-21-publication-links-design.md, "Backfill".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Hosts that appear in study descriptions but never denote a paper.
REJECTED_HOSTS: tuple[str, ...] = (
    "i.imgur.com",
    "imgur.com",
    "omero.mit.edu",
)

#: A GEO accession lives on an NCBI host but is data, not a publication.
_GEO_PATH = "/geo/"

#: Deliberately permissive after the slash (``*`` not ``+``) so that a truncated
#: DOI such as ``10.3390/`` still matches and can be reported as unresolvable
#: rather than silently vanishing.
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"'<>)\]]*")

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+")
_NATURE_RE = re.compile(r"nature\.com/articles/([A-Za-z0-9\-.]+)")
_PMC_RE = re.compile(r"/pmc/articles/(PMC\d+)", re.IGNORECASE)

#: bioRxiv/medRxiv append a version, and sometimes a view suffix, to the DOI.
_VERSION_SUFFIX_RE = re.compile(r"v\d+(\.full(-text)?)?$", re.IGNORECASE)

_TRAILING_JUNK = ".,;:)]}>\"'"


@dataclass(frozen=True)
class Candidate:
    """One publication reference found in a description.

    ``kind`` is ``"doi"`` (``value`` is a normalized DOI), ``"pmc"`` (``value``
    is a PMC id needing a lookup), or ``"unresolvable"`` (``value`` is empty and
    a human must decide — ``note`` says why).
    """

    kind: str
    value: str
    raw: str
    note: str = ""


def normalize_doi(raw: str) -> str | None:
    """Normalize a DOI-ish string, or return None if it is not usable.

    Returns None for a prefix with no suffix (``10.3390/``), which is a real case
    in the data and must never be guessed at.
    """
    doi = raw.strip()
    doi = doi.split("#", 1)[0]
    doi = doi.rstrip(_TRAILING_JUNK)
    doi = _VERSION_SUFFIX_RE.sub("", doi)
    doi = doi.rstrip(_TRAILING_JUNK)

    if not doi.lower().startswith("10."):
        return None
    _, _, suffix = doi.partition("/")
    if not suffix:
        return None
    return doi.lower()


def _is_rejected(url: str) -> bool:
    """True for URLs that are figures, image servers, or data accessions."""
    lowered = url.lower()
    if any(host in lowered for host in REJECTED_HOSTS):
        return True
    return "ncbi.nlm.nih.gov" in lowered and _GEO_PATH in lowered


def extract_publication_candidates(description: str | None) -> list[Candidate]:
    """Find publication references in a study description.

    A DOI anywhere in the text wins. Only when no DOI is present do we fall back
    to interpreting bare URLs, because a description usually contains figure
    links alongside its citation.
    """
    if not description:
        return []

    found: list[Candidate] = []
    seen: set[tuple[str, str]] = set()

    for match in _DOI_RE.finditer(description):
        raw = match.group(0)
        doi = normalize_doi(raw)
        if doi is None:
            candidate = Candidate("unresolvable", "", raw, "DOI prefix with no suffix")
        else:
            candidate = Candidate("doi", doi, raw)
        key = (candidate.kind, candidate.value or candidate.raw)
        if key not in seen:
            seen.add(key)
            found.append(candidate)

    if any(c.kind == "doi" for c in found):
        return [c for c in found if c.kind == "doi"]
    if found:
        return found

    for url in _URL_RE.findall(description):
        if _is_rejected(url):
            continue
        nature = _NATURE_RE.search(url)
        if nature:
            article_id = nature.group(1).rstrip(_TRAILING_JUNK).lower()
            found.append(
                Candidate(
                    "doi",
                    f"10.1038/{article_id}",
                    url,
                    "derived from nature.com article id",
                )
            )
            continue
        pmc = _PMC_RE.search(url)
        if pmc:
            found.append(Candidate("pmc", pmc.group(1).upper(), url))
            continue
        found.append(
            Candidate("unresolvable", "", url, "publisher URL with no extractable identifier")
        )

    return found
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest seek/tests/test_doi_extract.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Sanity-check against every real description**

This is the point of the task — the fixtures cover the cases we know about, this covers the ones we don't.

```bash
docker compose exec -T nextseek uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','dmac.settings'); django.setup()
from django.db import connections
from seek.doi_extract import extract_publication_candidates
from collections import Counter
with connections['seek'].cursor() as c:
    c.execute('SELECT id, description FROM studies')
    rows = c.fetchall()
tally = Counter()
for sid, desc in rows:
    cands = extract_publication_candidates(desc)
    kinds = tuple(sorted({x.kind for x in cands})) or ('none',)
    tally[kinds] += 1
    for x in cands:
        print(sid, x.kind, x.value or x.raw)
print(dict(tally))
"
```

Expected: 33 studies produce a `doi` or `pmc` candidate, 1 produces `unresolvable` for `10.3390/`, and the rest produce none. If the totals differ, fix the extractor — not the expectation — and add the surprising string as a new fixture.

- [ ] **Step 6: Commit**

```bash
git add seek/doi_extract.py seek/tests/test_doi_extract.py
git commit -m "feat(publications): extract DOI/PMC references from study descriptions"
```
---

### Task 2: Study publication columns and the read layer

The heart of the feature. Two columns, and one module every downstream surface reads through.

**Files:**
- Create: `seek/publications.py`
- Test: `seek/tests/test_publications.py`

**Interfaces:**
- Consumes: `seek.doi_extract.extract_publication_candidates` (Task 1).
- Produces:
  - `PublicationRef` — frozen dataclass, fields `study_id: int`, `study_title: str | None`, `doi: str | None`, `pmid: int | None`; properties `doi_url`, `pmid_url`; methods `citation() -> str`, `as_dict() -> dict`
  - `ensure_study_publication_columns() -> list[str]`
  - `publications_for_samples(sample_ids) -> dict[int, list[PublicationRef]]`
  - `publications_for_sample(sample_id) -> list[dict]`
  - `resolve_study_ids(query: str) -> list[int]`
  - `sample_ids_subquery(study_ids) -> str`
  - `published_sample_ids_subquery() -> str`
  - `publication_where_clause(query, published_only: bool) -> str`
  - `attach_publications(rows: list[dict]) -> list[dict]`

- [ ] **Step 1: Write the failing test**

Create `seek/tests/test_publications.py`:

```python
"""Read layer for study-level publication attributes.

No database: `_rows` is monkeypatched. What is tested here is the SQL text and
the Python-side shaping, which is where the mistakes actually live.
"""

import pytest

from seek import publications as pub


class TestPublicationRef:
    def test_doi_url(self):
        ref = pub.PublicationRef(1, "S", "10.1/a", None)
        assert ref.doi_url == "https://doi.org/10.1/a"

    def test_doi_url_is_none_without_doi(self):
        assert pub.PublicationRef(1, "S", None, 5).doi_url is None

    def test_pmid_url(self):
        assert pub.PublicationRef(1, "S", None, 12345).pmid_url == \
            "https://pubmed.ncbi.nlm.nih.gov/12345/"

    def test_citation_prefers_the_study_title(self):
        assert pub.PublicationRef(1, "A paper", "10.1/a", None).citation() == "A paper"

    def test_citation_falls_back_to_the_doi(self):
        assert pub.PublicationRef(1, None, "10.1/a", None).citation() == "10.1/a"

    def test_as_dict_is_json_safe(self):
        import json
        json.dumps(pub.PublicationRef(1, "S", "10.1/a", 2).as_dict())


class TestPublicationsForSamples:
    def test_empty_input_does_not_query(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("should not have queried")

        monkeypatch.setattr(pub, "_rows", explode)
        assert pub.publications_for_samples([]) == {}

    def test_groups_by_sample(self, monkeypatch):
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [
            {"sample_id": 1, "study_id": 10, "study_title": "A", "doi": "10.1/a", "pmid": 111},
            {"sample_id": 2, "study_id": 10, "study_title": "A", "doi": "10.1/a", "pmid": 111},
        ])
        got = pub.publications_for_samples([1, 2, 3])
        assert sorted(got) == [1, 2]
        assert got[1][0].doi == "10.1/a"

    def test_sample_in_two_published_studies_shows_both(self, monkeypatch):
        # 18 samples really do belong to two studies.
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [
            {"sample_id": 7, "study_id": 10, "study_title": "A", "doi": "10.1/a", "pmid": None},
            {"sample_id": 7, "study_id": 20, "study_title": "B", "doi": "10.1/b", "pmid": None},
        ])
        assert len(pub.publications_for_samples([7])[7]) == 2

    def test_query_only_returns_published_studies(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(pub, "_rows",
                            lambda sql, params=None: captured.update(sql=sql) or [])
        pub.publications_for_samples([1])
        assert "s.doi IS NOT NULL OR s.pmid IS NOT NULL" in captured["sql"]
        assert "'Sample'" in captured["sql"]

    def test_query_is_parameterized_on_sample_ids(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(pub, "_rows",
                            lambda sql, params=None: captured.update(params=params) or [])
        pub.publications_for_samples([4, 5])
        assert captured["params"] == [4, 5]


class TestResolveStudyIds:
    def test_doi_lookup_is_parameterized_and_lowercased(self, monkeypatch):
        captured = {}

        def fake_rows(sql, params=None):
            captured["sql"] = sql
            captured["params"] = params
            return [{"id": 3}]

        monkeypatch.setattr(pub, "_rows", fake_rows)
        # A real DOI shape: the extractor requires 10.<4-9 digits>/, so a
        # made-up "10.1/a" would fall through to the title branch instead.
        assert pub.resolve_study_ids("https://doi.org/10.1038/S41590-021-01066-1") == [3]
        assert "%s" in captured["sql"]
        assert captured["params"] == ["10.1038/s41590-021-01066-1"]

    def test_pmid_lookup(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(pub, "_rows",
                            lambda sql, params=None: captured.update(params=params) or [{"id": 4}])
        assert pub.resolve_study_ids("99") == [4]
        assert captured["params"] == ["99"]

    def test_title_lookup(self, monkeypatch):
        captured = {}
        monkeypatch.setattr(pub, "_rows",
                            lambda sql, params=None: captured.update(params=params) or [{"id": 5}])
        assert pub.resolve_study_ids("Some paper title") == [5]
        assert captured["params"] == ["Some paper title"]

    def test_several_matches_return_all(self, monkeypatch):
        # A paper spanning two studies is legitimate; return both rather than erroring.
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [{"id": 1}, {"id": 2}])
        assert pub.resolve_study_ids("10.1038/s41590-021-01066-1") == [1, 2]

    def test_unknown_returns_empty(self, monkeypatch):
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [])
        assert pub.resolve_study_ids("nothing") == []


class TestSubqueries:
    def test_sample_ids_subquery_rejects_non_integers(self):
        with pytest.raises(ValueError):
            pub.sample_ids_subquery(["1 OR 1=1"])

    def test_sample_ids_subquery_splices_integers(self):
        sql = pub.sample_ids_subquery([7, 8])
        assert "IN (7,8)" in sql.replace(" ", "").replace("IN(", "IN (")

    def test_published_subquery_filters_on_doi_or_pmid(self):
        assert "s.doi IS NOT NULL OR s.pmid IS NOT NULL" in pub.published_sample_ids_subquery()


class TestPublicationWhereClause:
    def test_no_filter_yields_no_clause(self):
        assert pub.publication_where_clause(None, False) == ""
        assert pub.publication_where_clause("", False) == ""

    def test_published_only(self):
        clause = pub.publication_where_clause(None, True)
        assert clause.startswith(" AND ")
        assert "A.id IN (" in clause

    def test_resolved_query_constrains_to_its_studies(self, monkeypatch):
        monkeypatch.setattr(pub, "resolve_study_ids", lambda q: [7])
        clause = pub.publication_where_clause("10.1/a", False)
        assert "A.id IN (" in clause
        assert "7" in clause

    def test_unknown_publication_matches_nothing(self, monkeypatch):
        monkeypatch.setattr(pub, "resolve_study_ids", lambda q: [])
        assert pub.publication_where_clause("no such paper", False) == " AND 1=0"

    def test_injection_attempt_cannot_reach_sql(self, monkeypatch):
        monkeypatch.setattr(pub, "resolve_study_ids", lambda q: [])
        clause = pub.publication_where_clause("'; DROP TABLE samples; --", False)
        assert "DROP" not in clause


class TestAttachPublications:
    def test_adds_key_to_every_row(self, monkeypatch):
        monkeypatch.setattr(
            pub, "publications_for_samples",
            lambda ids: {1: [pub.PublicationRef(9, "A", "10.1/a", None)]},
        )
        rows = [{"id": 1}, {"id": 2}]
        pub.attach_publications(rows)
        assert rows[0]["publications"][0]["doi"] == "10.1/a"
        assert rows[1]["publications"] == []

    def test_one_query_per_page_not_per_row(self, monkeypatch):
        calls = []
        monkeypatch.setattr(pub, "publications_for_samples",
                            lambda ids: calls.append(list(ids)) or {})
        pub.attach_publications([{"id": 1}, {"id": 2}, {"id": 3}])
        assert calls == [[1, 2, 3]]

    def test_empty_rows(self, monkeypatch):
        monkeypatch.setattr(pub, "publications_for_samples", lambda ids: {})
        assert pub.attach_publications([]) == []


class TestEnsureColumns:
    def test_no_ddl_when_both_columns_exist(self, monkeypatch):
        executed = []
        monkeypatch.setattr(pub, "_rows",
                            lambda sql, params=None: [{"COLUMN_NAME": "doi"},
                                                      {"COLUMN_NAME": "pmid"}])
        monkeypatch.setattr(pub, "_execute", lambda sql: executed.append(sql))
        assert pub.ensure_study_publication_columns() == []
        assert executed == []

    def test_adds_only_the_missing_column(self, monkeypatch):
        executed = []
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [{"COLUMN_NAME": "doi"}])
        monkeypatch.setattr(pub, "_execute", lambda sql: executed.append(sql))
        assert pub.ensure_study_publication_columns() == ["pmid"]
        assert len(executed) == 1
        assert "ADD COLUMN pmid" in executed[0]

    def test_adds_both_when_absent(self, monkeypatch):
        executed = []
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [])
        monkeypatch.setattr(pub, "_execute", lambda sql: executed.append(sql))
        assert pub.ensure_study_publication_columns() == ["doi", "pmid"]
        assert len(executed) == 2
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest seek/tests/test_publications.py -v
```

Expected: collection error — `No module named 'seek.publications'`.

- [ ] **Step 3: Write the implementation**

Create `seek/publications.py`:

```python
"""Publication (DOI/PMID) links for samples.

The DOI and PMID are attributes of the study. A sample is published in a paper if
it belongs to a study whose doi or pmid is set — reached in SQL through
assay_assets, and in the graph through IN_STUDY.

All SQL here runs on the SEEK connection, which is where samples, assays and
studies live. See docs/2026-08-21-publication-links-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db import connections

#: Column definitions for the two attributes added to seek_production.studies.
#: MySQL 8 has no ADD COLUMN IF NOT EXISTS, so presence is checked first.
STUDY_PUBLICATION_COLUMNS = {
    "doi": "VARCHAR(255) NULL",
    "pmid": "INT NULL",
}


@dataclass(frozen=True)
class PublicationRef:
    study_id: int
    study_title: str | None
    doi: str | None
    pmid: int | None

    @property
    def doi_url(self) -> str | None:
        return f"https://doi.org/{self.doi}" if self.doi else None

    @property
    def pmid_url(self) -> str | None:
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/" if self.pmid else None

    def citation(self) -> str:
        """What to show in one cell. The study title is the paper title here."""
        return self.study_title or self.doi or (str(self.pmid) if self.pmid else "")

    def as_dict(self) -> dict:
        return {
            "study_id": self.study_id,
            "study_title": self.study_title,
            "doi": self.doi,
            "pmid": self.pmid,
            "doi_url": self.doi_url,
            "pmid_url": self.pmid_url,
            "citation": self.citation(),
        }


def _rows(sql: str, params: list | None = None) -> list[dict]:
    """Run a parameterized query on the SEEK connection, return dict rows."""
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(sql, params or [])
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _execute(sql: str) -> None:
    """Run a statement that returns no rows, on the SEEK connection."""
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(sql)


def ensure_study_publication_columns() -> list[str]:
    """Add doi/pmid to studies if absent. Returns the names actually added.

    A Django migration cannot do this: the entrypoint runs a plain `migrate`,
    which touches only the default database, so a migration against the SEEK
    schema would silently never run. See design finding 11.
    """
    schema = settings.DATABASES[settings.SEEK_DATABASE]["NAME"]
    present = {
        r["COLUMN_NAME"]
        for r in _rows(
            "SELECT COLUMN_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'studies' "
            "AND COLUMN_NAME IN ('doi', 'pmid')",
            [schema],
        )
    }
    added = []
    for name, definition in STUDY_PUBLICATION_COLUMNS.items():
        if name in present:
            continue
        _execute(f"ALTER TABLE studies ADD COLUMN {name} {definition}")
        added.append(name)
    return added


_SAMPLE_TO_STUDY_JOIN = """
    FROM assay_assets aa
    JOIN assays a ON a.id = aa.assay_id
    JOIN studies s ON s.id = a.study_id
    WHERE aa.asset_type = 'Sample'
      AND (s.doi IS NOT NULL OR s.pmid IS NOT NULL)
"""


def publications_for_samples(sample_ids) -> dict[int, list[PublicationRef]]:
    """Map each sample id to the published studies it belongs to. One query."""
    ids = list(sample_ids)
    if not ids:
        return {}
    placeholders = ",".join(["%s"] * len(ids))
    sql = f"""
        SELECT DISTINCT aa.asset_id AS sample_id, s.id AS study_id,
               s.title AS study_title, s.doi AS doi, s.pmid AS pmid
        {_SAMPLE_TO_STUDY_JOIN}
          AND aa.asset_id IN ({placeholders})
        ORDER BY aa.asset_id, s.id
    """
    grouped: dict[int, list[PublicationRef]] = {}
    for row in _rows(sql, ids):
        grouped.setdefault(row["sample_id"], []).append(
            PublicationRef(row["study_id"], row["study_title"], row["doi"], row["pmid"])
        )
    return grouped


def publications_for_sample(sample_id) -> list[dict]:
    """The published studies one sample belongs to, as template-ready dicts."""
    key = int(sample_id)
    return [ref.as_dict() for ref in publications_for_samples([key]).get(key, [])]


def _doi_in(text: str) -> str | None:
    """The DOI in a user-typed string, or None. Accepts a bare DOI or a doi.org URL."""
    from .doi_extract import extract_publication_candidates

    for candidate in extract_publication_candidates(text):
        if candidate.kind == "doi":
            return candidate.value
    return None


def resolve_study_ids(query: str) -> list[int]:
    """Study ids matching a DOI, a PMID, or an exact title.

    Returns every match rather than erroring on several: a paper spanning two
    studies is legitimate, and the caller wants the union of their samples.
    """
    text = (query or "").strip()
    if not text:
        return []

    doi = _doi_in(text)
    if doi:
        rows = _rows("SELECT id FROM studies WHERE LOWER(doi) = %s", [doi])
    elif text.isdigit():
        rows = _rows("SELECT id FROM studies WHERE pmid = %s", [text])
    else:
        rows = _rows("SELECT id FROM studies WHERE LOWER(title) = LOWER(%s)", [text])
    return [r["id"] for r in rows]


def sample_ids_subquery(study_ids) -> str:
    """Samples belonging to the given studies, as a spliceable subquery.

    The search builder concatenates SQL strings, so ids are forced through int().
    """
    ids = ",".join(str(int(i)) for i in study_ids)
    return (
        "SELECT aa.asset_id FROM assay_assets aa "
        "JOIN assays a ON a.id = aa.assay_id "
        f"WHERE aa.asset_type = 'Sample' AND a.study_id IN ({ids})"
    )


def published_sample_ids_subquery() -> str:
    """Samples belonging to any study that has a DOI or a PMID."""
    return f"SELECT DISTINCT aa.asset_id {_SAMPLE_TO_STUDY_JOIN}"


def publication_where_clause(query, published_only: bool) -> str:
    """A WHERE fragment constraining samples by publication, or "".

    Returns " AND 1=0" when the query names a paper that does not exist — an
    empty result is the honest answer and is easier to reason about than silently
    dropping the filter.
    """
    if query:
        study_ids = resolve_study_ids(query)
        if not study_ids:
            return " AND 1=0"
        return f" AND A.id IN ({sample_ids_subquery(study_ids)})"
    if published_only:
        return f" AND A.id IN ({published_sample_ids_subquery()})"
    return ""


def attach_publications(rows: list[dict]) -> list[dict]:
    """Add a ``publications`` key to each search result row, in place.

    One query for the whole page of results, regardless of page size.
    """
    if not rows:
        return rows
    grouped = publications_for_samples([r["id"] for r in rows])
    for row in rows:
        row["publications"] = [ref.as_dict() for ref in grouped.get(row["id"], [])]
    return rows
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest seek/tests/test_publications.py -v
```

Expected: all PASS.

- [ ] **Step 5: Create the columns and verify the SQL runs**

The unit tests check SQL text, not SQL validity. This checks validity.

```bash
docker compose exec -T nextseek uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','dmac.settings'); django.setup()
from seek.publications import (ensure_study_publication_columns,
                               publications_for_samples,
                               published_sample_ids_subquery, _rows)
print('added:', ensure_study_publication_columns())
print('idempotent rerun:', ensure_study_publication_columns())
print('lookup:', publications_for_samples([1,2,3]))
print('published count:', _rows('SELECT COUNT(*) AS n FROM (' + published_sample_ids_subquery() + ') x'))
"
```

Expected: `added: ['doi', 'pmid']`, then `idempotent rerun: []`, then `{}` and a
count of `0` — the columns now exist but nothing is filled in yet. A MySQL syntax
error here means the SQL is malformed; fix it before continuing, because four
later tasks depend on this module.

- [ ] **Step 6: Confirm the columns landed**

```bash
docker exec seek-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" seek_production -e "SHOW COLUMNS FROM studies WHERE Field IN (\"doi\",\"pmid\");"'
```

Expected: two rows, `doi varchar(255) YES` and `pmid int YES`.

- [ ] **Step 7: Commit**

```bash
git add seek/publications.py seek/tests/test_publications.py
git commit -m "feat(publications): study doi/pmid columns and the sample read layer"
```

---

### Task 3: Publication column in sample search results

**Files:**
- Modify: `seek/dbtable_sample.py` — `__retrieveRecords_advanced` (line 3858)
- Test: covered by `TestAttachPublications` in `seek/tests/test_publications.py` (Task 2)

**Interfaces:**
- Consumes: `seek.publications.attach_publications(rows) -> list[dict]` from Task 2.
- Produces: every search result row carries a `publications` key holding a list of `PublicationRef.as_dict()` dicts.

- [ ] **Step 1: Confirm the guard-rail tests pass before changing anything**

```bash
uv run pytest seek/tests/test_publications.py -k AttachPublications -v
```

Expected: 3 PASS. These are what protect the change below.

- [ ] **Step 2: Wire the enrichment into the search path**

In `seek/dbtable_sample.py`, find `__retrieveRecords_advanced` (line 3858). Replace:

```python
        jdata_new = self.reformatDataForClient(jdata)
        footer = []
        data = {'total':total,'rows':jdata_new,'footer':footer}
        return data
```

with:

```python
        jdata_new = self.reformatDataForClient(jdata)

        # One query for the whole page: a sample's paper comes from its study, so
        # it is not part of the sample select. Imported here rather than at module
        # scope to keep the dependency one-way — see
        # docs/2026-08-21-publication-links-design.md.
        from .publications import attach_publications
        attach_publications(jdata_new)

        footer = []
        data = {'total':total,'rows':jdata_new,'footer':footer}
        return data
```

- [ ] **Step 3: Run the tests to verify nothing regressed**

```bash
uv run pytest seek/tests -v
```

Expected: all PASS.

- [ ] **Step 4: Verify against the running stack**

```bash
./startup.sh rebuild
```

```bash
docker compose exec -T nextseek uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','dmac.settings'); django.setup()
import simplejson
from seek.dbtable_sample import DBtable_sample
d = DBtable_sample()
data = d.searchAdvanced({'server':'','username':'','password':''}, {'sampletype_id':'0','attribute':'none','filter_rule':'','filter_valueFrom':'','filter_valueTo':''}, 'FILTERING', 0)
rows = simplejson.loads(data)['rows'][:3]
print([r.get('publications', 'MISSING') for r in rows])
"
```

Expected: `[[], [], []]` — the key is present and empty, since no study has a DOI
yet. `MISSING` means the wiring did not take effect.

- [ ] **Step 5: Commit**

```bash
git add seek/dbtable_sample.py
git commit -m "feat(publications): add publication links to sample search results"
```

---

### Task 4: Publication block on the sample detail page

**Files:**
- Modify: `seek/views.py` — `sample()` (line ~129-167)
- Modify: `seek/templates/pages/samples.embed.html` (inside the "Sample info" region)
- Test: `seek/tests/test_publications_detail_context.py`

**Interfaces:**
- Consumes: `seek.publications.publications_for_sample` from Task 2.
- Produces: `report['publications']` — a list of `as_dict()` dicts — available to `samples.embed.html`.

- [ ] **Step 1: Write the failing test**

Create `seek/tests/test_publications_detail_context.py`:

```python
"""The detail page reads through the same helper as search, so a sample cannot
show one paper in the results table and a different one on its own page."""

from seek import publications as pub


def test_detail_context_shape(monkeypatch):
    monkeypatch.setattr(
        pub, "publications_for_samples",
        lambda ids: {5: [pub.PublicationRef(9, "A paper", "10.1/a", 77)]},
    )
    got = pub.publications_for_sample(5)
    assert got[0]["doi_url"] == "https://doi.org/10.1/a"
    assert got[0]["pmid_url"] == "https://pubmed.ncbi.nlm.nih.gov/77/"
    assert got[0]["citation"] == "A paper"


def test_detail_context_empty_for_unpublished(monkeypatch):
    monkeypatch.setattr(pub, "publications_for_samples", lambda ids: {})
    assert pub.publications_for_sample(5) == []


def test_accepts_string_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(pub, "publications_for_samples",
                        lambda ids: captured.update(ids=list(ids)) or {})
    pub.publications_for_sample("5")
    assert captured["ids"] == [5]
```

- [ ] **Step 2: Run the test to verify it fails or passes**

```bash
uv run pytest seek/tests/test_publications_detail_context.py -v
```

Expected: PASS, since `publications_for_sample` was built in Task 2. These are the
guard rail for the view and template change below.

- [ ] **Step 3: Populate the view context**

In `seek/views.py`, in `sample()`, after these lines:

```python
    sampledic, samplelist = dbsample.getSampleInfo(sample_id)
    report['sampledic'] = sampledic
    report['sampleinfo'] = samplelist
```

add:

```python
    from .publications import publications_for_sample
    report['publications'] = publications_for_sample(sample_id)
```

- [ ] **Step 4: Render the block**

In `seek/templates/pages/samples.embed.html`, immediately before the line
`<div class="widget-body">`, insert:

```html
					{% if report.publications %}
					<div class="well" style="margin-bottom:10px;">
						<strong>Published in</strong>
						<table class="TFtable">
							{% for p in report.publications %}
							<tr>
								<td>{{ p.citation }}</td>
								<td>
									{% if p.doi_url %}<a href="{{ p.doi_url }}" target="_blank" rel="noopener">DOI: {{ p.doi }}</a>{% endif %}
									{% if p.pmid_url %} &nbsp;<a href="{{ p.pmid_url }}" target="_blank" rel="noopener">PMID: {{ p.pmid }}</a>{% endif %}
								</td>
							</tr>
							{% endfor %}
						</table>
					</div>
					{% endif %}
```

There is deliberately no "not published" message: most samples are unpublished,
and a banner on every one of 51,000 sample pages is noise.

- [ ] **Step 5: Verify the page renders**

`seek/templates/` is baked into the image, so this needs a rebuild:

```bash
./startup.sh rebuild
```

Open a sample detail page and confirm it still renders. The block stays absent
until Task 7 fills in DOIs.

- [ ] **Step 6: Commit**

```bash
git add seek/views.py seek/templates/pages/samples.embed.html seek/tests/test_publications_detail_context.py
git commit -m "feat(publications): show the linked paper on the sample detail page"
```

---

### Task 5: Search by publication

**Files:**
- Modify: `seek/dbtable_sample.py` — `__initSearchFilters` (line 1907), `__parseSearchFilters` (line 3729), `__sqlQuery_select_records_filters_advanced` (line 3876)
- Test: covered by `TestPublicationWhereClause` in `seek/tests/test_publications.py` (Task 2)

**Interfaces:**
- Consumes: `seek.publications.publication_where_clause(query, published_only) -> str` from Task 2.
- Produces: `filtersdic` keys `publication_query: str | None` and `published_only: bool`.

- [ ] **Step 1: Confirm the guard-rail tests pass**

```bash
uv run pytest seek/tests/test_publications.py -k PublicationWhereClause -v
```

Expected: 5 PASS, including `test_injection_attempt_cannot_reach_sql`.

- [ ] **Step 2: Thread the filter through the search builder**

In `seek/dbtable_sample.py`:

**2a.** In `__initSearchFilters`, before `return filtersdic`, add:

```python
        filtersdic['publication_query'] = None
        filtersdic['published_only'] = False
```

**2b.** In `__parseSearchFilters`, immediately after
`filtersdic = self.__initSearchFilters(searchType, sampletype_id, project_id)`, add:

```python
        # Publication filter applies to every search type. `filters` is a QueryDict.
        filtersdic['publication_query'] = filters.get('filter_publication') or None
        filtersdic['published_only'] = filters.get('filter_published_only') in ('1', 'true', 'True', 'on')
```

**2c.** In `__sqlQuery_select_records_filters_advanced`, replace:

```python
        logger.debug(sqlquery_filter)
        return sqlquery_filter
```

with:

```python
        from .publications import publication_where_clause
        sqlquery_filter += publication_where_clause(
            filtersdic.get('publication_query'),
            filtersdic.get('published_only', False),
        )

        logger.debug(sqlquery_filter)
        return sqlquery_filter
```

- [ ] **Step 3: Verify the generated SQL is valid**

```bash
./startup.sh rebuild
```

```bash
docker compose exec -T nextseek uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','dmac.settings'); django.setup()
from seek.publications import publication_where_clause, _rows
for q, only in [(None, True), ('no such paper', False)]:
    clause = publication_where_clause(q, only)
    print(repr(q), only, '->', _rows('SELECT COUNT(*) AS n FROM samples A WHERE 1=1' + clause))
"
```

Expected: `[{'n': 0}]` in both cases — no study has a DOI yet, and the unknown
paper returns the `1=0` clause. A MySQL error means the spliced subquery is
malformed.

- [ ] **Step 4: Commit**

```bash
git add seek/dbtable_sample.py
git commit -m "feat(publications): filter sample search by DOI, PMID, or study title"
```

---

### Task 6: Push study attributes into Neo4j

**Files:**
- Create: `seek/publications_graph.py`
- Create: `nextseek_api/management/commands/sync_study_publications.py`
- Test: `seek/tests/test_publications_graph.py`

**Interfaces:**
- Consumes: `seek.publications._rows` from Task 2.
- Produces:
  - `STUDY_PROPERTY_CYPHER: str`
  - `build_study_rows(rows) -> list[dict]`
  - `sync_study_publications() -> dict` with keys `studies`, `with_doi`, `with_pmid`
  - `try_sync_study_publications() -> bool` (False when Neo4j is unreachable — never raises)

- [ ] **Step 1: Write the failing test**

Create `seek/tests/test_publications_graph.py`:

```python
"""Graph payload construction and failure tolerance.

No Neo4j: the driver is stubbed. The property write must cover every study, not
only the published ones, so that clearing a DOI in MySQL clears it in the graph.
"""

from seek import publications_graph as pg


class TestBuildStudyRows:
    def test_maps_columns_to_properties(self):
        rows = [{"id": 3, "doi": "10.1/a", "pmid": 7}]
        assert pg.build_study_rows(rows) == [{"study_id": 3, "doi": "10.1/a", "pmid": 7}]

    def test_unpublished_study_is_included_with_nulls(self):
        # Included on purpose: this is what clears a DOI removed in MySQL.
        rows = [{"id": 4, "doi": None, "pmid": None}]
        assert pg.build_study_rows(rows) == [{"study_id": 4, "doi": None, "pmid": None}]

    def test_empty(self):
        assert pg.build_study_rows([]) == []


class TestCypher:
    def test_matches_study_by_id(self):
        assert "MATCH (st:Study {id: row.study_id})" in pg.STUDY_PROPERTY_CYPHER

    def test_sets_both_properties(self):
        assert "st.doi = row.doi" in pg.STUDY_PROPERTY_CYPHER
        assert "st.pmid = row.pmid" in pg.STUDY_PROPERTY_CYPHER

    def test_does_not_create_studies(self):
        # MERGE here would invent Study nodes that MySQL has but the graph does not.
        assert "MERGE" not in pg.STUDY_PROPERTY_CYPHER


class TestFailureTolerance:
    def test_try_sync_returns_false_when_neo4j_is_down(self, monkeypatch):
        def explode(*args, **kwargs):
            raise OSError("connection refused")

        monkeypatch.setattr(pg, "_driver", explode)
        assert pg.try_sync_study_publications() is False

    def test_try_sync_returns_true_on_success(self, monkeypatch):
        class FakeDriver:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute_query(self, *a, **k):
                return None

        monkeypatch.setattr(pg, "_driver", lambda: FakeDriver())
        monkeypatch.setattr(pg, "_study_rows", lambda: [])
        assert pg.try_sync_study_publications() is True

    def test_counts_are_reported(self, monkeypatch):
        class FakeDriver:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute_query(self, *a, **k):
                return None

        monkeypatch.setattr(pg, "_driver", lambda: FakeDriver())
        monkeypatch.setattr(pg, "_study_rows", lambda: [
            {"id": 1, "doi": "10.1/a", "pmid": 5},
            {"id": 2, "doi": None, "pmid": None},
            {"id": 3, "doi": "10.1/c", "pmid": None},
        ])
        stats = pg.sync_study_publications()
        assert stats == {"studies": 3, "with_doi": 2, "with_pmid": 1}
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest seek/tests/test_publications_graph.py -v
```

Expected: collection error — `No module named 'seek.publications_graph'`.

- [ ] **Step 3: Write the implementation**

Create `seek/publications_graph.py`:

```python
"""Copy study publication attributes from MySQL into Neo4j.

MySQL is the source of truth. Every study is written, including those with no
DOI — that is what clears a value removed in MySQL rather than leaving a stale
one in the graph.

The properties already exist on dev and prod and do not exist on the local docker
stack; SET covers both cases. MATCH, never MERGE: a study missing from the graph
is a graph-sync problem to investigate, not a node to invent here.

See docs/2026-08-21-publication-links-design.md.
"""

from __future__ import annotations

import logging

from django.conf import settings
from neo4j import GraphDatabase

from .publications import _rows

log = logging.getLogger(__name__)

STUDY_PROPERTY_CYPHER = """
UNWIND $rows AS row
MATCH (st:Study {id: row.study_id})
SET st.doi = row.doi, st.pmid = row.pmid
"""


def _driver():
    cfg = settings.NEO4J_DATABASE
    return GraphDatabase.driver(cfg["URI"], auth=cfg["AUTH"])


def _db_name() -> str:
    return settings.NEO4J_DATABASE["NAME"]


def _study_rows() -> list[dict]:
    return _rows("SELECT id, doi, pmid FROM studies ORDER BY id")


def build_study_rows(rows: list[dict]) -> list[dict]:
    return [
        {"study_id": r["id"], "doi": r.get("doi"), "pmid": r.get("pmid")}
        for r in rows
    ]


def sync_study_publications() -> dict:
    """Write doi/pmid onto every Study node. Idempotent."""
    rows = _study_rows()
    payload = build_study_rows(rows)
    if payload:
        with _driver() as driver:
            driver.execute_query(
                STUDY_PROPERTY_CYPHER, {"rows": payload}, database_=_db_name()
            )
    return {
        "studies": len(payload),
        "with_doi": sum(1 for r in payload if r["doi"]),
        "with_pmid": sum(1 for r in payload if r["pmid"]),
    }


def try_sync_study_publications() -> bool:
    """Sync, swallowing any failure. Returns False if the graph was not written.

    Curation must not fail because Neo4j is unavailable — the standalone
    management command repairs it later.
    """
    try:
        sync_study_publications()
        return True
    except Exception:
        log.warning("Study publication graph sync deferred", exc_info=True)
        return False
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest seek/tests/test_publications_graph.py -v
```

Expected: all PASS.

- [ ] **Step 5: Add the management command**

Create `nextseek_api/management/commands/sync_study_publications.py`:

```python
"""Copy studies.doi / studies.pmid from MySQL onto Neo4j Study nodes.

Run after editing DOIs by any route other than `fill_study_publications --apply`,
or to repair a deferred sync.
"""

from django.core.management.base import BaseCommand

from seek.publications_graph import sync_study_publications


class Command(BaseCommand):
    help = "Sync study doi/pmid attributes from MySQL into Neo4j."

    def handle(self, *args, **options):
        stats = sync_study_publications()
        self.stdout.write(
            f"studies={stats['studies']} "
            f"with_doi={stats['with_doi']} with_pmid={stats['with_pmid']}"
        )
```

- [ ] **Step 6: Verify against the running stack, including clearing**

```bash
./startup.sh rebuild
```

```bash
docker compose exec -T nextseek uv run manage.py sync_study_publications
```

Expected: `studies=51 with_doi=0 with_pmid=0`.

Now prove a value round-trips and, crucially, that removing it in MySQL removes
it from the graph:

```bash
docker exec seek-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" seek_production -e "UPDATE studies SET doi=\"10.9999/test\", pmid=1234567 WHERE id=1;"'
docker compose exec -T nextseek uv run manage.py sync_study_publications
docker exec neo4j sh -c 'cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "MATCH (s:Study {id:1}) RETURN s.doi, s.pmid;"'
docker exec seek-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" seek_production -e "UPDATE studies SET doi=NULL, pmid=NULL WHERE id=1;"'
docker compose exec -T nextseek uv run manage.py sync_study_publications
docker exec neo4j sh -c 'cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "MATCH (s:Study {id:1}) RETURN s.doi, s.pmid;"'
```

Expected: `with_doi=1` on the first sync, then `"10.9999/test", 1234567`, then
`with_doi=0`, then `NULL, NULL`. If the second read still shows the test DOI, the
sync is not clearing and stale values will persist forever.

- [ ] **Step 7: Commit**

```bash
git add seek/publications_graph.py nextseek_api/management/commands/sync_study_publications.py seek/tests/test_publications_graph.py
git commit -m "feat(publications): sync study doi/pmid attributes into Neo4j"
```

---

### Task 7: Fill command

**Files:**
- Create: `nextseek_api/management/commands/fill_study_publications.py`
- Test: `nextseek_api/tests/test_fill_study_publications.py`

**Interfaces:**
- Consumes: `seek.doi_extract.extract_publication_candidates` (Task 1); `seek.publications._rows`, `ensure_study_publication_columns` (Task 2); `seek.publications_graph.try_sync_study_publications` (Task 6).
- Produces: `title_similarity(a, b) -> float`, `build_review_rows(studies, resolver) -> list[dict]`, `REVIEW_COLUMNS: list[str]`, `parse_review_file(path) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

Create `nextseek_api/tests/test_fill_study_publications.py`:

```python
"""Review-file construction and the approval gate.

Network resolution is injected, so these tests never call Crossref or NCBI.
"""

import pytest

from nextseek_api.management.commands import fill_study_publications as cmd


class TestTitleSimilarity:
    def test_identical(self):
        assert cmd.title_similarity("A paper", "A paper") == 1.0

    def test_unrelated(self):
        assert cmd.title_similarity("Endometrium organoids", "Tuberculosis granulomas") < 0.5

    def test_handles_none(self):
        assert cmd.title_similarity(None, "x") == 0.0
        assert cmd.title_similarity("x", None) == 0.0


class TestBuildReviewRows:
    def test_resolved_doi_row(self):
        studies = [{"id": 2, "title": "Organoid co-culture model",
                    "description": "https://doi.org/10.1101/2021.09.30.462577"}]

        def resolver(kind, value):
            return {"doi": "10.1101/2021.09.30.462577", "pmid": 34981053,
                    "title": "Organoid co-culture model", "journal": "bioRxiv",
                    "year": 2021}

        rows = cmd.build_review_rows(studies, resolver)
        assert len(rows) == 1
        assert rows[0]["normalized_doi"] == "10.1101/2021.09.30.462577"
        assert rows[0]["pmid"] == 34981053
        assert rows[0]["title_similarity"] == pytest.approx(1.0)
        assert rows[0]["approve"] == ""

    def test_low_similarity_is_visible_not_hidden(self):
        studies = [{"id": 9, "title": "Study about mice",
                    "description": "https://doi.org/10.1/x"}]

        def resolver(kind, value):
            return {"doi": "10.1/x", "pmid": None, "title": "Something else entirely",
                    "journal": None, "year": None}

        rows = cmd.build_review_rows(studies, resolver)
        assert rows[0]["title_similarity"] < 0.5
        assert rows[0]["approve"] == ""

    def test_truncated_doi_is_reported_as_manual(self):
        studies = [{"id": 31, "title": "T", "description": "https://doi.org/10.3390/"}]
        rows = cmd.build_review_rows(studies, lambda kind, value: None)
        assert rows[0]["proposed_action"] == "manual"
        assert "no suffix" in rows[0]["notes"]

    def test_study_without_reference_produces_no_row(self):
        studies = [{"id": 44, "title": "Mike Chao Barcoding Placeholder",
                    "description": "![](https://i.imgur.com/x.png)"}]
        assert cmd.build_review_rows(studies, lambda kind, value: None) == []

    def test_null_description_produces_no_row(self):
        studies = [{"id": 40, "title": "T", "description": None}]
        assert cmd.build_review_rows(studies, lambda kind, value: None) == []

    def test_offline_resolution_still_records_the_doi(self):
        studies = [{"id": 2, "title": "T", "description": "https://doi.org/10.1/a"}]
        rows = cmd.build_review_rows(studies, lambda kind, value: None)
        assert rows[0]["proposed_action"] == "unresolved"
        assert rows[0]["normalized_doi"] == "10.1/a"


class TestApprovalGate:
    def _write(self, path, rows):
        header = "\t".join(cmd.REVIEW_COLUMNS)
        lines = [header]
        for study_id, approve in rows:
            values = {c: "" for c in cmd.REVIEW_COLUMNS}
            values["study_id"] = str(study_id)
            values["normalized_doi"] = f"10.1/{study_id}"
            values["approve"] = approve
            lines.append("\t".join(values[c] for c in cmd.REVIEW_COLUMNS))
        path.write_text("\n".join(lines) + "\n")

    def test_only_yes_is_applied(self, tmp_path):
        path = tmp_path / "review.tsv"
        self._write(path, [(1, "yes"), (2, "no"), (3, ""), (4, "YES")])
        assert [r["study_id"] for r in cmd.parse_review_file(str(path))] == ["1", "4"]

    def test_unreviewed_file_applies_nothing(self, tmp_path):
        path = tmp_path / "review.tsv"
        self._write(path, [(1, ""), (2, "")])
        assert cmd.parse_review_file(str(path)) == []
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest nextseek_api/tests/test_fill_study_publications.py -v
```

Expected: collection error — no module `fill_study_publications`.

- [ ] **Step 3: Write the command**

Create `nextseek_api/management/commands/fill_study_publications.py`:

```python
"""Turn the DOIs sitting in study-description prose into study attributes.

Two phases, deliberately separated by a human:

    uv run manage.py fill_study_publications --extract --out review.tsv
    # curator edits the `approve` column
    uv run manage.py fill_study_publications --apply review.tsv

Nothing is written to any database by --extract. --apply writes only rows whose
`approve` column is exactly "yes" (case-insensitive), so an unreviewed file
writes nothing.

See docs/2026-08-21-publication-links-design.md, "Backfill".
"""

from __future__ import annotations

import csv
import json
import os
from difflib import SequenceMatcher

import requests
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import connections

from seek.doi_extract import extract_publication_candidates
from seek.publications import _rows, ensure_study_publication_columns
from seek.publications_graph import try_sync_study_publications

REVIEW_COLUMNS = [
    "approve",
    "study_id",
    "study_title",
    "raw_match",
    "normalized_doi",
    "resolved_title",
    "journal",
    "year",
    "pmid",
    "title_similarity",
    "proposed_action",
    "notes",
]

CROSSREF_URL = "https://api.crossref.org/works/{doi}"
IDCONV_URL = "https://www.ncbi.nlm.nih.gov/pmc/utils/idconv/v1.0/"
_TIMEOUT = 20


def title_similarity(a: str | None, b: str | None) -> float:
    """0.0-1.0 similarity between a study title and a resolved paper title."""
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def _cache_load(path: str) -> dict:
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    return {}


def _cache_save(path: str, cache: dict) -> None:
    if path:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(cache, handle, indent=2)


def make_resolver(cache: dict, offline: bool):
    """Return resolver(kind, value) -> metadata dict or None.

    Cached on disk so a rerun after a transient failure does not re-fetch what
    already worked.
    """

    def resolve(kind: str, value: str):
        if offline or not value:
            return None
        key = f"{kind}:{value}"
        if key in cache:
            return cache[key]

        meta = None
        try:
            if kind == "pmc":
                response = requests.get(
                    IDCONV_URL, params={"ids": value, "format": "json"}, timeout=_TIMEOUT
                )
                response.raise_for_status()
                records = response.json().get("records") or []
                doi = records[0].get("doi") if records else None
                meta = resolve("doi", doi) if doi else None
            else:
                response = requests.get(CROSSREF_URL.format(doi=value), timeout=_TIMEOUT)
                response.raise_for_status()
                message = response.json()["message"]
                parts = (message.get("issued", {}).get("date-parts") or [[None]])[0]
                meta = {
                    "doi": (message.get("DOI") or value).lower(),
                    "title": (message.get("title") or [None])[0],
                    "journal": (message.get("container-title") or [None])[0],
                    "year": parts[0] if parts else None,
                    "pmid": None,
                }
                idconv = requests.get(
                    IDCONV_URL, params={"ids": meta["doi"], "format": "json"},
                    timeout=_TIMEOUT,
                )
                if idconv.ok:
                    records = idconv.json().get("records") or []
                    if records and records[0].get("pmid"):
                        meta["pmid"] = int(records[0]["pmid"])
        except Exception:
            meta = None

        cache[key] = meta
        return meta

    return resolve


def build_review_rows(studies: list[dict], resolver) -> list[dict]:
    """One review row per publication reference found. Never writes anything."""
    rows: list[dict] = []
    for study in studies:
        for candidate in extract_publication_candidates(study.get("description")):
            row = {c: "" for c in REVIEW_COLUMNS}
            row["study_id"] = str(study["id"])
            row["study_title"] = study.get("title") or ""
            row["raw_match"] = candidate.raw
            row["notes"] = candidate.note

            if candidate.kind == "unresolvable":
                row["proposed_action"] = "manual"
                rows.append(row)
                continue

            if candidate.kind == "doi":
                row["normalized_doi"] = candidate.value

            meta = resolver(candidate.kind, candidate.value)
            if not meta:
                row["proposed_action"] = "unresolved"
                rows.append(row)
                continue

            row["normalized_doi"] = meta.get("doi") or row["normalized_doi"]
            row["resolved_title"] = meta.get("title") or ""
            row["journal"] = meta.get("journal") or ""
            row["year"] = str(meta.get("year") or "")
            row["pmid"] = meta.get("pmid") or ""
            row["title_similarity"] = round(
                title_similarity(study.get("title"), meta.get("title")), 3
            )
            row["proposed_action"] = "fill"
            rows.append(row)
    return rows


def parse_review_file(path: str) -> list[dict]:
    """Rows the curator approved. Anything not exactly "yes" is ignored."""
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [r for r in reader if (r.get("approve") or "").strip().lower() == "yes"]


def _write_study(study_id: int, doi: str, pmid) -> None:
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(
            "UPDATE studies SET doi = %s, pmid = %s WHERE id = %s",
            [doi or None, int(pmid) if pmid else None, int(study_id)],
        )


class Command(BaseCommand):
    help = "Extract DOIs from study descriptions for review, then fill studies.doi/pmid."

    def add_arguments(self, parser):
        parser.add_argument("--extract", action="store_true",
                            help="Write a review file. Touches no database.")
        parser.add_argument("--apply", metavar="FILE",
                            help="Fill studies.doi/pmid from a reviewed file.")
        parser.add_argument("--out", default="study_publication_review.tsv",
                            help="Where --extract writes its review file.")
        parser.add_argument("--offline", action="store_true",
                            help="Skip Crossref/NCBI; extract identifiers only.")
        parser.add_argument("--cache", default="study_publication_cache.json",
                            help="Resolution cache path.")

    def handle(self, *args, **options):
        if bool(options["extract"]) == bool(options["apply"]):
            raise CommandError("pass exactly one of --extract or --apply")
        if options["extract"]:
            self._extract(options)
        else:
            self._apply(options)

    def _extract(self, options):
        studies = _rows("SELECT id, title, description FROM studies ORDER BY id")
        cache = _cache_load(options["cache"])
        resolver = make_resolver(cache, options["offline"])
        rows = build_review_rows(studies, resolver)
        _cache_save(options["cache"], cache)

        with open(options["out"], "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=REVIEW_COLUMNS, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)

        actions = {}
        for row in rows:
            actions[row["proposed_action"]] = actions.get(row["proposed_action"], 0) + 1
        self.stdout.write(
            f"{len(studies)} studies scanned, {len(rows)} candidates written to "
            f"{options['out']}: {actions}"
        )
        self.stdout.write(
            "Nothing was written to the database. Set `approve` to yes on the rows "
            "you accept, checking title_similarity, then rerun with --apply."
        )

    def _apply(self, options):
        approved = parse_review_file(options["apply"])
        if not approved:
            self.stdout.write("No rows approved — nothing to do.")
            return

        added = ensure_study_publication_columns()
        if added:
            self.stdout.write(f"added columns to studies: {added}")

        for row in approved:
            doi = (row.get("normalized_doi") or "").strip().lower()
            if not doi:
                raise CommandError(f"study {row['study_id']}: approved row has no DOI")
            _write_study(int(row["study_id"]), doi, row.get("pmid"))

        synced = try_sync_study_publications()
        self.stdout.write(
            f"filled {len(approved)} studies; graph sync "
            f"{'ok' if synced else 'deferred'}"
        )
        if not synced:
            self.stdout.write("Run `manage.py sync_study_publications` to repair the graph.")
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest nextseek_api/tests/test_fill_study_publications.py -v
```

Expected: all PASS.

- [ ] **Step 5: Extract offline first**

```bash
docker compose exec -T nextseek uv run manage.py fill_study_publications --extract --offline --out /tmp/review_offline.tsv
```

Expected: `51 studies scanned` with roughly 34 candidates — about 33 `unresolved`
and 1 `manual` for the truncated `10.3390/`. This proves extraction works with no
network.

- [ ] **Step 6: Extract with resolution**

```bash
docker compose exec -T nextseek uv run manage.py fill_study_publications --extract --out /tmp/review.tsv
```

```bash
docker compose exec -T nextseek sh -c 'column -t -s "$(printf "\t")" /tmp/review.tsv | head -40'
```

Expected: most rows `proposed_action=fill` with `resolved_title`, `journal`,
`year` and often a `pmid`. **Read the `title_similarity` column** — anything below
roughly 0.6 is citing a paper whose title does not match its study.

- [ ] **Step 7: Hand the file to a curator — do not approve it yourself**

```bash
docker compose cp nextseek:/tmp/review.tsv ./study_publication_review.tsv
```

When approved rows come back:

```bash
docker compose cp ./study_publication_review.tsv nextseek:/tmp/review_approved.tsv
docker compose exec -T nextseek uv run manage.py fill_study_publications --apply /tmp/review_approved.tsv
```

- [ ] **Step 8: Commit** (the command, not the review file)

```bash
git add nextseek_api/management/commands/fill_study_publications.py nextseek_api/tests/test_fill_study_publications.py
git commit -m "feat(publications): fill study doi/pmid from descriptions with a review gate"
```

---

### Task 8: Teach Nessie the study attributes

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/context/min_graph_schema.json`
- Modify: `chat_nextseek/src/chat_nextseek/prompts/graph_agent.txt`
- Modify: `chat_nextseek/src/chat_nextseek/context/capabilities.md`
- Modify: `chat_nextseek/src/chat_nextseek/config.py` — the vocabulary block in the schema fetch (line ~1590)
- Test: `chat_nextseek/tests/test_publication_schema_context.py`

**Interfaces:**
- Consumes: the `Study.doi` / `Study.pmid` properties written in Task 6.
- Produces: no Python API. `min_graph_schema.json`'s `Study` entry names the new properties, and two `graph_query_triggers` are added.

- [ ] **Step 1: Note what must NOT be edited**

`neo4j_schema.json`, `neo4j_schema_dev.json` and `neo4j_schema_prod.json` are
**generated** by `config.py:1574` and overwritten on each fetch — that is what
`fetched_at` means. `min_graph_schema.json` is hand-written and is the file to
change. No new node label or relationship type is involved in this revision.

- [ ] **Step 2: Write the failing test**

Create `chat_nextseek/tests/test_publication_schema_context.py`:

```python
"""The routing schema must describe the study publication attributes.

The graph agent writes Cypher from this file. A property that exists in Neo4j
but not here is a property the agent will never query.
"""

import json
from pathlib import Path

CONTEXT = Path(__file__).resolve().parents[1] / "src" / "chat_nextseek" / "context"


def _min_schema():
    return json.loads((CONTEXT / "min_graph_schema.json").read_text())


def test_study_description_names_doi_and_pmid():
    study = next(n for n in _min_schema()["node_types"] if n["label"] == "Study")
    assert "doi" in study["description"]
    assert "pmid" in study["description"]


def test_study_description_says_most_studies_are_unpublished():
    study = next(n for n in _min_schema()["node_types"] if n["label"] == "Study")
    assert "unpublished" in study["description"].lower()


def test_no_publication_node_was_introduced():
    # This revision deliberately has no Publication label.
    labels = [n["label"] for n in _min_schema()["node_types"]]
    assert "Publication" not in labels


def test_triggers_cover_both_directions():
    triggers = " ".join(_min_schema()["graph_query_triggers"]).lower()
    assert "doi" in triggers
    assert "pmid" in triggers or "pubmed" in triggers
    assert "paper" in triggers or "publication" in triggers


def test_generated_schema_files_are_not_hand_edited():
    for name in ("neo4j_schema.json", "neo4j_schema_dev.json", "neo4j_schema_prod.json"):
        assert "fetched_at" in json.loads((CONTEXT / name).read_text())
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd chat_nextseek && uv run pytest tests/test_publication_schema_context.py -v
```

Expected: FAIL — the `Study` description does not mention `doi`.

- [ ] **Step 4: Update `min_graph_schema.json`**

Replace the `Study` entry in `node_types` with:

```json
    {
      "label": "Study",
      "description": "A research study grouping samples. In this instance a study IS a published paper: 'title' is the paper title. Properties: 'title', 'doi', 'pmid'. A study with a non-null 'doi' or 'pmid' is published; most studies are unpublished and have neither, which is expected rather than missing data. Linked to an Investigation via IN_INVESTIGATION."
    }
```

Add to `graph_query_triggers`:

```json
    "Query asks which paper or publication a sample appears in ('what paper is this sample from', 'is UID X published', 'which publication used these samples') — traverse (s:Sample)-[:IN_STUDY]->(st:Study) and read st.doi / st.pmid",
    "Query names a paper by title, DOI, or PMID and asks for its samples ('what samples were used in the SureQuant paper', 'samples for 10.1101/2021.09.30.462577', 'samples in PMID 34981053')"
```

Add to `disambiguation_rules`:

```json
    "If the query mentions a DOI, a PMID, a paper, or a publication → graph_query (doi/pmid live on Study nodes and no REST endpoint filters samples by them)"
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd chat_nextseek && uv run pytest tests/test_publication_schema_context.py -v
```

Expected: all PASS.

- [ ] **Step 6: Update the graph agent prompt**

In `chat_nextseek/src/chat_nextseek/prompts/graph_agent.txt`, add to the section
describing the graph shape:

```
Publications
  A study IS a paper here. The identifiers are properties on the Study node:
    (:Study {id, title, doi, pmid})

  Which paper a sample is from:
    MATCH (s:Sample {uuid: $uid})-[:IN_STUDY]->(st:Study)
    WHERE st.doi IS NOT NULL OR st.pmid IS NOT NULL
    RETURN st.title, st.doi, st.pmid

  Which samples a paper used — match on toLower(st.doi) = toLower($doi), on
  st.pmid, or on a title CONTAINS. DOIs are stored lowercased.

  Most studies are unpublished and have neither property. "Not published" is a
  correct answer, not a failed lookup — do not widen the query to find something.
```

- [ ] **Step 7: Update `capabilities.md`**

In `chat_nextseek/src/chat_nextseek/context/capabilities.md`, add to the
capability list:

```markdown
- **Publication links.** Which published paper a sample appears in, and the
  reverse — the samples used in a paper, looked up by title, DOI, or PMID. The
  DOI and PMID are attributes of the Study, and a sample inherits its studies'.
  Most studies are unpublished; that is expected, not a gap.
```

- [ ] **Step 8: Add study DOIs to the generated vocabulary**

In `chat_nextseek/src/chat_nextseek/config.py`, in the schema fetch that builds the
`vocabulary` block (near line 1590), add alongside the existing vocabulary lookups:

```python
        # Published study titles and DOIs, so the graph agent can map a phrase
        # like "the SureQuant paper" onto a real node.
        try:
            records, _, _ = driver.execute_query(
                "MATCH (st:Study) WHERE st.doi IS NOT NULL OR st.pmid IS NOT NULL "
                "RETURN st.title AS title, st.doi AS doi, st.pmid AS pmid LIMIT 500",
                database_=self.NEO4J_DATABASE_NAME,
            )
            schema["vocabulary"]["published_studies"] = [
                {"title": r["title"], "doi": r["doi"], "pmid": r["pmid"]} for r in records
            ]
        except Exception as e:
            print(f"[CONFIG][GRAPHDB] Published-study vocabulary fetch failed: {e!r}")
```

Use whatever database-name expression the neighbouring calls in that function
already use rather than inventing one — read the surrounding code first.

- [ ] **Step 9: Verify Nessie answers both directions**

Only meaningful once Task 7 has filled real DOIs.

```bash
docker compose exec -T nextseek uv run manage.py nessie --question "what samples were used in the SureQuant paper?"
```

```bash
docker compose exec -T nextseek uv run manage.py nessie --question "what paper is sample <a real published UID> from?"
```

Expected: the first returns a sample list scoped to that study; the second returns
the study title with its DOI. If the agent returns nothing, first check that DOIs
exist in the graph (`MATCH (s:Study) WHERE s.doi IS NOT NULL RETURN count(s)`)
before suspecting the prompt.

- [ ] **Step 10: Commit**

```bash
git add chat_nextseek/src/chat_nextseek/context/min_graph_schema.json chat_nextseek/src/chat_nextseek/prompts/graph_agent.txt chat_nextseek/src/chat_nextseek/context/capabilities.md chat_nextseek/src/chat_nextseek/config.py chat_nextseek/tests/test_publication_schema_context.py
git commit -m "feat(nessie): teach the graph agent study doi/pmid attributes"
```

---

## Final verification

- [ ] **Full test suite**

```bash
uv run pytest seek nextseek_api -v
```

Expected: all new tests pass and nothing pre-existing regressed. Note pytest uses
`DJANGO_SETTINGS_MODULE = "dmac.settings"` (pyproject.toml:147) — the
`dmac.test_settings` mentioned in CLAUDE.md is not what pyproject configures.

- [ ] **MySQL and Neo4j agree**

```bash
docker compose exec -T nextseek uv run manage.py sync_study_publications
```

```bash
docker exec seek-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" seek_production -N -B -e "SELECT COUNT(*) FROM studies WHERE doi IS NOT NULL;"'
docker exec neo4j sh -c 'cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "MATCH (s:Study) WHERE s.doi IS NOT NULL RETURN count(s);"'
```

The two counts must match. If they do not, the graph is stale — rerun the sync
and investigate before shipping.

- [ ] **Published sample counts are plausible**

```bash
docker exec neo4j sh -c 'cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "MATCH (s:Sample)-[:IN_STUDY]->(st:Study) WHERE st.doi IS NOT NULL RETURN st.title, count(s) ORDER BY count(s) DESC LIMIT 10;"'
```

Expected: one row per filled study with its sample count.

- [ ] **Unpublished samples stayed silent**

```bash
docker exec neo4j sh -c 'cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "MATCH (s:Sample) WHERE NOT (s)-[:IN_STUDY]->(:Study) OR NOT EXISTS { MATCH (s)-[:IN_STUDY]->(st:Study) WHERE st.doi IS NOT NULL } RETURN count(s);"'
```

Expected: a large share of the 51,361 samples. If this is near zero, inheritance
is over-applying and the read layer is wrong.

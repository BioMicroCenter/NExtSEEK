# Publication (DOI/PMID) Links for Samples — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show which published paper a sample appears in — in sample search, on the sample detail page, and through Nessie — and support the reverse lookup "what samples were used in \<study\>?".

**Architecture:** Publication records and their Study links use SEEK's existing (currently empty) `publications` and `relationships` tables. A sample inherits its studies' publications; one new NExtSEEK table records per-sample include/exclude corrections. The effective set is *derived* in MySQL by a single SQL expression, and *materialized* in Neo4j as `(:Sample)-[:REPORTED_IN]->(:Publication)` edges so Nessie's graph agent only ever writes a one-hop pattern.

**Tech Stack:** Django 4 + Mezzanine, MySQL 8 (two schemas on one server), Neo4j, DRF, pytest, `uv`.

**Design document:** `docs/2026-08-21-publication-links-design.md` — read it first. Every finding cited below was verified against the running stack on 2026-08-21.

## Global Constraints

- **`uv`, never `pip`.** Run everything as `uv run …`. Do not hand-edit dependency pins.
- **No new dependencies.** `requests` (pyproject.toml:92) and stdlib `difflib` cover the backfill. Adding anything else is out of scope.
- **Never hardcode the schema name `dmac`.** It is the default value of the `NEXTSEEK_MYSQL_DATABASE` environment variable. Always read `settings.DATABASES["default"]["NAME"]`.
- **Never interpolate user text into SQL.** The existing search builder concatenates strings; every value this plan adds to it must be an `int()` first, or go through a parameterized query.
- **The SEEK link convention is fixed:** `relationships` rows with `subject_type='Study'`, `predicate='related_to_publication'`, `other_object_type='Publication'`. Do not invent a different predicate.
- **DOIs are stored lowercased** for comparison; DOIs are case-insensitive by specification.
- **Tests must not require a database.** Existing suite style (`seek/tests/test_search_pubmed_nested.py`) is pure-unit. Use stubs for anything that would touch MySQL or Neo4j. Run with `uv run pytest`.
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

### Task 2: Override table

**Files:**
- Modify: `seek/models.py` (append at end of file)
- Create: `seek/migrations/0003_sample_publication_override.py` (generated)
- Test: `seek/tests/test_publication_override_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `seek.models.Sample_publication_override` with class attributes `INCLUDE = "include"`, `EXCLUDE = "exclude"`, DB table `sample_publication_override` in the **default** (NExtSEEK) schema.

- [ ] **Step 1: Write the failing test**

Create `seek/tests/test_publication_override_model.py`:

```python
"""The override table is a NExtSEEK concept and must live in the NExtSEEK schema.

If it were routed to the SEEK database it would not be created by the plain
`migrate` that the container entrypoint runs, and would silently not exist.
"""

from seek.models import Sample_publication_override


def test_table_name():
    assert Sample_publication_override._meta.db_table == "sample_publication_override"


def test_routes_to_default_database():
    # seek/dbrouters.py sends a model to getattr(model, "_DATABASE", "default").
    assert getattr(Sample_publication_override, "_DATABASE", "default") == "default"


def test_mode_choices():
    values = [v for v, _ in Sample_publication_override._meta.get_field("mode").choices]
    assert values == ["include", "exclude"]


def test_one_override_per_sample_publication_pair():
    assert ("sample_id", "publication_id") in Sample_publication_override._meta.unique_together
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest seek/tests/test_publication_override_model.py -v
```

Expected: FAIL with `ImportError: cannot import name 'Sample_publication_override'`.

- [ ] **Step 3: Add the model**

Append to `seek/models.py`:

```python
class Sample_publication_override(models.Model):
    """A per-sample correction to the publications inherited from its studies.

    A sample normally inherits every publication linked to the studies it belongs
    to. A study can contain samples that never appeared in its paper, and a paper
    can use a sample from elsewhere, so curators record the exceptions here.

    Deliberately has no ``_DATABASE`` attribute: this is NExtSEEK's own table, so
    it belongs in the default schema where the entrypoint's plain ``migrate``
    creates it. See docs/2026-08-21-publication-links-design.md.
    """

    INCLUDE = "include"
    EXCLUDE = "exclude"
    MODE_CHOICES = [(INCLUDE, "include"), (EXCLUDE, "exclude")]

    sample_id = models.IntegerField()
    publication_id = models.IntegerField()
    mode = models.CharField(max_length=7, choices=MODE_CHOICES)
    note = models.TextField(null=True, blank=True)
    created_by_id = models.IntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "sample_publication_override"
        unique_together = ("sample_id", "publication_id")
        indexes = [models.Index(fields=["publication_id"])]
```

- [ ] **Step 4: Generate the migration**

```bash
uv run manage.py makemigrations seek --name sample_publication_override
```

Expected: creates `seek/migrations/0003_sample_publication_override.py`. Open it and confirm it contains `CreateModel` for `Sample_publication_override` and nothing else — if it has picked up unrelated model drift, delete the file, resolve the drift separately, and regenerate.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest seek/tests/test_publication_override_model.py -v
```

Expected: all PASS.

- [ ] **Step 6: Apply the migration and confirm the table exists**

```bash
./startup.sh rebuild
```

```bash
docker exec seek-mysql sh -c 'mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -e "DESCRIBE dmac.sample_publication_override;"'
```

Expected: the seven columns above.

- [ ] **Step 7: Commit**

```bash
git add seek/models.py seek/migrations/0003_sample_publication_override.py seek/tests/test_publication_override_model.py
git commit -m "feat(publications): add sample_publication_override table"
```

---

### Task 3: Effective-set query layer

The heart of the feature. One SQL expression encodes the whole inheritance-plus-override rule; everything downstream reads through it.

**Files:**
- Create: `seek/publications.py`
- Test: `seek/tests/test_publications.py`

**Interfaces:**
- Consumes: `seek.models.Sample_publication_override` (table name only, via SQL).
- Produces:
  - `PublicationRef` — frozen dataclass, fields `seek_id: int`, `doi: str | None`, `pmid: int | None`, `title: str | None`, `journal: str | None`, `year: int | None`; methods `as_dict() -> dict`, property `doi_url: str | None`, property `pmid_url: str | None`
  - `AmbiguousPublication(Exception)`
  - `nextseek_schema() -> str`
  - `effective_samples_sql(schema: str) -> str`
  - `publications_for_samples(sample_ids: list[int]) -> dict[int, list[PublicationRef]]`
  - `sample_ids_subquery(publication_id: int | None) -> str`
  - `resolve_publication(query: str) -> PublicationRef | None`
  - `attach_publications(rows: list[dict]) -> list[dict]`

- [ ] **Step 1: Write the failing test**

Create `seek/tests/test_publications.py`:

```python
"""Query layer for publication links.

No database: `_rows` is monkeypatched. What is tested here is the SQL text and
the Python-side shaping, which is where the mistakes actually live.
"""

import pytest

from seek import publications as pub


class TestEffectiveSamplesSql:
    def test_uses_the_supplied_schema_not_a_hardcoded_one(self):
        sql = pub.effective_samples_sql("some_other_schema")
        assert "some_other_schema.sample_publication_override" in sql
        assert "dmac." not in sql

    def test_uses_seeks_relationship_convention(self):
        sql = pub.effective_samples_sql("s")
        assert "'related_to_publication'" in sql
        assert "'Study'" in sql
        assert "'Publication'" in sql

    def test_excludes_are_subtracted(self):
        sql = pub.effective_samples_sql("s")
        assert "NOT EXISTS" in sql
        assert "'exclude'" in sql

    def test_includes_are_added(self):
        sql = pub.effective_samples_sql("s")
        assert "UNION" in sql
        assert "'include'" in sql

    def test_only_sample_assets(self):
        assert "'Sample'" in pub.effective_samples_sql("s")


class TestPublicationsForSamples:
    def test_empty_input_does_not_query(self, monkeypatch):
        def explode(*args, **kwargs):
            raise AssertionError("should not have queried")

        monkeypatch.setattr(pub, "_rows", explode)
        assert pub.publications_for_samples([]) == {}

    def test_groups_by_sample(self, monkeypatch):
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [
            {"sample_id": 1, "seek_id": 10, "doi": "10.1/a", "pubmed_id": 111,
             "title": "Paper A", "journal": "Nature", "year": 2024},
            {"sample_id": 1, "seek_id": 11, "doi": "10.1/b", "pubmed_id": None,
             "title": "Paper B", "journal": "Cell", "year": 2025},
            {"sample_id": 2, "seek_id": 10, "doi": "10.1/a", "pubmed_id": 111,
             "title": "Paper A", "journal": "Nature", "year": 2024},
        ])
        got = pub.publications_for_samples([1, 2, 3])
        assert sorted(got) == [1, 2]
        assert [r.doi for r in got[1]] == ["10.1/a", "10.1/b"]
        assert got[2][0].title == "Paper A"

    def test_sample_in_two_published_studies_is_not_deduplicated_away(self, monkeypatch):
        # 18 samples really do belong to two studies. Both papers must survive.
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [
            {"sample_id": 7, "seek_id": 10, "doi": "10.1/a", "pubmed_id": None,
             "title": "A", "journal": None, "year": None},
            {"sample_id": 7, "seek_id": 20, "doi": "10.1/b", "pubmed_id": None,
             "title": "B", "journal": None, "year": None},
        ])
        assert len(pub.publications_for_samples([7])[7]) == 2


class TestPublicationRef:
    def test_doi_url(self):
        ref = pub.PublicationRef(1, "10.1/a", None, "T", None, None)
        assert ref.doi_url == "https://doi.org/10.1/a"

    def test_doi_url_is_none_without_doi(self):
        assert pub.PublicationRef(1, None, 5, "T", None, None).doi_url is None

    def test_pmid_url(self):
        ref = pub.PublicationRef(1, None, 12345, "T", None, None)
        assert ref.pmid_url == "https://pubmed.ncbi.nlm.nih.gov/12345/"

    def test_as_dict_is_json_safe(self):
        import json
        json.dumps(pub.PublicationRef(1, "10.1/a", 2, "T", "J", 2024).as_dict())


class TestResolvePublication:
    def test_doi_lookup_is_parameterized(self, monkeypatch):
        captured = {}

        def fake_rows(sql, params=None):
            captured["sql"] = sql
            captured["params"] = params
            return [{"seek_id": 3, "doi": "10.1/a", "pubmed_id": None,
                     "title": "T", "journal": None, "year": None}]

        monkeypatch.setattr(pub, "_rows", fake_rows)
        got = pub.resolve_publication("https://doi.org/10.1/A")
        assert got.seek_id == 3
        assert "%s" in captured["sql"]
        assert captured["params"] == ["10.1/a"]

    def test_pmid_lookup(self, monkeypatch):
        captured = {}

        def fake_rows(sql, params=None):
            captured["params"] = params
            return [{"seek_id": 4, "doi": None, "pubmed_id": 99,
                     "title": "T", "journal": None, "year": None}]

        monkeypatch.setattr(pub, "_rows", fake_rows)
        assert pub.resolve_publication("99").seek_id == 4
        assert captured["params"] == ["99"]

    def test_unknown_returns_none(self, monkeypatch):
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [])
        assert pub.resolve_publication("nothing matches") is None

    def test_ambiguous_title_raises_rather_than_guessing(self, monkeypatch):
        monkeypatch.setattr(pub, "_rows", lambda sql, params=None: [
            {"seek_id": 1, "doi": None, "pubmed_id": None, "title": "Same",
             "journal": None, "year": None},
            {"seek_id": 2, "doi": None, "pubmed_id": None, "title": "Same",
             "journal": None, "year": None},
        ])
        with pytest.raises(pub.AmbiguousPublication) as excinfo:
            pub.resolve_publication("Same")
        assert "1" in str(excinfo.value) and "2" in str(excinfo.value)


class TestSampleIdsSubquery:
    def test_rejects_non_integer_publication_id(self):
        with pytest.raises(ValueError):
            pub.sample_ids_subquery("1 OR 1=1")

    def test_accepts_integer_like_string(self):
        assert "= 7" in pub.sample_ids_subquery("7")

    def test_none_means_any_publication(self):
        sql = pub.sample_ids_subquery(None)
        assert "publication_id =" not in sql


class TestAttachPublications:
    def test_adds_key_to_every_row(self, monkeypatch):
        monkeypatch.setattr(
            pub, "publications_for_samples",
            lambda ids: {1: [pub.PublicationRef(9, "10.1/a", None, "T", "J", 2024)]},
        )
        rows = [{"id": 1}, {"id": 2}]
        pub.attach_publications(rows)
        assert rows[0]["publications"][0]["doi"] == "10.1/a"
        assert rows[1]["publications"] == []

    def test_empty_rows(self, monkeypatch):
        monkeypatch.setattr(pub, "publications_for_samples", lambda ids: {})
        assert pub.attach_publications([]) == []
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

A sample inherits the publications of every study it belongs to; the
``sample_publication_override`` table adds or removes individual samples. That
rule is expressed once, in :func:`effective_samples_sql`, and everything else
reads through it.

All SQL runs on the SEEK connection because that is where samples, assays and
publications live. The override table lives in the NExtSEEK schema on the same
MySQL server (both connections read MYSQL_HOST — dmac/settings.py:29,38), so it
is referenced schema-qualified using the name from settings, never the literal
"dmac", which is only an environment default.

See docs/2026-08-21-publication-links-design.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings
from django.db import connections


class AmbiguousPublication(Exception):
    """A lookup matched more than one publication.

    Raised rather than picking one, because a silent wrong pick misattributes
    samples to the wrong paper.
    """


@dataclass(frozen=True)
class PublicationRef:
    seek_id: int
    doi: str | None
    pmid: int | None
    title: str | None
    journal: str | None
    year: int | None

    @property
    def doi_url(self) -> str | None:
        return f"https://doi.org/{self.doi}" if self.doi else None

    @property
    def pmid_url(self) -> str | None:
        return f"https://pubmed.ncbi.nlm.nih.gov/{self.pmid}/" if self.pmid else None

    def short_citation(self) -> str:
        bits = [b for b in (self.journal, str(self.year) if self.year else None) if b]
        suffix = f" ({', '.join(bits)})" if bits else ""
        return f"{self.title or self.doi or self.pmid}{suffix}"

    def as_dict(self) -> dict:
        return {
            "id": self.seek_id,
            "doi": self.doi,
            "pmid": self.pmid,
            "title": self.title,
            "journal": self.journal,
            "year": self.year,
            "doi_url": self.doi_url,
            "pmid_url": self.pmid_url,
            "citation": self.short_citation(),
        }


_SELECT_FIELDS = (
    "p.id AS seek_id, p.doi AS doi, p.pubmed_id AS pubmed_id, "
    "p.title AS title, p.journal AS journal, YEAR(p.published_date) AS year"
)


def nextseek_schema() -> str:
    """The NExtSEEK schema name, from settings — never hardcoded."""
    return settings.DATABASES["default"]["NAME"]


def _rows(sql: str, params: list | None = None) -> list[dict]:
    """Run a parameterized query on the SEEK connection, return dict rows."""
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(sql, params or [])
        columns = [c[0] for c in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def effective_samples_sql(schema: str) -> str:
    """(sample_id, publication_id) pairs: study inheritance, minus excludes, plus includes."""
    return f"""
        SELECT DISTINCT aa.asset_id AS sample_id, r.other_object_id AS publication_id
        FROM assay_assets aa
        JOIN assays a ON a.id = aa.assay_id
        JOIN relationships r
          ON r.subject_type = 'Study'
         AND r.subject_id = a.study_id
         AND r.predicate = 'related_to_publication'
         AND r.other_object_type = 'Publication'
        WHERE aa.asset_type = 'Sample'
          AND NOT EXISTS (
              SELECT 1 FROM {schema}.sample_publication_override o
              WHERE o.sample_id = aa.asset_id
                AND o.publication_id = r.other_object_id
                AND o.mode = 'exclude'
          )
        UNION
        SELECT o.sample_id AS sample_id, o.publication_id AS publication_id
        FROM {schema}.sample_publication_override o
        WHERE o.mode = 'include'
    """


def _ref(row: dict) -> PublicationRef:
    return PublicationRef(
        seek_id=row["seek_id"],
        doi=row.get("doi"),
        pmid=row.get("pubmed_id"),
        title=row.get("title"),
        journal=row.get("journal"),
        year=row.get("year"),
    )


def publications_for_samples(sample_ids) -> dict[int, list[PublicationRef]]:
    """Map each sample id to the publications it appears in. One query."""
    ids = list(sample_ids)
    if not ids:
        return {}
    placeholders = ",".join(["%s"] * len(ids))
    sql = f"""
        SELECT e.sample_id AS sample_id, {_SELECT_FIELDS}
        FROM ({effective_samples_sql(nextseek_schema())}) e
        JOIN publications p ON p.id = e.publication_id
        WHERE e.sample_id IN ({placeholders})
        ORDER BY e.sample_id, p.published_date, p.id
    """
    grouped: dict[int, list[PublicationRef]] = {}
    for row in _rows(sql, ids):
        grouped.setdefault(row["sample_id"], []).append(_ref(row))
    return grouped


def sample_ids_subquery(publication_id) -> str:
    """A subquery yielding sample ids, for splicing into the search WHERE clause.

    The search builder concatenates SQL strings, so the publication id is forced
    through ``int()`` first — it is never allowed to reach SQL as text. Pass None
    to mean "appears in any publication" (the published-only filter).
    """
    inner = effective_samples_sql(nextseek_schema())
    if publication_id is None:
        return f"SELECT e.sample_id FROM ({inner}) e"
    pub_id = int(publication_id)
    return f"SELECT e.sample_id FROM ({inner}) e WHERE e.publication_id = {pub_id}"


def resolve_publication(query: str) -> PublicationRef | None:
    """Resolve a DOI, PMID, or exact title to one publication.

    Order is deterministic: DOI, then PMID, then case-insensitive exact title.
    Raises AmbiguousPublication if a title matches more than one row.
    """
    text = (query or "").strip()
    if not text:
        return None

    doi = extract_doi_from_query(text)
    if doi:
        rows = _rows(f"SELECT {_SELECT_FIELDS} FROM publications p WHERE LOWER(p.doi) = %s",
                     [doi])
    elif text.isdigit():
        rows = _rows(f"SELECT {_SELECT_FIELDS} FROM publications p WHERE p.pubmed_id = %s",
                     [text])
    else:
        rows = _rows(f"SELECT {_SELECT_FIELDS} FROM publications p WHERE LOWER(p.title) = LOWER(%s)",
                     [text])

    if not rows:
        return None
    if len(rows) > 1:
        ids = ", ".join(str(r["seek_id"]) for r in rows)
        raise AmbiguousPublication(f"{len(rows)} publications match {text!r}: ids {ids}")
    return _ref(rows[0])


def extract_doi_from_query(text: str) -> str | None:
    """The DOI in a user-typed string, or None. Accepts a bare DOI or a doi.org URL."""
    from .doi_extract import extract_publication_candidates

    for candidate in extract_publication_candidates(text):
        if candidate.kind == "doi":
            return candidate.value
    return None


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

Expected: all PASS. If `TestResolvePublication::test_doi_lookup_is_parameterized` fails, check that `extract_doi_from_query` lowercases — the test passes `10.1/A` and expects `10.1/a`.

- [ ] **Step 5: Verify the SQL actually runs against the real database**

The unit tests check SQL text, not SQL validity. This checks validity.

```bash
docker compose exec -T nextseek uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','dmac.settings'); django.setup()
from seek.publications import publications_for_samples, sample_ids_subquery, _rows
print('empty set ok:', publications_for_samples([1,2,3]))
print('subquery rowcount:', len(_rows(sample_ids_subquery(None))))
"
```

Expected: `{}` (no publications exist yet) and `0`. A MySQL syntax error here means the SQL is malformed — fix it before continuing, because five later tasks depend on this query.

- [ ] **Step 6: Commit**

```bash
git add seek/publications.py seek/tests/test_publications.py
git commit -m "feat(publications): effective-set query layer for sample publication links"
```

---

### Task 4: Publication column in sample search results

**Files:**
- Modify: `seek/dbtable_sample.py` — `__retrieveRecords_advanced` (line 3858)
- Test: `seek/tests/test_publications_search_column.py`

**Interfaces:**
- Consumes: `seek.publications.attach_publications(rows) -> list[dict]` from Task 3.
- Produces: every search result row carries a `publications` key holding a list of the dicts returned by `PublicationRef.as_dict()`.

- [ ] **Step 1: Write the failing test**

Create `seek/tests/test_publications_search_column.py`:

```python
"""The search result enrichment must be one query for the whole page.

Importing seek.dbtable_sample pulls in MySQLdb, pandas and the neo4j driver, so
this test exercises the seam (attach_publications) rather than the caller.
"""

from seek import publications as pub


def test_one_query_per_page_not_per_row(monkeypatch):
    calls = []

    def fake_lookup(ids):
        calls.append(list(ids))
        return {}

    monkeypatch.setattr(pub, "publications_for_samples", fake_lookup)
    pub.attach_publications([{"id": 1}, {"id": 2}, {"id": 3}])
    assert calls == [[1, 2, 3]]


def test_unpublished_sample_gets_empty_list_not_missing_key(monkeypatch):
    monkeypatch.setattr(pub, "publications_for_samples", lambda ids: {})
    rows = [{"id": 42}]
    pub.attach_publications(rows)
    assert rows[0]["publications"] == []


def test_row_shape_is_serializable(monkeypatch):
    import simplejson

    monkeypatch.setattr(
        pub, "publications_for_samples",
        lambda ids: {1: [pub.PublicationRef(9, "10.1/a", 77, "T", "Nature", 2024)]},
    )
    rows = [{"id": 1}]
    pub.attach_publications(rows)
    simplejson.dumps(rows, default=str)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest seek/tests/test_publications_search_column.py -v
```

Expected: `test_one_query_per_page_not_per_row` FAILS only if Task 3 is missing; if Task 3 is done these pass immediately. That is fine — they are the guard rail for the change in Step 3, which is what could break them.

- [ ] **Step 3: Wire the enrichment into the search path**

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

        # One query for the whole page: publication links are derived from study
        # membership, so they are not part of the sample select. Imported here
        # rather than at module scope to keep the dependency one-way — see
        # docs/2026-08-21-publication-links-design.md.
        from .publications import attach_publications
        attach_publications(jdata_new)

        footer = []
        data = {'total':total,'rows':jdata_new,'footer':footer}
        return data
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest seek/tests/test_publications_search_column.py seek/tests/test_publications.py -v
```

Expected: all PASS.

- [ ] **Step 5: Verify against the running stack**

```bash
./startup.sh rebuild
```

```bash
docker compose exec -T nextseek uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','dmac.settings'); django.setup()
from seek.dbtable_sample import DBtable_sample
d = DBtable_sample()
data = d.searchAdvanced({'server':'','username':'','password':''}, {'sampletype_id':'0','attribute':'none','filter_rule':'','filter_valueFrom':'','filter_valueTo':''}, 'FILTERING', 0)
import simplejson
rows = simplejson.loads(data)['rows'][:3]
print([r.get('publications', 'MISSING') for r in rows])
"
```

Expected: `[[], [], []]` — the key is present and empty, since no publications are registered yet. `MISSING` means the wiring did not take effect.

- [ ] **Step 6: Commit**

```bash
git add seek/dbtable_sample.py seek/tests/test_publications_search_column.py
git commit -m "feat(publications): add publication links to sample search results"
```

---

### Task 5: Publication block on the sample detail page

**Files:**
- Modify: `seek/views.py` — `sample()` (line ~129-167)
- Modify: `seek/templates/pages/samples.embed.html` (line ~50, inside the "Sample info" region)
- Test: `seek/tests/test_publications_detail_context.py`

**Interfaces:**
- Consumes: `seek.publications.publications_for_samples` from Task 3.
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
        lambda ids: {5: [pub.PublicationRef(9, "10.1/a", 77, "Paper", "Nature", 2024)]},
    )
    got = pub.publications_for_sample(5)
    assert got[0]["doi_url"] == "https://doi.org/10.1/a"
    assert got[0]["pmid_url"] == "https://pubmed.ncbi.nlm.nih.gov/77/"
    assert got[0]["citation"] == "Paper (Nature, 2024)"


def test_detail_context_empty_for_unpublished(monkeypatch):
    monkeypatch.setattr(pub, "publications_for_samples", lambda ids: {})
    assert pub.publications_for_sample(5) == []


def test_accepts_string_id(monkeypatch):
    captured = {}

    def fake(ids):
        captured["ids"] = list(ids)
        return {}

    monkeypatch.setattr(pub, "publications_for_samples", fake)
    pub.publications_for_sample("5")
    assert captured["ids"] == [5]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest seek/tests/test_publications_detail_context.py -v
```

Expected: FAIL with `AttributeError: module 'seek.publications' has no attribute 'publications_for_sample'`.

- [ ] **Step 3: Add the single-sample helper**

Append to `seek/publications.py`:

```python
def publications_for_sample(sample_id) -> list[dict]:
    """The publications one sample appears in, as template-ready dicts."""
    key = int(sample_id)
    return [ref.as_dict() for ref in publications_for_samples([key]).get(key, [])]
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest seek/tests/test_publications_detail_context.py -v
```

Expected: all PASS.

- [ ] **Step 5: Populate the view context**

In `seek/views.py`, in `sample()`, after these two lines:

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

- [ ] **Step 6: Render the block**

In `seek/templates/pages/samples.embed.html`, immediately before the line `<div class="widget-body">`, insert:

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

There is deliberately no "not published" message: most samples are unpublished, and a banner on every one of 51,000 sample pages is noise.

- [ ] **Step 7: Verify the page renders**

`themes/NextSeek/` is bind-mounted but `seek/templates/` is baked into the image, so this needs a rebuild:

```bash
./startup.sh rebuild
```

Then open a sample detail page in the browser and confirm the page still renders (the block will be absent until publications exist — Task 9 populates them).

- [ ] **Step 8: Commit**

```bash
git add seek/publications.py seek/views.py seek/templates/pages/samples.embed.html seek/tests/test_publications_detail_context.py
git commit -m "feat(publications): show linked papers on the sample detail page"
```

---

### Task 6: Search by publication

**Files:**
- Modify: `seek/dbtable_sample.py` — `__initSearchFilters` (line 1907), `__parseSearchFilters` (line 3729), `__sqlQuery_select_records_filters_advanced` (line 3876)
- Test: `seek/tests/test_publications_search_filter.py`

**Interfaces:**
- Consumes: `seek.publications.sample_ids_subquery`, `resolve_publication`, `AmbiguousPublication` from Task 3.
- Produces: `filtersdic` keys `publication_query: str | None` and `published_only: bool`; a new module-level function `seek.publications.publication_where_clause(publication_query, published_only) -> str` returning `""` or a clause beginning with `" AND "`.

- [ ] **Step 1: Write the failing test**

Create `seek/tests/test_publications_search_filter.py`:

```python
"""The search builder concatenates SQL strings, so nothing user-typed may reach
it as text. The publication filter resolves through a parameterized query first
and splices only an integer."""

import pytest

from seek import publications as pub


def test_no_filter_yields_no_clause():
    assert pub.publication_where_clause(None, False) == ""
    assert pub.publication_where_clause("", False) == ""


def test_published_only_constrains_to_any_publication(monkeypatch):
    monkeypatch.setattr(pub, "nextseek_schema", lambda: "s")
    clause = pub.publication_where_clause(None, True)
    assert clause.startswith(" AND ")
    assert "A.id IN (" in clause
    assert "publication_id =" not in clause


def test_resolved_publication_splices_only_an_integer(monkeypatch):
    monkeypatch.setattr(pub, "nextseek_schema", lambda: "s")
    monkeypatch.setattr(
        pub, "resolve_publication",
        lambda q: pub.PublicationRef(7, "10.1/a", None, "T", None, None),
    )
    clause = pub.publication_where_clause("10.1/a", False)
    assert "publication_id = 7" in clause


def test_injection_attempt_cannot_reach_sql(monkeypatch):
    monkeypatch.setattr(pub, "nextseek_schema", lambda: "s")
    monkeypatch.setattr(pub, "resolve_publication", lambda q: None)
    clause = pub.publication_where_clause("'; DROP TABLE samples; --", False)
    assert "DROP" not in clause


def test_unknown_publication_matches_nothing(monkeypatch):
    monkeypatch.setattr(pub, "nextseek_schema", lambda: "s")
    monkeypatch.setattr(pub, "resolve_publication", lambda q: None)
    clause = pub.publication_where_clause("no such paper", False)
    assert clause == " AND 1=0"


def test_ambiguous_title_propagates(monkeypatch):
    def boom(q):
        raise pub.AmbiguousPublication("2 publications match: ids 1, 2")

    monkeypatch.setattr(pub, "resolve_publication", boom)
    with pytest.raises(pub.AmbiguousPublication):
        pub.publication_where_clause("Same", False)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest seek/tests/test_publications_search_filter.py -v
```

Expected: FAIL — `module 'seek.publications' has no attribute 'publication_where_clause'`.

- [ ] **Step 3: Add the clause builder**

Append to `seek/publications.py`:

```python
def publication_where_clause(publication_query, published_only: bool) -> str:
    """A WHERE fragment constraining samples by publication, or "".

    Returns " AND 1=0" when the query names a publication that does not exist —
    an empty result is the honest answer, and is much easier to reason about than
    silently dropping the filter.

    Raises AmbiguousPublication when a title matches several papers; the caller
    turns that into a message for the user.
    """
    clauses = []

    if publication_query:
        found = resolve_publication(publication_query)
        if found is None:
            return " AND 1=0"
        clauses.append(f" AND A.id IN ({sample_ids_subquery(found.seek_id)})")
    elif published_only:
        clauses.append(f" AND A.id IN ({sample_ids_subquery(None)})")

    return "".join(clauses)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest seek/tests/test_publications_search_filter.py -v
```

Expected: all PASS.

- [ ] **Step 5: Thread the filter through the search builder**

In `seek/dbtable_sample.py`:

**5a.** In `__initSearchFilters`, before `return filtersdic`, add:

```python
        filtersdic['publication_query'] = None
        filtersdic['published_only'] = False
```

**5b.** In `__parseSearchFilters`, immediately after `filtersdic = self.__initSearchFilters(searchType, sampletype_id, project_id)`, add:

```python
        # Publication filter applies to every search type. `filters` is a QueryDict.
        filtersdic['publication_query'] = filters.get('filter_publication') or None
        filtersdic['published_only'] = filters.get('filter_published_only') in ('1', 'true', 'True', 'on')
```

**5c.** In `__sqlQuery_select_records_filters_advanced`, replace:

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

- [ ] **Step 6: Verify the generated SQL is valid**

```bash
./startup.sh rebuild
```

```bash
docker compose exec -T nextseek uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','dmac.settings'); django.setup()
from seek.publications import publication_where_clause, _rows
clause = publication_where_clause(None, True)
print(_rows('SELECT COUNT(*) AS n FROM samples A WHERE 1=1' + clause))
"
```

Expected: `[{'n': 0}]` — no publications exist yet, so nothing is published. A MySQL error means the spliced subquery is malformed.

- [ ] **Step 7: Commit**

```bash
git add seek/publications.py seek/dbtable_sample.py seek/tests/test_publications_search_filter.py
git commit -m "feat(publications): filter sample search by DOI, PMID, or paper title"
```

---

### Task 7: Neo4j publication subgraph

**Files:**
- Create: `seek/publications_graph.py`
- Create: `nextseek_api/management/commands/sync_publications_graph.py`
- Modify: `nextseek_api/batch_upload/neo4j_sync.py` — `ensure_constraints` (line 77)
- Modify: `dmac/settings.py` — add `CRONJOBS`
- Test: `seek/tests/test_publications_graph.py`

**Interfaces:**
- Consumes: `seek.publications.effective_samples_sql`, `nextseek_schema`, `_rows` from Task 3.
- Produces:
  - `build_publication_nodes(rows) -> list[dict]`
  - `build_sample_edges(rows) -> list[dict]`
  - `PUBLICATION_NODE_CYPHER: str`, `SAMPLE_EDGE_CYPHER: str`, `STUDY_EDGE_CYPHER: str`, `PRUNE_SAMPLE_EDGE_CYPHER: str`
  - `sync_publication_to_graph(publication_id: int) -> bool` (False when Neo4j is unreachable — never raises)
  - `reconcile_publication_graph() -> dict` with keys `publications`, `sample_edges`, `study_edges`, `pruned`

- [ ] **Step 1: Write the failing test**

Create `seek/tests/test_publications_graph.py`:

```python
"""Graph payload construction and the drift-repair Cypher.

No Neo4j: the driver is stubbed. What matters here is that reconcile removes
edges that are no longer implied — an append-only sync silently accumulates
wrong claims about which samples are in which paper.
"""

from seek import publications_graph as pg


class TestBuildPayloads:
    def test_publication_node_rows(self):
        rows = [{"seek_id": 3, "doi": "10.1/a", "pubmed_id": 7, "title": "T",
                 "journal": "Nature", "year": 2024}]
        assert pg.build_publication_nodes(rows) == [
            {"seek_id": 3, "doi": "10.1/a", "pmid": 7, "title": "T",
             "journal": "Nature", "year": 2024,
             "url": "https://doi.org/10.1/a"}
        ]

    def test_publication_without_doi_has_no_url(self):
        rows = [{"seek_id": 3, "doi": None, "pubmed_id": 7, "title": "T",
                 "journal": None, "year": None}]
        assert pg.build_publication_nodes(rows)[0]["url"] is None

    def test_sample_edge_rows(self):
        rows = [{"sample_uuid": "u1", "publication_id": 3}]
        assert pg.build_sample_edges(rows) == [{"sample_uuid": "u1", "publication_id": 3}]

    def test_empty_input(self):
        assert pg.build_publication_nodes([]) == []
        assert pg.build_sample_edges([]) == []


class TestCypher:
    def test_node_merge_is_keyed_on_seek_id(self):
        assert "MERGE (p:Publication {seek_id: row.seek_id})" in pg.PUBLICATION_NODE_CYPHER

    def test_sample_edge_uses_reported_in(self):
        assert "[:REPORTED_IN]" in pg.SAMPLE_EDGE_CYPHER
        assert "(s:Sample" in pg.SAMPLE_EDGE_CYPHER

    def test_study_edge_uses_reported_in(self):
        assert "[:REPORTED_IN]" in pg.STUDY_EDGE_CYPHER
        assert "(st:Study" in pg.STUDY_EDGE_CYPHER

    def test_prune_deletes_edges_not_in_the_current_set(self):
        # Without a DELETE, drift accumulates and never heals.
        assert "DELETE" in pg.PRUNE_SAMPLE_EDGE_CYPHER
        assert "NOT" in pg.PRUNE_SAMPLE_EDGE_CYPHER


class TestWriteThroughNeverBreaksCuration:
    def test_returns_false_when_neo4j_is_down(self, monkeypatch):
        def explode(*args, **kwargs):
            raise OSError("connection refused")

        monkeypatch.setattr(pg, "_driver", explode)
        assert pg.sync_publication_to_graph(1) is False

    def test_returns_true_on_success(self, monkeypatch):
        class FakeDriver:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def execute_query(self, *a, **k):
                return None

        monkeypatch.setattr(pg, "_driver", lambda: FakeDriver())
        monkeypatch.setattr(pg, "_publication_rows", lambda pid=None: [])
        monkeypatch.setattr(pg, "_sample_edge_rows", lambda pid=None: [])
        monkeypatch.setattr(pg, "_study_edge_rows", lambda pid=None: [])
        assert pg.sync_publication_to_graph(1) is True
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest seek/tests/test_publications_graph.py -v
```

Expected: collection error — `No module named 'seek.publications_graph'`.

- [ ] **Step 3: Write the implementation**

Create `seek/publications_graph.py`:

```python
"""Materialize the publication subgraph into Neo4j.

MySQL is the source of truth; the graph holds a materialized view so that
Nessie's graph agent only ever writes a one-hop pattern:

    (:Sample)-[:REPORTED_IN]->(:Publication)
    (:Study) -[:REPORTED_IN]->(:Publication)

One relationship type serves both because Cypher patterns carry labels. A query
that matches REPORTED_IN without a label on the source will see both kinds.

Write-through keeps Nessie current; reconcile repairs drift, including drift
caused by papers registered directly in SEEK's own UI. See
docs/2026-08-21-publication-links-design.md.
"""

from __future__ import annotations

import logging

from django.conf import settings
from neo4j import GraphDatabase

from .publications import _rows, effective_samples_sql, nextseek_schema

log = logging.getLogger(__name__)


PUBLICATION_NODE_CYPHER = """
UNWIND $rows AS row
MERGE (p:Publication {seek_id: row.seek_id})
SET p.doi = row.doi, p.pmid = row.pmid, p.title = row.title,
    p.journal = row.journal, p.year = row.year, p.url = row.url
"""

SAMPLE_EDGE_CYPHER = """
UNWIND $rows AS row
MATCH (s:Sample {uuid: row.sample_uuid})
MATCH (p:Publication {seek_id: row.publication_id})
MERGE (s)-[:REPORTED_IN]->(p)
"""

STUDY_EDGE_CYPHER = """
UNWIND $rows AS row
MATCH (st:Study {id: row.study_id})
MATCH (p:Publication {seek_id: row.publication_id})
MERGE (st)-[:REPORTED_IN]->(p)
"""

#: Remove Sample->Publication edges that MySQL no longer implies. Without this,
#: an un-excluded sample keeps claiming it appeared in a paper forever.
PRUNE_SAMPLE_EDGE_CYPHER = """
MATCH (s:Sample)-[r:REPORTED_IN]->(p:Publication)
WHERE NOT [s.uuid, p.seek_id] IN $keep
DELETE r
"""


def _driver():
    cfg = settings.NEO4J_DATABASE
    return GraphDatabase.driver(cfg["URI"], auth=cfg["AUTH"])


def _db_name() -> str:
    return settings.NEO4J_DATABASE["NAME"]


def build_publication_nodes(rows: list[dict]) -> list[dict]:
    return [
        {
            "seek_id": r["seek_id"],
            "doi": r.get("doi"),
            "pmid": r.get("pubmed_id"),
            "title": r.get("title"),
            "journal": r.get("journal"),
            "year": r.get("year"),
            "url": f"https://doi.org/{r['doi']}" if r.get("doi") else None,
        }
        for r in rows
    ]


def build_sample_edges(rows: list[dict]) -> list[dict]:
    return [
        {"sample_uuid": r["sample_uuid"], "publication_id": r["publication_id"]}
        for r in rows
    ]


def _publication_rows(publication_id: int | None = None) -> list[dict]:
    sql = ("SELECT p.id AS seek_id, p.doi, p.pubmed_id, p.title, p.journal, "
           "YEAR(p.published_date) AS year FROM publications p")
    if publication_id is None:
        return _rows(sql)
    return _rows(sql + " WHERE p.id = %s", [int(publication_id)])


def _sample_edge_rows(publication_id: int | None = None) -> list[dict]:
    inner = effective_samples_sql(nextseek_schema())
    sql = f"""
        SELECT s.uuid AS sample_uuid, e.publication_id AS publication_id
        FROM ({inner}) e
        JOIN samples s ON s.id = e.sample_id
    """
    if publication_id is None:
        return _rows(sql)
    return _rows(sql + " WHERE e.publication_id = %s", [int(publication_id)])


def _study_edge_rows(publication_id: int | None = None) -> list[dict]:
    sql = """
        SELECT r.subject_id AS study_id, r.other_object_id AS publication_id
        FROM relationships r
        WHERE r.subject_type = 'Study'
          AND r.predicate = 'related_to_publication'
          AND r.other_object_type = 'Publication'
    """
    if publication_id is None:
        return _rows(sql)
    return _rows(sql + " AND r.other_object_id = %s", [int(publication_id)])


def sync_publication_to_graph(publication_id: int) -> bool:
    """Push one publication and its edges to Neo4j. Never raises.

    Returns False if the graph could not be written. Curation must not fail
    because Neo4j is unavailable — the reconcile command repairs it later.
    """
    try:
        nodes = build_publication_nodes(_publication_rows(publication_id))
        sample_edges = build_sample_edges(_sample_edge_rows(publication_id))
        study_edges = _study_edge_rows(publication_id)
        with _driver() as driver:
            db = _db_name()
            if nodes:
                driver.execute_query(PUBLICATION_NODE_CYPHER, {"rows": nodes}, database_=db)
            if sample_edges:
                driver.execute_query(SAMPLE_EDGE_CYPHER, {"rows": sample_edges}, database_=db)
            if study_edges:
                driver.execute_query(STUDY_EDGE_CYPHER, {"rows": study_edges}, database_=db)
        return True
    except Exception:
        log.warning("Publication %s: graph write deferred", publication_id, exc_info=True)
        return False


def reconcile_publication_graph() -> dict:
    """Re-derive the whole publication subgraph from MySQL, including deletions."""
    nodes = build_publication_nodes(_publication_rows())
    sample_edges = build_sample_edges(_sample_edge_rows())
    study_edges = _study_edge_rows()
    keep = [[e["sample_uuid"], e["publication_id"]] for e in sample_edges]

    with _driver() as driver:
        db = _db_name()
        if nodes:
            driver.execute_query(PUBLICATION_NODE_CYPHER, {"rows": nodes}, database_=db)
        if sample_edges:
            driver.execute_query(SAMPLE_EDGE_CYPHER, {"rows": sample_edges}, database_=db)
        if study_edges:
            driver.execute_query(STUDY_EDGE_CYPHER, {"rows": study_edges}, database_=db)
        pruned = driver.execute_query(
            PRUNE_SAMPLE_EDGE_CYPHER, {"keep": keep}, database_=db
        )

    return {
        "publications": len(nodes),
        "sample_edges": len(sample_edges),
        "study_edges": len(study_edges),
        "pruned": getattr(getattr(pruned, "summary", None), "counters", None)
        and pruned.summary.counters.relationships_deleted or 0,
    }
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
uv run pytest seek/tests/test_publications_graph.py -v
```

Expected: all PASS.

- [ ] **Step 5: Add the Neo4j constraints**

In `nextseek_api/batch_upload/neo4j_sync.py`, in `ensure_constraints`, add two entries to the `constraints` list:

```python
        "CREATE CONSTRAINT publication_seek_id_unique IF NOT EXISTS FOR (p:Publication) REQUIRE p.seek_id IS UNIQUE",
        "CREATE CONSTRAINT publication_doi_unique IF NOT EXISTS FOR (p:Publication) REQUIRE p.doi IS UNIQUE",
```

- [ ] **Step 6: Add the management command**

Create `nextseek_api/management/commands/sync_publications_graph.py`:

```python
"""Re-derive the Neo4j publication subgraph from MySQL.

Run after registering papers in SEEK's own UI (which cannot write to the graph),
or to repair drift left by a deferred write-through.
"""

from django.core.management.base import BaseCommand

from seek.publications_graph import reconcile_publication_graph


class Command(BaseCommand):
    help = "Sync Publication nodes and REPORTED_IN edges from MySQL into Neo4j."

    def handle(self, *args, **options):
        stats = reconcile_publication_graph()
        self.stdout.write(
            f"publications={stats['publications']} "
            f"sample_edges={stats['sample_edges']} "
            f"study_edges={stats['study_edges']} "
            f"pruned={stats['pruned']}"
        )
```

- [ ] **Step 7: Register the cron job**

There is no `CRONJOBS` setting today — `django_crontab` is installed (`dmac/settings.py:160`) but no jobs are registered. Add near the end of `dmac/settings.py`:

```python
# Scheduled jobs (django_crontab). Re-derives the Neo4j publication subgraph so
# that papers registered directly in SEEK's UI reach the graph, and so that any
# deferred write-through is repaired. This is the first entry — the list did not
# exist before.
CRONJOBS = [
    ("17 3 * * *", "django.core.management.call_command", ["sync_publications_graph"]),
]
```

- [ ] **Step 8: Verify against the running stack**

```bash
./startup.sh rebuild
```

```bash
docker compose exec -T nextseek uv run manage.py sync_publications_graph
```

Expected: `publications=0 sample_edges=0 study_edges=0 pruned=0` — nothing to sync yet. Then confirm the constraints exist:

```bash
docker exec neo4j sh -c 'cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "SHOW CONSTRAINTS YIELD name WHERE name STARTS WITH \"publication\" RETURN name;"'
```

Expected: `publication_seek_id_unique` and `publication_doi_unique`.

- [ ] **Step 9: Verify idempotency and drift repair against real Neo4j**

The unit tests assert the prune Cypher *contains* a DELETE. This asserts it
actually deletes. Run it now, while the subgraph is empty and a planted edge is
unambiguous.

```bash
docker compose exec -T nextseek uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','dmac.settings'); django.setup()
from seek.publications_graph import reconcile_publication_graph, _driver, _db_name

print('run 1:', reconcile_publication_graph())
print('run 2:', reconcile_publication_graph())

# Plant an edge MySQL does not imply, then confirm reconcile removes it.
with _driver() as d:
    db = _db_name()
    d.execute_query('MERGE (p:Publication {seek_id: -1}) SET p.title = \'planted\'', database_=db)
    d.execute_query('MATCH (s:Sample) WITH s LIMIT 1 MATCH (p:Publication {seek_id:-1}) MERGE (s)-[:REPORTED_IN]->(p)', database_=db)
    r,_,_ = d.execute_query('MATCH (:Sample)-[r:REPORTED_IN]->(:Publication {seek_id:-1}) RETURN count(r) AS n', database_=db)
    print('planted edges:', r[0]['n'])

print('run 3:', reconcile_publication_graph())

with _driver() as d:
    db = _db_name()
    r,_,_ = d.execute_query('MATCH (:Sample)-[r:REPORTED_IN]->(:Publication {seek_id:-1}) RETURN count(r) AS n', database_=db)
    print('edges after reconcile:', r[0]['n'])
    d.execute_query('MATCH (p:Publication {seek_id:-1}) DETACH DELETE p', database_=db)
"
```

Expected: runs 1 and 2 return identical counts (idempotent); `planted edges: 1`;
`edges after reconcile: 0`. If the planted edge survives, the prune is not
working and drift will accumulate silently — fix it before continuing.

- [ ] **Step 10: Commit**

```bash
git add seek/publications_graph.py nextseek_api/management/commands/sync_publications_graph.py nextseek_api/batch_upload/neo4j_sync.py dmac/settings.py seek/tests/test_publications_graph.py
git commit -m "feat(publications): materialize publication subgraph in Neo4j with reconcile"
```

---

### Task 8: Publication API endpoints

**Files:**
- Create: `nextseek_api/services/publications.py`
- Modify: `nextseek_api/views.py` (add import/export of the ViewSet, following the existing pattern)
- Modify: `nextseek_api/urls.py` (register the route)
- Test: `nextseek_api/tests/test_publications_api.py`

**Interfaces:**
- Consumes: `seek.publications.publications_for_samples`, `resolve_publication`, `AmbiguousPublication`, `nextseek_schema`, `_rows`; `seek.publications_graph.sync_publication_to_graph`; `seek.models.Sample_publication_override`.
- Produces: `PublicationViewSet` registered at `publications`; `SamplePublicationsView` (the reverse lookup); helper `apply_overrides(publication_id, uids, mode, user_id) -> dict` with keys `created`, `no_effect`, `graph_sync`.

- [ ] **Step 1: Write the failing test**

Create `nextseek_api/tests/test_publications_api.py`:

```python
"""Override application: atomicity, no-op reporting, and graph-failure tolerance."""

import pytest

from nextseek_api.services import publications as svc


class TestUidResolution:
    def test_unknown_uid_is_atomic(self, monkeypatch):
        written = []
        monkeypatch.setattr(svc, "_resolve_uids", lambda uids: ({"U1": 1}, ["U2"]))
        monkeypatch.setattr(svc, "_write_overrides", lambda *a, **k: written.append(a))

        with pytest.raises(svc.UnknownSamples) as excinfo:
            svc.apply_overrides(3, ["U1", "U2"], "include", user_id=None)

        assert "U2" in str(excinfo.value)
        assert written == [], "nothing may be written when any UID is unknown"

    def test_all_known_uids_are_written(self, monkeypatch):
        written = []
        monkeypatch.setattr(svc, "_resolve_uids", lambda uids: ({"U1": 1, "U2": 2}, []))
        monkeypatch.setattr(svc, "_write_overrides",
                            lambda pid, ids, mode, uid: written.append((pid, sorted(ids), mode)) or 2)
        monkeypatch.setattr(svc, "_inherited_sample_ids", lambda pid: set())
        monkeypatch.setattr(svc, "sync_publication_to_graph", lambda pid: True)

        result = svc.apply_overrides(3, ["U1", "U2"], "include", user_id=None)
        assert written == [(3, [1, 2], "include")]
        assert result["created"] == 2


class TestNoEffectReporting:
    def test_include_of_already_inherited_sample_is_reported(self, monkeypatch):
        monkeypatch.setattr(svc, "_resolve_uids", lambda uids: ({"U1": 1}, []))
        monkeypatch.setattr(svc, "_write_overrides", lambda *a, **k: 1)
        monkeypatch.setattr(svc, "_inherited_sample_ids", lambda pid: {1})
        monkeypatch.setattr(svc, "sync_publication_to_graph", lambda pid: True)

        result = svc.apply_overrides(3, ["U1"], "include", user_id=None)
        assert result["no_effect"] == ["U1"]

    def test_exclude_of_sample_not_in_study_is_accepted(self, monkeypatch):
        # Accepted, not rejected: the override protects the sample if it joins
        # that study later.
        monkeypatch.setattr(svc, "_resolve_uids", lambda uids: ({"U9": 9}, []))
        monkeypatch.setattr(svc, "_write_overrides", lambda *a, **k: 1)
        monkeypatch.setattr(svc, "_inherited_sample_ids", lambda pid: set())
        monkeypatch.setattr(svc, "sync_publication_to_graph", lambda pid: True)

        result = svc.apply_overrides(3, ["U9"], "exclude", user_id=None)
        assert result["created"] == 1
        assert result["no_effect"] == ["U9"]


class TestGraphFailureIsNonFatal:
    def test_deferred_when_neo4j_is_down(self, monkeypatch):
        monkeypatch.setattr(svc, "_resolve_uids", lambda uids: ({"U1": 1}, []))
        monkeypatch.setattr(svc, "_write_overrides", lambda *a, **k: 1)
        monkeypatch.setattr(svc, "_inherited_sample_ids", lambda pid: set())
        monkeypatch.setattr(svc, "sync_publication_to_graph", lambda pid: False)

        result = svc.apply_overrides(3, ["U1"], "include", user_id=None)
        assert result["graph_sync"] == "deferred"
        assert result["created"] == 1, "the MySQL write must still have happened"

    def test_ok_when_neo4j_is_up(self, monkeypatch):
        monkeypatch.setattr(svc, "_resolve_uids", lambda uids: ({"U1": 1}, []))
        monkeypatch.setattr(svc, "_write_overrides", lambda *a, **k: 1)
        monkeypatch.setattr(svc, "_inherited_sample_ids", lambda pid: set())
        monkeypatch.setattr(svc, "sync_publication_to_graph", lambda pid: True)

        assert svc.apply_overrides(3, ["U1"], "include", user_id=None)["graph_sync"] == "ok"


class TestModeValidation:
    def test_rejects_unknown_mode(self):
        with pytest.raises(ValueError):
            svc.apply_overrides(3, ["U1"], "maybe", user_id=None)


class TestReverseLookup:
    def test_unknown_uid_is_404(self, monkeypatch, rf):
        monkeypatch.setattr(svc, "_resolve_uids", lambda uids: ({}, list(uids)))
        response = svc.SamplePublicationsView().get(rf.get("/"), uid="NOPE")
        assert response.status_code == 404

    def test_known_uid_returns_its_papers(self, monkeypatch, rf):
        from seek.publications import PublicationRef

        monkeypatch.setattr(svc, "_resolve_uids", lambda uids: ({"U1": 1}, []))
        monkeypatch.setattr(
            svc, "publications_for_samples",
            lambda ids: {1: [PublicationRef(9, "10.1/a", None, "T", "Nature", 2024)]},
        )
        response = svc.SamplePublicationsView().get(rf.get("/"), uid="U1")
        assert response.data[0]["doi"] == "10.1/a"

    def test_unpublished_sample_returns_empty_list(self, monkeypatch, rf):
        monkeypatch.setattr(svc, "_resolve_uids", lambda uids: ({"U1": 1}, []))
        monkeypatch.setattr(svc, "publications_for_samples", lambda ids: {})
        response = svc.SamplePublicationsView().get(rf.get("/"), uid="U1")
        assert response.data == []
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest nextseek_api/tests/test_publications_api.py -v
```

Expected: collection error — `No module named 'nextseek_api.services.publications'`.

- [ ] **Step 3: Write the service**

Create `nextseek_api/services/publications.py`:

```python
"""Publication endpoints: read the effective sample set, curate the overrides.

Overrides are API-only in this version — there is no curation UI. See
docs/2026-08-21-publication-links-design.md.
"""

from __future__ import annotations

from django.db import transaction
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from seek.models import Sample_publication_override
from seek.publications import (
    AmbiguousPublication,
    PublicationRef,
    _rows,
    effective_samples_sql,
    nextseek_schema,
    publications_for_samples,
    resolve_publication,
)
from seek.publications_graph import sync_publication_to_graph

VALID_MODES = (Sample_publication_override.INCLUDE, Sample_publication_override.EXCLUDE)


class UnknownSamples(Exception):
    """One or more UIDs did not resolve. Nothing was written."""


def _resolve_uids(uids: list[str]) -> tuple[dict[str, int], list[str]]:
    """Map UIDs to sample ids. Returns (resolved, unknown)."""
    if not uids:
        return {}, []
    placeholders = ",".join(["%s"] * len(uids))
    rows = _rows(
        f"SELECT uuid, id FROM samples WHERE uuid IN ({placeholders})", list(uids)
    )
    resolved = {r["uuid"]: r["id"] for r in rows}
    unknown = [u for u in uids if u not in resolved]
    return resolved, unknown


def _inherited_sample_ids(publication_id: int) -> set[int]:
    """Sample ids that already reach this publication through study membership."""
    inner = effective_samples_sql(nextseek_schema())
    rows = _rows(
        f"SELECT e.sample_id AS sample_id FROM ({inner}) e WHERE e.publication_id = %s",
        [int(publication_id)],
    )
    return {r["sample_id"] for r in rows}


def _write_overrides(publication_id: int, sample_ids, mode: str, user_id) -> int:
    """Upsert override rows. Returns the number written."""
    written = 0
    with transaction.atomic():
        for sample_id in sample_ids:
            Sample_publication_override.objects.update_or_create(
                sample_id=sample_id,
                publication_id=publication_id,
                defaults={"mode": mode, "created_by_id": user_id},
            )
            written += 1
    return written


def apply_overrides(publication_id: int, uids: list[str], mode: str, user_id) -> dict:
    """Attach or detach samples for a publication.

    Atomic on unknown UIDs: if any UID does not resolve, nothing is written.
    A write whose graph sync fails still succeeds — reconcile repairs the graph.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"mode must be one of {VALID_MODES}, got {mode!r}")

    resolved, unknown = _resolve_uids(uids)
    if unknown:
        raise UnknownSamples(f"unknown sample UIDs: {', '.join(sorted(unknown))}")

    created = _write_overrides(publication_id, resolved.values(), mode, user_id)

    inherited = _inherited_sample_ids(publication_id)
    if mode == Sample_publication_override.INCLUDE:
        no_effect = [uid for uid, sid in resolved.items() if sid in inherited]
    else:
        no_effect = [uid for uid, sid in resolved.items() if sid not in inherited]

    synced = sync_publication_to_graph(publication_id)
    return {
        "created": created,
        "no_effect": sorted(no_effect),
        "graph_sync": "ok" if synced else "deferred",
    }


def remove_overrides(publication_id: int, uids: list[str]) -> dict:
    """Delete override rows, reverting those samples to plain inheritance."""
    resolved, unknown = _resolve_uids(uids)
    if unknown:
        raise UnknownSamples(f"unknown sample UIDs: {', '.join(sorted(unknown))}")
    deleted, _ = Sample_publication_override.objects.filter(
        publication_id=publication_id, sample_id__in=list(resolved.values())
    ).delete()
    synced = sync_publication_to_graph(publication_id)
    return {"deleted": deleted, "graph_sync": "ok" if synced else "deferred"}


def _project_filter_sql(project_id) -> tuple[str, list]:
    """Restrict to a project unless the caller is a supervisor (project_id 0)."""
    if not project_id:
        return "", []
    return (
        " AND EXISTS (SELECT 1 FROM projects_samples ps "
        "WHERE ps.sample_id = e.sample_id AND ps.project_id = %s)",
        [int(project_id)],
    )


def samples_for_publication(publication_id: int, project_id=None) -> list[dict]:
    """The effective sample set, filtered to what the caller may see."""
    inner = effective_samples_sql(nextseek_schema())
    clause, params = _project_filter_sql(project_id)
    rows = _rows(
        f"""
        SELECT s.id AS id, s.uuid AS uid, s.title AS title
        FROM ({inner}) e
        JOIN samples s ON s.id = e.sample_id
        WHERE e.publication_id = %s{clause}
        ORDER BY s.id
        """,
        [int(publication_id)] + params,
    )
    return rows


def list_publications() -> list[dict]:
    rows = _rows(
        "SELECT p.id AS seek_id, p.doi, p.pubmed_id, p.title, p.journal, "
        "YEAR(p.published_date) AS year FROM publications p ORDER BY p.published_date DESC, p.id"
    )
    return [
        PublicationRef(r["seek_id"], r["doi"], r["pubmed_id"], r["title"],
                       r["journal"], r["year"]).as_dict()
        for r in rows
    ]


class PublicationViewSet(viewsets.ViewSet):
    """Publications and their sample associations."""

    permission_classes = [IsAuthenticated]

    def _project_id(self, request):
        """0 for supervisors, otherwise the caller's project.

        Mirrors seek/views.py:435-438. Which samples a paper used is not public
        even though the DOI is, so this filter is required, not optional.
        """
        from seek.views import verifySuperUser
        from seek.seekdb import SeekDB

        if verifySuperUser(request):
            return 0
        user_seek = SeekDB(None, None, None).getSeekLogin(request, True)
        return user_seek.get("projectid")

    def list(self, request):
        return Response(list_publications())

    def retrieve(self, request, pk=None):
        try:
            found = resolve_publication(str(pk)) if not str(pk).isdigit() else None
        except AmbiguousPublication as exc:
            return Response({"detail": str(exc)}, status=409)
        pub_id = found.seek_id if found else int(pk)
        samples = samples_for_publication(pub_id, self._project_id(request))
        matches = [p for p in list_publications() if p["id"] == pub_id]
        if not matches:
            return Response({"detail": "not found"}, status=404)
        payload = dict(matches[0])
        payload["sample_count"] = len(samples)
        return Response(payload)

    @action(detail=True, methods=["get", "post", "delete"])
    def samples(self, request, pk=None):
        pub_id = int(pk)
        if request.method == "GET":
            return Response(samples_for_publication(pub_id, self._project_id(request)))

        uids = request.data.get("uids") or []
        if not isinstance(uids, list) or not uids:
            return Response({"detail": "uids must be a non-empty list"}, status=400)

        try:
            if request.method == "POST":
                mode = request.data.get("mode", "include")
                result = apply_overrides(pub_id, uids, mode, getattr(request.user, "id", None))
            else:
                result = remove_overrides(pub_id, uids)
        except UnknownSamples as exc:
            return Response({"detail": str(exc)}, status=400)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=400)
        return Response(result)


class SamplePublicationsView(APIView):
    """GET /nextseek_api/samples/<uid>/publications/ — the reverse lookup.

    A plain APIView rather than an action on SampleViewSet: the route is the only
    thing the two share, and threading it through that viewset would couple this
    feature to a class it has no other reason to touch.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, uid=None):
        resolved, unknown = _resolve_uids([uid])
        if unknown:
            return Response({"detail": f"unknown sample UID: {uid}"}, status=404)
        sample_id = resolved[uid]
        refs = publications_for_samples([sample_id]).get(sample_id, [])
        return Response([ref.as_dict() for ref in refs])
```

- [ ] **Step 4: Register the route**

In `nextseek_api/views.py`, add near the other service imports:

```python
from nextseek_api.services.publications import PublicationViewSet  # noqa: F401
```

In `nextseek_api/urls.py`, after the `studies` registration (line 22):

```python
router.register(r"publications", views.PublicationViewSet, basename="publications")
```

The reverse lookup is not a router route — add it to `urlpatterns`, before the
`re_path(r'^', include(router.urls))` catch-all:

```python
    re_path(
        r'^samples/(?P<uid>[^/]+)/publications/$',
        views.SamplePublicationsView.as_view(),
        name='sample-publications',
    ),
```

And in `nextseek_api/views.py`, alongside the ViewSet import:

```python
from nextseek_api.services.publications import SamplePublicationsView  # noqa: F401
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
uv run pytest nextseek_api/tests/test_publications_api.py -v
```

Expected: all PASS.

- [ ] **Step 6: Verify the route is live**

```bash
./startup.sh rebuild
```

```bash
docker compose exec -T nextseek uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','dmac.settings'); django.setup()
from django.urls import reverse
print(reverse('nextseek_api:publications-list'))
print(reverse('nextseek_api:sample-publications', kwargs={'uid': 'ABC-1'}))
"
```

Expected: `/nextseek_api/publications/` and `/nextseek_api/samples/ABC-1/publications/`.

- [ ] **Step 7: Commit**

```bash
git add nextseek_api/services/publications.py nextseek_api/views.py nextseek_api/urls.py nextseek_api/tests/test_publications_api.py
git commit -m "feat(publications): API for publication lookup and sample overrides"
```

---

### Task 9: Backfill command

**Files:**
- Create: `nextseek_api/management/commands/backfill_study_publications.py`
- Test: `nextseek_api/tests/test_backfill_study_publications.py`

**Interfaces:**
- Consumes: `seek.doi_extract.extract_publication_candidates`, `Candidate` (Task 1); `seek.publications._rows` (Task 3); `seek.publications_graph.sync_publication_to_graph` (Task 7).
- Produces: module-level functions `title_similarity(a, b) -> float`, `build_review_rows(studies, resolver) -> list[dict]`, `REVIEW_COLUMNS: list[str]`, `parse_review_file(path) -> list[dict]`.

- [ ] **Step 1: Write the failing test**

Create `nextseek_api/tests/test_backfill_study_publications.py`:

```python
"""Backfill review-file construction and the approval gate.

Network resolution is injected, so these tests never call Crossref or NCBI.
"""

import pytest

from nextseek_api.management.commands import backfill_study_publications as cmd


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

    def test_truncated_doi_is_reported_as_unresolvable(self):
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

    def test_offline_resolution_marks_unresolved(self):
        studies = [{"id": 2, "title": "T", "description": "https://doi.org/10.1/a"}]
        rows = cmd.build_review_rows(studies, lambda kind, value: None)
        assert rows[0]["proposed_action"] == "unresolved"
        assert rows[0]["normalized_doi"] == "10.1/a"


class TestApprovalGate:
    def test_only_yes_is_applied(self, tmp_path):
        path = tmp_path / "review.tsv"
        header = "\t".join(cmd.REVIEW_COLUMNS)
        def line(study_id, approve):
            values = {c: "" for c in cmd.REVIEW_COLUMNS}
            values["study_id"] = str(study_id)
            values["normalized_doi"] = f"10.1/{study_id}"
            values["approve"] = approve
            return "\t".join(values[c] for c in cmd.REVIEW_COLUMNS)

        path.write_text("\n".join([header, line(1, "yes"), line(2, "no"),
                                   line(3, ""), line(4, "YES")]) + "\n")

        approved = cmd.parse_review_file(str(path))
        assert [r["study_id"] for r in approved] == ["1", "4"]

    def test_unreviewed_file_applies_nothing(self, tmp_path):
        path = tmp_path / "review.tsv"
        header = "\t".join(cmd.REVIEW_COLUMNS)
        values = {c: "" for c in cmd.REVIEW_COLUMNS}
        values["study_id"] = "1"
        path.write_text(header + "\n" + "\t".join(values[c] for c in cmd.REVIEW_COLUMNS) + "\n")
        assert cmd.parse_review_file(str(path)) == []
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest nextseek_api/tests/test_backfill_study_publications.py -v
```

Expected: collection error — no module `backfill_study_publications`.

- [ ] **Step 3: Write the command**

Create `nextseek_api/management/commands/backfill_study_publications.py`:

```python
"""Turn the DOIs sitting in study-description prose into publication records.

Two phases, deliberately separated by a human:

    uv run manage.py backfill_study_publications --extract --out review.tsv
    # curator edits the `approve` column
    uv run manage.py backfill_study_publications --apply review.tsv

Nothing is written to any database by --extract. --apply writes only rows whose
`approve` column is exactly "yes" (case-insensitive), so an unreviewed file
inserts nothing.

See docs/2026-08-21-publication-links-design.md, "Backfill".
"""

from __future__ import annotations

import csv
import json
import os
from difflib import SequenceMatcher

import requests
from django.core.management.base import BaseCommand, CommandError

from seek.doi_extract import extract_publication_candidates
from seek.publications import _rows
from seek.publications_graph import sync_publication_to_graph

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

    Cached on disk so --apply never re-hits the network, and so a rerun after a
    transient failure does not re-fetch what already worked.
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
                if records:
                    doi = records[0].get("doi")
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
            row["proposed_action"] = "create"
            rows.append(row)
    return rows


def parse_review_file(path: str) -> list[dict]:
    """Rows the curator approved. Anything not exactly "yes" is ignored."""
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        return [r for r in reader if (r.get("approve") or "").strip().lower() == "yes"]


def _insert_publication(row: dict) -> int:
    """Insert or reuse a publication, returning its id. Idempotent on DOI."""
    doi = (row.get("normalized_doi") or "").strip().lower()
    if not doi:
        raise CommandError(f"study {row['study_id']}: approved row has no DOI")

    existing = _rows("SELECT id FROM publications WHERE LOWER(doi) = %s", [doi])
    if existing:
        return existing[0]["id"]

    import uuid as uuid_module
    from django.db import connections
    from django.conf import settings

    pmid = row.get("pmid") or None
    year = row.get("year") or None
    published = f"{year}-01-01" if year else None
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO publications
                (doi, pubmed_id, title, journal, published_date, uuid,
                 first_letter, version, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 1, NOW(), NOW())
            """,
            [
                doi,
                int(pmid) if pmid else None,
                row.get("resolved_title") or None,
                row.get("journal") or None,
                published,
                str(uuid_module.uuid4()),
                (row.get("resolved_title") or "?")[:1].upper(),
            ],
        )
        cursor.execute("SELECT LAST_INSERT_ID()")
        return cursor.fetchone()[0]


def _link_study(study_id: int, publication_id: int) -> None:
    """Create the SEEK relationships row, if it is not already there."""
    from django.db import connections
    from django.conf import settings

    existing = _rows(
        """
        SELECT id FROM relationships
        WHERE subject_type='Study' AND subject_id=%s
          AND predicate='related_to_publication'
          AND other_object_type='Publication' AND other_object_id=%s
        """,
        [int(study_id), int(publication_id)],
    )
    if existing:
        return
    with connections[settings.SEEK_DATABASE].cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO relationships
                (subject_type, subject_id, predicate, other_object_type,
                 other_object_id, created_at, updated_at)
            VALUES ('Study', %s, 'related_to_publication', 'Publication', %s, NOW(), NOW())
            """,
            [int(study_id), int(publication_id)],
        )


class Command(BaseCommand):
    help = "Extract DOIs from study descriptions for curator review, then apply them."

    def add_arguments(self, parser):
        parser.add_argument("--extract", action="store_true",
                            help="Write a review file. Touches no database.")
        parser.add_argument("--apply", metavar="FILE",
                            help="Insert the approved rows from a reviewed file.")
        parser.add_argument("--out", default="publication_review.tsv",
                            help="Where --extract writes its review file.")
        parser.add_argument("--offline", action="store_true",
                            help="Skip Crossref/NCBI; extract identifiers only.")
        parser.add_argument("--cache", default="publication_resolve_cache.json",
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

        touched = set()
        for row in approved:
            publication_id = _insert_publication(row)
            _link_study(int(row["study_id"]), publication_id)
            touched.add(publication_id)

        deferred = 0
        for publication_id in touched:
            if not sync_publication_to_graph(publication_id):
                deferred += 1

        self.stdout.write(
            f"applied {len(approved)} rows, {len(touched)} publications, "
            f"{deferred} graph syncs deferred"
        )
        if deferred:
            self.stdout.write("Run `manage.py sync_publications_graph` to repair the graph.")
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
uv run pytest nextseek_api/tests/test_backfill_study_publications.py -v
```

Expected: all PASS.

- [ ] **Step 5: Run the extraction against the real data, offline first**

```bash
docker compose exec -T nextseek uv run manage.py backfill_study_publications --extract --offline --out /tmp/review_offline.tsv
```

Expected: `51 studies scanned` with roughly 34 candidates — about 33 `unresolved` (offline, so no metadata) and 1 `manual` for the truncated `10.3390/`. This proves extraction works with no network.

- [ ] **Step 6: Run it with resolution**

```bash
docker compose exec -T nextseek uv run manage.py backfill_study_publications --extract --out /tmp/review.tsv
```

```bash
docker compose exec -T nextseek sh -c 'column -t -s "$(printf "\t")" /tmp/review.tsv | head -40'
```

Expected: most rows have `proposed_action=create` with a populated `resolved_title`, `journal`, `year`, and often a `pmid`. **Read the `title_similarity` column.** Any row below roughly 0.6 is citing a paper whose title does not match its study — check it by hand before approving.

- [ ] **Step 7: Do not apply blind — hand the file to a curator**

Copy the file out and have it reviewed:

```bash
docker compose cp nextseek:/tmp/review.tsv ./publication_review.tsv
```

Applying is a curator's decision, not the implementer's. When approved rows come back:

```bash
docker compose cp ./publication_review.tsv nextseek:/tmp/review_approved.tsv
docker compose exec -T nextseek uv run manage.py backfill_study_publications --apply /tmp/review_approved.tsv
```

- [ ] **Step 8: Commit** (the command, not the review file)

```bash
git add nextseek_api/management/commands/backfill_study_publications.py nextseek_api/tests/test_backfill_study_publications.py
git commit -m "feat(publications): backfill command with curator review gate"
```

---

### Task 10: Teach Nessie the publication subgraph

**Files:**
- Modify: `chat_nextseek/src/chat_nextseek/context/min_graph_schema.json`
- Modify: `chat_nextseek/src/chat_nextseek/prompts/graph_agent.txt`
- Modify: `chat_nextseek/src/chat_nextseek/context/capabilities.md`
- Modify: `chat_nextseek/src/chat_nextseek/config.py` — the vocabulary block in the schema fetch (line ~1590)
- Test: `chat_nextseek/tests/test_publication_schema_context.py`

**Interfaces:**
- Consumes: the graph shape produced by Task 7.
- Produces: no Python API. `min_graph_schema.json` gains a `Publication` entry in `node_types`, a `REPORTED_IN` entry in `relationships`, and two new `graph_query_triggers`.

- [ ] **Step 1: Note what must NOT be edited**

`neo4j_schema.json`, `neo4j_schema_dev.json` and `neo4j_schema_prod.json` are **generated** by `config.py:1574` and overwritten on each fetch — that is what the `fetched_at` key means. Editing them by hand would be undone. They pick up `Publication` automatically once nodes exist.

`min_graph_schema.json` is hand-written and is the file to change.

- [ ] **Step 2: Write the failing test**

Create `chat_nextseek/tests/test_publication_schema_context.py`:

```python
"""The routing schema must describe the publication subgraph.

The graph agent writes Cypher from this file. A node label that exists in Neo4j
but not here is a label the agent will never query.
"""

import json
from pathlib import Path

CONTEXT = Path(__file__).resolve().parents[1] / "src" / "chat_nextseek" / "context"


def _min_schema():
    return json.loads((CONTEXT / "min_graph_schema.json").read_text())


def test_publication_node_is_described():
    labels = [n["label"] for n in _min_schema()["node_types"]]
    assert "Publication" in labels


def test_publication_description_names_its_properties():
    node = next(n for n in _min_schema()["node_types"] if n["label"] == "Publication")
    for prop in ("doi", "pmid", "title"):
        assert prop in node["description"]


def test_reported_in_relationship_is_described():
    types = [r["type"] for r in _min_schema()["relationships"]]
    assert "REPORTED_IN" in types


def test_reported_in_covers_both_sources():
    rel = next(r for r in _min_schema()["relationships"] if r["type"] == "REPORTED_IN")
    text = rel["direction"] + " " + rel["note"]
    assert "sample" in text.lower()
    assert "study" in text.lower()


def test_triggers_cover_both_directions():
    triggers = " ".join(_min_schema()["graph_query_triggers"]).lower()
    assert "doi" in triggers
    assert "pmid" in triggers or "pubmed" in triggers
    assert "paper" in triggers or "publication" in triggers


def test_generated_schema_files_are_not_hand_edited():
    # Guard rail: these carry fetched_at because config.py regenerates them.
    for name in ("neo4j_schema.json", "neo4j_schema_dev.json", "neo4j_schema_prod.json"):
        assert "fetched_at" in json.loads((CONTEXT / name).read_text())
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
cd chat_nextseek && uv run pytest tests/test_publication_schema_context.py -v
```

Expected: FAIL — `"Publication" in labels` is False.

- [ ] **Step 4: Update `min_graph_schema.json`**

Add to `node_types`, after the `Investigation` entry:

```json
    {
      "label": "Publication",
      "description": "A published paper. Properties: 'doi', 'pmid', 'title', 'journal', 'year', 'url'. Samples that appeared in a paper link to it via REPORTED_IN, as do the studies the paper reports on. Most samples are unpublished and have no Publication at all."
    }
```

Add to `relationships`:

```json
    {
      "type": "REPORTED_IN",
      "direction": "(sample)-[:REPORTED_IN]->(publication) and (study)-[:REPORTED_IN]->(publication)",
      "note": "Both samples and studies use this same relationship type; the label on the left of the pattern is what distinguishes them. Always write (s:Sample)-[:REPORTED_IN]->(p:Publication) or (st:Study)-[:REPORTED_IN]->(p:Publication), never an unlabelled source, or samples and studies will be mixed in one result."
    }
```

Add to `graph_query_triggers`:

```json
    "Query asks which paper or publication a sample appears in ('what paper is this sample from', 'is UID X published', 'which publication used these samples')",
    "Query names a paper by title, DOI, or PMID and asks for its samples ('what samples were used in the SureQuant paper', 'samples for 10.1101/2021.09.30.462577', 'samples in PMID 34981053')"
```

Add to `disambiguation_rules`:

```json
    "If the query mentions a DOI, a PMID, a paper, or a publication → graph_query (publication links exist only in the graph and have no REST endpoint for sample traversal)"
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd chat_nextseek && uv run pytest tests/test_publication_schema_context.py -v
```

Expected: all PASS.

- [ ] **Step 6: Update the graph agent prompt**

In `chat_nextseek/src/chat_nextseek/prompts/graph_agent.txt`, add to the section describing the graph shape:

```
Publications
  (:Publication {doi, pmid, title, journal, year, url})
  (s:Sample)-[:REPORTED_IN]->(p:Publication)   -- this sample appeared in that paper
  (st:Study)-[:REPORTED_IN]->(p:Publication)   -- that paper reports on this study

  Samples and studies share the REPORTED_IN type, so ALWAYS put a label on the
  left of the pattern. An unlabelled source mixes samples and studies together.

  Match a paper by DOI with toLower(p.doi) = toLower($doi); DOIs are stored
  lowercased. Match by PMID with p.pmid. Match by name with a title CONTAINS.

  Most samples are unpublished. "No publication" is a correct answer, not a
  failed lookup — do not fall back to a broader query to find something.
```

- [ ] **Step 7: Update `capabilities.md`**

In `chat_nextseek/src/chat_nextseek/context/capabilities.md`, add to the capability list:

```markdown
- **Publication links.** Which published paper a sample appears in, and the
  reverse — the samples used in a paper, looked up by title, DOI, or PMID.
  Samples inherit their studies' publications, with curator-recorded exceptions.
  Most samples are unpublished; that is expected, not a gap.
```

- [ ] **Step 8: Add publications to the generated vocabulary**

In `chat_nextseek/src/chat_nextseek/config.py`, in the schema fetch that builds the `vocabulary` block (near line 1590), add a query alongside the existing vocabulary lookups:

```python
        # Publication titles and DOIs, so the graph agent can map a phrase like
        # "the SureQuant paper" onto a real node.
        try:
            records, _, _ = driver.execute_query(
                "MATCH (p:Publication) RETURN p.title AS title, p.doi AS doi, p.pmid AS pmid LIMIT 500",
                database_=self.NEO4J_DATABASE_NAME,
            )
            schema["vocabulary"]["publications"] = [
                {"title": r["title"], "doi": r["doi"], "pmid": r["pmid"]} for r in records
            ]
        except Exception as e:
            print(f"[CONFIG][GRAPHDB] Publication vocabulary fetch failed: {e!r}")
```

Match the surrounding code's exact attribute name for the database (read the neighbouring calls in that function — earlier code uses `NEO4J_DATABASE['NAME']` via a local, so use whatever that function already uses rather than inventing a name).

- [ ] **Step 9: Verify Nessie can answer both directions**

Only meaningful once Task 9 has applied real publications. With them in place:

```bash
docker compose exec -T nextseek uv run manage.py nessie --question "what samples were used in the SureQuant paper?"
```

```bash
docker compose exec -T nextseek uv run manage.py nessie --question "what paper is sample <a real published UID> from?"
```

Expected: the first returns a sample list scoped to that study; the second returns the paper with its DOI. If the agent returns nothing, check that `Publication` nodes exist (`MATCH (p:Publication) RETURN count(p)`) before suspecting the prompt.

- [ ] **Step 10: Commit**

```bash
git add chat_nextseek/src/chat_nextseek/context/min_graph_schema.json chat_nextseek/src/chat_nextseek/prompts/graph_agent.txt chat_nextseek/src/chat_nextseek/context/capabilities.md chat_nextseek/src/chat_nextseek/config.py chat_nextseek/tests/test_publication_schema_context.py
git commit -m "feat(nessie): teach the graph agent the publication subgraph"
```

---

## Final verification

- [ ] **Full test suite**

```bash
uv run pytest seek nextseek_api -v
```

Expected: all new tests pass and nothing pre-existing regressed. Note pytest uses `DJANGO_SETTINGS_MODULE = "dmac.settings"` (pyproject.toml:147) — the `dmac.test_settings` mentioned in CLAUDE.md is not what pyproject configures.

- [ ] **End-to-end check on the running stack**

```bash
./startup.sh rebuild && docker compose exec -T nextseek uv run manage.py sync_publications_graph
```

```bash
docker exec neo4j sh -c 'cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "MATCH (s:Sample)-[:REPORTED_IN]->(p:Publication) RETURN p.title, count(s) ORDER BY count(s) DESC LIMIT 10;"'
```

Expected: one row per backfilled paper with its sample count. Cross-check one against MySQL:

```bash
docker compose exec -T nextseek uv run python -c "
import django, os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','dmac.settings'); django.setup()
from nextseek_api.services.publications import samples_for_publication
print(len(samples_for_publication(1)))
"
```

The two counts must agree. If they do not, the graph is stale — rerun the reconcile command and investigate before shipping.

- [ ] **Confirm unpublished samples stayed silent**

```bash
docker exec neo4j sh -c 'cypher-shell -u neo4j -p "$NEO4J_PASSWORD" "MATCH (s:Sample) WHERE NOT (s)-[:REPORTED_IN]->() RETURN count(s);"'
```

Expected: the large majority of the 51,361 samples. If this number is near zero, inheritance is over-applying and the effective-set SQL is wrong.

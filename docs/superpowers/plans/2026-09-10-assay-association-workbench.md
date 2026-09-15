# Assay Association Workbench Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the internal-assay and clade association pages show what needs work, accept the mechanical majority in bulk with visible reasoning, and save honestly.

**Architecture:** A pure Python resolver in `dmac/vocab_resolver.py` ranks candidate vocabulary terms for an unmapped entity title and is served read-only by one new endpoint. A domain-agnostic JS module renders tier-grouped rows with inline evidence over easyui's native `detailview`. The six existing save/delete endpoints move from `GET`-with-query-string to `POST`+JSON with atomic batches.

**Tech Stack:** Django 4 (Mezzanine), MySQL, jQuery + easyui datagrid, pytest-django, uv.

**Spec:** `docs/superpowers/specs/2026-09-10-assay-association-workbench-design.md`

## Global Constraints

- **No new Python dependencies.** Do not run `uv add`. The resolver uses only the standard library.
- **`dmac/vocab_resolver.py` must never import Django** (no `django.*`, no `dmac.settings`, no models). It is pure and Project B reuses it.
- **`static/js/custom/ns-vocab-workbench.js` must never contain the string `assay`.** Domain labels arrive only through the config object each template passes in.
- **Tests run via `./scripts/run_tests.sh <paths> -q`.** This is the ONLY correct lane:
  it mounts *this* checkout over `/app` in the stack image. A bare
  `docker exec ... nextseek ... pytest` runs the code **baked into the image**, which is
  stale — `/app` is not a bind mount (only `logs`, `themes/NextSeek`, `local_settings.py`
  and `outputs` are). Verified 2026-09-10: the baked copy was three commits behind and
  silently reported 17 passed where the checkout had 22.
- **No schema migration.** The association tables keep `(id, entity_id, vocabulary_id)`.
- **Conventional commits with module scopes**, e.g. `feat(seek): …`, `fix(seek): …`, `test(dmac): …`.
- **The repo is PUBLIC.** No hostnames, credentials, or environment concretes in any committed file.
- **Rebuild with `./startup.sh rebuild`**, never raw `docker compose` — it runs `collectstatic` itself, which a plain rebuild does not.
- **Superuser gating via `verifySuperUser(request)` (`seek/views.py:794`) stays on every endpoint.** It returns `1` for superusers and `0` otherwise.

## File Structure

| File | Responsibility |
|---|---|
| `dmac/vocab_resolver.py` *(create)* | Pure ranking of vocabulary candidates for a title |
| `dmac/tests/__init__.py` *(create)* | Makes `dmac/tests` a package for pytest |
| `dmac/tests/test_vocab_resolver.py` *(create)* | Table-driven resolver tests, no DB |
| `seek/views.py` *(modify)* | 6 endpoints to POST+JSON+atomic; 1 new suggestions view |
| `seek/urls.py` *(modify)* | 1 new route |
| `seek/tests/test_vocab_endpoints.py` *(create)* | Endpoint tests: POST-only, gating, atomicity |
| `static/js/custom/ns-vocab-workbench.js` *(create)* | Domain-agnostic tier-grouped workbench |
| `seek/templates/internal_assays.html` *(modify)* | Config + include; delete local `saveSelectedIntoDB` |
| `seek/templates/clades.html` *(modify)* | Config + include; delete local `saveSelectedIntoDB` |
| `themes/NextSeek/static/css/nextseek.css` *(modify)* | Tier badge and group styling |

---

### Task 1: Resolver — normalization and the exact tier

**Files:**
- Create: `dmac/vocab_resolver.py`
- Create: `dmac/tests/__init__.py`
- Test: `dmac/tests/test_vocab_resolver.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `normalize(title) -> str`, `strip_disposition(title) -> str`, `Candidate` dataclass with fields `vocabulary_id: int`, `vocabulary_title: str`, `tier: str`, `basis: str`, `support: int`, and `suggest(title, vocabulary, precedents) -> list[Candidate]`. `vocabulary` is a list of `(id, title)` tuples. `precedents` is a list of `(entity_title, vocabulary_id, vocabulary_title)` tuples. Tier constants `TIER_EXACT`, `TIER_PRECEDENT`, `TIER_FUZZY`, `TIER_CONFLICT`, `TIER_NONE`.

- [ ] **Step 1: Write the failing test**

Create `dmac/tests/__init__.py` as an empty file, then create `dmac/tests/test_vocab_resolver.py`:

```python
"""Resolver unit tests. Pure — no DB, no Django."""

import pytest

from dmac.vocab_resolver import (
    TIER_EXACT,
    TIER_NONE,
    normalize,
    strip_disposition,
    suggest,
)

VOCAB = [
    (74, "Tissue Collection"),
    (58, "PET/CT Scan"),
    (40, "Library Prep"),
    (57, "PCR"),
]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Tissue Collection - Metadata", "Tissue Collection"),
        ("Flow Cytometry - Data Linked", "Flow Cytometry"),
        ("Comet Chip Analysis - Data Attached", "Comet Chip Analysis"),
        ("Library Prep: Validation Data", "Library Prep"),
        ("Gene Expression Analysis: Training Data", "Gene Expression Analysis"),
        # en dash, not hyphen — the case a naive split silently misses
        ("Patient Visit – Metadata", "Patient Visit"),
        ("All Metadata", "All Metadata"),
    ],
)
def test_strip_disposition(raw, expected):
    assert strip_disposition(raw) == expected


def test_normalize_collapses_punctuation_and_case():
    assert normalize("PET-CT Scan") == normalize("PET/CT Scan")
    assert normalize("  Tissue   Collection ") == "tissue collection"


def test_exact_match_after_stripping():
    cands = suggest("Tissue Collection - Metadata", VOCAB, [])
    assert len(cands) == 1
    assert cands[0].vocabulary_id == 74
    assert cands[0].tier == TIER_EXACT


def test_exact_match_ignores_punctuation_difference():
    cands = suggest("PET-CT Scan - Data Linked", VOCAB, [])
    assert cands[0].vocabulary_id == 58
    assert cands[0].tier == TIER_EXACT


def test_no_candidate_is_a_tier_not_an_empty_list():
    cands = suggest("All Metadata", VOCAB, [])
    assert len(cands) == 1
    assert cands[0].tier == TIER_NONE
    assert cands[0].vocabulary_id is None


def test_blank_title_does_not_raise():
    assert suggest("", VOCAB, [])[0].tier == TIER_NONE
    assert suggest(None, VOCAB, [])[0].tier == TIER_NONE
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./scripts/run_tests.sh dmac/tests/test_vocab_resolver.py -q
```

Expected: collection error, `ModuleNotFoundError: No module named 'dmac.vocab_resolver'`.

- [ ] **Step 3: Write minimal implementation**

Create `dmac/vocab_resolver.py`:

```python
"""Rank controlled-vocabulary candidates for an entity title.

Pure: no Django, no ORM, no I/O. Both the admin workbench and (later) the
batch-upload auto-mapper consume this, so identical inputs must always give
identical output — ties break on vocabulary id.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

TIER_EXACT = "exact"
TIER_PRECEDENT = "precedent"
TIER_FUZZY = "fuzzy"
TIER_CONFLICT = "conflict"
TIER_NONE = "none"

# Unicode dash variants seen in real titles, normalized to ASCII hyphen before
# any suffix matching. An en dash here is why naive prefix splitting misses rows.
_DASHES = "‐‑‒–—―−"

# Disposition suffixes: what the assay carries, not what it is.
DISPOSITION_SUFFIXES = (
    " - Metadata",
    " - Data Linked",
    " - Data Attached",
    ": Training Data",
    ": Validation Data",
)


@dataclass(frozen=True)
class Candidate:
    vocabulary_id: Optional[int]
    vocabulary_title: Optional[str]
    tier: str
    basis: str
    support: int


def _dash_normalize(text: str) -> str:
    for dash in _DASHES:
        text = text.replace(dash, "-")
    return text


def strip_disposition(title: Optional[str]) -> str:
    """Remove a trailing disposition suffix. Case- and dash-insensitive."""
    if not title:
        return ""
    text = _dash_normalize(str(title)).strip()
    lowered = text.casefold()
    for suffix in DISPOSITION_SUFFIXES:
        if lowered.endswith(suffix.casefold()):
            return text[: len(text) - len(suffix)].strip()
    return text


def normalize(title: Optional[str]) -> str:
    """Casefolded, punctuation-insensitive comparison key."""
    if not title:
        return ""
    text = _dash_normalize(str(title))
    text = re.sub(r"[^0-9A-Za-z]+", " ", text)
    return re.sub(r"\s+", " ", text).strip().casefold()


def _none_candidate() -> Candidate:
    return Candidate(
        vocabulary_id=None,
        vocabulary_title=None,
        tier=TIER_NONE,
        basis="No vocabulary term matched this title.",
        support=0,
    )


def suggest(
    title: Optional[str],
    vocabulary: Sequence[Tuple[int, str]],
    precedents: Iterable[Tuple[str, int, str]],
) -> List[Candidate]:
    """Return ranked candidates, always at least one (possibly TIER_NONE)."""
    key = normalize(strip_disposition(title))
    if not key:
        return [_none_candidate()]

    for vocab_id, vocab_title in sorted(vocabulary, key=lambda v: v[0]):
        if normalize(vocab_title) == key:
            return [
                Candidate(
                    vocabulary_id=vocab_id,
                    vocabulary_title=vocab_title,
                    tier=TIER_EXACT,
                    basis="Title matches the vocabulary term exactly.",
                    support=0,
                )
            ]

    return [_none_candidate()]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./scripts/run_tests.sh dmac/tests/test_vocab_resolver.py -q
```

Expected: PASS, 12 passed (7 parametrized strip_disposition cases + 5 test functions).

- [ ] **Step 5: Commit**

```bash
git add dmac/vocab_resolver.py dmac/tests/__init__.py dmac/tests/test_vocab_resolver.py
git commit -m "feat(dmac): vocabulary resolver with exact-match tier"
```

---

### Task 2: Resolver — precedent tier and its guard

**Files:**
- Modify: `dmac/vocab_resolver.py`
- Test: `dmac/tests/test_vocab_resolver.py`

**Interfaces:**
- Consumes: Task 1's `suggest`, `normalize`, `strip_disposition`, `Candidate`.
- Produces: `suggest` additionally returns `TIER_PRECEDENT` (support ≥ 2), demotes single-precedent matches to `TIER_FUZZY`, and returns multiple candidates at `TIER_CONFLICT` when precedents disagree.

- [ ] **Step 1: Write the failing test**

Append to `dmac/tests/test_vocab_resolver.py`:

```python
from dmac.vocab_resolver import TIER_CONFLICT, TIER_FUZZY, TIER_PRECEDENT

VOCAB2 = VOCAB + [(12, "Cell Isolation"), (34, "Genome Alignment")]

# Two mapped assays share the stripped title "Cell Extraction" -> Cell Isolation.
PRECEDENTS_STRONG = [
    ("Cell Extraction", 12, "Cell Isolation"),
    ("Cell Extraction: Validation Data", 12, "Cell Isolation"),
]


def test_precedent_with_two_supporters_is_precedent_tier():
    cands = suggest("Cell Extraction - Metadata", VOCAB2, PRECEDENTS_STRONG)
    assert cands[0].tier == TIER_PRECEDENT
    assert cands[0].vocabulary_id == 12
    assert cands[0].support == 2


def test_precedent_basis_names_its_support():
    basis = suggest("Cell Extraction - Metadata", VOCAB2, PRECEDENTS_STRONG)[0].basis
    assert "2" in basis and "Cell Isolation" in basis


def test_single_precedent_is_demoted_to_fuzzy():
    """One prior mapping is an anecdote, not a convention."""
    cands = suggest(
        "Glycosylation Assay - Data Linked",
        VOCAB2,
        [("Glycosylation Assay", 34, "Genome Alignment")],
    )
    assert cands[0].tier == TIER_FUZZY
    assert cands[0].support == 1


def test_disagreeing_precedents_produce_a_conflict_with_both_shown():
    cands = suggest(
        "Imaging Run - Metadata",
        VOCAB2,
        [
            ("Imaging Run", 12, "Cell Isolation"),
            ("Imaging Run", 12, "Cell Isolation"),
            ("Imaging Run", 34, "Genome Alignment"),
            ("Imaging Run", 34, "Genome Alignment"),
        ],
    )
    assert all(c.tier == TIER_CONFLICT for c in cands)
    assert {c.vocabulary_id for c in cands} == {12, 34}


def test_exact_match_beats_precedent():
    cands = suggest("Library Prep - Metadata", VOCAB2, [("Library Prep", 12, "Cell Isolation")])
    assert cands[0].tier == TIER_EXACT
    assert cands[0].vocabulary_id == 40
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./scripts/run_tests.sh dmac/tests/test_vocab_resolver.py -q
```

Expected: FAIL — `ImportError: cannot import name 'TIER_CONFLICT'`.

- [ ] **Step 3: Write minimal implementation**

In `dmac/vocab_resolver.py`, add this helper above `suggest`:

```python
def _precedent_index(precedents: Iterable[Tuple[str, int, str]]) -> dict:
    """Map normalized stripped entity title -> {vocab_id: (title, count)}."""
    index: dict = {}
    for entity_title, vocab_id, vocab_title in precedents:
        key = normalize(strip_disposition(entity_title))
        if not key or vocab_id is None:
            continue
        bucket = index.setdefault(key, {})
        title, count = bucket.get(vocab_id, (vocab_title, 0))
        bucket[vocab_id] = (title, count + 1)
    return index
```

Then replace the `return [_none_candidate()]` at the end of `suggest` with:

```python
    bucket = _precedent_index(precedents).get(key, {})
    if bucket:
        ordered = sorted(bucket.items(), key=lambda kv: (-kv[1][1], kv[0]))
        if len(ordered) > 1:
            return [
                Candidate(
                    vocabulary_id=vocab_id,
                    vocabulary_title=vocab_title,
                    tier=TIER_CONFLICT,
                    basis=(
                        f"{count} mapped entities with this title map to "
                        f"{vocab_title}, but others disagree."
                    ),
                    support=count,
                )
                for vocab_id, (vocab_title, count) in ordered
            ]
        vocab_id, (vocab_title, count) = ordered[0]
        # A single prior mapping is an anecdote, not a convention. Demoting it
        # is what stops one bad mapping propagating with a confident badge.
        tier = TIER_PRECEDENT if count >= 2 else TIER_FUZZY
        return [
            Candidate(
                vocabulary_id=vocab_id,
                vocabulary_title=vocab_title,
                tier=tier,
                basis=(
                    f"{count} mapped entit{'ies' if count != 1 else 'y'} with "
                    f"this title map to {vocab_title}."
                ),
                support=count,
            )
        ]

    return [_none_candidate()]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./scripts/run_tests.sh dmac/tests/test_vocab_resolver.py -q
```

Expected: PASS, 17 passed.

- [ ] **Step 5: Commit**

```bash
git add dmac/vocab_resolver.py dmac/tests/test_vocab_resolver.py
git commit -m "feat(dmac): precedent tier with n>=2 guard and conflict detection"
```

---

### Task 3: Resolver — fuzzy tier

**Files:**
- Modify: `dmac/vocab_resolver.py`
- Test: `dmac/tests/test_vocab_resolver.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: `suggest` falls back to token-overlap matching against vocabulary titles before returning `TIER_NONE`. Adds module constant `FUZZY_THRESHOLD = 0.6`.

- [ ] **Step 1: Write the failing test**

Append to `dmac/tests/test_vocab_resolver.py`:

```python
VOCAB3 = VOCAB2 + [(49, "Mass Spectrometry Proteomics Analysis"), (42, "Luminex")]


def test_fuzzy_matches_a_qualified_variant():
    """'Real Time RT-PCR' is a PCR; the only PCR term should surface."""
    cands = suggest("Real Time RT-PCR - Data Linked", VOCAB3, [])
    assert cands[0].tier == TIER_FUZZY
    assert cands[0].vocabulary_id == 57


def test_fuzzy_matches_a_shortened_variant():
    cands = suggest("Proteomics Analysis - Data Linked", VOCAB3, [])
    assert cands[0].tier == TIER_FUZZY
    assert cands[0].vocabulary_id == 49


def test_fuzzy_matches_a_prefixed_variant():
    cands = suggest("Cytokine Luminex - Data Linked", VOCAB3, [])
    assert cands[0].tier == TIER_FUZZY
    assert cands[0].vocabulary_id == 42


def test_unrelated_title_stays_none_rather_than_guessing():
    """The resolver declining is correct behaviour, not a failure."""
    cands = suggest("RaDR - Data Linked", VOCAB3, [])
    assert cands[0].tier == TIER_NONE


def test_fuzzy_never_outranks_exact():
    cands = suggest("PCR - Data Linked", VOCAB3, [])
    assert cands[0].tier == TIER_EXACT
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./scripts/run_tests.sh dmac/tests/test_vocab_resolver.py -q
```

Expected: FAIL — `test_fuzzy_matches_a_qualified_variant` asserts `TIER_FUZZY`, gets `TIER_NONE`.

- [ ] **Step 3: Write minimal implementation**

In `dmac/vocab_resolver.py`, add near the tier constants:

```python
# Overlap needed before a guess is worth showing at all. Fuzzy is the lowest
# actionable tier: a curator must approve it, so mild permissiveness is fine.
FUZZY_THRESHOLD = 0.6
```

Add this helper above `suggest`:

```python
def _overlap(left: str, right: str) -> float:
    """Shared tokens over the smaller token set. 0.0 when either side is empty."""
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    shared = left_tokens & right_tokens
    return len(shared) / min(len(left_tokens), len(right_tokens))
```

Then, in `suggest`, replace the final `return [_none_candidate()]` with:

```python
    scored = []
    for vocab_id, vocab_title in vocabulary:
        score = _overlap(key, normalize(vocab_title))
        if score >= FUZZY_THRESHOLD:
            scored.append((score, -vocab_id, vocab_id, vocab_title))
    if scored:
        scored.sort(reverse=True)
        _, _, vocab_id, vocab_title = scored[0]
        return [
            Candidate(
                vocabulary_id=vocab_id,
                vocabulary_title=vocab_title,
                tier=TIER_FUZZY,
                basis=f"Title overlaps the vocabulary term {vocab_title}.",
                support=0,
            )
        ]

    return [_none_candidate()]
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./scripts/run_tests.sh dmac/tests/test_vocab_resolver.py -q
```

Expected: PASS, 22 passed.

- [ ] **Step 5: Commit**

```bash
git add dmac/vocab_resolver.py dmac/tests/test_vocab_resolver.py
git commit -m "feat(dmac): fuzzy token-overlap tier for the vocabulary resolver"
```

---

### Task 4: Suggestions endpoint

**Files:**
- Modify: `seek/views.py` (add view after `internalAssays`, currently at line 1620)
- Modify: `seek/urls.py` (add route beside line 27)
- Test: `seek/tests/test_vocab_endpoints.py` *(create)*

**Interfaces:**
- Consumes: `dmac.vocab_resolver.suggest`, `Candidate`.
- Produces: `GET /seek/admin/internal_assays/suggestions` named `internalAssaySuggestions`, returning
  `{"status": 1, "msg": "", "errors": [], "suggestions": {"<assay_id>": [{"vocabulary_id", "vocabulary_title", "tier", "basis", "support"}]}}`.
  Only unmapped rows appear as keys.

- [ ] **Step 1: Write the failing test**

Create `seek/tests/test_vocab_endpoints.py`:

```python
"""Endpoint contract for the association workbench.

These assert the shape the shared JS relies on, and the gating that keeps the
admin surface superuser-only.
"""

import json

import pytest
from django.contrib.auth.models import User
from django.test import Client
from django.urls import reverse


@pytest.fixture
def superuser(db):
    return User.objects.create_superuser("wb-admin", "wb@example.invalid", "pw-not-real")


@pytest.fixture
def plain_user(db):
    return User.objects.create_user("wb-plain", "wp@example.invalid", "pw-not-real")


def test_suggestions_route_resolves():
    assert reverse("internalAssaySuggestions") == "/seek/admin/internal_assays/suggestions"


@pytest.mark.django_db
def test_suggestions_rejects_non_superuser(plain_user, monkeypatch):
    import seek.views as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    client = Client()
    client.force_login(plain_user)
    resp = client.get("/seek/admin/internal_assays/suggestions")
    assert json.loads(resp.content)["status"] == 0


@pytest.mark.django_db
def test_suggestions_returns_envelope(superuser, monkeypatch):
    import seek.views as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    monkeypatch.setattr(
        views.DBtable_internalassays,
        "getAll",
        lambda self: [{"id": 74, "internal_assay_title": "Tissue Collection"}],
    )
    monkeypatch.setattr(
        views.DBtable_assaysinternalassays,
        "getAllWithTitles",
        lambda self: [
            {"assay_id": 1, "assay_title": "Tissue Collection - Metadata",
             "internal_assay_id": None, "internal_assay_title": None},
            {"assay_id": 2, "assay_title": "Tissue Collection - Metadata",
             "internal_assay_id": 74, "internal_assay_title": "Tissue Collection"},
        ],
    )
    client = Client()
    client.force_login(superuser)
    body = json.loads(client.get("/seek/admin/internal_assays/suggestions").content)

    assert body["status"] == 1
    # Only the unmapped row is offered a suggestion.
    assert set(body["suggestions"]) == {"1"}
    assert body["suggestions"]["1"][0]["tier"] == "exact"
    assert body["suggestions"]["1"][0]["vocabulary_id"] == 74
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./scripts/run_tests.sh seek/tests/test_vocab_endpoints.py -q
```

Expected: FAIL — `NoReverseMatch: Reverse for 'internalAssaySuggestions' not found`.

- [ ] **Step 3: Write minimal implementation**

At the top of `seek/views.py`, beside the other `dmac` imports, add:

```python
from dmac.vocab_resolver import suggest as vocab_suggest
```

Add this view immediately after `internalAssays` (which ends at line 1641):

```python
def internalAssaySuggestions(request):
    """Read-only tiered suggestions for every unmapped assay.

    Deliberately fails soft: the workbench renders 'unavailable' and stays
    usable, the same way router.py degrades on a BAML failure.
    """
    seekdb = SeekDB(None, None, None)
    user_seek = seekdb.getSeekLogin(request, False)
    if not user_seek['status']:
        return JsonResponse({'status': 0, 'msg': user_seek.get('err', 'Not signed in'),
                             'errors': [], 'suggestions': {}})

    if verifySuperUser(request) != 1:
        return JsonResponse({'status': 0, 'msg': 'Superuser required.',
                             'errors': [], 'suggestions': {}})

    vocabulary = [(v['id'], v['internal_assay_title'])
                  for v in DBtable_internalassays().getAll()]
    rows = DBtable_assaysinternalassays().getAllWithTitles()
    precedents = [(r['assay_title'], r['internal_assay_id'], r['internal_assay_title'])
                  for r in rows if r.get('internal_assay_id')]

    suggestions = {}
    for row in rows:
        if row.get('internal_assay_id'):
            continue
        candidates = vocab_suggest(row.get('assay_title'), vocabulary, precedents)
        suggestions[str(row['assay_id'])] = [
            {'vocabulary_id': c.vocabulary_id, 'vocabulary_title': c.vocabulary_title,
             'tier': c.tier, 'basis': c.basis, 'support': c.support}
            for c in candidates
        ]

    return JsonResponse({'status': 1, 'msg': '', 'errors': [],
                         'suggestions': suggestions})
```

If `JsonResponse` is not already imported in `seek/views.py`, add `from django.http import JsonResponse` beside the existing `django.http` imports.

In `seek/urls.py`, after line 27, add:

```python
    re_path(r'^admin/internal_assays/suggestions$', views.internalAssaySuggestions, name="internalAssaySuggestions"),
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./scripts/run_tests.sh seek/tests/test_vocab_endpoints.py -q
```

Expected: PASS, 3 passed.

- [ ] **Step 5: Commit**

```bash
git add seek/views.py seek/urls.py seek/tests/test_vocab_endpoints.py
git commit -m "feat(seek): read-only suggestions endpoint for assay associations"
```

---

### Task 5: Assay endpoints — POST, JSON envelope, atomic batches

**Files:**
- Modify: `seek/views.py` — `internalAssaySave` (1643), `internalAssayDelete` (1680), `assayAssociationSave` (1712)
- Test: `seek/tests/test_vocab_endpoints.py`

**Interfaces:**
- Consumes: Task 4's `JsonResponse` import.
- Produces: module-level helpers `_wb_envelope(status, msg, updated=0, errors=None) -> JsonResponse`, `_wb_records(request) -> list`, `_wb_guard(request, action) -> JsonResponse|None`, exception `_WbRowError`, and `_wb_batch(records, apply_row, noun) -> JsonResponse`. All three endpoints accept POST only and return `{"status", "msg", "updated", "errors"}`.
- **Semantics: partial success.** Each record commits in its own transaction. A failing record is reported in `errors[]` and its siblings still commit. `status` is `1` when at least one record was written (even alongside errors) and `0` when nothing was written. Task 6 reuses `_wb_batch` unchanged.

- [ ] **Step 1: Write the failing test**

Append to `seek/tests/test_vocab_endpoints.py`:

```python
ASSOC_URL = "/seek/internal_assays/assayAssociation/save"


@pytest.mark.django_db
def test_association_save_rejects_get(superuser, monkeypatch):
    import seek.views as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    client = Client()
    client.force_login(superuser)
    assert client.get(ASSOC_URL).status_code == 405


@pytest.mark.django_db
def test_association_save_reports_updated_count(superuser, monkeypatch):
    import seek.views as views

    seen = []
    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    monkeypatch.setattr(
        views.DBtable_assaysinternalassays, "update",
        lambda self, a, i: seen.append((a, i)),
    )
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        ASSOC_URL,
        data=json.dumps({"records": [{"assay_id": 1, "internal_assay_id": 74},
                                     {"assay_id": 2, "internal_assay_id": 74}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)
    assert body["status"] == 1
    assert body["updated"] == 2
    assert seen == [(1, 74), (2, 74)]


@pytest.mark.django_db
def test_association_save_collects_per_row_errors(superuser, monkeypatch):
    """A row removed by an intervening Sync must not 500 the batch."""
    import seek.views as views

    def _boom(self, assay_id, internal_assay_id):
        raise views.Assays_internal_assays.DoesNotExist("gone")

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    monkeypatch.setattr(views.DBtable_assaysinternalassays, "update", _boom)
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        ASSOC_URL,
        data=json.dumps({"records": [{"assay_id": 999, "internal_assay_id": 74}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)
    assert body["status"] == 0
    assert body["updated"] == 0
    assert "999" in json.dumps(body["errors"])


@pytest.mark.django_db
def test_association_save_commits_the_rows_that_worked(superuser, monkeypatch):
    """Partial success: one stale row must not undo its 38 healthy siblings."""
    import seek.views as views

    def _one_bad(self, assay_id, internal_assay_id):
        if assay_id == 999:
            raise views.Assays_internal_assays.DoesNotExist("gone")

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    monkeypatch.setattr(views.DBtable_assaysinternalassays, "update", _one_bad)
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        ASSOC_URL,
        data=json.dumps({"records": [{"assay_id": 1, "internal_assay_id": 74},
                                     {"assay_id": 999, "internal_assay_id": 74},
                                     {"assay_id": 2, "internal_assay_id": 74}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)
    assert body["status"] == 1
    assert body["updated"] == 2
    assert len(body["errors"]) == 1


@pytest.mark.django_db
def test_a_real_value_error_is_reported_with_its_type(superuser, monkeypatch):
    """No sentinel-exception control flow: a genuine ValueError is not a rollback."""
    import seek.views as views

    def _raises(self, assay_id, internal_assay_id):
        raise ValueError("bad internal_assay_id")

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    monkeypatch.setattr(views.DBtable_assaysinternalassays, "update", _raises)
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        ASSOC_URL,
        data=json.dumps({"records": [{"assay_id": 1, "internal_assay_id": 74}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)
    assert body["updated"] == 0
    assert "ValueError" in json.dumps(body["errors"])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./scripts/run_tests.sh seek/tests/test_vocab_endpoints.py -q
```

Expected: FAIL — `test_association_save_rejects_get` gets 200, not 405.

- [ ] **Step 3: Write minimal implementation**

Add near the top of `seek/views.py`:

```python
from django.db import transaction
from django.views.decorators.http import require_POST
from seek.models import Assays_internal_assays
```

Add these helpers immediately above `internalAssaySave`:

```python
def _wb_envelope(status, msg, updated=0, errors=None):
    """The one response shape every workbench endpoint returns."""
    return JsonResponse({'status': status, 'msg': msg,
                         'updated': updated, 'errors': errors or []})


def _wb_records(request):
    """Records from a JSON body. Raises ValueError on anything malformed."""
    payload = json.loads(request.body.decode('utf-8') or '{}')
    records = payload.get('records')
    if not isinstance(records, list):
        raise ValueError('records must be a list')
    return records


def _wb_guard(request, action):
    """Shared auth gate. Returns an envelope to return early, or None to proceed."""
    user_seek = SeekDB(None, None, None).getSeekLogin(request, False)
    if not user_seek['status']:
        return _wb_envelope(0, user_seek.get('err', 'Not signed in'))
    if verifySuperUser(request) != 1:
        return _wb_envelope(0, f'You do not have permission to {action}.')
    return None


class _WbRowError(Exception):
    """One record is unusable. Its siblings are unaffected."""


def _wb_batch(records, apply_row, noun):
    """Apply apply_row to each record; commit what works, report what does not.

    Partial success is deliberate. Accepting 39 suggestions must not be undone
    because one row vanished to an intervening Sync, so each record gets its own
    transaction and a failure cannot poison its siblings.

    Note there is no sentinel exception used as control flow: a genuine
    ValueError raised inside apply_row is reported against its own record with
    its type, never mistaken for a rollback signal.
    """
    errors = []
    updated = 0
    for record in records:
        try:
            with transaction.atomic():
                apply_row(record)
        except _WbRowError as exc:
            errors.append({'record': record, 'error': str(exc)})
        except Exception as exc:  # noqa: BLE001 - reported per row, never swallowed
            errors.append({'record': record,
                           'error': f'{type(exc).__name__}: {exc}'})
        else:
            updated += 1

    if errors and not updated:
        return _wb_envelope(0, f'Saved nothing; {len(errors)} record(s) failed.',
                            0, errors)
    if errors:
        return _wb_envelope(1, f'Saved {updated} {noun}; {len(errors)} failed.',
                            updated, errors)
    return _wb_envelope(1, f'Saved {updated} {noun}.', updated)
```

Replace the whole body of `internalAssaySave` with:

```python
@require_POST
def internalAssaySave(request):
    blocked = _wb_guard(request, 'add the internal assay')
    if blocked:
        return blocked

    try:
        records = _wb_records(request)
    except (ValueError, UnicodeDecodeError) as exc:
        return _wb_envelope(0, f'Malformed request: {exc}')

    ia = DBtable_internalassays()

    def apply_row(record):
        title = record.get('internal_assay_title')
        if not title:
            raise _WbRowError('missing internal_assay_title')
        if 'id' not in record:
            ia.new(internal_assay_title=title)
        else:
            ia.update(internal_assay_id=record['id'], internal_assay_title=title)

    return _wb_batch(records, apply_row, 'internal assay(s)')
```

Replace the whole body of `internalAssayDelete` with:

```python
@require_POST
def internalAssayDelete(request):
    blocked = _wb_guard(request, 'delete the internal assay')
    if blocked:
        return blocked

    try:
        records = _wb_records(request)
    except (ValueError, UnicodeDecodeError) as exc:
        return _wb_envelope(0, f'Malformed request: {exc}')

    ia = DBtable_internalassays()

    def apply_row(record):
        if 'id' not in record:
            raise _WbRowError('missing id')
        try:
            ia.delete(record['id'])
        except Internal_assays.DoesNotExist:
            raise _WbRowError('internal assay no longer exists')

    return _wb_batch(records, apply_row, 'internal assay deletion(s)')
```

Add `Internal_assays` to the existing `from seek.models import …` line if absent.

Replace the whole body of `assayAssociationSave` with:

```python
@require_POST
def assayAssociationSave(request):
    blocked = _wb_guard(request, 'add the assay association')
    if blocked:
        return blocked

    try:
        records = _wb_records(request)
    except (ValueError, UnicodeDecodeError) as exc:
        return _wb_envelope(0, f'Malformed request: {exc}')

    aia = DBtable_assaysinternalassays()

    def apply_row(record):
        try:
            aia.update(record.get('assay_id'), record.get('internal_assay_id'))
        except Assays_internal_assays.DoesNotExist:
            # Row vanished between page load and save — almost always an
            # intervening Sync. Reported per row; siblings still commit.
            raise _WbRowError('association row no longer exists; re-sync and retry')

    return _wb_batch(records, apply_row, 'association(s)')
```

- [ ] **Step 4: Run test to verify it passes**

```bash
./scripts/run_tests.sh seek/tests/test_vocab_endpoints.py -q
```

Expected: PASS, 8 passed (3 from Task 4 + 5 new).

- [ ] **Step 5: Commit**

```bash
git add seek/views.py seek/tests/test_vocab_endpoints.py
git commit -m "fix(seek): assay association endpoints POST-only, atomic, JSON envelope"
```

---

### Task 6: Clade endpoints — POST, JSON envelope, atomic batches

**Files:**
- Modify: `seek/views.py` — `cladeSave` (1501), `cladeDelete` (1542), `cladeSampleTypesSave` (1574)
- Test: `seek/tests/test_vocab_endpoints.py`

**Interfaces:**
- Consumes: Task 5's `_wb_envelope`, `_wb_records`, `_wb_guard`, `_WbRowError`, `_wb_batch`. Add no new helpers — `_wb_batch` is the shared batch loop and these three endpoints supply only their own `apply_row`.
- Produces: the three clade endpoints share the identical contract and the same partial-success semantics.

- [ ] **Step 1: Write the failing test**

Append to `seek/tests/test_vocab_endpoints.py`:

```python
CLADE_ASSOC_URL = "/seek/clade/sampleTypes/save/"


@pytest.mark.django_db
def test_clade_sample_types_save_rejects_get(superuser, monkeypatch):
    import seek.views as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    client = Client()
    client.force_login(superuser)
    assert client.get(CLADE_ASSOC_URL).status_code == 405


@pytest.mark.django_db
def test_clade_sample_types_save_returns_same_envelope(superuser, monkeypatch):
    import seek.views as views

    monkeypatch.setattr(views.SeekDB, "getSeekLogin", lambda *a, **k: {"status": 1})
    # views.py aliases the class: `from dmac.dbtable_sampletypesclades import
    # DBtable_sample_types_clades as DBtable_stc` (seek/views.py:50).
    monkeypatch.setattr(views.DBtable_stc, "update", lambda self, s, c: None)
    client = Client()
    client.force_login(superuser)
    resp = client.post(
        CLADE_ASSOC_URL,
        data=json.dumps({"records": [{"sample_type_id": 3, "clade_id": 9}]}),
        content_type="application/json",
    )
    body = json.loads(resp.content)
    assert set(body) == {"status", "msg", "updated", "errors"}
    assert body["status"] == 1 and body["updated"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
./scripts/run_tests.sh seek/tests/test_vocab_endpoints.py -q
```

Expected: FAIL — GET returns 200, not 405.

- [ ] **Step 3: Write minimal implementation**

Replace the whole body of `cladeSave` with:

```python
@require_POST
def cladeSave(request):
    blocked = _wb_guard(request, 'add the clade')
    if blocked:
        return blocked

    try:
        records = _wb_records(request)
    except (ValueError, UnicodeDecodeError) as exc:
        return _wb_envelope(0, f'Malformed request: {exc}')

    clades = DBtable_clades()

    def apply_row(record):
        title = record.get('title')
        if not title:
            raise _WbRowError('missing title')
        # DBtable_clades.new/update both do int(order), so a missing order
        # raises TypeError rather than saving a null. Default it.
        color = record.get('color') or ''
        order = record.get('order')
        order = 0 if order in (None, '') else order
        if 'id' not in record:
            clades.new(title=title, color=color, order=order)
        else:
            clades.update(clade_id=record['id'], title=title,
                          color=color, order=order)

    return _wb_batch(records, apply_row, 'clade(s)')
```

Signatures verified against `dmac/dbtable_clades.py:85-98`:
`new(self, title, color, order)`, `update(self, clade_id, title, color, order)`,
`delete(self, clade_id)`.

Replace the whole body of `cladeDelete` with:

```python
@require_POST
def cladeDelete(request):
    blocked = _wb_guard(request, 'delete the clade')
    if blocked:
        return blocked

    try:
        records = _wb_records(request)
    except (ValueError, UnicodeDecodeError) as exc:
        return _wb_envelope(0, f'Malformed request: {exc}')

    clades = DBtable_clades()

    def apply_row(record):
        if 'id' not in record:
            raise _WbRowError('missing id')
        try:
            clades.delete(record['id'])
        except Clades.DoesNotExist:
            raise _WbRowError('clade no longer exists')

    return _wb_batch(records, apply_row, 'clade deletion(s)')
```

Replace the whole body of `cladeSampleTypesSave` with:

```python
@require_POST
def cladeSampleTypesSave(request):
    blocked = _wb_guard(request, 'add the clade association')
    if blocked:
        return blocked

    try:
        records = _wb_records(request)
    except (ValueError, UnicodeDecodeError) as exc:
        return _wb_envelope(0, f'Malformed request: {exc}')

    stc = DBtable_stc()

    def apply_row(record):
        try:
            stc.update(record.get('sample_type_id'), record.get('clade_id'))
        except Sample_types_clades.DoesNotExist:
            raise _WbRowError('association row no longer exists; re-sync and retry')

    return _wb_batch(records, apply_row, 'association(s)')
```

Add `Clades` and `Sample_types_clades` to the `from seek.models import …` line if
absent. Both class names verified: `seek/models.py:158` and `seek/models.py:194`.

- [ ] **Step 4: Run test to verify it passes**

```bash
./scripts/run_tests.sh seek/tests/test_vocab_endpoints.py -q
```

Expected: PASS, 10 passed (8 from Task 5 + 2 new).

- [ ] **Step 5: Commit**

```bash
git add seek/views.py seek/tests/test_vocab_endpoints.py
git commit -m "fix(seek): clade endpoints POST-only, atomic, JSON envelope"
```

---

### Task 7: The workbench module

**Files:**
- Create: `static/js/custom/ns-vocab-workbench.js`

**Interfaces:**
- Consumes: `nsEscapeHtml` from `datagrid-custom.js`; jQuery + easyui.
- Produces: global `nsVocabWorkbench(config)` where `config` is
  `{grid, entityLabel, entityIdField, entityTitleField, vocabIdField, vocabTitleField, rows, saveUrl, suggestionsUrl|null, csrfToken}`.
  There is deliberately no `vocabulary` key — the combobox editor is declared in
  each template's `thead` and the module never needs the term list.
  Exposes `postJson(url, records, csrfToken)` returning a jQuery promise.

- [ ] **Step 1: Write the module**

There is no JS test harness in this repo and the `Dockerfile` has no npm stage, so this task is verified by the manual checks in Task 11 rather than by an automated test. Every decision this file makes is presentational; all ranking happens server-side.

Create `static/js/custom/ns-vocab-workbench.js`:

```javascript
/* Tier-grouped association workbench.
 *
 * Domain-agnostic on purpose: everything specific arrives through `config`.
 * The file must never mention a particular entity type — see the design spec's
 * boundary test. Ranking is the server's job; this renders and posts.
 */

var NS_TIER_ORDER = ['exact', 'precedent', 'fuzzy', 'conflict', 'none'];

var NS_TIER_LABEL = {
  exact: 'Exact match',
  precedent: 'Precedent',
  fuzzy: 'Fuzzy',
  conflict: 'Conflict',
  none: 'No candidate'
};

function nsPostJson(url, records, csrfToken) {
  return $.ajax({
    url: url,
    type: 'POST',
    contentType: 'application/json',
    headers: { 'X-CSRFToken': csrfToken },
    data: JSON.stringify({ records: records })
  });
}

function nsVocabWorkbench(config) {
  var suggestions = {};
  var collapsed = {};

  function idOf(row) { return String(row[config.entityIdField]); }

  function tierOf(row) {
    if (row[config.vocabIdField]) { return 'mapped'; }
    var found = suggestions[idOf(row)];
    return found && found.length ? found[0].tier : 'none';
  }

  function candidateOf(row) {
    var found = suggestions[idOf(row)];
    return found && found.length ? found[0] : null;
  }

  // Rows regrouped into tier order, each group preceded by a synthetic header
  // row. easyui has no grouping view, so the header is a row the formatter
  // renders differently — cheaper and less fragile than a grouping plugin.
  function layout() {
    var buckets = {};
    NS_TIER_ORDER.concat(['mapped']).forEach(function (t) { buckets[t] = []; });
    (config.rows || []).forEach(function (row) {
      var tier = tierOf(row);
      (buckets[tier] = buckets[tier] || []).push(row);
    });

    var out = [];
    NS_TIER_ORDER.concat(['mapped']).forEach(function (tier) {
      var group = buckets[tier] || [];
      if (!group.length) { return; }
      out.push({ _nsHeader: true, _nsTier: tier, _nsCount: group.length });
      if (!collapsed[tier]) { out = out.concat(group); }
    });
    return out;
  }

  function render() {
    config.grid.datagrid('loadData', layout());
    updateCount();
  }

  function updateCount() {
    var unmapped = (config.rows || []).filter(function (r) {
      return !r[config.vocabIdField];
    }).length;
    $('#ns-wb-count').text(unmapped + ' unmapped');
  }

  function titleFormatter(value, row) {
    if (row._nsHeader) {
      var arrow = collapsed[row._nsTier] ? '&#9654;' : '&#9660;';
      var label = NS_TIER_LABEL[row._nsTier] || 'Already mapped';
      var btn = '';
      if (row._nsTier !== 'none' && row._nsTier !== 'conflict' && row._nsTier !== 'mapped') {
        btn = '<a href="javascript:void(0)" class="ns-wb-accept-tier" ' +
              'data-tier="' + row._nsTier + '">Accept all ' + row._nsCount + '</a>';
      }
      return '<span class="ns-wb-group" data-tier="' + row._nsTier + '">' + arrow +
             ' ' + nsEscapeHtml(label) + ' <span class="ns-tier ns-tier-' +
             row._nsTier + '">' + row._nsCount + '</span> ' + btn + '</span>';
    }
    return nsEscapeHtml(value == null ? '' : String(value));
  }

  function suggestedFormatter(value, row) {
    if (row._nsHeader) { return ''; }
    if (row[config.vocabIdField]) {
      return nsEscapeHtml(row[config.vocabTitleField] || '');
    }
    if (!config.suggestionsUrl) { return ''; }
    var candidate = candidateOf(row);
    if (!candidate || !candidate.vocabulary_title) {
      return '<span class="ns-tier ns-tier-none">no candidate</span>';
    }
    return '<span class="ns-tier ns-tier-' + candidate.tier + '">' +
           nsEscapeHtml(candidate.tier) + '</span> ' +
           nsEscapeHtml(candidate.vocabulary_title);
  }

  // easyui's native detailview: evidence expands in place under the row.
  function detailFormatter(index, row) {
    if (row._nsHeader) { return ''; }
    var candidate = candidateOf(row);
    if (!candidate) { return '<div class="ns-wb-evidence">No suggestion available.</div>'; }
    return '<div class="ns-wb-evidence"><dl>' +
           '<dt>Suggested</dt><dd>' + nsEscapeHtml(candidate.vocabulary_title || '—') + '</dd>' +
           '<dt>Why</dt><dd>' + nsEscapeHtml(candidate.basis || '') + '</dd>' +
           '</dl></div>';
  }

  function acceptRows(rows) {
    var records = [];
    rows.forEach(function (row) {
      var candidate = candidateOf(row);
      if (!candidate || !candidate.vocabulary_id) { return; }
      var record = {};
      record[config.entityIdField] = row[config.entityIdField];
      record[config.vocabIdField] = candidate.vocabulary_id;
      records.push(record);
    });
    if (!records.length) {
      $.messager.alert('Nothing to accept', 'No rows in this group carry a suggestion.', 'info');
      return;
    }
    nsPostJson(config.saveUrl, records, config.csrfToken)
      .done(function (data) {
        if (!data.status) {
          $.messager.alert('Not saved', data.msg + '<br>' + JSON.stringify(data.errors), 'error');
          return;
        }
        if (data.errors && data.errors.length) {
          // Partial success. Do NOT auto-reload — that would wipe the only
          // report of which rows failed before it can be read.
          $.messager.alert('Partly saved',
                           data.msg + '<br>' + JSON.stringify(data.errors), 'warning');
          return;
        }
        $.messager.show({ title: 'Saved', msg: data.msg });
        window.location.reload();
      })
      .fail(function (xhr) {
        $.messager.alert('Not saved', 'Request failed: ' + xhr.status, 'error');
      });
  }

  function bindGroupClicks() {
    config.grid.datagrid('getPanel').on('click', '.ns-wb-group', function (event) {
      var tier = $(this).data('tier');
      if ($(event.target).hasClass('ns-wb-accept-tier')) {
        event.stopPropagation();
        acceptRows((config.rows || []).filter(function (r) {
          return !r[config.vocabIdField] && tierOf(r) === tier;
        }));
        return;
      }
      collapsed[tier] = !collapsed[tier];
      render();
    });
  }

  function loadSuggestions() {
    if (!config.suggestionsUrl) { render(); return; }
    $.getJSON(config.suggestionsUrl)
      .done(function (data) {
        // Degrade, never block: a failed lookup leaves the page fully usable.
        suggestions = (data && data.status) ? (data.suggestions || {}) : {};
        render();
      })
      .fail(function () {
        suggestions = {};
        $('#ns-wb-count').append(' — suggestions unavailable');
        render();
      });
  }

  bindGroupClicks();
  // Deferred so the caller's `nsWorkbench = nsVocabWorkbench(...)` assignment
  // completes before any formatter runs. The no-suggester path (clades) renders
  // synchronously and would otherwise dereference an undefined nsWorkbench.
  setTimeout(loadSuggestions, 0);

  return {
    titleFormatter: titleFormatter,
    suggestedFormatter: suggestedFormatter,
    detailFormatter: detailFormatter,
    reload: loadSuggestions
  };
}
```

- [ ] **Step 2: Verify the boundary constraint**

```bash
grep -ci "assay" static/js/custom/ns-vocab-workbench.js
```

Expected: `0`. Any other number means domain knowledge leaked into the shared module — fix before committing.

- [ ] **Step 3: Verify it parses**

```bash
node --check static/js/custom/ns-vocab-workbench.js 2>&1 || echo "node unavailable on the host — check the browser console in Task 11 instead"
```

Expected: no output (parse clean), or the fallback message.

- [ ] **Step 4: Commit**

```bash
git add static/js/custom/ns-vocab-workbench.js
git commit -m "feat(seek): domain-agnostic tier-grouped association workbench"
```

---

### Task 8: Internal assays page adopts the workbench

**Files:**
- Modify: `seek/templates/internal_assays.html`

**Interfaces:**
- Consumes: Task 7's `nsVocabWorkbench`, Task 4's suggestions route, Task 5's endpoints.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Delete the shadowing copy and wire the module**

In `seek/templates/internal_assays.html`:

1. Delete the entire local `function saveSelectedIntoDB(dg, url) { … }` block (lines 51–107). It shadows the shared helper in `datagrid-custom.js` and parses a response body that no longer exists.
2. Add the detailview extension **and** the module after the `datagrid-custom.js`
script tag. The extension already ships at
`themes/NextSeek/static/js/easyui/datagrid-detailview.js` but neither page
currently loads it; it defines the global `detailview` used below.

```html
<script type="text/javascript" src="{{STATIC_URL}}js/easyui/datagrid-detailview.js"></script>
<script type="text/javascript" src="{{STATIC_URL}}js/custom/ns-vocab-workbench.js"></script>
```

3. Replace the association grid's `$(document).ready` block with:

```javascript
  var nsWorkbench;

  $(document).ready(function(){
    window.internal_assay_association_dg = $('#internal_assay_association_dg').datagrid({
      view: detailview,
      detailFormatter: function(index, row){
        return nsWorkbench.detailFormatter(index, row);
      }
    });
    nsWorkbench = nsVocabWorkbench({
      grid: window.internal_assay_association_dg,
      entityLabel: 'Assay',
      entityIdField: 'assay_id',
      entityTitleField: 'assay_title',
      vocabIdField: 'internal_assay_id',
      vocabTitleField: 'internal_assay_title',
      rows: assay_associations,
      saveUrl: '/seek/internal_assays/assayAssociation/save',
      suggestionsUrl: '/seek/admin/internal_assays/suggestions',
      csrfToken: '{{ csrf_token }}'
    });
  });
```

4. Replace the association table's `<thead>` with columns that use the module's formatters, and enable `detailview`:

```html
        		<thead>
        			<tr>
                <th data-options="field:'assay_title', width:420, formatter: function(v,r){ return nsWorkbench.titleFormatter(v,r); }">
                  <B>Assay</B>
                </th>
        				<th data-options='field:"internal_assay_id",width:300,
                    formatter: function(v,r){ return nsWorkbench.suggestedFormatter(v,r); },
        						editor:{
        							type:"combobox",
        							options:{
        								valueField: "id",
        								textField: "internal_assay_title",
        								data: internal_assays,
        								required: true
        							}
        						}'>
                  <B>Internal Assay</B>
                </th>
        			</tr>
        		</thead>
```

5. Add the count element to the association toolbar, before the existing buttons:

```html
            <span id="ns-wb-count" class="ns-wb-count"></span>
```

6. Change the two `accept(...)` toolbar handlers to re-fetch suggestions after a vocabulary change. Replace the internal-assays Save link with:

```html
        		<a href="javascript:void(0)" class="easyui-linkbutton" data-options="iconCls:'icon-save',plain:true" onclick="accept($('#dg_internal_assays'), '/seek/internal_assays/save'); nsWorkbench.reload();">Save</a>
```

- [ ] **Step 2: Confirm the shadowing copy is gone**

```bash
grep -c "function saveSelectedIntoDB" seek/templates/internal_assays.html
```

Expected: `0`.

- [ ] **Step 3: Commit**

```bash
git add seek/templates/internal_assays.html
git commit -m "feat(seek): internal assays page adopts the workbench module"
```

---

### Task 9: Clades page adopts the workbench

**Files:**
- Modify: `seek/templates/clades.html`

**Interfaces:**
- Consumes: Task 7's `nsVocabWorkbench`, Task 6's endpoints.
- Produces: nothing.

- [ ] **Step 1: Delete the shadowing copy and wire the module without a suggester**

In `seek/templates/clades.html`:

1. Delete the entire local `function saveSelectedIntoDB(dg, url) { … }` block starting at line 31.
2. Add the detailview extension and the module after the `datagrid-custom.js`
script tag:

```html
<script type="text/javascript" src="{{STATIC_URL}}js/easyui/datagrid-detailview.js"></script>
<script type="text/javascript" src="{{STATIC_URL}}js/custom/ns-vocab-workbench.js"></script>
```

3. Replace the sample-types association `$(document).ready` block with:

```javascript
  var nsWorkbench;

  $(document).ready(function(){
    window.stc_dg = $('#stc_dg').datagrid({
      view: detailview,
      detailFormatter: function(index, row){
        return nsWorkbench.detailFormatter(index, row);
      }
    });
    nsWorkbench = nsVocabWorkbench({
      grid: window.stc_dg,
      entityLabel: 'Sample Type',
      entityIdField: 'sample_type_id',
      entityTitleField: 'sample_type_title',
      vocabIdField: 'clade_id',
      vocabTitleField: 'clade_title',
      rows: sample_types,
      saveUrl: '/seek/clade/sampleTypes/save/',
      suggestionsUrl: null,
      csrfToken: '{{ csrf_token }}'
    });
  });
```

`suggestionsUrl: null` is the point of this task — sample-type to clade has no title convention, so the Suggested column stays blank and the module must not assume otherwise.

4. Add the count element to the `#stc_tb_toolbar` div, before the existing buttons:

```html
            <span id="ns-wb-count" class="ns-wb-count"></span>
```

5. Wire the formatters into the existing `#stc_dg` `<thead>`. Without a formatter
on the entity-title column the synthetic group-header rows render as blank cells.
Change the `sample_type_title` and `clade_title` column definitions to:

```html
        				<th data-options="field:'sample_type_title',width:420, formatter: function(v,r){ return nsWorkbench.titleFormatter(v,r); }">
                  <B>Sample Type</B>
                </th>
        				<th data-options='field:"clade_title",width:300,
                    formatter: function(v,r){ return nsWorkbench.suggestedFormatter(v,r); },
        						editor:{
        							type:"combobox",
        							options:{
        								valueField: "id",
        								textField: "title",
        								data: clades,
        								required: true
        							}
        						}'>
                  <B>Clade</B>
                </th>
```

Leave the `sample_type_id` column as it is.

- [ ] **Step 2: Confirm the shadowing copy is gone**

```bash
grep -c "function saveSelectedIntoDB" seek/templates/clades.html
```

Expected: `0`.

- [ ] **Step 3: Commit**

```bash
git add seek/templates/clades.html
git commit -m "feat(seek): clades page adopts the workbench module"
```

---

### Task 10: Tier styling

**Files:**
- Modify: `themes/NextSeek/static/css/nextseek.css`

**Interfaces:**
- Consumes: class names emitted by Task 7 — `.ns-tier`, `.ns-tier-{exact,precedent,fuzzy,conflict,none}`, `.ns-wb-group`, `.ns-wb-evidence`, `.ns-wb-count`, `.ns-wb-accept-tier`.
- Produces: nothing.

- [ ] **Step 1: Append the styles**

Append to `themes/NextSeek/static/css/nextseek.css`:

```css
/* --- Association workbench (see docs/superpowers/specs/2026-09-10-…-design.md) --- */

.ns-wb-count { font-weight: 700; margin-right: 1em; }

.ns-wb-group { display: inline-block; width: 100%; font-weight: 700; cursor: pointer; }
.ns-wb-accept-tier { margin-left: 1em; font-weight: 400; }

.ns-tier {
  display: inline-block;
  font-size: 10px;
  line-height: 1.6;
  padding: 0 6px;
  border: 1px solid;
  border-radius: 9px;
  vertical-align: middle;
}
.ns-tier-exact     { color: #1a7f4b; border-color: #1a7f4b; }
.ns-tier-precedent { color: #8a6100; border-color: #8a6100; }
.ns-tier-fuzzy     { color: #8a4b00; border-color: #8a4b00; }
.ns-tier-conflict  { color: #a11;    border-color: #a11; }
.ns-tier-none      { color: #a11;    border-color: #a11; }

.ns-wb-evidence { padding: 8px 12px 10px 26px; background: rgba(0, 0, 0, .03); }
.ns-wb-evidence dt {
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: .05em;
  opacity: .6;
  margin-top: 6px;
}
.ns-wb-evidence dd { margin: 2px 0 0; }
```

- [ ] **Step 2: Commit**

```bash
git add themes/NextSeek/static/css/nextseek.css
git commit -m "style(seek): tier badges and evidence styling for the workbench"
```

---

### Task 11: Full verification

**Files:** none modified unless a check fails.

**Interfaces:**
- Consumes: everything.
- Produces: a verified deployment.

- [ ] **Step 1: Run the whole affected suite**

```bash
./scripts/run_tests.sh seek/tests dmac/tests -q
```

Expected: all pass, no errors. Record the count.

- [ ] **Step 2: Rebuild and serve the static changes**

```bash
./startup.sh rebuild
```

`static/` and theme CSS changed, so `collectstatic` must run — `./startup.sh rebuild` does it; a raw `docker compose up --build` does not.

- [ ] **Step 3: Verify the boundary constraints held**

```bash
grep -ci "assay" static/js/custom/ns-vocab-workbench.js
grep -cE "^from django|^import django|dmac\.settings" dmac/vocab_resolver.py
```

Expected: `0` and `0`.

- [ ] **Step 4: Manual browser checks**

Sign in as a superuser and open the internal assays admin page. Confirm each of:

1. The unmapped count renders and matches the number of blank rows.
2. Rows are grouped by tier, highest confidence first, and group headers collapse and expand on click.
3. Clicking a row expands evidence in place, showing the suggested term and the basis text.
4. *Accept all N* on the exact group saves, reports a count, and the rows move to the mapped group after reload.
5. Adding a vocabulary term and saving makes it selectable **without a full page reload**.
6. Long titles render without truncation.
7. In devtools, the association save is a `POST` with a JSON body — not a `GET` with a query string.

Then open the clades admin page and confirm the grid still loads, saves, and shows **no** Suggested content, with no console errors.

- [ ] **Step 5: Confirm degradation**

Temporarily rename the suggestions view's URL in `seek/urls.py`, reload the assays page, and confirm it still renders and saves with "suggestions unavailable" beside the count. Restore the URL afterwards.

- [ ] **Step 6: Commit any fixes**

```bash
git add -A
git commit -m "fix(seek): corrections from workbench verification pass"
```

---

## Notes for the implementer

- **`docs/superpowers/plans/*` is gitignored** with per-file negations. To commit this plan, add a `!docs/superpowers/plans/2026-09-10-assay-association-workbench.md` line to `.gitignore` alongside the existing exceptions, matching their comment style.
- **The resolver will not reproduce every mapping a human would make.** `Microfluidic Network Formation` → `Device Creation` needs domain knowledge it does not have, so that row lands in `none`. That is correct: an honest abstention beats a confident wrong answer, and `none` is a visible tier precisely so those rows get a human.
- **Do not add provenance columns.** It was considered and declined; the accepted risk and its mitigation are recorded in the spec's *Data model* section.
